# CoolPilot

A local web control panel for **laptops on Linux**: power modes, CPU frequency and power
limits, GPU limits, fan curves, battery charge limit, profiles that survive reboots, and
built-in tools for diagnosing random freezes / resets.

It works best on **ASUS TUF / ROG** laptops (fan curves, CPU/GPU power limits and GPU boost come
from the kernel's ASUS drivers). On other laptops the generic parts still work — CPU boost and
frequency, energy preference, battery charge limit (where supported), sensors, crash diagnostics
and PCIe link health — and anything the machine doesn't expose simply shows as "not supported".

It was born on an ASUS TUF A15 (FA507NVR) with an intermittent hardware fault (freezes when
moved under load), so it also includes a configurable **Stability mode** that keeps the machine
cool and steady to reduce freezes until it is repaired.

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

Works on **any Linux distribution with systemd** — Arch / Manjaro / EndeavourOS, Debian / Ubuntu /
Mint / Pop!_OS, Fedora, openSUSE, and others — with any desktop (GNOME, KDE, Hyprland, Sway, X11 or Wayland).

| Needed | Why |
|---|---|
| **systemd** | runs the panel and re-applies settings after boot / sleep |
| **Python 3.9+** with `venv` | the backend (tested on Python 3.9, 3.10, 3.12, 3.13 and 3.14) |
| **A laptop** — ideally ASUS TUF / ROG | the ASUS-specific features use the kernel's `asus-wmi` / `asus-armoury` drivers; AMD or Intel CPU, NVIDIA or no dGPU |
| Node.js 20.19+ / 22.12+ *(optional)* | only to rebuild the UI — a prebuilt UI is included |

Optional tools (crash tests and extra readings): `stress-ng`, `glmark2`, `pciutils` (`lspci`),
`nvidia-smi` (comes with the NVIDIA driver), `power-profiles-daemon`.

**Install the requirements for your distro:**

```bash
# Arch / Manjaro / EndeavourOS
sudo pacman -S --needed git python stress-ng glmark2 pciutils power-profiles-daemon

# Debian / Ubuntu / Mint / Pop!_OS
sudo apt install git python3 python3-venv stress-ng glmark2 pciutils power-profiles-daemon

# Fedora
sudo dnf install git python3 stress-ng glmark2 pciutils

# openSUSE (Leap needs python311; Tumbleweed's python3 is fine)
sudo zypper install git python311 stress-ng glmark2 pciutils power-profiles-daemon
```

Package names can differ slightly between releases, and some distros split glmark2 into separate
X11 / Wayland / ES2 builds — any of them works.

**What depends on your kernel / model** — anything missing just shows as "not supported":

| Feature | Needs |
|---|---|
| Silent / Balanced / Turbo | `platform_profile` (all recent kernels) |
| CPU & GPU power limits, GPU Dynamic Boost | the `asus-armoury` driver (recent kernels) |
| Custom fan curves | `asus_custom_fan_curve` (kernel 5.17+, most TUF/ROG models) |
| CPU boost switch | AMD (`amd-pstate` / `acpi-cpufreq`) or Intel (`intel_pstate`) |
| PCIe press test | an NVIDIA dGPU |

Not supported: distros without systemd (Void, Alpine, Artix/OpenRC), and NixOS through the
installer (paths like `/opt` and `/etc/systemd/system` are managed declaratively there).

---

## Install

```bash
git clone https://github.com/ahmad-el-abdallah/coolpilot.git
cd coolpilot
./install.sh --check      # optional: checks this system and tells you what's missing
sudo ./install.sh
```

Then open **http://127.0.0.1:8787** — or launch **CoolPilot** from your app launcher.

`install.sh` will:

1. check the system (Python, drivers, optional tools, conflicting services) and stop with the
   exact package to install if something required is missing,
2. rebuild the UI if a recent Node is available (system, `mise`, `nvm`, `fnm`, `volta`, `asdf`),
   otherwise use the prebuilt one,
3. copy the app to `/opt/coolpilot` and create a Python venv with Flask + Waitress,
4. create a secret API token in `/etc/coolpilot/token`,
5. install and enable the systemd services:
   - `coolpilot` — the web panel (port **8787**, localhost only)
   - `coolpilot-boot` — re-applies your "apply at boot" profile and fan mode after boot
   - `coolpilot-resume` — re-applies after sleep and when the charger is plugged / unplugged
     (via a udev rule; ASUS firmware swaps its power-limit tables then)
6. copy the crash-test scripts to `~/crashdiag` (existing logs are left alone),
7. add a "CoolPilot" launcher entry.

### Other tools that manage the same settings

`asusctl` (`asusd`), TLP, auto-cpufreq, TuneD and laptop-mode-tools also change power modes,
CPU boost, fan curves or charge limits, and can silently undo changes made here. The installer
and the Dashboard warn when one of them is running — use one tool for these settings, e.g.
`sudo systemctl disable --now tlp`.

### Update

Coming from the old name (**tuf-control**)? Just run the new installer: it moves your saved
settings from `/etc/tuf-control` to `/etc/coolpilot` and removes the old services and folders.

Pull the new code and run the installer again — restarting the service alone does **not**
pick up new code, because the service runs the copy in `/opt/coolpilot`:

```bash
git pull
sudo ./install.sh
```

### Uninstall

```bash
sudo ./uninstall.sh
```

Removes the services, udev rule, `/opt/coolpilot` and the launcher entry, and asks whether to
delete your saved profiles in `/etc/coolpilot`. Hardware settings return to firmware defaults
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
- every `/api` call needs the secret `X-CoolPilot-Token` header — the token is only embedded in the
  page the server itself serves, so other websites can't read it or call the API;
- only a **whitelist of settings** can be written, each validated against its type and the
  firmware's live min/max (`backend/coolpilot/hw/sysfs.py`); nothing accepts raw file paths;
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

Saved state lives in `/etc/coolpilot/` (`config.json`, `profiles.json`, `stability.json`, `token`).

### Project layout

```
backend/            Flask API (Python)
  coolpilot/app.py          routes + serving the built UI
  coolpilot/security.py     host check + token
  coolpilot/hw/             sysfs settings whitelist, sensors, fans, GPU, PCIe
  coolpilot/profiles.py     profiles, apply order, boot / resume re-apply, factory reset
  coolpilot/stability.py    configurable Stability mode
  coolpilot/fanmode.py      Default / Stability / Custom fan mode that sticks
  coolpilot/diag.py         crash tests + crash history
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

# frontend type-check and build (commit frontend/dist too - it's the prebuilt UI)
cd frontend && npm install && npm run lint && npm run build

# run the UI with hot reload against a running backend
cd frontend && VITE_COOLPILOT_TOKEN=$(sudo cat /etc/coolpilot/token) npm run dev
```

Running the backend as a normal user works for reading (writes fail with "permission denied"):

```bash
cd backend && COOLPILOT_PORT=8788 COOLPILOT_CONFIG_DIR=/tmp/coolpilot-conf uv run python -m coolpilot
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Backend unreachable" | `systemctl status coolpilot` · `journalctl -u coolpilot -n 50` |
| Changes don't show up after `git pull` | run `sudo ./install.sh` again, then hard-reload the page (Ctrl+Shift+R) |
| "Permission denied" when changing a setting | you're running a dev copy as a normal user — use the installed service |
| A setting shows 🔒 | the firmware locks it right now (usually on battery) — plug in the charger |
| Profile not re-applied after boot | `journalctl -u coolpilot-boot` |
| Fan curves reset | make sure you picked a fan mode on the Fans page — it is re-applied after reboot / sleep / mode changes |
| Settings keep changing back | another tool (asusctl, TLP, auto-cpufreq, TuneD) is managing them — see the Dashboard warning |
| Crash history only shows the current boot | the journal isn't persistent: `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald` |
| `python3 -m venv` fails (Debian / Ubuntu) | `sudo apt install python3-venv` |
| GPU crash test does nothing | install `glmark2`; on NVIDIA laptops make sure the NVIDIA driver is loaded (`nvidia-smi`) |
