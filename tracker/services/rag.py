"""Derived RAG (spec §5 + DESIGN.md). Recomputed on every save; worst rule wins.

Thresholds come from ThresholdConfig (current rows) over settings.RAG_THRESHOLD_DEFAULTS.
Override auto-expires when the derived RAG becomes worse than it was when the override was set.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from core import clock
from tracker.models import (
    ControlImpact,
    Initiative,
    Milestone,
    Rag,
    RAG_ORDER,
    RagEvaluation,
    RagOverride,
    RiskItem,
    ThresholdConfig,
)
from tracker.services.bundle import Bundle, load_bundle


IN_PROGRESS_STATES = (
    ControlImpact.Readiness.IN_PROGRESS,
    ControlImpact.Readiness.SUBMITTED,
    ControlImpact.Readiness.SENT_BACK,
)


def _fmt(d):
    return f"{d:%b} {d.day}, {d.year}"


def _control_rules(bundle, t, bump, impacts=None):
    """Fire the control rules by days to each control's own due date (go-live unless linked)."""
    groups = {"red": [], "amber": []}
    for i in bundle.impacts if impacts is None else impacts:
        label, due = bundle.control_due(i)
        days = clock.days_until(due)
        if days is None:
            continue
        item = (days, i, label, bundle.needed_by_for(i) is None)
        if i.readiness == ControlImpact.Readiness.NOT_STARTED and days <= t["red_go_live_window_days"]:
            groups["red"].append(item)
        elif i.readiness in IN_PROGRESS_STATES and days <= t["amber_go_live_window_days"]:
            groups["amber"].append(item)
    for level, text, name in (("red", "Control not ready", Rag.RED), ("amber", "Control in progress", Rag.AMBER)):
        items = sorted(groups[level], key=lambda x: x[0])
        if not items:
            continue
        days, _i, _label, is_go_live = items[0]
        if is_go_live:
            when = f"go-live in {days}d" if days >= 0 else "go-live passed"
        else:
            when = f"due in {days}d" if days >= 0 else "past due"
        detail = "; ".join(
            f"{c.control_id} ({label})" for _d, i, label, _g in items if (c := bundle.control_for(i))
        )
        bump(name, f"{text}, {when}", detail)


def thresholds() -> dict:
    values = dict(settings.RAG_THRESHOLD_DEFAULTS)
    for row in ThresholdConfig.objects.current():
        if row.key in values:
            values[row.key] = row.value
    return values


@dataclass
class Evaluation:
    derived: str = Rag.GREEN
    effective: str = Rag.GREEN
    rule: str = "No rule fired"  # brief detail for the top rule (tooltip)
    chip: str = ""  # plain-words label for the top rule, e.g. "Go-live slipped 35d"
    fired: list = field(default_factory=list)  # [(level, chip, detail)]
    stale_days: int | None = None
    days_to_go_live: int | None = None
    override: RagOverride | None = None
    computed_at: object = None
    thresholds: dict = field(default_factory=dict)
    workstreams: list = field(default_factory=list)  # [(Workstream, Evaluation)], derived over its tagged items

    @property
    def is_stale(self):
        return self.stale_days is not None and self.stale_days > self.thresholds["amber_stale_days"]

    @property
    def early_warning_stale(self):
        return self.stale_days is not None and self.stale_days > self.thresholds["early_warning_stale_days"]


def stale_days(bundle: Bundle, ws=None) -> int | None:
    """Days since the last check-in: the workstream's, else the initiative's own."""
    if ws is not None:
        return clock.days_since(bundle.ws_checkin_base(ws))
    last = bundle.own_last_update
    if last:
        return clock.days_since(last.posted_at)
    if bundle.initiative.accepted_at:
        return clock.days_since(bundle.initiative.accepted_at)
    return None


def _milestone_rules(milestones, t, bump):
    for m in milestones:
        if not m.is_key or m.status == Milestone.Status.DONE:
            continue
        slip = m.slippage_days
        if slip is None:
            continue
        if slip > t["red_key_milestone_slip_days"]:
            bump(Rag.RED, f"Milestone slipped {slip}d", m.name)
        elif slip > t["amber_key_milestone_slip_days"]:
            bump(Rag.AMBER, f"Milestone slipped {slip}d", m.name)


def _risk_rules(risks, bump):
    for r in risks:
        if r.rating == RiskItem.Rating.HIGH and r.is_open:
            if r.target_date and r.target_date < clock.today():
                bump(Rag.RED, "High risk past due", r.title)
            else:
                bump(Rag.AMBER, "High risk open", r.title)


def _stale_rule(ev, last_update, t, bump):
    if ev.stale_days is None:
        return
    last = f"Last check-in {_fmt(clock.local_date(last_update.posted_at))}" if last_update else "No check-in yet"
    if ev.stale_days > t["red_stale_days"]:
        bump(Rag.RED, f"No check-in {ev.stale_days}d", last)
    elif ev.stale_days > t["amber_stale_days"]:
        bump(Rag.AMBER, f"No check-in {ev.stale_days}d", last)


