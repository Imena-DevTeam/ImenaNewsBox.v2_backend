# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Backend API for Imena NewsBox, a private family platform for member profiles, birthday wishes and a blog. v2 is a from-scratch rewrite of v1 (`../backend`, Node/Express/MongoDB) with the same mission. **v1 is reference only:** don't port its code, endpoints or response shapes. Its data will be imported (see "v1 data import"). The v2 changes are documented in Notion ("Imena NewsBox" page).

**Hard constraint: everything must run on free tiers.** That means Neon free Postgres and GitHub Actions. The backend is moving from Render to **Docker on a VPS** (decision 69); the team is still settling the details, including whether the VPS changes this rule, so keep everything else on free tiers until told otherwise. Don't introduce anything that needs a paid plan or an always-on extra process (workers, cron services, Redis) without asking.

## Stack

- Python 3.14, **uv** (`pyproject.toml` + `uv.lock`), FastAPI, Pydantic v2, pydantic-settings
- **Neon** Postgres via **async** SQLAlchemy 2.x and Alembic (async `env.py`). The dependency is `sqlalchemy[asyncio]`, because 2.1 no longer installs `greenlet` by default. Use `AsyncSession`, `create_async_engine` and `postgresql+psycopg://` (psycopg 3).
- Auth (`core/security.py`): PyJWT access tokens (`ACCESS_TOKEN_SECRET`), opaque random refresh tokens stored as an HMAC (`REFRESH_TOKEN_SECRET`), 6-digit codes stored as an HMAC (`CODE_SECRET`), and pwdlib[argon2] for admin passwords (async, run in a thread)
- Background work: FastAPI `BackgroundTasks` only, for fire-and-forget cleanup. **No Celery, no Redis, no scheduler.**
- Caching: `cachetools`, in-process (see "Caching")
- External services:
  - **Sanity CMS:** everything blog-related, including its images. Called through its HTTP API with `httpx`; there's no official Python SDK. The PyPI package named `sanity` is unrelated, so never add it.
  - **Cloudinary:** profile photos only, using the official `cloudinary` SDK.
  - **Google sign-in:** `core/google.py` checks Google ID tokens with PyJWT (`pyjwt[crypto]`) against Google's public keys, fetched with `httpx`. No Google SDK. `GOOGLE_CLIENT_ID` is the frontend button's client ID; empty turns Google sign-in off.
  - **Email:** plain **SMTP**, sent with `aiosmtplib` (async) behind `core/email.py` (`SmtpEmailSender`). Settings: `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls`, `tls` or `none`; credentials need TLS), `SMTP_USERNAME`, `SMTP_PASSWORD`. Never the blocking `smtplib`. Tests use the fake; `core/tests/test_smtp.py` checks the real sender against a local `aiosmtpd` server.
- pytest + pytest-asyncio + httpx; ruff; mypy
- Docker, deployed as one container on a **VPS** (details pending; see "Deployment")

## Architecture

```
src/app/
├── main.py              # create_app(): middleware (CORS allowlist, no-store), handlers, /api/v1 routers
├── core/                # cross-cutting only: config.py, database/ (base.py: Base, engine.py: engine +
│                        # SessionLocal, session.py: get_session),
│                        # security.py, exceptions.py, pagination.py, and thin wrappers for external services:
│                        # email.py, media.py (Cloudinary), sanity.py, plus fakes.py (test doubles)
├── auth/                # login, email codes, sessions/tokens, current-user, role + ownership deps
├── users/               # users (profile fields included), sub-families, promotions, admin user management
├── wishes/              # birthday wishes (sender → recipient), upcoming birthdays
├── posts/               # blog writes and member reads, proxied to Sanity (no posts table)
└── <feature>/
    ├── router.py        # thin: parse input → call service → shape response
    ├── schemas.py       # Pydantic DTOs (separate Create / Update / Read)
    ├── models.py        # SQLAlchemy models (only if the feature owns tables)
    ├── service.py       # business logic + DB / external calls
    ├── dependencies.py  # feature Depends
    ├── exceptions.py
    └── tests/
alembic/                 # env.py imports every feature's models
tests/                   # cross-feature / e2e tests
conftest.py              # shared fixtures; at the repo root so it also applies to src/app/*/tests/
scripts/                 # one-off operational scripts (v1 import, admin seed), not imported by the app
docs/                    # human-facing docs: architecture, domain, auth, infrastructure, decision log, roadmap
.github/workflows/       # CI (ruff + mypy + pytest) and Malware Guard
```

