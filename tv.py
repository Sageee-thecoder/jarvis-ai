"""
Samsung smart-TV control for J.A.R.V.I.S. (samsungtvws, local WebSocket API).

The TV is paired once: the first secure connection (wss, port 8002) shows an Allow prompt on
screen. The granted token is kept in .samsung_token.json next to this file (gitignored), so later
connections are silent.

.env:
    SAMSUNG_TV_HOST=192.168.1.60
    SAMSUNG_TV_MAC=AA:BB:CC:DD:EE:FF   (optional; needed to wake the TV from standby)

Power state is read from the TV's REST endpoint (port 8001), which answers even in standby.
Turning the TV on sends a wake-on-LAN packet first, then waits for the TV to report on.
"""

import json
import os
import socket
import threading
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv()

try:
    from samsungtvws import SamsungTVWS
except ImportError:  # keep the rest of Jarvis running without the library
    SamsungTVWS = None

HOST = os.getenv("SAMSUNG_TV_HOST", "").strip()
MAC = os.getenv("SAMSUNG_TV_MAC", "").strip().replace("-", ":").upper()
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".samsung_token.json")
NAME = "Jarvis"
PORT = 8002
WAKE_SECONDS = 25

_LOCK = threading.Lock()
_INFO_CACHE = {"at": 0.0, "info": {}}
_APP_CACHE = {"at": 0.0, "apps": []}

# Installed-app listing (ed.installedApp.get) is unreliable on 2025 models, so well-known
# streaming apps launch by their Tizen app id.
KNOWN_APPS = {
    "YouTube": "111299001912",
    "Netflix": "3201907018807",
    "Spotify": "3201606009684",
    "Disney+": "3201901017640",
    "Prime Video": "3201910019365",
    "Apple TV": "3201807016597",
    "Plex": "3201512006963",
    "Twitch": "3202203026841",
    "HBO Max": "3201601007230",
    "Samsung TV Plus": "3201512006785",
}

APP_ALIASES = {
    "amazonprime": "prime video",
    "prime": "prime video",
    "disneyplus": "disney+",
    "disney": "disney+",
    "appletv": "apple tv",
    "hbomax": "hbo max",
    "hbo": "hbo max",
    "samsungtvplus": "samsung tv plus",
}


def _norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _resolve_known(query):
    wanted = APP_ALIASES.get(_norm(query), str(query).strip().lower())
    for label, app_id in KNOWN_APPS.items():
        if wanted == label.lower() or _norm(wanted) == _norm(label):
            return label, app_id
    return None, None

KEYS = {
    "off": "KEY_POWER",
    "power": "KEY_POWER",
    "play": "KEY_PLAY",
    "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
    "rewind": "KEY_REWIND",
    "fast_forward": "KEY_FF",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "enter": "KEY_ENTER",
    "ok": "KEY_ENTER",
    "select": "KEY_ENTER",
    "back": "KEY_RETURN",
    "return": "KEY_RETURN",
    "home": "KEY_HOME",
    "exit": "KEY_EXIT",
    "menu": "KEY_MENU",
    "source": "KEY_SOURCE",
    "guide": "KEY_GUIDE",
    "info": "KEY_INFO",
    "tools": "KEY_TOOLS",
    "channel_list": "KEY_CH_LIST",
    "mute": "KEY_MUTE",
    "channel_up": "KEY_CHUP",
    "channel_down": "KEY_CHDOWN",
    "volume_up": "KEY_VOLUP",
    "volume_down": "KEY_VOLDOWN",
    "red": "KEY_RED",
    "green": "KEY_GREEN",
    "yellow": "KEY_YELLOW",
    "blue": "KEY_BLUE",
    "0": "KEY_0",
    "1": "KEY_1",
    "2": "KEY_2",
    "3": "KEY_3",
    "4": "KEY_4",
    "5": "KEY_5",
    "6": "KEY_6",
    "7": "KEY_7",
    "8": "KEY_8",
    "9": "KEY_9",
}


