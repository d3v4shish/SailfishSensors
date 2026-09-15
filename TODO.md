# TODO

- [ ] Add a desktop IIO monitor GUI and validation suite.
  - Contract: discover the nine `sailfish-*` IIO devices through sysfs, display
    raw values, IIO scale, converted SI values, availability, device path, and
    PC-monotonic sample freshness without contacting the phone bridge directly.
  - Validation: deterministic fake-sysfs tests cover discovery, unavailable and
    malformed values, scale conversion, and all known channel layouts; a live
    desktop check observes the registered virtual IIO devices.
  - Current result: the GTK4 viewer launched successfully on this desktop; the
    live sysfs read found all nine registered devices. It correctly marked old
    orientation/proximity cache values as stale and the other devices as
    unavailable because the phone bridge was not running. Five deterministic
    viewer tests and the complete 17-test suite pass.
- [ ] Add the authenticated Sailfish location-to-GPSD feed.
  - Contract: only an authenticated PC client can activate the phone's Qt
    Positioning source; valid position updates become NMEA RMC records on a
    PC loopback-only TCP feed for `gpsd`. The feed must not fabricate a
    coordinate, timestamp, satellite count, or local RF receiver.
  - Validation: deterministic NMEA/protocol tests, Sailfish SDK RPM build, and
    an end-to-end `gpsd` TPV observation after the phone obtains a real fix.
  - Current result: the 12 deterministic tests pass; the armv7hl 1.0.0-3 RPM
    builds with Qt Positioning and was installed on the phone. A temporary
    end-to-end SSH test verified the real location listener's authenticated
    hello/subscription protocol, invalid-token rejection, and PC localhost
    NMEA listener. The phone did not yield a valid location in a 10-second
    observation, and `gpsd` is not installed on the PC, so a genuine GPSD TPV
    fix remains to be validated after Location/GNSS is enabled and fixed.
- [ ] Install and validate the native Sailfish Qt SensorFW publisher on the phone.
  - Contract: authenticate a PC client, activate only subscribed working
    backends, acknowledge subscriptions before any native reading, and publish
    readings at the configured rate.
  - Validation: `scripts/build-phone-rpm.sh` builds the exact Sailfish 3.1
    armv7hl RPM, then `scripts/install.sh` installs it and delivers repeated
    fresh accelerometer samples.
  - Current result: the SDK built and the phone upgraded to
    `sailfish-sensors-bridge 1.0.0-3 armv7hl` without phone development
    packages. A temporary token and SSH forward verified ordered protocol
    acknowledgement, invalid-token rejection, and fresh native proximity and
    orientation values at the PC Unix socket and virtual IIO devices. The
    stationary phone accepted an accelerometer subscription but did not emit a
    fresh accelerometer event; repeated motion samples still need a test while
    the phone is moved.
- [ ] Perform privileged PC IIO runtime validation and proxy integration.
  - Contract: the DKMS module creates `sailfish-*` IIO devices, accepts fixed
    records at `/dev/sailfish-iio`, and iio-sensor-proxy observes supported
    accelerometer, light, proximity, and compass channel shapes.
  - Validation: run `scripts/install.sh`, then `scripts/status.sh` and the
    documented raw-IIO and `monitor-sensor` checks.
  - Current result: the module was manually loaded with its `industrialio` and
    `kfifo_buf` dependencies; all nine virtual IIO devices registered. Fresh
    phone proximity and orientation readings updated raw values, availability,
    and timestamps. The proxy udev override is ready but still needs installing
    and a `monitor-sensor` validation; accelerometer validation requires phone
    movement.
- [x] Implement the PC relay and local sensor API.
  - Contract: reconnect to the phone, retain the most recent reading, and
    expose snapshot and streaming requests over a Unix socket.
  - Validation: deterministic protocol and reconnect unit tests.
- [x] Provide reproducible build, run, deployment, test, and benchmark
  commands and concise operational documentation.
  - Contract: a clean checkout needs Python 3 on the PC; the runtime publisher
    uses the phone's installed SensorFW D-Bus tools.
  - Validation: run `scripts/test.sh` and `scripts/benchmark.sh`.
- [x] Implement the PC IIO injection path and persistent service definitions.
  - Contract: an unprivileged relay converts authenticated readings into bounded
    binary records; the kernel module owns virtual IIO channels.
  - Validation: module compilation and fixed record/unit tests pass locally.
