import pytest

from conftest import ARM, read


# ----------------------------------------------------------------- security
def test_missing_token_rejected(client):
    r = client.get("/api/settings", headers={"X-CoolPilot-Token": ""})
    assert r.status_code == 403


def test_wrong_token_rejected(client):
    r = client.post("/api/settings/charge_limit", json={"value": 80}, headers={"X-CoolPilot-Token": "nope"})
    assert r.status_code == 403
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "100"


def test_foreign_host_rejected(client):
    r = client.get("/api/settings", headers={"Host": "evil.example:8787"})
    assert r.status_code == 403


def test_token_injected_into_page(client, tmp_path):
    import os
    dist = os.environ["COOLPILOT_DIST"]
    os.makedirs(dist, exist_ok=True)
    with open(f"{dist}/index.html", "w") as f:
        f.write("<html><head></head><body></body></html>")
    r = client.get("/", headers={"X-CoolPilot-Token": ""})
    assert r.status_code == 200
    assert b'name="coolpilot-token" content="test-token"' in r.data


# ----------------------------------------------------------------- settings
def test_settings_listed(client):
    data = {s["key"]: s for s in client.get("/api/settings").get_json()}
    assert data["platform_profile"]["value"] == "balanced"
    assert data["platform_profile"]["choices"] == ["quiet", "balanced", "performance"]
    assert data["cpu_max_mhz"]["max"] == 4553
    assert data["ppt_pl2"]["min"] == 35
    assert "custom" not in data["epp"]["choices"]
    assert data["screen_brightness"]["available"] is False


def test_set_platform_profile(client):
    r = client.post("/api/settings/platform_profile", json={"value": "quiet"})
    assert r.status_code == 200
    assert read("sys/firmware/acpi/platform_profile") == "quiet"


def test_set_max_freq_writes_all_cpus_in_khz(client):
    assert client.post("/api/settings/cpu_max_mhz", json={"value": 2500}).status_code == 200
    for c in range(2):
        assert read(f"sys/devices/system/cpu/cpu{c}/cpufreq/scaling_max_freq") == "2500000"


def test_boost_off_writes_global_and_per_cpu(client):
    assert client.post("/api/settings/cpu_boost", json={"value": False}).status_code == 200
    assert read("sys/devices/system/cpu/cpufreq/boost") == "0"
    assert read("sys/devices/system/cpu/cpu1/cpufreq/boost") == "0"


def test_out_of_range_rejected(client):
    r = client.post("/api/settings/ppt_pl1", json={"value": 200})
    assert r.status_code == 400 and "above maximum" in r.get_json()["error"]
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80"
    assert client.post("/api/settings/charge_limit", json={"value": 5}).status_code == 400
    assert client.post("/api/settings/platform_profile", json={"value": "turbo"}).status_code == 400
    assert client.post("/api/settings/cpu_max_mhz", json={"value": "2500"}).status_code == 400


def test_unknown_setting_rejected(client):
    r = client.post("/api/settings/../../etc/passwd", json={"value": 1})
    assert r.status_code in (400, 404, 405)  # never reaches a write
    assert client.post("/api/settings/nope", json={"value": 1}).status_code == 400


# ----------------------------------------------------------------- fans
def test_fan_curve_roundtrip(client):
    pts = [[40, 0], [50, 30], [60, 60], [65, 90], [70, 120], [80, 160], [85, 200], [90, 255]]
    r = client.post("/api/fans/1", json={"points": pts})
    assert r.status_code == 200
    fan1 = r.get_json()["fans"][0]
    assert fan1["custom"] is True and fan1["points"] == pts
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "1"


def test_fan_curve_validation(client):
    bad_order = [[40, 0], [30, 30]] + [[60 + i, 100] for i in range(6)]
    assert client.post("/api/fans/1", json={"points": bad_order}).status_code == 400
    decreasing = [[40 + i * 5, 200 - i * 10] for i in range(8)]
    assert client.post("/api/fans/1", json={"points": decreasing}).status_code == 400
    assert client.post("/api/fans/1", json={"points": [[40, 0]]}).status_code == 400
    assert client.post("/api/fans/7", json={"custom": True}).status_code == 400


# ----------------------------------------------------------------- profiles
def test_stability_on_and_off(client):
    r = client.post("/api/stability", json={"enabled": True})
    assert r.status_code == 200
    res = r.get_json()["results"]
    assert res["cpu_boost"] == "ok" and res["ppt_pl1"] == "ok"
    assert read("sys/firmware/acpi/platform_profile") == "quiet"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "3000000"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "35"
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "80"

    client.post("/api/stability", json={"enabled": False})
    assert read("sys/firmware/acpi/platform_profile") == "balanced"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/boost") == "1"


def test_save_apply_delete_user_profile(client):
    client.post("/api/settings/charge_limit", json={"value": 60})
    assert client.post("/api/profiles", json={"name": "Mine"}).status_code == 200
    client.post("/api/settings/charge_limit", json={"value": 100})
    assert client.post("/api/profiles/Mine/apply").status_code == 200
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "60"
    assert client.delete("/api/profiles/Mine").status_code == 200
    assert client.post("/api/profiles/Mine/apply").status_code == 404


def test_builtin_protected(client):
    assert client.delete("/api/profiles/Stability").status_code == 400
    assert client.post("/api/profiles", json={"name": "Stability"}).status_code == 400


def test_boot_profile(client):
    from coolpilot import profiles
    client.post("/api/profiles/Quiet/boot", json={"enabled": True})
    assert profiles.config()["boot_profile"] == "Quiet"
    results = profiles.apply_boot()
    assert results["platform_profile"] == "ok"
    assert read("sys/firmware/acpi/platform_profile") == "quiet"


def test_factory_defaults(client):
    client.post("/api/profiles/Stability/apply")
    client.post("/api/fans/1", json={"custom": True})
    client.post("/api/profiles/Factory defaults/apply")
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "4553000"
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "2"


# ----------------------------------------------------------------- sensors
def test_sensors(client):
    s = client.get("/api/sensors").get_json()
    assert s["cpu"]["temp"] == 55.0
    assert s["battery"]["watts"] == 20.0 and s["battery"]["ac"] is False
    assert s["ram"]["total_gb"] == 15.3
    assert s["profile"] == "balanced"


# ----------------------------------------------------------------- PCIe link health
GPU_AER = "sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/aer_dev_correctable"


def _set_gpu_errors(n):
    from conftest import w
    w(GPU_AER, f"RxErr 0\nBadTLP {n}\nTOTAL_ERR_COR {n}")


def test_pcie_links(client):
    _set_gpu_errors(42)
    d = client.get("/api/pcie").get_json()
    assert d["available"] is True
    links = {link["bdf"]: link for link in d["links"]}
    gpu = links["0000:01:00.0"]
    assert gpu["label"] == "NVIDIA GPU" and gpu["correctable"] == 42
    assert gpu["breakdown"] == {"BadTLP": 42}
    assert gpu["gen"] == 4 and gpu["width"] == 8 and gpu["lanes_lost"] is False
    assert links["0000:00:01.1"]["label"] == "CPU root port → NVIDIA GPU"
    assert links["0000:04:00.0"]["label"] == "NVMe SSD"
    assert d["links"][0]["bdf"] in ("0000:00:01.1", "0000:01:00.0")  # GPU chain listed first


