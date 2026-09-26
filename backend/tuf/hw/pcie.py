"""PCIe link health: link speed/width and AER error counters per device, plus a
live "press test" watcher for the CPU <-> GPU link (web version of
~/crashdiag/pcie-watch.sh).

Corrected errors (e.g. BadTLP) mean a packet arrived corrupted and the hardware
resent it. A link whose lanes run through weak solder joints shows them in
bursts, especially when the board is flexed.
"""
from __future__ import annotations

import collections
import os
import shutil
import signal
import subprocess
import threading
import time

from .. import diag
from . import sysfs

PCI = "sys/bus/pci/devices"
INTERVAL = float(os.environ.get("TUF_PCIE_INTERVAL", "1"))
GENS = {"2.5": 1, "5.0": 2, "8.0": 3, "16.0": 4, "32.0": 5, "64.0": 6}

_names: dict[str, str] = {}


def _dev(bdf: str) -> str:
    return f"{PCI}/{bdf}"


def _counts(bdf: str, kind: str) -> dict[str, int] | None:
    """Parse aer_dev_{correctable,nonfatal,fatal}: 'Type N' lines."""
    raw = sysfs.read(f"{_dev(bdf)}/aer_dev_{kind}")
    if raw is None:
        return None
    out = {}
    for line in raw.splitlines():
        k, _, v = line.rpartition(" ")
        if k and v.isdigit():
            out[k] = int(v)
    return out


def _total(counts: dict[str, int] | None) -> int:
    if not counts:
        return 0
    if any(k.startswith("TOTAL") for k in counts):
        return sum(v for k, v in counts.items() if k.startswith("TOTAL"))
    return sum(counts.values())


def _speed(bdf: str, f: str) -> float | None:
    raw = sysfs.read(f"{_dev(bdf)}/{f}")
    try:
        return float(raw.split()[0]) if raw else None
    except ValueError:
        return None


def _parent(bdf: str) -> str | None:
    real = os.path.realpath(os.path.join(sysfs.ROOT, _dev(bdf)))
    up = os.path.basename(os.path.dirname(real))
    return up if up.count(":") == 2 else None


def _label(bdf: str) -> str:
    cls = (sysfs.read(f"{_dev(bdf)}/class") or "")[2:]
    vendor = sysfs.read(f"{_dev(bdf)}/vendor")
    nvidia = vendor == "0x10de"
    if cls.startswith("03"):
        return "NVIDIA GPU" if nvidia else "GPU"
    if cls.startswith("0403"):
        return "NVIDIA GPU audio" if nvidia else "Audio"
    if cls.startswith("0108"):
        return "NVMe SSD"
    if cls.startswith("0280"):
        return "Wi-Fi"
    if cls.startswith("0200"):
        return "Ethernet"
    if cls.startswith("0604"):
        return "CPU root port"
    return "Device"


def _name(bdf: str) -> str:
    if bdf not in _names:
        name = ""
        if shutil.which("lspci"):
            try:
                out = subprocess.run(["lspci", "-s", bdf], capture_output=True, text=True, timeout=3).stdout
                name = out.split(": ", 1)[1].strip() if ": " in out else ""
            except (OSError, subprocess.TimeoutExpired):
                pass
        _names[bdf] = name
    return _names[bdf]


def gpu_bdf() -> str | None:
    for d in sysfs.glob_rel(f"{PCI}/*"):
        bdf = os.path.basename(d)
        if sysfs.read(f"{d}/vendor") == "0x10de" and (sysfs.read(f"{d}/class") or "").startswith("0x03"):
            return bdf
    return None


