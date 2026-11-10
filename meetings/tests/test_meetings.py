from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.urls import reverse
from scripts.generate_fake_data import seed as generate_fake_data

from core import clock
from meetings import services
from meetings.models import FollowUp, Meeting, MeetingInitiative
from tracker.models import CurationEvent, Initiative
from tracker.services import exec_summary, triage

pytestmark = pytest.mark.django_db

TR, OWNER, CTO, AUD = "E00001", "E00101", "E00201", "E00301"
TODAY = date(2026, 11, 8)


@pytest.fixture
def seeded(db):
    generate_fake_data(stdout=lambda *a: None)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


@pytest.fixture
def bare(seeded):
    """Seeded initiative with the sample follow-ups retired, so counts depend only on the test."""
    for f in FollowUp.objects.current():
        f.retire(TR)
    return seeded


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: TODAY)
    return TODAY


@pytest.fixture
def as_user(client):
    def _as(emp_id):
        client.post("/auth/switch/", {"emp_id": emp_id})
        return client

    return _as


@pytest.fixture
def second(seeded):
    return Initiative.create(
        "AUTH", name="Second initiative", owner_emp_id="OWN2", status=Initiative.Status.ACTIVE
    )


def data(**kw):
    return {"held_on": TODAY, "what": "Kickoff", "who": "A, B", "result": "Point one", "stage": 2, **kw}


def make_meeting(init, **kw):
    m = services.save_meeting(TR, None, data(**kw), [init.entity_id])
    return m


def make_followup(m, init, **kw):
    kw.setdefault("text", "Do the thing")
    return FollowUp.create(TR, meeting_entity=m.entity_id, initiative_entity=init.entity_id if init else None, **kw)


def form_post(init_ids, followups=(), **kw):
    body = {
        "held_on": "2026-11-01", "what": "T", "who": "A", "result": "KP", "stage": "3",
        "initiatives": [str(i) for i in init_ids],
        "fu-TOTAL_FORMS": str(len(followups) + 3), "fu-INITIAL_FORMS": str(sum(1 for f in followups if f.get("entity_id"))),
        "fu-MIN_NUM_FORMS": "0", "fu-MAX_NUM_FORMS": "1000",
    }
    body.update(kw)
    for n, f in enumerate(followups):
        for k, v in f.items():
            body[f"fu-{n}-{k}"] = v
    return body


# --- models and versioning ---------------------------------------------------------------


def test_edit_creates_new_version_and_keeps_history(seeded):
    m = make_meeting(seeded)
    edited = services.save_meeting(TR, m, data(result="Changed"), [seeded.entity_id])
    assert edited.entity_id == m.entity_id and edited.pk != m.pk
    assert Meeting.objects.history(m.entity_id).count() == 2
    assert Meeting.objects.get_current(m.entity_id).result == "Changed"
    with pytest.raises(ValueError):
        Meeting.objects.get(pk=m.pk).save()


def test_unchanged_save_adds_no_version(seeded):
    m = make_meeting(seeded)
    services.save_meeting(TR, m, data(), [seeded.entity_id])
    assert Meeting.objects.history(m.entity_id).count() == 1


def test_save_meeting_touches_initiative(seeded):
    make_meeting(seeded)
    assert CurationEvent.objects.filter(initiative_entity=seeded.entity_id, what="meeting logged").exists()


# --- overdue ----------------------------------------------------------------------------


def test_overdue_logic(bare, fixed_today):
    m = make_meeting(bare)
    late = make_followup(m, bare, due_date=TODAY - timedelta(days=5))
    due_today = make_followup(m, bare, due_date=TODAY)
    no_due = make_followup(m, bare)
    done = make_followup(m, bare, due_date=TODAY - timedelta(days=9), status="done", closed_on=TODAY, closure_note="ok")
    assert late.is_overdue and late.days_overdue == 5
    assert not due_today.is_overdue and not no_due.is_overdue and not done.is_overdue
    got = {f.entity_id for f in services.overdue_followups([bare.entity_id])}
    assert got == {late.entity_id}
    assert services.overdue_followups([uuid4()]) == []
    assert late.entity_id in {f.entity_id for f in services.open_followups()}
    assert done.entity_id not in {f.entity_id for f in services.open_followups()}


