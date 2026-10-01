#!/usr/bin/env bash
set -euo pipefail
cd -- "$1"
image="$2"
[[ "$image" =~ ^ghcr.io/zoyanywhere/seedance-prompt-review:[a-f0-9]{40}$ ]]
test -f .env || { echo 'Create the private server .env first.' >&2; exit 1; }
chmod 600 .env
docker network inspect web-network >/dev/null
compose=(docker compose --project-name promptlab --env-file .env -f compose.production.yaml)
previous=""
if [[ -f .release.env ]]; then
  previous=$(sed -n 's/^APP_IMAGE=//p' .release.env)
fi
export APP_IMAGE="$image"
"${compose[@]}" config --quiet
"${compose[@]}" pull
# Preserve a database backup before an existing installation is changed.
if "${compose[@]}" ps --status running --services | grep -qx db; then
  mkdir -p backups
  chmod 700 backups
  umask 077
  "${compose[@]}" exec -T db pg_dump -U seedance -d seedance -Fc > "backups/predeploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
fi
if ! "${compose[@]}" up -d --wait --wait-timeout 600; then
  if [[ "$previous" =~ ^ghcr.io/zoyanywhere/seedance-prompt-review:[a-f0-9]{40}$ ]]; then
    export APP_IMAGE="$previous"
    "${compose[@]}" up -d --wait --wait-timeout 120 || true
  fi
  echo 'Deployment failed; previous app image restored when available. Database was not rolled back.' >&2
  exit 1
fi
umask 077
printf 'APP_IMAGE=%s\n' "$image" > .release.env
echo "Deployment healthy: $image"
