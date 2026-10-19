import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    CODE_LENGTH,
    InvalidToken,
    TokenExpired,
    create_access_token,
    decode_access_token,
    generate_code,
    generate_refresh_token,
    hash_code,
    hash_password,
    hash_refresh_token,
    verify_code,
    verify_password,
)

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("00000000-0000-4000-8000-000000000002")


def _access_secret() -> str:
    return get_settings().access_token_secret.get_secret_value()


def _valid_payload(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return payload


# --- Passwords ---


async def test_password_round_trip() -> None:
    password_hash = await hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2")
    assert "correct horse" not in password_hash
    assert await verify_password("correct horse battery staple", password_hash)
    assert not await verify_password("wrong password", password_hash)


async def test_same_password_hashes_differently() -> None:
    assert await hash_password("same") != await hash_password("same")


async def test_missing_hash_never_verifies() -> None:
    assert not await verify_password("anything", None)


# --- Access tokens ---


def test_access_token_round_trip() -> None:
    token, expires_at = create_access_token(USER_ID, SESSION_ID)

    claims = decode_access_token(token)

    assert claims.user_id == USER_ID
    assert claims.session_id == SESSION_ID
    assert claims.expires_at == expires_at.replace(microsecond=0)


def test_access_token_lifetime_comes_from_settings() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

    _, expires_at = create_access_token(USER_ID, SESSION_ID, now=now)

    assert expires_at == now + timedelta(seconds=get_settings().access_token_expires_in)


def test_access_token_holds_no_role_or_personal_data() -> None:
    token, _ = create_access_token(USER_ID, SESSION_ID)

    payload = jwt.decode(token, options={"verify_signature": False})

    assert set(payload) == {"sub", "sid", "type", "iat", "exp"}


def test_expired_access_token_is_rejected_as_expired() -> None:
    long_ago = datetime.now(UTC) - timedelta(days=1)
    token, _ = create_access_token(USER_ID, SESSION_ID, now=long_ago)

    with pytest.raises(TokenExpired) as exc_info:
        decode_access_token(token)

    assert exc_info.value.code == "token_expired"
    assert exc_info.value.status_code == 401


def test_tampered_access_token_is_rejected() -> None:
    token, _ = create_access_token(USER_ID, SESSION_ID)
    header, _payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        f'{{"sub":"{uuid4()}","sid":"{SESSION_ID}","type":"access","iat":1,"exp":9999999999}}'.encode()
    ).decode()

    with pytest.raises(InvalidToken):
        decode_access_token(f"{header}.{forged_payload}.{signature}")


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(_valid_payload(), "a-different-secret-of-sufficient-length!!", "HS256"),
        jwt.encode(_valid_payload(), key="", algorithm="none"),
        "not-a-jwt",
        "",
    ],
    ids=["other-secret", "alg-none", "garbage", "empty"],
)
def test_foreign_or_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidToken):
        decode_access_token(token)


@pytest.mark.parametrize(
    "payload",
    [
        _valid_payload(type="refresh"),
        _valid_payload(sub="not-a-uuid"),
        _valid_payload(sid=42),
        {key: value for key, value in _valid_payload().items() if key != "exp"},
        {key: value for key, value in _valid_payload().items() if key != "sid"},
        {key: value for key, value in _valid_payload().items() if key != "type"},
    ],
    ids=["wrong-type", "bad-sub", "bad-sid", "no-exp", "no-sid", "no-type"],
)
def test_tokens_with_wrong_or_missing_claims_are_rejected(payload: dict[str, object]) -> None:
    token = jwt.encode(payload, _access_secret(), algorithm="HS256")

    with pytest.raises(InvalidToken):
        decode_access_token(token)


# --- Refresh tokens ---


def test_refresh_tokens_are_long_and_unique() -> None:
    tokens = {generate_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) >= 43 for token in tokens)


def test_refresh_token_hash_is_stable_and_keyed() -> None:
    token = generate_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert hash_refresh_token(token) != hash_refresh_token(generate_refresh_token())
    assert token not in hash_refresh_token(token)
    # Keyed: not the same as a plain SHA-256 of the token.
    assert hash_refresh_token(token) != hashlib.sha256(token.encode()).hexdigest()


# --- Email codes ---


def test_codes_are_six_digits() -> None:
    codes = [generate_code() for _ in range(200)]

    assert all(len(code) == CODE_LENGTH and code.isdigit() for code in codes)
    assert len(set(codes)) > 150


def test_code_round_trip() -> None:
    code = generate_code()
    code_hash = hash_code(code)

    assert code not in code_hash
    assert verify_code(code, code_hash)
    assert not verify_code(f"{(int(code) + 1) % 10**CODE_LENGTH:06d}", code_hash)


def test_code_hash_uses_its_own_secret() -> None:
    # The same input hashes differently as a code and as a refresh token.
    assert hash_code("123456") != hash_refresh_token("123456")
