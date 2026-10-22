"""The SMTP sender against a real SMTP server (aiosmtpd) on localhost."""

import socket
import ssl
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import Message
from functools import partial
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import aiosmtplib
import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, AuthResult, Envelope, LoginPassword, Session
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr, ValidationError

from app.core import email as email_module
from app.core.config import Settings, get_settings
from app.core.email import (
    CodePurpose,
    EmailDeliveryError,
    EmailMessage,
    NotConfiguredEmailSender,
    SmtpEmailSender,
    get_email_sender,
    send_code,
)

FROM = "Imena NewsBox <no-reply@imena.example>"
TO = "aline@example.com"
USERNAME, PASSWORD = "newsbox", "smtp-password"


class Inbox:
    """aiosmtpd handler: keeps every delivered message, or refuses every recipient."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.envelopes: list[Envelope] = []
        self.refuse = refuse

    async def handle_RCPT(
        self, server: SMTP, session: Session, envelope: Envelope, address: str, options: list[str]
    ) -> str:
        if self.refuse:
            return f"550 No such user: {address}"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        self.envelopes.append(envelope)
        return "250 Message accepted"

    def only_message(self) -> Message:
        [envelope] = self.envelopes
        assert isinstance(envelope.content, bytes)
        return message_from_bytes(envelope.content)


def _check_login(
    server: SMTP, session: Session, envelope: Envelope, mechanism: str, auth_data: Any
) -> AuthResult:
    ok = isinstance(auth_data, LoginPassword) and (auth_data.login, auth_data.password) == (
        USERNAME.encode(),
        PASSWORD.encode(),
    )
    return AuthResult(success=ok)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _run(inbox: Inbox, **options: Any) -> Iterator[int]:
    controller = Controller(inbox, hostname="127.0.0.1", port=_free_port(), **options)
    controller.start()
    try:
        yield controller.port
    finally:
        controller.stop()


@pytest.fixture
def inbox() -> Inbox:
    return Inbox()


@pytest.fixture
def plain_server(inbox: Inbox) -> Iterator[int]:
    """A relay without TLS or login, like a mail server on the same machine."""
    yield from _run(inbox)


def _settings(port: int, **fields: Any) -> Settings:
    # model_copy skips validation, so tests can also build settings the validator refuses.
    values: dict[str, Any] = {
        "email_from": FROM,
        "smtp_host": "127.0.0.1",
        "smtp_port": port,
        "smtp_security": "none",
        **fields,
    }
    return get_settings().model_copy(update=values)


# --- Sending ---


async def test_a_message_is_delivered(inbox: Inbox, plain_server: int) -> None:
    sender = SmtpEmailSender(_settings(plain_server))

    await sender.send(EmailMessage(to=TO, subject="Your login code", text="Your code is 123456."))

    [envelope] = inbox.envelopes
    assert envelope.mail_from == "no-reply@imena.example"
    assert envelope.rcpt_tos == [TO]
    message = inbox.only_message()
    assert (message["From"], message["To"], message["Subject"]) == (FROM, TO, "Your login code")
    assert message["Message-ID"].endswith("@imena.example>")
    assert message["Date"]
    payload = message.get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert "Your code is 123456." in payload.decode()


async def test_send_code_reaches_the_inbox_through_smtp(inbox: Inbox, plain_server: int) -> None:
    sender = SmtpEmailSender(_settings(plain_server))

    await send_code(sender, to=TO, code="654321", purpose=CodePurpose.LOGIN)

    payload = inbox.only_message().get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert "654321" in payload.decode()


async def test_non_ascii_text_and_names_survive(inbox: Inbox, plain_server: int) -> None:
    sender = SmtpEmailSender(
        _settings(plain_server, email_from="Imena Café <no-reply@imena.example>")
    )

    await sender.send(EmailMessage(to=TO, subject="Murakoze — merci", text="Café crème ✓"))

    message = inbox.only_message()
    payload = message.get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert "Café crème ✓" in payload.decode(message.get_content_charset() or "utf-8")


# --- Logging in ---


@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
async def test_it_logs_in_with_the_username_and_password(inbox: Inbox) -> None:
    for port in _run(inbox, authenticator=_check_login, auth_require_tls=False, auth_required=True):
        # Plain text only because the test server has no certificate; the settings refuse it.
        settings = _settings(port, smtp_username=USERNAME, smtp_password=SecretStr(PASSWORD))

        await SmtpEmailSender(settings).send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert len(inbox.envelopes) == 1


@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
async def test_a_wrong_password_is_a_delivery_error(inbox: Inbox) -> None:
    for port in _run(inbox, authenticator=_check_login, auth_require_tls=False, auth_required=True):
        settings = _settings(port, smtp_username=USERNAME, smtp_password=SecretStr("wrong"))

        with pytest.raises(EmailDeliveryError):
            await SmtpEmailSender(settings).send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert inbox.envelopes == []


# --- Failures ---


async def test_starttls_is_required_never_skipped(inbox: Inbox, plain_server: int) -> None:
    # This server doesn't offer STARTTLS: the sender refuses instead of sending in plain text.
    sender = SmtpEmailSender(_settings(plain_server, smtp_security="starttls"))

    with pytest.raises(EmailDeliveryError):
        await sender.send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert inbox.envelopes == []


async def test_an_unreachable_server_is_a_delivery_error() -> None:
    sender = SmtpEmailSender(_settings(_free_port()))

    with pytest.raises(EmailDeliveryError):
        await sender.send(EmailMessage(to=TO, subject="Hi", text="Hi"))


async def test_a_refused_recipient_is_an_error_without_the_address() -> None:
    inbox = Inbox(refuse=True)
    for port in _run(inbox):
        with pytest.raises(EmailDeliveryError) as exc_info:
            await SmtpEmailSender(_settings(port)).send(
                EmailMessage(to=TO, subject="Hi", text="Hi")
            )

    assert TO not in str(exc_info.value)


# --- Settings ---


def test_smtp_is_used_once_it_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(587))
    assert isinstance(get_email_sender(), SmtpEmailSender)

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(587, smtp_host=""))
    assert isinstance(get_email_sender(), NotConfiguredEmailSender)

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(587, email_from=""))
    assert isinstance(get_email_sender(), NotConfiguredEmailSender)


@pytest.mark.parametrize(
    "fields",
    [{"smtp_username": USERNAME}, {"smtp_password": PASSWORD}],
    ids=["username", "password"],
)
def test_credentials_are_refused_without_tls(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="never sent unencrypted"):
        Settings.model_validate({**get_settings().model_dump(), "smtp_security": "none", **fields})


def test_credentials_are_accepted_with_tls() -> None:
    base = get_settings().model_dump()
    for security in ("starttls", "tls"):
        settings = Settings.model_validate(
            {
                **base,
                "smtp_security": security,
                "smtp_username": USERNAME,
                "smtp_password": PASSWORD,
            }
        )
        assert settings.smtp_username == USERNAME


@pytest.mark.parametrize("email_from", ["no-reply", "Imena NewsBox", "<>"])
def test_email_from_must_be_an_address(email_from: str) -> None:
    with pytest.raises(ValidationError, match="EMAIL_FROM"):
        Settings.model_validate({**get_settings().model_dump(), "email_from": email_from})


# --- TLS, with a real certificate for 127.0.0.1 ---


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A self-signed certificate for 127.0.0.1, written as one PEM file (key and cert)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path_factory.mktemp("tls") / "server.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + cert.public_bytes(serialization.Encoding.PEM)
    )
    return path


def _server_tls(certificate: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certificate)
    return context


@pytest.fixture
def trust(monkeypatch: pytest.MonkeyPatch, certificate: Path) -> None:
    """Make the sender trust the test certificate, as it trusts real ones. Verification
    stays on: this only adds our test certificate to what's trusted."""
    real_send = aiosmtplib.send
    monkeypatch.setattr(aiosmtplib, "send", partial(real_send, cert_bundle=str(certificate)))


