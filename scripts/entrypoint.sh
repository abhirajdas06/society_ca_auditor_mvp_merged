#!/bin/sh
# Container entrypoint. Migrations run only when RUN_MIGRATIONS=1 so that exactly one
# release step (not every web replica) applies schema changes.
set -e
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi
exec "$@"
