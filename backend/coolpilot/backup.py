"""Export / import CoolPilot's settings as one JSON file: saved profiles, the
Stability mode setup and preferences (boot profile, fan mode, alerts, black box,
warranty and repair-report notes). Useful after reinstalling, on another distro
or another laptop.

Values are checked for type, not against this laptop's live ranges: they're
fitted into the firmware's range when applied, like any profile. Things that
only make sense on the machine that made them (crash ids, the charge-once timer,
the token) are never exported.
"""
from __future__ import annotations

import time

from . import alerts, fanmode, profiles, stability
from .hw import fans, sysfs
from .hw.device import device_name

FORMAT = "coolpilot-backup"
VERSION = 1
PARTS = ("profiles", "stability", "preferences")
PREFERENCE_KEYS = ("boot_profile", "active_profile", "stability_on", "fan_mode", "fan_custom",
                   "blackbox", "alerts", "warranty", "report_symptoms")
SENTINELS = (profiles.DEFAULT, profiles.MIN, profiles.MAX)


class BackupError(ValueError):
    pass


def export() -> dict:
    cfg = profiles.config()
    return {
        "format": FORMAT, "version": VERSION, "exported": time.time(), "device": device_name(),
        "profiles": {k: v for k, v in profiles.user_profiles().items() if not k.startswith("_")},
        "stability": stability._stored(),
        "preferences": {k: cfg[k] for k in PREFERENCE_KEYS if k in cfg},
    }


# --------------------------------------------------------------------------- #
# checking a file
# --------------------------------------------------------------------------- #

def _value(key: str, value):
    """A setting value that's the right type for its setting (range is fitted when applied)."""
    s = sysfs.SETTINGS.get(key)
    if s is None:
        raise ValueError("unknown setting")
    if value in SENTINELS:
        return value
    if s.kind == "bool" and isinstance(value, bool):
        return value
    if s.kind == "int" and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000:
        return value
    if s.kind == "choice" and isinstance(value, str) and 0 < len(value) <= 40 and value.replace("_", "").replace("-", "").isalnum():
        return value
    raise ValueError(f"'{value}' isn't a valid value")


def _fan_spec(spec, where: str, warn: list[str]):
    if spec == "reset":
        return spec
    if not isinstance(spec, dict):
        raise BackupError(f"{where}: fan curves are malformed")
    out = {}
    for n, f in spec.items():
        if str(n) not in {str(x) for x in fans.FANS} or not isinstance(f, dict):
            warn.append(f"{where}: skipped unknown fan {n}")
            continue
        if not f.get("custom"):
            out[str(n)] = {"custom": False}
            continue
        try:
            out[str(n)] = {"custom": True, "points": fans.validate(f.get("points"))}
        except fans.FanError as e:
            warn.append(f"{where}: fan {n} skipped ({e})")
    return out or None


def _profiles(data, warn: list[str]) -> dict:
    if not isinstance(data, dict):
        raise BackupError("profiles are malformed")
    out = {}
    for name, p in data.items():
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 40 or name.startswith("_"):
            warn.append(f"skipped a profile with an invalid name ({str(name)[:40]!r})")
            continue
        if name in profiles.BUILTIN:
            warn.append(f"skipped “{name}”: that name belongs to a built-in profile")
            continue
        if not isinstance(p, dict) or not isinstance(p.get("settings", {}), dict):
            warn.append(f"skipped “{name}”: malformed")
            continue
        settings = {}
        for k, v in p.get("settings", {}).items():
            try:
                settings[k] = _value(k, v)
            except ValueError as e:
                warn.append(f"“{name}”: {k} skipped ({e})")
        prof = {"description": str(p.get("description") or "")[:200], "settings": settings,
                "created": p["created"] if isinstance(p.get("created"), (int, float)) else time.time()}
        if p.get("fans") is not None:
            spec = _fan_spec(p["fans"], f"“{name}”", warn)
            if spec:
                prof["fans"] = spec
        if p.get("fans_preset") in fans.PRESETS:
            prof["fans_preset"] = p["fans_preset"]
        out[name] = prof
    return out


def _stability(data, warn: list[str]) -> dict:
    if not isinstance(data, dict):
        raise BackupError("the Stability setup is malformed")
    items = {}
    for k, item in (data.get("items") or {}).items():
        if k not in stability.RECOMMENDED or not isinstance(item, dict):
            warn.append(f"Stability: skipped unknown item {k}")
            continue
        clean = {}
        if isinstance(item.get("enabled"), bool):
            clean["enabled"] = item["enabled"]
        if "value" in item:
            try:
                clean["value"] = _value(k, item["value"])
            except ValueError as e:
                warn.append(f"Stability: {k} value skipped ({e})")
        if clean:
            items[k] = clean
    out: dict = {"items": items}
    f = data.get("fans")
    if isinstance(f, dict):
        clean = {}
        if isinstance(f.get("enabled"), bool):
            clean["enabled"] = f["enabled"]
        if f.get("preset") in fans.PRESETS:
            clean["preset"] = f["preset"]
        if clean:
            out["fans"] = clean
    return out


