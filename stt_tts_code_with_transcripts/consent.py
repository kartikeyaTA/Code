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

OAuth Identity Passthrough (consent):
    The Foundry agent (`voice-agent`) authenticates its downstream tools (e.g. a
    ServiceNow MCP server) using OAuth Identity Passthrough. The first time a user
    hits a tool that needs their delegated consent, the agent does NOT answer — instead
    its Responses output contains an `oauth_consent_request` item carrying a
    `consent_link`. When we see that, we:
        * send the client a {"type": "consent", "link": ...} control frame so the UI
          can render the clickable authorization link,
        * SPEAK a short "please authorize…" prompt back over TTS (this is a voice UI,
          so the user should hear what to do, not just see a link),
        * skip the normal answer/logging for that turn.
    After the user completes consent in the browser and re-asks, the same conversation
    resumes and the agent answers normally. Detecting the consent item requires the
    AIProjectClient to be created with allow_preview=True.

Barge-in:
    The user can press-and-hold the mic button again at any point — even while the
    agent is still "thinking" or "synthesizing", or while the client is playing back
    the agent's audio. Client-side, this immediately stops any agent audio that's
    playing. Server-side, sending a new {"type": "start_turn"} cancels whatever turn
    is currently in flight (the STT/agent/TTS pipeline for the *previous* turn) before
    the new turn starts recording. This is turn-level barge-in: it can't interrupt a
    blocking Speech SDK call mid-flight (the SDK calls run in a worker thread and keep
    running to completion in the background), but it guarantees the *user* never has
    to wait for a stale turn, and stale results are discarded rather than sent to the
    client or logged.

Session logging:
    Every session gets its own JSON transcript at SESSION_LOG_DIR/<session_id>.json,
    rewritten atomically after every turn, containing the user text, agent text, and
    token usage for that turn. The same information is also printed to the terminal
    as it happens.

Auth model — HARDCODED TOKENS (dev/test only):
    For now, both the Speech and the Foundry calls authenticate with Microsoft Entra
    access tokens that are pasted directly into this file as variables
    (SPEECH_AUTH_TOKEN and FOUNDRY_AUTH_TOKEN).

    * Speech: the SDK does NOT accept a bare Entra token. Per Microsoft's documented
      pattern you must build "aad#<speech-resource-ARM-ID>#<entra-access-token>" and
      hand THAT to SpeechConfig / SpeechRecognizer / SpeechSynthesizer.
    * Foundry: azure-ai-projects' AIProjectClient expects a TokenCredential, so we wrap
      the hardcoded token in a tiny StaticTokenCredential that just returns it.

    NOTE: Entra access tokens are short-lived (~60-90 min). When they expire you'll get
    401s and must paste fresh ones. This is fine for a quick end-to-end test but is NOT
    a production auth model — put DefaultAzureCredential / Managed Identity back for that.

    Generate the two tokens with the Azure CLI:
        # Speech token (audience = cognitive services)
        az account get-access-token \
          --resource https://cognitiveservices.azure.com \
          --query accessToken -o tsv

        # Foundry token (audience = Azure AI). If Foundry rejects it, fall back to
        # --resource https://cognitiveservices.azure.com
        az account get-access-token \
          --resource https://ai.azure.com \
          --query accessToken -o tsv

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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import azure.cognitiveservices.speech as speechsdk
from azure.ai.projects import AIProjectClient
from azure.core.credentials import AccessToken
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("chat-backend")

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------

SPEECH_REGION = "eastus"  # e.g. "eastus"
SPEECH_RESOURCE_ID = (
    "/subscriptions/7514ef27-0355-4347-b91e-6c0085baa70a/resourceGroups/"
    "txrh_tiger_rg/providers/"
    "Microsoft.CognitiveServices/accounts/txrhspeechservice"
)  # full ARM resource ID
SPEECH_RECOGNITION_LANGUAGE = os.environ.get("SPEECH_RECOGNITION_LANGUAGE", "en-US")
SPEECH_SYNTHESIS_VOICE = os.environ.get("SPEECH_SYNTHESIS_VOICE", "en-US-AvaNeural")

# Audio format the FRONTEND must send: raw PCM16 mono at this sample rate.
INPUT_SAMPLE_RATE = int(os.environ.get("INPUT_SAMPLE_RATE", "16000"))

