"""Duplex voice bridge between the phone app and Gemini Live.

The Android app opens ws://<pc>:LIVE_PORT/live?token=... and streams raw PCM
(16 kHz, mono, signed 16-bit little-endian) microphone frames. Replies come back
as 24 kHz PCM frames in the same format. Transcriptions and call lifecycle
events travel as small JSON text frames in both directions.

Client -> server text commands: {"type": "text", "text": "..."} (typed input) and
{"type": "end"}. Server -> client text events: ready, connected, transcript,
interrupted, turn_complete, error, closed.
"""

import asyncio
import hmac
import json
import logging
import threading
from urllib.parse import parse_qs, urlparse

import websockets
from google import genai
from google.genai import types

log = logging.getLogger("jarvis.live")

INPUT_MIME = "audio/pcm;rate=16000"
OUTPUT_SAMPLE_RATE = 24000
MAX_GREETING = 1500


class LiveBridge:
    def __init__(self, api_key, token, model, voice, prompt_provider):
        self.api_key = api_key
        self.token = token
        self.model = model
        self.voice = voice
        self.prompt_provider = prompt_provider
        self.port = None

    def start(self, host, port):
        self.port = port
        thread = threading.Thread(target=self._run, args=(host, port), daemon=True, name="jarvis-live")
        thread.start()
        return thread

    def _run(self, host, port):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve(host, port))
        except Exception:  # noqa: BLE001
            log.exception("Live voice bridge stopped")
        finally:
            loop.close()

    async def _serve(self, host, port):
        async with websockets.serve(
            self._handle,
            host,
            port,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ):
            log.info("Live voice bridge listening on ws://%s:%s/live", host, port)
            await asyncio.Future()  # run until the process exits

    def _allowed(self, ws):
        peer = (ws.remote_address or ("", 0))[0]
        token = parse_qs(urlparse(ws.request.path).query).get("token", [""])[0]
        if not self.token:
            return peer in ("127.0.0.1", "::1")
        return bool(token) and hmac.compare_digest(token, self.token)

    async def _handle(self, ws):
        peer = (ws.remote_address or ("", 0))[0]
        if not self._allowed(ws):
            log.warning("Live call rejected from %s: token mismatch", peer)
            await ws.close(code=4003, reason="forbidden")
            return
        query = parse_qs(urlparse(ws.request.path).query)
        greeting = query.get("greet", [""])[0].strip()[:MAX_GREETING]
        log.info("Live call accepted from %s (greeting %d chars)", peer, len(greeting))
        failed = False

        try:
            try:
                system_instruction = self.prompt_provider()
            except Exception:  # noqa: BLE001
                log.exception("Live prompt failed; using a minimal persona")
                system_instruction = "You are Jarvis, a personal assistant on a live phone call. Keep every turn short."

            client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    api_version="v1alpha",
                    timeout=600_000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                system_instruction=system_instruction,
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
            )

            await self._send_json(ws, {"type": "ready", "model": self.model, "rate": OUTPUT_SAMPLE_RATE})
            async with client.aio.live.connect(model=self.model, config=config) as session:
                log.info("Gemini live session open for %s", peer)
                await self._send_json(ws, {"type": "connected"})
                if greeting:
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    text=(
                                        "The owner just answered your call. Greet them briefly, "
                                        "then tell them this: " + greeting
                                    )
                                )
                            ],
                        ),
                        turn_complete=True,
                    )
                uplink = asyncio.create_task(self._uplink(ws, session, peer), name="uplink")
                downlink = asyncio.create_task(self._downlink(ws, session, peer), name="downlink")
                done, pending = await asyncio.wait(
                    {uplink, downlink}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    log.info("Live call %s finished for %s", task.get_name(), peer)
                    exc = task.exception()
                    if exc:
                        raise exc
        except websockets.ConnectionClosed as exc:
            log.info("Live call socket closed by the phone: code=%s reason=%s", exc.code, exc.reason)
        except Exception as exc:  # noqa: BLE001
            failed = True
            log.exception("Live call ended with error: %s", exc)
            try:
                await self._send_json(ws, {"type": "error", "text": str(exc)[:300]})
            except Exception:  # noqa: BLE001
                pass
        finally:
            log.info("Live call with %s ended (%s)", peer, "error" if failed else "normal")
            try:
                await ws.close(code=1011 if failed else 1000, reason="call ended" if failed else "bye")
            except Exception:  # noqa: BLE001
                pass

    async def _uplink(self, ws, session, peer):
        log.info("Uplink started for %s", peer)
        try:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    if message:
                        await session.send_realtime_input(
                            audio=types.Blob(data=bytes(message), mime_type=INPUT_MIME)
                        )
                    continue
                try:
                    data = json.loads(message)
                except (TypeError, ValueError):
                    continue
                kind = data.get("type")
                if kind == "end":
                    return
                if kind == "text" and data.get("text"):
                    await session.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(text=str(data["text"])[:2000])]),
                        turn_complete=True,
                    )
        finally:
            log.info("Uplink ended for %s", peer)

    async def _downlink(self, ws, session, peer):
        log.info("Downlink started for %s", peer)
        try:
            while True:
                received = 0
                async for message in session.receive():  # one model turn per pass
                    received += 1
                    content = message.server_content
                    if content is not None:
                        if content.interrupted:
                            await self._send_json(ws, {"type": "interrupted"})
                        if content.input_transcription and content.input_transcription.text:
                            await self._send_json(
                                ws, {"type": "transcript", "role": "user", "text": content.input_transcription.text}
                            )
                        if content.output_transcription and content.output_transcription.text:
                            await self._send_json(
                                ws, {"type": "transcript", "role": "jarvis", "text": content.output_transcription.text}
                            )
                        if content.model_turn:
                            for part in content.model_turn.parts or []:
                                blob = part.inline_data
                                if blob is not None and blob.data:
                                    await ws.send(blob.data)
                                elif part.text:
                                    await self._send_json(ws, {"type": "text", "text": part.text})
                        if content.turn_complete:
                            await self._send_json(ws, {"type": "turn_complete"})
                    if message.go_away:
                        log.info("Gemini go_away for %s: %s", peer, message.go_away)
                        await self._send_json(ws, {"type": "closed", "reason": "session rotating"})
                        return
                if received == 0:
                    break
        except Exception:
            log.exception("Gemini receive failed for %s", peer)
            raise
        log.info("Gemini receive loop ended for %s", peer)

    @staticmethod
    async def _send_json(ws, payload):
        await ws.send(json.dumps(payload, ensure_ascii=False))
