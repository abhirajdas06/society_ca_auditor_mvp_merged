# Deployment guide — shared Ubuntu host (`/home/multihost`)

This host already runs several projects (`aquaria`, `DMSBackend`, `HrmSoftware`, `inventory_project`,
`tailor`, `zcpl`, …). This guide deploys **Society CA** beside them without touching anything that
already works.

Stack: PostgreSQL → Gunicorn (one systemd service) → Nginx reverse proxy. No containers.

**The rule for a shared host:** everything this project owns gets a unique name — its own directory,
port, systemd unit, database, database user and nginx config file. Nothing global is edited except
adding one new nginx site file.

| Must be unique per project | This deployment uses |
|---|---|
| Directory | `/home/multihost/society_ca` |
| Gunicorn port (localhost only) | `$APP_PORT` (chosen in step 2) |
| systemd unit | `society-ca.service` |
| PostgreSQL database / user | `society_ca_db` / `society_ca_user` |
| Nginx site file | `/etc/nginx/sites-available/society-ca` |
| Domain | `$APP_DOMAIN` |

> There are already `society` and `soc_demo` directories in `/home/multihost`. This guide installs
> into a **new** `society_ca` directory so nothing existing is overwritten. Step 1 tells you how to
> check what those are; if one of them is an older copy of this app, read
> [Upgrading an existing deployment](#12-upgrading-an-existing-deployment) instead.

---

## 1. Survey the host first

Run this and read the output before doing anything else. It changes nothing.

```bash
echo "== OS / Python =="; lsb_release -ds; python3 --version
echo; echo "== Web server =="; (nginx -v 2>&1 || echo "no nginx"); (apache2 -v 2>&1 | head -1 || true)
echo; echo "== Ports already in use (pick one that is NOT listed) =="; (ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null) | awk 'NR==1 || /LISTEN/'
echo; echo "== Existing app services =="; systemctl list-units --type=service --state=running | grep -Ei 'gunicorn|uwsgi|node|django|daphne|uvicorn' || echo "none matched"
echo; echo "== PostgreSQL =="; (psql --version 2>/dev/null || echo "psql not installed"); systemctl is-active postgresql 2>/dev/null
echo; echo "== Existing databases =="; sudo -u postgres psql -lqt 2>/dev/null | cut -d'|' -f1 | sed '/^\s*$/d' || echo "(cannot list - check sudo access)"
echo; echo "== Nginx sites =="; ls -1 /etc/nginx/sites-enabled/ 2>/dev/null
echo; echo "== Disk =="; df -h /home | tail -1
echo; echo "== What are the existing society folders? =="; ls -la ~/society ~/soc_demo 2>/dev/null | head -40
```

What you need from that output:

- a **free TCP port** for Gunicorn (nothing listening on it),
- whether **nginx** is the front-end (this guide assumes it; see §11 for Apache),
- whether **PostgreSQL** is installed and you can reach it with `sudo -u postgres psql`,
- whether `~/society` / `~/soc_demo` are older copies of this app.

If PostgreSQL is missing: `sudo apt update && sudo apt install -y postgresql postgresql-contrib`.
If `python3 --version` is below 3.12: `sudo apt install -y python3.12 python3.12-venv` and use
`python3.12` wherever this guide says `python3`.

---

## 2. Set the values for this deployment

Edit the four values at the top, then paste the whole block. **Everything below reuses these
variables, so run the rest of the guide in this same SSH session** (or re-paste this block after
reconnecting).

```bash
# ---- edit these four ----
export APP_DOMAIN="society.example.com"     # the domain or subdomain pointing at this server
export APP_PORT="8010"                      # a free port from step 1 (localhost only)
export DB_PASSWORD="$(openssl rand -hex 24)"   # generated; printed below, store it in your password manager
export DJANGO_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
# ---- leave these alone ----
export APP_NAME="society-ca"
export APP_DIR="/home/multihost/society_ca"
export DB_NAME="society_ca_db"
export DB_USER="society_ca_user"
export APP_USER="$(whoami)"

printf '\nDomain   : %s\nPort     : %s\nDirectory: %s\nDB       : %s / %s\nDB pass  : %s\n\n' \
  "$APP_DOMAIN" "$APP_PORT" "$APP_DIR" "$DB_NAME" "$DB_USER" "$DB_PASSWORD"
```

Confirm the port really is free (no output = free):

```bash
(ss -lnt 2>/dev/null || netstat -lnt) | grep ":$APP_PORT " || echo "port $APP_PORT is free"
```

---

## 3. Put the code on the server

**Option A — from a Git remote (preferred):**

```bash
sudo apt install -y git
git clone <YOUR-REPO-URL> "$APP_DIR"
cd "$APP_DIR" && git log --oneline -1
```

**Option B — copy from your Windows machine.** Run this in PowerShell *on your PC*, not on the
server (it skips the local virtualenv, database and secrets):

```powershell
scp -r D:\Project\Soc\society_ca_auditor_mvp_merged multihost@hostingserver:/home/multihost/society_ca
```

Then on the server, remove anything local that should never be deployed:

```bash
cd "$APP_DIR" && rm -rf .venv __pycache__ .pytest_cache .ruff_cache db.sqlite3 .env staticfiles
ls -a
```

---

## 4. Virtualenv and dependencies

```bash
sudo apt install -y python3-venv python3-dev build-essential libpq-dev
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-prod.txt
.venv/bin/python -c "import django, gunicorn; print('django', django.get_version(), '| gunicorn ok')"
```

`requirements-prod.txt` is the app's dependencies plus Gunicorn. (`requirements-dev.txt` adds pytest
and ruff — only install that if you intend to run the test suite on this host.)

