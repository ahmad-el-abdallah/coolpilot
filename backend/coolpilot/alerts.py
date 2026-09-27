"""Desktop alerts: CPU too hot, a burst of PCIe link errors, Stability mode
switched off, and "the laptop froze or restarted" after a crash.

The black box calls check() with every reading. Alerts go to every logged-in
desktop user as a normal notification, and are kept in a short list the web app
shows. Each alert can be switched off, and all of them at once.

Delivery works on any systemd distro and desktop: the service runs as root, so
it talks to each user's session bus (/run/user/<uid>/bus) as that user, with
notify-send (libnotify) when installed, else gdbus (GLib) or busctl (systemd,
always there). All of them speak the freedesktop Notifications protocol that
GNOME, KDE, mako, dunst, swaync, etc. implement.
"""
from __future__ import annotations

import collections
import os
import pwd
import shutil
import subprocess
import threading
import time

from . import profiles

RUN_USER = os.environ.get("COOLPILOT_RUN_USER", "/run/user")
UID_MIN = 1000
RECENT_MAX = 30
PENDING_MAX_AGE = 3600  # an alert that found no desktop is retried for an hour (e.g. a crash alert at boot)

KINDS: dict[str, dict] = {
    "cpu_hot": {
        "label": "CPU too hot",
        "help": "When the CPU stays at or above this temperature for a few seconds.",
        "threshold": 90, "min": 70, "max": 105, "unit": "°C", "cooldown": 600,
    },
    "pcie_burst": {
        "label": "Burst of GPU link errors",
        "help": "When the CPU ↔ GPU PCIe link has this many corrected errors within one minute. "
                "Bursts often come with moving or pressing the laptop, and can mean the fault is getting worse.",
        "threshold": 100, "min": 5, "max": 5000, "unit": "errors/min", "cooldown": 900,
    },
    "stability_off": {
        "label": "Stability mode switched off",
        "help": "When Stability mode turns off, so the freeze protection isn't active any more.",
        "cooldown": 0,
    },
    "crash": {
        "label": "Freeze or unexpected restart",
        "help": "After the laptop comes back from a freeze or reset, with a link to what the black box recorded.",
        "cooldown": 0,
    },
}
SUSTAIN = 3  # readings in a row above the CPU threshold (~6 s) before alerting
SHOW_MS = 15000  # how long a pop-up stays (servers may use their own timing)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #

def settings() -> dict:
    """Effective settings: defaults overlaid with what the user changed."""
    stored = profiles.config().get("alerts") or {}
    items = {}
    for kind, spec in KINDS.items():
        user = (stored.get("items") or {}).get(kind) or {}
        item = {"enabled": user.get("enabled", True) is not False}
        if "threshold" in spec:
            t = user.get("threshold", spec["threshold"])
            item["threshold"] = t if isinstance(t, int) and spec["min"] <= t <= spec["max"] else spec["threshold"]
        items[kind] = item
    return {"enabled": stored.get("enabled", True) is not False, "items": items}


def normalize(body: dict) -> dict:
    """Validate an update ({enabled?, items?: {kind: {enabled?, threshold?}}}) -> stored form."""
    cur = settings()
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("enabled must be true or false")
        cur["enabled"] = body["enabled"]
    for kind, upd in (body.get("items") or {}).items():
        if kind not in KINDS or not isinstance(upd, dict):
            raise ValueError(f"unknown alert '{kind}'")
        spec = KINDS[kind]
        if "enabled" in upd:
            if not isinstance(upd["enabled"], bool):
                raise ValueError(f"{spec['label']}: enabled must be true or false")
            cur["items"][kind]["enabled"] = upd["enabled"]
        if "threshold" in upd:
            t = upd["threshold"]
            if "threshold" not in spec:
                raise ValueError(f"{spec['label']} has no threshold")
            if isinstance(t, bool) or not isinstance(t, int) or not spec["min"] <= t <= spec["max"]:
                raise ValueError(f"{spec['label']}: threshold must be {spec['min']}-{spec['max']} {spec['unit']}")
            cur["items"][kind]["threshold"] = t
    return cur


def update(body: dict) -> dict:
    profiles.update_config(alerts=normalize(body))
    return state()


