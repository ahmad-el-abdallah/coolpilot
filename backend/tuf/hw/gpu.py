"""NVIDIA dGPU status. Skips nvidia-smi while the GPU is runtime-suspended so
polling the dashboard doesn't wake it up and waste power."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

from . import sysfs

FIELDS = ["name", "temperature.gpu", "power.draw", "clocks.gr", "clocks.mem",
          "utilization.gpu", "memory.used", "memory.total", "pstate", "driver_version"]


def _pci_dev() -> str | None:
    for d in glob.glob(os.path.join(sysfs.ROOT, "sys/bus/pci/devices/*")):
        try:
            with open(f"{d}/vendor") as v, open(f"{d}/class") as c:
                if v.read().strip() == "0x10de" and c.read().strip().startswith("0x03"):
                    return d
        except OSError:
            continue
    return None


def power_state() -> str | None:
    d = _pci_dev()
    if not d:
        return None
    try:
        with open(f"{d}/power/runtime_status") as f:
            return f.read().strip()
    except OSError:
        return None


def snapshot() -> dict:
    state = power_state()
    out: dict = {"present": state is not None, "state": state}
    if state == "suspended" or not shutil.which("nvidia-smi"):
        return out
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(FIELDS)}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        vals = [x.strip() for x in r.stdout.strip().split(",")]
        if r.returncode == 0 and len(vals) == len(FIELDS):
            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            out.update(name=vals[0], temp=num(vals[1]), watts=num(vals[2]), mhz=num(vals[3]),
                       mem_mhz=num(vals[4]), usage=num(vals[5]), vram_used=num(vals[6]),
                       vram_total=num(vals[7]), pstate=vals[8], driver=vals[9])
    except (OSError, subprocess.TimeoutExpired):
        out["error"] = "nvidia-smi failed"
    return out
