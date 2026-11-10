# Adds comments on initiatives and watch items.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0011_workstreams"),
    ]

    operations = [
        migrations.CreateModel(
            name="Comment",
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
                    "target_entity",
                    models.UUIDField(
                        db_index=True, help_text="Initiative or watch item entity"
                    ),
                ),
                ("commenter_emp_id", models.CharField(db_index=True, max_length=32)),
                (
                    "author_name",
                    models.CharField(
                        blank=True, help_text="Display name when posted", max_length=120
                    ),
                ),
                ("text", models.TextField(max_length=1000)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("published", "Published"),
                            ("dismissed", "Dismissed"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("reviewed_by", models.CharField(blank=True, max_length=32)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("valid_to__isnull", True)),
                        fields=("entity_id",),
                        name="tracker_comment_one_current_version",
                    )
                ],
            },
        ),
    ]
