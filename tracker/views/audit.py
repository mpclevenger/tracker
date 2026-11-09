"""Views: audit home (point-in-time pickers) and field-level initiative history."""

from datetime import date, timedelta

from django.core.exceptions import FieldDoesNotExist
from django.shortcuts import get_object_or_404, render

from core import clock
from core.auth.decorators import forbidden
from core.models import Person
from core.versioning import field_history
from meetings.models import FollowUp, Meeting, MeetingInitiative
from tracker.models import (
    Control,
    ControlImpact,
    Delegation,
    GateApproval,
    Initiative,
    Milestone,
    Overlay,
    OversightLevel,
    Phase,
    RagOverride,
    RiskItem,
    Snapshot,
    StageCheck,
)
from tracker.lifecycle import REQUIREMENT_LABELS
from tracker.views.common import get_initiative

LAYERED_MODELS = (Overlay, RiskItem, ControlImpact)
HISTORY_MODELS = (Initiative, Phase, Milestone, RiskItem, ControlImpact, Overlay, RagOverride, StageCheck,
                  GateApproval, Delegation, Meeting, FollowUp)
MODEL_LABELS = {
    Initiative: "Initiative", Phase: "Phase", Milestone: "Milestone", RiskItem: "Risk item",
    ControlImpact: "Control impact", Overlay: "Note", RagOverride: "RAG override",
    StageCheck: "Stage item", GateApproval: "Stage sign-off", Delegation: "Delegation",
    Meeting: "Meeting", FollowUp: "Follow-up",
}


def _audit_actor_or_403(request):
    a = request.actor
    if not (a.is_tech_risk or a.is_auditor):
        return forbidden(request)
    return None


# --- Audit home ---------------------------------------------------------------------------------


def audit_home(request):
    """Point-in-time pickers: a snapshot or an as-of date, both opening the exec summary."""
    if (denied := _audit_actor_or_403(request)):
        return denied
    return render(request, "tracker/audit_home.html", {
        "nav": "audit",
        "snapshots": Snapshot.objects.order_by("-taken_at"),
        "today": clock.today(),
    })


def snapshot_record(request, pk):
    """Everything a snapshot froze: header versions and, per initiative, status, oversight, stages and controls."""
    if (denied := _audit_actor_or_403(request)):
        return denied
    snap = get_object_or_404(Snapshot, pk=pk)
    rows = sorted((r.frozen_json for r in snap.rows.all()), key=lambda r: r.get("name", ""))
    for row in rows:
        for st in row.get("stages", []):
            if st.get("signoff") and st["signoff"].get("on"):
                st["signoff"]["on"] = date.fromisoformat(st["signoff"]["on"])
    return render(request, "tracker/snapshot_record.html", {
        "nav": "audit", "snap": snap, "rows": rows,
        "req_labels": REQUIREMENT_LABELS, "decisions": dict(GateApproval.Decision.choices),
        "readiness": dict(ControlImpact.Readiness.choices), "levels": dict(OversightLevel.choices),
    })


# --- History ------------------------------------------------------------------------------------


