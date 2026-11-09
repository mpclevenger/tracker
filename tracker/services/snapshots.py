"""Immutable snapshots of the exec/audit-visible state."""

from django.db import transaction
from django.utils import timezone

from core import clock
from tracker import lifecycle as rules
from tracker.models import Initiative, Snapshot, SnapshotRow, Visibility
from tracker.services import lifecycle, rag
from tracker.services.bundle import load_bundle


def last_snapshot(before=None) -> Snapshot | None:
    qs = Snapshot.objects.all()
    if before is not None:
        qs = qs.filter(taken_at__lt=before)
    return qs.order_by("-taken_at").first()


def _iso(d):
    return d.isoformat() if d else None


def _stage(st) -> dict:
    gate = st.gate
    return {
        "number": st.number, "name": st.name, "requirement": st.requirement,
        "done": st.done, "total": st.total, "complete": st.complete, "is_current": st.is_current,
        "signoff": ({"decision": gate.decision, "approver": gate.approver, "on": _iso(gate.approved_on),
                     "conditions": gate.conditions} if gate else None),
    }


def _control(bundle, impact) -> dict:
    c = bundle.control_for(impact)
    return {"control_id": c.control_id if c else "", "name": c.name if c else "", "readiness": impact.readiness}


def frozen_row(bundle, ev) -> dict:
    """Exec/audit-visible state only. Internal and owner-only items are never frozen."""
    init = bundle.initiative
    nm = bundle.next_milestone()
    return {
        "name": init.name,
        "owner_emp_id": init.owner_emp_id,
        "status": init.status,
        "phase": init.phase,
        "stage": bundle.current_stage(),
        "target_go_live": _iso(init.target_go_live),
        "baseline_go_live": _iso(init.baseline_go_live),
        "derived": ev.derived,
        "effective": ev.effective,
        "rule": ev.rule,
        "chip": ev.chip,
        "override": (
            {"value": ev.override.value, "reason": ev.override.reason, "by": ev.override.author_emp_id,
             "at": ev.override.effective_from.isoformat(), "review_by": _iso(ev.override.review_by)}
            if ev.override else None
        ),
        "stale_days": ev.stale_days,
        "next_milestone": {"name": nm.name, "date": _iso(nm.forecast or nm.planned)} if nm else None,
        "last_update_at": bundle.last_update.posted_at.isoformat() if bundle.last_update else None,
        "highlights": [o.text for o in bundle.highlights if o.visibility == Visibility.EXEC][:3],
        "commentary": [o.text for o in bundle.commentary if o.visibility == Visibility.EXEC],
        "controls_ready": sum(1 for i in bundle.impacts if i.readiness == "ready"),
        "controls_total": len(bundle.impacts),
        "open_high_risks": sum(
            1 for r in bundle.risks if r.rating == "H" and r.is_open and r.visibility == Visibility.EXEC
        ),
        "oversight": init.oversight,
        "oversight_overridden": bool(init.oversight_override),
        "stages": [_stage(st) for st in lifecycle.stage_statuses(bundle)],
        "controls": [_control(bundle, i) for i in bundle.impacts],
        "closed_at": init.closed_at.isoformat() if init.closed_at else None,
        "closure_control_outcome": init.closure_control_outcome,
    }


@transaction.atomic
def take_snapshot(author_emp_id, label, note="") -> Snapshot:
    inits = list(Initiative.objects.current().exclude(status=Initiative.Status.PROPOSED))
    t = rag.thresholds()
    rows, active_effective = [], []
    counts = {"golive90": 0, "high_open": 0, "stale": 0}
    for init in inits:
        b = load_bundle(init)
        ev = rag.evaluate(b, t, persist=False)
        row = frozen_row(b, ev)
        rows.append((init.entity_id, row))
        if init.status == Initiative.Status.ACTIVE:
            active_effective.append(ev.effective)
            to_go = clock.days_until(init.target_go_live)
            if to_go is not None and 0 <= to_go <= 90:
                counts["golive90"] += 1
            counts["high_open"] += row["open_high_risks"]
            if ev.is_stale:
                counts["stale"] += 1
    snap = Snapshot.objects.create(
        taken_at=timezone.now(),
        taken_by=author_emp_id,
        label=label,
        note=note,
        frozen_summary={
            "active": len(active_effective),
            "green": active_effective.count("green"),
            "amber": active_effective.count("amber"),
            "red": active_effective.count("red"),
            **counts,
            "thresholds": t,
            "gate_matrix_version": rules.gate_matrix_version(),
            "guidance_version": lifecycle.guidance_version(),
        },
    )
    for eid, row in rows:
        SnapshotRow.objects.create(snapshot=snap, initiative_entity=eid, frozen_json=row)
    return snap


def rows_by_entity(snapshot: Snapshot) -> dict:
    return {r.initiative_entity: r.frozen_json for r in snapshot.rows.all()}
