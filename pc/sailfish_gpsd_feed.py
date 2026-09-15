"""Turn authenticated Sailfish location updates into a localhost NMEA feed.

The feed is intended as a TCP source for gpsd. It has no GPS receiver of its
own: each NMEA RMC record represents one valid Qt Positioning update received
from the Sailfish phone.
"""

import argparse
import datetime
import logging
import math
import os
import select
import signal
import socket
import socketserver
import threading

from sailfish_sensors.protocol import JsonLineSocket, ProtocolError


LOG = logging.getLogger(__name__)
PROTOCOL_VERSION = 1
KNOTS_PER_METRE_PER_SECOND = 1.9438444924406


class NmeaError(ValueError):
    """A phone event cannot be represented truthfully as NMEA."""


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise NmeaError("{} must be a finite number".format(field))
    return float(value)


def _coordinate(value, maximum, degree_width, positive, negative, field):
    decimal = _finite_number(value, field)
    if decimal < -maximum or decimal > maximum:
        raise NmeaError("{} is out of range".format(field))

    # Calculate in 1/10,000 minute units so an NMEA rounding carry is applied
    # to degrees rather than producing an invalid 60.0000 minute value.
    total_minute_ten_thousandths = int(round(abs(decimal) * 60.0 * 10000.0))
    degrees, minute_ten_thousandths = divmod(total_minute_ten_thousandths, 60 * 10000)
    if degrees > maximum:
        raise NmeaError("{} cannot be rounded as NMEA".format(field))
    minutes = minute_ten_thousandths / 10000.0
    hemisphere = negative if decimal < 0 else positive
    return ("{degrees:0{width}d}{minutes:07.4f}".format(
        degrees=degrees, width=degree_width, minutes=minutes), hemisphere)


def nmea_sentence(body):
    """Return one checksummed ASCII NMEA sentence, including CRLF."""
    if not isinstance(body, str) or not body or "\r" in body or "\n" in body or "*" in body:
        raise NmeaError("invalid NMEA body")
    try:
        encoded = body.encode("ascii")
    except UnicodeEncodeError as error:
        raise NmeaError("NMEA body must be ASCII: {}".format(error))
    checksum = 0
    for byte in encoded:
        checksum ^= byte
    return b"$" + encoded + ("*{:02X}\r\n".format(checksum)).encode("ascii")


