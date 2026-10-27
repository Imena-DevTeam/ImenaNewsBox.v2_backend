# Domain: users, roles and features

## Who uses the platform

| Who | How they get in | What they can do |
|---|---|---|
| **Visitor** | Doesn't log in | Sees the frontend's public landing page only. No API access at all. |
| **Member** | Registered by an admin; logs in with family name + emailed code | Profiles, birthdays, wishes, reading the blog |
| **Social media manager** | Same as member | Everything a member can do, **plus** create, edit and delete blog posts |
| **Admin** | Registered by an admin (the first one is seeded); family name + password + emailed code | Everything, including user management |

Roles are **cumulative**: `member` ⊂ `social_media_manager` ⊂ `admin`. In code, check roles only through the shared dependency in `auth/dependencies.py` (e.g. `require_role(Role.SOCIAL_MEDIA_MANAGER)` lets in that role and anything above it). Never compare role strings by hand.

"Visitor" (or "guest") is **not** a role. There are no guest tokens and no public endpoints besides login and `/health`.

## Access rules

### Ownership

Users can change **their own** resources. Admins can change anything. One reusable check enforces this: *owner or admin, otherwise 403*.

| Field / action | Member (own profile) | Admin |
|---|---|---|
| Profile photo, display name, birthday | ✅ | ✅ |
| Family name, email, role | ❌ | ✅ |
| Sub-family, promotion | ❌ | ✅ |
| Deactivate a user | ❌ | ✅ |
| Edit or delete a wish | own wishes only | delete any |

### Every request re-checks the user

The access token says *who* you are, but on every request the backend loads your user row from the database, together with the session that issued the token (one quick query). Your **role** and whether you're **deactivated** come from the database, not from the token. That way, deactivating someone, signing out one of their devices or changing their role takes effect **immediately**, not when their token expires. This lookup is deliberately **never cached**.

### Who sees what

| Data | Other members | The user themselves and admins |
|---|---|---|
| Display name, family name, photo, sub-family, promotion | ✅ | ✅ |
| Birthday | day and month only | full date |
| Email, role, sessions, account status | ❌ | ✅ |

These are implemented as **separate response schemas** (e.g. `UserPublicRead` and `UserRead`), never as one schema with fields blanked out conditionally.

## Users

There's no separate "profile" table: profile fields live on `users`. A user has at least:

- **family name:** unique (case-insensitive) and used to log in
- **email:** where login codes are sent; `email_verified_at` records the first time a code to this address worked. Unique ignoring case, including deactivated users, so a code always reaches exactly one account.
- **sub-family** and optional **promotion:** chosen from fixed lists (`sub_families` and `promotions` tables, filled in by migrations)
- **role**, and a **password hash** (admins only)
- **display name**, **birthday**, **profile photo** (stored in Cloudinary)
- **deactivated_at:** set when the user is deactivated

> These are the minimum fields, not the final schema. Columns are added as features need them.

**The lookup lists** (seeded by a migration):
- **Sub-families:** HOPE, LIGHT, WIHOGORA. Every user has one.
- **Promotions,** oldest first: IBIMANUKA (graduates), NKONGI (year 3), SHEJA_NGABO (year 2), IBIHAME (year 1), as of 2026. Members move up a year with their promotion, and a new promotion arrives every year, so only the order (`position`) is stored, never the year. A promotion is optional: members imported from v1 have none until an admin sets it.
- **Adding next year's promotion** is a small data migration that inserts it at the next `position`.

**Rules:**
- **No self-signup.** Only admins create users. The very first admin is created by a seed script from environment variables.
- **Changing an email** (admin only) clears `email_verified_at` and sends a code to the new address, which the user confirms while logged in.
- **Soft delete only.** Deactivating sets `deactivated_at`, signs the user out everywhere, and blocks future logins. Deactivated users disappear from member listings, but their family name stays reserved and their wishes are kept.

### Users API

All under `/api/v1/users`, and all need an access token.