- **No controller layer.** The flow is router → service. Ask before adding a repository layer.
- **Cross-feature imports:** a feature may import another feature's `service` or `schemas`, never its router internals. Break cycles with `relationship("Model")` strings and `TYPE_CHECKING` imports.
- **One shared `Base`** lives in `core/database/base.py`. Import from the `core/database/` submodules (`base`, `engine`, `session`), not the package, so importing `Base` in a model never creates the engine.

## Domain and access control

- **No guests and no public API.** Visitors only ever see the frontend's static landing page. Every endpoint requires an access token, except these:
  - login, code verification, refresh and logout
  - `/health`
  - the Sanity webhook, which is verified by its signature
- **Roles are cumulative:** `member` ⊂ `social_media_manager` ⊂ `admin`. Social media managers are members who can also create, update and delete blog posts. Admins can do everything. Check roles through `auth/dependencies.py` only: take `CurrentUser`, `SocialMediaManagerUser` or `AdminUser` as a route parameter, or `Depends(require_role(Role.X))`, which admits that role and above. In code, use `role.at_least(...)`; never compare role strings or use `<`/`>` on roles.
- **Ownership-based access:** users may modify only resources they own, such as their own profile fields and their own wishes. Admins may modify anything. Enforce ownership with `require_owner_or_admin(user, owner_id)` from `auth/dependencies.py` (pass `allow_admin=False` where only the owner may act, e.g. editing a wish), never ad hoc.
  - **Members can edit:** their photo, display name and birthday.
  - **Only admins can change:** family name, email, role, sub-family, promotion and active status.
- **Users are created only by admins.** There's no self-signup. The first admin is created by a seed command, never through a public endpoint.
- **Users table:** profile fields live on `users`, with no separate profile table. It holds at least:
  - family name (**unique**, case-insensitive, used to log in)
  - email (**unique**, case-insensitive) and `email_verified_at` (nullable)
  - `sub_family_id` and `promotion_id`, which reference the fixed lookup tables `sub_families` and `promotions` (promotion is optional)
  - role, `password_hash` (admins only), display name, birthday, profile photo (Cloudinary public ID / URL)
  - `deactivated_at`

  The Notion doc lists minimums, not the final schema, so design columns as each feature needs them.
- **Soft delete only.** Deactivating sets `deactivated_at`, revokes all of that user's sessions and blocks login. Deactivated users are excluded from listings by default, and their family name stays reserved. Their wishes are kept.
- **Visibility:**
  - Members see each other's display name, family name, photo, sub-family, promotion and birthday as **day and month only**.
  - Email, birth year, role, sessions and status are visible only to the user themselves and to admins.
  - Use separate read schemas (e.g. `UserPublicRead` vs `UserRead`) and never one schema with optional hiding.
