"""The `client` fixture routes every external service to its fake."""

from httpx import AsyncClient

from app.core.email import get_email_sender
from app.core.fakes import FakeEmailSender, FakeMediaStore, FakeSanity
from app.core.media import get_media_store
from app.core.sanity import get_sanity
from app.main import app


async def test_client_uses_the_fakes(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    fake_media: FakeMediaStore,
    fake_sanity: FakeSanity,
) -> None:
    assert app.dependency_overrides[get_email_sender]() is fake_email
    assert app.dependency_overrides[get_media_store]() is fake_media
    assert app.dependency_overrides[get_sanity]() is fake_sanity
