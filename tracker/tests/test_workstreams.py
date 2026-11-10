import uuid

import pytest
from django.urls import reverse

from core.auth.actor import apply_modules, resolve_actor
from core.models import ModuleSettings, Person, Role, RoleAssignment
from scripts.load_initiative import load
from tracker.models import ControlImpact, Milestone, Phase, RiskItem, Snapshot, Update, Workstream
from tracker.services import checkin, rag, snapshots, triage, workstreams as ws_svc
from tracker.services.exec_summary import Tile
from tracker.services.timeline import initiative_svg
from tracker.tests.services_helpers import (
    AUTH,
    TR,
    active_initiative,
    add_control,
    add_milestone,
    add_phase,
    at,
    bundle_of,
    d,
    frozen_clock,  # noqa: F401
    post_update,
)
from tracker.tests.test_owner_intake_views import formset_post

pytestmark = pytest.mark.django_db

OWN, LEAD, TRU = "OWN1", "LEAD1", "TRU1"


def add_ws(init, name, lead="", order=0):
    return Workstream.create(AUTH, initiative_entity=init.entity_id, name=name, lead_emp_id=lead, order=order)


def ws_update(init, ws, days_ago=0):
    return Update.objects.create(
        initiative_entity=init.entity_id, author_emp_id=LEAD, posted_at=at(-days_ago), text="ws update",
        workstream_entity=ws.entity_id if ws else None,
    )


def ws_risk(init, ws, target_date=None):
    return RiskItem.create(
        AUTH, initiative_entity=init.entity_id, type="risk", title="Quota", rating="H", status="open",
        target_date=target_date, workstream_entity=ws.entity_id,
    )


def ws_impact(init, ws, readiness="in_progress"):
    return ControlImpact.create(
        AUTH, initiative_entity=init.entity_id, control_entity=add_control().entity_id, impact_type="modified",
        readiness=readiness, workstream_entity=ws.entity_id,
    )


@pytest.fixture
def people(db):
    for emp in (OWN, LEAD, TRU, "LEAD2"):
        Person.objects.create(emp_id=emp, display_name=f"{emp} Person")
    RoleAssignment.create(AUTH, emp_id=TRU, role=Role.TECH_RISK)


@pytest.fixture
def lake(people):
    """Active initiative with Compute (lead LEAD1) and Admin (lead LEAD2), fresh check-ins everywhere."""
    init = active_initiative(name="Lake", target_go_live=d(100), baseline_go_live=d(100))
    compute, admin = add_ws(init, "Compute", LEAD, 0), add_ws(init, "Admin", "LEAD2", 1)
    post_update(init, days_ago=1)
    ws_update(init, compute, 1)
    ws_update(init, admin, 1)
    return init, compute, admin


def login(client, emp):
    client.post("/auth/switch/", {"emp_id": emp})
    return client


# --- model ---------------------------------------------------------------------------------------------

def test_workstream_defaults_and_null_tags(people):
    init = active_initiative()
    ws = Workstream.create(AUTH, initiative_entity=init.entity_id, name="Front end")
    assert (ws.lead_emp_id, ws.order, ws.description) == ("", 0, "")
    assert add_phase(init).workstream_entity is None
    assert add_milestone(init).workstream_entity is None
    assert Update.objects.create(initiative_entity=init.entity_id, author_emp_id="x", posted_at=at(0), text="t").workstream_entity is None


# --- status ------------------------------------------------------------------------------------------------

def test_no_workstreams_is_unchanged(frozen_clock):
    init = active_initiative()
    ev = rag.evaluate(bundle_of(init), persist=False)
    assert ev.workstreams == [] and ev.effective == "green"


