"""Client API for programs on the PC."""

import socket

from .protocol import JsonLineSocket, ProtocolError


class SensorError(RuntimeError):
    """The local bridge could not fulfil a sensor request."""


class SensorClient(object):
    """Read Sailfish sensors as local values through a Unix-domain socket.

    The bridge keeps the latest reading for each subscribed phone sensor.  A
    reading is returned as a dictionary containing ``sensor``,
    ``timestamp_us``, ``received_ns``, and sensor-specific ``values``.
    """

    def __init__(self, path, timeout=2.0):
        self.path = path
        self.timeout = timeout

    def sensors(self):
        """Return sensors reported by the connected phone."""
        response = self._request({"op": "sensors"})
        return response["sensors"]

    def get(self, sensor, max_age_ms=1000):
        """Return a fresh reading or raise :class:`SensorError`."""
        response = self._request({
            "op": "get",
            "sensor": sensor,
            "max_age_ms": max_age_ms,
        })
        return response["reading"]

    def watch(self, sensors=None):
        """Yield future readings until the caller stops iterating.

        ``sensors`` is an optional iterable of sensor names.  The iterable
        holds one local socket open; use it in a ``for`` loop or close its
        generator explicitly.
        """
        request = {"op": "watch"}
        if sensors is not None:
            request["sensors"] = list(sensors)
        sock = self._connect()
        stream = JsonLineSocket(sock)
        try:
            stream.send(request)
            response = stream.receive()
            self._require_ok(response)
            while True:
                response = stream.receive()
                self._require_ok(response)
                if response.get("type") == "reading":
                    yield response["reading"]
        finally:
            sock.close()

    def _request(self, request):
        sock = self._connect()
        try:
            stream = JsonLineSocket(sock)
            stream.send(request)
            response = stream.receive()
            self._require_ok(response)
            return response
        except (ConnectionError, OSError, ProtocolError) as error:
            raise SensorError("local bridge is unavailable: {}".format(error))
        finally:
            sock.close()

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except OSError as error:
            sock.close()
            raise SensorError("cannot connect to {}: {}".format(self.path, error))
        return sock

    @staticmethod
    def _require_ok(response):
        if response.get("ok") is not True:
            raise SensorError(response.get("error", "invalid response from local bridge"))
