#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec env PYTHONPATH="$project_root/pc${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$project_root/pc/sailfish-iio-viewer" "$@"
