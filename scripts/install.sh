#!/usr/bin/env sh
# Install the persistent PC IIO bridge and provision its phone connection.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
host=${SAILFISH_HOST:-192.168.0.100}
user=${SAILFISH_USER:-nemo}
temporary_dir=$(mktemp -d)
cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT HUP INT TERM

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this installer as your normal user; it invokes sudo only where required." >&2
    exit 2
fi

"$project_root/scripts/build.sh"
"$project_root/scripts/build-phone-rpm.sh"
set -- "$project_root"/mobile/RPMS/sailfish-sensors-bridge-*.armv7hl.rpm
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Expected exactly one built phone RPM in $project_root/mobile/RPMS" >&2
    exit 1
fi
phone_rpm=$1

umask 077
openssl rand -hex 32 > "$temporary_dir/token"
ssh-keygen -q -t ed25519 -N '' -f "$temporary_dir/id_ed25519"

echo "Authorize the newly generated PC public key on $user@$host (normal SSH password prompt follows)."
cat "$temporary_dir/id_ed25519.pub" | ssh "$user@$host" \
    'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; cat >> ~/.ssh/authorized_keys'

printf '%s\n' "$(cat "$temporary_dir/token")" | ssh "$user@$host" \
    'umask 077; mkdir -p ~/.config/sailfish-sensors; cat > ~/.config/sailfish-sensors/token'
scp "$phone_rpm" "$user@$host:/tmp/sailfish-sensors-bridge.rpm"
ssh -tt "$user@$host" \
    "devel-su /bin/sh -c 'rpm -Uvh --replacepkgs /tmp/sailfish-sensors-bridge.rpm && systemctl enable --now sailfish-sensor-bridge.service'"

sudo groupadd --system sailfish-sensors 2>/dev/null || true
sudo useradd --system --gid sailfish-sensors --home-dir /nonexistent --shell /usr/sbin/nologin sailfish-sensors 2>/dev/null || true
sudo install -d -o root -g sailfish-sensors -m 0750 /etc/sailfish-sensors
sudo install -o sailfish-sensors -g sailfish-sensors -m 0600 "$temporary_dir/token" /etc/sailfish-sensors/token
sudo install -o sailfish-sensors -g sailfish-sensors -m 0600 "$temporary_dir/id_ed25519" /etc/sailfish-sensors/id_ed25519
sudo install -o root -g root -m 0644 "$project_root/config/bridge.conf" /etc/sailfish-sensors/bridge.conf
sudo install -d -o root -g root -m 0755 /usr/local/lib/sailfish-sensors
sudo cp -a "$project_root/pc/sailfish_sensors" /usr/local/lib/sailfish-sensors/
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish-sensorctl.py" /usr/local/lib/sailfish-sensors/sailfish-sensorctl
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish-iio-relay" /usr/local/lib/sailfish-sensors/sailfish-iio-relay
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish_gpsd_feed.py" /usr/local/lib/sailfish-sensors/sailfish_gpsd_feed.py
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish-gpsd-feed" /usr/local/lib/sailfish-sensors/sailfish-gpsd-feed
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish_iio_viewer.py" /usr/local/lib/sailfish-sensors/sailfish_iio_viewer.py
sudo install -o root -g root -m 0755 "$project_root/pc/sailfish-iio-viewer" /usr/local/lib/sailfish-sensors/sailfish-iio-viewer
sudo install -d -o root -g root -m 0755 /usr/src/sailfish-iio-1.0.0
sudo cp -a "$project_root/kernel/." /usr/src/sailfish-iio-1.0.0/
sudo dkms remove -m sailfish-iio -v 1.0.0 --all 2>/dev/null || true
sudo dkms add -m sailfish-iio -v 1.0.0
sudo dkms build -m sailfish-iio -v 1.0.0
sudo dkms install -m sailfish-iio -v 1.0.0
sudo install -o root -g root -m 0644 "$project_root/udev/99-sailfish-iio.rules" /etc/udev/rules.d/99-sailfish-iio.rules
sudo install -o root -g root -m 0644 "$project_root/systemd/sailfish-iio-module.service" /etc/systemd/system/
sudo install -o root -g root -m 0644 "$project_root/systemd/sailfish-ssh-tunnel.service" /etc/systemd/system/
sudo install -o root -g root -m 0644 "$project_root/systemd/sailfish-iio-relay.service" /etc/systemd/system/
sudo install -o root -g root -m 0644 "$project_root/systemd/sailfish-gps-ssh-tunnel.service" /etc/systemd/system/
sudo install -o root -g root -m 0644 "$project_root/systemd/sailfish-gpsd-feed.service" /etc/systemd/system/
sudo udevadm control --reload-rules
sudo systemctl daemon-reload
sudo systemctl enable --now sailfish-iio-module.service sailfish-ssh-tunnel.service sailfish-iio-relay.service sailfish-gps-ssh-tunnel.service sailfish-gpsd-feed.service
"$project_root/scripts/status.sh"
