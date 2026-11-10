from datetime import timedelta

import pytest
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from scripts.generate_fake_data import seed as generate_fake_data
from scripts.load_initiative import load

from core.auth.actor import resolve_actor
from core.models import Role
from tracker.models import Initiative, RiskItem, Visibility
from tracker.services import exec_summary, rag, snapshots, triage

pytestmark = pytest.mark.django_db

SOX_NAME, OTHER_NAME = "Sox Project", "Plain Project"


def go_live():
    return timezone.localdate() + timedelta(days=30)


@pytest.fixture
def pair(make_initiative, grant):
    """One SOX applicable and one other active initiative with an open High risk each; Tech Risk and auditor roles."""
    grant("TR1", Role.TECH_RISK)
    grant("AUD1", Role.AUDITOR)
    grant("BOTH1", Role.TECH_RISK)
    grant("BOTH1", Role.AUDITOR)
    grant("CTO1", Role.SENIOR_MANAGEMENT)
    out = []
    for name, sox in ((SOX_NAME, True), (OTHER_NAME, False)):
        init = make_initiative(name=name, owner="OWN1", target_go_live=go_live(), sox_applicable=sox)
        RiskItem.create("AUTH", initiative_entity=init.entity_id, title="Vendor slips", rating="H",
                        type=RiskItem.Type.RISK, visibility=Visibility.EXEC)
        rag.recompute(init)
        out.append(init)
    return out


def get(client, settings, emp, url="/exec/", **params):
    settings.DEV_DEFAULT_EMP_ID = emp
    return client.get(url, params)


# --- field and setting it ----------------------------------------------------------------------------

def test_model_default_is_false(make_initiative):
    assert make_initiative(name="X").sox_applicable is False


def test_intake_defaults_to_sox_app_answer(client):
    generate_fake_data(stdout=lambda *a: None)
    base = {"description": "d", "owner_emp_id": "E00999", "target_go_live": "2027-06-30", "why_it_matters": "w",
            "sponsor": "s", "business_driver": "b", "expected_systems": "e", "milestones": "", "risks": "",
            "delegates": ""}
    client.post("/auth/switch/", {"emp_id": "E00999"})
    for name, extra in (("Intake yes", {"affects_sox_app": "on"}), ("Intake no", {})):
        assert client.post(reverse("intake"), {**base, "name": name, **extra}).status_code == 302
    cur = Initiative.objects.current()
    assert cur.get(name="Intake yes").sox_applicable is True
    assert cur.get(name="Intake no").sox_applicable is False


def test_modal_tech_risk_changes_flag_versioned(client, pair):
    sox, _ = pair
    url = reverse("oversight_modal", args=[sox.entity_id])
    client.post("/auth/switch/", {"emp_id": "TR1"})
    body = client.get(url).content.decode()
    assert "SOX applicable" in body and "Show to auditors as SOX-relevant" in body
    assert client.post(url, {}).status_code == 204
    assert Initiative.objects.get_current(sox.entity_id).sox_applicable is False
    assert Initiative.objects.filter(entity_id=sox.entity_id).count() == 2
    assert client.post(url, {"sox_applicable": "on"}).status_code == 204
    assert Initiative.objects.get_current(sox.entity_id).sox_applicable is True


@pytest.mark.parametrize("emp", ["OWN1", "AUD1"])
def test_modal_forbidden_for_non_tech_risk(client, pair, emp):
    sox, _ = pair
    client.post("/auth/switch/", {"emp_id": emp})
    url = reverse("oversight_modal", args=[sox.entity_id])
    assert client.post(url, {}).status_code == 403
    assert Initiative.objects.get_current(sox.entity_id).sox_applicable is True


def loader_doc(**extra):
    return {"initiative": {"name": "Loader sox", "owner": {"emp_id": "E7"}, "target_go_live": "2027-03-01",
                           "oversight": {"affects_sox_app": True}, **extra}}


def test_loader_field_and_default():
    assert load(loader_doc(), out=lambda *a: None).sox_applicable is True
    Initiative.objects.all().delete()
    assert load(loader_doc(sox_applicable=False), out=lambda *a: None).sox_applicable is False


# --- display ----------------------------------------------------------------------------------------

def test_pill_on_tiles_and_initiative_page(client, settings, pair):
    sox, other = pair
    html = get(client, settings, "TR1", scope="all").content.decode()
    heads = [t.split("</h3>")[0] for t in html.split("<h3>")[1:]]
    assert any(SOX_NAME in h and "pill sox" in h for h in heads)
    assert not any(OTHER_NAME in h and "pill sox" in h for h in heads)
    assert 'pill sox' in get(client, settings, "TR1", reverse("initiative", args=[sox.entity_id])).content.decode()
    assert 'pill sox' not in get(client, settings, "TR1", reverse("initiative", args=[other.entity_id])).content.decode()


