# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Backend API for Imena NewsBox, a private family platform for member profiles, birthday wishes and a blog. v2 is a from-scratch rewrite of v1 (`../backend`, Node/Express/MongoDB) with the same mission. **v1 is reference only:** don't port its code, endpoints or response shapes. Its data will be imported (see "v1 data import"). The v2 changes are documented in Notion ("Imena NewsBox" page).

**Hard constraint: everything must run on free tiers.** That means a Render free web service, Neon free Postgres, and GitHub Actions. Don't introduce anything that needs a paid plan or an always-on extra process (workers, cron services, Redis) without asking.

## Stack

- Python 3.14, **uv** (`pyproject.toml` + `uv.lock`), FastAPI, Pydantic v2, pydantic-settings
- **Neon** Postgres via **async** SQLAlchemy 2.x and Alembic (async `env.py`). The dependency is `sqlalchemy[asyncio]`, because 2.1 no longer installs `greenlet` by default. Use `AsyncSession`, `create_async_engine` and `postgresql+psycopg://` (psycopg 3).
- Auth: PyJWT access + refresh tokens (separate secrets) and pwdlib[argon2] for admin passwords
- Background work: FastAPI `BackgroundTasks` only, for fire-and-forget cleanup. **No Celery, no Redis, no scheduler.**
- Caching: `cachetools`, in-process (see "Caching")
- External services:
  - **Sanity CMS:** everything blog-related, including its images. Called through its HTTP API with `httpx`; there's no official Python SDK. The PyPI package named `sanity` is unrelated, so never add it.
  - **Cloudinary:** profile photos only, using the official `cloudinary` SDK.
  - **Email:** Resend or the Gmail API, not chosen yet (see "Open decisions"). Either way it goes over HTTPS with `httpx`, behind `core/email.py`. No SDK, and never SMTP.
- pytest + pytest-asyncio + httpx; ruff; mypy
- Docker, deployed as a single **Render free** web service

## Architecture