FOUNDRY_PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"

FOUNDRY_AGENT_NAME = "txrh-demoagent-2"
#SPEECH_ENDPOINT = "https://eastus.api.cognitive.microsoft.com/"

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Spoken back over TTS when the agent asks for OAuth consent. Kept short because the
# user still has to read + click the link in the UI; the audio is just a nudge.
CONSENT_PROMPT_TEXT = (
    "To continue, please authorize access to your account using the link on your "
    "screen. Once you've done that, come back and ask me again."
)

# Where per-session transcript+token JSON logs are written.
SESSION_LOG_DIR = Path(os.environ.get("SESSION_LOG_DIR", "session_logs"))
SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Hard cap on buffered turn audio so a client that never sends end_turn can't grow
# memory unbounded. ~2 minutes of 16kHz mono PCM16.
MAX_TURN_AUDIO_BYTES = int(os.environ.get("MAX_TURN_AUDIO_BYTES", str(2 * 60 * INPUT_SAMPLE_RATE * 2)))

# ======================================================================================
# HARDCODED ENTRA ACCESS TOKENS (dev/test only — paste fresh values here).
# Do NOT include the "aad#..." prefix in SPEECH_AUTH_TOKEN — that prefix is added below.
# ======================================================================================

FOUNDRY_AUTH_TOKEN ="eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6IlQ1aDQwcTdHMHg0OXFuNDFsTTkta0tqcEQ5OCIsImtpZCI6IlQ1aDQwcTdHMHg0OXFuNDFsTTkta0tqcEQ5OCJ9.eyJhdWQiOiJodHRwczovL2FpLmF6dXJlLmNvbSIsImlzcyI6Imh0dHBzOi8vc3RzLndpbmRvd3MubmV0L2U3MTRlZjMxLWZhYWItNDFkMi05ZjFlLWU2ZGY0YWYxNmFiOC8iLCJpYXQiOjE3ODgxNzUwMTUsIm5iZiI6MTc4ODE3NTAxNSwiZXhwIjoxNzg4MTc5MTcxLCJhY3IiOiIxIiwiYWlvIjoiQVhRQWkvOGNBQUFBcHgwdCthUWpRdzUxVWtrOWlqMWgvbnNGbXZHL1IrdU1PNTE4bHBBTkp3WXNrdHgzamFlMVRDZmVsTm00NHRST1I1emR5Sm5yV0hwVWplNHQxakRHTUVYeVVQbWQ2anZsRDZidjQ2SmZLc3NabXFiODdiMk8wMFRJcEtrRXRCWjNxWm5tQW5VL3A2N3NIcXRQNkx6NWtnPT0iLCJhbXIiOlsicHdkIiwibWZhIl0sImFwcGlkIjoiMDRiMDc3OTUtOGRkYi00NjFhLWJiZWUtMDJmOWUxYmY3YjQ2IiwiYXBwaWRhY3IiOiIwIiwiZmFtaWx5X25hbWUiOiJSIiwiZ2l2ZW5fbmFtZSI6IlByaXRoaXZlZSIsImdyb3VwcyI6WyI4MDE0ZWI2NC05YWQzLTRiMmEtOWZlNy00NDllYzQ2NzQ0YjIiLCJkMDExZDU2OC04MTRiLTRlY2UtODhjOC1iOGFiODIwZjdhOGIiXSwiaWR0eXAiOiJ1c2VyIiwiaXBhZGRyIjoiNDkuNDMuMTYzLjk1IiwibmFtZSI6IlByaXRoaXZlZSBSIiwib2lkIjoiNjU0NjI0OTUtMzI3ZC00MjExLWJkMjEtMzY5ZTZiMWEyZDMwIiwicHVpZCI6IjEwMDMyMDAzODE5QkJEQTgiLCJyaCI6IjEuQVZZQU1lOFU1NnY2MGtHZkh1YmZTdkZxdUY5dnBoamYyeGRNbmRjV05IRXFuTDRBQUxCV0FBLiIsInNjcCI6InVzZXJfaW1wZXJzb25hdGlvbiIsInNpZCI6IjAwN2VkNGRhLTljN2EtNjY2Mi1mMzllLTc3MGI2MmU4ZjBlNiIsInN1YiI6IlNTNTlPNmxISFJfV2RSdnRBVnYwSl93SUtFMFdpM2NmV19IQ2dMQVFHelUiLCJ0aWQiOiJlNzE0ZWYzMS1mYWFiLTQxZDItOWYxZS1lNmRmNGFmMTZhYjgiLCJ1bmlxdWVfbmFtZSI6InByaXRoaXZlZS5yQHRpZ2VyYW5hbHl0aWNzLmNvbSIsInVwbiI6InByaXRoaXZlZS5yQHRpZ2VyYW5hbHl0aWNzLmNvbSIsInV0aSI6IkF5bGNwTVVmS0VHbTEtU1ZGUUlrQUEiLCJ2ZXIiOiIxLjAiLCJ4bXNfYWN0X2ZjdCI6IjUgMyIsInhtc19mdGQiOiJXX2ViSUpLSnZmdjB4dFVlbnFSX1F2M05WWjVnYUtZUDNtUXY3aEpHbS1zQmEyOXlaV0Z6YjNWMGFDMWtjMjF6IiwieG1zX2lkcmVsIjoiMSAyOCIsInhtc19zdWJfZmN0IjoiMTAgMyJ9.BOhXNIKHqLxtPuIe76BptiGmBnohQhZkbp1ib4Rthtcx4NTETNloUUhOANBQ2b58hHV7KFq2h9QA3DHSDcFkKz756TxsXziVjNo6y1DyxZZunzQ2s3zUJoxPKJQ0l2T_USgRnGsul3q0M7UHjpvNQJeTcUPSFUNfFGZZAzkDQPyRCdUWnY92pfOJfQIACFECFk0PK4jzmmYUFkSdop81M2qSvgLQR1bnbq8F_-ovZ9aOG-CY5Ej3-sBE93knyUgcd6oiQqH-K7UgFHqAZnJ5_LxhFLdRrV-uOCwlwJ1ckqzH5EhRik5A79RUGnjZ3TYu1a1KnoRvSyitHCl3IIN0Wg"
SPEECH_AUTH_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6IlQ1aDQwcTdHMHg0OXFuNDFsTTkta0tqcEQ5OCIsImtpZCI6IlQ1aDQwcTdHMHg0OXFuNDFsTTkta0tqcEQ5OCJ9.eyJhdWQiOiJodHRwczovL2NvZ25pdGl2ZXNlcnZpY2VzLmF6dXJlLmNvbSIsImlzcyI6Imh0dHBzOi8vc3RzLndpbmRvd3MubmV0L2U3MTRlZjMxLWZhYWItNDFkMi05ZjFlLWU2ZGY0YWYxNmFiOC8iLCJpYXQiOjE3ODgxNzQ5OTYsIm5iZiI6MTc4ODE3NDk5NiwiZXhwIjoxNzg4MTc5NDYzLCJhY3IiOiIxIiwiYWlvIjoiQVhRQWkvOGNBQUFBNnh5SHV6YkVjUUxRZWJIME5hQ3h4Tjk2Nnl5a3RqYTMvSVhzdXU2b1A3dDlsZmZiUzYvUmIyZmJTVTQySGpUL2FMaDBTTlhFSXlHM3Q3UjIvTWg2bWVmOTZPTzZkcmhsTUJaTzNOWDlSRzFvdDd6Qk9aNSttVEcycEhNYVBGRURNUmhHVm9YYzZyOXc0SFpPNDd6UWJBPT0iLCJhbXIiOlsicHdkIiwibWZhIl0sImFwcGlkIjoiMDRiMDc3OTUtOGRkYi00NjFhLWJiZWUtMDJmOWUxYmY3YjQ2IiwiYXBwaWRhY3IiOiIwIiwiZmFtaWx5X25hbWUiOiJSIiwiZ2l2ZW5fbmFtZSI6IlByaXRoaXZlZSIsImdyb3VwcyI6WyI4MDE0ZWI2NC05YWQzLTRiMmEtOWZlNy00NDllYzQ2NzQ0YjIiLCJkMDExZDU2OC04MTRiLTRlY2UtODhjOC1iOGFiODIwZjdhOGIiXSwiaWR0eXAiOiJ1c2VyIiwiaXBhZGRyIjoiNDkuNDMuMTYzLjk1IiwibmFtZSI6IlByaXRoaXZlZSBSIiwib2lkIjoiNjU0NjI0OTUtMzI3ZC00MjExLWJkMjEtMzY5ZTZiMWEyZDMwIiwicHVpZCI6IjEwMDMyMDAzODE5QkJEQTgiLCJyaCI6IjEuQVZZQU1lOFU1NnY2MGtHZkh1YmZTdkZxdUpBaU1YM0lLRHhIb08yT1UzU2JiVzBBQUxCV0FBLiIsInNjcCI6InVzZXJfaW1wZXJzb25hdGlvbiIsInNpZCI6IjAwN2VkNGRhLTljN2EtNjY2Mi1mMzllLTc3MGI2MmU4ZjBlNiIsInN1YiI6Ik5iNl9iREdqbVQ3aERSdmloQW1ONi05WjZKZWR3ZEdDdTQxbG1LVXUtSGsiLCJ0aWQiOiJlNzE0ZWYzMS1mYWFiLTQxZDItOWYxZS1lNmRmNGFmMTZhYjgiLCJ1bmlxdWVfbmFtZSI6InByaXRoaXZlZS5yQHRpZ2VyYW5hbHl0aWNzLmNvbSIsInVwbiI6InByaXRoaXZlZS5yQHRpZ2VyYW5hbHl0aWNzLmNvbSIsInV0aSI6Im00c3I1c1hOZFVxekg0Mnh3ckIyQUEiLCJ2ZXIiOiIxLjAiLCJ3aWRzIjpbImI3OWZiZjRkLTNlZjktNDY4OS04MTQzLTc2YjE5NGU4NTUwOSJdLCJ4bXNfYWN0X2ZjdCI6IjUgMyIsInhtc19mdGQiOiJLakd5X0tmQXVMbFEtcFE2NGRVeFZzRmxVNXo5QWsyY01xSnRlV0lLSndRQmFtRndZVzVsWVhOMExXUnpiWE0iLCJ4bXNfaWRyZWwiOiIxIDI4IiwieG1zX3N1Yl9mY3QiOiIzIDEyIn0.NhIeZuHNI4HK5n9eWUtC-TAavqmVg0l1j4ceq-u1ldc9AeURoufSTB_1I_wpnx3lgftCZNuHqQ18qF-oOd65vk46lWdy5nUyzNhcbLHjqxciCzNc8fp-Mbk8gxX6ehRb6Qhz9POCnuI6vgZN2R2i57nsJe3fMqeTgHs3lLIf_YGHMfPnNVLsOPXTkAs4Fkdle5Fyo5kX1M0JbJlPMCu2CcE1-xczez4lZFAVbl88bLmlsAwSatcDu8Do6jLfislQmRR5f_3ZssdEdgN4t4wobckKtY1fWgG5Xl85TcgHiP4rYCo3G5i-sAkzckuEx3PivC287ZBlmPolywTEMchNag"

