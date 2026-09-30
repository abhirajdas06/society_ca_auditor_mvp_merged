import csv
import json
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import importer, reports
from .forms import (
    BillingPeriodForm,
    ChargeHeadForm,
    FlatChargeRuleForm,
    FlatFilterForm,
    FlatForm,
    FlatMemberForm,
    ImportUploadForm,
    MemberForm,
    ReceiptForm,
    SocietyForm,
    VariableLineForm,
    WingForm,
)
from .models import (
    AuditLog,
    Bill,
    BillingPeriod,
    BillLine,
    ChargeHead,
    Flat,
    FlatChargeRule,
    FlatMember,
    ImportBatch,
    Member,
    Receipt,
    ReceiptAllocation,
    Society,
)
from .permissions import SESSION_KEY, accessible_societies, can, memberships, require
from .services import (
    AUTO_POLICIES,
    ZERO,
    allocate_receipt,
    approve_period,
    audit,
    auto_allocate_receipt,
    bounce_receipt,
    cancel_bill,
    cancel_receipt,
    clear_receipt,
    confirm_variable_line,
    create_receipt,
    generate_period_bills,
    issue_bill,
    lock_period,
    money,
    reallocate_receipt,
    reverse_allocation,
    submit_period_for_review,
    with_allocated,
    with_paid,
)

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _date_param(request, name):
    value = request.GET.get(name)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _errors(exc):
    return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _run(request, fn, success, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        messages.success(request, success)
        return result
    except ValidationError as exc:
        messages.error(request, _errors(exc))
        return None


def _master_form(request, form_class, title, back, instance=None, action="master_saved"):
    form = form_class(request.POST or None, instance=instance, society=request.society)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            changed = {f: str(form.cleaned_data.get(f)) for f in form.changed_data}
            obj = form.save()
            audit(request.user, f"{obj._meta.model_name}_{'updated' if instance else 'created'}", obj, request.society, changes=changed)
        messages.success(request, f"{title} saved.")
        return redirect(back)
    return render(request, "society/form.html", {"form": form, "title": title, "back_url": reverse(back)})


@require("view", session_only=True)   # changes the session only, so auditors may switch too
@require_POST
def switch_society(request):
    society = get_object_or_404(accessible_societies(request.user), pk=request.POST.get("society"))
    request.session[SESSION_KEY] = society.pk
    messages.info(request, f"Now working in {society.name}.")
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect("dashboard")


@require("view")
def portfolio(request):
    """Every society this user handles, in one list. The entry point for a CA or auditor
    who works across several societies; figures stay strictly per society."""
    societies = accessible_societies(request.user).order_by("name")
    roles = {m.society_id: m.get_role_display() for m in memberships(request.user)}
    if request.user.is_superuser:
        roles = {s.pk: roles.get(s.pk, "Superuser") for s in societies}
    return render(request, "society/portfolio.html", reports.portfolio(societies, roles))


# --------------------------------------------------------------------------------------
# dashboard & masters
# --------------------------------------------------------------------------------------


@require("view")
def dashboard(request):
    if request.society is None:
        return render(request, "society/dashboard.html", {})
    return render(request, "society/dashboard.html", reports.dashboard(request.society))


@require("config.edit")
def society_edit(request):
    society = request.society
    form = SocietyForm(request.POST or None, instance=society)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            changed = {f: str(form.cleaned_data.get(f)) for f in form.changed_data}
            form.save()
            audit(request.user, "society_settings_updated", society, society, changes=changed)
        messages.success(request, "Society settings saved.")
        return redirect("dashboard")
    return render(request, "society/form.html", {"form": form, "title": "Society Settings", "back_url": reverse("dashboard")})


@require("view")
def flat_list(request):
    qs = (
        Flat.objects.filter(society=request.society)
        .select_related("wing")
        .prefetch_related(Prefetch("member_links", queryset=FlatMember.objects.select_related("member").order_by("id")))
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(unit_no__icontains=q) | Q(member_links__member__full_name__icontains=q)).distinct()
    flat_filter = FlatFilterForm(request.GET or None, society=request.society)
    if flat_filter.is_valid():
        if flat_filter.cleaned_data.get("flat"):
            qs = qs.filter(pk=flat_filter.cleaned_data["flat"].pk)
        elif flat_filter.cleaned_data.get("wing"):
            qs = qs.filter(wing_id=flat_filter.cleaned_data["wing"])
    return render(request, "society/flats.html", {"flats": qs, "q": q, "flat_filter": flat_filter})


@require("master.edit")
def wing_create(request):
    return _master_form(request, WingForm, "Wing", "flat_list")


@require("master.edit")
def flat_create(request):
    return _master_form(request, FlatForm, "Flat", "flat_list")


@require("master.edit")
def flat_edit(request, pk):
    return _master_form(request, FlatForm, "Flat", "flat_list", get_object_or_404(Flat, pk=pk, society=request.society))


@require("master.edit")
def flat_member_create(request):
    return _master_form(request, FlatMemberForm, "Flat member link", "flat_list")


@require("view")
def member_list(request):
    members = Member.objects.filter(society=request.society).prefetch_related("flat_links__flat")
    return render(request, "society/members.html", {"members": members})


@require("master.edit")
def member_create(request):
    return _master_form(request, MemberForm, "Member", "member_list")


@require("master.edit")
def member_edit(request, pk):
    return _master_form(request, MemberForm, "Member", "member_list", get_object_or_404(Member, pk=pk, society=request.society))


@require("view")
def charge_head_list(request):
    return render(request, "society/charge_heads.html", {"heads": ChargeHead.objects.filter(society=request.society)})


@require("config.edit")
def charge_head_create(request):
    return _master_form(request, ChargeHeadForm, "Charge head", "charge_head_list")


@require("config.edit")
def charge_head_edit(request, pk):
    return _master_form(request, ChargeHeadForm, "Charge head", "charge_head_list", get_object_or_404(ChargeHead, pk=pk, society=request.society))


@require("view")
def charge_rule_list(request):
    rules = FlatChargeRule.objects.filter(flat__society=request.society).select_related("flat", "charge_head")
    flat_filter = FlatFilterForm(request.GET or None, society=request.society)
    if flat_filter.is_valid():
        if flat_filter.cleaned_data.get("flat"):
            rules = rules.filter(flat=flat_filter.cleaned_data["flat"])
        elif flat_filter.cleaned_data.get("wing"):
            rules = rules.filter(flat__wing_id=flat_filter.cleaned_data["wing"])
    return render(request, "society/charge_rules.html", {"rules": rules, "flat_filter": flat_filter})


@require("config.edit")
def charge_rule_create(request):
    return _master_form(request, FlatChargeRuleForm, "Flat charge rule", "charge_rule_list")


@require("config.edit")
def charge_rule_edit(request, pk):
    rule = get_object_or_404(FlatChargeRule, pk=pk, flat__society=request.society)
    return _master_form(request, FlatChargeRuleForm, "Flat charge rule", "charge_rule_list", rule)


# --------------------------------------------------------------------------------------
# billing
# --------------------------------------------------------------------------------------


@require("view")
def period_list(request):
    periods = BillingPeriod.objects.filter(society=request.society).select_related("generated_by", "submitted_by", "approved_by", "locked_by")
    return render(request, "society/periods.html", {"periods": periods})


@require("billing.prepare")
def period_create(request):
    initial = {
        "interest_rate_pa": request.society.interest_rate_pa,
        "interest_resolution_reference": request.society.interest_resolution_reference,
    }
    form = BillingPeriodForm(request.POST or None, initial=initial, society=request.society)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            period = form.save()
            audit(request.user, "billing_period_created", period, request.society, name=period.name, rate=str(period.interest_rate_pa))
        messages.success(request, "Billing period created.")
        return redirect("period_list")
    return render(request, "society/form.html", {"form": form, "title": "New Billing Period", "back_url": reverse("period_list")})


def _period(request, pk):
    return get_object_or_404(BillingPeriod, pk=pk, society=request.society)


@require("billing.prepare")
@require_POST
def period_generate(request, pk):
    created = _run(request, generate_period_bills, "Draft bills generated. Review variable lines, then submit for review.", _period(request, pk), request.user)
    return redirect(f"{reverse('bill_list')}?period={pk}" if created is not None else "period_list")


@require("billing.prepare")
@require_POST
def period_submit(request, pk):
    _run(request, submit_period_for_review, "Period submitted for review.", _period(request, pk), request.user)
    return redirect("period_list")


@require("billing.approve")
@require_POST
def period_approve(request, pk):
    _run(request, approve_period, "Period approved and all draft bills issued.", _period(request, pk), request.user)
    return redirect("period_list")


@require("billing.approve")
@require_POST
def period_lock(request, pk):
    _run(request, lock_period, "Period locked.", _period(request, pk), request.user)
    return redirect("period_list")


@require("view")
def bill_list(request):
    qs = (
        Bill.objects.filter(society=request.society)
        .select_related("flat", "period")
        .prefetch_related(Prefetch("lines", queryset=BillLine.objects.only("id", "bill_id", "amount", "line_type", "confirmed")))
        .order_by("-period__period_start", "bill_no")
    )
    period_id = request.GET.get("period")
    if period_id:
        qs = qs.filter(period_id=period_id)
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    flat_filter = FlatFilterForm(request.GET or None, society=request.society)
    if flat_filter.is_valid() and flat_filter.cleaned_data.get("flat"):
        qs = qs.filter(flat=flat_filter.cleaned_data["flat"])
    if request.GET.get("review") == "1":
        qs = qs.filter(lines__confirmed=False).distinct()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(bill_no__icontains=q) | Q(flat__unit_no__icontains=q) | Q(flat__member_links__member__full_name__icontains=q)).distinct()
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(
        request, "society/bills.html",
        {"page": page, "q": q, "periods": BillingPeriod.objects.filter(society=request.society), "period_id": period_id, "status": status,
         "statuses": Bill.STATUS_CHOICES, "flat_filter": flat_filter},
    )