- **Login is per device.** A device holding a valid refresh token never logs in again. A new device must prove itself:
  - **Admin:** family name + password, **then** an emailed code **or** Google sign-in (with the password).
  - **Member / social media manager:** family name, then an emailed code, **or** Google sign-in on its own.
  - **Google sign-in** (`POST /auth/google`) matches the active user by `lower(email)` and replaces the code: it works before verification too and sets `email_verified_at`. No login path ever creates an account. Every path ends in `start_session`.
  - **After a successful login:**
    - It issues an **access token (15 min)** and a **refresh token (90 days)**. Lifetimes come from `ACCESS_TOKEN_EXPIRES_IN` / `REFRESH_TOKEN_EXPIRES_IN`.
    - The refresh token's hash is stored in a `user_sessions` row: user, device label (from the user agent), created and last-used times, revoked.
    - Every refresh **rotates** the token, which resets the 90 days. Reusing an old refresh token revokes the session.
  - **Transport:** the frontend (on Vercel) proxies `/api/*` to this backend (on Render) with a `vercel.json` rewrite. To the browser the API is **same-origin**, so the refresh cookie is first-party and isn't blocked by Safari or other third-party-cookie restrictions.
    - The access token goes in the response body, and the frontend sends `Authorization: Bearer …`.
    - The refresh token is **only** ever set as a cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api/v1/auth`, **with no `Domain` attribute** (so it binds to the Vercel host). It is never put in a response body.
    - Refresh and logout also check `Origin` against the allowed frontend origins (`CORS_ORIGINS`). That's defense in depth against CSRF.
    - Browser traffic is same-origin through the proxy, so CORS only matters for local dev and direct calls. Use explicit origins with `allow_credentials=True`, never `*`.
    - Requests arrive through Vercel and Render's proxies. Read the client IP and user agent from forwarded headers (uvicorn `--proxy-headers`), and don't make IP the only throttle key.
  - Users can list and revoke their own sessions, and admins can revoke anyone's. Knowing a family name is never enough on its own to get tokens.
  - **Every authenticated request loads the user from the DB** in one query (by primary key, joined to the token's session), and rejects deactivated users and revoked or expired sessions. Role comes from the DB row, not the token. **Never cache this lookup**, so deactivation, session revocation and role changes take effect immediately.
- **Email verification:**
  - Codes are 6 digits, expire after 10 minutes, are single-use and stored hashed, and allow 5 attempts with a 60-second resend cooldown and at most 10 codes per user and purpose in 24 hours. Only the newest code works. Use `issue_code` / `consume_code` in `auth/service.py` for every purpose.
  - Throttling is stored in Postgres, not Redis.
  - The first successful code (or Google login) sets `email_verified_at`. When an admin changes a user's email, `email_verified_at` is cleared and a code is sent to the new address. The user confirms it with an authenticated endpoint.
- **Wishes** have a sender, a **recipient** and the birthday year, and are kept as history; there's no purge.
  - Any member can wish any other member, but not themselves.
  - There's **one wish per sender, recipient and year**, enforced with a unique constraint.
  - Senders can edit or delete their own wishes, and admins can delete any.
  - Wishes can only be posted when the recipient's birthday is today or within the next 30 days. "Upcoming birthdays" also means the next 30 days.
- **Blog posts live in Sanity,** including images, and the dataset is **private**.
  - All reads and writes go through our API. The frontend never talks to Sanity directly, and social media managers use our frontend, not Sanity Studio.
  - `posts/service.py` uses `SANITY_API_READ_TOKEN` for member reads and `SANITY_API_WRITE_TOKEN` for writes, which are gated by role.
  - Post image uploads go through our API to Sanity's assets endpoint.
  - The Sanity webhook (verified with `SANITY_WEBHOOK_SECRET`) is a backup for cache invalidation.
  - The post schema already exists in Sanity; its fields are listed in `docs/domain.md` (Blog posts). Don't invent others. Studio requires no fields, but **writes through our API require every field**, and reads accept missing ones.
  - **Authors:** each social media manager or admin can be linked to one Sanity `author` (`users.sanity_author_id`, unique; a check constraint keeps plain members without one, and demotion to member unlinks it). A post written through our API is credited to its writer's author, created on their first post if needed. Admins link people who already have an author with `PUT /users/{id}/sanity-author`.
- **Cloudinary is for profile photos only:** images up to 5 MB. Store the public ID and URL, never local files. Delete the old asset when a photo is replaced.
- **Background work is minimal:**
  - **Code emails are sent during the request,** with a short timeout and one retry. On failure the user gets a clear error and can resend.
  - **Old Cloudinary photo deletes** run as `BackgroundTasks`. A lost delete only leaves an unused image behind.
  - **Expired codes and sessions** are ignored by their expiry time, and old rows are deleted on every `POST /auth/login` (`delete_stale_rows`): codes after 24 hours (the daily code limit counts them), sessions 7 days after they expire or are revoked. No scheduler is needed.
  - **There's no jobs endpoint or scheduled workflow yet.** Add a scheduled GitHub Actions workflow only when a real scheduled feature arrives (e.g. birthday emails).
  - GET endpoints never trigger side effects.

## Caching

- **Backend: shared posts cache only.**
  - An in-process `cachetools.TTLCache` in `posts/service.py` (size-capped, 10-minute TTL as a safety net), keyed by query and never by user.
  - Auth and role checks run **before** the cache is consulted.
  - On a cache miss, read from Sanity's **live** API, not the API CDN, to avoid stacking two layers of staleness.
  - The whole posts cache is cleared after every successful post write and on the Sanity webhook.
  - This works because the backend runs as a single instance. Moving to multiple instances would need a shared cache.
- **Postgres data is not cached** (users, wishes, sessions, the current user). The queries are cheap at family scale, and stale or cross-user data is the bigger risk.
- **The one other cache:** Google's public signing keys, kept in memory by `core/google.py` for as long as Google's `Cache-Control` allows. They're public keys, not user data.
- **Every response sends `Cache-Control: private, no-store`,** so Vercel's proxy or edge can never cache member data. `NoStoreMiddleware` in `main.py` sets it on every response and overwrites any value a route sets, so never try to send `public` or `s-maxage`.
- **Frontend (Next.js) rule, for reference:** only per-user client caching (TanStack Query, SWR, router cache), plus static or ISR rendering for the public landing page. No shared Next or Vercel caching of anything behind a login.

## Conventions

- `schemas.py` holds Pydantic models and `models.py` holds SQLAlchemy models; never mix them. Always set `response_model` so fields such as `password_hash` never leak.
- Use SQLAlchemy 2.0 typed style (`Mapped[...]`, `mapped_column`) and full type hints. Every `datetime` column is `timestamptz`. Set and compare expiry times with the app's clock (`datetime.now(UTC)`), never the database's `now()`: the clocks can drift apart (Docker Desktop's often does), and `now()` is frozen at the start of the transaction. Primary keys of user-facing tables are UUIDv7 (`default=uuid.uuid7`), `TimestampMixin` adds `created_at`/`updated_at`, and relationships use `lazy="raise"` (load them explicitly with `selectinload`). Enums are stored as `VARCHAR` + a check constraint (`native_enum=False`), not Postgres enum types. New migrations are ruff-formatted by an Alembic hook. Review every autogenerated migration: for an `Enum` column it emits the check constraint twice, so keep only the `ck_<table>_<name>` one and set `create_constraint=False` on the column's `sa.Enum`. Name tables in plural snake_case. Enforce uniqueness with DB constraints (e.g. a unique index on `lower(family_name)`), not check-then-insert.
- **Alembic must see every model:** import each feature's `models.py` in `alembic/env.py`. Generate and review a migration for every model change, ship it in the same PR, and never edit a merged migration. Seed the fixed lookup lists through migrations.
- **Neon:** the app uses the **pooled** connection URL (`DATABASE_URL`), and Alembic uses the **direct** URL (`DATABASE_URL_DIRECT`). Neon scales to zero, so the first query after an idle period is slower.
- **No blocking calls in `async def`.** Use async DB, `httpx` (for Sanity and Google) and `aiosmtplib` (for email), and wrap the sync Cloudinary SDK with `run_in_threadpool`.
- All config goes through `core/config.py` (pydantic-settings). Add every new variable to `.env.example` with a placeholder.
- **Errors:** services raise `AppError` subclasses from `core/exceptions.py` (`BadRequest`, `Unauthorized`, `Forbidden`, `NotFound`, `Conflict`, `TooManyRequests`). A feature exception subclasses one of them and overrides `code` and `default_message`. Never raise `HTTPException` or return 200 with an error body. Every error response is `{"error": {"code", "message"}}`; validation errors add `details` (field + message, never the submitted input).
- The API design is new: use RESTful resource paths under `/api/v1` (e.g. `/api/v1/users/{id}`). Each feature's `router.py` exposes `router = APIRouter(prefix="/<feature>")`, and `main.py` mounts it under `/api/v1`.
- List endpoints take `params: Pagination` and return `Page[T]` from `core/pagination.py` (`page` from 1, `limit` default 20, max 100). A list with filters subclasses `PageParams` and takes `Annotated[ItsParams, Query()]`: FastAPI only spreads a model into query parameters when it's the endpoint's only query input, so never add a separate `Query` parameter next to `Pagination`.
- Use `logging`, never `print`. Never log request bodies, codes, tokens or emails.
- **Tests:**
  - Co-locate them in `<feature>/tests/` and run against real Postgres (never SQLite) with per-test transaction rollback via root `conftest.py` fixtures: `db_session` (services may `commit()`; it only releases a savepoint) and `client` (an `httpx.AsyncClient` whose requests use that session).
  - The test database is `TEST_DATABASE_URL` (the compose `db_test` service, port 5433). Each run drops and re-migrates its schema, so its name must end in `_test`.
  - On Windows, psycopg's async mode needs a selector event loop. `alembic/env.py` and `conftest.py` already handle that.
  - Users: `await make_user(role=Role.ADMIN, password="...", **fields)` creates a user with unique defaults in a fresh `sub_family`. Annotate it as `Callable[..., Awaitable[User]]`.
  - Auth: `headers = await login_as(user)` starts a real session and returns its `Authorization` header. Annotate it as `Callable[[User], Awaitable[dict[str, str]]]`.
  - Auth endpoint tests (`auth/tests/`): `conftest.py` makes `FRONTEND` the only allowed origin, `request_code(user)` and `sign_in(user)` go through the real endpoints, and `helpers.py` has `post_with_cookie` and `refresh_cookie`. The test client uses `http://`, so its cookie jar never sends the `Secure` refresh cookie: pass it explicitly.
  - Email, Sanity, Cloudinary and Google are faked at the `core/` wrapper boundary (`core/fakes.py`). The `client` fixture always installs the fakes; request `fake_email`, `fake_media`, `fake_sanity` or `fake_google` to inspect or script them (e.g. `fake_email.last_code(address)`, `fake_email.failures_left = 2`, `fake_google.token_for(email)`). The real Google checks are tested in `core/tests/test_google.py` with a test RSA key.
  - Services take the wrapper as a parameter, and routers get it with `Depends(get_email_sender)`, `Depends(get_media_store)`, `Depends(get_sanity)` or `Depends(get_google_verifier)`.
  - Every endpoint needs tests for success, auth, role and ownership denial, and its main failure cases.