| Endpoint | Who | Returns |
|---|---|---|
| `GET /` | any member | the directory: active members, alphabetical by display name, paginated (`?page=&limit=`), as `UserPublicRead` |
| `GET /{id}` | any member | one active member as `UserPublicRead`; 404 `user_not_found` for unknown or deactivated users |
| `GET /sub-families`, `GET /promotions` | any member | the fixed lookup lists: sub-families by name, promotions **oldest first** (by their `position`). They're short, so they're plain lists, not pages |
| `GET /me` | any member | your own account as `UserRead` |
| `PATCH /me` | any member | changes your own `display_name` and `birthday` (null clears it), and nothing else: any other field is a 422. Returns `UserRead` |
| `POST /me/email/verify` | any member | `{code}`: confirms your email with the code sent to it, and sets `email_verified_at`. 401 `invalid_code` otherwise; login codes don't work here |
| `POST /me/email/resend` | any member | emails a new confirmation code (same limits as login codes: one a minute, ten a day). 409 `email_already_verified` if it's confirmed already |
| `GET /{id}/account` | the user themselves, or an admin | one account as `UserRead`; admins also see deactivated users. Anyone else gets 403 `not_owner` |
| `GET /accounts` | admins | every active account as `UserRead`, paginated; add `?include_deactivated=true` for deactivated ones too |
| `POST /` | admins | registers a member (201, `UserRead`): `{family_name, display_name, email, sub_family_id, promotion_id?, birthday?, role?}`. Role defaults to `member`; an admin also needs a password before they can log in |
| `PATCH /{id}` | admins | changes any fields of any user, deactivated ones included; send only what changes (`promotion_id` and `birthday` can be `null`). Returns `UserRead` |
| `POST /{id}/deactivate` | admins | soft-deletes the user and signs them out of every device in the same transaction, so their next request fails. Repeating it changes nothing; admins can't deactivate themselves (409 `cannot_deactivate_self`) |
| `POST /{id}/reactivate` | admins | lets them log in again. Old sessions stay revoked, so each device logs in from scratch |
| `PUT /{id}/photo` | the user themselves, or an admin | uploads a profile photo (multipart field `photo`; JPEG, PNG, WebP or HEIC, at most 5 MB, checked by its content, not its name). Returns `UserRead`. Errors: 400 `unsupported_image`, 413 `image_too_large`, 502 `media_unavailable` |
| `DELETE /{id}/photo` | the user themselves, or an admin | removes the photo. Returns `UserRead` |
| `PUT /{id}/sanity-author` | admins | `{author_id}`: credits the user's blog posts to an existing Sanity author, or unlinks with `null` (see Blog posts → Authors). Returns `UserRead`. Errors: 400 `unknown_author`, 409 `not_a_writer` (plain members) or `author_already_linked` |
| `PUT /{id}/password` | admins | sets an admin's password, their own included (`{password}`, 12 to 256 characters; 204). For an admin who has none yet or forgot it; 409 `not_an_admin` for anyone else. Their sessions aren't touched |

`UserPublicRead` is `{id, family_name, display_name, photo_url, sub_family: {id, name}, promotion: {id, name} | null, birthday: {month, day} | null}`. The year of birth is dropped inside the schema itself, so no route can send it to other members by mistake.

`UserRead` adds the private fields: the full `birthday` date, `email`, `email_verified_at`, `role`, `has_password` (admins can't log in until it's true), `sanity_author_id` (the Sanity author their blog posts are credited to; see Blog posts), `deactivated_at` and `created_at`. The password hash is never in any response.

**Rules when an admin changes a user:**
- **A new email** (not just different capitals) clears `email_verified_at` and emails a confirmation code to the new address. If that email can't be sent, the admin's change is still saved; the member asks for another code with `POST /me/email/resend`.
- **Demoting an admin** removes their password, since only admins have one. **Promoting** someone to admin doesn't give them one: set it before they log in.
- **Demoting someone to a plain member** unlinks their Sanity author, since only post writers have one. Their old posts keep their author; if they're promoted again, an admin links them again.
- **Admins can't change their own role** (409 `cannot_change_own_role`), so the last admin can't demote themselves and leave nobody to manage users. They can change their other fields.

**Errors when creating or changing a user:**
- 409 `family_name_taken` or `email_taken`, ignoring case and including deactivated users. These come from the database's unique indexes, so two admins saving at once can't both win.
- 400 `unknown_sub_family` or `unknown_promotion`.
- 422 for invalid input: a malformed email, a birthday in the future or before 1900, a blank name, an unknown role, or **any unexpected field** (so a typo such as `"pasword"` is an error, not silently ignored).

## Birthdays and wishes

- A wish has a **sender**, a **recipient** and the **birthday year** it's for.
- Any member can wish any other member, but **not themselves**.
- **One wish per sender, per recipient, per year.** A database constraint enforces it.
- Wishes can only be posted when the recipient's birthday is **today or within the next 30 days**. "Upcoming birthdays" also means the next 30 days.
  - A wish is for the recipient's **next birthday**, and its `year` is that birthday's year (never the year of birth). On 20 December, a wish for a 5 January birthday is next year's wish.
  - **29 February birthdays** fall on 28 February in years without a 29th.
  - "Today" is the date in **UTC**, as in v1.
