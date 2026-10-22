"""Thin async client for the Sanity HTTP API: GROQ queries, mutations and image assets.

There's no official Python SDK (the PyPI package named `sanity` is unrelated), so this calls
the HTTP API with `httpx`. Reads use `SANITY_API_READ_TOKEN`, writes `SANITY_API_WRITE_TOKEN`.
Reads go to the live API (`api.sanity.io`), never the API CDN: posts are cached in-process
already, and two caches would stack staleness. Only published documents are returned.

`verify_webhook_signature` checks the signature Sanity puts on webhook calls.
"""

import base64
import hashlib
import hmac
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, Final, Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, Unauthorized

logger = logging.getLogger(__name__)

API_VERSION: Final = "v2025-02-19"
TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)
UPLOAD_TIMEOUT: Final = httpx.Timeout(30.0, connect=5.0)
WEBHOOK_SIGNATURE_HEADER: Final = "sanity-webhook-signature"
# Older calls are refused, so a recorded call can't be replayed later.
WEBHOOK_MAX_AGE: Final = timedelta(minutes=5)
# As Sanity's own `@sanity/webhook` library reads it: `t=<unix ms>,v1=<signature>`.
_WEBHOOK_SIGNATURE: Final = re.compile(r"^t=(\d{1,16})[, ]+v1=([^, ]+)$")


class SanityUnavailable(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    code = "cms_unavailable"
    default_message = "The blog is unavailable right now. Try again in a moment."


class SanityNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "cms_not_configured"
    default_message = "The blog isn't set up on this server yet."


class Sanity(Protocol):
    async def query(self, groq: str, params: Mapping[str, Any] | None = None) -> Any:
        """Run a GROQ query against published documents and return its `result`."""
        ...

    async def mutate(self, mutations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Apply mutations in one transaction, visible to queries once this returns."""
        ...

    async def upload_image(self, data: bytes, *, content_type: str) -> dict[str, Any]:
        """Upload an image asset and return its asset document (`_id`, `url`, ...)."""
        ...


class SanityClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        # Tests pass an `httpx.MockTransport`; production uses the default network transport.
        self._transport = transport

    async def query(self, groq: str, params: Mapping[str, Any] | None = None) -> Any:
        # POST keeps long queries and their parameters out of URLs (and access logs).
        body = await self._request(
            "POST",
            f"/data/query/{self._settings.sanity_dataset}",
            token=self._settings.sanity_api_read_token.get_secret_value(),
            params={"perspective": "published"},
            json={"query": groq, "params": dict(params or {})},
        )
        return body["result"]

    async def mutate(self, mutations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/data/mutate/{self._settings.sanity_dataset}",
            token=self._settings.sanity_api_write_token.get_secret_value(),
            params={"returnIds": "true", "returnDocuments": "true", "visibility": "sync"},
            json={"mutations": [dict(mutation) for mutation in mutations]},
        )

    async def upload_image(self, data: bytes, *, content_type: str) -> dict[str, Any]:
        body = await self._request(
            "POST",
            f"/assets/images/{self._settings.sanity_dataset}",
            token=self._settings.sanity_api_write_token.get_secret_value(),
            content=data,
            headers={"Content-Type": content_type},
            request_timeout=UPLOAD_TIMEOUT,
        )
        document: dict[str, Any] = body["document"]
        return document

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        request_timeout: httpx.Timeout = TIMEOUT,
        **kwargs: Any,
    ) -> dict[str, Any]:
        project_id = self._settings.sanity_project_id
        if not (project_id and token):
            raise SanityNotConfigured()
        base_url = f"https://{project_id}.api.sanity.io/{API_VERSION}"
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=request_timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            # Only the status: Sanity's error body can echo document content.
            logger.warning("Sanity %s %s returned %d", method, path, exc.response.status_code)
            raise SanityUnavailable() from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Sanity %s %s failed: %s", method, path, type(exc).__name__)
            raise SanityUnavailable() from exc
        return body


def get_sanity() -> Sanity:
    """FastAPI dependency. Tests override it with a fake."""
    return SanityClient(get_settings())


# --- Webhooks ---


class InvalidWebhookSignature(Unauthorized):
    code = "invalid_signature"
    default_message = "The webhook signature is missing or invalid."


def verify_webhook_signature(
    body: bytes, header: str | None, secret: str, *, now: datetime | None = None
) -> None:
    """Check a webhook call's `sanity-webhook-signature` header against its raw body.

    Same scheme as Sanity's `@sanity/webhook` library: an HMAC-SHA256 of `"<t>.<body>"` with
    the webhook's secret, base64url without padding. On top of that, calls more than
    `WEBHOOK_MAX_AGE` from now are refused. Raises `SanityNotConfigured` without a secret and
    `InvalidWebhookSignature` otherwise.
    """
    if not secret:
        raise SanityNotConfigured()
    match = _WEBHOOK_SIGNATURE.match((header or "").strip())
    if match is None:
        raise InvalidWebhookSignature()
    timestamp, signature = match.group(1), match.group(2)
    sent_at = datetime.fromtimestamp(int(timestamp) / 1000, UTC)
    if abs((now or datetime.now(UTC)) - sent_at) > WEBHOOK_MAX_AGE:
        raise InvalidWebhookSignature()
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, signature):
        raise InvalidWebhookSignature()