## Commands

Everything runs through `uv run`. The first `uv sync` installs the full dependency set, so it's slow. Local Postgres runs in Docker (Docker Desktop).

```bash
uv sync                                       # install deps (incl. dev group)
uv run fastapi dev src/app/main.py            # dev server → http://127.0.0.1:8000/docs
uv run pytest                                 # all tests
uv run pytest src/app/<feature>/tests         # one feature
uv run pytest path/to/test_x.py::test_name    # one test
uv run ruff check . --fix && uv run ruff format .
uv run mypy src
uv run alembic revision --autogenerate -m "msg"   # then review the generated file
uv run alembic upgrade head
uv run alembic check                          # fails if models drift from migrations
uv add <pkg> / uv add --dev <pkg>             # commit pyproject.toml + uv.lock together
uv run python scripts/seed_admin.py           # first admin from FIRST_ADMIN_* (docs/infrastructure.md)
```

Locally, `docker compose` runs just the API and Postgres.

## CI (all required before merge)

1. `ruff check .` and `ruff format --check .`
2. `mypy src` (strict)
3. `pytest` against a Postgres service container, including `tests/test_imports.py`, which imports every module under `src/app` so import-time crashes fail CI
4. `alembic upgrade head` on a fresh DB, then `alembic check` (runs before pytest)
5. **Malware Guard**, ported from v1's `.github/workflows/malware-guard.yml`. The GitHub org may still carry the 2026-09 malware. It fails on forbidden persistence files (`.vscode/tasks.json`, `*.bat` push scripts, `branch_structure.json`), VS Code auto-run settings (`runOn`, `folderOpen`, `allowAutomaticTasks`), obfuscated-code markers, and "font" files that are actually scripts.
Malware Guard (`.github/workflows/malware-guard.yml`) and CI (`.github/workflows/CI.yaml`: steps 1–4) exist.