@require("view")
def bill_detail(request, pk):
    bill = get_object_or_404(Bill.objects.select_related("flat", "flat__wing", "period", "issued_by", "cancelled_by"), pk=pk, society=request.society)
    lines = with_paid(bill.lines.all()).select_related("charge_head", "confirmed_by", "interest_charge").prefetch_related("interest_charge__segments__source_line")
    opening = bill.opening_items.select_related("source_line", "source_line__bill")
    return render(request, "society/bill_detail.html", {"bill": bill, "lines": lines, "opening": opening})


@require("billing.approve")
@require_POST
def bill_issue(request, pk):
    bill = get_object_or_404(Bill, pk=pk, society=request.society)
    _run(request, issue_bill, f"Bill {bill.bill_no} issued.", bill, request.user)
    return redirect("bill_detail", pk=pk)


@require("billing.prepare")
@require_POST
def bill_cancel(request, pk):
    bill = get_object_or_404(Bill, pk=pk, society=request.society)
    if bill.status == Bill.ISSUED and not can(request.user, request.society, "billing.approve"):
        raise PermissionDenied("Only an approver can cancel an issued bill.")
    _run(request, cancel_bill, f"Bill {bill.bill_no} cancelled.", bill, request.user, request.POST.get("reason", ""))
    return redirect("bill_detail", pk=pk)


