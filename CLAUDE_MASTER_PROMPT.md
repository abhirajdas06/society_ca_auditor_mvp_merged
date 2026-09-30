# Society CA / Auditor MVP — Claude Code Master Prompt

You are implementing and hardening a CA-first cooperative housing society billing and receivables system for the Indian/Maharashtra market.

Use this repository as the source of truth for architecture. Do not redesign the core ledger casually. The most important requirement is that billed demand, payments received, payment allocation, advance/unapplied money, and outstanding balances remain independently auditable.

## 1. Objective

Build a production-oriented MVP using:

- Python
- Django 5.2
- PostgreSQL
- Django templates
- HTML5
- CSS3
- vanilla JavaScript
- Django ORM
- transaction.atomic()
- select_for_update()
- GitHub Actions CI/CD
- Docker for local/staging/production parity

Do NOT introduce React, Vue, Angular, Django REST Framework, or a SPA in this MVP.

The application is for:

- CA / accountant
- Society administrator
- Collection/operator users
- Auditor

Resident portal, payment gateway, WhatsApp/SMS automation, full double-entry accounting ERP, and mobile app are later phases.

## 2. Primary source requirements

The user's supplied examples use:

- society master
- wing / unit
- member / joint member
- maintenance charges
- water charges
- sinking fund
- non-occupancy charges
- 2-wheeler parking
- 4-wheeler parking
- interest
- principal arrears
- interest arrears
- payable amount
- receipt/payment details
- collection sheet
- bill register

The supplied September 2026 sample also shows the operational relationship between a bill, arrears, interest and a receipt. Preserve this terminology in the UI and reports where practical.

## 3. Core accounting model

Never model payment by changing a bill amount.

Use this model:

Bill -> BillLine (receivable demand)
Receipt -> ReceiptAllocation (settlement event)
ReceiptAllocation -> ReceiptAllocationReversal (correction event)
Receipt residual -> Advance / Unapplied Credit

Financial invariants:

PAYABLE = original issued BillLine amount
PAID = effective receipt allocations
BALANCE = PAYABLE - PAID
ADVANCE = receipt amount - effective receipt allocations

For reporting, clamp display balance at zero only where overpayment is not allowed; otherwise preserve the signed accounting state separately. Never destroy evidence of over-allocation attempts.

A BillLine's payable amount is immutable after issue except through a formal credit/debit adjustment workflow that is outside ordinary receipt processing.

## 4. Fixed vs variable charge heads

The CA must be able to create Charge Heads.

Each ChargeHead must contain at least:

- code
- name
- active flag
- charge type: fixed / variable
- apportionment basis
- ledger code
- system code
- resolution reference
- resolution date
- automatic knock-off priority

Examples from the user's workflow:

Fixed:
- Maintenance
- Water
- Sinking Fund

Variable:
- Non Occupancy
- 2 Wheeler Parking
- 4 Wheeler Parking
- Other society-specific charges

IMPORTANT:

Fixed/Variable is an application behavior, NOT a statutory apportionment category.

Therefore retain a separate apportionment-basis field.

For fixed heads:
- FlatChargeRule stores configured monthly amount.
- Draft bill generation pulls the effective amount.

For variable heads:
- store quantity, rate, amount
- generate a draft line
- require review/confirmation before bill issue
- keep confirmation user/date in the audit trail

## 5. Effective-dated charge configuration

FlatChargeRule must be effective-dated.

The system must select the rule applicable to BillingPeriod.period_start.

Never silently use a future or expired rule.

Support historical changes such as:

April: Maintenance ₹800
May: Maintenance ₹850

without changing the April bill after it has been issued.

## 6. Bill component identity

Every receivable component must have a stable human-readable reference.

Examples:

MAIN-APR-2026
WATER-APR-2026
SINK-APR-2026
2W-APR-2026
4W-APR-2026
NONOCC-APR-2026
INT-APR-2026

Do not rely on display month strings alone. The database identity must include year/period and bill line primary key.

## 7. Receipt design

A Receipt stores money received.

Minimum fields:

- receipt number
- receipt date
- society
- flat
- payer/member
- payment mode
- amount
- cheque number if applicable
- cheque date if applicable
- bank / branch
- UTR / transaction reference if applicable
- narration
- status
- created by
- cleared/bounced/cancelled timestamps and user where applicable

Supported payment modes:

- Cash
- Cheque
- NEFT
- RTGS
- UPI
- Bank Transfer
- Other

Receipt lifecycle:

RECEIVED -> CLEARED
RECEIVED -> BOUNCED
RECEIVED -> CANCELLED

Bounced/cancelled receipts no longer contribute to PAID, but their historical allocations remain visible.

## 8. Component-level receipt allocation

A receipt may be mapped to exact bill components.

Example:

APRIL
MAIN-APR = ₹800
INT-APR = ₹120

MAY RECEIPT = ₹350

Operator can record:

₹300 -> MAIN-APR
₹50  -> INT-APR

Result:

MAIN-APR: payable 800 / paid 300 / balance 500
INT-APR:  payable 120 / paid  50 / balance  70
Receipt:   amount 350 / allocated 350 / advance 0

This is the central MVP behavior.

## 9. Partial payments

The system must support unlimited practical part-payments against one BillLine.

Example:

MAIN-APR ₹800

R001 -> ₹300
R002 -> ₹200
R003 -> ₹300

Final:

payable ₹800
paid ₹800
balance ₹0

Do not merge these receipts into one record.

## 10. Split payments

One receipt may allocate across:

- multiple charge heads
- principal and interest
- multiple months
- multiple bills belonging to the same flat/account

Cross-flat and cross-society allocation must be rejected.

## 11. Advance / unapplied credit

If receipt amount exceeds allocations:

ADVANCE = Receipt amount - effective allocated amount

Never create a negative BillLine.

Example:

Receipt ₹1,500
Allocated ₹1,000
Advance ₹500

The ₹500 remains linked to that receipt until a later allocation event.

Report advance separately.

## 12. Automatic knock-off

Support these policies:

1. member_directed
2. principal_then_interest
3. oldest_due

Rules:

- explicit payer/member allocation takes precedence
- automatic policy must be configurable per society
- automatic policy must never be presented as universal legal appropriation

For principal_then_interest:

1. oldest open principal components
2. within the same priority, use ChargeHead.knockoff_priority
3. then interest components

All automatic allocations must still create ordinary ReceiptAllocation rows so they remain auditable.

## 13. Reallocation / correction

Allocation records are append-only from an audit perspective.

Never delete a posted allocation.

To correct:

1. lock receipt
2. reverse the incorrect allocation amount
3. create the corrected allocation
4. record reason
5. record user
6. record timestamp

Example:

Original:
₹300 -> MAIN-APR
₹50 -> INT-APR

Corrected:
₹250 -> MAIN-APR
₹100 -> INT-APR

Persist:
- original ₹300 allocation
- ₹50 reversal against it
- new ₹50 INT allocation

Final effective state:
MAIN paid ₹250
INT paid ₹100

## 14. No silent journal corrections

The following are separate future workflows, not disguised receipt operations:

- waiver
- write-off
- credit note
- debit note
- charge correction
- refund

Do not implement these by deleting allocations or editing old BillLines.

## 15. Interest engine

Interest must be a separate receivable line.

Example:

MAIN-APR
INT-APR

Interest rules for MVP:

- simple interest
- principal only
- interest-on-interest disabled by default
- actual principal payment dates affect subsequent interest base
- rate snapshot stored per BillingPeriod
- calculation record stores formula inputs and result

Store enough audit data to answer:

- what principal was outstanding?
- what dates were used?
- what rate was used?
- what payment reduced the principal?
- how much interest was created?

Do not create interest merely because a bill exists. Use an explicit calculation date/rule.

## 16. Compliance configuration

Do not hard-code one legacy interest rate globally.

Historical society records may contain older rates. Preserve them for reproducibility.

New billing periods must use configurable society-approved settings and a compliance guard based on the current project legal notes.

The application is not legal advice. Where law/bye-law interpretation is uncertain, encode configuration + effective date + approval/resolution reference, and do not guess.

## 17. Arrears

Opening arrears shown on a later bill are presentation/snapshot data that point to underlying historical receivables.

