"""Load everything about one initiative, live or as of an instant, in a handful of queries."""

from dataclasses import dataclass, field
from datetime import datetime

from tracker.models import (
    Control,
    ControlImpact,
    CurationEvent,
    GateApproval,
    Initiative,
    Milestone,
    Overlay,
    Phase,
    RagOverride,
    RiskItem,
    StageCheck,
    Update,
)


def _qs(model, at):
    return model.objects.as_of(at) if at else model.objects.current()


@dataclass
class Bundle:
    initiative: Initiative
    phases: list = field(default_factory=list)
    milestones: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    impacts: list = field(default_factory=list)
    controls_by_entity: dict = field(default_factory=dict)
    overlays: list = field(default_factory=list)
    updates: list = field(default_factory=list)
    override: RagOverride | None = None
    checks: list = field(default_factory=list)
    gates: list = field(default_factory=list)
    last_curated: datetime | None = None
    at: datetime | None = None

    @property
    def entity_id(self):
        return self.initiative.entity_id

    @property
    def last_update(self):
        return self.updates[0] if self.updates else None

    @property
    def highlights(self):
        return [o for o in self.overlays if o.type == Overlay.Type.HIGHLIGHT]

    @property
    def commentary(self):
        return [o for o in self.overlays if o.type != Overlay.Type.HIGHLIGHT]

    def key_milestones(self):
        return [m for m in self.milestones if m.is_key]

    def next_milestone(self, key_only=False):
        open_ms = [
            m for m in self.milestones
            if m.status != Milestone.Status.DONE and (m.forecast or m.planned) and (m.is_key or not key_only)
        ]
        return min(open_ms, key=lambda m: m.forecast or m.planned, default=None)

    def current_stage(self) -> int:
        for ph in self.phases:
            if ph.name == self.initiative.phase:
                return ph.stage
        status = self.initiative.status
        if status == Initiative.Status.PROPOSED:
            return 0
        if status in (Initiative.Status.CLOSED, Initiative.Status.ARCHIVED):
            return 6
        return 2

    def checks_for(self, stage: int) -> dict:
        return {c.item_index: c for c in self.checks if c.stage == stage}

    def gate_for(self, stage: int):
        return next((g for g in self.gates if g.stage == stage), None)

    def control_for(self, impact):
        return self.controls_by_entity.get(impact.control_entity)

    def needed_by_for(self, impact):
        """The current key milestone a control is linked to, else None (meaning go-live)."""
        if not impact.needed_by_milestone:
            return None
        return next(
            (m for m in self.milestones if m.entity_id == impact.needed_by_milestone and m.is_key), None
        )

    def control_due(self, impact):
        """(label, date) a control must be ready by: its key milestone, else go-live."""
        m = self.needed_by_for(impact)
        if m is None:
            return "Go-live", self.initiative.target_go_live
        return m.name, m.actual or m.forecast or m.planned


def load_bundle(initiative: Initiative, at: datetime | None = None) -> Bundle:
    eid = initiative.entity_id
    b = Bundle(initiative=initiative, at=at)
    b.phases = list(_qs(Phase, at).filter(initiative_entity=eid).order_by("order"))
    b.milestones = list(_qs(Milestone, at).filter(initiative_entity=eid).order_by("planned"))
    b.risks = list(_qs(RiskItem, at).filter(initiative_entity=eid).order_by("-rating", "title"))
    b.impacts = list(_qs(ControlImpact, at).filter(initiative_entity=eid))
    control_ids = {i.control_entity for i in b.impacts}
    b.controls_by_entity = {c.entity_id: c for c in _qs(Control, at).filter(entity_id__in=control_ids)}
    b.overlays = list(_qs(Overlay, at).filter(initiative_entity=eid).order_by("order", "valid_from"))
    updates = Update.objects.filter(initiative_entity=eid)
    if at:
        updates = updates.filter(posted_at__lte=at)
    b.updates = list(updates.order_by("-posted_at"))
    overrides = _qs(RagOverride, at).filter(initiative_entity=eid, cleared_at__isnull=True)
    b.override = overrides.order_by("-effective_from").first()
    b.checks = list(_qs(StageCheck, at).filter(initiative_entity=eid))
    b.gates = list(_qs(GateApproval, at).filter(initiative_entity=eid))
    events = CurationEvent.objects.filter(initiative_entity=eid)
    if at:
        events = events.filter(at__lte=at)
    last = events.order_by("-at").first()
    b.last_curated = last.at if last else None
    return b


def load_bundles(initiatives, at=None):
    return [load_bundle(i, at) for i in initiatives]
