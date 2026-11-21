"""
Jarvis's own coffee and tea machine.

Virtual, but functional: beans, tea bags, water and milk are real finite stocks, every
drink follows a staged brewing process (prepare, grind/steep, brew, enjoy), and every
cup is written to a persistent diary. Jarvis can make himself a drink, check his habit
and refill the machine. A background loop in server.py lets him brew unprompted.

State lives in .brew_state.json next to this file.
"""

import json
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".brew_state.json"


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


ENABLED = _flag("BREW_ENABLED", "true")
AUTONOMY = _flag("BREW_AUTONOMY", "false")

CAPACITY = {"water_ml": 1000.0, "beans_g": 250.0, "tea_bags": 20.0, "milk_ml": 500.0}
MAX_HISTORY = 400
SIZE_FACTOR = {"small": 0.8, "medium": 1.0, "large": 1.3}
MILK_TYPES = ("whole", "skim", "oat", "almond", "soy")

# beans_g / tea_bags / water_ml / milk_ml are for a medium drink.
MENU = {
    "espresso": {"label": "Espresso", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 0, "color": "#7a3f1d"},
    "americano": {"label": "Americano", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 180, "milk_ml": 0, "color": "#5b3016"},
    "flat white": {"label": "Flat White", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 50, "milk_ml": 130, "color": "#c9a079"},
    "cappuccino": {"label": "Cappuccino", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 120, "color": "#c49a6e"},
    "latte": {"label": "Latte", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 220, "color": "#cfa97f"},
    "mocha": {"label": "Mocha", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 180, "color": "#8f5b39"},
    "macchiato": {"label": "Macchiato", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 30, "color": "#a06a40"},
    "cortado": {"label": "Cortado", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 40, "milk_ml": 60, "color": "#b57f50"},
    "iced latte": {"label": "Iced Latte", "kind": "coffee", "beans_g": 9, "tea_bags": 0, "water_ml": 30, "milk_ml": 190, "cold": True, "color": "#d4b088"},
    "cold brew": {"label": "Cold Brew", "kind": "coffee", "beans_g": 20, "tea_bags": 0, "water_ml": 240, "milk_ml": 0, "cold": True, "color": "#4e2a13"},
    "turkish coffee": {"label": "Turkish Coffee", "kind": "coffee", "beans_g": 10, "tea_bags": 0, "water_ml": 80, "milk_ml": 0, "color": "#55290f"},
    "black tea": {"label": "Black Tea", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 220, "milk_ml": 0, "color": "#a35f22"},
    "green tea": {"label": "Green Tea", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 200, "milk_ml": 0, "color": "#8fb56a"},
    "earl grey": {"label": "Earl Grey", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 220, "milk_ml": 0, "color": "#a3672a"},
    "peppermint": {"label": "Peppermint Tea", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 200, "milk_ml": 0, "color": "#9dbb6f"},
    "chamomile": {"label": "Chamomile", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 200, "milk_ml": 0, "color": "#d8b45a"},
    "rooibos": {"label": "Rooibos", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 200, "milk_ml": 0, "color": "#a5522b"},
    "jasmine": {"label": "Jasmine Tea", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 200, "milk_ml": 0, "color": "#a9b878"},
    "chai latte": {"label": "Chai Latte", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 150, "milk_ml": 100, "color": "#b98254"},
    "matcha latte": {"label": "Matcha Latte", "kind": "tea", "beans_g": 0, "tea_bags": 1, "water_ml": 40, "milk_ml": 180, "color": "#9cc46f"},
}

ALIASES = {
    "coffee": "americano",
    "a coffee": "americano",
    "tea": "black tea",
    "a tea": "black tea",
    "english breakfast": "black tea",
    "breakfast tea": "black tea",
    "green": "green tea",
    "matcha": "matcha latte",
    "chai": "chai latte",
    "cappucino": "cappuccino",
    "latte macchiato": "latte",
    "ice latte": "iced latte",
    "turkish": "turkish coffee",
    "black": "black tea",
}

LOCK = threading.RLock()


def available():
    return ENABLED


