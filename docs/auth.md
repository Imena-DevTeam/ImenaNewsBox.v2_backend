# Authentication

## The idea: log in once per device

A family name is easy to know, so on its own it must **never** be enough to get into an account. v1 got this wrong: anyone who knew a family name could log in as that person.

In v2, **each new device has to prove itself once**, and after that it stays logged in:

- **Members and social media managers:** family name, then a **6-digit code emailed** to their address.
- **Admins:** family name and **password**, then an emailed code (a second factor).

After that, the device holds a long-lived **refresh token** and doesn't have to log in again for up to 90 days of inactivity. Someone who knows your family name but not your inbox (or password) can't get in from their own device.

## Login flow (member)

```mermaid
sequenceDiagram
    actor M as Member (new device)
    participant F as Frontend (Vercel)
    participant A as API (Render)
    participant E as Email provider

    M->>F: enters family name
    F->>A: POST /api/v1/auth/login {family_name}
    A->>A: find active user, create hashed code (10 min, 5 attempts)
    A->>E: send code
    A-->>F: 200 "code sent"
    M->>F: enters 6-digit code
    F->>A: POST /api/v1/auth/verify {family_name, code}
    A->>A: check code, create user_sessions row
    A-->>F: access token (body) + refresh token (HttpOnly cookie)
    Note over M,A: For admins, the login step also checks the password<br/>before any code is sent.
```

## Endpoints

All under `/api/v1/auth`. None of them needs an access token.

### `POST /login` — step one on a new device

Body: `{"family_name": "...", "password": "..."}`. Only admins send `password`; members leave it out. The family name ignores case and surrounding spaces.

| Who | Response | Email |
|---|---|---|
| member or social media manager | 200 `{message, expires_in: 600, resend_after: 60}` | code sent |
| admin with the right password | the same 200 | code sent |
| unknown family name, deactivated user, or admin **without** a password | the same 200 | **none** |
| anyone who sent a password that isn't an admin's correct one | 401 `invalid_credentials` | none |

The 200 is identical in every case, so the endpoint doesn't say which family names exist or who is an admin. A sent password always costs the same Argon2 time. A few answers do only happen for real accounts: the 429 limits (`code_recently_sent`, `too_many_codes`) and an email failure (503 `email_not_sent` / `email_not_configured`). We accept that, because telling a real member what went wrong matters more, and family names aren't secret anyway; the code is what protects the account.

> **Frontend:** give admins a way to enter their password (for example an "I'm an admin" link that shows the field). An admin who logs in without it gets the normal "code is on its way" answer, but no email.

### `POST /verify` — step two: the code logs the device in

Body: `{"family_name": "...", "code": "123456"}`. Spaces around the code are ignored; anything other than 6 ASCII digits is a 422.