@require("billing.prepare")
def bill_line_review(request, pk):
    line = get_object_or_404(BillLine.objects.select_related("bill", "charge_head"), pk=pk, bill__society=request.society)
    form = VariableLineForm(request.POST or None, initial={"quantity": line.quantity, "rate": line.rate})
    if request.method == "POST" and form.is_valid():
        done = _run(request, confirm_variable_line, f"{line.display_code} confirmed.", line, request.user, form.cleaned_data["quantity"], form.cleaned_data["rate"])
        if done is not None:
            nxt = BillLine.objects.filter(bill__period=line.bill.period, bill__status=Bill.DRAFT, confirmed=False).order_by("bill__bill_no", "id").first()
            if request.POST.get("next") == "1" and nxt:
                return redirect("bill_line_review", pk=nxt.pk)
            return redirect("bill_detail", pk=line.bill_id)
    return render(request, "society/line_review.html", {"form": form, "line": line})


# --------------------------------------------------------------------------------------
# receipts
# --------------------------------------------------------------------------------------


@require("view")
def receipt_list(request):
    qs = with_allocated(Receipt.objects.filter(society=request.society)).select_related("flat", "member")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(receipt_no__icontains=q) | Q(flat__unit_no__icontains=q) | Q(cheque_no__icontains=q) | Q(transaction_ref__icontains=q))
    flat_filter = FlatFilterForm(request.GET or None, society=request.society)
    if flat_filter.is_valid() and flat_filter.cleaned_data.get("flat"):
        qs = qs.filter(flat=flat_filter.cleaned_data["flat"])
    page = Paginator(qs.order_by("-receipt_date", "-id"), 100).get_page(request.GET.get("page"))
    return render(request, "society/receipts.html",
                  {"page": page, "q": q, "status": status, "statuses": Receipt.STATUS_CHOICES, "flat_filter": flat_filter})


