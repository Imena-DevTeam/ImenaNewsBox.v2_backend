import asyncio
import logging

import pytest

from app.core.email import (
    CodePurpose,
    EmailDeliveryError,
    EmailMessage,
    EmailNotConfigured,
    EmailNotSent,
    NotConfiguredEmailSender,
    build_code_email,
    send_code,
)
from app.core.fakes import FakeEmailSender

ADDRESS = "member@example.test"
CODE = "04291"


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


async def test_send_code_times_out_a_slow_provider_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A slow server has used up the budget; a second attempt would only make the user
    # wait twice as long for the same error.
    monkeypatch.setattr("app.core.email.SEND_BUDGET_SECONDS", 0.01)
    attempts = 0

    class SlowSender:
        async def send(self, message: EmailMessage) -> None:
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(1)

    with pytest.raises(EmailNotSent):
        await send_code(SlowSender(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert attempts == 1


async def test_a_slow_send_inside_the_budget_gets_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reason for the budget: a send over a slow link takes many seconds, and the old
    # five-second limit per attempt cut every one of them off.
    monkeypatch.setattr("app.core.email.SEND_BUDGET_SECONDS", 0.5)
    sender = FakeEmailSender()

    class SlowButWorking:
        async def send(self, message: EmailMessage) -> None:
            await asyncio.sleep(0.1)
            await sender.send(message)

    await send_code(SlowButWorking(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert sender.last_code(ADDRESS) == CODE


async def test_the_retry_only_gets_what_is_left_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first attempt fails late, so the second has little time; together they never
    # take longer than the budget.
    monkeypatch.setattr("app.core.email.SEND_BUDGET_SECONDS", 0.2)
    attempts = 0

    class FailsLateThenHangs:
        async def send(self, message: EmailMessage) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await asyncio.sleep(0.15)
                raise EmailDeliveryError("SMTPServerDisconnected")
            await asyncio.sleep(1)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(EmailNotSent):
        await send_code(FailsLateThenHangs(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN)

    assert attempts == 2
    assert loop.time() - started < 0.5


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
    # Which sender `get_email_sender` picks is tested in test_smtp.py, with pinned settings:
    # this test mustn't depend on whether SMTP is set up in a local .env.
    with pytest.raises(EmailNotConfigured) as exc_info:
        await send_code(
            NotConfiguredEmailSender(), to=ADDRESS, code=CODE, purpose=CodePurpose.LOGIN
        )

    assert exc_info.value.code == "email_not_configured"
