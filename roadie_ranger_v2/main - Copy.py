from __future__ import annotations
import os
import asyncio
import base64
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from auth_layer import extract_user, UserContext, create_jwt, hash_password, verify_password
from storage_client import StorageClient

from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential, ChainedTokenCredential

from azure.ai.voicelive.aio import connect, AgentSessionConfig
from azure.ai.voicelive.models import (
    InputAudioFormat,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    MessageItem,
    InputTextContentPart,
    InterimResponseTrigger,
    StaticInterimResponseConfig,
    AzureSemanticVad,
    AudioNoiseReduction,
    AudioEchoCancellation,
)
from dotenv import load_dotenv

if TYPE_CHECKING:
    from azure.ai.voicelive.aio import VoiceLiveConnection

# ── Environment ───────────────────────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"), override=True)

_sessions_dir = os.path.join(_script_dir, "sessions")
os.makedirs(_sessions_dir, exist_ok=True)
os.makedirs(os.path.join(_script_dir, "logs"), exist_ok=True)
_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

logging.basicConfig(
    format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(
            os.path.join(_script_dir, "logs", f"{_ts}_voicelive.log"), mode="w"
        ),
        logging.StreamHandler(),  # also write to stdout so Azure log stream shows errors
    ],
)
logger = logging.getLogger(__name__)

# ── Quota defaults ─────────────────────────────────────────────────────────────
_USER_DAILY_SESSION_LIMIT = int(os.environ.get("USER_DAILY_SESSION_LIMIT", "10"))
_USER_DAILY_MINUTES_LIMIT = int(os.environ.get("USER_DAILY_MINUTES_LIMIT", "60"))
_JWT_EXPIRE_DAYS = 30

# ── WebSocket nonce store ─────────────────────────────────────────────────────
# Short-lived tokens issued by GET /api/ws-nonce over HTTP (where cookies work)
# and consumed once by the WebSocket endpoint as a query param. This is needed
# because Azure Container Apps strips httponly cookies from WS upgrade requests,
# so local-account users cannot be identified at the WebSocket layer via cookie.
_ws_nonces: dict[str, tuple[UserContext, datetime]] = {}
_WS_NONCE_TTL = timedelta(seconds=30)


def _generate_ws_nonce(user: UserContext) -> str:
    nonce = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + _WS_NONCE_TTL
    _ws_nonces[nonce] = (user, expires)
    return nonce


def _consume_ws_nonce(nonce: str) -> Optional[UserContext]:
    """Return and delete a valid nonce, or None if missing/expired."""
    entry = _ws_nonces.pop(nonce, None)
    if entry is None:
        return None
    user, expires = entry
    if datetime.now(timezone.utc) > expires:
        return None
    return user


# ── Session persistence helpers ───────────────────────────────────────────────

def _session_path(session_id: str) -> str:
    return os.path.join(_sessions_dir, f"{session_id}.json")


