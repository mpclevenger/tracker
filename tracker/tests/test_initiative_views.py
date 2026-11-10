import pytest
from django.urls import reverse

from scripts.generate_fake_data import seed as generate_fake_data
from tracker.models import (
    Control, ControlImpact, CurationEvent, Delegation, Initiative, RagOverride, RiskItem, Update, Visibility,
)
from tracker.services.bundle import load_bundle

pytestmark = pytest.mark.django_db

TR, OWNER, CTO, AUD = "E00001", "E00101", "E00201", "E00301"
INTERNAL_RISK = "Go-live overlaps the Q4"
TABS = ["overview", "plan", "risks", "lifecycle", "meetings", "history", "exec"]


@pytest.fixture
def seeded(db):
    generate_fake_data(stdout=lambda *a: None)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


@pytest.fixture
def as_user(client):
    def _as(emp_id):
        client.post("/auth/switch/", {"emp_id": emp_id})
        return client

    return _as


def page(client, init, tab=None):
    url = reverse("initiative", args=[init.entity_id])
    return client.get(url + (f"?tab={tab}" if tab else ""))


def html_of(client, init, tab=None):
    return page(client, init, tab).content.decode()


def impacts(init):
    return list(ControlImpact.objects.current().filter(initiative_entity=init.entity_id))


def test_tabs_render_for_each_role(seeded, as_user):
    for emp in (TR, OWNER, CTO, AUD):
        c = as_user(emp)
        for tab in TABS:
            assert page(c, seeded, tab).status_code == 200, (emp, tab)
    assert page(as_user(TR), seeded, "nonsense").status_code == 200


def test_summary_strip_facts(seeded, as_user):
    html = html_of(as_user(TR), seeded)
    assert "Go-live slipped 35d" in html and "slipped 35d" in html
    assert "Versus baseline" in html
    for label in ("Stage", "Go-live", "Next milestone", "Next check-in"):
        assert f'<span class="l">{label}</span>' in html
    assert initiative_oversight_label(seeded) in html
    assert "Due " in html or "Overdue " in html
    assert "Set by Tech Risk" in html
    assert reverse("oversight_modal", args=[seeded.entity_id]) in html


def initiative_oversight_label(init):
    return Initiative.objects.get_current(init.entity_id).oversight_label


def test_check_in_button_roles(seeded, as_user):
    url = reverse("check_in", args=[seeded.entity_id])
    assert url in html_of(as_user(OWNER), seeded)
    assert url in html_of(as_user(TR), seeded)
    assert url not in html_of(as_user(CTO), seeded)
    assert url not in html_of(as_user(AUD), seeded)


def test_cto_overview_hides_tech_risk_cards(seeded, as_user):
    html = html_of(as_user(CTO), seeded)
    assert INTERNAL_RISK not in html
    assert "Exec view" not in html and "RAG rules" not in html
    assert reverse("oversight_modal", args=[seeded.entity_id]) not in html
    assert page(c_ := as_user(CTO), seeded, "exec").status_code == 200 and "Exec tile preview" not in html_of(c_, seeded, "exec")


def test_tech_risk_overview_and_exec_tab(seeded, as_user):
    c = as_user(TR)
    html = html_of(c, seeded)
    assert "Why this status" in html and "RAG rules" not in html and "Exec tile preview" not in html
    assert "Set override" in html and "Actions" in html
    assert reverse("edit_people", args=[seeded.entity_id]) in html
    assert "John" in html
    ex = html_of(c, seeded, "exec")
    assert "Exec tile preview" in ex and "Last curated" in ex and "Commentary and notes" in ex
    assert reverse("overlay_add", args=[seeded.entity_id]) in ex


def test_risks_tab_columns_by_role(seeded, as_user):
    cto = html_of(as_user(CTO), seeded, "risks")
    assert INTERNAL_RISK not in cto
    assert "Edit visibility" not in cto and 'name="visibility"' not in cto
    tr = html_of(as_user(TR), seeded, "risks")
    assert INTERNAL_RISK in tr and 'class="tag internal"' in tr
    assert "Edit visibility" in tr and 'name="visibility"' in tr


def test_control_id_links_to_trac(seeded, as_user):
    imp = impacts(seeded)[0]
    ctl = Control.objects.current().get(entity_id=imp.control_entity)
    ctl.new_version(TR, trac_url="https://trac.example/controls/x")
    html = html_of(as_user(CTO), seeded, "risks")
    assert 'href="https://trac.example/controls/x" target="_blank"' in html and 'title="Guidance in TRAC"' in html


