import contextlib
import io
import pathlib
import sys
import tempfile
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "pc"))

from sailfish_iio_viewer import IioReader, SENSOR_SPECS, format_channel, main


def write_device(root, number, name, values):
    path = root / "iio:device{}".format(number)
    path.mkdir()
    (path / "name").write_text(name + "\n", encoding="ascii")
    for filename, value in values.items():
        (path / filename).write_text(str(value) + "\n", encoding="ascii")
    return path


class IioViewerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        write_device(self.root, 0, "sailfish-accelerometer", {
            "sailfish_available": 1,
            "sailfish_timestamp_ns": 1500000000,
            "in_accel_x_raw": 1000000,
            "in_accel_x_scale": "0.000001",
            "in_accel_y_raw": -2000000,
            "in_accel_y_scale": "0.000001",
            "in_accel_z_raw": 9800000,
            "in_accel_z_scale": "0.000001",
        })
        write_device(self.root, 1, "sailfish-proximity", {
            "sailfish_available": 0,
            "sailfish_timestamp_ns": 0,
        })
        write_device(self.root, 2, "sailfish-orientation", {
            "sailfish_available": 1,
            "sailfish_timestamp_ns": 1750000000,
            "in_angl_raw": 5,
            "in_angl_scale": 1,
        })
        write_device(self.root, 3, "sailfish-pressure", {
            "sailfish_available": 1,
            "sailfish_timestamp_ns": 1750000000,
            "in_pressure_raw": "bad-data",
            "in_pressure_scale": "0.001",
        })

    def tearDown(self):
        self.directory.cleanup()

    def snapshots(self):
        reader = IioReader(self.root, monotonic_ns=lambda: 2000000000)
        return {snapshot["key"]: snapshot for snapshot in reader.snapshot_all()}

    def test_discovers_known_devices_and_converts_scaled_values(self):
        snapshots = self.snapshots()
        accelerometer = snapshots["accelerometer"]

        self.assertEqual(set(snapshots), {spec.key for spec in SENSOR_SPECS})
        self.assertEqual(accelerometer["state"], "ready")
        self.assertTrue(accelerometer["available"])
        self.assertEqual(accelerometer["device_path"], str(self.root / "iio:device0"))
        self.assertEqual(accelerometer["age_ms"], 500.0)
        self.assertEqual([channel["scaled"] for channel in accelerometer["channels"][:2]], [1.0, -2.0])
        self.assertAlmostEqual(accelerometer["channels"][2]["scaled"], 9.8)

    def test_reports_unavailable_missing_and_malformed_devices_without_crashing(self):
        snapshots = self.snapshots()

        self.assertEqual(snapshots["proximity"]["state"], "unavailable")
        self.assertEqual(snapshots["proximity"]["error"], "no phone sample received")
        self.assertEqual(snapshots["gyroscope"]["state"], "missing")
        self.assertIn("not registered", snapshots["gyroscope"]["error"])
        self.assertEqual(snapshots["pressure"]["state"], "error")
        self.assertIn("in_pressure_raw is not an integer", snapshots["pressure"]["error"])

    def test_marks_an_old_sample_stale_without_hiding_its_value(self):
        reader = IioReader(self.root, monotonic_ns=lambda: 10000000000, stale_after_ms=5000)
        snapshots = {snapshot["key"]: snapshot for snapshot in reader.snapshot_all()}

        self.assertEqual(snapshots["accelerometer"]["state"], "stale")
        self.assertEqual(snapshots["accelerometer"]["channels"][0]["scaled"], 1.0)
        self.assertIn("old", snapshots["accelerometer"]["error"])

    def test_formatting_keeps_raw_scale_and_enum_meaning_visible(self):
        orientation = self.snapshots()["orientation"]
        rendered = format_channel("orientation", orientation["channels"][0])
        self.assertIn("face up", rendered)
        self.assertIn("raw=5", rendered)
        self.assertIn("scale=1", rendered)

    def test_once_mode_is_headless_and_includes_all_known_sensors(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--sysfs-root", str(self.root), "--once"]), 0)
        rendered = output.getvalue()
        self.assertIn("Accelerometer (sailfish-accelerometer)", rendered)
        self.assertIn("Proximity (sailfish-proximity)", rendered)
        self.assertIn("Gyroscope (sailfish-gyroscope)", rendered)


if __name__ == "__main__":
    unittest.main()
