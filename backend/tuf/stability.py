"""Stability mode: a user-configurable set of protections against the
freeze/reset fault (less heat and current = less stress on weak solder joints).

Config lives in $TUF_CONFIG_DIR/stability.json and only stores what the user
changed; everything else falls back to RECOMMENDED. While Stability is on,
config changes apply immediately, and an item that gets switched off is
restored to its value from before Stability was turned on.
"""
from __future__ import annotations

from . import profiles
from .hw import fans, sysfs
from .profiles import MIN

RECOMMENDED: dict[str, object] = {
    "platform_profile": "quiet",
    "cpu_boost": False,
    "cpu_max_mhz": 3000,
    "epp": "power",
    "ppt_pl1": 35,
    "ppt_pl2": 45,
    "ppt_pl3": 45,
    "gpu_dynamic_boost": MIN,
    "gpu_temp_target": MIN,
    "charge_limit": 80,
}
# settings whose allowed range changes between charger and battery: stored values
# may be outside today's range (they're clamped when applied) or MIN
POWER_DEPENDENT = {"ppt_pl1", "ppt_pl2", "ppt_pl3", "gpu_dynamic_boost", "gpu_temp_target"}
FANS_RECOMMENDED = {"enabled": False, "preset": "stability"}
FILE = "stability.json"
# page sections; each can be switched as a whole between factory Default and Stability
SECTIONS: dict[str, list[str]] = {
    "heat": ["platform_profile"],
    "cpu": ["cpu_boost", "cpu_max_mhz", "epp"],
    "limits": ["ppt_pl1", "ppt_pl2", "ppt_pl3"],
    "gpu": ["gpu_dynamic_boost", "gpu_temp_target"],
    "battery": ["charge_limit"],
}


def _stored() -> dict:
    return profiles._load(FILE, {})


def is_on() -> bool:
    cfg = profiles.config()
    # installs from before the Stability page only recorded it as the active profile
    return bool(cfg.get("stability_on", cfg.get("active_profile") == "Stability"))


def settings_config() -> dict:
    """Effective config: recommended values overlaid with the user's changes."""
    stored = _stored()
    items = {}
    for key, rec in RECOMMENDED.items():
        user = stored.get("items", {}).get(key, {})
        items[key] = {"enabled": user.get("enabled", True), "value": user.get("value", rec)}
    fan = {**FANS_RECOMMENDED, **stored.get("fans", {})}
    return {"items": items, "fans": fan}


def _fan_spec(preset: str) -> dict:
    return {str(n): {"custom": True, "points": fans.PRESETS[preset]} for n in fans.FANS}


def profile() -> dict:
    cfg = settings_config()
    prof = {
        "description": profiles.BUILTIN["Stability"]["description"],
        "settings": {k: i["value"] for k, i in cfg["items"].items() if i["enabled"]},
    }
    if cfg["fans"]["enabled"] and fans.available():
        prof["fans"] = _fan_spec(cfg["fans"]["preset"])
    return prof


# --------------------------------------------------------------------------- #
# on / off
# --------------------------------------------------------------------------- #

def _snapshot_before() -> None:
    snap: dict = {"settings": sysfs.snapshot()}
    if fans.available():
        snap["fans"] = {str(f["fan"]): {"custom": f["custom"], "points": f["points"]}
                        for f in fans.read_all()["fans"]}
    profs = profiles.user_profiles()
    profs[profiles.BEFORE_STABILITY] = snap
    profiles._save("profiles.json", profs)


def _before() -> dict | None:
    return profiles.user_profiles().get(profiles.BEFORE_STABILITY)


def turn_on() -> dict:
    from . import fanmode
    if not is_on():
        _snapshot_before()  # remember what to restore when it's turned off
    prof = profile()
    results = profiles.apply_settings(prof["settings"])
    profiles.update_config(stability_on=True, active_profile="Stability")
    results.update(fanmode.enforce())  # Stability's fan option, or the Fans page mode
    return results


def _restore(keys: list[str]) -> dict:
    prev = _before()
    if not prev:
        return {}
    return profiles.apply_settings({k: v for k, v in prev["settings"].items() if k in keys})


def turn_off() -> dict:
    from . import fanmode
    cfg = settings_config()
    keys = [k for k, i in cfg["items"].items() if i["enabled"]]
    if _before():
        results = _restore(keys)
    else:
        results = profiles.apply_settings(
            {k: v for k, v in profiles.BUILTIN["Balanced"]["settings"].items() if k in keys})
    profiles.update_config(stability_on=False, active_profile=None)
    results.update(fanmode.enforce())  # back to the Fans page mode
    return results


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

def _validate(key: str, value):
    s = sysfs.SETTINGS[key]
    if key in POWER_DEPENDENT:
        if value == MIN:
            return MIN
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 250:
            return value  # fitted into the live firmware range when applied
        raise ValueError(f"{s.label}: expected watts/°C or 'lowest allowed'")
    return s.validate(value)


def _after_change(before: dict) -> dict:
    """While on: restore items that were just switched off, then re-apply."""
    if not is_on():
        return {}
    after = settings_config()
    off = [k for k in RECOMMENDED if before["items"][k]["enabled"] and not after["items"][k]["enabled"]]
    results = _restore(off)
    results.update(turn_on())  # also puts the right fans back
    return results


