# Society CA/Auditor MVP — Research & Ledger Design

## Scope

This MVP is a Maharashtra cooperative-housing billing and collection application for a CA/auditor workflow. It is intentionally **ledger-first**: bills create receivable components; receipts settle those components through explicit allocation events.

This document separates:

- source-derived requirements from the supplied CA samples;
- current legal/regulatory observations;
- enterprise AR design patterns used as engineering references;
- product decisions made for the MVP.

It is not legal advice. The registered bye-laws, General Body resolutions and the society's CA/CS/advocate remain the authority for a particular society.

## 1. What the supplied CA samples establish

The September 2026 maintenance bill contains charge lines for Maintenance Charges, Water Charges, Sinking Fund, parking/non-occupancy in some cases, plus Interest, Adjustment, Principal Arrears, Interest Arrears and Total Due/Payable. The bill also identifies Unit Number, Member, Bill For, Wing, Floor, Bill Number, Bill Date and Due Date. Source: `September 2026.pdf`, page 1.

The Collection Sheet has the columns:

`Bill No | Unit No | Member | Bill Amt | Interest | Arrears | Total Amt | Amount Received | Payment Details | Balance Amount | Interest/Penalty`

The Bill Register breaks the current demand into charge heads and separates:

`Maintenance | Water | Sinking Fund | Non Occupancy | 2W | 4W | Amount | Interest | Bill Amount | Principal Arrear | Interest Arrear | Payable Amount | Balance Amount`

The MVP therefore treats each charge/interest amount as its own receivable component.

## 2. Legal / regulatory observations used by the MVP

The Maharashtra Co-operative Societies (Amendment) Rules, 2026 inserted Chapter XI-B (Rules 106C-1 to 106C-14) for cooperative housing societies. A July 2026 WIRC-ICAI technical article identifies Rule 106C-12 as the levy/apportionment rule and reports a 12% simple-interest ceiling and 10% non-occupancy charge, with specific apportionment bases for service charges, water, lift, parking, insurance, lease rent and funds.

The Maharashtra Cooperation Department's model-bye-laws page currently lists revised model bye-laws for tenant co-partnership cooperative housing societies dated 13 August 2026 and a Marathi draft dated 18 August 2026.

Engineering consequence:

- Do not hard-code the old 21% sample-bill interest note as the product default.
- Store the interest rate on the billing period so historical bills remain reproducible.
- Store effective dates and resolution references for configurable charge rules.
- Store an apportionment basis on each charge head; `Fixed` / `Variable` is a software calculation classification, not a statement of statutory apportionment.
- Block a new post-compliance-period rate above the current configured 12% ceiling in this MVP, pending CA/CS verification for the society's exact legal position.
- Preserve imported historical rates/bills as historical data instead of rewriting them.

## 3. Payment appropriation / knock-off research

The Indian Contract Act, 1872 contains sections 59-61 on appropriation of payments. Section 59 addresses cases where the debtor indicates the debt to be discharged; section 60 addresses cases where there is no indication; section 61 addresses cases where neither party appropriates.

For software architecture, this means the system should record the payer/member's allocation instruction whenever one exists instead of silently inventing an appropriation.

Enterprise receivables systems provide the same useful engineering pattern:

- SAP documents partial payments as separate open payment items linked to the original invoice; the original invoice remains open until later settlement.
- Oracle documents application of partial receipts to an open transaction.
- Microsoft Dynamics documents partial payments that leave the invoice open and allows automatic settlement policies.

These enterprise patterns are not sources of Indian law. They are engineering references for a reliable open-item design.

## 4. Chosen MVP ledger model

### Receivable

A `BillLine` is the source receivable component.

Examples:

- `MAIN-APR`
- `WATER-APR`
- `SINK-APR`
- `2W-APR`
- `NONOCC-APR`
- `INT-APR`

The internal database `component_code` includes year/calculation identifiers to keep keys unique, while the UI presents the short human-readable reference.

### Payment

A `Receipt` records the actual money received.

A `ReceiptAllocation` says:

`Receipt R1005 -> MAIN-APR -> ₹300`

or

`Receipt R1005 -> INT-APR -> ₹50`

### Correction

A `ReceiptAllocationReversal` records a correction without deleting the original allocation.

This permits:

`MAIN-APR allocation ₹300`

then:

`reversal ₹50`

then:

`new allocation ₹50 -> INT-APR`

The original event remains visible for audit.

### Advance / unapplied

`Receipt Amount - Effective Allocations = Advance / Unapplied Credit`

An advance is not a negative bill line. It remains attached to the receipt and can later be applied to newly issued or explicitly selected open components.

## 5. Payable / paid / balance invariant

For every receivable component:

`Paid = sum(effective receipt allocations)`

`Balance = max(Payable - Paid, 0)`

The software does not store an editable `paid_amount` field. Paid is derived from immutable allocation events plus reversal events so that the audit trail cannot silently diverge from the balance.

