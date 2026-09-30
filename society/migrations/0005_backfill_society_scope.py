"""Backfill society ownership for records that were previously global, and convert the
legacy auth-group roles into per-society memberships. No financial row is modified."""

from django.db import migrations

GROUP_ROLE_MAP = {"CA": "ca", "Operator": "operator", "Society Admin": "society_admin", "Auditor": "auditor"}


def forwards(apps, schema_editor):
    Society = apps.get_model("society", "Society")
    Bill = apps.get_model("society", "Bill")
    ChargeHead = apps.get_model("society", "ChargeHead")
    Member = apps.get_model("society", "Member")
    FlatChargeRule = apps.get_model("society", "FlatChargeRule")
    BillLine = apps.get_model("society", "BillLine")
    FlatMember = apps.get_model("society", "FlatMember")
    SocietyMembership = apps.get_model("society", "SocietyMembership")
    Group = apps.get_model("auth", "Group")

    for bill in Bill.objects.filter(society__isnull=True).select_related("period"):
        bill.society_id = bill.period.society_id
        bill.save(update_fields=["society"])

    fallback = Society.objects.order_by("id").first()
    for head in ChargeHead.objects.filter(society__isnull=True):
        used_by = set(FlatChargeRule.objects.filter(charge_head=head).values_list("flat__society_id", flat=True))
        used_by |= set(BillLine.objects.filter(charge_head=head).values_list("bill__period__society_id", flat=True))
        if len(used_by) > 1:
            raise RuntimeError(
                f"Charge head {head.code} is used by several societies {sorted(used_by)}; split it manually before migrating."
            )
        society_id = next(iter(used_by), None) or (fallback.id if fallback else None)
        if society_id is None:
            raise RuntimeError(f"Charge head {head.code} has no society to belong to; create a society first.")
        head.society_id = society_id
        head.save(update_fields=["society"])

    for member in Member.objects.filter(society__isnull=True):
        societies = set(FlatMember.objects.filter(member=member).values_list("flat__society_id", flat=True))
        if len(societies) > 1:
            raise RuntimeError(f"Member {member.pk} is linked to flats in several societies; split the record manually.")
        society_id = next(iter(societies), None) or (fallback.id if fallback else None)
        if society_id is None:
            raise RuntimeError(f"Member {member.pk} has no society to belong to; create a society first.")
        member.society_id = society_id
        member.save(update_fields=["society"])

    for group_name, role in GROUP_ROLE_MAP.items():
        group = Group.objects.filter(name=group_name).first()
        if not group:
            continue
        for user in group.user_set.all():
            for society in Society.objects.filter(is_active=True):
                SocietyMembership.objects.get_or_create(user_id=user.pk, society=society, defaults={"role": role})


class Migration(migrations.Migration):
    dependencies = [("society", "0004_ledger_hardening"), ("auth", "0012_alter_user_first_name_max_length")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
