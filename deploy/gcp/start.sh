#!/usr/bin/env bash
# Start or update Maestro-SC on the VM:  sudo /opt/maestro/deploy/gcp/start.sh
# Reads the API key and the admin passcode from Google Secret Manager (the VM's service
# account needs Secret Accessor on both), then builds and starts the containers.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "Create the settings file first: cp maestro.env.example .env, then edit it." >&2; exit 1; }
set -a; . ./.env; set +a

secret() { gcloud secrets versions access latest --secret="$1" --quiet; }

MAESTRO_ADMIN_PASSWORD="$(secret maestro-admin-password)"
[ -n "$MAESTRO_ADMIN_PASSWORD" ] || { echo "The secret maestro-admin-password is empty." >&2; exit 1; }
export MAESTRO_ADMIN_PASSWORD
if [ "${AI_ENABLED:-true}" = "true" ]; then
  ANTHROPIC_API_KEY="$(secret maestro-anthropic-api-key)"
  export ANTHROPIC_API_KEY
fi

docker compose up -d --build --remove-orphans
docker compose ps
echo
echo "Maestro-SC: https://${MAESTRO_HOST}/maestro   (health: https://${MAESTRO_HOST}/api/health)"
