# Claude Code Agent Plan

Use a maximum of four agents. Execute them sequentially when they touch adjacent code.

## Agent A — Ledger / Domain

Own:

- `society/models.py`
- `society/services.py`
- `society/migrations/*`
- `society/tests.py`

Tasks:

- validate fixed/variable charge model
- validate period interest snapshot
- validate receipt allocation/reversal/reallocation
- validate advance/unapplied
- validate concurrent locking
- add invariant tests

Output only:

1. files changed
2. tests run
3. failures/blockers
4. next action

Do not touch templates or JavaScript.

## Agent B — CA Workflow UI

Own:

- `society/views.py`
- `society/forms.py`
- `society/templates/*`
- `society/static/*`

Tasks:

- receipt allocation grid
- exact component references
- reallocation UI
- reversal UI
- fixed/variable charge UX
- review/issue/approve/lock UX
- read-only auditor behavior

Do not change accounting formulas or database structure.

## Agent C — Reports / Seed / Import

Own:

- `society/management/commands/*`
- report templates if Agent B is already done
- import helper scripts under `tools/`
- sample-driven fixtures
- report tests that do not alter ledger services

Tasks:

- Chandresh demo seed
- optional Excel import skeleton
- Bill Register
- Collection Sheet
- Receipt Register
- Outstanding
- Advance
- Flat Statement

Do not modify the receipt allocation engine.

## Agent D — QA / DevOps

Own:

- CI configuration
- Docker files
- pytest config
- deployment docs
- smoke/health checks
- documentation cleanup

Tasks:

- run compile checks
- run tests in a network-enabled Django/PostgreSQL environment
- verify migrations
- verify Docker image/build
- verify CI
- check secrets/settings separation
- document backup/restore

Do not rewrite ledger logic.

## Merge order

`A -> B -> C -> D`

If a later agent needs a core-domain change, stop and send the issue back to Agent A instead of editing the same file concurrently.
