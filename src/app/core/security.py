"""Passwords, access tokens, refresh tokens and email codes.

- **Passwords** (admins only) are hashed with Argon2id. Hashing is deliberately slow and uses
  memory, so it runs in a worker thread instead of blocking the event loop, and only a couple
  of hashes run at once.
- **Access tokens** are short-lived JWTs (HS256, `ACCESS_TOKEN_SECRET`). They only say who the
  user is and which session issued them; the role is always read from the database.
- **Refresh tokens** are opaque random strings, not JWTs. Only their HMAC (keyed by
  `REFRESH_TOKEN_SECRET`) is stored, so rotation and reuse detection are a plain lookup, and a
  leaked database can't be used to forge or recognise tokens.
- **Email codes** are 6 random digits, stored as an HMAC keyed by `CODE_SECRET`. A keyed hash
  matters here: a million possible codes could be brute-forced from a plain hash in seconds.

Settings are read inside each function, never at import time.
"""

import asyncio
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.exceptions import TooManyRequests, Unauthorized

JWT_ALGORITHM: Final = "HS256"
ACCESS_TOKEN_TYPE: Final = "access"
CODE_LENGTH: Final = 6
CODE_TTL: Final = timedelta(minutes=10)
REFRESH_TOKEN_BYTES: Final = 32

# OWASP's recommended Argon2id settings (19 MiB, 2 passes, 1 lane), instead of argon2-cffi's
# default of 64 MiB. Each hash records its own settings, so older hashes still verify.
_password_hash = PasswordHash((Argon2Hasher(time_cost=2, memory_cost=19 * 1024, parallelism=1),))
# Login checks a password even for unknown users (to keep the timing equal), so anyone can make
# the server hash. At most this many hashes run at once, which caps their memory at about
# 40 MiB. The others wait their turn, for up to this many seconds each.
MAX_CONCURRENT_HASHES: Final = 2
HASH_WAIT_SECONDS: Final = 5.0

# One semaphore per event loop: an asyncio semaphore can't be shared between loops, and each
# test runs in its own.
_hash_slots: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


class PasswordCheckBusy(TooManyRequests):
    code = "password_check_busy"
    default_message = "Too many sign-ins at once. Try again in a moment."


class InvalidToken(Unauthorized):
    code = "invalid_token"
    default_message = "The access token is invalid."


class TokenExpired(Unauthorized):
    """Separate from `InvalidToken` so the frontend knows to refresh instead of logging out."""

    code = "token_expired"
    default_message = "The access token has expired."


# --- Passwords ---


async def hash_password(password: str) -> str:
    """Raises `PasswordCheckBusy` if too many hashes are already waiting."""
    return await _hashing(_password_hash.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password. With no hash (unknown user, or not an admin), still spend the same
    time on a dummy hash and return False, so response times don't reveal which users exist.
    Raises `PasswordCheckBusy` if too many hashes are already waiting.
    """
    if password_hash is None:
        await _hashing(_verify_dummy, password)
        return False
    return await _hashing(_password_hash.verify, password, password_hash)


async def _hashing[T](function: Callable[..., T], *args: str) -> T:
    """Run an Argon2 call in a worker thread, at most `MAX_CONCURRENT_HASHES` at a time."""
    loop = asyncio.get_running_loop()
    slots = _hash_slots.get(loop)
    if slots is None:
        slots = _hash_slots[loop] = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
    try:
        async with asyncio.timeout(HASH_WAIT_SECONDS):
            await slots.acquire()
    except TimeoutError:
        raise PasswordCheckBusy() from None
    try:
        return await run_in_threadpool(function, *args)
    finally:
        slots.release()


def _verify_dummy(password: str) -> bool:
    # Runs in the worker thread, so making the dummy hash the first time never blocks the
    # event loop.
    return _password_hash.verify(password, _dummy_password_hash())


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
