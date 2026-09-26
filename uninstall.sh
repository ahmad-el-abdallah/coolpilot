#!/usr/bin/env bash
# Remove TUF Control (settings you applied stay until reboot; hardware resets to defaults on reboot).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo ./uninstall.sh"; exit 1; }
systemctl disable --now tuf-control.service tuf-control-boot.service tuf-control-resume.service 2>/dev/null || true
rm -f /etc/systemd/system/tuf-control{,-boot,-resume}.service /etc/udev/rules.d/90-tuf-control.rules
udevadm control --reload
systemctl daemon-reload
rm -rf /opt/tuf-control
USER_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
rm -f "$USER_HOME/.local/share/applications/tuf-control.desktop"
read -rp "Also delete saved profiles in /etc/tuf-control? [y/N] " a
[[ $a == [yY] ]] && rm -rf /etc/tuf-control
echo "Removed."
