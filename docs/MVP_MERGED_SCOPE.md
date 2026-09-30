# Merged MVP Scope

## Phase 1 included

### Master data
- Society
- Wing
- Flat
- Member
- Joint Member / Nominee relationship
- Charge Heads
- Flat Charge Rules

### Billing
- Billing Period
- Draft bill generation
- Fixed charge generation
- Variable charge review
- Principal receivable lines
- Separate interest receivable
- Historical/effective configuration
- Period approval/lock

### Collections
- Receipt
- Payment mode
- Cheque/bank reference
- Component allocation
- Part payment
- Split payment
- Multi-month settlement
- Advance/unapplied receipt balance
- Reallocation through reversal + new allocation
- Bounce/cancel lifecycle

### Reports
- Bill Register
- Collection Sheet
- Receipt Register
- Flat Statement
- Outstanding
- Advance/Unapplied Register

### Governance
- CA / Accountant
- Operator
- Society Admin
- Auditor read-only
- Audit log
- Maker/checker foundation

### Infrastructure
- PostgreSQL
- migration files
- seed command
- pytest suite and ruff lint

Deployment tooling is deliberately out of scope for this MVP; the project runs from a virtualenv.

## Core financial invariants

1. BillLine amount is demand.
2. Receipt is money received.
3. ReceiptAllocation is settlement.
4. ReceiptAllocationReversal is correction.
5. Advance is receipt residual.
6. Paid is derived, never manually entered.
7. Balance is derived, never manually entered.
8. Historical allocations are not deleted.
9. Cross-flat and cross-society allocations are rejected.
10. Financial mutations are transaction-safe.
