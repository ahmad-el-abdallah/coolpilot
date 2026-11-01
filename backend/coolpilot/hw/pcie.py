"""PCIe link readings: link speed/width and AER error counters per device, used by
the black box, alerts and the repair report.

Corrected errors (e.g. BadTLP) mean a packet arrived corrupted and the hardware
resent it. A link whose lanes run through weak solder joints shows them in
bursts, especially when the board is flexed.
"""
from __future__ import annotations

import collections
import os
import shutil
import subprocess

from .. import diag
from . import sysfs

PCI = "sys/bus/pci/devices"
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

