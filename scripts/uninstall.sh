#!/usr/bin/env sh
set -eu

sudo systemctl disable --now sailfish-iio-relay.service sailfish-ssh-tunnel.service sailfish-iio-module.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/sailfish-iio-module.service /etc/systemd/system/sailfish-ssh-tunnel.service /etc/systemd/system/sailfish-iio-relay.service
sudo rm -f /etc/udev/rules.d/99-sailfish-iio.rules
sudo rm -rf /usr/local/lib/sailfish-sensors /usr/src/sailfish-iio-1.0.0
sudo dkms remove -m sailfish-iio -v 1.0.0 --all 2>/dev/null || true
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
echo "Removed the PC IIO bridge. Phone source, service, SSH authorization, and /etc/sailfish-sensors credentials remain for deliberate manual cleanup."
