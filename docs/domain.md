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
| `GET /sub-families`, `GET /promotions` | any member | the fixed lookup lists, sorted by name. They're short and fixed, so they're plain lists, not pages |
| `GET /me` | any member | your own account as `UserRead` |
| `GET /{id}/account` | the user themselves, or an admin | one account as `UserRead`; admins also see deactivated users. Anyone else gets 403 `not_owner` |
| `GET /accounts` | admins | every active account as `UserRead`, paginated; add `?include_deactivated=true` for deactivated ones too |
| `POST /` | admins | registers a member (201, `UserRead`): `{family_name, display_name, email, sub_family_id, promotion_id?, birthday?, role?}`. Role defaults to `member`; an admin also needs a password before they can log in |
| `PATCH /{id}` | admins | changes any fields of any user, deactivated ones included; send only what changes (`promotion_id` and `birthday` can be `null`). Returns `UserRead` |
| `POST /{id}/deactivate` | admins | soft-deletes the user and signs them out of every device in the same transaction, so their next request fails. Repeating it changes nothing; admins can't deactivate themselves (409 `cannot_deactivate_self`) |
| `POST /{id}/reactivate` | admins | lets them log in again. Old sessions stay revoked, so each device logs in from scratch |
| `PUT /{id}/password` | admins | sets an admin's password, their own included (`{password}`, 12 to 256 characters; 204). For an admin who has none yet or forgot it; 409 `not_an_admin` for anyone else. Their sessions aren't touched |

`UserPublicRead` is `{id, family_name, display_name, photo_url, sub_family: {id, name}, promotion: {id, name} | null, birthday: {month, day} | null}`. The year of birth is dropped inside the schema itself, so no route can send it to other members by mistake.

`UserRead` adds the private fields: the full `birthday` date, `email`, `email_verified_at`, `role`, `has_password` (admins can't log in until it's true), `deactivated_at` and `created_at`. The password hash is never in any response.

**Rules when an admin changes a user:**
- **A new email** (not just different capitals) clears `email_verified_at`. Sending a confirmation code to the new address comes with the profile step.
- **Demoting an admin** removes their password, since only admins have one. **Promoting** someone to admin doesn't give them one: set it before they log in.
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
- Senders can edit or delete their own wishes, and admins can delete any.
- Wishes are kept as history. v1 deleted all wishes every night; v2 doesn't.
- **Birthday emails are a later feature.** For now, the family's Slack covers birthday reminders.

## Blog posts

- Posts are stored in **Sanity**, including their images. We have no posts table.
- The Sanity dataset is **private**. The frontend never talks to Sanity directly: all reads and writes go through our API, so login, roles and deactivation apply to the blog too.
- Members read posts. Social media managers (and admins) create, edit and delete them, **through our frontend**, not in Sanity Studio.
- Image uploads for posts also go through our API to Sanity.
- Reads are cached on the backend (see [Infrastructure → Caching](infrastructure.md#caching)).

## Profile photos

Profile photos are stored in **Cloudinary**: images up to 5 MB. We store the Cloudinary ID and URL, never a local file. When a photo is replaced, the old one is deleted in the background.

## Data imported from v1

v1's MongoDB profiles will be imported into `users` by a one-off script (`scripts/import_v1.py`), after the `users` feature exists:

| v1 (`Profile`) | v2 (`users`) |
|---|---|
| `name` | display name |
| `familyName` | family name |
| `email`, `birthday` | email, birthday |
| `subFam` | `sub_family_id` (must match the fixed list) |
| `profilePic` | profile photo |

- **Roles** come from v1's `ADMINNAMES` list. Imported admins need a password set before they can log in.
- **Imported state:** everyone starts with `email_verified_at` empty and no sessions, so each member verifies on their first login.
- **v1 wishes are not imported.** They had no recipient and were wiped nightly anyway.
- **Safety:** the script can be re-run safely (it upserts by family name), and it's tested on a Neon branch first.
