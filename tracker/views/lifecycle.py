"""Views: lifecycle stage panel, checklist item actions, sign-off and the guidance page."""

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.auth.decorators import forbidden, require_login
from meetings import services as meetings_services
from tracker import lifecycle as rules
from tracker.models import GateApproval, Initiative, OversightLevel, StageCheck
from tracker.services import lifecycle as lc
from tracker.services import rag
from tracker.services.bundle import load_bundle
from tracker.views.common import get_initiative, require_tech_risk_or_403

INTAKE_LABEL, INTAKE_TIP = "Intake acceptance", "Tech Risk accepts the intake"
DECISION_COLORS = {"proceed": "green", "conditions": "amber", "hold": "red"}
ACTION_LABELS = {
    "submit": "Submit evidence", "confirm": "Confirm", "accept": "Accept", "send_back": "Send back",
    "na": "Mark N/A", "reopen": "Reopen",
}
STATE_PILLS = {
    None: ("grey", "Open"),
    StageCheck.Status.SUBMITTED: ("blue", "Submitted"),
    StageCheck.Status.SENT_BACK: ("amber", "Sent back"),
    StageCheck.Status.ACCEPTED: ("green", "Accepted"),
    StageCheck.Status.CONFIRMED: ("green", "Confirmed"),
    StageCheck.Status.NOT_APPLICABLE: ("grey", "N/A"),
}


def _is_url(value):
    return bool(value) and value.lower().startswith(("http://", "https://"))


def allowed_actions(actor, entity_id, requirement, check):
    """Item actions this actor may take, in display order."""
    status = check.status if check else None
    open_like = status in (None, StageCheck.Status.SENT_BACK)
    actions = []
    if actor.is_owner_of(entity_id) and requirement is not None and open_like:
        actions.append("submit")
        if requirement == rules.OWNER and status is None:
            actions.append("confirm")
    if actor.is_tech_risk:
        if status == StageCheck.Status.SUBMITTED:
            actions += ["accept", "send_back"]
        elif open_like:
            actions += ["accept", "na"]
        if check and check.is_done:
            actions.append("reopen")
    return actions


def _item_rows(actor, entity_id, st):
    rows = []
    for index, text, check in st.items():
        status = check.status if check else None
        color, label = STATE_PILLS[status]
        rows.append({
            "index": index, "text": text, "check": check, "state": status or "open",
            "pill_color": color, "pill_label": label,
            "evidence_url": _is_url(check.evidence_ref) if check else False,
            "actions": [(a, ACTION_LABELS[a]) for a in allowed_actions(actor, entity_id, st.requirement, check)],
        })
    return rows


def build_stage_rows(bundle, actor):
    """Template-ready rows for the lifecycle tab's stage panel."""
    eid = bundle.entity_id
    by_stage = {}
    for m in meetings_services.meetings_for_initiative(eid):
        by_stage.setdefault(m.stage, []).append(m)
    sees_open = actor.is_tech_risk or actor.is_owner_of(eid)
    rows = []
    for st in lc.stage_statuses(bundle):
        gate = st.gate
        meetings = by_stage.get(st.number, [])
        rows.append({
            "status": st,
            "number": st.number,
            "name": st.name,
            "color": st.color,
            "req_label": rules.REQUIREMENT_LABELS[st.requirement] if st.requirement else INTAKE_LABEL,
            "req_tip": rules.REQUIREMENT_TIPS[st.requirement] if st.requirement else INTAKE_TIP,
            "done": st.done,
            "total": st.total,
            "gate": gate,
            "gate_color": DECISION_COLORS.get(gate.decision) if gate else "",
            "gate_url": _is_url(gate.evidence_ref) if gate else False,
            "needs_signoff": st.needs_signoff,
            "can_record": actor.is_tech_risk and st.needs_signoff and st.items_done
                          and (gate is None or gate.decision == GateApproval.Decision.HOLD),
            "meetings": meetings,
            "expanded": st.is_current and sees_open,
            "trac_url": st.page.trac_url if st.page else "",
            "items": _item_rows(actor, eid, st),
        })
    return rows


