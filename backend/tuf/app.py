"""TUF Control — Flask backend. Serves the built React app and the /api."""
from __future__ import annotations

import html
import os
import re
import platform
import time

from flask import Flask, jsonify, request, send_from_directory

from . import diag, fanmode, profiles, security, stability
from .hw import fans, gpu, pcie, sensors, sysfs

DIST = os.environ.get(
    "TUF_DIST", os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        meta = f'<meta name="tuf-token" content="{html.escape(token)}">'
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

    @app.errorhandler(403)
    def _forbidden(e):
        return _err(getattr(e, "description", "forbidden"), 403)

    return app
