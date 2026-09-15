"""Read the virtual Sailfish IIO devices and present their current values.

The reader accesses IIO sysfs directly.  It never opens the phone socket or
the privileged injection device, so it is safe to use as a desktop diagnostic
while the persistent bridge is running.
"""

import argparse
import math
from pathlib import Path
import time


DEFAULT_SYSFS_ROOT = "/sys/bus/iio/devices"
ORIENTATION_NAMES = {
    0: "undefined",
    1: "top up",
    2: "top down",
    3: "left up",
    4: "right up",
    5: "face up",
    6: "face down",
}


class ChannelSpec(object):
    """One IIO raw/scale channel shown by the viewer."""

    def __init__(self, label, raw_file, scale_file, unit):
        self.label = label
        self.raw_file = raw_file
        self.scale_file = scale_file
        self.unit = unit


class SensorSpec(object):
    """The documented sysfs shape of one virtual Sailfish IIO device."""

    def __init__(self, key, label, channels):
        self.key = key
        self.name = "sailfish-{}".format(key)
        self.label = label
        self.channels = tuple(channels)


SENSOR_SPECS = (
    SensorSpec("accelerometer", "Accelerometer", (
        ChannelSpec("X", "in_accel_x_raw", "in_accel_x_scale", "m/s²"),
        ChannelSpec("Y", "in_accel_y_raw", "in_accel_y_scale", "m/s²"),
        ChannelSpec("Z", "in_accel_z_raw", "in_accel_z_scale", "m/s²"),
    )),
    SensorSpec("gyroscope", "Gyroscope", (
        ChannelSpec("X", "in_anglvel_x_raw", "in_anglvel_x_scale", "rad/s"),
        ChannelSpec("Y", "in_anglvel_y_raw", "in_anglvel_y_scale", "rad/s"),
        ChannelSpec("Z", "in_anglvel_z_raw", "in_anglvel_z_scale", "rad/s"),
    )),
    SensorSpec("magnetometer", "Magnetometer", (
        ChannelSpec("X", "in_magn_x_raw", "in_magn_x_scale", "T"),
        ChannelSpec("Y", "in_magn_y_raw", "in_magn_y_scale", "T"),
        ChannelSpec("Z", "in_magn_z_raw", "in_magn_z_scale", "T"),
    )),
    SensorSpec("compass", "Compass", (
        ChannelSpec("Azimuth", "in_rot_from_north_magnetic_tilt_comp_raw",
                    "in_rot_from_north_magnetic_tilt_comp_scale", "rad"),
    )),
    SensorSpec("orientation", "Orientation", (
        ChannelSpec("Orientation", "in_angl_raw", "in_angl_scale", "Qt orientation enum"),
    )),
    SensorSpec("light", "Light", (
        ChannelSpec("Illuminance", "in_illuminance_raw", "in_illuminance_scale", "lux"),
    )),
    SensorSpec("proximity", "Proximity", (
        ChannelSpec("State", "in_proximity_raw", "in_proximity_scale", "0=far, 1=near"),
    )),
    SensorSpec("pressure", "Pressure", (
        ChannelSpec("Pressure", "in_pressure_raw", "in_pressure_scale", "Pa"),
    )),
    SensorSpec("rotation", "Rotation", (
        ChannelSpec("X", "in_rot_x_raw", "in_rot_x_scale", "rad"),
        ChannelSpec("Y", "in_rot_y_raw", "in_rot_y_scale", "rad"),
        ChannelSpec("Z", "in_rot_z_raw", "in_rot_z_scale", "rad"),
    )),
)


