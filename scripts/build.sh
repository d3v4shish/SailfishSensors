#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHONPATH="$project_root/pc${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m compileall -q "$project_root/pc"
make -C "$project_root/kernel"
echo "PC Python sources and the Sailfish IIO module compiled successfully."
