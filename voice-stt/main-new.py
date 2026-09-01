"""
main.py — Chat backend: browser mic -> STT -> Foundry Agent (text) -> TTS -> browser.

Architecture (per spec):
    1. Client opens ws://.../chat -> a new session is created and held server-side
       for the lifetime of that connection.
    2. Client clicks the mic once and sends {"type": "start_listening"}. The backend
       starts a continuous Speech-to-Text recognizer against a live PushAudioInputStream
       and streams "listening" status back.
    3. Client streams raw PCM16 mono audio as binary WebSocket frames continuously
       while listening is on; each chunk is written straight into the push stream as
       it arrives (no buffering — the SDK's own voice-activity/endpoint detection
       decides where one utterance ends and the next begins).
    4. Every time the recognizer finalizes an utterance (Speech SDK's `recognized`
       event), the backend sends the transcript to the client for display, then sends
       that text to the Foundry agent (Responses API, text in / text out — NOT Voice
       Live) — all without the client sending any further per-utterance message.
    5. Backend runs the agent's reply through Text-to-Speech and sends the audio back,
       along with the reply text and that response's id. Recognition keeps running the
       whole time, so the next utterance can start as soon as the user talks again.
    6. Client sends {"type": "stop_listening"} (second mic click) or disconnects to
       tear the recognizer and push stream down.

Conversation continuity — previous_response_id chaining, not a server-side "conversation"
object:
    Each turn calls responses.create(input=text, previous_response_id=...), chaining to
    the prior turn's response.id instead of creating/holding a separate conversation
    resource. ChatSession.last_response_id carries this between turns for the life of
    one WebSocket connection — it starts at None on a fresh connection, so a new
    connection always starts a fresh agent context (no continuity across reconnects).

Barge-in:
    The moment the user starts talking again — Speech SDK's interim `recognizing`
    event, which fires before the phrase is even finished — cancels whatever turn is
    currently in flight (the agent/TTS pipeline for the *previous* utterance) and/or
    tells the client to stop any agent audio that's playing. This does NOT touch the
    continuous recognizer itself, which keeps running throughout the whole listening
    session; only the downstream agent+TTS turn / playback gets cancelled.

    The server's turn task finishes as soon as it *sends* the agent_audio message —
    long before the client is done playing a multi-second clip — so "is a turn in
    flight" alone isn't enough to know whether the user is barging in on live
    playback. The client acks {"type": "playback_started"} when it starts playing a
    clip and {"type": "playback_ended"} when it finishes naturally; the server tracks
    this as ChatSession._agent_speaking and treats it as an equally valid barge-in
    target alongside an in-flight turn task.

    This is turn-level barge-in: it can't interrupt a blocking Speech SDK call
    mid-flight (the SDK calls run in a worker thread and keep running to completion in
    the background), but it guarantees the *user* never has to wait for a stale turn
    or stale playback, and stale results are discarded rather than sent to the client
    or logged.

Session logging:
    Every session gets its own JSON transcript at SESSION_LOG_DIR/<session_id>.json,
    rewritten atomically after every turn, containing the user text, agent text, and
    token usage for that turn. The same information is also printed to the terminal
    as it happens.

Auth model — Cognitive Services Speech User only (no key):
    Speech SDK doesn't take a plain Entra token directly. Per Microsoft's documented
    pattern, you build a special authorization token string of the form
    "aad#<speech-resource-ARM-ID>#<entra-access-token>" and refresh it before each
    call (Entra tokens are typically valid ~60-90 min; we refresh proactively).
    Confirmed against the installed SDK: SpeechConfig(auth_token=..., region=...) and
    both SpeechRecognizer/SpeechSynthesizer expose a settable .authorization_token.

    The Foundry agent call uses the same DefaultAzureCredential via azure-ai-projects'
    AIProjectClient.get_openai_client(), which returns a client whose .responses.create()
    is used with previous_response_id chaining for multi-turn continuity (see above) —
    no separate conversation resource is created or held.

    NOTE: SPEECH_RESOURCE_ID / FOUNDRY_PROJECT_ENDPOINT below are hardcoded for local
    dev convenience. Before shipping this, move them (and anything else identifying
    your Azure resources) back to required environment variables / a secrets manager —
    don't ship resource IDs in source control.

Install:
    pip install -r requirements.txt

Run:
    uvicorn main:app --host 0.0.0.0 --port 3001 --reload
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import azure.cognitiveservices.speech as speechsdk
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("chat-backend")

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------

# SPEECH_REGION = os.environ["SPEECH_REGION"]                  # e.g. "eastus"
# SPEECH_RESOURCE_ID = os.environ["SPEECH_RESOURCE_ID"]         # full ARM resource ID
# SPEECH_RECOGNITION_LANGUAGE = os.environ.get("SPEECH_RECOGNITION_LANGUAGE", "en-US")
# SPEECH_SYNTHESIS_VOICE = os.environ.get("SPEECH_SYNTHESIS_VOICE", "en-US-AvaNeural")

# # Audio format the FRONTEND must send: raw PCM16 mono at this sample rate.
# INPUT_SAMPLE_RATE = int(os.environ.get("INPUT_SAMPLE_RATE", "16000"))

# FOUNDRY_PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]  # https://<resource>.services.ai.azure.com/api/projects/<project>
# FOUNDRY_AGENT_NAME = os.environ["FOUNDRY_AGENT_NAME"]

SPEECH_REGION = "eastus"                # e.g. "eastus"
SPEECH_RESOURCE_ID = "/subscriptions/c34b42f0-a89c-4e9d-9205-57ae6b91357f/resourceGroups/VisualStudioOnline-C88866B319C044FFB02C78F550AEF0FB/providers/Microsoft.CognitiveServices/accounts/txrh"         # full ARM resource ID
SPEECH_RECOGNITION_LANGUAGE = os.environ.get("SPEECH_RECOGNITION_LANGUAGE", "en-US")
SPEECH_SYNTHESIS_VOICE = os.environ.get("SPEECH_SYNTHESIS_VOICE", "en-US-AvaNeural")

# Audio format the FRONTEND must send: raw PCM16 mono at this sample rate.
INPUT_SAMPLE_RATE = int(os.environ.get("INPUT_SAMPLE_RATE", "16000"))

FOUNDRY_PROJECT_ENDPOINT = "https://voice-agent-txrh.services.ai.azure.com/api/projects/voice-agent-txrh"  # https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_AGENT_NAME = "Voice"

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

SPEECH_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

# Where per-session transcript+token JSON logs are written.
SESSION_LOG_DIR = Path(os.environ.get("SESSION_LOG_DIR", "session_logs"))
SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Upper bound on how long a single turn's TTS synthesis may take before we give up on
# it and tell the client, rather than leaving them stuck on "synthesizing" indefinitely.
# Cancelling the await here does NOT stop the underlying blocking SDK call running in
# its worker thread (same limitation as barge-in — see module docstring) — it only
# stops us from waiting on it any longer.
#
# The agent call has NO timeout, deliberately — the agent is allowed to take as long
# as it needs to think; only barge-in (the user talking again) cuts it off.
TTS_CALL_TIMEOUT_S = float(os.environ.get("TTS_CALL_TIMEOUT_S", "15"))

# --------------------------------------------------------------------------------------
# Shared credentials / clients (module-level, reused across sessions)
# --------------------------------------------------------------------------------------

_credential = DefaultAzureCredential()

_project_client = AIProjectClient(endpoint=FOUNDRY_PROJECT_ENDPOINT, credential=_credential)
_openai_client = _project_client.get_openai_client(agent_name=FOUNDRY_AGENT_NAME)

# Speech auth token cache — the "aad#<resourceId>#<token>" string, refreshed proactively.
# Read/refreshed from whatever worker thread happens to be building a recognizer or
# running TTS for a given session, so concurrent sessions can hit this at the same
# time; _token_lock makes the check-then-refresh atomic instead of racing.
_speech_token_cache: dict = {"value": None, "expires_at": 0.0}
_token_lock = threading.Lock()


def _startup_credential_check() -> None:
    """Fails fast and loudly if `az login` hasn't been run (or whatever
    DefaultAzureCredential source you're relying on isn't available), instead of
    only surfacing an auth error deep inside the first WebSocket session."""
    try:
        _credential.get_token(SPEECH_TOKEN_SCOPE)
        log.info("DefaultAzureCredential resolved OK — ready to serve /chat")
    except Exception:
        log.exception(
            "DefaultAzureCredential could not acquire a token. If you're running "
            "locally, run `az login` first. If you're in a container/CI, check "
            "AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET or managed identity."
        )
        raise


def _get_speech_auth_token() -> str:
    """Returns a cached 'aad#<resourceId>#<token>' string, refreshing when it's within
    5 minutes of expiry. Building this string is REQUIRED by the Speech service's
    documented Microsoft Entra auth format — a bare Entra token is not accepted as-is.

    Guarded by _token_lock: this can be called concurrently from multiple sessions'
    worker threads (TTS synthesis, recognizer construction), and the check-then-refresh
    below isn't atomic on its own — without the lock, two threads racing past an
    expired token could both fire a redundant get_token() call at once."""
    with _token_lock:
        now = time.time()
        if _speech_token_cache["value"] and now < _speech_token_cache["expires_at"] - 300:
            return _speech_token_cache["value"]

        entra_token = _credential.get_token(SPEECH_TOKEN_SCOPE)
        auth_token = f"aad#{SPEECH_RESOURCE_ID}#{entra_token.token}"
        _speech_token_cache["value"] = auth_token
        _speech_token_cache["expires_at"] = entra_token.expires_on
        return auth_token


def _new_speech_config() -> speechsdk.SpeechConfig:
    cfg = speechsdk.SpeechConfig(auth_token=_get_speech_auth_token(), region=SPEECH_REGION)
    cfg.speech_recognition_language = SPEECH_RECOGNITION_LANGUAGE
    cfg.speech_synthesis_voice_name = SPEECH_SYNTHESIS_VOICE
    return cfg


# --------------------------------------------------------------------------------------
# STT (continuous) / TTS helpers — SDK calls are blocking, so run them in a thread
# --------------------------------------------------------------------------------------

def _start_continuous_recognition_sync(recognizer: speechsdk.SpeechRecognizer) -> None:
    recognizer.start_continuous_recognition_async().get()


def _stop_continuous_recognition_sync(recognizer: speechsdk.SpeechRecognizer) -> None:
    recognizer.stop_continuous_recognition_async().get()


def _synthesize_sync(text: str) -> Optional[bytes]:
    cfg = _new_speech_config()
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    # audio_config=None -> don't play to a local speaker (there isn't one on a server);
    # the synthesized bytes come back on result.audio_data instead.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        log.error("TTS canceled: %s / %s", details.reason, details.error_details)
        return None
    return None


async def text_to_speech(text: str) -> Optional[bytes]:
    if not text:
        return None
    return await asyncio.to_thread(_synthesize_sync, text)


# --------------------------------------------------------------------------------------
# Foundry agent call (text in / text out, Responses API — not Voice Live).
# Multi-turn continuity via previous_response_id chaining — see module docstring.
# --------------------------------------------------------------------------------------

def _usage_to_dict(usage) -> dict:
    """Normalizes whatever `.usage` shape the Responses API gives back into a plain
    dict. Different SDK versions/backends have used input_tokens/output_tokens vs
    prompt_tokens/completion_tokens, so we check both rather than assuming."""
    if usage is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    def _get(*names):
        for name in names:
            val = getattr(usage, name, None)
            if val is not None:
                return val
        return None

    input_tokens = _get("input_tokens", "prompt_tokens")
    output_tokens = _get("output_tokens", "completion_tokens")
    total_tokens = _get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _ask_agent_sync(text: str, previous_response_id: Optional[str]) -> tuple[str, str, dict]:
    kwargs: dict = {"input": text}
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    response = _openai_client.responses.create(**kwargs)
    tokens = _usage_to_dict(getattr(response, "usage", None))
    return response.output_text, response.id, tokens


async def ask_agent(text: str, previous_response_id: Optional[str]) -> tuple[str, str, dict]:
    return await asyncio.to_thread(_ask_agent_sync, text, previous_response_id)


# --------------------------------------------------------------------------------------
# SessionLogger — per-session JSON transcript + token usage, plus terminal echo
# --------------------------------------------------------------------------------------

class SessionLogger:
    """Owns one JSON file per session under SESSION_LOG_DIR. The file is rewritten
    (atomically, via a temp-file + rename) after every turn, so it's always valid
    JSON on disk even if the process dies mid-session — no partial/corrupt writes."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.ended_at: Optional[str] = None
        self.turns: list[dict] = []
        self.path = SESSION_LOG_DIR / f"{session_id}.json"
        self._lock = asyncio.Lock()

    def _snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "turn_count": len(self.turns),
            "turns": self.turns,
        }

    def _write_sync(self, data: dict) -> None:
        tmp_path = self.path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)  # atomic rename on the same filesystem

    async def _flush(self) -> None:
        data = self._snapshot()
        async with self._lock:
            await asyncio.to_thread(self._write_sync, data)

    async def log_turn(
        self,
        *,
        user_text: str,
        agent_text: str,
        tokens: dict,
        latency_ms: int,
        interrupted: bool = False,
    ) -> None:
        turn = {
            "turn": len(self.turns) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_text": user_text,
            "agent_text": agent_text,
            "tokens": tokens,
            "latency_ms": latency_ms,
            "interrupted": interrupted,
        }
        self.turns.append(turn)
        await self._flush()
        self._print_turn(turn)

    def _print_turn(self, turn: dict) -> None:
        t = turn["tokens"] or {}
        short_id = self.session_id[:8]
        print(f"\n[{short_id}] turn {turn['turn']}  {turn['timestamp']}  ({turn['latency_ms']} ms)")
        print(f"  You:   {turn['user_text']}")
        print(f"  Agent: {turn['agent_text']}")
        print(
            f"  Tokens: input={t.get('input_tokens')}  "
            f"output={t.get('output_tokens')}  total={t.get('total_tokens')}"
        )

    async def close(self) -> None:
        self.ended_at = datetime.now(timezone.utc).isoformat()
        await self._flush()
        log.info("[%s] session log finalized: %s", self.session_id, self.path)


