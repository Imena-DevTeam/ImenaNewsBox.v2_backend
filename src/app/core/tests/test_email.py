import asyncio
import logging

import pytest

from app.core.email import (
    CodePurpose,
    EmailMessage,
    EmailNotConfigured,
    EmailNotSent,
    NotConfiguredEmailSender,
    build_code_email,
    get_email_sender,
    send_code,
)
from app.core.fakes import FakeEmailSender

ADDRESS = "member@example.test"
CODE = "042917"


@pytest.mark.parametrize("purpose", list(CodePurpose))
def test_code_email_contains_the_code_and_expiry(purpose: CodePurpose) -> None:
    message = build_code_email(ADDRESS, CODE, purpose)

    assert message.to == ADDRESS
    assert CODE in message.text
    assert "10 minutes" in message.text
    # The code stays out of the subject, which shows on lock screens and in inbox lists.
    assert CODE not in message.subject


async def test_send_code_sends_one_email() -> None:
    sender = FakeEmailSender()

    await send_code(sender, to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert len(sender.sent) == 1
    assert sender.last_code(ADDRESS) == CODE


async def test_send_code_retries_once_after_a_failure() -> None:
    sender = FakeEmailSender(failures_left=1)

    await send_code(sender, to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert len(sender.sent) == 1


async def test_send_code_gives_up_after_two_failures() -> None:
    sender = FakeEmailSender(failures_left=2)

    with pytest.raises(EmailNotSent) as exc_info:
        await send_code(sender, to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert exc_info.value.status_code == 503
    assert sender.sent == []


async def test_send_code_times_out_a_slow_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.email.SEND_TIMEOUT_SECONDS", 0.01)
    attempts = 0

    class SlowSender:
        async def send(self, message: EmailMessage) -> None:
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(1)

    with pytest.raises(EmailNotSent):
        await send_code(SlowSender(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert attempts == 2


async def test_failures_never_log_the_address_or_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmailNotSent):
        await send_code(
            FakeEmailSender(failures_left=2), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN
        )

    assert caplog.records
    assert ADDRESS not in caplog.text
    assert CODE not in caplog.text


async def test_without_a_provider_sending_fails_clearly_and_is_not_retried() -> None:
    assert isinstance(get_email_sender(), NotConfiguredEmailSender)

    with pytest.raises(EmailNotConfigured) as exc_info:
        await send_code(get_email_sender(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert exc_info.value.code == "email_not_configured"
