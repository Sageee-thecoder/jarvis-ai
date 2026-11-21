"""Wire J.A.R.V.I.S.'s LiveKit phone number to his call agent.

Creates (once) an individual-dispatch rule that gives each inbound caller his
own room and dispatches the "jarvis" agent there, then records it against the
rented number. LiveKit treats a rule with no trunk/number filter as matching
every inbound call, so it refuses to pin such a rule to a single number - the
rule already applies to it. Safe to re-run.

Run:  python livekit_setup.py            # create/verify everything
      python livekit_setup.py --status   # only show current state
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta

import httpx
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from livekit import api  # noqa: E402

URL = (os.getenv("LIVEKIT_URL") or "").strip()
KEY = (os.getenv("LIVEKIT_API_KEY") or "").strip()
SECRET = (os.getenv("LIVEKIT_API_SECRET") or "").strip()
AGENT_NAME = (os.getenv("LIVEKIT_AGENT_NAME") or "jarvis").strip()
PHONE = (os.getenv("JARVIS_NUMBER") or "").strip()
RULE_NAME = os.getenv("LIVEKIT_DISPATCH_RULE_NAME") or "jarvis-inbound"
ROOM_PREFIX = os.getenv("LIVEKIT_ROOM_PREFIX") or "jarvis-phone-"
STATE_FILE = os.path.join(BASE_DIR, "logs", "livekit_line.json")

HTTP_BASE = URL.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def _twirp_token() -> str:
    token = api.AccessToken(KEY, SECRET)
    token.with_identity("jarvis-phone-setup")
    token.with_ttl(timedelta(minutes=10))
    token.with_sip_grants(api.SIPGrants(admin=True, call=True))
    return token.to_jwt()


async def ensure_rule() -> str:
    async with api.LiveKitAPI(url=URL, api_key=KEY, api_secret=SECRET) as lk:
        existing = await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
        for rule in existing.items:
            if rule.name == RULE_NAME:
                print(f"dispatch rule already exists: {rule.sip_dispatch_rule_id}")
                return rule.sip_dispatch_rule_id
        created = await lk.sip.create_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name=RULE_NAME,
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
                ),
                room_config=api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]),
            )
        )
        print(f"created dispatch rule: {created.sip_dispatch_rule_id} (rooms {ROOM_PREFIX}*)")
        return created.sip_dispatch_rule_id


def _call_twirp(method: str, payload: dict) -> dict:
    response = httpx.post(
        f"{HTTP_BASE}/twirp/livekit.PhoneNumberService/{method}",
        json=payload,
        headers={"Authorization": f"Bearer {_twirp_token()}", "Content-Type": "application/json"},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} failed ({response.status_code}): {response.text[:400]}")
    return response.json()


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _item_number(item: dict) -> str:
    return (
        item.get("e164_format")
        or item.get("phoneNumber")
        or item.get("phone_number")
        or item.get("e164")
        or ""
    )


def _rule_ids(item: dict) -> list:
    ids = item.get("sip_dispatch_rule_ids") or []
    single = item.get("sip_dispatch_rule_id") or item.get("sipDispatchRuleId")
    if single and single not in ids:
        ids = list(ids) + [single]
    return ids


def find_number() -> dict:
    data = _call_twirp("ListPhoneNumbers", {})
    wanted = _digits(PHONE)
    for item in data.get("items", []):
        if _digits(_item_number(item)) == wanted:
            return item
    raise SystemExit(f"Phone number {PHONE} not found on this LiveKit project.")


def _is_catch_all_error(text: str) -> bool:
    return "catch all dispatch rule" in text


def assign(rule_id: str) -> dict:
    item = find_number()
    number_id = item.get("id")
    if rule_id in _rule_ids(item):
        print(f"{PHONE} is already attached to the dispatch rule.")
        return item
    try:
        _call_twirp("UpdatePhoneNumber", {"id": number_id, "sip_dispatch_rule_id": rule_id})
        print(f"{PHONE} now routes calls to agent '{AGENT_NAME}'.")
        return item
    except RuntimeError as exc:
        if _is_catch_all_error(str(exc)):
            print(
                "The rule matches every inbound call in this project, so LiveKit refuses to\n"
                f"pin it to one number - it already applies to {PHONE}. No assignment needed.\n"
                "(The dashboard's 'Assign dispatch rule' rejects it for the same reason.)"
            )
            return item
        if "400" not in str(exc):
            raise
        # Older API revisions identify the number by its E.164 value instead of the id.
        try:
            _call_twirp("UpdatePhoneNumber", {"phone_number": PHONE, "sip_dispatch_rule_id": rule_id})
            print(f"{PHONE} now routes calls to agent '{AGENT_NAME}'.")
        except RuntimeError as retry_exc:
            if not _is_catch_all_error(str(retry_exc)):
                raise
            print(f"The rule already applies to every inbound call, including {PHONE}.")
        return item


def save_state(rule_id: str) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"rule": RULE_NAME, "rule_id": rule_id, "number": PHONE, "agent": AGENT_NAME}, fh, indent=2)
    except OSError as exc:
        print(f"(could not save state: {exc})")


def status() -> None:
    async def _rules():
        async with api.LiveKitAPI(url=URL, api_key=KEY, api_secret=SECRET) as lk:
            return await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())

    rules = asyncio.run(_rules())
    print("dispatch rules:")
    for rule in rules.items:
        print(f"  - {rule.name or '(unnamed)'}: {rule.sip_dispatch_rule_id}")
    item = find_number()
    print(f"number {PHONE}: id={item.get('id')} rule={', '.join(_rule_ids(item)) or '-'}")


def main() -> int:
    if not (URL and KEY and SECRET):
        print("LiveKit is not configured: set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env")
        return 2
    try:
        if "--status" in sys.argv:
            status()
            return 0
        rule_id = asyncio.run(ensure_rule())
        assign(rule_id)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if getattr(exc, "status", None) == 401 or "401" in text:
            print(
                "LiveKit rejected the credentials (401). Check in LiveKit Cloud:\n"
                f"  - you are in the project whose URL is {URL}\n"
                "  - Settings -> Keys: copy that project's key AND the full secret (deleted or\n"
                "    rotated keys stop working; the secret is longer than the key)\n"
                "  - .env has no quotes or stray spaces around the three LIVEKIT_* values"
            )
            return 1
        raise
    save_state(rule_id)
    print("Done. Start the agent with: python livekit_agent.py start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
