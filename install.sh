#!/usr/bin/env bash
# Install / update CoolPilot.
#
#   sudo ./install.sh          install or update
#   ./install.sh --check       only check this system (no changes, no root needed)
#
# Works on any systemd distro (Arch, Debian/Ubuntu, Fedora, openSUSE, ...).
set -euo pipefail

SRC=$(cd "$(dirname "$0")" && pwd)
DEST=/opt/coolpilot
PORT=8787
CHECK_ONLY=false
[[ ${1:-} == --check ]] && CHECK_ONLY=true

red() { printf '\e[31m%s\e[0m\n' "$*"; }
yel() { printf '\e[33m%s\e[0m\n' "$*"; }
grn() { printf '\e[32m%s\e[0m\n' "$*"; }
ok() { grn "  ✓ $*"; }
warn() { yel "  ! $*"; }
bad() { red "  ✗ $*"; }

# ------------------------------------------------------------------ who / which distro
if $CHECK_ONLY; then
  USER_NAME=${SUDO_USER:-$(id -un)}
else
  [[ $EUID -eq 0 ]] || { echo "Run with sudo:  sudo ./install.sh     (or ./install.sh --check to only check)"; exit 1; }
  USER_NAME=${SUDO_USER:?run via sudo from your normal user, not from a root shell}
fi
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
USER_GROUP=$(id -gn "$USER_NAME")

DISTRO=unknown
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in
    *" arch "*) DISTRO=arch ;;
    *" debian "*|*" ubuntu "*) DISTRO=debian ;;
    *" fedora "*|*" rhel "*) DISTRO=fedora ;;
    *" suse "*|*" opensuse "*) DISTRO=suse ;;
  esac
fi
hint() {  # hint <what> : distro-specific install command
  case "$DISTRO:$1" in
    arch:python)   echo "sudo pacman -S --needed python" ;;
    debian:python) echo "sudo apt install python3 python3-venv" ;;
    fedora:python) echo "sudo dnf install python3" ;;
    suse:python)   echo "sudo zypper install python311" ;;
    arch:tools)    echo "sudo pacman -S --needed stress-ng glmark2 pciutils" ;;
    debian:tools)  echo "sudo apt install stress-ng glmark2 pciutils" ;;
    fedora:tools)  echo "sudo dnf install stress-ng glmark2 pciutils" ;;
    suse:tools)    echo "sudo zypper install stress-ng glmark2 pciutils" ;;
    *:python)      echo "install Python 3.9+ with the venv module" ;;
    *:tools)       echo "install stress-ng, glmark2 and pciutils with your package manager" ;;
  esac
}

echo "==> Checking this system (${PRETTY_NAME:-unknown distro}, user $USER_NAME)"
FATAL=0

# ------------------------------------------------------------------ systemd
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; then
  ok "systemd"
else
  bad "systemd is required (services for the web panel and boot re-apply)"; FATAL=1
fi