def available():
    return bool(HOST) and SamsungTVWS is not None


def unavailable_reason():
    if SamsungTVWS is None:
        return "The samsungtvws package is not installed (pip install samsungtvws)."
    if not HOST:
        return "No Samsung TV configured. Add SAMSUNG_TV_HOST to .env."
    return ""


def _tv(timeout=8.0):
    return SamsungTVWS(
        HOST,
        token_file=TOKEN_FILE,
        port=PORT,
        name=NAME,
        timeout=timeout,
        key_press_delay=0.15,
    )


def _device_info(refresh=False):
    now = time.time()
    if not refresh and _INFO_CACHE["info"] and now - _INFO_CACHE["at"] < 30:
        return _INFO_CACHE["info"]
    try:
        with urllib.request.urlopen(f"http://{HOST}:8001/api/v2/", timeout=3) as resp:
            info = json.load(resp)
        _INFO_CACHE["info"] = info
        _INFO_CACHE["at"] = now
        return info
    except Exception:  # noqa: BLE001
        return {}


def power_state():
    device = _device_info(refresh=True).get("device") or {}
    return str(device.get("PowerState") or "").lower()


def _mac():
    if MAC:
        return MAC
    device = _device_info().get("device") or {}
    mac = device.get("wifiMac") or device.get("mac") or ""
    return str(mac).replace("-", ":").upper()


def wake():
    mac = _mac()
    if not mac:
        return False, "I do not know the TV's MAC address, so I cannot wake it. Add SAMSUNG_TV_MAC to .env."
    try:
        payload = bytes.fromhex(mac.replace(":", ""))
    except ValueError:
        return False, "The TV MAC address in .env is not valid."
    packet = b"\xff" * 6 + payload * 16
    targets = ["255.255.255.255"]
    parts = HOST.split(".")
    if len(parts) == 4:
        targets.append(".".join(parts[:3]) + ".255")
    for _ in range(5):
        for target in targets:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.sendto(packet, (target, 9))
            finally:
                sock.close()
        time.sleep(0.6)
    deadline = time.time() + WAKE_SECONDS
    while time.time() < deadline:
        if power_state() == "on":
            return True, "TV is on."
        time.sleep(1.5)
    return False, "I sent the wake signal, but the TV did not come on. Check that Network Standby is enabled on it."


def _connect():
    tv = _tv()
    tv.open()
    return tv


def _close(tv):
    try:
        tv.close()
    except Exception:  # noqa: BLE001
        pass


def _send_keys(tv, key, times=1):
    times = max(1, min(int(times), 20))
    tv.send_key(key, times=times, key_press_delay=0.15)


def _app_ids(tv):
    listed = tv.app_list() or []
    apps = []
    for item in listed:
        name = str(item.get("name") or "").strip()
        app_id = item.get("appId") or item.get("app_id") or item.get("id")
        if name and app_id:
            apps.append({"name": name, "id": str(app_id)})
    return apps


def _installed_apps():
    """Installed apps via the event API; empty on TVs that no longer answer it."""
    if time.time() - _APP_CACHE["at"] < (300 if _APP_CACHE["apps"] else 900):
        return _APP_CACHE["apps"]
    tv = _tv()
    try:
        tv.start_listening()
        apps = _app_ids(tv)
    except Exception:  # noqa: BLE001
        apps = []
    finally:
        _close(tv)
    _APP_CACHE["apps"] = apps
    _APP_CACHE["at"] = time.time()
    return apps


def _resolve_app(query):
    label, app_id = _resolve_known(query)
    if app_id:
        return label, app_id
    wanted = str(query).strip().lower()
    for app in _installed_apps():
        if app["name"].lower() == wanted or _norm(app["name"]) == _norm(wanted):
            return app["name"], app["id"]
    return None, None


def apps_text():
    if not available():
        return "Error: " + unavailable_reason()
    names = ", ".join(sorted(KNOWN_APPS))
    installed = _installed_apps()
    if installed:
        extra = sorted({a["name"] for a in installed} - set(KNOWN_APPS), key=str.lower)
        if extra:
            names += "; also installed: " + ", ".join(extra)
    return "TV apps I can open: " + names


