"""The narrow, fixed-record interface to the Sailfish IIO kernel module."""

import math
import os
import struct


ABI_VERSION = 1
VALUE_COUNT = 3
RECORD = struct.Struct("<HHHHQiii")

SENSOR_IDS = {
    "accelerometer": 1,
    "gyroscope": 2,
    "magnetometer": 3,
    "compass": 4,
    "orientation": 5,
    "light": 6,
    "proximity": 7,
    "pressure": 8,
    "rotation": 9,
}
VECTOR_SENSORS = frozenset(("accelerometer", "gyroscope", "magnetometer", "rotation"))


class IioError(ValueError):
    """A phone reading cannot be safely converted to an IIO record."""


def parse_mount_matrix(value):
    """Return a nine-element row-major mount matrix, or the identity matrix."""
    if value is None:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    try:
        matrix = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise IioError("mount matrix must contain numeric values") from error
    if len(matrix) != 9 or not all(math.isfinite(item) for item in matrix):
        raise IioError("mount matrix must contain exactly nine finite values")
    return matrix


def _number(values, key):
    try:
        result = float(values[key])
    except (KeyError, TypeError, ValueError) as error:
        raise IioError("missing or invalid {} value".format(key)) from error
    if not math.isfinite(result):
        raise IioError("{} must be finite".format(key))
    return result


def _int32(value):
    result = int(round(value))
    if not -(2 ** 31) <= result < 2 ** 31:
        raise IioError("value is outside the IIO int32 range")
    return result


def _transform_vector(values, matrix):
    x, y, z = (_number(values, name) for name in ("x", "y", "z"))
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z,
        matrix[3] * x + matrix[4] * y + matrix[5] * z,
        matrix[6] * x + matrix[7] * y + matrix[8] * z,
    )


def record_for_reading(reading, matrix=None):
    """Convert one validated JSON reading to the kernel module's binary ABI.

    Values are normalised before packing: acceleration is micro m/s², angular
    quantities are micro rad/s or micro rad, light and pressure are milli SI
    units, and magnetometer values are nano tesla.
    """
    if not isinstance(reading, dict):
        raise IioError("reading must be an object")
    sensor = reading.get("sensor")
    values = reading.get("values")
    timestamp_ns = reading.get("received_ns")
    if sensor not in SENSOR_IDS or not isinstance(values, dict):
        raise IioError("unsupported or malformed sensor reading")
    if not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
        raise IioError("reading has no local monotonic timestamp")
    if matrix is None:
        matrix = parse_mount_matrix(None)

    if sensor == "accelerometer":
        output = tuple(_int32(item * 1000000.0) for item in _transform_vector(values, matrix))
    elif sensor == "gyroscope":
        output = tuple(_int32(math.radians(item) * 1000000.0)
                       for item in _transform_vector(values, matrix))
    elif sensor == "magnetometer":
        output = tuple(_int32(item) for item in _transform_vector(values, matrix))
    elif sensor == "rotation":
        output = tuple(_int32(math.radians(item) * 1000000.0)
                       for item in _transform_vector(values, matrix))
    elif sensor == "compass":
        output = (_int32(math.radians(_number(values, "azimuth")) * 1000000.0), 0, 0)
    elif sensor == "orientation":
        output = (_int32(_number(values, "orientation")), 0, 0)
    elif sensor == "light":
        output = (_int32(_number(values, "lux") * 1000.0), 0, 0)
    elif sensor == "proximity":
        close = values.get("close")
        if not isinstance(close, bool):
            raise IioError("proximity close must be a boolean")
        output = (int(close), 0, 0)
    else:
        output = (_int32(_number(values, "pressure") * 1000.0), 0, 0)

    count = 3 if sensor in VECTOR_SENSORS else 1
    return RECORD.pack(ABI_VERSION, SENSOR_IDS[sensor], count, 0, timestamp_ns, *output)


class IioInjector(object):
    """Write normalised samples to a local Sailfish IIO module."""

    def __init__(self, path, matrix=None):
        self.path = path
        self.matrix = parse_mount_matrix(matrix) if isinstance(matrix, str) or matrix is None else tuple(matrix)
        if len(self.matrix) != 9:
            raise IioError("mount matrix must contain exactly nine values")
        self._fd = None

    def inject(self, reading):
        payload = record_for_reading(reading, self.matrix)
        if self._fd is None:
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CLOEXEC)
        try:
            written = os.write(self._fd, payload)
        except OSError:
            self.close()
            raise
        if written != len(payload):
            raise OSError("short write to {}".format(self.path))

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
