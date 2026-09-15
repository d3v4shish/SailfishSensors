# Sailfish Sensors Bridge

Sailfish Sensors Bridge makes a Sailfish OS phone's SensorFW readings and
location updates usable on a Linux PC in three forms:

- a mode-0600 Unix socket with the dependency-free Python API; and
- privileged virtual Linux IIO devices named `sailfish-*`, with timestamped
  buffers for direct IIO consumers and channel layouts recognized by
  `iio-sensor-proxy`; and
- a localhost-only NMEA RMC feed that `gpsd` can consume and re-publish to
  normal GPSD clients.

The included desktop IIO Viewer reads those IIO sysfs devices directly. It is a
diagnostic UI, not another relay: it shows raw values, kernel-provided scale,
converted values, sample age, and availability for every virtual sensor.

No physical hardware is emulated. The PC installs a narrow out-of-tree kernel
module that accepts fixed-size local samples only. JSON parsing, phone
authentication, reconnection, unit conversion, and coordinate transforms remain
in an unprivileged PC relay.

This is a SensorFW-to-IIO bridge, not a general phone-hardware virtual machine.
It does not expose a Bluetooth HCI adapter, cellular modem/WWAN interface,
Linux GNSS hardware device, raw satellite/RF interface, camera, microphone,
NFC controller, SIM, or phone control APIs. The optional GPSD feed represents
the phone's valid Qt Positioning fixes; it does not make the PC receive
satellite signals itself.

The native phone publisher uses Qt Sensors/SensorFW and can provide
accelerometer, gyroscope, magnetometer, compass, orientation, light, proximity,
pressure, and rotation readings when the connected Sailfish backend supports
them. The current phone advertised accelerometer, light, magnetometer,
orientation, pressure, proximity, and rotation SensorFW plugins. It does not
claim a gyroscope or compass unless Qt Sensors reports one at runtime.

The same native publisher has a separate Qt Positioning listener. It activates
the phone location source only while its authenticated PC feed is subscribed.
It emits no NMEA sentence until the phone supplies a valid coordinate and UTC
timestamp. The NMEA compatibility stream is RMC-only, so it does not invent
altitude, satellite count, DOP, or the origin of the fix.

The current phone test delivered native proximity and orientation readings into
the PC's virtual IIO devices. Its accelerometer backend activated but a
stationary phone did not produce a fresh motion event; move the phone to test
accelerometer consumers.

The persistent services use SSH tunnels and keep both phone listeners on
`127.0.0.1`; they also require a per-install token. See [BUILD.md](BUILD.md)
for commands and [ARCHITECTURE.md](ARCHITECTURE.md) for interfaces and trust
boundaries.
