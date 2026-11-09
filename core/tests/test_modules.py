import uuid
from datetime import timedelta

import pytest
from django.core import signing
from django.urls import reverse

from core import clock, modules
from core.auth.actor import resolve_actor
from core.models import ModuleSettings, Person, Role, RoleAssignment
from meetings.models import FollowUp
from tracker.models import Initiative
from tracker.services import triage

pytestmark = pytest.mark.django_db

TR, OWN, AUD = "TR1", "OWN1", "AUD1"


@pytest.fixture
def world(db):
    for emp in (TR, OWN, AUD):
        Person.objects.create(emp_id=emp, display_name=emp)
    RoleAssignment.create("SYS", emp_id=TR, role=Role.TECH_RISK)
    RoleAssignment.create("SYS", emp_id=AUD, role=Role.AUDITOR)
    init = Initiative.create("SYS", name="Alpha", owner_emp_id=OWN, status=Initiative.Status.ACTIVE, phase="Build")
    FollowUp.create("SYS", meeting_entity=uuid.uuid4(), initiative_entity=init.entity_id, text="Chase vendor",
                    due_date=clock.today() - timedelta(days=5))
    return init


def switch(**flags):
    """Save a new version of the module settings."""
    row = modules.current()
    fields = {**{k: True for k in modules.KEYS}, **flags, "change_note": "test"}
    return row.new_version("SYS", **fields) if row else ModuleSettings.create("SYS", **fields)


def as_user(client, emp):
    client.post("/auth/switch/", {"emp_id": emp})
    return client


def nav(client, path="/workbench/"):
    return client.get(path).content.decode()


# --- helper and versioning ---


def test_no_row_means_all_on():
    assert modules.current() is None
    assert all(modules.enabled(k) for k in modules.KEYS)


def test_settings_are_versioned_not_overwritten(world):
    first = switch(meetings=False)
    second = switch(meetings=False, audit=False)
    assert ModuleSettings.objects.count() == 2
    first.refresh_from_db()
    assert first.valid_to is not None and second.entity_id == first.entity_id
    assert modules.current().pk == second.pk
    assert modules.states() == {"owner_access": True, "meetings": False, "audit": False, "share_links": True}


# --- meetings ---


def test_meetings_off_hides_nav_tab_and_urls(client, world):
    as_user(client, TR)
    assert "Meetings" in nav(client)
    assert 'tab=meetings' in nav(client, f"/initiative/{world.entity_id}/")
    switch(meetings=False)
    assert 'href="/meetings/"' not in nav(client)
    page = nav(client, f"/initiative/{world.entity_id}/")
    assert "tab=meetings" not in page and "Open follow-ups" not in page
    assert client.get(f"/initiative/{world.entity_id}/?tab=meetings").status_code == 200
    assert client.get("/meetings/").status_code == 404
    assert client.get("/meetings/new/").status_code == 404


def test_meetings_off_drops_triage_followups(world):
    def chase():
        return [r.what for r in triage.triage()[0]["Chase"]]

    assert any("Follow-up overdue" in w for w in chase())
    rows, _ = triage.triage(followups=False)
    assert not any("Follow-up overdue" in r.what for r in rows["Chase"])


def test_meetings_off_workbench_has_no_followup_rows(client, world):
    as_user(client, TR)
    assert "Follow-up overdue" in nav(client)
    switch(meetings=False)
    assert "Follow-up overdue" not in nav(client)


def test_meetings_back_on_restores_data(client, world):
    as_user(client, TR)
    switch(meetings=False)
    assert client.get("/meetings/").status_code == 404
    switch()
    assert client.get("/meetings/").status_code == 200
    assert FollowUp.objects.current().filter(text="Chase vendor").exists()
    assert "Follow-up overdue" in nav(client)


# --- audit ---


def test_audit_off_blocks_auditor_and_hides_pages(client, world):
    as_user(client, AUD)
    assert client.get("/audit/").status_code == 200
    switch(audit=False)
    assert client.get("/audit/").status_code == 404
    assert client.get("/audit/snapshot/1/").status_code == 404
    assert client.get("/exec/").status_code == 403
    assert resolve_actor(AUD).is_auditor  # stored role is untouched
    as_user(client, TR)
    assert 'href="/audit/"' not in nav(client)
    assert client.get(f"/initiative/{world.entity_id}/history/").status_code == 200