# --------------------------------------------------------------------------------------
# Static (hardcoded) token credential for Foundry
# --------------------------------------------------------------------------------------

class StaticTokenCredential:
    """Minimal TokenCredential that always returns a pre-supplied Entra access token.

    AIProjectClient / the OpenAI client only ever call `.get_token(*scopes)` and read
    `.token` off the result, so this is enough to feed them a hardcoded token. The
    reported expiry is cosmetic (the real expiry is baked into the JWT); if the token
    is actually expired the service returns 401 regardless of what we claim here."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        # Claim ~1h of validity so the client libraries don't try anything clever.
        return AccessToken(self._token, int(time.time()) + 3600)

    def close(self) -> None:  # AIProjectClient calls close() on shutdown
        pass


# --------------------------------------------------------------------------------------
# Shared clients (module-level, reused across sessions)
# --------------------------------------------------------------------------------------

_foundry_credential = StaticTokenCredential(FOUNDRY_AUTH_TOKEN)

# allow_preview=True is REQUIRED for the OAuth Identity Passthrough consent item
# (`oauth_consent_request`) to be surfaced in the Responses output.
_project_client = AIProjectClient(
    endpoint=FOUNDRY_PROJECT_ENDPOINT,
    credential=_foundry_credential,
    allow_preview=True,
)
_openai_client = _project_client.get_openai_client(agent_name=FOUNDRY_AGENT_NAME)


def _startup_token_check() -> None:
    """Fails fast and loudly if the hardcoded tokens still contain the placeholder
    strings, instead of only surfacing an auth error deep inside the first session."""
    problems = []
    if not SPEECH_AUTH_TOKEN or SPEECH_AUTH_TOKEN.startswith("PASTE_"):
        problems.append("SPEECH_AUTH_TOKEN is not set")
    if not FOUNDRY_AUTH_TOKEN or FOUNDRY_AUTH_TOKEN.startswith("PASTE_"):
        problems.append("FOUNDRY_AUTH_TOKEN is not set")
    if problems:
        raise RuntimeError(
            "Hardcoded token(s) missing: "
            + "; ".join(problems)
            + ". Paste fresh Entra access tokens into main.py (or set them as env vars)."
        )
    log.info("Hardcoded Speech + Foundry tokens present — ready to serve /chat")


def _get_speech_auth_token() -> str:
    """Returns the 'aad#<resourceId>#<token>' string that the Speech service requires.
    We build it from the hardcoded SPEECH_AUTH_TOKEN on every call so that if you paste
    a fresh token and hit --reload, the new one is picked up. Building this string is
    REQUIRED by the Speech service's documented Microsoft Entra auth format — a bare
    Entra token is not accepted as-is."""
    return f"aad#{SPEECH_RESOURCE_ID}#{SPEECH_AUTH_TOKEN}"


# def _new_speech_config() -> speechsdk.SpeechConfig:
#     cfg = speechsdk.SpeechConfig(endpoint=SPEECH_ENDPOINT)
#     cfg.authorization_token = _get_speech_auth_token()
#     cfg.speech_recognition_language = SPEECH_RECOGNITION_LANGUAGE
#     cfg.speech_synthesis_voice_name = SPEECH_SYNTHESIS_VOICE
#     return cfg

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


def find_consent_item(response) -> Optional[dict]:
    """Returns the `oauth_consent_request` item if the Foundry agent emitted one
    (OAuth Identity Passthrough). Accepts either a response object or an already-dumped
    dict. Same detection logic as the streaming text backend."""
    try:
        if isinstance(response, dict):
            items = response.get("output") or []
        else:
            items = response.model_dump().get("output") or []
    except Exception:
        return None

    for item in items:
        if isinstance(item, dict) and item.get("type") == "oauth_consent_request":
            return item

    return None


def _ask_agent_sync(conversation_id: str, text: str) -> tuple[str, dict, Optional[str]]:
    """Calls the agent and returns (output_text, tokens, consent_link).

    consent_link is None on a normal answer, or the authorization URL when the agent
    responded with an OAuth Identity Passthrough consent request instead of answering."""
    response = _openai_client.responses.create(conversation=conversation_id, input=text)
    tokens = _usage_to_dict(getattr(response, "usage", None))

    consent = find_consent_item(response)
    if consent:
        consent_link = str(consent.get("consent_link") or "")
        return "", tokens, consent_link

    return response.output_text, tokens, None


async def ask_agent(conversation_id: str, text: str) -> tuple[str, dict, Optional[str]]:
    return await asyncio.to_thread(_ask_agent_sync, conversation_id, text)


def _create_conversation_sync() -> str:
    return _openai_client.conversations.create().id


async def create_conversation() -> str:
    return await asyncio.to_thread(_create_conversation_sync)


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
        consent_required: bool = False,
        consent_link: Optional[str] = None,
    ) -> None:
        turn = {
            "turn": len(self.turns) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_text": user_text,
            "agent_text": agent_text,
            "tokens": tokens,
            "latency_ms": latency_ms,
            "interrupted": interrupted,
            "consent_required": consent_required,
            "consent_link": consent_link,
        }
        self.turns.append(turn)
        await self._flush()
        self._print_turn(turn)

    def _print_turn(self, turn: dict) -> None:
        t = turn["tokens"] or {}
        short_id = self.session_id[:8]
        print(f"\n[{short_id}] turn {turn['turn']}  {turn['timestamp']}  ({turn['latency_ms']} ms)")
        print(f"  You:   {turn['user_text']}")
        if turn.get("consent_required"):
            print(f"  Agent: [CONSENT REQUIRED] {turn.get('consent_link')}")
        else:
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
        self.conversation_id: Optional[str] = None
        self._audio_buffer = bytearray()
        self.logger = SessionLogger(session_id)
        # The task currently running the STT -> agent -> TTS pipeline for a turn, if
        # any. Tracked so a fresh start_turn (barge-in) can cancel it.
        self._current_turn_task: Optional[asyncio.Task] = None

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
        # Establish the log file on disk immediately (0 turns) so it exists for the
        # full lifetime of the session, not just once a turn completes.
        await self.logger._flush()

    def append_audio(self, chunk: bytes) -> None:
        if len(self._audio_buffer) + len(chunk) > MAX_TURN_AUDIO_BYTES:
            log.warning("[%s] turn audio exceeded cap, dropping extra bytes", self.session_id)
            return
        self._audio_buffer.extend(chunk)

    async def handle_start_turn(self) -> None:
        """Called when the client presses the mic button. If a previous turn is
        still being processed (transcribing / thinking / synthesizing), this is a
        barge-in: cancel it so the user isn't kept waiting on, and never receives,
        a response to a turn they've already moved past."""
        if self._current_turn_task is not None and not self._current_turn_task.done():
            log.info("[%s] barge-in: cancelling in-flight turn", self.session_id)
            self._current_turn_task.cancel()
            await self._send({"type": "status", "text": "interrupted"})
        self._audio_buffer.clear()

    def _on_turn_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            log.info("[%s] turn task cancelled (barge-in)", self.session_id)
            return
        exc = task.exception()
        if exc is not None:
            log.exception("[%s] turn task failed", self.session_id, exc_info=exc)

    async def _handle_consent(
        self, *, user_text: str, consent_link: str, tokens: dict, start_time: float
    ) -> None:
        """The agent asked for OAuth consent instead of answering. Tell the UI (so it
        can render the link), speak a short prompt, log the turn, and stop here — no
        normal answer for this turn. The user re-asks after authorizing."""
        log.info("[%s] agent requires OAuth consent", self.session_id)

        # Control frame the frontend uses to render the clickable authorization link.
        await self._send({
            "type": "consent",
            "link": consent_link,
            "text": CONSENT_PROMPT_TEXT,
        })

        # Speak the nudge so the voice user knows to look at / click the link.
        audio = await text_to_speech(CONSENT_PROMPT_TEXT)
        if audio:
            await self._send({
                "type": "agent_audio",
                "format": "mp3",
                "audio_base64": base64.b64encode(audio).decode(),
            })

        await self._send({"type": "status", "text": "ready"})

        latency_ms = int((time.monotonic() - start_time) * 1000)
        await self.logger.log_turn(
            user_text=user_text,
            agent_text=CONSENT_PROMPT_TEXT,
            tokens=tokens,
            latency_ms=latency_ms,
            consent_required=True,
            consent_link=consent_link,
        )

    async def handle_end_turn(self) -> None:
        start_time = time.monotonic()
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
        agent_text, tokens, consent_link = await ask_agent(self.conversation_id, user_text)

        # OAuth Identity Passthrough: agent returned a consent request, not an answer.
        if consent_link:
            await self._handle_consent(
                user_text=user_text,
                consent_link=consent_link,
                tokens=tokens,
                start_time=start_time,
            )
            return

        await self._send({"type": "agent_text", "text": agent_text})

        await self._send({"type": "status", "text": "synthesizing"})
        audio = await text_to_speech(agent_text)

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

    def _start_turn_task(self) -> None:
        # Defensive: if somehow a turn task is still running (e.g. end_turn sent
        # twice without a start_turn in between), cancel it first so we never have
        # two turns racing to send responses on the same socket.
        if self._current_turn_task is not None and not self._current_turn_task.done():
            self._current_turn_task.cancel()

        task = asyncio.create_task(self.handle_end_turn())
        task.add_done_callback(self._on_turn_task_done)
        self._current_turn_task = task

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
                        self._start_turn_task()
                    elif msg_type == "start_turn":
                        await self.handle_start_turn()
                    elif msg_type == "ping":
                        await self._send({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("[%s] session error", self.session_id)
            await self._send({"type": "error", "message": "internal server error"})
        finally:
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
    _startup_token_check()
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
    _foundry_credential.close()  # static credential -> sync close, not awaitable


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
    module_name = os.path.splitext(os.path.basename(__file__))[0]  # "main"
    reload_enabled = os.environ.get("RELOAD", "1") == "1"
    uvicorn.run(f"{module_name}:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 3001)), reload=reload_enabled)
