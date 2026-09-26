#!/usr/bin/env bash
# Install / update TUF Control.   Usage:  sudo ./install.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo ./install.sh"; exit 1; }
USER_NAME=${SUDO_USER:?run via sudo from your normal user}
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)
DEST=/opt/tuf-control

echo "==> Building frontend (as $USER_NAME)"
# npm usually comes from mise, which isn't on PATH under sudo
NODE_BIN=$(ls -d "$USER_HOME"/.local/share/mise/installs/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
if ! runuser -u "$USER_NAME" -- env PATH="${NODE_BIN:+$NODE_BIN:}$PATH" HOME="$USER_HOME" \
     bash -c "cd '$SRC/frontend' && npm install --no-audit --no-fund && npm run build"; then
  [[ -f $SRC/frontend/dist/index.html ]] || { echo "Frontend build failed and no previous build exists"; exit 1; }
  echo "   (build failed - using the existing frontend/dist)"
fi

echo "==> Copying app to $DEST"
mkdir -p "$DEST/frontend"
rm -rf "$DEST/backend" "$DEST/frontend/dist"
cp -r "$SRC/backend" "$DEST/backend"
rm -rf "$DEST/backend/.venv" "$DEST/backend/tests" "$DEST/backend/.pytest_cache"
find "$DEST/backend" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$SRC/frontend/dist" "$DEST/frontend/dist"

echo "==> Python environment"
[[ -x $DEST/venv/bin/python ]] || /usr/bin/python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --upgrade "flask>=3.0" "waitress>=3.0"

echo "==> Config + token in /etc/tuf-control"
install -d -m 700 /etc/tuf-control
if [[ ! -s /etc/tuf-control/token ]]; then
  (umask 077; /usr/bin/python3 -c "import secrets;print(secrets.token_urlsafe(32))" > /etc/tuf-control/token)
fi

echo "==> systemd units"
for u in tuf-control tuf-control-boot tuf-control-resume; do
  sed "s/@USER@/$USER_NAME/" "$SRC/systemd/$u.service" > "/etc/systemd/system/$u.service"
done
install -m 644 "$SRC/systemd/90-tuf-control.rules" /etc/udev/rules.d/90-tuf-control.rules
udevadm control --reload
systemctl daemon-reload
systemctl enable tuf-control-boot.service tuf-control-resume.service >/dev/null
systemctl enable --now tuf-control.service >/dev/null
systemctl restart tuf-control.service

echo "==> Crash-test scripts in $USER_HOME/crashdiag (logs there are left alone)"
runuser -u "$USER_NAME" -- mkdir -p "$USER_HOME/crashdiag/logs"
for f in crashdiag.sh pcie-watch.sh; do
  install -m 755 -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$SRC/crashdiag/$f" "$USER_HOME/crashdiag/$f"
done

echo "==> App launcher entry"
APPS="$USER_HOME/.local/share/applications"
runuser -u "$USER_NAME" -- mkdir -p "$APPS"
cat > "$APPS/tuf-control.desktop" <<DESKTOP
[Desktop Entry]
Name=TUF Control
Comment=Fans, power, CPU frequency and crash diagnostics for the ASUS TUF A15
Exec=xdg-open http://127.0.0.1:8787
Icon=preferences-system
Terminal=false
Type=Application
Categories=System;Settings;
DESKTOP
chown "$USER_NAME:" "$APPS/tuf-control.desktop"

for _ in $(seq 20); do curl -fsS -o /dev/null http://127.0.0.1:8787/ && break; sleep 0.5; done
if curl -fsS -o /dev/null http://127.0.0.1:8787/; then
  echo "✅ TUF Control is running: http://127.0.0.1:8787"
else
  echo "⚠️  Service didn't answer yet. Check: journalctl -u tuf-control -n 50"
fi
