import pytest

from core.auth.actor import ANONYMOUS, Actor, resolve_actor
from core.models import Person, Role, RoleAssignment
from tracker.models import Delegation, Initiative

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("emp_id", [None, ""])
def test_empty_emp_id_is_anonymous(emp_id):
    actor = resolve_actor(emp_id)
    assert actor is ANONYMOUS
    assert not actor.is_authenticated


def test_unknown_emp_id_has_no_permissions():
    actor = resolve_actor("NOBODY")
    assert actor.is_authenticated
    assert actor.display_name == "NOBODY"
    assert actor.roles == frozenset()
    assert actor.owned_initiatives == frozenset()
    assert actor.visible_layers() == frozenset()


def test_display_name_from_person():
    Person.objects.create(emp_id="E1", display_name="Eve")
    assert resolve_actor("E1").display_name == "Eve"


@pytest.mark.parametrize(
    "role,prop",
    [
        (Role.TECH_RISK, "is_tech_risk"),
        (Role.AUDITOR, "is_auditor"),
        (Role.SENIOR_MANAGEMENT, "is_senior_management"),
    ],
)
def test_global_roles(grant, role, prop):
    grant("E1", role)
    actor = resolve_actor("E1")
    assert role in actor.roles
    for p in ("is_tech_risk", "is_auditor", "is_senior_management"):
        assert getattr(actor, p) is (p == prop)


def test_multiple_global_roles(grant):
    grant("E1", Role.AUDITOR)
    grant("E1", Role.SENIOR_MANAGEMENT)
    assert resolve_actor("E1").roles == {Role.AUDITOR, Role.SENIOR_MANAGEMENT}


def test_roles_are_per_employee(grant):
    grant("E1", Role.AUDITOR)
    assert resolve_actor("E2").roles == frozenset()


def test_scoped_role_is_not_global(grant, make_initiative):
    init = make_initiative()
    grant("E1", Role.AUDITOR, initiative_entity=init.entity_id)
    assert resolve_actor("E1").roles == frozenset()


def test_owner_via_initiative_owner_emp_id(make_initiative):
    init = make_initiative(owner="E1")
    other = make_initiative(name="Other", owner="E2")
    actor = resolve_actor("E1")
    assert actor.is_any_owner
    assert actor.is_owner_of(init.entity_id)
    assert not actor.is_owner_of(other.entity_id)
    assert actor.owned_initiatives == {init.entity_id}


def test_owner_via_delegation(make_initiative):
    init = make_initiative(owner="E2")
    Delegation.create("E2", initiative_entity=init.entity_id, delegate_emp_id="E1")
    actor = resolve_actor("E1")
    assert actor.is_owner_of(init.entity_id)
    assert actor.can_edit_initiative(init.entity_id)


def test_retired_delegation_revokes(make_initiative):
    init = make_initiative(owner="E2")
    d = Delegation.create("E2", initiative_entity=init.entity_id, delegate_emp_id="E1")
    d.retire("E2")
    assert not resolve_actor("E1").is_any_owner


def test_owner_via_scoped_role_assignment(grant, make_initiative):
    init = make_initiative(owner="E2")
    grant("E1", Role.OWNER, initiative_entity=init.entity_id)
    actor = resolve_actor("E1")
    assert actor.is_owner_of(init.entity_id)
    assert actor.roles == frozenset()


def test_global_owner_role_without_scope_grants_no_initiatives(grant, make_initiative):
    make_initiative(owner="E2")
    grant("E1", Role.OWNER)
    actor = resolve_actor("E1")
    assert Role.OWNER in actor.roles
    assert actor.owned_initiatives == frozenset()


def test_retired_initiative_does_not_grant_ownership(make_initiative):
    init = make_initiative(owner="E1")
    init.retire("A")
    assert not resolve_actor("E1").is_any_owner


def test_owner_reassigned_via_new_version(make_initiative):
    init = make_initiative(owner="E1")
    init.new_version("A", owner_emp_id="E2")
    assert not resolve_actor("E1").is_any_owner
    assert resolve_actor("E2").is_any_owner


def test_retired_role_assignment_revokes(grant):
    ra = grant("E1", Role.TECH_RISK)
    assert resolve_actor("E1").is_tech_risk
    ra.retire("A")
    assert not resolve_actor("E1").is_tech_risk
    assert RoleAssignment.objects.history(ra.entity_id).count() == 2


def test_retired_scoped_owner_role_revokes(grant, make_initiative):
    init = make_initiative(owner="E2")
    ra = grant("E1", Role.OWNER, initiative_entity=init.entity_id)
    ra.retire("A")
    assert not resolve_actor("E1").is_any_owner


def test_visible_layers_tech_risk(grant):
    grant("E1", Role.TECH_RISK)
    assert resolve_actor("E1").visible_layers() == {"internal", "owner", "exec"}


@pytest.mark.parametrize("role", [Role.AUDITOR, Role.SENIOR_MANAGEMENT])
def test_visible_layers_exec_roles(grant, role, make_initiative):
    init = make_initiative()
    grant("E1", role)
    actor = resolve_actor("E1")
    assert actor.visible_layers() == {"exec"}
    assert actor.visible_layers(init.entity_id) == {"exec"}


def test_visible_layers_owner_scoped_to_initiative(make_initiative):
    x = make_initiative(name="X", owner="E1")
    y = make_initiative(name="Y", owner="E2")
    actor = resolve_actor("E1")
    assert actor.visible_layers(x.entity_id) == {"owner", "exec"}
    assert actor.visible_layers(y.entity_id) == frozenset()
    assert actor.visible_layers() == frozenset()


def test_visible_layers_no_roles():
    assert resolve_actor("E1").visible_layers() == frozenset()
    assert ANONYMOUS.visible_layers() == frozenset()


def test_auditor_owner_combines_layers(grant, make_initiative):
    x = make_initiative(owner="E1")
    grant("E1", Role.AUDITOR)
    assert resolve_actor("E1").visible_layers(x.entity_id) == {"owner", "exec"}


@pytest.mark.parametrize(
    "roles,internal,override,exec_pages",
    [
        (set(), False, False, False),
        ({Role.TECH_RISK}, True, True, True),
        ({Role.AUDITOR}, False, True, True),
        ({Role.SENIOR_MANAGEMENT}, False, False, True),
        ({Role.OWNER}, False, False, False),
        ({Role.AUDITOR, Role.SENIOR_MANAGEMENT}, False, True, True),
    ],
)
def test_visibility_truth_table(roles, internal, override, exec_pages):
    actor = Actor(emp_id="E1", roles=frozenset(roles))
    assert actor.sees_internal is internal
    assert actor.sees_override_detail is override
    assert actor.sees_exec_pages is exec_pages


def test_can_edit_initiative(grant, make_initiative):
    x = make_initiative(owner="E1")
    y = make_initiative(name="Y", owner="E2")
    grant("E3", Role.TECH_RISK)
    assert resolve_actor("E1").can_edit_initiative(x.entity_id)
    assert not resolve_actor("E1").can_edit_initiative(y.entity_id)
    assert resolve_actor("E3").can_edit_initiative(y.entity_id)
    assert not resolve_actor("E4").can_edit_initiative(y.entity_id)
