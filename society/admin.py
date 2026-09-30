from django.contrib import admin

from .models import (
    AuditLog,
    Bill,
    BillingPeriod,
    BillLine,
    BillOpeningItem,
    ChargeHead,
    Flat,
    FlatChargeRule,
    FlatMember,
    ImportBatch,
    ImportRow,
    InterestCharge,
    InterestSegment,
    Member,
    Receipt,
    ReceiptAllocation,
    ReceiptAllocationReversal,
    Society,
    SocietyMembership,
    Wing,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Financial evidence is read-only in the admin. All changes go through services.py so the
    ledger invariants, locking and audit logging cannot be bypassed."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Society)
class SocietyAdmin(admin.ModelAdmin):
    list_display = ("name", "registration_number", "interest_rate_pa", "interest_enabled", "allocation_policy", "is_active")
    readonly_fields = ("next_bill_number", "next_receipt_number")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SocietyMembership)
class SocietyMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "society", "role", "is_active")
    list_filter = ("role", "is_active", "society")


@admin.register(ChargeHead)
class ChargeHeadAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "society", "charge_type", "apportionment_basis", "knockoff_priority", "system_code", "is_active")
    list_filter = ("society", "charge_type", "apportionment_basis", "is_active")


admin.site.register([Wing, Flat, Member, FlatMember, FlatChargeRule])


@admin.register(BillingPeriod)
class BillingPeriodAdmin(ReadOnlyAdmin):
    list_display = ("name", "society", "status", "bill_date", "due_date", "interest_rate_pa", "approved_by", "locked_at")
    list_filter = ("society", "status")


@admin.register(Bill)
class BillAdmin(ReadOnlyAdmin):
    list_display = ("bill_no", "society", "period", "flat", "status", "bill_date", "due_date")
    list_filter = ("society", "status", "period")


@admin.register(BillLine)
class BillLineAdmin(ReadOnlyAdmin):
    list_display = ("component_code", "bill", "line_type", "amount", "confirmed")
    list_filter = ("line_type", "confirmed")


@admin.register(Receipt)
class ReceiptAdmin(ReadOnlyAdmin):
    list_display = ("receipt_no", "society", "receipt_date", "flat", "amount", "status")
    list_filter = ("society", "status", "payment_mode")


@admin.register(ReceiptAllocation)
class ReceiptAllocationAdmin(ReadOnlyAdmin):
    list_display = ("id", "receipt", "bill_line", "amount", "mode", "created_by", "created_at")


@admin.register(ReceiptAllocationReversal)
class ReceiptAllocationReversalAdmin(ReadOnlyAdmin):
    list_display = ("allocation", "amount", "reason", "created_by", "created_at")


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "society", "user", "action", "model_name", "object_id")
    list_filter = ("society", "action", "model_name")


admin.site.register([BillOpeningItem, InterestCharge, InterestSegment, ImportBatch, ImportRow], ReadOnlyAdmin)
