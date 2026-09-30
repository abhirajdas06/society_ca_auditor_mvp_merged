"""Staged CSV import: upload -> validate -> preview -> import -> exception report.

Existing master records are never overwritten. A row that matches an existing record is
reported as DUPLICATE and skipped; the operator fixes it through the normal edit screens.
"""

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ChargeHead, Flat, FlatChargeRule, FlatMember, ImportBatch, ImportRow, Member, Wing
from .services import audit

# Header aliases accepted from the CA spreadsheets (case/space-insensitive).
ALIASES = {
    "unit_no": {"unit_no", "unit", "unit no", "flat", "flat no", "unit number"},
    "wing": {"wing", "building", "bldg"},
    "floor": {"floor"},
    "area_sqft": {"area", "area_sqft", "unit area", "area (sq ft)", "sq ft"},
    "unit_type": {"unit_type", "type", "unit type"},
    "member": {"member", "member name", "name", "owner", "member / joint member"},
    "joint_member": {"joint member", "joint_member", "jt member"},
    "mobile": {"mobile", "phone", "contact"},
    "email": {"email", "e-mail"},
    "charge_head": {"charge_head", "charge head", "head", "head code"},
    "amount": {"amount", "configured_amount", "monthly amount"},
    "quantity": {"quantity", "qty"},
    "rate": {"rate"},
    "effective_from": {"effective_from", "effective from", "from"},
    "effective_to": {"effective_to", "effective to", "to"},
}

REQUIRED = {
    ImportBatch.SOCIETY_REGISTER: {"unit_no", "member"},
    ImportBatch.CHARGE_RULES: {"unit_no", "charge_head", "effective_from"},
}


def _canonical(header):
    h = (header or "").strip().lower().replace("_", " ")
    for key, names in ALIASES.items():
        if h in {n.replace("_", " ") for n in names}:
            return key
    return None


def _dec(value, field, errors, default=None):
    value = (value or "").strip().replace(",", "").replace("₹", "")
    if not value:
        return default
    try:
        result = Decimal(value)
    except InvalidOperation:
        errors.append(f"{field}: '{value}' is not a number")
        return None
    if result < 0:
        errors.append(f"{field}: cannot be negative")
    return result


def _date(value, field, errors):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    errors.append(f"{field}: '{value}' is not a date (use YYYY-MM-DD or DD-MM-YYYY)")
    return None


@transaction.atomic
def stage_upload(society, kind, uploaded_file, actor):
    raw = uploaded_file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("The file is not UTF-8 encoded CSV.")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        raise ValidationError("The file is empty.")
    mapping = {i: _canonical(h) for i, h in enumerate(headers)}
    present = {v for v in mapping.values() if v}
    missing = REQUIRED[kind] - present
    if missing:
        raise ValidationError(f"Missing required column(s): {', '.join(sorted(missing))}. Found: {', '.join(headers)}")
    batch = ImportBatch.objects.create(society=society, kind=kind, file_name=uploaded_file.name[:255], created_by=actor)
    rows = []
    for n, record in enumerate(reader, start=2):
        if not any(c.strip() for c in record):
            continue
        data = {mapping[i]: v.strip() for i, v in enumerate(record) if i in mapping and mapping[i]}
        rows.append(ImportRow(batch=batch, row_number=n, data=data))
    ImportRow.objects.bulk_create(rows)
    audit(actor, "import_uploaded", batch, society, kind=kind, file=batch.file_name, rows=len(rows))
    validate_batch(batch)
    return batch


def _validate_register_row(batch, row, seen):
    errors, society = [], batch.society
    d = row.data
    unit = d.get("unit_no", "").upper()
    if not unit:
        errors.append("unit_no is required")
    if not d.get("member"):
        errors.append("member is required")
    if unit in seen:
        errors.append(f"unit {unit} appears more than once in the file")
    seen.add(unit)
    _dec(d.get("area_sqft"), "area_sqft", errors)
    if d.get("unit_type") and d["unit_type"].lower() not in {"residential", "commercial"}:
        errors.append("unit_type must be residential or commercial")
    if errors:
        return ImportRow.ERROR, errors, ""
    if Flat.objects.filter(society=society, unit_no__iexact=unit).exists():
        return ImportRow.DUPLICATE, [f"Flat {unit} already exists — not overwritten"], f"Flat {unit}"
    return ImportRow.VALID, [], f"Flat {unit}"


