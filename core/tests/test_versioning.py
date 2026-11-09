import time
import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Role, RoleAssignment
from core.versioning import field_history
from tracker.models import Initiative

pytestmark = pytest.mark.django_db


def test_create_makes_single_current_row():
    obj = Initiative.create("A1", name="X", owner_emp_id="O1")
    assert obj.pk is not None
    assert obj.valid_to is None
    assert obj.author_emp_id == "A1"
    assert not obj.is_deleted
    assert Initiative.objects.count() == 1
    assert list(Initiative.objects.current()) == [obj]


def test_create_works_for_role_assignment():
    ra = RoleAssignment.create("A1", emp_id="E1", role=Role.AUDITOR)
    assert ra.valid_to is None
    assert ra.initiative_entity is None


def test_new_version_closes_old_and_inserts_successor():
    v1 = Initiative.create("A1", name="X", owner_emp_id="O1", sponsor="S")
    v2 = v1.new_version("A2", name="Y")
    v1.refresh_from_db()
    assert v1.valid_to is not None
    assert v2.valid_to is None
    assert v2.pk != v1.pk
    assert v2.entity_id == v1.entity_id
    assert v2.valid_from == v1.valid_to
    assert v2.name == "Y"
    assert v2.sponsor == "S"
    assert v2.owner_emp_id == "O1"
    assert v2.author_emp_id == "A2"
    assert v1.author_emp_id == "A1"
    assert Initiative.objects.filter(entity_id=v1.entity_id).count() == 2


def test_current_returns_only_latest():
    v1 = Initiative.create("A", name="X", owner_emp_id="O")
    v2 = v1.new_version("A", name="Y")
    other = Initiative.create("A", name="Z", owner_emp_id="O")
    assert set(Initiative.objects.current()) == {v2, other}
    assert Initiative.objects.get_current(v1.entity_id) == v2


def test_history_returns_all_in_order():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    v2 = v1.new_version("A", name="2")
    v3 = v2.new_version("A", name="3")
    Initiative.create("A", name="other", owner_emp_id="O")
    hist = list(Initiative.objects.history(v1.entity_id))
    assert hist == [v1, v2, v3]


def test_as_of_selects_correct_version():
    t_before = timezone.now()
    time.sleep(0.01)
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    time.sleep(0.01)
    t_mid = timezone.now()
    time.sleep(0.01)
    v2 = v1.new_version("A", name="2")
    time.sleep(0.01)
    t_after = timezone.now()

    assert list(Initiative.objects.as_of(t_before)) == []
    assert list(Initiative.objects.as_of(t_mid)) == [v1]
    assert list(Initiative.objects.as_of(t_after)) == [v2]
    assert list(Initiative.objects.as_of(v2.valid_from)) == [v2]


def test_retire_creates_tombstone():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    time.sleep(0.01)
    t_alive = timezone.now()
    time.sleep(0.01)
    tomb = v1.retire("A2")
    time.sleep(0.01)
    t_after = timezone.now()

    assert tomb.is_deleted
    assert tomb.valid_to is None
    assert tomb.author_emp_id == "A2"
    assert tomb.name == "1"
    assert list(Initiative.objects.current()) == []
    assert list(Initiative.objects.current_including_deleted()) == [tomb]
    assert list(Initiative.objects.as_of(t_after)) == []
    assert list(Initiative.objects.as_of(t_alive)) == [v1]
    assert list(Initiative.objects.history(v1.entity_id)) == [v1, tomb]


def test_save_on_existing_row_raises():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    v1.name = "changed"
    with pytest.raises(ValueError):
        v1.save()
    v1.refresh_from_db()
    assert v1.name == "1"


def test_new_version_on_closed_version_raises():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    v1.new_version("A", name="2")
    v1.refresh_from_db()
    with pytest.raises(ValueError):
        v1.new_version("A", name="3")


def test_new_version_on_stale_in_memory_current_raises():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    stale = Initiative.objects.get(pk=v1.pk)
    v1.new_version("A", name="2")
    with pytest.raises(ValueError):
        stale.new_version("A", name="3")
    assert Initiative.objects.filter(entity_id=v1.entity_id).count() == 2


def test_diff_from_previous_lists_only_changes():
    v1 = Initiative.create("A", name="1", owner_emp_id="O", sponsor="S")
    v2 = v1.new_version("A", name="2", phase="Build")
    assert v2.diff_from(v1) == {"name": ("1", "2"), "phase": ("", "Build")}


def test_diff_from_includes_deletion_flag():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    tomb = v1.retire("A")
    assert tomb.diff_from(v1) == {"is_deleted": (False, True)}


def test_diff_from_none_lists_all_fields():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    diff = v1.diff_from(None)
    assert set(diff) == set(Initiative.versioned_field_names())
    assert diff["name"] == (None, "1")
    assert all(old is None for old, _ in diff.values())


def test_previous_version():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    v2 = v1.new_version("A", name="2")
    assert v1.previous_version() is None
    assert v2.previous_version() == v1


def test_field_history_pairs():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    v2 = v1.new_version("A", name="2")
    v3 = v2.new_version("A", status=Initiative.Status.ACTIVE)
    out = field_history(Initiative, v1.entity_id)
    assert [v for v, _ in out] == [v1, v2, v3]
    assert out[0][1] == v1.diff_from(None)
    assert out[1][1] == {"name": ("1", "2")}
    assert out[2][1] == {"status": ("proposed", "active")}


@pytest.mark.parametrize(
    "field", ["entity_id", "valid_from", "valid_to", "id"]
)
def test_new_version_rejects_system_fields(field):
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    with pytest.raises(ValueError):
        v1.new_version("A", **{field: uuid.uuid4() if field == "entity_id" else None})
    assert Initiative.objects.filter(entity_id=v1.entity_id).count() == 1
    v1.refresh_from_db()
    assert v1.valid_to is None


def test_failed_new_version_rolls_back_close():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    with pytest.raises(ValueError):
        v1.new_version("A", entity_id=uuid.uuid4())
    v1.refresh_from_db()
    assert v1.valid_to is None


def test_unique_current_constraint():
    v1 = Initiative.create("A", name="1", owner_emp_id="O")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Initiative.objects.create(
                entity_id=v1.entity_id, name="dup", owner_emp_id="O", author_emp_id="A"
            )


def test_role_assignment_versioning_and_retire():
    ra = RoleAssignment.create("A", emp_id="E1", role=Role.AUDITOR)
    ra2 = ra.new_version("A", role=Role.TECH_RISK)
    assert RoleAssignment.objects.current().get().role == Role.TECH_RISK
    ra2.retire("A")
    assert RoleAssignment.objects.current().count() == 0
    assert RoleAssignment.objects.history(ra.entity_id).count() == 3
