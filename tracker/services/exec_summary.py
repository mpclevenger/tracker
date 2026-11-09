"""Builds the exec summary context, live or from a snapshot (DESIGN.md → Exec summary)."""

from dataclasses import dataclass, field
from datetime import date, datetime

from core import clock
from meetings import services as meetings_services
from tracker.models import Initiative, RAG_ORDER, RiskItem, Snapshot
from tracker.services import rag, snapshots
from tracker.services.bundle import Bundle, load_bundles
from tracker.visibility import initiatives_for

CAP = lambda s: s[0].upper() + s[1:] if s else s  # noqa: E731



def _control_label(bundle, impact) -> str:
    c = bundle.control_for(impact)
    return f"{c.control_id} {c.name} · {impact.get_readiness_display()}" if c else impact.get_readiness_display()

@dataclass
class Tile:
    bundle: Bundle | None
    entity_id: object
    name: str
    owner_emp_id: str
    status: str
    phase: str
    effective: str
    derived: str
    rule: str
    stale_days: int | None
    is_stale: bool
    override: object  # RagOverride | dict | None
    highlights: list
    next_milestone: tuple | None  # (name, date)
    go_live: date | None
    last_update_at: datetime | None
    controls_ready: int
    controls_total: int
    prior: str | None  # effective RAG at the comparison snapshot, or None
    closed_at: datetime | None = None
    closure: str = ""
    open_high_risks: int = 0
    controls: list = field(default_factory=list)  # [(label, readiness)] for the go-live squares
    chip: str = ""  # plain-words label of the top RAG rule
    as_of: date | None = None  # snapshot date when frozen; None = today

    @property
    def comparison(self):
        """('up'|'down'|'same'|'new', text)"""
        if self.prior is None:
            return ("new", "New since the last snapshot")
        if self.prior == self.effective:
            return ("same", f"— Unchanged ({CAP(self.effective)})")
        worse = RAG_ORDER[self.effective] > RAG_ORDER[self.prior]
        return ("up" if worse else "down",
                f"{'▲ Increased' if worse else '▼ Decreased'} to {CAP(self.effective)} from {CAP(self.prior)}")

    def _ref(self):
        return self.as_of or clock.today()

    @property
    def days_to_go_live(self):
        return clock.days_between(self._ref(), self.go_live) if self.go_live else None

    @property
    def days_since_update(self):
        return clock.days_between(self.last_update_at, self._ref()) if self.last_update_at else None

    @property
    def next_milestone_days(self):
        return clock.days_between(self._ref(), self.next_milestone[1]) if self.next_milestone else None


@dataclass
class ExecContext:
    mode: str  # live | snapshot
    snapshot: Snapshot | None
    comparison_snapshot: Snapshot | None
    comparison_label: str
    counts: dict
    worse: list = field(default_factory=list)
    better: list = field(default_factory=list)
    attention: list = field(default_factory=list)
    on_track: list = field(default_factory=list)
    upcoming: list = field(default_factory=list)
    high_by_initiative: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    closed_since: list = field(default_factory=list)
    proposed: list = field(default_factory=list)
    timeline_bundles: list = field(default_factory=list)
    evaluations: dict = field(default_factory=dict)
    overdue_followups: list = field(default_factory=list)  # [(tile, n)], live mode only

    prev_counts: dict = field(default_factory=dict)  # like-for-like counts at the comparison snapshot
    movers: dict = field(default_factory=dict)  # {rag: [tile]} initiatives whose RAG changed, by current RAG

    def delta(self, rag):
        """Change in this RAG's count since the comparison snapshot, comparing the same initiatives."""
        if not self.comparison_snapshot:
            return None
        return self.counts.get(rag, 0) - self.prev_counts.get(rag, 0)

    @property
    def n_high(self):
        return sum(n for _, n in self.high_by_initiative)


def _live_tile(b: Bundle, ev, actor, prior_rows) -> Tile:
    layers = actor.visible_layers(b.entity_id)
    hl = [o.text for o in b.highlights if o.visibility in layers][:3]
    nm = b.next_milestone()
    prior = prior_rows.get(b.entity_id)
    init = b.initiative
    return Tile(
        bundle=b, entity_id=b.entity_id, name=init.name, owner_emp_id=init.owner_emp_id, status=init.status,
        phase=init.phase, effective=ev.effective, derived=ev.derived, rule=ev.rule, stale_days=ev.stale_days,
        is_stale=ev.is_stale, override=ev.override, highlights=hl,
        next_milestone=(nm.name, nm.forecast or nm.planned) if nm else None, go_live=init.target_go_live,
        last_update_at=b.last_update.posted_at if b.last_update else None,
        controls_ready=sum(1 for i in b.impacts if i.readiness == "ready"), controls_total=len(b.impacts),
        controls=[(_control_label(b, i), i.readiness) for i in b.impacts],
        chip=ev.chip,
        prior=prior["effective"] if prior else None, closed_at=init.closed_at, closure=init.closure_control_outcome,
        open_high_risks=sum(1 for r in b.risks if r.rating == RiskItem.Rating.HIGH and r.is_open and r.visibility in layers),
    )


