from datetime import date

import pytest
from django.urls import reverse
from scripts.generate_fake_data import seed as generate_fake_data

from tracker import lifecycle as rules
from tracker.models import GateApproval, GateSettings, Initiative, StageCheck
from tracker.services import lifecycle
from tracker.services.bundle import load_bundle
from tracker.views.lifecycle import build_stage_rows

pytestmark = pytest.mark.django_db

TR, OWNER, CTO, AUD = "E00001", "E00101", "E00201", "E00301"


@pytest.fixture
def init(db):
    generate_fake_data(stdout=lambda *a: None)
    for row in [*StageCheck.objects.current(), *GateApproval.objects.current()]:
        row.retire(TR)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


@pytest.fixture
def as_user(client):
    def _as(emp_id):
        client.post("/auth/switch/", {"emp_id": emp_id})
        return client

    return _as


def set_level(init, level):
    return Initiative.objects.get_current(init.entity_id).new_version("AUTH", oversight_override=level, oversight_override_reason="test")


def item_url(init, stage, item, action):
    return reverse("stage_item", args=[init.entity_id, stage, item, action])


def check(init, stage, item):
    return load_bundle(Initiative.objects.get_current(init.entity_id)).checks_for(stage).get(item)


def actor(emp_id):
    from core.auth.actor import resolve_actor

    return resolve_actor(emp_id)


# --- stage rows -------------------------------------------------------------------------


def test_stage_rows_actions_by_role(init):
    set_level(init, "standard")
    bundle = load_bundle(Initiative.objects.get_current(init.entity_id))
    lifecycle.submit_evidence(bundle, OWNER, 2, 0, "https://ev/a")
    bundle = load_bundle(Initiative.objects.get_current(init.entity_id))
    names = lambda rows, stage, i: [a for a, _ in rows[stage]["items"][i]["actions"]]  # noqa: E731

    tr_rows = build_stage_rows(bundle, actor(TR))
    assert names(tr_rows, 2, 0) == ["accept", "send_back"]
    assert names(tr_rows, 2, 1) == ["accept", "na"]
    assert tr_rows[2]["req_label"] == "Tech Risk review" and tr_rows[0]["req_label"] == "Intake acceptance"
    assert tr_rows[2]["expanded"] is True and tr_rows[1]["expanded"] is False
    assert tr_rows[2]["items"][0]["state"] == "submitted" and tr_rows[2]["items"][0]["evidence_url"]

    owner_rows = build_stage_rows(bundle, actor(OWNER))
    assert names(owner_rows, 2, 0) == []
    assert names(owner_rows, 2, 1) == ["submit"]
    assert names(owner_rows, 0, 0) == []
    assert owner_rows[2]["expanded"] is True

    for emp in (CTO, AUD):
        rows = build_stage_rows(bundle, actor(emp))
        assert not any(it["actions"] for r in rows for it in r["items"])
        assert not any(r["expanded"] or r["can_record"] for r in rows)


def test_owner_confirm_action_only_on_owner_stages(init):
    set_level(init, "light")
    bundle = load_bundle(Initiative.objects.get_current(init.entity_id))
    rows = build_stage_rows(bundle, actor(OWNER))
    assert [a for a, _ in rows[2]["items"][0]["actions"]] == ["submit", "confirm"]
    assert [a for a, _ in rows[1]["items"][0]["actions"]] == ["submit"]


def test_stage_rows_meeting_counts_and_signoff(init):
    set_level(init, "enhanced")
    bundle = load_bundle(Initiative.objects.get_current(init.entity_id))
    rows = build_stage_rows(bundle, actor(TR))
    assert len(rows[2]["meetings"]) == 3 and rows[3]["meetings"] == []
    assert rows[2]["can_record"] is False
    for i in range(rows[2]["total"]):
        lifecycle.accept_item(bundle, TR, 2, i, evidence_ref="ev")
    rows = build_stage_rows(load_bundle(Initiative.objects.get_current(init.entity_id)), actor(TR))
    assert rows[2]["can_record"] is True
    assert build_stage_rows(load_bundle(Initiative.objects.get_current(init.entity_id)), actor(OWNER))[2]["can_record"] is False


# --- item actions -----------------------------------------------------------------------


def test_owner_submits_but_cannot_accept(init, as_user):
    set_level(init, "standard")
    c = as_user(OWNER)
    assert c.get(item_url(init, 2, 0, "submit")).status_code == 200
    assert c.post(item_url(init, 2, 0, "submit"), {"evidence_ref": ""}).status_code == 200
    assert check(init, 2, 0) is None
    r = c.post(item_url(init, 2, 0, "submit"), {"evidence_ref": "https://ev/1", "note": "n"})
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    ck = check(init, 2, 0)
    assert (ck.status, ck.evidence_ref, ck.author_emp_id) == ("submitted", "https://ev/1", OWNER)
    for action in ("accept", "send_back", "na", "reopen"):
        assert c.post(item_url(init, 2, 0, action), {"basis": "reviewed", "text": "x"}).status_code == 403
    assert check(init, 2, 0).status == "submitted"


