"""Guidance pages, stage checklists and gate approvals."""

from dataclasses import dataclass

from django.db import transaction

from tracker.lifecycle import OWNER, SIGNOFF, STAGES, color, gate_matrix, requirement
from tracker.models import GateApproval, GuidancePage, StageCheck
from tracker.services.bundle import Bundle
from tracker.services.curation import touch


def current_guidance() -> dict:
    """{stage_number: GuidancePage} for the current version of each stage."""
    pages = {p.stage: p for p in GuidancePage.objects.current()}
    return pages


@dataclass(frozen=True)
class StageInfo:
    number: int
    name: str
    color: str


def stage_info(number: int, guidance: dict | None = None) -> StageInfo:
    """Stage name from current guidance (editable in the app), falling back to the seeded default."""
    guidance = guidance if guidance is not None else current_guidance()
    page = guidance.get(number)
    return StageInfo(number, page.name if page else STAGES[number].name, color(number))


def stage_infos(guidance: dict | None = None) -> list[StageInfo]:
    guidance = guidance if guidance is not None else current_guidance()
    return [stage_info(st.number, guidance) for st in STAGES]


def guidance_version() -> str:
    pages = current_guidance()
    return max((p.version_label for p in pages.values()), default="—")


def seed_guidance(author_emp_id, version_label="v1.0", change_note="Initial lifecycle and checklists."):
    if GuidancePage.objects.current().exists():
        return
    for st in STAGES:
        GuidancePage.create(
            author_emp_id,
            stage=st.number,
            version_label=version_label,
            change_note=change_note,
            name=st.name,
            approver=st.approver,
            checklist=list(st.checklist),
        )


def map_checklist(old: list, new: list) -> dict:
    """{old_index: new_index} for items kept: same wording anywhere, else edited in place at the same position."""
    mapping, used = {}, set()
    for i, text in enumerate(old):
        for j, candidate in enumerate(new):
            if j not in used and candidate == text:
                mapping[i] = j
                used.add(j)
                break
    for i in range(len(old)):
        if i not in mapping and i < len(new) and i not in used:
            mapping[i] = i
            used.add(i)
    return mapping


@transaction.atomic
def save_guidance(page: GuidancePage, author_emp_id, **fields) -> GuidancePage:
    """New guidance version; existing item records follow their item when the checklist is reordered or trimmed."""
    mapping = map_checklist(page.checklist, fields.get("checklist", page.checklist))
    for check in StageCheck.objects.current().filter(stage=page.stage):
        target = mapping.get(check.item_index)
        if target is None:
            check.retire(author_emp_id)
        elif target != check.item_index:
            check.new_version(author_emp_id, item_index=target)
    return page.new_version(author_emp_id, **fields)


@dataclass
class StageStatus:
    number: int
    name: str
    color: str
    page: GuidancePage | None
    checks: dict  # item_index -> StageCheck (current state of each item that has one)
    gate: GateApproval | None
    is_current: bool
    requirement: str | None  # SIGNOFF / REVIEW / OWNER from the gate matrix; None for intake

    @property
    def total(self):
        return len(self.page.checklist) if self.page else 0

    @property
    def done(self):
        return sum(1 for c in self.checks.values() if c.is_done)

    @property
    def items_done(self):
        return self.page is not None and self.done >= self.total

    @property
    def needs_signoff(self):
        return self.requirement == SIGNOFF

    @property
    def signed_off(self):
        return self.gate is not None and self.gate.decision != GateApproval.Decision.HOLD

    @property
    def ready_for_signoff(self):
        return self.needs_signoff and self.items_done and not self.signed_off

    @property
    def complete(self):
        return self.items_done and (self.signed_off if self.needs_signoff else True)

    @property
    def submitted(self):
        return [c for c in self.checks.values() if c.status == StageCheck.Status.SUBMITTED]

    def items(self):
        if not self.page:
            return []
        return [(i, text, self.checks.get(i)) for i, text in enumerate(self.page.checklist)]


def stage_statuses(bundle: Bundle, guidance: dict | None = None) -> list[StageStatus]:
    guidance = guidance if guidance is not None else current_guidance()
    cur = bundle.current_stage()
    level = bundle.initiative.oversight
    matrix = gate_matrix()
    return [
        StageStatus(
            number=info.number,
            name=info.name,
            color=info.color,
            page=guidance.get(info.number),
            checks=bundle.checks_for(info.number),
            gate=bundle.gate_for(info.number),
            is_current=info.number == cur,
            requirement=requirement(level, info.number, matrix),
        )
        for info in stage_infos(guidance)
    ]


