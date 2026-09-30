"""Read-only report builders. Every figure is derived from BillLine / ReceiptAllocation /
ReceiptAllocationReversal / Receipt rows — never from stored counters."""

from collections import OrderedDict, defaultdict

from django.db.models import Prefetch, Sum
from django.utils import timezone

from .models import Bill, BillingPeriod, BillLine, BillOpeningItem, ChargeHead, FlatMember, Receipt, ReceiptAllocation
from .services import EFFECTIVE, ZERO, money, receivable_lines, society_advance_total, society_ledger_totals, with_allocated, with_paid


def _bills_qs(period):
    lines = with_paid(BillLine.objects.all()).select_related("charge_head")
    opening = BillOpeningItem.objects.select_related("source_line")
    return (
        period.bills.exclude(status=Bill.CANCELLED)
        .select_related("flat", "flat__wing")
        .prefetch_related(
            Prefetch("lines", queryset=lines),
            Prefetch("opening_items", queryset=opening),
            Prefetch("flat__member_links", queryset=FlatMember.objects.select_related("member").order_by("id")),
        )
        .order_by("flat__wing__code", "flat__unit_no")
    )


def _source_balances(bills):
    """Current balance of every source line referenced by opening items (one query)."""
    ids = {oi.source_line_id for b in bills for oi in b.opening_items.all()}
    return {x.pk: x.balance for x in with_paid(BillLine.objects.filter(pk__in=ids))}


def bill_register(period):
    """Bill Register in the source-sheet shape. Current-charge columns tie to BillLines."""
    bills = list(_bills_qs(period))
    heads = list(
        ChargeHead.objects.filter(society=period.society, bill_lines__bill__period=period, bill_lines__line_type=BillLine.CHARGE)
        .distinct()
        .order_by("knockoff_priority", "name")
    )
    src_bal = _source_balances(bills)
    rows, totals = [], defaultdict(lambda: ZERO)
    for bill in bills:
        by_head = defaultdict(lambda: ZERO)
        interest = ZERO
        for line in bill.lines.all():
            if line.line_type == BillLine.CHARGE:
                by_head[line.charge_head_id] += line.amount
            elif line.line_type == BillLine.INTEREST:
                interest += line.amount
        amount = sum(by_head.values(), ZERO)
        p_arr = sum((oi.snapshot_amount for oi in bill.opening_items.all() if oi.source_line.line_type == BillLine.CHARGE), ZERO)
        i_arr = sum((oi.snapshot_amount for oi in bill.opening_items.all() if oi.source_line.line_type == BillLine.INTEREST), ZERO)
        current_balance = sum((l.balance for l in bill.lines.all() if l.is_receivable), ZERO)
        opening_balance = sum((min(oi.snapshot_amount, src_bal.get(oi.source_line_id, ZERO)) for oi in bill.opening_items.all()), ZERO)
        payable = amount + interest + p_arr + i_arr
        balance = current_balance + opening_balance
        row = {
            "bill": bill,
            "member": " & ".join(l.member.full_name for l in bill.flat.member_links.all() if l.role != FlatMember.NOMINEE),
            "heads": [by_head[h.pk] for h in heads],
            "amount": amount,
            "interest": interest,
            "bill_amount": amount + interest,
            "principal_arrear": p_arr,
            "interest_arrear": i_arr,
            "payable": payable,
            "paid": payable - balance,
            "balance": balance,
        }
        rows.append(row)
        for i, h in enumerate(heads):
            totals[f"h{h.pk}"] += row["heads"][i]
        for key in ("amount", "interest", "bill_amount", "principal_arrear", "interest_arrear", "payable", "paid", "balance"):
            totals[key] += row[key]
    totals["heads"] = [totals[f"h{h.pk}"] for h in heads]
    return {"heads": heads, "rows": rows, "totals": dict(totals)}


