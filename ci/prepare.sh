#!/usr/bin/env bash
# Install systemd plus what install.sh expects on a normal desktop system.
set -euo pipefail
. /etc/os-release
case " ${ID} ${ID_LIKE:-} " in
  *" arch "*)
    pacman -Syu --noconfirm --needed systemd python util-linux procps-ng ;;
  *" debian "*|*" ubuntu "*)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends systemd systemd-sysv python3 python3-venv udev procps ca-certificates ;;
  *" fedora "*|*" rhel "*)
    dnf install -y -q systemd python3 procps-ng util-linux passwd ;;
  *" suse "*|*" opensuse "*)
    zypper -n -q in systemd python3 procps util-linux udev shadow ;;
  *) echo "unknown distro $ID"; exit 1 ;;
esac
# no logins or getty in the container
systemctl mask getty@tty1.service console-getty.service systemd-logind.service 2>/dev/null || true
useradd -m tester
