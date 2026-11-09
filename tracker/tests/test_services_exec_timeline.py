import re

import pytest

from tracker.models import Overlay, Visibility
from tracker.services import curation, exec_summary, rag, snapshots, timeline
from tracker.services.exec_summary import Tile
from tracker.tests.services_helpers import (
    TR,
    active_initiative,
    add_control,
    add_impact,
    add_milestone,
    add_phase,
    add_risk,
    at,
    bundle_of,
    cto_actor,
    d,
    frozen_clock,  # noqa: F401
    tech_risk_actor,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_clock")]


def names(tiles):
    return [t.name for t in tiles]


def make_tile(effective, prior):
    return Tile(
        bundle=None, entity_id=None, name="X", owner_emp_id="O", status="active", phase="", effective=effective,
        derived=effective, rule="", stale_days=None, is_stale=False, override=None, highlights=[],
        next_milestone=None, go_live=None, last_update_at=None, controls_ready=0, controls_total=0, prior=prior,
    )


def test_internal_only_high_risk_counts_for_tech_risk_not_cto():
    init = active_initiative()
    add_risk(init, rating="H", visibility="internal")
    add_risk(init, rating="H", visibility="exec")
    tr = exec_summary.build(tech_risk_actor())
    cto = exec_summary.build(cto_actor())
    assert tr.n_high == 2
    assert cto.n_high == 1
    assert tr.high_by_initiative[0][1] == 2
    assert cto.high_by_initiative[0][1] == 1


def test_internal_only_risk_hides_flag_entirely_from_cto():
    init = active_initiative()
    add_risk(init, rating="H", visibility="internal")
    cto = exec_summary.build(cto_actor())
    assert cto.n_high == 0
    assert cto.high_by_initiative == []
    assert exec_summary.build(tech_risk_actor()).n_high == 1


def test_tile_highlights_respect_visibility_layers():
    init = active_initiative()
    curation.add_overlay(init.entity_id, TR, Overlay.Type.HIGHLIGHT, "Exec visible", visibility=Visibility.EXEC)
    curation.add_overlay(init.entity_id, TR, Overlay.Type.HIGHLIGHT, "Internal", visibility=Visibility.INTERNAL)
    tr_tile = exec_summary.build(tech_risk_actor()).on_track[0]
    cto_tile = exec_summary.build(cto_actor()).on_track[0]
    assert tr_tile.highlights == ["Exec visible", "Internal"]
    assert cto_tile.highlights == ["Exec visible"]


def test_attention_sorted_red_first_and_on_track_holds_greens():
    amber = active_initiative(name="Amber one")
    add_risk(amber, rating="H", target_date=d(10))
    red = active_initiative(name="Red one")
    add_risk(red, rating="H", target_date=d(-1))
    active_initiative(name="Green one")
    amber2 = active_initiative(name="Amber two")
    add_risk(amber2, rating="H", target_date=d(10))
    ctx = exec_summary.build(tech_risk_actor())
    assert names(ctx.attention)[0] == "Red one"
    assert sorted(names(ctx.attention)[1:]) == ["Amber one", "Amber two"]
    assert [t.effective for t in ctx.attention] == ["red", "amber", "amber"]
    assert names(ctx.on_track) == ["Green one"]
    assert ctx.counts == {"active": 4, "green": 1, "amber": 2, "red": 1}


def test_header_collections_upcoming_and_stale():
    active_initiative(name="Soon", target_go_live=d(90))
    active_initiative(name="Later", target_go_live=d(91))
    active_initiative(name="Old", accepted_at=at(-40))
    ctx = exec_summary.build(cto_actor())
    assert names(ctx.upcoming) == ["Soon"]
    assert names(ctx.stale) == ["Old"]


def test_counts_only_include_active_initiatives():
    active_initiative(name="Live")
    active_initiative(name="Done", status="closed", closed_at=at(-1))
    active_initiative(name="Prop", status="proposed")
    ctx = exec_summary.build(tech_risk_actor())
    assert ctx.counts["active"] == 1


def test_archived_hidden_from_exec_summary():
    active_initiative(name="Gone", status="archived")
    ctx = exec_summary.build(tech_risk_actor())
    assert ctx.counts["active"] == 0
    assert ctx.on_track == [] and ctx.closed_since == []


def test_proposed_shown_to_tech_risk_hidden_from_senior_management():
    active_initiative(name="Pending", status="proposed")
    tr = exec_summary.build(tech_risk_actor())
    cto = exec_summary.build(cto_actor())
    assert names(tr.proposed) == ["Pending"]
    assert cto.proposed == []
    assert "Pending" not in names(cto.on_track) + names(cto.attention)


def test_closed_since_respects_comparison_snapshot_date(frozen_clock):
    active_initiative(name="Closed early", status="closed", closed_at=at(-10))
    actor = tech_risk_actor()
    assert names(exec_summary.build(actor).closed_since) == ["Closed early"]

    snapshots.take_snapshot(TR, "One")
    frozen_clock.advance(minutes=1)
    active_initiative(name="Closed mid", status="closed", closed_at=frozen_clock.now())
    frozen_clock.advance(minutes=1)
    snap2 = snapshots.take_snapshot(TR, "Two")
    frozen_clock.advance(minutes=1)
    active_initiative(name="Closed late", status="closed", closed_at=frozen_clock.now())

    assert names(exec_summary.build(actor).closed_since) == ["Closed late"]
    assert names(exec_summary.build(actor, snap2).closed_since) == ["Closed mid"]


def test_closed_since_carries_closure_outcome():
    active_initiative(
        name="Done", status="closed", closed_at=at(-1), closure_control_outcome="All controls ready"
    )
    [tile] = exec_summary.build(cto_actor()).closed_since
    assert tile.closure == "All controls ready"


@pytest.mark.parametrize(
    "effective,prior,expected",
    [
        ("amber", None, ("new", "New since the last snapshot")),
        ("amber", "amber", ("same", "— Unchanged (Amber)")),
        ("red", "amber", ("up", "▲ Increased to Red from Amber")),
        ("green", "amber", ("down", "▼ Decreased to Green from Amber")),
        ("amber", "green", ("up", "▲ Increased to Amber from Green")),
        ("amber", "red", ("down", "▼ Decreased to Amber from Red")),
    ],
)
def test_tile_comparison_kinds(effective, prior, expected):
    assert make_tile(effective, prior).comparison == expected


def test_live_tile_comparison_kinds_from_last_snapshot(frozen_clock):
    active_initiative(name="Same")
    up = active_initiative(name="Up")
    down = active_initiative(name="Down")
    down_risk = add_risk(down, rating="H", target_date=d(10))
    snapshots.take_snapshot(TR, "One")
    frozen_clock.advance(minutes=1)
    add_risk(up, rating="H", target_date=d(10))
    down_risk.new_version(TR, status="closed")
    new = active_initiative(name="New")
    add_risk(new, rating="H", target_date=d(10))
    ctx = exec_summary.build(tech_risk_actor())
    by_name = {t.name: t for t in ctx.attention + ctx.on_track}
    assert by_name["Same"].comparison == ("same", "— Unchanged (Green)")
    assert by_name["Up"].comparison == ("up", "▲ Increased to Amber from Green")
    assert by_name["Down"].comparison == ("down", "▼ Decreased to Green from Amber")
    assert by_name["New"].comparison == ("new", "New since the last snapshot")
    assert names(ctx.worse) == ["Up"] and names(ctx.better) == ["Down"]


def test_movers_prev_counts_and_delta(frozen_clock):
    active_initiative(name="Same")
    up = active_initiative(name="Up")
    down = active_initiative(name="Down")
    down_risk = add_risk(down, rating="H", target_date=d(10))
    snapshots.take_snapshot(TR, "Oct review")
    frozen_clock.advance(minutes=1)
    add_risk(up, rating="H", target_date=d(10))
    down_risk.new_version(TR, status="closed")
    new = active_initiative(name="New")
    add_risk(new, rating="H", target_date=d(10))
    ctx = exec_summary.build(tech_risk_actor())
    assert ctx.counts["amber"] == 2 and ctx.counts["green"] == 2
    assert ctx.prev_counts == {"green": 2, "amber": 2, "red": 0}  # New is counted in its current color
    assert (ctx.delta("amber"), ctx.delta("green"), ctx.delta("red")) == (0, 0, 0)
    assert names(ctx.movers["amber"]) == ["Up"] and names(ctx.movers["green"]) == ["Down"]
    assert ctx.movers["red"] == []


def test_delta_is_none_without_comparison_snapshot():
    active_initiative(name="Only")
    ctx = exec_summary.build(tech_risk_actor())
    assert ctx.delta("green") is None
    assert ctx.movers == {"green": [], "amber": [], "red": []}


def test_tile_chip_is_plain_words_and_upcoming_has_controls():
    init = active_initiative(name="Soon", target_go_live=d(30))
    add_risk(init, rating="H", target_date=d(10))
    add_impact(init, readiness="ready", control=add_control("CTL-1"))
    add_impact(init, readiness="not_started", control=add_control("CTL-2"))
    tile = exec_summary.build(tech_risk_actor()).upcoming[0]
    assert tile.chip and tile.chip == tile.chip.strip() and "." not in tile.chip
    assert [r for _label, r in sorted(tile.controls)] == ["ready", "not_started"]
    assert sorted(tile.controls)[0][0].startswith("CTL-1 ") and sorted(tile.controls)[0][0].endswith("· Ready")
    assert (tile.controls_ready, tile.controls_total, tile.days_to_go_live) == (1, 2, 30)


def test_portfolio_svg_marks_green_active_lanes_on_track():
    active_initiative(name="Fine")
    flagged = active_initiative(name="Flagged")
    add_risk(flagged, rating="H", target_date=d(10))
    ctx = exec_summary.build(tech_risk_actor())
    svg = timeline.portfolio_svg(ctx.timeline_bundles, ctx.evaluations)
    assert svg.count("✓ On track") == 1


def test_tile_days_helpers():
    tile = make_tile("green", None)
    tile.go_live = d(12)
    tile.last_update_at = at(-3)
    tile.next_milestone = ("M", d(7))
    assert (tile.days_to_go_live, tile.days_since_update, tile.next_milestone_days) == (12, 3, 7)


def timeline_initiative(**kw):
    init = active_initiative(**kw)
    add_phase(init, "Design", stage=1, order=0, planned_start=d(-60), planned_end=d(-30),
              forecast_start=d(-60), forecast_end=d(-20))
    add_phase(init, "Build", stage=2, order=1, planned_start=d(-29), planned_end=d(30),
              forecast_start=d(-19), forecast_end=d(60))
    return init


def test_initiative_svg_contents():
    init = timeline_initiative()
    add_milestone(init, planned=d(20), forecast=d(50), name="Build exit", status="slipped")
    svg = timeline.initiative_svg(bundle_of(init))
    assert "<svg" in svg and svg.count("<svg") == 1
    for needle in ("Design", "Build", "Planned", "Key milestones", "(was ", "Build exit", "today"):
        assert needle in svg
    assert "(+" in svg
    assert 'class="legend"' in svg


def test_initiative_svg_slipped_milestone_shows_original_date():
    init = timeline_initiative()
    add_milestone(init, planned=d(20), forecast=d(50), name="Build exit", status="slipped")
    svg = timeline.initiative_svg(bundle_of(init))
    assert f"(was {timeline._short(d(20))})" in svg


def test_initiative_svg_on_track_milestone_has_no_was_label():
    init = timeline_initiative()
    add_milestone(init, planned=d(20), forecast=d(20), name="On time")
    svg = timeline.initiative_svg(bundle_of(init))
    assert "Key milestones" in svg
    assert "(was " not in svg


def test_initiative_svg_done_milestone_has_no_was_label():
    init = timeline_initiative()
    add_milestone(init, planned=d(-30), forecast=d(-20), actual=d(-20), name="Done", status="done")
    assert "(was " not in timeline.initiative_svg(bundle_of(init))


def test_initiative_svg_without_milestones_omits_key_lane():
    init = timeline_initiative()
    svg = timeline.initiative_svg(bundle_of(init))
    assert "Key milestones" not in svg
    assert "Planned" in svg


def test_initiative_svg_escapes_names():
    init = active_initiative()
    add_phase(init, "Build <u>", stage=2, planned_start=d(-5), planned_end=d(20),
              forecast_start=d(-5), forecast_end=d(20))
    add_milestone(init, planned=d(5), forecast=d(9), name="M <i>")
    svg = timeline.initiative_svg(bundle_of(init))
    assert "Build &lt;u&gt;" in svg
    assert "M &lt;i&gt;" in svg
    assert "<u>" not in svg and "<i>" not in svg


def portfolio(*inits):
    bundles = [bundle_of(i) for i in inits]
    evals = {b.entity_id: rag.evaluate(b, persist=False) for b in bundles}
    return timeline.portfolio_svg(bundles, evals)


def test_portfolio_svg_proposed_name_is_grey_and_legend_present():
    prop = timeline_initiative(name="Pending thing", status="proposed")
    svg = portfolio(prop)
    assert "<svg" in svg
    assert re.search(r'fill="#6e7781">Pending thing \(proposed\)<', svg)
    assert 'class="legend"' in svg
    assert "planned (outline)" in svg and "forecast/actual (filled)" in svg


@pytest.mark.parametrize(
    "setup,color",
    [("green", "#1a7f37"), ("amber", "#bf8700"), ("red", "#cf222e")],
)
def test_portfolio_svg_active_name_colored_by_effective_rag(setup, color):
    init = timeline_initiative(name="Colored")
    if setup == "amber":
        add_risk(init, rating="H", target_date=d(10))
    if setup == "red":
        add_risk(init, rating="H", target_date=d(-1))
    assert f'fill="{color}">Colored<' in portfolio(init)


def test_portfolio_svg_uses_override_color():
    init = timeline_initiative(name="Overridden")
    add_risk(init, rating="H", target_date=d(10))
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    assert 'fill="#1a7f37">Overridden<' in portfolio(init)


def test_portfolio_svg_draws_key_milestones_and_phases():
    init = timeline_initiative(name="Lane")
    add_milestone(init, planned=d(10), forecast=d(20), name="Key one")
    add_milestone(init, planned=d(10), forecast=d(20), name="Minor one", is_key=False)
    svg = portfolio(init)
    assert "Key one" in svg
    assert "Minor one" not in svg
    assert "<polygon" in svg and "Design" in svg


def test_portfolio_svg_escapes_names():
    init = timeline_initiative(name="A <b> test")
    add_milestone(init, planned=d(10), forecast=d(12), name="K <i>")
    svg = portfolio(init)
    assert "A &lt;b&gt; test" in svg
    assert "A <b> test" not in svg
    assert "<b>" not in svg and "<i>" not in svg


def test_portfolio_svg_empty_still_valid():
    svg = timeline.portfolio_svg([], {})
    assert svg.startswith("<svg") and "</svg>" in svg and 'class="legend"' in svg


def test_exec_context_timeline_bundles_exclude_closed():
    active_initiative(name="Live")
    active_initiative(name="Done", status="closed", closed_at=at(-1))
    ctx = exec_summary.build(tech_risk_actor())
    assert [b.initiative.name for b in ctx.timeline_bundles] == ["Live"]
