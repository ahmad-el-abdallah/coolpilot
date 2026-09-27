"""'Charge to 100% once': lift the charge limit for one full charge, then put
the normal limit back automatically.

The override lives in config (`charge_full_once`) so every path that re-applies
settings (boot, resume, charger plug/unplug, Stability mode) keeps 100% while it
is active - see profiles.resolve(). A watcher thread in the service ends it when
the battery is full, or after MAX_HOURS at the latest.
"""
from __future__ import annotations

import threading
import time

from . import profiles
from .hw import sysfs

MAX_HOURS = 24
CHECK_EVERY = 30.0


class BatteryError(ValueError):
    pass


def active() -> dict | None:
    o = profiles.config().get("charge_full_once")
    return o if isinstance(o, dict) else None


def _limit_setting():
    s = sysfs.SETTINGS["charge_limit"]
    if not s.available():
        raise BatteryError("this laptop doesn't support a battery charge limit")
    return s


def _normal_limit(o: dict | None) -> int:
    """The limit to go back to: Stability mode's, if it manages the charge limit."""
    from . import stability
    if stability.is_on():
        item = stability.settings_config()["items"]["charge_limit"]
        if item["enabled"]:
            return int(item["value"])
    return int((o or {}).get("restore") or 100)


def start() -> dict:
    s = _limit_setting()
    o = active()
    if not o:
        o = {"since": time.time(), "until": time.time() + MAX_HOURS * 3600, "restore": s.get()}
    s.set(100)
    profiles.update_config(charge_full_once=o)
    return state()


def stop(reason: str = "cancelled", restore: bool = True) -> dict:
    o = active()
    if not o:
        return state()
    profiles.update_config(charge_full_once=None, charge_full_once_last={"reason": reason, "at": time.time()})
    if restore:
        try:
            _limit_setting().set(_normal_limit(o))
        except (BatteryError, sysfs.SettingError):
            pass
    return state()


def tick(now: float | None = None) -> str | None:
    """End the override when the battery is full or the time is up."""
    o = active()
    if not o:
        return None
    now = now or time.time()
    bat = sysfs.battery()
    pct = sysfs.read_int(f"{bat}/capacity") if bat else None
    status = sysfs.read(f"{bat}/status") if bat else None
    if pct is not None and (pct >= 100 or (pct >= 99 and status in ("Full", "Not charging"))):
        stop("full")
        return "full"
    if now >= o.get("until", 0):
        stop("timeout")
        return "timeout"
    return None


def state() -> dict:
    o = active()
    bat = sysfs.battery()
    try:
        available = _limit_setting() is not None
    except BatteryError:
        available = False
    return {
        "available": available,
        "active": bool(o),
        "since": o.get("since") if o else None,
        "until": o.get("until") if o else None,
        "back_to": _normal_limit(o) if o else None,
        "percent": sysfs.read_int(f"{bat}/capacity") if bat else None,
        "last": profiles.config().get("charge_full_once_last"),
    }


_thread: threading.Thread | None = None
_stop = threading.Event()


def start_watcher() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        while not _stop.wait(CHECK_EVERY):
            try:
                tick()
            except (OSError, ValueError):
                pass

    _thread = threading.Thread(target=loop, name="charge-once", daemon=True)
    _thread.start()
