# Infrastructure

> **Being updated:** the backend now deploys with **Docker on a VPS** instead of Render (decision 69), and email is plain SMTP (decision 68). The rest of this page still describes the Render setup until the team settles the VPS details.

## The free-tier rule

**Everything runs on free tiers.** Before adding a service, a process or a paid feature, ask the team. This rule is why there's no Celery, no Redis and no background workers.

## Where things run

```mermaid
flowchart LR
    U[Members' browsers] --> V["Vercel (free)<br/>Next.js frontend<br/>/api/* rewrite"]
    V --> R["Render (free)<br/>one Docker container<br/>FastAPI"]
    UR[UptimeRobot] -->|"GET /health<br/>every 5 min"| R
    R --> N[("Neon (free)<br/>Postgres")]
    R --> S["Sanity<br/>blog content"]
    R --> C["Cloudinary<br/>profile photos"]
    R --> M["SMTP server<br/>(email)"]
    S -->|webhook| R
```

| Piece | Service | Notes |
|---|---|---|
| Frontend | Vercel | Proxies `/api/*` to the backend (see [Authentication](auth.md#why-the-vercel-proxy)) |
| Backend | Render free web service | One Docker container, one instance |
| Database | Neon free | Scales to zero when idle |
| Keep-awake | UptimeRobot | Pings `/health` every 5 minutes so Render doesn't put the app to sleep |
| Blog | Sanity | Private dataset; only our backend talks to it |
| Photos | Cloudinary | Profile photos only |
| Email | SMTP server | Plain SMTP with STARTTLS or TLS, sent with `aiosmtplib`; server and account to be chosen by the team |

## Free-tier limits we design around

- **Render's disk is wiped on every restart.** Nothing is stored locally: data goes to Neon, photos to Cloudinary, posts to Sanity.
- **Render free has no pre-deploy step and runs a single instance.** The container's start command runs `alembic upgrade head` and then starts the server. That's safe with one instance.
- **Render free sleeps after 15 minutes without traffic.** UptimeRobot prevents this. **`/health` must never touch the database**, otherwise those pings would keep Neon awake too and burn through its free compute.
- **Neon scales to zero.** The first database query after a quiet period is a bit slower; that's expected.
- **Neon connection URLs:** the app uses the **pooled** URL (`DATABASE_URL`) and Alembic uses the **direct** URL (`DATABASE_URL_DIRECT`).

## Background work

There are no workers. The little background work we have is handled like this:

| Task | How |
|---|---|
| Sending login codes | During the request, with a short timeout and one retry |
| Deleting a replaced profile photo from Cloudinary | FastAPI `BackgroundTasks` (if it's ever lost, an unused image is left behind; that's harmless) |
| Cleaning up expired codes and sessions | Expired rows are ignored by their expiry time, and old rows are deleted during login. No scheduler needed. |

When a real scheduled feature arrives (e.g. birthday emails), a **scheduled GitHub Actions workflow** will call a protected endpoint. That's free, and it also wakes the app.

**GET requests never cause side effects** (no emails, no writes).

## Caching

Only **blog posts** are cached, and only on the **backend**.

- **What and where:** an in-memory cache (`cachetools.TTLCache`) in `posts/service.py`, holding at most 256 results (one per page, category or post). "Not found" and errors aren't cached, so made-up slugs can't push real posts out.
- **The key is the query, never the user:** every member sees the same posts. Login and role checks always happen **before** the cache is consulted.
- **Cache misses read Sanity's live API,** not its CDN, so we don't stack two layers of staleness.
- **When the cache is cleared:**
  - after every successful post create, update or delete through our API
  - when Sanity sends a webhook (a backup, e.g. if someone edits in Sanity Studio)
  - automatically after **10 minutes** at the latest, as a safety net
- **Why in-memory works:** Render free runs exactly one instance, so there's one cache. With multiple instances, the cache would have to move to a shared store.

**Not cached:**
- **Postgres data** (users, wishes, sessions): queries are cheap at family scale, and stale or mixed-up user data would be worse than a few milliseconds.
- **The current-user lookup:** never cached, so deactivation and role changes apply instantly.

**HTTP caching:**
- Every response sends `Cache-Control: private, no-store`, so Vercel's proxy can never cache member data and serve it to someone else. A middleware sets it on every response, including `/health` and errors, so no route can forget it.
- In production the OpenAPI schema and the `/docs` pages are turned off, since there is no public API.
- Images load straight from Sanity's and Cloudinary's CDNs, which handle their own caching.

**Frontend (Next.js), for reference:**
- Per-user caching in the browser is fine (TanStack Query, SWR, the router cache). Clear it on logout.
- Static generation or ISR is fine for the public landing page.
- **Never** use Next.js or Vercel server-side caching for anything behind a login; it's shared between all visitors.

## CI

Every push and PR to `main` runs these checks, and all of them must pass before merging:

1. `ruff check .` and `ruff format --check .`
2. `mypy src` in strict mode (catches wrong argument types and bad calls that ruff can't see)
3. `pytest` against a Postgres service container. `tests/test_imports.py` imports every module, so a module that crashes on import fails CI even before it has tests.
4. `alembic upgrade head` on a fresh database, then `alembic check` (catches model changes without a migration)
5. **Malware Guard:** the GitHub org was hit by malware in September 2026. This workflow fails if it finds the malware's persistence files (`.vscode/tasks.json`, `.bat` push scripts, `branch_structure.json`), any committed `.bat`/`.cmd`/`.ps1`/`.vbs` file, VS Code auto-run settings, obfuscated JavaScript, or "font" files that are really scripts.
Malware Guard (`malware-guard.yml`) and the CI workflow (`CI.yaml`: ruff, mypy, the Alembic checks and pytest with a Postgres service) are in place. The Alembic checks run before the tests.

> **Never commit** `.vscode/tasks.json`, VS Code auto-run settings or Windows script files. If you see any of these in the repo or on your machine, tell the team immediately.

## Local development

- **Database:** Postgres runs in Docker (`docker compose`, coming in an upcoming PR). Tests use it too.
- **Settings:** copy `.env.example` to `.env` and fill it in. Never commit `.env`.
- **Commands:** see [CONTRIBUTING.md](../CONTRIBUTING.md) and the commands section in [CLAUDE.md](../CLAUDE.md#commands).

## Seeding the first admin

Every user is created by an admin, so the very first admin comes from a command instead: `scripts/seed_admin.py`. Render's free plan has no shell, so you run it **from your own machine** against the target database.

1. **Sub-families first.** Every user needs one, and they're added by a migration (HOPE, LIGHT and WIHOGORA). The seed only works once that migration has run on the target database (the container runs `alembic upgrade head` on every start).
2. **Settings.** In your local `.env` (never committed), or as environment variables for one run:
   - `DATABASE_URL`: the target database. For Neon, the **direct** (unpooled) URL is the simplest for a one-off script.
   - `FIRST_ADMIN_FAMILY_NAME`, `FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD` (at least 12 characters) and `FIRST_ADMIN_SUB_FAMILY` (the **exact name** of an existing sub-family: `HOPE`, `LIGHT` or `WIHOGORA`).
3. **Run it:** `uv run python scripts/seed_admin.py`. It prints "Created the admin …" or "… already exists; nothing changed", and exits with 1 and a reason if it refuses.
4. **Log in:** the admin logs in with family name + password, then the emailed code (or Google). Remove `FIRST_ADMIN_PASSWORD` from your `.env` afterwards; the database only keeps its hash.

Running it again is safe: it never resets the password or changes another user. Try it on a Neon branch first, like the v1 import.

## Setting up Sanity

The backend needs four values from Sanity, set as environment variables on Render (never in the repo):

| Variable | Where to find it |
|---|---|
| `SANITY_PROJECT_ID`, `SANITY_DATASET` | sanity.io/manage → the project (its ID is at the top) → **Datasets** (usually `production`). Not secret |
| `SANITY_API_READ_TOKEN` | **API → Tokens → Add API token**, permission **Viewer**. Used for member reads |
| `SANITY_API_WRITE_TOKEN` | the same page, permission **Editor**. Used for post writes and image uploads |
| `SANITY_WEBHOOK_SECRET` | a long random string you choose, entered in the webhook below |

**The webhook** clears the posts cache when someone changes a post in the Studio, so members see it at once instead of within 10 minutes. In sanity.io/manage → **API → Webhooks → Create webhook**:
- **URL:** `https://<the Render service>/api/v1/posts/webhook`
- **Dataset:** the one above. **Trigger on:** create, update and delete.
- **Filter:** `_type == "post" || _type == "author"`
- **Projection:** `{_id}`. The backend never reads the body (it only checks the signature), so sending just the ID keeps each call tiny. Bodies over 1 MB are refused.
- **HTTP method:** POST. Leave drafts out (the default), since members only see published posts.
- **Secret:** the same value as `SANITY_WEBHOOK_SECRET`.

Each call is signed with that secret. Calls without a valid signature, or signed more than 5 minutes earlier, get a 401 and change nothing. While `SANITY_WEBHOOK_SECRET` is empty, the webhook answers 503 and the cache simply expires after 10 minutes.
