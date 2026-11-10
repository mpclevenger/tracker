import pytest
from django.conf import settings

from tracker.models import Initiative, RagEvaluation, RagOverride, RiskItem, ThresholdConfig
from tracker.services import curation, rag
from tracker.services.bundle import load_bundle
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
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_clock")]


def derive(init):
    return rag.derive(load_bundle(init))


def evaluate(init, persist=False):
    return rag.evaluate(load_bundle(init), persist=persist)


def test_default_thresholds_come_from_settings():
    assert rag.thresholds() == settings.RAG_THRESHOLD_DEFAULTS


def test_threshold_config_overrides_one_key():
    ThresholdConfig.create(TR, key="amber_stale_days", value=10)
    t = rag.thresholds()
    assert t["amber_stale_days"] == 10
    assert t["red_stale_days"] == settings.RAG_THRESHOLD_DEFAULTS["red_stale_days"]


def test_threshold_config_unknown_key_ignored():
    ThresholdConfig.create(TR, key="bogus", value=1)
    assert "bogus" not in rag.thresholds()


def test_threshold_config_new_version_wins():
    row = ThresholdConfig.create(TR, key="amber_stale_days", value=10)
    row.new_version(TR, value=20)
    assert rag.thresholds()["amber_stale_days"] == 20


@pytest.mark.parametrize(
    "slip,expected",
    [(30, "green"), (31, "amber"), (60, "amber"), (61, "red")],
)
def test_key_milestone_slip_boundaries(slip, expected):
    init = active_initiative()
    add_milestone(init, planned=d(0), forecast=d(slip))
    ev = derive(init)
    assert ev.derived == expected
    if expected != "green":
        assert ev.chip == f"Milestone slipped {slip}d"
        assert ev.rule == "Build exit"


def test_key_milestone_slip_uses_actual_over_forecast():
    init = active_initiative()
    add_milestone(init, planned=d(-100), forecast=d(-100), actual=d(-60), status="on_track")
    assert derive(init).derived == "amber"


def test_non_key_milestone_never_fires():
    init = active_initiative()
    add_milestone(init, planned=d(0), forecast=d(200), is_key=False)
    ev = derive(init)
    assert ev.derived == "green"
    assert ev.fired == []


def test_done_key_milestone_never_fires():
    init = active_initiative()
    add_milestone(init, planned=d(-100), forecast=d(-100), actual=d(-10), status="done")
    assert derive(init).derived == "green"


def test_milestone_without_forecast_or_planned_never_fires():
    init = active_initiative()
    add_milestone(init, planned=d(0))
    add_milestone(init, planned=None, forecast=d(100), name="No plan")
    assert derive(init).derived == "green"


def test_milestone_threshold_override_changes_behavior():
    init = active_initiative()
    add_milestone(init, planned=d(0), forecast=d(10))
    assert derive(init).derived == "green"
    ThresholdConfig.create(TR, key="amber_key_milestone_slip_days", value=9)
    assert derive(init).derived == "amber"
    ThresholdConfig.create(TR, key="red_key_milestone_slip_days", value=9)
    assert derive(init).derived == "red"


@pytest.mark.parametrize("slip,expected", [(30, "green"), (31, "red")])
def test_go_live_slip_vs_baseline_boundaries(slip, expected):
    init = active_initiative(baseline_go_live=d(300), target_go_live=d(300 + slip))
    ev = derive(init)
    assert ev.derived == expected
    if expected == "red":
        assert ev.chip == f"Go-live slipped {slip}d"
        assert ev.rule.startswith("Versus baseline")


def test_go_live_slip_needs_baseline():
    init = active_initiative(baseline_go_live=None, target_go_live=d(400))
    assert derive(init).derived == "green"


def test_open_high_risk_is_amber():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(10))
    ev = derive(init)
    assert ev.derived == "amber"
    assert ev.chip == "High risk open"
    assert ev.rule == ev.fired[0][2]