def test_worst_of_rollup_and_chip_prefix(lake, frozen_clock):
    init, compute, admin = lake
    ws_risk(init, compute, target_date=d(-3))
    ev = rag.evaluate(bundle_of(init), persist=False)
    assert ev.effective == "red"
    assert ev.chip == "Compute: High risk past due"
    assert [f[1] for f in ev.fired] == ["Compute: High risk past due"]
    by_name = {w.name: e for w, e in ev.workstreams}
    assert by_name["Compute"].effective == "red" and by_name["Compute"].chip == "High risk past due"
    assert by_name["Admin"].effective == "green"


def test_workstream_milestone_and_go_live_slip(lake, frozen_clock):
    init, compute, _admin = lake
    add_milestone(init, planned=d(10), forecast=d(75), status="slipped", name="Provisioned", workstream_entity=compute.entity_id)
    b = bundle_of(init)
    ev = rag.evaluate_workstream(b, compute)
    chips = [f[1] for f in ev.fired]
    assert "Milestone slipped 65d" in chips and "Go-live slipped 65d" in chips
    assert rag.evaluate_workstream(b, b.workstreams[1]).fired == []


def test_workstream_without_key_milestone_skips_go_live_slip(lake, frozen_clock):
    init, compute, _admin = lake
    add_milestone(init, planned=d(10), forecast=d(75), is_key=False, workstream_entity=compute.entity_id)
    assert rag.evaluate_workstream(bundle_of(init), compute).fired == []


def test_tagged_items_do_not_fire_initiative_rules(lake, frozen_clock):
    init, compute, _admin = lake
    ws_impact(init, compute, readiness="not_started")  # due at go-live d(100): not within windows anyway
    ws_risk(init, compute)
    own = rag.derive(bundle_of(init))
    assert [f[1] for f in own.fired] == ["Compute: High risk open"]


def test_workstream_only_stale_checkin(lake, frozen_clock):
    init, compute, admin = lake
    Update.objects.filter(workstream_entity=admin.entity_id).delete()
    ws_update(init, admin, 40)
    ev = rag.evaluate(bundle_of(init), persist=False)
    assert ev.effective == "amber" and ev.chip == "Admin: No check-in 40d"
    assert ev.stale_days == 1 and not ev.is_stale  # the initiative's own clock is fine


def test_new_workstream_clock_starts_when_added(people, frozen_clock):
    init = active_initiative(accepted_at=at(-200))
    Workstream.create(AUTH, initiative_entity=init.entity_id, name="Fresh", valid_from=at(-3))
    b = bundle_of(init)
    assert rag.stale_days(b, b.workstreams[0]) == 3
    assert rag.evaluate_workstream(b, b.workstreams[0]).effective == "green"


# --- check-in due and triage -------------------------------------------------------------------------------

def test_check_in_due_per_workstream(lake, frozen_clock):
    init, compute, admin = lake
    Update.objects.filter(workstream_entity=admin.entity_id).delete()
    ws_update(init, admin, 20)
    b = bundle_of(init)
    assert checkin.check_in_due(b)[1] == 0
    assert checkin.check_in_due(b, compute)[1] == 0
    assert checkin.check_in_due(b, admin)[1] == 6


def test_triage_rows_per_workstream(lake, frozen_clock):
    init, _compute, admin = lake
    Update.objects.filter(workstream_entity=admin.entity_id).delete()
    ws_update(init, admin, 20)
    rows, _ = triage.triage()
    [row] = [r for r in rows["Chase"] if r.bundle.entity_id == init.entity_id]
    assert row.what == "Admin: check-in overdue 6d" and row.person == "LEAD2" and row.action == "remind"
    assert row.reminder.startswith("Hello LEAD2, your Admin check-in for Lake is overdue.")
    assert triage.reminder_text(bundle_of(init)).startswith("Hello OWN1")


# --- permissions ---------------------------------------------------------------------------------------------

def test_lead_resolves_like_a_delegate_but_cannot_manage(lake):
    init, *_ = lake
    lead = resolve_actor(LEAD)
    assert lead.is_owner_of(init.entity_id) and init.entity_id in lead.owned_initiatives
    assert lead.can_check_in(init.entity_id) and not lead.can_edit_initiative(init.entity_id)
    owner = resolve_actor(OWN)
    assert owner.can_edit_initiative(init.entity_id) and not owner.led_initiatives


