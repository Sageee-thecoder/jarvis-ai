"""
WiZ smart-light control for J.A.R.V.I.S. (pywizlight).

Lights are declared in .env as a comma-separated list. Either "Name=IP" pairs (recommended, so you can
say "turn off the bedroom") or bare IP addresses:

    WIZ_LIGHTS=Living Room=192.168.1.20, Bedroom=192.168.1.21, Desk=192.168.1.22

One light can be a fixture of several bulbs that always act together. Join their IPs with | (or repeat the
name), and Jarvis treats them as a single light:

    WIZ_LIGHTS=Main Light=192.168.1.23|192.168.1.24|192.168.1.25|192.168.1.26

pywizlight is asyncio based, so every public function here is synchronous and runs its own short event loop.
"""

import re
import os
import asyncio
import threading
import time

from dotenv import load_dotenv

load_dotenv()

try:
    from pywizlight import wizlight, PilotBuilder
except ImportError:  # the rest of Jarvis keeps working if the library is not installed
    wizlight = None
    PilotBuilder = None

try:
    from pywizlight.scenes import SCENES as _LIB_SCENES
except Exception:  # noqa: BLE001
    _LIB_SCENES = None

FALLBACK_SCENES = {
    1: "Ocean", 2: "Romance", 3: "Sunset", 4: "Party", 5: "Fireplace", 6: "Cozy", 7: "Forest",
    8: "Pastel colors", 9: "Wake up", 10: "Bedtime", 11: "Warm white", 12: "Daylight", 13: "Cool white",
    14: "Night light", 15: "Focus", 16: "Relax", 17: "True colors", 18: "TV time", 19: "Plantgrowth",
    20: "Spring", 21: "Summer", 22: "Fall", 23: "Deepdive", 24: "Jungle", 25: "Mojito", 26: "Club",
    27: "Christmas", 28: "Halloween", 29: "Candlelight", 30: "Golden white", 31: "Pulse", 32: "Steampunk",
    33: "Diwali", 34: "White", 35: "Alarm",
}


def _load_scene_names():
    """Scene id -> name, taken from the library when possible."""
    try:
        if isinstance(_LIB_SCENES, dict) and _LIB_SCENES:
            first_key = next(iter(_LIB_SCENES))
            if isinstance(first_key, str):  # name -> id
                return {int(v): str(k) for k, v in _LIB_SCENES.items()}
            return {int(k): str(v) for k, v in _LIB_SCENES.items()}
    except Exception:  # noqa: BLE001
        pass
    return dict(FALLBACK_SCENES)


SCENE_NAMES = _load_scene_names()

WIZ_TIMEOUT = float(os.getenv("WIZ_TIMEOUT", "4"))
MIN_BRIGHTNESS_PCT = 10  # WiZ bulbs cannot dim below roughly 10 percent

COLOR_RGB = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 200, 0),
    "orange": (255, 110, 0), "purple": (140, 0, 255), "violet": (180, 0, 255), "pink": (255, 60, 140),
    "magenta": (255, 0, 255), "cyan": (0, 255, 255), "teal": (0, 200, 170), "lime": (120, 255, 0),
    "turquoise": (0, 220, 200), "indigo": (75, 0, 200), "gold": (255, 170, 0), "amber": (255, 120, 0),
}
COLOR_KELVIN = {
    "white": 4000, "neutral white": 4000, "warm white": 2700, "warm": 2700, "soft white": 2700,
    "cool white": 6000, "cool": 6000, "cold white": 6500, "daylight": 5500,
}

