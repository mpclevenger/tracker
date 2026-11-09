import pytest

from core.auth.actor import ANONYMOUS, resolve_actor
from core.models import Role
from tracker.models import Initiative, RiskItem, Visibility
from tracker.visibility import initiatives_for, items_for

pytestmark = pytest.mark.django_db

S = Initiative.Status


@pytest.fixture
def initiatives(make_initiative):
    return {
        s: make_initiative(name=s.value, owner=f"OWN_{s.value}", status=s)
        for s in (S.PROPOSED, S.ACTIVE, S.CLOSED, S.ARCHIVED)
    }


def names(qs):
    return {i.name for i in qs}


def test_tech_risk_sees_all_statuses(initiatives, grant):
    grant("E1", Role.TECH_RISK)
    assert names(initiatives_for(resolve_actor("E1"))) == {
        "proposed", "active", "closed", "archived"
    }


@pytest.mark.parametrize("role", [Role.AUDITOR, Role.SENIOR_MANAGEMENT])
def test_exec_roles_exclude_proposed(initiatives, grant, role):
    grant("E1", role)
    assert names(initiatives_for(resolve_actor("E1"))) == {"active", "closed", "archived"}


def test_owner_sees_only_own_including_proposed(initiatives):
    actor = resolve_actor("OWN_proposed")
    assert names(initiatives_for(actor)) == {"proposed"}


def test_no_role_actor_sees_none(initiatives):
    assert list(initiatives_for(resolve_actor("NOBODY"))) == []
    assert list(initiatives_for(ANONYMOUS)) == []


def test_auditor_who_owns_proposed_sees_it(initiatives, grant):
    grant("OWN_proposed", Role.AUDITOR)
    assert names(initiatives_for(resolve_actor("OWN_proposed"))) == {
        "proposed", "active", "closed", "archived"
    }


def test_retired_initiative_excluded(initiatives, grant):
    grant("E1", Role.TECH_RISK)
    initiatives[S.ACTIVE].retire("A")
    assert "active" not in names(initiatives_for(resolve_actor("E1")))


def test_initiatives_for_respects_custom_queryset(initiatives, grant):
    grant("E1", Role.TECH_RISK)
    qs = Initiative.objects.current().filter(name="active")
    assert names(initiatives_for(resolve_actor("E1"), qs)) == {"active"}


@pytest.fixture
def items(make_initiative):
    init = make_initiative(name="X", owner="OWN_X")
    other = make_initiative(name="Y", owner="OWN_Y")
    for v in Visibility:
        RiskItem.create(
            "A", initiative_entity=init.entity_id, type="risk", title=v.value,
            rating="H", visibility=v,
        )
    return init, other


def item_titles(actor, init):
    qs = RiskItem.objects.current().filter(initiative_entity=init.entity_id)
    return {i.title for i in items_for(actor, init.entity_id, qs)}


def test_tech_risk_sees_all_layers(items, grant):
    init, _ = items
    grant("E1", Role.TECH_RISK)
    assert item_titles(resolve_actor("E1"), init) == {"internal", "owner", "exec"}


def test_owner_sees_owner_and_exec(items):
    init, _ = items
    assert item_titles(resolve_actor("OWN_X"), init) == {"owner", "exec"}


def test_auditor_sees_exec_only(items, grant):
    init, _ = items
    grant("E1", Role.AUDITOR)
    assert item_titles(resolve_actor("E1"), init) == {"exec"}


def test_owner_of_other_initiative_sees_none(items):
    init, _ = items
    assert item_titles(resolve_actor("OWN_Y"), init) == set()


def test_no_role_sees_none(items):
    init, _ = items
    assert item_titles(resolve_actor("NOBODY"), init) == set()