def test_pcie_watch_counts_errors_per_area(client):
    import time
    _set_gpu_errors(100)
    r = client.post("/api/pcie/watch/start", json={"load": False, "minutes": 1})
    assert r.status_code == 200 and r.get_json()["running"] is True
    time.sleep(0.2)
    assert client.post("/api/pcie/watch/mark", json={"area": "3"}).get_json()["area"] == "top-right"
    time.sleep(0.1)
    _set_gpu_errors(130)  # 30 new errors while pressing top-right
    time.sleep(0.3)
    st = client.post("/api/pcie/watch/stop").get_json()
    assert st["running"] is False
    assert st["errors_since_start"] == 30
    areas = {a["area"]: a for a in st["per_area"]}
    assert areas["top-right"]["errors"] == 30
    assert areas["baseline"]["errors"] == 0
    assert st["per_area"][0]["area"] == "top-right"  # worst area first
    import os
    from coolpilot import diag
    with open(os.path.join(diag.LOGS, st["log"])) as f:
        log = f.read()
    assert "now pressing: top-right" in log and "+30" in log


def test_pcie_mark_needs_running_watch(client):
    assert client.post("/api/pcie/watch/mark", json={"area": "1"}).status_code == 400
    r = client.post("/api/pcie/watch/start", json={"load": False, "minutes": 1})
    assert client.post("/api/pcie/watch/mark", json={"area": "x"}).status_code == 400
    assert client.post("/api/pcie/watch/start", json={"load": False}).status_code == 400  # already running
    client.post("/api/pcie/watch/stop")
    assert r.status_code == 200


# ----------------------------------------------------------------- battery limits (firmware swaps ranges)
def _on_battery_limits():
    from conftest import w
    w("sys/class/power_supply/ACAD/online", 0)
    for attr, cur, lo, hi in [("nv_dynamic_boost", 0, 0, 0), ("ppt_pl1_spl", 45, 15, 65),
                              ("ppt_pl2_sppt", 54, 35, 65), ("ppt_pl3_fppt", 65, 35, 65)]:
        w(f"{ARM}/{attr}/current_value", cur)
        w(f"{ARM}/{attr}/min_value", lo)
        w(f"{ARM}/{attr}/max_value", hi)
        w(f"{ARM}/{attr}/default_value", cur)


def test_stability_on_battery_has_no_failures(client):
    from coolpilot import profiles
    _on_battery_limits()
    res = client.post("/api/stability", json={"enabled": True}).get_json()["results"]
    assert all(profiles.is_ok(v) for v in res.values()), res
    assert res["gpu_dynamic_boost"].startswith("skipped: fixed at 0W")
    assert read(f"{ARM}/nv_dynamic_boost/current_value") == "0"      # untouched
    assert read(f"{ARM}/nv_temp_target/current_value") == "75"       # MIN on this range
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "35"


def test_stability_on_charger_uses_minimum_gpu_boost(client):
    client.post("/api/stability", json={"enabled": True})
    assert read(f"{ARM}/nv_dynamic_boost/current_value") == "5"


def test_profile_values_clamped_to_battery_range(client):
    from coolpilot import profiles
    res = profiles.apply_settings({"ppt_pl1": 80, "ppt_pl3": 80})
    assert res["ppt_pl1"] == "ok"
    _on_battery_limits()
    res = profiles.apply_settings({"ppt_pl1": 80, "ppt_pl3": 80, "gpu_dynamic_boost": 25})
    assert res["ppt_pl1"] == "ok: limited to 65W on battery"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "65"
    assert res["gpu_dynamic_boost"].startswith("skipped")


def test_locked_setting_direct_change_explains(client):
    _on_battery_limits()
    r = client.post("/api/settings/gpu_dynamic_boost", json={"value": 0})
    assert r.status_code == 400 and "plug in the charger" in r.get_json()["error"]
    s = {x["key"]: x for x in client.get("/api/settings").get_json()}
    assert s["gpu_dynamic_boost"]["locked"] is True and s["ppt_pl1"]["locked"] is False


def test_reapply_uses_last_applied_profile(client):
    from coolpilot import profiles
    client.post("/api/profiles/Quiet/boot", json={"enabled": True})
    client.post("/api/profiles/Performance/apply")
    client.post("/api/settings/platform_profile", json={"value": "balanced"})  # manual change clears active
    assert profiles.apply_boot(reapply=True)["platform_profile"] == "ok"
    assert read("sys/firmware/acpi/platform_profile") == "quiet"            # fell back to boot profile
    client.post("/api/profiles/Performance/apply")
    profiles.apply_boot(reapply=True)
    assert read("sys/firmware/acpi/platform_profile") == "performance"      # last applied wins


# ----------------------------------------------------------------- configurable Stability page
def _state(client):
    return client.get("/api/stability").get_json()


def _item(state, key):
    return next(i for i in state["items"] if i["key"] == key)


def test_stability_excluded_item_not_applied(client):
    r = client.post("/api/stability/config", json={"items": {"charge_limit": {"enabled": False}}})
    assert r.status_code == 200
    client.post("/api/stability", json={"enabled": True})
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "100"
    assert read("sys/firmware/acpi/platform_profile") == "quiet"
    st = _state(client)
    assert st["on"] and _item(st, "charge_limit")["status"] == "off"
    assert _item(st, "platform_profile")["status"] == "applied"


def test_stability_changes_apply_live_and_disabling_restores(client):
    client.post("/api/stability", json={"enabled": True})
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "35"
    client.post("/api/stability/config", json={"items": {"cpu_max_mhz": {"value": 2500}}})
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "2500000"
    client.post("/api/stability/config", json={"items": {"ppt_pl1": {"enabled": False}}})
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80"   # back to before-Stability value
    assert read(f"{ARM}/ppt_pl2_sppt/current_value") == "45"  # others untouched


def test_stability_reset_item_and_all(client):
    client.post("/api/stability/config", json={"items": {"cpu_max_mhz": {"value": 2000}, "epp": {"enabled": False}}})
    st = _state(client)
    assert _item(st, "cpu_max_mhz")["customized"] and _item(st, "epp")["customized"]
    st = client.post("/api/stability/reset", json={"key": "cpu_max_mhz"}).get_json()["state"]
    assert _item(st, "cpu_max_mhz")["value"] == 3000 and _item(st, "epp")["customized"]
    st = client.post("/api/stability/reset", json={}).get_json()["state"]
    assert not any(i["customized"] for i in st["items"])