- **Right code:** 200 `{"access_token", "token_type": "bearer", "expires_in": 900}`, plus the refresh cookie (see [Tokens](#tokens)). A new `user_sessions` row is created, labelled from the `User-Agent` (e.g. "Safari on iPhone"), and the first successful code sets `email_verified_at`.
- **Anything else** (wrong, used, expired, out of attempts, an older code, someone else's code, an unknown or deactivated user): 401 `invalid_code`, and no cookie.

Every way of logging in ends in `start_session` (`auth/service.py`), which creates the session and the tokens, so Google sign-in will behave exactly the same from that point on.

### `POST /refresh` — a new access token

No body. The browser sends the refresh cookie by itself, and the request must carry our `Origin` (see [Why the Vercel proxy](#why-the-vercel-proxy)).

| Situation | Response |
|---|---|
| the current refresh token | 200 with a new access token, and a **new** refresh cookie; the session's 90 days start again |
| the token this one replaced, **within 30 seconds** of the replacement | 409 `refresh_conflict`: another tab refreshed at the same moment. **Retry once**: the browser already has the new cookie |
| the replaced token, **later than that** | 401 `session_ended`, and the whole session is revoked: someone else has a copy of the token |
| no cookie | 401 `not_authenticated` |
| unknown token, revoked or expired session, deactivated user | 401 `session_ended` |
| missing or foreign `Origin` | 403 `origin_not_allowed` |

### `POST /logout` — log this device out

No body; it needs the cookie and our `Origin`, like refresh. It revokes this device's session (the replaced token works too) and clears the cookie, then returns 204. With no cookie or an unknown token it still returns 204, so the frontend can always treat logout as done. Other devices stay logged in. A missing or foreign `Origin` gets 403 `origin_not_allowed`.

> **Frontend:** when a call returns 401 `token_expired`, call `/refresh` once and retry the call. On 409, retry the refresh after a short pause. On any other refresh error, send the user to the login page. Make concurrent calls share one refresh rather than each starting its own.

## Tokens

| | Access token | Refresh token |
|---|---|---|
| **Lifetime** | 15 minutes | 90 days, reset on every use |
| **Where the frontend keeps it** | in memory | an `HttpOnly` cookie it can't read |
| **How it's sent** | `Authorization: Bearer …` header | automatically by the browser, only to `/api/v1/auth` |
| **What it's for** | calling the API | getting a new access token |
| **What it is** | a signed JWT holding only the user and session IDs (never the role) | a random string with no meaning of its own |
| **What the server stores** | nothing | an HMAC of it, keyed by `REFRESH_TOKEN_SECRET` |

**How refresh works:**
- When the access token expires, the frontend calls the refresh endpoint. The browser sends the cookie, and the API returns a new access token **and a new refresh token**: it **rotates** on every use.
- If an *old* refresh token is reused more than 30 seconds after it was replaced (a sign it was stolen), that whole session is revoked. Within those 30 seconds it gets a "retry" answer instead, because two tabs refreshing at once is normal.
- Each refresh token is stored only as a **hash** in a `user_sessions` row, together with the user, a device label (from the browser's user agent), the creation and last-used times, and a revoked flag.
- Users can see their own sessions ("devices") and sign any of them out. Admins can revoke anyone's sessions, and deactivating a user revokes all of them.

**Lifetimes are configurable** through `ACCESS_TOKEN_EXPIRES_IN` and `REFRESH_TOKEN_EXPIRES_IN` (in seconds).

**Error codes the frontend should handle:**

| Status | Code | Meaning | Frontend should |
|---|---|---|---|
| 401 | `not_authenticated` | no `Authorization: Bearer` header | log in |
| 401 | `token_expired` | the access token is older than 15 minutes | refresh, then retry |
| 401 | `invalid_token` | the token is malformed, tampered with or signed with another key | log in |
| 401 | `session_ended` | that device was signed out, its session expired, or the user was deactivated (deliberately not saying which) | log in |
| 403 | `insufficient_role` | logged in, but the role is too low | show "not allowed" |
| 403 | `not_owner` | logged in, but it's someone else's resource | show "not allowed" |
| 403 | `origin_not_allowed` | a refresh or logout request had no `Origin` header, or one not in `CORS_ORIGINS` | nothing: it means the request didn't come from our site (or the deploy's `CORS_ORIGINS` is wrong) |

## Emailed codes

- **Format and limits:** 6 digits, valid for 10 minutes, single-use, 5 attempts.
- **Only the newest code works.** Asking for a new code makes any earlier one useless.
- **Resends:** one per 60 seconds (429 `code_recently_sent`), and at most **10 codes per user in any 24 hours** (429 `too_many_codes`). Ten codes with five attempts each is 50 guesses a day out of a million possible codes, and it stops anyone flooding a member's inbox or using up the email quota.
- **Wrong, used, expired or exhausted** codes all get the same 401 `invalid_code`, so a guesser learns nothing.
- **Storage:** codes are stored **hashed**, never in plain text. The hash is keyed by `CODE_SECRET`, because there are only a million possible codes: a plain hash could be reversed in seconds.
- **Throttling** is stored in Postgres (there's no Redis).
- **Cleanup:** there's no scheduler, so every login request deletes codes older than 24 hours and sessions that expired or were revoked more than 7 days ago.
- **Sending:** codes are sent **during the request**. If the email provider fails, the user sees an error right away and can try again, instead of waiting for an email that never arrives. A code that couldn't be sent is deleted, so it doesn't trigger the 60-second cooldown.
- **Concurrency:** issuing a code locks the user's row, and checking one locks the code's row, so two requests at once can't both get past the limits or both use the same code. The code is committed *before* the email is sent, so no lock is held while waiting for the provider.
- **`email_verified_at`** is set the first time a code to an address works. It doesn't grant access; it tells us the address is real. When an admin changes someone's email, it's cleared and a code goes to the new address.

## Why the Vercel proxy

The frontend runs on Vercel and the API on Render, which are **different sites** as far as the browser is concerned (`vercel.app` and `onrender.com` are both "public suffixes"). Browsers, Safari especially, block cookies set by a *different* site. If the frontend called the API directly, iPhone users would never keep their refresh cookie and would have to log in with a code every 15 minutes.

**The fix:** the frontend calls `/api/...` on **its own domain**, and Vercel forwards those requests to Render with a rewrite in `vercel.json`. To the browser, the API is the same site, so the refresh cookie is an ordinary first-party cookie.

**What this means for the backend:**
- **Cookie attributes:** the refresh cookie is `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` with **no `Domain` attribute**, so it binds to the frontend's host.
- **Origin check:** refresh and logout also check the request's `Origin` against the allowed frontend origins (`CORS_ORIGINS`), as extra protection against cross-site request forgery (`require_allowed_origin` in `auth/dependencies.py`). A request **without** an `Origin` header is refused: browsers always send one on a POST, so only non-browser clients lack it, and those (Postman, scripts) must set it themselves.
- **CORS** mostly matters for local development. It always lists explicit origins, never `*`.
- **Client IP:** requests reach us through two proxies, so the real IP and user agent come from forwarded headers. IP is never the only thing we throttle on.

> **Frontend team:** the `/api/*` rewrite in `vercel.json` is required. Without it, logins break on Safari.

## Roles and ownership in code

- `get_current_user` reads the access token, then loads the user from the database (role and active status come from there).
- `require_role(Role.X)` lets in role X and above.
- An ownership check (owner or admin) protects edits to profiles and wishes.

See [Domain → Access rules](domain.md#access-rules) for who can do what.
