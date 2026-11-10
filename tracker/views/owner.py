"""Views: owner. My initiative, check-in, and the edit pages for initiative fields and child rows."""

import uuid
from datetime import date

from django import forms
from django.contrib import messages
from django.db import transaction
from django.forms import formset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from core.auth.decorators import forbidden
from core.models import Person
from tracker.lifecycle import STAGE_CHOICES
from tracker.models import (
    Control,
    ControlImpact,
    Initiative,
    Milestone,
    Phase,
    RiskItem,
    Workstream,
)
from tracker.services import checkin, rag, workstreams as ws_svc
from tracker.services.bundle import load_bundle
from tracker.views.common import get_initiative, require_editor_or_403, safe_next
from tracker.visibility import initiatives_for, items_for

DATE_WIDGET = {"type": "date"}
EMPTY = "—"


# --- shared helpers -----------------------------------------------------------------


def style_fields(form):
    """Apply Bootstrap control classes to every visible field of a form."""
    for field in form.fields.values():
        w = field.widget
        if isinstance(w, forms.HiddenInput):
            continue
        if isinstance(w, forms.CheckboxInput):
            w.attrs["class"] = "form-check-input"
        elif isinstance(w, forms.Select):
            w.attrs["class"] = "form-select form-select-sm"
        else:
            w.attrs["class"] = "form-control form-control-sm"
    return form


def date_field(**kw):
    return forms.DateField(required=False, widget=forms.DateInput(attrs=DATE_WIDGET, format="%Y-%m-%d"), **kw)


def _editable(request, entity_id):
    """Return (initiative, None) for an editor, or (None, response) for a 404/403 outcome."""
    init = get_initiative(request, entity_id)
    denied = require_editor_or_403(request, init)
    return (None, denied) if denied else (init, None)


def _current(eid):
    return Initiative.objects.get_current(eid)


def _back(request, init):
    """Back to `next` when it is safe, else the initiative page."""
    return redirect(safe_next(request) or reverse("initiative", args=[init.entity_id]))


# --- my initiative -------------------------------------------------------------------


def my_initiative(request):
    """Send an owner to their initiative, or list the ones they own."""
    actor = request.actor
    if not actor.owned_initiatives:
        return forbidden(request, "You do not own or help run any initiative.")
    inits = list(
        initiatives_for(actor).filter(entity_id__in=actor.owned_initiatives).order_by("name")
    )
    if not inits:
        return forbidden(request, "You do not own or help run any initiative.")
    if len(inits) == 1:
        return redirect("initiative", entity_id=inits[0].entity_id)
    return render(request, "tracker/my_initiative_choose.html", {"nav": "home", "initiatives": inits})


# --- check-in ------------------------------------------------------------------------


