"""PC relay from the Sailfish TCP publisher to a private Unix socket."""

import argparse
import logging
import os
import signal
import socket
import socketserver
import stat
import threading
import time

from .iio import IioError, IioInjector
from .protocol import JsonLineSocket, ProtocolError, decode_message, encode_message


LOG = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


class SensorState(object):
    """Thread-safe latest-reading cache and notification point."""

    def __init__(self):
        self._condition = threading.Condition()
        self._readings = {}
        self._sensors = []
        self._connected = False
        self._sequence = 0
        self._last_reading = None

    def set_phone_sensors(self, sensors):
        names = sorted(set(name for name in sensors if isinstance(name, str)))
        with self._condition:
            self._sensors = names
            self._connected = True
            self._condition.notify_all()

    def set_disconnected(self):
        with self._condition:
            self._connected = False
            self._condition.notify_all()

    def update(self, event):
        sensor = event.get("sensor")
        timestamp_us = event.get("timestamp_us")
        values = event.get("values")
        if (event.get("type") != "reading" or not isinstance(sensor, str) or
                not isinstance(timestamp_us, (int, float)) or not isinstance(values, dict)):
            raise ProtocolError("invalid reading event")
        reading = {
            "sensor": sensor,
            "timestamp_us": timestamp_us,
            "received_ns": time.monotonic_ns(),
            "values": values,
        }
        with self._condition:
            self._readings[sensor] = reading
            self._last_reading = reading
            self._sequence += 1
            self._condition.notify_all()
            return self._sequence

    def sensors(self):
        with self._condition:
            return list(self._sensors)

    def get(self, sensor, max_age_ms):
        if not isinstance(sensor, str) or not sensor:
            raise ValueError("sensor must be a non-empty string")
        if not isinstance(max_age_ms, (int, float)) or max_age_ms < 0:
            raise ValueError("max_age_ms must be a non-negative number")
        with self._condition:
            reading = self._readings.get(sensor)
            if reading is None:
                return None, "no reading for {}".format(sensor)
            age_ns = time.monotonic_ns() - reading["received_ns"]
            if age_ns > int(max_age_ms * 1000000):
                return None, "reading for {} is stale".format(sensor)
            return dict(reading), None

    def wait_for_update(self, previous_sequence, sensors, stop_event):
        selected = set(sensors) if sensors else None
        with self._condition:
            while not stop_event.is_set():
                if self._sequence != previous_sequence:
                    reading = self._last_reading
                    if reading and (selected is None or reading["sensor"] in selected):
                        return self._sequence, dict(reading)
                    previous_sequence = self._sequence
                self._condition.wait(0.5)
            return previous_sequence, None

    def sequence(self):
        with self._condition:
            return self._sequence

    def last_reading(self):
        with self._condition:
            return dict(self._last_reading) if self._last_reading else None

    def wake(self):
        with self._condition:
            self._condition.notify_all()


