"""Fixtures for the posts tests."""

from collections.abc import Iterator

import pytest

from app.posts import service


@pytest.fixture(autouse=True)
def empty_posts_cache() -> Iterator[None]:
    """The posts cache lives for the whole process, so every test starts and ends empty."""
    service.clear_cache()
    yield
    service.clear_cache()
