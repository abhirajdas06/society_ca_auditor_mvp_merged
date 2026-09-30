"""Compliance guard configuration.

This is not legal advice. Each entry records a ceiling the MVP enforces for NEW billing, the date
it applies from and the source it was taken from (see docs/LEGAL_NOTES.md). Historical periods
billed before the society's compliance_effective_date keep their legacy rate snapshot.

Add a new entry (never edit an old one) when the project's legal notes change.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InterestCeiling:
    effective_from: datetime.date
    max_rate_pa: Decimal
    source: str


INTEREST_CEILINGS = (
    InterestCeiling(
        effective_from=datetime.date(2026, 6, 18),
        max_rate_pa=Decimal("12.00"),
        source="MCS (Amendment) Rules 2026, Rule 106C-12, per WIRC-ICAI July 2026 summary — verify per society",
    ),
)


def interest_ceiling(bill_date, society_compliance_date):
    """Ceiling applicable to a bill dated bill_date, or None for pre-compliance historical billing.

    Conservative by design: the ceiling starts at the EARLIER of the rule's date and the society's
    configured compliance date. A society may apply the ceiling sooner, but configuration can never
    postpone it past the rule's own date.
    """
    applicable = None
    for ceiling in INTEREST_CEILINGS:
        start = min(ceiling.effective_from, society_compliance_date) if society_compliance_date else ceiling.effective_from
        if bill_date >= start:
            applicable = ceiling
    return applicable
