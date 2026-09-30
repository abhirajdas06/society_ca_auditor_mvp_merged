import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

MONEY = dict(max_digits=14, decimal_places=2)
ZERO = Decimal("0.00")


def compliance_effective_default():
    return datetime.date(2026, 6, 30)


class Society(models.Model):
    MEMBER_DIRECTED = "member_directed"
    PRINCIPAL_THEN_INTEREST = "principal_then_interest"
    OLDEST_DUE = "oldest_due"
    ALLOCATION_POLICY_CHOICES = [
        (MEMBER_DIRECTED, "Member-directed / manual"),
        (PRINCIPAL_THEN_INTEREST, "Oldest bill: principal then interest"),
        (OLDEST_DUE, "Oldest due component"),
    ]

    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=120, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, default="Mumbai")
    state = models.CharField(max_length=100, default="Maharashtra")
    pincode = models.CharField(max_length=10, blank=True)
    default_bill_day = models.PositiveSmallIntegerField(default=5)
    default_due_day = models.PositiveSmallIntegerField(default=25)

    # Society-approved interest configuration. The rate used for a billing period is
    # snapshotted onto BillingPeriod so historical bills stay reproducible.
    interest_enabled = models.BooleanField(default=True)
    interest_rate_pa = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("12.00"))
    interest_grace_days = models.PositiveSmallIntegerField(default=0)
    interest_on_interest = models.BooleanField(default=False)
    compliance_effective_date = models.DateField(default=compliance_effective_default)
    interest_resolution_reference = models.CharField(max_length=120, blank=True)
    interest_resolution_date = models.DateField(null=True, blank=True)

    # Used only when the payer gives no allocation instruction. Never presented as legal appropriation.
    allocation_policy = models.CharField(max_length=40, default=MEMBER_DIRECTED, choices=ALLOCATION_POLICY_CHOICES)
    # When enabled, the user who generated a billing period cannot approve it.
    enforce_maker_checker = models.BooleanField(default=True)

    # Per-society document sequences. Locked with select_for_update when a number is assigned.
    next_bill_number = models.PositiveIntegerField(default=1)
    next_receipt_number = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SocietyMembership(models.Model):
    """Grants a user a role within one society. Permissions are always checked server-side."""

    CA = "ca"
    OPERATOR = "operator"
    SOCIETY_ADMIN = "society_admin"
    AUDITOR = "auditor"
    ROLE_CHOICES = [
        (CA, "CA / Accountant"),
        (OPERATOR, "Operator"),
        (SOCIETY_ADMIN, "Society Admin"),
        (AUDITOR, "Auditor (read-only)"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="society_memberships")
    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "society"], name="uniq_society_membership")]

    def __str__(self):
        return f"{self.user} @ {self.society} ({self.get_role_display()})"


class Wing(models.Model):
    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="wings")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    floors = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["society", "code"], name="uniq_wing_code")]
        ordering = ["code"]

    def __str__(self):
        return self.code


class Flat(models.Model):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    TYPE_CHOICES = [(RESIDENTIAL, "Residential"), (COMMERCIAL, "Commercial")]

    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="flats")
    wing = models.ForeignKey(Wing, on_delete=models.PROTECT, related_name="flats")
    unit_no = models.CharField(max_length=30)
    floor = models.CharField(max_length=30, blank=True)
    area_sqft = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    unit_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=RESIDENTIAL)
    occupancy_status = models.CharField(max_length=30, default="self_occupied")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["society", "unit_no"], name="uniq_flat_unit")]
        ordering = ["wing__code", "unit_no"]

    def __str__(self):
        return self.unit_no

    def clean(self):
        if self.wing_id and self.society_id and self.wing.society_id != self.society_id:
            raise ValidationError("Wing belongs to a different society.")

    @property
    def primary_member(self):
        link = self.member_links.filter(role=FlatMember.OWNER).select_related("member").order_by("id").first()
        return link.member if link else None

    @property
    def member_display(self):
        names = [x.member.full_name for x in self.member_links.all() if x.role in (FlatMember.OWNER, FlatMember.JOINT)]
        return " & ".join(names)


