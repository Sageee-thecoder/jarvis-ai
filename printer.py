"""MXW01 thermal receipts for J.A.R.V.I.S.

Renders 384 px wide "receipt" designs with Pillow and prints them over BLE
using the CAT-printer packet protocol (MXW01 / MXW10 family).

Usage:
    python printer.py --daily            render + print the daily receipt
    python printer.py --weekly           render + print the weekly receipt
    python printer.py --daily --render   render a PNG preview only
    python printer.py --status           show printer/scheduler state
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECEIPT_DIR = os.path.join(BASE_DIR, "logs", "receipts")
STATE_FILE = os.path.join(BASE_DIR, "logs", "printer_state.json")

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except Exception:
    pass

DEVICE_MAC = (os.getenv("MXW01_MAC") or "AA:BB:CC:DD:EE:FF").strip().upper()
PRINT_HOUR = 21
PRINT_WINDOW_HOURS = 3          # catch up until 23:59 if 21:00 was missed
ROTATE = (os.getenv("MXW01_ROTATE") or "180").strip()


def _level(name: str, fallback: int, low: int = 0x00, high: int = 0xFF) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw, 0) if raw else fallback
    except ValueError:
        value = fallback
    return max(low, min(high, value))


INTENSITY = _level("MXW01_INTENSITY", 0x96)        # 0x5D is the usual default; higher = darker
THRESHOLD = _level("MXW01_THRESHOLD", 190, 1, 254)  # luminance below this prints black
BOLD = (os.getenv("MXW01_BOLD") or "0").strip() not in ("0", "false", "off")
PACE_S = _level("MXW01_PACE_MS", 15, 1, 60) / 1000.0  # per 48-byte row
CHUNK_ROWS = _level("MXW01_CHUNK_ROWS", 4, 1, 8)      # rows per BLE write

SERVICE_UUID = "0000ae30-0000-1000-8000-00805f9b34fb"
CTRL_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"
DATA_UUID = "0000ae03-0000-1000-8000-00805f9b34fb"

ROW_BYTES = 48                  # 384 px
MIN_DATA_BYTES = 4320           # device requires at least 90 lines of payload
ACK_TIMEOUT_S = 7.0
PRINT_BASE_TIMEOUT_S = 15.0
LINES_PER_SECOND = 15.0

W = 384
MARGIN = 16
MAX_H = 2400

FONT_FILES = {
    "black": r"C:\Windows\Fonts\ariblk.ttf",
    "bold": r"C:\Windows\Fonts\seguisb.ttf",
    "mono": r"C:\Windows\Fonts\consolab.ttf",
}

QUOTES = [
    "At your service, sir.",
    "All systems nominal.",
    "A pleasure, as always.",
    "Everything is under control.",
    "Standing by, sir.",
    "Another day, handled.",
    "The suit and I are at your disposal.",
]
WEEK_QUOTES = [
    "The week, summarised. As requested.",
    "Seven days, accounted for.",
    "A full report, sir. No charge.",
]

LOCK = threading.Lock()
STATE = {"last_daily": "", "last_weekly": "", "last_result": "", "last_kind": "", "last_ts": 0}
STATUS = {"busy": False, "scheduler": False, "error": ""}

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    PIL_OK = True
except Exception:  # pragma: no cover - Pillow is a hard dependency for previews
    PIL_OK = False

_FONTS: dict = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _font(name: str, size: int):
    key = (name, size)
    cached = _FONTS.get(key)
    if cached is None:
        cached = ImageFont.truetype(FONT_FILES[name], size)
        _FONTS[key] = cached
    return cached


def _load_state() -> None:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in ("last_daily", "last_weekly", "last_result", "last_kind"):
            if key in data:
                STATE[key] = data[key]
        STATE["last_ts"] = float(data.get("last_ts") or 0)
    except Exception:
        pass


def _save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(STATE, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def _week_key(day) -> str:
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------------------
# BLE protocol
# ---------------------------------------------------------------------------


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _packet(cmd: int, payload: bytes = b"") -> bytes:
    length = len(payload)
    return bytes([0x22, 0x21, cmd, 0x00, length & 0xFF, (length >> 8) & 0xFF]) + payload + bytes(
        [_crc8(payload), 0xFF]
    )


def _rows_from_image(img):
    if ROTATE == "180":
        img = img.rotate(180)
    img = img.convert("L")
    if BOLD:
        # MinFilter expands dark pixels by one pixel: bolder strokes, darker output
        img = img.filter(ImageFilter.MinFilter(3))
    width, height = img.size
    pixels = img.load()
    rows = []
    for y in range(height):
        row = bytearray(ROW_BYTES)
        for x in range(width):
            if pixels[x, y] < THRESHOLD:
                row[x >> 3] |= 1 << (x & 7)
        rows.append(bytes(row))
    data = b"".join(rows)
    if len(data) < MIN_DATA_BYTES:
        data += b"\x00" * (MIN_DATA_BYTES - len(data))
    return data, height


class _Inbox:
    def __init__(self) -> None:
        self.packets = []

    def callback(self, _sender, data: bytearray) -> None:
        self.packets.append(bytes(data))

    def has(self, cmd: int) -> bool:
        return any(len(p) >= 8 and p[2] == cmd for p in self.packets)

    def payload(self, cmd: int):
        for p in self.packets:
            if len(p) >= 8 and p[2] == cmd:
                size = p[4] | (p[5] << 8)
                return p[7 : 7 + size]
        return None


async def _wait_cmd(inbox: _Inbox, cmd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if inbox.has(cmd):
            return True
        await asyncio.sleep(0.05)
    return False


async def _connect():
    from bleak import BleakClient, BleakScanner

    try:
        client = BleakClient(DEVICE_MAC, timeout=12.0)
        await client.connect()
        return client
    except Exception:
        device = await BleakScanner.find_device_by_filter(
            lambda dev, adv: (dev.address or "").upper() == DEVICE_MAC
            or "mxw01" in (dev.name or "").lower(),
            timeout=12.0,
        )
        if device is None:
            raise RuntimeError(f"Printer {DEVICE_MAC} not found nearby - switch it on and try again")
        client = BleakClient(device, timeout=12.0)
        await client.connect()
        return client


async def _print_async(data: bytes, lines: int):
    began = time.monotonic()
    client = await _connect()
    inbox = _Inbox()
    battery = None
    try:
        await client.start_notify(NOTIFY_UUID, inbox.callback)
        # Intensity first, then status, then the job - the documented order, so the
        # value is not reset by the status query.
        await client.write_gatt_char(CTRL_UUID, _packet(0xA2, bytes([INTENSITY])), response=False)
        await asyncio.sleep(0.15)
        await client.write_gatt_char(CTRL_UUID, _packet(0xA1, b"\x00"), response=False)
        if await _wait_cmd(inbox, 0xA1, ACK_TIMEOUT_S):
            status = inbox.payload(0xA1) or b""
            if len(status) > 12 and status[12] != 0:
                raise RuntimeError(f"Printer reports an error (code {status[13] if len(status) > 13 else '?'})")
            if len(status) > 9:
                battery = status[9]
        else:
            raise RuntimeError("Printer did not answer the status request")

        inbox.packets.clear()
        payload = bytes([lines & 0xFF, (lines >> 8) & 0xFF, 0x30, 0x00])
        await client.write_gatt_char(CTRL_UUID, _packet(0xA9, payload), response=False)
        if not await _wait_cmd(inbox, 0xA9, ACK_TIMEOUT_S):
            raise RuntimeError("Printer did not accept the job")

        # Batch rows into one BLE write to cut per-packet latency; fall back to
        # single rows if the negotiated ATT MTU is too small for a batch.
        chunk_size = ROW_BYTES * CHUNK_ROWS
        rows_per_write = CHUNK_ROWS
        feed_start = time.monotonic()
        pos = 0
        while pos < len(data):
            piece = data[pos : pos + chunk_size]
            try:
                await client.write_gatt_char(DATA_UUID, piece, response=False)
            except Exception:
                if chunk_size == ROW_BYTES:
                    raise
                chunk_size = max(ROW_BYTES, (chunk_size // 2) // ROW_BYTES * ROW_BYTES)
                rows_per_write = chunk_size // ROW_BYTES
                continue
            rows_per_write = len(piece) // ROW_BYTES
            pos += len(piece)
            await asyncio.sleep(PACE_S * max(1, rows_per_write))
        feed_s = time.monotonic() - feed_start

        # Flush first (end-of-data marker), then wait for the print-complete notification.
        await client.write_gatt_char(CTRL_UUID, _packet(0xAD, b"\x00"), response=False)
        timeout = PRINT_BASE_TIMEOUT_S + lines / LINES_PER_SECOND
        done = await _wait_cmd(inbox, 0xAA, timeout)
        if not done:
            raise RuntimeError("Print did not confirm in time")
        return battery, rows_per_write, feed_s, time.monotonic() - began
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def _run_print(img):
    data, lines = _rows_from_image(img)
    battery, rows_per_write, feed_s, total_s = asyncio.run(_print_async(data, lines))
    return battery, lines, rows_per_write, feed_s, total_s


# ---------------------------------------------------------------------------
# Receipt canvas
# ---------------------------------------------------------------------------


def _fit(draw, text: str, font, max_width: float) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    cut = text
    while cut and draw.textlength(cut + "...", font=font) > max_width:
        cut = cut[:-1]
    return cut.rstrip(" .") + "..."


class Sheet:
    def __init__(self) -> None:
        self.img = Image.new("L", (W, MAX_H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.y = 0

    # -- primitives ------------------------------------------------------
    def write(self, text, x=MARGIN, size=14, weight="bold", gap=4, max_w=None, fill=0, anchor=None):
        font = _font(weight, size)
        if anchor is None:
            text = _fit(self.d, text, font, max_w if max_w is not None else W - x - MARGIN)
        self.d.text((x, self.y), text, font=font, fill=fill, anchor=anchor)
        self.y += size + gap

    def right(self, text, x=W - MARGIN, size=11, weight="mono", gap=0, max_w=160, fill=0):
        font = _font(weight, size)
        text = _fit(self.d, text, font, max_w)
        self.d.text((x, self.y), text, font=font, fill=fill, anchor="ra")
        self.y += size + gap

    def gap(self, px: int) -> None:
        self.y += px

    def rule(self, dashed=False, gap_before=6, gap_after=8) -> None:
        self.y += gap_before
        if dashed:
            for x in range(MARGIN, W - MARGIN, 8):
                self.d.rectangle([x, self.y, x + 3, self.y + 1], fill=0)
            self.y += 2
        else:
            self.d.rectangle([MARGIN, self.y, W - MARGIN, self.y + 2], fill=0)
            self.y += 3
        self.y += gap_after

    def band(self, height=26, fill=0) -> None:
        self.d.rectangle([MARGIN, self.y, W - MARGIN, self.y + height - 1], fill=fill)
        self.y += height

    def section(self, title: str, meta: str = "") -> None:
        self.y += 6
        font = _font("mono", 12)
        self.d.rectangle([MARGIN, self.y, W - MARGIN, self.y + 21], fill=0)
        self.d.text((MARGIN + 7, self.y + 4), title.upper(), font=font, fill=255)
        if meta:
            self.d.text((W - MARGIN - 7, self.y + 4), meta.upper(), font=font, fill=255, anchor="ra")
        self.y += 21 + 8

    def stats(self, items, box_h=56) -> None:
        count = len(items)
        gap = 6
        box_w = (W - 2 * MARGIN - gap * (count - 1)) // count
        for index, (value, caption) in enumerate(items):
            x = MARGIN + index * (box_w + gap)
            self.d.rectangle([x, self.y, x + box_w - 1, self.y + box_h - 1], outline=0, width=2)
            self.d.text((x + box_w // 2, self.y + 7), str(value), font=_font("black", 24), fill=0, anchor="ma")
            self.d.text(
                (x + box_w // 2, self.y + box_h - 17), caption.upper(), font=_font("mono", 9), fill=0, anchor="ma"
            )
        self.y += box_h + 10

    # -- composites ------------------------------------------------------
    def header(self, tag: str, date_line: str) -> None:
        self.d.rectangle([0, 0, W, 7], fill=0)
        self.y = 20
        self.d.text((MARGIN, self.y - 4), "J.A.R.V.I.S.", font=_font("black", 42), fill=0)
        self.d.rectangle([W - MARGIN - 46, self.y + 2, W - MARGIN, self.y + 18], outline=0, width=2)
        self.d.text((W - MARGIN - 23, self.y + 4), "MK VII", font=_font("mono", 8), fill=0, anchor="ma")
        self.y += 48
        self.d.rectangle([MARGIN, self.y, W - MARGIN, self.y + 25], fill=0)
        self.d.text((MARGIN + 7, self.y + 5), tag.upper(), font=_font("mono", 13), fill=255)
        self.d.text((W - MARGIN - 7, self.y + 5), date_line.upper(), font=_font("mono", 13), fill=255, anchor="ra")
        self.y += 25 + 6

    def barcode(self, seed: str, height=22) -> None:
        self.y += 8
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        left = MARGIN + 12
        right = W - MARGIN - 12
        count = 84
        step = (right - left) / count
        for index in range(count):
            if not ((digest[index % 32] >> (index % 6)) & 1) and index % 3:
                continue
            x = int(left + index * step)
            width = 2 if (digest[(index * 5) % 32] >> (index % 4)) & 1 else 1
            self.d.rectangle([x, self.y, x + width - 1, self.y + height], fill=0)
        self.y += height + 4
        code = hashlib.md5(seed.encode("utf-8")).hexdigest()[:10].upper()
        self.d.text((W // 2, self.y), f"#{code}", font=_font("mono", 9), fill=0, anchor="ma")
        self.y += 9

    def footer(self, quote: str, seed: str) -> None:
        self.rule(dashed=True, gap_before=8, gap_after=8)
        font = _font("bold", 12)
        self.d.text((W // 2, self.y), _fit(self.d, quote, font, W - 2 * MARGIN), font=font, fill=0, anchor="ma")
        self.y += 15
        self.barcode(seed)
        self.d.text((W // 2, self.y + 2), "END OF REPORT", font=_font("mono", 9), fill=0, anchor="ma")
        self.y += 12

    def finish(self) -> Image:
        height = min(MAX_H, self.y + 14)
        return self.img.crop((0, 0, W, height))


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _safe(fn, default):
    try:
        value = fn()
        return default if value is None else value
    except Exception:
        return default


def _server_module():
    for name in ("server", "__main__"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "weather_payload"):
            return mod
    return None


def _weather():
    mod = _server_module()
    if mod is None:
        return None
    data = _safe(mod.weather_payload, {})
    return data if data.get("ok") else None


def _calendar_items(days=1, limit=2):
    def fetch():
        import gcal

        if not gcal.ready():
            return []
        items, _ = gcal._fetch(days=days, limit=limit)
        out = []
        for item in items:
            start = item.get("start", {})
            raw = start.get("dateTime") or start.get("date")
            if not raw:
                continue
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            stamp = "" if start.get("date") else when.strftime("%H:%M")
            out.append({"when": stamp, "summary": item.get("summary", "Event")})
        return out

    return _safe(fetch, [])


def _school_snapshot():
    def snap():
        import school

        return school.snapshot()

    return _safe(snap, {})


def _autonomy_today():
    def snap():
        import autonomy

        return autonomy.snapshot()

    data = _safe(snap, {})
    today = datetime.now().date()
    entries = []
    for entry in data.get("log", []):
        try:
            if datetime.fromtimestamp(entry["ts"]).date() == today:
                entries.append(entry)
        except Exception:
            continue
    return len(entries), entries[:4]


def _brew_today():
    def snap():
        import brew

        return brew.snapshot()

    data = _safe(snap, {})
    today = data.get("today") or {}
    recent = data.get("recent") or []
    return today, (recent[0] if recent else None)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def render_daily(now=None) -> Image:
    now = now or datetime.now()
    sheet = Sheet()
    sheet.header("Daily progress", now.strftime("%a %d %b %Y"))
    sheet.gap(4)

    school = _school_snapshot()
    rows = school.get("lessons") or []
    attended = sum(1 for r in rows if r.get("state") in ("past", "current"))
    actions, action_entries = _autonomy_today()
    brew, last_drink = _brew_today()
    weather = _weather()

    sheet.stats(
        [
            (f"{attended}/{len(rows)}" if rows else "--", "classes"),
            (str(actions), "actions"),
            (str(brew.get("total", 0)), "cups"),
        ]
    )

    sheet.section("School", f"{attended}/{len(rows)} done" if rows else "no data")
    if not rows:
        sheet.write("No lessons scheduled today." if school.get("configured") else "Timetable not configured.", size=13)
    else:
        for row in rows:
            state = row.get("state")
            marker_y = sheet.y + 5
            if state in ("past", "current"):
                sheet.d.rectangle([MARGIN, marker_y, MARGIN + 8, marker_y + 8], fill=0)
                if state == "current":
                    sheet.d.rectangle([MARGIN - 3, marker_y - 3, MARGIN + 11, marker_y + 11], outline=0, width=1)
            else:
                sheet.d.rectangle([MARGIN, marker_y, MARGIN + 8, marker_y + 8], outline=0, width=1)
            sheet.d.text((MARGIN + 16, sheet.y), f"{row.get('start', '')}", font=_font("mono", 12), fill=0)
            title = _fit(sheet.d, row.get("subject", ""), _font("bold", 14), 230)
            sheet.d.text((MARGIN + 72, sheet.y - 1), title, font=_font("bold", 14), fill=0)
            tag = "NOW" if state == "current" else ""
            if tag:
                sheet.d.text((W - MARGIN, sheet.y + 1), tag, font=_font("mono", 10), fill=0, anchor="ra")
            sheet.y += 19
        sheet.gap(2)

    tom = school.get("tomorrow_text")
    if tom:
        sheet.d.text((MARGIN, sheet.y), "TOMORROW", font=_font("mono", 10), fill=0)
        sheet.d.text(
            (MARGIN + 62, sheet.y - 1),
            _fit(sheet.d, tom, _font("bold", 12), W - MARGIN - 62 - MARGIN),
            font=_font("bold", 12),
            fill=0,
        )
        sheet.y += 19

    sheet.section("System actions", f"{actions} today")
    if not action_entries:
        sheet.write("Quiet day. All systems standing by.", size=13)
    else:
        for entry in action_entries:
            stamp = entry.get("time", "")[-5:] or "--:--"
            text = entry.get("text", "") or entry.get("kind", "")
            sheet.d.text((MARGIN, sheet.y), stamp, font=_font("mono", 11), fill=0)
            sheet.d.text(
                (MARGIN + 52, sheet.y - 1),
                _fit(sheet.d, text, _font("bold", 13), W - MARGIN - 52 - MARGIN),
                font=_font("bold", 13),
                fill=0,
            )
            sheet.y += 18
        sheet.gap(2)

    sheet.section("Fuel", f"{brew.get('total', 0)} cups")
    line = f"{brew.get('coffee', 0)} coffee  /  {brew.get('tea', 0)} tea"
    if last_drink:
        line += f"   last: {last_drink.get('label', '')} {last_drink.get('at', '')}"
    sheet.write(line, size=13)

    unit_t = "°C"
    unit_w = "km/h"
    if weather:
        units = weather.get("units") or {}
        unit_t = units.get("temp") or unit_t
        unit_w = units.get("wind") or unit_w
        cur = weather.get("current") or {}
        text = f"{round(cur.get('temp', 0))}{unit_t} · {cur.get('desc', '')} · wind {cur.get('wind_disp', '--')} {unit_w}"
        sheet.section("Skies", cur.get("city", ""))
        sheet.write(text, size=13)

    events = _calendar_items(days=1, limit=2)
    if events:
        sheet.section("Up next", "calendar")
        for event in events:
            when = event["when"] or "all day"
            sheet.d.text((MARGIN, sheet.y), when, font=_font("mono", 11), fill=0)
            sheet.d.text(
                (MARGIN + 52, sheet.y - 1),
                _fit(sheet.d, event["summary"], _font("bold", 13), W - MARGIN - 52 - MARGIN),
                font=_font("bold", 13),
                fill=0,
            )
            sheet.y += 18

    sheet.footer(QUOTES[now.toordinal() % len(QUOTES)], f"daily-{now.date().isoformat()}")
    return sheet.finish()


def _week_days(end_date):
    monday = end_date - timedelta(days=end_date.weekday())
    return [monday + timedelta(days=offset) for offset in range(7)]


def _week_lessons(days):
    def load():
        import school

        return school

    school = _safe(load, None)
    if school is None:
        return [0] * len(days)
    counts = []
    for day in days:
        counts.append(len(_safe(lambda d=day: school._lessons(d.strftime("%A")), [])))
    return counts


def _week_subjects(days):
    def load():
        import school

        return school

    school = _safe(load, None)
    if school is None:
        return []
    tally = {}
    for day in days:
        for slot in _safe(lambda d=day: school._lessons(d.strftime("%A")), []):
            tally[slot["subject"]] = tally.get(slot["subject"], 0) + 1
    return sorted(tally.items(), key=lambda kv: kv[1], reverse=True)


def _week_autonomy(monday):
    def snap():
        import autonomy

        return autonomy.snapshot()

    data = _safe(snap, {})
    cutoff = datetime.combine(monday, datetime.min.time()).timestamp()
    entries = []
    for entry in data.get("log", []):
        try:
            if float(entry.get("ts") or 0) >= cutoff:
                entries.append(entry)
        except Exception:
            continue
    return entries


def _week_drinks(monday):
    def snap():
        import brew

        return brew.STATE, brew.LOCK, brew.MENU

    STATE_B, LOCK_B, MENU = _safe(snap, ({}, None, {}))
    cutoff = datetime.combine(monday, datetime.min.time()).timestamp()
    history = []
    try:
        with LOCK_B:
            history = [dict(item) for item in STATE_B.get("history", []) if item.get("ts", 0) >= cutoff]
    except Exception:
        history = []
    coffee = sum(1 for h in history if h.get("kind") == "coffee")
    tea = sum(1 for h in history if h.get("kind") == "tea")
    tally = {}
    for item in history:
        label = item.get("label") or item.get("drink") or "drink"
        tally[label] = tally.get(label, 0) + 1
    top = max(tally.items(), key=lambda kv: kv[1]) if tally else None
    return {"total": len(history), "coffee": coffee, "tea": tea, "top": top}


def render_weekly(now=None) -> Image:
    now = now or datetime.now()
    today = now.date()
    days = _week_days(today)
    monday = days[0]
    sheet = Sheet()
    sheet.header("Week in review", f"W{now.isocalendar()[1]:02d} · {monday.strftime('%d %b')} – {today.strftime('%d %b')}")
    sheet.gap(2)

    counts = _week_lessons(days)
    total_lessons = sum(counts)
    actions = _week_autonomy(monday)
    drinks = _week_drinks(monday)

    sheet.stats(
        [
            (str(total_lessons), "lessons"),
            (str(len(actions)), "actions"),
            (str(drinks["total"]), "cups"),
        ]
    )

    # -- week chart -------------------------------------------------------
    sheet.section("Week at a glance", "periods / day")
    area_left = MARGIN
    area_right = W - MARGIN
    chart_h = 74
    base_y = sheet.y + chart_h
    sheet.d.line([area_left, base_y, area_right, base_y], fill=0, width=2)
    peak = max(counts) or 1
    slot = (area_right - area_left) / 7
    bar_w = 22
    for index, count in enumerate(counts):
        cx = area_left + slot * index + slot / 2
        bar_h = int((chart_h - 16) * count / peak)
        x0 = int(cx - bar_w / 2)
        x1 = int(cx + bar_w / 2)
        if count:
            sheet.d.rectangle([x0, base_y - bar_h, x1, base_y - 1], fill=0)
        sheet.d.text((cx, base_y - bar_h - 13), str(count), font=_font("mono", 9), fill=0, anchor="ma")
        label = days[index].strftime("%a")[0:2].upper()
        sheet.d.text((cx, base_y + 6), label, font=_font("mono", 10), fill=0, anchor="ma")
    sheet.y = base_y + 22

    subjects = _week_subjects([d for d in days if d.weekday() < 5])
    if subjects:
        sheet.section("Subjects", f"{len(subjects)} in rotation")
        for subject, count in subjects[:5]:
            name = _fit(sheet.d, subject, _font("bold", 13), 270)
            sheet.d.text((MARGIN, sheet.y - 1), name, font=_font("bold", 13), fill=0)
            sheet.d.text((W - MARGIN, sheet.y), f"x{count}", font=_font("mono", 12), fill=0, anchor="ra")
            sheet.y += 18
        sheet.gap(2)

    sheet.section("Fuel", "week total")
    line = f"{drinks['coffee']} coffee / {drinks['tea']} tea"
    sheet.write(line, size=13)
    if drinks["top"]:
        label, count = drinks["top"]
        sheet.write(f"Most brewed: {label} x{count}", size=13)

    if actions:
        sheet.section("Autonomy", f"{len(actions)} actions")
        kinds = {}
        for entry in actions:
            kind = entry.get("kind") or "task"
            kinds[kind] = kinds.get(kind, 0) + 1
        kind_line = "  ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)[:4])
        sheet.write(kind_line, size=13)
        latest = actions[0]
        sheet.write(f"Latest: {latest.get('text', '')}", size=12, gap=1)
        sheet.gap(2)

    # -- next week outlook ------------------------------------------------
    next_monday = monday + timedelta(days=7)
    next_days = [next_monday + timedelta(days=offset) for offset in range(5)]
    next_counts = _week_lessons(next_days)
    next_total = sum(next_counts)

    def first_lesson():
        import school

        slots = school._lessons("Monday")
        return slots[0] if slots else None

    first = _safe(first_lesson, None)
    sheet.section("Next week", f"{next_total} periods")
    if first:
        sheet.write(f"Monday starts with {first['subject']} at {first['start']}.", size=13)
    else:
        sheet.write("No lessons loaded for next week.", size=13)

    sheet.footer(WEEK_QUOTES[now.isocalendar()[1] % len(WEEK_QUOTES)], f"weekly-{_week_key(today)}")
    return sheet.finish()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _preview_paths(kind: str, now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    history = os.path.join(RECEIPT_DIR, f"receipt-{kind}-{stamp}.png")
    latest = os.path.join(RECEIPT_DIR, f"latest-{kind}.png")
    return history, latest


def preview(kind: str = "daily", now=None) -> str:
    """Render a receipt PNG and return its path. Never touches the printer."""
    if not PIL_OK:
        raise RuntimeError("Pillow is not installed")
    kind = "weekly" if kind == "weekly" else "daily"
    if not LOCK.acquire(timeout=2.0):
        existing = os.path.join(RECEIPT_DIR, f"latest-{kind}.png")
        if os.path.exists(existing):
            return existing
        raise RuntimeError("Printer is busy")
    try:
        image = render_weekly(now) if kind == "weekly" else render_daily(now)
        history, latest = _preview_paths(kind, now)
        image.save(history, "PNG")
        image.save(latest, "PNG")
        return latest
    finally:
        LOCK.release()


def print_receipt(kind: str = "daily", now=None) -> tuple[bool, str]:
    """Render and print a receipt. Returns (ok, human readable result)."""
    kind = "weekly" if str(kind).lower() in ("weekly", "week") else "daily"
    if not PIL_OK:
        return False, "Receipt renderer unavailable: Pillow is not installed."
    if not LOCK.acquire(blocking=False):
        return False, "The printer is busy with another receipt."
    STATUS["busy"] = True
    try:
        now = now or datetime.now()
        image = render_weekly(now) if kind == "weekly" else render_daily(now)
        history, latest = _preview_paths(kind, now)
        image.save(history, "PNG")
        image.save(latest, "PNG")
        try:
            battery, _, rows_per_write, feed_s, total_s = _run_print(image)
        except Exception as exc:
            STATUS["error"] = str(exc)
            STATE["last_result"] = f"{kind}: failed - {exc}"
            STATE["last_kind"] = kind
            STATE["last_ts"] = time.time()
            _save_state()
            return False, f"{kind.capitalize()} receipt rendered, but printing failed: {exc}"
        STATE["last_result"] = f"{kind}: printed"
        STATE["last_kind"] = kind
        STATE["last_ts"] = time.time()
        if kind == "weekly":
            STATE["last_weekly"] = _week_key(now.date())
        else:
            STATE["last_daily"] = now.date().isoformat()
        _save_state()
        STATUS["error"] = ""
        message = (
            f"{kind.capitalize()} receipt printed in {total_s:.0f}s "
            f"({image.height} lines, intensity {INTENSITY}, feed {feed_s:.0f}s with {rows_per_write}-row BLE writes)"
        )
        if battery is not None and 0 < battery <= 100:
            message += f", battery {battery}%"
            if battery < 25:
                message += ". Battery is low; charge the printer for darker prints"
        message += f". Preview: logs/receipts/{os.path.basename(history)}"
        return True, message
    finally:
        STATUS["busy"] = False
        LOCK.release()


def status_text() -> str:
    _load_state()
    now = datetime.now()
    lines = [f"Printer: MXW01 ({DEVICE_MAC})", f"Scheduler: {'on' if STATUS['scheduler'] else 'off'}"]
    lines.append(f"Darkness: intensity {INTENSITY}/255, threshold {THRESHOLD}" + (", bold" if BOLD else ""))
    lines.append(f"Daily: last {STATE['last_daily'] or 'never'}; prints every evening at {PRINT_HOUR:02d}:00")
    lines.append(f"Weekly: last {STATE['last_weekly'] or 'never'}; prints Sundays at {PRINT_HOUR:02d}:00")
    if STATE["last_result"]:
        lines.append(f"Last run: {STATE['last_result']}")
    if STATUS["error"]:
        lines.append(f"Last error: {STATUS['error']}")
    return ". ".join(lines) + "."


def status() -> dict:
    _load_state()
    return {
        "configured": True,
        "mac": DEVICE_MAC,
        "renderer": PIL_OK,
        "busy": STATUS["busy"],
        "scheduler": STATUS["scheduler"],
        "last_daily": STATE["last_daily"],
        "last_weekly": STATE["last_weekly"],
        "last_result": STATE["last_result"],
        "last_ts": STATE["last_ts"],
        "error": STATUS["error"],
    }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def _due(kind: str, now: datetime) -> bool:
    if now.hour < PRINT_HOUR or now.hour >= PRINT_HOUR + PRINT_WINDOW_HOURS:
        return False
    if kind == "weekly":
        if now.weekday() != 6:
            return False
        return STATE["last_weekly"] != _week_key(now.date())
    return STATE["last_daily"] != now.date().isoformat()


_NEXT_TRY = {"daily": 0.0, "weekly": 0.0}


def _loop() -> None:
    STATUS["scheduler"] = True
    _load_state()
    while True:
        try:
            _load_state()
            now = datetime.now()
            jobs = []
            if _due("daily", now) and time.time() >= _NEXT_TRY["daily"]:
                jobs.append("daily")
            if _due("weekly", now) and time.time() >= _NEXT_TRY["weekly"]:
                jobs.append("weekly")
            for kind in jobs:
                ok, message = print_receipt(kind)
                print(f"[printer] {message}", flush=True)
                _NEXT_TRY[kind] = 0.0 if ok else time.time() + 600
        except Exception as exc:
            STATUS["error"] = str(exc)
            print(f"[printer] scheduler error: {exc}", flush=True)
        time.sleep(40)


def start_scheduler() -> bool:
    if STATUS["scheduler"]:
        return False
    threading.Thread(target=_loop, name="printer-schedule", daemon=True).start()
    return True


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--status" in args:
        print(status_text())
    else:
        kind = "weekly" if "--weekly" in args else "daily"
        if "--render" in args:
            print(f"Preview saved: {preview(kind)}")
        else:
            ok, message = print_receipt(kind)
            print(message)
            sys.exit(0 if ok else 1)
