"""Google sign-in: checking the ID tokens that Google gives the frontend.

The frontend runs Google's sign-in button and sends us the resulting ID token (a JWT signed
by Google). We check it ourselves with PyJWT, with no Google SDK:

- the signature (RS256) against Google's published public keys,
- `iss` is Google, `aud` is our `GOOGLE_CLIENT_ID`, and the token hasn't expired,
- `email_verified` is true, so Google vouches that the person owns the address.

Google's public keys are fetched with `httpx` and kept in memory for as long as Google's
`Cache-Control` allows. They're public keys, not user data, so this is a deliberate exception
to "cache only posts". Never log tokens or email addresses.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from http import HTTPStatus
from typing import Any, Final, Protocol

import httpx
import jwt

from app.core.config import get_settings
from app.core.exceptions import AppError, Unauthorized

logger = logging.getLogger(__name__)

GOOGLE_KEYS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS: Final = ("accounts.google.com", "https://accounts.google.com")
ALGORITHM: Final = "RS256"
TIMEOUT: Final = httpx.Timeout(5.0)
# Used when Google's response has no max-age.
DEFAULT_KEYS_TTL: Final = timedelta(hours=1)
# A token signed with a key we don't know makes us refetch the keys (Google rotates them),
# but no more often than this, so junk tokens can't make us hammer Google.
MIN_REFETCH_INTERVAL: Final = timedelta(minutes=1)
# Google's ID tokens are around 1 KB; anything far bigger isn't one.
ID_TOKEN_MAX_LENGTH: Final = 4096

_MAX_AGE = re.compile(r"max-age=(\d+)")


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str


class InvalidGoogleToken(Unauthorized):
    code = "invalid_google_token"
    default_message = "Google sign-in didn't work. Try again."


class GoogleEmailNotVerified(Unauthorized):
    code = "google_email_not_verified"
    default_message = "Google hasn't verified this account's email address."


class GoogleUnavailable(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    code = "google_unavailable"
    default_message = "We couldn't reach Google. Try again in a moment, or use an emailed code."


class GoogleNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "google_not_configured"
    default_message = "Google sign-in isn't set up on this server yet."


class GoogleVerifier(Protocol):
    async def verify(self, id_token: str) -> GoogleIdentity:
        """Check an ID token and return who it belongs to, or raise one of the errors above."""
        ...


class GoogleIdTokenVerifier:
    def __init__(
        self,
        client_id: str,
        *,
        keys_url: str = GOOGLE_KEYS_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._keys_url = keys_url
        # Tests pass an `httpx.MockTransport`; production uses the default network transport.
        self._transport = transport
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_expire_at = datetime.min.replace(tzinfo=UTC)
        self._fetched_at = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()

    async def verify(self, id_token: str) -> GoogleIdentity:
        if len(id_token) > ID_TOKEN_MAX_LENGTH:
            raise InvalidGoogleToken()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise InvalidGoogleToken() from exc
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str):
            raise InvalidGoogleToken()

        key = await self._key(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=[ALGORITHM],
                audience=self._client_id,
                issuer=GOOGLE_ISSUERS,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidGoogleToken() from exc

        subject, email = claims.get("sub"), claims.get("email")
        if not isinstance(subject, str) or not isinstance(email, str) or not email:
            raise InvalidGoogleToken()
        if claims.get("email_verified") is not True:
            raise GoogleEmailNotVerified()
        return GoogleIdentity(subject=subject, email=email)

    async def _key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            now = datetime.now(UTC)
            stale = now >= self._keys_expire_at
            unknown = kid not in self._keys
            if stale or (unknown and now - self._fetched_at >= MIN_REFETCH_INTERVAL):
                await self._fetch_keys(now)
            key = self._keys.get(kid)
        if key is None:
            raise InvalidGoogleToken()
        return key

    async def _fetch_keys(self, now: datetime) -> None:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                response = await client.get(self._keys_url)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            logger.warning("Fetching Google's sign-in keys failed: %s", type(exc).__name__)
            raise GoogleUnavailable() from exc
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
        self._fetched_at = now
        match = _MAX_AGE.search(response.headers.get("cache-control", ""))
        ttl = timedelta(seconds=int(match.group(1))) if match else DEFAULT_KEYS_TTL
        self._keys_expire_at = now + ttl


class NotConfiguredGoogleVerifier:
    """Used while `GOOGLE_CLIENT_ID` is empty: Google sign-in fails clearly."""

    async def verify(self, id_token: str) -> GoogleIdentity:
        raise GoogleNotConfigured()


@cache
def _verifier(client_id: str) -> GoogleVerifier:
    # One instance per client ID, so its key cache lasts for the life of the process.
    if not client_id:
        return NotConfiguredGoogleVerifier()
    return GoogleIdTokenVerifier(client_id)


def get_google_verifier() -> GoogleVerifier:
    """FastAPI dependency. Tests override it with a fake."""
    return _verifier(get_settings().google_client_id)
