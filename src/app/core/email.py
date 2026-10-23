"""Email sending, over SMTP with `aiosmtplib`, behind this module.

Services call `send_code(sender, ...)`, which builds the message and sends it during the
request with a short timeout and one retry. A sender only has to implement `EmailSender`:
send one message, and raise `EmailDeliveryError` when that fails. `aiosmtplib` is async, so
a slow mail server never holds up other requests (the standard `smtplib` would).

Never log email addresses or codes.
"""

import asyncio
import logging
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid, parseaddr
from enum import StrEnum
from http import HTTPStatus
from typing import Final, Protocol

import aiosmtplib

from app.core.config import Settings, get_settings
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
    """Raised by a sender when a send fails. `send_code` retries it once."""


class EmailNotSent(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "email_not_sent"
    default_message = "We couldn't send the email. Try again in a moment."


class EmailNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "email_not_configured"
    default_message = "Email sending isn't set up on this server yet."


class NotConfiguredEmailSender:
    """Used while SMTP_HOST or EMAIL_FROM is empty. Sending always fails clearly, and codes
    are never written to the logs instead."""

    async def send(self, message: EmailMessage) -> None:
        logger.error("SMTP isn't configured, so an email could not be sent")
        raise EmailNotConfigured()


class SmtpEmailSender:
    """Sends each message over a new SMTP connection: codes are rare, so there's no
    connection to keep open. With `starttls` or `tls`, the server's certificate is checked
    and the connection never falls back to plain text."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> None:
        settings = self._settings
        try:
            mail = MimeMessage()
            mail["From"] = settings.email_from
            mail["To"] = message.to
            mail["Subject"] = message.subject
            mail["Date"] = formatdate(usegmt=True)
            # The sending domain, not this server's hostname, so the ID reveals nothing.
            mail["Message-ID"] = make_msgid(
                domain=parseaddr(settings.email_from)[1].rpartition("@")[2]
            )
            mail.set_content(message.text)
            await aiosmtplib.send(
                mail,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username or None,
                password=settings.smtp_password.get_secret_value() or None,
                use_tls=settings.smtp_security == "tls",
                start_tls=settings.smtp_security == "starttls",
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except (aiosmtplib.SMTPException, OSError, ValueError) as exc:
            # Only the error's type: SMTP replies can repeat the recipient's address.
            raise EmailDeliveryError(type(exc).__name__) from exc


def get_email_sender() -> EmailSender:
    """FastAPI dependency: SMTP once it's configured. Tests override it with a fake."""
    settings = get_settings()
    if settings.smtp_host and settings.email_from:
        return SmtpEmailSender(settings)
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
