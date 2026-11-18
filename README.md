# J.A.R.V.I.S. - local real-time assistant (Gemini + WhatsApp bridge)

Hears you (mic), sees you (webcam), talks back, emails you from its own Gmail,
and messages you on WhatsApp. You can also text it on WhatsApp and it answers.

## Fill in .env first

Copy `.env.example` to `.env` (`cp .env.example .env`) and fill in your credentials:

- `GEMINI_API_KEY`   your Google Gemini API key
- `SMTP_USER`        Jarvis's dedicated Gmail address
- `SMTP_PASS`        must be a Gmail **App Password** (see below), not the normal password
- `OWNER_EMAIL`      your own inbox (Jarvis emails only this address)
- `OWNER_WHATSAPP`   your own number, digits only with country code, e.g. 905551234567
- `BRIDGE_TOKEN`     a random secret string to secure the WhatsApp bridge

## Run (two terminals)

Terminal 1 - the brain:

    python -m venv .venv
    .venv\Scripts\activate            (Windows)   |   source .venv/bin/activate  (macOS/Linux)
    pip install -r requirements.txt
    python server.py

Terminal 2 - the WhatsApp bridge (Node 18+):

    cd wa-bridge
    npm install
    npm start

A QR code appears in terminal 2. On the phone whose WhatsApp Jarvis should use:
WhatsApp > Settings > Linked devices > Link a device, and scan it. The session is saved in
`wa-bridge/.wwebjs_auth`, so you only scan once.

Then open http://127.0.0.1:5000 in Chrome or Edge and click Initialize.

## Speed

- Replies stream: Jarvis starts speaking the first sentence while the rest is still being written.
- `JARVIS_THINKING=minimal` in `.env` turns Gemini's reasoning down to the fastest level. Raise it to
  `low` or `medium` if you want deeper answers at the cost of speed.
- Local browser voices start instantly; network voices (names starting with "Google") add a delay.
  Pick a local voice in the Voice dropdown if replies feel laggy.
- The startup self-test prints the AI round-trip time in the transcript.

## Weather (OpenWeather)

- Set `WEATHER_CITY` in `.env` (format `City,CountryCode`, e.g. `Istanbul,TR`).
- The server refreshes weather every `WEATHER_REFRESH_MIN` minutes (default 10, about 290 calls a day,
  inside the free limit) and shows it in the HUD panel.
- Jarvis already knows the live weather, so "do I need a jacket?" needs no extra lookup.
  "What's the weather in Izmir?" fetches that city on demand.
- Spoken alerts (toggle: "Speak weather alerts"): precipitation starting or stopping, a temperature swing
  of `WEATHER_TEMP_ALERT` degrees, strong wind, and rain likely within about 6 hours.
- A brand-new OpenWeather key can take up to two hours to activate and returns 401 until then.

## Smart lights (WiZ)

Put your bulbs in `.env`, ideally as `Name=IP` pairs so you can say "turn off the bedroom":

    WIZ_LIGHTS=Living Room=192.168.1.20, Bedroom=192.168.1.21, Desk=192.168.1.22

- Several bulbs in one fixture are ONE light: join their IPs with `|`, for example
  `WIZ_LIGHTS=Main Light=192.168.5.5|192.168.5.2|192.168.5.8|192.168.5.12`. They always switch together,
  the HUD shows a single row, and if only some respond Jarvis tells you which bulb did not.
  Animated scenes (Party, Fireplace and so on) run on each bulb separately, so they may not be perfectly in sync.
- Give every bulb a fixed IP in your router (DHCP reservation), otherwise the addresses change.
- The PC must be on the same network, and "Local communication" must be enabled in the WiZ app
  (Settings > Security) if your bulbs ignore commands.