def collection_sheet(period, date_from=None, date_to=None):
    """Collection Sheet: bill demand next to the effective receipts received for the flat in the window."""
    date_from = date_from or period.bill_date
    date_to = date_to or period.period_end
    register = bill_register(period)
    receipts = defaultdict(list)
    receipt_qs = with_allocated(
        Receipt.objects.filter(society=period.society, receipt_date__gte=date_from, receipt_date__lte=date_to, status__in=EFFECTIVE)
    ).order_by("receipt_date", "id")
    for r in receipt_qs:
        receipts[r.flat_id].append(r)
    rows, total_received = [], ZERO
    for row in register["rows"]:
        flat_receipts = receipts.pop(row["bill"].flat_id, [])
        received = sum((r.amount for r in flat_receipts), ZERO)
        total_received += received
        rows.append({**row, "receipts": flat_receipts, "received": received, "arrears": row["principal_arrear"] + row["interest_arrear"]})
    # Receipts from flats without a bill in this period are still collections; never hide them.
    unbilled = [r for rs in receipts.values() for r in rs]
    total_received += sum((r.amount for r in unbilled), ZERO)
    return {
        **register,
        "rows": rows,
        "unbilled_receipts": unbilled,
        "total_received": total_received,
        "date_from": date_from,
        "date_to": date_to,
    }


def receipt_register(society, date_from=None, date_to=None, status=None):
    qs = with_allocated(Receipt.objects.filter(society=society)).select_related("flat", "member")
    if date_from:
        qs = qs.filter(receipt_date__gte=date_from)
    if date_to:
        qs = qs.filter(receipt_date__lte=date_to)
    if status:
        qs = qs.filter(status=status)
    rows, totals = [], defaultdict(lambda: ZERO)
    for r in qs.order_by("receipt_date", "id"):
        rows.append({"receipt": r, "allocated": r.allocated_amount, "advance": r.unallocated_amount})
        if r.is_effective:
            totals["amount"] += r.amount
            totals["allocated"] += r.allocated_amount
            totals["advance"] += r.unallocated_amount
        else:
            totals["ineffective"] += r.amount
    return {"rows": rows, "totals": dict(totals)}


def advance_register(society):
    qs = with_allocated(Receipt.objects.filter(society=society, status__in=EFFECTIVE)).select_related("flat", "member").order_by("receipt_date", "id")
    rows = [{"receipt": r, "allocated": r.allocated_amount, "advance": r.unallocated_amount} for r in qs if r.unallocated_amount > 0]
    return {"rows": rows, "total": sum((x["advance"] for x in rows), ZERO)}


def outstanding_by_flat(society, as_of=None):
    lines = with_paid(receivable_lines(bill__society=society)).values("bill__flat_id", "line_type", "amount", "paid_amount", "due_date")
    today = as_of or timezone.localdate()
    agg = defaultdict(lambda: {"payable": ZERO, "paid": ZERO, "principal": ZERO, "interest": ZERO, "overdue": ZERO})
    for l in lines:
        a = agg[l["bill__flat_id"]]
        bal = money(l["amount"]) - money(l["paid_amount"])
        a["payable"] += money(l["amount"])
        a["paid"] += money(l["paid_amount"])
        a["principal" if l["line_type"] == BillLine.CHARGE else "interest"] += bal
        if l["due_date"] < today:
            a["overdue"] += bal
    advances = defaultdict(lambda: ZERO)
    for r in with_allocated(Receipt.objects.filter(society=society, status__in=EFFECTIVE)):
        advances[r.flat_id] += r.unallocated_amount
    rows = []
    for flat in society.flats.select_related("wing").prefetch_related(
        Prefetch("member_links", queryset=FlatMember.objects.select_related("member").order_by("id"))
    ):
        a = agg.get(flat.pk)
        if not a and not advances.get(flat.pk):
            continue
        a = a or {"payable": ZERO, "paid": ZERO, "principal": ZERO, "interest": ZERO, "overdue": ZERO}
        rows.append({"flat": flat, **a, "balance": a["payable"] - a["paid"], "advance": advances.get(flat.pk, ZERO)})
    rows.sort(key=lambda r: (-r["balance"], r["flat"].unit_no))
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("payable", "paid", "balance", "principal", "interest", "overdue", "advance")}
    return {"rows": rows, "totals": totals}


