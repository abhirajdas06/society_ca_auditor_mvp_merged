from django import forms

from .models import BillingPeriod, ChargeHead, Flat, FlatChargeRule, FlatMember, ImportBatch, Member, Receipt, Society, Wing

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _dates(*names):
    return {n: DATE for n in names}


class DependentSelect(forms.Select):
    """A <select> whose options are narrowed by another field's value.

    Each option carries the parent values it belongs to (``data-parent="3 7"``) and the select
    points at its parent (``data-depends-on="id_wing"``). static/society/js/dependent.js does the
    filtering in the browser; every option is still present in the HTML, so the form degrades to a
    plain dropdown without JavaScript. This is display only — the server re-validates the choice.
    """

    def __init__(self, parent_field, option_parents=None, attrs=None):
        super().__init__(attrs)
        self.parent_field = parent_field
        self.option_parents = option_parents or {}

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["data-depends-on"] = f"id_{self.parent_field}"
        return attrs

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        parents = self.option_parents.get(str(getattr(value, "value", value)))
        if parents:
            option["attrs"]["data-parent"] = " ".join(str(p) for p in parents)
        return option


def depends_on(field, parent_field, option_parents):
    """Swap a field's widget for a DependentSelect, carrying over its attrs and choices."""
    widget = DependentSelect(parent_field, option_parents, attrs=field.widget.attrs)
    # A fresh widget has no choices; ModelChoiceField only pushes them on queryset assignment.
    widget.choices = field.choices
    widget.is_required = field.required
    field.widget = widget
    return field


def wing_choices(society):
    return [("", "All wings")] + [(str(w.pk), w.code) for w in Wing.objects.filter(society=society)]


def flats_by_wing(society):
    """{flat id: [wing id]} for dependent flat dropdowns."""
    return {str(pk): [wing_id] for pk, wing_id in Flat.objects.filter(society=society).values_list("pk", "wing_id")}


def members_by_flat(society):
    """{member id: [flat ids]} so the payer dropdown can follow the selected flat."""
    mapping = {}
    for member_id, flat_id in FlatMember.objects.filter(flat__society=society).values_list("member_id", "flat_id"):
        mapping.setdefault(str(member_id), []).append(flat_id)
    return mapping


class WingFilterMixin:
    """Adds an unsaved 'wing' dropdown in front of a 'flat' dropdown and links the two."""

    def add_wing_filter(self, flat_field="flat"):
        self.fields["wing"] = forms.ChoiceField(
            choices=wing_choices(self.society), required=False, label="Wing",
            help_text="Narrows the list below. Not saved on the record.",
        )
        depends_on(self.fields[flat_field], "wing", flats_by_wing(self.society))
        order = ["wing"] + [n for n in self.fields if n != "wing"]
        self.order_fields(order)


class SocietyScopedForm(forms.ModelForm):
    """Restricts every related-object dropdown to the active society and pins the society field."""

    scoped_fields = {}

    def __init__(self, *args, society=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.society = society
        for name, model in self.scoped_fields.items():
            if name in self.fields:
                lookup = "society" if hasattr(model, "society") else "flat__society"
                self.fields[name].queryset = model.objects.filter(**{lookup: society})

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "society_id") and not obj.society_id:
            obj.society = self.society
        if commit:
            obj.save()
            self.save_m2m()
        return obj

    def _post_clean(self):
        if hasattr(self.instance, "society_id") and not self.instance.society_id and self.society:
            self.instance.society = self.society
        super()._post_clean()

    def _get_validation_exclusions(self):
        # Keep (society, code)-style unique constraints validated even though society is not a form field.
        exclusions = super()._get_validation_exclusions()
        exclusions.discard("society")
        return exclusions


class SocietyForm(forms.ModelForm):
    class Meta:
        model = Society
        fields = [
            "name", "registration_number", "registration_date", "address", "city", "state", "pincode",
            "default_bill_day", "default_due_day", "interest_enabled", "interest_rate_pa", "interest_grace_days",
            "compliance_effective_date", "interest_resolution_reference", "interest_resolution_date",
            "allocation_policy", "enforce_maker_checker",
        ]
        widgets = _dates("registration_date", "compliance_effective_date", "interest_resolution_date")
        help_texts = {
            "allocation_policy": "Used only when the payer gives no instruction. It is a product policy, not a legal appropriation rule.",
            "compliance_effective_date": "Date from which the compliance interest ceiling applies. It can bring the ceiling forward, never postpone it.",
        }


class WingForm(SocietyScopedForm):
    class Meta:
        model = Wing
        fields = ["code", "name", "floors"]


class FlatForm(SocietyScopedForm):
    scoped_fields = {"wing": Wing}

    class Meta:
        model = Flat
        fields = ["wing", "unit_no", "floor", "area_sqft", "unit_type", "occupancy_status", "is_active"]


class MemberForm(SocietyScopedForm):
    class Meta:
        model = Member
        fields = ["member_no", "title", "full_name", "mobile", "email", "address", "pan", "is_active"]


class FlatMemberForm(WingFilterMixin, SocietyScopedForm):
    scoped_fields = {"flat": Flat, "member": Member}

    class Meta:
        model = FlatMember
        fields = ["flat", "member", "role", "from_date", "to_date"]
        widgets = _dates("from_date", "to_date")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_wing_filter()