def test_history_tab_links(seeded, as_user):
    assert reverse("initiative_history", args=[seeded.entity_id]) in html_of(as_user(TR), seeded, "history")
    assert reverse("initiative_history", args=[seeded.entity_id]) in html_of(as_user(AUD), seeded, "history")
    assert reverse("initiative_history", args=[seeded.entity_id]) not in html_of(as_user(CTO), seeded, "history")


# --- check-in ---------------------------------------------------------------------------------------

def checkin_url(init):
    return reverse("check_in", args=[init.entity_id])


def test_check_in_modal_get(seeded, as_user):
    r = as_user(OWNER).get(checkin_url(seeded))
    html = r.content.decode()
    assert r.status_code == 200 and "What&#x27;s changed?" in html or "What's changed?" in html
    assert "Go-live date" in html and "% complete" in html and "New risk" in html
    assert as_user(CTO).get(checkin_url(seeded)).status_code == 403


def test_check_in_post_applies_changes_and_logs_diff(seeded, as_user):
    c = as_user(OWNER)
    ms = load_bundle(seeded).next_milestone(key_only=True)
    pending = [i for i in impacts(seeded) if i.readiness == "not_started"][0]
    before = Update.objects.filter(initiative_entity=seeded.entity_id).count()
    r = c.post(checkin_url(seeded), {
        "text": "Connectors on track.", "go_live": "2027-03-01", "forecast": "2026-12-20", "pct_complete": "60",
        f"ctl_{pending.entity_id}": "in_progress", "risk_title": "Vendor staffing", "risk_rating": "H",
    })
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    assert Update.objects.filter(initiative_entity=seeded.entity_id).count() == before + 1
    up = Update.objects.filter(initiative_entity=seeded.entity_id).order_by("-posted_at").first()
    assert up.text == "Connectors on track."
    assert up.change_diff["Go-live"][1] == "Mar 1, 2027"
    assert up.change_diff[f"{ms.name} · % complete"][1] == "60%"
    assert up.change_diff["New risk"][1] == "Vendor staffing (H)"
    assert any(k.startswith("Control ") for k in up.change_diff)
    assert Initiative.objects.get_current(seeded.entity_id).target_go_live.isoformat() == "2027-03-01"
    assert ControlImpact.objects.get_current(pending.entity_id).readiness == "in_progress"
    assert RiskItem.objects.current().filter(initiative_entity=seeded.entity_id, title="Vendor staffing").exists()


def test_check_in_without_changes_records_empty_diff(seeded, as_user):
    c = as_user(OWNER)
    html = c.get(checkin_url(seeded)).content.decode()
    assert html
    assert c.post(checkin_url(seeded), {"text": "No news."}).status_code == 204
    assert Update.objects.filter(initiative_entity=seeded.entity_id).order_by("-posted_at").first().change_diff == {}


def test_check_in_requires_text(seeded, as_user):
    before = Update.objects.count()
    r = as_user(OWNER).post(checkin_url(seeded), {"text": "  "})
    assert r.status_code == 200 and "Describe what has changed" in r.content.decode()
    assert Update.objects.count() == before


def test_check_in_submit_requires_evidence(seeded, as_user):
    c = as_user(OWNER)
    imp = [i for i in impacts(seeded) if i.readiness == "not_started"][0]
    before = Update.objects.count()
    r = c.post(checkin_url(seeded), {"text": "Done", f"ctl_{imp.entity_id}": "submitted", f"ev_{imp.entity_id}": ""})
    assert r.status_code == 200 and "Enter an evidence link" in r.content.decode()
    assert Update.objects.count() == before
    assert ControlImpact.objects.get_current(imp.entity_id).readiness == "not_started"
    r = c.post(checkin_url(seeded), {"text": "Done", f"ctl_{imp.entity_id}": "submitted", f"ev_{imp.entity_id}": "https://ev.example/1"})
    assert r.status_code == 204
    new = ControlImpact.objects.get_current(imp.entity_id)
    assert new.readiness == "submitted" and new.evidence_link == "https://ev.example/1"


def test_check_in_shows_send_back_comment(seeded, as_user):
    imp = impacts(seeded)[0]
    imp.new_version(TR, readiness="sent_back", review_comment="Attach the signed test plan")
    assert "Attach the signed test plan" in as_user(OWNER).get(checkin_url(seeded)).content.decode()


def test_check_in_closed_initiative_not_found(seeded, as_user):
    seeded.new_version(TR, status="closed")
    assert as_user(TR).get(checkin_url(seeded)).status_code == 404