class CheckInForm(forms.Form):
    text = forms.CharField(error_messages={"required": "Describe what has changed."}, widget=forms.Textarea(attrs={"rows": 3}))
    go_live = date_field()
    forecast = date_field()
    pct_complete = forms.IntegerField(required=False, min_value=0, max_value=100)
    risk_title = forms.CharField(required=False, max_length=300)
    risk_rating = forms.ChoiceField(required=False, choices=RiskItem.Rating.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


def _check_in_rows(bundle, data=None, ws=None):
    """Controls not yet Ready in the chosen scope, with the submitted choice and evidence when a POST is being re-shown."""
    rows = []
    for impact in bundle.scoped(bundle.impacts, ws.entity_id if ws else None):
        if impact.readiness == ControlImpact.Readiness.READY:
            continue
        key = str(impact.entity_id)
        choice = impact.readiness if impact.readiness != ControlImpact.Readiness.SENT_BACK else ""
        evidence = impact.evidence_link
        if data is not None:
            choice, evidence = data.get(f"ctl_{key}", ""), data.get(f"ev_{key}", "").strip()
        rows.append({"impact": impact, "control": bundle.control_for(impact), "choice": choice, "evidence": evidence, "error": ""})
    return rows


def _read_controls(rows):
    """Validate the posted control choices; returns {impact_entity: (readiness, evidence)}."""
    chosen = {}
    for row in rows:
        if row["choice"] not in checkin.OWNER_CONTROL_CHOICES:
            continue
        if row["choice"] == ControlImpact.Readiness.SUBMITTED:
            try:
                row["evidence"] = forms.URLField(assume_scheme="https").clean(row["evidence"])
            except forms.ValidationError:
                row["error"] = "Enter an evidence link to submit for review."
                continue
        chosen[row["impact"].entity_id] = (row["choice"], row["evidence"])
    return chosen


def _check_in_scopes(actor, bundle):
    """Workstreams this actor may check in for, and whether the whole initiative is allowed."""
    if actor.is_tech_risk or bundle.entity_id not in actor.led_initiatives:
        return list(bundle.workstreams), True
    return [w for w in bundle.workstreams if w.lead_emp_id == actor.emp_id], False


def check_in(request, entity_id):
    """Check-in modal: what changed, go-live, next key milestone, control readiness and a new risk."""
    init = get_initiative(request, entity_id)
    if not request.actor.can_check_in(entity_id):
        return forbidden(request, "Only the initiative owner, a delegate, a workstream lead or Tech Risk can check in.")
    if init.status != Initiative.Status.ACTIVE:
        raise Http404("Check-ins are for active initiatives")
    bundle = load_bundle(init)
    posted = request.method == "POST"
    params = request.POST if posted else request.GET
    allowed, whole_ok = _check_in_scopes(request.actor, bundle)
    by_id = {str(w.entity_id): w for w in allowed}
    if "ws" in params:
        chosen = params.get("ws", "")
        ws = by_id.get(chosen)
        if (chosen and ws is None) or (not chosen and not whole_ok):
            return forbidden(request, "You cannot check in for that workstream.")
    else:
        ws = next((w for w in allowed if w.lead_emp_id == request.actor.emp_id), None) if bundle.workstreams else None
        if ws is None and not whole_ok and allowed:
            ws = allowed[0]
    ws_entity = ws.entity_id if ws else None
    milestone = bundle.next_milestone(key_only=True, ws=ws_entity)
    baseline = (milestone.forecast or milestone.planned) if milestone else None
    if posted:
        form = CheckInForm(request.POST)
        rows = _check_in_rows(bundle, request.POST, ws)
        controls = _read_controls(rows)
        if form.is_valid() and not any(r["error"] for r in rows):
            d = form.cleaned_data
            try:
                checkin.post_check_in(
                    init, request.actor.emp_id, d["text"], go_live=None if ws else d["go_live"],
                    milestone=milestone, forecast=None if d["forecast"] == baseline else d["forecast"],
                    pct_complete=d["pct_complete"], controls=controls, workstream=ws,
                    new_risk=(d["risk_title"], d["risk_rating"] or RiskItem.Rating.MEDIUM) if d["risk_title"] else None,
                )
            except ValueError as exc:
                form.add_error("text", str(exc))
            else:
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    else:
        form = CheckInForm(initial={
            "text": request.GET.get("text", ""),
            "go_live": init.target_go_live, "forecast": baseline,
            "pct_complete": milestone.pct_complete if milestone else None,
            "risk_rating": RiskItem.Rating.MEDIUM,
        })
        rows = _check_in_rows(bundle, ws=ws)
    return render(request, "tracker/partials/checkin_modal.html", {
        "initiative": init, "eid": entity_id, "form": form, "rows": rows, "milestone": milestone,
        "choices": [(c.value, c.label) for c in checkin.OWNER_CONTROL_CHOICES],
        "workstream": ws, "ws_options": allowed if bundle.workstreams else [], "whole_ok": whole_ok,
    })


# --- edit initiative fields ----------------------------------------------------------


class InitiativeForm(forms.Form):
    name = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    sponsor = forms.CharField(required=False, max_length=200)
    phase = forms.ChoiceField(required=False)
    target_go_live = date_field()
    business_driver = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    why_it_matters = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    expected_systems = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, phase_names=(), current_phase="", **kwargs):
        super().__init__(*args, **kwargs)
        names = list(phase_names)
        if current_phase and current_phase not in names:
            names.insert(0, current_phase)
        self.fields["phase"].choices = [("", EMPTY)] + [(n, n) for n in names]
        style_fields(self)


