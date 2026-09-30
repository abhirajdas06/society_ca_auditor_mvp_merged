# Claude Code Repository Context

Read `CLAUDE_MASTER_PROMPT.md` first.

Core invariants:
- BillLine = receivable demand
- Receipt = money received
- ReceiptAllocation = settlement event
- ReceiptAllocationReversal = correction event
- Advance = receipt residual

Never mutate billed demand to record payment.
Use Decimal, transaction.atomic(), select_for_update(), and controlled document sequences.

Explicit payer/member allocation takes precedence over automatic knock-off policy.

Fixed/Variable charge behavior is separate from statutory apportionment.

Interest is a separate receivable component and must remain historically reproducible.

Use reversal + reallocation; never delete posted financial history.

Run the smallest relevant tests after every meaningful change, then the full suite at phase boundaries.