---

## 5. PostgreSQL database and user

This creates **one new database and one new user**. It does not touch other projects' databases.

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8';
ALTER ROLE $DB_USER SET client_encoding TO 'utf8';
ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';
ALTER ROLE $DB_USER SET timezone TO 'Asia/Kolkata';
SQL
```

Verify the app's user can actually connect (should print `connected`):

```bash
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -c "SELECT 'connected';"
```

If that fails with a peer-authentication error, connect over TCP as above (`-h 127.0.0.1`) — which
is what the app does — and make sure `/etc/postgresql/*/main/pg_hba.conf` has a `host all all
127.0.0.1/32 scram-sha-256` line. After editing it: `sudo systemctl reload postgresql`.

---

## 6. The `.env` file

The app reads `/home/multihost/society_ca/.env`. It holds the only secrets on the box, so it is
written `0600` (owner read/write only) and is already in `.gitignore`.

```bash
cd "$APP_DIR"
cat > .env <<ENV
DEBUG=0
SECRET_KEY=$DJANGO_SECRET
DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME
ALLOWED_HOSTS=$APP_DOMAIN,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://$APP_DOMAIN
SECURE_SSL_REDIRECT=0
LOG_LEVEL=INFO
ENV
chmod 600 .env
cat .env
```

`SECURE_SSL_REDIRECT` stays `0` until HTTPS is working (§10); turning it on too early makes the
site redirect to a certificate that does not exist yet.

---

## 7. Migrate, collect static files, create the first user

```bash
cd "$APP_DIR"
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy
```

`check --deploy` prints warnings about HTTPS settings — expected until §10 is done.

```bash
.venv/bin/python manage.py createsuperuser
```

**Do not run `seed_demo` on a real deployment** — it creates demo users with a shared password. It
is only for a throwaway demo/staging box.

---

## 8. Smoke-test Gunicorn by hand

Before involving systemd, confirm the app actually serves:

```bash
cd "$APP_DIR"
.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:$APP_PORT --workers 1 &
sleep 3
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -H "Host: $APP_DOMAIN" "http://127.0.0.1:$APP_PORT/login/"
kill %1
```

`HTTP 200` means the app, settings and database are all fine. Anything else: see
[Troubleshooting](#13-troubleshooting) before continuing.

---

## 9. systemd service

One unit, named for this project only.

```bash
sudo tee /etc/systemd/system/$APP_NAME.service >/dev/null <<UNIT
[Unit]
Description=Society CA (Django/Gunicorn)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn config.wsgi:application \\
          --bind 127.0.0.1:$APP_PORT \\
          --workers 3 \\
          --timeout 120 \\
          --access-logfile - \\
          --error-logfile -
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=5
KillMode=mixed
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now $APP_NAME
sleep 3
systemctl status $APP_NAME --no-pager
```

Settings come from `.env`, which Django loads itself — no `EnvironmentFile` needed.

Useful afterwards:

```bash
sudo systemctl restart $APP_NAME     # after a code change
sudo systemctl stop $APP_NAME
journalctl -u $APP_NAME -f           # live logs
journalctl -u $APP_NAME --since "10 min ago" --no-pager
```

**No sudo on this host?** Ask the admin to run the three `systemctl` commands above, or use a user
unit at `~/.config/systemd/user/$APP_NAME.service` with the same `[Service]` block (drop `User=`
and `Group=`), then `systemctl --user daemon-reload && systemctl --user enable --now $APP_NAME`.
A user unit only survives logout if root has run `loginctl enable-linger multihost` once.

---

## 10. Nginx site

Nginx must be able to traverse into the project directory to serve static files:

```bash
chmod o+x /home/multihost "$APP_DIR"
chmod -R o+rX "$APP_DIR/staticfiles"
```

One new file; existing sites are untouched:

```bash
sudo tee /etc/nginx/sites-available/$APP_NAME >/dev/null <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $APP_DOMAIN;

    client_max_body_size 6m;          # CSV imports are capped at 5 MB in the app
    access_log /var/log/nginx/$APP_NAME.access.log;
    error_log  /var/log/nginx/$APP_NAME.error.log;

    location /static/ {
        alias $APP_DIR/staticfiles/;
        expires 30d;
        access_log off;
    }

    location /media/ {
        alias $APP_DIR/media/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
        proxy_read_timeout 120s;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/$APP_NAME /etc/nginx/sites-enabled/$APP_NAME
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` must say *syntax is ok / test is successful* before the reload. Use `reload`, never
`restart`, so the other sites keep serving.

Check over HTTP:

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "http://$APP_DOMAIN/login/"
```

---

## 11. HTTPS

The host already uses Let's Encrypt (`letsencrypt.tar.gz` in the home directory), so certbot is
likely installed. DNS for `$APP_DOMAIN` must already point at this server.

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d "$APP_DOMAIN" --redirect --agree-tos -m you@example.com --no-eff-email
sudo nginx -t && sudo systemctl reload nginx
```

Certbot edits only this project's site file. Now turn on the app's own HTTPS settings:

```bash
cd "$APP_DIR"
sed -i "s/^SECURE_SSL_REDIRECT=.*/SECURE_SSL_REDIRECT=1/" .env
sudo systemctl restart $APP_NAME
curl -sS -o /dev/null -w "HTTPS %{http_code}\n" "https://$APP_DOMAIN/login/"
```

Renewal is automatic; confirm with `sudo certbot renew --dry-run`.

Once HTTPS has been working for a day or two, turn on HSTS. Do this **after** you are sure the
certificate renews, because browsers remember the instruction for the full duration:

```bash
cd "$APP_DIR"
grep -q '^SECURE_HSTS_SECONDS=' .env || echo "SECURE_HSTS_SECONDS=31536000" >> .env
sudo systemctl restart $APP_NAME
.venv/bin/python manage.py check --deploy
```

**Apache instead of nginx?** Use the equivalent vhost with
`ProxyPass / http://127.0.0.1:$APP_PORT/`, `ProxyPassReverse`,
`RequestHeader set X-Forwarded-Proto "https"`, and `Alias /static/ $APP_DIR/staticfiles/`.
Everything else in this guide is unchanged.