def _validate_rule_row(batch, row, seen):
    errors, society = [], batch.society
    d = row.data
    unit = d.get("unit_no", "").upper()
    flat = Flat.objects.filter(society=society, unit_no__iexact=unit).first()
    if not flat:
        errors.append(f"flat {unit or '(blank)'} does not exist")
    head = ChargeHead.objects.filter(society=society, code__iexact=d.get("charge_head", "")).first()
    if not head:
        errors.append(f"charge head {d.get('charge_head') or '(blank)'} does not exist")
    elif head.is_interest:
        errors.append("interest is calculated by the interest engine, not imported as a rule")
    start = _date(d.get("effective_from"), "effective_from", errors)
    end = _date(d.get("effective_to"), "effective_to", errors)
    if start and end and end < start:
        errors.append("effective_to is before effective_from")
    amount = _dec(d.get("amount"), "amount", errors)
    _dec(d.get("quantity"), "quantity", errors)
    rate = _dec(d.get("rate"), "rate", errors)
    if head and head.charge_type == ChargeHead.FIXED and amount is None:
        errors.append("amount is required for a fixed charge head")
    if head and head.charge_type == ChargeHead.VARIABLE and rate is None and amount is None:
        errors.append("rate (or amount) is required for a variable charge head")
    key = (unit, (d.get("charge_head") or "").upper(), start)
    if key in seen:
        errors.append("duplicate flat/head/effective_from in the file")
    seen.add(key)
    if errors:
        return ImportRow.ERROR, errors, ""
    target = f"{unit} {head.code} from {start}"
    if FlatChargeRule.objects.filter(flat=flat, charge_head=head, effective_from=start).exists():
        return ImportRow.DUPLICATE, ["Rule with the same effective date already exists — not overwritten"], target
    candidate = FlatChargeRule(flat=flat, charge_head=head, effective_from=start, effective_to=end)
    try:
        candidate.clean()
    except ValidationError as exc:
        return ImportRow.ERROR, exc.messages, target
    return ImportRow.VALID, [], target


@transaction.atomic
def validate_batch(batch):
    if batch.status not in {ImportBatch.UPLOADED, ImportBatch.VALIDATED}:
        raise ValidationError("Only uploaded batches can be validated.")
    check = _validate_register_row if batch.kind == ImportBatch.SOCIETY_REGISTER else _validate_rule_row
    seen = set()
    counts = {ImportRow.VALID: 0, ImportRow.ERROR: 0, ImportRow.DUPLICATE: 0}
    for row in batch.rows.all():
        row.status, row.messages, row.target = check(batch, row, seen)
        row.save(update_fields=["status", "messages", "target"])
        counts[row.status] += 1
    batch.status = ImportBatch.VALIDATED
    batch.summary = counts
    batch.save(update_fields=["status", "summary"])
    return batch


def _import_register_row(batch, row):
    d, society = row.data, batch.society
    unit = d["unit_no"].upper()
    wing_code = (d.get("wing") or "".join(ch for ch in unit if ch.isalpha())[:1] or "-").upper()
    wing, _ = Wing.objects.get_or_create(society=society, code=wing_code)
    flat = Flat.objects.create(
        society=society, wing=wing, unit_no=unit, floor=d.get("floor", ""),
        area_sqft=_dec(d.get("area_sqft"), "area", []) or Decimal("0"),
        unit_type=(d.get("unit_type") or Flat.RESIDENTIAL).lower(),
    )
    owner = Member.objects.create(society=society, full_name=d["member"], mobile=d.get("mobile", ""), email=d.get("email", ""))
    FlatMember.objects.create(flat=flat, member=owner, role=FlatMember.OWNER)
    if d.get("joint_member"):
        joint = Member.objects.create(society=society, full_name=d["joint_member"])
        FlatMember.objects.create(flat=flat, member=joint, role=FlatMember.JOINT)
    return f"Flat {unit} (#{flat.pk})"


def _import_rule_row(batch, row):
    d, society = row.data, batch.society
    flat = Flat.objects.get(society=society, unit_no__iexact=d["unit_no"])
    head = ChargeHead.objects.get(society=society, code__iexact=d["charge_head"])
    errors = []
    amount = _dec(d.get("amount"), "amount", errors) or Decimal("0")
    quantity = _dec(d.get("quantity"), "quantity", errors, Decimal("1"))
    rate = _dec(d.get("rate"), "rate", errors)
    if rate is None:
        rate = amount
    rule = FlatChargeRule(
        flat=flat, charge_head=head, configured_amount=amount, default_quantity=quantity, default_rate=rate,
        effective_from=_date(d["effective_from"], "effective_from", errors), effective_to=_date(d.get("effective_to"), "effective_to", errors),
        resolution_reference=f"Import batch {batch.pk}",
    )
    rule.full_clean()
    rule.save()
    return f"{flat.unit_no} {head.code} from {rule.effective_from} (#{rule.pk})"


@transaction.atomic
def run_import(batch, actor):
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != ImportBatch.VALIDATED:
        raise ValidationError("Validate the batch before importing.")
    validate_batch(batch)  # Re-validate against the current database state.
    do = _import_register_row if batch.kind == ImportBatch.SOCIETY_REGISTER else _import_rule_row
    imported = 0
    for row in batch.rows.filter(status=ImportRow.VALID):
        row.target = do(batch, row)
        row.status = ImportRow.IMPORTED
        row.save(update_fields=["status", "target"])
        imported += 1
    batch.status, batch.imported_by, batch.imported_at = ImportBatch.IMPORTED, actor, timezone.now()
    batch.summary = {**batch.summary, "imported": imported}
    batch.save(update_fields=["status", "imported_by", "imported_at", "summary"])
    audit(actor, "import_completed", batch, batch.society, kind=batch.kind, summary=batch.summary)
    return batch


def exception_report_csv(batch):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["row", "status", "target", "messages", "data"])
    for row in batch.rows.exclude(status__in=[ImportRow.VALID, ImportRow.IMPORTED]):
        writer.writerow([row.row_number, row.get_status_display(), row.target, "; ".join(row.messages), row.data])
    return out.getvalue()
