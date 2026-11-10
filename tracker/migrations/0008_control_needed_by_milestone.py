# Adds the optional needed-by milestone to control impacts.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0007_gate_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="controlimpact",
            name="needed_by_milestone",
            field=models.UUIDField(
                blank=True,
                help_text="Key milestone the control must be ready by; blank means go-live",
                null=True,
            ),
        ),
    ]
