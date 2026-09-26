# TUF Control

A local web control panel for **ASUS TUF Gaming laptops on Linux**: power modes, CPU frequency
and power limits, GPU limits, fan curves, battery charge limit, profiles that survive reboots,
and built-in tools for diagnosing random freezes / resets.

It was built for an ASUS TUF A15 (FA507NVR, Ryzen 7 7435HS, RTX 4060) with an intermittent
hardware fault (freezes when moved under load), so it also includes a configurable
**Stability mode** that keeps the machine cool and steady to reduce freezes until it is repaired.

> ⚠️ This tool changes firmware power limits and fan curves. Everything is range-checked
> against what the firmware allows, but use it at your own risk.

---

## Features

| Page | What you can do |
|---|---|
| **Dashboard** | Live CPU / GPU / RAM / SSD temperatures, clocks, fans, battery draw. One-click Stability mode and performance modes. **Reset everything to default** (first-boot state). |
| **Stability mode** | Pick exactly which protections to use (silent mode, no boost, GHz cap, CPU/GPU power limits, charge limit, cooler fans). Each one explains what it helps and what it costs. Presets: Light / Recommended / Maximum. Applies live, restores your previous values when turned off. |
| **Power & CPU** | Silent / Balanced / Turbo, CPU boost on/off, max & min CPU frequency (GHz), energy preference, CPU power limits PL1/PL2/PL3. |
| **Fans** | Fan mode buttons: **Default** (factory) or **Stability** (steady, never-stop curve). Drag-and-drop 8-point curve editor per fan. The choice sticks across reboot, sleep and mode changes. |
| **GPU** | NVIDIA Dynamic Boost and temperature target, live GPU stats (doesn't wake a sleeping GPU). |
| **Battery & Display** | Charge limit (e.g. 80%), battery health, screen brightness, keyboard backlight, panel overdrive. |
| **Profiles** | Save everything as a named profile, apply it, or set one to apply at every boot. |
| **Crash diagnostics** | Run CPU / RAM / GPU / SSD / idle stress tests while logging sensors to disk every 0.5 s (the log survives a freeze), mark which area of the laptop you're pressing, read the crash report, see which past sessions ended in a crash. |
| **PCIe link health** | Error counters for every PCIe link and a live "press test" for the CPU ↔ GPU link: press areas of the laptop and see if corrected errors jump. |
| **System** | Model, serial, BIOS, kernel, and your warranty info (entered by you, stored locally). |

`Ctrl+K` (or `/`) opens a search over every page, setting and action.

---

## Requirements

- **Linux with systemd** (developed on Arch Linux; any distro with a recent kernel should work)
- **An ASUS TUF / ROG laptop** using the kernel's `asus-wmi` / `asus-armoury` drivers.
  Anything your kernel doesn't expose is simply shown as "not supported".
- **Python 3.11+** with `venv` (system Python at `/usr/bin/python3`)
- **Node.js 20.19+ or 22.12+** and **npm** (only to build the web UI)

Optional, for extra features:

| Package | Used for |
|---|---|
| `power-profiles-daemon` | switching Silent / Balanced / Turbo without fighting the daemon |
| `stress-ng` | CPU / RAM / SSD crash tests |
| `glmark2` | GPU crash test and PCIe press test load |
| `nvidia-utils` (`nvidia-smi`) | GPU temperature / power / clock readings |
| `pciutils` (`lspci`) | device names on the PCIe page |

On Arch:

```bash
sudo pacman -S --needed python nodejs npm stress-ng glmark2 pciutils power-profiles-daemon
```

---

## Install

```bash
git clone https://github.com/ahmad-el-abdallah/tuf-control.git
cd tuf-control
sudo ./install.sh
```

Then open **http://127.0.0.1:8787** — or launch **TUF Control** from your app launcher.

`install.sh` will:

1. build the React UI (as your normal user; it also finds Node installed through `mise`),
2. copy the app to `/opt/tuf-control` and create a Python venv with Flask + Waitress,
3. create a secret API token in `/etc/tuf-control/token`,
4. install and enable the systemd services:
   - `tuf-control` — the web panel (port **8787**, localhost only)
   - `tuf-control-boot` — re-applies your "apply at boot" profile and fan mode after boot
   - `tuf-control-resume` — re-applies after sleep and when the charger is plugged / unplugged
     (via a udev rule; ASUS firmware swaps its power-limit tables then)
5. copy the crash-test scripts to `~/crashdiag` (existing logs are left alone),
6. add a "TUF Control" launcher entry.

### Update

Pull the new code and run the installer again — restarting the service alone does **not**
pick up new code, because the service runs the copy in `/opt/tuf-control`:

```bash
git pull
sudo ./install.sh
```

### Uninstall

```bash
sudo ./uninstall.sh
```

Removes the services, udev rule, `/opt/tuf-control` and the launcher entry, and asks whether to
delete your saved profiles in `/etc/tuf-control`. Hardware settings return to firmware defaults
on the next reboot (or use **Dashboard → Reset everything to default** first).

---

## Usage tips

- **Stability mode** is a workaround, not a fix: less heat and current means less flexing of the
  board and its solder joints. Turn it on from the Dashboard or configure it on its own page;
  enable *Turn on at every boot* to keep it.
- **On battery** the firmware lowers limits (e.g. GPU Dynamic Boost is locked to 0 W, CPU
  limits max 65 W instead of 80 W). The app fits your values into the current range and shows
  locked settings as 🔒 instead of failing.
- **Crash tests:** start a test on *Crash diagnostics*, wait ~2 minutes, then press or lift
  the laptop while clicking the matching area on the screen. After a freeze and reboot, open
  *Crash report* — the last log lines show the moment it died.
- The crash-test scripts also work without the web app:

  ```bash
  ~/crashdiag/crashdiag.sh test cpu 15     # idle | cpu | ram | gpu | disk | all
  ~/crashdiag/crashdiag.sh report          # after a crash + reboot
  ~/crashdiag/pcie-watch.sh                # live CPU<->GPU PCIe error counter
  ```

---

## Security

The backend must run as **root** to write to sysfs, so it is locked down:

- listens on **127.0.0.1 only**;
- rejects any request whose `Host` header isn't `127.0.0.1:8787` / `localhost:8787`
  (blocks DNS-rebinding attacks from web pages);
- every `/api` call needs the secret `X-TUF-Token` header — the token is only embedded in the
  page the server itself serves, so other websites can't read it or call the API;
- only a **whitelist of settings** can be written, each validated against its type and the
  firmware's live min/max (`backend/tuf/hw/sysfs.py`); nothing accepts raw file paths;
- crash tests run as your normal user, not root.

---

## How it works

Everything is plain Linux sysfs — no vendor tools required:

| Setting | Interface |
|---|---|
| Performance mode | `/sys/firmware/acpi/platform_profile` (via `powerprofilesctl` when available) |
| CPU boost / frequency / EPP | `/sys/devices/system/cpu/cpu*/cpufreq/` (amd-pstate) |
| CPU & GPU power limits | `/sys/class/firmware-attributes/asus-armoury/attributes/` |
| Fan curves | hwmon `asus_custom_fan_curve` (`pwmN_auto_pointM_{temp,pwm}`, `pwmN_enable`) |
| Charge limit | `/sys/class/power_supply/BAT*/charge_control_end_threshold` |
| PCIe errors | `/sys/bus/pci/devices/*/aer_dev_{correctable,nonfatal,fatal}` |

Saved state lives in `/etc/tuf-control/` (`config.json`, `profiles.json`, `stability.json`, `token`).

### Project layout

```
backend/            Flask API (Python)
  tuf/app.py          routes + serving the built UI
  tuf/security.py     host check + token
  tuf/hw/             sysfs settings whitelist, sensors, fans, GPU, PCIe
  tuf/profiles.py     profiles, apply order, boot / resume re-apply, factory reset
  tuf/stability.py    configurable Stability mode
  tuf/fanmode.py      Default / Stability / Custom fan mode that sticks
  tuf/diag.py         crash tests + crash history
  tests/              pytest suite against a fake sysfs tree
frontend/           React + TypeScript + Vite UI
crashdiag/          standalone crash-test and PCIe-watch scripts
systemd/            service units + udev rule
install.sh / uninstall.sh
```

---

## Development

```bash
# backend tests (use a fake sysfs tree - safe to run anywhere)
cd backend && uv run pytest          # or: python -m venv .venv && pip install flask waitress pytest

# frontend type-check and build
cd frontend && npm install && npm run lint && npm run build

# run the UI with hot reload against a running backend
cd frontend && VITE_TUF_TOKEN=$(sudo cat /etc/tuf-control/token) npm run dev
```

Running the backend as a normal user works for reading (writes fail with "permission denied"):

```bash
cd backend && TUF_PORT=8788 TUF_CONFIG_DIR=/tmp/tuf-conf uv run python -m tuf
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Backend unreachable" | `systemctl status tuf-control` · `journalctl -u tuf-control -n 50` |
| Changes don't show up after `git pull` | run `sudo ./install.sh` again, then hard-reload the page (Ctrl+Shift+R) |
| "Permission denied" when changing a setting | you're running a dev copy as a normal user — use the installed service |
| A setting shows 🔒 | the firmware locks it right now (usually on battery) — plug in the charger |
| Profile not re-applied after boot | `journalctl -u tuf-control-boot` |
| Fan curves reset | make sure you picked a fan mode on the Fans page — it is re-applied after reboot / sleep / mode changes |