def edit_initiative(request, entity_id):
    """Edit the initiative's own fields; writes one new version only when something changed."""
    init, denied = _editable(request, entity_id)
    if denied:
        return denied
    names = list(
        Phase.objects.current().filter(initiative_entity=init.entity_id).order_by("order").values_list("name", flat=True)
    )
    kw = {"phase_names": names, "current_phase": init.phase}
    initial = {k: getattr(init, k) for k in InitiativeForm.base_fields}
    if request.method == "POST":
        form = InitiativeForm(request.POST, initial=initial, **kw)
        if form.is_valid():
            changes = {k: v for k, v in form.cleaned_data.items() if v != getattr(init, k)}
            if changes:
                with transaction.atomic():
                    new = init.new_version(request.actor.emp_id, **changes)
                    rag.recompute(new)
                messages.success(request, "Initiative details saved.")
            else:
                messages.info(request, "No changes to save.")
            return _back(request, init)
    else:
        form = InitiativeForm(initial=initial, **kw)
    return render(request, "tracker/owner_edit_initiative.html", {
        "nav": "home", "initiative": init, "form": form, "next": safe_next(request)})


# --- row editors ---------------------------------------------------------------------


def _to_uuid(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class RowForm(forms.Form):
    """Base for one table row: hidden entity id, delete checkbox, and required-unless-deleted checks."""

    required_fields = ()
    entity_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    delete = forms.BooleanField(required=False)
    workstream_entity = forms.TypedChoiceField(required=False, coerce=_to_uuid, empty_value=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def clean(self):
        data = super().clean()
        if data.get("delete"):
            return data
        for name in self.required_fields:
            if data.get(name) in (None, "") and name not in self.errors:
                self.add_error(name, "This field is required.")
        return data


class PhaseRow(RowForm):
    required_fields = ("name", "stage")
    name = forms.CharField(required=False, max_length=100)
    stage = forms.TypedChoiceField(required=False, choices=STAGE_CHOICES, coerce=int, empty_value=None)
    order = forms.IntegerField(required=False, min_value=0, widget=forms.NumberInput(attrs={"style": "width:4.5em"}))
    planned_start = date_field()
    planned_end = date_field()
    forecast_start = date_field()
    forecast_end = date_field()
    actual_start = date_field()
    actual_end = date_field()


class MilestoneRow(RowForm):
    required_fields = ("name",)
    name = forms.CharField(required=False, max_length=200)
    phase_entity = forms.TypedChoiceField(required=False, coerce=_to_uuid, empty_value=None)
    planned = date_field()
    forecast = date_field()
    actual = date_field()
    pct_complete = forms.IntegerField(
        required=False, min_value=0, max_value=100, widget=forms.NumberInput(attrs={"style": "width:4.5em"})
    )
    status = forms.ChoiceField(required=False, choices=Milestone.Status.choices)
    is_key = forms.BooleanField(required=False)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 1}))

    def clean(self):
        data = super().clean()
        if not data.get("delete"):
            data["pct_complete"] = data.get("pct_complete") or 0
            data["status"] = data.get("status") or Milestone.Status.PLANNED
        return data


class RiskRow(RowForm):
    required_fields = ("type", "title", "rating")
    type = forms.ChoiceField(required=False, choices=RiskItem.Type.choices)
    title = forms.CharField(required=False, max_length=300)
    rating = forms.ChoiceField(required=False, choices=RiskItem.Rating.choices)
    owner = forms.CharField(required=False, max_length=200)
    target_date = date_field()
    status = forms.ChoiceField(required=False, choices=RiskItem.Status.choices)
    mitigation = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 1}))

    def clean(self):
        data = super().clean()
        if not data.get("delete"):
            data["status"] = data.get("status") or RiskItem.Status.OPEN
        return data


