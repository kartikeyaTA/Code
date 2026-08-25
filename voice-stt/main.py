"""
main.py — Chat backend: browser mic -> STT -> Foundry Agent (text) -> TTS -> browser.

Architecture (per spec):
    1. Client opens ws://.../chat -> a new session is created and held server-side
       for the lifetime of that connection.
    2. Client streams raw PCM16 mono audio as binary WebSocket frames while the user
       talks.
    3. Client sends {"type": "end_turn"} once streaming is done. Backend runs the
       buffered audio through Speech-to-Text as a single batch recognition.
    4. Backend sends the transcript back to the client for display, then sends that
       text to the Foundry agent (Responses API, text in / text out — NOT Voice Live).
    5. Backend runs the agent's reply through Text-to-Speech and sends the audio back,
       along with the reply text.

Auth model — Cognitive Services Speech User only (no key):
    Speech SDK doesn't take a plain Entra token directly. Per Microsoft's documented
    pattern, you build a special authorization token string of the form
    "aad#<speech-resource-ARM-ID>#<entra-access-token>" and refresh it before each
    call (Entra tokens are typically valid ~60-90 min; we refresh proactively).
    Confirmed against the installed SDK: SpeechConfig(auth_token=..., region=...) and
    both SpeechRecognizer/SpeechSynthesizer expose a settable .authorization_token.

    The Foundry agent call uses the same DefaultAzureCredential via azure-ai-projects'
    AIProjectClient.get_openai_client(), which returns a client whose .responses.create()
    / .conversations.create() are the documented way to call a Foundry agent by name.

Install:
    pip install -r requirements.txt

Run:
    uvicorn main:app --host 0.0.0.0 --port 3001 --reload
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import time
import uuid
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
SPEECH_RESOURCE_ID = "/subscriptions/7514ef27-0355-4347-b91e-6c0085baa70a/resourceGroups/txrh_tiger_rg/providers/Microsoft.CognitiveServices/accounts/txrhspeechservice"         # full ARM resource ID
SPEECH_RECOGNITION_LANGUAGE = os.environ.get("SPEECH_RECOGNITION_LANGUAGE", "en-US")
SPEECH_SYNTHESIS_VOICE = os.environ.get("SPEECH_SYNTHESIS_VOICE", "en-US-AvaNeural")

# Audio format the FRONTEND must send: raw PCM16 mono at this sample rate.
INPUT_SAMPLE_RATE = int(os.environ.get("INPUT_SAMPLE_RATE", "16000"))

FOUNDRY_PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"  # https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_AGENT_NAME = "txrh-demoagent-2-copy1352324"

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

SPEECH_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

# --------------------------------------------------------------------------------------
# Shared credentials / clients (module-level, reused across sessions)
# --------------------------------------------------------------------------------------

_credential = DefaultAzureCredential()

_project_client = AIProjectClient(endpoint=FOUNDRY_PROJECT_ENDPOINT, credential=_credential)
_openai_client = _project_client.get_openai_client(agent_name=FOUNDRY_AGENT_NAME)

# Speech auth token cache — the "aad#<resourceId>#<token>" string, refreshed proactively.
_speech_token_cache: dict = {"value": None, "expires_at": 0.0}


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
    documented Microsoft Entra auth format — a bare Entra token is not accepted as-is."""
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
# STT / TTS helpers — SDK calls are blocking, so run them in a thread
# --------------------------------------------------------------------------------------

def _recognize_sync(audio_bytes: bytes) -> speechsdk.SpeechRecognitionResult:
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=INPUT_SAMPLE_RATE, bits_per_sample=16, channels=1
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=_new_speech_config(), audio_config=audio_config
    )

    push_stream.write(audio_bytes)
    push_stream.close()

    return recognizer.recognize_once_async().get()


async def speech_to_text(audio_bytes: bytes) -> Optional[str]:
    if not audio_bytes:
        return None
    result = await asyncio.to_thread(_recognize_sync, audio_bytes)

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    if result.reason == speechsdk.ResultReason.NoMatch:
        log.info("STT: no speech recognized")
        return None
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        log.error("STT canceled: %s / %s", details.reason, details.error_details)
        return None
    return None


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
# Foundry agent call (text in / text out, Responses API — not Voice Live)
# --------------------------------------------------------------------------------------

def _ask_agent_sync(conversation_id: str, text: str) -> str:
    response = _openai_client.responses.create(conversation=conversation_id, input=text)
    return response.output_text


async def ask_agent(conversation_id: str, text: str) -> str:
    return await asyncio.to_thread(_ask_agent_sync, conversation_id, text)


def _create_conversation_sync() -> str:
    return _openai_client.conversations.create().id


async def create_conversation() -> str:
    return await asyncio.to_thread(_create_conversation_sync)


# --------------------------------------------------------------------------------------
# ChatSession — one per WebSocket connection, lives for its duration
# --------------------------------------------------------------------------------------

class ChatSession:
    def __init__(self, session_id: str, ws: WebSocket) -> None:
        self.session_id = session_id
        self.ws = ws
        self.conversation_id: Optional[str] = None
        self._audio_buffer = bytearray()

    async def _send(self, msg: dict) -> None:
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass

    async def init(self) -> None:
        self.conversation_id = await create_conversation()
        await self._send({
            "type": "session_ready",
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
        })

    def append_audio(self, chunk: bytes) -> None:
        self._audio_buffer.extend(chunk)

    async def handle_end_turn(self) -> None:
        audio_bytes = bytes(self._audio_buffer)
        self._audio_buffer.clear()

        if not audio_bytes:
            await self._send({"type": "error", "message": "no audio received for this turn"})
            return

        await self._send({"type": "status", "text": "transcribing"})
        user_text = await speech_to_text(audio_bytes)

        if not user_text:
            await self._send({"type": "error", "message": "could not transcribe audio"})
            return

        await self._send({"type": "user_text", "text": user_text})

        await self._send({"type": "status", "text": "thinking"})
        agent_text = await ask_agent(self.conversation_id, user_text)
        await self._send({"type": "agent_text", "text": agent_text})

        await self._send({"type": "status", "text": "synthesizing"})
        audio = await text_to_speech(agent_text)
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

    async def run(self) -> None:
        await self.init()
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
                    if msg_type == "end_turn":
                        await self.handle_end_turn()
                    elif msg_type == "start_turn":
                        self._audio_buffer.clear()
                    elif msg_type == "ping":
                        await self._send({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("[%s] session error", self.session_id)
            await self._send({"type": "error", "message": "internal server error"})
        finally:
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


@app.on_event("shutdown")
async def shutdown() -> None:
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
