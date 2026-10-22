from typing import Any

import pytest
from pydantic import ValidationError

from app.posts.schemas import PostCreate, PostUpdate, slugify

ASSET = "image-cff88f7d8e03653e754ee6a403328beeeb5ab6bb-6048x6048-jpg"


def block(text: str = "We went.", **fields: Any) -> dict[str, Any]:
    return {"_type": "block", "children": [{"_type": "span", "text": text}], **fields}


def post(**fields: Any) -> dict[str, Any]:
    return {
        "title": "A visit to our very own Ikuzwe",
        "summary": "A visit",
        "category": "Enjoyment",
        "image_asset_id": ASSET,
        "body": [block()],
        **fields,
    }


# --- Slugs ---


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("A visit to our very own Ikuzwe", "a-visit-to-our-very-own-ikuzwe"),
        ("  Café & Crème -- 2026!  ", "cafe-creme-2026"),
        ("Umuganura w'Imena", "umuganura-w-imena"),
        ("🎉🎂", ""),
    ],
)
def test_slugify(title: str, slug: str) -> None:
    assert slugify(title) == slug


def test_long_titles_make_slugs_of_at_most_200_characters() -> None:
    slug = slugify("word " * 100)

    assert len(slug) <= 200
    assert not slug.endswith("-")


def test_the_slug_comes_from_the_title_unless_sent() -> None:
    assert PostCreate.model_validate(post()).slug == "a-visit-to-our-very-own-ikuzwe"
    assert PostCreate.model_validate(post(slug="ikuzwe")).slug == "ikuzwe"


def test_a_title_without_letters_needs_a_slug() -> None:
    with pytest.raises(ValidationError, match="send a slug"):
        PostCreate.model_validate(post(title="🎉🎂"))


@pytest.mark.parametrize("slug", ["Ikuzwe", "two--dashes", "-start", "spa ce", "x" * 201, ""])
def test_malformed_slugs_are_refused(slug: str) -> None:
    with pytest.raises(ValidationError):
        PostCreate.model_validate(post(slug=slug))


# --- Required fields ---


@pytest.mark.parametrize("missing", ["title", "summary", "category", "image_asset_id", "body"])
def test_every_field_is_required(missing: str) -> None:
    fields = post()
    del fields[missing]

    with pytest.raises(ValidationError):
        PostCreate.model_validate(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "   "},
        {"summary": ""},
        {"category": "x" * 101},
        {"image_asset_id": "https://example.com/photo.jpg"},
        {"published_at": "2026-10-22T10:00:00"},  # no time zone
        {"author": "someone-else"},  # the author is always the writer
    ],
)
def test_invalid_posts_are_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PostCreate.model_validate(post(**fields))


def test_updates_can_change_one_field_but_never_clear_one() -> None:
    assert PostUpdate.model_validate({"title": "New"}).model_fields_set == {"title"}
    with pytest.raises(ValidationError, match="can't be null"):
        PostUpdate.model_validate({"summary": None})


# --- The body ---


def test_a_rich_body_is_accepted_and_gets_keys() -> None:
    body = [
        block("Our visit", style="h2"),
        {
            "_type": "block",
            "_key": "p1",
            "style": "normal",
            "markDefs": [{"_type": "link", "_key": "l1", "href": "https://ikuzwe.example"}],
            "children": [
                {"_type": "span", "text": "Bold, ", "marks": ["strong", "em"]},
                {"_type": "span", "text": "a link", "marks": ["l1"]},
            ],
        },
        block("First", listItem="bullet"),
        block("Quote", style="blockquote"),
    ]

    created = PostCreate.model_validate(post(body=body))
    dumped = [block.model_dump(exclude_none=True) for block in created.body]

    assert dumped[1]["_key"] == "p1"
    assert dumped[1]["markDefs"] == [
        {"_type": "link", "_key": "l1", "href": "https://ikuzwe.example"}
    ]
    assert all(item["_key"] for item in dumped)
    assert all(span["_key"] for item in dumped for span in item["children"])
    # A list item without a level is at the first level.
    assert dumped[2]["listItem"] == "bullet"
    assert dumped[2]["level"] == 1


@pytest.mark.parametrize(
    "body",
    [
        [],
        [block("   ")],
        [block(style="h7")],
        [block(listItem="checkbox")],
        [block(level=2)],
        [{"_type": "image", "asset": {"_ref": ASSET}}],
        [{**block(), "children": [{"_type": "span", "text": "x", "marks": ["highlight"]}]}],
        [{**block(), "children": [{"_type": "span", "text": "x", "marks": ["missing-link"]}]}],
        [block(_key="same"), block(_key="same")],
        [block(color="red")],
    ],
    ids=[
        "empty",
        "blank",
        "unknown-style",
        "unknown-list",
        "level-outside-list",
        "inline-image",
        "unknown-decorator",
        "link-without-def",
        "duplicate-keys",
        "unknown-field",
    ],
)
def test_bodies_the_editor_cant_produce_are_refused(body: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        PostCreate.model_validate(post(body=body))


@pytest.mark.parametrize(
    "href",
    ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>", "vbscript:x", "/relative"],
)
def test_only_web_and_email_links_are_allowed(href: str) -> None:
    body = [
        {
            "_type": "block",
            "markDefs": [{"_type": "link", "_key": "l1", "href": href}],
            "children": [{"_type": "span", "text": "click", "marks": ["l1"]}],
        }
    ]

    with pytest.raises(ValidationError, match="links must start with"):
        PostCreate.model_validate(post(body=body))


def test_email_links_are_allowed() -> None:
    body = [
        {
            "_type": "block",
            "markDefs": [{"_type": "link", "_key": "l1", "href": "mailto:family@example.com"}],
            "children": [{"_type": "span", "text": "write", "marks": ["l1"]}],
        }
    ]

    assert PostCreate.model_validate(post(body=body))