class ControlRow(RowForm):
    required_fields = ("control_entity", "impact_type")
    control_entity = forms.TypedChoiceField(required=False, coerce=_to_uuid, empty_value=None)
    impact_type = forms.ChoiceField(required=False, choices=ControlImpact.ImpactType.choices)
    compensating_control = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 1}))
    readiness = forms.ChoiceField(required=False, choices=ControlImpact.Readiness.choices)
    needed_by_milestone = forms.TypedChoiceField(required=False, coerce=_to_uuid, empty_value=None)
    evidence_link = forms.URLField(required=False, max_length=500, assume_scheme="https")

    def clean(self):
        data = super().clean()
        if not data.get("delete"):
            data["readiness"] = data.get("readiness") or ControlImpact.Readiness.NOT_STARTED
        return data


class WorkstreamRow(RowForm):
    required_fields = ("name",)
    name = forms.CharField(required=False, max_length=100)
    lead_emp_id = forms.ChoiceField(required=False)
    order = forms.IntegerField(required=False, min_value=0, widget=forms.NumberInput(attrs={"style": "width:4.5em"}))
    description = forms.CharField(required=False, max_length=300)


def _workstream_items(init, actor):
    return Workstream.objects.current().filter(initiative_entity=init.entity_id).order_by("order", "name")


def _workstream_choices(init):
    rows = Workstream.objects.current().filter(initiative_entity=init.entity_id).order_by("order", "name")
    return [("", "Whole initiative")] + [(str(w.entity_id), w.name) for w in rows]


def _lead_choices(items):
    people = {p.emp_id: p.display_name for p in Person.objects.all()}
    for w in items:
        if w.lead_emp_id and w.lead_emp_id not in people:
            people[w.lead_emp_id] = w.lead_emp_id
    return [("", EMPTY)] + [(emp, name) for emp, name in sorted(people.items(), key=lambda p: p[1])]


def _phase_items(init, actor):
    return Phase.objects.current().filter(initiative_entity=init.entity_id).order_by("order")


def _milestone_items(init, actor):
    return Milestone.objects.current().filter(initiative_entity=init.entity_id).order_by("planned", "name")


def _risk_items(init, actor):
    qs = RiskItem.objects.current().filter(initiative_entity=init.entity_id).order_by("-rating", "title")
    return items_for(actor, init.entity_id, qs)


def _control_items(init, actor):
    qs = ControlImpact.objects.current().filter(initiative_entity=init.entity_id)
    return items_for(actor, init.entity_id, qs)


def _phase_choices(init):
    rows = Phase.objects.current().filter(initiative_entity=init.entity_id).order_by("order")
    return [("", EMPTY)] + [(str(p.entity_id), p.name) for p in rows]


def _control_choices(init, items):
    controls = {c.entity_id: c for c in Control.objects.current().order_by("control_id")}
    choices = [("", EMPTY)] + [(str(e), f"{c.control_id} — {c.name}") for e, c in controls.items()]
    for imp in items:
        if imp.control_entity not in controls:
            old = Control.objects.filter(entity_id=imp.control_entity).order_by("-valid_from").first()
            label = f"{old.control_id} — {old.name} (retired)" if old else "Unknown control"
            choices.append((str(imp.control_entity), label))
    return choices


def _needed_by_choices(init):
    """Blank (go-live) plus the initiative's current key milestones, by name and date."""
    rows = Milestone.objects.current().filter(initiative_entity=init.entity_id, is_key=True)
    dated = sorted(rows, key=lambda m: (m.actual or m.forecast or m.planned or date.max, m.name))
    choices = [("", "Go-live")]
    for m in dated:
        when = m.actual or m.forecast or m.planned
        choices.append((str(m.entity_id), f"{m.name} · {when:%b} {when.day}, {when.year}" if when else m.name))
    return choices