## Deployment

**Changing:** the backend now deploys with **Docker on a VPS** instead of Render's free plan (decision 69). Only that and SMTP email are decided; the team will share the rest (which VPS, the SMTP server, the reverse proxy and HTTPS, and whether Neon and Vercel stay). Don't build around a guess, and update this section when they arrive. Until then, the design below still holds:

- **Hosting:** one Docker container, **one instance** (the posts cache relies on it). The frontend is on **Vercel** and reaches the API through its `/api/*` rewrite.
- **`/health`** must stay cheap and **must not touch the database**, so a monitor can poll it while Neon still scales to zero. On a VPS nothing sleeps, so no keep-awake ping is needed.
- **Startup:** the container start command runs `alembic upgrade head` with the direct URL, then starts uvicorn. That's safe with a single instance.
- **Database:** Neon free.
- **Storage:** don't rely on the container's disk. Data goes to Neon, photos to Cloudinary, posts and their images to Sanity.
- **Settings** come only from env vars; never bake secrets into the image.

## v1 data import

This is a one-off script, `scripts/import_v1.py`, that copies v1 MongoDB profiles into `users`. `pymongo` lives only in its own `import` dependency group: `uv run --group import python scripts/import_v1.py`. It is built **after** the `users` feature.
- **Field mapping:** `name` → display name, `familyName` → family name, `email`, `birthday`, `subFam` → `sub_family_id`, `profilePic` → photo.
- **Sub-families:** every `subFam` value must map to an entry in the fixed `sub_families` list.
- **Roles:** come from v1's `ADMINNAMES`. Imported admins need a password set before they can log in.
- **Imported state:** users start with `email_verified_at = null` and no sessions.
- **Wishes are not imported.** They had no recipient and were purged nightly.
- Make the script idempotent (upsert on family name) and run it against a Neon branch first.