class Member(models.Model):
    society = models.ForeignKey(Society, on_delete=models.PROTECT, related_name="members")
    member_no = models.CharField(max_length=50, blank=True)
    title = models.CharField(max_length=20, blank=True)
    full_name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    pan = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class FlatMember(models.Model):
    OWNER = "owner"
    JOINT = "joint"
    NOMINEE = "nominee"
    ROLE_CHOICES = [(OWNER, "Owner"), (JOINT, "Joint Member"), (NOMINEE, "Nominee")]

    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="member_links")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="flat_links")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=OWNER)
    from_date = models.DateField(default=timezone.localdate)
    to_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["flat", "member", "role"], name="uniq_flat_member_role")]

    def clean(self):
        if self.flat_id and self.member_id and self.member.society_id and self.member.society_id != self.flat.society_id:
            raise ValidationError("Member belongs to a different society.")


class ChargeHead(models.Model):
    FIXED = "fixed"
    VARIABLE = "variable"
    CHARGE_TYPE_CHOICES = [(FIXED, "Fixed"), (VARIABLE, "Variable")]

    # Apportionment basis is the statutory/bye-law basis. It is deliberately separate from
    # Fixed/Variable, which is only application behaviour.
    FLAT_EQUAL = "flat_equal"
    CARPET_AREA = "carpet_area"
    SANCTIONED_INLET = "sanctioned_inlet"
    BUILDING_EQUAL = "building_equal"
    ACTUAL_MEASUREMENT = "actual_measurement"
    GENERAL_BODY_RATE = "gb_rate"
    MANUAL = "manual"
    APPORTIONMENT_CHOICES = [
        (FLAT_EQUAL, "Equally by flats / units"),
        (CARPET_AREA, "By carpet area"),
        (SANCTIONED_INLET, "By sanctioned water inlet / approved basis"),
        (BUILDING_EQUAL, "Equally within building / wing"),
        (ACTUAL_MEASUREMENT, "By actual measurement / consumption"),
        (GENERAL_BODY_RATE, "General Body approved rate"),
        (MANUAL, "Manual / society-specific"),
    ]

    INTEREST_SYSTEM_CODE = "INT"

    society = models.ForeignKey(Society, on_delete=models.PROTECT, related_name="charge_heads")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE_CHOICES, default=FIXED)
    apportionment_basis = models.CharField(max_length=30, choices=APPORTIONMENT_CHOICES, default=MANUAL)
    ledger_code = models.CharField(max_length=30, blank=True)
    system_code = models.CharField(max_length=30, blank=True, help_text="Short prefix used in component references, e.g. MAIN, WATER, INT.")
    resolution_reference = models.CharField(max_length=120, blank=True)
    resolution_date = models.DateField(null=True, blank=True)
    # Used only by automatic knock-off. Member-directed allocation always wins.
    knockoff_priority = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["society", "code"], name="uniq_charge_head_society_code")]
        ordering = ["knockoff_priority", "name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def is_interest(self):
        return self.system_code == self.INTEREST_SYSTEM_CODE


class FlatChargeRule(models.Model):
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="charge_rules")
    charge_head = models.ForeignKey(ChargeHead, on_delete=models.PROTECT, related_name="flat_rules")

    # Fixed head: configured_amount is the monthly amount.
    # Variable head: default_quantity * default_rate is the draft amount; it must be confirmed before issue.
    configured_amount = models.DecimalField(**MONEY, default=Decimal("0"))
    default_quantity = models.DecimalField(**MONEY, default=Decimal("1"))
    default_rate = models.DecimalField(**MONEY, default=Decimal("0"))

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    resolution_reference = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["flat", "charge_head", "effective_from"], name="uniq_flat_charge_effective")]
        ordering = ["flat__unit_no", "charge_head__knockoff_priority", "effective_from"]

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Effective-to cannot be before effective-from.")
        if self.configured_amount < 0 or self.default_quantity < 0 or self.default_rate < 0:
            raise ValidationError("Charge values cannot be negative.")
        if self.flat_id and self.charge_head_id:
            if self.charge_head.society_id != self.flat.society_id:
                raise ValidationError("Charge head belongs to a different society.")
            if self.charge_head.is_interest:
                raise ValidationError("Interest is calculated by the interest engine, not by a flat charge rule.")
            if self.active and self.effective_from:
                overlapping = FlatChargeRule.objects.filter(flat_id=self.flat_id, charge_head_id=self.charge_head_id, active=True).exclude(pk=self.pk)
                far_future = datetime.date.max
                for other in overlapping:
                    if self.effective_from <= (other.effective_to or far_future) and other.effective_from <= (self.effective_to or far_future):
                        raise ValidationError(
                            f"Overlaps an existing rule effective {other.effective_from} to {other.effective_to or 'open'}. "
                            "Close the earlier rule (set effective-to) before adding a new rate."
                        )


