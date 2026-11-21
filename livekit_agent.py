"""LiveKit voice agent for J.A.R.V.I.S.'s own phone line.

Answers inbound SIP calls to JARVIS_NUMBER (<your LiveKit number>) using Gemini
Live speech-to-speech, and lets the caller drive real Jarvis actions through
the local server's agent (lights, TV, music, mail, weather, printer...).

Run:  python livekit_agent.py start
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from livekit import agents  # noqa: E402
from livekit.agents import (  # noqa: E402
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    function_tool,
)
from livekit.plugins import google  # noqa: E402

log = logging.getLogger("jarvis-call")

LIVEKIT_URL = (os.getenv("LIVEKIT_URL") or "").strip()
LIVEKIT_API_KEY = (os.getenv("LIVEKIT_API_KEY") or "").strip()
LIVEKIT_API_SECRET = (os.getenv("LIVEKIT_API_SECRET") or "").strip()
AGENT_NAME = (os.getenv("LIVEKIT_AGENT_NAME") or "jarvis").strip()
JARVIS_URL = (os.getenv("JARVIS_LOCAL_URL") or "http://127.0.0.1:5000").rstrip("/")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
LIVE_MODEL = (os.getenv("JARVIS_LIVE_MODEL") or "gemini-3.8-live").strip()
LIVE_VOICE = (os.getenv("JARVIS_LIVE_VOICE") or "Puck").strip()
LIVE_API_VERSION = (os.getenv("JARVIS_LIVE_API_VERSION") or "v1alpha").strip()

FALLBACK_PROMPT = (
    "You are J.A.R.V.I.S., a calm, dry-witted British personal assistant. "
    "You are speaking on your own dedicated telephone line. Keep every turn to one or three short sentences, "
    "never mention being an AI or a language model, and speak Turkish or English, whichever the caller uses. "
    "You can perform real actions for the caller with take_action."
)

AUTOMATED_CALL_RULES = (
    " Some calls on this line are automated (WhatsApp, banks, delivery services). "
    "If you hear a recorded or robotic voice instead of a person, do not greet it, do not talk over it "
    "and do not answer its questions with words - stay silent until it finishes. "
    "If it reads a numeric verification code, call relay_verification_code immediately with the digits "
    "exactly as heard (convert spoken words like 'one two three' to 123) and then stay silent again."
)


def _get_json(path: str, timeout: float = 10.0):
    request = urllib.request.Request(JARVIS_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(path: str, payload: dict, timeout: float = 90.0):
    request = urllib.request.Request(
        JARVIS_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_prompt() -> str:
    try:
        data = _get_json("/api/voice/prompt", timeout=8.0)
        prompt = (data.get("prompt") or "").strip()
        return prompt or FALLBACK_PROMPT
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch prompt from the server: %s", exc)
        return FALLBACK_PROMPT


def _caller_number(ctx: JobContext) -> str:
    try:
        for participant in ctx.room.remote_participants.values():
            attributes = getattr(participant, "attributes", None) or {}
            number = attributes.get("sip.phoneNumber") or attributes.get("sip.trunkPhoneNumber")
            if number:
                return str(number)
    except Exception:  # noqa: BLE001
        pass
    return ""


class JarvisCall(Agent):
    @function_tool()
    async def take_action(self, context: RunContext, request: str) -> str:
        """Perform a real action for the caller through Jarvis's computer tools.

        Use this whenever the caller asks for something that changes the world or
        needs real data: lights, TV, music, email, WhatsApp, calendar, weather,
        school schedule, files, reminders, the coffee machine or the printer.
        Pass a precise natural-language request.

        Args:
            request: The action to perform, e.g. "turn on the living room lights".
        """
        try:
            data = await asyncio.to_thread(_post_json, "/api/voice/tool", {"request": request})
        except Exception as exc:  # noqa: BLE001
            log.warning("take_action failed: %s", exc)
            return "The home computer is not reachable right now, so I could not do that."
        reply = str(data.get("reply") or "").strip()
        if not reply:
            return "Done, but there was nothing to report."
        return reply

    @function_tool()
    async def relay_verification_code(self, context: RunContext, code: str) -> str:
        """Send a verification code heard on this call to the owner's screen.

        Call this the moment an automated voice reads a numeric code (for
        example a WhatsApp or bank verification call). Do not read the code
        aloud back to the caller unless asked.

        Args:
            code: The digits of the code, e.g. "123456".
        """
        try:
            await asyncio.to_thread(_post_json, "/api/voice/code", {"code": code}, 15.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("relay_verification_code failed: %s", exc)
            return "Could not reach the home computer to deliver the code."
        return "The code is on the owner's screen. Say nothing further."


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    prompt = await asyncio.to_thread(_fetch_prompt)
    try:
        await asyncio.wait_for(ctx.wait_for_participant(), timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
    caller = _caller_number(ctx)
    if caller:
        prompt += f" The call is coming from {caller}."
        log.info("Inbound call from %s", caller)

    model_kwargs = {
        "api_key": GEMINI_API_KEY,
        "voice": LIVE_VOICE,
        "temperature": 0.7,
        "api_version": LIVE_API_VERSION,
    }
    try:
        model = google.beta.realtime.RealtimeModel(model=LIVE_MODEL, **model_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("Falling back to the plugin's default live model (%s): %s", LIVE_MODEL, exc)
        model = google.beta.realtime.RealtimeModel(**model_kwargs)

    session = AgentSession(llm=model)

    @session.on("conversation_item_added")
    def _log_item(event) -> None:
        try:
            item = event.item
            text = getattr(item, "text_content", "") or ""
            if text:
                log.info("[%s] %s", getattr(item, "role", "?"), text[:300])
        except Exception:  # noqa: BLE001
            pass

    await session.start(agent=JarvisCall(instructions=prompt + AUTOMATED_CALL_RULES), room=ctx.room)
    await session.generate_reply(
        instructions=(
            "If you can already hear a recorded or automated voice, say nothing at all and just listen "
            "for a code. Otherwise greet the caller as J.A.R.V.I.S. in one short sentence and ask how you can help."
        )
    )


def main() -> int:
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        print("LiveKit is not configured: set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env")
        return 2
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is missing from .env")
        return 2
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=AGENT_NAME,
            ws_url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