def _row_initial(item, fields):
    d = {f: getattr(item, f) for f in fields}
    d["entity_id"] = item.entity_id
    return d


def _normalize(value):
    return "" if value is None else value


def _apply_rows(request, init, cfg, items_by_entity, formset):
    """Create / version / retire rows from a validated formset. Returns (added, changed, retired)."""
    actor = request.actor
    added = changed = retired = 0
    next_order = max([getattr(i, "order", 0) for i in items_by_entity.values()] + [-1]) + 1
    for form in formset:
        data = form.cleaned_data
        if not data:
            continue
        eid = data.get("entity_id")
        if eid:
            item = items_by_entity[eid]
            if data.get("delete"):
                cfg.get("retire", lambda i, who: i.retire(who))(item, actor.emp_id)
                retired += 1
                continue
            changes = {
                f: data[f] for f in cfg["fields"]
                if _normalize(data.get(f)) != _normalize(getattr(item, f))
            }
            if "order" in changes and changes["order"] is None:
                del changes["order"]
            if changes:
                item.new_version(actor.emp_id, **changes)
                changed += 1
        else:
            if data.get("delete") or not form.has_changed():
                continue
            values = {f: data[f] for f in cfg["fields"] if data.get(f) not in (None,)}
            if cfg["model"] in (Phase, Workstream) and values.get("order") is None:
                values["order"] = next_order
                next_order += 1
            values.update(cfg["create_extra"](actor))
            cfg["model"].create(actor.emp_id, initiative_entity=init.entity_id, **values)
            added += 1
    return added, changed, retired


def _risk_extra(actor):
    return {"source": RiskItem.Source.TECH_RISK if actor.is_tech_risk else RiskItem.Source.OWNER}


ROW_PAGES = {
    "phases": {
        "model": Phase, "form": PhaseRow, "items": _phase_items, "title": "Edit phases",
        "label": "Phases",
        "help": "Each phase maps to one lifecycle stage. Leave Order blank on a new row to add it at the end.",
        "columns": [
            ("name", "Phase"), ("stage", "Stage"), ("order", "Order"), ("planned_start", "Planned start"),
            ("planned_end", "Planned end"), ("forecast_start", "Forecast start"), ("forecast_end", "Forecast end"),
            ("actual_start", "Actual start"), ("actual_end", "Actual end"),
        ],
        "create_extra": lambda actor: {},
    },
    "milestones": {
        "model": Milestone, "form": MilestoneRow, "items": _milestone_items, "title": "Edit milestones",
        "label": "Milestones",
        "help": "Changing a forecast date is a field edit: it is versioned but does not reset the staleness clock.",
        "columns": [
            ("name", "Milestone"), ("phase_entity", "Phase"), ("planned", "Planned"), ("forecast", "Forecast"),
            ("actual", "Actual"), ("pct_complete", "%"), ("status", "Status"), ("is_key", "Key"), ("note", "Note"),
        ],
        "create_extra": lambda actor: {},
    },
    "risks": {
        "model": RiskItem, "form": RiskRow, "items": _risk_items, "title": "Edit risks, issues and dependencies",
        "label": "Risks, issues & dependencies",
        "help": "Who can see each item is set by Tech Risk on the initiative page.",
        "columns": [
            ("type", "Type"), ("title", "Title"), ("rating", "Rating"), ("owner", "Owner"),
            ("target_date", "Target"), ("status", "Status"), ("mitigation", "Mitigation"),
        ],
        "create_extra": _risk_extra,
    },
    "controls": {
        "model": ControlImpact, "form": ControlRow, "items": _control_items, "title": "Edit control impact",
        "label": "Control impact",
        "help": "Pick controls from the current control library. Impact type, readiness and the needed-by milestone drive the RAG rules. Blank means go-live.",
        "columns": [
            ("control_entity", "Control"), ("impact_type", "Impact"), ("compensating_control", "Compensating control"),
            ("readiness", "Readiness"), ("needed_by_milestone", "Needed by"), ("evidence_link", "Evidence link"),
        ],
        "create_extra": lambda actor: {},
    },
    "workstreams": {
        "model": Workstream, "form": WorkstreamRow, "items": _workstream_items, "title": "Edit workstreams",
        "label": "Workstreams",
        "help": "Parts of a larger initiative, each with a lead. Retiring one moves its items back to the whole initiative.",
        "columns": [("name", "Workstream"), ("lead_emp_id", "Lead"), ("order", "Order"), ("description", "Description")],
        "create_extra": lambda actor: {},
        "retire": ws_svc.remove,
    },
}
for _cfg in ROW_PAGES.values():
    _cfg["fields"] = [n for n, _ in _cfg["columns"]]
    _cfg["formset_class"] = formset_factory(_cfg["form"], extra=3)


