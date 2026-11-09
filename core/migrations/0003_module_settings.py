# Module on/off settings (versioned singleton).

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_label_updates"),
    ]

    operations = [
        migrations.CreateModel(
            name="ModuleSettings",
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
                ("owner_access", models.BooleanField(default=True)),
                ("meetings", models.BooleanField(default=True)),
                ("audit", models.BooleanField(default=True)),
                ("share_links", models.BooleanField(default=True)),
                ("change_note", models.CharField(blank=True, max_length=300)),
            ],
            options={
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("valid_to__isnull", True)),
                        fields=("entity_id",),
                        name="core_modulesettings_one_current_version",
                    )
                ],
            },
        ),
    ]