def test_open_high_risk_without_target_is_amber():
    init = active_initiative()
    add_risk(init, rating="H", target_date=None)
    assert derive(init).derived == "amber"


def test_mitigating_high_risk_counts_as_open():
    init = active_initiative()
    add_risk(init, rating="H", status="mitigating")
    assert derive(init).derived == "amber"


def test_high_risk_target_today_is_amber_and_yesterday_is_red():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(0))
    assert derive(init).derived == "amber"
    add_risk(init, rating="H", target_date=d(-1))
    ev = derive(init)
    assert ev.derived == "red"
    assert ev.chip == "High risk past due"


def test_high_issue_past_due_detail_is_its_title():
    init = active_initiative()
    add_risk(init, rating="H", type="issue", target_date=d(-5), title="Broken feed")
    ev = derive(init)
    assert (ev.chip, ev.rule) == ("High risk past due", "Broken feed")


def test_closed_high_risk_fires_nothing():
    init = active_initiative()
    add_risk(init, rating="H", status="closed", target_date=d(-30))
    ev = derive(init)
    assert ev.derived == "green"
    assert ev.fired == []


@pytest.mark.parametrize("rating", ["M", "L"])
def test_non_high_risk_fires_nothing(rating):
    init = active_initiative()
    add_risk(init, rating=rating, target_date=d(-30))
    assert derive(init).derived == "green"


@pytest.mark.parametrize("days,expected", [(90, "amber"), (91, "green")])
def test_go_live_90d_with_in_progress_control(days, expected):
    init = active_initiative(target_go_live=d(days))
    add_impact(init, readiness="in_progress")
    assert derive(init).derived == expected


@pytest.mark.parametrize(
    "days,readiness,expected",
    [
        (60, "not_started", "red"),
        (61, "not_started", "green"),
        (60, "in_progress", "amber"),
        (60, "submitted", "amber"),
        (60, "sent_back", "amber"),
        (60, "ready", "green"),
    ],
)
def test_go_live_60d_control_readiness(days, readiness, expected):
    init = active_initiative(target_go_live=d(days))
    add_impact(init, readiness=readiness)
    ev = derive(init)
    assert ev.derived == expected
    assert ev.days_to_go_live == days
    if expected == "red":
        assert ev.chip == f"Control not ready, go-live in {days}d"
        assert ev.rule.endswith("(Go-live)")


@pytest.mark.parametrize("readiness", ["in_progress", "submitted", "sent_back"])
def test_in_progress_states_name_the_amber_chip(readiness):
    init = active_initiative(target_go_live=d(45))
    add_impact(init, readiness=readiness)
    ev = derive(init)
    assert ev.chip == "Control in progress, go-live in 45d"


def test_go_live_window_without_controls_fires_nothing():
    init = active_initiative(target_go_live=d(10))
    assert derive(init).derived == "green"


def test_go_live_window_thresholds_are_configurable():
    init = active_initiative(target_go_live=d(61))
    add_impact(init, readiness="not_started")
    assert derive(init).derived == "green"
    ThresholdConfig.create(TR, key="red_go_live_window_days", value=61)
    assert derive(init).derived == "red"


@pytest.mark.parametrize(
    "days,expected",
    [(30, "green"), (31, "amber"), (60, "amber"), (61, "red")],
)
def test_stale_boundaries_from_posted_update(days, expected):
    init = active_initiative(accepted_at=at(-200))
    post_update(init, days_ago=days)
    ev = derive(init)
    assert ev.derived == expected
    assert ev.stale_days == days


def test_stale_measured_from_latest_update_not_oldest():
    init = active_initiative(accepted_at=at(-200))
    post_update(init, days_ago=90)
    post_update(init, days_ago=5)
    ev = derive(init)
    assert ev.stale_days == 5
    assert ev.derived == "green"


