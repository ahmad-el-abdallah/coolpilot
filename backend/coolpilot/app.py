"""CoolPilot — Flask backend. Serves the built React app and the /api."""
from __future__ import annotations

import html
import os
import platform
import re
import shutil
import subprocess
import time

from flask import Flask, jsonify, request, send_from_directory

from . import battery, blackbox, diag, fanmode, profiles, report, security, stability
from .hw import fans, gpu, pcie, sensors, sysfs
from .hw.device import device_name

DIST = os.environ.get(
    "COOLPILOT_DIST", os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# services that manage the same settings and would silently undo changes
CONFLICTS = {
    "asusd": "asusctl daemon - manages performance mode, fan curves and charge limit",
    "tlp": "TLP - manages CPU boost, energy preference and charge thresholds",
    "auto-cpufreq": "auto-cpufreq - manages CPU boost and frequency",
    "tuned": "TuneD - manages CPU and power profiles",
    "laptop-mode": "laptop-mode-tools - manages CPU and power settings",
}


def conflicts() -> list[dict]:
    if not shutil.which("systemctl"):
        return []
    try:
        r = subprocess.run(["systemctl", "is-active", *CONFLICTS], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    states = r.stdout.split()
    return [{"unit": u, "what": CONFLICTS[u]} for u, st in zip(CONFLICTS, states) if st == "active"]


_crash_cache: dict = {"at": 0.0, "value": None}


def _newest_crash() -> dict | None:
    """Most recent crash, cached for a minute (the bar asks every few seconds)."""
    if time.time() - _crash_cache["at"] > 60:
        crashes = blackbox.crash_events(limit=5)
        _crash_cache.update(at=time.time(), value=crashes[0] if crashes else None)
    return _crash_cache["value"]


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def create_app(token: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    token = token or security.load_token()
    security.install(app, token)

    # ------------------------------------------------------------------ UI
    @app.get("/")
    @app.get("/<path:path>")
    def ui(path: str = ""):
        if path.startswith("api/"):
            return _err("not found", 404)
        dist = os.path.abspath(DIST)
        if path and os.path.isfile(os.path.join(dist, path)):
            return send_from_directory(dist, path)
        index = os.path.join(dist, "index.html")
        if not os.path.isfile(index):
            return "Frontend not built. Run: cd frontend && npm install && npm run build", 503
        with open(index) as f:
            page = f.read()
        meta = f'<meta name="coolpilot-token" content="{html.escape(token)}">'
        return page.replace("<head>", "<head>" + meta, 1), 200, {"Content-Type": "text/html"}

    # ------------------------------------------------------------------ read
    @app.get("/api/sensors")
    def api_sensors():
        return jsonify(sensors.snapshot())

    @app.get("/api/settings")
    def api_settings():
        return jsonify(sysfs.describe_all())

    @app.get("/api/fans")
    def api_fans():
        return jsonify({**fans.read_all(), **fanmode.state()})

    @app.post("/api/fans/mode")
    def api_fan_mode():
        try:
            results = fanmode.set_mode(str((request.get_json(silent=True) or {}).get("mode")))
        except ValueError as e:
            return _err(str(e))
        return jsonify({**fans.read_all(), **fanmode.state(), "results": results})

    @app.post("/api/reset")
    def api_factory_reset():
        body = request.get_json(silent=True) or {}
        results = profiles.factory_reset(bool(body.get("delete_profiles")))
        return jsonify({"results": results})

    @app.get("/api/system")
    def api_system():
        r = sysfs.read
        return jsonify({
            "device": device_name(),
            "model": r("sys/class/dmi/id/product_name"),
            "vendor": r("sys/class/dmi/id/sys_vendor"),
            "serial": r("sys/class/dmi/id/product_serial"),
            "bios": {"version": r("sys/class/dmi/id/bios_version"), "date": r("sys/class/dmi/id/bios_date")},
            "cpu": next((ln.split(":", 1)[1].strip() for ln in (r("proc/cpuinfo") or "").splitlines()
                         if ln.startswith("model name")), None),
            "kernel": platform.release(),
            "hostname": platform.node(),
            "uptime_min": round(float((r("proc/uptime") or "0").split()[0]) / 60),
            "cpu_driver": r("sys/devices/system/cpu/cpu0/cpufreq/scaling_driver"),
            "governor": r("sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "gpu_state": gpu.power_state(),
            "warranty": profiles.config().get("warranty"),
            "conflicts": conflicts(),
            "features": {"fan_curves": fans.available(), "diagnostics": diag.available(),
                         "pcie_errors": pcie.available(),
                         "gpu_mode_switch": False},
        })

    @app.post("/api/system/warranty")
    def api_warranty():
        """Warranty info the user copies from ASUS's checker (kept only on this machine)."""
        body = request.get_json(silent=True) or {}
        if body.get("clear"):
            profiles.update_config(warranty=None)
            return api_system()
        w = {}
        for k in ("start", "end"):
            v = str(body.get(k, "")).strip()
            if v and not DATE.match(v):
                return _err(f"{k} must be a date like 2030-12-31")
            w[k] = v or None
        for k in ("territory", "note"):
            w[k] = str(body.get(k, "")).strip()[:120] or None
        if not w["end"]:
            return _err("the warranty end date is required")
        profiles.update_config(warranty=w)
        return api_system()

    # ------------------------------------------------------------------ write
    @app.post("/api/settings/<key>")
    def api_set(key):
        body = request.get_json(silent=True) or {}
        if "value" not in body:
            return _err("missing 'value'")
        try:
            if key == "charge_limit" and battery.active():
                battery.stop("manual", restore=False)  # a hand-picked limit ends "100% once"
            if key == "platform_profile":
                profiles._set_platform_profile(sysfs.SETTINGS[key].validate(body["value"]))
                fanmode.enforce()  # the firmware just reloaded its own fan curves
                res = sysfs.SETTINGS[key].describe()
            else:
                res = sysfs.set_setting(key, body["value"])
        except sysfs.SettingError as e:
            return _err(str(e))
        profiles.update_config(active_profile=None)
        return jsonify(res)

    @app.post("/api/fans/<int:fan>")
    def api_fan(fan):
        body = request.get_json(silent=True) or {}
        try:
            if body.get("reset"):
                fans.reset(fan)
            elif "points" in body:
                fans.set_curve(fan, body["points"], enable=bool(body.get("custom", True)))
            elif "custom" in body:
                fans.set_mode(fan, bool(body["custom"]))
            else:
                return _err("nothing to do")
        except fans.FanError as e:
            return _err(str(e))
        fanmode.remember_custom()  # hand edits become the "custom" fan mode and stick
        return jsonify({**fans.read_all(), **fanmode.state()})

    # ------------------------------------------------------------------ profiles
    @app.get("/api/profiles")
    def api_profiles():
        return jsonify({"profiles": profiles.list_profiles(),
                        "config": {**profiles.config(), "stability_on": stability.is_on()}})

    @app.post("/api/profiles")
    def api_profile_save():
        body = request.get_json(silent=True) or {}
        try:
            profiles.save_current(str(body.get("name", "")), str(body.get("description", "")),
                                  bool(body.get("include_fans", True)))
        except ValueError as e:
            return _err(str(e))
        return api_profiles()

    @app.post("/api/profiles/<name>/apply")
    def api_profile_apply(name):
        try:
            results = profiles.apply(name)
        except ValueError as e:
            return _err(str(e), 404)
        return jsonify({"results": results})

    @app.post("/api/profiles/<name>/boot")
    def api_profile_boot(name):
        body = request.get_json(silent=True) or {}
        try:
            profiles.set_boot(name if body.get("enabled", True) else None)
        except ValueError as e:
            return _err(str(e), 404)
        return api_profiles()

    @app.delete("/api/profiles/<name>")
    def api_profile_delete(name):
        try:
            profiles.delete(name)
        except ValueError as e:
            return _err(str(e))
        return api_profiles()

    # ------------------------------------------------------------------ stability mode
    @app.get("/api/stability")
    def api_stability_state():
        return jsonify(stability.state())

    @app.post("/api/stability")
    def api_stability():
        on = bool((request.get_json(silent=True) or {}).get("enabled"))
        results = stability.turn_on() if on else stability.turn_off()
        return jsonify({"results": results, "config": profiles.config(), "state": stability.state()})

    @app.post("/api/stability/config")
    def api_stability_config():
        body = request.get_json(silent=True) or {}
        try:
            results = stability.update(body.get("items"), body.get("fans"))
        except ValueError as e:
            return _err(str(e))
        return jsonify({"results": results, "state": stability.state()})

    @app.post("/api/stability/reset")
    def api_stability_reset():
        key = (request.get_json(silent=True) or {}).get("key")
        try:
            results = stability.reset(key)
        except ValueError as e:
            return _err(str(e))
        return jsonify({"results": results, "state": stability.state()})

    @app.post("/api/stability/section")
    def api_stability_section():
        body = request.get_json(silent=True) or {}
        try:
            results = stability.set_section(str(body.get("section")), str(body.get("mode")))
        except ValueError as e:
            return _err(str(e))
        return jsonify({"results": results, "state": stability.state()})

    @app.post("/api/stability/boot")
    def api_stability_boot():
        stability.set_boot(bool((request.get_json(silent=True) or {}).get("enabled")))
        return jsonify(stability.state())

    # ------------------------------------------------------------------ diagnostics
    @app.get("/api/diag")
    def api_diag():
        return jsonify(diag.status())

    @app.post("/api/diag/start")
    def api_diag_start():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(diag.start(str(body.get("test")), int(body.get("minutes", 10))))
        except (diag.DiagError, ValueError, TypeError) as e:
            return _err(str(e))

    @app.post("/api/diag/stop")
    def api_diag_stop():
        return jsonify(diag.stop())

    @app.post("/api/diag/mark")
    def api_diag_mark():
        try:
            return jsonify(diag.mark(str((request.get_json(silent=True) or {}).get("area"))))
        except diag.DiagError as e:
            return _err(str(e))

    @app.get("/api/diag/logs")
    def api_diag_logs():
        return jsonify({"logs": diag.logs(), "results": diag.results()})

    @app.get("/api/diag/logs/<name>")
    def api_diag_log(name):
        try:
            return jsonify(diag.read_log(name))
        except diag.DiagError as e:
            return _err(str(e), 404)

    @app.get("/api/diag/report")
    def api_diag_report():
        try:
            return jsonify({"report": diag.report()})
        except diag.DiagError as e:
            return _err(str(e))

    @app.get("/api/diag/crashes")
    def api_diag_crashes():
        return jsonify({"boots": diag.crash_history(), "now": time.time()})

    # ------------------------------------------------------------------ PCIe link health
    @app.get("/api/pcie")
    def api_pcie():
        return jsonify({"available": pcie.available(), "links": pcie.links(), "watch": pcie.status()})

    @app.post("/api/pcie/watch/start")
    def api_pcie_start():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(pcie.start(bool(body.get("load", True)), int(body.get("minutes", 30))))
        except (RuntimeError, ValueError, TypeError) as e:
            return _err(str(e))

    @app.post("/api/pcie/watch/stop")
    def api_pcie_stop():
        return jsonify(pcie.stop())

    @app.post("/api/pcie/watch/mark")
    def api_pcie_mark():
        try:
            return jsonify(pcie.mark(str((request.get_json(silent=True) or {}).get("area"))))
        except RuntimeError as e:
            return _err(str(e))

    @app.get("/api/pcie/history")
    def api_pcie_history():
        return jsonify({"boots": pcie.history()})

    # ------------------------------------------------------------------ charge to 100% once
    @app.get("/api/battery/full-once")
    def api_full_once():
        return jsonify(battery.state())

    @app.post("/api/battery/full-once")
    def api_full_once_set():
        try:
            if (request.get_json(silent=True) or {}).get("enabled", True):
                return jsonify(battery.start())
            return jsonify(battery.stop("cancelled"))
        except (battery.BatteryError, sysfs.SettingError) as e:
            return _err(str(e))

    # ------------------------------------------------------------------ cheap status (bar widget / CLI)
    @app.get("/api/status")
    def api_status():
        """Everything the bar needs, from sysfs only: never runs nvidia-smi, so polling
        it every few seconds doesn't keep a sleeping GPU awake."""
        cpu = next((d for d in map(sysfs.find_hwmon, sensors.CPU_TEMP_DRIVERS) if d), None)
        asus = sysfs.find_hwmon("asus")
        bat = sysfs.battery()
        last = blackbox._last or {}
        newest = _newest_crash() if request.args.get("crash") else None
        seen = profiles.config().get("crash_seen")
        return jsonify({
            "device": device_name(),
            "profile": sysfs.read("sys/firmware/acpi/platform_profile"),
            "profiles": sysfs.SETTINGS["platform_profile"].choices() if sysfs.SETTINGS["platform_profile"].available() else [],
            "stability": stability.is_on(),
            "cpu_temp": sensors._milli(f"{cpu}/temp1_input") if cpu else None,
            "cpu_mhz": sensors._cpu_mhz()["avg"],
            "gpu_state": gpu.power_state(),
            "gpu_temp": last.get("gpu_temp") if last.get("gpu_state") not in (None, "suspended") else None,
            "fans": [sysfs.read_int(f"{asus}/fan{i}_input") for i in (1, 2)] if asus else [],
            "fan_mode": fanmode.mode(),
            "battery": sysfs.read_int(f"{bat}/capacity") if bat else None,
            "ac": sysfs.on_ac(),
            "charge_limit": sysfs.SETTINGS["charge_limit"].get() if sysfs.SETTINGS["charge_limit"].available() else None,
            "full_once": battery.active() is not None,
            "new_crash": bool(newest and newest["boot"] != seen and time.time() - newest["end"] < 7 * 86400),
        })

    # ------------------------------------------------------------------ black box + history
    @app.get("/api/blackbox")
    def api_blackbox():
        return jsonify(blackbox.status())

    @app.post("/api/blackbox")
    def api_blackbox_toggle():
        profiles.update_config(blackbox=bool((request.get_json(silent=True) or {}).get("enabled")))
        return jsonify(blackbox.status())

    @app.get("/api/blackbox/crashes")
    def api_blackbox_crashes():
        return jsonify({"crashes": blackbox.crash_events(), "seen": profiles.config().get("crash_seen")})

    @app.get("/api/blackbox/crashes/<boot>")
    def api_blackbox_crash(boot):
        d = blackbox.crash_detail(boot) if re.fullmatch(r"[0-9a-f]{32}", boot) else None
        return jsonify(d) if d else _err("no recording for that session", 404)

    @app.post("/api/blackbox/seen")
    def api_blackbox_seen():
        boot = str((request.get_json(silent=True) or {}).get("boot", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", boot):
            return _err("bad session id")
        profiles.update_config(crash_seen=boot)
        return jsonify({"seen": boot})

    @app.get("/api/history")
    def api_history():
        try:
            return jsonify(blackbox.history(request.args.get("range", "24h")))
        except ValueError as e:
            return _err(str(e))

    # ------------------------------------------------------------------ repair report
    @app.get("/api/report")
    def api_report():
        try:
            return jsonify(report.build(int(request.args.get("days", 30))))
        except ValueError as e:
            return _err(str(e))

    @app.post("/api/report/exclude")
    def api_report_exclude():
        body = request.get_json(silent=True) or {}
        boot = str(body.get("boot", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", boot):
            return _err("bad session id")
        return jsonify({"excluded": report.set_excluded(boot, bool(body.get("excluded", True)))})

    @app.post("/api/report/symptoms")
    def api_report_symptoms():
        return jsonify(report.save_symptoms((request.get_json(silent=True) or {}).get("symptoms", "")))

    @app.errorhandler(403)
    def _forbidden(e):
        return _err(getattr(e, "description", "forbidden"), 403)

    return app
