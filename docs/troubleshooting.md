# Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Backend unreachable" | `systemctl status coolpilot` · `journalctl -u coolpilot -n 50` |
| Changes don't show up after `git pull` | run `sudo ./install.sh` again, then hard-reload the page (Ctrl+Shift+R) |
| "Permission denied" when changing a setting | you're running a dev copy as a normal user — use the installed service |
| A setting shows 🔒 | the firmware locks it right now (usually on battery) — plug in the charger |
| Profile not re-applied after boot | `journalctl -u coolpilot-boot` |
| Fan curves reset | make sure you picked a fan mode on the Fans page — it is re-applied after reboot / sleep / mode changes |
| Settings keep changing back | another tool (asusctl, TLP, auto-cpufreq, TuneD) is managing them — see the Dashboard warning |
| Crash history only shows the current boot | the journal isn't persistent: `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald` |
| `python3 -m venv` fails (Debian / Ubuntu) | `sudo apt install python3-venv` |
| GPU crash test does nothing | install `glmark2`; on NVIDIA laptops make sure the NVIDIA driver is loaded (`nvidia-smi`) |
| Desktop alerts don't appear | Alerts page → *Send a test alert*. It needs a notification daemon (built into GNOME/KDE; mako, dunst or swaync on tiling desktops). Alerts that found no desktop wait up to an hour and show after you log in |
| Touchpad/mouse speed shows "Not supported on this desktop" | No desktop session was found, or it isn't one of Hyprland, Sway, GNOME, KDE Plasma or X11 (`xinput`) — log in on the desktop first; see [Input & Keyboard](../README.md#features) |
| Keyboard RGB doesn't change | `kbd_rgb_mode` is write-only on the firmware — CoolPilot can't read back what's set, only replay the last value it wrote. If nothing happens, the kernel likely doesn't expose this attribute on your model |
