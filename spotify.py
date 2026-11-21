"""
Spotify control for Jarvis.

Authorization Code flow against the Spotify Web API. The HUD sends the owner to
/api/spotify/login, Spotify redirects back to /api/spotify/callback, and the refresh
token is stored in .spotify_token.json (keep it private).

Playback commands need a Premium account and an available Spotify Connect device.
"""

import base64
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "").strip() or (
    f"http://127.0.0.1:{os.getenv('PORT', '5000')}/api/spotify/callback"
)
MARKET = os.getenv("SPOTIFY_MARKET", "").strip()
TOKEN_FILE = Path(os.getenv("SPOTIFY_TOKEN_FILE", "").strip() or (BASE_DIR / ".spotify_token.json"))

SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "playlist-read-private playlist-read-collaborative user-library-read user-read-private"
)
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"

GENRE_HINTS = (
    "jazz", "lofi", "lo-fi", "chill", "classical", "rock", "pop", "hip hop", "hip-hop",
    "rap", "electronic", "house", "techno", "focus", "study", "sleep", "workout", "party",
    "ambient", "piano", "acoustic", "indie", "metal", "country", "blues", "soul", "funk",
    "music", "mix", "playlist", "tanbur", "turkish", "türkçe",
)

LOCK = threading.RLock()
HTTP = requests.Session()
_AUTH = {"state": "", "at": 0.0}
_CACHE = {"t": 0.0, "data": None, "error": ""}
_PROFILE = {"id": "", "name": ""}


def _load_token():
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


TOKEN = _load_token()


# ------------------------------------------------------------------------
# Configuration and auth
# ------------------------------------------------------------------------
def available():
    return bool(CLIENT_ID and CLIENT_SECRET)


def ready():
    return available() and bool(TOKEN.get("refresh_token") or TOKEN.get("access_token"))


def unavailable_reason():
    if not available():
        return "Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env, then restart the server."
    if not ready():
        return "Spotify is configured but not connected. Click Connect Spotify in the HUD."
    return ""


def _basic():
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _save_token():
    try:
        TOKEN_FILE.write_text(json.dumps(TOKEN, indent=1), encoding="utf-8")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def _forget_token():
    TOKEN.clear()
    try:
        TOKEN_FILE.unlink()
    except OSError:
        pass
    _CACHE.update(t=0.0, data=None, error="")


def disconnect():
    with LOCK:
        _forget_token()
    return "Spotify disconnected."


def auth_url():
    if not available():
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are missing in .env.")
    state = secrets.token_urlsafe(18)
    _AUTH.update(state=state, at=time.time())
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "false",
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


def exchange(code, state):
    if not available():
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are missing in .env.")
    code = str(code or "").strip()
    if not code:
        raise RuntimeError("Spotify did not return an authorization code.")
    if not _AUTH["state"] or state != _AUTH["state"] or time.time() - _AUTH["at"] > 900:
        raise RuntimeError("The Spotify login expired or the state did not match. Start the connection again.")
    resp = HTTP.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        headers={"Authorization": "Basic " + _basic()},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Spotify rejected the login ({resp.status_code}): {resp.text[:200]}")
    payload = resp.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not access or not refresh:
        raise RuntimeError("Spotify returned an incomplete token response.")
    with LOCK:
        TOKEN.update(
            {
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": time.time() + int(payload.get("expires_in", 3600)) - 60,
                "scope": payload.get("scope", SCOPES),
                "connected_at": time.time(),
            }
        )
        _save_token()
        _AUTH.update(state="", at=0.0)
        _PROFILE.update(id="", name="")
        _CACHE.update(t=0.0, data=None, error="")
    return "Spotify connected."