@pytest.mark.parametrize("days,expected", [(30, "green"), (31, "amber"), (61, "red")])
def test_stale_falls_back_to_accepted_at(days, expected):
    init = active_initiative(accepted_at=at(-days))
    ev = derive(init)
    assert ev.stale_days == days
    assert ev.derived == expected


def test_stale_none_without_update_or_acceptance():
    init = active_initiative(accepted_at=None)
    ev = derive(init)
    assert ev.stale_days is None
    assert ev.derived == "green"


def test_stale_threshold_override():
    init = active_initiative()
    post_update(init, days_ago=11)
    assert derive(init).derived == "green"
    ThresholdConfig.create(TR, key="amber_stale_days", value=10)
    assert derive(init).derived == "amber"


def test_stale_flags_on_evaluation():
    init = active_initiative()
    post_update(init, days_ago=22)
    ev = derive(init)
    assert ev.early_warning_stale and not ev.is_stale
    init2 = active_initiative(name="Beta")
    post_update(init2, days_ago=31)
    ev2 = derive(init2)
    assert ev2.early_warning_stale and ev2.is_stale
    init3 = active_initiative(name="Gamma")
    post_update(init3, days_ago=21)
    assert not derive(init3).early_warning_stale


@pytest.mark.parametrize("status", ["closed", "archived"])
def test_closed_and_archived_are_green_with_no_rules(status):
    init = active_initiative(
        status=status, accepted_at=at(-400), baseline_go_live=d(100), target_go_live=d(200)
    )
    add_milestone(init, planned=d(0), forecast=d(100))
    add_risk(init, rating="H", target_date=d(-10))
    add_impact(init, readiness="not_started")
    ev = derive(init)
    assert ev.derived == "green"
    assert ev.effective == "green"
    assert ev.fired == []
    assert ev.rule == "No rule fired"


def test_worst_rule_wins_and_rule_names_top_rule():
    init = active_initiative(baseline_go_live=d(300), target_go_live=d(340))
    add_risk(init, rating="H", target_date=d(10))
    post_update(init, days_ago=35)
    ev = derive(init)
    assert ev.derived == "red"
    assert ev.chip == "Go-live slipped 40d"
    assert ev.rule.startswith("Versus baseline")
    assert [level for level, _chip, _detail in ev.fired] == ["red", "amber", "amber"]
    assert [chip for _level, chip, _detail in ev.fired][1:] == ["High risk open", "No check-in 35d"]


def test_amber_rule_named_when_no_red():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(10), title="Vendor")
    post_update(init, days_ago=35)
    ev = derive(init)
    assert ev.derived == "amber"
    assert (ev.chip, ev.rule) == ("High risk open", "Vendor")
    assert len(ev.fired) == 2


def test_fired_lists_every_rule():
    init = active_initiative(target_go_live=d(50), baseline_go_live=d(10))
    add_milestone(init, planned=d(0), forecast=d(70))
    add_risk(init, rating="H", target_date=d(-1))
    add_impact(init, readiness="not_started")
    add_impact(init, readiness="in_progress")
    post_update(init, days_ago=70)
    ev = derive(init)
    assert [chip for _level, chip, _detail in ev.fired] == [
        "Milestone slipped 70d", "Go-live slipped 40d", "High risk past due",
        "Control not ready, go-live in 50d", "Control in progress, go-live in 50d", "No check-in 70d",
    ]


def test_green_when_nothing_fires():
    init = active_initiative()
    post_update(init, days_ago=1)
    ev = derive(init)
    assert (ev.derived, ev.rule, ev.fired) == ("green", "No rule fired", [])


def test_evaluate_persist_true_appends_row():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(-1), title="Late")
    ev = evaluate(init, persist=True)
    row = RagEvaluation.objects.get(initiative_entity=init.entity_id)
    assert (row.derived, row.effective) == ("red", "red")
    assert row.rule_fired == ev.chip == "High risk past due"
    assert row.override_entity is None
    evaluate(init, persist=True)
    assert RagEvaluation.objects.filter(initiative_entity=init.entity_id).count() == 2
    assert rag.latest_logged(init.entity_id).derived == "red"


