#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"
PYTHONPATH="$project_root/pc${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m unittest discover -s tests -v