def links() -> list[dict]:
    devs = [os.path.basename(d) for d in sysfs.glob_rel(f"{PCI}/*")
            if sysfs.exists(f"{d}/aer_dev_correctable") and sysfs.exists(f"{d}/current_link_speed")]
    children: dict[str, list[str]] = collections.defaultdict(list)
    for bdf in devs:
        p = _parent(bdf)
        if p:
            children[p].append(bdf)
    out = []
    for bdf in devs:
        corr, nonfatal, fatal = (_counts(bdf, k) for k in ("correctable", "nonfatal", "fatal"))
        label = _label(bdf)
        if label == "CPU root port" and children.get(bdf):
            label = f"CPU root port → {_label(children[bdf][0])}"
        speed, max_speed = _speed(bdf, "current_link_speed"), _speed(bdf, "max_link_speed")
        width, max_width = sysfs.read_int(f"{_dev(bdf)}/current_link_width"), sysfs.read_int(f"{_dev(bdf)}/max_link_width")
        out.append({
            "bdf": bdf, "label": label, "name": _name(bdf), "parent": _parent(bdf),
            "speed": speed, "max_speed": max_speed,
            "gen": GENS.get(f"{speed}"), "max_gen": GENS.get(f"{max_speed}"),
            "width": width, "max_width": max_width,
            "lanes_lost": bool(width and max_width and width < max_width),
            "correctable": _total(corr), "nonfatal": _total(nonfatal), "fatal": _total(fatal),
            "breakdown": {k: v for k, v in (corr or {}).items() if v and not k.startswith("TOTAL")},
        })
    # GPU chain first, then the rest
    gpu = gpu_bdf()
    gpu_root = _parent(gpu) if gpu else None
    out.sort(key=lambda link: (link["bdf"] != gpu_root and link["parent"] != gpu_root, link["bdf"]))
    return out


# --------------------------------------------------------------------------- #
# Live watch (press test)
# --------------------------------------------------------------------------- #

class _Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.load: subprocess.Popen | None = None
        self.samples: collections.deque[dict] = collections.deque(maxlen=3600)
        self.marks: list[dict] = []
        self.area = "baseline"
        self.started = None
        self.ends = None
        self.base = 0
        self.log_path = None
        self.bdf = None


_w = _Watch()


