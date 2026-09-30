from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("society", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="society",
            name="next_bill_number",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="society",
            name="next_receipt_number",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
