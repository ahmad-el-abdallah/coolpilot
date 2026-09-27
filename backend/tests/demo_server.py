"""Run CoolPilot with example data - for screenshots and working on the UI
without the hardware. Nothing is read from or written to the real machine:
sysfs, config and the black box all live in temporary folders, and the journal,
nvidia-smi and powerprofilesctl are hidden.

    cd backend && uv run python tests/demo_server.py      # http://127.0.0.1:8790
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import conftest  # noqa: E402  (sets the COOLPILOT_* folders to temp dirs)
from conftest import ARM, build, w  # noqa: E402

PORT = int(os.environ.get("COOLPILOT_DEMO_PORT", "8790"))
os.environ["COOLPILOT_PORT"] = str(PORT)
os.environ["COOLPILOT_DIST"] = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")

from coolpilot import alerts, blackbox, diag, profiles, stability  # noqa: E402
from coolpilot.hw import gpu  # noqa: E402

NOW = time.time()
DAY = 86400
GPU = "sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0"
rnd = random.Random(7)


def fake_hardware() -> None:
    build()
    w("sys/class/dmi/id/product_serial", "EXAMPLE0SERIAL")
    w("sys/class/dmi/id/bios_version", "FA507NVR.305")
    w("sys/class/dmi/id/bios_date", "03/14/2026")
    w("sys/class/power_supply/ACAD/online", 1)
    w("sys/class/power_supply/BAT1/capacity", 80)
    w("sys/class/power_supply/BAT1/status", "Not charging")
    w(f"{GPU}/power/runtime_status", "active")
    w(f"{GPU}/aer_dev_correctable", "RxErr 0\nBadTLP 1243\nTOTAL_ERR_COR 1243")
    w(f"{ARM}/nv_dynamic_boost/min_value", 5)
    w("sys/class/power_supply/BAT1/power_now", 0)
    w("proc/cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen 7 7435HS\n")
    w("sys/class/power_supply/BAT1/energy_full", 84100000)
    w("sys/class/power_supply/BAT1/energy_full_design", 90000000)
    w("sys/class/hwmon/hwmon2/name", "nvme")
    w("sys/class/hwmon/hwmon2/temp1_input", 41850)
    for i, t in ((3, 47500), (4, 46250)):
        w(f"sys/class/hwmon/hwmon{i}/name", "spd5118")
        w(f"sys/class/hwmon/hwmon{i}/temp1_input", t)


# --------------------------------------------------------------------------- #
# a month of history: hot with crashes until Stability mode went on 12 days ago
# --------------------------------------------------------------------------- #
STAB_FROM = NOW - 12 * DAY
CRASH_DAYS = [29.3, 27.8, 26.1, 25.5, 23.2, 21.9, 20.4, 18.7, 17.2, 16.6, 15.1, 13.4]


def boots() -> list[dict]:
    out = [{"index": 0, "boot_id": "0" * 32, "start": NOW - 5 * 3600, "end": NOW, "ending": "running", "minutes": 300}]
    t = NOW - 5 * 3600 - 600
    i = 1
    crash_ends = sorted((NOW - d * DAY for d in CRASH_DAYS), reverse=True)
    while t > NOW - 31 * DAY:
        start = t - rnd.uniform(4, 11) * 3600
        crash = next((c for c in crash_ends if start < c <= t), None)
        end = crash or t
        out.append({"index": -i, "boot_id": f"{i:032x}", "start": start, "end": end,
                    "ending": "crash" if crash else "clean", "minutes": int((end - start) / 60)})
        t = start - rnd.uniform(0.02, 0.15) * 3600  # a quick restart
        i += 1
    return out


BOOTS = boots()


def temp_at(ts: float) -> tuple[float, float]:
    """(cpu, gpu) °C: daily rhythm, hotter before Stability mode."""
    hour = time.localtime(ts).tm_hour
    busy = 0.5 + 0.5 * math.sin((hour - 9) / 24 * 2 * math.pi)
    if ts < STAB_FROM:
        cpu = 70 + 18 * busy + rnd.uniform(-4, 6)
        gpu_t = 58 + 14 * busy + rnd.uniform(-3, 4)
    else:
        cpu = 54 + 9 * busy + rnd.uniform(-3, 3)
        gpu_t = 46 + 7 * busy + rnd.uniform(-2, 2)
    return round(cpu, 1), round(gpu_t, 1)


def fake_blackbox() -> None:
    con = blackbox.connect()
    sessions = [b for b in BOOTS]
    rows = []
    for b in sessions:
        stab = 1 if b["start"] >= STAB_FROM else 0
        for m in range(int(b["start"] // 60 * 60), int(b["end"]), 60):
            cpu, g = temp_at(m)
            errs = rnd.choice([0] * 30 + [2, 4, 9]) + (rnd.randint(40, 260) if not stab and rnd.random() < 0.02 else 0)
            rows.append((m, b["boot_id"], 30, cpu, cpu + rnd.uniform(1, 6), g, g + rnd.uniform(1, 4),
                         rnd.uniform(8, 45) if not stab else rnd.uniform(5, 25), rnd.uniform(18, 34) if not stab else rnd.uniform(9, 16),
                         (3600 if not stab else 2900) + rnd.uniform(-300, 300), errs, 1, stab,
                         "quiet" if stab else "performance"))
    con.executemany("INSERT OR REPLACE INTO minutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    # the last two minutes before each crash, every 2 s
    for b in sessions:
        if b["ending"] != "crash":
            continue
        samples = []
        per_reading = rnd.randint(2, 14)
        for k in range(60):
            ts = b["end"] - 120 + k * 2
            cpu, g = temp_at(ts)
            samples.append({"ts": ts, "boot": b["boot_id"], "cpu_temp": cpu + k * 0.1, "cpu_mhz": 4100 + rnd.randint(-300, 300),
                            "cpu_usage": rnd.uniform(40, 90), "load1": 6.5, "gpu_temp": g, "gpu_w": rnd.uniform(40, 90),
                            "gpu_state": "active", "ssd_temp": 48.0, "ram_temp": 55.0, "fan1": 4200, "fan2": 4100,
                            "bat_w": 25.0, "bat_v": 16.4, "bat_pct": 100, "ac": 1, "pcie_err": 900 + k * per_reading,
                            "pcie_new": per_reading, "profile": "performance", "stability": 0})
        con.execute("INSERT OR REPLACE INTO crashes VALUES (?,?,?,?)", (b["boot_id"], b["start"], b["end"], json.dumps(samples)))
    # the last 3 hours of raw readings (what the recorder keeps for 3 days)
    rows = []
    for k in range(5400):
        ts = NOW - 3 * 3600 + k * 2
        cpu, g = temp_at(ts)
        rows.append({"ts": ts, "boot": "0" * 32, "cpu_temp": cpu, "cpu_mhz": 2700, "cpu_usage": 12.0,
                     "load1": 0.8, "gpu_temp": g, "gpu_w": 11.0, "gpu_state": "active", "ssd_temp": 42.0,
                     "ram_temp": 47.0, "fan1": 2900, "fan2": 2750, "bat_w": 0.0, "bat_v": 16.9,
                     "bat_pct": 80, "ac": 1, "pcie_err": 1243, "pcie_new": 0, "profile": "quiet", "stability": 1})
    con.executemany(f"INSERT INTO samples VALUES ({', '.join('?' * len(blackbox.COLS))})",
                    [[r[c] for c in blackbox.COLS] for r in rows])
    con.commit()
    con.close()


def fake_settings() -> None:
    profiles.save_current("Uni lectures", "Silent and cool for long days on battery")
    stability.turn_on()
    profiles.set_boot("Stability")
    profiles.update_config(warranty={"start": "2025-06-01", "end": "2027-06-01", "territory": "International", "note": None},
                           crash_seen=BOOTS[1]["boot_id"],
                           report_symptoms="Freezes or restarts when the laptop is lifted or moved while busy. "
                                           "No error message; the screen stops and it reboots after a few seconds.")
    now = time.time()
    profiles.update_config(alerts_recent=[
        {"id": "a1", "kind": "pcie_burst", "ts": now - 2 * 3600, "title": "126 GPU link errors in the last minute",
         "body": "The CPU ↔ GPU link had to resend corrupted data. Was the laptop moved or pressed?", "critical": False, "sent": now},
        {"id": "a2", "kind": "cpu_hot", "ts": now - 3 * DAY, "title": "CPU is very hot: 92°C",
         "body": "Put the laptop on a hard, flat surface and let it cool down.", "critical": True, "sent": now},
        {"id": "a3", "kind": "crash", "ts": STAB_FROM - DAY * 1.4, "title": "The laptop froze or restarted",
         "body": "It stopped unexpectedly. Open CoolPilot → History to see what the black box recorded.", "critical": True, "sent": now},
    ])


def live() -> None:
    """Keep the sensor readings moving so the dashboard is alive (fast enough for
    headless screenshots, where the page's clock runs faster than real time)."""
    t = 0
    while True:
        t += 1
        cpu = 57 + 3 * math.sin(t / 40) + rnd.uniform(-1, 1)
        w("sys/class/hwmon/hwmon5/temp1_input", int(cpu * 1000))
        w("sys/class/hwmon/hwmon7/fan1_input", 2900 + rnd.randint(-40, 40))
        w("sys/class/hwmon/hwmon7/fan2_input", 2750 + rnd.randint(-40, 40))
        for c in range(2):
            w(f"sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", rnd.randint(2400000, 3000000))
        busy = 20 * t + rnd.randint(0, 8)
        w("proc/stat", f"cpu  {busy} 0 {busy // 3} {160 * t} 0 0 0 0 0 0")
        time.sleep(0.2)


def main() -> None:
    fake_hardware()
    diag.crash_history = lambda limit=30: BOOTS[:limit]
    gpu.snapshot = lambda: {"present": True, "state": "active", "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
                            "temp": 47.0, "watts": 11.8, "mhz": 210.0, "mem_mhz": 405.0, "usage": 3.0,
                            "vram_used": 412.0, "vram_total": 8188.0, "pstate": "P8", "driver": "580.95"}
    alerts.sessions = lambda: [(1000, "/nonexistent")]   # looks logged in; nothing can be shown
    fake_blackbox()
    fake_settings()
    blackbox._last = {"ts": time.time(), "gpu_temp": 47.0, "gpu_state": "active"}
    blackbox._thread = threading.Thread(target=threading.Event().wait, daemon=True)  # "recorder running"
    blackbox._thread.start()
    threading.Thread(target=live, daemon=True).start()
    from waitress import serve

    from coolpilot.app import create_app
    print(f"CoolPilot demo: http://127.0.0.1:{PORT}  (example data in {conftest.ROOT})")
    serve(create_app("demo-token"), host="127.0.0.1", port=PORT, threads=8)


if __name__ == "__main__":
    main()
