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
    Workstream,
)


ALL = object()  # "every workstream" marker for scoped lookups


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
    workstreams: list = field(default_factory=list)
    ws_since: dict = field(default_factory=dict)  # workstream entity -> when it first existed

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

    # --- workstreams ---------------------------------------------------------------

    @property
    def ws_ids(self):
        return {w.entity_id for w in self.workstreams}

    def ws_name(self, entity):
        return next((w.name for w in self.workstreams if w.entity_id == entity), "")

    def own(self, items):
        """Items for the whole initiative: untagged, or tagged to a workstream that no longer exists."""
        ids = self.ws_ids
        return [i for i in items if i.workstream_entity not in ids]

    def scoped(self, items, ws_entity):
        """Items of one workstream; None means the whole initiative's own items."""
        if ws_entity is None:
            return self.own(items)
        return [i for i in items if i.workstream_entity == ws_entity]

    def ws_last_update(self, ws_entity):
        return next((u for u in self.updates if u.workstream_entity == ws_entity), None)

    @property
    def own_last_update(self):
        """Latest check-in not tagged to a current workstream (all of them when there are none)."""
        ids = self.ws_ids
        return next((u for u in self.updates if u.workstream_entity not in ids), None)

    def ws_checkin_base(self, ws):
        """When a workstream's check-in clock started: its last check-in, else when it was set up."""
        last = self.ws_last_update(ws.entity_id)
        if last:
            return last.posted_at
        starts = [d for d in (self.initiative.accepted_at, self.ws_since.get(ws.entity_id)) if d]
        return max(starts, default=None)

    def next_milestone(self, key_only=False, ws=ALL):
        pool = self.milestones if ws is ALL else self.scoped(self.milestones, ws)
        open_ms = [
            m for m in pool
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
    b.workstreams = list(_qs(Workstream, at).filter(initiative_entity=eid).order_by("order", "name"))
    if b.workstreams:
        for w in Workstream.objects.filter(entity_id__in=b.ws_ids).order_by("-valid_from"):
            b.ws_since[w.entity_id] = w.valid_from
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