def _refresh():
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


class EvidenceForm(forms.Form):
    evidence_ref = forms.CharField(max_length=500, label="Evidence link")
    note = forms.CharField(max_length=500, required=False, label="Note (optional)")

    def __init__(self, *args, evidence_required=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evidence_ref"].required = evidence_required
        if not evidence_required:
            self.fields["evidence_ref"].label = "Evidence link (optional)"


class AcceptForm(forms.Form):
    basis = forms.ChoiceField(choices=StageCheck.Basis.choices, widget=forms.RadioSelect, initial=StageCheck.Basis.REVIEWED)
    evidence_ref = forms.CharField(max_length=500, label="Evidence link")

    def __init__(self, *args, evidence_required=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not evidence_required:
            del self.fields["evidence_ref"]


class TextForm(forms.Form):
    text = forms.CharField(max_length=1000)

    def __init__(self, *args, label="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].label = f"{label} (required)"


def _item_form(action, data, check):
    if action in ("submit", "confirm"):
        return EvidenceForm(data, evidence_required=action == "submit")
    if action == "accept":
        return AcceptForm(data, evidence_required=not (check and check.evidence_ref))
    return TextForm(data, label="Comment" if action == "send_back" else "Reason")


def _apply(action, bundle, emp_id, stage, item, d):
    if action == "submit":
        lc.submit_evidence(bundle, emp_id, stage, item, d["evidence_ref"], d["note"])
    elif action == "confirm":
        lc.owner_confirm(bundle, emp_id, stage, item, d["evidence_ref"], d["note"])
    elif action == "accept":
        lc.accept_item(bundle, emp_id, stage, item, d["basis"], d.get("evidence_ref") or None)
    elif action == "send_back":
        lc.send_back_item(bundle, emp_id, stage, item, d["text"])
    else:
        lc.mark_not_applicable(bundle, emp_id, stage, item, d["text"])


def _guidance_page(stage):
    page = lc.current_guidance().get(stage)
    if page is None:
        raise Http404("No guidance for that stage")
    return page


@require_http_methods(["GET", "POST"])
def item_action(request, entity_id, stage, item, action):
    """Modal (GET) and write (POST) for one checklist item action."""
    if action not in ACTION_LABELS:
        raise Http404("Unknown action")
    initiative = get_initiative(request, entity_id)
    page = _guidance_page(stage)
    if item >= len(page.checklist):
        raise Http404("No such checklist item")
    bundle = load_bundle(initiative)
    check = bundle.checks_for(stage).get(item)
    if action not in allowed_actions(request.actor, entity_id, rules.requirement(initiative.oversight, stage), check):
        return forbidden(request, "You cannot take that action on this item.")
    emp_id = request.actor.emp_id

    if action == "reopen":
        if request.method == "GET":
            return HttpResponseNotAllowed(["POST"])
        lc.reopen_item(bundle, emp_id, stage, item)
        rag.recompute(initiative)
        return _refresh()

    form = _item_form(action, request.POST if request.method == "POST" else None, check)
    if request.method == "POST" and form.is_valid():
        try:
            _apply(action, bundle, emp_id, stage, item, form.cleaned_data)
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            rag.recompute(initiative)
            return _refresh()
    return render(request, "tracker/partials/item_modal.html", {
        "form": form, "action": action, "title": ACTION_LABELS[action], "stage": lc.stage_info(stage),
        "item_text": page.checklist[item], "eid": entity_id,
        "post_url": request.path,
    })


class GateForm(forms.Form):
    approver = forms.CharField(max_length=200, label="Approver (name and role)")
    approved_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}, format="%Y-%m-%d"), label="Date")
    evidence_ref = forms.CharField(max_length=500, label="Approval evidence link")
    decision = forms.ChoiceField(choices=GateApproval.Decision.choices, initial=GateApproval.Decision.PROCEED)
    conditions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                 label="Conditions (required with conditions)")

    def clean(self):
        data = super().clean()
        if data.get("decision") == GateApproval.Decision.CONDITIONS and not (data.get("conditions") or "").strip():
            self.add_error("conditions", "Conditions are required.")
        return data


