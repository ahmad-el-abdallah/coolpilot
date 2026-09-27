"""Friendly device name from DMI, e.g. "ASUS TUF Gaming A15 FA507NVR"."""
from __future__ import annotations

from . import sysfs

VENDORS = {"asustek": "ASUS", "lenovo": "Lenovo", "hewlett": "HP", "hp": "HP", "dell": "Dell",
           "micro-star": "MSI", "acer": "Acer", "gigabyte": "Gigabyte", "razer": "Razer",
           "samsung": "Samsung", "microsoft": "Microsoft", "framework": "Framework",
           "tuxedo": "TUXEDO", "system76": "System76", "huawei": "Huawei", "xiaomi": "Xiaomi"}
PLACEHOLDERS = {"", "to be filled by o.e.m.", "system product name", "system manufacturer", "default string",
                "not applicable", "none", "system version", "0123456789"}


def device_name() -> str:
    """A friendly model name from DMI, e.g. "ASUS TUF Gaming A15 FA507NVR"."""
    r = lambda f: (sysfs.read(f"sys/class/dmi/id/{f}") or "").strip()  # noqa: E731
    vendor, product, version = r("sys_vendor"), r("product_name"), r("product_version")
    if vendor.lower() in PLACEHOLDERS:
        vendor = ""
    if product.lower() in PLACEHOLDERS:
        product = ""
    # Lenovo: product_name is a machine type ("82JU"), the model is in product_version
    if vendor.lower().startswith("lenovo") and version.lower() not in PLACEHOLDERS:
        product = version
    # ASUS repeats the model code: "... FA507NVR_FA507NVR"
    words = [w.split("_")[0] if "_" in w and len(set(w.split("_"))) == 1 else w for w in product.split()]
    name = " ".join(words)
    short = next((v for k, v in VENDORS.items() if vendor.lower().startswith(k)), vendor.split(" ")[0] if vendor else "")
    if short and not name.lower().startswith(short.lower()):
        name = f"{short} {name}".strip()
    return name or "This laptop"
