from datetime import timedelta

import pytest
from django.urls import reverse

from core import clock
from core.auth.actor import resolve_actor
from core.models import ModuleSettings, Role
from meetings.views import _initiative_choices
from tracker.models import CurationEvent, Initiative, Snapshot
from tracker.services import curation, exec_summary, snapshots, triage, watch
from tracker.visibility import initiatives_for

pytestmark = pytest.mark.django_db

TODAY = clock.today()
D = lambda n: TODAY + timedelta(days=n)  # noqa: E731


@pytest.fixture
def people(grant):
    grant("TR1", Role.TECH_RISK)
    grant("AUD1", Role.AUDITOR)
    grant("CTO1", Role.SENIOR_MANAGEMENT)


@pytest.fixture
def item(people):
    return watch.create_watch_item(
        "TR1", name="Vendor X renewal", owner_emp_id="OWN9", decision="Renew or move", decision_due=D(10),
        key_date=D(40), key_date_label="Contract ends", options_considered="Renew; move", sox_applicable=True,
        moves_to_vendor_or_cloud=True, next_check=D(-1),
    )


def login(client, emp):
    client.post("/auth/switch/", {"emp_id": emp})
    return client


def modules_off(**flags):
    ModuleSettings.create("SYS", **{"watch_list": True, **flags, "change_note": "t"})


# --- service ---------------------------------------------------------------------------------------

def test_create_and_update(item):
    assert item.status == Initiative.Status.WATCH
    new = watch.update_watch_item(item, "TR1", decision="Renew now")
    assert new.decision == "Renew now" and new.entity_id == item.entity_id
    assert watch.update_watch_item(new, "TR1", decision="Renew now") is new
    with pytest.raises(ValueError):
        watch.update_watch_item(new, "TR1", status="active")


def test_confirm_goes_to_intake_queue_with_fields(item, client):
    new = watch.confirm_watch_item(item, "TR1")
    assert new.status == Initiative.Status.PROPOSED and new.submitted_at
    rows, _ = triage.triage()
    row = rows["Decide"][0]
    init = row.bundle.initiative
    assert (init.entity_id, init.decision, init.options_considered, init.sox_applicable) == (
        item.entity_id, "Renew or move", "Renew; move", True)
    assert init.moves_to_vendor_or_cloud and init.owner_emp_id == "OWN9"
    login(client, "TR1")
    assert client.post(reverse("intake_accept", args=[item.entity_id])).status_code == 302
    assert Initiative.objects.get_current(item.entity_id).status == Initiative.Status.ACTIVE
    with pytest.raises(ValueError):
        watch.confirm_watch_item(new, "TR1")


def test_drop_requires_reason(item):
    for bad in ("", "  "):
        with pytest.raises(ValueError):
            watch.drop_watch_item(item, "TR1", bad)
    new = watch.drop_watch_item(item, "TR1", "Renewed as is")
    assert new.status == Initiative.Status.DROPPED and new.outcome_reason == "Renewed as is"
    assert [h["outcome"] for h in watch.history()] == ["No change needed: Renewed as is"]


def test_history_lists_confirmed(item):
    watch.confirm_watch_item(item, "TR1")
    assert [h["outcome"] for h in watch.history()] == ["Confirmed as initiative"]


@pytest.mark.parametrize("fields,chips", [
    ({"decision_due": D(30)}, ["Decision due in 30d"]),
    ({"decision_due": D(31)}, []),
    ({"decision_due": D(0)}, ["Decision due in 0d"]),
    ({"decision_due": D(-3)}, ["Decision overdue 3d"]),
    ({"key_date": D(90), "key_date_label": "Contract ends"}, ["Contract ends in 90d, no decision"]),
    ({"key_date": D(91)}, []),
    ({"key_date": D(5)}, ["Key date in 5d, no decision"]),
    ({"next_check": D(-1)}, ["Check due"]),
    ({"next_check": D(0)}, []),
    ({}, []),
])
def test_warnings(people, fields, chips):
    it = watch.create_watch_item("TR1", name="X", owner_emp_id="O", **fields)
    assert watch.watch_warnings(it, TODAY) == chips


# --- exclusion from initiative listings -------------------------------------------------------------

def test_excluded_from_initiative_listings(item, make_initiative, people):
    dropped = watch.drop_watch_item(
        watch.create_watch_item("TR1", name="Gone", owner_emp_id="OWN9", sox_applicable=True), "TR1", "n/a")
    real = make_initiative(name="Real", owner="OWN1", sox_applicable=True)
    tr = resolve_actor("TR1")
    assert [i.name for i in initiatives_for(tr)] == ["Real"]
    ctx = exec_summary.build(tr)
    assert [b.initiative.name for b in ctx.timeline_bundles] == ["Real"] and ctx.counts["active"] == 1
    assert not ctx.proposed and all(t.name == "Real" for t in ctx.attention + ctx.on_track)
    rows, _ = triage.triage()
    assert all(r.bundle.initiative.entity_id != item.entity_id for g in ("Decide", "Confirm", "Review", "Chase") for r in rows[g])
    assert not resolve_actor("OWN9").is_any_owner
    assert [name for _, name in _initiative_choices()] == ["Real"]
    snap = snapshots.take_snapshot("TR1", "S")
    assert set(snapshots.rows_by_entity(snap)) == {real.entity_id}
    assert dropped.entity_id not in snapshots.rows_by_entity(snap)


