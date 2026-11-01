# Dual boot: the same protection on Windows (G-Helper)

CoolPilot's settings don't carry over to other operating systems. CPU boost, the GHz cap and
the energy preference are set by each OS's own CPU driver, and the firmware forgets power
limits and fan curves at every restart, so each OS has to apply them itself. The BIOS
on these laptops has no options for any of them.

- **Another Linux distro:** install CoolPilot there too. It works on any distro with systemd.
- **Windows:** use [G-Helper](https://github.com/seerge/g-helper), a small, free, open-source
  replacement for Armoury Crate on ASUS laptops. These are the values of CoolPilot's
  **Recommended** Stability mode:

| Stability setting | CoolPilot value | In G-Helper |
|---|---|---|
| Performance mode | Silent | Main window → **Silent** |
| CPU boost | Off | *Fans + Power* → **CPU Boost: Disabled** |
| CPU max frequency | 3.0 GHz | Not needed: with boost off the CPU stays near its base clock |
| CPU sustained limit (PL1 / SPL) | 35 W | *Fans + Power* → Power Limits → **CPU Sustained** |
| CPU short limit (PL2 / sPPT) | 45 W | *Fans + Power* → Power Limits → **CPU Slow** |
| CPU burst limit (PL3 / fPPT) | 45 W | *Fans + Power* → Power Limits → **CPU Fast** |
| GPU Dynamic Boost | Lowest allowed | *Fans + Power* → GPU → **Dynamic Boost** at minimum |
| GPU temperature target | Lowest allowed | *Fans + Power* → GPU → **Temp Target** at minimum |
| Battery charge limit | 80% | Main window → **Battery Charge Limit** |

G-Helper stores power limits and fan curves **per mode**, so select Silent first, then set
the values in *Fans + Power* and turn on its option to apply the power limits. G-Helper
re-applies them at startup, just like CoolPilot does on Linux. Menu names may differ slightly
between G-Helper versions.

> Don't use Armoury Crate at the same time as G-Helper; both will fight over the same settings.
