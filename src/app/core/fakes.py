"""In-memory fakes of the `core/` wrappers (email, Cloudinary, Sanity), for tests only.

The root `conftest.py` installs them for every request made through the `client` fixture, so
no test can reach a real external service. Tests get the same instances from the
`fake_email`, `fake_media` and `fake_sanity` fixtures to inspect or script them.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.email import EmailDeliveryError, EmailMessage
from app.core.media import MediaUnavailable, UploadedImage, check_image
from app.core.sanity import SanityUnavailable

_CODE = re.compile(r"\b(\d{6})\b")


@dataclass
class FakeEmailSender:
    sent: list[EmailMessage] = field(default_factory=list)
    # Make the next N sends fail, to exercise the retry and the error response.
    failures_left: int = 0

    async def send(self, message: EmailMessage) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise EmailDeliveryError("fake failure")
        self.sent.append(message)

    def sent_to(self, address: str) -> list[EmailMessage]:
        return [message for message in self.sent if message.to == address]

    def last_code(self, address: str) -> str:
        """The code in the most recent email to `address`, as a user would read it."""
        messages = self.sent_to(address)
        assert messages, f"no email was sent to {address}"
        match = _CODE.search(messages[-1].text)
        assert match, "the last email has no 6-digit code"
        return match.group(1)


@dataclass
class FakeMediaStore:
    uploads: dict[str, bytes] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_uploads: bool = False

    async def upload_profile_photo(self, data: bytes) -> UploadedImage:
        check_image(data)
        if self.fail_uploads:
            raise MediaUnavailable()
        public_id = f"test/profile-photos/{len(self.uploads) + 1}"
        self.uploads[public_id] = data
        return UploadedImage(public_id=public_id, url=f"https://media.test/{public_id}.jpg")

    async def delete_image(self, public_id: str) -> None:
        self.deleted.append(public_id)


@dataclass
class FakeSanity:
    """Records every call. Set `query_handler` to decide what queries return."""

    queries: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    mutations: list[list[dict[str, Any]]] = field(default_factory=list)
    uploads: list[tuple[bytes, str]] = field(default_factory=list)
    query_handler: Callable[[str, dict[str, Any]], Any] = lambda groq, params: None
    unavailable: bool = False

    async def query(self, groq: str, params: Mapping[str, Any] | None = None) -> Any:
        self._check_available()
        self.queries.append((groq, dict(params or {})))
        return self.query_handler(groq, dict(params or {}))

    async def mutate(self, mutations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        self._check_available()
        batch = [dict(mutation) for mutation in mutations]
        self.mutations.append(batch)
        return {
            "transactionId": f"tx-{len(self.mutations)}",
            "results": [{"operation": next(iter(mutation)), "id": "fake"} for mutation in batch],
        }

    async def upload_image(self, data: bytes, *, content_type: str) -> dict[str, Any]:
        self._check_available()
        self.uploads.append((data, content_type))
        asset_id = f"image-fake{len(self.uploads)}-1x1-jpg"
        return {"_id": asset_id, "url": f"https://cdn.sanity.test/{asset_id}.jpg"}

    def _check_available(self) -> None:
        if self.unavailable:
            raise SanityUnavailable()