def _log(line: str) -> None:
    if not _w.log_path:
        return
    with open(_w.log_path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _sample_loop() -> None:
    bdf = _w.bdf
    root = _parent(bdf) if bdf else None
    prev = _total(_counts(bdf, "correctable"))
    prev_root = _total(_counts(root, "correctable")) if root else 0
    while not _w.stop_evt.wait(INTERVAL):
        now = _total(_counts(bdf, "correctable"))
        now_root = _total(_counts(root, "correctable")) if root else 0
        s = {
            "t": time.time(), "area": _w.area,
            "speed": _speed(bdf, "current_link_speed"),
            "width": sysfs.read_int(f"{_dev(bdf)}/current_link_width"),
            "total": now, "delta": max(0, now - prev), "root_delta": max(0, now_root - prev_root),
        }
        prev, prev_root = now, now_root
        with _w.lock:
            _w.samples.append(s)
        _log(f"{time.strftime('%H:%M:%S')}  {s['speed']}GT/s x{s['width']}  total={now}  +{s['delta']}"
             f"  root+{s['root_delta']}  area={s['area']}")
        if _w.ends and time.time() >= _w.ends:
            break
    _stop_load()
    _log(f"{time.strftime('%H:%M:%S')}  == stopped")


def _start_load() -> str | None:
    if not shutil.which("glmark2"):
        return "glmark2 not installed - watching without GPU load"
    cmd = diag._as_user(["env", "__NV_PRIME_RENDER_OFFLOAD=1", "__GLX_VENDOR_LIBRARY_NAME=nvidia",
                         "glmark2", "--off-screen", "--run-forever", "-s", "800x600"])
    _w.load = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    return None


def _stop_load() -> None:
    p, _w.load = _w.load, None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def running() -> bool:
    return bool(_w.thread and _w.thread.is_alive())


def start(load: bool = True, minutes: int = 30) -> dict:
    if running():
        raise RuntimeError("watch already running")
    if not isinstance(minutes, int) or not 1 <= minutes <= 240:
        raise RuntimeError("minutes must be 1-240")
    bdf = gpu_bdf()
    if not bdf or _counts(bdf, "correctable") is None:
        raise RuntimeError("GPU PCIe error counters not available")
    os.makedirs(diag.LOGS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    _w.log_path = os.path.join(diag.LOGS, f"{stamp}-pcie.log")
    if os.getuid() == 0:
        with open(_w.log_path, "a"):
            pass
        os.chown(_w.log_path, diag._pw.pw_uid, diag._pw.pw_gid)
    _w.bdf = bdf
    _w.samples.clear()
    _w.marks = []
    _w.area = "baseline"
    _w.started = time.time()
    _w.ends = _w.started + minutes * 60
    _w.base = _total(_counts(bdf, "correctable"))
    _w.stop_evt.clear()
    _log(f"== PCIe watch {bdf}  start total={_w.base}  load={'on' if load else 'off'}")
    note = _start_load() if load else None
    _w.thread = threading.Thread(target=_sample_loop, daemon=True)
    _w.thread.start()
    st = status()
    st["note"] = note
    return st


def stop() -> dict:
    _w.stop_evt.set()
    if _w.thread:
        _w.thread.join(timeout=INTERVAL + 6)
    _stop_load()
    return status()


def mark(area: str) -> dict:
    if not running():
        raise RuntimeError("watch not running")
    name = diag.AREAS.get(area)
    if not name:
        raise RuntimeError("unknown area")
    with _w.lock:
        _w.area = name
        _w.marks.append({"t": time.time(), "area": name})
    _log(f"{time.strftime('%H:%M:%S')}  >>> now pressing: {name}")
    return {"ok": True, "area": name}


def status(history: int = 180) -> dict:
    with _w.lock:
        samples = list(_w.samples)
    per_area: dict[str, dict] = {}
    for s in samples:
        a = per_area.setdefault(s["area"], {"area": s["area"], "seconds": 0.0, "errors": 0, "bursts": 0})
        a["seconds"] += INTERVAL
        a["errors"] += s["delta"]
        a["bursts"] += 1 if s["delta"] else 0
    for a in per_area.values():
        a["per_min"] = round(a["errors"] / (a["seconds"] / 60), 1) if a["seconds"] else 0
        a["seconds"] = round(a["seconds"])
    last60 = sum(s["delta"] for s in samples if s["t"] >= time.time() - 60)
    return {
        "running": running(),
        "load": bool(_w.load and _w.load.poll() is None),
        "started": _w.started, "ends": _w.ends, "area": _w.area if running() else None,
        "bdf": _w.bdf, "log": os.path.basename(_w.log_path) if _w.log_path else None,
        "errors_since_start": (samples[-1]["total"] - _w.base) if samples else 0,
        "errors_last_min": last60,
        "samples": samples[-history:], "marks": _w.marks[-50:],
        "per_area": sorted(per_area.values(), key=lambda a: -a["per_min"]),
    }


# --------------------------------------------------------------------------- #
# Per-session history from the kernel log
# --------------------------------------------------------------------------- #

_hist_cache: dict[str, int] = {}


def history() -> list[dict]:
    """Logged GPU PCIe errors per boot (the kernel rate-limits these messages,
    so this undercounts; it's for comparing sessions), joined with crash info."""
    bdf = gpu_bdf()
    if not bdf:
        return []
    out = []
    for b in diag.crash_history():
        bid = b["boot_id"]
        if b["ending"] != "running" and bid in _hist_cache:
            n = _hist_cache[bid]
        else:
            try:
                log = subprocess.run(["journalctl", "-k", "-b", bid, "-o", "cat", "--no-pager", "-q"],
                                     capture_output=True, text=True, timeout=20).stdout
                n = sum(1 for ln in log.splitlines() if bdf in ln and "PCIe Bus Error" in ln)
            except (OSError, subprocess.TimeoutExpired):
                n = -1
            if b["ending"] != "running":
                _hist_cache[bid] = n
        out.append({**b, "gpu_pcie_errors": n})
    return out


def available() -> bool:
    bdf = gpu_bdf()
    return bool(bdf and _counts(bdf, "correctable") is not None)