# --- close and reopen -------------------------------------------------------------------


def test_close_requires_note(seeded):
    m = make_meeting(seeded)
    f = make_followup(m, seeded)
    with pytest.raises(ValueError):
        services.close_followup(TR, f, TODAY, "  ")
    closed = services.close_followup(TR, f, TODAY, "Done and evidenced")
    assert closed.status == "done" and closed.closed_on == TODAY
    assert CurationEvent.objects.filter(initiative_entity=seeded.entity_id, what="follow-up closed").exists()
    reopened = services.reopen_followup(TR, closed)
    assert reopened.status == "open" and reopened.closed_on is None and reopened.closure_note == ""


def test_close_view_requires_note(seeded, as_user):
    m = make_meeting(seeded)
    f = make_followup(m, seeded)
    c = as_user(TR)
    url = reverse("meetings:followup_close", args=[f.entity_id])
    assert c.get(url).status_code == 200
    r = c.post(url, {"closed_on": "2026-11-08", "closure_note": ""})
    assert r.status_code == 200
    assert FollowUp.objects.get_current(f.entity_id).status == "open"
    r = c.post(url, {"closed_on": "2026-11-08", "closure_note": "Evidence filed"})
    assert r.status_code == 302
    assert FollowUp.objects.get_current(f.entity_id).status == "done"
    c.post(reverse("meetings:followup_reopen", args=[f.entity_id]))
    assert FollowUp.objects.get_current(f.entity_id).status == "open"


# --- links ------------------------------------------------------------------------------


def test_link_add_and_remove_retires_rows(seeded, second):
    m = make_meeting(seeded)
    services.save_meeting(TR, m, data(), [seeded.entity_id, second.entity_id])
    assert services.linked_initiatives(m.entity_id) == {seeded.entity_id, second.entity_id}
    services.save_meeting(TR, m, data(), [second.entity_id])
    assert services.linked_initiatives(m.entity_id) == {second.entity_id}
    old = MeetingInitiative.objects.history(  # retired link keeps a tombstone version
        MeetingInitiative.objects.filter(meeting_entity=m.entity_id, initiative_entity=seeded.entity_id).first().entity_id
    )
    assert old.last().is_deleted
    assert m.entity_id not in {x.entity_id for x in services.meetings_for_initiative(seeded.entity_id)}
    assert [x.entity_id for x in services.meetings_for_initiative(second.entity_id)] == [m.entity_id]


def test_form_creates_edits_and_deletes_followups(seeded, as_user):
    c = as_user(TR)
    body = form_post([seeded.entity_id], [{"text": "First", "owner": "Pat", "due_date": "2026-11-20", "initiative": str(seeded.entity_id)}])
    r = c.post(reverse("meetings:new"), body)
    assert r.status_code == 302, r.content.decode()[:500]
    m = Meeting.objects.current().get(what="T")
    fu = FollowUp.objects.current().get(meeting_entity=m.entity_id)
    assert fu.text == "First" and fu.initiative_entity == seeded.entity_id
    edit = reverse("meetings:edit", args=[m.entity_id])
    assert c.get(edit).status_code == 200
    body = form_post([seeded.entity_id], [{"entity_id": str(fu.entity_id), "text": "First", "DELETE": "on"}])
    assert c.post(edit, body).status_code == 302
    assert not FollowUp.objects.current().filter(meeting_entity=m.entity_id).exists()
    assert FollowUp.objects.history(fu.entity_id).last().is_deleted


def test_followup_initiative_must_be_linked(seeded, second, as_user):
    body = form_post([seeded.entity_id], [{"text": "X", "initiative": str(second.entity_id)}])
    r = as_user(TR).post(reverse("meetings:new"), body)
    assert r.status_code == 200 and "linked initiative" in r.content.decode()
    assert not Meeting.objects.current().filter(what="T").exists()


def test_required_fields(seeded, as_user):
    body = form_post([], who="", result="")
    r = as_user(TR).post(reverse("meetings:new"), body)
    assert r.status_code == 200 and not Meeting.objects.current().filter(what="T").exists()


# --- permissions ------------------------------------------------------------------------