def _edit_rows(request, entity_id, key):
    init, denied = _editable(request, entity_id)
    if denied:
        return denied
    cfg = dict(ROW_PAGES[key])
    items = list(cfg["items"](init, request.actor))
    ws_choices = _workstream_choices(init)
    tagged = key != "workstreams" and len(ws_choices) > 1
    if tagged:
        cfg["columns"] = cfg["columns"] + [("workstream_entity", "Workstream")]
        cfg["fields"] = cfg["fields"] + ["workstream_entity"]
    by_entity = {i.entity_id: i for i in items}
    initial = [_row_initial(i, cfg["fields"]) for i in items]
    FormSet = cfg["formset_class"]

    def build(*args, **kwargs):
        fs = FormSet(*args, initial=initial, prefix="rows", **kwargs)
        for form in fs.forms:
            form.fields["workstream_entity"].choices = ws_choices
            if "lead_emp_id" in form.fields:
                form.fields["lead_emp_id"].choices = _lead_choices(items)
            if "phase_entity" in form.fields:
                form.fields["phase_entity"].choices = _phase_choices(init)
            if "needed_by_milestone" in form.fields:
                form.fields["needed_by_milestone"].choices = _needed_by_choices(init)
            if "control_entity" in form.fields:
                form.fields["control_entity"].choices = _control_choices(init, items)
        return fs

    if request.method == "POST":
        formset = build(request.POST)
        unknown = False
        if formset.is_valid():
            for form in formset:
                eid = form.cleaned_data.get("entity_id")
                if eid and eid not in by_entity:
                    form.add_error(None, "This row is no longer available; reload the page.")
                    unknown = True
            if not unknown:
                with transaction.atomic():
                    added, changed, retired = _apply_rows(request, init, cfg, by_entity, formset)
                    if added or changed or retired:
                        rag.recompute(_current(init.entity_id))
                if added or changed or retired:
                    messages.success(
                        request,
                        f"{cfg['label']} saved: {added} added, {changed} changed, {retired} retired.",
                    )
                else:
                    messages.info(request, "No changes to save.")
                return _back(request, init)
    else:
        formset = build()
    columns = cfg["columns"]
    rows = [(form, [form[name] for name, _ in columns]) for form in formset]
    ctx = {
        "nav": "home", "initiative": init, "title": cfg["title"], "help": cfg["help"], "columns": columns,
        "formset": formset, "rows": rows, "key": key, "next": safe_next(request),
    }
    return render(request, "tracker/owner_edit_rows.html", ctx)


def edit_phases(request, entity_id):
    """Table editor for phases."""
    return _edit_rows(request, entity_id, "phases")


def edit_milestones(request, entity_id):
    """Table editor for milestones."""
    return _edit_rows(request, entity_id, "milestones")


def edit_risks(request, entity_id):
    """Table editor for risks, issues and dependencies."""
    return _edit_rows(request, entity_id, "risks")


def edit_controls(request, entity_id):
    """Table editor for control impacts."""
    return _edit_rows(request, entity_id, "controls")


def edit_workstreams(request, entity_id):
    """Table editor for workstreams: add, rename, reorder, set the lead and remove."""
    return _edit_rows(request, entity_id, "workstreams")