def charge_head_summary(society, period=None):
    qs = with_paid(receivable_lines(bill__society=society))
    if period:
        qs = qs.filter(bill__period=period)
    agg = OrderedDict()
    for l in qs.select_related("charge_head").order_by("charge_head__knockoff_priority", "charge_head__name"):
        key = l.charge_head_id
        if key not in agg:
            agg[key] = {"head": l.charge_head, "line_type": l.get_line_type_display(), "payable": ZERO, "paid": ZERO, "lines": 0}
        agg[key]["payable"] += l.amount
        agg[key]["paid"] += l.paid
        agg[key]["lines"] += 1
    rows = [{**v, "balance": v["payable"] - v["paid"]} for v in agg.values()]
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("payable", "paid", "balance")}
    return {"rows": rows, "totals": totals}


def flat_statement(flat, date_from=None, date_to=None):
    """Chronological open-item statement for one flat.

    Debits: issued bill lines (on bill date), effect of receipts bounced/cancelled after allocation,
    and allocation reversals. Credits: receipt allocations (on receipt date).
    Opening outstanding + billed - effective payments = closing outstanding.
    """
    events = []
    lines = list(
        BillLine.objects.filter(bill__flat=flat, bill__status=Bill.ISSUED, line_type__in=[BillLine.CHARGE, BillLine.INTEREST], amount__gt=0)
        .select_related("bill", "charge_head")
        .order_by("bill__bill_date", "id")
    )
    for l in lines:
        events.append({
            "date": l.bill.bill_date, "sort": (0, l.pk), "document": f"Bill {l.bill.bill_no}", "component": l.display_code,
            "line": l, "kind": "interest" if l.line_type == BillLine.INTEREST else "bill",
            "debit": l.amount, "credit": ZERO, "receipt": None, "narration": l.description,
        })
    allocations = (
        ReceiptAllocation.objects.filter(bill_line__in=lines)
        .select_related("receipt", "bill_line", "bill_line__bill")
        .prefetch_related("reversals")
        .order_by("receipt__receipt_date", "id")
    )
    for a in allocations:
        r = a.receipt
        events.append({
            "date": r.receipt_date, "sort": (1, a.pk), "document": f"Receipt {r.receipt_no}", "component": a.bill_line.display_code,
            "line": a.bill_line, "kind": "receipt", "debit": ZERO, "credit": a.amount, "receipt": r,
            "narration": f"{r.get_payment_mode_display()} {r.cheque_no or r.transaction_ref}".strip() + (f" · {a.note}" if a.note else ""),
        })
        for rev in a.reversals.all():
            events.append({
                "date": timezone.localtime(rev.created_at).date(), "sort": (2, rev.pk), "document": f"Reversal of {r.receipt_no}",
                "component": a.bill_line.display_code, "line": a.bill_line, "kind": "reversal",
                "debit": rev.amount, "credit": ZERO, "receipt": r, "narration": rev.reason,
            })
        if not r.is_effective and a.effective_amount > 0:
            when = r.bounced_at or r.cancelled_at
            events.append({
                "date": timezone.localtime(when).date() if when else r.receipt_date, "sort": (3, a.pk),
                "document": f"Receipt {r.receipt_no} {r.get_status_display().lower()}", "component": a.bill_line.display_code,
                "line": a.bill_line, "kind": "bounce", "debit": a.effective_amount, "credit": ZERO, "receipt": r,
                "narration": r.status_reason,
            })
    events.sort(key=lambda e: (e["date"], e["sort"]))

    opening = ZERO
    shown = []
    running = ZERO
    for e in events:
        running += e["debit"] - e["credit"]
        e["balance"] = running
        if date_from and e["date"] < date_from:
            opening = running
            continue
        if date_to and e["date"] > date_to:
            continue
        shown.append(e)
    closing = shown[-1]["balance"] if shown else opening
    billed = sum((e["debit"] for e in shown if e["kind"] == "bill"), ZERO)
    interest = sum((e["debit"] for e in shown if e["kind"] == "interest"), ZERO)
    payments = sum((e["credit"] for e in shown), ZERO) - sum((e["debit"] for e in shown if e["kind"] in ("reversal", "bounce")), ZERO)

    component_rows = [
        {"line": l, "payable": l.amount, "paid": l.paid, "balance": l.balance}
        for l in with_paid(BillLine.objects.filter(pk__in=[x.pk for x in lines])).select_related("bill", "charge_head").order_by("service_period_start", "line_type", "id")
    ]
    advances = [r for r in with_allocated(Receipt.objects.filter(flat=flat, status__in=EFFECTIVE)).order_by("receipt_date") if r.unallocated_amount > 0]
    return {
        "events": shown,
        "opening": opening,
        "billed": billed,
        "interest": interest,
        "payments": payments,
        "closing": closing,
        "components": component_rows,
        "advances": advances,
        "advance_total": sum((r.unallocated_amount for r in advances), ZERO),
    }