- Senders can edit or delete their own wishes, and admins can delete any.
- Wishes are kept as history. v1 deleted all wishes every night; v2 doesn't.
- **Birthday emails are a later feature.** For now, the family's Slack covers birthday reminders.

### Wishes API

All under `/api/v1/wishes`, and all need an access token.

| Endpoint | Who | Returns |
|---|---|---|
| `POST /` | any member | `{recipient_id, message}` (1 to 2000 characters): wishes an active member for their next birthday (201, `WishRead`). The server sets the `year`; sending one is a 422 |
| `GET /upcoming-birthdays` | any member | active members whose birthday is today or in the next 30 days, soonest first, yours included: `[{member: UserPublicRead, falls_on, days_until, wished}]`. `falls_on` is the birthday's next date, `days_until` is 0 on the day, and `wished` says whether you've already wished them for it. A month of birthdays is short, so it's a plain list, not a page |
| `GET /received` | any member | wishes sent to you, newest first, paginated; `?year=` keeps one birthday's. Admins can add `?user_id=` to see anyone's; for anyone else that's a 403 `not_owner`. Wishes from deactivated members are kept and listed |
| `GET /sent` | any member | wishes you sent, with the same options as `/received` |
| `PATCH /{id}` | the sender only, not admins | `{message}`: changes the message. Returns `WishRead` |
| `DELETE /{id}` | the sender, or an admin | deletes the wish (204) |

`WishRead` is `{id, sender, recipient, year, message, created_at, updated_at}`, with `sender` and `recipient` as `UserPublicRead`, so a wish never shows a year of birth.

**Errors:**
- 400 `cannot_wish_yourself`.
- 409 `birthday_not_soon`: the recipient's birthday isn't today or in the next 30 days, or they haven't given one.
- 409 `wish_already_sent`: you've already wished them for this birthday. It comes from the database's unique constraint, so two requests at once can't both get through.
- 404 `user_not_found` for an unknown or deactivated recipient, and `wish_not_found` for an unknown wish. Someone else's wish is a 403 `not_owner`.

## Blog posts

