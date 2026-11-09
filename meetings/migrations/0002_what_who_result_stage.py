"""Meetings use a standard What / Who / Result structure and are tagged to a lifecycle stage."""

from django.db import migrations, models

import tracker.lifecycle


def fill_what(apps, schema_editor):
    Meeting = apps.get_model("meetings", "Meeting")
    Meeting.objects.filter(what="").update(what="Meeting")


class Migration(migrations.Migration):

    dependencies = [
        ("meetings", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="meeting", old_name="title", new_name="what"),
        migrations.RenameField(model_name="meeting", old_name="attendees", new_name="who"),
        migrations.RenameField(model_name="meeting", old_name="key_points", new_name="result"),
        migrations.RunPython(fill_what, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="meeting", name="what",
            field=models.CharField(help_text="Topic or purpose", max_length=200),
        ),
        migrations.AlterField(model_name="meeting", name="who", field=models.TextField(help_text="Attendees")),
        migrations.AlterField(
            model_name="meeting", name="result", field=models.TextField(help_text="Outcome and decisions"),
        ),
        migrations.AddField(
            model_name="meeting", name="stage",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, choices=tracker.lifecycle.STAGE_CHOICES,
            ),
        ),
    ]