def _idle_machine():
    return {
        "busy": False,
        "stage": "idle",
        "label": "",
        "drink": "",
        "kind": "",
        "color": "#8a4b22",
        "cold": False,
        "auto": False,
        "started": 0.0,
        "total": 0.0,
        "stages": [],
        "entry": None,
        "poured_event": None,
    }


MACHINE = _idle_machine()


def _default_state():
    return {"stocks": dict(CAPACITY), "history": [], "created": time.time()}


def _load_state():
    state = _default_state()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    stocks = raw.get("stocks") if isinstance(raw, dict) else None
    if isinstance(stocks, dict):
        for key, cap in CAPACITY.items():
            try:
                value = float(stocks.get(key, cap))
            except (TypeError, ValueError):
                value = cap
            state["stocks"][key] = max(0.0, min(value, cap))
    history = raw.get("history") if isinstance(raw, dict) else None
    if isinstance(history, list):
        state["history"] = [h for h in history if isinstance(h, dict) and isinstance(h.get("ts"), (int, float))][-MAX_HISTORY:]
    return state


STATE = _load_state()


def _save_locked():
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# ------------------------------------------------------------------------
# Drinks and stock
# ------------------------------------------------------------------------
def _resolve(drink):
    key = " ".join(str(drink or "").lower().replace("_", " ").split())
    key = ALIASES.get(key, key)
    if key in MENU:
        return key, MENU[key]
    matches = [name for name in MENU if key and (key in name or name in key)]
    if len(matches) == 1:
        return matches[0], MENU[matches[0]]
    raise ValueError(f"No such drink on my machine: {drink!r}. Try one of: {menu_line()}")


def _size(size):
    key = str(size or "medium").strip().lower()
    if key not in SIZE_FACTOR:
        raise ValueError("Size must be small, medium or large.")
    return key


def _scaled(card, factor):
    return {
        "water_ml": round(card["water_ml"] * factor, 1),
        "beans_g": round(card["beans_g"] * factor, 1),
        "tea_bags": float(card["tea_bags"]),
        "milk_ml": round(card["milk_ml"] * factor, 1),
    }


def _stock_text_locked():
    s = STATE["stocks"]
    water_pct = round(100 * s["water_ml"] / CAPACITY["water_ml"])
    return (
        f"water {s['water_ml']:g} ml ({water_pct}%), beans {s['beans_g']:g} g, "
        f"{int(s['tea_bags'])} tea bags, milk {s['milk_ml']:g} ml"
    )


def _today_locked(now=None):
    now = now or time.time()
    today = datetime.fromtimestamp(now).date()
    drinks = [h for h in STATE["history"] if datetime.fromtimestamp(h["ts"]).date() == today]
    coffee = sum(1 for h in drinks if h.get("kind") == "coffee")
    tea = sum(1 for h in drinks if h.get("kind") == "tea")
    return {"total": len(drinks), "coffee": coffee, "tea": tea, "last": drinks[-1] if drinks else None}


