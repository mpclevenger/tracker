import re

import pytest
from django.urls import reverse

from scripts.generate_fake_data import seed as generate_fake_data
from scripts.load_initiative import load
from tracker.models import ControlImpact, Initiative, Milestone
from tracker.services import snapshots, timeline
from tracker.tests.services_helpers import (
    TR,
    active_initiative,
    add_control,
    add_impact,
    add_milestone,
    add_phase,
    bundle_of,
    d,
    frozen_clock,  # noqa: F401
)
from tracker.tests.test_owner_intake_views import OWNER, formset_post, switch

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(client):
    generate_fake_data(stdout=lambda *a: None)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


def test_model_default_is_null():
    init = active_initiative()
    assert add_impact(init).needed_by_milestone is None


@pytest.mark.usefixtures("frozen_clock")
class TestDue:
    def test_blank_falls_back_to_go_live(self):
        init = active_initiative(target_go_live=d(40))
        imp = add_impact(init)
        assert bundle_of(init).control_due(imp) == ("Go-live", d(40))

    def test_milestone_date_precedence(self):
        init = active_initiative(target_go_live=d(40))
        m = add_milestone(init, planned=d(10), forecast=d(12), name="Test exit")
        imp = add_impact(init, needed_by=m)
        assert bundle_of(init).control_due(imp) == ("Test exit", d(12))
        m2 = m.new_version(TR, actual=d(11))
        assert m2.actual == d(11)
        assert bundle_of(init).control_due(imp) == ("Test exit", d(11))

    def test_no_longer_key_or_deleted_falls_back(self):
        init = active_initiative(target_go_live=d(40))
        m = add_milestone(init, planned=d(10), name="Test exit")
        imp = add_impact(init, needed_by=m)
        m = m.new_version(TR, is_key=False)
        assert bundle_of(init).control_due(imp) == ("Go-live", d(40))
        m.new_version(TR, is_key=True)
        assert bundle_of(init).control_due(imp)[0] == "Test exit"
        Milestone.objects.get_current(m.entity_id).retire(TR)
        assert bundle_of(init).control_due(imp) == ("Go-live", d(40))


def post_controls(client, init, rows):
    return client.post(reverse("edit_controls", args=[init.entity_id]), formset_post("c", init, rows, extra_blank=0))


def control_rows(init):
    return [
        {"entity_id": i.entity_id, "control_entity": i.control_entity, "impact_type": i.impact_type,
         "readiness": i.readiness, "needed_by_milestone": i.needed_by_milestone or ""}
        for i in ControlImpact.objects.current().filter(initiative_entity=init.entity_id)
    ]


@pytest.mark.parametrize("who", [OWNER, "E00001"])
def test_editor_sets_needed_by(client, seeded, who):
    switch(client, who)
    page = client.get(reverse("edit_controls", args=[seeded.entity_id])).content.decode()
    assert "Needed by" in page and ">Go-live<" in page and "Certification dry run (UAT exit) · Jan 5, 2027" in page
    ms = Milestone.objects.current().get(initiative_entity=seeded.entity_id, name="Wave 1 connectors built")
    rows = control_rows(seeded)
    rows[0]["needed_by_milestone"] = ms.entity_id
    post_controls(client, seeded, rows)
    saved = ControlImpact.objects.current().get(entity_id=rows[0]["entity_id"])
    assert saved.needed_by_milestone == ms.entity_id
    rows[0]["needed_by_milestone"] = ""
    post_controls(client, seeded, rows)
    assert ControlImpact.objects.current().get(entity_id=rows[0]["entity_id"]).needed_by_milestone is None


def test_editor_rejects_non_key_and_foreign_milestones(client, seeded):
    switch(client, OWNER)
    plain = add_milestone(seeded, planned=d(5), is_key=False, name="Plain")
    other = active_initiative(name="Other")
    foreign = add_milestone(other, planned=d(5), name="Foreign")
    rows = control_rows(seeded)
    before = ControlImpact.objects.current().get(entity_id=rows[0]["entity_id"]).needed_by_milestone
    for bad in (plain, foreign):
        rows[0]["needed_by_milestone"] = bad.entity_id
        resp = post_controls(client, seeded, rows)
        assert resp.status_code == 200 and b"Select a valid choice" in resp.content
    assert ControlImpact.objects.current().get(entity_id=rows[0]["entity_id"]).needed_by_milestone == before


def test_risks_tab_shows_needed_by(client, seeded):
    switch(client, OWNER)
    page = client.get(reverse("initiative", args=[seeded.entity_id]) + "?tab=risks").content.decode()
    assert "Needed by: Certification dry run (UAT exit) · Jan 5, 2027" in page
    assert "Needed by: Go-live · Jan 19, 2027" in page


