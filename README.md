# J.A.R.V.I.S.

A personal, real-time AI assistant that lives across a PC, a phone, and a real phone line.

J.A.R.V.I.S. is a local-first project: a Flask server on your machine does the thinking and the actions, a browser HUD gives it a voice and a face, an Android app lets it take calls, and a LiveKit agent answers inbound SIP calls. It is built for one user (the owner), not as a multi-tenant product.

## What it can do

- **Talk** — speech-to-speech with Gemini Live, a wake phrase, barge-in, and streaming replies.
- **See** — webcam snapshots and presence detection.
- **Act** — WiZ lights, a Samsung TV, Spotify, a thermal receipt printer, a coffee/tea machine, and PC screen/mouse/keyboard control.
- **Know** — live weather, Google Calendar, a school timetable, morning briefings, and autonomous reminders.
- **Communicate** — send email from its own Gmail, read the owner's inbox read-only over IMAP, send and read WhatsApp through a `whatsapp-web.js` bridge, and send Telegram alerts.
- **Answer the phone** — a LiveKit SIP number rings into a Gemini Live voice agent that can run the same tools.
- **Go mobile** — a Kotlin/Compose Android app pairs over the LAN with `PHONE_TOKEN` and shows incoming calls full-screen.
- **Wear the shell** — a Chrome app-mode desktop launcher starts the server and opens the HUD in a frameless app window, so the Web Speech API behaves exactly as it does in Chrome.

## Architecture

```text
Chrome HUD (static/index.html)
        |  HTTP + JSON (127.0.0.1:5000)
        v
Flask server (server.py) -----> Gemini Live / Gemini API
        |
        +--> lights.py, tv.py, spotify.py, printer.py, brew.py, desk.py
        +--> gcal.py, school.py, inbox.py, live_bridge.py
        +--> WhatsApp bridge (wa-bridge/bridge.js, Node)
        +--> Telegram
        ^
        |  LAN + PHONE_TOKEN
Android app (android/)

LiveKit SIP call ---> livekit_agent.py ---> server tools
```

## Quick start

Requirements: Python 3.10+, Node 18+ for the WhatsApp bridge, and Chrome or Edge for the HUD. The Android app and LiveKit phone line are optional.

```bash
git clone https://github.com/Sageee-thecoder/jarvis-ai.git
cd jarvis-ai
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
copy .env.example .env        # or: cp .env.example .env
python server.py
```

Open <http://127.0.0.1:5000> in Chrome or Edge and click **Initialize**. The HUD asks for microphone (and optionally camera) access.

Optional WhatsApp bridge:

```bash
cd wa-bridge
npm install
npm start
```

Scan the QR code with the WhatsApp account the assistant should use. The session is stored in `wa-bridge/.wwebjs_auth` (gitignored).

Optional LiveKit phone line: fill `LIVEKIT_*` and `JARVIS_NUMBER` in `.env`, then run `python livekit_agent.py start`.

Optional Android app: open `android/` in Android Studio, build it, and pair it with the PC address plus `PHONE_TOKEN`.

## Configuration

All configuration is read from `.env`; `.env.example` lists every supported key. The defaults bind the server to `127.0.0.1` and turn off desktop control and WhatsApp self-mode. Keep it that way unless you understand the exposure.

Notable groups:

- `GEMINI_*` — Gemini API key and model.
- `SMTP_*`, `OWNER_EMAIL`, `IMAP_*` — send from the assistant's own mailbox and read the owner's inbox read-only.
- `WIZ_LIGHTS`, `SAMSUNG_TV_*`, `MXW01_*`, `SPOTIFY_*` — smart-home and output devices.
- `LIVEKIT_*`, `JARVIS_NUMBER`, `PHONE_TOKEN` — inbound phone line and LAN pairing.
- `DESKTOP_CONTROL`, `DESKTOP_SCREENSHOTS` — local screen/mouse/keyboard tools.
- `WA_MODE`, `WA_ANNOUNCE_GROUPS` — WhatsApp bridge behavior.

## School timetable

`school.py` ships with a small sample timetable. For a real timetable, create `school.local.json` (gitignored) with the same shape:

```json
{
  "SCHOOL": "Example High School",
  "CLASS_NAME": "9-A",
  "ISSUED": "2026-09-01",
  "PERIODS": [{"n": 1, "start": "09:00", "end": "09:40"}],
  "WEEKDAYS": ["Monday"],
  "DAYS": {"Monday": ["MAT"]},
  "SUBJECTS": {"MAT": {"subject": "Mathematics", "teacher": "", "hours": 1}},
  "GRID_INITIALS": {}
}
```

Without the file the sample is used; with it, the server answers questions about the real timetable. The file is never committed.

## Security and privacy

- **Local-first.** The server binds to `127.0.0.1` by default. The Android app is the only LAN client and must present `PHONE_TOKEN`.
- **Secrets stay in `.env`.** API keys, OAuth tokens, and phone-line credentials are not in source. Never commit `.env`, `credentials.json`, `token.json`, or the `.*_token.json` files.
- **Untrusted content.** Email, WhatsApp messages, web pages, and screen content are treated as data, not instructions. Prompt-injection text is reported, never executed.
- **Read-only inbox.** IMAP access uses `BODY.PEEK` and never marks, moves, or deletes mail. Sending is limited to the owner's address.
- **WhatsApp is unofficial.** `whatsapp-web.js` drives WhatsApp Web; linking a real account carries a ban risk. Use a spare account (`WA_MODE=assistant`) if that matters.
- **Desktop control is opt-in.** `DESKTOP_CONTROL` and `DESKTOP_SCREENSHOTS` are off by default.
- **Rotate anything that leaks.** If a key is pasted into a chat or a screenshot, revoke it and create a new one.

## Project layout

- `server.py` — Flask app, Gemini tools, email/WhatsApp/Telegram, HTTP endpoints.
- `static/index.html` — the HUD: wake phrase, speech in/out, camera, presence, panels.
- `live_bridge.py` — real-time audio bridge for Gemini Live.
- `livekit_agent.py`, `livekit_setup.py` — inbound phone line.
- `autonomy.py` — scheduled and event-driven actions.
- `school.py` — timetable loader with a generic sample; `school.local.json` is local-only.
- `lights.py`, `tv.py`, `spotify.py`, `printer.py`, `brew.py`, `desk.py` — device tools.
- `gcal.py`, `inbox.py` — Google Calendar and read-only inbox.
- `wa-bridge/` — Node.js WhatsApp bridge.
- `android/` — Kotlin/Compose phone app.
- `desktop/` — Chrome app-mode launcher and icon.
- `.env.example` — every supported setting with safe placeholders.

## Status

Personal project, actively evolving. The PC HUD, voice loop, smart-home tools, and email/WhatsApp integrations are used daily. The phone line and Android app are newer and experimental. APIs and schemas may change.

## License

MIT — see [LICENSE](LICENSE).
