"""Repair report: everything a service center needs, in one place.

Pulls together the device, warranty info (entered by the user), their symptom
description, the crash timeline with black-box readings, PCIe link errors, the
crash-test results and whether the CPU ever reported a hardware error.
"""
from __future__ import annotations

import datetime as dt
import platform
import re
import subprocess
import time

from . import blackbox, diag, profiles, stability
from .hw import gpu, pcie, sysfs
from .hw.device import device_name

DAYS = (14, 30, 90)
MCE = re.compile(r"\[Hardware Error\]|Machine check events logged|mce: \[", re.I)


def _os_name() -> str | None:
    for line in (sysfs.read("etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return None


def _machine_checks(days: int) -> dict:
    """Hardware errors the CPU itself reported (MCE), across the kept journal."""
    try:
        out = subprocess.run(["journalctl", "-k", "--no-pager", "-q", "-o", "short-iso",
                              "--since", f"-{days}d"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {"checked": False, "count": 0, "examples": []}
    hits = [ln for ln in out.splitlines() if MCE.search(ln)]
    return {"checked": True, "count": len(hits), "examples": hits[:5]}


def _tests(crashes: list[dict]) -> list[dict]:
    """Crash tests run with ~/crashdiag, and whether each one ended in a crash."""
    ends = [c["end"] for c in crashes]
    survived = " ".join(diag.results())
    out = []
    for lg in diag.logs():
        if lg["test"] not in diag.TESTS:  # e.g. PCIe watch logs
            continue
        try:
            start = dt.datetime.strptime(lg["name"][:15], "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        end = start
        if lg["last"]:
            h, m, s = (int(float(x)) for x in lg["last"].split(":"))
            end = start.replace(hour=h, minute=m, second=s)
            if end < start:
                end += dt.timedelta(days=1)
        end_ts = end.timestamp()
        crashed = any(abs(e - end_ts) < 180 for e in ends)
        out.append({"test": lg["test"], "start": start.timestamp(), "end": end_ts,
                    "seconds": round(end_ts - start.timestamp()), "marks": lg["marks"],
                    "result": "crashed" if crashed else "survived" if lg["test"] in survived else "stopped"})
    return out


def build(days: int = 30) -> dict:
    if days not in DAYS:
        raise ValueError(f"days must be one of {DAYS}")
    now = time.time()
    since = now - days * 86400
    r = sysfs.read
    cfg = profiles.config()
    # sessions the owner marked as intentional power-offs (held the power button, etc.)
    excluded_ids = set(cfg.get("report_excluded") or [])

    sessions = [b for b in diag.crash_history(limit=200) if b["start"] >= since]
    in_window = [c for c in blackbox.crash_events(limit=200) if c["start"] >= since]
    crashes = [c for c in in_window if c["boot"] not in excluded_ids]
    excluded = [c for c in in_window if c["boot"] in excluded_ids]
    finished = [b for b in sessions if b["ending"] in ("clean", "crash")]
    gbdf = pcie.gpu_bdf()
    links = pcie.links()
    gpu_link = next((lk for lk in links if lk["bdf"] == gbdf), None)
    other_links = [lk for lk in links if lk["bdf"] != gbdf and not lk["label"].startswith("CPU root port")]
    try:
        per_boot = [{"start": b["start"], "ending": b["ending"], "errors": b["gpu_pcie_errors"]}
                    for b in pcie.history() if b["start"] >= since]
    except (OSError, subprocess.SubprocessError):
        per_boot = []

    return {
        "generated": now,
        "window_days": days,
        "device": {
            "name": device_name(), "vendor": r("sys/class/dmi/id/sys_vendor"),
            "model": r("sys/class/dmi/id/product_name"), "serial": r("sys/class/dmi/id/product_serial"),
            "bios": f"{r('sys/class/dmi/id/bios_version') or '?'} ({r('sys/class/dmi/id/bios_date') or '?'})",
            "cpu": next((ln.split(":", 1)[1].strip() for ln in (r("proc/cpuinfo") or "").splitlines()
                         if ln.startswith("model name")), None),
            "gpu": gpu.snapshot().get("name") if gbdf else None,
            "os": _os_name(), "kernel": platform.release(),
        },
        "warranty": cfg.get("warranty"),
        "symptoms": cfg.get("report_symptoms") or "",
        "summary": {
            "sessions": len(finished),
            "crashes": sum(1 for b in finished if b["ending"] == "crash" and b["boot_id"] not in excluded_ids),
            "excluded": len(excluded),
            "first_crash": min((c["end"] for c in crashes), default=None),
            "last_crash": max((c["end"] for c in crashes), default=None),
            "recorded_crashes": sum(1 for c in crashes if c["recorded"]),
            "stability_on": stability.is_on(),
            "blackbox_since": blackbox.status().get("oldest"),
        },
        "crashes": crashes,
        "excluded": [{"boot": c["boot"], "end": c["end"], "minutes": c["minutes"]} for c in excluded],
        "pcie": {
            "gpu_link": gpu_link,
            "other_links": [{"label": lk["label"], "correctable": lk["correctable"], "fatal": lk["fatal"]}
                            for lk in other_links],
            "per_session": per_boot,
        },
        "machine_checks": _machine_checks(days),
        "tests": _tests(crashes),
    }


def set_excluded(boot: str, excluded: bool) -> list[str]:
    ids = [b for b in (profiles.config().get("report_excluded") or []) if b != boot]
    if excluded:
        ids.append(boot)
    profiles.update_config(report_excluded=ids)
    return ids


def save_symptoms(text: str) -> dict:
    profiles.update_config(report_symptoms=str(text)[:4000])
    return {"symptoms": profiles.config().get("report_symptoms", "")}

