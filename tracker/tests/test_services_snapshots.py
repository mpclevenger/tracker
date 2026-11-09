import pytest

from tracker.models import Initiative, Overlay, Snapshot, SnapshotRow, ThresholdConfig, Visibility
from tracker.services import curation, exec_summary, rag, snapshots
from tracker.tests.services_helpers import (
    TR,
    active_initiative,
    add_impact,
    add_milestone,
    add_risk,
    at,
    d,
    frozen_clock,  # noqa: F401
    post_update,
    tech_risk_actor,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_clock")]


def rows(snap):
    return snapshots.rows_by_entity(snap)


def highlight(init, text, visibility):
    return curation.add_overlay(init.entity_id, TR, Overlay.Type.HIGHLIGHT, text, visibility=visibility)


def test_take_snapshot_freezes_active_and_closed_not_proposed():
    active = active_initiative(name="Active")
    closed = active_initiative(name="Closed", status="closed", closed_at=at(-3))
    proposed = active_initiative(name="Proposed", status="proposed")
    snap = snapshots.take_snapshot(TR, "Nov", "note")
    frozen = rows(snap)
    assert set(frozen) == {active.entity_id, closed.entity_id}
    assert proposed.entity_id not in frozen
    assert frozen[active.entity_id]["name"] == "Active"
    assert frozen[closed.entity_id]["status"] == "closed"
    assert (snap.label, snap.note, snap.taken_by) == ("Nov", "note", TR)


def test_take_snapshot_includes_archived():
    archived = active_initiative(status="archived")
    snap = snapshots.take_snapshot(TR, "Nov")
    assert archived.entity_id in rows(snap)


def test_take_snapshot_with_nothing_to_freeze():
    snap = snapshots.take_snapshot(TR, "Empty")
    assert snap.rows.count() == 0
    assert snap.frozen_summary["active"] == 0


def test_frozen_json_excludes_internal_and_owner_highlights():
    init = active_initiative()
    highlight(init, "Visible to exec", Visibility.EXEC)
    highlight(init, "Internal only", Visibility.INTERNAL)
    highlight(init, "Owner only", Visibility.OWNER)
    curation.add_overlay(init.entity_id, TR, Overlay.Type.COMMENTARY, "Exec commentary", visibility=Visibility.EXEC)
    curation.add_overlay(init.entity_id, TR, Overlay.Type.COMMENTARY, "Owner commentary", visibility=Visibility.OWNER)
    curation.add_overlay(init.entity_id, TR, Overlay.Type.INTERNAL_NOTE, "Secret note")
    frozen = rows(snapshots.take_snapshot(TR, "Nov"))[init.entity_id]
    assert frozen["highlights"] == ["Visible to exec"]
    assert frozen["commentary"] == ["Exec commentary"]
    assert "Secret note" not in str(frozen)
    assert "Internal only" not in str(frozen)


def test_frozen_highlights_capped_at_three_in_order():
    init = active_initiative()
    for n in range(4):
        highlight(init, f"H{n}", Visibility.EXEC)
    frozen = rows(snapshots.take_snapshot(TR, "Nov"))[init.entity_id]
    assert frozen["highlights"] == ["H0", "H1", "H2"]


def test_frozen_json_counts_only_exec_visible_open_high_risks():
    init = active_initiative()
    add_risk(init, rating="H", visibility="exec")
    add_risk(init, rating="H", visibility="internal")
    add_risk(init, rating="H", visibility="owner")
    add_risk(init, rating="H", visibility="exec", status="closed")
    add_risk(init, rating="M", visibility="exec")
    frozen = rows(snapshots.take_snapshot(TR, "Nov"))[init.entity_id]
    assert frozen["open_high_risks"] == 1


def test_frozen_json_carries_rag_and_controls():
    init = active_initiative()
    late = add_risk(init, rating="H", target_date=d(-1))
    add_impact(init, readiness="ready")
    add_impact(init, readiness="in_progress")
    add_milestone(init, planned=d(5), forecast=d(5), name="Next up")
    post_update(init, days_ago=2)
    frozen = rows(snapshots.take_snapshot(TR, "Nov"))[init.entity_id]
    assert (frozen["derived"], frozen["effective"]) == ("red", "red")
    assert frozen["chip"] == "High risk past due"
    assert frozen["rule"] == late.title
    assert (frozen["controls_ready"], frozen["controls_total"]) == (1, 2)
    assert frozen["next_milestone"] == {"name": "Next up", "date": d(5).isoformat()}
    assert frozen["stale_days"] == 2
    assert frozen["override"] is None


def test_frozen_json_carries_override_pair():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(5))
    curation.set_override(init, TR, "green", "Mitigated", "TICKET", d(7))
    frozen = rows(snapshots.take_snapshot(TR, "Nov"))[init.entity_id]
    assert (frozen["derived"], frozen["effective"]) == ("amber", "green")
    assert frozen["override"]["value"] == "green"
    assert frozen["override"]["reason"] == "Mitigated"
    assert frozen["override"]["review_by"] == d(7).isoformat()


