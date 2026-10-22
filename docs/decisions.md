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

**40. Refresh tokens are random strings, not JWTs.**
Every refresh already needs a database lookup (to rotate the token and detect reuse), so a self-contained JWT adds nothing. A random 256-bit string is simpler and can't be decoded. Only its HMAC, keyed by `REFRESH_TOKEN_SECRET`, is stored, so a leaked database or backup can't be used to recognise or forge tokens.

**41. Email codes are stored as an HMAC with their own `CODE_SECRET`.**
A 6-digit code has only a million possible values, so a plain SHA-256 could be reversed instantly from a leaked database while the code is still valid. A keyed hash can't be reversed without the secret. It's a separate secret so that rotating it only invalidates codes in flight, without logging every device out.

**42. Access tokens carry a session ID, and expired tokens get their own error code.**
Access tokens hold the user ID and the ID of the session that issued them (so "this device" can be marked in the sessions list), never the role. An expired token returns `token_expired` and any other bad token `invalid_token`, so the frontend knows when to refresh and when to log out. Password hashing runs in a worker thread because Argon2 is deliberately slow, and verifying against a missing hash still spends the same time, so response times don't reveal which family names exist.

**43. Code emails: 5-second timeout, one retry, and a clear error without a provider.**
Each send attempt gets 5 seconds, and a failed or timed-out attempt is retried once, so the worst case is about 10 seconds before the user sees `email_not_sent` (503) and can resend. A provider only implements `send(message)`; building the message and the retry are shared. Until a provider is chosen, sending fails with `email_not_configured` (503) rather than silently doing nothing, and codes are never written to logs as a stand-in.

**44. Profile photos are checked by content and stored small.**
The format comes from the file's first bytes, not its name or content type: JPEG, PNG, WebP or HEIC, up to 5 MB. Cloudinary stores each photo as a JPEG of at most 1024px a side (HEIC doesn't display in most browsers, and small files stay well inside the free plan), under `imena-newsbox/<environment>/profile-photos`, so development uploads never mix with production. Credentials are passed on each SDK call instead of through its global config.

**45. External service wrappers are injected dependencies, with fakes in `core/fakes.py`.**
Routers get the email sender, media store and Sanity client through `Depends`, and the test `client` fixture replaces all three with in-memory fakes, so no test can reach a real service. The fakes live in `src/app/core/fakes.py` so feature tests can import their types under strict mypy. The real Sanity client opens a short-lived `httpx` client per call: posts are cached, so calls are rare, and there's no connection pool to manage.

**46. User IDs are UUIDv7, and roles are stored as text with a check constraint.**
UUIDs don't reveal how many members there are or let anyone guess other IDs, and version 7 is time-ordered, so new rows land at the end of the primary key index (Python 3.14 generates them; Postgres 17 has no built-in v7). Roles are a `VARCHAR` column with a `CHECK` constraint instead of a Postgres enum type, because adding or renaming a value is then an ordinary migration. Code compares roles only with `Role.at_least()`: a `StrEnum` compares alphabetically, so `"admin" < "member"`.

**47. Family names and emails are both unique, ignoring case.**
Unique indexes on `lower(family_name)` and `lower(email)` stop "Mugisha" and "mugisha" (or two spellings of one address) from both existing, including when one belongs to a deactivated user, and lookups use the same indexes. Unique emails mean a login or confirmation code always reaches exactly one account, so every member needs their own address. The sub-family is required and the promotion optional; both lookup tables stay empty until the lists are agreed, and tests create their own rows.

**48. Relationships never lazy-load.**
In async SQLAlchemy a lazy load inside a request fails with a confusing error. Every relationship uses `lazy="raise"`, so a missing `selectinload` fails loudly in tests instead.

**49. Each request checks the session as well as the user, in the same single query.**
The access token carries the session ID (`sid`), and the current-user lookup joins `users` to `user_sessions`. A request fails if the user is deactivated or the session is revoked or expired. Signing out a device therefore takes effect on its next request, not up to 15 minutes later when its access token would expire, and it still costs one indexed query. All three cases return the same `session_ended` error, so a caller can't tell a deactivated account from a signed-out device.

