#!/usr/bin/env sh
# Observe desktop-facing sensor events after the persistent bridge is running.
set -eu

echo "Move the phone now; this exits after the first accelerometer event or 20 seconds."
timeout 20 monitor-sensor --accel