class PhoneWorker(object):
    """Reconnect to a phone and feed events to :class:`SensorState`."""

    def __init__(self, host, port, token, sensors, state, reconnect_seconds=2.0,
                 reading_callback=None):
        self.host = host
        self.port = port
        self.token = token
        self.sensors = list(sensors)
        self.state = state
        self.reading_callback = reading_callback
        self.reconnect_seconds = reconnect_seconds
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sailfish-phone", daemon=True)
        self._socket_lock = threading.Lock()
        self._socket = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self._socket_lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._socket.close()
                self._socket = None
        self.thread.join(timeout=3.0)
        self.state.set_disconnected()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._connect_and_stream()
            except (ConnectionError, OSError, ProtocolError, ValueError) as error:
                if not self.stop_event.is_set():
                    LOG.warning("phone connection failed: %s", error)
            finally:
                self.state.set_disconnected()
            self.stop_event.wait(self.reconnect_seconds)

    def _connect_and_stream(self):
        sock = socket.create_connection((self.host, self.port), timeout=5.0)
        with self._socket_lock:
            self._socket = sock
        try:
            stream = JsonLineSocket(sock)
            stream.send({"type": "hello", "protocol": PROTOCOL_VERSION, "token": self.token})
            hello = stream.receive()
            self._require_type(hello, "hello")
            if hello.get("protocol") != PROTOCOL_VERSION or not isinstance(hello.get("sensors"), list):
                raise ProtocolError("phone has an incompatible hello response")
            self.state.set_phone_sensors(hello["sensors"])

            requested = self.sensors or hello["sensors"]
            stream.send({"type": "subscribe", "sensors": requested})
            while True:
                subscribed = stream.receive()
                if subscribed.get("type") == "reading":
                    self._publish_reading(subscribed)
                    continue
                self._require_type(subscribed, "subscribed")
                break

            sock.settimeout(0.5)
            while not self.stop_event.is_set():
                try:
                    event = stream.receive()
                except socket.timeout:
                    continue
                if event.get("type") == "reading":
                    self._publish_reading(event)
                elif event.get("type") == "error":
                    raise ProtocolError(event.get("message", "phone reported an error"))
        finally:
            with self._socket_lock:
                if self._socket is sock:
                    self._socket = None
            sock.close()

    def _publish_reading(self, event):
        sequence = self.state.update(event)
        if self.reading_callback is not None:
            self.reading_callback(self.state.last_reading(), sequence)

    @staticmethod
    def _require_type(message, expected):
        if message.get("type") == "error":
            raise ProtocolError(message.get("message", "phone reported an error"))
        if message.get("type") != expected:
            raise ProtocolError("expected {}, got {}".format(expected, message.get("type")))


class LocalRequestHandler(socketserver.StreamRequestHandler):
    """One Unix-socket client speaking local JSON-lines."""

    def handle(self):
        while not self.server.stop_event.is_set():
            line = self.rfile.readline(65537)
            if not line:
                return
            if len(line) > 65536 or not line.endswith(b"\n"):
                self._write({"ok": False, "error": "message exceeds 64 KiB"})
                return
            try:
                request = decode_message(line[:-1])
                self._dispatch(request)
            except (ProtocolError, ValueError) as error:
                self._write({"ok": False, "error": str(error)})

    def _dispatch(self, request):
        operation = request.get("op")
        if operation == "sensors":
            self._write({"ok": True, "sensors": self.server.state.sensors()})
        elif operation == "get":
            reading, error = self.server.state.get(request.get("sensor"), request.get("max_age_ms", 1000))
            if error:
                self._write({"ok": False, "error": error})
            else:
                self._write({"ok": True, "reading": reading})
        elif operation == "watch":
            requested = request.get("sensors")
            if requested is not None and (not isinstance(requested, list) or not all(isinstance(item, str) for item in requested)):
                raise ValueError("sensors must be a list of names")
            self._watch(requested)
        else:
            self._write({"ok": False, "error": "unknown operation"})

    def _watch(self, requested):
        sequence = self.server.state.sequence()
        self._write({"ok": True, "type": "watching"})
        while not self.server.stop_event.is_set():
            sequence, reading = self.server.state.wait_for_update(sequence, requested, self.server.stop_event)
            if reading is None:
                return
            self._write({"ok": True, "type": "reading", "reading": reading})

    def _write(self, message):
        self.wfile.write(encode_message(message))
        self.wfile.flush()


