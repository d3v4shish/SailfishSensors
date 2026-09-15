#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
host=${SAILFISH_HOST:-192.168.0.100}
user=${SAILFISH_USER:-nemo}
remote_dir=${SAILFISH_REMOTE_DIR:-/home/nemo/sailfish-sensors-bridge}
target="$user@$host"

ssh "$target" "mkdir -p '$remote_dir'"
rsync -a --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    "$project_root/mobile/" "$target:$remote_dir/mobile/"
ssh "$target" "chmod 700 '$remote_dir/mobile/sailfish-sensor-bridge.py'"

echo "Deployed the native publisher, legacy fallback, and phone systemd unit under $remote_dir/mobile on $target"
echo "After installing the Qt development packages, build with: cd $remote_dir/mobile && qmake sailfish-sensor-bridge.pro && make"
