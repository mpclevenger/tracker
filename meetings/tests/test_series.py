from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.urls import reverse

from core import clock
from core.models import ModuleSettings
from meetings import services
from meetings.models import FollowUp, Meeting, MeetingSeries
from meetings.tests.test_meetings import (  # noqa: F401
    AUD, CTO, OWNER, TODAY, TR, as_user, data, fixed_today, form_post, make_followup, make_meeting, second, seeded,
)
from tracker.services import triage

pytestmark = pytest.mark.django_db

SERIES_POST = {"name": "Vendor sync", "cadence": "monthly", "usual_attendees": "A, B", "default_stage": "3",
               "description": "Monthly sync"}


@pytest.fixture
def bare(seeded):
    """Seeded initiative with the sample meetings, follow-ups and series retired."""
    for model in (FollowUp, Meeting, MeetingSeries):
        for row in model.objects.current():
            row.retire(TR)
    return seeded


def make_series(init=None, **kw):
    fields = {"name": "Weekly steering", "cadence": "weekly", "usual_attendees": "Jane, John", "default_stage": 3}
    fields.update(kw)
    ids = [init.entity_id] if init else []
    return services.save_series(TR, None, fields | {"description": fields.get("description", "")}, ids)


def in_series(init, series, **kw):
    return make_meeting(init, series_entity=series.entity_id, **kw)


# --- series CRUD ---------------------------------------------------------------------------


def test_series_create_edit_and_remove_unlinks_meetings(bare, as_user):
    c = as_user(TR)
    r = c.post(reverse("meetings:series_new"), {**SERIES_POST, "initiatives": [str(bare.entity_id)]})
    assert r.status_code == 302
    s = MeetingSeries.objects.current().get(name="Vendor sync")
    assert s.cadence == "monthly" and s.default_stage == 3 and s.initiative_entities == [str(bare.entity_id)]
    assert c.post(reverse("meetings:series_edit", args=[s.entity_id]), {**SERIES_POST, "name": "Renamed"}).status_code == 302
    s = MeetingSeries.objects.get_current(s.entity_id)
    assert s.name == "Renamed" and s.initiative_entities == [] and MeetingSeries.objects.history(s.entity_id).count() == 2
    m = in_series(bare, s)
    assert c.post(reverse("meetings:series_remove", args=[s.entity_id])).status_code == 302
    assert not MeetingSeries.objects.current().filter(entity_id=s.entity_id).exists()
    kept = Meeting.objects.get_current(m.entity_id)
    assert kept.series_entity is None and kept.what == m.what
    assert Meeting.objects.history(m.entity_id).count() == 2
    assert c.get(reverse("meetings:series_detail", args=[s.entity_id])).status_code == 404


def test_series_name_required(bare, as_user):
    r = as_user(TR).post(reverse("meetings:series_new"), {**SERIES_POST, "name": ""})
    assert r.status_code == 200 and not MeetingSeries.objects.current().filter(name="").exists()


# --- log next meeting ----------------------------------------------------------------------


def test_log_next_meeting_prefill(bare, as_user, fixed_today):
    s = make_series(bare)
    html = as_user(TR).get(reverse("meetings:new"), {"series": str(s.entity_id)}).content.decode()
    assert 'value="Weekly steering"' in html and "Jane, John" in html
    assert f'<option value="{s.entity_id}" selected>' in html
    assert '<option value="3" selected>' in html and 'value="2026-11-08"' in html
    assert f'value="{bare.entity_id}" checked' in html or f'checked' in html.split(str(bare.entity_id))[1][:80]
    assert "Log next meeting" in as_user(TR).get(reverse("meetings:series_detail", args=[s.entity_id])).content.decode()


def test_series_select_sets_and_changes_series(bare, as_user):
    s1, s2 = make_series(bare), make_series(bare, name="Other")
    c = as_user(TR)
    html = c.get(reverse("meetings:new")).content.decode()
    assert "None (one-off)" in html and "Weekly steering" in html
    assert c.post(reverse("meetings:new"), form_post([bare.entity_id], series=str(s1.entity_id))).status_code == 302
    m = Meeting.objects.current().get(what="T")
    assert m.series_entity == s1.entity_id
    body = form_post([bare.entity_id], series=str(s2.entity_id))
    assert c.post(reverse("meetings:edit", args=[m.entity_id]), body).status_code == 302
    assert Meeting.objects.get_current(m.entity_id).series_entity == s2.entity_id
    body = form_post([bare.entity_id], series="")
    c.post(reverse("meetings:edit", args=[m.entity_id]), body)
    assert Meeting.objects.get_current(m.entity_id).series_entity is None
    bad = form_post([bare.entity_id], series=str(uuid4()))
    assert c.post(reverse("meetings:new"), bad).status_code == 200


# --- carried-over follow-ups ---------------------------------------------------------------