def _preferences(data, warn: list[str]) -> dict:
    if not isinstance(data, dict):
        raise BackupError("preferences are malformed")
    out: dict = {}
    for k in ("boot_profile", "active_profile"):
        if isinstance(data.get(k), str) and len(data[k]) <= 40:
            out[k] = data[k]
    for k in ("stability_on", "blackbox"):
        if isinstance(data.get(k), bool):
            out[k] = data[k]
    if data.get("fan_mode") in fanmode.MODES:
        out["fan_mode"] = data["fan_mode"]
    if data.get("fan_custom") is not None:
        out["fan_custom"] = _fan_spec(data["fan_custom"], "custom fan curves", warn)
    if isinstance(data.get("alerts"), dict):
        try:
            a = data["alerts"]
            out["alerts"] = alerts.normalize({"enabled": a.get("enabled", True), "items": a.get("items") or {}})
        except ValueError as e:
            warn.append(f"alerts skipped ({e})")
    w = data.get("warranty")
    if isinstance(w, dict):
        out["warranty"] = {k: str(w[k])[:120] for k in ("start", "end", "territory", "note")
                           if isinstance(w.get(k), str) and w[k]} or None
    if isinstance(data.get("report_symptoms"), str):
        out["report_symptoms"] = data["report_symptoms"][:4000]
    return out


def check(data) -> dict:
    """Validate a backup -> cleaned parts + warnings. Raises BackupError if it isn't one."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("this isn't a CoolPilot backup file")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise BackupError("this backup was made by a newer CoolPilot - update CoolPilot first")
    warn: list[str] = []
    clean = {
        "profiles": _profiles(data.get("profiles") or {}, warn),
        "stability": _stability(data.get("stability") or {}, warn),
        "preferences": _preferences(data.get("preferences") or {}, warn),
    }
    return {"clean": clean, "warnings": warn,
            "device": str(data.get("device") or "")[:80] or None,
            "exported": data["exported"] if isinstance(data.get("exported"), (int, float)) else None}


def inspect(data) -> dict:
    """What a file contains, for the confirmation dialog."""
    c = check(data)
    p = c["clean"]["preferences"]
    return {
        "device": c["device"], "exported": c["exported"], "this_device": device_name(),
        "profiles": sorted(c["clean"]["profiles"]),
        "stability_items": len(c["clean"]["stability"].get("items", {})),
        "stability_on": p.get("stability_on"),
        "preferences": sorted(p),
        "warnings": c["warnings"],
    }


# --------------------------------------------------------------------------- #
# restoring
# --------------------------------------------------------------------------- #

def restore(data, parts=PARTS) -> dict:
    parts = [x for x in parts if x in PARTS]
    if not parts:
        raise BackupError("choose what to restore")
    c = check(data)
    clean = c["clean"]
    results: dict = {}
    pref = clean["preferences"] if "preferences" in parts else {}
    touches_modes = "stability" in parts or "preferences" in parts

    # hand the hardware back cleanly first, so Stability restores what it changed
    was_on = stability.is_on()
    if was_on and touches_modes:
        results.update(stability.turn_off())

    if "profiles" in parts:
        mine = profiles.user_profiles()
        mine.update(clean["profiles"])
        profiles._save("profiles.json", mine)
    if "stability" in parts:
        profiles._save(stability.FILE, clean["stability"])
    if pref:
        keep = {k: v for k, v in pref.items() if k not in ("stability_on", "active_profile", "boot_profile")}
        for k in ("boot_profile", "active_profile"):
            name = pref.get(k)
            if name and profiles.get(name) is None:
                c["warnings"].append(f"{k.replace('_', ' ')} “{name}” isn't on this laptop - left unset")
            else:
                keep[k] = name
        if keep:
            profiles.update_config(**keep)

    # put the laptop into the state the backup describes
    if touches_modes:
        want_on = pref.get("stability_on", was_on) if pref else was_on
        name = pref.get("active_profile") if pref else None
        if want_on:
            results.update(stability.turn_on())
        elif name and name != "Stability" and profiles.get(name) is not None:
            results.update(profiles.apply(name))
        else:
            results.update(fanmode.enforce())
    return {"restored": parts, "warnings": c["warnings"], "results": results}
