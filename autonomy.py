"""
Jarvis autonomy engine: decides WHEN Jarvis acts on his own.

This module holds no AI logic. It answers one question - "is something due right
now?" - and remembers what already fired today so a restart does not repeat
announcements. server.py runs the Gemini agent for each trigger and announces
the result. Every state-changing tool call during an autonomous run is reported
back to the owner in the HUD's automation log.

Triggers, in priority order:
  morning    one briefing a day after AUTONOMY_MORNING
  lesson     a heads-up shortly before each school lesson
  day_end    wrap-up after the last lesson of the day
  wind_down  a calm closing line in the evening
  free       a self-directed check-in every AUTONOMY_MINUTES

.env (all optional):
    AUTONOMY=1               master switch
    AUTONOMY_MINUTES=25      free check-in interval
    AUTONOMY_START=07:30     active hours begin
    AUTONOMY_END=23:00       active hours end
    AUTONOMY_MORNING=07:30   morning briefing
    AUTONOMY_DAY_END=auto    wrap-up time, or a clock time such as 17:20
    AUTONOMY_WIND_DOWN=22:30 evening line
    AUTONOMY_FREE=1          allow free check-ins
    AUTONOMY_LESSONS=1       heads-up before every lesson
"""

import json
import os
import threading
import time
from datetime import datetime

from dotenv import load_dotenv

import school

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, ".autonomy_state.json")
OWNER_NAME = os.getenv("OWNER_NAME", "boss")
LOCK = threading.Lock()
MAX_LOG = 60
QUIET_AFTER_CHAT = 5 * 60      # do not interrupt a conversation
LESSON_LEAD_MIN = 5            # heads-up this many minutes before a lesson
DAY_END_GRACE = 5


def _flag(name, default=True):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _clock(raw, default):
    for value in (raw, default):
        try:
            hours, minutes = (int(x) for x in str(value).strip().split(":"))
            if 0 <= hours < 24 and 0 <= minutes < 60:
                return hours * 60 + minutes
        except (ValueError, TypeError):
            continue
    return 0


def _env_clock(name, default="00:00"):
    raw = (os.getenv(name) or "").strip()
    if not raw or raw.lower() == "auto":
        return None
    return _clock(raw, default)


START = _clock(os.getenv("AUTONOMY_START") or "07:30", "07:30")
END = _clock(os.getenv("AUTONOMY_END") or "23:00", "23:00")
MORNING = _clock(os.getenv("AUTONOMY_MORNING") or "07:30", "07:30")
DAY_END = _env_clock("AUTONOMY_DAY_END", "auto")
WIND_DOWN = _clock(os.getenv("AUTONOMY_WIND_DOWN") or "22:30", "22:30")
ENABLED = _flag("AUTONOMY", True)
FREE = _flag("AUTONOMY_FREE", True)
LESSONS = _flag("AUTONOMY_LESSONS", True)


def _minutes_env(name, default, floor):
    try:
        value = int(float(os.getenv(name, "")))
    except (TypeError, ValueError):
        value = default
    return max(floor, value)


MINUTES = _minutes_env("AUTONOMY_MINUTES", 25, 5)


def _hm(minutes):
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _stamp(epoch):
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _clock_from_text(text):
    try:
        hours, minutes = (int(x) for x in str(text).split(":"))
        return hours * 60 + minutes
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Persisted state
# --------------------------------------------------------------------------
def _default_state():
    return {
        "enabled": None,      # None means "use the .env default"
        "triggers": {},       # {"morning": "2026-11-18", "lesson": "2026-11-18#3", ...}
        "last_free": 0.0,
        "last_run": 0.0,
        "last_chat": 0.0,
        "log": [],
    }


def _load():
    state = _default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            state.update(data)
    except (OSError, ValueError):
        pass
    if not isinstance(state.get("log"), list):
        state["log"] = []
    if not isinstance(state.get("triggers"), dict):
        state["triggers"] = {}
    stale = time.time() - float(state.get("last_free") or 0) > 20 * 3600
    if stale:
        state["last_free"] = time.time()
    return state


STATE = _load()


