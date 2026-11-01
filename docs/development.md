# Development

```bash
# backend tests (use a fake sysfs tree - safe to run anywhere)
cd backend && uv run pytest          # or: python -m venv .venv && pip install flask waitress pytest

# frontend type-check and build (commit frontend/dist too - it's the prebuilt UI)
cd frontend && npm install && npm run lint && npm run build

# run the UI with hot reload against a running backend
cd frontend && VITE_COOLPILOT_TOKEN=$(sudo cat /etc/coolpilot/token) npm run dev
```

Running the backend as a normal user works for reading (writes fail with "permission denied"):

```bash
cd backend && COOLPILOT_PORT=8788 COOLPILOT_CONFIG_DIR=/tmp/coolpilot-conf uv run python -m coolpilot
```

**Demo server** — the whole app with example data (a month of history, crashes, alerts), no hardware
needed and nothing read from or written to your machine. Used for the screenshots above:

```bash
cd frontend && npm run build && cd ../backend && uv run python tests/demo_server.py   # http://127.0.0.1:8790
```

**Continuous integration** — every push runs the backend tests on Python 3.9–3.14, type-checks and
builds the UI (and checks the committed `frontend/dist` matches), runs shellcheck, and installs,
uses and uninstalls CoolPilot in systemd containers of Ubuntu, Debian, Fedora, openSUSE and Arch
(`ci/distro-test.sh`).

## How it works

Everything is plain Linux sysfs — no vendor tools required:

| Setting | Interface |
|---|---|
| Performance mode | `/sys/firmware/acpi/platform_profile` (via `powerprofilesctl` when available) |
| CPU boost / frequency / EPP | `/sys/devices/system/cpu/cpu*/cpufreq/` (amd-pstate, acpi-cpufreq or intel_pstate) |
| CPU & GPU power limits | `/sys/class/firmware-attributes/asus-armoury/attributes/` |
| Fan curves | hwmon `asus_custom_fan_curve` (`pwmN_auto_pointM_{temp,pwm}`, `pwmN_enable`) |
| Charge limit | `/sys/class/power_supply/BAT*/charge_control_end_threshold` |
| PCIe errors | `/sys/bus/pci/devices/*/aer_dev_{correctable,nonfatal,fatal}` |

Saved state lives in `/etc/coolpilot/` (`config.json`, `profiles.json`, `stability.json`, `token`).
Black-box recordings and history live in `/var/lib/coolpilot/blackbox.db` (SQLite, every reading
committed with `synchronous=FULL`): raw 2-second readings for 3 days, per-minute history for a year,
and the last 2 minutes before every crash forever — a few MB in total.

## Project layout

```
backend/            Flask API (Python)
  coolpilot/app.py          routes + serving the built UI
  coolpilot/security.py     host check + token
  coolpilot/hw/             sysfs settings whitelist, sensors, fans, GPU, PCIe
  coolpilot/profiles.py     profiles, apply order, boot / resume re-apply, factory reset
  coolpilot/stability.py    configurable Stability mode
  coolpilot/fanmode.py      Default / Stability / Custom fan mode that sticks
  coolpilot/diag.py         crash tests + crash history
  coolpilot/blackbox.py     always-on recorder, history store, crash capture
  coolpilot/report.py       repair report
  coolpilot/alerts.py       desktop alerts (checks + delivery to each user's desktop)
  coolpilot/backup.py       settings export / import
  tests/              pytest suite against a fake sysfs tree, plus demo_server.py
frontend/           React + TypeScript + Vite UI
crashdiag/          standalone crash-test script
systemd/            service units + udev rule
ci/                 install test on Ubuntu, Debian, Fedora, openSUSE and Arch (used by GitHub Actions)
install.sh / uninstall.sh
```
