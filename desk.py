"""
Desktop control for J.A.R.V.I.S.: see the screen, move the mouse, type, press shortcuts,
open whitelisted apps and switch windows (pyautogui + pygetwindow, Windows-friendly).

Disabled unless the owner opts in:
    DESKTOP_CONTROL=true       enables the mouse/keyboard/app/window tools
    DESKTOP_SCREENSHOTS=true   allows screenshot capture for the model
    DESKTOP_APPS=steam,discord You can extend the app whitelist (comma list, or Name=command)
    DESKTOP_SCREEN_MAX=1600    Width the screenshot is downscaled to before sending to Gemini

Safety rails:
  * pyautogui FAILSAFE: slam the mouse into a screen corner to abort any running action.
  * Only whitelisted apps can be launched; nothing here can delete files or kill processes.
  * Screenshots capture the PRIMARY monitor only, and are sent to Google's Gemini API.
"""

import io
import os
import re
import time
import platform
import subprocess
import webbrowser
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

try:
    import pyautogui
    from PIL import Image, ImageDraw

    pyautogui.FAILSAFE = True  # moving the mouse into a corner aborts the current action
    pyautogui.PAUSE = 0.05
except Exception:  # the rest of Jarvis keeps working without desktop control
    pyautogui = None
    Image = ImageDraw = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    import pyperclip
except ImportError:
    pyperclip = None


