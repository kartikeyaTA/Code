"""
auth_layer.py
─────────────
User identity extraction supporting two auth paths:

  1. Azure Easy Auth (Microsoft Entra ID) — headers injected by Container Apps:
       X-MS-CLIENT-PRINCIPAL-ID    Entra object ID
       X-MS-CLIENT-PRINCIPAL-NAME  Display name / UPN (often the user's email)

  2. Local account JWT cookie — issued by POST /auth/login for users who
     registered with email + password. Cookie name: voicelive_token.
     JWT payload carries: sub (user_id), name, email.

Falls back to dev-user sentinel when neither is present (local dev, no auth).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from jose import jwt as _jwt, JWTError
    _JOSE_AVAILABLE = True
except ImportError:
    _JOSE_AVAILABLE = False

try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_DAYS = 30


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET_KEY", "dev-insecure-secret-change-in-prod")


# ── Token helpers ─────────────────────────────────────────────────────────────

def create_jwt(user_id: str, user_name: str, email: str = "") -> str:
    if not _JOSE_AVAILABLE:
        raise RuntimeError("python-jose is not installed")
    exp = datetime.now(timezone.utc) + timedelta(days=_JWT_EXPIRE_DAYS)
    return _jwt.encode(
        {"sub": user_id, "name": user_name, "email": email, "exp": exp},
        _jwt_secret(),
        algorithm=_JWT_ALGORITHM,
    )


def _decode_jwt(token: str) -> Optional[dict]:
    if not _JOSE_AVAILABLE or not token:
        return None
    try:
        return _jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGORITHM])
    except Exception:
        return None


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    if not _BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt is not installed")
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not _BCRYPT_AVAILABLE:
        return False
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── UserContext ───────────────────────────────────────────────────────────────

@dataclass
class UserContext:
    user_id: str
    user_name: str
    is_authenticated: bool
    auth_method: str = field(default="none")  # "microsoft" | "local" | "dev"
    email: str = field(default="")


_DEV_USER = UserContext(
    user_id="dev-user",
    user_name="Dev User",
    is_authenticated=False,
    auth_method="dev",
    email="",
)


def extract_user(request: Any) -> UserContext:
    """
    Return a UserContext from:
      1. voicelive_token JWT cookie (local account login) — checked first so a
         local login always overrides an ambient Microsoft Easy Auth session.
      2. Easy Auth headers (Microsoft login)
      3. dev-user sentinel fallback

    Accepts any object with .headers and .cookies — works with FastAPI
    Request and WebSocket (both share the same HTTPConnection interface).
    """
    # ── JWT cookie (local account) — takes priority ───────────────────────────
    # Checked before Easy Auth so that a user who explicitly signed in with a
    # local account is never silently overridden by a residual Microsoft session.
    cookies = getattr(request, "cookies", {})
    token = cookies.get("voicelive_token", "")
    if token:
        payload = _decode_jwt(token)
        if payload and payload.get("sub"):
            return UserContext(
                user_id=payload["sub"],
                user_name=payload.get("name", "User"),
                is_authenticated=True,
                auth_method="local",
                email=payload.get("email", ""),
            )

    # ── Easy Auth (Microsoft) ─────────────────────────────────────────────────
    user_id = request.headers.get("x-ms-client-principal-id", "").strip()
    if user_id:
        # X-MS-CLIENT-PRINCIPAL-NAME is the UPN, which is typically the email
        upn = request.headers.get("x-ms-client-principal-name", "").strip()
        return UserContext(
            user_id=user_id,
            user_name=upn or user_id,
            is_authenticated=True,
            auth_method="microsoft",
            email=upn,
        )

    return _DEV_USER
