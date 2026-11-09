import pytest

from tracker.models import Overlay, Visibility
from tracker.services import curation, lifecycle, snapshots, triage
from tracker.tests.services_helpers import (
    TR,
    active_initiative,
    add_control,
    add_impact,
    add_phase,
    add_risk,
    at,
    bundle_of,
    complete_stage,
    d,
    frozen_clock,  # noqa: F401
    intake_stage_initiative,
    post_update,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_clock")]


ENHANCED = {"affects_tier01_app": True, "affects_sox_app": True}


def group(name, init=None):
    rows, _ = triage.triage()
    out = rows[name]
    if init is not None:
        out = [r for r in out if r.bundle.entity_id == init.entity_id]
    return out


def quiet_names():
    return [b.initiative.name for b in triage.triage()[1]]


def test_proposed_goes_to_decide():
    init = active_initiative(name="Intake me", status="proposed", submitted_at=at(-2))
    rows = group("Decide", init)
    assert len(rows) == 1
    row = rows[0]
    assert (row.what, row.action, row.severity, row.since) == ("Intake submitted", "decide", "grey", at(-2))
    assert group("Confirm", init) == [] and group("Chase", init) == []


def test_proposed_never_in_quiet_list():
    active_initiative(status="proposed")
    assert quiet_names() == []


def test_all_groups_present_when_empty():
    rows, quiet = triage.triage()
    assert rows == {"Decide": [], "Confirm": [], "Review": [], "Chase": []}
    assert quiet == []


@pytest.mark.parametrize("days,fires", [(60, True), (61, False)])
def test_go_live_window_with_not_ready_control_confirms(days, fires):
    init = intake_stage_initiative(target_go_live=d(days))
    add_impact(init, readiness="in_progress", control=add_control("ITGC-9"))
    rows = group("Confirm", init)
    assert bool(rows) is fires
    if fires:
        assert rows[0].severity == "red"
        assert rows[0].what == f"Go-live in {days}d; 1 control not ready (ITGC-9)"


def test_go_live_confirm_pluralises_and_ignores_ready_controls():
    init = intake_stage_initiative(target_go_live=d(10))
    add_impact(init, readiness="not_started", control=add_control("A-1"))
    add_impact(init, readiness="sent_back", control=add_control("B-2"))
    add_impact(init, readiness="ready")
    [row] = group("Confirm", init)
    assert row.what.startswith("Go-live in 10d; 2 controls not ready (")
    assert "A-1" in row.what and "B-2" in row.what


def test_go_live_window_with_all_ready_controls_is_quiet():
    init = intake_stage_initiative(target_go_live=d(10))
    add_impact(init, readiness="ready")
    assert group("Confirm", init) == []
    assert init.name in quiet_names()


def test_open_high_risk_confirms_amber():
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(5), title="Vendor slip")
    [row] = group("Confirm", init)
    assert row.severity == "amber"
    assert row.what == "High risk: Vendor slip"
    assert row.since == d(5)


def test_open_high_risk_past_target_is_red():
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(-1), title="Late", type="issue")
    [row] = group("Confirm", init)
    assert row.severity == "red"
    assert row.what == "High issue: Late — past target"


def test_high_risk_due_today_is_not_past_target():
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(0))
    assert group("Confirm", init)[0].severity == "amber"


def test_closed_high_and_medium_risks_do_not_confirm():
    init = intake_stage_initiative()
    add_risk(init, rating="H", status="closed")
    add_risk(init, rating="M")
    assert group("Confirm", init) == []


def past_stage_initiative(**fields):
    """Active initiative in stage 2 with stage 0 complete and stage 1 open. Light oversight needs only
    Tech Risk review at stage 1; pass `ENHANCED` for sign-off."""
    lifecycle.seed_guidance("E1")
    init = active_initiative(phase="Build", **fields)
    add_phase(init, "Design", stage=1, order=0)
    add_phase(init, "Build", stage=2, order=1)
    complete_stage(init, 0)
    return init


def test_incomplete_past_stage_confirms():
    init = past_stage_initiative(**ENHANCED)
    [row] = group("Confirm", init)
    assert row.severity == "amber"
    assert row.tab == "lifecycle"
    assert row.what == "Mobilize & Design: 0/5 items done, sign-off not recorded"


def test_incomplete_stage_reports_missing_signoff_only():
    init = past_stage_initiative(**ENHANCED)
    for idx in range(len(lifecycle.current_guidance()[1].checklist)):
        lifecycle.accept_item(bundle_of(init), TR, 1, idx, evidence_ref="ev")
    [row] = group("Confirm", init)
    assert row.what == "Mobilize & Design: sign-off not recorded"


def test_incomplete_stage_reports_missing_items_only():
    init = past_stage_initiative(**ENHANCED)
    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "ev")
    [row] = group("Confirm", init)
    assert row.what == "Mobilize & Design: 0/5 items done"


