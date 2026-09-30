"""Financial business logic.

Every state change to billed demand, receipts or allocations goes through this module.
Invariants (see CLAUDE_MASTER_PROMPT.md §3):

    PAYABLE = issued BillLine.amount (never reduced by a receipt)
    PAID    = effective ReceiptAllocation amounts - their reversals
    BALANCE = PAYABLE - PAID
    ADVANCE = Receipt.amount - effective allocations

All mutations run inside transaction.atomic() and lock rows in a fixed order:
Society (sequence) -> Receipt -> BillLine (ascending id). Keeping that order avoids deadlocks.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import compliance
from .models import (
    AuditLog,
    Bill,
    BillingPeriod,
    BillLine,
    BillOpeningItem,
    ChargeHead,
    FlatChargeRule,
    InterestCharge,
    InterestSegment,
    Receipt,
    ReceiptAllocation,
    ReceiptAllocationReversal,
    Society,
)

TWOPLACES = Decimal("0.01")
ZERO = Decimal("0.00")
EFFECTIVE = Receipt.EFFECTIVE_STATUSES
DEC = DecimalField(max_digits=16, decimal_places=2)
AUTO_POLICIES = (Society.PRINCIPAL_THEN_INTEREST, Society.OLDEST_DUE)


def money(value):
    return Decimal(value or 0).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def parse_money(value, field="amount"):
    try:
        return money(Decimal(str(value).strip().replace(",", "")))
    except Exception:
        raise ValidationError(f"Invalid {field}: {value!r}")


def simple_interest(principal, annual_rate, days):
    return money(Decimal(principal) * Decimal(annual_rate) / Decimal("100") * Decimal(days) / Decimal("365"))


def audit(actor, action, obj, society=None, **details):
    return AuditLog.objects.create(
        society=society,
        user=actor if getattr(actor, "pk", None) else None,
        action=action,
        model_name=type(obj).__name__,
        object_id=str(obj.pk),
        details=details,
    )


# --------------------------------------------------------------------------------------
# Document numbering (never COUNT(*) + 1)
# --------------------------------------------------------------------------------------


def _next_number(society, field):
    locked = Society.objects.select_for_update().get(pk=society.pk)
    number = getattr(locked, field)
    setattr(locked, field, number + 1)
    locked.save(update_fields=[field])
    return number


def next_bill_no(society, width=4):
    return f"{_next_number(society, 'next_bill_number'):0{width}d}"


def next_receipt_no(society, width=4):
    return f"R{_next_number(society, 'next_receipt_number'):0{width}d}"


# --------------------------------------------------------------------------------------
# Derived amounts
# --------------------------------------------------------------------------------------


def _sum_subquery(qs, group_field):
    sub = qs.order_by().values(group_field).annotate(total=Sum("amount")).values("total")[:1]
    return Coalesce(Subquery(sub, output_field=DEC), Value(ZERO), output_field=DEC)


def with_paid(line_qs):
    """Annotate BillLines with paid_amount = effective allocations - reversals (one query)."""
    alloc = ReceiptAllocation.objects.filter(bill_line=OuterRef("pk"), receipt__status__in=EFFECTIVE)
    rev = ReceiptAllocationReversal.objects.filter(allocation__bill_line=OuterRef("pk"), allocation__receipt__status__in=EFFECTIVE)
    return line_qs.annotate(paid_amount=_sum_subquery(alloc, "bill_line") - _sum_subquery(rev, "allocation__bill_line"))


def with_allocated(receipt_qs):
    """Annotate Receipts with allocated_total (gross allocations - reversals, ignoring status)."""
    alloc = ReceiptAllocation.objects.filter(receipt=OuterRef("pk"))
    rev = ReceiptAllocationReversal.objects.filter(allocation__receipt=OuterRef("pk"))
    return receipt_qs.annotate(allocated_total=_sum_subquery(alloc, "receipt") - _sum_subquery(rev, "allocation__receipt"))


def bill_line_paid(line):
    gross = ReceiptAllocation.objects.filter(bill_line=line, receipt__status__in=EFFECTIVE).aggregate(t=Sum("amount"))["t"]
    rev = ReceiptAllocationReversal.objects.filter(allocation__bill_line=line, allocation__receipt__status__in=EFFECTIVE).aggregate(
        t=Sum("amount")
    )["t"]
    return money(gross) - money(rev)


def receipt_allocated(receipt):
    if not receipt.is_effective:
        return ZERO
    gross = ReceiptAllocation.objects.filter(receipt=receipt).aggregate(t=Sum("amount"))["t"]
    rev = ReceiptAllocationReversal.objects.filter(allocation__receipt=receipt).aggregate(t=Sum("amount"))["t"]
    return money(gross) - money(rev)


def line_balance(line):
    return max(money(line.amount) - bill_line_paid(line), ZERO)


def receivable_lines(**filters):
    return BillLine.objects.filter(
        bill__status=Bill.ISSUED, line_type__in=[BillLine.CHARGE, BillLine.INTEREST], amount__gt=0, **filters
    )


def flat_open_receivable_lines(flat, before_date=None):
    qs = receivable_lines(bill__flat=flat)
    if before_date:
        qs = qs.filter(service_period_start__lt=before_date)
    qs = with_paid(qs).select_related("bill", "bill__period", "charge_head").order_by("due_date", "service_period_start", "line_type", "id")
    return [line for line in qs if line.balance > 0]


def society_ledger_totals(society):
    totals = with_paid(receivable_lines(bill__society=society)).aggregate(payable=Sum("amount"), paid=Sum("paid_amount"))
    payable, paid = money(totals["payable"]), money(totals["paid"])
    return payable, paid, payable - paid


def society_advance_total(society):
    total = with_allocated(Receipt.objects.filter(society=society, status__in=EFFECTIVE)).aggregate(
        t=Sum("amount") - Sum("allocated_total")
    )["t"]
    return money(total)


# --------------------------------------------------------------------------------------
# Charge configuration
# --------------------------------------------------------------------------------------


def active_rule_for_flat(flat, head, on_date):
    """Return the single rule effective on on_date. Future and expired rules are never used."""
    rules = list(
        FlatChargeRule.objects.filter(flat=flat, charge_head=head, active=True, effective_from__lte=on_date)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
        .order_by("-effective_from")[:2]
    )
    if len(rules) > 1:
        raise ValidationError(
            f"{flat.unit_no}: more than one {head.code} rule is effective on {on_date}. Close the older rule before generating bills."
        )
    return rules[0] if rules else None


def component_code(prefix, period_start):
    return f"{prefix}-{period_start.strftime('%b').upper()}-{period_start.year}"


def head_prefix(head):
    return head.system_code or head.code.upper()


# --------------------------------------------------------------------------------------
# Interest engine
# --------------------------------------------------------------------------------------


def _interest_already_calculated_to(source_line):
    """Last date up to which interest on this principal line has been billed (non-cancelled bills)."""
    seg = (
        InterestSegment.objects.filter(source_line=source_line)
        .exclude(interest_charge__bill_line__bill__status=Bill.CANCELLED)
        .order_by("-to_date")
        .first()
    )
    return seg.to_date if seg else None


def principal_interest_segments(line, as_of_date, rate_pa, grace_days=0, start_after=None):
    """Split the principal line's outstanding history into constant-balance segments.

    Interest runs from due_date + grace_days (or from start_after, if interest was already billed
    up to that date) until as_of_date. Each effective payment reduces the base from its receipt date.
    Returns a list of dicts; amounts are unrounded (4 dp) so the total can be rounded once.
    """
    if line.line_type != BillLine.CHARGE or line.amount <= 0:
        return []
    start = line.due_date + timedelta(days=grace_days)
    if start_after and start_after > start:
        start = start_after
    if as_of_date <= start:
        return []

    allocations = [
        a
        for a in ReceiptAllocation.objects.filter(bill_line=line, receipt__status__in=EFFECTIVE, receipt__receipt_date__lte=as_of_date)
        .select_related("receipt")
        .prefetch_related("reversals")
        .order_by("receipt__receipt_date", "receipt_id", "id")
        if a.effective_amount > 0
    ]
    principal = money(line.amount) - sum((a.effective_amount for a in allocations if a.receipt.receipt_date <= start), ZERO)
    segments = []
    cursor = start
    for allocation in (a for a in allocations if a.receipt.receipt_date > start):
        if principal <= 0:
            break
        end = allocation.receipt.receipt_date
        days = (end - cursor).days
        if days > 0:
            segments.append(_segment(line, cursor, end, principal, rate_pa, allocation))
        principal -= allocation.effective_amount
        cursor = end
    if principal > 0 and as_of_date > cursor:
        segments.append(_segment(line, cursor, as_of_date, principal, rate_pa, None))
    return segments


def _segment(line, from_date, to_date, principal, rate_pa, ended_by):
    days = (to_date - from_date).days
    amount = (Decimal(principal) * Decimal(rate_pa) / Decimal("100") * Decimal(days) / Decimal("365")).quantize(Decimal("0.0001"))
    return {
        "source_line": line,
        "from_date": from_date,
        "to_date": to_date,
        "days": days,
        "principal": money(principal),
        "rate_pa": Decimal(rate_pa),
        "amount": amount,
        "ended_by_allocation": ended_by,
    }


def daily_simple_interest(line, as_of_date, rate_pa, grace_days=0, start_after=None):
    """Convenience wrapper: (interest, days-in-window, principal) for one principal line."""
    segs = principal_interest_segments(line, as_of_date, rate_pa, grace_days, start_after)
    total = money(sum((s["amount"] for s in segs), Decimal("0")))
    days = (segs[-1]["to_date"] - segs[0]["from_date"]).days if segs else 0
    return total, days, line.amount


def validate_interest_rate(society, period):
    rate = money(period.interest_rate_pa)
    if rate < 0:
        raise ValidationError("Interest rate cannot be negative.")
    ceiling = compliance.interest_ceiling(period.bill_date, society.compliance_effective_date)
    if ceiling is not None and rate > ceiling.max_rate_pa:
        raise ValidationError(
            f"Billing dated {period.bill_date} cannot use {rate}% p.a.: the configured compliance ceiling is "
            f"{ceiling.max_rate_pa}% simple p.a. ({ceiling.source}). Verify the society's current bye-laws/General Body "
            "resolution with professional advice before changing configuration."
        )
    if society.interest_on_interest:
        raise ValidationError("Interest-on-interest is not supported in this MVP. Disable it in society settings.")
    return rate


def _create_interest_lines(bill, period, rate, actor):
    society = period.society
    calc_date = period.interest_calculation_date
    interest_head = ChargeHead.objects.filter(society=society, system_code=ChargeHead.INTEREST_SYSTEM_CODE, is_active=True).first()
    if not interest_head:
        raise ValidationError("Interest is enabled but the society has no active charge head with system code INT.")

    sources = (
        BillLine.objects.filter(
            bill__flat=bill.flat, bill__status=Bill.ISSUED, line_type=BillLine.CHARGE, amount__gt=0,
            service_period_start__lt=period.period_start, due_date__lt=calc_date,
        )
        .select_related("bill")
        .order_by("service_period_start", "id")
    )
    grouped = {}
    for source in sources:
        segs = principal_interest_segments(
            source, calc_date, rate, society.interest_grace_days, start_after=_interest_already_calculated_to(source)
        )
        segs = [s for s in segs if s["amount"] > 0]
        if segs:
            grouped.setdefault((source.service_period_start, source.service_period_end), []).extend(segs)

    for (src_start, src_end), segs in grouped.items():
        amount = money(sum((s["amount"] for s in segs), Decimal("0")))
        if amount <= 0:
            continue
        line = BillLine.objects.create(
            bill=bill,
            charge_head=interest_head,
            line_type=BillLine.INTEREST,
            component_code=f"{component_code('INT', src_start)}-C{calc_date:%Y%m%d}",
            service_period_start=src_start,
            service_period_end=src_end,
            due_date=period.due_date,
            description=f"Interest on {src_start:%B %Y} principal to {calc_date:%d-%m-%Y}",
            quantity=Decimal("1"),
            rate=rate,
            amount=amount,
            confirmed=True,
        )
        from_date = min(s["from_date"] for s in segs)
        charge = InterestCharge.objects.create(
            bill_line=line,
            source_period_start=src_start,
            source_period_end=src_end,
            calculation_date=calc_date,
            from_date=from_date,
            to_date=calc_date,
            days=(calc_date - from_date).days,
            rate_pa=rate,
            base_amount=money(sum((s["principal"] for s in segs if s["from_date"] == from_date), ZERO)),
            interest_on_interest=False,
            formula="Σ principal × rate% × days / 365 per segment; principal only; interest-on-interest disabled",
        )
        charge.source_lines.add(*{s["source_line"].pk for s in segs})
        InterestSegment.objects.bulk_create(
            [InterestSegment(interest_charge=charge, **{k: v for k, v in s.items()}) for s in segs]
        )
        audit(
            actor, "interest_calculated", line, society, bill_no=bill.bill_no, component=line.component_code,
            amount=str(amount), rate_pa=str(rate), calculation_date=str(calc_date),
            segments=[
                {"source": s["source_line"].component_code, "from": str(s["from_date"]), "to": str(s["to_date"]),
                 "days": s["days"], "principal": str(s["principal"]), "interest": str(s["amount"])}
                for s in segs
            ],
        )


# --------------------------------------------------------------------------------------
# Billing
# --------------------------------------------------------------------------------------


def _assert_period_open(period):
    if period.is_closed:
        raise ValidationError(f"{period.name} is {period.get_status_display().lower()}; its bills cannot be changed.")


@transaction.atomic
def generate_bill(period, flat, actor=None, bill_no=None):
    """Create a DRAFT bill for one flat. Returns the existing live bill if one exists."""
    period = BillingPeriod.objects.select_for_update().select_related("society").get(pk=period.pk)
    _assert_period_open(period)
    if flat.society_id != period.society_id:
        raise ValidationError("Flat and billing period belong to different societies.")
    existing = Bill.objects.filter(period=period, flat=flat).exclude(status=Bill.CANCELLED).first()
    if existing:
        return existing
    rate = validate_interest_rate(period.society, period)

    bill = Bill.objects.create(
        society=period.society,
        period=period,
        flat=flat,
        bill_no=bill_no or next_bill_no(period.society),
        bill_date=period.bill_date,
        due_date=period.due_date,
        status=Bill.DRAFT,
    )

    # Arrears are presentation snapshots pointing at the original receivables, never new debt.
    BillOpeningItem.objects.bulk_create(
        [BillOpeningItem(bill=bill, source_line=src, snapshot_amount=src.balance) for src in flat_open_receivable_lines(flat, period.period_start)]
    )

    heads = ChargeHead.objects.filter(society=period.society, is_active=True).exclude(system_code=ChargeHead.INTEREST_SYSTEM_CODE)
    for head in heads:
        rule = active_rule_for_flat(flat, head, period.period_start)
        if not rule:
            continue
        if head.charge_type == ChargeHead.FIXED:
            quantity, rate_value, amount, confirmed = Decimal("1"), money(rule.configured_amount), money(rule.configured_amount), True
            if amount <= 0:
                continue
        else:
            # Variable lines are always review-controlled, even when a default exists.
            quantity, rate_value = money(rule.default_quantity), money(rule.default_rate)
            amount, confirmed = money(quantity * rate_value), False
        BillLine.objects.create(
            bill=bill,
            charge_head=head,
            line_type=BillLine.CHARGE,
            component_code=component_code(head_prefix(head), period.period_start),
            service_period_start=period.period_start,
            service_period_end=period.period_end,
            due_date=period.due_date,
            description=head.name,
            quantity=quantity,
            rate=rate_value,
            amount=amount,
            confirmed=confirmed,
        )

    if period.society.interest_enabled and period.interest_calculation_date:
        _create_interest_lines(bill, period, rate, actor)

    audit(actor, "bill_generated", bill, period.society, bill_no=bill.bill_no, flat=flat.unit_no, period=period.name,
          total=str(bill.current_total))
    return bill


@transaction.atomic
def generate_period_bills(period, actor):
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status not in {BillingPeriod.DRAFT, BillingPeriod.GENERATED, BillingPeriod.REVIEW}:
        raise ValidationError("Bills can only be generated for draft/generated/review periods.")
    existing = set(period.bills.exclude(status=Bill.CANCELLED).values_list("flat_id", flat=True))
    created = []
    for flat in period.society.flats.filter(is_active=True).order_by("wing__code", "unit_no"):
        if flat.pk not in existing:
            created.append(generate_bill(period, flat, actor))
    period.status = BillingPeriod.GENERATED
    period.generated_by = actor
    period.generated_at = timezone.now()
    period.save(update_fields=["status", "generated_by", "generated_at"])
    audit(actor, "billing_period_generated", period, period.society, bills_created=len(created))
    return created


@transaction.atomic
def confirm_variable_line(line, actor, quantity, rate):
    line = BillLine.objects.select_for_update().select_related("bill", "bill__period", "charge_head").get(pk=line.pk)
    if line.bill.status != Bill.DRAFT:
        raise ValidationError("Only lines on draft bills can be reviewed.")
    _assert_period_open(line.bill.period)
    if line.line_type != BillLine.CHARGE or not line.charge_head or line.charge_head.charge_type != ChargeHead.VARIABLE:
        raise ValidationError("Only variable charge lines require review.")
    quantity, rate = money(quantity), money(rate)
    if quantity < 0 or rate < 0:
        raise ValidationError("Quantity and rate cannot be negative.")
    before = {"quantity": str(line.quantity), "rate": str(line.rate), "amount": str(line.amount), "confirmed": line.confirmed}
    line.quantity, line.rate, line.amount = quantity, rate, money(quantity * rate)
    line.confirmed, line.confirmed_by, line.confirmed_at = True, actor, timezone.now()
    line.save(update_fields=["quantity", "rate", "amount", "confirmed", "confirmed_by", "confirmed_at"])
    audit(actor, "variable_line_confirmed", line, line.bill.society, bill_no=line.bill.bill_no, component=line.component_code,
          before=before, after={"quantity": str(quantity), "rate": str(rate), "amount": str(line.amount)})
    return line


def _check_maker_checker(period, actor):
    if period.society.enforce_maker_checker and period.generated_by_id and period.generated_by_id == actor.pk:
        raise ValidationError("Maker-checker: the user who generated this period's bills cannot approve/issue them.")


@transaction.atomic
def issue_bill(bill, actor):
    bill = Bill.objects.select_for_update().select_related("period", "period__society").get(pk=bill.pk)
    if bill.status != Bill.DRAFT:
        raise ValidationError(f"Bill {bill.bill_no} is {bill.get_status_display().lower()}, not draft.")
    if bill.period.status == BillingPeriod.LOCKED:
        raise ValidationError("The billing period is locked.")
    _check_maker_checker(bill.period, actor)
    if bill.lines.filter(confirmed=False).exists():
        raise ValidationError(f"Bill {bill.bill_no}: variable charge lines must be confirmed before issue.")
    bill.status, bill.issued_at, bill.issued_by = Bill.ISSUED, timezone.now(), actor
    bill.save(update_fields=["status", "issued_at", "issued_by"])
    audit(actor, "bill_issued", bill, bill.society, bill_no=bill.bill_no, total=str(bill.current_total))
    return bill


@transaction.atomic
def cancel_bill(bill, actor, reason):
    if not (reason or "").strip():
        raise ValidationError("A reason is required to cancel a bill.")
    bill = Bill.objects.select_for_update().select_related("period").get(pk=bill.pk)
    if bill.status == Bill.CANCELLED:
        raise ValidationError("Bill is already cancelled.")
    _assert_period_open(bill.period)
    if bill.status == Bill.ISSUED:
        lines = list(BillLine.objects.select_for_update().filter(bill=bill).order_by("id"))
        if any(bill_line_paid(line) > 0 for line in lines):
            raise ValidationError("Receipts are allocated to this bill. Reverse those allocations before cancelling it.")
        if InterestSegment.objects.filter(source_line__in=lines).exclude(interest_charge__bill_line__bill__status=Bill.CANCELLED).exists():
            raise ValidationError("Later interest was calculated on this bill's principal. Cancel those interest bills first.")
    previous = bill.status
    bill.status, bill.cancelled_at, bill.cancelled_by, bill.cancel_reason = Bill.CANCELLED, timezone.now(), actor, reason.strip()
    bill.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
    audit(actor, "bill_cancelled", bill, bill.society, bill_no=bill.bill_no, previous_status=previous, reason=reason)
    return bill


@transaction.atomic
def submit_period_for_review(period, actor):
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status != BillingPeriod.GENERATED:
        raise ValidationError("Only a generated period can be submitted for review.")
    pending = BillLine.objects.filter(bill__period=period, bill__status=Bill.DRAFT, confirmed=False).count()
    if pending:
        raise ValidationError(f"{pending} variable charge line(s) are still unconfirmed.")
    period.status, period.submitted_by, period.submitted_at = BillingPeriod.REVIEW, actor, timezone.now()
    period.save(update_fields=["status", "submitted_by", "submitted_at"])
    audit(actor, "billing_period_submitted", period, period.society)
    return period


@transaction.atomic
def approve_period(period, actor):
    """Checker step: approves the period and issues every draft bill in it."""
    period = BillingPeriod.objects.select_for_update().select_related("society").get(pk=period.pk)
    if period.status != BillingPeriod.REVIEW:
        raise ValidationError("Only a period in review can be approved.")
    _check_maker_checker(period, actor)
    if BillLine.objects.filter(bill__period=period, bill__status=Bill.DRAFT, confirmed=False).exists():
        raise ValidationError("Variable charge lines remain unconfirmed.")
    issued = [issue_bill(bill, actor) for bill in period.bills.filter(status=Bill.DRAFT).order_by("id")]
    period.status, period.approved_by, period.approved_at = BillingPeriod.APPROVED, actor, timezone.now()
    period.save(update_fields=["status", "approved_by", "approved_at"])
    audit(actor, "billing_period_approved", period, period.society, bills_issued=len(issued))
    return period


@transaction.atomic
def lock_period(period, actor):
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status != BillingPeriod.APPROVED:
        raise ValidationError("Only approved periods can be locked.")
    period.status, period.locked_by, period.locked_at = BillingPeriod.LOCKED, actor, timezone.now()
    period.save(update_fields=["status", "locked_by", "locked_at"])
    audit(actor, "billing_period_locked", period, period.society)
    return period


# --------------------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------------------

ELECTRONIC_MODES = {Receipt.NEFT, Receipt.RTGS, Receipt.UPI, Receipt.BANK_TRANSFER}


def validate_receipt(receipt):
    errors = {}
    if receipt.amount is None or receipt.amount <= 0:
        errors["amount"] = "Receipt amount must be greater than zero."
    if receipt.flat_id and receipt.flat.society_id != receipt.society_id:
        errors["flat"] = "Flat belongs to a different society."
    if receipt.member_id:
        if receipt.member.society_id != receipt.society_id:
            errors["member"] = "Member belongs to a different society."
        elif receipt.flat_id and not receipt.flat.member_links.filter(member_id=receipt.member_id).exists():
            errors["member"] = "Payer is not linked to this flat."
    if receipt.payment_mode == Receipt.CHEQUE and not receipt.cheque_no:
        errors["cheque_no"] = "Cheque number is required for cheque receipts."
    if receipt.payment_mode in ELECTRONIC_MODES and not receipt.transaction_ref:
        errors["transaction_ref"] = "UTR / transaction reference is required for electronic receipts."
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def create_receipt(receipt, actor, allocations=None, auto_policy=None):
    """Save a new receipt, then apply member-directed allocations, then (optionally) an automatic policy.

    Explicit allocations always run first so they take precedence over the automatic policy.
    """
    if receipt.pk:
        raise ValidationError("Receipt already exists.")
    validate_receipt(receipt)
    receipt.receipt_no = next_receipt_no(receipt.society)
    receipt.created_by = actor
    receipt.status = Receipt.RECEIVED
    if receipt.payment_mode == Receipt.CASH:
        # Cash is realised on receipt.
        receipt.status, receipt.cleared_at, receipt.cleared_by = Receipt.CLEARED, timezone.now(), actor
    receipt.save()
    audit(actor, "receipt_created", receipt, receipt.society, receipt_no=receipt.receipt_no, flat=receipt.flat.unit_no,
          amount=str(receipt.amount), mode=receipt.payment_mode, status=receipt.status)
    if allocations:
        allocate_receipt(receipt, allocations, actor, mode=ReceiptAllocation.MANUAL, note=receipt.allocation_instruction)
    if auto_policy:
        auto_allocate_receipt(receipt, actor, auto_policy)
    return receipt


def _lock_receipt(receipt):
    locked = Receipt.objects.select_for_update().select_related("society", "flat").get(pk=receipt.pk)
    if not locked.is_effective:
        raise ValidationError(f"Receipt {locked.receipt_no} is {locked.get_status_display().lower()}; it cannot be allocated.")
    return locked


def _lock_lines(line_ids):
    return {
        line.id: line
        for line in BillLine.objects.select_for_update()
        .select_related("bill", "charge_head")
        .filter(id__in=sorted(set(line_ids)))
        .order_by("id")
    }


def _validate_target(receipt, line):
    if line is None:
        raise ValidationError("Allocation target does not exist.")
    if line.bill.society_id != receipt.society_id:
        raise ValidationError("Allocation target belongs to another society.")
    if line.bill.flat_id != receipt.flat_id:
        raise ValidationError(f"{line.display_code} belongs to another flat; cross-flat allocation is not allowed.")
    if line.bill.status != Bill.ISSUED:
        raise ValidationError(f"{line.display_code} is on a {line.bill.get_status_display().lower()} bill and is not receivable.")
    if not line.is_receivable:
        raise ValidationError(f"{line.display_code} is not a receivable line.")


def _normalise_request(requested):
    merged = {}
    for item in requested:
        try:
            line_id = int(item["bill_line_id"])
        except (TypeError, ValueError, KeyError):
            raise ValidationError("Invalid bill-line target in allocation request.")
        amount = parse_money(item.get("amount"))
        if amount < 0:
            raise ValidationError("Allocation amounts cannot be negative.")
        if amount > 0:
            merged[line_id] = merged.get(line_id, ZERO) + amount
    return merged


@transaction.atomic
def allocate_receipt(receipt, requested, actor, mode=ReceiptAllocation.MANUAL, note=""):
    """Apply a receipt to specific bill lines. Never changes a billed amount; only adds allocation rows."""
    locked = _lock_receipt(receipt)
    wanted = _normalise_request(requested)
    if not wanted:
        return []
    remaining = money(locked.amount) - receipt_allocated(locked)
    total = sum(wanted.values(), ZERO)
    if total > remaining:
        raise ValidationError(f"Allocation ₹{total} exceeds the receipt's unallocated amount ₹{remaining}.")
    lines = _lock_lines(wanted)
    created = []
    for line_id, amount in wanted.items():
        line = lines.get(line_id)
        _validate_target(locked, line)
        available = line_balance(line)
        if amount > available:
            raise ValidationError(f"₹{amount} exceeds the balance ₹{available} on {line.display_code}.")
        created.append(ReceiptAllocation.objects.create(receipt=locked, bill_line=line, amount=amount, mode=mode, note=note[:255], created_by=actor))
    audit(actor, "receipt_allocated", locked, locked.society, receipt_no=locked.receipt_no, mode=mode,
          allocations=[{"allocation_id": a.pk, "line_id": a.bill_line_id, "component": a.bill_line.component_code, "amount": str(a.amount)} for a in created])
    return created


def allocation_candidates(receipt, strategy):
    lines = flat_open_receivable_lines(receipt.flat)
    if strategy == Society.OLDEST_DUE:
        return sorted(lines, key=lambda x: (x.due_date, x.service_period_start, x.id))
    if strategy == Society.PRINCIPAL_THEN_INTEREST:
        # Oldest service period first; within it principal heads by knock-off priority, then that period's interest.
        return sorted(
            lines,
            key=lambda x: (
                x.service_period_start,
                0 if x.line_type == BillLine.CHARGE else 1,
                x.charge_head.knockoff_priority if x.charge_head else 1000,
                x.due_date,
                x.id,
            ),
        )
    raise ValidationError("Choose an automatic knock-off policy.")


@transaction.atomic
def auto_allocate_receipt(receipt, actor, strategy=None):
    locked = _lock_receipt(receipt)
    strategy = strategy or locked.society.allocation_policy
    if strategy not in AUTO_POLICIES:
        raise ValidationError("The society's policy is member-directed. Allocate manually or choose an automatic policy explicitly.")
    remaining = money(locked.amount) - receipt_allocated(locked)
    requested = []
    for line in allocation_candidates(locked, strategy):
        if remaining <= 0:
            break
        take = min(remaining, line.balance)
        if take > 0:
            requested.append({"bill_line_id": line.pk, "amount": take})
            remaining -= take
    if not requested:
        return []
    return allocate_receipt(locked, requested, actor, mode=ReceiptAllocation.AUTO, note=f"Automatic policy: {strategy}")


@transaction.atomic
def reverse_allocation(allocation, amount, actor, reason):
    if not (reason or "").strip():
        raise ValidationError("A reason is required to reverse an allocation.")
    allocation = ReceiptAllocation.objects.select_related("receipt").get(pk=allocation.pk)
    locked = _lock_receipt(allocation.receipt)
    allocation = ReceiptAllocation.objects.select_for_update().select_related("bill_line").prefetch_related("reversals").get(pk=allocation.pk)
    amount = parse_money(amount)
    if amount <= 0:
        raise ValidationError("Reversal amount must be greater than zero.")
    if amount > allocation.effective_amount:
        raise ValidationError(f"Reversal ₹{amount} exceeds the effective allocation ₹{allocation.effective_amount}.")
    reversal = ReceiptAllocationReversal.objects.create(allocation=allocation, amount=amount, reason=reason.strip()[:255], created_by=actor)
    audit(actor, "receipt_allocation_reversed", allocation, locked.society, receipt_no=locked.receipt_no,
          component=allocation.bill_line.component_code, amount=str(amount), reason=reason)
    return reversal


@transaction.atomic
def reallocate_receipt(receipt, desired, actor, reason):
    """Move a receipt's effective allocation to a new target state without deleting history.

    Reductions become ReceiptAllocationReversal rows; increases become new ReceiptAllocation rows.
    """
    if not (reason or "").strip():
        raise ValidationError("A reason is required for reallocation.")
    locked = _lock_receipt(receipt)
    wanted = _normalise_request(desired)
    if sum(wanted.values(), ZERO) > money(locked.amount):
        raise ValidationError("Desired allocations exceed the receipt amount.")

    allocations = list(ReceiptAllocation.objects.select_for_update().filter(receipt=locked).prefetch_related("reversals").order_by("id"))
    current = {}
    for a in allocations:
        current[a.bill_line_id] = current.get(a.bill_line_id, ZERO) + a.effective_amount

    lines = _lock_lines(set(current) | set(wanted))
    for line_id, amount in wanted.items():
        line = lines.get(line_id)
        _validate_target(locked, line)
        capacity = line_balance(line) + current.get(line_id, ZERO)
        if amount > capacity:
            raise ValidationError(f"₹{amount} exceeds the available capacity ₹{capacity} on {line.display_code}.")

    reversals = []
    for line_id, have in current.items():
        excess = have - wanted.get(line_id, ZERO)
        for a in reversed([a for a in allocations if a.bill_line_id == line_id and a.effective_amount > 0]):
            if excess <= 0:
                break
            take = min(excess, a.effective_amount)
            reversals.append(ReceiptAllocationReversal.objects.create(allocation=a, amount=take, reason=reason.strip()[:255], created_by=actor))
            excess -= take

    additions = [{"bill_line_id": lid, "amount": amt - current.get(lid, ZERO)} for lid, amt in wanted.items() if amt > current.get(lid, ZERO)]
    created = allocate_receipt(locked, additions, actor, mode=ReceiptAllocation.REALLOCATION, note=reason) if additions else []
    audit(actor, "receipt_reallocated", locked, locked.society, receipt_no=locked.receipt_no, reason=reason,
          before={str(k): str(v) for k, v in current.items() if v > 0},
          after={str(k): str(v) for k, v in wanted.items()},
          reversal_ids=[r.pk for r in reversals], allocation_ids=[a.pk for a in created])
    return locked


def _change_status(receipt, actor, new_status, reason, allowed_from, stamp):
    locked = Receipt.objects.select_for_update().get(pk=receipt.pk)
    if locked.status not in allowed_from:
        raise ValidationError(f"Receipt {locked.receipt_no} is {locked.get_status_display().lower()}; cannot mark it {new_status}.")
    previous = locked.status
    locked.status = new_status
    locked.status_reason = (reason or "").strip()[:255]
    setattr(locked, f"{stamp}_at", timezone.now())
    setattr(locked, f"{stamp}_by", actor)
    locked.save(update_fields=["status", "status_reason", f"{stamp}_at", f"{stamp}_by"])
    audit(actor, f"receipt_{new_status}", locked, locked.society, receipt_no=locked.receipt_no, previous_status=previous,
          reason=locked.status_reason, allocated_before=str(receipt_allocated_raw(locked)))
    return locked


def receipt_allocated_raw(receipt):
    """Allocated amount ignoring status (used to record what a bounce/cancel took out of PAID)."""
    gross = ReceiptAllocation.objects.filter(receipt=receipt).aggregate(t=Sum("amount"))["t"]
    rev = ReceiptAllocationReversal.objects.filter(allocation__receipt=receipt).aggregate(t=Sum("amount"))["t"]
    return money(gross) - money(rev)


@transaction.atomic
def clear_receipt(receipt, actor):
    return _change_status(receipt, actor, Receipt.CLEARED, "", {Receipt.RECEIVED}, "cleared")


@transaction.atomic
def bounce_receipt(receipt, actor, reason):
    if not (reason or "").strip():
        raise ValidationError("A reason is required to mark a receipt bounced.")
    return _change_status(receipt, actor, Receipt.BOUNCED, reason, {Receipt.RECEIVED}, "bounced")


@transaction.atomic
def cancel_receipt(receipt, actor, reason):
    if not (reason or "").strip():
        raise ValidationError("A reason is required to cancel a receipt.")
    return _change_status(receipt, actor, Receipt.CANCELLED, reason, {Receipt.RECEIVED}, "cancelled")