class IioReader(object):
    """Discover and safely read only virtual Sailfish IIO devices."""

    def __init__(self, sysfs_root=DEFAULT_SYSFS_ROOT, monotonic_ns=None, stale_after_ms=5000):
        self.sysfs_root = Path(sysfs_root)
        self.monotonic_ns = monotonic_ns or time.monotonic_ns
        self.stale_after_ms = float(stale_after_ms)

    @staticmethod
    def _read_text(path):
        try:
            return path.read_text(encoding="ascii").strip(), None
        except (OSError, UnicodeError) as error:
            return None, str(error)

    def _devices_by_name(self):
        devices = {}
        try:
            paths = sorted(self.sysfs_root.glob("iio:device*"))
        except OSError:
            return devices
        for path in paths:
            name, error = self._read_text(path / "name")
            if error is None and name and name not in devices:
                devices[name] = path
        return devices

    @staticmethod
    def _integer(text, field):
        try:
            return int(text, 10), None
        except (TypeError, ValueError):
            return None, "{} is not an integer".format(field)

    @staticmethod
    def _number(text, field):
        try:
            value = float(text)
        except (TypeError, ValueError):
            return None, "{} is not a number".format(field)
        if not math.isfinite(value):
            return None, "{} is not finite".format(field)
        return value, None

    def snapshot_all(self):
        """Return one current snapshot for every documented Sailfish sensor."""
        devices = self._devices_by_name()
        now_ns = self.monotonic_ns()
        return [self._snapshot(spec, devices.get(spec.name), now_ns) for spec in SENSOR_SPECS]

    def _snapshot(self, spec, path, now_ns):
        snapshot = {
            "key": spec.key,
            "label": spec.label,
            "name": spec.name,
            "device_path": str(path) if path is not None else None,
            "available": None,
            "timestamp_ns": None,
            "age_ms": None,
            "state": "missing",
            "error": None,
            "channels": [],
        }
        if path is None:
            snapshot["error"] = "virtual IIO device is not registered"
            return snapshot

        available_text, available_error = self._read_text(path / "sailfish_available")
        if available_error is not None:
            snapshot["state"] = "error"
            snapshot["error"] = "cannot read sailfish_available: {}".format(available_error)
            return snapshot
        available, available_error = self._integer(available_text, "sailfish_available")
        if available_error is not None or available not in (0, 1):
            snapshot["state"] = "error"
            snapshot["error"] = available_error or "sailfish_available must be 0 or 1"
            return snapshot
        snapshot["available"] = bool(available)
        if not snapshot["available"]:
            snapshot["state"] = "unavailable"
            snapshot["error"] = "no phone sample received"
            return snapshot

        timestamp_text, timestamp_error = self._read_text(path / "sailfish_timestamp_ns")
        if timestamp_error is None:
            timestamp_ns, timestamp_error = self._integer(timestamp_text, "sailfish_timestamp_ns")
            if timestamp_error is None and timestamp_ns > 0:
                snapshot["timestamp_ns"] = timestamp_ns
                snapshot["age_ms"] = max(0.0, (now_ns - timestamp_ns) / 1000000.0)
        if timestamp_error is not None:
            snapshot["error"] = "cannot read sailfish_timestamp_ns: {}".format(timestamp_error)

        channel_errors = []
        for channel in spec.channels:
            raw_text, raw_error = self._read_text(path / channel.raw_file)
            scale_text, scale_error = self._read_text(path / channel.scale_file)
            raw, raw_parse_error = self._integer(raw_text, channel.raw_file) if raw_error is None else (None, raw_error)
            scale, scale_parse_error = self._number(scale_text, channel.scale_file) if scale_error is None else (None, scale_error)
            error = raw_parse_error or scale_parse_error
            if error is not None:
                channel_errors.append("{}: {}".format(channel.label, error))
            snapshot["channels"].append({
                "label": channel.label,
                "raw": raw,
                "scale": scale,
                "scaled": raw * scale if raw is not None and scale is not None else None,
                "unit": channel.unit,
                "error": error,
            })

        if channel_errors:
            snapshot["state"] = "error"
            snapshot["error"] = "; ".join(channel_errors)
        elif snapshot["timestamp_ns"] is None:
            snapshot["state"] = "error"
            snapshot["error"] = snapshot["error"] or "sample timestamp is missing or invalid"
        elif snapshot["age_ms"] > self.stale_after_ms:
            snapshot["state"] = "stale"
            snapshot["error"] = "last phone sample is {:.1f} ms old".format(snapshot["age_ms"])
        else:
            snapshot["state"] = "ready"
        return snapshot


def format_channel(sensor_key, channel):
    """Render raw, scale, and converted values without hiding IIO details."""
    if channel["error"] is not None:
        return "{}: unavailable ({})".format(channel["label"], channel["error"])
    converted = "{:.8g}".format(channel["scaled"])
    if sensor_key == "orientation":
        converted = "{} ({})".format(converted, ORIENTATION_NAMES.get(channel["raw"], "unknown"))
    elif sensor_key == "proximity":
        converted = "{} ({})".format(converted, "near" if channel["raw"] else "far")
    return "{}: {} {}  [raw={}, scale={:.8g}]".format(
        channel["label"], converted, channel["unit"], channel["raw"], channel["scale"])


def format_snapshot(snapshot):
    """Return a terminal-friendly representation used by ``--once``."""
    lines = ["{} ({})".format(snapshot["label"], snapshot["name"])]
    if snapshot["device_path"] is not None:
        lines.append("  device: {}".format(snapshot["device_path"]))
    lines.append("  status: {}".format(snapshot["state"]))
    if snapshot["available"] is not None:
        lines.append("  available: {}".format("yes" if snapshot["available"] else "no"))
    if snapshot["timestamp_ns"] is not None:
        lines.append("  timestamp: {} ns; age: {:.1f} ms".format(snapshot["timestamp_ns"], snapshot["age_ms"]))
    if snapshot["error"] is not None:
        lines.append("  note: {}".format(snapshot["error"]))
    lines.extend("  {}".format(format_channel(snapshot["key"], channel)) for channel in snapshot["channels"])
    return "\n".join(lines)


