# Claude Code Index

Read in this order:

1. `CLAUDE_MASTER_PROMPT.md`
2. `docs/MVP_MERGED_SCOPE.md`
3. `docs/SOURCE_SAMPLE_MAPPING.md`
4. `docs/ALLOCATION_RULES.md`
5. `docs/LEGAL_NOTES.md`
6. Relevant agent file under `.claude/agents/`

Do not load every source file into context at once.
Use targeted inspection.
Run tests after focused edits.

Code map (Phase 1):

- `society/models.py` — domain model; `society/services.py` — every financial mutation
- `society/permissions.py` — role → capability matrix, `@require(...)` decorator
- `society/reports.py` — read-only report builders; `society/importer.py` — staged CSV import
- `society/compliance.py` — interest ceiling table (append-only)
- Tests: `society/tests.py` (ledger), `test_security.py`, `test_reports.py`, `test_import.py`, `test_concurrency.py` (PostgreSQL only)
- Acceptance matrix → test mapping: `docs/IMPLEMENTATION_STATUS.md`