class ChargeHeadForm(SocietyScopedForm):
    class Meta:
        model = ChargeHead
        fields = [
            "code", "name", "charge_type", "apportionment_basis", "ledger_code", "system_code",
            "resolution_reference", "resolution_date", "knockoff_priority", "is_active",
        ]
        widgets = _dates("resolution_date")
        help_texts = {
            "charge_type": "Fixed: amount flows into draft bills automatically. Variable: draft quantity × rate that must be reviewed before issue.",
            "apportionment_basis": "Statutory / bye-law basis. Independent of Fixed/Variable.",
            "knockoff_priority": "Lower is settled first by automatic knock-off. Payer instructions always win.",
        }

    def clean_system_code(self):
        return (self.cleaned_data.get("system_code") or "").strip().upper()


class FlatChargeRuleForm(WingFilterMixin, SocietyScopedForm):
    scoped_fields = {"flat": Flat, "charge_head": ChargeHead}

    class Meta:
        model = FlatChargeRule
        fields = ["flat", "charge_head", "configured_amount", "default_quantity", "default_rate", "effective_from", "effective_to", "active", "resolution_reference"]
        widgets = _dates("effective_from", "effective_to")
        help_texts = {
            "configured_amount": "Fixed heads: monthly amount.",
            "default_quantity": "Variable heads: default quantity for the draft line.",
            "default_rate": "Variable heads: default rate for the draft line.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["charge_head"].queryset = self.fields["charge_head"].queryset.exclude(system_code=ChargeHead.INTEREST_SYSTEM_CODE)
        self.add_wing_filter()
        if self.instance.pk and self.instance_is_billed():
            # A rule already used for billing is history: only its end date / active flag may change.
            # A new rate is recorded as a new rule with a later effective_from.
            for name in ("flat", "charge_head", "configured_amount", "default_quantity", "default_rate", "effective_from"):
                self.fields[name].disabled = True
                self.fields[name].help_text = "Locked: this rule has been used on a bill. Add a new effective-dated rule instead."

    def instance_is_billed(self):
        from .models import Bill, BillLine

        rule = self.instance
        lines = BillLine.objects.filter(
            bill__flat_id=rule.flat_id, charge_head_id=rule.charge_head_id, service_period_start__gte=rule.effective_from
        ).exclude(bill__status=Bill.CANCELLED)
        if rule.effective_to:
            lines = lines.filter(service_period_start__lte=rule.effective_to)
        return lines.exists()


class BillingPeriodForm(SocietyScopedForm):
    class Meta:
        model = BillingPeriod
        fields = ["name", "period_start", "period_end", "bill_date", "due_date", "interest_calculation_date", "interest_rate_pa", "interest_resolution_reference"]
        widgets = _dates("period_start", "period_end", "bill_date", "due_date", "interest_calculation_date")
        help_texts = {
            "interest_calculation_date": "Interest is only created when this date is set. Leave blank for no interest this period.",
            "interest_rate_pa": "Simple interest % p.a. snapshotted for this period.",
        }

    def clean(self):
        data = super().clean()
        start, end = data.get("period_start"), data.get("period_end")
        bill_date, due_date = data.get("bill_date"), data.get("due_date")
        if start and end and end < start:
            self.add_error("period_end", "Period end cannot be before period start.")
        if start and bill_date and bill_date < start:
            self.add_error("bill_date", "Bill date cannot be before the billing period start.")
        if bill_date and due_date and due_date < bill_date:
            self.add_error("due_date", "Due date cannot be before bill date.")
        if data.get("interest_rate_pa") is not None and data["interest_rate_pa"] < 0:
            self.add_error("interest_rate_pa", "Interest rate cannot be negative.")
        return data


class ReceiptForm(WingFilterMixin, SocietyScopedForm):
    scoped_fields = {"flat": Flat, "member": Member}

    class Meta:
        model = Receipt
        fields = [
            "flat", "member", "receipt_date", "payment_mode", "amount", "cheque_no", "cheque_date",
            "bank_name", "bank_branch", "transaction_ref", "narration", "allocation_instruction",
        ]
        widgets = {**_dates("receipt_date", "cheque_date"), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}
        labels = {"allocation_instruction": "Payer allocation instruction (as given by member)", "member": "Payer / member"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["flat"].queryset = self.fields["flat"].queryset.filter(is_active=True).select_related("wing")
        self.add_wing_filter()
        # The payer list follows the flat; services.validate_receipt re-checks the link server-side.
        depends_on(self.fields["member"], "flat", members_by_flat(self.society))

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError("Receipt amount must be greater than zero.")
        return amount


class FlatFilterForm(forms.Form):
    """Wing -> flat picker for list and report screens."""

    wing = forms.ChoiceField(required=False, label="Wing")
    flat = forms.ModelChoiceField(queryset=Flat.objects.none(), required=False, label="Flat", empty_label="All flats")

    def __init__(self, *args, society=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["wing"].choices = wing_choices(society)
        self.fields["flat"].queryset = Flat.objects.filter(society=society).select_related("wing")
        depends_on(self.fields["flat"], "wing", flats_by_wing(society))


class VariableLineForm(forms.Form):
    quantity = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    rate = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0)


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=255)


class ImportUploadForm(forms.Form):
    kind = forms.ChoiceField(choices=ImportBatch.KIND_CHOICES)
    file = forms.FileField(help_text="CSV (UTF-8) with a header row. Save Excel sheets as CSV first.")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError("File is larger than 5 MB.")
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        return f