**50. Sessions keep the previous refresh token's hash.**
On rotation the old hash moves to `previous_token_hash` (with `rotated_at`), so a reused token can be told apart from a random one. The login flow uses it to give two tabs refreshing at once a short grace window, and to revoke the session when an old token turns up later.


**51. Code limits live in Postgres: one code a minute, ten a day, only the newest works.**
There's no Redis, so the limits are counted from `email_codes` rows. Five attempts per code alone would let someone guess 300 times an hour by asking for a new code every minute; capping codes at ten per user per 24 hours limits that to 50 guesses a day (out of a million) and protects inboxes and the email quota. The cost is that someone spamming a member's family name can block that member's code logins for up to a day; the flood of emails also tells the member something is wrong. Issuing locks the user's row and commits before sending, so concurrent requests can't slip past the limits and no lock is held during the email call. A code that couldn't be sent is deleted, so the user can retry at once.

**52. The login endpoint answers the same way for unknown, deactivated and real accounts.**
`POST /auth/login` returns the same 200 whether or not the family name exists, and an admin who leaves out the password gets that 200 too, with no email. A sent password is checked with the same Argon2 cost even when there's no account, and every wrong password, whoever the account belongs to, returns `invalid_credentials`. So the endpoint can't be used to list members or find the admins. The rate-limit and email-failure errors only happen for real accounts; we accept that, because a member needs to know why no code arrived, and family names aren't secret in the family anyway.

**53. A 30-second grace window for the replaced refresh token, answered with 409.**
Two tabs on one device often refresh at the same moment with the same cookie. The first wins and rotates the token; without a grace window the second would look like a stolen token and log the device out. Within 30 seconds of a rotation the replaced token gets 409 `refresh_conflict` and no tokens, so the browser (which by then holds the new cookie) simply retries. The grace answer never hands out tokens, so a thief replaying the old token inside the window gains nothing; after the window, reuse revokes the session. Refreshes of one session are serialised with a row lock.

**54. Old codes and sessions are deleted by login requests, not by a scheduler.**
The free plan has no worker or cron, and the tables stay tiny at family scale, so every `POST /auth/login` runs two small deletes: codes older than 24 hours (younger ones are still needed for the daily code limit) and sessions that ended more than 7 days ago (kept briefly so a recent sign-out or a reuse revocation can still be looked into). Expired rows are already ignored by their expiry time, so cleanup is only about size, and missing a run is harmless.

