"""Load one initiative from a YAML file (see initiative_template.yaml).

    python scripts/load_initiative.py path/to/initiative.yaml

Creates the owner, delegates, any missing controls, and the initiative with its phases, milestones,
risks, control impacts, updates, Tech Risk overlay and lifecycle confirmations. Refuses to run if an
initiative with the same name already exists. Needs the app's environment (DATABASE_URL etc.) and PyYAML.
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
from django.apps import apps  # noqa: E402

if not apps.ready:
    django.setup()

import yaml  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.models import Person  # noqa: E402
from tracker.models import (  # noqa: E402
    Control,
    ControlImpact,
    CurationEvent,
    Delegation,
    GateApproval,
    Initiative,
    Milestone,
    Overlay,
    Phase,
    RiskItem,
    StageCheck,
    Update,
    Visibility,
)
from tracker.lifecycle import QUESTION_KEYS  # noqa: E402
from tracker.services import lifecycle, rag  # noqa: E402

SYSTEM = "LOADER"


def _date(v):
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _at(v):
    d = _date(v)
    return timezone.make_aware(datetime.combine(d, datetime.min.time().replace(hour=12))) if d else None


def _person(emp_id, name=None):
    if not emp_id:
        return None
    Person.objects.get_or_create(pk=emp_id, defaults={"display_name": name or emp_id})
    return emp_id


def _control(spec):
    cid = spec["control_id"]
    existing = Control.objects.current().filter(control_id=cid).first()
    if existing:
        return existing
    if not spec.get("name"):
        raise SystemExit(f"Control {cid} is not in the library; add name/framework/control_owner to create it.")
    return Control.create(
        SYSTEM, control_id=cid, name=spec["name"], framework=spec.get("framework", ""),
        control_owner=spec.get("control_owner", ""),
    )


@transaction.atomic
def load(doc: dict, out=print) -> Initiative:
    spec = doc["initiative"]
    if Initiative.objects.current().filter(name=spec["name"]).exists():
        raise SystemExit(f'An initiative named "{spec["name"]}" already exists; edit it in the app instead.')
    if not lifecycle.current_guidance():
        lifecycle.seed_guidance(SYSTEM)
        out("Lifecycle guidance v1.0 seeded.")

    owner = _person(spec["owner"]["emp_id"], spec["owner"].get("display_name"))
    status = spec.get("status", "proposed")
    init = Initiative.create(
        SYSTEM,
        name=spec["name"],
        description=spec.get("description", "").strip(),
        sponsor=spec.get("sponsor", ""),
        owner_emp_id=owner,
        status=status,
        phase=spec.get("current_phase", "Intake" if status == "proposed" else ""),
        business_driver=spec.get("business_driver", "").strip(),
        why_it_matters=spec.get("why_it_matters", "").strip(),
        expected_systems=spec.get("expected_systems", ""),
        target_go_live=_date(spec.get("target_go_live")),
        baseline_go_live=_date(spec.get("baseline_go_live")) if status != "proposed" else None,
        submitted_at=_at(spec.get("submitted_on")) or timezone.now(),
        accepted_at=_at(spec.get("accepted_on")) if status != "proposed" else None,
        **{key: bool((spec.get("oversight") or {}).get(key)) for key in QUESTION_KEYS},
    )
    eid = init.entity_id
    for d in spec.get("delegates") or []:
        Delegation.create(SYSTEM, initiative_entity=eid, delegate_emp_id=_person(d["emp_id"], d.get("display_name")))

    for order, ph in enumerate(doc.get("phases") or []):
        ps, pe = _date(ph.get("planned_start")), _date(ph.get("planned_end"))
        Phase.create(
            SYSTEM, initiative_entity=eid, name=ph["name"], order=order, stage=int(ph.get("stage", 2)),
            planned_start=ps, planned_end=pe,
            forecast_start=_date(ph.get("forecast_start")) or ps, forecast_end=_date(ph.get("forecast_end")) or pe,
            actual_start=_date(ph.get("actual_start")), actual_end=_date(ph.get("actual_end")),
        )
    for m in doc.get("milestones") or []:
        Milestone.create(
            SYSTEM, initiative_entity=eid, name=m["name"], planned=_date(m.get("planned")),
            forecast=_date(m.get("forecast")) or _date(m.get("planned")), actual=_date(m.get("actual")),
            pct_complete=int(m.get("pct_complete", 0)), status=m.get("status", "planned"),
            is_key=bool(m.get("key", False)), note=m.get("note", ""),
        )
    for r in doc.get("risks") or []:
        RiskItem.create(
            SYSTEM, initiative_entity=eid, type=r["type"], title=r["title"], rating=r["rating"],
            owner=r.get("owner", ""), target_date=_date(r.get("target_date")), status=r.get("status", "open"),
            mitigation=r.get("mitigation", ""), source=r.get("source", "owner"),
            visibility=r.get("visibility", Visibility.EXEC),
        )
    for c in doc.get("control_impacts") or []:
        ControlImpact.create(
            SYSTEM, initiative_entity=eid, control_entity=_control(c).entity_id, impact_type=c["impact_type"],
            compensating_control=c.get("compensating_control", ""), readiness=c.get("readiness", "not_started"),
            evidence_link=c.get("evidence_link", ""),
        )
    for u in doc.get("updates") or []:
        Update.objects.create(initiative_entity=eid, author_emp_id=owner, posted_at=_at(u["date"]), text=u["text"])

    tr = doc.get("tech_risk") or {}
    if tr:
        author = _person(tr.get("author_emp_id", "tech-risk"), tr.get("author_name"))
        if tr.get("commentary"):
            Overlay.create(author, initiative_entity=eid, type=Overlay.Type.COMMENTARY, text=tr["commentary"].strip(),
                           visibility=Visibility.EXEC, order=0)
        for i, h in enumerate(tr.get("highlights") or []):
            Overlay.create(author, initiative_entity=eid, type=Overlay.Type.HIGHLIGHT, text=h, visibility=Visibility.EXEC, order=i)
        for i, n in enumerate(tr.get("internal_notes") or []):
            Overlay.create(author, initiative_entity=eid, type=Overlay.Type.INTERNAL_NOTE, text=n,
                           visibility=Visibility.INTERNAL, order=i)
        guidance = lifecycle.current_guidance()
        for ck in tr.get("stage_checks") or []:
            page = guidance[int(ck["stage"])]
            idx = int(ck["item"])
            StageCheck.create(
                author, initiative_entity=eid, stage=int(ck["stage"]), item_index=idx,
                item_text=page.checklist[idx] if idx < len(page.checklist) else "",
                basis=ck.get("basis", "attested"), evidence_ref=str(ck.get("evidence", "")),
                guidance_version=page.version_label, valid_from=_at(ck["date"]) or timezone.now(),
            )
        for g in tr.get("gate_approvals") or []:
            GateApproval.create(
                author, initiative_entity=eid, stage=int(g["stage"]), approver=g["approver"],
                approved_on=_date(g["date"]), evidence_ref=str(g.get("evidence", "")),
                decision=g.get("decision", "proceed"), conditions=g.get("conditions", ""),
                valid_from=_at(g["date"]) or timezone.now(),
            )
        CurationEvent.objects.create(initiative_entity=eid, author_emp_id=author, at=timezone.now(), what="loaded from file")

    ev = rag.recompute(init)
    out(f'Loaded "{init.name}" ({status}); RAG {ev.effective} — {ev.rule}')
    return init


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1]) as fh:
        load(yaml.safe_load(fh))
