#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -z "${SFSB_TOKEN:-}" ]; then
    echo "Set SFSB_TOKEN to the shared token before running the GPSD feed." >&2
    exit 2
fi

exec env PYTHONPATH="$project_root/pc${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$project_root/pc/sailfish-gpsd-feed" "$@"