def rmc_sentence(event):
    """Convert one valid phone location reading to a NMEA RMC sentence.

    RMC is deliberately the only sentence produced. It carries coordinate,
    time, speed, and course without inventing altitude, satellite count, DOP,
    or a claim that the PC has a local RF receiver.
    """
    if not isinstance(event, dict) or event.get("type") != "reading" or event.get("sensor") != "location":
        raise NmeaError("expected a location reading")
    values = event.get("values")
    if not isinstance(values, dict):
        raise NmeaError("location values must be an object")

    timestamp_ms = _finite_number(event.get("timestamp_utc_ms"), "timestamp_utc_ms")
    if timestamp_ms <= 0 or timestamp_ms != int(timestamp_ms):
        raise NmeaError("timestamp_utc_ms must be a positive whole millisecond")
    try:
        timestamp = datetime.datetime.fromtimestamp(
            timestamp_ms / 1000.0, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise NmeaError("timestamp_utc_ms is invalid: {}".format(error))

    latitude, north_south = _coordinate(values.get("latitude"), 90, 2, "N", "S", "latitude")
    longitude, east_west = _coordinate(values.get("longitude"), 180, 3, "E", "W", "longitude")
    speed = values.get("speed_mps")
    if speed is None:
        speed_knots = ""
    else:
        speed_mps = _finite_number(speed, "speed_mps")
        if speed_mps < 0:
            raise NmeaError("speed_mps must not be negative")
        speed_knots = "{:.2f}".format(speed_mps * KNOTS_PER_METRE_PER_SECOND)
    bearing = values.get("bearing_degrees")
    if bearing is None:
        course = ""
    else:
        bearing_degrees = _finite_number(bearing, "bearing_degrees")
        if bearing_degrees < 0 or bearing_degrees >= 360:
            raise NmeaError("bearing_degrees is out of range")
        course = "{:.1f}".format(bearing_degrees)

    time_utc = "{:02d}{:02d}{:02d}.{:02d}".format(
        timestamp.hour, timestamp.minute, timestamp.second, timestamp.microsecond // 10000)
    date_utc = "{:02d}{:02d}{:02d}".format(timestamp.day, timestamp.month, timestamp.year % 100)
    return nmea_sentence("GPRMC,{},{},{},{},{},{},{},{},{},,,A".format(
        time_utc, "A", latitude, north_south, longitude, east_west,
        speed_knots, course, date_utc))


class NmeaState(object):
    """Keep the last complete NMEA update for clients that connect later."""

    def __init__(self):
        self._lock = threading.Lock()
        self._payload = b""

    def set_payload(self, payload):
        with self._lock:
            self._payload = payload

    def payload(self):
        with self._lock:
            return self._payload


class NmeaRequestHandler(socketserver.BaseRequestHandler):
    """Hold one localhost GPSD source connection open."""

    def handle(self):
        if self.client_address[0] != "127.0.0.1":
            return
        self.server.add_client(self.request)
        try:
            while not self.server.stop_event.wait(0.5):
                try:
                    readable, unused_writable, unused_errors = select.select([self.request], [], [], 0)
                    del unused_writable, unused_errors
                    if not readable:
                        continue
                    data = self.request.recv(4096)
                    if not data:
                        return
                except (AttributeError, OSError):
                    return
        finally:
            self.server.remove_client(self.request)


class NmeaServer(socketserver.ThreadingTCPServer):
    """A loopback-only, read-only NMEA TCP server."""

    address_family = socket.AF_INET
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host, port, state):
        self.state = state
        self.stop_event = threading.Event()
        self._clients_lock = threading.Lock()
        self._clients = set()
        socketserver.ThreadingTCPServer.__init__(self, (host, port), NmeaRequestHandler)

    def add_client(self, client):
        with self._clients_lock:
            self._clients.add(client)
            payload = self.state.payload()
            if payload:
                try:
                    client.sendall(payload)
                except OSError:
                    self._clients.discard(client)

    def remove_client(self, client):
        with self._clients_lock:
            self._clients.discard(client)

    def publish(self, payload):
        self.state.set_payload(payload)
        with self._clients_lock:
            for client in list(self._clients):
                try:
                    client.sendall(payload)
                except OSError:
                    self._clients.discard(client)

    def close(self):
        self.stop_event.set()
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        self.shutdown()
        self.server_close()


class LocationWorker(object):
    """Authenticate to the location listener and publish valid RMC updates."""

    def __init__(self, host, port, token, publish, reconnect_seconds=2.0):
        self.host = host
        self.port = port
        self.token = token
        self.publish = publish
        self.reconnect_seconds = reconnect_seconds
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sailfish-location", daemon=True)
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

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._connect_and_stream()
            except (ConnectionError, OSError, ProtocolError, ValueError) as error:
                if not self.stop_event.is_set():
                    LOG.warning("phone location connection failed: %s", error)
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
                raise ProtocolError("phone has an incompatible location hello response")
            if "location" not in hello["sensors"]:
                raise ProtocolError("phone does not expose a Qt Positioning source")

            stream.send({"type": "subscribe", "sensors": ["location"]})
            while True:
                response = stream.receive()
                if response.get("type") == "reading":
                    self._publish_reading(response)
                    continue
                self._require_type(response, "subscribed")
                if "location" not in response.get("sensors", []):
                    raise ProtocolError("phone rejected the location subscription")
                break

            sock.settimeout(0.5)
            while not self.stop_event.is_set():
                try:
                    response = stream.receive()
                except socket.timeout:
                    continue
                if response.get("type") == "reading":
                    self._publish_reading(response)
                elif response.get("type") == "error":
                    raise ProtocolError(response.get("message", "phone reported an error"))
                else:
                    raise ProtocolError("unexpected phone location message")
        finally:
            with self._socket_lock:
                if self._socket is sock:
                    self._socket = None
            sock.close()

    def _publish_reading(self, event):
        try:
            self.publish(rmc_sentence(event))
        except NmeaError as error:
            LOG.warning("discarded invalid phone location update: %s", error)

    @staticmethod
    def _require_type(message, expected):
        if message.get("type") == "error":
            raise ProtocolError(message.get("message", "phone reported an error"))
        if message.get("type") != expected:
            raise ProtocolError("expected {}, got {}".format(expected, message.get("type")))


class GpsdFeedRuntime(object):
    """Own the authenticated phone worker and local NMEA source server."""

    def __init__(self, host, port, token, listen, listen_port):
        self.state = NmeaState()
        self.server = NmeaServer(listen, listen_port, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, name="gpsd-nmea-feed", daemon=True)
        self.worker = LocationWorker(host, port, token, self.server.publish)

    def start(self):
        self.thread.start()
        self.worker.start()

    def stop(self):
        self.worker.stop()
        self.server.close()
        self.thread.join(timeout=3.0)


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
    parser = argparse.ArgumentParser(description="Feed authenticated Sailfish location updates to localhost GPSD.")
    parser.add_argument("--host", required=True, help="Sailfish location listener address")
    parser.add_argument("--port", type=int, default=8766, help="Sailfish location listener TCP port (default: 8766)")
    parser.add_argument("--token", default=os.environ.get("SFSB_TOKEN"), help="phone shared secret; SFSB_TOKEN is accepted")
    parser.add_argument("--token-file", help="file containing the phone shared secret")
    parser.add_argument("--listen", default="127.0.0.1", help="must be 127.0.0.1 (default: %(default)s)")
    parser.add_argument("--listen-port", type=int, default=2948, help="local NMEA TCP port (default: 2948)")
    parser.add_argument("--verbose", action="store_true", help="log reconnect attempts and invalid updates")
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
    if arguments.listen != "127.0.0.1":
        parser.error("--listen must be 127.0.0.1; the NMEA feed is localhost-only")
    if not 1 <= arguments.port <= 65535 or not 1 <= arguments.listen_port <= 65535:
        parser.error("ports must be from 1 through 65535")

    logging.basicConfig(level=logging.DEBUG if arguments.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    runtime = GpsdFeedRuntime(arguments.host, arguments.port, arguments.token,
                              arguments.listen, arguments.listen_port)
    stopping = threading.Event()

    def stop_runtime(signum, frame):
        del signum, frame
        stopping.set()

    signal.signal(signal.SIGINT, stop_runtime)
    signal.signal(signal.SIGTERM, stop_runtime)
    runtime.start()
    LOG.info("localhost NMEA feed listening at %s:%s", arguments.listen, arguments.listen_port)
    try:
        stopping.wait()
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
