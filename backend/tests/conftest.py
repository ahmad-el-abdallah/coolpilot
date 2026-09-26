"""Build a fake sysfs tree mirroring this laptop, before the app modules import."""
import os
import tempfile

import pytest

ROOT = tempfile.mkdtemp(prefix="tuf-sysfs-")
CONF = tempfile.mkdtemp(prefix="tuf-conf-")
os.environ["TUF_SYSFS_ROOT"] = ROOT
os.environ["TUF_CONFIG_DIR"] = CONF
os.environ["TUF_TOKEN"] = "test-token"
os.environ["TUF_DIST"] = os.path.join(ROOT, "dist")
os.environ["PATH"] = "/nonexistent"  # no powerprofilesctl / nvidia-smi / lspci / glmark2 in tests
os.environ["TUF_DIAG_DIR"] = tempfile.mkdtemp(prefix="tuf-diag-")
os.environ["TUF_PCIE_INTERVAL"] = "0.05"

ARM = "sys/class/firmware-attributes/asus-armoury/attributes"


def w(rel, value):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def build():
    w("sys/firmware/acpi/platform_profile", "balanced")
    w("sys/firmware/acpi/platform_profile_choices", "quiet balanced performance")
    w("sys/devices/system/cpu/cpufreq/boost", 1)
    for c in range(2):
        d = f"sys/devices/system/cpu/cpu{c}/cpufreq"
        w(f"{d}/boost", 1)
        w(f"{d}/scaling_max_freq", 3100000)
        w(f"{d}/scaling_min_freq", 411505)
        w(f"{d}/scaling_cur_freq", 1500000)
        w(f"{d}/cpuinfo_min_freq", 411505)
        w(f"{d}/cpuinfo_max_freq", 3100000)
        w(f"{d}/amd_pstate_max_freq", 4553983)
        w(f"{d}/energy_performance_preference", "balance_performance")
        w(f"{d}/energy_performance_available_preferences",
          "default performance balance_performance balance_power power custom")
    for attr, cur, lo, hi in [("ppt_pl1_spl", 80, 15, 80), ("ppt_pl2_sppt", 80, 35, 80),
                              ("ppt_pl3_fppt", 80, 35, 80), ("nv_dynamic_boost", 25, 5, 25),
                              ("nv_temp_target", 87, 75, 87)]:
        w(f"{ARM}/{attr}/current_value", cur)
        w(f"{ARM}/{attr}/min_value", lo)
        w(f"{ARM}/{attr}/max_value", hi)
        w(f"{ARM}/{attr}/default_value", cur)
    w(f"{ARM}/panel_overdrive/current_value", 1)
    w(f"{ARM}/panel_overdrive/possible_values", "0;1")
    w("sys/class/power_supply/BAT1/charge_control_end_threshold", 100)
    w("sys/class/power_supply/BAT1/voltage_now", 15000000)
    w("sys/class/power_supply/BAT1/power_now", 20000000)
    w("sys/class/power_supply/BAT1/capacity", 77)
    w("sys/class/power_supply/ACAD/online", 0)
    w("sys/class/power_supply/ACAD/type", "Mains")
    w("sys/class/power_supply/BAT1/type", "Battery")
    w("sys/class/power_supply/BAT1/scope", "System")
    w("sys/class/leds/asus::kbd_backlight/brightness", 1)
    w("sys/class/leds/asus::kbd_backlight/max_brightness", 3)
    w("sys/class/hwmon/hwmon5/name", "k10temp")
    w("sys/class/hwmon/hwmon5/temp1_input", 55000)
    w("sys/class/hwmon/hwmon9/name", "asus_custom_fan_curve")
    for fan in (1, 2):
        w(f"sys/class/hwmon/hwmon9/pwm{fan}_enable", 2)
        for i in range(1, 9):
            w(f"sys/class/hwmon/hwmon9/pwm{fan}_auto_point{i}_temp", 40 + i * 5)
            w(f"sys/class/hwmon/hwmon9/pwm{fan}_auto_point{i}_pwm", i * 20)
    # PCI: GPU behind its CPU root port, like the real laptop (bus/pci/devices are symlinks)
    tree = "sys/devices/pci0000:00"
    for bdf, rel, vendor, cls in [("0000:00:01.1", "0000:00:01.1", "0x1022", "0x060400"),
                                  ("0000:01:00.0", "0000:00:01.1/0000:01:00.0", "0x10de", "0x030000"),
                                  ("0000:04:00.0", "0000:04:00.0", "0x1344", "0x010802")]:
        d = f"{tree}/{rel}"
        w(f"{d}/vendor", vendor)
        w(f"{d}/class", cls)
        w(f"{d}/current_link_speed", "16.0 GT/s PCIe")
        w(f"{d}/max_link_speed", "16.0 GT/s PCIe")
        w(f"{d}/current_link_width", 8 if bdf != "0000:04:00.0" else 4)
        w(f"{d}/max_link_width", 8 if bdf != "0000:04:00.0" else 4)
        w(f"{d}/aer_dev_correctable", "RxErr 0\nBadTLP 0\nTOTAL_ERR_COR 0")
        w(f"{d}/aer_dev_nonfatal", "Undefined 0\nTOTAL_ERR_NONFATAL 0")
        w(f"{d}/aer_dev_fatal", "Undefined 0\nTOTAL_ERR_FATAL 0")
        link = os.path.join(ROOT, "sys/bus/pci/devices", bdf)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.islink(link):
            os.symlink(os.path.join(ROOT, d), link)
    w("proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0")
    w("proc/loadavg", "0.5 0.4 0.3 1/100 1")
    w("proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable: 8000000 kB")


@pytest.fixture(autouse=True)
def fresh_tree():
    build()
    for f in os.listdir(CONF):
        if f != "token":
            os.remove(os.path.join(CONF, f))
    yield


def read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read().strip()


@pytest.fixture
def client():
    from tuf.app import create_app
    app = create_app("test-token")
    app.config["SERVER_NAME"] = "127.0.0.1:8787"  # makes the test client send this Host
    c = app.test_client()
    c.environ_base["HTTP_X_TUF_TOKEN"] = "test-token"
    return c
