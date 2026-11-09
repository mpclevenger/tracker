import pytest

from tracker import lifecycle as rules
from tracker.lifecycle import STAGES
from tracker.models import CurationEvent, GateApproval, GuidancePage, Initiative, StageCheck
from tracker.services import lifecycle
from tracker.tests.services_helpers import (
    TR,
    active_initiative,
    add_phase,
    bundle_of,
    complete_stage,
    d,
    frozen_clock,  # noqa: F401
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_clock")]


@pytest.fixture
def guided():
    lifecycle.seed_guidance("E1")


def statuses(init):
    return {s.number: s for s in lifecycle.stage_statuses(bundle_of(init))}


def test_seed_guidance_creates_seven_current_pages():
    lifecycle.seed_guidance("E1")
    pages = GuidancePage.objects.current()
    assert pages.count() == 7
    assert sorted(p.stage for p in pages) == list(range(7))
    assert {p.version_label for p in pages} == {"v1.0"}
    assert lifecycle.guidance_version() == "v1.0"
    page = lifecycle.current_guidance()[1]
    assert page.checklist == STAGES[1].checklist
    assert page.author_emp_id == "E1"


def test_seed_guidance_is_idempotent():
    lifecycle.seed_guidance("E1")
    lifecycle.seed_guidance("E2", version_label="v9")
    assert GuidancePage.objects.count() == 7
    assert GuidancePage.objects.current().count() == 7
    assert {p.version_label for p in GuidancePage.objects.current()} == {"v1.0"}


def test_guidance_version_without_pages():
    assert lifecycle.guidance_version() == "—"


def test_stage_statuses_without_guidance_is_never_complete():
    init = active_initiative()
    s = statuses(init)[1]
    assert (s.total, s.done, s.complete, s.page) == (0, 0, False, None)
    assert s.items() == []


def test_stage_statuses_cover_seven_stages_and_mark_current(guided):
    init = active_initiative()
    add_phase(init, "Build", stage=2)
    sts = lifecycle.stage_statuses(bundle_of(init))
    assert [s.number for s in sts] == list(range(7))
    assert [s.number for s in sts if s.is_current] == [2]
    assert sts[1].total == len(STAGES[1].checklist)


def accept_all(init, stage, upto=None):
    page = lifecycle.current_guidance()[stage]
    for idx in range(upto if upto is not None else len(page.checklist)):
        lifecycle.accept_item(bundle_of(init), TR, stage, idx, evidence_ref=f"ev-{idx}")


def test_signoff_stage_complete_requires_all_items_and_a_gate(guided):
    init = active_initiative(oversight_override="enhanced")
    total = len(lifecycle.current_guidance()[1].checklist)
    accept_all(init, 1, upto=total - 1)
    s = statuses(init)[1]
    assert (s.done, s.total, s.items_done, s.complete) == (total - 1, total, False, False)

    accept_all(init, 1)
    s = statuses(init)[1]
    assert (s.items_done, s.ready_for_signoff, s.complete) == (True, True, False)

    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "approval")
    s = statuses(init)[1]
    assert (s.signed_off, s.ready_for_signoff, s.complete) == (True, False, True)


def test_hold_does_not_sign_off(guided):
    init = active_initiative(oversight_override="enhanced")
    accept_all(init, 1)
    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "ev", decision="hold")
    s = statuses(init)[1]
    assert (s.signed_off, s.complete, s.ready_for_signoff) == (False, False, True)
    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(0), "ev2", decision="proceed")
    assert statuses(init)[1].complete is True


def test_gate_without_all_items_is_not_complete(guided):
    init = active_initiative(oversight_override="enhanced")
    accept_all(init, 1, upto=1)
    lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "approval")
    s = statuses(init)[1]
    assert s.gate is not None and s.complete is False


def test_requirement_follows_oversight_level(guided):
    levels = {}
    for level in ("enhanced", "standard", "light"):
        init = active_initiative(name=level, oversight_override=level)
        levels[level] = [s.requirement for s in lifecycle.stage_statuses(bundle_of(init))]
    assert levels["enhanced"][0] is None and levels["enhanced"][1:] == [rules.SIGNOFF] * 6
    assert levels["standard"][2] == rules.REVIEW and levels["light"][2] == rules.OWNER
    assert all(r[4] == rules.SIGNOFF for r in levels.values())
    assert levels["light"][1:] == [rules.REVIEW, rules.OWNER, rules.REVIEW, rules.SIGNOFF, rules.REVIEW, rules.REVIEW]


def test_review_and_owner_stages_complete_without_a_gate(guided):
    init = active_initiative(oversight_override="light")
    accept_all(init, 2)
    s = statuses(init)[2]
    assert (s.needs_signoff, s.signed_off, s.complete) == (False, False, True)


def test_stage_status_items_pairs_text_with_check(guided):
    init = active_initiative()
    lifecycle.accept_item(bundle_of(init), TR, 0, 1, evidence_ref="ev")
    items = statuses(init)[0].items()
    assert [i for i, _, _ in items] == [0, 1, 2]
    assert items[0][2] is None and items[1][2].item_index == 1


