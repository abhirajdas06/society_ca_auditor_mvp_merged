# Agent D — QA / DevOps

Read CLAUDE_MASTER_PROMPT.md.

Own:
- .github/*
- Dockerfile
- docker-compose.yml
- pytest.ini
- deployment/health-check docs
- documentation cleanup

Verify:
- Python syntax
- Django checks
- migrations
- unit tests
- PostgreSQL test run where available
- Docker build
- CI workflow
- secret separation
- backup/restore documentation

Do not rewrite ledger logic. Report domain defects back to Agent A.