def _parse_date(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


HIDDEN_FIELDS = {"initiative_entity"}
FIELD_LABELS = {"item_index": "Checklist item", "is_deleted": "Removed", "guidance_version": "Guidance version"}


def _field_label(model, field):
    if field in FIELD_LABELS:
        return FIELD_LABELS[field]
    try:
        name = str(model._meta.get_field(field).verbose_name)
    except FieldDoesNotExist:
        name = field.replace("_", " ")
    return name[:1].upper() + name[1:]


def _fmt(value, model=None, field=None):
    """Display text for a field value; coded choices (status, decision, basis…) show their labels."""
    if value is None or value == "":
        return "—"
    if field == "item_index":
        value = value + 1
    if model is not None:
        try:
            value = dict(model._meta.get_field(field).flatchoices).get(value, value)
        except FieldDoesNotExist:
            pass
    text = str(value)
    return text if len(text) <= 200 else text[:197] + "…"


def _label(model, obj, controls, people):
    """Human label for an entity from one of its versions."""
    if model is Initiative:
        return obj.name
    if model in (Phase, Milestone):
        return obj.name
    if model is RiskItem:
        return obj.title
    if model is ControlImpact:
        c = controls.get(obj.control_entity)
        return f"{c.control_id} {c.name}" if c else str(obj.control_entity)
    if model is Overlay:
        return f"{obj.get_type_display()}: {obj.text[:60]}"
    if model is RagOverride:
        return "RAG override"
    if model is StageCheck:
        return f"Stage {obj.stage} item {obj.item_index + 1}: {obj.item_text[:80]}"
    if model is GateApproval:
        return f"Stage {obj.stage} gate"
    if model is Delegation:
        return f"Delegate {people.get(obj.delegate_emp_id, obj.delegate_emp_id)}"
    if model is Meeting:
        return obj.what
    if model is FollowUp:
        return obj.text[:60]
    return str(obj)


def _scoped(model, qs, eid):
    """Restrict a queryset of `model` to rows that belong to initiative `eid`."""
    if model is Initiative:
        return qs.filter(entity_id=eid)
    if model is Meeting:
        links = MeetingInitiative.objects.filter(initiative_entity=eid).values("meeting_entity")
        return qs.filter(entity_id__in=links)
    return qs.filter(initiative_entity=eid)


def _entity_ids_for(model, eid):
    if model is Initiative:
        return [eid]
    if model is Meeting:
        return list(_scoped(model, model.objects.all(), eid).values_list("entity_id", flat=True).distinct())
    return list(
        model.objects.filter(initiative_entity=eid).values_list("entity_id", flat=True).distinct()
    )


def _hidden_entities(actor, eid):
    """Entity ids of layered items whose *current* visibility the actor may not read."""
    if actor.is_tech_risk:
        return set()
    layers = actor.visible_layers(eid)
    hidden = set()
    for model in LAYERED_MODELS:
        for row in model.objects.current_including_deleted().filter(initiative_entity=eid):
            if row.visibility not in layers:
                hidden.add(row.entity_id)
    return hidden


def _diff_rows(model, entity_id, label):
    """Rows for every field change of one entity; blank fields on creation are omitted."""
    rows = []
    for n, (version, diff) in enumerate(field_history(model, entity_id)):
        created = n == 0
        for field, (old, new) in diff.items():
            if field in HIDDEN_FIELDS or (created and new in (None, "", False, 0, [], {})):
                continue
            rows.append({
                "when": version.valid_from, "who": version.author_emp_id,
                "entity": f"{MODEL_LABELS[model]} · {label}", "field": _field_label(model, field),
                "old": _fmt(old, model, field), "new": _fmt(new, model, field), "created": created,
            })
    return rows


def _snapshot_state(model, eid, at, hidden):
    """{entity_id: version} for versions current at `at`, hidden entities excluded."""
    qs = _scoped(model, model.objects.as_of(at), eid)
    return {v.entity_id: v for v in qs if v.entity_id not in hidden}


def history(request, entity_id):
    """Field-level history for one initiative plus a two-point comparison."""
    if (denied := _audit_actor_or_403(request)):
        return denied
    init = get_initiative(request, entity_id)
    actor = request.actor
    eid = init.entity_id
    hidden = _hidden_entities(actor, eid)
    controls = {c.entity_id: c for c in Control.objects.all().order_by("valid_from")}
    people = {p.emp_id: p.display_name for p in Person.objects.all()}

    rows = []
    for model in HISTORY_MODELS:
        for ent in _entity_ids_for(model, eid):
            if ent in hidden:
                continue
            last = model.objects.filter(entity_id=ent).order_by("-valid_from").first()
            rows.extend(_diff_rows(model, ent, _label(model, last, controls, people)))
    rows.sort(key=lambda r: r["when"], reverse=True)

    today = clock.today()
    to_d = _parse_date(request.GET.get("to"), today)
    from_d = _parse_date(request.GET.get("from"), today - timedelta(days=30))
    at_from, at_to = clock.end_of_local_day(from_d), clock.end_of_local_day(to_d)
    comparison = []
    for model in HISTORY_MODELS:
        a = _snapshot_state(model, eid, at_from, hidden)
        b = _snapshot_state(model, eid, at_to, hidden)
        for ent in sorted(set(a) | set(b), key=str):
            va, vb = a.get(ent), b.get(ent)
            ref = vb or va
            label = _label(model, ref, controls, people)
            entry = {"entity": f"{MODEL_LABELS[model]} · {label}", "state": "changed", "fields": []}
            if va is None:
                entry["state"] = "added"
            elif vb is None:
                entry["state"] = "removed"
            else:
                entry["fields"] = [
                    {"field": _field_label(model, f), "old": _fmt(o, model, f), "new": _fmt(n, model, f)}
                    for f, (o, n) in vb.diff_from(va).items() if f not in HIDDEN_FIELDS
                ]
                if not entry["fields"]:
                    continue
            comparison.append(entry)

    return render(request, "tracker/history.html", {
        "nav": "audit", "initiative": init, "rows": rows, "comparison": comparison,
        "from_date": from_d, "to_date": to_d,
    })