class BillingPeriod(models.Model):
    DRAFT = "draft"
    GENERATED = "generated"
    REVIEW = "review"
    APPROVED = "approved"
    LOCKED = "locked"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (GENERATED, "Generated"),
        (REVIEW, "Review"),
        (APPROVED, "Approved"),
        (LOCKED, "Locked"),
    ]

    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="billing_periods")
    name = models.CharField(max_length=60)
    period_start = models.DateField()
    period_end = models.DateField()
    bill_date = models.DateField()
    due_date = models.DateField()
    interest_calculation_date = models.DateField(null=True, blank=True)
    # Snapshot of the society-approved simple-interest rate used for this billing period.
    interest_rate_pa = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("12.00"))
    interest_resolution_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="generated_billing_periods"
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_billing_periods"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_billing_periods"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="locked_billing_periods"
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["society", "period_start", "period_end"], name="uniq_billing_period")]
        ordering = ["-period_start"]

    def __str__(self):
        return self.name

    @property
    def is_closed(self):
        return self.status in {self.APPROVED, self.LOCKED}


class Bill(models.Model):
    DRAFT = "draft"
    ISSUED = "issued"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (ISSUED, "Issued"), (CANCELLED, "Cancelled")]

    society = models.ForeignKey(Society, on_delete=models.PROTECT, related_name="bills")
    period = models.ForeignKey(BillingPeriod, on_delete=models.PROTECT, related_name="bills")
    flat = models.ForeignKey(Flat, on_delete=models.PROTECT, related_name="bills")
    bill_no = models.CharField(max_length=30)
    bill_date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    generated_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="issued_bills")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="cancelled_bills")
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["period", "flat"], condition=~Q(status="cancelled"), name="uniq_live_bill_period_flat"),
            models.UniqueConstraint(fields=["society", "bill_no"], name="uniq_bill_no_per_society"),
        ]
        ordering = ["bill_no"]

    def __str__(self):
        return self.bill_no

    def _lines(self):
        return list(self.lines.all())

    @property
    def current_charge_total(self):
        return sum((x.amount for x in self._lines() if x.line_type == BillLine.CHARGE), ZERO)

    @property
    def current_interest_total(self):
        return sum((x.amount for x in self._lines() if x.line_type == BillLine.INTEREST), ZERO)

    @property
    def current_total(self):
        return self.current_charge_total + self.current_interest_total

    def _opening(self, line_type):
        return sum((x.snapshot_amount for x in self.opening_items.all() if x.source_line.line_type == line_type), ZERO)

    @property
    def opening_principal(self):
        return self._opening(BillLine.CHARGE)

    @property
    def opening_interest(self):
        return self._opening(BillLine.INTEREST)

    @property
    def opening_total(self):
        return self.opening_principal + self.opening_interest

    @property
    def total_payable(self):
        """Presentation total as printed on the bill: arrears snapshot + current components."""
        return self.opening_total + self.current_total

    @property
    def current_paid(self):
        return sum((x.paid for x in self._lines() if x.is_receivable), ZERO)

    @property
    def current_balance(self):
        return sum((x.balance for x in self._lines() if x.is_receivable), ZERO)

    @property
    def opening_balance_now(self):
        return sum((x.current_balance_snapshot() for x in self.opening_items.all()), ZERO)

    @property
    def total_paid(self):
        return self.total_payable - self.current_outstanding

    @property
    def current_outstanding(self):
        return self.opening_balance_now + self.current_balance

    @property
    def primary_member(self):
        return self.flat.primary_member


