from datetime import timedelta

import pytest
from django.core import signing
from scripts.generate_fake_data import seed as generate_fake_data
from django.urls import reverse
from django.utils import timezone

from core.models import Role
from tracker.models import Control, ControlImpact, RiskItem, Visibility
from tracker.services import curation, rag, snapshots

pytestmark = pytest.mark.django_db

NAME = "Exec Test Initiative"


@pytest.fixture
def amber_init(make_initiative):
    """An active initiative flagged Amber by an open High risk."""
    init = make_initiative(name=NAME, owner="OWN1", target_go_live=timezone.localdate() + timedelta(days=200))
    RiskItem.create("AUTH", initiative_entity=init.entity_id, title="Vendor slips", rating="H",
                    type=RiskItem.Type.RISK, visibility=Visibility.EXEC)
    rag.recompute(init)
    return init


def test_anonymous_forbidden(client, settings):
    settings.DEV_DEFAULT_EMP_ID = ""
    assert client.get("/exec/").status_code == 403
    assert client.get("/").status_code == 403


def test_owner_redirected_from_home(client, settings, make_initiative):
    make_initiative(name="Mine", owner="OWN1")
    settings.DEV_DEFAULT_EMP_ID = "OWN1"
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp["Location"] == reverse("my_initiative")
    assert client.get("/exec/").status_code == 403


def test_no_role_page(client, settings):
    settings.DEV_DEFAULT_EMP_ID = "NOROLE"
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No role is assigned" in resp.content


def test_tech_risk_sees_attention(client, settings, grant, amber_init):
    grant("TR1", Role.TECH_RISK)
    settings.DEV_DEFAULT_EMP_ID = "TR1"
    resp = client.get("/exec/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Needs attention" in html
    assert "(1 of 1 active)" in html
    assert 'class="tile amber"' in html
    assert NAME in html
    assert "Timeline" in html
    assert "Status summary" in html and "Next 90 days" in html
    assert "On track" not in html and "Flags" not in html
    assert "No go-lives in the next 90 days" in html
    assert "No change" not in html  # no comparison snapshot yet
    assert "since" not in html.split("Needs attention")[0].split("Status summary")[1]


def test_pulse_check_deltas_and_mover_pill(client, settings, grant, make_initiative):
    grant("TR1", Role.TECH_RISK)
    settings.DEV_DEFAULT_EMP_ID = "TR1"
    steady = make_initiative(name="Steady One", owner="OWN1")
    mover = make_initiative(name="Mover One", owner="OWN2")
    snapshots.take_snapshot("TR1", "Oct review")
    RiskItem.create("AUTH", initiative_entity=mover.entity_id, title="Slip", rating="H",
                    type=RiskItem.Type.RISK, visibility=Visibility.EXEC)
    rag.recompute(mover)
    html = client.get("/exec/").content.decode()
    pulse = html.split("Status summary")[1].split("Next 90 days")[0]
    blocks = pulse.split('class="stat ')
    amber = next(b for b in blocks if b.startswith("amber"))
    green = next(b for b in blocks if b.startswith("green"))
    assert "▲ 1 since Oct" in amber and "▼ 1 since Oct" in green
    assert "▲ Mover One" in amber and "Mover One" not in green
    assert 'title="Green → Amber since Oct"' in amber
    assert 'title="Red"' in pulse and "Steady One" not in pulse
    assert "closed since Oct" in pulse


def test_next_90_days_rows_with_control_squares(client, settings, grant, make_initiative):
    grant("TR1", Role.TECH_RISK)
    settings.DEV_DEFAULT_EMP_ID = "TR1"
    init = make_initiative(name="Soon One", owner="OWN1", target_go_live=timezone.localdate() + timedelta(days=30))
    for cid, readiness in (("CTL-A", "ready"), ("CTL-B", "submitted"), ("CTL-C", "not_started")):
        ControlImpact.create("AUTH", initiative_entity=init.entity_id,
                             control_entity=Control.create("AUTH", control_id=cid, name=cid).entity_id,
                             impact_type="modified", readiness=readiness)
    html = client.get("/exec/").content.decode()
    box = html.split("Next 90 days")[1].split("Needs attention")[0]
    assert "<b>30</b><span>days</span>" in box and "Soon One" in box
    assert 'class="sq green" title="CTL-A CTL-A · Ready"' in box
    assert 'class="sq amber" title="CTL-B CTL-B · Submitted for review"' in box
    assert 'class="sq red" title="CTL-C CTL-C · Not started"' in box
    assert "1 of 3 controls ready" in box
    snap = snapshots.take_snapshot("TR1", "Snap")
    box = client.get("/exec/", {"snapshot": snap.pk}).content.decode().split("Next 90 days")[1]
    assert "Soon One" in box and 'class="sq' not in box


def test_override_pill_visibility(client, settings, grant, amber_init):
    grant("TR1", Role.TECH_RISK)
    grant("AUD1", Role.AUDITOR)
    grant("CTO1", Role.SENIOR_MANAGEMENT)
    curation.set_override(amber_init, "TR1", "red", "Reason text", "Support text",
                          timezone.localdate() + timedelta(days=14))
    rag.recompute(amber_init)
    settings.DEV_DEFAULT_EMP_ID = "AUD1"
    html = client.get("/exec/", {"scope": "all"}).content.decode()
    assert "Overridden: Amber → Red" in html
    assert "hx-get" in html
    settings.DEV_DEFAULT_EMP_ID = "CTO1"
    html = client.get("/exec/").content.decode()
    assert NAME in html
    assert "Overridden" not in html


def test_snapshot_mode_banner(client, settings, grant, amber_init):
    grant("TR1", Role.TECH_RISK)
    snap = snapshots.take_snapshot("TR1", "Q4 review")
    settings.DEV_DEFAULT_EMP_ID = "TR1"
    resp = client.get("/exec/", {"snapshot": snap.pk})
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "banner snap" in html
    assert "Q4 review" in html
    assert "Back to live" in html
    assert "Live view only" in html
    assert client.get("/exec/", {"snapshot": 99999}).status_code == 404


def test_as_of_uses_latest_snapshot_on_or_before(client, settings, grant, amber_init):
    from django.utils import timezone
    grant("TR1", Role.TECH_RISK)
    settings.DEV_DEFAULT_EMP_ID = "TR1"
    snap = snapshots.take_snapshot("TR1", "Sep review")
    type(snap).objects.filter(pk=snap.pk).update(taken_at=timezone.make_aware(timezone.datetime(2026, 9, 28, 12)))
    html = client.get("/exec/", {"as_of": "2026-09-30"}).content.decode()
    assert "As of" in html and "Sep review" in html
    html = client.get("/exec/", {"as_of": "2026-09-01"}).content.decode()
    assert "No snapshot on or before Sep 1, 2026" in html and "snapshot &quot;Sep review" not in html and "<b>snapshot" not in html


def test_shared_token(client, settings, grant, amber_init):
    settings.DEV_DEFAULT_EMP_ID = ""
    token = signing.dumps({"snapshot": None})
    resp = client.get(reverse("exec_shared", args=[token]))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Read-only share link" in html
    assert NAME in html
    assert "Workbench" not in html
    assert client.get(reverse("exec_shared", args=["bad-token"])).status_code == 404


def test_seeded_exec_renders(client, settings):
    generate_fake_data(stdout=lambda *a: None)
    settings.DEV_DEFAULT_EMP_ID = "E00001"
    resp = client.get("/exec/")
    assert resp.status_code == 200