# --------------------------------------------------------------------------------------
# ChatSession — one per WebSocket connection, lives for its duration
# --------------------------------------------------------------------------------------

class ChatSession:
    def __init__(self, session_id: str, ws: WebSocket) -> None:
        self.session_id = session_id
        self.ws = ws
        # The prior turn's response.id, chained into the next call via
        # previous_response_id. None until the first successful turn on this
        # connection — a new connection always starts a fresh agent context.
        self.last_response_id: Optional[str] = None
        self.logger = SessionLogger(session_id)
        # The task currently running the agent -> TTS pipeline for a turn, if any.
        # Tracked so a fresh recognizing (barge-in) event can cancel it.
        self._current_turn_task: Optional[asyncio.Task] = None

        # Continuous-recognition plumbing.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._event_consumer_task: Optional[asyncio.Task] = None
        self._recognizer: Optional[speechsdk.SpeechRecognizer] = None
        self._push_stream: Optional[speechsdk.audio.PushAudioInputStream] = None
        self._listening = False

        # True while the CLIENT is actually playing back agent audio. This is distinct
        # from `_current_turn_task` being in flight: that task finishes as soon as the
        # agent_audio message is *sent*, long before the client is done playing a
        # multi-second clip. Without this, barge-in only worked during "thinking"/
        # "synthesizing" and silently no-op'd for the (common) case of the user talking
        # over actual audio playback. Driven by playback_started/playback_ended acks
        # from the client (see run()).
        self._agent_speaking = False

    async def _send(self, msg: dict) -> None:
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass

    async def init(self) -> None:
        await self._send({
            "type": "session_ready",
            "session_id": self.session_id,
        })
        # Establish the log file on disk immediately (0 turns) so it exists for the
        # full lifetime of the session, not just once a turn completes.
        await self.logger._flush()

    def append_audio(self, chunk: bytes) -> None:
        if self._push_stream is not None:
            self._push_stream.write(chunk)
        # else: not currently listening (start/stop race) — drop.

    # ---- continuous recognition: SDK-thread callbacks -> asyncio bridge --------------

    def _queue_event(self, event: dict) -> None:
        """Called on the Speech SDK's own worker thread. Only plain values are put on
        the queue — never SDK objects — since they're about to cross a thread
        boundary. call_soon_threadsafe + put_nowait is a lightweight, non-blocking
        handoff onto the session's asyncio loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._event_queue.put_nowait, event)

    def _build_recognizer(self) -> None:
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=INPUT_SAMPLE_RATE, bits_per_sample=16, channels=1
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
        # TODO: the auth token below is fetched once and never refreshed for the life
        # of this recognizer. A listening session that runs longer than the Entra
        # token's ~60-90 min validity will start failing with `canceled` events.
        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=_new_speech_config(), audio_config=audio_config
        )

        def _on_recognizing(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            self._queue_event({"type": "recognizing"})

        def _on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            text = evt.result.text if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech else None
            self._queue_event({"type": "recognized", "text": text})

        def _on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
            details = f"{evt.cancellation_details.reason} / {evt.cancellation_details.error_details}"
            self._queue_event({"type": "canceled", "details": details})

        self._recognizer.recognizing.connect(_on_recognizing)
        self._recognizer.recognized.connect(_on_recognized)
        self._recognizer.canceled.connect(_on_canceled)

    async def _consume_recognition_events(self) -> None:
        while True:
            event = await self._event_queue.get()
            kind = event["type"]
            if kind == "recognizing":
                await self._on_interim()
            elif kind == "recognized":
                text = event.get("text")
                if text:
                    self._start_turn_task(text)
            elif kind == "canceled":
                log.error("[%s] continuous recognition canceled: %s", self.session_id, event.get("details"))
                await self._send({"type": "error", "message": "speech recognition error"})

    async def _on_interim(self) -> None:
        """Fires the instant the user starts talking again (Speech SDK's interim
        `recognizing` event) — even while a previous turn is still "thinking" or
        "synthesizing", or while the client is playing back agent audio. Cancels the
        in-flight turn (if any) and/or tells the client to stop playback (if any) so
        the user isn't kept waiting on, and never receives, a response to an utterance
        they've already moved past. Does NOT touch the recognizer/push stream —
        recognition keeps running throughout the whole listening session."""
        turn_running = self._current_turn_task is not None and not self._current_turn_task.done()
        if not turn_running and not self._agent_speaking:
            return

        log.info("[%s] barge-in: interim speech detected (turn_running=%s, agent_speaking=%s)",
                  self.session_id, turn_running, self._agent_speaking)
        if turn_running:
            self._current_turn_task.cancel()
        self._agent_speaking = False
        await self._send({"type": "status", "text": "interrupted"})

    # ---- listening lifecycle ----------------------------------------------------------

    async def handle_start_listening(self) -> None:
        if self._listening:
            return
        self._build_recognizer()
        await asyncio.to_thread(_start_continuous_recognition_sync, self._recognizer)
        self._listening = True
        await self._send({"type": "status", "text": "listening"})
        log.info("[%s] continuous recognition started", self.session_id)

    async def handle_stop_listening(self) -> None:
        if not self._listening:
            return
        self._listening = False
        try:
            await asyncio.to_thread(_stop_continuous_recognition_sync, self._recognizer)
        except Exception:
            log.exception("[%s] error stopping continuous recognition", self.session_id)
        if self._push_stream is not None:
            self._push_stream.close()
        self._recognizer = None
        self._push_stream = None
        await self._send({"type": "status", "text": "idle"})
        log.info("[%s] continuous recognition stopped", self.session_id)

    # ---- turn processing (agent + TTS) -------------------------------------------------

    def _on_turn_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            log.info("[%s] turn task cancelled (barge-in)", self.session_id)
            return
        exc = task.exception()
        if exc is not None:
            log.exception("[%s] turn task failed", self.session_id, exc_info=exc)

    async def _process_turn(self, user_text: str) -> None:
        start_time = time.monotonic()

        await self._send({"type": "user_text", "text": user_text})

        await self._send({"type": "status", "text": "thinking"})
        try:
            # No timeout here, deliberately — the agent may take as long as it needs;
            # only barge-in (self._current_turn_task.cancel()) cuts this off.
            agent_text, response_id, tokens = await ask_agent(user_text, self.last_response_id)
        except Exception:
            # Covers Exception, not (Base)CancelledError, so a barge-in cancellation
            # still propagates and isn't mistaken for an agent failure.
            log.exception("[%s] agent call failed", self.session_id)
            await self._send({"type": "error", "message": "agent request failed"})
            await self._send({"type": "status", "text": "ready"})
            return

        # Chain the next turn off this response. Only updated on success — a barge-in
        # cancellation or failure above leaves the last known-good id in place, so a
        # discarded turn can never break continuity for the one after it.
        self.last_response_id = response_id

        await self._send({"type": "agent_text", "text": agent_text, "response_id": response_id})

        await self._send({"type": "status", "text": "synthesizing"})
        try:
            audio = await asyncio.wait_for(text_to_speech(agent_text), timeout=TTS_CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.error("[%s] TTS call timed out after %.0fs", self.session_id, TTS_CALL_TIMEOUT_S)
            audio = None
        except Exception:
            log.exception("[%s] TTS call failed", self.session_id)
            audio = None

        latency_ms = int((time.monotonic() - start_time) * 1000)

        if audio:
            # Sent as a JSON control message with base64 payload so it can't be
            # confused with an inbound binary audio frame on the same socket.
            await self._send({
                "type": "agent_audio",
                "format": "mp3",
                "audio_base64": base64.b64encode(audio).decode(),
            })
        else:
            await self._send({"type": "error", "message": "speech synthesis failed"})

        await self._send({"type": "status", "text": "ready"})

        # If we get here the turn completed normally (wasn't cancelled) — log it.
        await self.logger.log_turn(
            user_text=user_text,
            agent_text=agent_text,
            tokens=tokens,
            latency_ms=latency_ms,
        )

    def _start_turn_task(self, user_text: str) -> None:
        # Defensive: if somehow a turn task is still running, cancel it first so we
        # never have two turns racing to send responses on the same socket.
        if self._current_turn_task is not None and not self._current_turn_task.done():
            self._current_turn_task.cancel()

        task = asyncio.create_task(self._process_turn(user_text))
        task.add_done_callback(self._on_turn_task_done)
        self._current_turn_task = task

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.init()
        self._event_consumer_task = asyncio.create_task(self._consume_recognition_events())
        try:
            while True:
                message = await self.ws.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if message.get("bytes") is not None:
                    self.append_audio(message["bytes"])
                    continue

                if message.get("text") is not None:
                    try:
                        control = json.loads(message["text"])
                    except json.JSONDecodeError:
                        continue

                    msg_type = control.get("type")
                    if msg_type == "start_listening":
                        await self.handle_start_listening()
                    elif msg_type == "stop_listening":
                        await self.handle_stop_listening()
                    elif msg_type == "playback_started":
                        self._agent_speaking = True
                    elif msg_type == "playback_ended":
                        self._agent_speaking = False
                    elif msg_type == "ping":
                        await self._send({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("[%s] session error", self.session_id)
            await self._send({"type": "error", "message": "internal server error"})
        finally:
            await self.handle_stop_listening()
            if self._event_consumer_task is not None:
                self._event_consumer_task.cancel()
            if self._current_turn_task is not None and not self._current_turn_task.done():
                self._current_turn_task.cancel()
            await self.logger.close()
            log.info("[%s] session ended", self.session_id)


# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------

app = FastAPI(title="STT/TTS Chat Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active sessions, keyed by session_id — useful for introspection/metrics; the
# session's actual lifetime is tied to its WebSocket connection, not this dict.
active_sessions: dict[str, ChatSession] = {}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "active_sessions": len(active_sessions)}


@app.on_event("startup")
async def startup() -> None:
    _startup_credential_check()
    log.info("session logs will be written to: %s", SESSION_LOG_DIR.resolve())


@app.on_event("shutdown")
async def shutdown() -> None:
    # Flush every still-open session log so nothing is lost on a clean shutdown.
    for session in list(active_sessions.values()):
        try:
            if session._current_turn_task is not None and not session._current_turn_task.done():
                session._current_turn_task.cancel()
            await session.logger.close()
        except Exception:
            log.exception("[%s] failed to close session log on shutdown", session.session_id)
    _credential.close()  # sync credential -> sync close, not awaitable


@app.websocket("/chat")
async def chat_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid.uuid4())
    session = ChatSession(session_id, ws)
    active_sessions[session_id] = session
    log.info("[%s] session created", session_id)

    try:
        await session.run()
    finally:
        active_sessions.pop(session_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 3001)))
