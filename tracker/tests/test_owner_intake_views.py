import pytest
from scripts.generate_fake_data import seed as generate_fake_data
from django.urls import reverse

from tracker.models import ControlImpact, Initiative, Milestone, Phase, RiskItem, Update

pytestmark = pytest.mark.django_db

OWNER, TR, CTO, NEW = "E00101", "E00001", "E00201", "E00999"


@pytest.fixture
def seeded(client):
    generate_fake_data(stdout=lambda *a: None)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


def switch(client, emp_id):
    client.post("/auth/switch/", {"emp_id": emp_id})


def formset_post(url_name, init, rows, extra_blank=1, client=None):
    """Build POST data for a rows formset from a list of dicts (existing rows first)."""
    data = {
        "rows-TOTAL_FORMS": str(len(rows) + extra_blank),
        "rows-INITIAL_FORMS": str(len(rows) - sum(1 for r in rows if not r.get("entity_id"))),
        "rows-MIN_NUM_FORMS": "0",
        "rows-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for k, v in row.items():
            if v is True:
                data[f"rows-{i}-{k}"] = "on"
            elif v not in (None, False):
                data[f"rows-{i}-{k}"] = str(v)
    return data


def milestone_rows(init):
    rows = []
    for m in Milestone.objects.current().filter(initiative_entity=init.entity_id).order_by("planned", "name"):
        rows.append({
            "entity_id": m.entity_id, "name": m.name, "phase_entity": m.phase_entity or "",
            "planned": m.planned, "forecast": m.forecast, "actual": m.actual or "",
            "pct_complete": m.pct_complete, "status": m.status, "is_key": m.is_key, "note": m.note,
        })
    return rows


def edit_forecast(client, init, name, new_date):
    rows = milestone_rows(init)
    for r in rows:
        if r["name"] == name:
            r["forecast"] = new_date
    return client.post(reverse("edit_milestones", args=[init.entity_id]), formset_post("m", init, rows))


def test_my_initiative_redirects_single_owner(client, seeded):
    switch(client, OWNER)
    resp = client.get(reverse("my_initiative"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("initiative", args=[seeded.entity_id])


def test_my_initiative_403_without_ownership_and_chooser_for_many(client, seeded):
    switch(client, CTO)
    assert client.get(reverse("my_initiative")).status_code == 403
    Initiative.create("SYSTEM", name="Second", owner_emp_id=OWNER, status=Initiative.Status.ACTIVE)
    switch(client, OWNER)
    resp = client.get(reverse("my_initiative"))
    assert resp.status_code == 200
    assert b"Second" in resp.content and b"SailPoint Onboarding" in resp.content


def test_owner_milestone_edit_versions_without_update(client, seeded):
    switch(client, OWNER)
    updates = Update.objects.filter(initiative_entity=seeded.entity_id).count()
    old = Milestone.objects.current().get(initiative_entity=seeded.entity_id, name="Wave 1 connectors built")
    resp = edit_forecast(client, seeded, "Wave 1 connectors built", "2026-12-01")
    assert resp.status_code == 302
    versions = list(Milestone.objects.history(old.entity_id))
    assert len(versions) == 2
    assert versions[0].valid_to is not None and versions[1].valid_to is None
    assert str(versions[1].forecast) == "2026-12-01"
    assert versions[1].author_emp_id == OWNER
    assert Update.objects.filter(initiative_entity=seeded.entity_id).count() == updates
    # Unchanged rows create no further versions.
    assert len(list(Milestone.objects.history(
        Milestone.objects.current().get(initiative_entity=seeded.entity_id, name="Go-live (Wave 1)").entity_id))) == 1


def test_milestone_add_and_retire(client, seeded):
    switch(client, OWNER)
    rows = milestone_rows(seeded)
    rows[0]["delete"] = True
    retired_name = rows[0]["name"]
    rows.append({"name": "New checkpoint", "planned": "2026-12-20", "status": "planned", "pct_complete": 0})
    client.post(reverse("edit_milestones", args=[seeded.entity_id]), formset_post("m", seeded, rows, extra_blank=0))
    names = set(Milestone.objects.current().filter(initiative_entity=seeded.entity_id).values_list("name", flat=True))
    assert "New checkpoint" in names and retired_name not in names


def test_owner_cannot_edit_visibility_or_see_internal_risks(client, seeded):
    switch(client, OWNER)
    resp = client.get(reverse("edit_risks", args=[seeded.entity_id]))
    assert resp.status_code == 200
    assert "visibility" not in resp.content.decode().lower().replace("visibility defaults", "")
    assert b"Go-live overlaps the Q4 access certification cycle" not in resp.content  # internal-only risk
    internal = RiskItem.objects.current().get(initiative_entity=seeded.entity_id, title__startswith="Go-live overlaps")
    rows = [{"entity_id": internal.entity_id, "type": "risk", "title": "tampered", "rating": "L", "status": "open"}]
    data = formset_post("r", seeded, rows, extra_blank=0)
    data["rows-0-visibility"] = "exec"
    client.post(reverse("edit_risks", args=[seeded.entity_id]), data)
    internal.refresh_from_db()
    assert internal.title.startswith("Go-live overlaps") and internal.visibility == "internal"
    assert len(list(RiskItem.objects.history(internal.entity_id))) == 1


def test_risk_add_source_by_actor(client, seeded):
    switch(client, OWNER)
    row = {"type": "risk", "title": "Owner-raised risk", "rating": "M", "status": "open"}
    client.post(reverse("edit_risks", args=[seeded.entity_id]), formset_post("r", seeded, [row], extra_blank=0))
    r = RiskItem.objects.current().get(title="Owner-raised risk")
    assert r.source == "owner" and r.visibility == "exec"
    switch(client, TR)
    row = {"type": "issue", "title": "TR-raised issue", "rating": "H", "status": "open"}
    client.post(reverse("edit_risks", args=[seeded.entity_id]), formset_post("r", seeded, [row], extra_blank=0))
    assert RiskItem.objects.current().get(title="TR-raised issue").source == "tech_risk"


def test_control_impact_add(client, seeded):
    from tracker.models import Control
    ctl = Control.objects.current().get(control_id="ITGC-BK-01")
    switch(client, OWNER)
    resp = client.get(reverse("edit_controls", args=[seeded.entity_id]))
    assert b"ITGC-BK-01 \xe2\x80\x94 Backup completion" in resp.content
    row = {"control_entity": ctl.entity_id, "impact_type": "new", "readiness": "in_progress"}
    client.post(reverse("edit_controls", args=[seeded.entity_id]), formset_post("c", seeded, [row], extra_blank=0))
    assert ControlImpact.objects.current().filter(initiative_entity=seeded.entity_id, control_entity=ctl.entity_id).exists()


def test_phase_edit_and_initiative_edit(client, seeded):
    switch(client, OWNER)
    ph = Phase.objects.current().get(initiative_entity=seeded.entity_id, name="Build")
    rows = [{"entity_id": p.entity_id, "name": p.name, "stage": p.stage, "order": p.order,
             "planned_start": p.planned_start, "planned_end": p.planned_end,
             "forecast_start": p.forecast_start, "forecast_end": "2026-11-30" if p.name == "Build" else p.forecast_end,
             "actual_start": p.actual_start or "", "actual_end": p.actual_end or ""}
            for p in Phase.objects.current().filter(initiative_entity=seeded.entity_id).order_by("order")]
    client.post(reverse("edit_phases", args=[seeded.entity_id]), formset_post("p", seeded, rows, extra_blank=0))
    assert str(Phase.objects.current().get(entity_id=ph.entity_id).forecast_end) == "2026-11-30"

    data = {"name": "SailPoint Onboarding", "description": seeded.description, "sponsor": "CIO", "phase": "Test",
            "target_go_live": "2027-01-19", "business_driver": "", "why_it_matters": seeded.why_it_matters,
            "expected_systems": seeded.expected_systems}
    client.post(reverse("edit_initiative", args=[seeded.entity_id]), data)
    cur = Initiative.objects.get_current(seeded.entity_id)
    assert cur.sponsor == "CIO" and cur.phase == "Test"
    versions = len(list(Initiative.objects.history(seeded.entity_id)))
    client.post(reverse("edit_initiative", args=[seeded.entity_id]), data)
    assert len(list(Initiative.objects.history(seeded.entity_id))) == versions  # no change, no version


def test_cto_forbidden_on_edit_views(client, seeded):
    switch(client, CTO)
    for name in ("edit_initiative", "edit_phases", "edit_milestones", "edit_risks", "edit_controls"):
        assert client.get(reverse(name, args=[seeded.entity_id])).status_code == 403
        assert client.post(reverse(name, args=[seeded.entity_id]), {}).status_code == 403
