"""Guidance prose moves to TRAC: add stage name and TRAC link; drop the prose columns.

Existing rows get their stage name from the seeded defaults before the prose columns are removed.
"""

from django.db import migrations, models

DEFAULT_NAMES = {
    0: "Intake",
    1: "Mobilise & Design",
    2: "Build & Configure",
    3: "Test & Validate",
    4: "Cutover & Go-live",
    5: "Hypercare & Stabilise",
    6: "Close",
}


def fill_names(apps, schema_editor):
    GuidancePage = apps.get_model("tracker", "GuidancePage")
    for stage, name in DEFAULT_NAMES.items():
        GuidancePage.objects.filter(stage=stage).update(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0002_lifecycle_and_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="guidancepage",
            name="name",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="guidancepage",
            name="trac_url",
            field=models.URLField(blank=True, help_text="Stage guidance page in TRAC", max_length=500),
        ),
        migrations.RunPython(fill_names, migrations.RunPython.noop),
        migrations.RemoveField(model_name="guidancepage", name="auditor_question"),
        migrations.RemoveField(model_name="guidancepage", name="controls"),
        migrations.RemoveField(model_name="guidancepage", name="evidence"),
        migrations.RemoveField(model_name="guidancepage", name="gate"),
        migrations.RemoveField(model_name="guidancepage", name="method"),
    ]
