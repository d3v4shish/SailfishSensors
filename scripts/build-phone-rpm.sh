#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
sfdk=${SFDK:-/home/d3v/SailfishOS/bin/sfdk}
target=${SAILFISH_SDK_TARGET:-SailfishOS-3.1.0.12-armv7hl}

if [ ! -x "$sfdk" ]; then
    echo "Sailfish SDK command not found: $sfdk" >&2
    exit 2
fi

exec "$sfdk" -C "$project_root/mobile" -c target="$target" build
