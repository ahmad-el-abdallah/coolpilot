"""Run ~/crashdiag/crashdiag.sh tests from the web UI and read crash history.

The script runs as the desktop user (not root) so its logs stay owned by them;
area marks are sent to it the same way a key press would be (its stdin).
"""
from __future__ import annotations

import collections
import glob
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import threading
import time

USER = os.environ.get("COOLPILOT_USER") or os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
_pw = pwd.getpwnam(USER)
HOME = _pw.pw_dir
DIAG_DIR = os.environ.get("COOLPILOT_DIAG_DIR") or os.path.join(HOME, "crashdiag")
SCRIPT = os.path.join(DIAG_DIR, "crashdiag.sh")
LOGS = os.path.join(DIAG_DIR, "logs")

TESTS = ["idle", "cpu", "ram", "gpu", "disk", "all"]
AREAS = {"1": "top-left", "2": "top-middle", "3": "top-right", "4": "palm-left",
         "5": "touchpad", "6": "palm-right", "7": "hinge-left", "8": "hinge-right",
         "9": "lift/tilt", "0": "not-touching"}
LOG_NAME = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z]+(\.kernel)?\.log$")
CLEAN_END = re.compile(r"Journal stopped|Stopped target|Unmounted|powering down|Reached target "
                       r"(System )?(Shutdown|Power-Off|Reboot)|systemd-shutdown|Hibernat", re.I)


class DiagError(RuntimeError):
    pass


class _Run:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.test = None
        self.minutes = None
        self.started = None
        self.output: collections.deque[str] = collections.deque(maxlen=300)
        self.lock = threading.Lock()


_run = _Run()


SESSION_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")