def test_owner_access_off_removes_lead_rights(lake):
    init, *_ = lake
    off = apply_modules(resolve_actor(LEAD), {"owner_access": False})
    assert not off.is_owner_of(init.entity_id) and not off.can_check_in(init.entity_id)


def test_lead_views(client, lake):
    init, compute, admin = lake
    login(client, LEAD)
    assert client.get(reverse("initiative", args=[init.entity_id])).status_code == 200
    assert client.get(reverse("edit_workstreams", args=[init.entity_id])).status_code == 403
    assert client.get(reverse("edit_phases", args=[init.entity_id])).status_code == 403
    page = client.get(reverse("check_in", args=[init.entity_id])).content.decode()
    assert "Compute" in page and "Admin" not in page and "Whole initiative" not in page
    assert client.post(reverse("check_in", args=[init.entity_id]), {"text": "x", "ws": str(admin.entity_id)}).status_code == 403


def test_lead_with_owner_access_off_has_no_access(client, lake):
    init, *_ = lake
    ModuleSettings.create("SYS", owner_access=False, change_note="t")
    login(client, LEAD)
    assert client.get(reverse("initiative", args=[init.entity_id])).status_code == 404
    assert client.get(reverse("check_in", args=[init.entity_id])).status_code == 404


def test_owner_and_tech_risk_manage_workstreams(client, lake):
    init, *_ = lake
    for emp in (OWN, TRU):
        login(client, emp)
        assert client.get(reverse("edit_workstreams", args=[init.entity_id])).status_code == 200


# --- check-in tagging ----------------------------------------------------------------------------------------

def test_modal_defaults_and_filters(client, lake):
    init, compute, admin = lake
    add_milestone(init, planned=d(20), forecast=d(20), name="Compute ms", workstream_entity=compute.entity_id)
    add_milestone(init, planned=d(30), forecast=d(30), name="Admin ms", workstream_entity=admin.entity_id)
    ws_impact(init, admin)
    login(client, OWN)
    url = reverse("check_in", args=[init.entity_id])
    page = client.get(url).content.decode()
    assert 'name="ws"' in page and "Whole initiative" in page and "Compute ms" not in page and "Admin ms" not in page
    page = client.get(url, {"ws": str(admin.entity_id)}).content.decode()
    assert "Admin ms" in page and "Compute ms" not in page and 'name="ctl_' in page and "Go-live date" not in page


def test_post_check_in_tags_update_and_risk(client, lake):
    init, compute, _admin = lake
    login(client, LEAD)
    resp = client.post(reverse("check_in", args=[init.entity_id]), {
        "text": "Quota approved", "ws": str(compute.entity_id), "risk_title": "New thing", "risk_rating": "H",
    })
    assert resp.status_code == 204
    update = Update.objects.filter(initiative_entity=init.entity_id).order_by("-posted_at").first()
    assert update.workstream_entity == compute.entity_id and update.author_emp_id == LEAD
    assert RiskItem.objects.current().get(title="New thing").workstream_entity == compute.entity_id


def test_owner_check_in_whole_initiative_untagged(client, lake):
    init, *_ = lake
    login(client, OWN)
    client.post(reverse("check_in", args=[init.entity_id]), {"text": "All good", "ws": ""})
    assert Update.objects.filter(initiative_entity=init.entity_id, text="All good").get().workstream_entity is None


# --- pages ---------------------------------------------------------------------------------------------------