def test_stability_survives_manual_change_and_off_restores_only_managed(client):
    client.post("/api/stability/config", json={"items": {"charge_limit": {"enabled": False}}})
    client.post("/api/stability", json={"enabled": True})
    client.post("/api/settings/cpu_max_mhz", json={"value": 3500})   # manual tweak
    client.post("/api/settings/charge_limit", json={"value": 60})    # not managed by Stability
    st = _state(client)
    assert st["on"] is True and _item(st, "cpu_max_mhz")["status"] == "different"
    client.post("/api/stability", json={"enabled": False})
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "3100000"  # restored
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "60"   # left alone
    assert _state(client)["on"] is False


def test_stability_fan_option(client):
    from coolpilot.hw import fans
    client.post("/api/stability/config", json={"fans": {"enabled": True, "preset": "cool"}})
    client.post("/api/stability", json={"enabled": True})
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "1"
    assert read("sys/class/hwmon/hwmon9/pwm2_auto_point1_pwm") == str(fans.PRESETS["cool"][0][1])
    assert _state(client)["fans"]["status"] == "applied"
    client.post("/api/stability/config", json={"fans": {"enabled": False}})
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "2"  # back to firmware auto


def test_stability_config_validation(client):
    bad = [{"items": {"nope": {"enabled": True}}},
           {"items": {"cpu_max_mhz": {"value": 99999}}},
           {"items": {"platform_profile": {"value": "turbo"}}},
           {"items": {"cpu_boost": {"enabled": "yes"}}},
           {"items": {"ppt_pl1": {"value": 999}}},
           {"fans": {"preset": "hurricane"}}]
    for body in bad:
        assert client.post("/api/stability/config", json=body).status_code == 400, body
    ok = client.post("/api/stability/config", json={"items": {"ppt_pl1": {"value": "__min__"}}})
    assert ok.status_code == 200


def test_stability_boot_toggle_and_counts(client):
    st = client.post("/api/stability/boot", json={"enabled": True}).get_json()
    assert st["boot"] is True
    assert st["enabled_count"] == st["total_count"] - 1  # fans option off by default
    assert client.post("/api/stability/boot", json={"enabled": False}).get_json()["boot"] is False


def test_stability_status_firmware_on_battery(client):
    _on_battery_limits()
    client.post("/api/stability", json={"enabled": True})
    st = _state(client)
    assert _item(st, "gpu_dynamic_boost")["status"] == "firmware"
    assert st["power_source"] == "on battery"


def test_stability_on_from_older_install_is_recognised(client):
    from coolpilot import profiles
    profiles.update_config(active_profile="Stability")  # what the previous version stored
    assert client.get("/api/stability").get_json()["on"] is True


# ----------------------------------------------------------------- fan mode buttons + factory reset
HW = "sys/class/hwmon/hwmon9"


def _firmware_reloads_curves():
    """What the firmware does after a reboot / sleep / Silent-Balanced-Turbo change."""
    from conftest import w
    for fan in (1, 2):
        w(f"{HW}/pwm{fan}_enable", 2)


def test_fan_mode_stability_and_default(client):
    from coolpilot.hw import fans
    d = client.post("/api/fans/mode", json={"mode": "stability"}).get_json()
    assert d["mode"] == "stability" and d["controlled_by"] == "stability"
    assert read(f"{HW}/pwm1_enable") == "1" and read(f"{HW}/pwm2_enable") == "1"
    assert read(f"{HW}/pwm1_auto_point1_pwm") == str(fans.PRESETS["stability"][0][1])
    d = client.post("/api/fans/mode", json={"mode": "default"}).get_json()
    assert d["mode"] == "default" and read(f"{HW}/pwm1_enable") == "2"
    assert client.post("/api/fans/mode", json={"mode": "jet"}).status_code == 400


def test_fan_mode_survives_performance_mode_change(client):
    client.post("/api/fans/mode", json={"mode": "stability"})
    _firmware_reloads_curves()
    client.post("/api/settings/platform_profile", json={"value": "performance"})
    assert read(f"{HW}/pwm1_enable") == "1"


def test_fan_mode_reapplied_at_boot_without_boot_profile(client):
    from coolpilot import profiles
    client.post("/api/fans/mode", json={"mode": "stability"})
    _firmware_reloads_curves()
    profiles.apply_boot()
    assert read(f"{HW}/pwm1_enable") == "1"


def test_hand_edited_curve_becomes_custom_and_sticks(client):
    pts = [[40, 0], [50, 30], [60, 60], [65, 90], [70, 120], [80, 160], [85, 200], [90, 255]]
    d = client.post("/api/fans/1", json={"points": pts}).get_json()
    assert d["mode"] == "custom"
    _firmware_reloads_curves()
    client.post("/api/settings/platform_profile", json={"value": "quiet"})
    assert read(f"{HW}/pwm1_enable") == "1" and read(f"{HW}/pwm2_enable") == "2"
    assert read(f"{HW}/pwm1_auto_point2_pwm") == "30"


def test_stability_mode_fan_option_wins_then_hands_back(client):
    from coolpilot.hw import fans
    client.post("/api/fans/mode", json={"mode": "default"})
    client.post("/api/stability/config", json={"fans": {"enabled": True, "preset": "max"}})
    client.post("/api/stability", json={"enabled": True})
    assert client.get("/api/fans").get_json()["controlled_by"] == "stability_mode"
    assert read(f"{HW}/pwm1_auto_point1_pwm") == str(fans.PRESETS["max"][0][1])
    client.post("/api/stability", json={"enabled": False})
    assert read(f"{HW}/pwm1_enable") == "2"  # back to the Fans page choice (default)


def test_factory_reset_everything(client):
    from coolpilot import profiles, stability
    client.post("/api/settings/charge_limit", json={"value": 60})
    client.post("/api/profiles", json={"name": "Mine"})
    client.post("/api/stability/config", json={"items": {"cpu_max_mhz": {"value": 2000}}})
    client.post("/api/stability", json={"enabled": True})
    client.post("/api/stability/boot", json={"enabled": True})
    client.post("/api/fans/mode", json={"mode": "stability"})
    r = client.post("/api/reset", json={})
    assert r.status_code == 200
    assert all(profiles.is_ok(v) for v in r.get_json()["results"].values())
    assert read("sys/firmware/acpi/platform_profile") == "balanced"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/boost") == "1"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "4553000"
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "100"
    assert read(f"{HW}/pwm1_enable") == "2"
    cfg = profiles.config()
    assert not stability.is_on() and cfg["boot_profile"] is None and cfg["fan_mode"] == "default"
    assert not any(i["customized"] for i in stability.state()["items"])
    names = [p["name"] for p in client.get("/api/profiles").get_json()["profiles"]]
    assert "Mine" in names                                   # kept by default
    client.post("/api/reset", json={"delete_profiles": True})
    names = [p["name"] for p in client.get("/api/profiles").get_json()["profiles"]]
    assert "Mine" not in names