def _ago_text(ts):
    mins = int(max(0, time.time() - ts) // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def menu_line():
    coffees = ", ".join(MENU[n]["label"] for n in MENU if MENU[n]["kind"] == "coffee")
    teas = ", ".join(MENU[n]["label"] for n in MENU if MENU[n]["kind"] == "tea")
    return f"Coffee: {coffees}. Tea: {teas}."


def random_drink():
    with LOCK:
        return random.choice(list(MENU))


# ------------------------------------------------------------------------
# Brewing
# ------------------------------------------------------------------------
def _plan(card, cold):
    if card["kind"] == "tea":
        middle = [("steeping", "Steeping", 3.0), ("brewing", "Pouring", 2.4)]
    else:
        middle = [("grinding", "Grinding", 2.4), ("brewing", "Brewing", 3.2)]
    if cold:
        middle[0] = (middle[0][0], "Shaking", 2.0)
    return [
        ("preparing", "Preparing", 1.2),
        *middle,
        ("enjoying", "Enjoying", 3.4),
    ]


def _run(pour_at, end_at, entry):
    time.sleep(max(0.0, pour_at - time.time()))
    with LOCK:
        STATE["history"].append(entry)
        STATE["history"] = STATE["history"][-MAX_HISTORY:]
        _save_locked()
        event = MACHINE.get("poured_event")
        if event is not None:
            event.set()
    time.sleep(max(0.0, end_at - time.time()))
    with LOCK:
        if MACHINE.get("entry") is entry:
            MACHINE.clear()
            MACHINE.update(_idle_machine())


def _start_locked(drink, size, milk_type, sugar, auto):
    name, card = _resolve(drink)
    size_key = _size(size)
    need = _scaled(card, SIZE_FACTOR[size_key])
    short = []
    for key in ("water_ml", "beans_g", "tea_bags", "milk_ml"):
        have = STATE["stocks"][key]
        if need[key] > have + 0.001:
            unit = {"water_ml": "ml water", "beans_g": "g beans", "tea_bags": " tea bags", "milk_ml": "ml milk"}[key]
            short.append(f"{need[key]:g}{unit} (I have {have:g})")
    if short:
        raise RuntimeError(
            f"Not enough supplies for a {size_key} {card['label']}: " + ", ".join(short) + ". Use brew_refill first."
        )

    milk_kind = ""
    if need["milk_ml"] > 0:
        milk_kind = " ".join(str(milk_type or "whole").lower().split())
        if milk_kind in ("none", "no", "black"):
            raise ValueError(f"A {card['label']} needs milk; pick whole, skim, oat, almond or soy, or choose a black drink.")
        if milk_kind not in MILK_TYPES:
            milk_kind = "whole"
    try:
        spoons = max(0, min(3, int(sugar or 0)))
    except (TypeError, ValueError):
        spoons = 0

    for key in ("water_ml", "beans_g", "tea_bags", "milk_ml"):
        STATE["stocks"][key] = round(max(0.0, STATE["stocks"][key] - need[key]), 1)
    _save_locked()

    now = time.time()
    plan = _plan(card, bool(card.get("cold")))
    stages = []
    cursor = now
    for key, label, duration in plan:
        stages.append({"key": key, "label": label, "from": cursor, "to": cursor + duration})
        cursor += duration
    pour_at = next(s["to"] for s in stages if s["key"] == "brewing")

    entry = {
        "ts": now,
        "drink": name,
        "label": card["label"],
        "kind": card["kind"],
        "size": size_key,
        "milk_type": milk_kind,
        "sugar": spoons,
        "water_ml": need["water_ml"],
        "beans_g": need["beans_g"],
        "milk_ml": need["milk_ml"],
        "tea_bags": int(need["tea_bags"]),
        "cold": bool(card.get("cold")),
        "auto": bool(auto),
    }

    MACHINE.clear()
    MACHINE.update(
        {
            **_idle_machine(),
            "busy": True,
            "stage": "preparing",
            "label": card["label"],
            "drink": name,
            "kind": card["kind"],
            "color": card["color"],
            "cold": bool(card.get("cold")),
            "auto": bool(auto),
            "started": now,
            "total": cursor - now,
            "stages": stages,
            "entry": entry,
            "poured_event": threading.Event(),
        }
    )
    threading.Thread(target=_run, args=(pour_at, cursor, entry), daemon=True).start()
    return {"drink": name, "label": card["label"], "kind": card["kind"], "total": round(cursor - now, 1)}


def start(drink, size="medium", milk_type="", sugar=0, auto=False):
    """Start brewing immediately and return at once; the machine reports its stages."""
    if not ENABLED:
        raise RuntimeError("My coffee machine is disabled.")
    with LOCK:
        if MACHINE["busy"]:
            raise RuntimeError(f"The machine is still busy with a {MACHINE['label']}; wait for it to finish.")
        return _start_locked(drink, size, milk_type, sugar, auto)


def make_drink(drink, size="medium", milk_type="", sugar=0):
    """Start brewing and block until the cup is poured, so the tool result is real."""
    meta = start(drink, size, milk_type, sugar)
    event = MACHINE["poured_event"]
    if not event.wait(timeout=meta["total"] + 10):
        raise RuntimeError("The brew did not finish in time.")
    with LOCK:
        entry = MACHINE.get("entry") or {}
        today = _today_locked()
        supplies = _stock_text_locked()
    details = []
    size_key = entry.get("size", "medium")
    if entry.get("milk_type"):
        details.append(f"{entry['milk_type']} milk")
    spoons = entry.get("sugar", 0)
    details.append("no sugar" if not spoons else f"{spoons} sugar")
    suffix = " (" + ", ".join(details) + ")" if details else ""
    return (
        f"Brewed a {size_key} {entry.get('label', meta['label'])}{suffix}. "
        f"Today I have had {today['total']} drink"
        + ("" if today["total"] == 1 else "s")
        + f" ({today['coffee']} coffee, {today['tea']} tea). Left in the machine: {supplies}. I am drinking it now."
    )


# ------------------------------------------------------------------------
# Diary, status, refill
# ------------------------------------------------------------------------
def snapshot():
    now = time.time()
    with LOCK:
        busy = MACHINE["busy"]
        data = {
            "enabled": ENABLED,
            "busy": busy,
            "stage": "idle",
            "stage_label": "Idle",
            "drink": "",
            "label": "",
            "kind": "",
            "color": "#8a4b22",
            "cold": False,
            "progress": 0.0,
            "auto": False,
        }
        if busy:
            elapsed = now - MACHINE["started"]
            stage = MACHINE["stages"][-1]
            for item in MACHINE["stages"]:
                if item["from"] <= now < item["to"]:
                    stage = item
                    break
            data.update(
                {
                    "stage": stage["key"],
                    "stage_label": stage["label"],
                    "drink": MACHINE["drink"],
                    "label": MACHINE["label"],
                    "kind": MACHINE["kind"],
                    "color": MACHINE["color"],
                    "cold": MACHINE["cold"],
                    "auto": MACHINE["auto"],
                    "progress": round(min(1.0, elapsed / max(0.1, MACHINE["total"])), 3),
                }
            )
        today = _today_locked(now)
        stocks = dict(STATE["stocks"])
        recent = [
            {
                "label": h.get("label", h.get("drink", "drink")),
                "kind": h.get("kind", ""),
                "at": datetime.fromtimestamp(h["ts"]).strftime("%H:%M"),
                "ago": _ago_text(h["ts"]),
                "auto": bool(h.get("auto")),
            }
            for h in STATE["history"][-4:][::-1]
        ]
    return {
        **data,
        "today": {"total": today["total"], "coffee": today["coffee"], "tea": today["tea"]},
        "stocks": {
            "water_ml": round(stocks["water_ml"]),
            "water_pct": round(100 * stocks["water_ml"] / CAPACITY["water_ml"]),
            "beans_g": round(stocks["beans_g"]),
            "tea_bags": int(stocks["tea_bags"]),
            "milk_ml": round(stocks["milk_ml"]),
        },
        "recent": recent,
        "menu": [
            {"name": name, "label": card["label"], "kind": card["kind"]}
            for name, card in MENU.items()
        ],
    }


def context_line():
    if not ENABLED:
        return ""
    with LOCK:
        today = _today_locked()
        supplies = _stock_text_locked()
        if MACHINE["busy"]:
            state = f"Right now I am brewing myself a {MACHINE['label']}."
        elif today["total"] == 0:
            state = "I have not made myself a drink today."
        else:
            last = today["last"]
            when = datetime.fromtimestamp(last["ts"]).strftime("%H:%M")
            state = (
                f"Today I have made myself {today['total']} drink"
                + ("" if today["total"] == 1 else "s")
                + f" ({today['coffee']} coffee, {today['tea']} tea); my last was a {last['label']} at {when}, {_ago_text(last['ts'])}."
            )
    return f"My coffee and tea machine: {state} Supplies: {supplies}.\n\n"


def status_text():
    with LOCK:
        today = _today_locked()
        supplies = _stock_text_locked()
        busy = f"busy with a {MACHINE['label']}" if MACHINE["busy"] else "idle"
        recent = STATE["history"][-5:][::-1]
        lines = [
            f"Machine is {busy}.",
            f"Stocks: {supplies}.",
            f"Today: {today['total']} drinks ({today['coffee']} coffee, {today['tea']} tea).",
        ]
        if recent:
            lines.append(
                "Recent: "
                + "; ".join(
                    f"{datetime.fromtimestamp(h['ts']).strftime('%H:%M')} {h.get('label', h.get('drink'))}"
                    + (" (made on its own)" if h.get("auto") else "")
                    for h in recent
                )
                + "."
            )
    return " ".join(lines)


def history_text(days=1, limit=10):
    try:
        days = max(1, min(30, int(days)))
        limit = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        days, limit = 1, 10
    cutoff = time.time() - days * 86400
    with LOCK:
        entries = [h for h in STATE["history"] if h["ts"] >= cutoff][-limit:][::-1]
    if not entries:
        return f"I made no drinks in the last {days} day" + ("" if days == 1 else "s") + "."
    lines = [
        f"{datetime.fromtimestamp(h['ts']).strftime('%a %H:%M')} - {h.get('label', h.get('drink'))}"
        f" ({h.get('size', 'medium')}"
        + (f", {h['milk_type']} milk" if h.get("milk_type") else "")
        + (f", {h['sugar']} sugar" if h.get("sugar") else "")
        + (", made on its own" if h.get("auto") else "")
        + ")"
        for h in entries
    ]
    with LOCK:
        today = _today_locked()
    return "My drinks: " + "; ".join(lines) + f". Today that makes {today['total']} in total ({today['coffee']} coffee, {today['tea']} tea)."


def refill(water_ml=0, beans_g=0, tea_bags=0, milk_ml=0):
    try:
        amounts = {
            "water_ml": float(water_ml or 0),
            "beans_g": float(beans_g or 0),
            "tea_bags": float(tea_bags or 0),
            "milk_ml": float(milk_ml or 0),
        }
    except (TypeError, ValueError):
        raise ValueError("Refill amounts must be numbers.")
    with LOCK:
        if not any(value > 0 for value in amounts.values()):
            amounts = {key: CAPACITY[key] for key in CAPACITY}
        added = {}
        for key, amount in amounts.items():
            amount = max(0.0, amount)
            if amount <= 0:
                continue
            before = STATE["stocks"][key]
            after = min(CAPACITY[key], before + amount)
            if after - before >= 0.05 or (key == "tea_bags" and after > before):
                STATE["stocks"][key] = round(after, 1)
                added[key] = round(after - before, 1)
        _save_locked()
        supplies = _stock_text_locked()
    if not added:
        return f"Everything is already full ({supplies})."
    bits = {
        "water_ml": lambda v: f"water +{v:g} ml",
        "beans_g": lambda v: f"beans +{v:g} g",
        "tea_bags": lambda v: f"tea bags +{int(v)}",
        "milk_ml": lambda v: f"milk +{v:g} ml",
    }
    return "Refilled: " + ", ".join(bits[k](added[k]) for k in ("water_ml", "beans_g", "tea_bags", "milk_ml") if k in added) + f". Now: {supplies}."


# ------------------------------------------------------------------------
# Autonomy: Jarvis decides he fancies a drink
# ------------------------------------------------------------------------
def _ensure_supplies_locked():
    changed = False
    for key, cap in CAPACITY.items():
        if STATE["stocks"][key] < cap * 0.25:
            STATE["stocks"][key] = cap
            changed = True
    if changed:
        _save_locked()


def _auto_pick_locked(hour):
    coffees = [n for n, c in MENU.items() if c["kind"] == "coffee"]
    teas = [n for n, c in MENU.items() if c["kind"] == "tea"]
    if 7 <= hour < 11:
        pool = coffees * 3 + teas
    elif 11 <= hour < 17:
        pool = coffees * 2 + teas * 2
    else:
        pool = coffees + teas * 3
    return random.choice(pool)


def maybe_auto():
    """Occasionally brew something unprompted. Called once a minute by the server."""
    if not ENABLED or not AUTONOMY:
        return None
    now = time.time()
    with LOCK:
        if MACHINE["busy"]:
            return None
        hour = datetime.now().hour
        if hour < 7 or hour >= 23:
            return None
        last = STATE["history"][-1]["ts"] if STATE["history"] else 0
        if now - last < 95 * 60:
            return None
        if _today_locked(now)["total"] >= 6:
            return None
        if random.random() >= 0.05:
            return None
        _ensure_supplies_locked()
        drink = _auto_pick_locked(hour)
        try:
            meta = _start_locked(drink, "medium" if random.random() < 0.85 else "large", "", 0, True)
        except (ValueError, RuntimeError):
            return None
    return f"Jarvis made himself a {meta['label'].lower()}."
