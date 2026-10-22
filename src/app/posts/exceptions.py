"""Exceptions raised by this feature."""

from app.core.exceptions import NotFound


class PostNotFound(NotFound):
    code = "post_not_found"
    default_message = "That post doesn't exist."