def test_evaluate_persist_false_writes_nothing():
    init = active_initiative()
    evaluate(init, persist=False)
    assert RagEvaluation.objects.count() == 0


def test_recompute_persists():
    init = active_initiative()
    rag.recompute(init)
    assert RagEvaluation.objects.filter(initiative_entity=init.entity_id).count() == 1


def test_evaluation_records_override_entity_and_effective():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(10))
    ov = curation.set_override(init, TR, "green", "Mitigated", "", d(10))
    row = rag.latest_logged(init.entity_id)
    assert (row.derived, row.effective) == ("amber", "green")
    assert row.override_entity == ov.entity_id


def amber_initiative():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(10))
    return init


def test_set_override_stores_derived_at_set_and_effective_value():
    init = amber_initiative()
    ov = curation.set_override(init, TR, "green", "Mitigated", "TICKET-1", d(14))
    assert ov.derived_at_set == "amber"
    assert (ov.value, ov.reason, ov.support, ov.review_by) == ("green", "Mitigated", "TICKET-1", d(14))
    ev = evaluate(init)
    assert ev.derived == "amber"
    assert ev.effective == "green"
    assert ev.override.entity_id == ov.entity_id


def test_override_holds_while_derived_equals_derived_at_set():
    init = amber_initiative()
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    ev = rag.recompute(init)
    assert ev.effective == "green"
    assert RagOverride.objects.current().filter(initiative_entity=init.entity_id, cleared_at__isnull=True).count() == 1


def test_override_holds_when_derived_improves():
    init = amber_initiative()
    curation.set_override(init, TR, "red", "Exposure higher", "", None)
    risk = RiskItem.objects.current().get(initiative_entity=init.entity_id)
    risk.new_version(TR, status="closed")
    ev = rag.recompute(init)
    assert ev.derived == "green"
    assert ev.effective == "red"


def test_override_auto_expires_when_derived_worsens():
    init = amber_initiative()
    ov = curation.set_override(init, TR, "green", "Mitigated", "", None)
    add_risk(init, rating="H", target_date=d(-1))
    ev = rag.recompute(init)
    assert ev.derived == "red"
    assert ev.effective == "red"
    assert ev.override is None
    history = curation.override_history(init.entity_id)
    assert len(history) == 2
    current = history[0]
    assert current.entity_id == ov.entity_id
    assert current.cleared_by == RagOverride.ClearedBy.AUTO
    assert current.cleared_at is not None
    assert load_bundle(init).override is None
    row = rag.latest_logged(init.entity_id)
    assert row.override_entity is None and row.effective == "red"


def test_auto_expire_not_persisted_when_persist_false():
    init = amber_initiative()
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    add_risk(init, rating="H", target_date=d(-1))
    bundle = load_bundle(init)
    ev = rag.evaluate(bundle, persist=False)
    assert ev.effective == "red"
    assert load_bundle(init).override is not None
    assert len(curation.override_history(init.entity_id)) == 1


def test_override_set_at_red_to_amber_does_not_expire_while_red():
    init = active_initiative()
    add_risk(init, rating="H", target_date=d(-1))
    ov = curation.set_override(init, TR, "amber", "Partly mitigated", "", None)
    assert ov.derived_at_set == "red"
    ev = rag.recompute(init)
    assert ev.derived == "red"
    assert ev.effective == "amber"
    assert ev.override is not None
    assert len(curation.override_history(init.entity_id)) == 1


def test_override_set_at_amber_survives_red_only_if_derived_at_set_was_red():
    init = amber_initiative()
    ov = curation.set_override(init, TR, "red", "Higher exposure", "", None)
    assert ov.derived_at_set == "amber"
    add_risk(init, rating="H", target_date=d(-1))
    ev = rag.recompute(init)
    assert ev.override is None
    assert ev.effective == "red"