class BillLine(models.Model):
    CHARGE = "charge"
    INTEREST = "interest"
    ADJUSTMENT = "adjustment"
    LINE_TYPES = [(CHARGE, "Principal"), (INTEREST, "Interest"), (ADJUSTMENT, "Adjustment")]

    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="lines")
    charge_head = models.ForeignKey(ChargeHead, on_delete=models.PROTECT, related_name="bill_lines", null=True, blank=True)
    line_type = models.CharField(max_length=20, choices=LINE_TYPES, default=CHARGE)
    # Stable human-readable reference, e.g. MAIN-APR-2026 or INT-APR-2026-C20260531.
    component_code = models.CharField(max_length=60)
    service_period_start = models.DateField()
    service_period_end = models.DateField()
    due_date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(**MONEY, default=Decimal("1"))
    rate = models.DecimalField(**MONEY, default=Decimal("0"))
    amount = models.DecimalField(**MONEY, default=Decimal("0"))
    confirmed = models.BooleanField(default=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="confirmed_bill_lines")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["bill", "component_code"], name="uniq_bill_component_code")]
        ordering = ["service_period_start", "line_type", "component_code", "id"]

    def __str__(self):
        return self.display_code

    @property
    def is_receivable(self):
        return self.line_type in {self.CHARGE, self.INTEREST} and self.amount > 0

    @property
    def display_code(self):
        """Reference shown to users. Always carries month and year (e.g. MAIN-APR-2026)."""
        return self.component_code.split("-C2")[0] if self.line_type == self.INTEREST else self.component_code

    @property
    def reference(self):
        """Unambiguous database identity: display code + bill number + line id."""
        return f"{self.display_code} / Bill {self.bill.bill_no} / #{self.pk}"

    @property
    def paid(self):
        cached = getattr(self, "paid_amount", None)
        if cached is not None:
            return Decimal(cached).quantize(ZERO)
        from .services import bill_line_paid

        return bill_line_paid(self)

    @property
    def signed_balance(self):
        return self.amount - self.paid

    @property
    def balance(self):
        # Over-allocation is rejected at write time, so the displayed balance is never negative.
        # signed_balance preserves the raw accounting state for diagnostics.
        return max(self.signed_balance, ZERO)


class BillOpeningItem(models.Model):
    """Arrears shown on a later bill. A presentation snapshot that points to the original receivable;
    it never creates a second receivable for the same economic debt."""

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="opening_items")
    source_line = models.ForeignKey(BillLine, on_delete=models.PROTECT, related_name="opening_snapshots")
    snapshot_amount = models.DecimalField(**MONEY)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["bill", "source_line"], name="uniq_bill_opening_source")]

    def current_balance_snapshot(self):
        return min(self.snapshot_amount, self.source_line.balance)


class InterestCharge(models.Model):
    """Calculation record for one interest BillLine. Together with its segments it answers:
    what principal was outstanding, over which dates, at what rate, and which payment reduced it."""

    bill_line = models.OneToOneField(BillLine, on_delete=models.PROTECT, related_name="interest_charge")
    source_period_start = models.DateField()
    source_period_end = models.DateField()
    calculation_date = models.DateField()
    from_date = models.DateField()
    to_date = models.DateField()
    days = models.PositiveIntegerField(default=0)
    rate_pa = models.DecimalField(max_digits=6, decimal_places=2)
    base_amount = models.DecimalField(**MONEY, default=Decimal("0"))
    interest_on_interest = models.BooleanField(default=False)
    formula = models.CharField(max_length=255, blank=True)
    source_lines = models.ManyToManyField(BillLine, related_name="interest_as_base", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class InterestSegment(models.Model):
    """One constant-principal slice of an interest calculation."""

    interest_charge = models.ForeignKey(InterestCharge, on_delete=models.CASCADE, related_name="segments")
    source_line = models.ForeignKey(BillLine, on_delete=models.PROTECT, related_name="interest_segments")
    from_date = models.DateField()
    to_date = models.DateField()
    days = models.PositiveIntegerField()
    principal = models.DecimalField(**MONEY)
    rate_pa = models.DecimalField(max_digits=6, decimal_places=2)
    amount = models.DecimalField(max_digits=16, decimal_places=4)
    # The allocation whose payment date ends this segment (reducing principal), if any.
    ended_by_allocation = models.ForeignKey("ReceiptAllocation", on_delete=models.PROTECT, null=True, blank=True, related_name="interest_segments_ended")

    class Meta:
        ordering = ["source_line_id", "from_date"]


class Receipt(models.Model):
    CASH = "cash"
    CHEQUE = "cheque"
    NEFT = "neft"
    RTGS = "rtgs"
    UPI = "upi"
    BANK_TRANSFER = "bank_transfer"
    OTHER = "other"
    PAYMENT_CHOICES = [
        (CASH, "Cash"),
        (CHEQUE, "Cheque"),
        (NEFT, "NEFT"),
        (RTGS, "RTGS"),
        (UPI, "UPI"),
        (BANK_TRANSFER, "Bank Transfer"),
        (OTHER, "Other"),
    ]

    RECEIVED = "received"
    CLEARED = "cleared"
    BOUNCED = "bounced"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (RECEIVED, "Received / Pending clearance"),
        (CLEARED, "Cleared / Realised"),
        (BOUNCED, "Bounced"),
        (CANCELLED, "Cancelled"),
    ]
    EFFECTIVE_STATUSES = (RECEIVED, CLEARED)

    receipt_no = models.CharField(max_length=30)
    society = models.ForeignKey(Society, on_delete=models.PROTECT, related_name="receipts")
    flat = models.ForeignKey(Flat, on_delete=models.PROTECT, related_name="receipts")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts")
    receipt_date = models.DateField(default=timezone.localdate)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_CHOICES)
    amount = models.DecimalField(**MONEY)
    cheque_no = models.CharField(max_length=50, blank=True)
    cheque_date = models.DateField(null=True, blank=True)
    bank_name = models.CharField(max_length=150, blank=True)
    bank_branch = models.CharField(max_length=150, blank=True)
    transaction_ref = models.CharField("UTR / transaction reference", max_length=100, blank=True)
    narration = models.CharField(max_length=255, blank=True)
    allocation_instruction = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=RECEIVED)
    status_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_receipts")
    created_at = models.DateTimeField(auto_now_add=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="cleared_receipts")
    bounced_at = models.DateTimeField(null=True, blank=True)
    bounced_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="bounced_receipts")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="cancelled_receipts")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["society", "receipt_no"], name="uniq_receipt_no_per_society"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="receipt_amount_positive"),
        ]
        ordering = ["-receipt_date", "-id"]

    def __str__(self):
        return self.receipt_no

    @property
    def allocated_amount(self):
        cached = getattr(self, "allocated_total", None)
        if cached is not None:
            return Decimal(cached).quantize(ZERO) if self.is_effective else ZERO
        from .services import receipt_allocated

        return receipt_allocated(self)

    @property
    def unallocated_amount(self):
        """ADVANCE / unapplied = receipt amount - effective allocations (zero once bounced/cancelled)."""
        if not self.is_effective:
            return ZERO
        return max(self.amount - self.allocated_amount, ZERO)

    @property
    def is_effective(self):
        return self.status in self.EFFECTIVE_STATUSES