def test_held_gate_is_not_a_sign_off():
    init = past_stage_initiative(**ENHANCED)
    for idx in range(len(lifecycle.current_guidance()[1].checklist)):
        lifecycle.accept_item(bundle_of(init), TR, 1, idx, evidence_ref="ev")
    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "ev", decision="hold")
    [row] = group("Confirm", init)
    assert row.what == "Mobilize & Design: sign-off not recorded"


def test_light_stage_without_signoff_requirement_only_needs_items():
    init = past_stage_initiative()
    [row] = group("Confirm", init)
    assert row.what == "Mobilize & Design: 0/5 items done"
    for idx in range(len(lifecycle.current_guidance()[1].checklist)):
        lifecycle.accept_item(bundle_of(init), TR, 1, idx, evidence_ref="ev")
    assert group("Confirm", init) == []


def test_complete_past_stages_do_not_confirm():
    init = past_stage_initiative(**ENHANCED)
    complete_stage(init, 1)
    assert group("Confirm", init) == []


def test_submitted_checklist_item_reviews_with_tab():
    init = past_stage_initiative()
    lifecycle.submit_evidence(bundle_of(init), "OWN1", 2, 0, "DOC-1")
    [row] = [r for r in group("Review", init) if r.what.startswith("Evidence submitted")]
    assert (row.action, row.tab, row.severity) == ("review", "lifecycle", "grey")
    assert row.what == "Evidence submitted: " + lifecycle.current_guidance()[2].checklist[0][:80]


def test_accepted_and_sent_back_items_do_not_review():
    init = past_stage_initiative()
    lifecycle.submit_evidence(bundle_of(init), "OWN1", 2, 0, "DOC-1")
    lifecycle.send_back_item(bundle_of(init), TR, 2, 0, "Need the signed copy")
    lifecycle.accept_item(bundle_of(init), TR, 2, 1, evidence_ref="ev")
    assert [r for r in group("Review", init) if r.what.startswith("Evidence")] == []


def test_submitted_control_reviews_with_tab():
    init = intake_stage_initiative()
    add_impact(init, readiness="submitted", control=add_control("ITGC-7"))
    [row] = group("Review", init)
    assert (row.what, row.action, row.tab) == ("Evidence submitted: ITGC-7", "review", "risks")


def test_control_not_submitted_does_not_review():
    init = intake_stage_initiative()
    add_impact(init, readiness="in_progress")
    add_impact(init, readiness="sent_back")
    assert group("Review", init) == []


def test_rag_changed_since_last_snapshot_reviews(frozen_clock):
    init = intake_stage_initiative()
    snapshots.take_snapshot(TR, "October")
    frozen_clock.advance(hours=1)
    add_risk(init, rating="H", target_date=d(5))
    [row] = group("Review", init)
    assert row.what == "RAG changed since October: green → amber"
    assert row.severity == "amber"


def test_rag_improved_since_last_snapshot_is_green_review(frozen_clock):
    init = intake_stage_initiative()
    risk = add_risk(init, rating="H", target_date=d(5))
    snapshots.take_snapshot(TR, "October")
    frozen_clock.advance(hours=1)
    risk.new_version(TR, status="closed")
    [row] = group("Review", init)
    assert row.what == "RAG changed since October: amber → green"
    assert row.severity == "green"


def test_unchanged_rag_does_not_review(frozen_clock):
    init = intake_stage_initiative()
    snapshots.take_snapshot(TR, "October")
    assert group("Review", init) == []


def test_new_initiative_after_snapshot_does_not_review(frozen_clock):
    snapshots.take_snapshot(TR, "October")
    init = intake_stage_initiative()
    assert group("Review", init) == []


def override_rows(init):
    return [r for r in group("Review", init) if r.what.startswith("Override")]


def test_override_reviews_grey_when_review_by_in_future():
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(5))
    curation.set_override(init, TR, "green", "Mitigated", "", d(7))
    [row] = override_rows(init)
    assert row.severity == "grey"
    assert row.what.startswith("Override green in place (derived amber) — review by Nov 12, 2026")


@pytest.mark.parametrize("review_by,severity", [(d(0), "grey"), (d(-1), "red")])
def test_override_review_by_boundary(review_by, severity):
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(5))
    curation.set_override(init, TR, "green", "Mitigated", "", review_by)
    assert override_rows(init)[0].severity == severity


def test_override_without_review_by_has_no_date_text():
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(5))
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    [row] = override_rows(init)
    assert row.what == "Override green in place (derived amber)"
    assert row.severity == "grey"


