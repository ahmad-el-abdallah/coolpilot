"""Fan mode that sticks: "default" (factory firmware curves), "stability" (the
anti-freeze curve) or "custom" (the user's own curves).

The firmware reloads its own curves after a reboot, after sleep and whenever the
performance mode (Silent/Balanced/Turbo) changes, so `enforce()` is called after
each of those. Stability mode's own fan option (on the Stability page) wins
while Stability mode is on.
"""
from __future__ import annotations

from . import profiles
from .hw import fans

MODES = ("default", "stability", "custom")


def mode() -> str:
    m = profiles.config().get("fan_mode", "default")
    return m if m in MODES else "default"


def _all(custom: bool, points=None) -> dict:
    return {str(n): ({"custom": True, "points": points} if custom else {"custom": False}) for n in fans.FANS}


def desired() -> tuple[dict, str]:
    """(fan spec to apply, who decides it)."""
    from . import stability
    if stability.is_on():
        sc = stability.settings_config()["fans"]
        if sc["enabled"]:
            return _all(True, fans.PRESETS[sc["preset"]]), "stability_mode"
    m = mode()
    if m == "stability":
        return _all(True, fans.PRESETS["stability"]), "stability"
    if m == "custom":
        custom = profiles.config().get("fan_custom") or {}
        if custom:
            return custom, "custom"
    return _all(False), "default"


def enforce() -> dict:
    if not fans.available():
        return {}
    spec, _ = desired()
    return profiles.apply_fans(spec)


def set_mode(m: str) -> dict:
    if m not in ("default", "stability"):
        raise ValueError("mode must be 'default' or 'stability' (edit a curve for custom)")
    profiles.update_config(fan_mode=m)
    return enforce()


def remember_custom() -> None:
    """Called after the user edits curves on the Fans page: keep them as 'custom'."""
    now = fans.read_all()["fans"]
    spec = {str(f["fan"]): {"custom": f["custom"], "points": f["points"]} for f in now}
    if any(f["custom"] for f in now):
        profiles.update_config(fan_mode="custom", fan_custom=spec)
    else:
        profiles.update_config(fan_mode="default", fan_custom=None)


def state() -> dict:
    _, by = desired()
    return {"mode": mode(), "controlled_by": by, "stability_curve": fans.PRESETS["stability"]}
