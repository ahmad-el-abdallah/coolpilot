"""Named profiles = a set of setting values (+ optional fan curves).

Stored in $COOLPILOT_CONFIG_DIR/profiles.json (default /etc/coolpilot). Built-in
profiles live in code and can't be deleted. The value DEFAULT means "the
hardware/firmware default for this setting".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from .hw import fans, sysfs

CONFIG_DIR = os.environ.get("COOLPILOT_CONFIG_DIR", "/etc/coolpilot")
DEFAULT = "__default__"
MIN = "__min__"  # the lowest value the firmware allows right now (differs on battery vs charger)
BEFORE_STABILITY = "_before_stability"

# order matters: the platform profile makes firmware reload power limits, fan
# curves and (via power-profiles-daemon) EPP, so it goes first.
APPLY_ORDER = ["platform_profile", "cpu_boost", "cpu_max_mhz", "cpu_min_mhz", "epp",
               "ppt_pl1", "ppt_pl2", "ppt_pl3", "gpu_dynamic_boost", "gpu_temp_target",
               "charge_limit", "panel_overdrive", "kbd_backlight", "screen_brightness"]

BUILTIN: dict[str, dict] = {
    # settings/fans are built from the user's Stability page config (coolpilot/stability.py)
    "Stability": {
        "description": "Cool and steady: low power, no boost. Helps laptops that freeze or reset under load.",
        "settings": {},
    },
    "Quiet": {
        "description": "Silent fans, power-saving CPU, boost still allowed.",
        "settings": {"platform_profile": "quiet", "epp": "power", "cpu_boost": True,
                     "cpu_max_mhz": DEFAULT},
    },
    "Balanced": {
        "description": "ASUS default everyday mode.",
        "settings": {"platform_profile": "balanced", "epp": "balance_performance",
                     "cpu_boost": True, "cpu_max_mhz": DEFAULT},
    },
    "Performance": {
        "description": "Maximum speed, loud fans, most heat.",
        "settings": {"platform_profile": "performance", "epp": "performance", "cpu_boost": True,
                     "cpu_max_mhz": DEFAULT, "ppt_pl1": DEFAULT, "ppt_pl2": DEFAULT,
                     "ppt_pl3": DEFAULT, "gpu_dynamic_boost": DEFAULT, "gpu_temp_target": DEFAULT},
    },
    "Factory defaults": {
        "description": "Undo everything: firmware defaults, full frequency range, automatic fans, 100% charge.",
        "settings": {k: DEFAULT for k in ["platform_profile", "epp", "cpu_boost", "cpu_max_mhz",
                                          "cpu_min_mhz", "ppt_pl1", "ppt_pl2", "ppt_pl3",
                                          "gpu_dynamic_boost", "gpu_temp_target", "charge_limit"]},
        "fans": "reset",
    },
}

PPD_NAMES = {"quiet": "power-saver", "low-power": "power-saver", "cool": "power-saver",
             "balanced": "balanced", "performance": "performance"}
# models name the modes differently (e.g. "low-power" instead of "quiet")
PROFILE_ALIASES = {"quiet": ["low-power", "cool"], "low-power": ["quiet", "cool"],
                   "cool": ["quiet", "low-power"], "performance": ["balanced-performance"]}


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def _path(name: str) -> str:
    return os.path.join(CONFIG_DIR, name)


def _load(name: str, fallback):
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _save(name: str, data) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = _path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _path(name))


def user_profiles() -> dict:
    return _load("profiles.json", {})


def config() -> dict:
    return _load("config.json", {})


def update_config(**kw) -> dict:
    c = config()
    c.update(kw)
    _save("config.json", c)
    return c


def list_profiles() -> list[dict]:
    cfg = config()
    out = []
    for name in BUILTIN:
        out.append({"name": name, "builtin": True, **get(name)})
    for name, prof in user_profiles().items():
        if not name.startswith("_"):
            out.append({"name": name, "builtin": False, **prof})
    for p in out:
        p["boot"] = p["name"] == cfg.get("boot_profile")
        from . import stability
        p["active"] = (stability.is_on() if p["name"] == "Stability"
                       else p["name"] == cfg.get("active_profile"))
    return out


def get(name: str) -> dict | None:
    if name == "Stability":
        from . import stability  # imports this module
        return stability.profile()
    return BUILTIN.get(name) or user_profiles().get(name)


def save_current(name: str, description: str = "", include_fans: bool = True) -> dict:
    name = name.strip()
    if not name or len(name) > 40:
        raise ValueError("profile name must be 1-40 characters")
    if name in BUILTIN:
        raise ValueError("that name is used by a built-in profile")
    prof = {"description": description[:200], "settings": sysfs.snapshot(), "created": time.time()}
    if include_fans and fans.available():
        prof["fans"] = {str(f["fan"]): {"custom": f["custom"], "points": f["points"]}
                        for f in fans.read_all()["fans"]}
    profs = user_profiles()
    profs[name] = prof
    _save("profiles.json", profs)
    return prof


def delete(name: str) -> None:
    if name in BUILTIN:
        raise ValueError("built-in profiles can't be deleted")
    profs = user_profiles()
    if name not in profs:
        raise ValueError("no such profile")
    del profs[name]
    _save("profiles.json", profs)
    cfg = config()
    if cfg.get("boot_profile") == name:
        update_config(boot_profile=None)


# --------------------------------------------------------------------------- #
# applying
# --------------------------------------------------------------------------- #

def _set_platform_profile(value: str) -> None:
    """Go through power-profiles-daemon when it runs, so it doesn't fight us."""
    ppd = shutil.which("powerprofilesctl")
    if ppd and value in PPD_NAMES:
        r = subprocess.run([ppd, "set", PPD_NAMES[value]], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            time.sleep(0.4)  # let the daemon finish writing EPP/platform profile
            if sysfs.SETTINGS["platform_profile"].get() == value:
                return
    sysfs.set_setting("platform_profile", value)
    time.sleep(0.2)


class Skip(Exception):
    """Nothing should be written for this setting right now (reason in the message)."""


def resolve(key: str, value):
    """Turn a stored profile value into what to write right now -> (value, note).

    Firmware ranges change with the power source (e.g. GPU Dynamic Boost is 0-0 W
    on battery, CPU limits max 65 W instead of 80 W), so int values are fitted
    into the live range. Raises Skip when nothing should be written."""
    s = sysfs.SETTINGS[key]
    if value == DEFAULT:
        value = s.default() if s.default else None
        if value is None:
            raise Skip("no default")
    note = ""
    if key == "platform_profile":
        opts = s.choices() if s.choices else []
        if value not in opts:
            alt = next((a for a in PROFILE_ALIASES.get(value, []) if a in opts), None)
            if alt is None:
                raise Skip(f"'{value}' mode doesn't exist on this laptop")
            value = alt
    if s.kind == "int":
        lo, hi = (s.min() if s.min else None), (s.max() if s.max else None)
        if value == MIN:
            value = lo
            if value is None:
                raise Skip("no minimum")
        if lo is not None and hi is not None and lo == hi:
            # pinned by firmware: nothing to write (writing can even fail with EINVAL)
            raise Skip(f"fixed at {lo}{s.unit} by firmware {_power_source()}")
        clamped = min(max(value, lo if lo is not None else value), hi if hi is not None else value)
        if clamped != value:
            note = f": limited to {clamped}{s.unit} {_power_source()}"
            value = clamped
    return value, note


def apply_settings(settings: dict) -> dict:
    results: dict[str, str] = {}
    profile_changed = False
    keys = [k for k in APPLY_ORDER if k in settings] + [k for k in settings if k not in APPLY_ORDER]
    for key in keys:
        s = sysfs.SETTINGS.get(key)
        if not s:
            results[key] = "unknown setting"
            continue
        if not s.available():
            results[key] = "not supported"
            continue
        try:
            value, note = resolve(key, settings[key])
        except Skip as e:
            results[key] = "no default" if str(e) == "no default" else f"skipped: {e}"
            continue
        try:
            if key == "platform_profile":
                # re-setting the same mode makes firmware reload its power limits
                # and fan curves, so leave it alone when it's already right
                if s.get() != value:
                    _set_platform_profile(value)
                    profile_changed = True
            elif key == "cpu_max_mhz":
                # the max can't go below the current min
                cur_min = sysfs.SETTINGS["cpu_min_mhz"].get()
                if cur_min and value < cur_min:
                    sysfs.set_setting("cpu_min_mhz", sysfs.SETTINGS["cpu_min_mhz"].min())
                sysfs.set_setting(key, value)
            else:
                sysfs.set_setting(key, value)
            results[key] = "ok" + note
        except (sysfs.SettingError, OSError, subprocess.SubprocessError) as e:
            results[key] = str(e)
    if profile_changed:
        # the firmware just reloaded its own fan curves - put the chosen fan mode back
        from . import fanmode
        results.update(fanmode.enforce())
    return results


def _power_source() -> str:
    return "on charger" if sysfs.on_ac() else "on battery"


def is_ok(result: str) -> bool:
    return result.startswith("ok") or result.startswith("skipped") or result == "not supported"


def apply_fans(spec) -> dict:
    results = {}
    if not spec or not fans.available():
        return results
    if spec == "reset":
        for n in fans.FANS:
            try:
                fans.reset(n)
                results[f"fan{n}"] = "ok"
            except fans.FanError as e:
                results[f"fan{n}"] = str(e)
        return results
    for n, f in spec.items():
        try:
            if f.get("custom"):
                fans.set_curve(int(n), f["points"], enable=True)
            else:
                fans.set_mode(int(n), False)
            results[f"fan{n}"] = "ok"
        except (fans.FanError, KeyError, ValueError) as e:
            results[f"fan{n}"] = str(e)
    return results


def apply(name: str) -> dict:
    if name == "Stability":
        from . import stability
        return stability.turn_on()
    from . import fanmode
    prof = get(name)
    if prof is None:
        raise ValueError("no such profile")
    # a profile's fans become the fan mode, so they stick like the Fans page choice
    spec = prof.get("fans")
    if spec == "reset":
        update_config(fan_mode="default", fan_custom=None)
    elif spec:
        update_config(fan_mode="custom" if any(f.get("custom") for f in spec.values()) else "default",
                      fan_custom=spec)
    # choosing another profile ends Stability mode (without restoring anything)
    update_config(active_profile=name if not name.startswith("_") else None, stability_on=False)
    results = apply_settings(prof.get("settings", {}))
    if spec == "reset":
        results.update(apply_fans("reset"))
    results.update(fanmode.enforce())
    return results


def set_boot(name: str | None) -> dict:
    if name is not None and get(name) is None:
        raise ValueError("no such profile")
    return update_config(boot_profile=name)


def apply_boot(reapply: bool = False) -> dict:
    """At boot: the "apply at boot" profile. With reapply (after sleep or when
    the charger is plugged/unplugged): Stability mode if it's on, else the last
    applied profile, else the boot one. The fan mode is always put back."""
    from . import fanmode, stability
    cfg = config()
    results: dict = {}
    if reapply and stability.is_on():
        results = apply("Stability")
    else:
        if not reapply and stability.is_on() and cfg.get("boot_profile") != "Stability":
            # the reboot reset the hardware and Stability isn't set to apply at boot
            update_config(stability_on=False, active_profile=None)
        name = (cfg.get("active_profile") if reapply else None) or cfg.get("boot_profile")
        if name and get(name) is not None:
            results = apply(name)
    results.update(fanmode.enforce())
    return results


def factory_reset(delete_profiles: bool = False) -> dict:
    """Everything back to how the laptop was on first boot: firmware defaults,
    automatic fans, Stability mode off and its setup back to recommended,
    nothing applied at boot. Saved profiles are kept unless asked."""
    from . import stability
    try:
        os.remove(_path(stability.FILE))
    except FileNotFoundError:
        pass
    profs = {} if delete_profiles else {k: v for k, v in user_profiles().items() if not k.startswith("_")}
    _save("profiles.json", profs)
    update_config(stability_on=False, active_profile=None, boot_profile=None,
                  fan_mode="default", fan_custom=None)
    results = apply_settings(BUILTIN["Factory defaults"]["settings"])
    results.update(apply_fans("reset"))
    return results
