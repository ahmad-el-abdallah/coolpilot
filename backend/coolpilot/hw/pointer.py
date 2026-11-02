"""Touchpad / mouse pointer speed.

Unlike every other setting in this app, pointer speed isn't a kernel/firmware
value - it's owned by whichever desktop session is running (libinput via the
compositor or DE). The backend runs as root, so each read/write has to reach
into a logged-in user's session the same way alerts.py reaches a user's
notification bus (see desktop.py).

Best-effort support, detected per session:
  - Hyprland: fully supported and testable (what this laptop runs).
  - Sway, GNOME, KDE, generic X11 (xinput): implemented from each project's
    documented CLI/config conventions, but unverified - this machine has no
    session for any of them to test against.

Persistence: Hyprland and Sway get a clearly marked block appended to their own
config file (never touching the user's hand-written settings), rewritten in
place on every change. GNOME (dconf) and KDE (kcminputrc) persist on their own.
X11 gets a root-written xorg.conf.d snippet (applies on next X start).
"""
from __future__ import annotations

import glob
import json
import os
import pwd
import re
import shutil
import subprocess
import time

from ..desktop import run_as_user, sessions

KINDS = ("touchpad", "mouse")
MIN, MAX = -1.0, 1.0


class PointerError(ValueError):
    pass


