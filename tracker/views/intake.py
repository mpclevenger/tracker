"""Views: intake. Submission form, and Tech Risk accept / return."""

import re
from datetime import datetime

from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core import clock
from core.auth.decorators import forbidden
from tracker.lifecycle import DELIVERY_STAGES, QUESTION_KEYS, oversight_questions
from tracker.models import Delegation, Initiative, Milestone, Phase, RiskItem, Visibility
from tracker.services import curation, rag
from tracker.views.common import get_initiative, require_tech_risk_or_403
from tracker.views.owner import date_field, style_fields

ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
KEY_TAIL = re.compile(r"[\s,;-]*\(?\bkey\b\)?\s*$", re.IGNORECASE)
RATINGS = {"h": "H", "m": "M", "l": "L"}


def _parse_date(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_milestones(text):
    """Lenient parse of 'Name | YYYY-MM-DD | key' lines into (name, planned, is_key) tuples."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        planned, is_key = None, False
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            name = parts[0]
            for part in parts[1:]:
                low = part.lower().strip("() ")
                if low in ("key", "k", "yes", "y"):
                    is_key = True
                elif planned is None:
                    planned = _parse_date(part)
        else:
            name = line
            if KEY_TAIL.search(name):
                is_key = True
                name = KEY_TAIL.sub("", name)
            m = ISO_DATE.search(name)
            if m:
                planned = _parse_date(m.group(1))
                name = (name[: m.start()] + name[m.end():]).strip()
        name = name.strip(" ,;-")[:200]
        if name:
            out.append((name, planned, is_key))
    return out


def parse_risks(text):
    """Lenient parse of 'title | H/M/L' lines into (title, rating) tuples; rating defaults to M."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0][:300]
        rating = "M"
        if len(parts) > 1 and parts[1]:
            rating = RATINGS.get(parts[1][0].lower(), "M")
        if title:
            out.append((title, rating))
    return out


def parse_emp_ids(text):
    seen, out = set(), []
    for token in re.split(r"[,\s;]+", text or ""):
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


class IntakeForm(forms.Form):
    name = forms.CharField(max_length=200, label="Initiative name",
                           widget=forms.TextInput(attrs={"placeholder": "e.g. SailPoint onboarding"}))
    sponsor = forms.CharField(required=False, max_length=200, label="Sponsor (name or role)")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    owner_emp_id = forms.CharField(max_length=32, label="Owner (employee ID)")
    delegates = forms.CharField(required=False, label="Delegates (employee IDs, comma-separated)")
    business_driver = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    target_go_live = date_field(label="Target go-live")
    expected_systems = forms.CharField(
        required=False, label="Expected systems / controls affected", widget=forms.Textarea(attrs={"rows": 2})
    )
    milestones = forms.CharField(
        required=False, label="Initial milestones", widget=forms.Textarea(attrs={
            "rows": 3, "placeholder": "Design sign-off | 2027-02-26 | key\nUAT exit | 2027-04-30"})
    )
    risks = forms.CharField(
        required=False, label="Known risks", widget=forms.Textarea(attrs={
            "rows": 3, "placeholder": "Vendor connector delivery slips | H"})
    )
    why_it_matters = forms.CharField(label="Why it matters to risk and controls", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in oversight_questions():
            self.fields[key] = forms.BooleanField(required=False, label=label)
        for k, stage in enumerate(DELIVERY_STAGES):
            self.fields[f"ph{k}_name"] = forms.CharField(required=False, max_length=100, initial=stage.name)
            self.fields[f"ph{k}_start"] = date_field()
            self.fields[f"ph{k}_end"] = date_field()
        style_fields(self)

    def clean(self):
        data = super().clean()
        phases = []
        for k, stage in enumerate(DELIVERY_STAGES):
            name = (data.get(f"ph{k}_name") or "").strip()
            start, end = data.get(f"ph{k}_start"), data.get(f"ph{k}_end")
            if not name or not (start or end):
                continue
            if start and end and end < start:
                self.add_error(f"ph{k}_end", "End date must be on or after the start date.")
                continue
            phases.append({"stage": stage.number, "name": name, "start": start, "end": end})
        data["phases"] = phases
        return data

    def oversight_fields(self):
        """Bound checkboxes for the six oversight questions."""
        return [self[key] for key in QUESTION_KEYS]

    def phase_rows(self):
        """Per-stage bound fields for the template."""
        return [
            {"stage": st, "name": self[f"ph{k}_name"], "start": self[f"ph{k}_start"], "end": self[f"ph{k}_end"]}
            for k, st in enumerate(DELIVERY_STAGES)
        ]


def _create_intake(actor, data):
    emp = actor.emp_id
    now = clock.now()
    init = Initiative.create(
        emp,
        name=data["name"], description=data["description"], sponsor=data["sponsor"],
        owner_emp_id=data["owner_emp_id"].strip(), status=Initiative.Status.PROPOSED, phase="Intake",
        business_driver=data["business_driver"], why_it_matters=data["why_it_matters"],
        expected_systems=data["expected_systems"], target_go_live=data["target_go_live"], submitted_at=now,
        sox_applicable=bool(data["affects_sox_app"]),
        **{key: data[key] for key in QUESTION_KEYS},
    )
    eid = init.entity_id
    for order, ph in enumerate(data["phases"]):
        Phase.create(
            emp, initiative_entity=eid, name=ph["name"], order=order, stage=ph["stage"],
            planned_start=ph["start"], planned_end=ph["end"], forecast_start=ph["start"], forecast_end=ph["end"],
        )
    for name, planned, is_key in parse_milestones(data["milestones"]):
        Milestone.create(
            emp, initiative_entity=eid, name=name, planned=planned, forecast=planned,
            status=Milestone.Status.PLANNED, is_key=is_key,
        )
    for title, rating in parse_risks(data["risks"]):
        RiskItem.create(
            emp, initiative_entity=eid, type=RiskItem.Type.RISK, title=title, rating=rating,
            status=RiskItem.Status.OPEN, source=RiskItem.Source.OWNER, visibility=Visibility.EXEC,
        )
    for delegate in parse_emp_ids(data["delegates"]):
        if delegate != init.owner_emp_id:
            Delegation.create(emp, initiative_entity=eid, delegate_emp_id=delegate)
    rag.recompute(init)
    return init


def intake_form(request):
    """Submit a new initiative to the Tech Risk queue."""
    actor = request.actor
    if not actor.is_authenticated:
        return forbidden(request, "Sign in to submit an initiative.")
    if request.method == "POST":
        form = IntakeForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                init = _create_intake(actor, form.cleaned_data)
            if actor.is_tech_risk:
                messages.success(request, f'Created "{init.name}". Accept it to make it active.')
                return redirect("initiative", init.entity_id)
            messages.success(request, "Submitted to the Tech Risk queue")
            return redirect("my_initiative")
    else:
        form = IntakeForm(initial={"owner_emp_id": actor.emp_id})
    return render(request, "tracker/intake.html", {"nav": "intake", "form": form, "phase_rows": form.phase_rows(),
        "oversight_fields": form.oversight_fields(),
    })


def _back(request):
    """Return to the page the action was taken from (same-origin path only), else the workbench."""
    nxt = request.POST.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("workbench")


@require_POST
def accept(request, entity_id):
    """Tech Risk accepts a proposed initiative and fixes the baseline go-live."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    init = get_initiative(request, entity_id)
    if init.status != Initiative.Status.PROPOSED:
        messages.warning(request, f"{init.name} is not awaiting intake review.")
        return redirect("workbench")
    emp = request.actor.emp_id
    first = Phase.objects.current().filter(initiative_entity=init.entity_id).order_by("order").first()
    with transaction.atomic():
        new = init.new_version(
            emp,
            status=Initiative.Status.ACTIVE,
            accepted_at=clock.now(),
            baseline_go_live=init.target_go_live,
            phase=first.name if first else "Mobilize & Design",
        )
        curation.touch(new.entity_id, emp, "intake accepted")
        rag.recompute(new)
    b = new.baseline_go_live
    when = f"{b:%b} {b.day}, {b.year}" if b else "not set (no target go-live)"
    messages.success(request, f"Accepted — baseline go-live fixed at {when}")
    return _back(request)


@require_POST
def return_to_owner(request, entity_id):
    """Tech Risk returns a proposed initiative to its owner with comments; it stays proposed."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    init = get_initiative(request, entity_id)
    comments = request.POST.get("comments", "").strip()
    if not comments:
        messages.warning(request, "Enter a comment to return an intake.")
        return redirect("workbench")
    if init.status != Initiative.Status.PROPOSED:
        messages.warning(request, f"{init.name} is not awaiting intake review.")
        return redirect("workbench")
    emp = request.actor.emp_id
    with transaction.atomic():
        new = init.new_version(emp, intake_comments=comments)
        curation.touch(new.entity_id, emp, "intake returned to owner")
        rag.recompute(new)
    messages.success(request, f"Returned {init.name} to its owner with comments.")
    return _back(request)