def test_close_carried_followups_with_note_rule(bare, as_user):
    s = make_series(bare)
    old = in_series(bare, s, held_on=TODAY - timedelta(days=7))
    fu = make_followup(old, bare, text="Send plan")
    keep = make_followup(old, bare, text="Keep open")
    c = as_user(TR)
    url = f'{reverse("meetings:new")}?series={s.entity_id}'
    html = c.get(url).content.decode()
    assert "Open from earlier meetings" in html and "Send plan" in html
    body = form_post([bare.entity_id], series=str(s.entity_id), **{f"close_{fu.entity_id}": "on"})
    r = c.post(url, body)
    assert r.status_code == 200 and "Enter a closure note" in r.content.decode()
    assert not Meeting.objects.current().filter(what="T").exists()
    assert FollowUp.objects.get_current(fu.entity_id).status == "open"
    body[f"note_{fu.entity_id}"] = "Sent on Monday"
    assert c.post(url, body).status_code == 302
    done = FollowUp.objects.get_current(fu.entity_id)
    assert done.status == "done" and done.closure_note == "Sent on Monday" and done.closed_on == date(2026, 11, 1)
    assert FollowUp.objects.get_current(keep.entity_id).status == "open"


def test_unchecked_carried_followup_stays_open(bare, as_user):
    s = make_series(bare)
    fu = make_followup(in_series(bare, s), bare)
    body = form_post([bare.entity_id], series=str(s.entity_id), **{f"note_{fu.entity_id}": "ignored"})
    as_user(TR).post(reverse("meetings:new"), body)
    assert FollowUp.objects.get_current(fu.entity_id).status == "open"


def test_no_carried_section_without_series(bare, as_user):
    make_followup(make_meeting(bare), bare)
    assert "Open from earlier" not in as_user(TR).get(reverse("meetings:new")).content.decode()


# --- pages ---------------------------------------------------------------------------------


def test_series_page_lists_newest_first_and_open_followups(bare, as_user):
    s = make_series(bare, usual_attendees="Jane, John")
    in_series(bare, s, what="Older one", held_on=TODAY - timedelta(days=7))
    new = in_series(bare, s, what="Newer one", held_on=TODAY)
    make_followup(new, bare, text="Open item")
    make_followup(new, bare, text="Done item", status="done", closed_on=TODAY, closure_note="ok")
    make_meeting(bare, what="Unrelated")
    html = as_user(TR).get(reverse("meetings:series_detail", args=[s.entity_id])).content.decode()
    assert html.index("Newer one") < html.index("Older one") and "Unrelated" not in html
    assert "Open follow-ups" in html and html.index("Open follow-ups") < html.index("Newer one")
    assert "Open item" in html.split("Newer one")[0] and "Done item" in html
    assert "Every 2 weeks" not in html and "Weekly" in html and bare.name in html
    assert "Edit series" in html and "Remove series" in html


def test_series_list_row(bare, as_user):
    s = make_series(bare)
    m = in_series(bare, s, held_on=TODAY)
    make_followup(m, bare)
    html = as_user(TR).get(reverse("meetings:series_list")).content.decode()
    assert "Weekly steering" in html and "Nov 8, 2026" in html and "Add series" in html and bare.name in html
    assert "Series" in as_user(TR).get(reverse("meetings:list")).content.decode()


def test_meetings_list_series_tag(bare, as_user):
    s = make_series(bare)
    in_series(bare, s, what="In series")
    make_meeting(bare, what="Alone")
    html = as_user(TR).get(reverse("meetings:list")).content.decode()
    link = reverse("meetings:series_detail", args=[s.entity_id])
    assert html.count(f'href="{link}"') == 1


def _tab_html(init, emp_id, rf):
    from django.template.loader import render_to_string

    from core.auth.actor import resolve_actor
    from meetings.views import initiative_tab_context

    request = rf.get("/")
    request.actor = resolve_actor(emp_id)
    ctx = initiative_tab_context(request, init)
    return render_to_string("meetings/partials/initiative_meetings.html", {**ctx, "actor": request.actor}, request)


def test_initiative_tab_groups_by_series(bare, rf):
    s = make_series(bare)
    in_series(bare, s, what="Series one")
    make_meeting(bare, what="Loose one")
    html = _tab_html(bare, TR, rf)
    link = reverse("meetings:series_detail", args=[s.entity_id])
    assert f'<a href="{link}">Weekly steering</a>' in html and "Other meetings" in html
    assert html.index("Weekly steering") < html.index("Series one") < html.index("Other meetings") < html.index("Loose one")


def test_initiative_tab_unchanged_without_series(bare, rf):
    make_meeting(bare, what="Loose one")
    html = _tab_html(bare, TR, rf)
    assert "Other meetings" not in html and "Loose one" in html


def test_initiative_tab_series_not_linked_for_owner(bare, rf):
    s = make_series(bare)
    in_series(bare, s)
    html = _tab_html(bare, OWNER, rf)
    assert "Weekly steering" in html and reverse("meetings:series_detail", args=[s.entity_id]) not in html


