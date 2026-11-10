# Adds watch list statuses and fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0009_sox_applicable"),
    ]

    operations = [
        migrations.AddField(
            model_name="initiative",
            name="decision",
            field=models.CharField(
                blank=True, help_text="What is being decided", max_length=300
            ),
        ),
        migrations.AddField(
            model_name="initiative",
            name="decision_due",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="key_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="key_date_label",
            field=models.CharField(
                blank=True, help_text="For example, Contract ends", max_length=60
            ),
        ),
        migrations.AddField(
            model_name="initiative",
            name="next_check",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="options_considered",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="initiative",
            name="outcome_reason",
            field=models.TextField(blank=True, help_text="Why no change is needed"),
        ),
        migrations.AlterField(
            model_name="initiative",
            name="status",
            field=models.CharField(
                choices=[
                    ("watch", "Watch list"),
                    ("dropped", "No change needed"),
                    ("proposed", "Proposed"),
                    ("active", "Active"),
                    ("closed", "Closed"),
                    ("archived", "Archived"),
                ],
                default="proposed",
                max_length=16,
            ),
        ),
    ]
