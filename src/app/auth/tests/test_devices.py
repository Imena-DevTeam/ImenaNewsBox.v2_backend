import pytest

from app.auth.service import UNKNOWN_DEVICE, describe_device


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iPhone",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/130.0.0.0 Mobile/15E148 Safari/604.1",
            "Chrome on iPhone",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36",
            "Chrome on Windows",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:131.0) Gecko/20100101 Firefox/131.0",
            "Firefox on macOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) "
            "SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36",
            "Samsung Internet on Android",
        ),
        ("PostmanRuntime/7.42.0", UNKNOWN_DEVICE),
        ("", UNKNOWN_DEVICE),
        (None, UNKNOWN_DEVICE),
    ],
)
def test_describe_device(user_agent: str | None, label: str) -> None:
    assert describe_device(user_agent) == label
