# Design decisions

This is a log of the decisions behind v2, made in October 2026. Each entry says what we decided and **why**, so nobody has to guess, and so we know what we'd be undoing if we change our minds. When a decision changes, update its entry (and the other docs) in the same PR.

Unless noted, the source is team discussion. The original change proposal is the "Imena NewsBox" page in Notion.

## Rewrite and stack

**1. Rewrite from scratch; v1 is reference only.**
v1 (Node/Express/MongoDB) was hard to maintain: one 700-line controller, validation bugs, docs that didn't match the code. We kept the mission and the data, not the code, endpoints or response shapes.

**2. Python 3.14 + FastAPI + Pydantic v2, managed with uv.**
FastAPI gives typed request validation and automatic OpenAPI docs (replacing v1's hand-written Swagger comments, which had drifted).

**3. Postgres (Neon) with async SQLAlchemy 2.x and Alembic.**
The Notion doc asked for a real user database. Relational data (users, sessions, wishes) fits Postgres, and Alembic gives reviewable migrations. The project is async throughout, using psycopg 3 as the driver. Note that `sqlalchemy[asyncio]` is required, because SQLAlchemy 2.1 no longer installs `greenlet` by default.

**4. Feature-based folders, no controller layer.**
Grouping by feature keeps related code together and lets teammates work on different features without colliding. The flow is router → service. A repository layer is only added if queries become complex, and only after discussion.

## Hosting and services

**5. Everything on free tiers.**
This is a family project with no budget. That rules out paid plans, background workers, cron services and managed Redis.

**6. Backend on Render (free), frontend on Vercel, database on Neon.**
Render's own free Postgres expires after 30 days, so the database is on Neon, whose free tier doesn't expire. We considered Fly.io, but it no longer has a free plan for new accounts.

**7. UptimeRobot pings `/health` every 5 minutes.**
Render free puts the app to sleep after 15 idle minutes, and the first request after that is slow. `/health` must not touch the database, so Neon can still scale to zero.

**8. No Celery, no Redis.**
v1's only background job was a nightly wish purge, which v2 drops. The remaining work is tiny: login codes are sent during the request, photo cleanup uses FastAPI `BackgroundTasks`, and expired rows are cleaned up during login. Workers would need a paid plan anyway.

**9. Email over an HTTP API: Resend or the Gmail API (open).**
Render free blocks SMTP, so v1's Gmail SMTP approach can't work. Resend needs a verified domain; the Gmail API is free with no domain but needs a one-time OAuth setup. The choice is open. All email goes through `core/email.py`, so switching later is easy.

**10. Sanity for the blog; Cloudinary only for profile photos.**
Sanity handles rich content and images well. Cloudinary was already used in v1 for profile photos.

## Users and access

**11. Admins register users; there's no self-signup.**
The Notion doc moved user management to admins. Users now live in a real table instead of v1's lists of names in an `.env` file.

**12. Three cumulative roles: member ⊂ social media manager ⊂ admin.**
The Notion doc introduced the social media manager role for the family committee, who manage the blog. Guests are not a role: visitors only see the public landing page, and the API has no public endpoints.

**13. Ownership-based access control.**
Users can change what they own (their profile basics, their wishes); admins can change anything. Identity fields (family name, email, role, sub-family, promotion) are admin-only.

**14. Family name is unique and is the login handle; email is for codes.**
That's how the family already identifies people. Uniqueness is case-insensitive and enforced by the database.

**15. Profile fields live on `users`; sub-families and promotions are fixed lists.**
One table is simpler than v1's separate Profile collection. Fixed lists (lookup tables) prevent typos and inconsistent spellings.

**16. Soft delete only.**
Deactivating keeps history (wishes) and reserves the family name, while immediately signing the user out everywhere.

**17. Members see each other's birthday as day and month only.**
Email, birth year, role and sessions are visible only to the user and admins. Separate response schemas make it impossible to leak a field by accident.

## Authentication

**18. Per-device login with emailed codes; admins also use a password.**
In v1, knowing a family name was enough to log in as someone. The Notion doc proposed email verification and admin passwords. We first considered verifying an email once and then trusting the family name forever, but that would have reopened the same hole. So each **device** proves itself once with a code (plus a password for admins) and then stays logged in.

**19. Short access token (15 min) + rotating refresh token (90 days).**
Short access tokens limit the damage if one leaks. Refresh tokens rotate on every use and are stored only as hashes, so sessions can be listed and revoked, and a reused (stolen) refresh token revokes its session.

**20. Refresh token in an HttpOnly cookie; access token in a header.**
JavaScript can't read an HttpOnly cookie, which protects the long-lived token. The short-lived access token is kept in memory by the frontend.

**21. The frontend proxies `/api/*` to the backend.**
Vercel and Render are different sites to the browser, and Safari blocks cross-site cookies. Proxying makes the API same-site, so the refresh cookie works everywhere with `SameSite=Lax`.

**22. Every request loads the user from the database.**
That way role changes and deactivation apply immediately, instead of after the access token expires. It costs one primary-key query, and it's never cached.

## Data and content

**23. Wishes have a recipient, one per sender per recipient per year, and are kept as history.**
v1 wishes had no recipient and were deleted every night. The 30-day window from v1 stays: you can wish someone whose birthday is today or within 30 days.

**24. Birthday emails are postponed.**
The family's Slack already covers birthday reminders, so emails aren't urgent. That also gives us time to settle the email provider.

**25. Blog reads go through our API, never straight to Sanity.**
Reading from Sanity's CDN in the browser would need a *public* dataset. Anyone could then download every post and image, deactivated members included, and strangers could use up our free quota. Going through the API keeps the blog members-only.

**26. Social media managers work through our frontend, not Sanity Studio.**
This keeps role checks in one place. The Sanity webhook is only a backup for clearing the cache.

**27. Cache blog posts in memory on the backend; cache nothing else.**
Posts are identical for everyone and rarely change, so one shared cache saves Sanity calls. It works because there's a single Render instance. Database data isn't cached, because stale or mixed-up user data is a bigger risk than a few milliseconds.

**28. Import v1's MongoDB profiles; don't import wishes.**
Members keep their profiles. v1 wishes had no recipient, so they can't fit the new model.

## Repository and process

**29. Keep the Malware Guard workflow from v1.**
The GitHub org was hit by malware in September 2026 and may still be affected, so the scan runs on every push and PR.

**30. Dependencies cleaned up.**
- **Wrong package:** the `sanity` package on PyPI turned out to be an unrelated 2010 Flask app, not Sanity CMS (which has no official Python SDK). We call Sanity's HTTP API directly with `httpx`.
- **Removed:** `typing` (an obsolete backport), `celery`, `redis`, `fastapi-cache2`, `asyncpg` and `passlib`.

**31. Services commit explicitly; `get_session` never auto-commits.**
Committing in the dependency's teardown runs after FastAPI has sent the response, so a failed commit could still return 200. `get_session` only yields the session, and anything left uncommitted is rolled back when it closes.

**32. Database setup lives in a `core/database/` package, split by concern.**
`base.py` holds the shared `Base`, `engine.py` the engine and session factory, and `session.py` the `get_session` dependency. Models import only `base.py`, so loading them (in Alembic or tests) doesn't create an engine as a side effect. The package's `__init__.py` re-exports nothing for the same reason.

**33. CI runs strict mypy, a wider ruff rule set and an import check on every PR.**
Two early auth PRs passed CI while crashing at runtime: `super.__init__` without parentheses, a column default of `func.now` instead of `func.now()`, and `SecretStr` secrets passed straight to PyJWT. Ruff's original rules (`E, W, F, I, UP, B, ASYNC, SIM`) don't check calls or types, mypy wasn't in CI, and no test imported the broken modules. So mypy (strict) is now a required CI step, ruff also selects `PLE`, `PLW` and `RUF`, and `tests/test_imports.py` imports every module under `src/app`. This was previously planned for after launch; it moved forward because it would have caught most of those bugs.

**34. Tests reset a dedicated `_test` database and roll back every test.**
Once per run, the fixtures drop and re-create the test database's `public` schema and migrate it to head, so the tests always exercise the real migrations. Each test then runs inside one outer transaction that is rolled back, and the session joins it with savepoints (`join_transaction_mode="create_savepoint"`), so services can `commit()` as they would in production. Because the schema is wiped, the fixtures refuse any `TEST_DATABASE_URL` whose database name doesn't end in `_test`.

**35. A naming convention on `Base.metadata`.**
Indexes and constraints get explicit names (`ix_…`, `uq_…`, `fk_…`, `pk_…`, `ck_…`) instead of names Postgres generates. Migrations are then the same on every machine, and later migrations can drop or rename a constraint by a name they know.

**36. A selector event loop on Windows.**
psycopg's async mode can't use Windows' default asyncio event loop. Most of the team develops on Windows, so `alembic/env.py` and the pytest loop use `asyncio.SelectorEventLoop` there. Linux (CI and Render) is unaffected.

**37. One error body for every error response.**
Every error is `{"error": {"code", "message"}}`, including unknown routes, wrong methods and unexpected 500s, so the frontend handles errors in one place and branches on a stable `code`. Services raise `AppError` subclasses and never `HTTPException`, which keeps HTTP details out of business logic. Validation errors add a `details` list of fields and messages, without FastAPI's default echo of the submitted input, because that input can contain login codes or passwords.

**38. `Cache-Control: private, no-store` on every response, not just authenticated ones.**
Almost every route is authenticated, and checking which ones are would mean a route could be missed. A middleware sets the header on every response and overwrites anything a route sets. Caching `/health` or an error page has no value, so nothing is lost.

**39. No OpenAPI schema or `/docs` in production.**
There is no public API, so production doesn't publish a map of it. Development and test environments (and a staging deploy with `ENVIRONMENT=development`) still serve `/docs`.

