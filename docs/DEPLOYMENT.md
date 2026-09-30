# Deployment

## Branches and pipeline

`feature/*` and `hotfix/*` → PR into `develop` (staging) → PR into `main` (production).

`.github/workflows/ci.yml` runs on every push/PR:

1. **lint** — `ruff check`, `compileall`
2. **test** — PostgreSQL 16 service: `check`, `check --deploy`, `makemigrations --check`, `collectstatic`, `migrate` on an empty database, `seed_demo --with-activity` (twice, to prove idempotency), `pytest` (including the PostgreSQL row-lock concurrency tests)
3. **build** — Docker image; pushed to GHCR as `ghcr.io/<repo>:<sha12>` on `develop`/`main`
4. **deploy-staging** — `develop` only, GitHub environment `staging`
5. **uat-signoff** — `main` only, GitHub environment `uat` (configure required reviewers)
6. **deploy-production** — `main` only, after UAT; GitHub environment `production` (configure required reviewers and a `main`-only deployment branch rule)

## GitHub configuration (one-time)

Environments `staging`, `uat`, `production`:

| Kind | Name | Used by |
|---|---|---|
| secret | `STAGING_HOST`, `STAGING_USER`, `STAGING_SSH_KEY` | staging deploy |
| secret | `PRODUCTION_HOST`, `PRODUCTION_USER`, `PRODUCTION_SSH_KEY` | production deploy |
| variable | `STAGING_URL`, `PRODUCTION_URL` | health check after deploy |

No application secret is stored in Git or in GitHub: each host keeps `/srv/society-ca/.env`.

## Host setup

```
/srv/society-ca/
  .env                      # SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, DEBUG=0
  docker-compose.prod.yml   # copy from the repository
```

A TLS-terminating reverse proxy (nginx/Caddy) forwards to `127.0.0.1:8000` and sets `X-Forwarded-Proto`.
`scripts/deploy.sh` pulls the image, runs migrations once in a one-off container, restarts the web
service and polls `/healthz/` (returns `{"status": "ok", "database": "ok"}`).

## Required production settings

| Variable | Notes |
|---|---|
| `DEBUG=0` | Startup fails without `SECRET_KEY` when debug is off |
| `SECRET_KEY` | long random value, per environment |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` (URL-encode special characters) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | the public hostname(s) |
| `SECURE_SSL_REDIRECT` | default `1`; `/healthz/` is exempt |

## First run

```bash
docker compose run --rm -e RUN_MIGRATIONS=1 web true
docker compose run --rm web python manage.py createsuperuser
```

Then, in Django admin, create the society and add **Society memberships** (user + society + role).

## Backups

PostgreSQL is the system of record. Before go-live, schedule `pg_dump` (or managed snapshots) and
perform a restore drill into staging; financial history is append-only and must be recoverable.
