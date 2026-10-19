from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.google import (
    GOOGLE_KEYS_URL,
    MIN_REFETCH_INTERVAL,
    GoogleEmailNotVerified,
    GoogleIdTokenVerifier,
    GoogleNotConfigured,
    GoogleUnavailable,
    InvalidGoogleToken,
    NotConfiguredGoogleVerifier,
)

CLIENT_ID = "test-client.apps.googleusercontent.com"
KID = "test-key-1"


@pytest.fixture(scope="module")
def google_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "alg": "RS256", "use": "sig"}]}


def _token(key: rsa.RSAPrivateKey, *, kid: str = KID, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "member@example.test",
        "email_verified": True,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class FakeGoogleKeys:
    """Serves a JWKS like Google's certs endpoint and counts the fetches."""

    def __init__(self, jwks: dict[str, Any], *, cache_control: str = "max-age=3600") -> None:
        self.jwks = jwks
        self.cache_control = cache_control
        self.fetches = 0
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GOOGLE_KEYS_URL
        self.fetches += 1
        return httpx.Response(
            self.status, json=self.jwks, headers={"Cache-Control": self.cache_control}
        )


MakeVerifier = Callable[..., tuple[GoogleIdTokenVerifier, FakeGoogleKeys]]


@pytest.fixture
def make_verifier(google_key: rsa.RSAPrivateKey) -> MakeVerifier:
    def _make(**keys_options: Any) -> tuple[GoogleIdTokenVerifier, FakeGoogleKeys]:
        keys = FakeGoogleKeys(_jwks(google_key), **keys_options)
        verifier = GoogleIdTokenVerifier(CLIENT_ID, transport=httpx.MockTransport(keys.handler))
        return verifier, keys

    return _make


async def test_a_genuine_token_is_accepted(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey
) -> None:
    verifier, _ = make_verifier()

    identity = await verifier.verify(_token(google_key))

    assert identity.subject == "1234567890"
    assert identity.email == "member@example.test"


async def test_the_issuer_without_https_is_accepted(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey
) -> None:
    verifier, _ = make_verifier()

    await verifier.verify(_token(google_key, iss="accounts.google.com"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-client.apps.googleusercontent.com"},
        {"iss": "https://evil.example"},
        {"exp": datetime.now(UTC) - timedelta(minutes=5)},
        {"sub": None},
        {"email": None},
        {"aud": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-subject", "no-email", "no-audience"],
)
async def test_bad_claims_are_refused(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey, overrides: dict[str, Any]
) -> None:
    verifier, _ = make_verifier()

    with pytest.raises(InvalidGoogleToken):
        await verifier.verify(_token(google_key, **overrides))


@pytest.mark.parametrize("email_verified", [False, None, "true"])
async def test_an_unverified_email_is_refused(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey, email_verified: object
) -> None:
    verifier, _ = make_verifier()

    with pytest.raises(GoogleEmailNotVerified):
        await verifier.verify(_token(google_key, email_verified=email_verified))


async def test_a_token_signed_by_another_key_is_refused(
    make_verifier: MakeVerifier, other_key: rsa.RSAPrivateKey
) -> None:
    verifier, _ = make_verifier()

    with pytest.raises(InvalidGoogleToken):
        await verifier.verify(_token(other_key))


async def test_a_symmetric_token_is_refused(make_verifier: MakeVerifier) -> None:
    verifier, _ = make_verifier()
    token = jwt.encode(
        {"sub": "x", "aud": CLIENT_ID}, "a-shared-secret-of-32-bytes-long!", headers={"kid": KID}
    )

    with pytest.raises(InvalidGoogleToken):
        await verifier.verify(token)


@pytest.mark.parametrize("token", ["not-a-jwt", "", "x" * 5000])
async def test_garbage_is_refused_without_fetching_keys(
    make_verifier: MakeVerifier, token: str
) -> None:
    verifier, keys = make_verifier()

    with pytest.raises(InvalidGoogleToken):
        await verifier.verify(token)

    assert keys.fetches == 0


async def test_keys_are_cached(make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey) -> None:
    verifier, keys = make_verifier()

    for _ in range(3):
        await verifier.verify(_token(google_key))

    assert keys.fetches == 1


async def test_keys_are_refetched_when_their_max_age_ends(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey
) -> None:
    verifier, keys = make_verifier(cache_control="public, max-age=0, must-revalidate")

    await verifier.verify(_token(google_key))
    await verifier.verify(_token(google_key))

    assert keys.fetches == 2


async def test_a_rotated_key_is_picked_up(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
) -> None:
    verifier, keys = make_verifier()
    await verifier.verify(_token(google_key))
    # Google publishes a new key; tokens signed with it name a kid we haven't seen yet.
    keys.jwks = _jwks(other_key, kid="test-key-2")
    verifier._fetched_at -= MIN_REFETCH_INTERVAL

    identity = await verifier.verify(_token(other_key, kid="test-key-2"))

    assert identity.email == "member@example.test"
    assert keys.fetches == 2


async def test_unknown_key_ids_cannot_make_us_refetch_constantly(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey
) -> None:
    verifier, keys = make_verifier()
    await verifier.verify(_token(google_key))

    for attempt in range(3):
        with pytest.raises(InvalidGoogleToken):
            await verifier.verify(_token(google_key, kid=f"made-up-{attempt}"))

    assert keys.fetches == 1


async def test_google_being_down_is_reported(
    make_verifier: MakeVerifier, google_key: rsa.RSAPrivateKey
) -> None:
    verifier, keys = make_verifier()
    keys.status = 503

    with pytest.raises(GoogleUnavailable):
        await verifier.verify(_token(google_key))


async def test_without_a_client_id_google_sign_in_is_off() -> None:
    with pytest.raises(GoogleNotConfigured):
        await NotConfiguredGoogleVerifier().verify("anything")
