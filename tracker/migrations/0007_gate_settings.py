# Versioned gate settings: requirement matrix and oversight question labels.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0006_label_updates"),
    ]

    operations = [
        migrations.CreateModel(
            name="GateSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "entity_id",
                    models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
                ),
                (
                    "valid_from",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "valid_to",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("author_emp_id", models.CharField(max_length=32)),
                ("is_deleted", models.BooleanField(default=False)),
                (
                    "matrix",
                    models.JSONField(
                        help_text="{level: [None, stage 1..6 requirement]}"
                    ),
                ),
                (
                    "question_labels",
                    models.JSONField(
                        blank=True, default=dict, help_text="{field key: label}"
                    ),
                ),
                ("version_label", models.CharField(max_length=20)),
                ("change_note", models.CharField(blank=True, max_length=300)),
                ("approved_by", models.CharField(blank=True, max_length=200)),
                ("approved_on", models.DateField(blank=True, null=True)),
            ],
            options={
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("valid_to__isnull", True)),
                        fields=("entity_id",),
                        name="tracker_gatesettings_one_current_version",
                    )
                ],
            },
        ),
    ]
