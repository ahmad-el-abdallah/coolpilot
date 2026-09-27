#!/usr/bin/env bash
# Remove CoolPilot (settings you applied stay until reboot; hardware resets to defaults on reboot).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo ./uninstall.sh"; exit 1; }
systemctl disable --now coolpilot.service coolpilot-boot.service coolpilot-resume.service 2>/dev/null || true
rm -f /etc/systemd/system/coolpilot{,-boot,-resume}.service /etc/udev/rules.d/90-coolpilot.rules
# leftovers from the old name, if any
systemctl disable --now tuf-control.service tuf-control-boot.service tuf-control-resume.service 2>/dev/null || true
rm -f /etc/systemd/system/tuf-control{,-boot,-resume}.service /etc/udev/rules.d/90-tuf-control.rules
rm -rf /opt/tuf-control
udevadm control --reload 2>/dev/null || true   # no udev daemon in containers
systemctl daemon-reload
rm -rf /opt/coolpilot
rm -f /usr/local/bin/coolpilot
USER_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
rm -f "$USER_HOME/.local/share/applications/coolpilot.desktop" "$USER_HOME/.local/share/applications/tuf-control.desktop"
rm -f "$USER_HOME/.local/share/icons/hicolor/256x256/apps/coolpilot.png" "$USER_HOME/.local/share/icons/hicolor/scalable/apps/coolpilot.svg"
read -rp "Also delete saved profiles in /etc/coolpilot? [y/N] " a
[[ $a == [yY] ]] && rm -rf /etc/coolpilot
read -rp "Also delete black-box recordings and history in /var/lib/coolpilot? [y/N] " a
[[ $a == [yY] ]] && rm -rf /var/lib/coolpilot
echo "Removed."