def _finish(ev, worst):
    ev.derived = worst
    top = next((f for f in ev.fired if f[0] == ev.derived), None)
    ev.chip, ev.rule = (top[1], top[2]) if top else ("", "No rule fired")
    return ev


def _bumper(ev, worst):
    def bump(level, chip, detail):
        """chip: short plain-words label shown on pages; detail: one brief phrase for its tooltip."""
        ev.fired.append((level, chip, detail))
        if RAG_ORDER[level] > RAG_ORDER[worst[0]]:
            worst[0] = level

    return bump


def derive_workstream(bundle: Bundle, ws, t: dict | None = None) -> Evaluation:
    """The same rules over one workstream's tagged items; go-live slip uses its latest key milestone."""
    t = t or thresholds()
    ev = Evaluation(thresholds=t)
    if bundle.initiative.status in (Initiative.Status.CLOSED, Initiative.Status.ARCHIVED):
        return ev
    worst = [Rag.GREEN]
    bump = _bumper(ev, worst)
    eid = ws.entity_id
    milestones = bundle.scoped(bundle.milestones, eid)
    _milestone_rules(milestones, t, bump)

    keys = [m for m in milestones if m.is_key and (m.actual or m.forecast or m.planned)]
    latest = max(keys, key=lambda m: m.actual or m.forecast or m.planned, default=None)
    if latest and latest.status != Milestone.Status.DONE:
        slip = latest.slippage_days
        if slip is not None and slip > t["red_go_live_slip_days"]:
            bump(Rag.RED, f"Go-live slipped {slip}d", f"Versus plan {_fmt(latest.planned)}")

    _risk_rules(bundle.scoped(bundle.risks, eid), bump)
    _control_rules(bundle, t, bump, bundle.scoped(bundle.impacts, eid))
    ev.stale_days = stale_days(bundle, ws)
    _stale_rule(ev, bundle.ws_last_update(eid), t, bump)
    ev.effective = _finish(ev, worst[0]).derived
    return ev


def evaluate_workstream(bundle: Bundle, ws, t: dict | None = None) -> Evaluation:
    """Status of one workstream (no override; those stay at the initiative level)."""
    return derive_workstream(bundle, ws, t)


def derive(bundle: Bundle, t: dict | None = None) -> Evaluation:
    """Worst of the initiative's own untagged-item rules and every workstream's result."""
    t = t or thresholds()
    ev = Evaluation(thresholds=t)
    init = bundle.initiative
    if init.status in (Initiative.Status.CLOSED, Initiative.Status.ARCHIVED):
        return ev

    worst = [Rag.GREEN]
    bump = _bumper(ev, worst)

    _milestone_rules(bundle.own(bundle.milestones), t, bump)

    if init.baseline_go_live and init.target_go_live:
        slip = (init.target_go_live - init.baseline_go_live).days
        if slip > t["red_go_live_slip_days"]:
            bump(Rag.RED, f"Go-live slipped {slip}d", f"Versus baseline {_fmt(init.baseline_go_live)}")

    _risk_rules(bundle.own(bundle.risks), bump)

    ev.days_to_go_live = clock.days_until(init.target_go_live)
    _control_rules(bundle, t, bump, bundle.own(bundle.impacts))

    ev.stale_days = stale_days(bundle)
    _stale_rule(ev, bundle.own_last_update, t, bump)

    for ws in bundle.workstreams:
        wev = derive_workstream(bundle, ws, t)
        ev.workstreams.append((ws, wev))
        for level, chip, detail in wev.fired:
            bump(level, f"{ws.name}: {chip}", detail)

    return _finish(ev, worst[0])


def evaluate(bundle: Bundle, t: dict | None = None, persist: bool = True) -> Evaluation:
    """Derive, apply/expire the override, and (optionally) append a RagEvaluation row."""
    ev = derive(bundle, t)
    ov = bundle.override
    if ov and RAG_ORDER[ev.derived] > RAG_ORDER[ov.derived_at_set]:
        if persist and bundle.at is None:
            ov.new_version(
                "SYSTEM", cleared_at=timezone.now(), cleared_by=RagOverride.ClearedBy.AUTO
            )
        bundle.override = None
        ov = None
    ev.override = ov
    ev.effective = ov.value if ov else ev.derived
    ev.computed_at = timezone.now()
    if persist and bundle.at is None:
        RagEvaluation.objects.create(
            initiative_entity=bundle.entity_id,
            computed_at=ev.computed_at,
            derived=ev.derived,
            effective=ev.effective,
            rule_fired=(ev.chip or ev.rule)[:200],
            override_entity=ov.entity_id if ov else None,
            stale_days=ev.stale_days,
            details={"fired": ev.fired, "days_to_go_live": ev.days_to_go_live},
        )
    return ev


def recompute(initiative: Initiative) -> Evaluation:
    """Call after any save touching an initiative."""
    return evaluate(load_bundle(initiative))


def latest_logged(entity_id) -> RagEvaluation | None:
    return RagEvaluation.objects.filter(initiative_entity=entity_id).order_by("-computed_at").first()