def update(items: dict | None = None, fan: dict | None = None) -> dict:
    before = settings_config()
    stored = _stored()
    for key, patch in (items or {}).items():
        if key not in RECOMMENDED:
            raise ValueError(f"'{key}' is not part of Stability mode")
        if not isinstance(patch, dict):
            raise ValueError("each item must be an object")
        entry = stored.setdefault("items", {}).setdefault(key, {})
        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                raise ValueError("enabled must be true/false")
            entry["enabled"] = patch["enabled"]
        if "value" in patch:
            entry["value"] = _validate(key, patch["value"])
    if fan:
        entry = stored.setdefault("fans", {})
        if "enabled" in fan:
            if not isinstance(fan["enabled"], bool):
                raise ValueError("enabled must be true/false")
            entry["enabled"] = fan["enabled"]
        if "preset" in fan:
            if fan["preset"] not in fans.PRESETS:
                raise ValueError(f"unknown fan preset '{fan['preset']}'")
            entry["preset"] = fan["preset"]
    profiles._save(FILE, stored)
    return _after_change(before)


def reset(key: str | None = None) -> dict:
    """Back to recommended: one item, the fans ('fans'), or everything (None)."""
    before = settings_config()
    stored = _stored()
    if key is None:
        stored = {}
    elif key == "fans":
        stored.pop("fans", None)
    elif key in RECOMMENDED:
        stored.get("items", {}).pop(key, None)
    else:
        raise ValueError(f"'{key}' is not part of Stability mode")
    profiles._save(FILE, stored)
    return _after_change(before)


def set_section(section: str, mode: str) -> dict:
    """Switch a whole page section:
    - "default":   leave it out of Stability mode and put its factory (first-boot)
                   values back right away;
    - "stability": include it in Stability mode (applied now if Stability mode is on).
    """
    from . import fanmode
    if mode not in ("default", "stability"):
        raise ValueError("mode must be 'default' or 'stability'")
    if section != "fans" and section not in SECTIONS:
        raise ValueError(f"unknown section '{section}'")
    include = mode == "stability"
    stored = _stored()
    results: dict = {}
    if section == "fans":
        stored.setdefault("fans", {})["enabled"] = include
        profiles._save(FILE, stored)
        if not include:
            profiles.update_config(fan_mode="default", fan_custom=None)  # factory automatic fans
            results.update(fanmode.enforce())
        elif is_on():
            results.update(fanmode.enforce())
        return results

    keys = SECTIONS[section]
    for key in keys:
        stored.setdefault("items", {}).setdefault(key, {})["enabled"] = include
    profiles._save(FILE, stored)
    if not include:
        results.update(profiles.apply_settings(
            {k: profiles.DEFAULT for k in keys if sysfs.SETTINGS[k].available()}))
    if is_on():
        # re-apply the included sections too: e.g. a performance-mode change
        # makes the firmware reload its power limits
        results.update(turn_on())
    return results


def _section_mode(keys: list[str], cfg: dict) -> str:
    avail = [k for k in keys if sysfs.SETTINGS[k].available()]
    if not avail:
        return "unsupported"
    enabled = [cfg["items"][k]["enabled"] for k in avail]
    return "stability" if all(enabled) else "default" if not any(enabled) else "mixed"


def set_boot(enabled: bool) -> None:
    cfg = profiles.config()
    if enabled:
        profiles.set_boot("Stability")
    elif cfg.get("boot_profile") == "Stability":
        profiles.set_boot(None)


# --------------------------------------------------------------------------- #
# status for the page
# --------------------------------------------------------------------------- #

def state() -> dict:
    cfg = settings_config()
    on = is_on()
    prev = _before() if on else None
    items = []
    for key, rec in RECOMMENDED.items():
        s = sysfs.SETTINGS[key]
        d = s.describe()
        item = cfg["items"][key]
        target = note = skip = None
        if d["available"]:
            try:
                target, note = profiles.resolve(key, item["value"])
            except profiles.Skip as e:
                skip = str(e)
        if not d["available"]:
            status = "unsupported"
        elif not on:
            status = None
        elif not item["enabled"]:
            status = "off"
        elif skip:
            status = "firmware"
        else:
            status = "applied" if d["value"] == target else "different"
        items.append({
            "key": key, "enabled": item["enabled"], "value": item["value"],
            "recommended": rec, "customized": item["enabled"] is not True or item["value"] != rec,
            "target": target, "note": (note or "").lstrip(": ") or None, "skip": skip, "status": status,
            "restore": prev["settings"].get(key) if prev and item["enabled"] else None,
            "setting": d,
        })

    fan_status = None
    if on and cfg["fans"]["enabled"] and fans.available():
        want = fans.PRESETS[cfg["fans"]["preset"]]
        now = fans.read_all()["fans"]
        fan_status = "applied" if all(f["custom"] and f["points"] == want for f in now) else "different"
    sections = [{"id": sid, "keys": keys, "mode": _section_mode(keys, cfg)} for sid, keys in SECTIONS.items()]
    return {
        "on": on,
        "sections": sections,
        "boot": profiles.config().get("boot_profile") == "Stability",
        "power_source": profiles._power_source(),
        "items": items,
        "fans": {**cfg["fans"], "recommended": FANS_RECOMMENDED, "presets": fans.PRESETS,
                 "available": fans.available(), "status": fan_status},
        "enabled_count": sum(1 for i in items if i["enabled"] and i["setting"]["available"])
        + (1 if cfg["fans"]["enabled"] else 0),
        "total_count": sum(1 for i in items if i["setting"]["available"]) + (1 if fans.available() else 0),
    }
