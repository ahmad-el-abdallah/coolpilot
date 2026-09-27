#!/usr/bin/env bash
# Runs inside a systemd container (CI): install CoolPilot like a user would, use it, remove it.
#   docker exec <container> bash /src/ci/distro-test.sh
set -euo pipefail
cd /src
step() { printf '\n==== %s\n' "$*"; }

step "waiting for systemd"
for _ in $(seq 60); do
  s=$(systemctl is-system-running 2>/dev/null || true)
  [[ $s == running || $s == degraded ]] && break
  sleep 1
done
echo "systemd: $s"

step "install.sh --check"
./install.sh --check

step "install"
SUDO_USER=tester ./install.sh
systemctl is-active coolpilot.service
systemctl is-enabled coolpilot-boot.service coolpilot-resume.service

step "the web app answers, and only with the token"
python3 - <<'PY'
import re, urllib.error, urllib.request
page = urllib.request.urlopen("http://127.0.0.1:8787/", timeout=5).read().decode()
token = re.search(r'name="coolpilot-token" content="([^"]+)"', page).group(1)
try:
    urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=5)
    raise SystemExit("API answered without a token")
except urllib.error.HTTPError as e:
    assert e.code == 403, e.code
req = urllib.request.Request("http://127.0.0.1:8787/api/settings", headers={"X-CoolPilot-Token": token})
print(len(urllib.request.urlopen(req, timeout=5).read()), "bytes of settings")
PY

step "coolpilot command"
coolpilot status
coolpilot alerts
coolpilot export /tmp/settings.json
python3 -c 'import json; d = json.load(open("/tmp/settings.json")); assert d["format"] == "coolpilot-backup", d'
coolpilot import /tmp/settings.json
coolpilot alerts off && coolpilot alerts on
if coolpilot alerts test; then echo "a test alert can't be shown without a desktop"; exit 1; fi

step "the boot service runs"
systemctl start coolpilot-boot.service || true   # nothing to apply on a fresh install
journalctl -u coolpilot-boot.service -n 5 --no-pager || true

step "uninstall"
printf 'n\nn\n' | ./uninstall.sh
if systemctl is-active --quiet coolpilot.service; then echo "service still running"; exit 1; fi
[[ ! -e /usr/local/bin/coolpilot && ! -d /opt/coolpilot ]]
[[ -d /etc/coolpilot ]]    # kept: the user said N
echo "OK"