@pytest.mark.usefixtures("trust")
async def test_starttls_then_login(inbox: Inbox, certificate: Path) -> None:
    for port in _run(
        inbox,
        tls_context=_server_tls(certificate),
        require_starttls=True,
        authenticator=_check_login,
        auth_required=True,
    ):
        settings = _settings(
            port,
            smtp_security="starttls",
            smtp_username=USERNAME,
            smtp_password=SecretStr(PASSWORD),
        )
        await SmtpEmailSender(settings).send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert len(inbox.envelopes) == 1


@pytest.mark.usefixtures("trust")
@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
async def test_direct_tls_then_login(inbox: Inbox, certificate: Path) -> None:
    for port in _run(
        inbox,
        ssl_context=_server_tls(certificate),
        authenticator=_check_login,
        auth_required=True,
        # aiosmtpd only counts STARTTLS as TLS when offering AUTH, so with direct TLS it would
        # never offer it. The whole connection is TLS here.
        auth_require_tls=False,
    ):
        settings = _settings(
            port, smtp_security="tls", smtp_username=USERNAME, smtp_password=SecretStr(PASSWORD)
        )
        await SmtpEmailSender(settings).send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert len(inbox.envelopes) == 1


async def test_an_untrusted_certificate_is_refused(inbox: Inbox, certificate: Path) -> None:
    # Without `trust`, the self-signed certificate is unknown: nothing is sent, and the
    # password never leaves.
    for port in _run(inbox, tls_context=_server_tls(certificate), require_starttls=True):
        settings = _settings(port, smtp_security="starttls")

        with pytest.raises(EmailDeliveryError):
            await SmtpEmailSender(settings).send(EmailMessage(to=TO, subject="Hi", text="Hi"))

    assert inbox.envelopes == []