def _parse_allocations(raw):
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        raise ValidationError("Allocation data is not valid.")
    if not isinstance(data, list):
        raise ValidationError("Allocation data is not valid.")
    return data


@require("receipt.create")
def receipt_create(request):
    form = ReceiptForm(request.POST or None, society=request.society, initial={"flat": request.GET.get("flat")})
    if request.method == "POST" and form.is_valid():
        receipt = form.save(commit=False)
        receipt.society = request.society
        auto = request.POST.get("auto_policy") or None
        if auto and auto not in AUTO_POLICIES:
            auto = None
        try:
            with transaction.atomic():
                create_receipt(receipt, request.user, _parse_allocations(request.POST.get("allocations_json", "")), auto)
            messages.success(request, f"Receipt {receipt.receipt_no} saved. Advance / unapplied: ₹{receipt.unallocated_amount}")
            return redirect("receipt_detail", pk=receipt.pk)
        except ValidationError as exc:
            receipt.pk = None
            if hasattr(exc, "error_dict"):
                for field, errs in exc.error_dict.items():
                    form.add_error(field if field in form.fields else None, errs)
            else:
                messages.error(request, _errors(exc))
    return render(request, "society/receipt_form.html", {"form": form, "policies": Society.ALLOCATION_POLICY_CHOICES[1:]})


def _receipt(request, pk):
    return get_object_or_404(Receipt.objects.select_related("flat", "member", "society"), pk=pk, society=request.society)


@require("view")
def receipt_detail(request, pk):
    receipt = _receipt(request, pk)
    allocations = receipt.allocations.select_related("bill_line", "bill_line__bill", "created_by").prefetch_related("reversals__created_by")
    history = AuditLog.objects.filter(society=request.society, model_name="Receipt", object_id=str(receipt.pk)).select_related("user")
    return render(request, "society/receipt_detail.html", {"receipt": receipt, "allocations": allocations, "history": history,
                                                           "policies": Society.ALLOCATION_POLICY_CHOICES[1:]})


@require("allocation.apply")
def receipt_allocate(request, pk):
    receipt = _receipt(request, pk)
    if request.method == "POST":
        try:
            with transaction.atomic():
                allocate_receipt(receipt, _parse_allocations(request.POST.get("allocations_json", "")), request.user,
                                 ReceiptAllocation.MANUAL, request.POST.get("note", ""))
            messages.success(request, "Allocation posted.")
            return redirect("receipt_detail", pk=pk)
        except ValidationError as exc:
            messages.error(request, _errors(exc))
    return render(request, "society/receipt_allocate.html", {"receipt": receipt, "mode": "allocate"})


@require("allocation.correct")
def receipt_reallocate(request, pk):
    receipt = _receipt(request, pk)
    if request.method == "POST":
        try:
            reallocate_receipt(receipt, _parse_allocations(request.POST.get("allocations_json", "")), request.user, request.POST.get("reason", ""))
            messages.success(request, f"Receipt {receipt.receipt_no} reallocated. Original allocation history is retained.")
            return redirect("receipt_detail", pk=pk)
        except ValidationError as exc:
            messages.error(request, _errors(exc))
    return render(request, "society/receipt_allocate.html", {"receipt": receipt, "mode": "reallocate"})


@require("allocation.apply")
@require_POST
def receipt_auto_allocate(request, pk):
    _run(request, auto_allocate_receipt, "Remaining amount allocated by automatic policy.", _receipt(request, pk), request.user,
         request.POST.get("policy") or None)
    return redirect("receipt_detail", pk=pk)


@require("receipt.clear")
@require_POST
def receipt_clear(request, pk):
    _run(request, clear_receipt, "Receipt marked cleared.", _receipt(request, pk), request.user)
    return redirect("receipt_detail", pk=pk)


@require("receipt.bounce")
@require_POST
def receipt_bounce(request, pk):
    _run(request, bounce_receipt, "Receipt marked bounced. Its allocations remain visible but no longer count as paid.",
         _receipt(request, pk), request.user, request.POST.get("reason", ""))
    return redirect("receipt_detail", pk=pk)