def test_owner_confirm_requires_owner_stage(init, as_user):
    c = as_user(OWNER)
    set_level(init, "standard")
    assert c.post(item_url(init, 2, 0, "confirm"), {}).status_code == 403
    set_level(init, "light")
    assert c.post(item_url(init, 2, 0, "confirm"), {}).status_code == 204
    assert check(init, 2, 0).status == "confirmed"


def test_tech_risk_accepts_submitted_with_basis(init, as_user):
    set_level(init, "standard")
    lifecycle.submit_evidence(load_bundle(Initiative.objects.get_current(init.entity_id)), OWNER, 2, 0, "https://ev/1")
    c = as_user(TR)
    html = c.get(item_url(init, 2, 0, "accept")).content.decode()
    assert "Observed by Tech Risk" in html and 'name="evidence_ref"' not in html
    assert c.post(item_url(init, 2, 0, "accept"), {"basis": "bogus"}).status_code == 200
    assert c.post(item_url(init, 2, 0, "accept"), {"basis": "observed"}).status_code == 204
    ck = check(init, 2, 0)
    assert (ck.status, ck.basis, ck.evidence_ref) == ("accepted", "observed", "https://ev/1")


def test_tech_risk_direct_accept_needs_evidence(init, as_user):
    c = as_user(TR)
    assert 'name="evidence_ref"' in c.get(item_url(init, 0, 0, "accept")).content.decode()
    assert c.post(item_url(init, 0, 0, "accept"), {"basis": "reviewed", "evidence_ref": ""}).status_code == 200
    assert check(init, 0, 0) is None
    assert c.post(item_url(init, 0, 0, "accept"), {"basis": "reviewed", "evidence_ref": "TICKET-9"}).status_code == 204
    assert check(init, 0, 0).evidence_ref == "TICKET-9"


def test_tech_risk_send_back_needs_comment_then_owner_resubmits(init, as_user):
    set_level(init, "standard")
    lifecycle.submit_evidence(load_bundle(Initiative.objects.get_current(init.entity_id)), OWNER, 2, 0, "ev")
    c = as_user(TR)
    assert c.post(item_url(init, 2, 0, "send_back"), {"text": " "}).status_code == 200
    assert check(init, 2, 0).status == "submitted"
    assert c.post(item_url(init, 2, 0, "send_back"), {"text": "Need prod access list"}).status_code == 204
    ck = check(init, 2, 0)
    assert (ck.status, ck.comment) == ("sent_back", "Need prod access list")
    html = as_user(OWNER).get(reverse("stage_item", args=[init.entity_id, 2, 0, "submit"])).content.decode()
    assert "Submit evidence" in html


def test_tech_risk_na_and_reopen(init, as_user):
    set_level(init, "standard")
    c = as_user(TR)
    assert c.post(item_url(init, 2, 1, "na"), {"text": ""}).status_code == 200
    assert c.post(item_url(init, 2, 1, "na"), {"text": "No data migration"}).status_code == 204
    ck = check(init, 2, 1)
    assert (ck.status, ck.comment) == ("na", "No data migration")
    assert c.get(item_url(init, 2, 1, "reopen")).status_code == 405
    assert c.post(item_url(init, 2, 1, "reopen")).status_code == 204
    assert check(init, 2, 1) is None
    assert c.post(item_url(init, 2, 1, "reopen")).status_code == 403


@pytest.mark.parametrize("emp", [CTO, AUD])
def test_cto_and_auditor_cannot_act(init, as_user, emp):
    c = as_user(emp)
    for action in ("submit", "accept", "send_back", "na", "confirm", "reopen"):
        assert c.post(item_url(init, 2, 0, action), {"evidence_ref": "x", "text": "x", "basis": "reviewed"}).status_code == 403
    assert not StageCheck.objects.current().filter(initiative_entity=init.entity_id).exists()


def test_unknown_action_or_item_is_404(init, as_user):
    c = as_user(TR)
    assert c.get(item_url(init, 2, 0, "explode")).status_code == 404
    assert c.get(item_url(init, 2, 99, "accept")).status_code == 404


# --- sign-off ---------------------------------------------------------------------------


def accept_stage(init, stage):
    bundle = load_bundle(Initiative.objects.get_current(init.entity_id))
    for i in range(len(lifecycle.current_guidance()[stage].checklist)):
        lifecycle.accept_item(bundle, TR, stage, i, evidence_ref="ev")


def gate_url(init, stage):
    return reverse("stage_gate", args=[init.entity_id, stage])


