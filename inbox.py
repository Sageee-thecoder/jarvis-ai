"""
Read-only access to the OWNER'S OWN mailbox over IMAP.

The mailbox is opened read-only and messages are fetched with BODY.PEEK, so nothing is marked as read,
moved, deleted or sent from here. (Sending from Jarvis's own mailbox lives in server.py.)

.env:
    IMAP_HOST=imap.gmail.com
    IMAP_PORT=993
    IMAP_USER=you@gmail.com        (defaults to OWNER_EMAIL)
    IMAP_PASS=<app password for YOUR mailbox>
"""

import os
import re
import email
import imaplib
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "") or os.getenv("OWNER_EMAIL", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")

_poll_state = {"seen": set(), "baseline": False}


def ready():
    return bool(IMAP_HOST and IMAP_USER and IMAP_PASS)


# --------------------------------------------------------------------------
# Connection helpers
# --------------------------------------------------------------------------
def _connect():
    if not ready():
        raise RuntimeError("Inbox access is not configured: set IMAP_USER and IMAP_PASS in .env")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=20)
        conn.login(IMAP_USER, IMAP_PASS)
        conn.select("INBOX", readonly=True)
        return conn
    except (imaplib.IMAP4.error, OSError) as exc:
        raise RuntimeError(
            "Could not open your mailbox. For Gmail, turn on 2-Step Verification and use an App Password "
            f"in IMAP_PASS. Server said: {str(exc)[:160]}"
        ) from exc


def _close(conn):
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _decode(value):
    try:
        return str(make_header(decode_header(str(value or "")))).replace("\r", " ").replace("\n", " ").strip()
    except Exception:  # noqa: BLE001
        return str(value or "").strip()


def _sender(raw):
    name, address = parseaddr(str(raw or ""))
    return _decode(name) or address or str(raw or "")


def _date(raw):
    try:
        stamp = parsedate_to_datetime(str(raw)).astimezone()
    except Exception:  # noqa: BLE001
        return str(raw or "")[:25]
    if stamp.date() == datetime.now().astimezone().date():
        return stamp.strftime("%H:%M")
    return stamp.strftime("%d %b %H:%M")


def _body_text(msg):
    plain, markup = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (plain if kind == "text/plain" else markup).append(text)
    if plain:
        body = "\n".join(plain)
    elif markup:
        body = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", "\n".join(markup), flags=re.I)
        body = unescape(re.sub(r"<[^>]+>", " ", body))
    else:
        body = ""
    return re.sub(r"\s+", " ", body).strip()


def _fetch_headers(conn, uids):
    if not uids:
        return []
    _, fetched = conn.uid("fetch", ",".join(uids), "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    items = []
    for part in fetched or []:
        if not isinstance(part, tuple):
            continue
        meta = part[0].decode("utf-8", errors="replace")
        uid_match = re.search(r"UID (\d+)", meta)
        if not uid_match:
            continue
        flags_match = re.search(r"FLAGS \(([^)]*)\)", meta)
        flags = flags_match.group(1) if flags_match else ""
        msg = email.message_from_bytes(part[1])
        items.append(
            {
                "uid": uid_match.group(1),
                "unread": "\\Seen" not in flags,
                "from": _sender(msg.get("From", "")),
                "subject": _decode(msg.get("Subject", "")) or "(no subject)",
                "date": _date(msg.get("Date", "")),
            }
        )
    items.sort(key=lambda item: int(item["uid"]), reverse=True)
    return items


def _search_uids(conn, criteria):
    _, data = conn.uid("search", None, criteria)
    raw = data[0].split() if data and data[0] else []
    return [u.decode() for u in raw]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def list_mail(unread_only=True, limit=5, search=""):
    """Newest first. Only the 60 most recent matching emails are considered."""
    conn = _connect()
    try:
        uids = _search_uids(conn, "UNSEEN" if unread_only else "ALL")[-60:]
        items = _fetch_headers(conn, uids)
    finally:
        _close(conn)
    needle = (search or "").strip().lower()
    if needle:
        items = [i for i in items if needle in i["from"].lower() or needle in i["subject"].lower()]
    return items[: max(1, min(int(limit), 15))]


def read_mail(uid):
    uid = re.sub(r"\D", "", str(uid))
    if not uid:
        raise ValueError("That is not a valid email id. Use an id from check_email.")
    conn = _connect()
    try:
        _, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        raw = None
        for part in data or []:
            if isinstance(part, tuple):
                raw = part[1]
                break
    finally:
        _close(conn)
    if raw is None:
        raise RuntimeError("That email was not found.")
    msg = email.message_from_bytes(raw)
    return {
        "from": _sender(msg.get("From", "")),
        "subject": _decode(msg.get("Subject", "")) or "(no subject)",
        "date": _date(msg.get("Date", "")),
        "body": _body_text(msg)[:3000],
    }


def poll_new():
    """Returns a short announcement when new unread mail arrived since the last call, otherwise None.
    The first call only records what is already unread."""
    conn = _connect()
    try:
        uids = _search_uids(conn, "UNSEEN")
        if not _poll_state["baseline"]:
            _poll_state["seen"] = set(uids)
            _poll_state["baseline"] = True
            return None
        new = [u for u in uids if u not in _poll_state["seen"]]
        _poll_state["seen"] = set(uids)
        items = _fetch_headers(conn, new[-3:]) if new else []
    finally:
        _close(conn)
    if not new or not items:
        return None
    latest = items[0]
    if len(new) == 1:
        return f"New email from {latest['from']}: {latest['subject']}."
    return f"You have {len(new)} new emails. The latest is from {latest['from']}: {latest['subject']}."
