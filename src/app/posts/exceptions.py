"""Exceptions raised by this feature."""

from app.core.exceptions import BadRequest, NotFound


class PostNotFound(NotFound):
    code = "post_not_found"
    default_message = "That post doesn't exist."


class UnknownAuthor(BadRequest):
    code = "unknown_author"
    default_message = "There's no published Sanity author with that ID."
