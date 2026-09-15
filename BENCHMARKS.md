# Benchmarks

`scripts/benchmark.sh` uses fixed inputs: 100,000 accelerometer messages with
a verified final cache value, 100,000 valid Sailfish location updates with a
verified RMC result, then 1,000 scans of a fixed nine-device IIO sysfs fixture.
It reports host-dependent timing. The latest measurement on Python 3.14.4 and
an AMD Ryzen 7 7800X3D under normal desktop load was:

```text
updates=100000 elapsed_seconds=0.052435 updates_per_second=1907107
nmea_records=100000 elapsed_seconds=0.524145 records_per_second=190787
iio_snapshots=1000 elapsed_seconds=0.589198 snapshots_per_second=1697
```

`python3 -m cProfile -s cumulative tests/benchmark.py` on the same host spent
1.269 of 2.703 seconds in the 100,000 RMC conversions; coordinate formatting,
field validation, and checksum generation were the visible contributors. The
1,000 fake-sysfs scans consumed 1.114 seconds, primarily 61,000 file opens and
reads. The result is about 1,697 snapshots per second, far above the deliberate
4 Hz GUI refresh, so no performance-oriented complexity was added.

The fixture scan exercises only the read-only viewer's discovery, parsing, and
conversion; it does not measure kernel sysfs latency or GTK rendering. This
does not measure the module path, network transport, GNSS acquisition, or GPSD
parsing and is not an IIO or GPS performance claim. Direct buffered-IIO, GPSD,
and iio-sensor-proxy measurements require the privileged module and a connected
phone, so record them as environment-specific observations. Include the
command, kernel, CPU/load, phone connection type, requested rate, sample count,
drops, CPU/memory, and latency percentiles. Do not claim an improvement without
comparable paired measurements.
