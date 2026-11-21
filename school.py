"""Generic school timetable for Jarvis.

The real timetable lives in `school.local.json` (gitignored). Without that file
a small sample timetable is used, so the school feature still runs.
"""

import json
import os
from datetime import datetime, timedelta

_FOLD = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})

_DAY_ALIASES = {
    "monday": 0, "mon": 0, "pazartesi": 0, "pzt": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "sali": 1, "sal": 1,
    "wednesday": 2, "wed": 2, "carsamba": 2, "car": 2,
    "thursday": 3, "thu": 3, "thur": 3, "persembe": 3, "per": 3,
    "friday": 4, "fri": 4, "cuma": 4, "cum": 4,
    "saturday": 5, "sat": 5, "cumartesi": 5, "cmt": 5,
    "sunday": 6, "sun": 6, "pazar": 6, "paz": 6,
}

GRID_INITIALS = {}

SCHOOL = "Example High School"
CLASS_NAME = "9-A"
ISSUED = "2026-09-01"

PERIODS = [
    {"n": 1, "start": "09:00", "end": "09:40"},
    {"n": 2, "start": "09:50", "end": "10:30"},
    {"n": 3, "start": "10:40", "end": "11:20"},
    {"n": 4, "start": "11:30", "end": "12:10"},
    {"n": 5, "start": "12:20", "end": "13:00"},
    {"n": 6, "start": "14:00", "end": "14:40"},
    {"n": 7, "start": "14:50", "end": "15:30"},
    {"n": 8, "start": "15:40", "end": "16:20"},
    {"n": 9, "start": "16:30", "end": "17:10"},
]

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

DAYS = {
    "Monday": ["MAT", "MAT", "SCI", "SCI", "ENG", "ENG", "TUR", "HIS", "PE"],
    "Tuesday": ["SCI", "SCI", "MAT", "MAT", "ENG", "ENG", "ART", "MUS", "COM"],
    "Wednesday": ["TUR", "TUR", "MAT", "MAT", "SCI", "SCI", "HIS", "PE", "COM"],
    "Thursday": ["ENG", "ENG", "MAT", "MAT", "TUR", "TUR", "SCI", "SCI", "ART"],
    "Friday": ["HIS", "HIS", "ENG", "ENG", "MAT", "MAT", "PE", "PE", None],
    "Saturday": [None, None, None, None, None, None, None, None, None],
}

SUBJECTS = {
    "MAT": {"subject": "Mathematics", "teacher": "", "hours": 6},
    "SCI": {"subject": "Science", "teacher": "", "hours": 6},
    "ENG": {"subject": "English", "teacher": "", "hours": 4},
    "TUR": {"subject": "Turkish", "teacher": "", "hours": 4},
    "HIS": {"subject": "History", "teacher": "", "hours": 3},
    "PE": {"subject": "Physical Education", "teacher": "", "hours": 3},
    "ART": {"subject": "Art", "teacher": "", "hours": 1},
    "MUS": {"subject": "Music", "teacher": "", "hours": 1},
    "COM": {"subject": "Computer Science", "teacher": "", "hours": 2},
}

_LOCAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school.local.json")
if os.path.exists(_LOCAL_FILE):
    try:
        with open(_LOCAL_FILE, encoding="utf-8") as _fh:
            _local = json.load(_fh)
        SCHOOL = _local.get("SCHOOL", SCHOOL)
        CLASS_NAME = _local.get("CLASS_NAME", CLASS_NAME)
        ISSUED = _local.get("ISSUED", ISSUED)
        PERIODS = _local.get("PERIODS", PERIODS)
        WEEKDAYS = _local.get("WEEKDAYS", WEEKDAYS)
        DAYS = _local.get("DAYS", DAYS)
        SUBJECTS = _local.get("SUBJECTS", SUBJECTS)
        GRID_INITIALS = _local.get("GRID_INITIALS", GRID_INITIALS)
    except (OSError, ValueError):
        pass

def _fold(text):
    return str(text or "").translate(_FOLD).lower().strip()


def _now():
    return datetime.now()


def available():
    return bool(DAYS and SUBJECTS)


def _lessons(day):
    out = []
    codes = DAYS.get(day, [])
    for index, code in enumerate(codes):
        if not code:
            continue
        meta = SUBJECTS.get(code, {})
        period = PERIODS[index]
        out.append({
            "period": period["n"],
            "start": period["start"],
            "end": period["end"],
            "code": code,
            "subject": meta.get("subject", code),
            "teacher": meta.get("teacher", ""),
        })
    return out


def _span(date, slot):
    sh, sm = (int(x) for x in slot["start"].split(":"))
    eh, em = (int(x) for x in slot["end"].split(":"))
    return (
        datetime(date.year, date.month, date.day, sh, sm),
        datetime(date.year, date.month, date.day, eh, em),
    )


def _current(now):
    day = now.strftime("%A")
    for slot in _lessons(day):
        start, end = _span(now.date(), slot)
        if start <= now < end:
            return now.date(), slot, start, end
    return None