def test_frozen_row_has_flag(pair):
    snap = snapshots.take_snapshot("TR1", "S")
    rows = snapshots.rows_by_entity(snap)
    assert rows[pair[0].entity_id]["sox_applicable"] is True
    assert rows[pair[1].entity_id]["sox_applicable"] is False


# --- filter -----------------------------------------------------------------------------------------

def names_on_page(html):
    return SOX_NAME in html, OTHER_NAME in html


@pytest.mark.parametrize("emp,expected", [
    ("AUD1", (True, False)), ("TR1", (True, True)), ("BOTH1", (True, True)), ("CTO1", (True, True)),
])
def test_exec_scope_defaults_per_role(client, settings, pair, emp, expected):
    assert names_on_page(get(client, settings, emp).content.decode()) == expected


def test_toggle_overrides_default(client, settings, pair):
    assert names_on_page(get(client, settings, "AUD1", scope="all").content.decode()) == (True, True)
    assert names_on_page(get(client, settings, "TR1", scope="sox").content.decode()) == (True, False)
    html = get(client, settings, "AUD1").content.decode()
    assert 'class="on" aria-current' in html and "All initiatives" in html and "SOX applicable" in html


def test_counts_match_filtered_set(pair):
    tr = resolve_actor("TR1")
    full = exec_summary.build(tr, scope="all")
    sox = exec_summary.build(tr, scope="sox")
    assert full.counts["active"] == 2 and sox.counts["active"] == 1
    assert sum(sox.counts[r] for r in ("red", "amber", "green")) == 1
    assert [t.name for t in sox.upcoming] == [SOX_NAME] and len(full.upcoming) == 2
    assert sox.n_high == 1 and full.n_high == 2
    assert [b.initiative.name for b in sox.timeline_bundles] == [SOX_NAME]


def test_snapshot_filters_on_frozen_flag(client, settings, pair):
    snap = snapshots.take_snapshot("TR1", "S")
    # Flag changes after the snapshot; the frozen value wins.
    pair[0].new_version("TR1", sox_applicable=False)
    ctx = exec_summary.build(resolve_actor("TR1"), snap, scope="sox")
    assert [t.name for t in ctx.attention] == [SOX_NAME]
    assert ctx.counts["active"] == 1 and ctx.counts["golive90"] == 1 and ctx.counts["high_open"] == 1
    assert exec_summary.build(resolve_actor("TR1"), snap, scope="all").counts["active"] == 2


def test_old_snapshot_without_key_counts_as_not_sox(pair):
    snap = snapshots.take_snapshot("TR1", "Old")
    for r in snap.rows.all():
        r.frozen_json.pop("sox_applicable")
        type(r).objects.filter(pk=r.pk).update(frozen_json=r.frozen_json)
    ctx = exec_summary.build(resolve_actor("TR1"), snap, scope="sox")
    assert ctx.attention == [] and ctx.counts["active"] == 0


def test_snapshot_page_keeps_scope_in_links(client, settings, pair):
    snap = snapshots.take_snapshot("TR1", "S")
    html = get(client, settings, "AUD1", snapshot=snap.pk).content.decode()
    assert names_on_page(html) == (True, False)
    assert f"snapshot={snap.pk}&amp;scope=all" in html or f"scope=all&amp;snapshot={snap.pk}" in html


def test_shared_link_defaults_to_all_unless_scope(client, settings, pair):
    settings.DEV_DEFAULT_EMP_ID = ""
    url = reverse("exec_shared", args=[signing.dumps({"snapshot": None})])
    assert names_on_page(client.get(url).content.decode()) == (True, True)
    assert names_on_page(client.get(url, {"scope": "sox"}).content.decode()) == (True, False)


def test_triage_scope(client, settings, pair):
    for init in pair:
        init.new_version("TR1", status=Initiative.Status.PROPOSED)
    rows, _ = triage.triage(scope="all")
    assert len(rows["Decide"]) == 2
    rows, _ = triage.triage(scope="sox")
    assert [r.bundle.initiative.name for r in rows["Decide"]] == [SOX_NAME]
    assert names_on_page(get(client, settings, "TR1", reverse("workbench")).content.decode()) == (True, True)
    assert names_on_page(get(client, settings, "TR1", reverse("workbench"), scope="sox").content.decode()) == (True, False)


def test_audit_page_scope(client, settings, pair):
    html = get(client, settings, "AUD1", reverse("audit")).content.decode()
    assert 'name="scope" value="sox"' in html
    html = get(client, settings, "BOTH1", reverse("audit")).content.decode()
    assert 'name="scope" value="all"' in html
    html = get(client, settings, "BOTH1", reverse("audit"), scope="sox").content.decode()
    assert 'name="scope" value="sox"' in html
