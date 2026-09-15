# Hotspots

- Phone SensorFW cadence, Wi-Fi quality, and SSH encryption dominate live
  latency; the relay therefore uses PC receipt time rather than comparing
  unsynchronized clocks.
- JSON decoding, unit conversion, matrix multiplication, and the cache lock
  are the relay's per-reading CPU path. The privileged ABI stays binary.
- Unix watchers and IIO buffer readers can apply backpressure; the relay keeps
  one latest value per sensor while IIO uses bounded kfifo buffers.
- iio-sensor-proxy adds a process and D-Bus boundary beyond direct IIO reads.
- Native SensorFW backend rate and power behavior are expected to dominate
  phone battery use at the default 50 Hz motion rate.
- Phone location acquisition and GPSD client demand dominate the optional
  location path. The bridge intentionally requests updates at 1 Hz, performs a
  small fixed RMC conversion, and keeps only the latest complete sentence for
  newly connected localhost clients. No attempt is made to infer or synthesize
  satellite data between genuine phone updates.
- A fixed-input `cProfile` run identifies RMC conversion, coordinate formatting,
  validation, and checksum generation as the CPU path within the feed. Its
  measured capacity is far above 1 Hz, so location acquisition and transport,
  not PC formatting, remain the actionable live hotspots.
- The viewer reads 34 value/scale attributes, 18 availability/timestamp
  attributes, and nine device names per refresh. At its default 250 ms cadence
  that is intentionally a small, read-only sysfs workload; profiling confirms
  file open/read overhead dominates the fixture scan. GTK rendering and actual
  kernel sysfs latency should be profiled on the target desktop before changing
  the refresh cadence.