def control(action, value=""):
    """Return (ok, text). Raises nothing; every failure comes back as ok=False."""
    if not available():
        return False, unavailable_reason()
    act = str(action or "").strip().lower().replace(" ", "_").replace("-", "_")
    val = str(value or "").strip()
    if act in ("on", "wake", "power_on", "turn_on"):
        with _LOCK:
            return wake()
    if not act:
        return False, "Tell me what to do with the TV (on, off, volume, channel, app, play, pause, home...)."

    with _LOCK:
        tv = None
        try:
            tv = _connect()
            if act in ("volume_up", "volume_down"):
                steps = int(val) if val.isdigit() else 1
                _send_keys(tv, KEYS[act], steps)
                return True, f"TV volume {'up' if act == 'volume_up' else 'down'} ({max(1, min(steps, 20))} step{'s' if steps > 1 else ''})."
            if act == "channel" or act == "set_channel":
                digits = "".join(ch for ch in val if ch.isdigit())
                if not digits:
                    return False, "Give me the channel number."
                for ch in digits:
                    _send_keys(tv, "KEY_" + ch)
                _send_keys(tv, "KEY_ENTER")
                return True, f"TV switched to channel {digits}."
            if act in ("app", "open_app", "launch"):
                if not val:
                    return False, "Tell me which app to open (for example YouTube, Netflix or Spotify)."
                label, app_id = _resolve_app(val)
                if not app_id:
                    known = ", ".join(sorted(KNOWN_APPS))
                    return False, f"No app matches '{val}'. I can open: {known}"
                tv.run_app(app_id)
                return True, f"Opened {label} on the TV."
            if act in ("key", "send_key"):
                key = val.upper()
                if not key:
                    return False, "Tell me which remote key to press."
                if not key.startswith("KEY_"):
                    key = KEYS.get(val.lower()) or ("KEY_" + key)
                _send_keys(tv, key)
                return True, f"Pressed {key} on the TV."
            key = KEYS.get(act)
            if not key:
                return False, f"Unsupported TV action '{action}'."
            _send_keys(tv, key)
            labels = {
                "off": "TV turned off.",
                "mute": "TV mute toggled.",
                "play": "Play.", "pause": "Paused.", "stop": "Stopped.",
                "channel_up": "Next channel.", "channel_down": "Previous channel.",
            }
            return True, labels.get(act, f"Pressed {act.replace('_', ' ')} on the TV.")
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "Unauthorized" in name:
                return False, "The TV rejected the connection. Re-pair it (delete .samsung_token.json and accept the prompt on the TV)."
            if "timeout" in name.lower() or "Timeout" in name:
                return False, "The TV did not answer. Is it on and on the same Wi-Fi?"
            return False, f"TV control failed ({name})."
        finally:
            if tv is not None:
                _close(tv)


def status_text():
    if not available():
        return "Error: " + unavailable_reason()
    info = _device_info(refresh=True)
    device = info.get("device") or {}
    if not device:
        return f"The Samsung TV at {HOST} is unreachable on the network."
    state = str(device.get("PowerState") or "").lower()
    name = device.get("name") or "Samsung TV"
    model = device.get("modelName") or ""
    where = f" ({model})" if model else ""
    if state == "on":
        return f"The TV is on{where}."
    if state in ("standby", "off"):
        return f"The TV is in standby{where}; turn it on and it wakes from stand-by."
    return f"The TV state is '{state or 'unknown'}'{where}."


def snapshot():
    info = _device_info(refresh=True)
    device = info.get("device") or {}
    state = str(device.get("PowerState") or "").lower()
    return {
        "host": HOST,
        "name": device.get("name") or "Samsung TV",
        "model": device.get("modelName") or "",
        "mac": _mac(),
        "paired": os.path.exists(TOKEN_FILE),
        "reachable": bool(device),
        "state": state,
        "on": state == "on",
    }
