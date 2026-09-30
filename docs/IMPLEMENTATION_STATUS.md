# Implementation status — Phase 0 + Phase 1

Last updated: 18 September 2026.

## Acceptance matrix (CLAUDE_MASTER_PROMPT.md §32) → tests

| # | Requirement | Test(s) |
|---|---|---|
| A | Fixed charge generation, effective-dated rules | `tests.FixedChargeGenerationTests` |
| B | Variable lines start unconfirmed, block issue, confirmation audited | `tests.VariableChargeTests` |
| C | ₹300 against ₹800 leaves ₹500 | `tests.AllocationTests.test_partial_receipt_leaves_balance` |
| D | ₹300 MAIN-APR + ₹50 INT-APR from one ₹350 receipt | `tests.AllocationTests.test_split_receipt_principal_and_interest` |
| E | Three receipts settle one receivable | `tests.AllocationTests.test_three_receipts_fully_settle_one_component_as_separate_records` |
| F | One receipt settles several months | `tests.AllocationTests.test_one_receipt_settles_multiple_months` |
| G | Excess becomes advance; later allocation reduces it | `tests.AllocationTests.test_excess_becomes_advance_and_later_allocation_reduces_it` |
| H | Reversal + replacement, nothing deleted | `tests.ReallocationTests` |
| I | Bounce removes PAID, history remains | `tests.ReceiptLifecycleTests` |
| J | Concurrent allocations cannot over-allocate; unique numbers | `test_concurrency` (runs on PostgreSQL only — CI) |
| K | Payment date changes interest base; separate line; no interest-on-interest; no double-charging; compliance ceiling | `tests.InterestTests` |
| L | Bill register / collection sheet / statement / advance register tie out | `test_reports.ReportTieOutTests` |
| M | Auditor cannot POST; operator cannot bypass approval via URL; society isolation | `test_security.SecurityTests` |
| N | Migrations on empty PostgreSQL, seed loads, no duplicate numbers | CI `migrate` + `seed_demo --with-activity` steps; `tests.NumberingTests` |

## Role matrix (enforced in `society/permissions.py`)

| Capability | CA | Operator | Society Admin | Auditor |
|---|:-:|:-:|:-:|:-:|
| View dashboards, bills, receipts, reports, statements | ✓ | ✓ | ✓ | ✓ |
| Audit log | ✓ | | ✓ | ✓ |
| Flats, wings, members | ✓ | ✓ | ✓ | |
| Society settings, charge heads, charge rules | ✓ | | ✓ | |
| Create period, generate drafts, confirm variable lines, submit for review (maker) | ✓ | ✓ | | |
| Approve & issue, lock, issue/cancel individual bills (checker) | ✓ | | ✓ | |
| Record / clear / bounce receipts, allocate advance | ✓ | ✓ | | |
| Cancel receipt, reverse / reallocate allocations | ✓ | | | |
| CSV import | ✓ | | ✓ | |

Auditors are refused on every non-GET request regardless of capability. With
`Society.enforce_maker_checker` on (default), the user who generated a period cannot approve or issue it.

## Multi-society handling

A CA or auditor works across several societies, so the active society is part of the session, not of
the login:

- `/societies/` (`reports.portfolio`) summarises every society the user belongs to — outstanding,
  overdue, advance, latest period, variable lines awaiting review, receipts awaiting clearance.
  Totals are a sum of separate per-society figures; no query ever spans societies.
- The top-bar switcher posts to `switch_society` with a `next` URL (validated with
  `url_has_allowed_host_and_scheme`) so the user stays on the page they were reading.
- `switch_society` is declared `@require("view", session_only=True)`: it changes only the user's
  session, so read-only auditors can switch while still being refused every data mutation.
- Roles are per membership, so the same user can be CA of one society and auditor of another.
- Isolation is enforced by `permissions.resolve_society` plus society-filtered lookups in every view;
  `test_multisociety` and `test_security` cover cross-society 404s and dropdown contents.

## Dependent dropdowns

`forms.DependentSelect` renders each option with the parent values it belongs to
(`data-parent="3"`) and points the select at its parent (`data-depends-on="id_wing"`);
`static/society/js/dependent.js` filters options in the browser and shows an "n of m shown" hint.
Chains: wing → flat → payer (receipt), wing → flat (charge rule, flat-member, and the flat/bill/
receipt/charge-rule list filters). Because every option is in the HTML, the forms degrade to plain
dropdowns without JavaScript, and the server re-validates independently — the wing field is a filter
only and is never saved.

## Design decisions made in this phase

- **Society scoping.** `ChargeHead`, `Member` and `Bill` now carry a `society` FK (migrations 0004–0006 backfill existing rows). Bill and receipt numbers are unique per society.
- **Interest engine.** Interest is calculated per principal line in segments of constant outstanding principal (`InterestSegment`), from `due_date + grace` (or from the end of the last billed interest segment) to the period's `interest_calculation_date`. This prevents the same days being charged twice across consecutive bills. Interest lines are never used as a base; enabling `interest_on_interest` blocks generation.
- **Compliance ceiling.** `society/compliance.py` holds the 12% simple-interest ceiling (effective 18 June 2026, per the project legal notes). It applies from the earlier of that date and the society's `compliance_effective_date`, so configuration can bring the ceiling forward but never postpone it. Pre-compliance periods keep their legacy rate snapshot.
- **Receipt lifecycle.** Cash receipts are cleared on creation; other modes start as *Received*. Bounce/cancel are allowed only from *Received* and require a reason. Cheques need a cheque number; NEFT/RTGS/UPI/bank transfer need a UTR.
- **Charge rules.** Overlapping active rules for the same flat and head are rejected. Once a rule has been billed, only its end date / active flag can be edited — a new rate is a new effective-dated rule.
- **Bill cancellation.** Draft bills can be cancelled by the maker. Issued bills need an approver, an open period, no effective allocations, and no later interest computed on them.
- **Admin.** Django admin is read-only for all financial models so the service layer cannot be bypassed.

## Known gaps (not blockers for Phase 1)

- Historical bill-register / collection-sheet import as financial records (the import framework supports society register and charge rules only).
- Backdated receipts entered after interest was billed do not retro-adjust that interest; that correction belongs to the future credit-note workflow.
- PDF bill/receipt generation and immutable document archive.
- Waiver, write-off, credit/debit note and refund workflows (Phase 2 by design).