def test_no_initiative_page_or_my_initiative(item, client, people):
    assert login(client, "TR1").get(reverse("initiative", args=[item.entity_id])).status_code == 404
    assert login(client, "OWN9").get(reverse("my_initiative")).status_code in (403, 302)


def test_audit_home_and_snapshot_record_have_no_watch_rows(item, client):
    snap = snapshots.take_snapshot("TR1", "S")
    r = login(client, "AUD1").get(reverse("snapshot_record", args=[snap.pk]))
    assert "Vendor X renewal" not in r.content.decode()


# --- pages and permissions --------------------------------------------------------------------------

def test_view_permissions(item, client):
    for emp, code in (("TR1", 200), ("CTO1", 200), ("AUD1", 200), ("OWN9", 403)):
        c = login(client, emp)
        assert c.get("/watch/").status_code == code
        assert c.get(f"/watch/{item.entity_id}/").status_code == code


def test_only_tech_risk_changes(item, client):
    for emp in ("CTO1", "AUD1", "OWN9"):
        c = login(client, emp)
        assert c.get("/watch/add/").status_code == 403
        assert c.get(f"/watch/{item.entity_id}/edit/").status_code == 403
        for action in ("confirm", "drop", "delete"):
            assert c.post(f"/watch/{item.entity_id}/{action}/", {"reason": "x"}).status_code == 403
    assert Initiative.objects.get_current(item.entity_id).status == Initiative.Status.WATCH
    html = login(client, "CTO1").get("/watch/").content.decode()
    assert "Add watch item" not in html


def test_add_edit_confirm_drop_delete_flow(people, client):
    c = login(client, "TR1")
    r = c.post("/watch/add/", {"name": "New thing", "owner_emp_id": "E5", "owner_name": "Pat",
                               "decision": "Pick one", "moves_to_vendor_or_cloud": "on", "sox_applicable": "on"})
    assert r.status_code == 302
    it = Initiative.objects.watch_items().get(name="New thing")
    assert it.moves_to_vendor_or_cloud and it.sox_applicable
    page = c.get(r.url).content.decode()
    assert "Likely oversight" in page and "Standard" in page and "Pat" in page
    assert c.post(f"/watch/{it.entity_id}/edit/", {"name": "New thing 2", "owner_emp_id": "E5"}).status_code == 302
    assert Initiative.objects.get_current(it.entity_id).name == "New thing 2"
    c.post(f"/watch/{it.entity_id}/drop/", {"reason": ""})
    assert Initiative.objects.get_current(it.entity_id).status == Initiative.Status.WATCH
    c.post(f"/watch/{it.entity_id}/drop/", {"reason": "Not needed"})
    assert Initiative.objects.get_current(it.entity_id).status == Initiative.Status.DROPPED
    assert "Not needed" in c.get("/watch/").content.decode()
    other = watch.create_watch_item("TR1", name="Oops", owner_emp_id="E5")
    assert c.post(f"/watch/{other.entity_id}/delete/", {"reason": ""}).status_code == 302
    assert Initiative.objects.watch_items().filter(name="Oops").exists()
    c.post(f"/watch/{other.entity_id}/delete/", {"reason": "Mistake"})
    assert not Initiative.objects.watch_items().filter(name="Oops").exists()
    assert CurationEvent.objects.filter(initiative_entity=other.entity_id, what__startswith=curation.DELETE_PREFIX).exists()


def test_confirm_view_sends_to_queue(item, client):
    c = login(client, "TR1")
    assert c.post(f"/watch/{item.entity_id}/confirm/").status_code == 302
    assert Initiative.objects.get_current(item.entity_id).status == Initiative.Status.PROPOSED


def test_list_sorted_blanks_last_and_history_section(people, client):
    watch.create_watch_item("TR1", name="No date", owner_emp_id="O")
    watch.create_watch_item("TR1", name="Late", owner_emp_id="O", decision_due=D(50))
    watch.create_watch_item("TR1", name="Soon", owner_emp_id="O", decision_due=D(5))
    html = login(client, "TR1").get("/watch/").content.decode()
    assert html.index("Soon") < html.index("Late") < html.index("No date")
    assert "<details" in html and "Dropped / Confirmed history" in html


def test_history_page_for_watch_item(item, client):
    r = login(client, "TR1").get(reverse("initiative_history", args=[item.entity_id]))
    assert r.status_code == 200 and "Vendor X renewal" in r.content.decode()
    assert login(client, "AUD1").get(reverse("initiative_history", args=[item.entity_id])).status_code == 200


# --- SOX scope -------------------------------------------------------------------------------------

