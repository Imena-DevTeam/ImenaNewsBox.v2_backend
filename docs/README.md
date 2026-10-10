# Imena NewsBox backend: documentation

Welcome! These docs explain **what** we're building, **how** the backend is put together, and **why** we made the choices we did. Read them in this order on your first day:

| # | Doc | What you'll learn |
|---|---|---|
| 1 | [Architecture](architecture.md) | How the code is organized, where new code goes, and how a request flows through the app |
| 2 | [Domain](domain.md) | Users, roles, access rules, wishes and blog posts: the business rules |
| 3 | [Authentication](auth.md) | Per-device login, emailed codes, tokens and cookies, step by step |
| 4 | [Infrastructure](infrastructure.md) | Where everything runs, the free-tier constraints, caching and CI |
| 5 | [Decisions](decisions.md) | The log of design decisions and the reasoning behind each one |

For the day-to-day workflow (setup, branches, commits, PRs), see [CONTRIBUTING.md](../CONTRIBUTING.md).

## The project in one paragraph

Imena NewsBox is a **private family platform**. Admins register family members, and members can see each other's profiles, wish each other a happy birthday, and read the family blog. Social media managers from the family committee publish blog posts. Visitors who aren't members only see a public landing page served by the frontend; the API itself has **no public endpoints** except login and a health check. v2 is a from-scratch rewrite of the v1 Node/Express backend. We kept the mission and dropped the code.

## The stack at a glance

- **API:** Python 3.14, FastAPI, Pydantic v2, async SQLAlchemy 2.x, Alembic. Managed with **uv**.
- **Database:** Postgres on **Neon** (free tier).
- **Hosting:** one Docker container on **Render** (free tier). The Next.js frontend is on **Vercel**.
- **External services:** **Sanity** (blog content), **Cloudinary** (profile photos), and an email provider (Resend or the Gmail API; not chosen yet).
- **Hard rule:** everything must run on free tiers.

## Open decisions

These are not settled yet. Don't build around a guess; ask in the team channel.

- **Email provider:** Resend (needs a verified sending domain) or the Gmail API (free, no domain). All email goes through `core/email.py` either way.
- **Sub-family and promotion lists:** values will be provided when the `users` feature is built.
- **Sanity post fields:** the schema already exists in Sanity, so get the details before building `posts`.