def _frozen_tile(eid, row, prior_rows, summary, as_of: date) -> Tile:
    prior = prior_rows.get(eid)
    p = lambda s: datetime.fromisoformat(s) if s else None  # noqa: E731
    nm = row.get("next_milestone")
    amber_stale = summary.get("thresholds", {}).get("amber_stale_days", 30)
    stale_days = row.get("stale_days")
    tile = Tile(
        bundle=None, entity_id=eid, name=row["name"], owner_emp_id=row["owner_emp_id"], status=row["status"],
        phase=row["phase"], effective=row["effective"], derived=row["derived"], rule=row["rule"],
        stale_days=stale_days, is_stale=stale_days is not None and stale_days > amber_stale,
        override=row.get("override"), highlights=row.get("highlights", []),
        next_milestone=(nm["name"], date.fromisoformat(nm["date"])) if nm and nm.get("date") else None,
        go_live=date.fromisoformat(row["target_go_live"]) if row.get("target_go_live") else None,
        last_update_at=p(row.get("last_update_at")), controls_ready=row.get("controls_ready", 0),
        controls_total=row.get("controls_total", 0), prior=prior["effective"] if prior else None,
        closed_at=p(row.get("closed_at")), closure=row.get("closure_control_outcome", ""),
        open_high_risks=row.get("open_high_risks", 0), chip=row.get("chip", ""),
    )
    tile.as_of = as_of
    return tile


def build(actor, snapshot: Snapshot | None = None, followups: bool = True) -> ExecContext:
    visible = list(initiatives_for(actor).exclude(status=Initiative.Status.ARCHIVED))
    if snapshot:
        comparison = snapshots.last_snapshot(before=snapshot.taken_at)
        rows = snapshots.rows_by_entity(snapshot)
        prior_rows = snapshots.rows_by_entity(comparison) if comparison else {}
        counts = dict(snapshot.frozen_summary)
        as_of = clock.local_date(snapshot.taken_at)
        tiles = [_frozen_tile(i.entity_id, rows[i.entity_id], prior_rows, counts, as_of) for i in visible if i.entity_id in rows]
        ctx = ExecContext("snapshot", snapshot, comparison, comparison.label if comparison else "", counts)
        ref_at = comparison.taken_at if comparison else None
        upto = snapshot.taken_at
    else:
        comparison = snapshots.last_snapshot()
        prior_rows = snapshots.rows_by_entity(comparison) if comparison else {}
        bundles = load_bundles(visible)
        t = rag.thresholds()
        evals = {b.entity_id: rag.evaluate(b, t, persist=False) for b in bundles}
        tiles = [_live_tile(b, evals[b.entity_id], actor, prior_rows) for b in bundles]
        active = [tl for tl in tiles if tl.status == Initiative.Status.ACTIVE]
        counts = {
            "active": len(active),
            "green": sum(1 for tl in active if tl.effective == "green"),
            "amber": sum(1 for tl in active if tl.effective == "amber"),
            "red": sum(1 for tl in active if tl.effective == "red"),
        }
        ctx = ExecContext("live", None, comparison, comparison.label if comparison else "", counts)
        ctx.evaluations = evals
        ctx.timeline_bundles = [b for b in bundles if b.initiative.status not in (Initiative.Status.CLOSED,)]
        ref_at = comparison.taken_at if comparison else None
        upto = None

    active = [tl for tl in tiles if tl.status == Initiative.Status.ACTIVE]
    for tl in active:
        if tl.prior and tl.prior != tl.effective:
            (ctx.worse if RAG_ORDER[tl.effective] > RAG_ORDER[tl.prior] else ctx.better).append(tl)
    ctx.prev_counts = {"green": 0, "amber": 0, "red": 0}
    ctx.movers = {"green": [], "amber": [], "red": []}
    for tl in active:
        ctx.prev_counts[tl.prior or tl.effective] += 1  # new since the snapshot: counted in its current color
        if tl.prior and tl.prior != tl.effective:
            ctx.movers[tl.effective].append(tl)
    ctx.attention = sorted([tl for tl in active if tl.effective != "green"], key=lambda tl: -RAG_ORDER[tl.effective])
    ctx.on_track = [tl for tl in active if tl.effective == "green"]
    ctx.upcoming = sorted(
        [tl for tl in active if tl.days_to_go_live is not None and 0 <= tl.days_to_go_live <= 90],
        key=lambda tl: tl.go_live,
    )
    ctx.high_by_initiative = [(tl, tl.open_high_risks) for tl in active if tl.open_high_risks]
    ctx.stale = [tl for tl in active if tl.is_stale]
    if ctx.mode == "live" and followups:
        per_initiative = {}
        for f in meetings_services.overdue_followups([tl.entity_id for tl in active]):
            per_initiative[f.initiative_entity] = per_initiative.get(f.initiative_entity, 0) + 1
        ctx.overdue_followups = [(tl, per_initiative[tl.entity_id]) for tl in active if tl.entity_id in per_initiative]
    ctx.closed_since = [
        tl for tl in tiles
        if tl.status == Initiative.Status.CLOSED and tl.closed_at
        and (ref_at is None or tl.closed_at > ref_at) and (upto is None or tl.closed_at <= upto)
    ]
    if not actor.is_senior_management:
        ctx.proposed = [tl for tl in tiles if tl.status == Initiative.Status.PROPOSED]
    return ctx
