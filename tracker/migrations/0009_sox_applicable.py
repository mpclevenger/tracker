# Adds the SOX applicable flag and backfills it from the SOX in-scope app answer.

from django.db import migrations, models
from django.db.models import F


def backfill(apps, schema_editor):
    apps.get_model("tracker", "Initiative").objects.update(sox_applicable=F("affects_sox_app"))


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0008_control_needed_by_milestone"),
    ]

    operations = [
        migrations.AddField(
            model_name="initiative",
            name="sox_applicable",
            field=models.BooleanField(
                default=False,
                help_text="Relevant to SOX: affects a SOX in-scope app or a SOX control",
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