Reports always show:

`Payable | Paid | Balance`

## 6. Required examples

### Example A — May payment for April maintenance and interest

April:

- `MAIN-APR` Payable ₹800
- `INT-APR` Payable ₹120

May receipt:

`₹350`

Operator records:

- `₹300 -> MAIN-APR`
- `₹50 -> INT-APR`

Final:

- `MAIN-APR`: Payable 800 / Paid 300 / Balance 500
- `INT-APR`: Payable 120 / Paid 50 / Balance 70
- Receipt: 350 / Allocated 350 / Advance 0

### Example B — part payments over months

April `MAIN-APR` = ₹800.

Receipt 1 = ₹300
Receipt 2 = ₹200
Receipt 3 = ₹300

Final:

`Payable 800 / Paid 800 / Balance 0`

The bill amount never changes.

### Example C — overpayment / advance

April dues = ₹1,000.

Receipt = ₹1,500.

Allocation = ₹1,000.

Advance = ₹500.

Later, the ₹500 can be allocated to May components or another authorized open item for the same flat/account.

### Example D — reallocation

A receipt was initially:

`₹300 -> MAIN-APR`

Later the payer instruction is clarified:

`₹250 -> MAIN-APR`
`₹100 -> INT-APR`

The system creates a ₹50 reversal against the original MAIN allocation and a new ₹50 allocation to INT-APR. Nothing is deleted.

## 7. Automatic knock-off

Automatic settlement is a **society/product policy**, not a claim about mandatory statutory appropriation.

The MVP supports:

1. Member-directed/manual
2. Oldest bill: principal first, then interest
3. Oldest due component

Charge heads also have a `knockoff_priority` used only by automatic settlement.

Recommended seeded order:

- Maintenance 10
- Water 20
- Sinking Fund 30
- Non-Occupancy 40
- 2 Wheeler 50
- 4 Wheeler 60
- Interest 900

Explicit member-directed allocation always takes precedence.

## 8. Fixed vs variable charge heads

The application has two concepts that must remain separate:

### Billing calculation type

`Fixed`

The flat-level configured amount is carried into the bill automatically.

`Variable`

The system carries a default quantity/rate/amount into a **draft line that requires confirmation** before bill issue.

### Statutory / society apportionment basis

The charge head separately stores a basis such as:

- equal by units/flats
- carpet area
- sanctioned inlet/tap basis
- building equal
- actual measurement
- General Body approved rate
- manual/society-specific

This distinction avoids treating the words "fixed" and "variable" as statutory categories.

## 9. Interest engine

Default MVP rules:

- simple interest;
- principal lines only;
- no interest-on-interest;
- actual receipt allocation dates reduce the outstanding principal base for later days;
- separate interest receivable line;
- separate `InterestCharge` audit record;
- rate snapshot on the billing period.

If a principal line receives a payment on 30 April, an interest calculation through 31 May should calculate the post-30-April period on the reduced principal balance.

Historical rates can be preserved on imported historical records.

## 10. Why not residual-item accounting for every part payment?

The MVP intentionally uses an open-item + allocation-event model similar to the partial-payment pattern documented by SAP/Oracle/Microsoft.

Reason:

- the original society bill remains an immutable demand document;
- every payment remains independently traceable;
- a large outstanding can be settled across dozens of receipts;
- principal and interest remain independent;
- corrections can be represented as reversal events;
- a receipt can remain partly unapplied/advance;
- auditor can reconstruct the exact state as of a point in time.

A future full accounting ledger may represent financial postings separately from the operational society receivable ledger.

## 11. Acceptance invariants

The implementation is not complete until these are true:

1. A payment never changes a bill line's payable amount.
2. One bill line can be settled by many receipts.
3. One receipt can settle many bill lines.
4. A receipt can settle principal and interest independently.
5. A receipt can be partially applied and leave an advance.
6. An allocation can be corrected only through a reversal/new allocation event.
7. Cross-flat and cross-society allocations are rejected.
8. Bounced/cancelled receipts contribute zero effective paid.
9. Reversal cannot exceed the effective allocation remaining.
10. Concurrent allocation cannot over-apply the same receipt or bill line.
11. Variable charge lines cannot be issued until confirmed.
12. Historical component and interest information remains reproducible.
13. Dashboard/report paid totals subtract allocation reversals.
14. Interest calculations ignore reversed principal allocations.

## 12. Sources consulted

- Supplied: `September 2026.pdf`, `Collection Sheet.pdf`, `Bill Register.pdf`.
- Maharashtra Cooperation Department — Model Bye-Laws page, accessed September 2026.
- WIRC-ICAI, “Co-operative Housing Societies”, July 2026.
- India Code — Indian Contract Act, 1872, sections 59-61.
- SAP Help Portal — Partial Payments Versus Residual Items / Posting Partial Payments.
- Oracle Financials — Partial receipts.
- Microsoft Learn — Customer payments for a partial amount / settlement overview.