- Jarvis can switch lights on, off or toggle, set brightness (10 to 100 percent, bulbs cannot dim lower),
  colors by name or hex, white temperature (2200 to 6500 K) and WiZ scenes (Cozy, Relax, Focus, Party,
  Candlelight, TV time, Night light and so on), report their status, and schedule changes ("turn the
  bedroom off in 20 minutes"). A room word like "bedroom" controls every light whose name contains it.
- The HUD has a Lights panel with on/off buttons and brightness sliders for each bulb.

## Reading your own email and WhatsApp (read-only)

- Email: fill in `IMAP_USER` and `IMAP_PASS` (for Gmail an App Password for YOUR inbox). Jarvis opens
  it read-only: it never marks mail as read, deletes, moves or sends from it. "Anything new from my bank?",
  "read me that email" and "what's unread?" work. New mail is announced (sender and subject).
- WhatsApp (needs `WA_MODE=self`): "who messaged me?", "what did Ayse say?". Reading never marks chats
  as read. New private messages are announced by sender only, never the content; groups and muted chats
  stay silent unless `WA_ANNOUNCE_GROUPS=true`.
- Talking to Jarvis on WhatsApp in self mode: open "Message yourself" and start with "Jarvis, ...".
  Jarvis's replies there start with a robot emoji. Messages you send to yourself may not buzz your phone,
  so use Telegram or email for urgent alerts.
- Anything inside an email or message is treated as untrusted text: Jarvis reports it but is told never to
  follow instructions found in it. Jarvis can only send to you, never to other people.
- An app password gives full access to the mailbox, even though Jarvis only reads. Revoke it any time in
  your Google account.
- Announcements play on your PC speakers. Untick "Announce new mail & WhatsApp" if others can overhear.

## WhatsApp bridge troubleshooting

- The bridge listens on port 3917 (`BRIDGE_PORT` and `BRIDGE_URL` in `.env`, keep them in step). Open
  http://127.0.0.1:3917 in a browser: seeing `{"service":"jarvis-wa-bridge","error":"Forbidden..."}` means
  the bridge is alive. Any other page, such as "Upgrade Required", means another program owns that port.
- If the bridge terminal prints `EADDRINUSE`, pick another free port in `.env` and restart both servers.
- The HUD log now says why the bridge is unavailable: not running, wrong port, wrong token or waiting for the QR scan.

## Things that will bite you

1. Gmail: normal passwords are rejected over SMTP. On your dedicated Jarvis Gmail account, enable
   2-Step Verification, create an App Password, and put it in `SMTP_PASS`.
2. Gemini AQ. keys: Google's newer key format. Some accounts get `401 ACCESS_TOKEN_TYPE_UNSUPPORTED`.
   The HUD runs a self-test on startup and shows the exact error. Fix: `pip install -U google-genai`;
   if it persists, create a standard key in Google Cloud Console (Credentials), restrict it to
   the Generative Language API, and replace `GEMINI_API_KEY`.
3. `whatsapp-web.js` is unofficial (it drives WhatsApp Web) and WhatsApp can ban accounts that use it.
   In `WA_MODE=self` (needed to read YOUR chats) the bridge is linked to your real account, so that
   risk applies to your own number. If you would rather not risk it, set `WA_MODE=assistant` and link a
   spare number instead; Jarvis then cannot read your chats. Switching modes means deleting
   `wa-bridge/.wwebjs_auth` and scanning the QR code again.
4. Both servers bind to `127.0.0.1` and the bridge needs the `BRIDGE_TOKEN` secret. Keep it that way.

## Usage

- "Jarvis, can you see me? What am I holding?"
- "Jarvis, email me a summary of this conversation."
- "Jarvis, WhatsApp me in 20 minutes to call the client."
- Text Jarvis on WhatsApp from your own number and it replies there.

## Files

- `lights.py`            WiZ light control (pywizlight)
- `inbox.py`             read-only IMAP access to your own mailbox
- `server.py`            Flask backend, Gemini function calling, email/WhatsApp/Telegram senders, reminders
- `static/index.html`    HUD: wake word, speech in/out, camera, presence detection
- `wa-bridge/bridge.js`  whatsapp-web.js bridge (send + receive)
- `.env`                 your configuration (secrets, keep private)

> **Note:** Email sending is broken right now, it'll be fixed soon.
