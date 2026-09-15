"""Fixed-input benchmark for the local in-memory reading cache."""

import pathlib
import sys
import tempfile
import time


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "pc"))

from sailfish_sensors.bridge import SensorState
from sailfish_gpsd_feed import rmc_sentence
from sailfish_iio_viewer import IioReader, SENSOR_SPECS


def build_iio_fixture(root):
    """Create a fixed nine-device sysfs-shaped tree for reader benchmarking."""
    for index, sensor in enumerate(SENSOR_SPECS):
        device = root / "iio:device{}".format(index)
        device.mkdir()
        (device / "name").write_text(sensor.name + "\n", encoding="ascii")
        (device / "sailfish_available").write_text("1\n", encoding="ascii")
        (device / "sailfish_timestamp_ns").write_text("1000000000\n", encoding="ascii")
        for channel in sensor.channels:
            (device / channel.raw_file).write_text("1\n", encoding="ascii")
            (device / channel.scale_file).write_text("1\n", encoding="ascii")


def main():
    iterations = 100000
    state = SensorState()
    start = time.perf_counter()
    for index in range(iterations):
        state.update({
            "type": "reading",
            "sensor": "accelerometer",
            "timestamp_us": index,
            "values": {"x": 1.0, "y": -2.0, "z": 9.8},
        })
    elapsed = time.perf_counter() - start
    reading, error = state.get("accelerometer", max_age_ms=10000)
    if error or reading["timestamp_us"] != iterations - 1:
        raise RuntimeError("cache contract failed during benchmark")
    print("updates={} elapsed_seconds={:.6f} updates_per_second={:.0f}".format(
        iterations, elapsed, iterations / elapsed))

    location = {
        "type": "reading",
        "sensor": "location",
        "timestamp_utc_ms": 1704164645678,
        "values": {
            "latitude": 12.345678,
            "longitude": -98.765432,
            "speed_mps": 5.0,
            "bearing_degrees": 123.4,
        },
    }
    start = time.perf_counter()
    for unused_index in range(iterations):
        payload = rmc_sentence(location)
    elapsed = time.perf_counter() - start
    if not payload.startswith(b"$GPRMC,") or not payload.endswith(b"\r\n"):
        raise RuntimeError("NMEA contract failed during benchmark")
    print("nmea_records={} elapsed_seconds={:.6f} records_per_second={:.0f}".format(
        iterations, elapsed, iterations / elapsed))

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = pathlib.Path(temporary_directory)
        build_iio_fixture(root)
        reader = IioReader(root, monotonic_ns=lambda: 2000000000)
        iio_iterations = 1000
        start = time.perf_counter()
        for unused_index in range(iio_iterations):
            snapshots = reader.snapshot_all()
        elapsed = time.perf_counter() - start
        if len(snapshots) != len(SENSOR_SPECS) or any(snapshot["state"] != "ready" for snapshot in snapshots):
            raise RuntimeError("IIO viewer contract failed during benchmark")
        print("iio_snapshots={} elapsed_seconds={:.6f} snapshots_per_second={:.0f}".format(
            iio_iterations, elapsed, iio_iterations / elapsed))


if __name__ == "__main__":
    main()
