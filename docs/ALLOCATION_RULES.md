# Receipt Allocation / Knock-off Rules

## 1. Ledger principle

**Bill lines are receivables. Receipt allocations are settlement events.**

Never reduce, overwrite, or mutate the billed amount when a member pays.

For every receivable component:

`balance = payable - effective_paid`

where:

`effective_paid = allocation.amount - allocation_reversal.amount`

Only receipts with status `received` or `cleared` contribute to effective paid.

`bounced` and `cancelled` receipts remain in the audit trail but contribute zero to paid.

## 2. Component references

Every receivable component has a human-readable reference:

- `MAIN-APR`
- `WATER-APR`
- `SINK-APR`
- `2W-APR`
- `4W-APR`
- `NONOCC-APR`
- `INT-APR`

The database `component_code` also stores the year/calculation identifier so the same month name cannot collide across years or interest calculations.

## 3. Payable / Paid / Balance

`Payable` is the bill line amount.

`Paid` is derived from effective receipt allocations.

`Balance` is derived from payable minus paid.

Do not add a manually editable paid field.

Reports must show:

`Payable | Paid | Balance`

## 4. Fixed vs variable heads

Fixed heads (for example Maintenance, Water and Sinking Fund) populate automatically from the configured flat charge rule.

Variable heads (for example Parking or Non-Occupancy) are generated as draft lines and remain unconfirmed until the CA/operator reviews the quantity/rate/amount.

The Fixed/Variable classification is a software behavior. Statutory apportionment is stored separately on the charge head.

## 5. Member-directed allocation

When the payer/member indicates which debt should be settled, record the instruction and allow the operator to allocate to exact bill components.

Example:

April:

- `MAIN-APR` payable ₹800
- `INT-APR` payable ₹120

May receipt = ₹350

Allocation:

- ₹300 → `MAIN-APR`
- ₹50 → `INT-APR`

Result:

- `MAIN-APR`: Payable ₹800 / Paid ₹300 / Balance ₹500
- `INT-APR`: Payable ₹120 / Paid ₹50 / Balance ₹70
- Receipt: ₹350 / Allocated ₹350 / Advance ₹0

## 6. Part payments

A component may be paid through any number of receipts.

Example:

`MAIN-APR ₹800`

- R1 → ₹300
- R2 → ₹200
- R3 → ₹300

Final:

`Payable ₹800 / Paid ₹800 / Balance ₹0`

The bill's payable value is never changed.

## 7. One receipt to many components

A single receipt may settle:

- multiple charge heads;
- multiple months;
- multiple bills;
- principal and interest separately.

Cross-flat and cross-society allocation is rejected.

## 8. Advance / unapplied credit

If:

`receipt amount > effective allocations`

the residual is:

`Advance / Unapplied Credit`

Never create a negative receivable to represent the residual.

The receipt remains the source of the advance until a later allocation event applies it.

## 9. Auto knock-off

Supported product policies:

1. `member_directed` — operator must allocate explicitly.
2. `principal_then_interest` — oldest service period first; within it, principal components ordered by `knockoff_priority`, then that period's interest (e.g. MAIN-APR, WATER-APR, INT-APR, then MAIN-MAY …).
3. `oldest_due` — oldest due component first.

Automatic settlement is a product policy. It is not presented as a universal statutory appropriation rule.

When the society policy is `member_directed`, the system never auto-allocates silently: the operator must allocate manually or explicitly pick an automatic policy for that receipt. Automatic allocations are ordinary `ReceiptAllocation` rows with mode `auto`.

An explicit payer/member instruction always overrides automatic settlement.

## 10. Reallocation

Posted allocation events are immutable.

To correct a receipt:

1. lock receipt;
2. reverse the unwanted allocation amount;
3. create a new allocation to the correct component;
4. write reason, user and timestamp to the audit log.

Example:

Original:

`₹300 -> MAIN-APR`
`₹50 -> INT-APR`

Corrected target state:

`₹250 -> MAIN-APR`
`₹100 -> INT-APR`

System result:

- original ₹300 MAIN allocation remains;
- ₹50 reversal is added to that event;
- a new ₹50 INT allocation is added;
- effective MAIN paid becomes ₹250;
- effective INT paid becomes ₹100;
- receipt advance remains ₹0.

## 11. Reversal

A reversal is not a delete and is not a waiver.

It records a correction to an allocation only.

`reversal <= effective allocation remaining`

is mandatory.

A waiver/write-off/adjustment requires a separate business operation, approval and reason code; it must not be disguised as a receipt allocation reversal.

## 12. Receipt lifecycle

`Received -> Cleared`

or

`Received -> Bounced`

or

`Received -> Cancelled`

A bounced/cancelled receipt no longer counts toward paid balances. Its allocation history remains visible.

## 13. Interest interaction

Interest is a separate receivable line.

Principal allocation reduces the principal balance used by subsequent interest calculations.

Interest allocation does not reduce principal.

Default MVP rule:

- simple interest;
- principal only;
- actual payment dates;
- no interest-on-interest.

## 14. Concurrency

Allocation/reallocation operations run inside database transactions and lock the receipt and all relevant bill lines.

The application must reject:

- allocation > receipt's remaining capacity;
- allocation > target capacity;
- cross-flat target;
- cross-society target;
- non-receivable target;
- bounced/cancelled receipt;
- reversal > effective allocation.

## 15. Legal/policy context

The Indian Contract Act contains sections 59-61 on appropriation of payments. The system therefore captures explicit allocation instructions and preserves a transparent settlement trail.

The exact legal appropriation treatment for a cooperative housing society should be verified against the society's registered bye-laws, resolutions and current Maharashtra rules by its professional advisers.
