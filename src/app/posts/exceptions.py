"""Exceptions raised by this feature."""

from app.core.exceptions import BadRequest, Conflict, NotFound


class PostNotFound(NotFound):
    code = "post_not_found"
    default_message = "That post doesn't exist."


class UnknownAuthor(BadRequest):
    code = "unknown_author"
    default_message = "There's no published Sanity author with that ID."


class UnsupportedPostImage(BadRequest):
    """Post images skip HEIC, which profile photos accept: Cloudinary converts photos, but
    Sanity stores post images as they are, and most browsers can't show HEIC."""

    code = "unsupported_image"
    default_message = "Upload a JPEG, PNG or WebP image."


class SlugTaken(Conflict):
    code = "slug_taken"
    default_message = "Another post already uses this slug. Choose another one."


class UnknownImage(BadRequest):
    code = "unknown_image"
    default_message = "That image doesn't exist. Upload it with POST /posts/images first."