def _load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_session(data: dict) -> None:
    path = _session_path(data["session_id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_turn(session_data: dict, role: str, text: str) -> None:
    """Append a conversation turn and persist immediately."""
    turn = {
        "role": role,
        "text": text,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    session_data.setdefault("turns", []).append(turn)
    _save_session(session_data)


def _list_local_sessions(user_id: str) -> list:
    """Read local session files filtered by user_id. Used as fallback when blob is unavailable."""
    results = []
    try:
        for fname in os.listdir(_sessions_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(_sessions_dir, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("user_id") != user_id:
                    continue
                turns = data.get("turns", [])
                preview = next(
                    (t["text"] for t in turns if t.get("role") == "user"),
                    "No messages yet",
                )
                results.append({
                    "session_id": data["session_id"],
                    "started_at": data.get("started_at", ""),
                    "preview": preview[:80] + ("…" if len(preview) > 80 else ""),
                    "turn_count": len(turns),
                })
            except Exception:
                continue
    except Exception:
        pass
    results.sort(key=lambda x: x.get("started_at", ""), reverse=True)
    return results


# ── StorageClient (initialised once at startup) ───────────────────────────────
_storage_client: Optional[StorageClient] = None


def _get_storage() -> Optional[StorageClient]:
    return _storage_client


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="VoiceLive Assistant")
app.mount("/static", StaticFiles(directory=os.path.join(_script_dir, "static")), name="static")


@app.on_event("startup")
async def _startup() -> None:
    global _storage_client
    credential = ChainedTokenCredential(AzureCliCredential(), ManagedIdentityCredential())
    _storage_client = StorageClient.create(credential)
    await _storage_client.ensure_infrastructure()


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Upload the current log file to blob storage on graceful shutdown."""
    storage = _get_storage()
    if not storage or not storage.enabled:
        return
    log_file = os.path.join(_script_dir, "logs", f"{_ts}_voicelive.log")
    await storage.upload_log_file(log_file, _ts)


@app.get("/")
async def root():
    return FileResponse(os.path.join(_script_dir, "static", "index.html"))


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(_script_dir, "static", "login.html"))


@app.get("/signup")
async def signup_page():
    return FileResponse(os.path.join(_script_dir, "static", "signup.html"))


# ── Local account auth endpoints ──────────────────────────────────────────────

@app.post("/auth/register")
async def auth_register(request: Request):
    """Create a local account with email + password."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    required_fields = ["first_name", "last_name", "email", "organisation", "password"]
    for field in required_fields:
        if not str(body.get(field, "")).strip():
            raise HTTPException(400, f"{field} is required")

    email = body["email"].strip().lower()
    password = body["password"]

    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Invalid email address")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    storage = _get_storage()
    user_id = str(uuid.uuid4())
    full_name = f"{body['first_name'].strip()} {body['last_name'].strip()}"
    user_data = {
        "user_id": user_id,
        "email": email,
        "first_name": body["first_name"].strip(),
        "last_name": body["last_name"].strip(),
        "organisation": body["organisation"].strip(),
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if storage and storage.enabled:
        created = await storage.create_local_user(user_data)
        if not created:
            raise HTTPException(409, "An account with this email already exists")
    else:
        logger.warning("Storage not enabled — local account created in dev mode only")

    token = create_jwt(user_id, full_name, email=email)
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp = JSONResponse({"user_id": user_id, "user_name": full_name, "email": email})
    resp.set_cookie(
        "voicelive_token", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=_JWT_EXPIRE_DAYS * 86400,
    )
    logger.info("Local account created: %s (%s)", full_name, email)
    return resp


@app.post("/auth/login")
async def auth_login(request: Request):
    """Authenticate a local account and issue a JWT cookie."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not email or not password:
        raise HTTPException(400, "Email and password are required")

    storage = _get_storage()
    if not storage or not storage.enabled:
        # Dev mode: accept any credentials and issue a dev token
        token = create_jwt(f"dev-local-{email}", email, email=email)
        resp = JSONResponse({"user_id": f"dev-local-{email}", "user_name": email})
        resp.set_cookie("voicelive_token", token, httponly=True, samesite="lax",
                        max_age=_JWT_EXPIRE_DAYS * 86400)
        return resp

    user = await storage.get_local_user_by_email(email)
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise HTTPException(401, "Invalid email or password")

    full_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    token = create_jwt(user["user_id"], full_name, email=email)
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp = JSONResponse({"user_id": user["user_id"], "user_name": full_name, "email": email})
    resp.set_cookie(
        "voicelive_token", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=_JWT_EXPIRE_DAYS * 86400,
    )
    logger.info("Local account login: %s", email)
    return resp


@app.post("/auth/logout")
async def auth_logout(request: Request):
    """Clear the local account JWT cookie."""
    is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(
        "voicelive_token",
        httponly=True,
        secure=is_https,
        samesite="lax",
    )
    return resp


# ── REST: session history ─────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions(request: Request):
    """
    Return sessions for the current user sorted newest-first.
    Authenticated users with storage enabled: reads from Blob (persists across restarts).
    Dev / unauthenticated: reads from local session files.
    """
    user = extract_user(request)
    storage = _get_storage()

    if user.is_authenticated and storage and storage.enabled:
        results = await storage.list_user_sessions(user.user_id)
        if results:
            return JSONResponse(results)
        # Blob returned nothing — fall back to local files so sessions are visible
        # even when Storage IAM roles are missing or uploads are failing.
        local = _list_local_sessions(user.user_id)
        if local:
            logger.warning(
                "No blob sessions for user=%s — serving %d local file(s). "
                "Verify Storage Blob Data Contributor role is assigned to the managed identity.",
                user.user_id, len(local),
            )
        return JSONResponse(local)

    # Dev / unauthenticated: read all local session files (unfiltered)
    results = []
    for fname in os.listdir(_sessions_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(_sessions_dir, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            turns = data.get("turns", [])
            preview = next((t["text"] for t in turns if t["role"] == "user"), "No messages yet")
            results.append({
                "session_id": data["session_id"],
                "started_at": data.get("started_at", ""),
                "preview": preview[:80] + ("…" if len(preview) > 80 else ""),
                "turn_count": len(turns),
            })
        except Exception:
            continue
    results.sort(key=lambda x: x["started_at"], reverse=True)
    return JSONResponse(results)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Return the full turn list for one session."""
    if not all(c.isalnum() or c == "-" for c in session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")

    user = extract_user(request)
    storage = _get_storage()

    if user.is_authenticated and storage and storage.enabled:
        data = await storage.get_session_blob(user.user_id, session_id)
        if data:
            return JSONResponse(data)
        # Session not in blob — may be in-progress local file; verify ownership
        data = _load_session(session_id)
        if data and data.get("user_id") == user.user_id:
            return JSONResponse(data)
        raise HTTPException(status_code=404, detail="Session not found")

    data = _load_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(data)


@app.get("/api/me")
async def api_me(request: Request):
    """Return the identity of the calling user."""
    user = extract_user(request)
    return JSONResponse({
        "user_id": user.user_id,
        "user_name": user.user_name,
        "is_authenticated": user.is_authenticated,
        "auth_method": user.auth_method,
    })


@app.get("/api/ws-nonce")
async def api_ws_nonce(request: Request):
    """
    Issue a short-lived (30 s) single-use token for WebSocket authentication.

    The browser calls this over HTTP (where the httponly JWT cookie is readable),
    then passes the returned nonce as ?nonce=<token> on the WS upgrade URL.
    The WebSocket endpoint consumes the nonce and recovers the full UserContext.
    This is necessary because Azure Container Apps strips httponly cookies from
    WebSocket upgrade requests, making local-account users invisible at WS time.
    """
    user = extract_user(request)
    if not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    nonce = _generate_ws_nonce(user)
    return JSONResponse({"nonce": nonce})


@app.get("/api/debug/storage")
async def debug_storage(request: Request):
    """Diagnostic: shows storage connectivity status and what the server sees for this user."""
    user = extract_user(request)
    storage = _get_storage()
    info: dict = {
        "user_id": user.user_id,
        "auth_method": user.auth_method,
        "is_authenticated": user.is_authenticated,
        "storage_enabled": bool(storage and storage.enabled),
        "azure_storage_url_set": bool(os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()),
    }
    if storage and storage.enabled:
        try:
            sessions = await storage.list_user_sessions(user.user_id)
            info["blob_list_ok"] = True
            info["blob_session_count"] = len(sessions)
        except Exception as exc:
            info["blob_list_ok"] = False
            info["blob_list_error"] = str(exc)
        info["local_session_count"] = len(_list_local_sessions(user.user_id))
    return JSONResponse(info)


@app.get("/api/usage")
async def api_usage(request: Request):
    """Return today's quota usage for the calling user."""
    user = extract_user(request)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage = _get_storage()
    if storage and storage.enabled and user.is_authenticated:
        usage = await storage.get_usage(user.user_id, date_str)
    else:
        usage = {"sessions_count": 0, "minutes_used": 0}
    return JSONResponse({
        "sessions": {"used": usage["sessions_count"], "limit": _USER_DAILY_SESSION_LIMIT},
        "minutes":  {"used": usage["minutes_used"],   "limit": _USER_DAILY_MINUTES_LIMIT},
        "date": date_str,
    })


# ── VoiceLive session ────────────────────────────────────────────────────────

class VoiceLiveSession:
    """
    Bridges one browser WebSocket ↔ one Azure VoiceLive connection.

    Message protocol (JSON over WS):
      Browser → Server:
        { type: "audio_chunk", data: "<base64 PCM16 24kHz mono>" }
        { type: "stop" }

      Server → Browser:
        { type: "session_id",       id: "..."  }
        { type: "audio_chunk",      data: "..." }
        { type: "user_text_delta",  delta: "..." }
        { type: "user_text",        text: "..."  }
        { type: "agent_text_delta", delta: "..." }
        { type: "agent_text",       text: "..."  }
        { type: "status",           text: "..." }
        { type: "error",            text: "..." }
    """

    def __init__(self, ws: WebSocket, user: Optional[UserContext] = None) -> None:
        self.ws = ws
        self._user = user
        self.connection: Optional[VoiceLiveConnection] = None
        self._active_response = False
        self._response_api_done = False
        self._running = True
        self._session_ready_event = asyncio.Event()
        # True while an interim (filler) response has finished but the real tool-call
        # answer has not yet arrived. Barge-in cancellation is suppressed during this
        # window to avoid killing the real answer due to background VAD noise.
        self._awaiting_tool_response = False
        # Tracks whether the current response cycle produced a real transcript.
        # If False at RESPONSE_DONE, the response was an interim filler phrase.
        self._current_response_had_transcript = False
        # Cancellation handle for the barge-in suppression timeout task.
        self._tool_wait_timeout_task: Optional[asyncio.Task] = None
        # True from the moment the user barges in until the next RESPONSE_CREATED.
        # Audio delta chunks arriving in this window are dropped so stale audio
        # from the cancelled response never reaches the client.
        self._is_barging_in = False
        # True while the greeting response is in-flight so the user cannot
        # barge-in and cancel it before it is heard.
        self._greeting_in_progress = False
        # True for the duration of the response that was created as the greeting
        # (set on RESPONSE_CREATED when _greeting_in_progress is True, cleared on
        # RESPONSE_DONE). Prevents RESPONSE_DONE from misclassifying the greeting
        # as an interim filler when _current_response_had_transcript is False.
        self._greeting_response_active = False

        # Server-side transcript accumulators — delta text is collected here so that
        # if event.get("transcript") returns empty on the COMPLETED/DONE event (a known
        # VoiceLive SDK quirk), the full text is still available from accumulated deltas.
        self._user_transcript_buf: str = ""
        self._agent_transcript_buf: str = ""

        # Unique ID for this conversation session
        self._session_id = str(uuid.uuid4())
        self._start_time = datetime.now(timezone.utc)
        self._session_data: dict = {
            "session_id": self._session_id,
            "started_at": self._start_time.isoformat(),
            "source": "browser",
            "user_id": user.user_id if user else "dev-user",
            "user_name": user.user_name if user else "Dev User",
            "turns": [],
        }
        # Persist the empty shell immediately so it shows in the sidebar right away
        _save_session(self._session_data)

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _send(self, msg: dict) -> None:
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass

    def _persist_turn(self, role: str, text: str) -> None:
        if text and text.strip():
            _append_turn(self._session_data, role, text)

    def _cancel_tool_wait_timeout(self) -> None:
        if self._tool_wait_timeout_task and not self._tool_wait_timeout_task.done():
            self._tool_wait_timeout_task.cancel()
        self._tool_wait_timeout_task = None

    def _arm_tool_wait_timeout(self, timeout_s: float = 8.0) -> None:
        """If the real tool answer hasn't arrived within timeout_s, un-suppress barge-in."""
        self._cancel_tool_wait_timeout()

        async def _timeout() -> None:
            await asyncio.sleep(timeout_s)
            if self._awaiting_tool_response:
                logger.warning(
                    "[%s] tool-response timeout — re-enabling barge-in", self._session_id
                )
                self._awaiting_tool_response = False

        self._tool_wait_timeout_task = asyncio.create_task(_timeout())

    # ── main entry ───────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            # Upload empty shell to blob before telling the browser the session ID,
            # so the sidebar refresh triggered by 'session_id' finds it immediately.
            storage = _get_storage()
            if storage and storage.enabled and self._user and self._user.is_authenticated:
                try:
                    await storage.upload_session(self._session_data)
                except Exception:
                    logger.warning("[%s] Could not upload session shell to blob", self._session_id)

            await self._send({"type": "session_id", "id": self._session_id})

            endpoint     = os.environ.get("AZURE_VOICELIVE_ENDPOINT", "")
            agent_name   = os.environ.get("AZURE_VOICELIVE_AGENT_ID", "")
            project_name = os.environ.get("AZURE_VOICELIVE_PROJECT_NAME", "")
            agent_version    = os.environ.get("AZURE_VOICELIVE_AGENT_VERSION")
            conversation_id  = os.environ.get("AZURE_VOICELIVE_CONVERSATION_ID")
            foundry_override = os.environ.get("AZURE_VOICELIVE_FOUNDRY_RESOURCE_OVERRIDE")
            auth_identity    = os.environ.get("AZURE_VOICELIVE_AUTH_IDENTITY_CLIENT_ID")

            if not endpoint or not agent_name or not project_name:
                await self._send({"type": "error", "text": "Missing required environment variables."})
                return  # finally block still runs → _finalize() is called

            agent_config: AgentSessionConfig = {
                "agent_name": agent_name,
                "agent_version": agent_version or None,
                "project_name": project_name,
                "conversation_id": conversation_id or None,
                "foundry_resource_override": foundry_override or None,
                "authentication_identity_client_id": (
                    auth_identity if auth_identity and foundry_override else None
                ),
            }

            credential = ChainedTokenCredential(AzureCliCredential(), ManagedIdentityCredential())

            async with connect(
                endpoint=endpoint,
                credential=credential,
                api_version="2026-01-01-preview",
                agent_config=agent_config,
            ) as conn:
                self.connection = conn
                await asyncio.gather(
                    self._listen_voicelive(),
                    self._setup_and_forward(),
                )
        except Exception as exc:
            logger.exception("VoiceLive session error")
            await self._send({"type": "error", "text": str(exc)})
        finally:
            await self._finalize()

    # ── session close ─────────────────────────────────────────────────────────

    async def _finalize(self) -> None:
        """Upload session to Blob Storage and record duration in the quota table."""
        storage = _get_storage()
        if not storage or not storage.enabled:
            logger.info("[%s] _finalize: storage not enabled — skipping blob upload", self._session_id)
            return
        user = self._user
        if not user or not user.is_authenticated:
            logger.warning(
                "[%s] _finalize: user not authenticated (auth_method=%s, user_id=%s) — "
                "skipping blob upload. Easy Auth headers may not be present on WS upgrade.",
                self._session_id,
                user.auth_method if user else "none",
                user.user_id if user else "none",
            )
            return
        # Flush any transcript text that was buffered but never committed because
        # the session ended before the COMPLETED / DONE event arrived.
        if self._user_transcript_buf.strip():
            logger.info("[%s] _finalize: flushing buffered user transcript", self._session_id)
            _append_turn(self._session_data, "user", self._user_transcript_buf.strip())
            self._user_transcript_buf = ""
        if self._agent_transcript_buf.strip():
            logger.info("[%s] _finalize: flushing buffered agent transcript", self._session_id)
            _append_turn(self._session_data, "agent", self._agent_transcript_buf.strip())
            self._agent_transcript_buf = ""

        duration_s = (datetime.now(timezone.utc) - self._start_time).total_seconds()
        self._session_data["duration_seconds"] = round(duration_s)
        _save_session(self._session_data)
        date_str = self._start_time.strftime("%Y-%m-%d")
        turns = len(self._session_data.get("turns", []))
        logger.info(
            "[%s] _finalize: uploading blob for user=%s turns=%d duration=%.0fs",
            self._session_id, user.user_id, turns, duration_s,
        )
        await storage.upload_session(self._session_data)
        await storage.record_duration(user.user_id, duration_s, date_str)

    # ── session config ────────────────────────────────────────────────────────

    async def _setup_and_forward(self) -> None:
        # Yield first so _listen_voicelive() starts consuming events before we
        # send session.update — otherwise SESSION_UPDATED can arrive before the
        # listener is ready and _session_ready_event never gets set.
        await asyncio.sleep(0)
        await self._setup_session()
        await self._session_ready_event.wait()
        await self._send({"type": "status", "text": "connected"})
        self._greeting_in_progress = True
        try:
            conn = self.connection
            await conn.conversation.item.create(
                item=MessageItem(
                    role="system",
                    content=[InputTextContentPart(text="Say something to welcome the user in English.")],
                )
            )
            await conn.response.create()
            logger.info("[%s] Proactive greeting sent", self._session_id)
        except Exception:
            logger.exception("[%s] Failed to send greeting", self._session_id)
            self._greeting_in_progress = False

        async def _greeting_timeout() -> None:
            await asyncio.sleep(8.0)
            if self._greeting_in_progress:
                logger.warning("[%s] Greeting timeout — forcing _greeting_in_progress=False", self._session_id)
                self._greeting_in_progress = False

        asyncio.create_task(_greeting_timeout())
        try:
            await self._forward_browser_audio()
        finally:
            # Browser disconnected (or error). Close the VoiceLive connection so
            # _listen_voicelive()'s `async for` loop unblocks and returns, allowing
            # asyncio.gather to complete and _finalize() to upload the session.
            if self.connection:
                try:
                    await asyncio.wait_for(self.connection.close(), timeout=3.0)
                except Exception:
                    pass

    async def _setup_session(self) -> None:
        # interim_response is intentionally OMITTED here so the greeting response
        # cannot trigger an interim filler. It is enabled by _enable_interim_responses
        # only after the greeting RESPONSE_DONE event fires.
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
            logger.info("[%s] Interim responses enabled", self._session_id)
        except Exception:
            logger.warning("[%s] Failed to enable interim responses", self._session_id)

    # ── browser → voicelive ───────────────────────────────────────────────────

    async def _forward_browser_audio(self) -> None:
        try:
            while self._running:
                msg = await self.ws.receive_json()
                if msg.get("type") == "audio_chunk":
                    # Suppress forwarding audio while the greeting is playing so
                    # VAD does not interrupt it before the caller hears it.
                    if not self._greeting_in_progress:
                        await self.connection.input_audio_buffer.append(audio=msg["data"])
                elif msg.get("type") == "stop":
                    self._running = False
                    break
        except WebSocketDisconnect:
            self._running = False
        except Exception as exc:
            logger.warning("Error forwarding audio: %s", exc)
            self._running = False

    # ── voicelive → browser ───────────────────────────────────────────────────

    async def _listen_voicelive(self) -> None:
        try:
            async for event in self.connection:
                if not self._running:
                    break
                await self._handle_event(event)
        except Exception as exc:
            logger.exception("Error in VoiceLive event loop")
            await self._send({"type": "error", "text": str(exc)})

    async def _handle_event(self, event: Any) -> None:
        conn = self.connection

        if event.type == ServerEventType.SESSION_UPDATED:
            logger.info("Session ready: %s", event.session.id)
            self._session_ready_event.set()

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_DELTA:
            delta = event.get("delta", "")
            if delta:
                self._user_transcript_buf += delta
                await self._send({"type": "user_text_delta", "delta": delta})

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            # Prefer the event's transcript field; fall back to the accumulated deltas
            # because event.get("transcript") can return empty in the VoiceLive SDK.
            text = event.get("transcript", "") or self._user_transcript_buf
            self._user_transcript_buf = ""
            await self._send({"type": "user_text", "text": text})
            await asyncio.to_thread(self._persist_turn, "user", text)

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            delta = event.get("delta", "")
            if delta:
                self._agent_transcript_buf += delta
                self._awaiting_tool_response = False
                self._current_response_had_transcript = True
                await self._send({"type": "agent_text_delta", "delta": delta})

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            # Same fallback pattern as user transcript.
            text = event.get("transcript", "") or self._agent_transcript_buf
            self._agent_transcript_buf = ""
            self._current_response_had_transcript = True
            logger.info("[%s] agent transcript done: %.120s", self._session_id, text)
            await self._send({"type": "agent_text", "text": text})
            await asyncio.to_thread(self._persist_turn, "agent", text)

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            if self._awaiting_tool_response or self._greeting_in_progress:
                logger.info("[%s] speech_started suppressed — waiting for tool response or greeting", self._session_id)
                return
            logger.info("[%s] speech_started — active_response=%s", self._session_id, self._active_response)
            # Always flush client audio immediately — user is speaking so stop all queued audio.
            await self._send({"type": "status", "text": "barge_in"})
            self._is_barging_in = True
            if self._active_response and not self._response_api_done:
                try:
                    await conn.response.cancel()
                    logger.info("[%s] barge-in: response cancelled", self._session_id)
                except Exception as e:
                    if "no active response" not in str(e).lower():
                        logger.warning("[%s] barge-in cancel failed: %s", self._session_id, e)
            await self._send({"type": "status", "text": "listening"})

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            logger.info("[%s] speech_stopped", self._session_id)
            await self._send({"type": "status", "text": "processing"})

        elif event.type == ServerEventType.RESPONSE_CREATED:
            was_waiting = self._awaiting_tool_response
            self._cancel_tool_wait_timeout()
            self._active_response = True
            self._response_api_done = False
            self._current_response_had_transcript = False
            self._awaiting_tool_response = False
            self._is_barging_in = False
            self._greeting_response_active = self._greeting_in_progress
            logger.info("[%s] response_created (was_awaiting_tool=%s greeting=%s)", self._session_id, was_waiting, self._greeting_response_active)

        elif event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            if self._is_barging_in:
                return  # drop stale audio from the cancelled response
            raw = event.delta
            if raw:
                await self._send({
                    "type": "audio_chunk",
                    "data": base64.b64encode(raw).decode() if isinstance(raw, bytes) else raw,
                })

        elif event.type == ServerEventType.RESPONSE_AUDIO_DONE:
            logger.info(
                "[%s] response_audio_done (awaiting_tool=%s greeting_in_progress=%s)",
                self._session_id, self._awaiting_tool_response, self._greeting_in_progress,
            )
            if self._greeting_in_progress:
                self._greeting_in_progress = False
                logger.info("[%s] Greeting complete — barge-in re-enabled", self._session_id)
            if not self._awaiting_tool_response:
                await self._send({"type": "status", "text": "ready"})

        elif event.type == ServerEventType.RESPONSE_DONE:
            self._active_response = False
            self._response_api_done = True
            was_greeting = self._greeting_response_active
            self._greeting_response_active = False
            # Safety net: if RESPONSE_AUDIO_DONE was missed, clear greeting gate here.
            if self._greeting_in_progress:
                self._greeting_in_progress = False
                logger.info("[%s] Greeting done (RESPONSE_DONE fallback) — barge-in re-enabled", self._session_id)
            if was_greeting:
                # The greeting response completing is not an interim filler — always
                # treat it as a real answer so barge-in is not suppressed afterward.
                self._cancel_tool_wait_timeout()
                self._awaiting_tool_response = False
                logger.info("[%s] response_done — greeting complete", self._session_id)
                # Greeting fully done — now safe to enable interim filler responses
                # for subsequent user turns.
                asyncio.create_task(self._enable_interim_responses())
            elif not self._current_response_had_transcript:
                # No transcript = interim filler phrase. Suppress barge-in until the next
                # RESPONSE_CREATED fires, but arm a timeout so we don't suppress forever
                # if the real tool answer never arrives (e.g. agent error).
                self._awaiting_tool_response = True
                self._arm_tool_wait_timeout()
                logger.info("[%s] response_done — interim filler, suppressing barge-in", self._session_id)
            else:
                self._cancel_tool_wait_timeout()
                self._awaiting_tool_response = False
                logger.info("[%s] response_done — real answer complete", self._session_id)

        elif event.type == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed: no active response" not in msg:
                logger.error("[%s] voicelive error: %s", self._session_id, msg)
                await self._send({"type": "error", "text": msg})

# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    # Try nonce first — local-account users pass this because httponly cookies
    # are stripped from WS upgrade requests by the Azure Container Apps proxy.
    nonce = websocket.query_params.get("nonce", "")
    user = _consume_ws_nonce(nonce) if nonce else None
    if user is None:
        user = extract_user(websocket)
    logger.info(
        "WS /ws/voice: user_id=%s auth_method=%s is_authenticated=%s nonce_used=%s",
        user.user_id, user.auth_method, user.is_authenticated, bool(nonce and user),
    )
    storage = _get_storage()

    # Quota check — only enforced for authenticated users with storage enabled.
    if storage and storage.enabled and user.is_authenticated:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        allowed = await storage.check_and_reserve(
            entity_id=user.user_id,
            session_limit=_USER_DAILY_SESSION_LIMIT,
            minutes_limit=_USER_DAILY_MINUTES_LIMIT,
            date_str=date_str,
        )
        if not allowed:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "text": "Daily usage limit reached. Please try again tomorrow.",
            })
            await websocket.close(code=1008)
            logger.warning(
                "Session denied for user=%s (quota exceeded)", user.user_id
            )
            return

    await websocket.accept()

    # Record this user in the registry (creates on first login, updates last_seen).
    if storage and storage.enabled and user.is_authenticated:
        asyncio.create_task(storage.record_user_login(
            user_id=user.user_id,
            display_name=user.user_name,
            email=user.email,
            auth_method=user.auth_method,
        ))

    session = VoiceLiveSession(websocket, user=user)
    try:
        await session.run()
    finally:
        logger.info("WebSocket session %s closed (user=%s)", session._session_id, user.user_id)