def test_frozen_summary_has_counts_and_thresholds():
    active_initiative(name="G")
    amber = active_initiative(name="A")
    add_risk(amber, rating="H", target_date=d(5))
    red = active_initiative(name="R")
    add_risk(red, rating="H", target_date=d(-1))
    active_initiative(name="Closed", status="closed")
    summary = snapshots.take_snapshot(TR, "Nov").frozen_summary
    assert (summary["active"], summary["green"], summary["amber"], summary["red"]) == (3, 1, 1, 1)
    assert summary["high_open"] == 2
    assert summary["thresholds"] == rag.thresholds()
    assert summary["golive90"] == 0 and summary["stale"] == 0


@pytest.mark.parametrize("days,counted", [(90, 1), (91, 0), (0, 1), (-1, 0)])
def test_frozen_summary_go_live_90d_boundary(days, counted):
    active_initiative(target_go_live=d(days))
    assert snapshots.take_snapshot(TR, "Nov").frozen_summary["golive90"] == counted


@pytest.mark.parametrize("stale,counted", [(30, 0), (31, 1)])
def test_frozen_summary_stale_count(stale, counted):
    init = active_initiative(accepted_at=at(-300))
    post_update(init, days_ago=stale)
    assert snapshots.take_snapshot(TR, "Nov").frozen_summary["stale"] == counted


def test_frozen_summary_records_changed_thresholds():
    ThresholdConfig.create(TR, key="amber_stale_days", value=12)
    summary = snapshots.take_snapshot(TR, "Nov").frozen_summary
    assert summary["thresholds"]["amber_stale_days"] == 12


def test_snapshot_summary_excludes_closed_from_counts():
    active_initiative(status="closed", closed_at=at(-1))
    add = active_initiative(name="Live")
    add_risk(add, rating="H", target_date=d(5))
    summary = snapshots.take_snapshot(TR, "Nov").frozen_summary
    assert summary["active"] == 1
    assert summary["high_open"] == 1


def test_snapshot_and_rows_are_immutable():
    init = active_initiative()
    snap = snapshots.take_snapshot(TR, "Nov")
    row = snap.rows.get()
    snap.label = "Edited"
    with pytest.raises(ValueError):
        snap.save()
    with pytest.raises(ValueError):
        snap.delete()
    row.frozen_json = {}
    with pytest.raises(ValueError):
        row.save()
    with pytest.raises(ValueError):
        row.delete()
    snap.refresh_from_db()
    assert snap.label == "Nov"
    assert SnapshotRow.objects.get(pk=row.pk).frozen_json["name"] == init.name


def test_later_changes_do_not_alter_frozen_row():
    init = active_initiative(name="Before")
    snap = snapshots.take_snapshot(TR, "Nov")
    before = dict(rows(snap)[init.entity_id])
    current = Initiative.objects.get_current(init.entity_id)
    current.new_version(TR, name="After", phase="Test")
    add_risk(init, rating="H", target_date=d(-5))
    highlight(init, "New highlight", Visibility.EXEC)
    post_update(init, days_ago=0)
    after = rows(Snapshot.objects.get(pk=snap.pk))[init.entity_id]
    assert after == before
    assert after["name"] == "Before"
    assert after["effective"] == "green"
    assert after["highlights"] == []


