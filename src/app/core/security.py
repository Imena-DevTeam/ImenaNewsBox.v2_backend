"""Passwords, access tokens, refresh tokens and email codes.

- **Passwords** (admins only) are hashed with Argon2. Hashing is deliberately slow, so it runs
  in a worker thread instead of blocking the event loop.
- **Access tokens** are short-lived JWTs (HS256, `ACCESS_TOKEN_SECRET`). They only say who the
  user is and which session issued them; the role is always read from the database.
- **Refresh tokens** are opaque random strings, not JWTs. Only their HMAC (keyed by
  `REFRESH_TOKEN_SECRET`) is stored, so rotation and reuse detection are a plain lookup, and a
  leaked database can't be used to forge or recognise tokens.
- **Email codes** are 6 random digits, stored as an HMAC keyed by `CODE_SECRET`. A keyed hash
  matters here: a million possible codes could be brute-forced from a plain hash in seconds.

Settings are read inside each function, never at import time.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Final
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.exceptions import Unauthorized

JWT_ALGORITHM: Final = "HS256"
ACCESS_TOKEN_TYPE: Final = "access"
CODE_LENGTH: Final = 6
REFRESH_TOKEN_BYTES: Final = 32

_password_hash = PasswordHash((Argon2Hasher(),))


class InvalidToken(Unauthorized):
    code = "invalid_token"
    default_message = "The access token is invalid."


class TokenExpired(Unauthorized):
    """Separate from `InvalidToken` so the frontend knows to refresh instead of logging out."""

    code = "token_expired"
    default_message = "The access token has expired."


# --- Passwords ---


async def hash_password(password: str) -> str:
    return await run_in_threadpool(_password_hash.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password. With no hash (unknown user, or not an admin), still spend the same
    time on a dummy hash and return False, so response times don't reveal which users exist.
    """
    if password_hash is None:
        await run_in_threadpool(_password_hash.verify, password, _dummy_password_hash())
        return False
    return await run_in_threadpool(_password_hash.verify, password, password_hash)


@cache
def _dummy_password_hash() -> str:
    return _password_hash.hash(secrets.token_urlsafe(16))


# --- Access tokens ---


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    session_id: UUID
    expires_at: datetime


def create_access_token(
    user_id: UUID, session_id: UUID, *, now: datetime | None = None
) -> tuple[str, datetime]:
    """Return a signed access token and its expiry time."""
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.access_token_expires_in)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(
        payload, settings.access_token_secret.get_secret_value(), algorithm=JWT_ALGORITHM
    )
    return token, expires_at


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify an access token. Raises `TokenExpired` or `InvalidToken`."""
    secret = get_settings().access_token_secret.get_secret_value()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "sid", "type", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken() from exc

    sub, sid = payload["sub"], payload["sid"]
    if payload["type"] != ACCESS_TOKEN_TYPE or not isinstance(sub, str) or not isinstance(sid, str):
        raise InvalidToken()
    try:
        user_id, session_id = UUID(sub), UUID(sid)
    except ValueError as exc:
        raise InvalidToken() from exc
    return AccessTokenClaims(
        user_id=user_id,
        session_id=session_id,
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


# --- Refresh tokens ---


def generate_refresh_token() -> str:
    """A new random refresh token. Send it to the client; store only `hash_refresh_token`."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """The value stored in `user_sessions` and looked up on refresh."""
    return _hmac_hex(get_settings().refresh_token_secret.get_secret_value(), token)


# --- Email codes ---


def generate_code() -> str:
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(code: str) -> str:
    return _hmac_hex(get_settings().code_secret.get_secret_value(), code)


def verify_code(code: str, code_hash: str) -> bool:
    """Constant-time check of a submitted code against its stored hash."""
    return hmac.compare_digest(hash_code(code), code_hash)


def _hmac_hex(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()