```
src/app/
├── main.py              # app factory, middleware (CORS allowlist), router registration
├── core/                # cross-cutting only: config.py, database/ (base.py: Base, engine.py: engine +
│                        # SessionLocal, session.py: get_session),
│                        # security.py, exceptions.py, and thin wrappers for external services:
│                        # email.py, media.py (Cloudinary), sanity.py
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
- **Roles are cumulative:** `member` ⊂ `social_media_manager` ⊂ `admin`. Social media managers are members who can also create, update and delete blog posts. Admins can do everything. Check roles through a single dependency in `auth/dependencies.py` (e.g. `require_role(Role.SOCIAL_MEDIA_MANAGER)` admits that role and above). Never compare role strings inline.
- **Ownership-based access:** users may modify only resources they own, such as their own profile fields and their own wishes. Admins may modify anything. Enforce ownership in one reusable check (owner or admin, else 403), never ad hoc.
  - **Members can edit:** their photo, display name and birthday.
  - **Only admins can change:** family name, email, role, sub-family, promotion and active status.
- **Users are created only by admins.** There's no self-signup. The first admin is created by a seed command, never through a public endpoint.
- **Users table:** profile fields live on `users`, with no separate profile table. It holds at least:
  - family name (**unique**, case-insensitive, used to log in)
  - email and `email_verified_at` (nullable)
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
  - **Admin:** family name + password, **then** an emailed code.
  - **Member / social media manager:** family name, then an emailed code.
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
  - **Every authenticated request loads the user from the DB** with one primary-key query, and rejects deactivated users. Role comes from the DB row, not the token. **Never cache this lookup**, so deactivation and role changes take effect immediately.
- **Email verification:**
  - Codes are 6 digits, expire after 10 minutes, are single-use and stored hashed, and allow 5 attempts with a 60-second resend cooldown.
  - Throttling is stored in Postgres, not Redis.
  - The first successful code sets `email_verified_at`. When an admin changes a user's email, `email_verified_at` is cleared and a code is sent to the new address. The user confirms it with an authenticated endpoint.
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
  - The post schema already exists in Sanity. Get its fields from the user before building `posts`, and don't invent them.
- **Cloudinary is for profile photos only:** images up to 5 MB. Store the public ID and URL, never local files. Delete the old asset when a photo is replaced.
- **Background work is minimal:**
  - **Code emails are sent during the request,** with a short timeout and one retry. On failure the user gets a clear error and can resend.
  - **Old Cloudinary photo deletes** run as `BackgroundTasks`. A lost delete only leaves an unused image behind.
  - **Expired codes and sessions** are ignored by their expiry time, and old rows are deleted as part of the login flow. No scheduler is needed.
  - **There's no jobs endpoint or scheduled workflow yet.** Add a scheduled GitHub Actions workflow only when a real scheduled feature arrives (e.g. birthday emails).
  - GET endpoints never trigger side effects.

## Caching

- **Backend: shared posts cache only.**
  - An in-process `cachetools.TTLCache` in `posts/service.py` (size-capped, 10-minute TTL as a safety net), keyed by query and never by user.
  - Auth and role checks run **before** the cache is consulted.
  - On a cache miss, read from Sanity's **live** API, not the API CDN, to avoid stacking two layers of staleness.
  - The whole posts cache is cleared after every successful post write and on the Sanity webhook.
  - This works because the free plan runs one instance. Moving to multiple instances would need a shared cache.
- **Postgres data is not cached** (users, wishes, sessions, the current user). The queries are cheap at family scale, and stale or cross-user data is the bigger risk.
- **Every authenticated response sends `Cache-Control: private, no-store`,** so Vercel's proxy or edge can never cache member data. Never send `public` or `s-maxage` on authenticated routes.
- **Frontend (Next.js) rule, for reference:** only per-user client caching (TanStack Query, SWR, router cache), plus static or ISR rendering for the public landing page. No shared Next or Vercel caching of anything behind a login.

## Conventions

- `schemas.py` holds Pydantic models and `models.py` holds SQLAlchemy models; never mix them. Always set `response_model` so fields such as `password_hash` never leak.
- Use SQLAlchemy 2.0 typed style (`Mapped[...]`, `mapped_column`) and full type hints. Name tables in plural snake_case. Enforce uniqueness with DB constraints (e.g. a unique index on `lower(family_name)`), not check-then-insert.
- **Alembic must see every model:** import each feature's `models.py` in `alembic/env.py`. Generate and review a migration for every model change, ship it in the same PR, and never edit a merged migration. Seed the fixed lookup lists through migrations.
- **Neon:** the app uses the **pooled** connection URL (`DATABASE_URL`), and Alembic uses the **direct** URL (`DATABASE_URL_DIRECT`). Neon scales to zero, so the first query after an idle period is slower.
- **No blocking calls in `async def`.** Use async DB and `httpx` (for Sanity and the email provider), and wrap the sync Cloudinary SDK with `run_in_threadpool`.
- All config goes through `core/config.py` (pydantic-settings). Add every new variable to `.env.example` with a placeholder.
- Raise feature exceptions and map them to HTTP responses in `core/exceptions.py`; don't return 200 with an error body. The API design is new: use RESTful resource paths under `/api/v1` (e.g. `/api/v1/users/{id}`).
- List endpoints paginate consistently (`page`, `limit`, with a capped limit).
- Use `logging`, never `print`. Never log request bodies, codes, tokens or emails.
- **Tests:**
  - Co-locate them in `<feature>/tests/` and run against real Postgres (never SQLite) with per-test transaction rollback via root `conftest.py` fixtures: `db_session` (services may `commit()`; it only releases a savepoint) and `client` (an `httpx.AsyncClient` whose requests use that session).
  - The test database is `TEST_DATABASE_URL` (the compose `db_test` service, port 5433). Each run drops and re-migrates its schema, so its name must end in `_test`.
  - On Windows, psycopg's async mode needs a selector event loop. `alembic/env.py` and `conftest.py` already handle that.
  - Mock email, Sanity and Cloudinary at the `core/` wrapper boundary.
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

- **Hosting:** one Docker image on one **Render free** web service. The frontend is on **Vercel** and reaches the API through its `/api/*` rewrite.
- **Keep-awake:** an **UptimeRobot** monitor hits `/health` every 5 minutes so the instance never sleeps. `/health` must stay cheap and **must not touch the database**, so Neon can still scale to zero.
- **Startup:** the container start command runs `alembic upgrade head` with the direct URL, then starts uvicorn. That's safe because the free plan runs a single instance and has no pre-deploy step.
- **Database:** Neon free.
- **Free-plan limits to respect:**
  - No outbound SMTP (ports 25/465/587 are blocked), which is why email goes over an HTTPS API (Resend or Gmail API).
  - The disk is wiped on restart.
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

- **Email provider:** Resend (needs a verified sending domain) or the Gmail API with a family Gmail account (free, no domain; the OAuth app must be set to "In production"). Credentials will arrive as env vars; `.env.example` has placeholders for both. Implement only the chosen one, behind `core/email.py`, and fake it in tests until then.
- **Sub-family and promotion lists:** the user will provide these during the `users` work. Don't invent values.
- **Sanity post schema fields:** ask the user before building `posts`.
- **Later features:** birthday emails (Slack covers this for now; these will need a scheduled GitHub Actions trigger) and Slack integration.

## v1 → v2 notes

- **Dropped:** `.env` family-name allow-lists, family-name-only login, guest login, single-secret 7-day JWTs, the controller layer, Joi, swagger-jsdoc (FastAPI generates OpenAPI), node-cron, local `uploads/`, emails sent during GET requests, nightly wish purge, and `{message}` error bodies.
- **Renamed:** `Profile` became fields on `users`, and `subFam` became `sub_family_id`.

## Current state (not built yet)

The layout, dependencies, tool config, CI, settings and the DB session are in place, but every feature module is still a placeholder. Still missing:
- **Done:** `config.py`, `database/`, Alembic (async `env.py`, no migrations yet), the DB test fixtures in `conftest.py`, CI (ruff, mypy, Alembic checks, pytest), `Dockerfile` and `docker-compose.yml`.
- **Code:** `core/exceptions.py`, `core/security.py`, and the `core/` service wrappers (`email.py`, `media.py`, `sanity.py`).
- **Features:** all four (auth, users, wishes, posts), plus `scripts/import_v1.py` and the admin seed.