def test_last_snapshot(frozen_clock):
    assert snapshots.last_snapshot() is None
    first = snapshots.take_snapshot(TR, "One")
    frozen_clock.advance(days=7)
    second = snapshots.take_snapshot(TR, "Two")
    assert snapshots.last_snapshot() == second
    assert snapshots.last_snapshot(before=second.taken_at) == first
    assert snapshots.last_snapshot(before=first.taken_at) is None
    frozen_clock.advance(days=1)
    assert snapshots.last_snapshot(before=frozen_clock.now()) == second


def test_exec_summary_snapshot_view_reads_frozen_rag(frozen_clock):
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(5))
    actor = tech_risk_actor()
    snap = snapshots.take_snapshot(TR, "Nov")
    frozen_clock.advance(hours=1)
    add_risk(init, rating="H", target_date=d(-1))
    live = exec_summary.build(actor)
    frozen = exec_summary.build(actor, snap)
    assert live.mode == "live" and frozen.mode == "snapshot"
    assert live.attention[0].effective == "red"
    assert frozen.attention[0].effective == "amber"
    assert frozen.snapshot == snap
    assert frozen.counts["amber"] == 1 and frozen.counts["red"] == 0
    assert live.counts["red"] == 1 and live.counts["amber"] == 0


def test_exec_summary_snapshot_view_ignores_later_name_and_highlights(frozen_clock):
    init = active_initiative(name="Before")
    highlight(init, "Frozen highlight", Visibility.EXEC)
    actor = tech_risk_actor()
    snap = snapshots.take_snapshot(TR, "Nov")
    Initiative.objects.get_current(init.entity_id).new_version(TR, name="After")
    tile = exec_summary.build(actor, snap).on_track[0]
    assert tile.name == "Before"
    assert tile.highlights == ["Frozen highlight"]
    assert tile.bundle is None


def test_exec_summary_snapshot_comparison_uses_previous_snapshot(frozen_clock):
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(5))
    actor = tech_risk_actor()
    first = snapshots.take_snapshot(TR, "First")
    frozen_clock.advance(days=7)
    add_risk(init, rating="H", target_date=d(-1))
    second = snapshots.take_snapshot(TR, "Second")
    frozen_clock.advance(days=7)
    add_risk(init, rating="H", target_date=d(-2))

    ctx_first = exec_summary.build(actor, first)
    ctx_second = exec_summary.build(actor, second)

    assert ctx_first.comparison_snapshot is None
    assert ctx_first.attention[0].comparison == ("new", "New since the last snapshot")
    assert ctx_second.comparison_snapshot == first
    assert ctx_second.comparison_label == "First"
    assert ctx_second.attention[0].comparison == ("up", "▲ Increased to Red from Amber")
    assert [t.name for t in ctx_second.worse] == ["Alpha"]


def test_exec_summary_live_comparison_uses_latest_snapshot(frozen_clock):
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(5))
    actor = tech_risk_actor()
    snapshots.take_snapshot(TR, "Latest")
    frozen_clock.advance(hours=1)
    add_risk(init, rating="H", target_date=d(-1))
    ctx = exec_summary.build(actor)
    assert ctx.comparison_label == "Latest"
    assert ctx.attention[0].comparison[0] == "up"


def test_frozen_json_carries_oversight_stages_and_controls():
    from tracker.services import lifecycle
    lifecycle.seed_guidance(TR)
    init = active_initiative()
    add_impact(init, readiness="in_progress")
    snap = snapshots.take_snapshot(TR, "Nov")
    frozen = rows(snap)[init.entity_id]
    assert frozen["oversight"] == init.oversight and frozen["oversight_overridden"] is False
    assert [s["number"] for s in frozen["stages"]] == list(range(7))
    assert {"requirement", "done", "total", "signoff"} <= set(frozen["stages"][1])
    assert frozen["controls"][0]["readiness"] == "in_progress"
    assert snap.frozen_summary["gate_matrix_version"] and snap.frozen_summary["guidance_version"] == "v1.0"
