#!/usr/bin/env python3
"""Run a token-protected SensorFW publisher on a Sailfish phone.

This runtime implementation uses only Python 3 and D-Bus tools included on
Sailfish Developer Mode images.  It exposes the same version-1 protocol as the
native C++ implementation in this directory.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import signal
import socket
import socketserver
import subprocess
import sys
import threading


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
SENSOR_SPECS = {
    "accelerometersensor": ("accelerometer", "local.AccelerometerSensor", "xyz"),
    "alssensor": ("light", "local.ALSSensor", "lux"),
    "magnetometersensor": ("magnetometer", "local.MagnetometerSensor", "xyz"),
    "orientationsensor": ("orientation", "local.OrientationSensor", "orientation"),
    "pressuresensor": ("pressure", "local.PressureSensor", "pressure"),
    "proximitysensor": ("proximity", "local.ProximitySensor", "close"),
    "rotationsensor": ("rotation", "local.RotationSensor", "rotation"),
}

# SensorFW's accelerometer interface is verified on the target Sailfish 3.1
# image.  The native Qt publisher in this directory exposes the remaining Qt
# Sensors once development headers are installed; do not spin up unverified
# legacy D-Bus interfaces here because some block indefinitely on that image.
POLLABLE_PLUGIN_IDS = ("accelerometersensor",)


def encode(message):
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def decode(line):
    if len(line) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 64 KiB")
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid JSON: {}".format(error))
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")
    return message


class SensorFwReader(object):
    """Poll installed SensorFW D-Bus interfaces using standard Sailfish tools."""

    def __init__(self, callback):
        self.callback = callback
        self.sessions = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.threads = []

    @staticmethod
    def available_sensor_names():
        output = SensorFwReader._call([
            "/SensorManager", "local.SensorManager.availableSensorPlugins",
        ])
        plugin_ids = re.findall(r'string "([^"]+)"', output.decode("utf-8", "replace"))
        return sorted(SENSOR_SPECS[plugin_id][0] for plugin_id in plugin_ids if plugin_id in POLLABLE_PLUGIN_IDS)

    @staticmethod
    def _call(arguments):
        return subprocess.check_output([
            "dbus-send", "--system", "--print-reply", "--reply-timeout=5000",
            "--dest=com.nokia.SensorService",
        ] + arguments, stderr=subprocess.STDOUT)

    def start(self):
        with self.lock:
            if self.sessions:
                return
            try:
                self._start_available_sensors()
            except (OSError, subprocess.CalledProcessError) as error:
                sys.stderr.write("SensorFW startup failed: {}\n".format(error))
                self._stop_locked()
            if self.sessions:
                self.stop_event.clear()
                for plugin_id, interface, unused_session_id in self.sessions:
                    thread = threading.Thread(target=self._poll_sensor, args=(plugin_id, interface),
                                              name="sensorfw-{}".format(plugin_id))
                    thread.daemon = True
                    thread.start()
                    self.threads.append(thread)

    def _start_available_sensors(self):
        pid = os.getpid()
        for plugin_id in POLLABLE_PLUGIN_IDS:
            try:
                result = self._call([
                    "/SensorManager", "local.SensorManager.requestSensor",
                    "string:{}".format(plugin_id), "int64:{}".format(pid),
                ])
                match = re.search(r'int32 (-?\d+)', result.decode("utf-8", "replace"))
                if match is None:
                    continue
                session_id = int(match.group(1))
                interface = SENSOR_SPECS[plugin_id][1]
                path = "/SensorManager/{}".format(plugin_id)
                self._call([path, "{}.start".format(interface), "int32:{}".format(session_id)])
                self.sessions.append((plugin_id, interface, session_id))
            except (OSError, subprocess.CalledProcessError) as error:
                sys.stderr.write("SensorFW {} startup failed: {}\n".format(plugin_id, error))
                continue

    def stop(self):
        self.stop_event.set()
        self.threads = []
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        for plugin_id, interface, session_id in self.sessions:
            path = "/SensorManager/{}".format(plugin_id)
            try:
                self._call([path, "{}.stop".format(interface), "int32:{}".format(session_id)])
                self._call([
                    "/SensorManager", "local.SensorManager.releaseSensor",
                    "string:{}".format(plugin_id), "int32:{}".format(session_id),
                    "int64:{}".format(os.getpid()),
                ])
            except (OSError, subprocess.CalledProcessError) as error:
                sys.stderr.write("SensorFW {} shutdown failed: {}\n".format(plugin_id, error))
        self.sessions = []

    def _poll_sensor(self, plugin_id, interface):
        while not self.stop_event.is_set():
            try:
                path = "/SensorManager/{}".format(plugin_id)
                read_method = SENSOR_SPECS[plugin_id][2]
                output = self._call([path, "{}.{}".format(interface, read_method)])
                values = self._numbers(output.decode("utf-8", "replace"))
                if values:
                    self.callback(self._event(plugin_id, values))
            except (OSError, subprocess.CalledProcessError) as error:
                sys.stderr.write("SensorFW {} read failed: {}\n".format(plugin_id, error))
            self.stop_event.wait(0.2)

    @staticmethod
    def _numbers(output):
        values = []
        for line in output.splitlines():
            match = re.search(r'(?:uint64|int64|uint32|int32|double) ([-+0-9.eE]+)$', line.strip())
            if match:
                values.append(float(match.group(1)) if any(char in match.group(1) for char in ".eE")
                              else int(match.group(1)))
            elif line.strip() == "true":
                values.append(True)
            elif line.strip() == "false":
                values.append(False)
        return values

    @staticmethod
    def _event(plugin_id, raw):
        name = SENSOR_SPECS[plugin_id][0]
        timestamp_us = raw[0] if raw else 0
        data = raw[1:]
        if name in ("accelerometer", "magnetometer") and len(data) >= 3:
            values = {"x": data[0], "y": data[1], "z": data[2], "raw": data}
        elif name == "light" and data:
            values = {"lux": data[0], "raw": data}
        elif name == "orientation" and data:
            values = {"orientation": data[0], "raw": data}
        elif name == "proximity" and data:
            values = {"close": bool(data[0]), "raw": data}
        elif name == "pressure" and data:
            values = {"pressure": data[0], "raw": data}
        elif name == "rotation" and data:
            values = {"rotation": data[0], "raw": data}
        else:
            values = {"raw": data}
        return {"sensor": name, "timestamp_us": timestamp_us, "values": values}


class BridgeController(object):
    def __init__(self, token):
        self.token = token
        self.clients = {}
        self.lock = threading.RLock()
        self.reader = SensorFwReader(self.publish)
        try:
            self.sensor_names = self.reader.available_sensor_names()
        except (OSError, subprocess.CalledProcessError):
            self.sensor_names = []

    def subscribe(self, handler, sensors):
        selected = set(sensor for sensor in sensors if sensor in self.sensor_names)
        with self.lock:
            self.clients[handler] = selected
        if selected:
            thread = threading.Thread(target=self._start_reader_if_needed, name="sensorfw-start")
            thread.daemon = True
            thread.start()
        return sorted(selected)

    def _start_reader_if_needed(self):
        with self.lock:
            needed = any(self.clients.values())
        if not needed:
            return
        self.reader.start()
        with self.lock:
            still_needed = any(self.clients.values())
        if not still_needed:
            self.reader.stop()

    def disconnect(self, handler):
        with self.lock:
            self.clients.pop(handler, None)
            should_stop = not any(self.clients.values())
        if should_stop:
            self.reader.stop()

    def publish(self, event):
        message = {"type": "reading"}
        message.update(event)
        with self.lock:
            recipients = [
                handler for handler, sensors in self.clients.items()
                if event["sensor"] in sensors
            ]
        for handler in recipients:
            try:
                handler.send(message)
            except socket.error:
                self.disconnect(handler)

    def stop(self):
        with self.lock:
            self.clients.clear()
        self.reader.stop()


class SensorRequestHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.buffer = bytearray()
        self.write_lock = threading.Lock()
        self.authenticated = False

    def handle(self):
        try:
            while True:
                message = self.receive()
                if message is None:
                    return
                if not self.authenticated:
                    if (message.get("type") != "hello" or
                            message.get("protocol") != PROTOCOL_VERSION or
                            message.get("token") != self.server.controller.token):
                        self.send({"type": "error", "message": "authentication failed"})
                        return
                    self.authenticated = True
                    self.send({
                        "type": "hello", "protocol": PROTOCOL_VERSION,
                        "sensors": self.server.controller.sensor_names,
                    })
                elif message.get("type") == "subscribe" and isinstance(message.get("sensors"), list):
                    enabled = self.server.controller.subscribe(self, message["sensors"])
                    self.send({"type": "subscribed", "sensors": enabled})
                else:
                    self.send({"type": "error", "message": "expected a subscribe message"})
                    return
        except (ValueError, socket.error):
            return
        finally:
            self.server.controller.disconnect(self)

    def receive(self):
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                if line:
                    return decode(line)
                continue
            chunk = self.request.recv(4096)
            if not chunk:
                return None
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_MESSAGE_BYTES:
                raise ValueError("message exceeds 64 KiB")

    def send(self, message):
        with self.write_lock:
            self.request.sendall(encode(message))


class ThreadedTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish Sailfish SensorFW readings over authenticated TCP.")
    parser.add_argument("--listen", default="127.0.0.1", help="listen address (default: loopback)")
    parser.add_argument("--port", type=int, default=8765, help="TCP port (default: 8765)")
    parser.add_argument("--token", required=True, help="shared secret of at least 16 characters")
    arguments = parser.parse_args(argv)
    if len(arguments.token) < 16:
        parser.error("--token must contain at least 16 characters")
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be from 1 through 65535")
    controller = BridgeController(arguments.token)
    server = ThreadedTcpServer((arguments.listen, arguments.port), SensorRequestHandler)
    server.controller = controller
    stopping = threading.Event()

    def stop(signum, frame):
        del signum, frame
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("Listening on {}:{}".format(arguments.listen, arguments.port))
    thread = threading.Thread(target=server.serve_forever, name="sensor-tcp-server")
    thread.daemon = True
    thread.start()
    try:
        stopping.wait()
    finally:
        server.shutdown()
        server.server_close()
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
