#!/usr/bin/env bash
# Run from a checkout or an extracted, immutable release archive.
set -euo pipefail
umask 077

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
export IMENA_ENV_FILE=${IMENA_ENV_FILE:-$repo_dir/.env}
export IMENA_IMAGE_TAG=${IMENA_IMAGE_TAG:-$(git rev-parse HEAD)}

if [[ "$IMENA_ENV_FILE" != /* || ! -f "$IMENA_ENV_FILE" ]]; then
  echo "Set IMENA_ENV_FILE to the absolute path of the production .env file." >&2
  exit 1
fi
if [[ ! "$IMENA_IMAGE_TAG" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo "Invalid image tag." >&2
  exit 1
fi

# Serialize manual and automated deployments using the shared env directory.
exec 9>"$(dirname -- "$IMENA_ENV_FILE")/.deploy.lock"
flock -w 1200 9
compose=(docker compose --project-name imena-newsbox-prod
  --env-file "$IMENA_ENV_FILE" -f "$repo_dir/compose.production.yml")
"${compose[@]}" config --quiet

# A failed build leaves the running release alone.
"${compose[@]}" build --pull api
if ! "${compose[@]}" up --detach --wait --wait-timeout 180 api; then
  "${compose[@]}" ps
  echo "Deployment failed. Inspect docker compose logs on the server." >&2
  exit 1
fi
"${compose[@]}" ps
printf 'Healthy release: %s\n' "$IMENA_IMAGE_TAG"