def test_submit_evidence_then_accept(guided):
    init = active_initiative()
    page = lifecycle.current_guidance()[1]
    row = lifecycle.submit_evidence(bundle_of(init), "OWN1", 1, 2, "https://ev/x", note="n")
    assert (row.status, row.evidence_ref, row.note, row.author_emp_id) == ("submitted", "https://ev/x", "n", "OWN1")
    assert (row.item_text, row.guidance_version, row.initiative_entity) == (page.checklist[2], "v1.0", init.entity_id)
    s = statuses(init)[1]
    assert (s.done, len(s.submitted)) == (0, 1)

    accepted = lifecycle.accept_item(bundle_of(init), TR, 1, 2, StageCheck.Basis.OBSERVED)
    assert (accepted.status, accepted.basis, accepted.evidence_ref, accepted.author_emp_id) == ("accepted", "observed", "https://ev/x", TR)
    assert accepted.entity_id == row.entity_id and accepted.comment == ""
    s = statuses(init)[1]
    assert (s.done, len(s.submitted)) == (1, 0)
    whats = list(CurationEvent.objects.values_list("what", flat=True))
    assert "stage 1 item 2: evidence submitted" in whats and "stage 1 item 2: accepted" in whats


def test_accept_directly_with_evidence_reference(guided):
    init = active_initiative()
    row = lifecycle.accept_item(bundle_of(init), TR, 0, 0, evidence_ref="TICKET-1")
    assert (row.status, row.basis, row.evidence_ref) == ("accepted", "reviewed", "TICKET-1")


def test_send_back_requires_comment_and_keeps_item_open(guided):
    init = active_initiative()
    lifecycle.submit_evidence(bundle_of(init), "OWN1", 1, 0, "ev")
    with pytest.raises(ValueError):
        lifecycle.send_back_item(bundle_of(init), TR, 1, 0, "  ")
    row = lifecycle.send_back_item(bundle_of(init), TR, 1, 0, "Does not show approval")
    assert (row.status, row.comment) == ("sent_back", "Does not show approval")
    assert statuses(init)[1].done == 0
    resubmitted = lifecycle.submit_evidence(bundle_of(init), "OWN1", 1, 0, "ev2")
    assert (resubmitted.status, resubmitted.comment) == ("submitted", "")


def test_not_applicable_requires_reason_and_counts_as_done(guided):
    init = active_initiative()
    with pytest.raises(ValueError):
        lifecycle.mark_not_applicable(bundle_of(init), TR, 1, 3, "")
    row = lifecycle.mark_not_applicable(bundle_of(init), TR, 1, 3, "No data migration")
    assert (row.status, row.comment, row.basis) == ("na", "No data migration", "")
    assert statuses(init)[1].done == 1


def test_owner_confirm_only_on_owner_confirms_stages(guided):
    light = active_initiative(name="Light", oversight_override="light")
    row = lifecycle.owner_confirm(bundle_of(light), "OWN1", 2, 0, "https://ev", "done")
    assert (row.status, row.basis, row.evidence_ref, row.author_emp_id) == ("confirmed", "attested", "https://ev", "OWN1")
    assert statuses(light)[2].done == 1
    with pytest.raises(ValueError):
        lifecycle.owner_confirm(bundle_of(light), "OWN1", 1, 0)
    enhanced = active_initiative(name="Enhanced", oversight_override="enhanced")
    with pytest.raises(ValueError):
        lifecycle.owner_confirm(bundle_of(enhanced), "OWN1", 2, 0)
    assert StageCheck.objects.current().count() == 1


def test_reopen_retires_item_and_logs(guided):
    init = active_initiative()
    row = lifecycle.accept_item(bundle_of(init), TR, 1, 0, evidence_ref="ev")
    lifecycle.reopen_item(bundle_of(init), TR, 1, 0)
    assert not StageCheck.objects.current().filter(initiative_entity=init.entity_id).exists()
    assert StageCheck.objects.current_including_deleted().get(entity_id=row.entity_id).is_deleted
    assert "stage 1 item 0: reopened" in CurationEvent.objects.values_list("what", flat=True)
    assert statuses(init)[1].done == 0


def test_reopen_open_item_is_noop(guided):
    init = active_initiative()
    lifecycle.reopen_item(bundle_of(init), TR, 1, 0)
    assert CurationEvent.objects.count() == 0


def test_item_out_of_range_is_rejected(guided):
    with pytest.raises(ValueError):
        lifecycle.submit_evidence(bundle_of(active_initiative()), "OWN1", 1, 99, "ev")
    assert not StageCheck.objects.exists()


def test_item_without_guidance_is_rejected():
    with pytest.raises(ValueError):
        lifecycle.submit_evidence(bundle_of(active_initiative()), "OWN1", 1, 0, "ev")
    assert not StageCheck.objects.exists()