@require("receipt.cancel")
@require_POST
def receipt_cancel(request, pk):
    _run(request, cancel_receipt, "Receipt cancelled. It no longer counts toward paid balances.", _receipt(request, pk), request.user,
         request.POST.get("reason", ""))
    return redirect("receipt_detail", pk=pk)


@require("allocation.correct")
@require_POST
def allocation_reverse(request, pk):
    allocation = get_object_or_404(ReceiptAllocation, pk=pk, receipt__society=request.society)
    _run(request, reverse_allocation, "Allocation reversed. The amount is available again as receipt advance.", allocation, request.user,
         request.POST.get("amount", "0"), request.POST.get("reason", ""))
    return redirect("receipt_detail", pk=allocation.receipt_id)


@require("view")
@require_GET
def receivable_lines_api(request, flat_id):
    flat = get_object_or_404(Flat, pk=flat_id, society=request.society)
    receipt = None
    if request.GET.get("receipt_id"):
        receipt = get_object_or_404(Receipt, pk=request.GET["receipt_id"], society=request.society, flat=flat)
    current = {}
    if receipt:
        for a in receipt.allocations.prefetch_related("reversals"):
            current[a.bill_line_id] = current.get(a.bill_line_id, ZERO) + a.effective_amount
    qs = with_paid(
        BillLine.objects.filter(bill__flat=flat, bill__status=Bill.ISSUED, line_type__in=[BillLine.CHARGE, BillLine.INTEREST], amount__gt=0)
    ).select_related("bill", "charge_head").order_by("service_period_start", "line_type", "charge_head__knockoff_priority", "id")
    rows = []
    for line in qs:
        mine = money(current.get(line.id, ZERO))
        if line.balance <= 0 and mine <= 0:
            continue
        rows.append({
            "id": line.id,
            "reference": line.display_code,
            "component": line.component_code,
            "head": line.charge_head.name if line.charge_head else "",
            "type": line.line_type,
            "bill_no": line.bill.bill_no,
            "bill_date": line.bill.bill_date.isoformat(),
            "due_date": line.due_date.isoformat(),
            "payable": str(line.amount),
            "paid": str(line.paid),
            "balance": str(line.balance),
            "receipt_current": str(mine),
            "capacity": str(money(line.balance + mine)),
        })
    return JsonResponse({"flat": flat.unit_no, "lines": rows})


# --------------------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------------------


def _report_period(request):
    periods = BillingPeriod.objects.filter(society=request.society)
    period_id = request.GET.get("period")
    period = get_object_or_404(periods, pk=period_id) if period_id else periods.exclude(status=BillingPeriod.DRAFT).first() or periods.first()
    return period, periods


