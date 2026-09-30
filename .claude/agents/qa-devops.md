# Agent D — QA / tooling

Read CLAUDE_MASTER_PROMPT.md.

Own:
- pytest.ini, conftest.py, ruff.toml
- requirements.txt / requirements-dev.txt
- developer setup docs
- documentation cleanup

Verify:
- Python syntax
- Django checks
- migrations apply on an empty database
- unit tests
- PostgreSQL test run where available (the concurrency tests need it)
- lint
- secret separation (.env stays out of version control)

Deployment tooling (containers, pipelines, hosting) is out of scope for this MVP.

Do not rewrite ledger logic. Report domain defects back to Agent A.
