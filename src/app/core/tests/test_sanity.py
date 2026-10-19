import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.sanity import API_VERSION, SanityClient, SanityNotConfigured, SanityUnavailable

BASE = f"https://proj123.api.sanity.io/{API_VERSION}"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sanity_project_id": "proj123",
        "sanity_dataset": "production",
        "sanity_api_read_token": SecretStr("read-token"),
        "sanity_api_write_token": SecretStr("write-token"),
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **overrides: object
) -> tuple[SanityClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return SanityClient(_settings(**overrides), transport=httpx.MockTransport(record)), requests


async def test_query_posts_to_the_live_api_with_the_read_token() -> None:
    sanity, requests = _client(lambda request: httpx.Response(200, json={"result": [{"a": 1}]}))

    result = await sanity.query("*[_type == $type]", {"type": "post"})

    assert result == [{"a": 1}]
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/data/query/production?perspective=published"
    assert request.headers["authorization"] == "Bearer read-token"
    assert json.loads(request.content) == {
        "query": "*[_type == $type]",
        "params": {"type": "post"},
    }


async def test_mutate_uses_the_write_token_and_waits_for_visibility() -> None:
    sanity, requests = _client(
        lambda request: httpx.Response(200, json={"transactionId": "tx", "results": []})
    )

    result = await sanity.mutate([{"create": {"_type": "post", "title": "Hi"}}])

    assert result["transactionId"] == "tx"
    request = requests[0]
    assert request.url.path == f"/{API_VERSION}/data/mutate/production"
    assert request.url.params["visibility"] == "sync"
    assert request.url.params["returnIds"] == "true"
    assert request.headers["authorization"] == "Bearer write-token"
    assert json.loads(request.content) == {
        "mutations": [{"create": {"_type": "post", "title": "Hi"}}]
    }


async def test_upload_image_sends_raw_bytes() -> None:
    sanity, requests = _client(
        lambda request: httpx.Response(200, json={"document": {"_id": "image-1", "url": "u"}})
    )

    document = await sanity.upload_image(b"\xff\xd8\xff", content_type="image/jpeg")

    assert document == {"_id": "image-1", "url": "u"}
    request = requests[0]
    assert request.url.path == f"/{API_VERSION}/assets/images/production"
    assert request.headers["content-type"] == "image/jpeg"
    assert request.headers["authorization"] == "Bearer write-token"
    assert request.content == b"\xff\xd8\xff"


async def test_error_responses_become_cms_unavailable_without_logging_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sanity, _ = _client(
        lambda request: httpx.Response(400, json={"error": {"description": "secret draft"}})
    )

    with pytest.raises(SanityUnavailable) as exc_info:
        await sanity.query("*")

    assert exc_info.value.status_code == 502
    assert "400" in caplog.text
    assert "secret draft" not in caplog.text


async def test_network_errors_become_cms_unavailable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    sanity, _ = _client(fail)

    with pytest.raises(SanityUnavailable):
        await sanity.mutate([])


async def test_non_json_responses_become_cms_unavailable() -> None:
    sanity, _ = _client(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(SanityUnavailable):
        await sanity.query("*")


@pytest.mark.parametrize(
    "overrides",
    [{"sanity_project_id": ""}, {"sanity_api_read_token": SecretStr("")}],
    ids=["no-project", "no-token"],
)
async def test_missing_configuration_fails_before_any_request(
    overrides: dict[str, object],
) -> None:
    sanity, requests = _client(
        lambda request: httpx.Response(200, json={"result": None}), **overrides
    )

    with pytest.raises(SanityNotConfigured):
        await sanity.query("*")

    assert requests == []


async def test_logs_never_contain_tokens(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sanity, _ = _client(lambda request: httpx.Response(500))

    with pytest.raises(SanityUnavailable):
        await sanity.query("*")

    assert "read-token" not in caplog.text