def test_pages_render_with_and_without_workstreams(client, lake):
    init, compute, _admin = lake
    add_phase(init, "Compute build", workstream_entity=compute.entity_id, planned_start=d(-10), planned_end=d(20),
              forecast_start=d(-10), forecast_end=d(25))
    add_milestone(init, planned=d(20), forecast=d(20), workstream_entity=compute.entity_id)
    plain = active_initiative(name="Plain")
    login(client, TRU)
    page = client.get(reverse("initiative", args=[init.entity_id])).content.decode()
    assert "<h2 class=\"h6\">Workstreams</h2>" in page and "Compute" in page and "Admin" in page
    plan = client.get(reverse("initiative", args=[init.entity_id]), {"tab": "plan"}).content.decode()
    assert "Compute · " in plan
    for tab in ("overview", "plan", "risks", "history", "exec"):
        assert client.get(reverse("initiative", args=[init.entity_id]), {"tab": tab}).status_code == 200
        assert client.get(reverse("initiative", args=[plain.entity_id]), {"tab": tab}).status_code == 200
    assert "<h2 class=\"h6\">Workstreams</h2>" not in client.get(reverse("initiative", args=[plain.entity_id])).content.decode()
    for name in ("phases", "milestones", "risks", "controls", "workstreams"):
        assert client.get(reverse(f"edit_{name}", args=[init.entity_id])).status_code == 200
    assert "<th>Workstream</th>" not in client.get(reverse("edit_phases", args=[plain.entity_id])).content.decode()
    assert "<th>Workstream</th>" in client.get(reverse("edit_phases", args=[init.entity_id])).content.decode()


def test_timeline_unchanged_without_workstreams(people):
    init = active_initiative()
    add_phase(init, planned_start=d(-5), planned_end=d(10), forecast_start=d(-5), forecast_end=d(10))
    svg = initiative_svg(bundle_of(init))
    assert "Whole initiative" not in svg and 'font-weight="700"' not in svg


def test_edit_milestone_workstream_column(client, lake):
    init, compute, _admin = lake
    m = add_milestone(init, planned=d(5), forecast=d(5), name="M1")
    login(client, OWN)
    rows = [{"entity_id": m.entity_id, "name": "M1", "planned": d(5), "forecast": d(5), "pct_complete": 0,
             "status": "planned", "is_key": True, "workstream_entity": compute.entity_id}]
    resp = client.post(reverse("edit_milestones", args=[init.entity_id]), formset_post("m", init, rows))
    assert resp.status_code == 302
    assert Milestone.objects.get_current(m.entity_id).workstream_entity == compute.entity_id


def test_workstream_editor_add_rename_and_remove_untags(client, lake):
    init, compute, admin = lake
    ph = add_phase(init, "P", workstream_entity=compute.entity_id)
    ms = add_milestone(init, planned=d(5), forecast=d(5), workstream_entity=compute.entity_id)
    rk = ws_risk(init, compute)
    ci = ws_impact(init, compute)
    login(client, OWN)
    rows = [
        {"entity_id": compute.entity_id, "name": "Compute", "lead_emp_id": LEAD, "order": 0, "delete": True},
        {"entity_id": admin.entity_id, "name": "Admin ops", "lead_emp_id": "LEAD2", "order": 1},
        {"name": "Front end", "lead_emp_id": LEAD},
    ]
    resp = client.post(reverse("edit_workstreams", args=[init.entity_id]), formset_post("w", init, rows, extra_blank=0))
    assert resp.status_code == 302
    names = list(Workstream.objects.current().filter(initiative_entity=init.entity_id).values_list("name", flat=True))
    assert names == ["Admin ops", "Front end"]
    for model, item in ((Phase, ph), (Milestone, ms), (RiskItem, rk), (ControlImpact, ci)):
        assert model.objects.get_current(item.entity_id).workstream_entity is None
        assert model.objects.history(item.entity_id).count() == 2
    assert not Workstream.objects.current().filter(entity_id=compute.entity_id).exists()


