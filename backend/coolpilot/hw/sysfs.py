"""Whitelisted sysfs settings: the only things the web app can write.

Every setting is described once in SETTINGS. Writes go through `write_setting`,
which validates against the setting's type and range; nothing accepts raw paths.
`COOLPILOT_SYSFS_ROOT` lets tests point everything at a fake tree.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Callable

ROOT = os.environ.get("COOLPILOT_SYSFS_ROOT", "/")

ARMOURY = "sys/class/firmware-attributes/asus-armoury/attributes"
CPUFREQ = "sys/devices/system/cpu"


class SettingError(ValueError):
    pass


def p(rel: str) -> str:
    return os.path.join(ROOT, rel)


def read(rel: str, default: str | None = None) -> str | None:
    try:
        with open(p(rel)) as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(rel: str, default: int | None = None) -> int | None:
    v = read(rel)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def write(rel: str, value: str | int) -> None:
    with open(p(rel), "w") as f:
        f.write(str(value))


def exists(rel: str) -> bool:
    return os.path.exists(p(rel))


def glob_rel(pattern: str) -> list[str]:
    return sorted(os.path.relpath(x, ROOT) for x in glob.glob(p(pattern)))


def cpu_policies(fname: str) -> list[str]:
    return glob_rel(f"{CPUFREQ}/cpu[0-9]*/cpufreq/{fname}")


def find_hwmon(name: str) -> str | None:
    for d in glob_rel("sys/class/hwmon/hwmon*"):
        if read(f"{d}/name") == name:
            return d
    return None


def find_all_hwmon(name: str) -> list[str]:
    return [d for d in glob_rel("sys/class/hwmon/hwmon*") if read(f"{d}/name") == name]


def power_supply(kind: str, want: str | None = None) -> str | None:
    """First power supply of this type ('Battery' or 'Mains'). Names differ per
    model (BAT0/BAT1/BATT, AC/AC0/ACAD/ADP1), so match on type; skip device
    batteries (wireless mice...) and prefer one that has the `want` file."""
    found = [d for d in glob_rel("sys/class/power_supply/*")
             if read(f"{d}/type") == kind and read(f"{d}/scope") != "Device"]
    if want:
        found.sort(key=lambda d: not exists(f"{d}/{want}"))
    return found[0] if found else None


def battery() -> str | None:
    return power_supply("Battery", "charge_control_end_threshold")


def on_ac() -> bool:
    ac = power_supply("Mains")
    return bool(ac) and read(f"{ac}/online") == "1"


def first_backlight() -> str | None:
    found = glob_rel("sys/class/backlight/*")
    return found[0] if found else None


# --------------------------------------------------------------------------- #
# Setting model
# --------------------------------------------------------------------------- #

@dataclass
class Setting:
    key: str
    label: str
    group: str
    kind: str  # "choice" | "int" | "bool"
    paths: Callable[[], list[str]]
    unit: str = ""
    help: str = ""
    keywords: list[str] = field(default_factory=list)
    choices: Callable[[], list[str]] | None = None
    min: Callable[[], int | None] | None = None
    max: Callable[[], int | None] | None = None
    default: Callable[[], object] | None = None
    # convert UI value <-> raw sysfs value (e.g. MHz <-> kHz)
    to_raw: Callable[[object], str] = str
    from_raw: Callable[[str], object] | None = None
    # bool files where 1 means "off" (Intel's intel_pstate/no_turbo)
    inverted: Callable[[str], bool] = lambda path: False

    def available(self) -> bool:
        return bool(self.paths()) and all(exists(x) for x in self.paths())

    def get(self):
        paths = self.paths()
        if not paths:
            return None
        raw = read(paths[0])
        if raw is None:
            return None
        if self.from_raw:
            return self.from_raw(raw)
        if self.kind == "int":
            return int(raw)
        if self.kind == "bool":
            return (raw in ("1", "Y", "y", "on")) != self.inverted(paths[0])
        return raw

    def describe(self) -> dict:
        d = {
            "key": self.key, "label": self.label, "group": self.group, "kind": self.kind,
            "unit": self.unit, "help": self.help, "keywords": self.keywords,
            "available": self.available(),
        }
        d["value"] = self.get() if d["available"] else None
        if self.choices:
            d["choices"] = self.choices()
        if self.min:
            d["min"] = self.min()
        if self.max:
            d["max"] = self.max()
        if self.default:
            d["default"] = self.default()
        # firmware can pin a range (e.g. GPU Dynamic Boost is 0-0 W on battery)
        d["locked"] = self.kind == "int" and d.get("min") is not None and d.get("min") == d.get("max")
        return d

    def validate(self, value):
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if value in (0, 1):
                return bool(value)
            raise SettingError(f"{self.key}: expected true/false")
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
                raise SettingError(f"{self.key}: expected an integer")
            value = int(value)
            lo = self.min() if self.min else None
            hi = self.max() if self.max else None
            if lo is not None and value < lo:
                raise SettingError(f"{self.key}: {value} is below minimum {lo}")
            if hi is not None and value > hi:
                raise SettingError(f"{self.key}: {value} is above maximum {hi}")
            return value
        if self.kind == "choice":
            opts = self.choices() if self.choices else []
            if value not in opts:
                raise SettingError(f"{self.key}: '{value}' not one of {opts}")
            return value
        raise SettingError(f"{self.key}: unknown kind")

    def set(self, value) -> None:
        if not self.available():
            raise SettingError(f"{self.key}: not supported on this machine")
        if self.kind == "int" and self.min and self.max and self.min() is not None and self.min() == self.max():
            raise SettingError(f"{self.label} is fixed at {self.min()}{self.unit} by the firmware right now "
                               "(ASUS locks some limits on battery - plug in the charger to change it)")
        value = self.validate(value)
        errors = []
        for path in self.paths():
            if self.kind == "bool":
                raw = "1" if value != self.inverted(path) else "0"
            else:
                raw = self.to_raw(value)
            try:
                write(path, raw)
            except OSError as e:
                errors.append(f"{path}: {e.strerror or e}")
        if errors and len(errors) == len(self.paths()):
            raise SettingError(f"{self.key}: write failed ({errors[0]})")


# --------------------------------------------------------------------------- #
# Helpers for specific settings
# --------------------------------------------------------------------------- #

def _armoury(attr: str, key: str, label: str, group: str, kind: str = "int",
             legacy: str | None = None, **kw) -> Setting:
    """asus-armoury firmware attribute (newer kernels: has min/max/default).
    `legacy` is the old asus-nb-wmi file, only used for on/off settings since it
    carries no range information."""
    base = f"{ARMOURY}/{attr}"

    def paths():
        if exists(f"{base}/current_value"):
            return [f"{base}/current_value"]
        if legacy and kind == "bool" and exists(f"sys/devices/platform/asus-nb-wmi/{legacy}"):
            return [f"sys/devices/platform/asus-nb-wmi/{legacy}"]
        return []

    def choices():
        pv = read(f"{base}/possible_values") or ""
        return [x for x in pv.split(";") if x]

    extra = {}
    if kind == "int":
        extra = dict(min=lambda: read_int(f"{base}/min_value"),
                     max=lambda: read_int(f"{base}/max_value"),
                     default=lambda: read_int(f"{base}/default_value"))
    return Setting(key=key, label=label, group=group, kind=kind, paths=paths, **extra, **kw)


def _max_freq_khz() -> int | None:
    # amd_pstate_max_freq is the real boost ceiling; cpuinfo_max_freq drops to
    # nominal while boost is off.
    return read_int(f"{CPUFREQ}/cpu0/cpufreq/amd_pstate_max_freq") or \
        read_int(f"{CPUFREQ}/cpu0/cpufreq/cpuinfo_max_freq")


def _min_freq_khz() -> int | None:
    return read_int(f"{CPUFREQ}/cpu0/cpufreq/cpuinfo_min_freq")


def _led(rel_fn: Callable[[], str | None], attr: str) -> Callable[[], list[str]]:
    def f():
        base = rel_fn()
        return [f"{base}/{attr}"] if base else []
    return f


KBD = "sys/class/leds/asus::kbd_backlight"

SETTINGS: dict[str, Setting] = {s.key: s for s in [
    Setting(
        key="platform_profile", label="Performance mode", group="power", kind="choice",
        paths=lambda: ["sys/firmware/acpi/platform_profile"],
        choices=lambda: (read("sys/firmware/acpi/platform_profile_choices") or "").split(),
        help="ASUS fan/power mode. Silent = cooler and slower. Same as Fn+F5.",
        keywords=["silent", "quiet", "low-power", "balanced", "performance", "turbo", "fn f5", "mode", "fan"],
        # factory behaviour: Turbo on the charger, Balanced on battery
        default=lambda: "performance" if on_ac() else "balanced"),
    Setting(
        key="epp", label="CPU energy preference", group="cpu", kind="choice",
        paths=lambda: cpu_policies("energy_performance_preference"),
        choices=lambda: [c for c in (read(f"{CPUFREQ}/cpu0/cpufreq/energy_performance_available_preferences") or "").split() if c != "custom"],
        help="Hint to the CPU on how aggressively to raise clocks. 'power' saves most energy/heat.",
        keywords=["epp", "energy", "efficiency", "power saving", "battery"],
        default=lambda: "balance_performance"),
    Setting(
        key="cpu_boost", label="CPU boost", group="cpu", kind="bool",
        # per-CPU files first: they are what actually applies (the global file can
        # read 1 while every policy is 0)
        # read 1 while every policy is 0). Intel (intel_pstate) only has no_turbo.
        paths=lambda: (cpu_policies("boost") + [x for x in ["sys/devices/system/cpu/cpufreq/boost"] if exists(x)])
        or [x for x in ["sys/devices/system/cpu/intel_pstate/no_turbo"] if exists(x)],
        inverted=lambda path: path.endswith("no_turbo"),
        help="Allow the CPU to run above its base clock (Precision Boost / Turbo Boost). Off = much cooler.",
        keywords=["turbo", "boost", "precision boost", "turbo boost", "frequency", "ghz", "heat"],
        default=lambda: True),
    Setting(
        key="cpu_max_mhz", label="Max CPU frequency", group="cpu", kind="int", unit="MHz",
        paths=lambda: cpu_policies("scaling_max_freq"),
        min=lambda: (_min_freq_khz() or 400000) // 1000,
        max=lambda: (_max_freq_khz() or 3100000) // 1000,
        default=lambda: (_max_freq_khz() or 3100000) // 1000,
        to_raw=lambda mhz: str(int(mhz) * 1000), from_raw=lambda raw: int(raw) // 1000,
        help="Cap the CPU clock. Lower = less heat and power. Values above base need boost on.",
        keywords=["ghz", "mhz", "frequency", "clock", "limit", "cap", "underclock", "speed"]),
    Setting(
        key="cpu_min_mhz", label="Min CPU frequency", group="cpu", kind="int", unit="MHz",
        paths=lambda: cpu_policies("scaling_min_freq"),
        min=lambda: (_min_freq_khz() or 400000) // 1000,
        max=lambda: (_max_freq_khz() or 3100000) // 1000,
        default=lambda: (_min_freq_khz() or 400000) // 1000,
        to_raw=lambda mhz: str(int(mhz) * 1000), from_raw=lambda raw: int(raw) // 1000,
        help="Lowest clock the CPU may idle at. Leave at minimum normally.",
        keywords=["ghz", "mhz", "frequency", "idle"]),
    _armoury("ppt_pl1_spl", "ppt_pl1", "CPU sustained power (PL1)", "power", unit="W",
             help="Long-term CPU power limit. Lower = cooler, quieter, slower.",
             keywords=["tdp", "watts", "power limit", "spl", "ppt"]),
    _armoury("ppt_pl2_sppt", "ppt_pl2", "CPU short boost power (PL2)", "power", unit="W",
             help="Power allowed for short bursts (~2 min).",
             keywords=["tdp", "watts", "power limit", "sppt", "ppt", "burst"]),
    _armoury("ppt_pl3_fppt", "ppt_pl3", "CPU peak power (PL3)", "power", unit="W",
             help="Power allowed for very short spikes (seconds).",
             keywords=["tdp", "watts", "power limit", "fppt", "ppt", "spike"]),
    _armoury("nv_dynamic_boost", "gpu_dynamic_boost", "GPU Dynamic Boost", "gpu", unit="W",
             help="Extra watts the NVIDIA GPU can borrow from the CPU budget.",
             keywords=["nvidia", "rtx", "dynamic boost", "gpu power", "watts"]),
    _armoury("nv_temp_target", "gpu_temp_target", "GPU temperature target", "gpu", unit="°C",
             help="GPU slows down to stay under this temperature.",
             keywords=["nvidia", "rtx", "temperature", "thermal", "throttle"]),
    _armoury("panel_overdrive", "panel_overdrive", "Panel overdrive", "display", kind="bool", legacy="panel_od",
             help="Faster pixel response on the internal screen (less ghosting).",
             keywords=["screen", "display", "overdrive", "ghosting", "response time"],
             from_raw=lambda raw: raw == "1"),
    Setting(
        key="charge_limit", label="Battery charge limit", group="battery", kind="int", unit="%",
        paths=lambda: [f"{battery()}/charge_control_end_threshold"] if battery() else [],
        min=lambda: 20, max=lambda: 100, default=lambda: 100,
        help="Stop charging at this level. 80% greatly extends battery life when plugged in often.",
        keywords=["battery", "charge", "limit", "health", "80"]),
    Setting(
        key="kbd_backlight", label="Keyboard backlight", group="display", kind="int",
        paths=lambda: [f"{KBD}/brightness"] if exists(KBD) else [],
        min=lambda: 0, max=lambda: read_int(f"{KBD}/max_brightness", 3),
        help="Keyboard light level.", keywords=["keyboard", "light", "rgb", "backlight"]),
    Setting(
        key="screen_brightness", label="Screen brightness", group="display", kind="int",
        paths=_led(first_backlight, "brightness"),
        min=lambda: 1,
        max=lambda: read_int(f"{first_backlight()}/max_brightness", 100) if first_backlight() else 100,
        help="Internal display brightness.", keywords=["screen", "brightness", "display", "dim"]),
]}


def describe_all() -> list[dict]:
    return [s.describe() for s in SETTINGS.values()]


def set_setting(key: str, value) -> dict:
    s = SETTINGS.get(key)
    if s is None:
        raise SettingError(f"unknown setting '{key}'")
    s.set(value)
    return s.describe()


def snapshot() -> dict:
    """Current value of every available setting (for saving a profile)."""
    return {k: s.get() for k, s in SETTINGS.items() if s.available()}
