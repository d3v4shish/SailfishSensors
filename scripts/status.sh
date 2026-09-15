#!/usr/bin/env sh
set -eu

systemctl --no-pager --full status sailfish-iio-module.service sailfish-ssh-tunnel.service sailfish-iio-relay.service sailfish-gps-ssh-tunnel.service sailfish-gpsd-feed.service
printf '%s\n' '--- virtual IIO devices ---'
find /sys/bus/iio/devices -maxdepth 1 -type l -printf '%f ' 2>/dev/null | sort
printf '\n'