def _flag(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


CONTROL = _flag("DESKTOP_CONTROL", "false")
SCREENSHOTS = _flag("DESKTOP_SCREENSHOTS", "false")
SCREEN_MAX_WIDTH = int(os.getenv("DESKTOP_SCREEN_MAX", "1600") or "1600")

BUILTIN_APPS = {
    "notepad": "notepad",
    "wordpad": "wordpad",
    "paint": "mspaint",
    "mspaint": "mspaint",
    "calculator": "calc",
    "calc": "calc",
    "explorer": "explorer",
    "files": "explorer",
    "file explorer": "explorer",
    "cmd": "cmd",
    "terminal": "wt",
    "powershell": "powershell",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
}
APP_NAME_RE = re.compile(r"[A-Za-z0-9 ._+\-]{1,60}")


def _load_apps():
    apps = dict(BUILTIN_APPS)
    for chunk in re.split(r"[;,]", os.getenv("DESKTOP_APPS", "")):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            name, command = [part.strip() for part in chunk.split("=", 1)]
        else:
            name = command = chunk
        if name and APP_NAME_RE.fullmatch(command):
            apps[name.lower()] = command
    return apps


APPS = _load_apps()


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------
def available():
    return pyautogui is not None and CONTROL


def screenshots_enabled():
    return pyautogui is not None and SCREENSHOTS


def unavailable_reason():
    if pyautogui is None:
        return "The desktop libraries are missing. Run: pip install -r requirements.txt"
    if not CONTROL:
        return "Desktop control is off. Set DESKTOP_CONTROL=true in .env and restart the server."
    return ""


def screen_size():
    if pyautogui is None:
        raise RuntimeError(unavailable_reason())
    width, height = pyautogui.size()
    return int(width), int(height)


# --------------------------------------------------------------------------
# Screen capture
# --------------------------------------------------------------------------
def _draw_grid(img):
    draw = ImageDraw.Draw(img)
    width, height = img.size
    for x in range(200, width, 200):
        draw.line([(x, 0), (x, height)], fill=(80, 80, 100), width=1)
        draw.text((x + 2, 2), str(x), fill=(255, 120, 120))
    for y in range(200, height, 200):
        draw.line([(0, y), (width, y)], fill=(80, 80, 100), width=1)
        draw.text((2, y + 1), str(y), fill=(255, 120, 120))


def screenshot_jpeg(quality=70, grid=True):
    """Live JPEG of the primary monitor, optionally with faint pixel labels, downscaled for the model."""
    if not screenshots_enabled():
        raise RuntimeError("Screen capture is off. Set DESKTOP_SCREENSHOTS=true in .env and restart the server.")
    img = pyautogui.screenshot().convert("RGB")
    if grid:
        _draw_grid(img)
    if img.width > SCREEN_MAX_WIDTH:
        ratio = SCREEN_MAX_WIDTH / img.width
        img = img.resize((SCREEN_MAX_WIDTH, max(1, round(img.height * ratio))), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Mouse
# --------------------------------------------------------------------------
def _clamp(x, y):
    width, height = screen_size()
    return max(0, min(int(x), width - 1)), max(0, min(int(y), height - 1))


def move_mouse(x, y):
    if not available():
        raise RuntimeError(unavailable_reason())
    x, y = _clamp(x, y)
    pyautogui.moveTo(x, y, duration=0.2)
    return f"Mouse moved to {x},{y}"


def click_mouse(x=None, y=None, button="left", clicks=1):
    if not available():
        raise RuntimeError(unavailable_reason())
    side = str(button or "left").strip().lower()
    if side not in ("left", "right", "middle"):
        raise ValueError("button must be left, right or middle")
    try:
        times = max(1, min(int(clicks or 1), 3))
    except (TypeError, ValueError):
        times = 1
    if x is None or y is None or int(x) < 0 or int(y) < 0:
        pyautogui.click(button=side, clicks=times, interval=0.15)
        return f"{times} {side} click at the current pointer position"
    x, y = _clamp(x, y)
    pyautogui.click(x, y, clicks=times, interval=0.15, button=side)
    return f"{times} {side} click at {x},{y}"


def scroll_mouse(amount):
    if not available():
        raise RuntimeError(unavailable_reason())
    try:
        value = max(-5000, min(int(amount), 5000))
    except (TypeError, ValueError):
        raise ValueError("amount must be a whole number")
    if value == 0:
        raise ValueError("amount cannot be 0")
    pyautogui.scroll(value)
    return f"Scrolled {'up' if value > 0 else 'down'} by {abs(value)}"


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------
def type_text(text):
    if not available():
        raise RuntimeError(unavailable_reason())
    value = str(text or "")
    if not value:
        raise ValueError("Nothing to type")
    if len(value) > 5000:
        raise ValueError("Text is too long; type it in smaller pieces")
    if value.isascii() or pyperclip is None:
        pyautogui.write(value, interval=0.02)
    else:
        # Non-ASCII (Turkish letters and so on) cannot be typed with key presses reliably.
        pyperclip.copy(value)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
    return f"Typed {len(value)} characters"


def press_keys(keys):
    if not available():
        raise RuntimeError(unavailable_reason())
    parts = [part.strip().lower() for part in re.split(r"[+,]", str(keys or "")) if part.strip()]
    if not parts:
        raise ValueError("No keys given, for example 'enter' or 'ctrl+tab'")
    if len(parts) > 4:
        raise ValueError("At most four keys can be combined")
    for part in parts:
        if part not in pyautogui.KEYBOARD_KEYS:
            raise ValueError(f"Unknown key '{part}'. Examples: enter, esc, tab, ctrl+tab, alt+tab, ctrl+w")
    if len(parts) == 1:
        pyautogui.press(parts[0])
    else:
        pyautogui.hotkey(*parts)
    return "Pressed " + "+".join(parts)


# --------------------------------------------------------------------------
# Apps and windows
# --------------------------------------------------------------------------
def open_app(name):
    if not available():
        raise RuntimeError(unavailable_reason())
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("Which app should I open?")
    command = APPS.get(key)
    if command is None:
        candidates = [known for known in APPS if key in known or known in key]
        if len(candidates) == 1:
            key = candidates[0]
            command = APPS[key]
    if command is None:
        raise ValueError(f"'{name}' is not on the app whitelist. Allowed: {', '.join(sorted(APPS))}")
    if platform.system() == "Windows":
        os.startfile(command)  # ShellExecute resolves registered apps such as chrome and calc
    else:
        subprocess.Popen([command], start_new_session=True)
    return f"Opened {key}"


def open_urls(urls):
    """Open up to six http/https pages as new browser tabs."""
    if not CONTROL:
        raise RuntimeError(unavailable_reason())
    items = urls if isinstance(urls, (list, tuple)) else re.split(r"[\n,]+", str(urls or ""))
    cleaned = []
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        if "://" not in value:
            value = "https://" + value
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Not a web address: {item}")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValueError("No web addresses given")
    if len(cleaned) > 6:
        raise ValueError("At most six tabs at once")
    opened = []
    failed = []
    for url in cleaned:
        try:
            if webbrowser.open_new_tab(url) is False:
                failed.append(url)
            else:
                opened.append(url)
        except Exception:  # noqa: BLE001
            failed.append(url)
        time.sleep(0.35)
    if not opened:
        raise RuntimeError("The browser did not accept any of the addresses")
    summary = f"Opened {len(opened)} tab(s): " + ", ".join(urlparse(u).netloc for u in opened)
    if failed:
        summary += f". Could not open: {', '.join(failed)}"
    return summary


def list_windows():
    if gw is None:
        raise RuntimeError("pygetwindow is missing. Run: pip install -r requirements.txt")
    titles = []
    for title in gw.getAllTitles():
        title = str(title or "").strip()
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return "No titled windows are open."
    return "Open windows:\n" + "\n".join(f"- {title}" for title in titles[:25])


def focus_window(title):
    if gw is None:
        raise RuntimeError("pygetwindow is missing. Run: pip install -r requirements.txt")
    needle = str(title or "").strip().lower()
    if not needle:
        raise ValueError("Which window? Give part of its title.")
    matches = [win for win in gw.getAllWindows() if win.title and needle in win.title.lower()]
    if not matches:
        raise ValueError(f"No window title contains '{title}'. Use list_windows to see what is open.")
    win = matches[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        # Some Windows apps refuse activation; retrying once usually works.
        try:
            win.minimize()
            win.restore()
            win.activate()
        except Exception:
            raise RuntimeError(f"Could not focus '{win.title}': {exc}") from exc
    return f"Focused '{win.title}'"