def run_gui(reader, interval_ms):
    """Run the GTK4 viewer; GTK is imported only when a GUI is requested."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as error:
        raise RuntimeError("GTK4 PyGObject is required for the GUI: {}".format(error))

    class ViewerApplication(Gtk.Application):
        def __init__(self):
            Gtk.Application.__init__(self, application_id="org.sailfish.SensorsIioViewer")
            self.cards = {}

        def do_activate(self):
            window = Gtk.ApplicationWindow(application=self, title="Sailfish IIO Sensor Viewer")
            window.set_default_size(960, 680)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            outer.set_margin_top(12)
            outer.set_margin_bottom(12)
            outer.set_margin_start(12)
            outer.set_margin_end(12)
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.summary = Gtk.Label(xalign=0)
            self.summary.set_hexpand(True)
            controls.append(self.summary)
            refresh_button = Gtk.Button(label="Refresh now")
            refresh_button.connect("clicked", lambda unused_button: self.refresh())
            controls.append(refresh_button)
            outer.append(controls)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_vexpand(True)
            grid = Gtk.Grid(column_spacing=10, row_spacing=10)
            for index, spec in enumerate(SENSOR_SPECS):
                frame = Gtk.Frame(label=spec.label)
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                content.set_margin_top(8)
                content.set_margin_bottom(8)
                content.set_margin_start(8)
                content.set_margin_end(8)
                status = Gtk.Label(xalign=0)
                device = Gtk.Label(xalign=0)
                sample = Gtk.Label(xalign=0)
                values = Gtk.Label(xalign=0)
                values.set_selectable(True)
                values.set_wrap(True)
                content.append(status)
                content.append(device)
                content.append(sample)
                content.append(values)
                frame.set_child(content)
                grid.attach(frame, index % 3, index // 3, 1, 1)
                self.cards[spec.key] = (status, device, sample, values)
            scrolled.set_child(grid)
            outer.append(scrolled)
            window.set_child(outer)
            self.refresh()
            GLib.timeout_add(interval_ms, self._refresh_timer)
            window.present()

        def _refresh_timer(self):
            self.refresh()
            return True

        def refresh(self):
            snapshots = reader.snapshot_all()
            ready_count = sum(snapshot["state"] == "ready" for snapshot in snapshots)
            self.summary.set_text(
                "IIO sysfs: {} — {} of {} devices have a current sample".format(
                    reader.sysfs_root, ready_count, len(snapshots)))
            for snapshot in snapshots:
                status, device, sample, values = self.cards[snapshot["key"]]
                status.set_text("Status: {}{}".format(
                    snapshot["state"].upper(),
                    " — {}".format(snapshot["error"]) if snapshot["error"] else ""))
                device.set_text("Device: {}".format(snapshot["device_path"] or "not registered"))
                if snapshot["timestamp_ns"] is not None:
                    sample.set_text("Sample: {} ns; age {:.1f} ms".format(
                        snapshot["timestamp_ns"], snapshot["age_ms"]))
                else:
                    sample.set_text("Sample: none")
                values.set_text("\n".join(format_channel(snapshot["key"], channel)
                                            for channel in snapshot["channels"]))

    application = ViewerApplication()
    try:
        return application.run([])
    except KeyboardInterrupt:
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Show live Sailfish virtual IIO readings on the desktop.")
    parser.add_argument("--sysfs-root", default=DEFAULT_SYSFS_ROOT,
                        help="IIO sysfs directory (default: %(default)s)")
    parser.add_argument("--interval-ms", type=int, default=250,
                        help="GUI refresh interval from 100 to 10000 ms (default: %(default)s)")
    parser.add_argument("--stale-after-ms", type=int, default=5000,
                        help="mark an old sample stale after this many ms (default: %(default)s)")
    parser.add_argument("--once", action="store_true",
                        help="print one sysfs snapshot instead of opening a GUI")
    arguments = parser.parse_args(argv)
    if not 100 <= arguments.interval_ms <= 10000:
        parser.error("--interval-ms must be from 100 through 10000")
    if arguments.stale_after_ms < 0:
        parser.error("--stale-after-ms must not be negative")
    reader = IioReader(arguments.sysfs_root, stale_after_ms=arguments.stale_after_ms)
    if arguments.once:
        print("\n\n".join(format_snapshot(snapshot) for snapshot in reader.snapshot_all()))
        return 0
    try:
        return run_gui(reader, arguments.interval_ms)
    except RuntimeError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
