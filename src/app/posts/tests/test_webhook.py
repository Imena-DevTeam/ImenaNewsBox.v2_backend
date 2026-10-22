import base64
import hashlib
import hmac
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.sanity import (
    InvalidWebhookSignature,
    SanityNotConfigured,
    verify_webhook_signature,
)
from app.main import app
from app.posts import service

WEBHOOK = "/api/v1/posts/webhook"
SECRET = "test-webhook-secret"
BODY = b'{"_id":"010a6464-cff7-497d-a08a-fc0add31e85c","_type":"post"}'

# Made by Sanity's own library (`@sanity/webhook` 4.0.4, `encodeSignatureHeader`) for BODY,
# SECRET and this timestamp, so our check is known to match theirs.
SANITY_SIGNED_AT = datetime.fromtimestamp(1761120000000 / 1000, UTC)
SANITY_HEADER = "t=1761120000000,v1=kmF8rPeMVz36hEG0MI3fFBKSxYpMlbde6YT3jvdDpsA"


def _sign(body: bytes, *, secret: str = SECRET, at: datetime | None = None) -> str:
    timestamp = str(int((at or datetime.now(UTC)).timestamp() * 1000))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).digest()
    return f"t={timestamp},v1={base64.urlsafe_b64encode(digest).rstrip(b'=').decode()}"


@pytest.fixture(autouse=True)
def webhook_secret() -> Iterator[None]:
    settings = get_settings().model_copy(update={"sanity_webhook_secret": SecretStr(SECRET)})
    app.dependency_overrides[get_settings] = lambda: settings
    yield
    app.dependency_overrides.pop(get_settings, None)


def _fill_cache() -> None:
    service._cache[("list", 0, 20, None)] = {"total": 0, "items": []}


# --- The signature check itself ---


def test_a_signature_made_by_sanitys_library_is_accepted() -> None:
    verify_webhook_signature(BODY, SANITY_HEADER, SECRET, now=SANITY_SIGNED_AT)


@pytest.mark.parametrize(
    ("body", "header", "secret"),
    [
        (BODY + b" ", SANITY_HEADER, SECRET),  # body changed
        (BODY, SANITY_HEADER, "another-secret"),
        (BODY, SANITY_HEADER.replace("v1=k", "v1=j"), SECRET),
        (BODY, "v1=kmF8rPeMVz36hEG0MI3fFBKSxYpMlbde6YT3jvdDpsA", SECRET),  # no timestamp
        (BODY, "", SECRET),
        (BODY, None, SECRET),
    ],
    ids=["body", "secret", "signature", "no-timestamp", "empty", "missing"],
)
def test_anything_else_is_refused(body: bytes, header: str | None, secret: str) -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_webhook_signature(body, header, secret, now=SANITY_SIGNED_AT)


@pytest.mark.parametrize("offset", [timedelta(minutes=6), -timedelta(minutes=6)])
def test_old_or_future_calls_are_refused(offset: timedelta) -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_webhook_signature(BODY, SANITY_HEADER, SECRET, now=SANITY_SIGNED_AT + offset)


def test_a_call_within_five_minutes_is_accepted() -> None:
    verify_webhook_signature(
        BODY, SANITY_HEADER, SECRET, now=SANITY_SIGNED_AT + timedelta(minutes=4)
    )


def test_without_a_secret_nothing_is_accepted() -> None:
    with pytest.raises(SanityNotConfigured):
        verify_webhook_signature(BODY, SANITY_HEADER, "", now=SANITY_SIGNED_AT)


# --- The endpoint ---


async def test_a_signed_call_clears_the_cache_without_a_login(client: AsyncClient) -> None:
    _fill_cache()

    response = await client.post(
        WEBHOOK, content=BODY, headers={"sanity-webhook-signature": _sign(BODY)}
    )

    assert response.status_code == 204
    assert len(service._cache) == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"sanity-webhook-signature": _sign(BODY, secret="wrong")},
        {"sanity-webhook-signature": _sign(BODY, at=datetime.now(UTC) - timedelta(hours=1))},
    ],
    ids=["unsigned", "wrong-secret", "replayed"],
)
async def test_unsigned_or_wrong_calls_change_nothing(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    _fill_cache()

    response = await client.post(WEBHOOK, content=BODY, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert len(service._cache) == 1


async def test_a_huge_body_is_refused(client: AsyncClient) -> None:
    body = b"x" * (1024 * 1024 + 1)

    response = await client.post(
        WEBHOOK, content=body, headers={"sanity-webhook-signature": _sign(body)}
    )

    assert response.status_code == 413


async def test_without_a_secret_the_webhook_is_off(client: AsyncClient) -> None:
    settings = get_settings().model_copy(update={"sanity_webhook_secret": SecretStr("")})
    app.dependency_overrides[get_settings] = lambda: settings

    response = await client.post(
        WEBHOOK, content=BODY, headers={"sanity-webhook-signature": _sign(BODY)}
    )

    assert response.status_code == 503