## Workflow rules for Claude

- **New feature:** create the full package as laid out above, import its models in `alembic/env.py`, generate a migration, and add tests in `<feature>/tests/`.
- **Before finishing a task:** run ruff and the relevant tests, and say so if you couldn't.
- **Keep `docs/` in sync.** When a design decision changes, update `docs/decisions.md` and any affected doc in the same commit as the code.
- **Ask first** before adding top-level folders, architectural layers, new external services or anything that breaks the free-tier constraint.
- **Never create** `.vscode/tasks.json`, auto-run settings or `.bat`/`.cmd`/`.ps1` scripts. If you find malware indicators anywhere in the workspace, stop and tell the user.
- **Git:** Claude only commits, on the branch the user currently has checked out.
  - **Never create a branch without the user's approval.**
  - Never commit to `main`, and **never push, open PRs or merge.** The user reviews, opens PRs and does the rest.
  - Branches are named `type/short-description`, and commits follow Conventional Commits (`feat(scope): imperative summary`).
  - PRs are squash-merged with the CONTRIBUTING.md §9 template.
- **Work one PR-sized step at a time,** and wait for the user's go-ahead before starting the next step.

## Open decisions (don't build around a guess)

- **Sub-family and promotion lists:** the user will provide these during the `users` work. Don't invent values.
- **Deployment details:** the VPS, the SMTP server and account, HTTPS and the reverse proxy, and whether Neon and Vercel stay. The team will share them; see "Deployment".
- **Later features:** birthday emails (Slack covers this for now; these will need a scheduled GitHub Actions trigger) and Slack integration.

## v1 → v2 notes

- **Dropped:** `.env` family-name allow-lists, family-name-only login, guest login, single-secret 7-day JWTs, the controller layer, Joi, swagger-jsdoc (FastAPI generates OpenAPI), node-cron, local `uploads/`, emails sent during GET requests, nightly wish purge, and `{message}` error bodies.
- **Renamed:** `Profile` became fields on `users`, and `subFam` became `sub_family_id`.

## Current state (not built yet)

The layout, dependencies, tool config, CI, settings and the DB session are in place, but every feature module is still a placeholder. Still missing:
- **Done:** the login flow (`POST /api/v1/auth/login`, `/verify`, `/google`, `/refresh`, `/logout`; see `docs/auth.md`), listing and revoking sessions (`GET /auth/sessions`, `DELETE /auth/sessions/{id}`), user management and the member directory (`/api/v1/users`: directory, accounts, create, edit, deactivate/reactivate, admin passwords; see `docs/domain.md`), members' own profile (`PATCH /users/me`, photos, confirming a changed email), email over SMTP (`core/email.py` `SmtpEmailSender`), birthday wishes (`/api/v1/wishes`: send, edit, delete, received and sent lists, upcoming birthdays; `wishes/models.py` `Wish`), the blog (`/api/v1/posts`: reads through the shared cache, writes, image uploads, Sanity authors and the signed webhook; see `docs/domain.md`), the first-admin seed (`users/service.py` `seed_first_admin`, `scripts/seed_admin.py`), `core/google.py`, `config.py`, `database/`, Alembic (async `env.py`; first migration: `users`, `sub_families`, `promotions`), `users/models.py` (`Role`, `User`, `SubFamily`, `Promotion`; the lookup lists are still empty), `auth/models.py` (`UserSession`, `EmailCode`) and `auth/dependencies.py` (current user, roles, ownership), the DB test fixtures in `conftest.py`, CI (ruff, mypy, Alembic checks, pytest), `Dockerfile`, `docker-compose.yml`, `core/exceptions.py`, `core/pagination.py`, `core/security.py`, the `core/` wrappers (`email.py` with no provider yet, `media.py`, `sanity.py`) and their fakes, and `main.py` wiring (CORS, no-store, error handlers, empty feature routers under `/api/v1`).
- **Features:** `scripts/import_v1.py`, with the migration that seeds the sub-family and promotion lists (gated).
