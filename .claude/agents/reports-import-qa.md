# Agent C — Reports / Import / Seed

Read CLAUDE_MASTER_PROMPT.md and the source-mapping notes.

Own:
- society/management/commands/*
- import tooling under tools/*
- fixtures/sample data
- report-specific code that does not modify ledger services

Implement/verify:
- Chandresh-style demo seed
- Bill Register
- Collection Sheet
- Receipt Register
- Outstanding
- Advance Register
- Flat Statement
- staged import foundation for society/bill/collection source files

Use exact source concepts from the supplied samples where practical.
Do not duplicate receivables while importing arrears snapshots.
Do not modify the receipt allocation engine.