def test_list_permissions(seeded, as_user):
    url = reverse("meetings:list")
    assert as_user(TR).get(url).status_code == 200
    assert as_user(AUD).get(url).status_code == 200
    assert as_user(OWNER).get(url).status_code == 403
    assert as_user(CTO).get(url).status_code == 403


def test_auditor_read_only(seeded, as_user):
    m = Meeting.objects.current().get(what="Connector defects checkpoint")
    f = FollowUp.objects.current().filter(status="open").get()
    c = as_user(AUD)
    html = c.get(reverse("meetings:list")).content.decode()
    assert "Log a meeting" not in html and "/edit/" not in html
    assert c.get(reverse("meetings:new")).status_code == 403
    assert c.get(reverse("meetings:edit", args=[m.entity_id])).status_code == 403
    assert c.get(reverse("meetings:followup_close", args=[f.entity_id])).status_code == 403
    assert c.post(reverse("meetings:followup_close", args=[f.entity_id]), {"closed_on": "2026-11-08", "closure_note": "x"}).status_code == 403
    assert c.post(reverse("meetings:followup_reopen", args=[f.entity_id])).status_code == 403
    assert FollowUp.objects.get_current(f.entity_id).status == "open"


def test_tech_risk_sees_edit_and_overdue_pill(seeded, as_user):
    html = as_user(TR).get(reverse("meetings:list")).content.decode()
    assert "Log a meeting" in html and "Edit" in html
    assert "Overdue · 7d" in html or "Overdue ·" in html
    assert "Connector defects checkpoint" in html


def test_list_filters(seeded, second, as_user):
    other = make_meeting(second, what="Other meeting")
    c = as_user(TR)
    url = reverse("meetings:list")
    html = c.get(url, {"initiative": str(second.entity_id)}).content.decode()
    assert "Other meeting" in html and "Connector defects checkpoint" not in html
    html = c.get(url, {"open": "1"}).content.decode()
    assert "Connector defects checkpoint" in html and "Other meeting" not in html
    assert "Confirm the certification report" not in html


def test_initiative_tab_context(seeded, second, rf):
    from meetings.views import initiative_tab_context

    newer = make_meeting(seeded, what="Newer", held_on=TODAY)
    make_followup(newer, seeded, text="Mine")
    make_followup(newer, second, text="Someone else's")
    other = make_meeting(second, what="Elsewhere")
    request = rf.get("/")
    request.actor = _actor(TR)
    ctx = initiative_tab_context(request, seeded)
    whats = [c["meeting"].what for c in ctx["meetings_tab"]]
    assert whats[0] == "Newer" and "Elsewhere" not in whats and other.what not in whats
    first = ctx["meetings_tab"][0]
    assert first["stage"].number == 2 and first["stage"].color
    assert [f.text for f in first["followups"]] == ["Mine"]
    assert ctx["can_log_meeting"] is True
    assert ctx["log_meeting_url"] == f"{reverse('meetings:new')}?initiative={seeded.entity_id}"
    request.actor = _actor(OWNER)
    assert initiative_tab_context(request, seeded)["can_log_meeting"] is False


def test_initiative_meetings_partial(seeded, rf):
    from django.template.loader import render_to_string

    from meetings.views import initiative_tab_context

    request = rf.get("/")
    request.actor = _actor(TR)
    ctx = initiative_tab_context(request, seeded)
    html = render_to_string("meetings/partials/initiative_meetings.html", {**ctx, "actor": request.actor}, request)
    assert "Log a meeting" in html and "Connector defects checkpoint" in html
    assert "Overdue" in html and "Close" in html and "2. Build" in html
    request.actor = _actor(AUD)
    html = render_to_string("meetings/partials/initiative_meetings.html", {**initiative_tab_context(request, seeded), "actor": request.actor}, request)
    assert "Log a meeting" not in html and ">Close</a>" not in html and "Overdue" in html


def test_form_defaults_to_initiative_stage(seeded, as_user):
    from tracker.services.bundle import load_bundle

    stage = load_bundle(seeded).current_stage()
    html = as_user(TR).get(reverse("meetings:new"), {"initiative": str(seeded.entity_id)}).content.decode()
    assert f'<option value="{stage}" selected>' in html
    assert "Result / next steps" in html and "Key points" not in html


