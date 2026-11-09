"""Shared factories and a frozen clock for the service-layer tests."""

import itertools
from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from core import clock
from core.auth.actor import resolve_actor
from core.models import Role, RoleAssignment
from tracker.models import (
    Control,
    ControlImpact,
    Initiative,
    Milestone,
    Phase,
    RiskItem,
    Update,
)
from tracker.services import lifecycle
from tracker.services.bundle import load_bundle

AUTH = "AUTH"
TR = "TR1"
TODAY = date(2026, 11, 5)
NOON = datetime(2026, 11, 5, 12, 0, tzinfo=clock.NY)

_seq = itertools.count(1)


class FakeClock:
    """Frozen NY wall clock; every read ticks one millisecond so event order stays strict."""

    def __init__(self, start=NOON):
        self.t = start

    def now(self):
        value = self.t
        self.t += timedelta(milliseconds=1)
        return value

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def frozen_clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(clock, "now", fc.now)
    monkeypatch.setattr(timezone, "now", fc.now)
    return fc


def d(offset: int) -> date:
    """Date `offset` days from the frozen today."""
    return TODAY + timedelta(days=offset)


def at(offset: int) -> datetime:
    """Noon New York, `offset` days from the frozen today."""
    return NOON + timedelta(days=offset)


def active_initiative(**overrides) -> Initiative:
    fields = dict(
        name="Alpha",
        owner_emp_id="OWN1",
        status=Initiative.Status.ACTIVE,
        phase="Build",
        accepted_at=at(-1),
    )
    fields.update(overrides)
    return Initiative.create(AUTH, **fields)


def intake_stage_initiative(**overrides) -> Initiative:
    """Active initiative whose current phase is stage 0, so no past gate can be incomplete."""
    init = active_initiative(phase="Intake", **overrides)
    add_phase(init, "Intake", stage=0, order=0)
    return init


def add_phase(init, name="Build", stage=2, order=0, **dates) -> Phase:
    return Phase.create(
        AUTH, initiative_entity=init.entity_id, name=name, order=order, stage=stage, **dates
    )


def add_milestone(init, planned=None, forecast=None, is_key=True, status="on_track", name="Build exit", **kw):
    return Milestone.create(
        AUTH,
        initiative_entity=init.entity_id,
        name=name,
        planned=planned,
        forecast=forecast,
        is_key=is_key,
        status=status,
        **kw,
    )


def add_risk(init, rating="H", status="open", target_date=None, visibility="exec", type="risk", title=None):
    return RiskItem.create(
        AUTH,
        initiative_entity=init.entity_id,
        type=type,
        title=title or f"Risk {next(_seq)}",
        rating=rating,
        status=status,
        target_date=target_date,
        visibility=visibility,
    )


def add_control(control_id=None) -> Control:
    n = next(_seq)
    return Control.create(AUTH, control_id=control_id or f"CTL-{n}", name=f"Control {n}")


def add_impact(init, readiness="ready", control=None, impact_type="modified") -> ControlImpact:
    control = control or add_control()
    return ControlImpact.create(
        AUTH,
        initiative_entity=init.entity_id,
        control_entity=control.entity_id,
        impact_type=impact_type,
        readiness=readiness,
    )


def post_update(init, days_ago=0, text="Status update") -> Update:
    return Update.objects.create(
        initiative_entity=init.entity_id, author_emp_id="OWN1", posted_at=at(-days_ago), text=text
    )


def bundle_of(init):
    return load_bundle(Initiative.objects.get_current(init.entity_id))


def make_actor(emp_id, role):
    RoleAssignment.create(AUTH, emp_id=emp_id, role=role)
    return resolve_actor(emp_id)


def tech_risk_actor(emp_id="TRA"):
    return make_actor(emp_id, Role.TECH_RISK)


def cto_actor(emp_id="CTO1"):
    return make_actor(emp_id, Role.SENIOR_MANAGEMENT)


def complete_stage(init, stage, approver="Head of Tech Risk"):
    """Accept every checklist item of `stage` and record its gate approval."""
    page = lifecycle.current_guidance()[stage]
    for idx in range(len(page.checklist)):
        lifecycle.accept_item(bundle_of(init), TR, stage, idx, evidence_ref=f"evidence-{stage}-{idx}")
    lifecycle.record_gate(bundle_of(init), TR, stage, approver, d(-1), f"approval-{stage}")