def _token(force=False):
    with LOCK:
        now = time.time()
        if not force and TOKEN.get("access_token") and TOKEN.get("expires_at", 0) > now + 30:
            return TOKEN["access_token"]
        refresh = TOKEN.get("refresh_token")
        if not refresh:
            raise RuntimeError("Spotify is not connected. Click Connect Spotify in the HUD.")
        resp = HTTP.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            headers={"Authorization": "Basic " + _basic()},
            timeout=20,
        )
        if resp.status_code >= 400:
            if resp.status_code == 400 and "invalid_grant" in resp.text:
                _forget_token()
                raise RuntimeError("Spotify authorization expired. Click Connect Spotify in the HUD again.")
            raise RuntimeError(f"Spotify token refresh failed ({resp.status_code}): {resp.text[:160]}")
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            raise RuntimeError("Spotify did not return an access token.")
        TOKEN["access_token"] = access
        TOKEN["expires_at"] = now + int(payload.get("expires_in", 3600)) - 60
        if payload.get("refresh_token"):
            TOKEN["refresh_token"] = payload["refresh_token"]
        if payload.get("scope"):
            TOKEN["scope"] = payload["scope"]
        _save_token()
        return access


# ------------------------------------------------------------------------
# Web API plumbing
# ------------------------------------------------------------------------
def _error_text(resp):
    message = ""
    try:
        body = resp.json()
        detail = body.get("error") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            message = detail.get("message") or ""
        elif detail:
            message = str(detail)
    except ValueError:
        message = ""
    message = message or (resp.text or "")[:200]
    low = message.lower()
    if resp.status_code == 403 and "premium" in low:
        return (
            "Spotify refused: playback control needs Spotify Premium. "
            "Also make sure this account is added under User Management in your Spotify app settings."
        )
    if resp.status_code == 403 and ("not registered" in low or "user may not" in low):
        return (
            "Spotify refused this account. In the Spotify developer dashboard open your app, "
            "then Settings > User Management and add the account you are connecting."
        )
    if resp.status_code == 404 and ("device" in low or "active device" in low):
        return "No active Spotify device. Open Spotify on your phone or computer, then try again."
    if resp.status_code == 429:
        return "Spotify is rate limiting requests. Wait a moment and try again."
    if resp.status_code == 401:
        return "Spotify rejected the access token. Reconnect the account in the HUD."
    return f"Spotify error {resp.status_code}: {message}"


def _api(method, path, params=None, body=None, retry=True):
    token = _token()
    if method != "GET":
        with LOCK:
            _CACHE["t"] = 0.0
    resp = HTTP.request(
        method,
        API + path,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        params=params,
        json=body,
        timeout=20,
    )
    if resp.status_code == 401 and retry:
        _token(force=True)
        return _api(method, path, params, body, retry=False)
    if resp.status_code in (202, 204):
        return None
    if resp.status_code >= 400:
        raise RuntimeError(_error_text(resp))
    if not resp.text:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _market():
    return MARKET or "from_token"


def _devices():
    data = _api("GET", "/me/player/devices") or {}
    return [d for d in data.get("devices", []) if d and d.get("id")]


def _device_id(name=""):
    devices = _devices()
    if not devices:
        raise RuntimeError(
            "No Spotify device is available. Open Spotify on your phone or computer, start a song once, then try again."
        )
    wanted = " ".join(str(name or "").split()).lower()
    if wanted:
        matches = [
            d for d in devices
            if wanted in (d.get("name") or "").lower() or wanted in (d.get("type") or "").lower()
        ]
        if not matches:
            names = ", ".join(d.get("name", "?") for d in devices)
            raise RuntimeError(f"No Spotify device matches \"{name}\". Available: {names}.")
        chosen = matches[0]
    else:
        chosen = next((d for d in devices if d.get("is_active")), devices[0])
    return chosen["id"], chosen


def _search(query):
    return _api(
        "GET",
        "/search",
        params={"q": query, "type": "track,artist,album,playlist", "limit": 5, "market": _market()},
    ) or {}


def _first(data, key):
    items = (data.get(key) or {}).get("items") or []
    return next((item for item in items if item), None)