def test_record_gate_creates_then_versions(guided):
    init = active_initiative()
    first = lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-5), "ev-1")
    assert (first.decision, first.conditions) == ("proceed", "")
    second = lifecycle.record_gate(bundle_of(init), TR, 1, "CISO", d(-1), "ev-2", "conditions", "Close defects by Dec 1")
    assert second.entity_id == first.entity_id
    assert GateApproval.objects.filter(entity_id=first.entity_id).count() == 2
    current = GateApproval.objects.current().get(initiative_entity=init.entity_id)
    assert (current.approver, current.approved_on, current.evidence_ref) == ("CISO", d(-1), "ev-2")
    assert (current.decision, current.conditions) == ("conditions", "Close defects by Dec 1")
    assert current.entity_id == first.entity_id
    assert CurationEvent.objects.filter(what="stage 1 gate review: conditions").exists()


def test_conditions_decision_requires_conditions(guided):
    init = active_initiative()
    with pytest.raises(ValueError):
        lifecycle.record_gate(bundle_of(init), TR, 1, "Sponsor", d(-1), "ev", "conditions", " ")
    assert not GateApproval.objects.exists()


def test_gates_are_per_stage(guided):
    init = active_initiative()
    lifecycle.record_gate(bundle_of(init), TR, 1, "A", d(-5), "e")
    lifecycle.record_gate(bundle_of(init), TR, 2, "B", d(-4), "e")
    assert GateApproval.objects.current().count() == 2


def test_incomplete_past_gates_lists_only_earlier_incomplete_stages(guided):
    init = active_initiative(phase="Test")
    add_phase(init, "Test", stage=3)
    assert [s.number for s in lifecycle.incomplete_past_gates(bundle_of(init))] == [0, 1, 2]
    complete_stage(init, 0)
    complete_stage(init, 1)
    assert [s.number for s in lifecycle.incomplete_past_gates(bundle_of(init))] == [2]
    complete_stage(init, 2)
    complete_stage(init, 3)
    assert lifecycle.incomplete_past_gates(bundle_of(init)) == []


def test_incomplete_past_gates_empty_at_stage_zero(guided):
    init = active_initiative(phase="Intake")
    add_phase(init, "Intake", stage=0)
    assert lifecycle.incomplete_past_gates(bundle_of(init)) == []


def test_incomplete_past_gates_for_closed_initiative(guided):
    init = active_initiative(status="closed")
    assert [s.number for s in lifecycle.incomplete_past_gates(bundle_of(init))] == [0, 1, 2, 3, 4, 5]


def test_incomplete_past_gates_without_guidance_flags_all_past_stages():
    init = active_initiative()
    assert [s.number for s in lifecycle.incomplete_past_gates(bundle_of(init))] == [0, 1]


def test_current_stage_from_named_phase():
    init = active_initiative(phase="Cutover")
    add_phase(init, "Build", stage=2, order=0)
    add_phase(init, "Cutover", stage=4, order=1)
    assert bundle_of(init).current_stage() == 4


@pytest.mark.parametrize(
    "status,expected",
    [("proposed", 0), ("active", 2), ("closed", 6), ("archived", 6)],
)
def test_current_stage_fallbacks_by_status(status, expected):
    init = active_initiative(status=status, phase="Unmatched")
    add_phase(init, "Build", stage=3)
    assert bundle_of(init).current_stage() == expected


def test_current_stage_phase_match_wins_over_status():
    init = active_initiative(status=Initiative.Status.PROPOSED, phase="Design")
    add_phase(init, "Design", stage=1)
    assert bundle_of(init).current_stage() == 1


def test_current_stage_without_phases_for_active_is_build():
    assert bundle_of(active_initiative(phase="")).current_stage() == 2


def test_map_checklist_reorder_reword_remove():
    from tracker.services.lifecycle import map_checklist
    assert map_checklist(["a", "b", "c"], ["c", "a", "b"]) == {0: 1, 1: 2, 2: 0}
    assert map_checklist(["a", "b", "c"], ["a", "B edited", "c"]) == {0: 0, 1: 1, 2: 2}
    assert map_checklist(["a", "b", "c"], ["a", "c"]) == {0: 0, 2: 1}


def test_save_guidance_moves_and_closes_item_records(guided):
    init = active_initiative()
    page = lifecycle.current_guidance()[1]
    first, second = page.checklist[0], page.checklist[1]
    lifecycle.accept_item(bundle_of(init), TR, 1, 0, evidence_ref="https://ev/a")
    lifecycle.accept_item(bundle_of(init), TR, 1, 1, evidence_ref="https://ev/b")
    lifecycle.save_guidance(page, TR, version_label="v1.1", checklist=[second, "New item", first])
    checks = bundle_of(init).checks_for(1)
    assert checks[2].evidence_ref == "https://ev/a" and checks[0].evidence_ref == "https://ev/b"
    assert 1 not in checks
    lifecycle.save_guidance(lifecycle.current_guidance()[1], TR, version_label="v1.2", checklist=[second])
    assert list(bundle_of(init).checks_for(1)) == [0]