---

## 12. First-run application setup

The app shows nothing until a society exists and users are linked to it.

1. Sign in at `https://$APP_DOMAIN/admin/` with the superuser from §7.
2. **Societies → Add**: name, registration number, address, interest rate, compliance date,
   allocation policy. Leave *enforce maker checker* on.
3. **Society memberships → Add**: one row per person — user + society + role
   (`CA / Accountant`, `Operator`, `Society Admin`, `Auditor`). A user with no membership sees nothing.
4. Sign in at `https://$APP_DOMAIN/` as the CA and set up wings, flats, members, charge heads and
   charge rules — or import them under **Data import**.

A CA or auditor who handles several societies gets a membership row per society and switches between
them from the top bar.

---

## 13. Deploying an update

```bash
cd "$APP_DIR"
git pull                                   # or re-copy the files with scp
.venv/bin/pip install -r requirements-prod.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy
sudo systemctl restart $APP_NAME
journalctl -u $APP_NAME --since "1 min ago" --no-pager | tail -20
```

Take a database backup first (§15) whenever the update contains migrations.

### Upgrading an existing deployment

If `~/society` or `~/soc_demo` turns out to be an older copy of this app, do **not** copy files over
it. Instead: back up its database (§15), note its port, systemd unit and nginx file, deploy the new
version into `$APP_DIR` as above pointing `DATABASE_URL` at a **restored copy** of that database,
verify it, then switch the nginx `proxy_pass` to the new port and stop the old service.

---

## 14. Verify the deployment

```bash
systemctl is-active $APP_NAME && echo "service: running"
curl -sS -o /dev/null -w "login page: HTTP %{http_code}\n" "https://$APP_DOMAIN/login/"
curl -sS -o /dev/null -w "admin: HTTP %{http_code}\n" "https://$APP_DOMAIN/admin/login/"
cd "$APP_DIR" && .venv/bin/python manage.py check --deploy   # only the HSTS-preload warning is expected
sudo nginx -t
journalctl -u $APP_NAME --since "5 min ago" --no-pager | grep -i error || echo "no errors in logs"
```

Then in a browser: sign in, open **Dashboard**, **Receipts → Record receipt**, and a **Flat
statement**. Confirm the other projects on this host still respond.

---

## 15. Backups

The database is the system of record — bills, receipts and allocations are append-only financial
evidence. Nightly dump at 01:30, keeping 30 days:

```bash
mkdir -p /home/multihost/backups/society_ca
cat > /home/multihost/backups/society_ca/backup.sh <<'SH'
#!/bin/bash
set -euo pipefail
cd /home/multihost/society_ca
export $(grep -E '^DATABASE_URL=' .env | xargs)
STAMP=$(date +%F-%H%M)
OUT=/home/multihost/backups/society_ca
pg_dump "$DATABASE_URL" --format=custom --file="$OUT/society_ca-$STAMP.dump"
cp .env "$OUT/env-$STAMP.bak"
find "$OUT" -name 'society_ca-*.dump' -mtime +30 -delete
find "$OUT" -name 'env-*.bak' -mtime +30 -delete
SH
chmod 700 /home/multihost/backups/society_ca/backup.sh
/home/multihost/backups/society_ca/backup.sh && ls -lh /home/multihost/backups/society_ca
(crontab -l 2>/dev/null; echo "30 1 * * * /home/multihost/backups/society_ca/backup.sh >> /home/multihost/backups/society_ca/backup.log 2>&1") | crontab -
crontab -l | tail -3
```

**Do a restore drill before go-live** — an untested backup is not a backup:

```bash
sudo -u postgres createdb society_ca_restore_test -O $DB_USER
PGPASSWORD="$DB_PASSWORD" pg_restore -h 127.0.0.1 -U "$DB_USER" -d society_ca_restore_test \
  /home/multihost/backups/society_ca/$(ls -t /home/multihost/backups/society_ca | grep '\.dump$' | head -1)
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d society_ca_restore_test \
  -c "SELECT count(*) AS bills FROM society_bill; SELECT count(*) AS allocations FROM society_receiptallocation;"
sudo -u postgres dropdb society_ca_restore_test
```

Copy the dumps off this server as well — a backup on the same disk does not survive the disk.

---

## 16. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `502 Bad Gateway` | Gunicorn is down or on another port | `systemctl status $APP_NAME`; `journalctl -u $APP_NAME -n 50`; check `proxy_pass` port matches `--bind` |
| `DisallowedHost at /` | Domain missing from `ALLOWED_HOSTS` | Add it in `.env`, `sudo systemctl restart $APP_NAME` |
| `CSRF verification failed` on login | `CSRF_TRUSTED_ORIGINS` missing the https origin | `CSRF_TRUSTED_ORIGINS=https://$APP_DOMAIN` in `.env`, restart |
| Page loads unstyled, `/static/…` 404 | `collectstatic` not run, or nginx cannot traverse `/home/multihost` | re-run `collectstatic`; `chmod o+x /home/multihost $APP_DIR`; `chmod -R o+rX $APP_DIR/staticfiles` |
| `ImproperlyConfigured: SECRET_KEY must be set` | `.env` missing/unreadable by the service user | check `ls -l $APP_DIR/.env` and that `User=` in the unit owns it |
| `FATAL: password authentication failed` | Wrong `DATABASE_URL` password | re-run §5 with a new password and update `.env` |
| `connection refused` to Postgres | Postgres down, or not listening on 127.0.0.1 | `systemctl status postgresql`; check `listen_addresses` in `postgresql.conf` |
| Redirect loop on https | `SECURE_SSL_REDIRECT=1` but nginx is not sending `X-Forwarded-Proto` | keep the `proxy_set_header X-Forwarded-Proto $scheme;` line |
| Port already in use at start | Another project took the port | pick a free port, update `.env`-independent `--bind` in the unit **and** `proxy_pass`, reload both |
| Another site broke | A global file was edited | `sudo nginx -t`, `ls /etc/nginx/sites-enabled/`, restore that site's own file; this project only adds `$APP_NAME` |

**Rollback:** `git checkout <previous-commit>`, re-run `pip install -r requirements-prod.txt`,
`collectstatic`, restart. If a migration must be undone, restore the pre-update dump (§15) rather
than hand-editing financial tables.

---

## 17. Hardening checklist before real data

- [ ] `DEBUG=0`, unique `SECRET_KEY`, `.env` is `0600` and never committed
- [ ] HTTPS works, `SECURE_SSL_REDIRECT=1`, `certbot renew --dry-run` passes
- [ ] No demo users (`demo_ca`, `demo_operator`, `demo_admin`, `demo_auditor`); real accounts only
- [ ] Every user has exactly the society memberships they need; auditors are `Auditor`
- [ ] Nightly backup runs and a **restore drill has been done**
- [ ] `manage.py check --deploy` reports no errors (the `SECURE_HSTS_PRELOAD` warning is fine to leave; only submit to the preload list deliberately)
- [ ] Firewall exposes 80/443 only; Gunicorn stays on 127.0.0.1
- [ ] A CA/CS/advocate has verified each society's interest rate, compliance date and charge heads