_snapshot = {"t": 0.0, "data": []}
_snapshot_lock = threading.Lock()


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def parse_lights(raw):
    """Entries with the same name, or several IPs joined with | or +, form ONE light (a group of bulbs)."""
    groups = []
    by_name = {}
    for chunk in re.split(r"[;,]", raw or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            name, value = [part.strip() for part in chunk.split("=", 1)]
        else:
            name, value = "", chunk
        addresses = [a.strip() for a in re.split(r"[|+]", value) if a.strip()]
        addresses = [a for a in addresses if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", a)]
        if not addresses:
            continue
        if not name:
            name = f"Light {len(groups) + 1}"
        key = name.lower()
        if key in by_name:
            group = by_name[key]
            for address in addresses:
                if address not in group["ips"]:
                    group["ips"].append(address)
        else:
            group = {"name": name, "ips": list(dict.fromkeys(addresses))}
            by_name[key] = group
            groups.append(group)
    for group in groups:
        group["ip"] = group["ips"][0]
    return groups


LIGHTS = parse_lights(os.getenv("WIZ_LIGHTS", ""))


def available():
    return bool(LIGHTS) and wizlight is not None


def unavailable_reason():
    if wizlight is None:
        return "The pywizlight library is not installed. Run: pip install pywizlight"
    if not LIGHTS:
        return "No lights configured. Add WIZ_LIGHTS to .env, for example: WIZ_LIGHTS=Living Room=192.168.1.20, Bedroom=192.168.1.21 (join several bulbs of one fixture with |)"
    return ""


def light_names():
    return [light["name"] for light in LIGHTS]


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def resolve_targets(target):
    wanted = (target or "all").strip().lower()
    everything = {"", "all", "everything", "all lights", "every light", "everywhere", "the house", "house", "whole house"}
    if wanted in everything:
        return list(LIGHTS)
    parts = [p.strip() for p in re.split(r",| and ", wanted) if p.strip()]
    matched = []
    for part in parts:
        exact = [l for l in LIGHTS if l["name"].lower() == part or part in l["ips"]]
        found = exact or [l for l in LIGHTS if part in l["name"].lower() or l["name"].lower() in part]
        for light in found:
            if light not in matched:
                matched.append(light)
    if not matched and len(LIGHTS) == 1:
        return list(LIGHTS)  # "the light" when there is only one
    return matched


def resolve_scene(value):
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.isdigit() and int(text) in SCENE_NAMES:
        return int(text)
    for scene_id, name in SCENE_NAMES.items():
        if name.lower() == text:
            return scene_id
    for scene_id, name in SCENE_NAMES.items():
        if text in name.lower() or name.lower() in text:
            return scene_id
    return None


def parse_color(value):
    """Returns ('rgb', (r, g, b)), ('kelvin', k) or None."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in COLOR_KELVIN:
        return ("kelvin", COLOR_KELVIN[text])
    if text in COLOR_RGB:
        return ("rgb", COLOR_RGB[text])
    hex_match = re.fullmatch(r"#?([0-9a-f]{6})", text)
    if hex_match:
        h = hex_match.group(1)
        return ("rgb", (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    triple = re.fullmatch(r"(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})", text)
    if triple:
        r, g, b = (max(0, min(255, int(x))) for x in triple.groups())
        return ("rgb", (r, g, b))
    return None


def build_pilot(brightness_percent=-1, color="", kelvin=0, scene=""):
    """Builds a PilotBuilder from friendly values. Returns None when nothing was requested."""
    settings = {}
    if scene and str(scene).strip():
        scene_id = resolve_scene(scene)
        if scene_id is None:
            raise ValueError(f"Unknown scene '{scene}'. Examples: Cozy, Relax, Focus, Party, Candlelight, TV time, Daylight, Night light.")
        settings["scene"] = scene_id
    elif color and str(color).strip():
        parsed = parse_color(color)
        if parsed is None:
            raise ValueError(f"I don't know the color '{color}'. Use a common color name, a hex code like #ff8800, or r,g,b.")
        kind, value = parsed
        if kind == "rgb":
            settings["rgb"] = value
        else:
            settings["colortemp"] = value
    elif kelvin and int(kelvin) > 0:
        settings["colortemp"] = max(2200, min(6500, int(kelvin)))

    if brightness_percent is not None and int(brightness_percent) >= 0:
        pct = max(MIN_BRIGHTNESS_PCT, min(100, int(brightness_percent)))
        settings["brightness"] = round(pct * 255 / 100)

    return PilotBuilder(**settings) if settings else None


# --------------------------------------------------------------------------
# Async work
# --------------------------------------------------------------------------
async def _close(bulb):
    closer = getattr(bulb, "async_close", None)
    if closer:
        try:
            await closer()
        except Exception:  # noqa: BLE001
            pass


def _error_text(exc):
    if isinstance(exc, asyncio.TimeoutError):
        return "no response (is the bulb powered, and on the same network as this PC?)"
    return (str(exc) or type(exc).__name__)[:120]


async def _bulb_set(ip, action, pilot):
    bulb = wizlight(ip)
    try:
        if action == "off":
            await asyncio.wait_for(bulb.turn_off(), WIZ_TIMEOUT)
        else:
            await asyncio.wait_for(bulb.turn_on(pilot if pilot is not None else PilotBuilder()), WIZ_TIMEOUT)
        return {"ip": ip, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ip": ip, "ok": False, "error": _error_text(exc)}
    finally:
        await _close(bulb)


async def _bulb_status(ip):
    bulb = wizlight(ip)
    try:
        state = await asyncio.wait_for(bulb.updateState(), WIZ_TIMEOUT)
        if state is None:
            return {"ip": ip, "reachable": False, "error": "no state returned"}
        brightness = state.get_brightness()
        rgb = state.get_rgb()
        return {
            "ip": ip,
            "reachable": True,
            "on": bool(state.get_state()),
            "brightness": round(brightness * 100 / 255) if brightness is not None else None,
            "kelvin": state.get_colortemp(),
            "rgb": list(rgb) if rgb else None,
            "scene": state.get_scene(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ip": ip, "reachable": False, "error": _error_text(exc)}
    finally:
        await _close(bulb)


async def _apply_one(light, action, pilot):
    """Applies the change to every bulb of one light (a light may be a group of bulbs)."""
    ips = light["ips"]
    if action == "toggle":
        states = await asyncio.gather(*[_bulb_status(ip) for ip in ips])
        any_on = any(item["reachable"] and item["on"] for item in states)
        action = "off" if any_on else "on"
    results = await asyncio.gather(*[_bulb_set(ip, action, pilot) for ip in ips])
    failed = [item for item in results if not item["ok"]]
    if not failed:
        return {"name": light["name"], "ok": True}
    if len(failed) == len(results):
        return {"name": light["name"], "ok": False, "error": failed[0]["error"]}
    answered = len(results) - len(failed)
    return {
        "name": light["name"],
        "ok": True,
        "note": f"only {answered} of {len(results)} bulbs responded; not responding: {', '.join(item['ip'] for item in failed)}",
    }


async def _status_one(light):
    bulbs = await asyncio.gather(*[_bulb_status(ip) for ip in light["ips"]])
    live = [item for item in bulbs if item["reachable"]]
    info = {
        "name": light["name"],
        "ip": light["ip"],
        "ips": light["ips"],
        "bulbs_total": len(bulbs),
        "bulbs_reachable": len(live),
    }
    if not live:
        info.update({"reachable": False, "error": bulbs[0].get("error", "no response")})
        return info
    lit = [item for item in live if item["on"]]
    reference = lit[0] if lit else live[0]
    levels = [item["brightness"] for item in lit if item.get("brightness") is not None]
    info.update(
        {
            "reachable": True,
            "on": bool(lit),
            "brightness": round(sum(levels) / len(levels)) if levels else None,
            "kelvin": reference.get("kelvin"),
            "rgb": reference.get("rgb"),
            "scene": reference.get("scene"),
            "unreachable_ips": [item["ip"] for item in bulbs if not item["reachable"]],
        }
    )
    return info


async def _gather_apply(targets, action, pilot):
    return await asyncio.gather(*[_apply_one(light, action, pilot) for light in targets])


async def _gather_status(targets):
    return await asyncio.gather(*[_status_one(light) for light in targets])


# --------------------------------------------------------------------------
# Public API (synchronous)
# --------------------------------------------------------------------------
def control(target="all", action="on", brightness_percent=-1, color="", kelvin=0, scene=""):
    """Returns (all_ok, summary_text). Raises ValueError / RuntimeError for bad input or missing setup."""
    if not available():
        raise RuntimeError(unavailable_reason())

    action = (action or "on").strip().lower()
    aliases = {"set": "on", "turn on": "on", "switch on": "on", "turn off": "off", "switch off": "off"}
    action = aliases.get(action, action)
    if action not in ("on", "off", "toggle"):
        raise ValueError("action must be on, off or toggle")

    targets = resolve_targets(target)
    if not targets:
        raise ValueError(f"No light matches '{target}'. Available lights: {', '.join(light_names())}")

    if action == "on" and brightness_percent is not None and int(brightness_percent) == 0:
        action = "off"
    pilot = None if action == "off" else build_pilot(brightness_percent, color, kelvin, scene)

    results = asyncio.run(_gather_apply(targets, action, pilot))
    with _snapshot_lock:
        _snapshot["t"] = 0.0  # force a fresh read next time

    done = [f"{r['name']} ({r['note']})" if r.get("note") else r["name"] for r in results if r["ok"]]
    failed = [f"{r['name']} ({r['error']})" for r in results if not r["ok"]]
    parts = []
    if done:
        parts.append(f"{action}: {', '.join(done)}")
    if failed:
        parts.append("failed: " + "; ".join(failed))
    return (not failed, " | ".join(parts))


def status(target="all"):
    """Returns a list of state dicts for the matching lights."""
    if not available():
        raise RuntimeError(unavailable_reason())
    targets = resolve_targets(target)
    if not targets:
        raise ValueError(f"No light matches '{target}'. Available lights: {', '.join(light_names())}")
    return list(asyncio.run(_gather_status(targets)))


def status_text(target="all"):
    lines = []
    for item in status(target):
        if not item["reachable"]:
            lines.append(f"{item['name']}: unreachable ({item.get('error', 'unknown error')})")
            continue
        note = ""
        if item["bulbs_total"] > 1 and item["bulbs_reachable"] < item["bulbs_total"]:
            missing = ", ".join(item.get("unreachable_ips", []))
            note = f" [only {item['bulbs_reachable']} of {item['bulbs_total']} bulbs responding; not responding: {missing}]"
        elif item["bulbs_total"] > 1:
            note = f" [{item['bulbs_total']} bulbs]"
        if not item["on"]:
            lines.append(f"{item['name']}: off{note}")
            continue
        bits = ["on"]
        if item.get("brightness") is not None:
            bits.append(f"{item['brightness']}% brightness")
        if item.get("scene"):
            bits.append(f"scene {item['scene']}")
        elif item.get("rgb") and any(item["rgb"]):
            bits.append(f"color rgb{tuple(item['rgb'])}")
        elif item.get("kelvin"):
            bits.append(f"{item['kelvin']} K white")
        lines.append(f"{item['name']}: " + ", ".join(bits) + note)
    return "\n".join(lines)


def snapshot(max_age=5.0):
    """Cached status of every light, for the HUD panel."""
    if not available():
        return []
    with _snapshot_lock:
        if time.time() - _snapshot["t"] < max_age and _snapshot["data"]:
            return list(_snapshot["data"])
    data = status("all")
    with _snapshot_lock:
        _snapshot["t"] = time.time()
        _snapshot["data"] = data
    return list(data)