def _csv(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


@require("view")
def bill_register(request):
    period, periods = _report_period(request)
    data = reports.bill_register(period) if period else {"rows": [], "heads": [], "totals": {}}
    if period and request.GET.get("format") == "csv":
        header = ["Bill No", "Unit", "Member"] + [h.name for h in data["heads"]] + [
            "Amount", "Interest", "Bill Amount", "Principal Arrear", "Interest Arrear", "Payable Amount", "Paid", "Balance Amount"]
        rows = [[r["bill"].bill_no, r["bill"].flat.unit_no, r["member"], *r["heads"], r["amount"], r["interest"], r["bill_amount"],
                 r["principal_arrear"], r["interest_arrear"], r["payable"], r["paid"], r["balance"]] for r in data["rows"]]
        return _csv(f"bill-register-{period.period_start:%Y-%m}.csv", header, rows)
    return render(request, "society/report_bill_register.html", {"period": period, "periods": periods, **data})


@require("view")
def collection_sheet(request):
    period, periods = _report_period(request)
    data = reports.collection_sheet(period, _date_param(request, "from"), _date_param(request, "to")) if period else {"rows": []}
    if period and request.GET.get("format") == "csv":
        header = ["Bill No", "Unit", "Member", "Bill Amount", "Interest", "Arrears", "Total Amount", "Amount Received", "Cheque / Ref", "Payment Date", "Bank / Cash", "Balance"]
        rows = [[r["bill"].bill_no, r["bill"].flat.unit_no, r["member"], r["amount"], r["interest"], r["arrears"],
                 r["payable"], r["received"], " / ".join(x.cheque_no or x.transaction_ref for x in r["receipts"]),
                 " / ".join(str(x.receipt_date) for x in r["receipts"]), " / ".join(x.bank_name or x.get_payment_mode_display() for x in r["receipts"]),
                 r["balance"]] for r in data["rows"]]
        return _csv(f"collection-sheet-{period.period_start:%Y-%m}.csv", header, rows)
    return render(request, "society/report_collection.html", {"period": period, "periods": periods, **data})


@require("view")
def receipt_register(request):
    data = reports.receipt_register(request.society, _date_param(request, "from"), _date_param(request, "to"), request.GET.get("status") or None)
    if request.GET.get("format") == "csv":
        rows = [[r["receipt"].receipt_no, r["receipt"].receipt_date, r["receipt"].flat.unit_no, r["receipt"].get_payment_mode_display(),
                 r["receipt"].cheque_no or r["receipt"].transaction_ref, r["receipt"].get_status_display(), r["receipt"].amount, r["allocated"], r["advance"]]
                for r in data["rows"]]
        return _csv("receipt-register.csv", ["Receipt", "Date", "Unit", "Mode", "Cheque / UTR", "Status", "Receipt Amount", "Allocated", "Advance"], rows)
    return render(request, "society/report_receipts.html", {**data, "statuses": Receipt.STATUS_CHOICES, "filters": request.GET})


@require("view")
def outstanding_report(request):
    data = reports.outstanding_by_flat(request.society)
    if request.GET.get("format") == "csv":
        rows = [[r["flat"].unit_no, r["flat"].member_display, r["payable"], r["paid"], r["principal"], r["interest"], r["balance"], r["overdue"], r["advance"]]
                for r in data["rows"]]
        return _csv("outstanding-by-flat.csv", ["Unit", "Member", "Payable", "Paid", "Principal Balance", "Interest Balance", "Balance", "Overdue", "Advance"], rows)
    return render(request, "society/report_outstanding.html", data)


@require("view")
def advance_report(request):
    return render(request, "society/report_advances.html", reports.advance_register(request.society))


@require("view")
def charge_head_summary(request):
    periods = BillingPeriod.objects.filter(society=request.society)
    period = get_object_or_404(periods, pk=request.GET["period"]) if request.GET.get("period") else None
    return render(request, "society/report_charge_heads.html", {"period": period, "periods": periods, **reports.charge_head_summary(request.society, period)})


@require("view")
def flat_statement(request, flat_id):
    flat = get_object_or_404(Flat.objects.select_related("wing"), pk=flat_id, society=request.society)
    data = reports.flat_statement(flat, _date_param(request, "from"), _date_param(request, "to"))
    return render(request, "society/statement.html", {"flat": flat, "filters": request.GET, **data})


@require("audit.view")
def audit_log(request):
    qs = AuditLog.objects.filter(society=request.society).select_related("user")
    for field in ("action", "model_name", "object_id"):
        if request.GET.get(field):
            qs = qs.filter(**{field: request.GET[field]})
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    actions = AuditLog.objects.filter(society=request.society).order_by().values_list("action", flat=True).distinct()
    return render(request, "society/audit_log.html", {"page": page, "actions": sorted(actions), "filters": request.GET})


# --------------------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------------------


@require("import.run")
def import_list(request):
    form = ImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = importer.stage_upload(request.society, form.cleaned_data["kind"], form.cleaned_data["file"], request.user)
            return redirect("import_detail", pk=batch.pk)
        except ValidationError as exc:
            form.add_error("file", _errors(exc))
    batches = ImportBatch.objects.filter(society=request.society).select_related("created_by")
    return render(request, "society/imports.html", {"form": form, "batches": batches})


@require("import.run")
def import_detail(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, society=request.society)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "import":
            _run(request, importer.run_import, "Import completed. Exceptions remain listed below.", batch, request.user)
        elif action == "discard" and batch.status != ImportBatch.IMPORTED:
            batch.status = ImportBatch.DISCARDED
            batch.save(update_fields=["status"])
            audit(request.user, "import_discarded", batch, request.society)
            messages.info(request, "Batch discarded; nothing was imported.")
        return redirect("import_detail", pk=pk)
    if request.GET.get("format") == "csv":
        response = HttpResponse(importer.exception_report_csv(batch), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="import-{batch.pk}-exceptions.csv"'
        return response
    return render(request, "society/import_detail.html", {"batch": batch, "rows": batch.rows.all()})
