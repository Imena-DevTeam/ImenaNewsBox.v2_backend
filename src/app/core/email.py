"""Email sending. The provider (Resend or the Gmail API, not chosen yet) stays behind this module.

Services call `send_code(sender, ...)`, which builds the message and sends it during the
request with a short timeout and one retry. A provider only has to implement `EmailSender`:
send one message over HTTPS, and raise `EmailDeliveryError` when that fails.

Never log email addresses or codes.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Final, Protocol

from app.core.exceptions import AppError
from app.core.security import CODE_TTL

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS: Final = 5.0
SEND_ATTEMPTS: Final = 2


class CodePurpose(StrEnum):
    LOGIN = "login"
    EMAIL_CHANGE = "email_change"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Send one message. Raise `EmailDeliveryError` if the provider fails."""
        ...


class EmailDeliveryError(Exception):
    """Raised by a provider when a send fails. `send_code` retries it once."""


class EmailNotSent(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "email_not_sent"
    default_message = "We couldn't send the email. Try again in a moment."


class EmailNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "email_not_configured"
    default_message = "Email sending isn't set up on this server yet."


class NotConfiguredEmailSender:
    """Used until an email provider is chosen and configured. Sending always fails clearly."""

    async def send(self, message: EmailMessage) -> None:
        logger.error("No email provider is configured, so an email could not be sent")
        raise EmailNotConfigured()


def get_email_sender() -> EmailSender:
    """FastAPI dependency. Returns the configured provider; tests override it with a fake."""
    return NotConfiguredEmailSender()


_CODE_EMAILS: Final = {
    CodePurpose.LOGIN: (
        "Your Imena NewsBox login code",
        "Your login code is {code}.\n\n"
        "It expires in {minutes} minutes and works once. If you didn't try to log in, "
        "ignore this email: nobody can get in without this code.",
    ),
    CodePurpose.EMAIL_CHANGE: (
        "Confirm your new Imena NewsBox email address",
        "Your confirmation code is {code}.\n\n"
        "Enter it in Imena NewsBox to confirm this email address. It expires in {minutes} "
        "minutes and works once.",
    ),
}


def build_code_email(to: str, code: str, purpose: CodePurpose) -> EmailMessage:
    subject, body = _CODE_EMAILS[purpose]
    minutes = int(CODE_TTL.total_seconds() // 60)
    return EmailMessage(to=to, subject=subject, text=body.format(code=code, minutes=minutes))


async def send_code(sender: EmailSender, *, to: str, code: str, purpose: CodePurpose) -> None:
    """Send a code email now. Raises `EmailNotSent` if both attempts fail."""
    message = build_code_email(to, code, purpose)
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                await sender.send(message)
            return
        except (EmailDeliveryError, TimeoutError) as exc:
            logger.warning(
                "Sending a %s code email failed (attempt %d of %d): %s",
                purpose,
                attempt,
                SEND_ATTEMPTS,
                type(exc).__name__,
            )
    raise EmailNotSent()