def test_audit_back_on_restores_auditor(client, world):
    switch(audit=False)
    as_user(client, AUD)
    assert client.get("/audit/").status_code == 404
    switch()
    assert client.get("/audit/").status_code == 200


# --- owner access ---


def test_owner_access_off_denies_owner(client, world):
    as_user(client, OWN)
    assert client.get("/my/").status_code in (200, 302)
    switch(owner_access=False)
    assert "No role" in nav(client, "/")
    assert client.get("/my/").status_code == 403
    assert client.get(f"/initiative/{world.entity_id}/").status_code == 404
    page = nav(client, "/")
    assert "My initiative" not in page and "Intake form" not in page


def test_owner_access_off_actor_flags(world):
    from core.auth.actor import apply_modules

    off = {"owner_access": False, "meetings": True, "audit": True, "share_links": True}
    owner = apply_modules(resolve_actor(OWN), off)
    assert not owner.is_any_owner and not owner.is_owner_of(world.entity_id)
    assert not owner.can_edit_initiative(world.entity_id)
    tr = apply_modules(resolve_actor(TR), off)
    assert tr.can_edit_initiative(world.entity_id) and tr.is_tech_risk


def test_owner_access_off_tech_risk_keeps_intake(client, world):
    switch(owner_access=False)
    as_user(client, TR)
    assert "Intake form" in nav(client)
    assert client.get("/intake/").status_code == 200


def test_owner_back_on_restores_access(client, world):
    switch(owner_access=False)
    as_user(client, OWN)
    assert client.get("/my/").status_code == 403
    switch()
    assert client.get("/my/").status_code in (200, 302)
    assert Initiative.objects.current().filter(owner_emp_id=OWN).exists()


# --- share links ---


def test_share_links_off(client, world):
    token = signing.dumps({"snapshot": None})
    assert client.get(reverse("exec_shared", args=[token])).status_code == 200
    as_user(client, TR)
    assert "Read-only link:" in nav(client, "/exec/")
    switch(share_links=False)
    assert client.get(reverse("exec_shared", args=[token])).status_code == 404
    assert "Read-only link:" not in nav(client, "/exec/")
    switch()
    assert client.get(reverse("exec_shared", args=[token])).status_code == 200


# --- banner and admin form ---


def test_banner_only_for_tech_risk_when_something_off(client, world):
    as_user(client, TR)
    assert "Off:" not in nav(client)
    switch(meetings=False, audit=False)
    assert "Off: Meetings, Audit access" in nav(client)
    as_user(client, AUD)
    assert "Off:" not in nav(client, "/exec/")


def test_admin_form_saves_new_version_and_lists_changes(client, world):
    as_user(client, TR)
    r = client.post(reverse("set_modules"), {"owner_access": "on", "audit": "on", "change_note": "Lighter setup"}, follow=True)
    assert "Meetings off; Read-only links off." in r.content.decode()
    row = modules.current()
    assert (row.meetings, row.share_links, row.owner_access, row.audit) == (False, False, True, True)
    assert row.change_note == "Lighter setup" and row.author_emp_id == TR
    page = r.content.decode()
    assert "Last changed by" in page and "Lighter setup" in page
    client.post(reverse("set_modules"), {k: "on" for k in modules.KEYS} | {"change_note": "Back"})
    assert ModuleSettings.objects.count() == 2 and modules.states() == dict.fromkeys(modules.KEYS, True)


def test_admin_form_requires_change_note(client, world):
    as_user(client, TR)
    client.post(reverse("set_modules"), {k: "on" for k in modules.KEYS if k != "meetings"})
    assert modules.current() is None


@pytest.mark.parametrize("emp", [OWN, AUD])
def test_admin_form_forbidden_for_others(client, world, emp):
    as_user(client, emp)
    assert client.post(reverse("set_modules"), {"change_note": "x"}).status_code == 403
    assert modules.current() is None