class LocalServer(socketserver.ThreadingUnixStreamServer):
    """Private Unix-domain endpoint for local programs."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, state):
        self.path = path
        self.state = state
        self.stop_event = threading.Event()
        self._prepare_path(path)
        socketserver.ThreadingUnixStreamServer.__init__(self, path, LocalRequestHandler)
        os.chmod(path, 0o600)

    @staticmethod
    def _prepare_path(path):
        if not os.path.lexists(path):
            return
        mode = os.lstat(path).st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError("refusing to replace non-socket path {}".format(path))
        os.unlink(path)

    def close(self):
        self.stop_event.set()
        self.state.wake()
        self.shutdown()
        self.server_close()
        if os.path.lexists(self.path) and stat.S_ISSOCK(os.lstat(self.path).st_mode):
            os.unlink(self.path)


class BridgeRuntime(object):
    """Own the phone worker and its local API server."""

    def __init__(self, host, port, token, sensors, socket_path, iio_device=None,
                 mount_matrix=None):
        self.state = SensorState()
        self.injector = IioInjector(iio_device, mount_matrix) if iio_device else None
        self.worker = PhoneWorker(host, port, token, sensors, self.state,
                                  reading_callback=self._inject_reading)
        self.local_server = LocalServer(socket_path, self.state)
        self.thread = threading.Thread(target=self.local_server.serve_forever, name="local-sensor-api", daemon=True)

    def start(self):
        self.thread.start()
        self.worker.start()

    def stop(self):
        self.worker.stop()
        self.local_server.close()
        self.thread.join(timeout=3.0)
        if self.injector is not None:
            self.injector.close()

    def _inject_reading(self, reading, sequence):
        del sequence
        if self.injector is None:
            return
        try:
            self.injector.inject(reading)
        except (IioError, OSError) as error:
            LOG.warning("could not inject %s into IIO: %s", reading.get("sensor"), error)


def default_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "sailfish-sensors.sock")
    return "/tmp/sailfish-sensors-{}.sock".format(os.getuid())


def parse_sensor_names(value):
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("at least one sensor name is required")
    return names


def read_token_file(path):
    try:
        with open(path, "r", encoding="utf-8") as token_file:
            token = token_file.read().strip()
    except OSError as error:
        raise argparse.ArgumentTypeError("cannot read token file {}: {}".format(path, error))
    if len(token) < 16:
        raise argparse.ArgumentTypeError("token file must contain at least 16 characters")
    return token


def main(argv=None):
    parser = argparse.ArgumentParser(description="Expose Sailfish phone sensors through a private local socket.")
    parser.add_argument("--host", required=True, help="Sailfish phone address")
    parser.add_argument("--port", type=int, default=8765, help="phone TCP port (default: 8765)")
    parser.add_argument("--token", default=os.environ.get("SFSB_TOKEN"), help="phone shared secret; SFSB_TOKEN is accepted")
    parser.add_argument("--token-file", help="file containing the phone shared secret")
    parser.add_argument("--sensors", type=parse_sensor_names, help="comma-separated sensors; defaults to all phone sensors")
    parser.add_argument("--socket", default=default_socket_path(), help="local Unix socket path")
    parser.add_argument("--iio-device", help="inject readings into this Sailfish IIO device")
    parser.add_argument("--mount-matrix", help="nine comma-separated row-major axis transform values")
    parser.add_argument("--verbose", action="store_true", help="log reconnect attempts")
    arguments = parser.parse_args(argv)
    if arguments.token and arguments.token_file:
        parser.error("use either --token or --token-file")
    if arguments.token_file:
        try:
            arguments.token = read_token_file(arguments.token_file)
        except argparse.ArgumentTypeError as error:
            parser.error(str(error))
    if not arguments.token or len(arguments.token) < 16:
        parser.error("--token (or SFSB_TOKEN) must contain at least 16 characters")
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be from 1 through 65535")

    logging.basicConfig(level=logging.DEBUG if arguments.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        runtime = BridgeRuntime(arguments.host, arguments.port, arguments.token,
                                arguments.sensors or [], arguments.socket,
                                arguments.iio_device, arguments.mount_matrix)
    except IioError as error:
        parser.error(str(error))
    stopping = threading.Event()

    def stop_runtime(signum, frame):
        del signum, frame
        stopping.set()

    signal.signal(signal.SIGINT, stop_runtime)
    signal.signal(signal.SIGTERM, stop_runtime)
    runtime.start()
    LOG.info("local sensor API listening at %s", arguments.socket)
    try:
        stopping.wait()
    finally:
        runtime.stop()
    return 0
