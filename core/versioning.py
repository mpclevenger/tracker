"""Append-only versioning.

Every mutable business entity is a row-per-version table. A logical entity is identified by
`entity_id` (stable UUID); each row is one version with a [valid_from, valid_to) interval.
The current version has valid_to = NULL. Nothing is ever updated in place except closing
the interval of the previous version. Deletions are tombstone versions (is_deleted=True).

Cross-entity references use the target's `entity_id`, never its row primary key.
"""

import uuid
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

SYSTEM_FIELDS = {"id", "entity_id", "valid_from", "valid_to", "author_emp_id", "is_deleted"}


class VersionedQuerySet(models.QuerySet):
    def current(self):
        return self.filter(valid_to__isnull=True, is_deleted=False)

    def current_including_deleted(self):
        return self.filter(valid_to__isnull=True)

    def as_of(self, at):
        """Versions that were current at instant `at` (tombstones excluded)."""
        return self.filter(valid_from__lte=at, is_deleted=False).filter(
            models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=at)
        )

    def history(self, entity_id):
        return self.filter(entity_id=entity_id).order_by("valid_from")

    def get_current(self, entity_id):
        return self.current().get(entity_id=entity_id)


class VersionedModel(models.Model):
    entity_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    valid_from = models.DateTimeField(default=timezone.now, db_index=True)
    valid_to = models.DateTimeField(null=True, blank=True, db_index=True)
    author_emp_id = models.CharField(max_length=32)
    is_deleted = models.BooleanField(default=False)

    objects = VersionedQuerySet.as_manager()

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["entity_id"],
                condition=models.Q(valid_to__isnull=True),
                name="%(app_label)s_%(class)s_one_current_version",
            )
        ]

    # --- field introspection -------------------------------------------------

    @classmethod
    def versioned_field_names(cls):
        return [
            f.name
            for f in cls._meta.concrete_fields
            if f.name not in SYSTEM_FIELDS
        ]

    def versioned_values(self):
        return {name: getattr(self, name) for name in self.versioned_field_names()}

    # --- lifecycle -----------------------------------------------------------

    @classmethod
    def create(cls, author_emp_id, **fields):
        obj = cls(author_emp_id=author_emp_id, **fields)
        obj.full_clean(exclude=["valid_to"])
        obj.save(force_insert=True)
        return obj

    @transaction.atomic
    def new_version(self, author_emp_id, **changes):
        """Close this (current) version and insert a successor carrying `changes`."""
        if self.valid_to is not None:
            raise ValueError("Only the current version can be superseded")
        at = timezone.now()
        if at <= self.valid_from:
            at = self.valid_from + timedelta(microseconds=1)
        closed = type(self).objects.filter(pk=self.pk, valid_to__isnull=True).update(valid_to=at)
        if closed != 1:
            raise ValueError("Version was already superseded")
        successor = type(self)(
            entity_id=self.entity_id,
            valid_from=at,
            author_emp_id=author_emp_id,
            is_deleted=self.is_deleted,
            **self.versioned_values(),
        )
        for name, value in changes.items():
            if name in SYSTEM_FIELDS and name != "is_deleted":
                raise ValueError(f"Cannot set system field {name}")
            setattr(successor, name, value)
        successor.full_clean(exclude=["valid_to"])
        successor.save(force_insert=True)
        return successor

    def retire(self, author_emp_id):
        return self.new_version(author_emp_id, is_deleted=True)

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.get("force_insert"):
            raise ValueError(
                "Versioned rows are immutable; use new_version() to record a change"
            )
        return super().save(*args, **kwargs)

    # --- diffs ---------------------------------------------------------------

    def diff_from(self, previous):
        """Field-level changes from `previous` version to this one: {field: (old, new)}."""
        if previous is None:
            return {name: (None, value) for name, value in self.versioned_values().items()}
        out = {}
        mine, theirs = self.versioned_values(), previous.versioned_values()
        for name in mine:
            if mine[name] != theirs.get(name):
                out[name] = (theirs.get(name), mine[name])
        if self.is_deleted != previous.is_deleted:
            out["is_deleted"] = (previous.is_deleted, self.is_deleted)
        return out

    def previous_version(self):
        return (
            type(self)
            .objects.filter(entity_id=self.entity_id, valid_from__lt=self.valid_from)
            .order_by("-valid_from")
            .first()
        )


def field_history(model, entity_id):
    """Chronological list of (version, diff_from_previous) for an entity."""
    versions = list(model.objects.history(entity_id))
    out, prev = [], None
    for v in versions:
        out.append((v, v.diff_from(prev)))
        prev = v
    return out
