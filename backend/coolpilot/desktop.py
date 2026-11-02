"""Reach from the root backend into a logged-in user's desktop session.

Shared by alerts.py (desktop notifications) and hw/pointer.py (touchpad/mouse
speed): the backend runs as root, but notifications and compositor/DE IPC
(hyprctl, swaymsg, gsettings, ...) only work inside the target user's own
session (its D-Bus bus, its compositor socket), addressed through
/run/user/<uid>.
"""
from __future__ import annotations

import os
import pwd
import subprocess

RUN_USER = os.environ.get("COOLPILOT_RUN_USER", "/run/user")
UID_MIN = 1000


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


def run_as_user(uid: int, rundir: str, cmd: list[str], extra_env: dict | None = None,
                 timeout: float = 5) -> subprocess.CompletedProcess | None:
    """Run cmd inside uid's session (its XDG_RUNTIME_DIR/D-Bus bus), as that user
    when we're root. Returns None if the uid/home can't be resolved or the
    command can't be started."""
    try:
        home = pwd.getpwuid(uid).pw_dir
        gid = pwd.getpwuid(uid).pw_gid
    except KeyError:
        return None
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": home,
           "XDG_RUNTIME_DIR": rundir, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={rundir}/bus"}
    if extra_env:
        env.update(extra_env)
    as_user = {"user": uid, "group": gid, "extra_groups": []} if os.geteuid() == 0 and uid != 0 else {}
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, **as_user)
    except (OSError, subprocess.SubprocessError):
        return None
