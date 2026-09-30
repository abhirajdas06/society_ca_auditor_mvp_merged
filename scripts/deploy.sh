#!/bin/sh
# Pull a pre-built image on the target host, run migrations once, restart, and verify /healthz/.
# Required env: IMAGE_REF DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY HEALTH_URL
# The host keeps its own /srv/society-ca/.env (secrets are never stored in Git).
set -eu
for v in IMAGE_REF DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY HEALTH_URL; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then echo "Missing $v — configure the GitHub environment secrets/vars." >&2; exit 1; fi
done
KEY=$(mktemp); trap 'rm -f "$KEY"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$KEY"; chmod 600 "$KEY"
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$DEPLOY_USER@$DEPLOY_HOST" "set -e
  cd /srv/society-ca
  docker pull '$IMAGE_REF'
  docker run --rm --env-file .env -e RUN_MIGRATIONS=1 '$IMAGE_REF' true
  IMAGE_REF='$IMAGE_REF' docker compose -f docker-compose.prod.yml up -d --remove-orphans"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then echo "Healthy: $HEALTH_URL"; exit 0; fi
  sleep 6
done
echo "Health check failed: $HEALTH_URL" >&2
exit 1