def _clean_query(text):
    cleaned = re.sub(r'["\u201c\u201d]', " ", str(text).lower())
    cleaned = re.sub(r"\b(play|please|my|the|some|on spotify|spotify|playlist)\b", " ", cleaned)
    return " ".join(cleaned.split())


def _my_playlist(query):
    cleaned = _clean_query(query)
    data = _api("GET", "/me/playlists", params={"limit": 50}) or {}
    items = [p for p in data.get("items", []) if p and p.get("uri")]
    if cleaned:
        exact = [p for p in items if cleaned == (p.get("name") or "").lower()]
        if exact:
            return exact[0]
        partial = [p for p in items if cleaned and cleaned in (p.get("name") or "").lower()]
        if partial:
            return partial[0]
    return None


def _liked_context():
    with LOCK:
        uid = _PROFILE.get("id")
    if not uid:
        profile = _api("GET", "/me") or {}
        uid = profile.get("id") or ""
        with LOCK:
            _PROFILE.update(id=uid, name=profile.get("display_name") or uid)
    if not uid:
        raise RuntimeError("Spotify would not tell me your user id, so liked songs are unavailable.")
    return f"spotify:user:{uid}:collection"


def _start(device_id, body):
    try:
        _api("PUT", "/me/player/play", params={"device_id": device_id}, body=body)
    except RuntimeError as exc:
        low = str(exc).lower()
        if "device" in low and ("not found" in low or "not active" in low):
            _api("PUT", "/me/player", body={"device_ids": [device_id], "play": False})
            _api("PUT", "/me/player/play", params={"device_id": device_id}, body=body)
        else:
            raise


# ------------------------------------------------------------------------
# Playback commands
# ------------------------------------------------------------------------
def play(query="", kind="", device=""):
    if not ready():
        raise RuntimeError(unavailable_reason() or "Spotify is not connected.")
    query = " ".join(str(query or "").split())
    kind = str(kind or "").strip().lower()
    device_id, dev = _device_id(device)
    device_name = dev.get("name") or "your device"

    if not query:
        _start(device_id, {})
        return f"Resumed Spotify on {device_name}."

    low = query.lower()
    if kind in ("liked", "liked songs", "favorites", "favourites") or "liked songs" in low:
        _start(device_id, {"context_uri": _liked_context()})
        return f"Playing your liked songs on {device_name}."

    if not kind:
        if "playlist" in low or low.startswith("my ") or any(hint in low for hint in GENRE_HINTS):
            kind = "playlist"
        else:
            kind = "track"

    if kind == "playlist":
        mine = _my_playlist(query)
        if mine:
            name = mine.get("name") or query
            _start(device_id, {"context_uri": mine["uri"]})
            return f"Playing your playlist \"{name}\" on {device_name}."
        found = _first(_search(_clean_query(query) or query), "playlists")
        if not found:
            raise RuntimeError(f"No Spotify playlist found for \"{query}\".")
        _start(device_id, {"context_uri": found["uri"]})
        return f"Playing the playlist \"{found.get('name', query)}\" on {device_name}."

    data = _search(query)

    if kind == "artist":
        artist = _first(data, "artists")
        if not artist:
            raise RuntimeError(f"No artist found on Spotify for \"{query}\".")
        tops = _api("GET", f"/artists/{artist['id']}/top-tracks", params={"market": _market()}) or {}
        uris = [t.get("uri") for t in tops.get("tracks", []) if t and t.get("uri")][:10]
        if not uris:
            raise RuntimeError(f"Spotify has no playable top tracks for {artist.get('name', query)}.")
        _start(device_id, {"uris": uris})
        return f"Playing {artist.get('name', query)} on {device_name}."

    if kind == "album":
        album = _first(data, "albums")
        if not album:
            raise RuntimeError(f"No album found on Spotify for \"{query}\".")
        _start(device_id, {"context_uri": album["uri"]})
        by = ", ".join(a.get("name", "") for a in album.get("artists", []) if a.get("name"))
        suffix = f" by {by}" if by else ""
        return f"Playing the album \"{album.get('name', query)}\"{suffix} on {device_name}."

    track = _first(data, "tracks")
    if not track:
        raise RuntimeError(f"No track found on Spotify for \"{query}\".")
    _start(device_id, {"uris": [track["uri"]]})
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    by = f" by {artists}" if artists else ""
    return f"Playing \"{track.get('name', query)}\"{by} on {device_name}."