# --- control review ---------------------------------------------------------------------------------

def submitted_impact(init):
    imp = impacts(init)[0]
    return imp.new_version(OWNER, readiness="submitted", evidence_link="https://ev.example/2")


def review_url(init, imp):
    return reverse("review_control", args=[init.entity_id, imp.entity_id])


def test_review_buttons_only_for_tech_risk(seeded, as_user):
    imp = submitted_impact(seeded)
    assert review_url(seeded, imp) in html_of(as_user(TR), seeded, "risks")
    assert review_url(seeded, imp) not in html_of(as_user(OWNER), seeded, "risks")


def test_accept_control(seeded, as_user):
    imp = submitted_impact(seeded)
    r = as_user(TR).post(review_url(seeded, imp), {"action": "accept"})
    assert r.status_code == 302 and r["Location"].endswith("?tab=risks")
    assert ControlImpact.objects.get_current(imp.entity_id).readiness == "ready"


def test_send_back_control_needs_comment_and_is_hidden_from_cto(seeded, as_user):
    imp = submitted_impact(seeded)
    c = as_user(TR)
    c.post(review_url(seeded, imp), {"action": "send_back", "comment": " "})
    assert ControlImpact.objects.get_current(imp.entity_id).readiness == "submitted"
    c.post(review_url(seeded, imp), {"action": "send_back", "comment": "Missing sign-off"})
    assert ControlImpact.objects.get_current(imp.entity_id).readiness == "sent_back"
    assert "Missing sign-off" in html_of(as_user(OWNER), seeded, "risks")
    assert "Missing sign-off" not in html_of(as_user(CTO), seeded, "risks")


def test_review_forbidden_for_owner(seeded, as_user):
    imp = submitted_impact(seeded)
    assert as_user(OWNER).post(review_url(seeded, imp), {"action": "accept"}).status_code == 403
    assert ControlImpact.objects.get_current(imp.entity_id).readiness == "submitted"


# --- oversight and people ---------------------------------------------------------------------------

def test_oversight_modal_override_needs_reason(seeded, as_user):
    c = as_user(TR)
    url = reverse("oversight_modal", args=[seeded.entity_id])
    assert "From answers:" in c.get(url).content.decode()
    r = c.post(url, {"override": "light", "reason": " "})
    assert r.status_code == 200 and "Enter a reason" in r.content.decode()
    assert Initiative.objects.get_current(seeded.entity_id).oversight_override == ""
    r = c.post(url, {"override": "light", "reason": "Small scope", "q_affects_sox_app": "on"})
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    html = html_of(c, seeded)
    assert 'pill ov-light" title="Override: Small scope"' in html


def test_oversight_answers_change_derived_level(seeded, as_user):
    c = as_user(TR)
    url = reverse("oversight_modal", args=[seeded.entity_id])
    c.post(url, {"q_affects_sox_app": "on", "q_changes_key_controls": "on"})
    assert Initiative.objects.get_current(seeded.entity_id).oversight == "enhanced"
    c.post(url, {})
    assert Initiative.objects.get_current(seeded.entity_id).oversight == "light"


def test_edit_people_changes_owner_and_delegates(seeded, as_user):
    c = as_user(TR)
    url = reverse("edit_people", args=[seeded.entity_id])
    assert c.get(url).status_code == 200
    r = c.post(url, {"owner": CTO, "delegates": [OWNER, AUD]})
    assert r.status_code == 204
    assert Initiative.objects.get_current(seeded.entity_id).owner_emp_id == CTO
    assert set(Delegation.objects.current().filter(initiative_entity=seeded.entity_id).values_list("delegate_emp_id", flat=True)) == {OWNER, AUD}
    c.post(url, {"owner": CTO, "delegates": [AUD]})
    assert set(Delegation.objects.current().filter(initiative_entity=seeded.entity_id).values_list("delegate_emp_id", flat=True)) == {AUD}
    assert CurationEvent.objects.filter(initiative_entity=seeded.entity_id, what__startswith="owner").exists()
    assert "CTO" in html_of(c, seeded)


def test_edit_people_rejects_unknown_owner(seeded, as_user):
    r = as_user(TR).post(reverse("edit_people", args=[seeded.entity_id]), {"owner": "NOBODY"})
    assert r.status_code == 200
    assert Initiative.objects.get_current(seeded.entity_id).owner_emp_id == OWNER