def test_fake_data_links_two_controls_before_go_live(seeded):
    linked = {
        bundle_of(seeded).control_for(i).control_id: i
        for i in ControlImpact.objects.current().filter(initiative_entity=seeded.entity_id, needed_by_milestone__isnull=False)
    }
    assert {k: v.readiness for k, v in linked.items()} == {"ITGC-CM-01": "ready", "ITGC-LA-04": "not_started"}


@pytest.mark.usefixtures("frozen_clock")
class TestTimeline:
    def svg(self, **kw):
        init = active_initiative(target_go_live=d(80))
        add_phase(init, "Build", stage=2, order=0, planned_start=d(-30), planned_end=d(60),
                  forecast_start=d(-30), forecast_end=d(60))
        m = add_milestone(init, planned=d(20), forecast=d(20), name="Test exit")
        add_impact(init, readiness="ready", control=add_control("C-1"), needed_by=m)
        add_impact(init, readiness="submitted", control=add_control("C-2"), needed_by=m)
        add_impact(init, readiness="not_started", control=add_control("C-3"))
        return init, timeline.initiative_svg(bundle_of(init), **kw)

    def test_lane_titles_colors_and_link(self):
        _init, svg = self.svg()
        assert "Controls" in svg and '<a href="?tab=risks">' in svg
        due = timeline._d(d(20))
        assert f"C-1 Control" in svg and f"· Ready · needed by Test exit, {due}</title>" in svg
        assert f"· Submitted for review · needed by Test exit, {due}</title>" in svg
        assert f"· Not started · needed by Go-live, {timeline._d(d(80))}</title>" in svg
        for color in ("#1a7f37", "#bf8700", "#cf222e"):
            assert f'fill="{color}" stroke="#fff"><title>C-' in svg
        assert "■ control, colored by readiness" in svg

    def test_shared_date_stacks_vertically(self):
        _init, svg = self.svg()
        squares = re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="11"', svg)
        assert len(squares) == 3 and len(set(squares)) == 3
        xs = [x for x, _y in squares]
        assert len(set(xs)) == 2  # C-1 and C-2 share a date and differ only in y

    def test_layers_hide_internal_controls(self):
        init, _ = self.svg()
        hidden = bundle_of(init).impacts[0]
        hidden.new_version(TR, visibility="internal")
        svg = timeline.initiative_svg(bundle_of(init), layers={"exec"})
        assert svg.count("<title>C-") == 2

    def test_clamps_to_window_and_omits_empty_lane(self):
        init = active_initiative(target_go_live=d(900))
        add_phase(init, "Build", stage=2, order=0, planned_start=d(-30), planned_end=d(60),
                  forecast_start=d(-30), forecast_end=d(60))
        assert "Controls" not in timeline.initiative_svg(bundle_of(init))
        add_impact(init, readiness="ready", control=add_control("C-9"))
        svg = timeline.initiative_svg(bundle_of(init))
        x = float(re.search(r'<rect x="([\d.]+)" y="[\d.]+" width="11"', svg).group(1))
        assert x + 5 <= 1100 - 20  # clamped to the right edge of the window


@pytest.mark.usefixtures("frozen_clock")
def test_snapshot_fields():
    init = active_initiative(target_go_live=d(40))
    m = add_milestone(init, planned=d(10), name="Test exit")
    add_impact(init, needed_by=m)
    add_impact(init)
    ctl = snapshots.rows_by_entity(snapshots.take_snapshot(TR, "S"))[init.entity_id]["controls"]
    assert [(c["needed_by"], c["due"]) for c in ctl] == [("Test exit", d(10).isoformat()), ("Go-live", d(40).isoformat())]


def doc(needed_by):
    return {
        "initiative": {"name": "Loader test", "owner": {"emp_id": "E7"}, "target_go_live": "2027-03-01"},
        "milestones": [
            {"name": "Key one", "planned": "2027-01-10", "key": True},
            {"name": "Minor", "planned": "2027-01-11"},
        ],
        "control_impacts": [
            {"control_id": "L-1", "name": "Loaded", "impact_type": "new", "needed_by": needed_by},
        ],
    }


def test_loader_needed_by():
    init = load(doc("Key one"), out=lambda *a: None)
    imp = ControlImpact.objects.current().get(initiative_entity=init.entity_id)
    ms = Milestone.objects.current().get(initiative_entity=init.entity_id, name="Key one")
    assert imp.needed_by_milestone == ms.entity_id


@pytest.mark.parametrize("name", ["Minor", "Nope"])
def test_loader_needed_by_must_be_key_milestone(name):
    with pytest.raises(SystemExit, match="needed_by"):
        load(doc(name), out=lambda *a: None)
