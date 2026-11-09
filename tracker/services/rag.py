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


def _control_refs(bundle, impacts):
    return ", ".join(bundle.control_for(i).control_id for i in impacts if bundle.control_for(i))


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

    @property
    def is_stale(self):
        return self.stale_days is not None and self.stale_days > self.thresholds["amber_stale_days"]

    @property
    def early_warning_stale(self):
        return self.stale_days is not None and self.stale_days > self.thresholds["early_warning_stale_days"]


def stale_days(bundle: Bundle) -> int | None:
    if bundle.last_update:
        return clock.days_since(bundle.last_update.posted_at)
    if bundle.initiative.accepted_at:
        return clock.days_since(bundle.initiative.accepted_at)
    return None


def derive(bundle: Bundle, t: dict | None = None) -> Evaluation:
    t = t or thresholds()
    ev = Evaluation(thresholds=t)
    init = bundle.initiative
    if init.status in (Initiative.Status.CLOSED, Initiative.Status.ARCHIVED):
        return ev

    worst = [Rag.GREEN]

    def bump(level, chip, detail):
        """chip: short plain-words label shown on pages; detail: one brief phrase for its tooltip."""
        ev.fired.append((level, chip, detail))
        if RAG_ORDER[level] > RAG_ORDER[worst[0]]:
            worst[0] = level

    for m in bundle.milestones:
        if not m.is_key or m.status == Milestone.Status.DONE:
            continue
        slip = m.slippage_days
        if slip is None:
            continue
        if slip > t["red_key_milestone_slip_days"]:
            bump(Rag.RED, f"Milestone slipped {slip}d", m.name)
        elif slip > t["amber_key_milestone_slip_days"]:
            bump(Rag.AMBER, f"Milestone slipped {slip}d", m.name)

    if init.baseline_go_live and init.target_go_live:
        slip = (init.target_go_live - init.baseline_go_live).days
        if slip > t["red_go_live_slip_days"]:
            bump(Rag.RED, f"Go-live slipped {slip}d", f"Versus baseline {_fmt(init.baseline_go_live)}")

    for r in bundle.risks:
        if r.rating == RiskItem.Rating.HIGH and r.is_open:
            if r.target_date and r.target_date < clock.today():
                bump(Rag.RED, "High risk past due", r.title)
            else:
                bump(Rag.AMBER, "High risk open", r.title)

    to_go = clock.days_until(init.target_go_live)
    ev.days_to_go_live = to_go
    if to_go is not None:
        when = f"go-live in {to_go}d" if to_go >= 0 else "go-live passed"
        not_started = [i for i in bundle.impacts if i.readiness == ControlImpact.Readiness.NOT_STARTED]
        underway = [i for i in bundle.impacts if i.readiness in IN_PROGRESS_STATES]
        if to_go <= t["red_go_live_window_days"] and not_started:
            bump(Rag.RED, f"Control not ready, {when}", _control_refs(bundle, not_started))
        if to_go <= t["amber_go_live_window_days"] and underway:
            bump(Rag.AMBER, f"Control in progress, {when}", _control_refs(bundle, underway))

    ev.stale_days = stale_days(bundle)
    if ev.stale_days is not None:
        last = f"Last check-in {_fmt(clock.local_date(bundle.last_update.posted_at))}" if bundle.last_update else "No check-in yet"
        if ev.stale_days > t["red_stale_days"]:
            bump(Rag.RED, f"No check-in {ev.stale_days}d", last)
        elif ev.stale_days > t["amber_stale_days"]:
            bump(Rag.AMBER, f"No check-in {ev.stale_days}d", last)

    ev.derived = worst[0]
    top = next((f for f in ev.fired if f[0] == ev.derived), None)
    ev.chip, ev.rule = (top[1], top[2]) if top else ("", "No rule fired")
    return ev


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