class ReceiptAllocation(models.Model):
    MANUAL = "manual"
    AUTO = "auto"
    REALLOCATION = "reallocation"
    MODE_CHOICES = [(MANUAL, "Manual / member-directed"), (AUTO, "Automatic policy"), (REALLOCATION, "Reallocation")]

    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, related_name="allocations")
    bill_line = models.ForeignKey(BillLine, on_delete=models.PROTECT, related_name="receipt_allocations")
    amount = models.DecimalField(**MONEY)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MANUAL)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receipt_allocations_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive")]
        ordering = ["id"]

    @property
    def reversed_amount(self):
        return sum((r.amount for r in self.reversals.all()), ZERO)

    @property
    def effective_amount(self):
        return max(self.amount - self.reversed_amount, ZERO)

    remaining_amount = effective_amount


class ReceiptAllocationReversal(models.Model):
    allocation = models.ForeignKey(ReceiptAllocation, on_delete=models.PROTECT, related_name="reversals")
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receipt_allocation_reversals_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="reversal_amount_positive")]
        ordering = ["id"]


class ImportBatch(models.Model):
    SOCIETY_REGISTER = "society_register"
    CHARGE_RULES = "charge_rules"
    KIND_CHOICES = [
        (SOCIETY_REGISTER, "Society register (wings, flats, members)"),
        (CHARGE_RULES, "Flat charge rules (fixed / variable amounts)"),
    ]
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    IMPORTED = "imported"
    DISCARDED = "discarded"
    STATUS_CHOICES = [(UPLOADED, "Uploaded"), (VALIDATED, "Validated"), (IMPORTED, "Imported"), (DISCARDED, "Discarded")]

    society = models.ForeignKey(Society, on_delete=models.PROTECT, related_name="import_batches")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=UPLOADED)
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="import_batches")
    created_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="imported_batches")
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ImportRow(models.Model):
    VALID = "valid"
    ERROR = "error"
    DUPLICATE = "duplicate"
    IMPORTED = "imported"
    STATUS_CHOICES = [(VALID, "Valid"), (ERROR, "Error"), (DUPLICATE, "Exists — skipped"), (IMPORTED, "Imported")]

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    data = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=VALID)
    messages = models.JSONField(default=list, blank=True)
    target = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["row_number"]


class AuditLog(models.Model):
    society = models.ForeignKey(Society, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=80)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["society", "-created_at"]), models.Index(fields=["model_name", "object_id"])]
