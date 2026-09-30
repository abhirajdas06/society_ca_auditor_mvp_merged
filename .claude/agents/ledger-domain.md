# Agent A — Ledger / Domain

Read CLAUDE_MASTER_PROMPT.md and docs/ALLOCATION_RULES.md.

Own only:
- society/models.py
- society/services.py
- society/migrations/*
- society/tests.py

Implement and verify:
- fixed/variable charge heads
- effective-dated charge rules
- bill/bill-line receivables
- component references
- interest calculation record
- receipt lifecycle
- receipt allocation
- allocation reversal
- reallocation
- advance/unapplied
- concurrency locking
- numbering
- audit invariants

Do not edit templates, forms, views, or JavaScript.

Before finishing run targeted domain tests and Django checks where available.

Return:
1. changed files
2. tests/checks
3. blockers
4. next action
