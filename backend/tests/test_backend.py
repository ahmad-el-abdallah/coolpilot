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
    from coolpilot.app import device_name
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