Do NOT duplicate the same economic debt as a new receivable every month.

A flat statement should be capable of reconstructing:

Opening outstanding
+ current billed components
+ interest billed
- effective payments
+/- approved adjustments
= closing outstanding

## 18. Flat statement

The statement is the most important auditor view.

Show chronological open-item activity:

Date
Document
Component
Debit/Payable
Credit/Paid
Balance
Receipt
Narration

The auditor should be able to trace a current balance back to its original bill and all receipt allocations.

## 19. Roles

CA / Accountant:
- full operational write access within assigned society scope

Operator:
- data entry
- receipt creation
- collection workflow
- permitted master maintenance
- no final approval where maker-checker is required

Society Admin:
- society configuration
- members/flats
- charge heads
- charge rules
- master data

Auditor:
- read-only
- reports
- statements
- audit log
- document history

Permissions MUST be enforced server-side.
Hidden buttons are not security.

## 20. Maker-checker

Billing workflow should support:

Draft -> Generated -> Review -> Approved -> Locked

Issued/locked financial documents are not casually editable.

Implement approval metadata:
- approved by
- approved at
- locked at

## 21. Audit log

Log at minimum:

- bill creation
- bill issue
- bill cancellation
- variable line confirmation
- receipt creation
- receipt status change
- allocation
- allocation reversal
- reallocation
- interest calculation
- period approval
- period lock
- master-data changes affecting billing

Audit entry should capture:

- actor
- action
- model
- object ID
- timestamp
- structured details where practical

## 22. Concurrency / transaction safety

All financial state-changing operations must use transaction.atomic().

Use select_for_update() on:

- receipt row
- relevant BillLine rows
- society sequence row when assigning bill/receipt numbers

Reject:

- allocation > receipt remaining
- allocation > target remaining capacity
- reversal > effective allocation
- cross-flat allocation
- cross-society allocation
- cancelled/bounced receipt allocation
- non-receivable target

Never use COUNT(*) + 1 for document numbering.

## 23. Reports

MVP reports:

1. Bill Register
2. Collection Sheet
3. Receipt Register
4. Flat Statement
5. Outstanding by Flat
6. Advance / Unapplied Register
7. Charge-head summary

Reports must show or derive:

PAYABLE | PAID | BALANCE

and receipt reports must show:

RECEIPT AMOUNT | ALLOCATED | ADVANCE

The reports should remain consistent with the source sample terminology where possible.

## 24. Dashboard

Include:

- total flats
- active members
- current billing period
- current billed amount
- total outstanding
- total effective collections
- total advance/unapplied
- overdue flats
- recent receipts
- receipts awaiting clearance

Never calculate financial totals from manually typed dashboard counters.

## 25. UI requirements

Use server-rendered Django templates.

UI goals:

- CA-friendly
- fast data entry
- keyboard friendly
- clear monetary columns
- strong filtering
- no decorative SPA complexity

Receipt allocation screen must have a grid with:

Reference | Bill Date | Due Date | Component | Payable | Paid | Balance | Allocate Now

Show live:

Receipt Amount
Allocated
Remaining / Advance

Do not permit total allocation greater than receipt amount.

## 26. Data import

Provide import foundations for the supplied CA spreadsheets.

Expected source concepts:

- society register
- bill register
- collection sheet
- maintenance bills / receipts

Import should be staged:

1. upload
2. validate
3. preview
4. import
5. exception report

Never silently overwrite an existing master or financial record.

## 27. Sample validation target

Use the supplied Chandresh Residency-style structure as the reference shape.

The sample has:
- unit numbers such as A001, A002, A101, etc.
- member/joint member information
- variable current charges
- interest
- principal arrears
- interest arrears
- payable totals
- receipt details

Use this structure for demo data and end-to-end tests, but do not hard-code the society as the only possible society.

## 28. CI/CD

Git strategy:

main
 develop
 feature/*
 hotfix/*

Pipeline:

lint / syntax
-> unit tests
-> Django checks
-> migration check
-> build
-> staging deploy
-> UAT
-> production deploy

Production deployments must be gated.

Do not store secrets in Git.

## 29. Phase plan

PHASE 0 — foundation
- Django project
- PostgreSQL
- auth/roles
- base layout
- CI/CD
- Docker
- audit log base

PHASE 1 — billing and collections MVP
- society/wing/flat/member
- charge heads fixed/variable
- effective-dated charge rules
- billing periods
- bills/bill lines
- interest engine
- component-level receipt allocation
- part payments
- advances
- reallocation/reversal
- reports
- dashboard

PHASE 2 — accounting
- chart of accounts
- ledgers
- vouchers
- expense/vendor
- bank reconciliation
- trial balance
- income & expenditure
- receipts & payments
- balance sheet

PHASE 3 — society operations
- notices
- complaints
- NOC
- transfer
- nomination
- parking
- document repository
- resident portal
- online payment

PHASE 4 — audit platform
- statutory registers
- audit workpapers
- observations
- rectification
- annual audit workflow
- AGM/committee resolutions

Do not build Phase 2-4 into Phase 1 unless necessary to preserve a clean data model.

## 30. Agent strategy

Maximum 4 agents, sequential where files overlap.

Agent A — Ledger/Domain
Own:
- models.py
- services.py
- migrations
- tests

Agent B — CA UI
Own:
- views.py
- forms.py
- templates
- static

Agent C — Reports/Import/Seed
Own:
- management commands
- import tooling
- report-specific code
- fixtures

Agent D — QA/DevOps
Own:
- CI
- Docker
- pytest config
- deployment docs
- health checks

Merge order:
A -> B -> C -> D

Do not allow overlapping agents to edit the same core files simultaneously.

## 31. Token-efficiency rules for Claude Code

Before coding:
- read README.md once
- read CLAUDE_MASTER_PROMPT.md once
- read only the domain docs needed for the active phase

Do not dump the whole repository into context.

Before editing a file:
- locate the smallest relevant section
- inspect only necessary context
- patch targeted code

After editing:
- run the smallest relevant tests first
- then run the broader suite at phase boundaries

Do not repeatedly explain the full architecture in every response.

Report only:
1. changed files
2. tests/checks run
3. blockers
4. next step

## 32. Acceptance test matrix

The MVP is not complete until these pass:

A. Fixed charge generation
- fixed monthly charge appears correctly in the draft bill
- effective-dated rule changes apply from the correct period

B. Variable charge control
- variable line starts unconfirmed
- bill cannot be issued while required review is incomplete
- confirmation is audited

C. Partial receipt
- ₹300 payment against ₹800 receivable leaves ₹500 balance

D. Split receipt
- ₹300 MAIN-APR + ₹50 INT-APR from one ₹350 receipt works

E. Multiple receipts
- 3 receipts can fully settle one receivable

F. Multi-month
- one receipt can settle multiple months for one flat

G. Advance
- excess receipt becomes unapplied/advance
- later allocation reduces advance

H. Reallocation
- no posted allocation is deleted
- reversal + replacement allocation reconstructs final state

I. Bounced cheque
- paid balance is reversed by receipt status effect
- history remains

J. Concurrency
- two simultaneous allocations cannot over-allocate a receipt or BillLine

K. Interest
- actual principal payment date changes future interest base
- interest is separate from principal
- no interest-on-interest by default

L. Reporting
- Bill Register ties to BillLines
- Collection Sheet ties to receipts/allocation
- Flat Statement reconstructs the balance
- Advance Register ties to receipt residuals

M. Security
- auditor cannot POST financial mutations
- operator cannot bypass approval restrictions through direct URLs

N. Migration/data integrity
- all migrations apply cleanly on empty PostgreSQL
- seed data loads cleanly
- no duplicate document numbers

## 33. Definition of done

A task is done only when:

- implementation is complete
- migrations are included
- server-side permissions are enforced
- tests are added for financial invariants
- relevant reports remain consistent
- audit logging exists for the state change
- Django checks pass
- targeted tests pass
- documentation is updated

Do not mark a feature complete merely because its page renders.

## 34. Final output format

At the end of a phase, return exactly:

### Changed
- files

### Verified
- commands/checks
- test results

### Blockers
- only actual blockers

### Next
- one recommended next development action

Do not claim a test passed unless it was actually executed.