def is_on(kind: str) -> bool:
    s = settings()
    return s["enabled"] and s["items"][kind]["enabled"]


# --------------------------------------------------------------------------- #
# delivery
# --------------------------------------------------------------------------- #

def sessions() -> list[tuple[int, str]]:
    """(uid, runtime dir) of every logged-in user with a session bus."""
    me = os.geteuid()
    out = []
    try:
        names = sorted(os.listdir(RUN_USER))
    except OSError:
        return []
    for name in names:
        if not name.isdigit():
            continue
        uid = int(name)
        # root: every real user (not gdm/sddm greeters); a normal user: only themselves
        if (uid >= UID_MIN if me == 0 else uid == me) and os.path.exists(os.path.join(RUN_USER, name, "bus")):
            out.append((uid, os.path.join(RUN_USER, name)))
    return out


def _commands(title: str, body: str, critical: bool) -> list[list[str]]:
    urgency = "critical" if critical else "normal"
    cmds = []
    if shutil.which("notify-send"):
        cmds.append(["notify-send", "-a", "CoolPilot", "-i", "coolpilot", "-u", urgency, title, body])
    if shutil.which("gdbus"):
        cmds.append(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                     "--object-path", "/org/freedesktop/Notifications",
                     "--method", "org.freedesktop.Notifications.Notify",
                     "CoolPilot", "0", "coolpilot", title, body, "[]",
                     f"{{'urgency': <byte {2 if critical else 1}>}}", str(SHOW_MS)])
    if shutil.which("busctl"):
        cmds.append(["busctl", "--user", "call", "org.freedesktop.Notifications",
                     "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
                     "susssasa{sv}i", "CoolPilot", "0", "coolpilot", title, body,
                     "0", "1", "urgency", "y", "2" if critical else "1", str(SHOW_MS)])
    return cmds


def _send_one(uid: int, rundir: str, title: str, body: str, critical: bool) -> str | None:
    """Show one notification on one user's desktop -> the tool that worked, or None."""
    try:
        home = pwd.getpwuid(uid).pw_dir
        gid = pwd.getpwuid(uid).pw_gid
    except KeyError:
        return None
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": home,
           "XDG_RUNTIME_DIR": rundir, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={rundir}/bus"}
    as_user = {"user": uid, "group": gid, "extra_groups": []} if os.geteuid() == 0 and uid != 0 else {}
    for cmd in _commands(title, body, critical):
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, timeout=5, **as_user)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return cmd[0]
    return None


def deliver(title: str, body: str, critical: bool = False) -> dict:
    sent, tools = 0, set()
    for uid, rundir in sessions():
        tool = _send_one(uid, rundir, title, body, critical)
        if tool:
            sent += 1
            tools.add(tool)
    return {"sent": sent, "via": sorted(tools)}


# --------------------------------------------------------------------------- #
# the recent list + retrying alerts nobody saw yet
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_pending: list[dict] = []


def _remember(alert: dict) -> None:
    with _lock:
        recent = [a for a in (profiles.config().get("alerts_recent") or []) if a.get("id") != alert["id"]]
        profiles.update_config(alerts_recent=([alert] + recent)[:RECENT_MAX])


def fire(kind: str, title: str, body: str, critical: bool = False, now: float | None = None) -> dict:
    """Record an alert and show it on the desktop (in the background)."""
    now = now or time.time()
    alert = {"id": f"{kind}-{int(now * 1000)}", "kind": kind, "ts": now, "title": title, "body": body,
             "critical": critical, "sent": None}
    _remember(alert)
    _deliver_later(alert)
    return alert


def _deliver_later(alert: dict) -> None:
    def run():
        r = deliver(alert["title"], alert["body"], alert["critical"])
        if r["sent"]:
            alert["sent"] = time.time()
            _remember(alert)
        elif time.time() - alert["ts"] < PENDING_MAX_AGE:
            with _lock:
                _pending.append(alert)
    threading.Thread(target=run, name="alert", daemon=True).start()


def retry_pending() -> None:
    """Show alerts that found no desktop earlier (called every minute or so)."""
    with _lock:
        todo = [a for a in _pending if time.time() - a["ts"] < PENDING_MAX_AGE]
        _pending.clear()
    if todo and sessions():
        for a in todo:
            _deliver_later(a)
    else:
        with _lock:
            _pending.extend(todo)