def test_record_signoff_with_conditions(init, as_user):
    set_level(init, "enhanced")
    c = as_user(TR)
    assert c.get(gate_url(init, 2)).status_code == 404  # items not done yet
    accept_stage(init, 2)
    assert c.get(gate_url(init, 2)).status_code == 200
    body = {"approver": "Program manager", "approved_on": "2026-11-02", "evidence_ref": "https://ev/signoff",
            "decision": "conditions", "conditions": ""}
    assert c.post(gate_url(init, 2), body).status_code == 200
    assert not GateApproval.objects.current().exists()
    r = c.post(gate_url(init, 2), {**body, "conditions": "Fix defects before cutover"})
    assert r.status_code == 204
    gate = GateApproval.objects.current().get(initiative_entity=init.entity_id)
    assert (gate.decision, gate.conditions, gate.stage) == ("conditions", "Fix defects before cutover", 2)
    html = c.get(reverse("initiative", args=[init.entity_id]), {"tab": "lifecycle"}).content.decode()
    assert "Proceed with conditions" in html


def test_hold_allows_rerecording(init, as_user):
    set_level(init, "enhanced")
    accept_stage(init, 2)
    c = as_user(TR)
    body = {"approver": "PM", "approved_on": "2026-11-02", "evidence_ref": "e", "decision": "hold"}
    assert c.post(gate_url(init, 2), body).status_code == 204
    rows = build_stage_rows(load_bundle(Initiative.objects.get_current(init.entity_id)), actor(TR))
    assert rows[2]["gate_color"] == "red" and rows[2]["can_record"] is True
    assert c.post(gate_url(init, 2), {**body, "decision": "proceed"}).status_code == 204
    rows = build_stage_rows(load_bundle(Initiative.objects.get_current(init.entity_id)), actor(TR))
    assert rows[2]["gate_color"] == "green" and rows[2]["can_record"] is False


def test_signoff_not_offered_on_review_stages_and_403_for_others(init, as_user):
    set_level(init, "standard")
    accept_stage(init, 2)
    assert as_user(TR).get(gate_url(init, 2)).status_code == 404
    set_level(init, "enhanced")
    for emp in (OWNER, CTO, AUD):
        assert as_user(emp).get(gate_url(init, 2)).status_code == 403


# --- guidance page ----------------------------------------------------------------------


def test_guidance_page_draft_tag_and_owner_highlight(init, as_user):
    set_level(init, "light")
    html = as_user(OWNER).get(reverse("lifecycle")).content.decode()
    assert "Draft" in html and "Pending Head of Tech Risk approval" in html
    assert "SailPoint Onboarding: Light." in html
    assert html.count('class="reqtag mine"') == 6 and "Approver sign-off" in html
    assert "Edit stage" not in html and "Tech Risk accepts the intake" in html

    html = as_user(TR).get(reverse("lifecycle")).content.decode()
    assert "SailPoint Onboarding:" not in html and 'reqtag mine' not in html
    assert "Edit stage" in html

    assert "Standard: any yes. Light: none." in html


def test_guidance_page_draft_then_approved(init, as_user):
    row = GateSettings.create(TR, matrix=rules.DEFAULT_GATE_MATRIX, question_labels={}, version_label="v2.0")
    html = as_user(CTO).get(reverse("lifecycle")).content.decode()
    assert "Pending Head of Tech Risk approval" in html and "Approved by" not in html

    row.new_version(TR, approved_by="Head of Tech Risk", approved_on=date(2026, 11, 9))
    html = as_user(CTO).get(reverse("lifecycle")).content.decode()
    assert "Pending Head of Tech Risk approval" not in html
    assert "Approved by Head of Tech Risk on Nov 9, 2026 · v2.0" in html


def test_guidance_page_trac_link(init, as_user):
    page = lifecycle.current_guidance()[2]
    page.new_version(TR, version_label="v1.1", change_note="link", trac_url="https://trac.example/stage-2")
    html = as_user(OWNER).get(reverse("lifecycle")).content.decode()
    assert 'href="https://trac.example/stage-2"' in html and "TRAC page not linked yet" in html


def test_edit_guidance(init, as_user):
    assert as_user(OWNER).get(reverse("edit_guidance", args=[2])).status_code == 403
    c = as_user(TR)
    url = reverse("edit_guidance", args=[2])
    assert c.get(url).status_code == 200
    r = c.post(url, {"name": "Build", "approver": "PM", "checklist": "One\nTwo", "trac_url": "trac.example/s2",
                     "version_label": "v1.2", "change_note": "edit"})
    assert r.status_code == 302
    page = lifecycle.current_guidance()[2]
    assert (page.name, page.checklist, page.trac_url, page.version_label) == ("Build", ["One", "Two"], "https://trac.example/s2", "v1.2")