# ------------------------------------------------------------------ Python 3.9+ with venv
PYTHON=
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 /usr/bin/python3; do
  p=$(command -v "$c" 2>/dev/null) || continue
  [[ $p == "$USER_HOME"/* ]] && continue  # the service must not depend on a user-installed Python
  if "$p" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PYTHON=$p; break
  fi
done
if [[ -n $PYTHON ]]; then
  ok "Python $("$PYTHON" -c 'import platform; print(platform.python_version())') ($PYTHON)"
else
  bad "Python 3.9+ with the venv module not found  →  $(hint python)"; FATAL=1
fi

# ------------------------------------------------------------------ Node (optional: UI is prebuilt)
node_ok() {  # node version >= 20.19 (and not 21 / 22.0-22.11), what Vite needs
  "$1" -e 'const [a,b]=process.versions.node.split(".").map(Number);
           process.exit((a==20&&b>=19)||(a==22&&b>=12)||a>=23?0:1)' 2>/dev/null
}
NODE_BIN=
as_user() { if [[ $EUID -eq 0 ]]; then runuser -u "$USER_NAME" -- "$@"; else "$@"; fi; }
for d in $(as_user bash -lc 'dirname "$(command -v node)"' 2>/dev/null) \
         /usr/local/bin /usr/bin \
         $(ls -d "$USER_HOME"/.local/share/mise/installs/node/*/bin "$USER_HOME"/.nvm/versions/node/*/bin \
                 "$USER_HOME"/.local/share/fnm/node-versions/*/installation/bin "$USER_HOME"/.volta/bin \
                 "$USER_HOME"/.asdf/installs/nodejs/*/bin 2>/dev/null | sort -rV); do
  if [[ -x $d/node && -x $d/npm ]] && node_ok "$d/node"; then NODE_BIN=$d; break; fi
done
if [[ -n $NODE_BIN ]]; then
  ok "Node $("$NODE_BIN/node" -v) — the UI will be rebuilt from source"
elif [[ -f $SRC/frontend/dist/index.html ]]; then
  ok "prebuilt UI found (Node 20.19+ only needed if you change the UI)"
else
  bad "no prebuilt UI and no Node 20.19+/22.12+ to build it — install Node from https://nodejs.org"; FATAL=1
fi

# ------------------------------------------------------------------ the laptop
if [[ -e /sys/firmware/acpi/platform_profile ]]; then ok "performance modes (platform_profile)"; else warn "no platform_profile — performance mode switching unavailable"; fi
if [[ -d /sys/class/firmware-attributes/asus-armoury ]]; then ok "ASUS power limits (asus-armoury)"
else warn "no asus-armoury driver — CPU/GPU power limits will show as not supported (needs a newer kernel)"; fi
if grep -qx asus_custom_fan_curve /sys/class/hwmon/hwmon*/name 2>/dev/null; then ok "custom fan curves"; else warn "no ASUS custom fan curves on this kernel/model"; fi
if [[ ! -d /sys/devices/platform/asus-nb-wmi ]]; then warn "asus-nb-wmi not loaded — is this an ASUS laptop?"; fi

# ------------------------------------------------------------------ optional tools
missing=()
for t in stress-ng glmark2 lspci; do command -v "$t" >/dev/null || missing+=("$t"); done
if ((${#missing[@]})); then warn "optional, for crash tests: ${missing[*]}  →  $(hint tools)"; else ok "crash-test tools (stress-ng, glmark2, lspci)"; fi
command -v powerprofilesctl >/dev/null && ok "powerprofilesctl" || warn "powerprofilesctl not found — performance mode is written directly"

# ------------------------------------------------------------------ conflicting tools
for u in asusd tlp auto-cpufreq tuned laptop-mode; do
  if systemctl is-active --quiet "$u" 2>/dev/null; then
    warn "$u is running and manages some of the same settings — it may undo changes (the app will show a warning)"
  fi
done
if [[ ! -d /var/log/journal ]]; then
  warn "journal isn't persistent — crash history only shows the current boot  →  sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald"
fi

if ((FATAL)); then red "==> Can't install until the ✗ items above are fixed."; exit 1; fi
if $CHECK_ONLY; then grn "==> This system can run CoolPilot. Install with:  sudo ./install.sh"; exit 0; fi

# ================================================================== install
if [[ -n $NODE_BIN ]]; then
  echo "==> Building the UI (as $USER_NAME)"
  if ! runuser -u "$USER_NAME" -- env PATH="$NODE_BIN:$PATH" HOME="$USER_HOME" \
       bash -c "cd '$SRC/frontend' && npm install --no-audit --no-fund && npm run build"; then
    [[ -f $SRC/frontend/dist/index.html ]] || { red "UI build failed and no prebuilt UI exists"; exit 1; }
    warn "UI build failed — using the prebuilt one"
  fi
fi

# ------------------------------------------------------------------ migrate from the old name (tuf-control)
if [[ -e /etc/systemd/system/tuf-control.service || -d /opt/tuf-control || -d /etc/tuf-control ]]; then
  echo "==> Migrating from the old name (tuf-control → coolpilot)"
  systemctl disable --now tuf-control.service tuf-control-boot.service tuf-control-resume.service 2>/dev/null || true
  rm -f /etc/systemd/system/tuf-control{,-boot,-resume}.service /etc/udev/rules.d/90-tuf-control.rules
  rm -rf /opt/tuf-control
  rm -f "$USER_HOME/.local/share/applications/tuf-control.desktop"
  if [[ -d /etc/tuf-control && ! -e /etc/coolpilot ]]; then
    mv /etc/tuf-control /etc/coolpilot   # saved profiles, Stability setup, token
  elif [[ -d /etc/tuf-control ]]; then
    warn "/etc/coolpilot already exists — left the old /etc/tuf-control in place (delete it if you don't need it)"
  fi
  systemctl daemon-reload
fi

echo "==> Copying the app to $DEST"
mkdir -p "$DEST/frontend"
rm -rf "$DEST/backend" "$DEST/frontend/dist"
cp -r "$SRC/backend" "$DEST/backend"
rm -rf "$DEST/backend/.venv" "$DEST/backend/tests" "$DEST/backend/.pytest_cache"
find "$DEST/backend" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$SRC/frontend/dist" "$DEST/frontend/dist"

echo "==> Python environment ($PYTHON)"
if [[ ! -x $DEST/venv/bin/python ]] || ! "$DEST/venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  rm -rf "$DEST/venv"
  "$PYTHON" -m venv "$DEST/venv"
fi
"$DEST/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "flask>=3.0" "waitress>=3.0"
command -v restorecon >/dev/null && restorecon -R "$DEST" 2>/dev/null || true   # SELinux (Fedora)

echo "==> Config + token in /etc/coolpilot"
install -d -m 700 /etc/coolpilot
if [[ ! -s /etc/coolpilot/token ]]; then
  (umask 077; "$PYTHON" -c "import secrets; print(secrets.token_urlsafe(32))" > /etc/coolpilot/token)
fi

echo "==> systemd services + udev rule"
SYSTEMCTL=$(command -v systemctl)
for u in coolpilot coolpilot-boot coolpilot-resume; do
  sed "s/@USER@/$USER_NAME/" "$SRC/systemd/$u.service" > "/etc/systemd/system/$u.service"
done
sed "s|/usr/bin/systemctl|$SYSTEMCTL|" "$SRC/systemd/90-coolpilot.rules" > /etc/udev/rules.d/90-coolpilot.rules
udevadm control --reload
systemctl daemon-reload
systemctl enable coolpilot-boot.service coolpilot-resume.service >/dev/null
systemctl enable coolpilot.service >/dev/null
systemctl restart coolpilot.service

echo "==> Crash-test scripts in $USER_HOME/crashdiag (logs there are left alone)"
runuser -u "$USER_NAME" -- mkdir -p "$USER_HOME/crashdiag/logs"
for f in crashdiag.sh pcie-watch.sh; do
  install -m 755 -o "$USER_NAME" -g "$USER_GROUP" "$SRC/crashdiag/$f" "$USER_HOME/crashdiag/$f"
done

echo "==> App launcher entry"
APPS="$USER_HOME/.local/share/applications"
runuser -u "$USER_NAME" -- mkdir -p "$APPS"
cat > "$APPS/coolpilot.desktop" <<DESKTOP
[Desktop Entry]
Name=CoolPilot
Comment=Fans, power, CPU frequency and crash diagnostics for your laptop
Exec=xdg-open http://127.0.0.1:$PORT
Icon=preferences-system
Terminal=false
Type=Application
Categories=System;Settings;
DESKTOP
chown "$USER_NAME:$USER_GROUP" "$APPS/coolpilot.desktop"

up() { "$PYTHON" -c "import urllib.request as u; u.urlopen('http://127.0.0.1:$PORT/', timeout=2)" 2>/dev/null; }
for _ in $(seq 30); do up && break; sleep 0.5; done
if up; then
  grn "✅ CoolPilot is running: http://127.0.0.1:$PORT"
else
  red "⚠️  The service didn't answer yet. Check:  journalctl -u coolpilot -n 50"
fi