def pause():
    if not ready():
        raise RuntimeError(unavailable_reason() or "Spotify is not connected.")
    state = _api("GET", "/me/player") or {}
    dev = state.get("device") or {}
    if not dev.get("id"):
        raise RuntimeError("No active Spotify device. Open Spotify on your phone or computer first.")
    if not state.get("is_playing"):
        return "Spotify is already paused."
    _api("PUT", "/me/player/pause", params={"device_id": dev["id"]})
    return f"Paused Spotify on {dev.get('name', 'your device')}."


def next_track():
    if not ready():
        raise RuntimeError(unavailable_reason() or "Spotify is not connected.")
    state = _api("GET", "/me/player") or {}
    dev = state.get("device") or {}
    if not dev.get("id"):
        raise RuntimeError("No active Spotify device. Open Spotify on your phone or computer first.")
    _api("POST", "/me/player/next", params={"device_id": dev["id"]})
    return "Skipped to the next track."


def previous_track():
    if not ready():
        raise RuntimeError(unavailable_reason() or "Spotify is not connected.")
    state = _api("GET", "/me/player") or {}
    dev = state.get("device") or {}
    if not dev.get("id"):
        raise RuntimeError("No active Spotify device. Open Spotify on your phone or computer first.")
    _api("POST", "/me/player/previous", params={"device_id": dev["id"]})
    return "Went back to the previous track."


def set_volume(level):
    if not ready():
        raise RuntimeError(unavailable_reason() or "Spotify is not connected.")
    try:
        value = int(round(float(level)))
    except (TypeError, ValueError):
        raise ValueError("Volume must be a number from 0 to 100.")
    value = max(0, min(100, value))
    state = _api("GET", "/me/player") or {}
    dev = state.get("device") or {}
    if not dev.get("id"):
        raise RuntimeError("No active Spotify device. Open Spotify on your phone or computer first.")
    _api("PUT", "/me/player/volume", params={"volume_percent": value, "device_id": dev["id"]})
    return f"Spotify volume is now {value}% on {dev.get('name', 'your device')}."


# ------------------------------------------------------------------------
# Now playing
# ------------------------------------------------------------------------
def _idle_payload():
    return {
        "playing": False,
        "idle": True,
        "track": "",
        "artist": "",
        "album": "",
        "image": "",
        "progress_ms": 0,
        "duration_ms": 0,
        "volume": None,
        "device": "",
        "device_type": "",
        "shuffle": False,
        "repeat": "off",
        "track_id": "",
        "uri": "",
        "type": "",
    }


def _state_payload(state):
    data = _idle_payload()
    if not state:
        return data
    dev = state.get("device") or {}
    data.update(
        {
            "playing": bool(state.get("is_playing")),
            "idle": not bool(state.get("item")),
            "volume": dev.get("volume_percent"),
            "device": dev.get("name") or "",
            "device_type": dev.get("type") or "",
            "shuffle": bool(state.get("shuffle_state")),
            "repeat": state.get("repeat_state") or "off",
        }
    )
    item = state.get("item") or {}
    if not item:
        return data
    if item.get("type") == "episode" or item.get("show"):
        show = item.get("show") or {}
        images = item.get("images") or show.get("images") or []
        data.update(
            {
                "idle": False,
                "track": item.get("name") or "",
                "artist": show.get("name") or "",
                "album": "Podcast",
                "image": images[0]["url"] if images else "",
                "duration_ms": item.get("duration_ms") or 0,
                "type": "episode",
            }
        )
    else:
        album = item.get("album") or {}
        images = album.get("images") or item.get("images") or []
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
        data.update(
            {
                "idle": False,
                "track": item.get("name") or "",
                "artist": artists,
                "album": album.get("name") or "",
                "image": images[0]["url"] if images else "",
                "duration_ms": item.get("duration_ms") or 0,
                "type": "track",
            }
        )
    data.update(
        {
            "progress_ms": state.get("progress_ms") or 0,
            "track_id": item.get("id") or "",
            "uri": item.get("uri") or "",
        }
    )
    return data


