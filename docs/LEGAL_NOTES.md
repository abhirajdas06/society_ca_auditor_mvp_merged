# Maharashtra Housing Society Compliance Notes — September 2026

This repository is a software MVP, not legal advice. The society's registered bye-laws, General Body resolutions and the advice of its CA/CS/advocate control the final configuration for each society.

## 2026 rule framework

The Maharashtra Co-operative Societies (Amendment) Rules, 2026 were notified on 18 June 2026 and inserted Chapter XI-B (Rules 106C-1 to 106C-14) for co-operative housing societies, according to the July 2026 WIRC-ICAI technical summary.

Rule 106C-12 is relevant to billing. The WIRC-ICAI summary reports mandatory apportionment bases for several charge categories and a maximum 12% simple-interest rate on defaults, with non-occupancy charges at 10% of service charges.

The Maharashtra Cooperation Department's current model-bye-laws page lists revised model bye-laws for tenant co-partnership cooperative housing societies dated 13 August 2026 and a Marathi draft dated 18 August 2026.

## Software treatment of those rules

The MVP does not hard-code one universal charge calculation.

Every charge head stores:

- Fixed / Variable calculation type;
- apportionment basis;
- effective configuration through flat charge rules;
- General Body resolution reference/date;
- knock-off priority for automatic settlement only.

`Fixed` means the configured amount can be carried into a bill automatically.

`Variable` means the system creates a draft quantity/rate/amount that must be reviewed and confirmed before bill issue.

Those terms are **software behavior**, not statutory categories.

## Interest

The supplied September 2026 sample bills state 21% p.a. interest on outstanding dues. That is historical source information and must not be treated as the current statutory default.

The MVP stores an interest-rate snapshot on every BillingPeriod and validates new post-compliance-date billing against the current 12% simple-interest ceiling used by the MVP. The exact applicable rate and transition treatment must still be checked for each society against its current registered bye-laws/resolutions and professional advice.

Historical imported bills can preserve their legacy rate and calculation data for audit reconstruction.

## Payment appropriation

Sections 59-61 of the Indian Contract Act address appropriation of payments. The product therefore records payer/member allocation instructions and provides explicit allocation rather than silently rewriting debt balances.

The automatic allocation policy is a product workflow. It is not presented as a mandatory legal appropriation rule.

## Recovery / audit evidence

The 2026 Rule 106C-14 procedure for recovery under section 154B-29, as summarised by WIRC-ICAI, refers to an up-to-date certified account/ledger, supporting resolutions and demand notice among the evidence for a recovery application.

This is one reason the MVP keeps:

- bill component history;
- receipt history;
- allocation history;
- reversal/reallocation history;
- user/time audit logs;
- payable/paid/balance reconstruction.

## Source hierarchy for production review

1. Maharashtra Co-operative Societies Act, Rules and applicable notifications.
2. Society's registered bye-laws.
3. Current Registrar / Cooperation Department directions.
4. Society's valid General Body / committee resolutions.
5. CA/CS/advocate interpretation for the specific society.
6. Legacy spreadsheets and PDFs as historical operating records, not as statutory authority.