def session_env() -> dict[str, str]:
    """Display variables of the user's desktop session, whatever it is (GNOME,
    KDE, Hyprland, Sway, X11...). Desktops export them to the user's systemd
    manager; fall back to looking at the Wayland / X11 sockets."""
    runtime = f"/run/user/{_pw.pw_uid}"
    env: dict[str, str] = {}
    base = ["runuser", "-u", USER, "--"] if os.getuid() == 0 else []
    try:
        out = subprocess.run([*base, "env", f"XDG_RUNTIME_DIR={runtime}", "systemctl", "--user",
                              "show-environment"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            k, _, v = line.partition("=")
            if k in SESSION_VARS and v:
                env[k] = v
    except (OSError, subprocess.TimeoutExpired):
        pass
    if "WAYLAND_DISPLAY" not in env:
        socks = [x for x in sorted(glob.glob(f"{runtime}/wayland-[0-9]*")) if not x.endswith(".lock")]
        if socks:
            env["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
    if "DISPLAY" not in env:
        xs = sorted(glob.glob("/tmp/.X11-unix/X[0-9]*"))
        if xs:
            env["DISPLAY"] = ":" + os.path.basename(xs[0])[1:]
    if "DBUS_SESSION_BUS_ADDRESS" not in env and os.path.exists(f"{runtime}/bus"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    for var, override in (("DISPLAY", "COOLPILOT_DISPLAY"), ("WAYLAND_DISPLAY", "COOLPILOT_WAYLAND_DISPLAY")):
        if os.environ.get(override):
            env[var] = os.environ[override]
    return env


def _as_user(args: list[str]) -> list[str]:
    env = [f"HOME={HOME}", f"USER={USER}", f"LOGNAME={USER}", f"XDG_RUNTIME_DIR=/run/user/{_pw.pw_uid}",
           "PATH=/usr/local/bin:/usr/bin:/bin", *(f"{k}={v}" for k, v in session_env().items())]
    if os.getuid() == 0:
        return ["runuser", "-u", USER, "--", "env", *env, *args]
    return ["env", *env, *args]


def nvidia_present() -> bool:
    return os.path.exists("/proc/driver/nvidia/version") or bool(shutil.which("nvidia-smi"))


def gpu_load_cmd(size: str = "800x600") -> list[str] | None:
    """glmark2 on the discrete GPU: NVIDIA PRIME offload, or DRI_PRIME for AMD/Mesa.
    Distros ship different builds (glmark2 / -wayland / -es2), use what's there."""
    exe = next((e for e in ("glmark2", "glmark2-wayland", "glmark2-es2", "glmark2-es2-wayland")
                if shutil.which(e)), None)
    if not exe:
        return None
    offload = (["__NV_PRIME_RENDER_OFFLOAD=1", "__GLX_VENDOR_LIBRARY_NAME=nvidia"] if nvidia_present()
               else ["DRI_PRIME=1"])
    return ["env", *offload, exe, "--off-screen", "--run-forever", "-s", size]


def available() -> bool:
    return os.path.isfile(SCRIPT)


def _reader(proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        _run.output.append(line.rstrip("\n"))


def start(test: str, minutes: int) -> dict:
    if not available():
        raise DiagError(f"{SCRIPT} not found")
    if test not in TESTS:
        raise DiagError(f"unknown test '{test}'")
    if not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise DiagError("minutes must be 1-120")
    with _run.lock:
        if _run.proc and _run.proc.poll() is None:
            raise DiagError("a test is already running")
        _run.output.clear()
        _run.proc = subprocess.Popen(
            _as_user(["bash", SCRIPT, "test", test, str(minutes)]),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True, cwd=DIAG_DIR)
        _run.test, _run.minutes, _run.started = test, minutes, time.time()
        threading.Thread(target=_reader, args=(_run.proc,), daemon=True).start()
    return status()


def mark(area: str) -> dict:
    if area not in AREAS:
        raise DiagError("unknown area")
    with _run.lock:
        if not _run.proc or _run.proc.poll() is not None:
            raise DiagError("no test running")
        try:
            _run.proc.stdin.write(area)  # type: ignore[union-attr]
            _run.proc.stdin.flush()  # type: ignore[union-attr]
        except OSError as e:
            raise DiagError(f"could not send mark: {e}") from e
    return {"ok": True, "area": AREAS[area]}


def stop() -> dict:
    with _run.lock:
        proc = _run.proc
    if not proc or proc.poll() is not None:
        return status()
    try:
        os.killpg(proc.pid, signal.SIGINT)  # the script's trap stops the load and logs "survived"
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return status()


def status() -> dict:
    proc = _run.proc
    running = bool(proc and proc.poll() is None)
    return {
        "available": available(),
        "running": running,
        "test": _run.test, "minutes": _run.minutes, "started": _run.started,
        "output": list(_run.output)[-40:],
        "tests": TESTS, "areas": AREAS,
    }


def logs() -> list[dict]:
    if not os.path.isdir(LOGS):
        return []
    out = []
    for name in sorted(os.listdir(LOGS), reverse=True):
        if LOG_NAME.match(name) and ".kernel" not in name:
            path = os.path.join(LOGS, name)
            with open(path, errors="replace") as f:
                lines = f.read().splitlines()
            out.append({"name": name, "test": name.split("-")[2].split(".")[0],
                        "lines": len(lines), "marks": sum("MARK" in ln for ln in lines),
                        "first": lines[1][:10] if len(lines) > 1 else None,
                        "last": lines[-1][:10] if len(lines) > 1 else None})
    return out


def read_log(name: str, tail: int = 200) -> dict:
    if not LOG_NAME.match(name):
        raise DiagError("bad log name")
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        raise DiagError("no such log")
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()
    kpath = path[:-4] + ".kernel.log"
    kernel = []
    if os.path.isfile(kpath):
        with open(kpath, errors="replace") as f:
            kernel = f.read().splitlines()[-50:]
    return {"name": name, "header": lines[:1], "lines": lines[1:][-tail:], "kernel": kernel}


def results() -> list[str]:
    try:
        with open(os.path.join(DIAG_DIR, "results.txt")) as f:
            return f.read().splitlines()
    except OSError:
        return []


def report() -> str:
    if not available():
        raise DiagError(f"{SCRIPT} not found")
    # run as root (only reads): the kernel log sections need journal access, which
    # a normal user doesn't have on every distro
    r = subprocess.run(["bash", SCRIPT, "report"], capture_output=True, text=True, timeout=30,
                       cwd=DIAG_DIR, env={**os.environ, "HOME": HOME})
    return r.stdout + r.stderr


_boot_cache: dict[str, dict] = {}
BOOT_LINE = re.compile(r"^\s*(-?\d+)\s+([0-9a-f]{32})\s+\w{3},? (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
                       r".*?\w{3},? (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _list_boots() -> list[dict]:
    """journalctl --list-boots as dicts. JSON output needs systemd 251+; older
    systemd (Ubuntu 22.04, Debian 11...) only prints text, so parse that."""
    try:
        r = subprocess.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"],
                           capture_output=True, text=True, timeout=15)
        boots = json.loads(r.stdout)
        if isinstance(boots, list):
            return boots
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(["journalctl", "--list-boots", "--no-pager"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        m = BOOT_LINE.match(line)
        if m:
            t0, t1 = (time.mktime(time.strptime(x, "%Y-%m-%d %H:%M:%S")) for x in (m[3], m[4]))
            out.append({"index": int(m[1]), "boot_id": m[2], "first_entry": t0 * 1e6, "last_entry": t1 * 1e6})
    return out


def crash_history(limit: int = 30) -> list[dict]:
    """Boots whose journal ends without any shutdown messages = crash/freeze/reset."""
    boots = _list_boots()
    out = []
    for b in boots[-limit:]:
        bid = b.get("boot_id")
        info = {"index": b.get("index"), "boot_id": bid,
                "start": b.get("first_entry", 0) / 1e6, "end": b.get("last_entry", 0) / 1e6}
        if b.get("index") == 0:
            info["ending"] = "running"
        elif bid in _boot_cache:
            info["ending"] = _boot_cache[bid]["ending"]
        else:
            try:
                t = subprocess.run(["journalctl", "-b", bid, "-n", "80", "-o", "cat", "--no-pager"],
                                   capture_output=True, text=True, timeout=15).stdout
                info["ending"] = "clean" if CLEAN_END.search(t) else "crash"
            except (OSError, subprocess.TimeoutExpired):
                info["ending"] = "unknown"
            _boot_cache[bid] = {"ending": info["ending"]}
        info["minutes"] = round((info["end"] - info["start"]) / 60, 1)
        out.append(info)
    return list(reversed(out))