def recent() -> list[dict]:
    return profiles.config().get("alerts_recent") or []


def clear_recent() -> None:
    profiles.update_config(alerts_recent=[])


def test() -> dict:
    r = deliver("CoolPilot test alert", "Desktop alerts work. You can change them on the Alerts page.")
    if not r["sent"]:
        r["error"] = ("no desktop session could show it" if sessions()
                      else "no logged-in desktop session found")
    return r


def state() -> dict:
    s = settings()
    return {
        "enabled": s["enabled"],
        "items": [{"kind": k, **{f: v for f, v in spec.items() if f != "cooldown"}, **s["items"][k]}
                  for k, spec in KINDS.items()],
        "recent": recent(),
        "desktops": len(sessions()),
        "tools": [t for t in ("notify-send", "gdbus", "busctl") if shutil.which(t)],
    }


# --------------------------------------------------------------------------- #
# watching the readings (called by the black box)
# --------------------------------------------------------------------------- #

class Watcher:
    def __init__(self):
        self.hot_run = 0
        self.hot_armed = True
        self.last: dict[str, float] = {}
        self.pcie: collections.deque[tuple[float, int]] = collections.deque()
        self.stability: bool | None = None

    def _cooled(self, kind: str, now: float) -> bool:
        return now - self.last.get(kind, 0) >= KINDS[kind]["cooldown"]

    def check(self, s: dict, now: float | None = None) -> list[dict]:
        now = now or s.get("ts") or time.time()
        cfg = settings()
        fired = []
        on = lambda k: cfg["enabled"] and cfg["items"][k]["enabled"]  # noqa: E731

        # CPU too hot: a few readings in a row, then not again until it cooled down 5 °C
        temp, limit = s.get("cpu_temp"), cfg["items"]["cpu_hot"]["threshold"]
        if temp is not None:
            self.hot_run = self.hot_run + 1 if temp >= limit else 0
            if temp < limit - 5:
                self.hot_armed = True
            if on("cpu_hot") and self.hot_run >= SUSTAIN and self.hot_armed and self._cooled("cpu_hot", now):
                self.hot_armed = False
                self.last["cpu_hot"] = now
                fired.append(fire("cpu_hot", f"CPU is very hot: {round(temp)}°C",
                                  "Put the laptop on a hard, flat surface and let it cool down. "
                                  "Stability mode or Silent mode lower the heat.", critical=True, now=now))

        # PCIe error burst: corrected errors on the GPU link within the last minute
        self.pcie.append((now, int(s.get("pcie_new") or 0)))
        while self.pcie and now - self.pcie[0][0] > 60:
            self.pcie.popleft()
        errors = sum(n for _, n in self.pcie)
        if on("pcie_burst") and errors >= cfg["items"]["pcie_burst"]["threshold"] and self._cooled("pcie_burst", now):
            self.last["pcie_burst"] = now
            self.pcie.clear()
            fired.append(fire("pcie_burst", f"{errors} GPU link errors in the last minute",
                              "The CPU ↔ GPU link had to resend corrupted data. Was the laptop moved or pressed? "
                              "The PCIe page shows details.", now=now))

        # Stability mode on -> off (not at startup)
        stab = bool(s.get("stability"))
        if self.stability and not stab and on("stability_off"):
            fired.append(fire("stability_off", "Stability mode is off",
                              "The freeze protection isn't active any more. Turn it back on from CoolPilot "
                              "or with: coolpilot stability on", now=now))
        self.stability = stab
        return fired

    def crash(self, event: dict | None, now: float | None = None) -> dict | None:
        """Alert once about a crash that ended a recent session (called at startup)."""
        now = now or time.time()
        if not event or not is_on("crash") or now - event["end"] > 86400:
            return None
        cfg = profiles.config()
        if event["boot"] in (cfg.get("crash_alerted"), cfg.get("crash_seen")):
            return None
        profiles.update_config(crash_alerted=event["boot"])
        when = time.strftime("%H:%M on %b %d", time.localtime(event["end"]))
        return fire("crash", "The laptop froze or restarted",
                    f"It stopped unexpectedly at {when}. Open CoolPilot → History to see what the black box "
                    "recorded in its last 2 minutes.", critical=True, now=now)
