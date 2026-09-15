# Build and operate

## Prerequisites

The PC needs Python 3, GNU make, kernel headers matching `uname -r`, DKMS,
OpenSSH, OpenSSL, `sudo`, and Sailfish SDK. Install `gpsd` and `gpspipe` only
when using the optional GPSD interface. The inspected PC has the exact
`SailfishOS-3.1.0.12-armv7hl` SDK target used for this phone. The phone needs
Developer Mode, SensorFW, Qt Positioning at runtime, and normal SSH access; it
does not need Qt development packages.

## Deterministic local validation

```sh
scripts/build.sh
scripts/test.sh
scripts/benchmark.sh
```

`build.sh` compiles Python and the kernel module against the running kernel.
`test.sh` uses fixed local fixtures only. `benchmark.sh` updates the cache with
100,000 fixed readings, checks the final value, then prints host timing. None
contacts the phone or needs a token.

## Persistent IIO installation

Run as the normal PC user:

```sh
scripts/install.sh
```

The script cross-builds an armv7hl RPM against Sailfish OS 3.1, then
interactively requests normal SSH, phone `devel-su`, and PC `sudo`
authentication. It installs the native phone publisher RPM, creates a
per-install SSH key and token without printing them, installs the DKMS module,
creates the restricted PC service account, and enables the module, sensor
tunnel/relay, and location tunnel/NMEA feed at boot. Defaults target
`nemo@192.168.0.100`; override with
`SAILFISH_HOST` and `SAILFISH_USER` before running it.

After a successful persistent install, use standard desktop sensor clients:

```sh
monitor-sensor --proximity
monitor-sensor --accel   # move the phone to create an accelerometer event
```

The direct PC API is intentionally private to the relay account. Inspect its
latest values without widening socket permissions with:

```sh
sudo -u sailfish-sensors /usr/local/lib/sailfish-sensors/sailfish-sensorctl \
  --socket /run/sailfish-sensors/sensors.sock sensors
```

To build only the phone package without touching the phone:

```sh
scripts/build-phone-rpm.sh
```

It writes `mobile/RPMS/sailfish-sensors-bridge-1.0.0-3.armv7hl.rpm`.

Use `scripts/status.sh` for service state. `scripts/uninstall.sh` removes PC
services, code, and module but leaves phone state and credentials for explicit
review. Configure the row-major 3×3 mount matrix in
`/etc/sailfish-sensors/bridge.conf` and restart `sailfish-iio-relay.service`.

## GPSD location interface

After `scripts/install.sh`, `sailfish-gpsd-feed.service` authenticates through
its own SSH loopback forward and listens only at `127.0.0.1:2948`. It will
stay silent until the Sailfish location source returns a valid position and UTC
timestamp. Enable the phone's Location/GNSS service and obtain a fix before
testing; an indoor phone may need a clear sky view.

Start GPSD with the bridge's local NMEA TCP source. Do this only when port 2947
is not already owned by another GPSD instance; do not stop an existing GPSD
that serves another receiver unless that is intentional.

```sh
sudo gpsd -N -S 2947 tcp://127.0.0.1:2948
# In a second terminal after the phone has a fix:
gpspipe -w -n 5 | sed -n '/"class":"TPV"/p'
```

`gpsd` treats a `tcp://` source as serial-device data, then provides its usual
local GPSD protocol on port 2947. Desktop GPSD clients such as `cgps`,
`gpspipe`, and applications using `libgps` can use that endpoint. The source
is the phone's position update, not the PC's local GNSS hardware; the bridge
does not provide satellite information, raw NMEA from the receiver, or a
kernel `/dev/tty*` GPS device.

For development without persistent services, use the same authenticated
location listener and a non-conflicting local port:

```sh
export SFSB_TOKEN='a-token-with-at-least-16-characters'
scripts/run-gpsd-feed.sh --host 127.0.0.1 --port 8766 --listen-port 2948
```

## Manual development driver load

When testing the uninstalled module directly, load its IIO dependencies first;
`insmod` does not resolve them:

```sh
scripts/build.sh
sudo modprobe industrialio
sudo modprobe kfifo_buf
sudo insmod "$PWD/kernel/sailfish_iio.ko"
```

The persistent DKMS service uses `modprobe sailfish_iio`, which resolves these
dependencies automatically.

The installed `udev/99-sailfish-iio.rules` also makes `iio-sensor-proxy` use
raw polling for the injected accelerometer and proximity devices. Reload rules
and restart the proxy after installing or changing that file.

## Raw IIO and desktop consumers

```sh
for d in /sys/bus/iio/devices/iio:device*; do
  printf '%s ' "$d"; cat "$d/name"
done
monitor-sensor --accel
```

Each sensor type has a stable logical IIO device. `sailfish_available` changes
to `1` after its first phone sample. The module never creates readings while
disconnected. `iio-sensor-proxy` recognizes accelerometer, light, proximity,
and tilt-compensated compass layouts; other sensor types remain direct IIO
devices.

## Desktop IIO Viewer

Run this in the logged-in graphical desktop session after the module is loaded:

```sh
scripts/run-iio-viewer.sh
```

The GTK4 window discovers the nine `sailfish-*` devices from
`/sys/bus/iio/devices`, refreshes every 250 ms, and shows the actual IIO raw
value, scale, converted SI value, timestamp, and sample age. `READY` means a
sample arrived recently; `STALE` retains and displays the last sample but makes
its age explicit; `UNAVAILABLE` means the kernel has not received a phone
sample. It has no phone credentials and never opens `/dev/sailfish-iio`.

For an SSH terminal, CI, or a quick read-only check without a GUI:

```sh
scripts/run-iio-viewer.sh --once
```

Use `--stale-after-ms 10000` to choose the stale threshold and
`--sysfs-root PATH` only for fixture-based testing.

## User-space API

The existing socket API still works without the kernel module:

```sh
export SFSB_TOKEN='a-token-with-at-least-16-characters'
scripts/run.sh --host 127.0.0.1 --port 8765 --sensors accelerometer
PYTHONPATH=pc python3 pc/sailfish-sensorctl.py get accelerometer
```

Keep the phone listeners on loopback and use the SSH forwarding parameters in
`systemd/sailfish-ssh-tunnel.service` and
`systemd/sailfish-gps-ssh-tunnel.service` when operating manually.
