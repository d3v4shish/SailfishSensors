# Architecture

## Data flow

```text
Sailfish SensorFW -> native Qt Sensors publisher RPM (phone loopback)
       -> authenticated JSON over SSH local forward
       -> unprivileged PC relay/cache -> private Unix socket -> applications
                                     -> fixed binary ABI -> sailfish_iio module
                                                          -> IIO sysfs/buffers
                                                          -> iio-sensor-proxy

Sailfish Qt Positioning -> native location listener (phone loopback)
       -> authenticated JSON over a separate SSH local forward
       -> unprivileged NMEA RMC feed at 127.0.0.1:2948
       -> gpsd tcp:// source -> standard GPSD clients on port 2947

Desktop IIO Viewer -> read-only IIO sysfs (`sailfish-*`) -> diagnostic GUI
```

The phone starts a backend only while an authenticated relay subscribes. The
relay reconnects, stamps readings with its own monotonic clock, normalizes
units, applies the configured mount matrix to vectors, updates the socket cache,
and writes a bounded record to `/dev/sailfish-iio`. The module never parses
network data or JSON.

The location source is also activated only while its authenticated feed is
subscribed. It accepts only a valid coordinate plus its UTC timestamp. The PC
validates the JSON again and emits a checksum-protected RMC record. It does not
make a Linux GNSS device or simulate RF/satellite reception: `gpsd` presents a
compatible location-service interface for the phone-originated fix.

## Interfaces

The phone protocol remains newline-delimited JSON protocol 1: authenticated
`hello`, `subscribe` acknowledgement, then reading events. The native
publisher queues that acknowledgement before activating a backend because a
backend may emit synchronously; the PC relay also accepts a pre-ack reading
from an older publisher. The Unix socket retains `sensors`, `get`, and `watch`.

The location listener uses the same protocol and token on a different phone
loopback port. Its `hello` and `subscribe` messages name only `location`.
Location readings contain `timestamp_utc_ms`, latitude, longitude, and only
the optional accuracy, speed, bearing, vertical speed, or altitude values that
Qt Positioning provided. The PC feed accepts no control messages and binds only
to IPv4 localhost. It emits only NMEA RMC because this is sufficient for GPSD
and avoids inventing satellite, DOP, or altitude values.

The viewer does not contact the relay, phone, Unix socket, or injection node.
It periodically reads the module's public IIO sysfs attributes: availability,
PC-monotonic timestamp, each raw channel, and each scale. Its values therefore
reflect exactly what a standard IIO sysfs consumer sees. It marks samples stale
after five seconds by default rather than conflating a retained old value with
a fresh phone update.

`kernel/uapi/sailfish_iio.h` defines ABI v1: a packed 28-byte record with
sensor ID, count, PC monotonic timestamp, and three signed 32-bit raw values.
One IIO device is registered for each logical sensor. Devices expose raw and
scale attributes, a software buffer, `sailfish_available`, and
`sailfish_timestamp_ns`.

Acceleration is micro m/s²; angular velocity and angle are micro rad/s and
micro rad; light and pressure are milli lux and milli pascal; magnetometer is
nano tesla; proximity is 0/1; orientation is the Qt orientation enum. The IIO
scale file provides conversion. PC receipt time is used for IIO timestamps
because the phone and PC monotonic clocks are unrelated.

## Concurrency and security

Qt owns phone sensor/socket events. The relay has a phone thread, Unix server
thread, per-client threads, and a condition-protected cache. The separate
location feed has one reconnecting phone thread, a localhost TCP server thread,
and one handler thread per GPSD source connection. The module locks each device
cache and pushes a sample synchronously to its IIO kfifo.

The native publisher is cross-built as an armv7hl RPM against the matching
Sailfish SDK target, so phone development headers are never installed. The
installer creates an SSH key and token per installation. Traffic is
loopback-only on the phone and encrypted through SSH. The relay and location
feed run as an unprivileged system account; the location NMEA feed is restricted
to `127.0.0.1`, while udev grants the relay account group access only to the
module injection node. A late udev rule selects iio-sensor-proxy's raw-poll
accelerometer and proximity paths; the bridge injects records directly and
does not require a hardware IIO trigger. DKMS, module loading, and service
setup require PC administrator rights. Passwords are never stored.
