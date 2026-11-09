from datetime import timedelta

from django.db import migrations
from django.utils import timezone

RENAMES = {"Mobilise & Design": "Mobilize & Design", "Hypercare & Stabilise": "Hypercare & Stabilize"}
SYSTEM = {"id", "entity_id", "valid_from", "valid_to", "author_emp_id", "is_deleted"}


def _new_version(model, row, field, value):
    at = max(timezone.now(), row.valid_from + timedelta(microseconds=1))
    model.objects.filter(pk=row.pk).update(valid_to=at)
    values = {f.name: getattr(row, f.name) for f in model._meta.concrete_fields if f.name not in SYSTEM}
    values[field] = value
    model.objects.create(entity_id=row.entity_id, valid_from=at, author_emp_id="system",
                         is_deleted=row.is_deleted, **values)


def rename(apps, schema_editor):
    for model_name, field in (("GuidancePage", "name"), ("Initiative", "phase")):
        model = apps.get_model("tracker", model_name)
        for old, new in RENAMES.items():
            for row in list(model.objects.filter(valid_to__isnull=True, **{field: old})):
                _new_version(model, row, field, new)


class Migration(migrations.Migration):
    dependencies = [("tracker", "0004_oversight_evidence_reviews")]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