def incomplete_past_gates(bundle: Bundle, guidance=None) -> list[StageStatus]:
    cur = bundle.current_stage()
    return [s for s in stage_statuses(bundle, guidance) if s.number < cur and not s.complete]


def _set_item(bundle: Bundle, author_emp_id, stage, item_index, what, **fields):
    """Write a new state for one checklist item (supersedes the current one) and log the curation touch."""
    page = current_guidance().get(stage)
    if page is None or item_index >= len(page.checklist):
        raise ValueError("No such checklist item")
    existing = bundle.checks_for(stage).get(item_index)
    if existing:
        row = existing.new_version(author_emp_id, **fields)
    else:
        row = StageCheck.create(
            author_emp_id,
            initiative_entity=bundle.entity_id,
            stage=stage,
            item_index=item_index,
            item_text=page.checklist[item_index][:300],
            guidance_version=page.version_label,
            **fields,
        )
    touch(bundle.entity_id, author_emp_id, f"stage {stage} item {item_index}: {what}")
    return row


@transaction.atomic
def submit_evidence(bundle, author_emp_id, stage, item_index, evidence_ref, note=""):
    """Owner submits evidence for Tech Risk to review."""
    return _set_item(bundle, author_emp_id, stage, item_index, "evidence submitted",
                     status=StageCheck.Status.SUBMITTED, evidence_ref=evidence_ref, note=note, comment="")


@transaction.atomic
def accept_item(bundle, author_emp_id, stage, item_index, basis=StageCheck.Basis.REVIEWED, evidence_ref=None):
    """Tech Risk accepts an item (submitted evidence, or directly with its own evidence reference)."""
    fields = {"status": StageCheck.Status.ACCEPTED, "basis": basis, "comment": ""}
    if evidence_ref is not None:
        fields["evidence_ref"] = evidence_ref
    return _set_item(bundle, author_emp_id, stage, item_index, "accepted", **fields)


@transaction.atomic
def send_back_item(bundle, author_emp_id, stage, item_index, comment):
    """Tech Risk returns submitted evidence to the owner with a comment."""
    if not comment.strip():
        raise ValueError("Enter a comment to send the evidence back.")
    return _set_item(bundle, author_emp_id, stage, item_index, "sent back",
                     status=StageCheck.Status.SENT_BACK, comment=comment)


@transaction.atomic
def mark_not_applicable(bundle, author_emp_id, stage, item_index, reason):
    """Tech Risk scopes an item out with a reason; it counts as done."""
    if not reason.strip():
        raise ValueError("Enter a reason to mark this item not applicable.")
    return _set_item(bundle, author_emp_id, stage, item_index, "not applicable",
                     status=StageCheck.Status.NOT_APPLICABLE, comment=reason, basis="")


@transaction.atomic
def owner_confirm(bundle, author_emp_id, stage, item_index, evidence_ref="", note=""):
    """Owner confirms an item directly; only for stages whose requirement is owner confirmation."""
    if requirement(bundle.initiative.oversight, stage) != OWNER:
        raise ValueError("This stage needs Tech Risk review")
    return _set_item(bundle, author_emp_id, stage, item_index, "confirmed by owner",
                     status=StageCheck.Status.CONFIRMED, basis=StageCheck.Basis.ATTESTED,
                     evidence_ref=evidence_ref, note=note, comment="")


@transaction.atomic
def reopen_item(bundle, author_emp_id, stage, item_index):
    """Clear an item's state (retire the current row); it goes back to open."""
    existing = bundle.checks_for(stage).get(item_index)
    if existing:
        existing.retire(author_emp_id)
        touch(bundle.entity_id, author_emp_id, f"stage {stage} item {item_index}: reopened")


@transaction.atomic
def record_gate(bundle, author_emp_id, stage, approver, approved_on, evidence_ref,
                decision=GateApproval.Decision.PROCEED, conditions=""):
    """Record (or re-record) a gate review with the approver's decision."""
    if decision == GateApproval.Decision.CONDITIONS and not conditions.strip():
        raise ValueError("Enter the conditions.")
    fields = {"approver": approver, "approved_on": approved_on, "evidence_ref": evidence_ref,
              "decision": decision, "conditions": conditions}
    existing = bundle.gate_for(stage)
    if existing:
        row = existing.new_version(author_emp_id, **fields)
    else:
        row = GateApproval.create(author_emp_id, initiative_entity=bundle.entity_id, stage=stage, **fields)
    touch(bundle.entity_id, author_emp_id, f"stage {stage} gate review: {decision}")
    return row
