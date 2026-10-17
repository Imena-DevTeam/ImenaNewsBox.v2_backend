"""Token, code and password helpers (PyJWT, pwdlib/argon2, hashing of codes and refresh tokens)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_hasher = PasswordHash.recommended()
config = get_settings()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


# private functions


def _encode(sub: uuid.UUID, kind: str, ttl: int, secret: str, **extra) -> str:

    now = datetime.now(UTC)
    payload = {
        "sub": str(sub),
        "type": kind,
        "iat": now,
        "exp": now + timedelta(seconds=ttl, **extra),
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def _decode(token: str, kind: str, secret: str) -> dict:
    payload = jwt.decode(
        token, secret, algorithms=["HS256"]
    )  # Twakoresheje the [] bcz it is the signing algorithm

    if payload.get("type") != kind:
        raise jwt.InvalidTokenError("Wrong token type.")

    return payload


def create_access_token(user_id: uuid.UUID) -> str:
    return _encode(
        user_id, "access_token", config.access_token_expires_in, config.access_token_secret
    )


def create_refresh_token(user_id: uuid.UUID, jti: uuid.UUID) -> str:
    return _encode(
        user_id,
        "refresh_token",
        config.refresh_token_expires_in,
        config.refresh_token_secret,
        jti=str(jti),
    )


def decode_access_token(token: str) -> dict:
    return _decode(token, "access_token", config.access_token_secret)


def decode_refresh_token(token: str) -> dict:
    return _decode(token, "refresh_token", config.refresh_token_secret)
