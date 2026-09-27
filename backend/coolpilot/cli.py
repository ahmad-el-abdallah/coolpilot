"""`coolpilot` command: control CoolPilot from a terminal, scripts or a status bar.

Talks to the local service on 127.0.0.1. Standard library only, so it runs from
the service's venv without importing the rest of the app.

    coolpilot status [--json]          what the laptop is doing
    coolpilot bar                      one-line JSON for Omarchy's bar / Waybar
    coolpilot stability on|off|toggle
    coolpilot mode silent|balanced|turbo|next
    coolpilot fans default|stability
    coolpilot profile [name]           list profiles, or apply one
    coolpilot gaming                   Gaming mode (best performance)
    coolpilot charge full|cancel|<20-100>
    coolpilot open                     open the web app
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = int(os.environ.get("COOLPILOT_PORT", "8787"))
BASE = f"http://127.0.0.1:{PORT}"
MODE_ALIASES = {"silent": "quiet", "quiet": "quiet", "low-power": "low-power", "balanced": "balanced",
                "turbo": "performance", "performance": "performance"}
MODE_NAMES = {"quiet": "Silent", "low-power": "Silent", "balanced": "Balanced", "performance": "Turbo"}
ICON = "󰈐"  # nerd-font fan (Omarchy's bar font has it)


class CliError(Exception):
    pass


def _token() -> str:
    # The page embeds the API token; any local program can read it, web pages can't.
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as r:
            page = r.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise CliError(f"CoolPilot isn't running on {BASE} ({e})") from e
    m = re.search(r'name="coolpilot-token" content="([^"]+)"', page)
    if not m:
        raise CliError("couldn't read the CoolPilot token from the page")
    return m.group(1)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"{BASE}/api{path}", method=method,
                                 headers={"X-CoolPilot-Token": _token(), "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except ValueError:
            msg = str(e)
        raise CliError(msg) from e
    except (urllib.error.URLError, OSError) as e:
        raise CliError(f"CoolPilot isn't running on {BASE} ({e})") from e


def _problems(results: dict) -> list[str]:
    return [f"{k}: {v}" for k, v in (results or {}).items()
            if not (v == "ok" or v.startswith("ok:") or v.startswith("skipped") or v == "not supported")]


def _temp(v) -> str:
    return f"{round(v)}°C" if isinstance(v, (int, float)) else "—"


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def describe(s: dict) -> list[str]:
    mode = MODE_NAMES.get(s.get("profile") or "", s.get("profile") or "?")
    gpu = "asleep" if s.get("gpu_state") == "suspended" else _temp(s.get("gpu_temp"))
    fans = " / ".join(str(f) for f in s.get("fans") or [] if f is not None) or "—"
    power = "on charger" if s.get("ac") else "on battery"
    lines = [
        f"CoolPilot · {s.get('device', '')}",
        f"Stability mode: {'ON' if s.get('stability') else 'off'} · Performance mode: {mode}",
        f"CPU {_temp(s.get('cpu_temp'))} at {(s.get('cpu_mhz') or 0) / 1000:.2f} GHz · GPU {gpu}",
        f"Fans {fans} rpm ({s.get('fan_mode', 'default')} fan mode)",
        f"Battery {s.get('battery', '—')}% {power} · charge limit {s.get('charge_limit', '—')}%"
        + (" · charging to 100% once" if s.get("full_once") else ""),
    ]
    if s.get("new_crash"):
        lines.append("⚠ The laptop froze or restarted recently — open CoolPilot to see what happened")
    return lines


def bar_json(s: dict | None, error: str | None = None) -> dict:
    """Waybar-style JSON, also understood by Omarchy's bar command modules."""
    if s is None:
        return {"text": f"{ICON} !", "tooltip": f"CoolPilot: {error or 'not running'}", "class": "error"}
    text = f"{ICON} {_temp(s.get('cpu_temp')).replace('C', '')}"
    if s.get("new_crash"):
        text = f"⚠ {text}"
    tip = "\n".join(describe(s) + ["", "Left-click: open · Right-click: Stability on/off · Middle-click: next mode"])
    classes = ["active"] if s.get("stability") else []
    if s.get("new_crash"):
        classes.append("crash")
    return {"text": text, "tooltip": tip, "class": classes or "normal",
            "alt": "stability" if s.get("stability") else (s.get("profile") or "")}


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    what, args = argv[0], argv[1:]

    if what == "bar":
        try:
            s = api("GET", "/status?crash=1")
            print(json.dumps(bar_json(s)))
        except CliError as e:
            print(json.dumps(bar_json(None, str(e))))
        return 0

    if what == "status":
        s = api("GET", "/status?crash=1")
        print(json.dumps(s, indent=2) if "--json" in args else "\n".join(describe(s)))
        return 0

    if what == "stability":
        want = (args or ["toggle"])[0]
        if want not in ("on", "off", "toggle"):
            raise CliError("usage: coolpilot stability on|off|toggle")
        on = (not api("GET", "/status")["stability"]) if want == "toggle" else want == "on"
        r = api("POST", "/stability", {"enabled": on})
        _notify(f"Stability mode {'on' if on else 'off'}", _problems(r.get("results")))
        return 0

    if what == "mode":
        if not args:
            raise CliError("usage: coolpilot mode silent|balanced|turbo|next")
        s = api("GET", "/status")
        choices = s.get("profiles") or []
        if args[0] == "next":
            cycle = [c for c in ("quiet", "low-power", "balanced", "performance") if c in choices]
            cur = s.get("profile")
            target = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else (cycle or [None])[0]
        else:
            target = MODE_ALIASES.get(args[0], args[0])
            if target == "quiet" and "quiet" not in choices and "low-power" in choices:
                target = "low-power"
        if not target:
            raise CliError("this laptop has no performance modes")
        api("POST", "/settings/platform_profile", {"value": target})
        _notify(f"Performance mode: {MODE_NAMES.get(target, target)}")
        return 0

    if what == "fans":
        if not args or args[0] not in ("default", "stability"):
            raise CliError("usage: coolpilot fans default|stability")
        r = api("POST", "/fans/mode", {"mode": args[0]})
        _notify(f"Fans: {args[0]}", _problems(r.get("results")))
        return 0

    if what == "gaming":
        what, args = "profile", ["Gaming"]

    if what == "profile":
        profs = api("GET", "/profiles")["profiles"]
        if not args:
            for p in profs:
                print(f"{'*' if p['active'] else ' '} {p['name']}{'  (at boot)' if p['boot'] else ''}")
            return 0
        name = " ".join(args)
        match = next((p["name"] for p in profs if p["name"].lower() == name.lower()), None)
        if not match:
            raise CliError(f"no profile called '{name}' (see: coolpilot profile)")
        r = api("POST", f"/profiles/{urllib.parse.quote(match)}/apply")
        _notify(f"Profile applied: {match}", _problems(r.get("results")))
        return 0

    if what == "charge":
        if not args:
            raise CliError("usage: coolpilot charge full|cancel|<20-100>")
        if args[0] == "full":
            r = api("POST", "/battery/full-once", {"enabled": True})
            _notify(f"Charging to 100% once — back to {r.get('back_to')}% when full")
        elif args[0] == "cancel":
            api("POST", "/battery/full-once", {"enabled": False})
            _notify("Charge-to-100% cancelled")
        elif args[0].isdigit():
            api("POST", "/settings/charge_limit", {"value": int(args[0])})
            _notify(f"Charge limit: {int(args[0])}%")
        else:
            raise CliError("usage: coolpilot charge full|cancel|<20-100>")
        return 0

    if what == "open":
        subprocess.Popen(["xdg-open", f"{BASE}/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    raise CliError(f"unknown command '{what}' (see: coolpilot help)")


def _notify(msg: str, problems: list[str] | None = None) -> None:
    """Print, and show a desktop notification when run from a bar click (no terminal)."""
    text = msg + (f" (problems: {'; '.join(problems)})" if problems else "")
    print(text)
    if not sys.stdout.isatty() and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        try:
            subprocess.Popen(["notify-send", "-a", "CoolPilot", "CoolPilot", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def main() -> int:
    try:
        return cmd(sys.argv[1:])
    except CliError as e:
        print(f"coolpilot: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