def test_warranty_info_is_user_supplied(client):
    assert client.get("/api/system").get_json()["warranty"] is None   # nothing hard-coded
    assert client.post("/api/system/warranty", json={"end": "next year"}).status_code == 400
    assert client.post("/api/system/warranty", json={"territory": "x"}).status_code == 400
    d = client.post("/api/system/warranty", json={"start": "2025-01-01", "end": "2027-01-01",
                                                  "territory": "International"}).get_json()
    assert d["warranty"] == {"start": "2025-01-01", "end": "2027-01-01", "territory": "International", "note": None}
    assert client.post("/api/system/warranty", json={"clear": True}).get_json()["warranty"] is None


# ----------------------------------------------------------------- other laptops / distros
def test_intel_turbo_switch_is_inverted(client):
    import glob
    import os
    from conftest import ROOT, w
    for f in glob.glob(f"{ROOT}/sys/devices/system/cpu/cpu*/cpufreq/boost") + [f"{ROOT}/sys/devices/system/cpu/cpufreq/boost"]:
        os.remove(f)
    w("sys/devices/system/cpu/intel_pstate/no_turbo", 0)          # 0 = turbo allowed
    s = {x["key"]: x for x in client.get("/api/settings").get_json()}
    assert s["cpu_boost"]["available"] and s["cpu_boost"]["value"] is True
    client.post("/api/settings/cpu_boost", json={"value": False})
    assert read("sys/devices/system/cpu/intel_pstate/no_turbo") == "1"


