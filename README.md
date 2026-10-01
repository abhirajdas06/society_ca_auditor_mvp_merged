# Society CA & Auditor MVP — Merged Ledger-First Edition

Django 5.2 + PostgreSQL + Django Templates + HTML/CSS + vanilla JavaScript.

This is the merged MVP package for the CA/Auditor society-management workflow.

## Start here

1. Read `CLAUDE_MASTER_PROMPT.md`.
2. Read `docs/MVP_MERGED_SCOPE.md`.
3. Read `docs/SOURCE_SAMPLE_MAPPING.md`.
4. Use the sequential agent files under `.claude/agents/` when parallelized work is necessary.

## Most important accounting rule

Bills create receivables. Receipts create settlement allocations.

For each component:

`PAYABLE = BillLine amount`

`PAID = effective ReceiptAllocation amounts`

`BALANCE = PAYABLE - PAID`

For each receipt:

`ADVANCE = Receipt amount - effective allocations`

Do not overwrite bill amounts when payment is received.

## User-requested behavior

The MVP supports:

- configurable charge heads
- Fixed vs Variable charge behavior
- fixed examples such as Maintenance, Water and Sinking Fund
- variable examples such as parking and Non-Occupancy
- exact component references such as `MAIN-APR-2026` and `INT-APR-2026`
- partial payments
- split payments
- many receipts against one component
- one receipt against many components
- principal/interest allocation separately
- large outstanding balances
- unapplied/advance receipts
- reallocation using reversal + new allocation
- audit-safe history

## Example

April:

`MAIN-APR-2026 = ₹800`
`INT-APR-2026 = ₹120`

May receipt:

`₹300 -> MAIN-APR-2026`
`₹50 -> INT-APR-2026`

The system reports:

`MAIN: 800 payable / 300 paid / 500 balance`

`INT:  120 payable /  50 paid /  70 balance`

`Receipt: 350 received / 350 allocated / 0 advance`

## Setup

Requirements: Python 3.12+ and (recommended) PostgreSQL 16.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
copy .env.example .env           # cp on macOS/Linux, then set SECRET_KEY and DATABASE_URL
python manage.py migrate
python manage.py seed_demo --with-activity --second-society
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in as one of the demo users: `demo_ca`, `demo_operator`,
`demo_admin`, `demo_auditor` (one per role). `seed_demo` prints a generated password — set
`DEMO_PASSWORD` to choose your own. Change demo credentials before any shared use.

Database: put a PostgreSQL URL in `DATABASE_URL`. Leave it out and the app falls back to a local
SQLite file, which is fine for looking at the UI but does not exercise row locking.

`seed_demo` is idempotent. `--with-activity` adds issued bills, receipts, an advance and a bounced
cheque; `--second-society` adds a second society so the multi-society views have something to show.

## Checks

```bash
ruff check .                                    # lint
python manage.py check                          # Django system checks
python manage.py makemigrations --check --dry-run
pytest -q                                       # unit + acceptance tests
```

The concurrency tests (`society/test_concurrency.py`) need real row locking, so they are skipped
unless `DATABASE_URL` points at PostgreSQL.

## Working across several societies

A CA or auditor normally handles many societies. Every record belongs to exactly one society and
figures are never pooled:

- **My societies** (`/societies/`) lists every society you are a member of, with its outstanding,
  overdue, advance, latest period and what is waiting on someone. It appears in the sidebar as soon
  as you have more than one society.
- The **switcher** in the top bar changes the society you are working in and keeps you on the same
  page. Read-only roles can switch too; it only touches your session.
- Your role is **per society** — you can be the CA of one and the auditor of another.
- Every list, report, dropdown and URL is filtered to the active society; reaching another society's
  record by URL returns 404. Bill and receipt numbers restart per society.

Memberships are managed in Django admin (**Society memberships**: user + society + role).

## Dependent dropdowns

Pickers narrow each other: **wing → flat → payer** on the receipt screen, and **wing → flat** on the
flat, charge-rule, bill and receipt filters and on the charge-rule and flat-member forms. All options
are rendered in the HTML with `data-parent` markers and
`society/static/society/js/dependent.js` filters them in the browser, so the forms still work without
JavaScript. The filtering is a convenience only — the server re-validates every choice (for example a
payer who is not linked to the chosen flat is rejected even if the POST is hand-crafted).

## Billing workflow

Draft period → **Generate drafts** (maker) → review/confirm variable lines → **Submit for review** →
**Approve & issue** (checker, a different user when maker-checker is on) → **Lock**.

## Repository structure

- `society/models.py` — domain model
- `society/services.py` — all financial mutations (atomic, row-locked, audited)
- `society/permissions.py` — role/capability matrix enforced on every view
- `society/reports.py` — bill register, collection sheet, receipt register, statement, outstanding, advance, charge-head summary, dashboard
- `society/importer.py` — staged CSV import (upload → validate → preview → import → exception report)
- `society/compliance.py` — interest-rate ceiling table
- `society/tests.py`, `society/test_*.py` — acceptance tests (see `docs/IMPLEMENTATION_STATUS.md`)
- `society/templates/`, `society/static/` — server-rendered UI, vanilla JS allocation grid
- `docs/DEPLOYMENT.md` — deploying to a shared Ubuntu host (PostgreSQL + Gunicorn + Nginx)

## Production gap list

`runserver` is for development only. To put this on a server, follow `docs/DEPLOYMENT.md`
(PostgreSQL + Gunicorn + Nginx, alongside other projects on the same host). Before go-live,
complete and verify:

- the hardening checklist at the end of `docs/DEPLOYMENT.md`
- a backup/restore drill

- production PDF document generation and an immutable issued-document archive
- PostgreSQL backup/restore drill
- historical bill-register / collection-sheet import as financial records
- credit/debit note, waiver, write-off and refund workflows
- statutory registers, accounting/voucher/ledger module, bank reconciliation (Phase 2+)
- independent CA/CS/advocate verification of each society's legal configuration
