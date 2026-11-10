# Meeting series and the series link on meetings (schema only).

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meetings", "0002_what_who_result_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="series_entity",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="MeetingSeries",
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
                ("name", models.CharField(max_length=200)),
                (
                    "cadence",
                    models.CharField(
                        choices=[
                            ("weekly", "Weekly"),
                            ("biweekly", "Every 2 weeks"),
                            ("monthly", "Monthly"),
                            ("adhoc", "Ad hoc"),
                        ],
                        default="weekly",
                        max_length=8,
                    ),
                ),
                ("usual_attendees", models.TextField(blank=True)),
                (
                    "initiative_entities",
                    models.JSONField(
                        blank=True, default=list, help_text="Initiative entity ids"
                    ),
                ),
                (
                    "default_stage",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        choices=[
                            (0, "0. Intake"),
                            (1, "1. Mobilize & Design"),
                            (2, "2. Build & Configure"),
                            (3, "3. Test & Validate"),
                            (4, "4. Cutover & Go-live"),
                            (5, "5. Hypercare & Stabilize"),
                            (6, "6. Close"),
                        ],
                        null=True,
                    ),
                ),
                ("description", models.CharField(blank=True, max_length=300)),
            ],
            options={
                "ordering": ["name"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("valid_to__isnull", True)),
                        fields=("entity_id",),
                        name="meetings_meetingseries_one_current_version",
                    )
                ],
            },
        ),
    ]