def _proc_running(uid: int, name: str) -> bool:
    if not shutil.which("pgrep"):
        return False
    try:
        r = subprocess.run(["pgrep", "-u", str(uid), "-x", name], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def detect(uid: int, rundir: str) -> str | None:
    if glob.glob(os.path.join(rundir, "hypr", "*")):
        return "hyprland"
    if _proc_running(uid, "sway"):
        return "sway"
    if _proc_running(uid, "gnome-shell") and shutil.which("gsettings"):
        return "gnome"
    if _proc_running(uid, "plasmashell") and (shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")):
        return "kde"
    if (_proc_running(uid, "Xorg") or _proc_running(uid, "Xwayland")) and shutil.which("xinput"):
        return "x11"
    return None


def _rewrite_block(path: str, begin: str, end: str, body: str) -> None:
    """Replace (or append) the text between two marker lines in `path`,
    leaving everything else untouched. Creates the file/directory if needed."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        text = ""
    block = f"{begin}\n{body}{end}\n"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n" if text else "") + block
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# --------------------------------------------------------------------------- #
# Hyprland
# --------------------------------------------------------------------------- #

HYPR_BEGIN = "-- BEGIN COOLPILOT POINTER (managed by CoolPilot - edits only this block) --"
HYPR_END = "-- END COOLPILOT POINTER --"


def _hypr_signature(rundir: str) -> str | None:
    """hyprctl won't find the running instance without this - it doesn't infer
    it from $XDG_RUNTIME_DIR/hypr/ alone, even when there's only one."""
    sigs = glob.glob(os.path.join(rundir, "hypr", "*"))
    return os.path.basename(sigs[0]) if sigs else None


def _hyprctl(uid: int, rundir: str, args: list[str]):
    sig = _hypr_signature(rundir)
    if not sig:
        return None
    return run_as_user(uid, rundir, ["hyprctl", *args], extra_env={"HYPRLAND_INSTANCE_SIGNATURE": sig})


def _hypr_devices(uid: int, rundir: str) -> dict:
    """{'mouse': name, 'touchpad': name} from `hyprctl -j devices`."""
    r = _hyprctl(uid, rundir, ["-j", "devices"])
    if not r or r.returncode != 0:
        return {}
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        return {}
    out: dict = {}
    for m in data.get("mice", []):
        name = m.get("name", "")
        kind = "touchpad" if "touchpad" in name.lower() else "mouse"
        out.setdefault(kind, name)
    return out


def _hypr_input_path(uid: int) -> str:
    return os.path.join(pwd.getpwuid(uid).pw_dir, ".config", "hypr", "input.lua")


def _hypr_saved(uid: int) -> dict:
    """Parse our own marked block back out -> {device_name: sensitivity}."""
    try:
        with open(_hypr_input_path(uid)) as f:
            text = f.read()
    except OSError:
        return {}
    m = re.search(re.escape(HYPR_BEGIN) + r"(.*?)" + re.escape(HYPR_END), text, re.DOTALL)
    if not m:
        return {}
    return {name: float(val) for name, val in re.findall(
        r'name\s*=\s*"([^"]+)"\s*,\s*sensitivity\s*=\s*(-?[\d.]+)', m.group(1))}


def _hypr_global_sensitivity(uid: int, rundir: str) -> float | None:
    r = _hyprctl(uid, rundir, ["-j", "getoption", "input:sensitivity"])
    if not r or r.returncode != 0:
        return None
    try:
        return float(json.loads(r.stdout)["float"])
    except (ValueError, KeyError, TypeError):
        return None


def _hypr_get(uid: int, rundir: str, kind: str) -> float | None:
    name = _hypr_devices(uid, rundir).get(kind)
    saved = _hypr_saved(uid)
    if name and name in saved:
        return saved[name]
    return _hypr_global_sensitivity(uid, rundir)


def _hypr_set(uid: int, rundir: str, kind: str, value: float) -> None:
    name = _hypr_devices(uid, rundir).get(kind)
    if not name:
        raise PointerError(f"no {kind} device found")
    saved = _hypr_saved(uid)
    saved[name] = value
    body = "".join(f'hl.device({{ name = "{n}", sensitivity = {v} }})\n' for n, v in saved.items())
    _rewrite_block(_hypr_input_path(uid), HYPR_BEGIN, HYPR_END, body)
    r = _hyprctl(uid, rundir, ["reload"])
    if not r or r.returncode != 0:
        raise PointerError("saved, but Hyprland didn't reload - it will apply next login")


# --------------------------------------------------------------------------- #
# Sway
# --------------------------------------------------------------------------- #

SWAY_BEGIN = "# BEGIN COOLPILOT POINTER (managed by CoolPilot - edits only this block) #"
SWAY_END = "# END COOLPILOT POINTER #"


def _sway_inputs(uid: int, rundir: str) -> dict:
    r = run_as_user(uid, rundir, ["swaymsg", "-t", "get_inputs", "-r"])
    if not r or r.returncode != 0:
        return {}
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        return {}
    out: dict = {}
    for dev in data:
        kind = "touchpad" if dev.get("type") == "touchpad" else "mouse" if dev.get("type") == "pointer" else None
        if kind and dev.get("identifier"):
            out.setdefault(kind, dev["identifier"])
    return out


def _sway_config_path(uid: int) -> str:
    return os.path.join(pwd.getpwuid(uid).pw_dir, ".config", "sway", "config")


def _sway_saved(uid: int) -> dict:
    try:
        with open(_sway_config_path(uid)) as f:
            text = f.read()
    except OSError:
        return {}
    m = re.search(re.escape(SWAY_BEGIN) + r"(.*?)" + re.escape(SWAY_END), text, re.DOTALL)
    if not m:
        return {}
    return {ident: float(val) for ident, val in re.findall(
        r'input\s+"([^"]+)"\s*\{[^}]*pointer_accel\s+(-?[\d.]+)', m.group(1), re.DOTALL)}


def _sway_get(uid: int, rundir: str, kind: str) -> float | None:
    ident = _sway_inputs(uid, rundir).get(kind)
    return _sway_saved(uid).get(ident) if ident else None


def _sway_set(uid: int, rundir: str, kind: str, value: float) -> None:
    ident = _sway_inputs(uid, rundir).get(kind)
    if not ident:
        raise PointerError(f"no {kind} device found")
    saved = _sway_saved(uid)
    saved[ident] = value
    body = "".join(f'input "{i}" {{\n  pointer_accel {v}\n  accel_profile flat\n}}\n' for i, v in saved.items())
    _rewrite_block(_sway_config_path(uid), SWAY_BEGIN, SWAY_END, body)
    r = run_as_user(uid, rundir, ["swaymsg", "reload"])
    if not r or r.returncode != 0:
        raise PointerError("saved, but sway didn't reload - it will apply next login")


# --------------------------------------------------------------------------- #
# GNOME (dconf persists on its own - no file to manage)
# --------------------------------------------------------------------------- #

GNOME_SCHEMA = {"touchpad": "org.gnome.desktop.peripherals.touchpad", "mouse": "org.gnome.desktop.peripherals.mouse"}


def _gnome_get(uid: int, rundir: str, kind: str) -> float | None:
    r = run_as_user(uid, rundir, ["gsettings", "get", GNOME_SCHEMA[kind], "speed"])
    if not r or r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _gnome_set(uid: int, rundir: str, kind: str, value: float) -> None:
    r = run_as_user(uid, rundir, ["gsettings", "set", GNOME_SCHEMA[kind], "speed", str(value)])
    if not r or r.returncode != 0:
        raise PointerError("gsettings set failed")


# --------------------------------------------------------------------------- #
# KDE Plasma - unverified: no Plasma session exists on this machine to test
# the exact kcminputrc keys against.
# --------------------------------------------------------------------------- #

def _kde_write_tool() -> str | None:
    return shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")


def _kde_read_tool() -> str | None:
    return shutil.which("kreadconfig6") or shutil.which("kreadconfig5")


def _kde_get(uid: int, rundir: str, kind: str) -> float | None:
    tool = _kde_read_tool()
    if not tool:
        return None
    r = run_as_user(uid, rundir, [tool, "--file", "kcminputrc", "--group", "Mouse", "--key", "XLbInptAccelSpeed"])
    if not r or r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _kde_set(uid: int, rundir: str, kind: str, value: float) -> None:
    tool = _kde_write_tool()
    if not tool:
        raise PointerError("kwriteconfig not found")
    r = run_as_user(uid, rundir, [tool, "--file", "kcminputrc", "--group", "Mouse", "--key",
                                   "XLbInptAccelSpeed", str(value)])
    if not r or r.returncode != 0:
        raise PointerError("kwriteconfig failed")
    run_as_user(uid, rundir, ["qdbus", "org.kde.KWin", "/KWin", "reconfigure"])


# --------------------------------------------------------------------------- #
# Generic X11 (xinput + libinput) - fallback for anything else
# --------------------------------------------------------------------------- #

X11_CONF = "/etc/X11/xorg.conf.d/91-coolpilot-pointer.conf"
X11_BEGIN = "# BEGIN COOLPILOT POINTER (managed by CoolPilot - edits only this block) #"
X11_END = "# END COOLPILOT POINTER #"


def _x11_device_name(uid: int, rundir: str, kind: str) -> str | None:
    r = run_as_user(uid, rundir, ["xinput", "list", "--name-only"])
    if not r or r.returncode != 0:
        return None
    names = [n for n in r.stdout.splitlines() if n.strip()]
    if kind == "touchpad":
        return next((n for n in names if "touchpad" in n.lower()), None)
    return next((n for n in names if "touchpad" not in n.lower()
                 and "keyboard" not in n.lower() and "virtual" not in n.lower()), None)


def _x11_get(uid: int, rundir: str, kind: str) -> float | None:
    name = _x11_device_name(uid, rundir, kind)
    if not name:
        return None
    r = run_as_user(uid, rundir, ["xinput", "list-props", name])
    if not r or r.returncode != 0:
        return None
    m = re.search(r"libinput Accel Speed \(\d+\):\s*(-?[\d.]+)", r.stdout)
    return float(m.group(1)) if m else None


def _x11_saved() -> dict:
    try:
        with open(X11_CONF) as f:
            text = f.read()
    except OSError:
        return {}
    m = re.search(re.escape(X11_BEGIN) + r"(.*?)" + re.escape(X11_END), text, re.DOTALL)
    if not m:
        return {}
    return {name: float(val) for name, val in re.findall(
        r'MatchProduct\s+"([^"]+)".*?Option\s+"AccelSpeed"\s+"(-?[\d.]+)"', m.group(1), re.DOTALL)}


def _x11_set(uid: int, rundir: str, kind: str, value: float) -> None:
    name = _x11_device_name(uid, rundir, kind)
    if not name:
        raise PointerError(f"no {kind} device found")
    r = run_as_user(uid, rundir, ["xinput", "set-prop", name, "libinput Accel Speed", str(value)])
    if not r or r.returncode != 0:
        raise PointerError("xinput set-prop failed")
    saved = _x11_saved()
    saved[name] = value
    body = "".join(
        f'Section "InputClass"\n  Identifier "CoolPilot {n}"\n  MatchProduct "{n}"\n'
        f'  Driver "libinput"\n  Option "AccelSpeed" "{v}"\nEndSection\n'
        for n, v in saved.items())
    _rewrite_block(X11_CONF, X11_BEGIN, X11_END, body)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

_GET = {"hyprland": _hypr_get, "sway": _sway_get, "gnome": _gnome_get, "kde": _kde_get, "x11": _x11_get}
_SET = {"hyprland": _hypr_set, "sway": _sway_set, "gnome": _gnome_set, "kde": _kde_set, "x11": _x11_set}


def _session() -> tuple[int, str, str] | None:
    for uid, rundir in sessions():
        name = detect(uid, rundir)
        if name:
            return uid, rundir, name
    return None


def describe() -> dict:
    found = _session()
    if not found:
        empty = {"available": False, "value": None, "min": MIN, "max": MAX}
        return {"desktop": None, "touchpad": dict(empty), "mouse": dict(empty)}
    uid, rundir, name = found
    out: dict = {"desktop": name}
    for kind in KINDS:
        try:
            value = _GET[name](uid, rundir, kind)
        except Exception:
            value = None
        out[kind] = {"available": value is not None, "value": value, "min": MIN, "max": MAX}
    return out


def set(kind: str, value: float) -> dict:
    if kind not in KINDS:
        raise PointerError(f"unknown kind '{kind}'")
    if not isinstance(value, (int, float)) or not (MIN <= value <= MAX):
        raise PointerError(f"sensitivity must be between {MIN} and {MAX}")
    found = _session()
    if not found:
        raise PointerError("no desktop session found to apply this to")
    uid, rundir, name = found
    try:
        _SET[name](uid, rundir, kind, float(value))
    except PointerError:
        raise
    except Exception as e:
        raise PointerError(f"{kind}: failed to apply ({e})")
    return describe()


def hypr_input_lua() -> dict:
    """The current user's actual ~/.config/hypr/input.lua, for export - this
    machine's own file (including CoolPilot's own managed block, if any), not
    a separate generated template. Lets someone share a known-good input.lua
    with other Omarchy users instead of the ones they hit problems with."""
    found = _session()
    if not found or found[2] != "hyprland":
        return {"available": False, "content": None, "path": None}
    uid, _rundir, _name = found
    path = _hypr_input_path(uid)
    try:
        with open(path) as f:
            return {"available": True, "content": f.read(), "path": path}
    except OSError:
        return {"available": False, "content": None, "path": path}


def set_hypr_input_lua(content: str) -> dict:
    """Replace ~/.config/hypr/input.lua with `content` (e.g. one downloaded
    from another machine via hypr_input_lua() above) - the previous file is
    backed up first, and if the new one doesn't reload cleanly, it's rolled
    back automatically so a bad file never gets left in place."""
    found = _session()
    if not found or found[2] != "hyprland":
        raise PointerError("no Hyprland session found to apply this to")
    if not isinstance(content, str) or not content.strip():
        raise PointerError("input.lua can't be empty")
    uid, rundir, _name = found
    path = _hypr_input_path(uid)

    before = _hyprctl(uid, rundir, ["configerrors"])
    before_errors = before.stdout.strip() if before and before.returncode == 0 else ""

    backup = None
    try:
        if os.path.exists(path):
            backup = f"{path}.bak.{int(time.time())}"
            shutil.copy2(path, backup)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    except OSError as e:
        raise PointerError(f"couldn't write input.lua ({e.strerror or e})")

    r = _hyprctl(uid, rundir, ["reload"])
    after = _hyprctl(uid, rundir, ["configerrors"]) if r and r.returncode == 0 else None
    after_errors = after.stdout.strip() if after and after.returncode == 0 else ""
    new_errors = after_errors if after_errors and after_errors != before_errors else None

    if not r or r.returncode != 0 or new_errors:
        if backup:
            shutil.copy2(backup, path)
            _hyprctl(uid, rundir, ["reload"])
        detail = new_errors or "Hyprland didn't reload"
        raise PointerError(f"that input.lua has a problem, so it wasn't applied - your previous file was restored ({detail})")

    result = hypr_input_lua()
    result["backup"] = backup
    return result