def _next(after):
    for offset in range(0, 8):
        date = (after + timedelta(days=offset)).date()
        for slot in _lessons(date.strftime("%A")):
            start, end = _span(date, slot)
            if start > after:
                return date, slot, start, end
    return None


def _minutes_until(target, now):
    return max(0, int((target - now).total_seconds() // 60))


def _in_text(target, now):
    mins = _minutes_until(target, now)
    if mins <= 0:
        return "now"
    if mins < 60:
        return f"in {mins} min"
    hours, rest = divmod(mins, 60)
    return f"in {hours} h {rest} min" if rest else f"in {hours} h"


def _when_text(date, target, now):
    days = (date - now.date()).days
    start = target.strftime("%H:%M")
    if days == 0:
        return f"today at {start} ({_in_text(target, now)})"
    if days == 1:
        return f"tomorrow ({date.strftime('%A')}) at {start}"
    if days < 7:
        return f"on {date.strftime('%A')} at {start}"
    return f"on {date.strftime('%A %d %B')} at {start}"


def _teacher_text(slot):
    return slot["teacher"] or "no teacher listed"


def next_text(now=None):
    now = now or _now()
    current = _current(now)
    if current:
        date, slot, start, end = current
        left = _minutes_until(end, now)
        text = (
            f"You are in {slot['subject']} ({slot['code']}) with {_teacher_text(slot)}, "
            f"until {slot['end']} ({left} min left)."
        )
        upcoming = _next(now)
        if upcoming:
            ndate, nslot, nstart, nend = upcoming
            text += (
                f" Next: {nslot['subject']} ({nslot['code']}) with {_teacher_text(nslot)} "
                f"{_when_text(ndate, nstart, now)}."
            )
        return text
    upcoming = _next(now)
    if not upcoming:
        return "No classes are listed in the timetable."
    date, slot, start, end = upcoming
    teacher = f" with {slot['teacher']}" if slot["teacher"] else ""
    return (
        f"No class right now. Next: {slot['subject']} ({slot['code']}){teacher} "
        f"{_when_text(date, start, now)}."
    )


def context_line(now=None):
    if not available():
        return ""
    return f"School ({CLASS_NAME}): {next_text(now)}\n\n"


def _parse_day(text, now):
    raw = _fold(text)
    if raw in ("", "today", "bugun"):
        return now.date()
    if raw in ("tomorrow", "yarin"):
        return now.date() + timedelta(days=1)
    if raw in ("yesterday", "dun"):
        return now.date() - timedelta(days=1)
    if raw in _DAY_ALIASES:
        target = _DAY_ALIASES[raw]
        return now.date() + timedelta(days=(target - now.weekday()) % 7)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            continue
    return None


def day_text(day="today", now=None):
    now = now or _now()
    raw = _fold(day)
    if raw in ("week", "hafta", "weekly", "timetable"):
        return week_text(now)
    date = _parse_day(day, now)
    if date is None:
        return (
            "I did not recognise that day. Use today, tomorrow, a weekday "
            "(Monday, Pazartesi...), a date like 20.11.2026, or week."
        )
    label = date.strftime("%A %d %B %Y")
    if date == now.date():
        label += " (today)"
    elif date == now.date() + timedelta(days=1):
        label += " (tomorrow)"
    lessons = _lessons(date.strftime("%A"))
    if not lessons:
        return f"{label}: no classes."
    lines = [f"{label} - {CLASS_NAME}, {len(lessons)} lessons:"]
    for slot in lessons:
        teacher = f" - {slot['teacher']}" if slot["teacher"] else ""
        lines.append(
            f"{slot['period']}. {slot['start']}-{slot['end']} {slot['code']} {slot['subject']}{teacher}"
        )
    return "\n".join(lines)


def _groups(day):
    groups = []
    for slot in _lessons(day):
        last = groups[-1] if groups else None
        if last and last["code"] == slot["code"] and last["end_period"] + 1 == slot["period"]:
            last["end_period"] = slot["period"]
            last["end"] = slot["end"]
        else:
            groups.append({
                "code": slot["code"],
                "subject": slot["subject"],
                "teacher": slot["teacher"],
                "start_period": slot["period"],
                "end_period": slot["period"],
                "start": slot["start"],
                "end": slot["end"],
            })
    return groups


def week_text(now=None):
    now = now or _now()
    lines = [f"{CLASS_NAME} weekly timetable (issued {ISSUED}):"]
    total = 0
    for day in WEEKDAYS:
        groups = _groups(day)
        if not groups:
            lines.append(f"{day}: no classes")
            continue
        total += sum(g["end_period"] - g["start_period"] + 1 for g in groups)
        parts = []
        for group in groups:
            teacher = f" ({group['teacher']})" if group["teacher"] else ""
            parts.append(f"{group['code']} {group['subject']}{teacher} {group['start']}-{group['end']}")
        lines.append(f"{day}: " + "; ".join(parts))
    lines.append(f"Total: {total} hours a week.")
    return "\n".join(lines)


def teacher_text(query):
    raw = _fold(query)
    if not raw:
        return "Give me a subject code like MA13, a subject like fizik, or a teacher surname like Uguz."
    compact = raw.replace(".", "").replace(" ", "")
    grid = {_fold(k).replace(".", ""): v for k, v in GRID_INITIALS.items()}
    found = []
    for code, meta in SUBJECTS.items():
        code_norm = _fold(code).replace(".", "")
        teacher = meta["teacher"]
        teacher_norm = _fold(teacher)
        abbr_match = grid.get(compact) == code
        name_match = bool(teacher_norm) and raw in teacher_norm
        parts = [p for p in teacher.split() if p]
        initial_match = False
        if len(parts) >= 2 and len(compact) >= 3:
            first = _fold(parts[0][0])
            surname = _fold(parts[-1])
            initial_match = compact[:1] == first and surname.startswith(compact[1:])
        matches = (
            compact == code_norm
            or raw in _fold(meta["subject"])
            or name_match
            or abbr_match
            or initial_match
        )
        if matches:
            found.append((code, meta))
    if not found:
        return (
            f"No match for \"{query}\" in the timetable. Try a code (MA13, COR7), "
            "a subject (matematik, fizik) or a teacher (Uguz, Kilic)."
        )
    lines = []
    for code, meta in found:
        teacher = meta["teacher"] or "no teacher listed"
        lines.append(
            f"{code} - {meta['subject']}, {meta['hours']} hours a week. Teacher: {teacher}. "
            f"Slots: {_slots_summary(code)}."
        )
    return "\n".join(lines)


def _slots_summary(code):
    bits = []
    for day in WEEKDAYS:
        nums = [
            PERIODS[i]["n"] for i, value in enumerate(DAYS.get(day, []))
            if value == code
        ]
        if not nums:
            continue
        ranges = []
        start = prev = nums[0]
        for number in nums[1:]:
            if number == prev + 1:
                prev = number
                continue
            ranges.append((start, prev))
            start = prev = number
        ranges.append((start, prev))
        text = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)
        bits.append(f"{day} {text}")
    return "; ".join(bits) if bits else "none"


def _payload(date, slot, start, end, now):
    days = (date - now.date()).days
    when = "today" if days == 0 else "tomorrow" if days == 1 else date.strftime("%A")
    return {
        "code": slot["code"],
        "subject": slot["subject"],
        "teacher": slot["teacher"],
        "period": slot["period"],
        "start": slot["start"],
        "end": slot["end"],
        "date": date.isoformat(),
        "when": when,
        "minutes": _minutes_until(start, now),
        "minutes_left": max(0, _minutes_until(end, now)),
    }


def snapshot(now=None):
    now = now or _now()
    current = _current(now)
    upcoming = _next(now)

    rows = []
    for slot in _lessons(now.strftime("%A")):
        start, end = _span(now.date(), slot)
        if current and current[1]["period"] == slot["period"] and current[1]["code"] == slot["code"]:
            state = "current"
        elif end <= now:
            state = "past"
        else:
            state = "upcoming"
        rows.append({
            "period": slot["period"],
            "start": slot["start"],
            "end": slot["end"],
            "code": slot["code"],
            "subject": slot["subject"],
            "teacher": slot["teacher"],
            "state": state,
        })

    if current:
        headline = f"{current[1]['subject']} · {_teacher_text(current[1])}"
        subline = f"Until {current[1]['end']} · {_minutes_until(current[3], now)} min left"
        if upcoming:
            subline += (
                f" · Next {upcoming[1]['subject']} ({upcoming[1]['start']})"
            )
    else:
        headline = "No class right now"
        subline = next_text(now)
        prefix = "No class right now. "
        if subline.startswith(prefix):
            subline = subline[len(prefix):]

    tomorrow_date = now.date() + timedelta(days=1)
    tomorrow = _lessons(tomorrow_date.strftime("%A"))
    if tomorrow:
        tomorrow_text = (
            f"Tomorrow ({tomorrow_date.strftime('%A')}): {len(tomorrow)} lessons, "
            f"starting with {tomorrow[0]['subject']} at {tomorrow[0]['start']}."
        )
    else:
        tomorrow_text = f"Tomorrow ({tomorrow_date.strftime('%A')}): no classes."

    return {
        "configured": available(),
        "school": SCHOOL,
        "class": CLASS_NAME,
        "issued": ISSUED,
        "day": now.strftime("%A"),
        "date": now.date().isoformat(),
        "date_label": now.strftime("%A %d %B %Y"),
        "headline": headline,
        "subline": subline,
        "current": _payload(*current, now) if current else None,
        "next": _payload(*upcoming, now) if upcoming else None,
        "lessons": rows,
        "tomorrow": {"day": tomorrow_date.strftime("%A"), "lessons": tomorrow},
        "tomorrow_text": tomorrow_text,
    }
