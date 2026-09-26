"""ASUS custom fan curves (asus-wmi `asus_custom_fan_curve` hwmon).

Each fan has 8 (temp °C, pwm 0-255) points. pwmN_enable: 1 = custom curve,
2 = firmware default for the current platform profile, 3 = reset curve to defaults.
Changing the platform profile makes the firmware reload its own curve, so
profiles apply the platform profile before fan curves.
"""
from __future__ import annotations

from . import sysfs

POINTS = 8
FANS = {1: "CPU fan", 2: "GPU fan"}

PRESETS: dict[str, list[list[int]]] = {
    # temps and pwm (0-255) for 8 points; used for both fans.
    # "stability": fans never stop (~24% floor) and ramp early and smoothly, so the
    # chips stay cooler and, above all, steadier - fewer heat swings means less
    # expanding/contracting of weak solder joints. A little louder at idle.
    "stability": [[30, 60], [40, 75], [50, 95], [58, 120], [66, 150], [74, 190], [82, 230], [88, 255]],
    "silent": [[45, 0], [55, 20], [65, 40], [72, 60], [78, 90], [84, 130], [90, 190], [95, 255]],
    "balanced": [[40, 10], [50, 35], [60, 60], [68, 90], [75, 120], [82, 160], [88, 210], [94, 255]],
    "cool": [[35, 40], [45, 70], [55, 100], [62, 130], [70, 170], [77, 210], [84, 245], [90, 255]],
    "max": [[30, 255]] + [[40 + i * 8, 255] for i in range(7)],
}


class FanError(ValueError):
    pass


def _dir() -> str | None:
    return sysfs.find_hwmon("asus_custom_fan_curve")


def available() -> bool:
    return _dir() is not None


def read_all() -> dict:
    d = _dir()
    if not d:
        return {"available": False, "fans": []}
    fans = []
    for n, label in FANS.items():
        if not sysfs.exists(f"{d}/pwm{n}_enable"):
            continue
        pts = [[sysfs.read_int(f"{d}/pwm{n}_auto_point{i}_temp", 0),
                sysfs.read_int(f"{d}/pwm{n}_auto_point{i}_pwm", 0)] for i in range(1, POINTS + 1)]
        fans.append({"fan": n, "label": label,
                     "custom": sysfs.read_int(f"{d}/pwm{n}_enable") == 1, "points": pts})
    return {"available": True, "fans": fans, "presets": PRESETS}


def validate(points) -> list[list[int]]:
    if not isinstance(points, list) or len(points) != POINTS:
        raise FanError(f"curve needs exactly {POINTS} points")
    out = []
    for pt in points:
        if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in pt)):
            raise FanError("each point must be [temp, pwm] integers")
        t, w = pt
        if not 20 <= t <= 110:
            raise FanError(f"temperature {t}°C out of range 20-110")
        if not 0 <= w <= 255:
            raise FanError(f"fan speed {w} out of range 0-255")
        out.append([t, w])
    for a, b in zip(out, out[1:]):
        if b[0] < a[0]:
            raise FanError("temperatures must increase from point to point")
        if b[1] < a[1]:
            raise FanError("fan speed must not decrease as temperature rises")
    return out


def set_curve(fan: int, points, enable: bool = True) -> None:
    d = _dir()
    if not d:
        raise FanError("custom fan curves not supported")
    if fan not in FANS:
        raise FanError(f"unknown fan {fan}")
    pts = validate(points)
    try:
        for i, (t, w) in enumerate(pts, start=1):
            sysfs.write(f"{d}/pwm{fan}_auto_point{i}_temp", t)
            sysfs.write(f"{d}/pwm{fan}_auto_point{i}_pwm", w)
        if enable:
            sysfs.write(f"{d}/pwm{fan}_enable", 1)
    except OSError as e:
        raise FanError(f"write failed: {e.strerror or e}") from e


def set_mode(fan: int, custom: bool) -> None:
    d = _dir()
    if not d or fan not in FANS:
        raise FanError("fan not supported")
    try:
        sysfs.write(f"{d}/pwm{fan}_enable", 1 if custom else 2)
    except OSError as e:
        raise FanError(f"write failed: {e.strerror or e}") from e


def reset(fan: int) -> None:
    d = _dir()
    if not d or fan not in FANS:
        raise FanError("fan not supported")
    try:
        sysfs.write(f"{d}/pwm{fan}_enable", 3)
        sysfs.write(f"{d}/pwm{fan}_enable", 2)
    except OSError as e:
        raise FanError(f"write failed: {e.strerror or e}") from e