def dashboard(society):
    payable, paid, outstanding = society_ledger_totals(society)
    current = society.billing_periods.exclude(status=BillingPeriod.DRAFT).order_by("-period_start").first() or society.billing_periods.order_by("-period_start").first()
    current_billed = ZERO
    if current:
        current_billed = money(
            BillLine.objects.filter(bill__period=current, bill__status=Bill.ISSUED, line_type__in=[BillLine.CHARGE, BillLine.INTEREST]).aggregate(t=Sum("amount"))["t"]
        )
    today = timezone.localdate()
    overdue = defaultdict(lambda: ZERO)
    for l in with_paid(receivable_lines(bill__society=society, due_date__lt=today)).values("bill__flat_id", "amount", "paid_amount"):
        overdue[l["bill__flat_id"]] += money(l["amount"]) - money(l["paid_amount"])
    return {
        "flat_count": society.flats.filter(is_active=True).count(),
        "member_count": FlatMember.objects.filter(flat__society=society, member__is_active=True, to_date__isnull=True).values("member").distinct().count(),
        "current_period": current,
        "current_billed": current_billed,
        "payable": payable,
        "paid": paid,
        "outstanding": outstanding,
        "advance": society_advance_total(society),
        "overdue_flats": sum(1 for v in overdue.values() if v > 0),
        "recent_receipts": with_allocated(Receipt.objects.filter(society=society)).select_related("flat").order_by("-receipt_date", "-id")[:10],
        "awaiting_clearance": Receipt.objects.filter(society=society, status=Receipt.RECEIVED).select_related("flat").order_by("receipt_date", "id"),
    }


def portfolio(societies, roles=None):
    """Cross-society summary for a CA or auditor who handles several societies.

    One row per society with the figures that decide where attention is needed: outstanding,
    advance sitting unapplied, the current period and whether it is waiting on someone.
    """
    rows = []
    for society in societies:
        payable, paid, outstanding = society_ledger_totals(society)
        period = society.billing_periods.order_by("-period_start").first()
        pending_review = BillLine.objects.filter(bill__society=society, bill__status=Bill.DRAFT, confirmed=False).count()
        rows.append({
            "society": society,
            "role": (roles or {}).get(society.pk, ""),
            "flats": society.flats.filter(is_active=True).count(),
            "payable": payable,
            "paid": paid,
            "outstanding": outstanding,
            "advance": society_advance_total(society),
            "overdue": money(
                with_paid(receivable_lines(bill__society=society, due_date__lt=timezone.localdate()))
                .aggregate(t=Sum("amount") - Sum("paid_amount"))["t"]
            ),
            "period": period,
            "pending_review": pending_review,
            "awaiting_clearance": Receipt.objects.filter(society=society, status=Receipt.RECEIVED).count(),
            "last_receipt": Receipt.objects.filter(society=society).order_by("-receipt_date").values_list("receipt_date", flat=True).first(),
            "needs_attention": pending_review > 0 or (period is not None and period.status in (BillingPeriod.GENERATED, BillingPeriod.REVIEW)),
        })
    rows.sort(key=lambda r: (-r["outstanding"], r["society"].name))
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("payable", "paid", "outstanding", "advance", "overdue")}
    totals["flats"] = sum(r["flats"] for r in rows)
    totals["societies"] = len(rows)
    return {"rows": rows, "totals": totals}