@pytest.mark.parametrize("days,overdue,severity", [(14, None, None), (15, 1, "amber"), (30, 16, "amber"), (31, 17, "red")])
def test_check_in_chase_boundaries(days, overdue, severity):
    init = intake_stage_initiative(accepted_at=at(-300))
    post_update(init, days_ago=days)
    rows = group("Chase", init)
    if severity is None:
        assert rows == []
        return
    [row] = rows
    assert (row.severity, row.action) == (severity, "remind")
    assert row.since == d(14 - days)
    assert row.what == f"Check-in overdue {overdue}d"


def test_chase_reminder_text_names_owner_and_link():
    init = intake_stage_initiative(accepted_at=at(-25))
    [row] = group("Chase", init)
    assert row.what == "Check-in overdue 11d"
    assert row.reminder == triage.reminder_text(bundle_of(init))
    assert "Alpha" in row.reminder and f"/initiative/{init.entity_id}/" in row.reminder
    assert row.reminder.count("\n") == 1


def test_chase_uses_accepted_at_when_no_update():
    init = intake_stage_initiative(accepted_at=at(-25))
    [row] = group("Chase", init)
    assert row.what == "Check-in overdue 11d"
    assert row.since == d(-11)


def test_quiet_list_contains_unflagged_actives_only():
    quiet = intake_stage_initiative(name="Quiet")
    flagged = intake_stage_initiative(name="Flagged")
    add_risk(flagged, rating="H")
    intake_stage_initiative(name="Proposed", status="proposed")
    post_update(quiet, days_ago=1)
    assert quiet_names() == ["Quiet"]
    assert flagged.name not in quiet_names()


def test_quiet_excludes_closed():
    active_initiative(status="closed")
    assert quiet_names() == []


def readiness_for(init):
    return next(r for r in triage.readiness() if r.bundle.entity_id == init.entity_id)


def highlight(init, text="H", visibility=Visibility.EXEC):
    return curation.add_overlay(init.entity_id, TR, Overlay.Type.HIGHLIGHT, text, visibility=visibility)


@pytest.mark.parametrize("n,ok", [(0, False), (1, True), (3, True), (4, False)])
def test_readiness_highlight_count_rule(n, ok):
    init = intake_stage_initiative()
    for i in range(n):
        highlight(init, f"H{i}")
    r = readiness_for(init)
    assert (r.highlights, r.highlights_ok) == (n, ok)


def test_readiness_counts_only_exec_visible_highlights():
    init = intake_stage_initiative()
    highlight(init, "A", Visibility.INTERNAL)
    highlight(init, "B", Visibility.OWNER)
    r = readiness_for(init)
    assert (r.highlights, r.highlights_ok) == (0, False)
    highlight(init, "C", Visibility.EXEC)
    assert readiness_for(init).highlights == 1


def test_readiness_ignores_non_active():
    active_initiative(status="proposed")
    active_initiative(status="closed")
    assert triage.readiness() == []


def test_curated_ok_true_with_no_snapshot():
    init = intake_stage_initiative()
    assert readiness_for(init).curated_ok is True


def test_curated_ok_false_when_curation_predates_snapshot(frozen_clock):
    init = intake_stage_initiative()
    highlight(init)
    frozen_clock.advance(minutes=5)
    snapshots.take_snapshot(TR, "Nov")
    r = readiness_for(init)
    assert r.bundle.last_curated is not None
    assert r.curated_ok is False


def test_curated_ok_false_when_never_curated_after_snapshot():
    init = intake_stage_initiative()
    snapshots.take_snapshot(TR, "Nov")
    assert readiness_for(init).curated_ok is False


def test_curated_ok_true_after_touch(frozen_clock):
    init = intake_stage_initiative()
    snapshots.take_snapshot(TR, "Nov")
    frozen_clock.advance(minutes=5)
    curation.touch(init.entity_id, TR, "reviewed")
    assert readiness_for(init).curated_ok is True


def test_readiness_override_ok_requires_review_after_snapshot(frozen_clock):
    init = intake_stage_initiative()
    add_risk(init, rating="H", target_date=d(5))
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    frozen_clock.advance(minutes=5)
    snapshots.take_snapshot(TR, "Nov")
    assert readiness_for(init).override_ok is False
    frozen_clock.advance(minutes=5)
    curation.set_override(init, TR, "green", "Re-reviewed", "", None)
    assert readiness_for(init).override_ok is True


def test_readiness_stale_ok_needs_curation_after_last_update():
    init = intake_stage_initiative(accepted_at=at(-300))
    post_update(init, days_ago=40)
    r = readiness_for(init)
    assert (r.stale, r.stale_ok) == (True, False)
    curation.touch(init.entity_id, TR, "chased")
    r = readiness_for(init)
    assert r.stale_ok is True


def test_readiness_ready_requires_all_checks(frozen_clock):
    init = intake_stage_initiative()
    highlight(init)
    r = readiness_for(init)
    assert r.ready is True
    snapshots.take_snapshot(TR, "Nov")
    assert readiness_for(init).ready is False
