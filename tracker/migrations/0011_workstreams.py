# Adds workstreams and the optional workstream tag on plan, risk, control and check-in rows.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0010_watch_list"),
    ]

    operations = [
        migrations.AddField(
            model_name="controlimpact",
            name="workstream_entity",
            field=models.UUIDField(
                blank=True,
                help_text="Workstream this belongs to; blank means the whole initiative",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="milestone",
            name="workstream_entity",
            field=models.UUIDField(
                blank=True,
                help_text="Workstream this belongs to; blank means the whole initiative",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="phase",
            name="workstream_entity",
            field=models.UUIDField(
                blank=True,
                help_text="Workstream this belongs to; blank means the whole initiative",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="riskitem",
            name="workstream_entity",
            field=models.UUIDField(
                blank=True,
                help_text="Workstream this belongs to; blank means the whole initiative",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="update",
            name="workstream_entity",
            field=models.UUIDField(
                blank=True,
                help_text="Workstream this belongs to; blank means the whole initiative",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="Workstream",
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
                ("initiative_entity", models.UUIDField(db_index=True)),
                ("name", models.CharField(max_length=100)),
                ("lead_emp_id", models.CharField(blank=True, max_length=32)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("description", models.CharField(blank=True, max_length=300)),
            ],
            options={
                "ordering": ["order"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("valid_to__isnull", True)),
                        fields=("entity_id",),
                        name="tracker_workstream_one_current_version",
                    )
                ],
            },
        ),
    ]