# --- permissions and module ----------------------------------------------------------------


def test_series_permissions(bare, as_user):
    s = make_series(bare)
    detail, edit = (reverse(f"meetings:series_{n}", args=[s.entity_id]) for n in ("detail", "edit"))
    c = as_user(AUD)
    assert c.get(reverse("meetings:series_list")).status_code == 200 and c.get(detail).status_code == 200
    html = c.get(detail).content.decode()
    assert "Log next meeting" not in html and "Edit series" not in html and "Remove series" not in html
    assert "Add series" not in c.get(reverse("meetings:series_list")).content.decode()
    for url in (reverse("meetings:series_new"), edit):
        assert c.get(url).status_code == 403
    assert c.post(reverse("meetings:series_remove", args=[s.entity_id])).status_code == 403
    assert MeetingSeries.objects.current().filter(entity_id=s.entity_id).exists()
    for emp in (OWNER, CTO):
        c = as_user(emp)
        assert c.get(reverse("meetings:series_list")).status_code == 403 and c.get(detail).status_code == 403


def test_series_hidden_when_module_off(bare, as_user):
    s = make_series(bare)
    row = ModuleSettings.objects.current().first()
    fields = {k: True for k in ("owner_access", "meetings", "audit", "share_links", "watch_list")} | {"meetings": False}
    (row.new_version("SYS", **fields, change_note="t") if row else ModuleSettings.create("SYS", **fields, change_note="t"))
    c = as_user(TR)
    for name, args in (("series_list", []), ("series_new", []), ("series_detail", [s.entity_id]),
                       ("series_edit", [s.entity_id])):
        assert c.get(reverse(f"meetings:{name}", args=args)).status_code == 404
    assert c.post(reverse("meetings:series_remove", args=[s.entity_id])).status_code == 404


# --- unchanged without a series --------------------------------------------------------------


def test_meeting_without_series_unchanged(bare, as_user):
    c = as_user(TR)
    assert c.post(reverse("meetings:new"), form_post([bare.entity_id])).status_code == 302
    m = Meeting.objects.current().get(what="T")
    assert m.series_entity is None
    assert c.get(reverse("meetings:list")).status_code == 200
    services.save_meeting(TR, m, data(what="T", result="KP", who="A", stage=3, held_on=date(2026, 11, 1)), [bare.entity_id])
    assert Meeting.objects.history(m.entity_id).count() == 1


# --- Workbench missed-meeting row ------------------------------------------------------------


def _chase(scope="all", meetings_on=True):
    rows, _ = triage.triage(meetings_on, scope)
    return [r for r in rows["Chase"] if r.series]


def test_missed_weekly_series_flags(bare, monkeypatch):
    s = make_series(bare)
    in_series(bare, s, held_on=date(2026, 11, 1))
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 9))
    (row,) = _chase()
    assert row.what == "Weekly steering: no meeting logged since Nov 1" and row.action == "review"
    assert row.series.entity_id == s.entity_id


def test_series_not_yet_due(bare, monkeypatch):
    s = make_series(bare)
    in_series(bare, s, held_on=date(2026, 11, 1))
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 8))
    assert _chase() == []
    services.save_series(TR, s, {**{k: getattr(s, k) for k in services.SERIES_FIELDS}, "cadence": "biweekly"}, [bare.entity_id])
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 15))
    assert _chase() == []
    monkeypatch.setattr(clock, "today", lambda: date(2026, 11, 16))
    assert len(_chase()) == 1


def test_adhoc_series_never_flags(bare, monkeypatch):
    make_series(bare, cadence="adhoc")
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=400))
    assert _chase() == []


def test_series_without_meetings_flags_after_cadence(bare, monkeypatch):
    make_series(bare)
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=8))
    (row,) = _chase()
    assert row.what == "Weekly steering: no meetings logged yet"
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=6))
    assert _chase() == []


def test_missed_series_hidden_when_module_off(bare, monkeypatch):
    make_series(bare)
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=30))
    assert len(_chase()) == 1 and _chase(meetings_on=False) == []


def test_missed_series_sox_scope(bare, second, monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=30))
    make_series(bare, name="In scope")
    make_series(second, name="Out of scope")
    make_series(None, name="No initiatives")
    from tracker.models import Initiative

    sox = Initiative.objects.current().filter(sox_applicable=True).values_list("entity_id", flat=True)
    assert len(_chase("all")) == 3
    names = {r.series.name for r in _chase("sox")}
    assert names == {n for n, i in (("In scope", bare), ("Out of scope", second)) if i.entity_id in set(sox)}


def test_missed_series_row_links_to_series(bare, as_user, monkeypatch):
    s = make_series(bare)
    monkeypatch.setattr(clock, "today", lambda: date.today() + timedelta(days=30))
    html = as_user(TR).get("/workbench/").content.decode()
    link = reverse("meetings:series_detail", args=[s.entity_id])
    assert f'href="{link}"' in html and "no meetings logged yet" in html and "Review" in html