def test_stage_is_saved_and_shown(seeded, as_user):
    c = as_user(TR)
    assert c.post(reverse("meetings:new"), form_post([seeded.entity_id])).status_code == 302
    assert Meeting.objects.current().get(what="T").stage == 3
    html = c.get(reverse("meetings:list")).content.decode()
    assert "3. Test" in html and "Result / next steps" in html


def test_unlinked_meeting_only_on_meetings_page(seeded, as_user):
    m = Meeting.create(TR, held_on=TODAY, what="Floating meeting", who="A", result="K", stage=1)
    assert "Floating meeting" in as_user(TR).get(reverse("meetings:list")).content.decode()
    assert "Floating meeting" not in as_user(CTO).get(reverse("initiative", args=[seeded.entity_id])).content.decode()
    assert services.meetings_for_initiative(seeded.entity_id) and m.entity_id not in {
        x.entity_id for x in services.meetings_for_initiative(seeded.entity_id)
    }


# --- triage, exec and history -----------------------------------------------------------


def test_triage_chase_row(bare, fixed_today):
    m = make_meeting(bare)
    make_followup(m, bare, text="Send the evidence pack", due_date=TODAY - timedelta(days=4))
    make_followup(m, None, text="Unlinked one", due_date=TODAY - timedelta(days=4))
    rows, _ = triage.triage()
    whats = [r.what for r in rows["Chase"]]
    assert "Follow-up overdue 4d: Send the evidence pack" in whats
    assert not any("Unlinked one" in w for w in whats)
    row = next(r for r in rows["Chase"] if r.what.startswith("Follow-up overdue"))
    assert row.severity == "red" and row.since == TODAY - timedelta(days=4)


def test_exec_overdue_count(bare, fixed_today):
    m = make_meeting(bare)
    make_followup(m, bare, due_date=TODAY - timedelta(days=4))
    make_followup(m, bare, due_date=TODAY - timedelta(days=1))
    ctx = exec_summary.build(_actor(CTO))
    assert [n for _, n in ctx.overdue_followups] == [2]


def _actor(emp_id):
    from core.auth.actor import resolve_actor

    return resolve_actor(emp_id)


def test_overdue_does_not_affect_rag(bare, fixed_today):
    from tracker.services import rag
    from tracker.services.bundle import load_bundle

    before = rag.evaluate(load_bundle(bare), persist=False).effective
    m = make_meeting(bare)
    make_followup(m, bare, due_date=TODAY - timedelta(days=90))
    assert rag.evaluate(load_bundle(bare), persist=False).effective == before


def test_history_includes_meeting_and_followup(seeded, as_user):
    m = services.save_meeting(TR, Meeting.objects.current().get(what="Connector defects checkpoint"), data(what="Renamed sample"), [seeded.entity_id])
    services.save_meeting(TR, m, data(what="Renamed sample", result="Edited points"), [seeded.entity_id])
    html = as_user(TR).get(reverse("initiative_history", args=[seeded.entity_id])).content.decode()
    assert "Meeting · " in html and "Edited points" in html
    assert "Follow-up · Share the connector defect burn-down chart" in html


def test_seed_is_idempotent(seeded):
    generate_fake_data(stdout=lambda *a: None)
    assert Meeting.objects.current().count() == 3
    assert FollowUp.objects.current().count() == 3


def test_untagged_followup_takes_single_linked_initiative(bare, second):
    """A follow-up left blank on a one-initiative meeting is tagged to it; on a multi-initiative meeting it stays blank."""
    row = {"entity_id": None, "delete": False, "text": "Chase vendor", "owner": "", "due_date": None,
           "initiative_entity": None}
    single = make_meeting(bare)
    services.save_followups(TR, single, [dict(row)])
    assert FollowUp.objects.current().get(meeting_entity=single.entity_id).initiative_entity == bare.entity_id
    multi = services.save_meeting(TR, None, data(), [bare.entity_id, second.entity_id])
    services.save_followups(TR, multi, [dict(row)])
    assert FollowUp.objects.current().get(meeting_entity=multi.entity_id).initiative_entity is None
