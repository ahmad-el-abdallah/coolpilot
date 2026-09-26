"""Live sensor snapshot (same sources as ~/crashdiag/crashdiag.sh)."""
from __future__ import annotations

import os
import time

from . import gpu, sysfs

BAT = "sys/class/power_supply/BAT1"
AC = "sys/class/power_supply/ACAD"

_prev_stat: tuple[int, int] | None = None


def _milli(rel: str) -> float | None:
    v = sysfs.read_int(rel)
    return round(v / 1000, 1) if v is not None else None


def _cpu_usage() -> float | None:
    """Percent busy across all cores since the previous call."""
    global _prev_stat
    line = sysfs.read("proc/stat")
    if not line:
        return None
    parts = [int(x) for x in line.splitlines()[0].split()[1:]]
    idle, total = parts[3] + parts[4], sum(parts)
    prev, _prev_stat = _prev_stat, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def _cpu_mhz() -> dict:
    freqs = [sysfs.read_int(x) for x in sysfs.cpu_policies("scaling_cur_freq")]
    freqs = [f for f in freqs if f]
    if not freqs:
        return {"avg": None, "max": None}
    return {"avg": round(sum(freqs) / len(freqs) / 1000), "max": round(max(freqs) / 1000)}


def snapshot() -> dict:
    cpu = sysfs.find_hwmon("k10temp")
    nvme = sysfs.find_hwmon("nvme")
    asus = sysfs.find_hwmon("asus")
    spd = sysfs.find_all_hwmon("spd5118")

    volt = sysfs.read_int(f"{BAT}/voltage_now")
    power = sysfs.read_int(f"{BAT}/power_now")
    if not power:
        cur = sysfs.read_int(f"{BAT}/current_now")
        power = cur * volt // 1_000_000 if cur and volt else None

    load = (sysfs.read("proc/loadavg") or "0 0 0").split()[:3]
    mhz = _cpu_mhz()
    return {
        "time": time.time(),
        "cpu": {
            "temp": _milli(f"{cpu}/temp1_input") if cpu else None,
            "mhz": mhz["avg"], "mhz_max": mhz["max"],
            "usage": _cpu_usage(),
            "load": [float(x) for x in load],
            "cores": os.cpu_count(),
        },
        "ram": {
            "temps": [_milli(f"{d}/temp1_input") for d in spd],
            **_meminfo(),
        },
        "ssd": {"temp": _milli(f"{nvme}/temp1_input") if nvme else None},
        "fans": [sysfs.read_int(f"{asus}/fan{i}_input") for i in (1, 2)] if asus else [],
        "battery": {
            "percent": sysfs.read_int(f"{BAT}/capacity"),
            "status": sysfs.read(f"{BAT}/status"),
            "volts": round(volt / 1e6, 2) if volt else None,
            "watts": round(power / 1e6, 1) if power else None,
            "ac": sysfs.read(f"{AC}/online") == "1",
            "health": _battery_health(),
        },
        "gpu": gpu.snapshot(),
        "profile": sysfs.read("sys/firmware/acpi/platform_profile"),
    }


def _meminfo() -> dict:
    info = {}
    for line in (sysfs.read("proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            info[k] = int(v.split()[0])
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if not total:
        return {"used_gb": None, "total_gb": None}
    return {"used_gb": round((total - (avail or 0)) / 1048576, 1), "total_gb": round(total / 1048576, 1)}


def _battery_health() -> float | None:
    full = sysfs.read_int(f"{BAT}/energy_full") or sysfs.read_int(f"{BAT}/charge_full")
    design = sysfs.read_int(f"{BAT}/energy_full_design") or sysfs.read_int(f"{BAT}/charge_full_design")
    return round(100 * full / design, 1) if full and design else None
