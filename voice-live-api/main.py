"""
main.py — Voice agent backend (Foundry Agent Service + Voice Live API).

*** LOCAL-TEST VARIANT ***
This version swaps the per-user OBO token exchange for `DefaultAzureCredential`,
so you can run it locally after `az login` without standing up the confidential-
client / MSAL OBO plumbing. It authenticates as *you* (the az-login'd identity),
not as the end user of the app — that's fine for local dev, but you must put the
OBO flow back before deploying multi-user, since otherwise every session runs
under one shared identity and Foundry RBAC is no longer evaluated per end user.

Everything else (barge-in, tool-wait suppression, transcript streaming, Azure
semantic VAD, noise reduction/echo cancellation, proactive greeting) is
unchanged from the reference implementation.

Install:
    pip install "azure-ai-voicelive[aiohttp]" azure-identity fastapi uvicorn python-dotenv

Local login (once per shell / until the cached token expires):
    az login
    # if your Foundry resource lives in a non-default tenant/subscription:
    az account set --subscription "<subscription-id-or-name>"

Run:
    uvicorn main:app --host 0.0.0.0 --port 3001 --reload
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Any, Optional

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioNoiseReduction,
    AzureSemanticVad,
    InputAudioFormat,
    InputTextContentPart,
    InterimResponseTrigger,
    MessageItem,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    StaticInterimResponseConfig,
)
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("voice-agent")

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


FOUNDRY_ENDPOINT = "https://voice-agent-txrh.services.ai.azure.com/" 
FOUNDRY_PROJECT_NAME = "voice-agent-txrh"
FOUNDRY_AGENT_NAME = "Voice"
FOUNDRY_AGENT_VERSION = os.environ.get("FOUNDRY_AGENT_VERSION", "")
FOUNDRY_CONVERSATION_ID = None
FOUNDRY_RESOURCE_OVERRIDE = os.environ.get("FOUNDRY_RESOURCE_OVERRIDE", "")
FOUNDRY_AUTH_IDENTITY_CLIENT_ID = os.environ.get("FOUNDRY_AUTH_IDENTITY_CLIENT_ID", "")
VOICE_LIVE_API_VERSION = os.environ.get("VOICE_LIVE_API_VERSION", "2026-04-10")

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# --------------------------------------------------------------------------------------
# Auth — LOCAL TEST ONLY
#
# `DefaultAzureCredential` walks a chain of credential sources (env vars, managed
# identity, VS Code, Azure CLI, ...). With just `az login` done, it resolves via
# the Azure CLI credential using whatever account you're logged into. One
# credential instance is created per process and shared across sessions/tabs —
# that's fine here since it's always "you" either way.
#
# When you're ready to bring back real per-user auth, restore the MSAL
# `acquire_token_on_behalf_of` exchange from the browser-supplied token and feed
# the resulting bearer token into an `AsyncTokenCredential` wrapper per session,
# instead of sharing this one.
# --------------------------------------------------------------------------------------

_default_credential = DefaultAzureCredential()


# --------------------------------------------------------------------------------------
# VoiceLiveSession — owns one browser <-> Voice Live conversation
# --------------------------------------------------------------------------------------

class VoiceLiveSession:
    def __init__(self, ws: WebSocket, credential: DefaultAzureCredential) -> None:
        self.ws = ws
        self.credential = credential
        self.connection = None  # type: ignore[assignment]

        self._running = True
        self._session_ready_event = asyncio.Event()
        self._session_id = str(uuid.uuid4())

        # Response lifecycle
        self._active_response = False
        self._response_api_done = False
        self._current_response_had_transcript = False

        # Barge-in
        self._is_barging_in = False

        # Tool-call latency handling
        self._awaiting_tool_response = False
        self._tool_wait_timeout_task: Optional[asyncio.Task] = None

        # Greeting
        self._greeting_in_progress = False
        self._greeting_response_active = False

        # Transcript buffers (streamed to client incrementally, see _handle_event)
        self._user_transcript_buf = ""
        self._agent_transcript_buf = ""

    async def _send(self, msg: dict) -> None:
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass

    def _cancel_tool_wait_timeout(self) -> None:
        if self._tool_wait_timeout_task and not self._tool_wait_timeout_task.done():
            self._tool_wait_timeout_task.cancel()
        self._tool_wait_timeout_task = None

    def _arm_tool_wait_timeout(self, timeout_s: float = 8.0) -> None:
        """Safety net: if a tool-call round never produces the expected follow-up
        event, don't leave barge-in permanently suppressed."""
        self._cancel_tool_wait_timeout()

        async def _timeout() -> None:
            await asyncio.sleep(timeout_s)
            if self._awaiting_tool_response:
                log.warning("[%s] tool-response timeout — re-enabling barge-in", self._session_id)
                self._awaiting_tool_response = False

        self._tool_wait_timeout_task = asyncio.create_task(_timeout())

    async def run(self) -> None:
        try:
            await self._send({"type": "session_id", "id": self._session_id})

            async with connect(
                endpoint=FOUNDRY_ENDPOINT,
                credential=self.credential,
                api_version=VOICE_LIVE_API_VERSION,
                agent_name=FOUNDRY_AGENT_NAME,
                project_name=FOUNDRY_PROJECT_NAME,
                agent_version=FOUNDRY_AGENT_VERSION or None,
                conversation_id=FOUNDRY_CONVERSATION_ID or None,
                foundry_resource_override=FOUNDRY_RESOURCE_OVERRIDE or None,
                authentication_identity_client_id=(
                    FOUNDRY_AUTH_IDENTITY_CLIENT_ID
                    if FOUNDRY_AUTH_IDENTITY_CLIENT_ID and FOUNDRY_RESOURCE_OVERRIDE
                    else None
                ),
            ) as conn:
                self.connection = conn
                await asyncio.gather(
                    self._listen_voicelive(),
                    self._setup_and_forward(),
                )
        except Exception as exc:
            log.exception("VoiceLive session error")
            await self._send({"type": "error", "text": str(exc)})
        # NOTE: credential is process-wide and shared across sessions in this
        # local-test variant, so it is intentionally NOT closed here. It's closed
        # once at process shutdown instead (see @app.on_event("shutdown")).

    # ---- session setup -----------------------------------------------------

    async def _setup_session(self) -> None:
        session_cfg = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            turn_detection=AzureSemanticVad(
                threshold=0.5,
                prefix_padding_ms=300,
                silence_duration_ms=500,
                interrupt_response=True,
                auto_truncate=True,
                create_response=True,
            ),
            input_audio_noise_reduction=AudioNoiseReduction(type="azure_deep_noise_suppression"),
            input_audio_echo_cancellation=AudioEchoCancellation(),
        )
        await self.connection.session.update(session=session_cfg)

    async def _enable_interim_responses(self) -> None:
        """Play a short filler line if a tool call is taking a moment, so there's
        no dead air while the agent waits on a function result."""
        try:
            await self.connection.session.update(
                session=RequestSession(
                    interim_response=StaticInterimResponseConfig(
                        triggers=[InterimResponseTrigger.TOOL],
                        latency_threshold_ms=100,
                        texts=[
                            "Just a moment please...",
                            "Let me check on that for you.",
                            "Hold on a second while I look that up.",
                            "One moment, I'm pulling up that information.",
                        ],
                    )
                )
            )
        except Exception:
            log.warning("[%s] failed to enable interim responses", self._session_id)

    async def _setup_and_forward(self) -> None:
        await asyncio.sleep(0)
        await self._setup_session()
        await self._session_ready_event.wait()
        await self._send({"type": "status", "text": "connected"})

        # Proactive greeting — barge-in is suppressed until it finishes or times out.
        self._greeting_in_progress = True
        try:
            await self.connection.conversation.item.create(
                item=MessageItem(
                    role="system",
                    content=[InputTextContentPart(text="Say something to welcome the user in English.")],
                )
            )
            await self.connection.response.create()
        except Exception:
            self._greeting_in_progress = False

        async def _greeting_timeout() -> None:
            await asyncio.sleep(8.0)
            if self._greeting_in_progress:
                self._greeting_in_progress = False

        asyncio.create_task(_greeting_timeout())

        try:
            await self._forward_browser_audio()
        finally:
            if self.connection:
                try:
                    await asyncio.wait_for(self.connection.close(), timeout=3.0)
                except Exception:
                    pass

    # ---- browser -> Voice Live ----------------------------------------------

    async def _forward_browser_audio(self) -> None:
        """Client sends {"type": "audio_chunk", "data": "<base64 pcm16>"} or
        {"type": "stop"}. Suppressed while the greeting is still playing."""
        try:
            while self._running:
                msg = await self.ws.receive_json()
                if msg.get("type") == "audio_chunk":
                    if not self._greeting_in_progress:
                        await self.connection.input_audio_buffer.append(audio=msg["data"])
                elif msg.get("type") == "stop":
                    self._running = False
                    break
        except WebSocketDisconnect:
            self._running = False
        except Exception:
            log.exception("[%s] error forwarding browser audio", self._session_id)
            self._running = False

    # ---- Voice Live -> browser ----------------------------------------------

    async def _listen_voicelive(self) -> None:
        try:
            async for event in self.connection:
                if not self._running:
                    break
                await self._handle_event(event)
        except Exception as exc:
            await self._send({"type": "error", "text": str(exc)})

    async def _handle_event(self, event: Any) -> None:
        conn = self.connection

        if event.type == ServerEventType.SESSION_UPDATED:
            self._session_ready_event.set()

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_DELTA:
            delta = getattr(event, "delta", "")
            if delta:
                self._user_transcript_buf += delta
                await self._send({"type": "user_text_delta", "delta": delta})

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            text = getattr(event, "transcript", "") or self._user_transcript_buf
            self._user_transcript_buf = ""
            await self._send({"type": "user_text", "text": text})

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            delta = getattr(event, "delta", "")
            if delta:
                self._agent_transcript_buf += delta
                self._awaiting_tool_response = False
                self._current_response_had_transcript = True
                await self._send({"type": "agent_text_delta", "delta": delta})

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            text = getattr(event, "transcript", "") or self._agent_transcript_buf
            self._agent_transcript_buf = ""
            self._current_response_had_transcript = True
            await self._send({"type": "agent_text", "text": text})

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            # Ignore stray VAD triggers during the greeting or while genuinely
            # waiting on a tool result (see _arm_tool_wait_timeout).
            if self._awaiting_tool_response or self._greeting_in_progress:
                return
            await self._send({"type": "status", "text": "barge_in"})
            self._is_barging_in = True
            if self._active_response and not self._response_api_done:
                try:
                    await conn.response.cancel()
                except Exception:
                    pass
            await self._send({"type": "status", "text": "listening"})

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            await self._send({"type": "status", "text": "processing"})

        elif event.type == ServerEventType.RESPONSE_CREATED:
            self._cancel_tool_wait_timeout()
            self._active_response = True
            self._response_api_done = False
            self._current_response_had_transcript = False
            self._awaiting_tool_response = False
            self._is_barging_in = False
            self._greeting_response_active = self._greeting_in_progress

        elif event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            # Drop any audio still in flight from a response we already cancelled.
            if self._is_barging_in:
                return
            raw = event.delta
            if raw:
                await self._send({
                    "type": "audio_chunk",
                    "data": base64.b64encode(raw).decode() if isinstance(raw, bytes) else raw,
                })

        elif event.type == ServerEventType.RESPONSE_AUDIO_DONE:
            if self._greeting_in_progress:
                self._greeting_in_progress = False
            if not self._awaiting_tool_response:
                await self._send({"type": "status", "text": "ready"})

        elif event.type == ServerEventType.RESPONSE_DONE:
            self._active_response = False
            self._response_api_done = True
            was_greeting = self._greeting_response_active
            self._greeting_response_active = False
            if self._greeting_in_progress:
                self._greeting_in_progress = False

            if was_greeting:
                self._cancel_tool_wait_timeout()
                self._awaiting_tool_response = False
                asyncio.create_task(self._enable_interim_responses())
            elif not self._current_response_had_transcript:
                # No transcript on this turn implies a tool-call round — suppress
                # barge-in until the real spoken reply arrives (or the timeout fires).
                self._awaiting_tool_response = True
                self._arm_tool_wait_timeout()
            else:
                self._cancel_tool_wait_timeout()
                self._awaiting_tool_response = False

        elif event.type == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed: no active response" not in msg:
                await self._send({"type": "error", "text": msg})


# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------

app = FastAPI(title="Voice Agent Backend (local test)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await _default_credential.close()


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.websocket("/voice")
async def voice_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    log.info("client connected")

    # ---- Handshake kept for wire-compatibility with the real client, but the
    # token is NOT used for auth in this local-test variant — DefaultAzureCredential
    # (your `az login` identity) is used for every session instead. Swap this
    # back to the OBO exchange before deploying multi-user. ----
    try:
        raw = await client_ws.receive_text()
        first_msg = json.loads(raw)
    except (json.JSONDecodeError, WebSocketDisconnect):
        await client_ws.close(code=4000, reason="first message must be JSON auth handshake")
        return

    if first_msg.get("type") != "auth":
        await client_ws.close(code=4000, reason="missing auth handshake")
        return

    session = VoiceLiveSession(client_ws, _default_credential)
    await session.run()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")