def test_service_remove_keeps_history(lake):
    init, compute, _admin = lake
    ph = add_phase(init, "P", workstream_entity=compute.entity_id)
    ws_svc.remove(compute, TR)
    assert Phase.objects.history(ph.entity_id).first().workstream_entity == compute.entity_id
    assert Workstream.objects.history(compute.entity_id).last().is_deleted
    assert bundle_of(init).workstreams[0].name == "Admin"


# --- exec tile and snapshots --------------------------------------------------------------------------------

def tile(workstreams):
    return Tile(bundle=None, entity_id=uuid.uuid4(), name="X", owner_emp_id="", status="active", phase="", effective="red",
                derived="red", rule="", stale_days=None, is_stale=False, override=None, highlights=[],
                next_milestone=None, go_live=None, last_update_at=None, controls_ready=0, controls_total=0, prior=None,
                workstreams=workstreams)


def test_rollup_line():
    assert tile([("Admin", "amber"), ("Front end", "green"), ("Compute", "red"), ("Back end", "green")]).workstream_rollup \
        == "Compute red · Admin amber · 2 green"
    assert tile([("A", "green"), ("B", "green")]).workstream_rollup == "2 green"
    assert tile([]).workstream_rollup == ""


def test_exec_page_shows_rollup(client, lake, frozen_clock):
    init, compute, _admin = lake
    ws_risk(init, compute, target_date=d(-3))
    login(client, TRU)
    page = client.get(reverse("exec_summary")).content.decode()
    assert "Compute red · 1 green" in page


def test_snapshot_freezes_workstreams(lake, frozen_clock):
    init, compute, admin = lake
    ws_risk(init, compute, target_date=d(-3))
    snap = snapshots.take_snapshot(TR, "Nov")
    row = snapshots.rows_by_entity(Snapshot.objects.get(pk=snap.pk))[init.entity_id]
    assert row["workstreams"] == [
        {"name": "Compute", "lead_emp_id": LEAD, "lead": f"{LEAD} Person", "effective": "red", "chip": "High risk past due"},
        {"name": "Admin", "lead_emp_id": "LEAD2", "lead": "LEAD2 Person", "effective": "green", "chip": ""},
    ]
    compute.new_version(TR, name="Renamed")
    again = snapshots.rows_by_entity(Snapshot.objects.get(pk=snap.pk))[init.entity_id]
    assert again["workstreams"][0]["name"] == "Compute"


# --- loader --------------------------------------------------------------------------------------------------

def loader_doc(**extra):
    return {
        "initiative": {"name": "Loaded lake", "owner": {"emp_id": "E7"}, "target_go_live": "2027-03-01"},
        "workstreams": [{"name": "Compute", "lead_emp_id": "E9", "lead_name": "Priya", "description": "Clusters"}],
        "phases": [{"name": "Build", "stage": 2, "workstream": "Compute", "planned_start": "2026-09-01", "planned_end": "2026-10-01"}],
        "milestones": [{"name": "Ready", "planned": "2026-10-01", "key": True, "workstream": "Compute"}],
        "risks": [{"type": "risk", "title": "Quota", "rating": "H", "workstream": "Compute"}],
        "control_impacts": [{"control_id": "C-1", "name": "Ctl", "impact_type": "new", "workstream": "Compute"}],
        **extra,
    }


def test_loader_workstreams():
    init = load(loader_doc(), out=lambda *a: None)
    ws = Workstream.objects.current().get(initiative_entity=init.entity_id)
    assert (ws.name, ws.lead_emp_id, ws.description) == ("Compute", "E9", "Clusters")
    assert Person.objects.get(pk="E9").display_name == "Priya"
    for model in (Phase, Milestone, RiskItem, ControlImpact):
        assert model.objects.current().get(initiative_entity=init.entity_id).workstream_entity == ws.entity_id


def test_loader_unknown_workstream_errors():
    doc = loader_doc()
    doc["risks"][0]["workstream"] = "Nope"
    with pytest.raises(SystemExit, match='workstream "Nope" is not in the workstreams list'):
        load(doc, out=lambda *a: None)
