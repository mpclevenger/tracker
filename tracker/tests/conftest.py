import pytest

from core.models import Role, RoleAssignment
from tracker.models import Initiative


@pytest.fixture
def make_initiative(db):
    def _make(name="Init", owner="OWN1", status=Initiative.Status.ACTIVE, **kw):
        return Initiative.create("AUTH", name=name, owner_emp_id=owner, status=status, **kw)

    return _make


@pytest.fixture
def grant(db):
    def _grant(emp_id, role, initiative_entity=None):
        return RoleAssignment.create(
            "AUTH", emp_id=emp_id, role=role, initiative_entity=initiative_entity
        )

    return _grant


@pytest.fixture
def roles():
    return Role
