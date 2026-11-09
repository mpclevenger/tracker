import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracker.models import Snapshot, SnapshotRow, Update

pytestmark = pytest.mark.django_db


@pytest.fixture
def snapshot():
    return Snapshot.objects.create(taken_at=timezone.now(), taken_by="E1", label="Q1")


def test_create_snapshot_and_row(snapshot):
    ent = uuid.uuid4()
    row = SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=ent, frozen_json={"a": 1})
    assert snapshot.pk is not None
    assert list(snapshot.rows.all()) == [row]
    assert str(snapshot) == "Q1"


def test_snapshot_save_existing_raises(snapshot):
    snapshot.label = "changed"
    with pytest.raises(ValueError):
        snapshot.save()
    snapshot.refresh_from_db()
    assert snapshot.label == "Q1"


def test_snapshot_delete_raises(snapshot):
    with pytest.raises(ValueError):
        snapshot.delete()
    assert Snapshot.objects.filter(pk=snapshot.pk).exists()


def test_snapshot_row_save_existing_raises(snapshot):
    row = SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=uuid.uuid4(), frozen_json={})
    row.frozen_json = {"x": 1}
    with pytest.raises(ValueError):
        row.save()
    row.refresh_from_db()
    assert row.frozen_json == {}


def test_snapshot_row_delete_raises(snapshot):
    row = SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=uuid.uuid4(), frozen_json={})
    with pytest.raises(ValueError):
        row.delete()
    assert SnapshotRow.objects.filter(pk=row.pk).exists()


# Queryset-level update()/delete() bypass Model.save()/delete(), so the models do not
# block them; immutability is enforced only for instance-level writes. Not asserted here.


def test_unique_row_per_initiative(snapshot):
    ent = uuid.uuid4()
    SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=ent, frozen_json={})
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=ent, frozen_json={})


def test_same_initiative_allowed_in_different_snapshots(snapshot):
    ent = uuid.uuid4()
    other = Snapshot.objects.create(taken_at=timezone.now(), taken_by="E1", label="Q2")
    SnapshotRow.objects.create(snapshot=snapshot, initiative_entity=ent, frozen_json={})
    SnapshotRow.objects.create(snapshot=other, initiative_entity=ent, frozen_json={})
    assert SnapshotRow.objects.count() == 2


def test_update_second_save_raises():
    u = Update(initiative_entity=uuid.uuid4(), author_emp_id="E1", posted_at=timezone.now(), text="hi")
    u.save()
    assert u.pk is not None
    u.text = "edited"
    with pytest.raises(ValueError):
        u.save()
    u.refresh_from_db()
    assert u.text == "hi"
