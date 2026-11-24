# VPS deployment

Target supplied by the team: `5.252.53.111`. SSH user, OS, backend domain and existing
reverse proxy still need confirmation. The commands below assume Ubuntu/Debian,
an existing Docker Engine with Compose v2, and a sudo-capable SSH user.

This keeps Neon PostgreSQL and the frontend's same-origin `/api/*` proxy. It runs
one API container; migrations run at startup through the existing Dockerfile.
`compose.production.yml` is standalone: **do not combine it with docker-compose.yml**.
It binds only to `127.0.0.1:8010`, enables no reload or source mounts, and publishes
no database. Settings and provider credentials stay in an untracked server file.

## First deployment

Replace `YOUR_SSH_USER` with the server login. Run locally:

```bash
ssh YOUR_SSH_USER@5.252.53.111
```

On the server, check the OS, installed tools and existing listeners first:

```bash
cat /etc/os-release
docker --version
docker compose version
sudo ss -ltnp
```

If Docker is missing, use the official instructions for the reported OS:
[Ubuntu](https://docs.docker.com/engine/install/ubuntu/) or
[Debian](https://docs.docker.com/engine/install/debian/).
Do not replace an existing Docker installation or proxy to follow this guide.
The login used below must be allowed to run Docker without a password prompt;
Docker access grants administrative control of the host.

After this deployment change is merged into `main`, run:

```bash
sudo install -d -m 750 -o "$(id -un)" -g "$(id -gn)" /opt/imena-newsbox
install -d -m 700 /opt/imena-newsbox/shared
install -d -m 750 /opt/imena-newsbox/releases
git clone https://github.com/Imena-DevTeam/ImenaNewsBox.v2_backend.git /opt/imena-newsbox/source
cd /opt/imena-newsbox/source

# Only create secrets for a NEW deployment. Never overwrite an existing .env.
if [ ! -e /opt/imena-newsbox/shared/.env ]; then
  umask 077
  cp .env.example /opt/imena-newsbox/shared/.env
  sed -i 's/^ENVIRONMENT=.*/ENVIRONMENT=production/; s/^API_PORT=.*/API_PORT=8010/' /opt/imena-newsbox/shared/.env
  for key in ACCESS_TOKEN_SECRET REFRESH_TOKEN_SECRET CODE_SECRET; do
    sed -i "s/^${key}=.*/${key}=$(openssl rand -hex 32)/" /opt/imena-newsbox/shared/.env
  done
fi
chmod 600 /opt/imena-newsbox/shared/.env
nano /opt/imena-newsbox/shared/.env
```

Before continuing, set these values in that file:

- `DATABASE_URL`: the existing Neon **pooled** URL, using `postgresql+psycopg://`
  and `?sslmode=require` (preserve any additional Neon connection options).
- `DATABASE_URL_DIRECT`: the same database's **direct** URL, using that same
  scheme and TLS options. Replace the localhost development URLs.
- `FRONTEND_URL=https://YOUR_FRONTEND_DOMAIN`, with no path or trailing slash.
- `CORS_ORIGINS=https://YOUR_FRONTEND_DOMAIN`, with additional explicit origins
  comma-separated if needed. Do not use `*`.
- Existing auth secrets when moving a live backend; preserve them to keep its
  sessions valid. The generated secrets above are for a fresh installation.
- `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`,
  `SMTP_PASSWORD`. Login codes require working email, except for configured Google
  sign-in. Use `starttls` on 587 or `tls` on 465 as your provider specifies.
- Existing Sanity, Cloudinary and optional Google settings from the current deployment.

Keep a database backup/recovery point before a production schema migration.
The startup migrations change the database configured above. Reusing the existing
database does not require reseeding the admin or importing v1 data again.

```bash
cd /opt/imena-newsbox/source
IMENA_ENV_FILE=/opt/imena-newsbox/shared/.env bash scripts/deploy.sh
ln -sfn /opt/imena-newsbox/source /opt/imena-newsbox/current
curl --fail --show-error http://127.0.0.1:8010/health
```

Expect `{"status":"ok"}`. The health check confirms process liveness; it deliberately
does not test the database, SMTP, Cloudinary or Sanity. Check an authenticated flow,
including receiving a login email, before switching the frontend to this backend.

For a genuinely empty v2 database, set the four `FIRST_ADMIN_*` values in the
shared env file (family name, email, password of at least 8 characters, and one of
`HOPE`, `LIGHT`, `WIHOGORA`), then seed the first admin after migrations succeed:

```bash
cd /opt/imena-newsbox/source
export IMENA_ENV_FILE=/opt/imena-newsbox/shared/.env
export IMENA_IMAGE_TAG=$(git rev-parse HEAD)
docker compose --env-file "$IMENA_ENV_FILE" -f compose.production.yml run --rm --no-deps \
  -v "$PWD/scripts:/app/scripts:ro" api python scripts/seed_admin.py
```

Remove `FIRST_ADMIN_PASSWORD` from the shared env file afterwards. Skip this
step when reusing a v2 database that already has an admin.

## HTTPS and the frontend

Point the backend domain's DNS A record to `5.252.53.111`, then configure the
server's existing reverse proxy to forward that hostname to `127.0.0.1:8010`
with HTTPS. Do not replace existing site configurations or take over ports 80/443.

For an existing Nginx installation, the following is a **new site's server block**;
replace the domain, choose an unused filename, and obtain a certificate using
your server's existing certificate tooling. This HTTP configuration is only the
initial configuration before enabling TLS:

```nginx
server {
    listen 80;
    server_name YOUR_BACKEND_DOMAIN;
    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # Replace untrusted client-supplied forwarding headers at this boundary.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }
}
```

Validate with `sudo nginx -t` before reloading Nginx. If this Nginx sits behind
another proxy/CDN, configure its trusted real-IP handling first. The Dockerfile
trusts forwarded headers, so the application port must remain private.
Apply the existing proxy's request rate limits to auth endpoints as appropriate.

For Vercel, retain the same-origin API rewrite, updating its destination:

```json
{
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://YOUR_BACKEND_DOMAIN/api/:path*"
    }
  ]
}
```

Merge this with the frontend's existing rewrites; do not overwrite the whole file.
The frontend should keep calling `/api/v1/...` on its own origin so secure refresh
cookies keep working. Update the Sanity webhook target if it used the old backend.

## Continuous delivery

CI runs on PRs, pushes to `main`, and manual dispatch. Lint, types, fresh database
migrations, tests, the repository guard, Compose validation, and the production
Docker build must pass before deployment. PRs and non-main manual runs cannot deploy.

In GitHub settings, create an environment named `production` and restrict it to
`main`. Keep repository Actions permissions read-only. Add these **repository variables**:

| Variable | Value |
|---|---|
| `DEPLOY_HOST` | `5.252.53.111` |
| `DEPLOY_USER` | The server login that owns `/opt/imena-newsbox` and can run Docker |
| `DEPLOY_PORT` | `22`, unless SSH uses a different port |
| `DEPLOY_ENABLED` | `true`, only after the first deployment and HTTPS checks pass |

Add these **production environment secrets**:

| Secret | Value |
|---|---|
| `DEPLOY_SSH_KEY` | Dedicated SSH private key authorized for that server login |
| `DEPLOY_KNOWN_HOSTS` | Verified SSH host-key line(s) for the server and configured SSH port |

Generate the dedicated key on your own computer using `ssh-keygen -t ed25519 -f
./imena_deploy -C imena-newsbox-actions`. Install only `imena_deploy.pub` in the
server login's `~/.ssh/authorized_keys`. Put the private key in the environment
secret, not the repository or a chat. Verify the host key fingerprint through a
trusted channel, such as the VPS console; `ssh-keyscan` alone does not authenticate
a server. For a nonstandard port, known_hosts uses `[5.252.53.111]:PORT`.

Each deployment uploads only the tested commit's tracked source into
`/opt/imena-newsbox/releases/<full-commit-sha>`. The server builds the image with
that SHA as its tag. It reads `/opt/imena-newsbox/shared/.env`; the workflow never
uploads production application credentials. Deployments are serialized, and the
`current` link changes only after the container is healthy. Failed builds leave
the running release in place. Startup/migration failures fail the workflow and
require inspection; there is no automatic database rollback or zero-downtime claim.

## Logs, updates and recovery

```bash
cd /opt/imena-newsbox/current
export IMENA_ENV_FILE=/opt/imena-newsbox/shared/.env
docker compose --env-file "$IMENA_ENV_FILE" -f compose.production.yml ps
docker compose --env-file "$IMENA_ENV_FILE" -f compose.production.yml logs --tail=100 api
```

Deploy an update by merging to `main` (with CD enabled), or manually:

```bash
cd /opt/imena-newsbox/source
git pull --ff-only origin main
IMENA_ENV_FILE=/opt/imena-newsbox/shared/.env bash scripts/deploy.sh
ln -sfn /opt/imena-newsbox/source /opt/imena-newsbox/current
```

Use one deployment method at a time. Old source releases and images remain for
inspection. To redeploy a previous automated release, review its compatibility
with the current schema, then run `scripts/deploy.sh` from that release directory
with its full SHA in `IMENA_IMAGE_TAG` and the shared env path in `IMENA_ENV_FILE`.
Update `current` after success. This runs migrations from the old release too, so
it may refuse if the database is ahead. Plan schema recovery separately; do not
blindly run `alembic downgrade`, delete volumes, or prune recovery images.
