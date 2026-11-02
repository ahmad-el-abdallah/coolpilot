"""ASUS keyboard RGB (asus_wmi `kbd_rgb_mode`).

Brightness is already a plain Setting (sysfs.py's "kbd_backlight", 0..max_brightness).
This is the color/effect/speed side, which is a different, write-only kernel file:
writing "cmd mode red green blue speed" to kbd_rgb_mode sets it, but it can't be
read back (the attribute is 0600/-w------- by design - confirmed on this machine,
`cat` fails with EACCES even as root). So unlike every other setting here, CoolPilot
itself is the only source of truth for "what's currently set" - we persist the last
value we wrote and replay it on every boot/resume, the same way fan curves "stick"
via fanmode.enforce().

The mode numbers below are community-reverse-engineered (this attribute has never
been officially documented) and come from the original asus-wmi RGB patch series -
they match this generation of the driver, but effect rendering can still vary by
model/firmware revision.
"""
from __future__ import annotations

from . import sysfs

MODES = {"static": 0, "breathing": 1, "color_cycle": 2, "strobe": 3}
RGB_FILE = f"{sysfs.KBD}/kbd_rgb_mode"


class KeyboardError(ValueError):
    pass


def available() -> bool:
    return sysfs.exists(RGB_FILE)


def _validate(mode: str, rgb: tuple[int, int, int], speed: int) -> tuple[int, int, int, int]:
    if mode not in MODES:
        raise KeyboardError(f"mode must be one of {sorted(MODES)}")
    if len(rgb) != 3 or any(not isinstance(c, int) or not (0 <= c <= 255) for c in rgb):
        raise KeyboardError("rgb must be three integers 0-255")
    if not isinstance(speed, int) or not (0 <= speed <= 2):
        raise KeyboardError("speed must be 0, 1 or 2")
    return MODES[mode], *rgb, speed


def set(mode: str, rgb: tuple[int, int, int], speed: int, *, _save: bool = True) -> None:
    if not available():
        raise KeyboardError("keyboard RGB isn't exposed by this kernel")
    mode_n, r, g, b, speed = _validate(mode, rgb, speed)
    try:
        sysfs.write(RGB_FILE, f"1 {mode_n} {r} {g} {b} {speed}")
    except OSError as e:
        raise KeyboardError(f"kbd_rgb_mode: write failed ({e.strerror or e})")
    if _save:
        from .. import profiles
        profiles.update_config(keyboard_rgb={"mode": mode, "rgb": list(rgb), "speed": speed})


def state() -> dict:
    from .. import profiles
    saved = profiles.config().get("keyboard_rgb") or {"mode": "static", "rgb": [255, 255, 255], "speed": 0}
    return {"available": available(), **saved}


def enforce() -> dict:
    """Re-apply the last saved RGB state (boot/resume) - belt-and-suspenders in
    case the firmware doesn't keep it across a reboot on its own."""
    if not available():
        return {}
    from .. import profiles
    saved = profiles.config().get("keyboard_rgb")
    if not saved:
        return {}
    try:
        set(saved["mode"], tuple(saved["rgb"]), saved["speed"], _save=False)
        return {"keyboard_rgb": "ok"}
    except (KeyboardError, KeyError, TypeError) as e:
        return {"keyboard_rgb": f"error: {e}"}