def test_low_power_named_models(client):
    from conftest import w
    w("sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    res = client.post("/api/stability", json={"enabled": True}).get_json()["results"]
    assert res["platform_profile"] == "ok"
    assert read("sys/firmware/acpi/platform_profile") == "low-power"


def test_battery_and_charger_found_by_type(client):
    import os
    import shutil
    from conftest import ROOT, w
    ps = f"{ROOT}/sys/class/power_supply"
    shutil.move(f"{ps}/BAT1", f"{ps}/BAT0")
    shutil.move(f"{ps}/ACAD", f"{ps}/AC0")
    w("sys/class/power_supply/AC0/online", 1)
    w("sys/class/power_supply/hidpp_battery_0/type", "Battery")       # a wireless mouse
    w("sys/class/power_supply/hidpp_battery_0/scope", "Device")
    s = client.get("/api/sensors").get_json()
    assert s["battery"]["ac"] is True and s["battery"]["percent"] == 77
    assert client.post("/api/settings/charge_limit", json={"value": 70}).status_code == 200
    assert read("sys/class/power_supply/BAT0/charge_control_end_threshold") == "70"
    shutil.rmtree(f"{ps}/BAT0"), shutil.rmtree(f"{ps}/AC0"), shutil.rmtree(f"{ps}/hidpp_battery_0")
    assert not os.path.exists(f"{ps}/BAT1")  # the autouse fixture rebuilds it for the next test


def test_intel_cpu_temperature(client):
    import shutil
    from conftest import ROOT, w
    shutil.rmtree(f"{ROOT}/sys/class/hwmon/hwmon5")
    w("sys/class/hwmon/hwmon3/name", "coretemp")
    w("sys/class/hwmon/hwmon3/temp1_input", 61000)
    assert client.get("/api/sensors").get_json()["cpu"]["temp"] == 61.0
    shutil.rmtree(f"{ROOT}/sys/class/hwmon/hwmon3")


# ----------------------------------------------------------------- Stability page sections: Default / Stability
def _section(state, sid):
    return next(x for x in state["sections"] if x["id"] == sid)


def test_section_default_puts_factory_values_back(client):
    client.post("/api/stability", json={"enabled": True})
    assert read(f"{ARM}/nv_temp_target/current_value") == "75"
    r = client.post("/api/stability/section", json={"section": "gpu", "mode": "default"}).get_json()
    assert read(f"{ARM}/nv_temp_target/current_value") == "87"       # factory
    assert read(f"{ARM}/nv_dynamic_boost/current_value") == "25"     # factory
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "35"          # other sections untouched
    st = r["state"]
    assert _section(st, "gpu")["mode"] == "default" and _section(st, "limits")["mode"] == "stability"
    assert st["on"] is True
    client.post("/api/stability", json={"enabled": False})
    assert read(f"{ARM}/nv_temp_target/current_value") == "87"       # stays factory after turning off


def test_section_stability_reincludes_and_applies(client):
    client.post("/api/stability", json={"enabled": True})
    client.post("/api/stability/section", json={"section": "cpu", "mode": "default"})
    assert read("sys/devices/system/cpu/cpu0/cpufreq/boost") == "1"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "4553000"
    client.post("/api/stability/section", json={"section": "cpu", "mode": "stability"})
    assert read("sys/devices/system/cpu/cpu0/cpufreq/boost") == "0"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq") == "3000000"


def test_section_default_works_while_stability_is_off(client):
    client.post("/api/settings/charge_limit", json={"value": 60})
    client.post("/api/stability/section", json={"section": "battery", "mode": "default"})
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "100"
    st = client.post("/api/stability/section", json={"section": "battery", "mode": "stability"}).get_json()["state"]
    assert read("sys/class/power_supply/BAT1/charge_control_end_threshold") == "100"  # applies only when on
    assert _section(st, "battery")["mode"] == "stability" and st["on"] is False


def test_section_mixed_and_heat_default_keeps_other_limits(client):
    client.post("/api/stability/config", json={"items": {"epp": {"enabled": False}}})
    assert _section(client.get("/api/stability").get_json(), "cpu")["mode"] == "mixed"
    client.post("/api/stability", json={"enabled": True})
    client.post("/api/stability/section", json={"section": "heat", "mode": "default"})
    assert read("sys/firmware/acpi/platform_profile") == "balanced"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "35"         # re-applied after the mode change


def test_section_fans_default_and_stability(client):
    client.post("/api/fans/mode", json={"mode": "stability"})
    client.post("/api/stability/section", json={"section": "fans", "mode": "default"})
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "2"
    assert client.get("/api/fans").get_json()["mode"] == "default"
    client.post("/api/stability", json={"enabled": True})
    client.post("/api/stability/section", json={"section": "fans", "mode": "stability"})
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "1"


def test_section_validation(client):
    assert client.post("/api/stability/section", json={"section": "gpu", "mode": "turbo"}).status_code == 400
    assert client.post("/api/stability/section", json={"section": "wifi", "mode": "default"}).status_code == 400


# ----------------------------------------------------------------- device name in the nav
def test_device_name_from_dmi(client):
    from conftest import w
    from coolpilot.hw.device import device_name
    cases = [
        ("ASUSTeK COMPUTER INC.", "ASUS TUF Gaming A15 FA507NVR_FA507NVR", "", "ASUS TUF Gaming A15 FA507NVR"),
        ("ASUSTeK COMPUTER INC.", "ROG Zephyrus G14 GA402RJ_GA402RJ", "", "ASUS ROG Zephyrus G14 GA402RJ"),
        ("LENOVO", "82JU", "Legion 5 15ACH6H", "Lenovo Legion 5 15ACH6H"),
        ("Dell Inc.", "XPS 13 9310", "", "Dell XPS 13 9310"),
        ("HP", "HP Victus by HP Laptop 16-d0xxx", "", "HP Victus by HP Laptop 16-d0xxx"),
        ("Micro-Star International Co., Ltd.", "Katana GF66 11UE", "", "MSI Katana GF66 11UE"),
        ("To be filled by O.E.M.", "System Product Name", "", "This laptop"),
        ("", "", "", "This laptop"),
    ]
    for vendor, product, version, want in cases:
        w("sys/class/dmi/id/sys_vendor", vendor)
        w("sys/class/dmi/id/product_name", product)
        w("sys/class/dmi/id/product_version", version)
        assert device_name() == want, (vendor, product)
    assert client.get("/api/system").get_json()["device"] == "This laptop"


# ----------------------------------------------------------------- black box, history, repair report
CRASH_BOOT = "a" * 32
NOW_BOOT = "11111111222233334444555555555555"


def _fake_boots(monkeypatch, crash_end):
    from coolpilot import diag
    boots = [
        {"index": 0, "boot_id": NOW_BOOT, "start": crash_end + 60, "end": crash_end + 600, "ending": "running", "minutes": 9},
        {"index": -1, "boot_id": CRASH_BOOT, "start": crash_end - 1800, "end": crash_end, "ending": "crash", "minutes": 30},
        {"index": -2, "boot_id": "b" * 32, "start": crash_end - 9000, "end": crash_end - 5000, "ending": "clean", "minutes": 66},
    ]
    monkeypatch.setattr(diag, "crash_history", lambda limit=30: boots)
    return boots


def _row(ts, boot, **kw):
    base = {"ts": ts, "boot": boot, "cpu_temp": 60.0, "cpu_mhz": 2500, "cpu_usage": 50.0, "load1": 4.0,
            "gpu_temp": 50.0, "gpu_w": 10.0, "gpu_state": "active", "ssd_temp": 40.0, "ram_temp": 45.0,
            "fan1": 2800, "fan2": 2700, "bat_w": 30.0, "bat_v": 15.0, "bat_pct": 80, "ac": 0,
            "pcie_err": 100, "pcie_new": 0, "profile": "quiet", "stability": 1}
    base.update(kw)
    return base


def test_blackbox_sample_reads_the_machine(client):
    from conftest import w
    from coolpilot import blackbox
    sampler = blackbox.Sampler()
    s = sampler.sample()
    assert s["boot"] == NOW_BOOT and s["cpu_temp"] == 55.0
    assert s["fan1"] == 2800 and s["bat_w"] == 20.0 and s["ac"] == 0
    assert s["pcie_err"] == 0 and s["pcie_new"] == 0 and s["profile"] == "balanced" and s["stability"] == 0
    w("sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/aer_dev_correctable", "BadTLP 7\nTOTAL_ERR_COR 7")
    assert sampler.sample()["pcie_new"] == 7
    con = blackbox.connect()
    blackbox.record(con, s)
    assert con.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_blackbox_keeps_last_two_minutes_of_a_crash(client, monkeypatch):
    import time
    from coolpilot import blackbox
    end = time.time() - 3600
    _fake_boots(monkeypatch, end)
    con = blackbox.connect()
    for i in range(300):  # 10 minutes of the crashed session, every 2 s, getting hotter
        blackbox.record(con, _row(end - 600 + i * 2, CRASH_BOOT, cpu_temp=60 + i * 0.1, pcie_err=100 + i))
    assert blackbox.capture_crashes(con) == 1
    assert blackbox.capture_crashes(con) == 0          # only once per crash
    d = client.get(f"/api/blackbox/crashes/{CRASH_BOOT}").get_json()
    assert 55 <= len(d["samples"]) <= 62                # the last CRASH_WINDOW seconds
    assert d["summary"]["cpu_temp_max"] == round(60 + 299 * 0.1, 1)
    assert d["summary"]["pcie_err_delta"] > 50 and d["summary"]["stability"] is True
    ev = client.get("/api/blackbox/crashes").get_json()["crashes"]
    assert len(ev) == 1 and ev[0]["recorded"] and ev[0]["boot"] == CRASH_BOOT
    assert client.get("/api/blackbox/crashes/" + "c" * 32).status_code == 404
    assert client.get("/api/blackbox/crashes/../../etc").status_code in (404, 405)


def test_history_rollup_buckets_and_crash_markers(client, monkeypatch):
    import time
    from coolpilot import blackbox
    now = time.time() // 60 * 60
    _fake_boots(monkeypatch, now - 1200)
    con = blackbox.connect()
    for i in range(600):  # 20 minutes
        blackbox.record(con, _row(now - 1200 + i * 2, NOW_BOOT, cpu_temp=50 + (i % 30), pcie_err=i // 10,
                                  pcie_new=1 if i and i % 10 == 0 else 0))
    blackbox.rollup(con, until=now)
    assert con.execute("SELECT COUNT(*) FROM minutes").fetchone()[0] == 20
    h = client.get("/api/history?range=6h").get_json()
    assert h["bucket"] == 60 and len(h["points"]) == 20
    p = h["points"][0]
    assert p["cpu_max"] == 79.0 and 60 < p["cpu"] < 70 and p["stab"] == 1
    # every error counted, including the ones between two minutes: 59 increments in 600 readings
    assert [q["pcie"] for q in h["points"][:2]] == [2, 3] and sum(q["pcie"] for q in h["points"]) == 59
    assert [c["boot"] for c in h["crashes"]] == [CRASH_BOOT]
    assert client.get("/api/history?range=1y").status_code == 400


def test_blackbox_prune_and_toggle(client):
    import time
    from coolpilot import blackbox
    con = blackbox.connect()
    blackbox.record(con, _row(time.time() - 10 * 86400, NOW_BOOT))
    blackbox.record(con, _row(time.time(), NOW_BOOT))
    blackbox.prune(con)
    assert con.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert client.post("/api/blackbox", json={"enabled": False}).get_json()["enabled"] is False
    assert client.get("/api/blackbox").get_json()["samples"] == 1
    assert client.post("/api/blackbox/seen", json={"boot": "nope"}).status_code == 400
    assert client.post("/api/blackbox/seen", json={"boot": CRASH_BOOT}).get_json()["seen"] == CRASH_BOOT


def test_repair_report(client, monkeypatch):
    import os
    import time
    from coolpilot import blackbox, diag
    end = time.time() - 3600
    _fake_boots(monkeypatch, end)
    con = blackbox.connect()
    for i in range(60):
        blackbox.record(con, _row(end - 120 + i * 2, CRASH_BOOT))
    blackbox.capture_crashes(con)
    # a crash-test log that ended when the session crashed
    os.makedirs(diag.LOGS, exist_ok=True)
    start = time.localtime(end - 300)
    name = time.strftime("%Y%m%d-%H%M%S", start) + "-ram.log"
    with open(os.path.join(diag.LOGS, name), "w") as f:
        f.write("header\n" + time.strftime("%H:%M:%S", time.localtime(end - 5)) + ".0 ram 60\n")
    client.post("/api/report/symptoms", json={"symptoms": "Freezes when lifted under load."})
    r = client.get("/api/report").get_json()
    assert r["device"]["name"] == "ASUS TUF Gaming A15 FA507NVR" and r["device"]["serial"] == "TESTSERIAL123"
    assert r["symptoms"] == "Freezes when lifted under load."
    assert r["summary"]["crashes"] == 1 and r["summary"]["sessions"] == 2 and r["summary"]["recorded_crashes"] == 1
    assert r["crashes"][0]["summary"]["cpu_temp_max"] == 60.0
    assert r["pcie"]["gpu_link"]["bdf"] == "0000:01:00.0"
    assert [t["result"] for t in r["tests"]] == ["crashed"]
    assert r["machine_checks"]["checked"] is False       # no journalctl in the test sandbox
    # the owner can mark a session as an intentional power-off: it stops counting as a crash
    assert client.post("/api/report/exclude", json={"boot": "x"}).status_code == 400
    client.post("/api/report/exclude", json={"boot": CRASH_BOOT, "excluded": True})
    r = client.get("/api/report?days=14").get_json()
    assert r["summary"]["crashes"] == 0 and r["summary"]["excluded"] == 1 and r["crashes"] == []
    assert [t["result"] for t in r["tests"]] != ["crashed"]
    client.post("/api/report/exclude", json={"boot": CRASH_BOOT, "excluded": False})
    assert client.get("/api/report?days=14").get_json()["summary"]["crashes"] == 1
    assert client.get("/api/report?days=7").status_code == 400
    os.remove(os.path.join(diag.LOGS, name))


# ----------------------------------------------------------------- charge to 100% once
BAT = "sys/class/power_supply/BAT1"


def test_charge_full_once_lifts_and_restores(client):
    from conftest import w
    from coolpilot import battery
    client.post("/api/settings/charge_limit", json={"value": 80})
    st = client.post("/api/battery/full-once", json={"enabled": True}).get_json()
    assert st["active"] and st["back_to"] == 80
    assert read(f"{BAT}/charge_control_end_threshold") == "100"
    w(f"{BAT}/capacity", 97)
    assert battery.tick() is None                          # not full yet
    w(f"{BAT}/capacity", 100)
    assert battery.tick() == "full"
    assert read(f"{BAT}/charge_control_end_threshold") == "80"
    assert client.get("/api/battery/full-once").get_json()["last"]["reason"] == "full"


def test_charge_full_once_survives_reapply_and_times_out(client):
    import time
    from coolpilot import battery, profiles
    client.post("/api/stability", json={"enabled": True})             # Stability sets 80 %
    assert read(f"{BAT}/charge_control_end_threshold") == "80"
    client.post("/api/battery/full-once", json={"enabled": True})
    profiles.apply_boot(reapply=True)                                  # e.g. charger plugged in
    assert read(f"{BAT}/charge_control_end_threshold") == "100"
    assert battery.tick(now=time.time() + 25 * 3600) == "timeout"
    assert read(f"{BAT}/charge_control_end_threshold") == "80"        # back to Stability's limit


def test_charge_full_once_cancel_and_manual_override(client):
    client.post("/api/settings/charge_limit", json={"value": 70})
    client.post("/api/battery/full-once", json={"enabled": True})
    client.post("/api/battery/full-once", json={"enabled": False})
    assert read(f"{BAT}/charge_control_end_threshold") == "70"
    client.post("/api/battery/full-once", json={"enabled": True})
    client.post("/api/settings/charge_limit", json={"value": 90})     # picking a limit by hand ends it
    st = client.get("/api/battery/full-once").get_json()
    assert st["active"] is False and read(f"{BAT}/charge_control_end_threshold") == "90"


def test_charge_full_once_without_charge_limit(client):
    import os
    from conftest import ROOT
    os.remove(os.path.join(ROOT, BAT, "charge_control_end_threshold"))
    r = client.post("/api/battery/full-once", json={"enabled": True})
    assert r.status_code == 400 and "doesn't support" in r.get_json()["error"]


# ----------------------------------------------------------------- lightweight status + CLI
def test_status_is_cheap_and_complete(client):
    s = client.get("/api/status").get_json()
    assert s["device"] == "ASUS TUF Gaming A15 FA507NVR" and s["profile"] == "balanced"
    assert s["cpu_temp"] == 55.0 and s["fans"] == [2800, 2700] and s["battery"] == 77
    assert s["stability"] is False and s["full_once"] is False and s["charge_limit"] == 100


def test_cli_bar_json_formatting():
    from coolpilot import cli
    s = {"device": "X", "profile": "quiet", "stability": True, "cpu_temp": 51.6, "cpu_mhz": 1500,
         "gpu_state": "suspended", "fans": [3000, 2900], "fan_mode": "stability", "battery": 80,
         "ac": False, "charge_limit": 80, "full_once": False, "new_crash": True}
    out = cli.bar_json(s)
    assert out["text"] == "⚠ 󰈐 52°" and out["class"] == ["active", "crash"]
    assert "Stability mode: ON" in out["tooltip"] and "GPU asleep" in out["tooltip"]
    assert cli.bar_json(None, "down")["class"] == "error"


def test_cli_end_to_end_against_a_live_server(monkeypatch, capsys):
    import os
    import threading
    from werkzeug.serving import make_server
    from coolpilot import cli
    from coolpilot.app import create_app
    dist = os.environ["COOLPILOT_DIST"]
    os.makedirs(dist, exist_ok=True)
    with open(f"{dist}/index.html", "w") as f:
        f.write("<html><head></head><body></body></html>")
    srv = make_server("127.0.0.1", 0, None)
    port = srv.server_port
    srv.server_close()
    monkeypatch.setenv("COOLPILOT_EXTRA_HOSTS", f"127.0.0.1:{port}")
    srv = make_server("127.0.0.1", port, create_app("test-token"), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(cli, "BASE", f"http://127.0.0.1:{port}")
    try:
        assert cli.cmd(["stability", "on"]) == 0
        assert read("sys/firmware/acpi/platform_profile") == "quiet"
        assert cli.cmd(["mode", "next"]) == 0                        # quiet -> balanced
        assert read("sys/firmware/acpi/platform_profile") == "balanced"
        assert cli.cmd(["charge", "full"]) == 0
        assert read(f"{BAT}/charge_control_end_threshold") == "100"
        assert cli.cmd(["fans", "stability"]) == 0
        assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "1"
        assert cli.cmd(["gaming"]) == 0
        assert read("sys/firmware/acpi/platform_profile") == "performance"
        assert cli.cmd(["stability", "on"]) == 0
        capsys.readouterr()
        assert cli.cmd(["bar"]) == 0
        assert '"class": ["active"]' in capsys.readouterr().out
        assert cli.cmd(["status"]) == 0 and "Stability mode: ON" in capsys.readouterr().out
        try:
            cli.cmd(["profile", "Nope"])
            raise AssertionError("expected an error")
        except cli.CliError as e:
            assert "no profile called" in str(e)
    finally:
        srv.shutdown()
    monkeypatch.setattr(cli, "BASE", "http://127.0.0.1:1")                # nothing listening
    capsys.readouterr()
    assert cli.cmd(["bar"]) == 0 and '"class": "error"' in capsys.readouterr().out


# ----------------------------------------------------------------- Gaming mode + charger-aware factory defaults
def test_gaming_mode_uses_the_highest_allowed_limits(client):
    from coolpilot.hw import fans
    client.post("/api/stability", json={"enabled": True})
    res = client.post("/api/profiles/Gaming/apply").get_json()["results"]
    assert read("sys/firmware/acpi/platform_profile") == "performance"
    assert read("sys/devices/system/cpu/cpu0/cpufreq/boost") == "1"
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "80" and res["ppt_pl1"] == "ok"
    assert read(f"{ARM}/nv_dynamic_boost/current_value") == "25"
    assert read(f"{ARM}/nv_temp_target/current_value") == "87"
    assert read("sys/class/hwmon/hwmon9/pwm1_enable") == "1"
    assert read("sys/class/hwmon/hwmon9/pwm1_auto_point1_pwm") == str(fans.PRESETS["cool"][0][1])
    assert client.get("/api/stability").get_json()["on"] is False       # Gaming ends Stability mode


def test_gaming_mode_on_battery(client):
    from coolpilot import profiles
    _on_battery_limits()
    res = client.post("/api/profiles/Gaming/apply").get_json()["results"]
    assert read(f"{ARM}/ppt_pl1_spl/current_value") == "65"               # battery maximum
    assert res["gpu_dynamic_boost"].startswith("skipped")               # locked to 0 W on battery
    assert all(profiles.is_ok(v) for v in res.values()), res


def test_factory_defaults_follow_the_charger(client):
    from conftest import w
    from coolpilot import profiles
    client.post("/api/reset", json={})                                   # on battery in the fake tree
    assert read("sys/firmware/acpi/platform_profile") == "balanced"
    w("sys/class/power_supply/ACAD/online", 1)                           # plug in
    profiles.apply_boot(reapply=True)                                    # what the udev rule triggers
    assert read("sys/firmware/acpi/platform_profile") == "performance"
    w("sys/class/power_supply/ACAD/online", 0)                           # unplug
    profiles.apply_boot(reapply=True)
    assert read("sys/firmware/acpi/platform_profile") == "balanced"


# ----------------------------------------------------------------- desktop alerts
@pytest.fixture
def shown(monkeypatch):
    """Capture alerts instead of showing them on a desktop."""
    from coolpilot import alerts
    out = []
    monkeypatch.setattr(alerts, "_deliver_later", lambda a: out.append(a))
    return out


def test_alert_settings_roundtrip_and_validation(client):
    a = client.get("/api/alerts").get_json()
    assert a["enabled"] is True and {i["kind"] for i in a["items"]} == {"cpu_hot", "pcie_burst", "stability_off", "crash"}
    a = client.post("/api/alerts", json={"items": {"cpu_hot": {"threshold": 85}, "crash": {"enabled": False}}}).get_json()
    items = {i["kind"]: i for i in a["items"]}
    assert items["cpu_hot"]["threshold"] == 85 and items["crash"]["enabled"] is False
    assert client.post("/api/alerts", json={"enabled": False}).get_json()["enabled"] is False
    for bad in ({"items": {"cpu_hot": {"threshold": 200}}}, {"items": {"nope": {}}},
                {"items": {"crash": {"threshold": 5}}}, {"enabled": "yes"}):
        assert client.post("/api/alerts", json=bad).status_code == 400, bad


def test_cpu_hot_alert_needs_a_few_hot_readings_and_rearms(client, shown):
    from coolpilot import alerts
    wt = alerts.Watcher()
    for i in range(2):
        wt.check({"cpu_temp": 95, "stability": 1}, now=1000 + i * 2)
    assert shown == []                                        # a short spike isn't enough
    wt.check({"cpu_temp": 95, "stability": 1}, now=1004)
    assert [a["kind"] for a in shown] == ["cpu_hot"] and shown[0]["critical"]
    for i in range(10):                                       # stays hot: no repeats
        wt.check({"cpu_temp": 96, "stability": 1}, now=1006 + i * 2)
    assert len(shown) == 1
    wt.check({"cpu_temp": 70, "stability": 1}, now=2000)      # cooled down...
    for i in range(3):                                        # ...and hot again after the cooldown
        wt.check({"cpu_temp": 95, "stability": 1}, now=2002 + i * 2)
    assert len(shown) == 2


def test_pcie_burst_alert_counts_the_last_minute(client, shown):
    from coolpilot import alerts
    wt = alerts.Watcher()
    for i in range(20):                                       # 20 x 4 errors spread over 40 s = 80 < 100
        wt.check({"pcie_new": 4}, now=1000 + i * 2)
    assert shown == []
    wt.check({"pcie_new": 30}, now=1042)
    assert [a["kind"] for a in shown] == ["pcie_burst"] and "110 GPU link errors" in shown[0]["title"]
    wt.check({"pcie_new": 500}, now=1050)                     # cooldown
    assert len(shown) == 1


def test_stability_off_alert_only_on_a_change(client, shown):
    from coolpilot import alerts
    wt = alerts.Watcher()
    wt.check({"stability": 0}, now=1000)                      # already off at startup: quiet
    wt.check({"stability": 1}, now=1002)
    wt.check({"stability": 0}, now=1004)
    assert [a["kind"] for a in shown] == ["stability_off"]


def test_alerts_respect_the_switches(client, shown):
    from coolpilot import alerts
    client.post("/api/alerts", json={"items": {"stability_off": {"enabled": False}}})
    wt = alerts.Watcher()
    wt.check({"stability": 1}, now=1000)
    wt.check({"stability": 0}, now=1002)
    assert shown == []
    client.post("/api/alerts", json={"enabled": False, "items": {"stability_off": {"enabled": True}}})
    for i in range(5):
        wt.check({"cpu_temp": 99, "pcie_new": 999, "stability": i % 2}, now=2000 + i * 2)
    assert shown == []                                        # everything off


def test_crash_alert_once_per_crash(client, shown):
    import time
    from coolpilot import alerts, profiles
    wt = alerts.Watcher()
    event = {"boot": CRASH_BOOT, "end": time.time() - 300}
    assert wt.crash(event)["kind"] == "crash"
    assert wt.crash(event) is None                             # not again after a restart of the service
    assert wt.crash({"boot": "c" * 32, "end": time.time() - 3 * 86400}) is None   # too old
    profiles.update_config(crash_seen="d" * 32)
    assert wt.crash({"boot": "d" * 32, "end": time.time()}) is None             # already looked at it
    assert client.get("/api/alerts").get_json()["recent"][0]["kind"] == "crash"


def test_alert_delivery_uses_each_users_session_bus(client, monkeypatch, tmp_path):
    import os
    from coolpilot import alerts
    for uid in ("1000", "120"):                                # a user and a login-screen account
        (tmp_path / uid).mkdir()
        (tmp_path / uid / "bus").touch()
    monkeypatch.setattr(alerts, "RUN_USER", str(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert [u for u, _ in alerts.sessions()] == [1000]
    calls = []

    class Done:
        returncode = 0
    monkeypatch.setattr(alerts.shutil, "which", lambda t: f"/usr/bin/{t}" if t == "busctl" else None)
    monkeypatch.setattr(alerts.pwd, "getpwuid", lambda uid: type("P", (), {"pw_dir": "/home/u", "pw_gid": 1000})())
    monkeypatch.setattr(alerts.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Done())
    r = alerts.deliver("Title", "Body")
    assert r == {"sent": 1, "via": ["busctl"]}
    cmd, kw = calls[0]
    assert cmd[:3] == ["busctl", "--user", "call"] and "Title" in cmd
    assert kw["user"] == 1000 and kw["env"]["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={tmp_path}/1000/bus"


# ----------------------------------------------------------------- backup / restore
def test_backup_roundtrip(client):
    from coolpilot import profiles
    client.post("/api/profiles", json={"name": "Lectures", "description": "quiet"})
    client.post("/api/stability/config", json={"items": {"cpu_max_mhz": {"value": 2500}}})
    client.post("/api/alerts", json={"items": {"cpu_hot": {"threshold": 88}}})
    client.post("/api/profiles/Lectures/boot", json={"enabled": True})
    profiles.update_config(charge_full_once={"since": 1}, crash_seen="e" * 32)
    data = client.get("/api/backup").get_json()
    assert data["format"] == "coolpilot-backup" and "Lectures" in data["profiles"]
    assert "charge_full_once" not in data["preferences"] and "crash_seen" not in data["preferences"]
    assert "_before_stability" not in data["profiles"]

    client.post("/api/reset", json={"delete_profiles": True})
    client.post("/api/alerts", json={"items": {"cpu_hot": {"threshold": 95}}})
    info = client.post("/api/backup/inspect", json={"data": data}).get_json()
    assert info["profiles"] == ["Lectures"] and info["warnings"] == []
    r = client.post("/api/backup/restore", json={"data": data}).get_json()
    assert r["restored"] == ["profiles", "stability", "preferences"]
    names = [p["name"] for p in client.get("/api/profiles").get_json()["profiles"]]
    assert "Lectures" in names
    stab = {i["key"]: i for i in client.get("/api/stability").get_json()["items"]}
    assert stab["cpu_max_mhz"]["value"] == 2500
    assert {i["kind"]: i for i in client.get("/api/alerts").get_json()["items"]}["cpu_hot"]["threshold"] == 88
    assert profiles.config()["boot_profile"] == "Lectures"


def test_restore_turns_stability_on_like_the_backup(client):
    client.post("/api/stability", json={"enabled": True})
    data = client.get("/api/backup").get_json()
    client.post("/api/stability", json={"enabled": False})
    assert read("sys/firmware/acpi/platform_profile") == "balanced"
    client.post("/api/backup/restore", json={"data": data})
    assert client.get("/api/stability").get_json()["on"] is True
    assert read("sys/firmware/acpi/platform_profile") == "quiet"
    client.post("/api/stability", json={"enabled": False})         # and it can still restore
    assert read("sys/firmware/acpi/platform_profile") == "balanced"


def test_restore_only_some_parts(client):
    client.post("/api/profiles", json={"name": "Mine"})
    client.post("/api/alerts", json={"enabled": False})
    data = client.get("/api/backup").get_json()
    client.post("/api/reset", json={"delete_profiles": True})
    client.post("/api/alerts", json={"enabled": True})
    client.post("/api/backup/restore", json={"data": data, "parts": ["profiles"]})
    assert "Mine" in [p["name"] for p in client.get("/api/profiles").get_json()["profiles"]]
    assert client.get("/api/alerts").get_json()["enabled"] is True   # preferences not touched
    assert client.post("/api/backup/restore", json={"data": data, "parts": []}).status_code == 400


def test_backup_rejects_bad_files_and_drops_bad_values(client):
    for bad in (None, {"format": "other"}, {"format": "coolpilot-backup", "version": 99}, [1, 2]):
        assert client.post("/api/backup/inspect", json={"data": bad}).status_code == 400, bad
    data = {"format": "coolpilot-backup", "version": 1, "profiles": {
        "Good": {"settings": {"platform_profile": "quiet", "ppt_pl1": 40, "cpu_boost": False, "epp": "__default__"}},
        "Bad values": {"settings": {"ppt_pl1": "lots", "made_up": 1, "cpu_boost": "yes"},
                       "fans": {"1": {"custom": True, "points": [[1, 2]]}}},
        "Stability": {"settings": {}}, "_hidden": {"settings": {}}},
        "stability": {"items": {"cpu_max_mhz": {"value": -5, "enabled": False}, "nope": {}}},
        "preferences": {"boot_profile": "Missing one", "fan_mode": "turbo", "alerts": {"items": {"cpu_hot": {"threshold": 1}}}}}
    info = client.post("/api/backup/inspect", json={"data": data}).get_json()
    assert info["profiles"] == ["Bad values", "Good"]
    text = " | ".join(info["warnings"])
    for bit in ("ppt_pl1 skipped", "made_up skipped", "cpu_boost skipped", "fan 1 skipped", "built-in profile",
                "invalid name", "cpu_max_mhz value skipped", "unknown item nope", "alerts skipped"):
        assert bit in text, bit
    r = client.post("/api/backup/restore", json={"data": data}).get_json()
    assert any("isn't on this laptop" in w for w in r["warnings"])
    good = next(p for p in client.get("/api/profiles").get_json()["profiles"] if p["name"] == "Good")
    assert good["settings"] == {"platform_profile": "quiet", "ppt_pl1": 40, "cpu_boost": False, "epp": "__default__"}


def test_cli_export_and_import(client, monkeypatch, tmp_path, capsys):
    from coolpilot import cli
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path, body))
        if path == "/backup":
            return client.get("/api/backup").get_json()
        return client.open(f"/api{path}", method=method, json=body).get_json()
    monkeypatch.setattr(cli, "api", fake_api)
    out = tmp_path / "b.json"
    assert cli.cmd(["export", str(out)]) == 0 and out.exists()
    assert cli.cmd(["import", str(out)]) == 0
    assert calls[-1][:2] == ("POST", "/backup/restore")
    assert "Settings restored" in capsys.readouterr().out
    assert cli.cmd(["alerts", "off"]) == 0 and client.get("/api/alerts").get_json()["enabled"] is False