**55. Google ID tokens are checked with PyJWT, without a Google SDK.**
Checking a Google ID token is a JWT check (RS256 signature against Google's published keys, plus `iss`, `aud`, `exp` and `email_verified`), and PyJWT already signs our access tokens, so its `crypto` extra is the only new dependency. Google's `google-auth` library would add a synchronous HTTP stack to an async app. The keys are cached in memory for as long as Google's `Cache-Control` says, which is the only cache besides posts; that's safe because they're public keys, not user data. A token naming an unknown key triggers a refetch (Google rotates keys), at most once a minute so junk tokens can't make us hammer Google. `GOOGLE_CLIENT_ID` is public (the frontend has it too), so it isn't a secret; while it's empty, Google sign-in answers 503.

**56. Google sign-in replaces the emailed code, works before verification and never creates accounts.**
A code proves the person controls the email address; a Google ID token with `email_verified` proves the same, so `POST /auth/google` is an alternative to the code for members, and an alternative second step for admins (who still send their password). Admins register every user, so a Google login only ever finds an existing, active user by `lower(email)`, and the first one marks the email verified exactly as a first code would. Matching by email instead of storing Google's account ID means an admin can fix a mistyped email and the next Google login just works. Its errors name the cause (`google_account_not_registered`, `admin_password_required`), unlike `/login`, because only the owner of the Google account can see them.

**57. The first-admin seed refuses rather than guesses.**
The seed is the only way to create a user without an admin, so it's deliberately strict. Running it again with the same settings does nothing (it never resets the password). If the family name belongs to anyone other than that active admin with that email, or the email belongs to another user, it stops instead of promoting or overwriting someone. Every user needs a sub-family, but the list is only seeded by a migration once its values are agreed, so `FIRST_ADMIN_SUB_FAMILY` must name an existing one: the seed never invents a sub-family. The password must be at least 12 characters.

**58. Uniqueness errors come from the database, inside a savepoint.**
Creating or changing a user applies the change inside a savepoint and maps the unique-index violations (`uq_users_family_name_lower`, `uq_users_email_lower`) to 409 `family_name_taken` / `email_taken`. Checking first and then saving would let two admins saving at once both pass the check. The savepoint means a duplicate only undoes that change, not the rest of the request's transaction. Sub-families and promotions are checked up front instead, because "doesn't exist" deserves its own clear error and the lists never shrink.

**59. Admins can't change their own role.**
Otherwise the only admin could demote themselves (or be tricked into it) and nobody could manage users or reach the seed again without the database. Another admin has to do it. Demotion also clears the password hash, since passwords are only for admins.

**60. A failed confirmation email doesn't undo an admin's email change.**
When an admin changes a member's email, the change is committed first and the confirmation code is sent afterwards. If the provider is down (or the limits stop the code), the change stays saved and the reason is logged; the member can ask for a new code with `POST /users/me/email/resend`, or simply log in with a code sent to the new address, which also confirms it. Failing the admin's request instead would leave them unsure whether anything had changed.

**61. Birthday dates use UTC, and 29 February falls on 28 February in other years.**
The 30-day wish window and upcoming birthdays need a "today". The server's UTC date is the simplest single answer, and v1 used UTC too. For a family around UTC+2 the window opens and closes up to two hours late, which doesn't matter over 30 days. A leap-day birthday needs a date in the other three years; 28 February keeps it in its own month. Each wish stores the year of the birthday it's for, computed by the server rather than sent by the client, so a late-December wish for a January birthday counts as next year's.

**62. A wish is seen by its sender, its recipient and admins.**
Members list the wishes they received and the ones they sent; admins can list anyone's, because they need to find a wish to delete it. There's no shared "wall" of everyone's wishes: nothing asked for one, and it's easy to add later, while making private messages public can't be undone. Only the sender can edit a wish, not even an admin, so a message never says something its sender didn't write; admins can still delete it.

**63. Upcoming birthdays are worked out in Python, not SQL.**
The endpoint reads every active member's birthday (two columns) and picks the next 30 days in Python, with the same `next_birthday` function the wish window uses. The year wrap and 29 February are hard to read in SQL, and having one function for both rules means the list and the window can't disagree. A family has hundreds of members at most, so it's one small query. If that ever grows, the filter can move into SQL behind the same function's tests.

**64. Members are linked to Sanity authors by a stored ID.**
Posts point to Sanity `author` documents, but people log in to our app as users, and a post written through the app must be credited to its writer. `users.sanity_author_id` (unique) holds the link. A writer who isn't linked gets a new author on their first post. People who already wrote posts in the Studio are linked by an admin once, so they don't end up with two authors of the same name and their posts split in two. We don't match by name, because names change and can repeat, and we don't drop Sanity authors, because the existing posts point to them. The link is checked against Sanity before it's saved, so it can't point to an author that doesn't exist. Only social media managers and admins can have one, enforced by a check constraint; demoting someone to a plain member unlinks it (their posts keep their author), just as demoting an admin removes their password.

**65. Posts are read by slug, and the list leaves out posts without one.**
The frontend's post pages use the slug in their URL, so `GET /posts/{slug}` looks posts up by it (writes will use the stable Sanity ID instead, since a slug can change). A post with no slug could never be opened, so the list skips it; every other missing field just reads as `null`. Sanity's `categories` field holds a single string, so our API calls it `category`.