def test_sox_scope(item, client):
    watch.create_watch_item("TR1", name="Plain item", owner_emp_id="O")
    c = login(client, "TR1")
    assert "Plain item" in c.get("/watch/").content.decode()
    html = c.get("/watch/?scope=sox").content.decode()
    assert "Vendor X renewal" in html and "Plain item" not in html
    aud = login(client, "AUD1").get("/watch/").content.decode()  # auditors default to SOX
    assert "Vendor X renewal" in aud and "Plain item" not in aud


# --- module switch ---------------------------------------------------------------------------------

def test_module_off(item, client):
    modules_off(watch_list=False)
    c = login(client, "TR1")
    for url in ("/watch/", "/watch/add/", f"/watch/{item.entity_id}/", f"/watch/{item.entity_id}/edit/"):
        assert c.get(url).status_code == 404
    assert c.post(f"/watch/{item.entity_id}/confirm/").status_code == 404
    assert 'href="/watch/"' not in c.get("/exec/").content.decode()
    assert "On the watch list" not in c.get("/exec/").content.decode()
    rows, _ = triage.triage(watch_list=False)
    assert "Watch" not in rows
    assert "Vendor X renewal" not in c.get("/workbench/").content.decode()


def test_module_on_shows_nav(item, client):
    assert 'href="/watch/"' in login(client, "CTO1").get("/exec/").content.decode()


# --- triage group ----------------------------------------------------------------------------------

def test_triage_group(item, people):
    watch.create_watch_item("TR1", name="Quiet", owner_emp_id="O", decision_due=D(200))
    rows, _ = triage.triage()
    assert triage.GROUPS[-1] == "Watch" and triage.GROUP_LABELS["Watch"] == "Watch list"
    assert [(r.bundle.initiative.name, r.what, r.action, r.watch) for r in rows["Watch"]] == [
        ("Vendor X renewal", "Decision due in 10d, Contract ends in 40d, no decision, Check due", "review", True)]
    assert "Watch" in triage.triage(scope="sox")[0]
    watch.update_watch_item(Initiative.objects.get_current(item.entity_id), "TR1", sox_applicable=False)
    assert "Watch" not in triage.triage(scope="sox")[0]


def test_triage_page_links_to_watch_item(item, client):
    html = login(client, "TR1").get("/workbench/").content.decode()
    assert "Watch list" in html and f"/watch/{item.entity_id}/" in html


# --- exec block and snapshot -----------------------------------------------------------------------

def test_exec_block_cap_and_order(people, client):
    for n in range(8):
        watch.create_watch_item("TR1", name=f"W{n}", owner_emp_id="O", decision="Choose", decision_due=D(20 - n))
    ctx = exec_summary.build(resolve_actor("CTO1"))
    assert [w["name"] for w in ctx.watch] == ["W7", "W6", "W5", "W4", "W3", "W2"] and ctx.watch_total == 8
    html = login(client, "CTO1").get("/exec/").content.decode()
    assert "On the watch list" in html and 'href="/watch/?scope=all"' in html


def test_exec_block_scope(item, people):
    watch.create_watch_item("TR1", name="Plain", owner_emp_id="O")
    assert [w["name"] for w in exec_summary.build(resolve_actor("TR1"), scope="sox").watch] == ["Vendor X renewal"]


def test_snapshot_freezes_watch(item, client):
    snap = snapshots.take_snapshot("TR1", "S")
    frozen = snap.frozen_summary["watch"]
    assert frozen == [{"name": "Vendor X renewal", "decision": "Renew or move", "decision_due": D(10).isoformat(),
                       "key_date_label": "Contract ends", "key_date": D(40).isoformat(), "sox_applicable": True}]
    watch.drop_watch_item(item, "TR1", "done")
    c = login(client, "CTO1")
    live = c.get("/exec/").content.decode()
    frozen_html = c.get(f"/exec/?snapshot={snap.pk}").content.decode()
    assert "On the watch list" not in live
    assert "On the watch list" in frozen_html and "Vendor X renewal" in frozen_html and "Contract ends" in frozen_html


def test_old_snapshot_without_watch_shows_no_block(item, client):
    snap = snapshots.take_snapshot("TR1", "S")
    summary = {k: v for k, v in snap.frozen_summary.items() if k != "watch"}
    Snapshot.objects.filter(pk=snap.pk).update(frozen_summary=summary)
    assert "On the watch list" not in login(client, "CTO1").get(f"/exec/?snapshot={snap.pk}").content.decode()


def test_fake_data_has_watch_item(db):
    from scripts.generate_fake_data import seed

    seed(stdout=lambda *a: None)
    seed(stdout=lambda *a: None)
    it = Initiative.objects.watch_items().get()
    assert it.name == "Expense management platform renewal" and it.sox_applicable
    assert (it.decision_due - TODAY).days == 60 and (it.key_date - TODAY).days == 150
    assert (it.next_check - TODAY).days == 14 and it.derived_oversight == "enhanced"
