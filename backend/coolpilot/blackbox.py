"""Black box: an always-on, low-rate recorder of what the laptop is doing.

Every INTERVAL seconds one row of sensor readings is committed to SQLite with
synchronous=FULL, so the last seconds before a hard freeze or reset are on disk.
After a crash is detected (via the journal, see diag.crash_history), the last
CRASH_WINDOW seconds of that session are copied into `crashes` and kept for good.

The same store feeds the History charts: rows are rolled up into per-minute
aggregates (`minutes`), kept for a year; raw rows are kept for SAMPLE_DAYS.

The NVIDIA GPU is only queried every GPU_EVERY seconds (nvidia-smi keeps a
sleeping GPU awake and costs battery); everything else is plain sysfs reads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import alerts, diag, profiles
from .hw import gpu, pcie, sensors, sysfs

DATA_DIR = os.environ.get("COOLPILOT_DATA_DIR", "/var/lib/coolpilot")
INTERVAL = float(os.environ.get("COOLPILOT_BLACKBOX_INTERVAL", "2"))
GPU_EVERY = 30.0
SAMPLE_DAYS = 3
MINUTE_DAYS = 365
CRASH_WINDOW = 120  # seconds of readings kept per crash

COLS = ["ts", "boot", "cpu_temp", "cpu_mhz", "cpu_usage", "load1", "gpu_temp", "gpu_w", "gpu_state",
        "ssd_temp", "ram_temp", "fan1", "fan2", "bat_w", "bat_v", "bat_pct", "ac", "pcie_err", "pcie_new",
        "profile", "stability"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples ({", ".join(COLS)});
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS samples_boot ON samples(boot, ts);
CREATE TABLE IF NOT EXISTS minutes (
  ts INTEGER, boot TEXT, n INTEGER, cpu_temp REAL, cpu_temp_max REAL, gpu_temp REAL, gpu_temp_max REAL,
  cpu_usage REAL, bat_w REAL, fan REAL, pcie_err INTEGER, ac INTEGER, stability INTEGER, profile TEXT,
  PRIMARY KEY (ts, boot));
CREATE TABLE IF NOT EXISTS crashes (boot TEXT PRIMARY KEY, start REAL, end REAL, samples TEXT);
"""