- Posts are stored in **Sanity**, including their images. We have no posts table.
- The Sanity dataset is **private**. The frontend never talks to Sanity directly: all reads and writes go through our API, so login, roles and deactivation apply to the blog too.
- Members read posts. Social media managers (and admins) create, edit and delete them, **through our frontend**, not in Sanity Studio.
- Image uploads for posts also go through our API to Sanity.
- Reads are cached on the backend (see [Infrastructure → Caching](infrastructure.md#caching)).

**The post fields in Sanity** (from the existing documents; the Studio requires none of them):

| Sanity field | Our API | What it is |
|---|---|---|
| `title`, `summary` | `title`, `summary` | text |
| `slug.current` | `slug` | the post's URL name, e.g. `visit-to-ikuzwe` |
| `categories` | `category` | **one** free-text category, despite the plural name |
| `publishedAt` | `published_at` | date and time |
| `image` | `image: {url, width, height, hotspot, crop}` | the main image, stored in Sanity, with its cropping data |
| `author` | `author: {id, name, image_url}` | a reference to an `author` document (`name`, `image`) |
| `body` | `body` | Portable Text: Normal, Heading 1–6 and Quote blocks; bold, italic, code, underline, strikethrough; bullet and numbered lists; links. No images inside the body |

Sanity serves image files from its CDN to anyone who has the URL, even though the dataset itself is private. That's the same as Cloudinary profile photos: the URLs are long and unguessable, but not secret.

### Posts API

All under `/api/v1/posts`, and all need an access token.

| Endpoint | Who | Returns |
|---|---|---|
| `GET /` | any member | published posts, newest first, without their body, paginated; `?category=` keeps one category's (exact match). Only posts with a slug are listed, since posts are opened by slug |
| `POST /` | social media managers and admins | publishes a post credited to you (201, `PostRead`): `{title, slug?, summary, category, published_at?, image_asset_id, body}`. Every field is required except `slug` (made from the title, e.g. "Café 2026!" → `cafe-2026`) and `published_at` (now). Errors: 409 `slug_taken`, 400 `unknown_image` |
| `POST /images` | social media managers and admins | uploads a post image to Sanity (multipart field `image`: JPEG, PNG or WebP, at most 5 MB, checked by its content). 201 `{asset_id, url}`; send `asset_id` with the post. HEIC isn't accepted here (unlike profile photos): Sanity keeps post images as they are, and most browsers can't show HEIC. An image no post uses stays in Sanity unused |
| `GET /authors` | admins | every published Sanity author by name, `[{id, name, image_url, user_id}]`, where `user_id` is the member it's linked to (or `null`). For linking authors; not cached |
| `GET /{slug}` | any member | one published post with its `body` and `updated_at`; 404 `post_not_found` |
| `PATCH /{id}` | social media managers and admins | changes **any** post's fields (by its Sanity ID, which never changes; a slug can). Send only what changes; nothing can be cleared. The author stays the original writer. Returns `PostRead`. Errors: 404 `post_not_found`, 409 `slug_taken`, 400 `unknown_image` |
| `DELETE /{id}` | social media managers and admins | deletes any post (204). Its image stays in Sanity unused |
| `POST /webhook` | Sanity only, **no login** | clears the posts cache when a post or author changes in the Studio (204). Checked by its `sanity-webhook-signature` instead of a token; 401 `invalid_signature` otherwise. The body is never used. Setup: [Infrastructure → Setting up Sanity](infrastructure.md#setting-up-sanity) |

Any social media manager can edit or delete any post, not only their own: it's one small team running one blog (decision 64's link only decides who a *new* post is credited to).

**Studio drafts:** our tokens only see published posts. If someone also has an unpublished draft of a post open in the Studio, publishing that draft there overwrites edits made through the app, and a deleted post comes back. Editing in the app or in the Studio, not both, avoids this.

**Authors.** Posts are credited to Sanity `author` documents, and each member can be linked to one author through `users.sanity_author_id`:
- A post written through our app is credited to its writer's author. If the writer isn't linked yet, the backend creates an author for them on their first post.
- Members who wrote posts in the Studio already have an author. An admin links them once with `PUT /api/v1/users/{id}/sanity-author` `{author_id}` (see the Users API), so their old and new posts share one author instead of two with the same name.
- Only **social media managers and admins** (the people who write posts) can have an author (409 `not_a_writer`); the database enforces it with a check constraint.
- An author can belong to only one member (409 `author_already_linked`), and must be a published author in Sanity (400 `unknown_author`). `{"author_id": null}` unlinks.

Reads accept posts with missing fields (each field can be `null`), so a post made in the Studio never breaks the list. If Sanity is down: 502 `cms_unavailable`.

**Rules for posts written through our API** (the Studio checks none of this, so our API is where the rules live):
- **Every field is required.** Limits: title 200 characters, summary 500, category 100, slug 200 (lowercase words joined by dashes).
- **Slugs are unique.** The check runs just before saving; two writers saving the same slug at the same moment could both get through, which is unlikely enough on a family blog to accept.
- **The body only accepts what the Studio's editor makes:** blocks with the Normal, Heading 1–6 or Quote styles; bold (`strong`), italic (`em`), `code`, `underline` and `strike-through`; bullet and numbered lists; and links. Anything else (an unknown style, an image inside the text, an unknown field) is a 422, so the frontend can render every post it gets. Missing `_key`s are filled in.
- **Links must be `http://`, `https://` or `mailto:`.** A `javascript:` link would run code in a reader's browser when clicked.
- **The author is always the writer** and can't be sent. On every new post, the writer's Sanity author takes their current display name.
- **One transaction:** the writer's author (created if needed) and the post are saved to Sanity together, so a failure leaves nothing half-written.

## Profile photos

Profile photos are stored in **Cloudinary**: images up to 5 MB. We store the Cloudinary ID and URL, never a local file. When a photo is replaced, the old one is deleted in the background.

The order matters: the new photo is uploaded and saved **before** the old one is deleted, so a failed upload never leaves a member without their photo. If the old delete is lost, Cloudinary just keeps an unused image. Cloudinary stores every photo as a JPEG of at most 1024 px a side, so HEIC photos from iPhones show in every browser.

## Data imported from v1

v1's MongoDB profiles are imported into `users` by a one-off script (`scripts/import_v1.py`). [Infrastructure](infrastructure.md#importing-v1s-members) has the steps.

| v1 (`Profile`) | v2 (`users`) |
|---|---|
| `name` | display name |
| `familyName` | family name |
| `email`, `birthday` | email, birthday |
| `subFam` | `sub_family_id` (must match the fixed list) |
| `profilePic` | profile photo (Cloudinary links only) |
| `_id` | `legacy_id` (unique), and its creation time becomes `created_at` |

- **Roles:** everyone is imported as a plain member. The seeded first admin promotes the others in the app and links the Sanity authors.
- **Imported state:** everyone starts with `email_verified_at` empty, no promotion and no sessions, so each member verifies on their first login.
- **v1 wishes are not imported.** They had no recipient and were wiped nightly anyway.
- **Safety:** it's a dry run unless asked to write, and running it again is safe. It finds people already in v2 by `legacy_id`, or else by family name with a matching email, and only fills their empty fields. It's tested on a Neon branch first.
