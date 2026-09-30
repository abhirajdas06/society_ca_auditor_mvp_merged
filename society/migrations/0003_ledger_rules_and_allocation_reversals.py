from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("society", "0002_society_sequences")]

    operations = [
        migrations.AddField(
            model_name="billingperiod",
            name="interest_rate_pa",
            field=models.DecimalField(decimal_places=2, default=Decimal("12.00"), max_digits=6),
        ),
        migrations.AddField(
            model_name="chargehead",
            name="apportionment_basis",
            field=models.CharField(
                choices=[
                    ("flat_equal", "Equally by flats / units"),
                    ("carpet_area", "By carpet area"),
                    ("sanctioned_inlet", "By sanctioned water inlet / approved basis"),
                    ("building_equal", "Equally within building / wing"),
                    ("actual_measurement", "By actual measurement / consumption"),
                    ("gb_rate", "General Body approved rate"),
                    ("manual", "Manual / society-specific"),
                ],
                default="manual",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="chargehead",
            name="resolution_reference",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="chargehead",
            name="resolution_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chargehead",
            name="knockoff_priority",
            field=models.PositiveSmallIntegerField(default=100),
        ),
        migrations.RemoveConstraint(
            model_name="receiptallocation",
            name="uniq_receipt_billline_allocation",
        ),
        migrations.CreateModel(
            name="ReceiptAllocationReversal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("reason", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("allocation", models.ForeignKey(on_delete=models.PROTECT, related_name="reversals", to="society.receiptallocation")),
                ("created_by", models.ForeignKey(on_delete=models.PROTECT, related_name="receipt_allocation_reversals_created", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