def db_path() -> str:
    return os.path.join(DATA_DIR, "blackbox.db")


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(db_path(), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")  # each reading is on disk before the next one
    con.executescript(SCHEMA)
    return con


def boot_id() -> str:
    return (sysfs.read("proc/sys/kernel/random/boot_id") or "unknown").replace("-", "")


def enabled() -> bool:
    return profiles.config().get("blackbox", True) is not False


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #

class Sampler:
    def __init__(self):
        self.prev_stat: tuple[int, int] | None = None
        self.gpu_at = 0.0
        self.gpu: dict = {}
        self.gpu_bdf = pcie.gpu_bdf()
        self.prev_err: int | None = None

    def _usage(self) -> float | None:
        line = (sysfs.read("proc/stat") or "").splitlines()
        if not line:
            return None
        parts = [int(x) for x in line[0].split()[1:]]
        idle, total = parts[3] + parts[4], sum(parts)
        prev, self.prev_stat = self.prev_stat, (idle, total)
        if not prev or total == prev[1]:
            return None
        return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def _gpu(self, now: float) -> tuple[float | None, float | None, str | None]:
        state = gpu.power_state()
        if state is None or state == "suspended":
            return None, None, state
        if now - self.gpu_at >= GPU_EVERY:
            snap = gpu.snapshot()
            self.gpu = {"temp": snap.get("temp"), "w": snap.get("watts")}
            self.gpu_at = now
        return self.gpu.get("temp"), self.gpu.get("w"), state

    def sample(self) -> dict:
        from . import stability
        now = time.time()
        cpu = next((d for d in map(sysfs.find_hwmon, sensors.CPU_TEMP_DRIVERS) if d), None)
        nvme = sysfs.find_hwmon("nvme")
        asus = sysfs.find_hwmon("asus")
        ram = [sensors._milli(f"{d}/temp1_input") for d in sysfs.find_all_hwmon("spd5118")]
        bat = sysfs.battery() or "sys/class/power_supply/none"
        volt = sysfs.read_int(f"{bat}/voltage_now")
        power = sysfs.read_int(f"{bat}/power_now")
        if not power:
            cur = sysfs.read_int(f"{bat}/current_now")
            power = cur * volt // 1_000_000 if cur and volt else None
        gpu_temp, gpu_w, gpu_state = self._gpu(now)
        err = pcie._total(pcie._counts(self.gpu_bdf, "correctable")) if self.gpu_bdf else None
        # new errors since the previous reading (the counter itself restarts every boot)
        new = max(0, err - self.prev_err) if err is not None and self.prev_err is not None else 0
        self.prev_err = err
        return {
            "ts": now, "boot": boot_id(),
            "cpu_temp": sensors._milli(f"{cpu}/temp1_input") if cpu else None,
            "cpu_mhz": sensors._cpu_mhz()["avg"],
            "cpu_usage": self._usage(),
            "load1": float((sysfs.read("proc/loadavg") or "0").split()[0]),
            "gpu_temp": gpu_temp, "gpu_w": gpu_w, "gpu_state": gpu_state,
            "ssd_temp": sensors._milli(f"{nvme}/temp1_input") if nvme else None,
            "ram_temp": max((t for t in ram if t is not None), default=None),
            "fan1": sysfs.read_int(f"{asus}/fan1_input") if asus else None,
            "fan2": sysfs.read_int(f"{asus}/fan2_input") if asus else None,
            "bat_w": round(power / 1e6, 2) if power else None,
            "bat_v": round(volt / 1e6, 3) if volt else None,
            "bat_pct": sysfs.read_int(f"{bat}/capacity"),
            "ac": 1 if sysfs.on_ac() else 0,
            "pcie_err": err, "pcie_new": new,
            "profile": sysfs.read("sys/firmware/acpi/platform_profile"),
            "stability": 1 if stability.is_on() else 0,
        }


def record(con: sqlite3.Connection, sample: dict) -> None:
    con.execute(f"INSERT INTO samples ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                [sample.get(c) for c in COLS])
    con.commit()


def rollup(con: sqlite3.Connection, until: float | None = None) -> None:
    """Aggregate finished minutes into `minutes` (idempotent)."""
    until = (until if until is not None else time.time()) // 60 * 60
    row = con.execute("SELECT MAX(ts) FROM minutes").fetchone()
    since = (row[0] + 60) if row and row[0] is not None else 0
    con.execute("""
        INSERT OR REPLACE INTO minutes (ts, boot, n, cpu_temp, cpu_temp_max, gpu_temp, gpu_temp_max,
                                        cpu_usage, bat_w, fan, pcie_err, ac, stability, profile)
        SELECT CAST(ts / 60 AS INTEGER) * 60 AS m, boot, COUNT(*), AVG(cpu_temp), MAX(cpu_temp),
               AVG(gpu_temp), MAX(gpu_temp), AVG(cpu_usage), AVG(bat_w),
               AVG((COALESCE(fan1, 0) + COALESCE(fan2, 0)) / 2.0),
               SUM(pcie_new), MAX(ac), MAX(stability), MAX(profile)
        FROM samples WHERE ts >= ? AND ts < ? GROUP BY m, boot""", (since, until))
    con.commit()


def prune(con: sqlite3.Connection, now: float | None = None) -> None:
    now = now or time.time()
    con.execute("DELETE FROM samples WHERE ts < ?", (now - SAMPLE_DAYS * 86400,))
    con.execute("DELETE FROM minutes WHERE ts < ?", (now - MINUTE_DAYS * 86400,))
    con.commit()


def capture_crashes(con: sqlite3.Connection) -> int:
    """Keep the last CRASH_WINDOW seconds of every session that ended in a crash."""
    added = 0
    known = {r[0] for r in con.execute("SELECT boot FROM crashes")}
    for b in diag.crash_history(limit=60):
        if b["ending"] != "crash" or b["boot_id"] in known:
            continue
        rows = con.execute("SELECT * FROM samples WHERE boot = ? AND ts >= "
                           "(SELECT MAX(ts) FROM samples WHERE boot = ?) - ? ORDER BY ts",
                           (b["boot_id"], b["boot_id"], CRASH_WINDOW)).fetchall()
        con.execute("INSERT OR REPLACE INTO crashes (boot, start, end, samples) VALUES (?, ?, ?, ?)",
                    (b["boot_id"], b["start"], b["end"], json.dumps([dict(r) for r in rows]) if rows else None))
        added += 1
    con.commit()
    return added


# --------------------------------------------------------------------------- #
# background thread (started by the service, not by tests)
# --------------------------------------------------------------------------- #

_stop = threading.Event()
_thread: threading.Thread | None = None
_last: dict = {}
_error: str | None = None


def _loop() -> None:
    global _last, _error
    try:
        con = connect()
        rollup(con)             # the previous session's last minutes, incl. a crash
        capture_crashes(con)
    except (sqlite3.Error, OSError) as e:
        _error = str(e)
        return
    sampler = Sampler()
    watcher = alerts.Watcher()
    try:
        crashes = crash_events(limit=5)
        watcher.crash(crashes[0] if crashes else None)
    except (sqlite3.Error, OSError, ValueError):
        pass
    last_minute = last_prune = last_crash_check = time.time()
    while not _stop.wait(INTERVAL):
        recording = enabled()
        if not recording and not alerts.settings()["enabled"]:
            continue
        try:
            s = sampler.sample()
            watcher.check(s)  # alerts work even with the recorder switched off
            now = time.time()
            if now // 60 != last_minute // 60:
                alerts.retry_pending()
            if not recording:
                last_minute = now
                continue
            record(con, s)
            _last, _error = s, None
            if now // 60 != last_minute // 60:
                rollup(con)
                last_minute = now
            if now - last_crash_check > 600:
                capture_crashes(con)
                last_crash_check = now
            if now - last_prune > 3600:
                prune(con)
                last_prune = now
        except (sqlite3.Error, OSError, ValueError) as e:
            _error = str(e)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="blackbox", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


# --------------------------------------------------------------------------- #
# reading (API)
# --------------------------------------------------------------------------- #

def status() -> dict:
    size = sum(os.path.getsize(p) for p in (db_path(), db_path() + "-wal") if os.path.exists(p))
    out = {"enabled": enabled(), "interval": INTERVAL, "running": bool(_thread and _thread.is_alive()),
           "error": _error, "db_bytes": size, "last": _last or None,
           "oldest": None, "samples": 0, "crashes_kept": 0}
    if os.path.exists(db_path()):
        con = connect()
        try:
            out["oldest"] = con.execute("SELECT MIN(ts) FROM minutes").fetchone()[0]
            out["samples"] = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            out["crashes_kept"] = con.execute("SELECT COUNT(*) FROM crashes WHERE samples IS NOT NULL").fetchone()[0]
        finally:
            con.close()
    return out


def _summary(samples: list[dict], end: float) -> dict:
    """What the laptop was doing in the recorded window before a crash."""
    if not samples:
        return {}

    def peak(k):
        vals = [s[k] for s in samples if s.get(k) is not None]
        return max(vals) if vals else None

    def mean(k):
        vals = [s[k] for s in samples if s.get(k) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    last = samples[-1]
    errs = [s["pcie_err"] for s in samples if s.get("pcie_err") is not None]
    return {
        "seconds": round(last["ts"] - samples[0]["ts"]),
        "last_ts": last["ts"],
        "gap_to_end": round(end - last["ts"], 1) if end else None,
        "cpu_temp_last": last.get("cpu_temp"), "cpu_temp_max": peak("cpu_temp"),
        "cpu_usage_avg": mean("cpu_usage"), "cpu_mhz_last": last.get("cpu_mhz"),
        "gpu_temp_max": peak("gpu_temp"), "gpu_state": last.get("gpu_state"),
        "ssd_temp_max": peak("ssd_temp"), "ram_temp_max": peak("ram_temp"),
        "bat_w_last": last.get("bat_w"), "ac": bool(last.get("ac")),
        "profile": last.get("profile"), "stability": bool(last.get("stability")),
        "pcie_err_delta": (errs[-1] - errs[0]) if len(errs) > 1 else 0,
    }


def crash_events(limit: int = 60) -> list[dict]:
    """Crash sessions (newest first) with the black box's last readings where recorded."""
    kept: dict[str, list[dict] | None] = {}
    if os.path.exists(db_path()):
        con = connect()
        try:
            for r in con.execute("SELECT boot, samples FROM crashes"):
                kept[r["boot"]] = json.loads(r["samples"]) if r["samples"] else None
        finally:
            con.close()
    out = []
    for b in diag.crash_history(limit=limit):
        if b["ending"] != "crash":
            continue
        samples = kept.get(b["boot_id"]) or []
        out.append({"boot": b["boot_id"], "start": b["start"], "end": b["end"], "minutes": b["minutes"],
                    "recorded": bool(samples), "summary": _summary(samples, b["end"])})
    return out


def crash_detail(boot: str) -> dict | None:
    if not os.path.exists(db_path()):
        return None
    con = connect()
    try:
        r = con.execute("SELECT * FROM crashes WHERE boot = ?", (boot,)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    samples = json.loads(r["samples"]) if r["samples"] else []
    return {"boot": boot, "start": r["start"], "end": r["end"], "samples": samples,
            "summary": _summary(samples, r["end"])}


RANGES = {"6h": (6 * 3600, 60), "24h": (86400, 240), "7d": (7 * 86400, 1800),
          "30d": (30 * 86400, 7200), "90d": (90 * 86400, 21600)}


def history(rng: str) -> dict:
    if rng not in RANGES:
        raise ValueError(f"range must be one of {list(RANGES)}")
    span, bucket = RANGES[rng]
    now = time.time()
    points: list[dict] = []
    oldest = None
    if os.path.exists(db_path()):
        con = connect()
        try:
            rollup(con)  # include the minutes finished since the recorder last rolled up
            oldest = con.execute("SELECT MIN(ts) FROM minutes").fetchone()[0]
            rows = con.execute("""
                SELECT CAST(ts / ? AS INTEGER) * ? AS t,
                       AVG(cpu_temp) AS cpu, MAX(cpu_temp_max) AS cpu_max,
                       AVG(gpu_temp) AS gpu, MAX(gpu_temp_max) AS gpu_max,
                       AVG(cpu_usage) AS usage, AVG(bat_w) AS bat_w, AVG(fan) AS fan,
                       SUM(pcie_err) AS pcie, AVG(ac) AS ac, AVG(stability) AS stab, SUM(n) AS n
                FROM minutes WHERE ts >= ? GROUP BY t ORDER BY t""",
                               (bucket, bucket, now - span)).fetchall()
            points = [{k: (round(r[k], 1) if isinstance(r[k], float) else r[k]) for k in r.keys()} for r in rows]
        finally:
            con.close()
    crashes = [{"t": b["end"], "boot": b["boot_id"], "minutes": b["minutes"]}
               for b in diag.crash_history(limit=200) if b["ending"] == "crash" and b["end"] >= now - span]
    return {"range": rng, "bucket": bucket, "from": now - span, "to": now,
            "oldest": oldest, "points": points, "crashes": crashes}