@require_http_methods(["GET", "POST"])
def record_gate(request, entity_id, stage):
    """Record a sign-off (gate review) for a stage whose items are all done."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    page = _guidance_page(stage)
    bundle = load_bundle(initiative)
    status = next(s for s in lc.stage_statuses(bundle) if s.number == stage)
    if not (status.needs_signoff and status.items_done):
        raise Http404("This stage is not ready for sign-off")
    if request.method == "POST":
        form = GateForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            lc.record_gate(bundle, request.actor.emp_id, stage, d["approver"], d["approved_on"],
                           d["evidence_ref"], d["decision"], d["conditions"].strip())
            rag.recompute(initiative)
            return _refresh()
    else:
        form = GateForm(initial={"approved_on": timezone.localdate()})
    return render(request, "tracker/partials/gate_modal.html", {
        "form": form, "stage": lc.stage_info(stage), "expected_approver": page.approver, "eid": entity_id,
    })


@require_login
def guidance_page(request):
    pages = lc.current_guidance()
    actor = request.actor
    mine = sorted(
        Initiative.objects.current().listed().filter(entity_id__in=actor.owned_initiatives)
        .exclude(status__in=[Initiative.Status.CLOSED, Initiative.Status.ARCHIVED]),
        key=lambda i: i.name.lower(),
    )
    my_levels = {i.oversight for i in mine}
    settings = rules.gate_settings()
    matrix = rules.gate_matrix(settings)
    stages = []
    for info in lc.stage_infos(pages):
        moves = [] if info.number == 0 else [{
            "label": label,
            "req_label": rules.REQUIREMENT_LABELS[rules.requirement(level, info.number, matrix)],
            "tip": rules.REQUIREMENT_TIPS[rules.requirement(level, info.number, matrix)],
            "mine": level in my_levels,
        } for level, label in OversightLevel.choices]
        stages.append({"stage": info, "page": pages.get(info.number), "moves": moves})
    return render(request, "tracker/lifecycle_guidance.html", {
        "nav": "lifecycle", "stages": stages, "mine": mine, "intake_tip": INTAKE_TIP,
        "is_draft": rules.gate_matrix_is_draft(settings), "gate_settings": settings,
        "gate_version": rules.gate_matrix_version(settings),
    })


def _lines(value):
    return [ln.strip() for ln in value.splitlines() if ln.strip()]


class GuidanceForm(forms.Form):
    name = forms.CharField(max_length=100, label="Stage name")
    approver = forms.CharField(max_length=300, label="Approver")
    checklist = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), help_text="One item per line")
    trac_url = forms.URLField(max_length=500, required=False, label="TRAC link", assume_scheme="https")
    version_label = forms.CharField(max_length=20, label="Version")
    change_note = forms.CharField(max_length=300, label="Change note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-control form-control-sm")

    def clean_checklist(self):
        items = _lines(self.cleaned_data["checklist"])
        if not items:
            raise forms.ValidationError("Enter at least one checklist item.")
        return items


@require_http_methods(["GET", "POST"])
def edit_guidance(request, stage):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    page = _guidance_page(stage)
    if request.method == "POST":
        form = GuidanceForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            lc.save_guidance(
                page, request.actor.emp_id,
                version_label=d["version_label"], change_note=d["change_note"], name=d["name"],
                approver=d["approver"], checklist=d["checklist"], trac_url=d["trac_url"],
            )
            messages.success(request, f"Stage {stage} saved as {d['version_label']}.")
            return redirect(f"/lifecycle/#stage-{stage}")
    else:
        form = GuidanceForm(initial={
            "name": page.name, "approver": page.approver, "checklist": "\n".join(page.checklist),
            "trac_url": page.trac_url, "version_label": "",
        })
    return render(request, "tracker/guidance_edit.html", {
        "nav": "lifecycle", "form": form, "stage": lc.stage_info(stage), "page": page,
    })