def test_owner_forbidden_on_tech_risk_modals(seeded, as_user):
    c = as_user(OWNER)
    for name in ("oversight_modal", "edit_people"):
        url = reverse(name, args=[seeded.entity_id])
        assert c.get(url).status_code == 403
        assert c.post(url, {}).status_code == 403


def test_close_modal_is_empty(seeded, as_user):
    r = as_user(OWNER).get(reverse("close_modal"))
    assert r.status_code == 200 and r.content == b""


def test_owner_view(seeded, as_user):
    html = html_of(as_user(OWNER), seeded)
    assert "Check in" in html and "Derived RAG" not in html
    assert "Sponsor pushing for January" not in html
    assert reverse("edit_risks", args=[seeded.entity_id]) in html_of(as_user(OWNER), seeded, "risks")


def test_auditor_sees_override_badge(seeded, as_user):
    as_user(TR).post(reverse("override_set", args=[seeded.entity_id]), {
        "value": "green", "reason": "Compensating controls", "support": "T-1", "review_by": "2030-01-01"})
    assert "Overridden:" in html_of(as_user(AUD), seeded) and ">Override<" in html_of(as_user(AUD), seeded)
    assert "Overridden:" not in html_of(as_user(CTO), seeded)


def test_override_set_and_modal(seeded, as_user):
    c = as_user(TR)
    url = reverse("override_set", args=[seeded.entity_id])
    c.post(url, {"value": "amber", "reason": "Interim controls cover the gap", "support": "TICKET-1",
                 "review_by": "2030-01-01"})
    ov = RagOverride.objects.current().get(initiative_entity=seeded.entity_id)
    assert ov.value == "amber" and ov.cleared_at is None
    r = c.get(reverse("override_modal", args=[seeded.entity_id]))
    assert r.status_code == 200
    assert "Interim controls cover the gap" in r.content.decode()
    assert "Auto-expires" in r.content.decode()


def test_override_requires_fields(seeded, as_user):
    as_user(TR).post(reverse("override_set", args=[seeded.entity_id]), {"value": "amber", "reason": "x"})
    assert not RagOverride.objects.filter(initiative_entity=seeded.entity_id).exists()


def test_override_forbidden_for_others(seeded, as_user):
    assert as_user(CTO).get(reverse("override_modal", args=[seeded.entity_id])).status_code == 403
    r = as_user(OWNER).post(reverse("override_set", args=[seeded.entity_id]),
                            {"value": "green", "reason": "a", "support": "b", "review_by": "2030-01-01"})
    assert r.status_code == 403


def test_set_visibility(seeded, as_user):
    risk = RiskItem.objects.current().get(initiative_entity=seeded.entity_id, title__startswith=INTERNAL_RISK)
    assert risk.visibility == Visibility.INTERNAL
    before = CurationEvent.objects.count()
    url = reverse("set_visibility", args=[seeded.entity_id, "risk", risk.entity_id])
    r = as_user(TR).post(url, {"visibility": "exec"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    new = RiskItem.objects.get_current(risk.entity_id)
    assert new.visibility == Visibility.EXEC
    assert CurationEvent.objects.count() == before + 1
    # now the CTO sees it
    assert INTERNAL_RISK in html_of(as_user(CTO), seeded, "risks")


def test_set_visibility_forbidden_for_owner(seeded, as_user):
    risk = RiskItem.objects.current().filter(initiative_entity=seeded.entity_id).first()
    url = reverse("set_visibility", args=[seeded.entity_id, "risk", risk.entity_id])
    assert as_user(OWNER).post(url, {"visibility": "internal"}).status_code == 403


def test_overlay_add_move_remove(seeded, as_user):
    from tracker.models import Overlay

    c = as_user(TR)
    c.post(reverse("overlay_add", args=[seeded.entity_id]), {"type": "highlight", "text": "Brand new highlight"})
    hls = list(Overlay.objects.current().filter(initiative_entity=seeded.entity_id, type="highlight").order_by("order"))
    assert hls[-1].text == "Brand new highlight"
    c.post(reverse("overlay_move", args=[seeded.entity_id, hls[-1].entity_id, "up"]))
    hls2 = list(Overlay.objects.current().filter(initiative_entity=seeded.entity_id, type="highlight").order_by("order"))
    assert hls2[-2].text == "Brand new highlight"
    c.post(reverse("overlay_remove", args=[seeded.entity_id, hls[-1].entity_id]))
    assert not Overlay.objects.current().filter(initiative_entity=seeded.entity_id, text="Brand new highlight").exists()