def refresh():
    if not ready():
        return None
    try:
        state = _api("GET", "/me/player")
        with LOCK:
            _CACHE.update(t=time.time(), data=_state_payload(state), error="")
    except Exception as exc:  # noqa: BLE001
        with LOCK:
            _CACHE.update(t=time.time(), data=_CACHE.get("data") or _idle_payload(), error=str(exc))
    return _CACHE.get("data")


def snapshot(force=False):
    base = {"configured": available(), "connected": ready(), "error": unavailable_reason()}
    if not ready():
        return {**base, **_idle_payload()}
    with LOCK:
        stale = force or _CACHE["data"] is None or time.time() - _CACHE["t"] > 4
    if stale:
        refresh()
    with LOCK:
        data = dict(_CACHE["data"] or _idle_payload())
        error = _CACHE.get("error") or ""
    return {**base, **data, "error": error}


def _mmss(ms):
    try:
        total = max(0, int(ms) // 1000)
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60}:{total % 60:02d}"


def context_line():
    try:
        state = snapshot(False)
    except Exception:  # noqa: BLE001
        return ""
    if not state.get("connected"):
        if state.get("configured"):
            return "Spotify is configured but not connected yet; the owner can connect it in the HUD.\n\n"
        return ""
    if state.get("error") and not state.get("track"):
        return f"Spotify: {state['error']}\n\n"
    if not state.get("track"):
        device = state.get("device") or "no active device"
        return f"Spotify: connected, nothing playing (device: {device}).\n\n"
    verb = "playing" if state.get("playing") else "paused on"
    artist = f" by {state['artist']}" if state.get("artist") else ""
    volume = state.get("volume")
    vol = f", volume {volume}%" if volume is not None else ""
    return (
        f"Spotify: {verb} \"{state['track']}\"{artist} on {state.get('device') or 'a device'}"
        f" ({_mmss(state.get('progress_ms'))} of {_mmss(state.get('duration_ms'))}{vol}).\n\n"
    )


def status_text():
    state = snapshot(force=True)
    if not state.get("connected"):
        return state.get("error") or unavailable_reason() or "Spotify is not connected."
    try:
        devices = _devices()
    except Exception:  # noqa: BLE001
        devices = []
    names = ", ".join(
        (d.get("name") or "device") + (" (active)" if d.get("is_active") else "") for d in devices
    )
    if state.get("error") and not state.get("track"):
        hint = f" Devices: {names}." if names else ""
        return f"Spotify problem: {state['error']}{hint}"
    if not state.get("track"):
        tail = f" Available devices: {names}." if names else " No device is active; open Spotify on a phone or computer."
        return "Spotify is connected but nothing is playing." + tail
    artist = f" by {state['artist']}" if state.get("artist") else ""
    album = f" from {state['album']}" if state.get("album") else ""
    volume = state.get("volume")
    vol = f", volume {volume}%" if volume is not None else ""
    verb = "Playing" if state.get("playing") else "Paused at"
    line = (
        f"{verb} \"{state['track']}\"{artist}{album} on {state.get('device') or 'a device'}, "
        f"{_mmss(state.get('progress_ms'))} of {_mmss(state.get('duration_ms'))}{vol}."
    )
    if names:
        line += f" Devices: {names}."
    return line