def _save():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(STATE, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


_save()


# --------------------------------------------------------------------------
# Public state helpers
# --------------------------------------------------------------------------
def enabled():
    with LOCK:
        value = STATE.get("enabled")
        return ENABLED if value is None else bool(value)


def set_enabled(on):
    with LOCK:
        STATE["enabled"] = bool(on)
        _save()
    return enabled()


def available():
    return True


def mark_chat():
    """Called whenever the owner talks to Jarvis so free check-ins stay quiet meanwhile."""
    with LOCK:
        STATE["last_chat"] = time.time()


def last_chat():
    with LOCK:
        return float(STATE.get("last_chat") or 0)


def note(kind, text):
    text = str(text or "").strip()
    if not text:
        return
    with LOCK:
        STATE["log"].append({"ts": time.time(), "kind": kind, "text": text[:400]})
        del STATE["log"][:-MAX_LOG]
        _save()


def consume(trigger):
    """Marks a trigger as handled so it never fires twice."""
    now = time.time()
    with LOCK:
        triggers = STATE["triggers"]
        kind = trigger.get("kind", "")
        if kind in ("morning", "day_end", "wind_down"):
            triggers[kind] = trigger.get("key") or datetime.now().date().isoformat()
        elif kind == "lesson":
            triggers["lesson"] = trigger.get("key", "")
        elif kind == "free":
            STATE["last_free"] = now
        STATE["last_run"] = now
        _save()


# --------------------------------------------------------------------------
# Trigger decisions
# --------------------------------------------------------------------------
def _active(now):
    minutes = now.hour * 60 + now.minute
    if START <= END:
        return START <= minutes < END
    return minutes >= START or minutes < END


def _last_lesson_end(now):
    for slot in reversed(school._lessons(now.strftime("%A"))):
        return _clock_from_text(slot["end"])
    return None


def _day_end_minutes(now):
    if DAY_END is not None:
        return DAY_END
    end = _last_lesson_end(now)
    return end + DAY_END_GRACE if end is not None else None


def _lesson_soon(now):
    try:
        payload = school.snapshot(now)
    except Exception:  # noqa: BLE001
        return None
    upcoming = payload.get("next") or {}
    minutes = upcoming.get("minutes")
    if minutes is None or not (0 <= minutes <= LESSON_LEAD_MIN):
        return None
    if upcoming.get("date") != now.date().isoformat():
        return None
    return {
        "key": f"{upcoming['date']}#{upcoming['period']}",
        "subject": upcoming.get("subject", "a lesson"),
        "code": upcoming.get("code", ""),
        "teacher": upcoming.get("teacher", ""),
        "start": upcoming.get("start", ""),
        "minutes": minutes,
    }


def _prompt_morning(now):
    return (
        f"Autonomous morning briefing at {now:%H:%M}, {now:%A %d %B}. Greet {OWNER_NAME} in one short line, "
        "then cover only what matters today: the first lessons, calendar events, weather worth acting on, and any "
        "unread email or WhatsApp that seems important (check with tools only if it is cheap). Keep it to at most "
        "four spoken sentences. If everything is quiet, one calm sentence is enough."
    )


def _prompt_lesson(now, lesson):
    teacher = f" with {lesson['teacher']}" if lesson.get("teacher") else ""
    return (
        f"In {lesson['minutes']} minute(s) {OWNER_NAME} has {lesson['subject']} ({lesson['code']}){teacher}, "
        f"starting at {lesson['start']}. Give a single short spoken heads-up. Use a tool only if something "
        "genuinely useful should happen right now, for example a reminder; otherwise call none."
    )


def _prompt_day_end(now):
    return (
        f"School is over for today ({now:%H:%M}). Give {OWNER_NAME} a brief wrap-up: tomorrow's timetable in one "
        "line and any calendar events for tomorrow. Set a reminder or open something only if it is clearly useful. "
        "Two or three spoken sentences at most."
    )


def _prompt_wind_down(now):
    return (
        f"It is {now:%H:%M}, wind-down time. If the lights are still on and bright, dim them to something warm and "
        f"quiet with control_lights. Then give {OWNER_NAME} one calm line about anything left for tomorrow. "
        "Two sentences at most."
    )


def _prompt_free(now):
    return (
        f"Free time at {now:%H:%M}. You are on your own for a few minutes. Pick one small, genuinely useful thing "
        f"for {OWNER_NAME} and finish it with your tools; you may use the computer yourself. Good choices: prepare "
        "tabs or apps for the next lesson, tidy the desktop, queue fitting music, adjust the lights or start a drink, "
        "or check something that saves the owner time. Do not send messages or email, do not delete anything and do "
        "not spend money. Afterwards confirm in one short line what you actually did. If nothing would genuinely "
        "help, call no tools and reply with exactly NO_ACTION."
    )


def manual_prompt(now=None):
    now = now or datetime.now()
    return (
        f"The owner pressed Run now at {now:%H:%M} and wants a full autonomous check-in. Review your live context "
        f"and handle anything genuinely useful for {OWNER_NAME} today: heads-ups, reminders, the calendar, lights, "
        "music, coffee, or a small desktop task; carry the chosen task through with your tools. Keep it to one or two "
        "spoken sentences. If everything is already in order, say so briefly."
    )


def due(now=None):
    """Returns the trigger that should run right now, or None."""
    now = now or datetime.now()
    if not enabled() or not _active(now):
        return None
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute
    wall = time.time()
    with LOCK:
        triggers = dict(STATE["triggers"])
        last_free = float(STATE["last_free"] or 0)
        last_run = float(STATE["last_run"] or 0)
        last_chat = float(STATE["last_chat"] or 0)

    if triggers.get("morning") != today and MORNING <= minutes < MORNING + 90:
        return {"kind": "morning", "key": today, "label": "Morning briefing", "prompt": _prompt_morning(now)}

    if LESSONS and school.available():
        lesson = _lesson_soon(now)
        if lesson and triggers.get("lesson") != lesson["key"]:
            return {
                "kind": "lesson",
                "key": lesson["key"],
                "label": f"Heads-up: {lesson['subject']}",
                "prompt": _prompt_lesson(now, lesson),
            }

    day_end = _day_end_minutes(now)
    if day_end is not None and triggers.get("day_end") != today and day_end <= minutes < day_end + 120:
        return {"kind": "day_end", "key": today, "label": "School wrap-up", "prompt": _prompt_day_end(now)}

    if triggers.get("wind_down") != today and WIND_DOWN <= minutes < WIND_DOWN + 90:
        return {"kind": "wind_down", "key": today, "label": "Wind down", "prompt": _prompt_wind_down(now)}

    if (
        FREE
        and last_free > 0
        and wall - last_free >= MINUTES * 60
        and wall - last_run >= MINUTES * 60
        and wall - last_chat >= QUIET_AFTER_CHAT
    ):
        return {"kind": "free", "key": "", "label": "Free check-in", "prompt": _prompt_free(now)}

    return None


def next_due(now=None):
    """Best-effort description of the next scheduled trigger, for the HUD."""
    now = now or datetime.now()
    if not enabled():
        return None
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute
    wall = time.time()
    with LOCK:
        triggers = dict(STATE["triggers"])
        last_free = float(STATE["last_free"] or 0)
        last_run = float(STATE["last_run"] or 0)

    options = []

    def add(kind, label, at_minutes):
        if at_minutes is None:
            return
        at_minutes = int(at_minutes)
        if at_minutes < START or at_minutes >= END:
            return
        options.append({"kind": kind, "label": label, "at": _hm(at_minutes), "in": max(0, at_minutes - minutes)})

    if triggers.get("morning") != today and minutes < MORNING + 90:
        add("morning", "Morning briefing", max(MORNING, minutes))

    if LESSONS and school.available():
        try:
            upcoming = school.snapshot(now).get("next") or {}
        except Exception:  # noqa: BLE001
            upcoming = {}
        if upcoming and upcoming.get("date") == today:
            key = f"{upcoming['date']}#{upcoming['period']}"
            if triggers.get("lesson") != key:
                start = _clock_from_text(upcoming.get("start"))
                if start is not None:
                    add("lesson", f"{upcoming.get('subject')} heads-up", max(start - LESSON_LEAD_MIN, minutes))

    day_end = _day_end_minutes(now)
    if day_end is not None and triggers.get("day_end") != today and minutes < day_end + 120:
        add("day_end", "School wrap-up", max(day_end, minutes))

    if triggers.get("wind_down") != today and minutes < WIND_DOWN + 90:
        add("wind_down", "Wind down", max(WIND_DOWN, minutes))

    if FREE and last_free > 0:
        free_at = minutes + max(0, int((last_free + MINUTES * 60 - wall) // 60))
        add("free", "Free check-in", free_at)

    if not options:
        return None
    options.sort(key=lambda item: item["in"])
    return options[0]


def snapshot(now=None):
    now = now or datetime.now()
    with LOCK:
        log = [dict(entry) for entry in STATE["log"][-20:]]
        last_run = float(STATE["last_run"] or 0)
        on = ENABLED if STATE.get("enabled") is None else bool(STATE["enabled"])
    entry = next_due(now)
    return {
        "enabled": on,
        "active": _active(now),
        "window": f"{_hm(START)}-{_hm(END)}",
        "minutes": MINUTES,
        "free": FREE,
        "lessons": LESSONS,
        "last_run": last_run,
        "last_run_text": _stamp(last_run) if last_run else "",
        "next": entry,
        "log": [{"ts": item.get("ts", 0), "time": _stamp(item.get("ts")), "kind": item.get("kind", ""), "text": item.get("text", "")}
                for item in reversed(log)],
    }
