# Agent B — CA Workflow UI

Read CLAUDE_MASTER_PROMPT.md and the existing models/services before editing UI.

Own only:
- society/views.py
- society/forms.py
- society/templates/*
- society/static/*

Implement:
- bill generation/review/issue/approval/lock screens
- fixed/variable charge review
- receipt entry
- component allocation grid
- live allocated/remaining/advance calculation
- reallocation workflow
- receipt history
- auditor read-only behavior in the UI

Do not change financial formulas or database schema. If a domain change is required, stop and report it to Agent A.

Do not rely on hidden buttons for authorization; preserve server-side permission checks.