def test_clear_override_writes_manual():
    init = amber_initiative()
    ov = curation.set_override(init, TR, "green", "Mitigated", "", None)
    curation.clear_override(init, TR)
    history = curation.override_history(init.entity_id)
    assert history[0].cleared_by == RagOverride.ClearedBy.MANUAL
    assert history[0].cleared_at is not None
    assert history[0].entity_id == ov.entity_id
    assert load_bundle(init).override is None
    assert rag.latest_logged(init.entity_id).effective == "amber"


def test_clear_override_without_override_is_noop():
    init = amber_initiative()
    curation.clear_override(init, TR)
    assert curation.override_history(init.entity_id) == []


def test_setting_second_override_clears_first_manually():
    init = amber_initiative()
    first = curation.set_override(init, TR, "green", "One", "", None)
    second = curation.set_override(init, TR, "red", "Two", "", None)
    history = curation.override_history(init.entity_id)
    assert len(history) == 3
    cleared = [h for h in history if h.entity_id == first.entity_id and h.cleared_at]
    assert cleared and cleared[0].cleared_by == RagOverride.ClearedBy.MANUAL
    assert load_bundle(init).override.entity_id == second.entity_id


def test_override_history_returns_versions_newest_first():
    init = amber_initiative()
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    curation.clear_override(init, TR)
    history = curation.override_history(init.entity_id)
    assert [h.cleared_at is None for h in history] == [False, True]
    assert history[0].valid_from > history[1].valid_from


def test_set_override_records_curation_event():
    init = amber_initiative()
    curation.set_override(init, TR, "green", "Mitigated", "", None)
    assert bundle_last_curated(init) is not None


def bundle_last_curated(init):
    return load_bundle(Initiative.objects.get_current(init.entity_id)).last_curated


def _linked(readiness, ms_days, go_live_days=200, **ms):
    init = active_initiative(target_go_live=d(go_live_days))
    m = add_milestone(init, planned=d(ms_days), forecast=d(ms_days), name="Test exit", **ms)
    add_impact(init, readiness=readiness, needed_by=m)
    return init


@pytest.mark.parametrize(
    "days,expected", [(60, "red"), (61, "green")],
)
def test_milestone_linked_control_uses_its_own_due_date(days, expected):
    ev = derive(_linked("not_started", days))
    assert ev.derived == expected
    if expected == "red":
        assert ev.chip == f"Control not ready, due in {days}d"
        assert ev.rule.endswith("(Test exit)")


def test_milestone_linked_control_in_progress_amber_before_go_live():
    ev = derive(_linked("submitted", 90))
    assert (ev.derived, ev.chip) == ("amber", "Control in progress, due in 90d")


def test_milestone_linked_control_past_due_wording():
    assert derive(_linked("not_started", -3, status="slipped")).chip == "Control not ready, past due"
    assert derive(_linked("in_progress", -3)).chip == "Control in progress, past due"


def test_go_live_passed_wording_kept():
    init = active_initiative(target_go_live=d(-2))
    add_impact(init, readiness="not_started")
    assert derive(init).chip == "Control not ready, go-live passed"


def test_ready_milestone_linked_control_does_not_fire():
    assert derive(_linked("ready", 5)).derived == "green"


def test_mixed_due_dates_chip_uses_most_urgent():
    init = active_initiative(target_go_live=d(50))
    m = add_milestone(init, planned=d(20), forecast=d(20), name="Test exit")
    add_impact(init, readiness="not_started")
    add_impact(init, readiness="not_started", needed_by=m)
    ev = derive(init)
    assert ev.chip == "Control not ready, due in 20d"
    assert "(Test exit)" in ev.rule and "(Go-live)" in ev.rule
