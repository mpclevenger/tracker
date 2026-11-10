"""Views: initiative page, oversight, people, control review, curation, override and visibility actions."""

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core import clock
from core.auth.decorators import forbidden
from core.models import Person
from meetings import services as meetings_services
from tracker.lifecycle import QUESTION_KEYS, derive_oversight, oversight_questions
from tracker.models import (
    ControlImpact, Delegation, Initiative, Overlay, OversightLevel, Rag, RagOverride, RiskItem, Visibility,
)
from tracker.services import checkin, curation, rag, timeline
from tracker.services import comments as comments_svc
from tracker.services import lifecycle as lc
from tracker.services.bundle import load_bundle
from tracker.views.common import close_modal_response, get_initiative, is_htmx, require_tech_risk_or_403

TABS = [
    ("overview", "Overview"), ("plan", "Plan"), ("risks", "Risks & controls"),
    ("lifecycle", "Lifecycle"), ("meetings", "Meetings"), ("history", "History"), ("exec", "Exec view"),
]


class OverrideForm(forms.Form):
    value = forms.ChoiceField(choices=Rag.choices)
    reason = forms.CharField(widget=forms.Textarea)
    support = forms.CharField(widget=forms.Textarea)
    review_by = forms.DateField()


def _tab_url(entity_id, tab):
    return f"{reverse('initiative', args=[entity_id])}?tab={tab}"


def _done(request, entity_id, tab="overview"):
    """HX-Refresh for HTMX callers, redirect to the page's tab otherwise."""
    if is_htmx(request):
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return redirect(_tab_url(entity_id, tab))


def _why_rows(ev, layers, bundle):
    """Fired rules for 'Why this status'; detail text is withheld when it names an item the viewer can't see."""
    hidden = {r.title for r in bundle.risks if r.visibility not in layers}
    for i in bundle.impacts:
        if i.visibility not in layers and (c := bundle.control_for(i)):
            hidden.add(c.control_id)
    return [(level, chip, "" if any(h and h in detail for h in hidden) else detail) for level, chip, detail in ev.fired]


def _overlay(entity_id, item_entity):
    ov = Overlay.objects.current().filter(initiative_entity=entity_id, entity_id=item_entity).first()
    if ov is None:
        raise Http404("No such overlay")
    return ov


def _summary(initiative, bundle):
    """Facts for the summary strip: go-live slip, next key milestone and check-in status."""
    slip = None
    if initiative.baseline_go_live and initiative.target_go_live:
        slip = (initiative.target_go_live - initiative.baseline_go_live).days
    due, overdue = checkin.check_in_due(bundle)
    last = bundle.last_update
    return {
        "slip": slip,
        "slip_early": -slip if slip is not None and slip < 0 else 0,
        "next_milestone": bundle.next_milestone(key_only=True),
        "due": due,
        "overdue": overdue,
        "due_soon": bool(due and not overdue and (due - clock.today()).days <= 3),
        "last_update_at": last.posted_at if last else None,
        "oversight_tip": f"Override: {initiative.oversight_override_reason}" if initiative.oversight_override else "Set by Tech Risk",
    }


def _ws_rows(bundle, ev):
    """Rows for the Overview workstream table: lead, status, next key milestone, last check-in."""
    rows = []
    for ws, wev in ev.workstreams:
        last = bundle.ws_last_update(ws.entity_id)
        _due, overdue = checkin.check_in_due(bundle, ws)
        rows.append({
            "ws": ws, "ev": wev, "next": bundle.next_milestone(key_only=True, ws=ws.entity_id),
            "last": last.posted_at if last else None, "overdue": overdue,
        })
    return rows


def _tab_context(request, tab, initiative, bundle, ev, layers):
    """Extra context for the selected tab."""
    actor = request.actor
    eid = initiative.entity_id
    updates = [u for u in bundle.updates if u.visibility in layers]
    ctx = {"updates": updates, "ws_names": {w.entity_id: w.name for w in bundle.workstreams}}
    if tab == "overview":
        overlays = [o for o in bundle.overlays if o.visibility in layers]
        ctx.update({
            "delegates": [d.delegate_emp_id for d in Delegation.objects.current().filter(initiative_entity=eid)],
            "highlights": [o for o in bundle.highlights if o.visibility == Visibility.EXEC],
            "commentary": [o for o in overlays if o.type != Overlay.Type.HIGHLIGHT and o.visibility != Visibility.INTERNAL],
            "last_update": updates[0] if updates else None,
            "open_followups": [f for f in meetings_services.followups_for_initiative(eid) if f.status == "open"]
            if request.modules["meetings"] else [],
            "why_rows": _why_rows(ev, layers, bundle),
            "ws_rows": _ws_rows(bundle, ev),
            "comments": comments_svc.for_viewer(actor, eid),
            "comment_post_url": reverse("comment_initiative", args=[eid]),
        })
    elif tab == "exec":
        overlays = [o for o in bundle.overlays if o.visibility in layers]
        ctx.update({
            "last_update": updates[0] if updates else None,
            "highlights": bundle.highlights,
            "commentary": [o for o in overlays if o.type != Overlay.Type.HIGHLIGHT],
            "exec_highlights": [o for o in bundle.highlights if o.visibility == Visibility.EXEC][:3],
            "visibility_choices": Visibility.choices,
        })
    elif tab == "plan":
        ctx["timeline_svg"] = timeline.initiative_svg(bundle, layers)
        guidance = lc.current_guidance()
        ctx["phase_rows"] = [(p, lc.stage_info(p.stage, guidance).name) for p in bundle.phases]
    elif tab == "risks":
        ctx.update({
            "risks": [r for r in bundle.risks if r.visibility in layers],
            "impact_rows": [
                {"impact": i, "control": bundle.control_for(i), "due": bundle.control_due(i)}
                for i in bundle.impacts if i.visibility in layers
            ],
            "visibility_choices": Visibility.choices,
        })
    elif tab == "lifecycle":
        from tracker.views.lifecycle import build_stage_rows

        ctx["stage_rows"] = build_stage_rows(bundle, actor)
    elif tab == "meetings":
        from meetings.views import initiative_tab_context

        ctx.update(initiative_tab_context(request, initiative))
    return ctx


@require_http_methods(["GET"])
def detail(request, entity_id):
    initiative = get_initiative(request, entity_id)
    actor = request.actor
    tab = request.GET.get("tab", "overview")
    tabs = [t for t in TABS if t[0] != "meetings" or request.modules["meetings"]]
    if not actor.is_tech_risk:
        tabs = [t for t in tabs if t[0] != "exec"]
    if tab not in dict(tabs):
        tab = "overview"
    bundle = load_bundle(initiative)
    ev = rag.evaluate(bundle, persist=False)
    ctx = {
        "nav": "",
        "initiative": initiative,
        "bundle": bundle,
        "ev": ev,
        "eid": entity_id,
        "stage": lc.stage_info(bundle.current_stage()),
        "tabs": tabs,
        "tab": tab,
        "can_edit": actor.can_edit_initiative(entity_id),
        "tab_url": _tab_url(entity_id, tab),
        "open_checkin": request.GET.get("checkin") == "1",
        "can_check_in": actor.can_check_in(entity_id) and initiative.status == Initiative.Status.ACTIVE,
        "show_history_link": actor.is_tech_risk or actor.is_auditor,
        "s": _summary(initiative, bundle),
        "rag_choices": Rag.choices,
    }
    ctx.update(_tab_context(request, tab, initiative, bundle, ev, actor.visible_layers(entity_id)))
    return render(request, "tracker/initiative.html", ctx)


@require_http_methods(["GET"])
def close_modal(request):
    """Empty the modal container."""
    return close_modal_response()


@require_http_methods(["GET"])
def override_modal(request, entity_id):
    initiative = get_initiative(request, entity_id)
    actor = request.actor
    if not actor.sees_override_detail:
        return forbidden(request)
    bundle = load_bundle(initiative)
    ev = rag.evaluate(bundle, persist=False)
    seen, history = set(), []
    for row in curation.override_history(entity_id):  # newest version first; keep one row per override
        if row.entity_id in seen:
            continue
        seen.add(row.entity_id)
        first = RagOverride.objects.history(row.entity_id).first()
        row.set_by = first.author_emp_id if first else row.author_emp_id
        history.append(row)
    ov = ev.override
    return render(request, "tracker/partials/override_modal.html", {
        "initiative": initiative,
        "eid": entity_id,
        "ev": ev,
        "ov": ov,
        "overdue": bool(ov and ov.review_by and ov.review_by < clock.today()),
        "history": history,
    })


@require_http_methods(["POST"])
def override_set(request, entity_id):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    form = OverrideForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Override not set: value, reasoning, supporting evidence and review-by date are all required.")
        return redirect("initiative", entity_id=entity_id)
    d = form.cleaned_data
    curation.set_override(initiative, request.actor.emp_id, d["value"], d["reason"], d["support"], d["review_by"])
    messages.success(request, f"Override set to {d['value'].capitalize()}.")
    return redirect("initiative", entity_id=entity_id)


@require_http_methods(["POST"])
def override_clear(request, entity_id):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    curation.clear_override(initiative, request.actor.emp_id)
    if is_htmx(request):
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    messages.success(request, "Override cleared.")
    return redirect("initiative", entity_id=entity_id)


_KINDS = {"risk": RiskItem, "overlay": Overlay, "impact": ControlImpact}


@require_http_methods(["POST"])
def set_visibility(request, entity_id, kind, item_entity):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    model = _KINDS.get(kind)
    if model is None:
        raise Http404("Unknown item kind")
    visibility = request.POST.get("visibility", "")
    if visibility not in Visibility.values:
        return HttpResponse("Invalid visibility", status=400)
    item = model.objects.current().filter(initiative_entity=entity_id, entity_id=item_entity).first()
    if item is None:
        raise Http404("No such item")
    curation.set_visibility(item, request.actor.emp_id, visibility)
    rag.recompute(initiative)
    return _done(request, entity_id, "exec" if kind == "overlay" else "risks")


@require_http_methods(["POST"])
def overlay_add(request, entity_id):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    type_ = request.POST.get("type", "")
    text = request.POST.get("text", "").strip()
    if type_ not in Overlay.Type.values or not text:
        messages.error(request, "Select a type and enter text.")
        return redirect(_tab_url(entity_id, "exec"))
    curation.add_overlay(entity_id, request.actor.emp_id, type_, text)
    rag.recompute(initiative)
    return _done(request, entity_id, "exec")


@require_http_methods(["POST"])
def overlay_remove(request, entity_id, item_entity):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    curation.remove_overlay(_overlay(entity_id, item_entity), request.actor.emp_id)
    rag.recompute(initiative)
    return _done(request, entity_id, "exec")


@require_http_methods(["POST"])
def overlay_move(request, entity_id, item_entity, direction):
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    if direction not in ("up", "down"):
        raise Http404("Unknown direction")
    ov = _overlay(entity_id, item_entity)
    if ov.type != Overlay.Type.HIGHLIGHT:
        raise Http404("Only highlights can be reordered")
    curation.move_highlight(ov, request.actor.emp_id, -1 if direction == "up" else 1)
    rag.recompute(initiative)
    return _done(request, entity_id, "exec")


def _oversight_ctx(initiative, answers, override, reason, error="", sox_applicable=None):
    return {
        "sox_applicable": initiative.sox_applicable if sox_applicable is None else sox_applicable,
        "initiative": initiative,
        "eid": initiative.entity_id,
        "questions": [(key, label, bool(answers.get(key))) for key, label in oversight_questions()],
        "derived_label": OversightLevel(derive_oversight(answers)).label,
        "levels": OversightLevel.choices,
        "override": override,
        "reason": reason,
        "error": error,
    }


@require_http_methods(["GET", "POST"])
def oversight_modal(request, entity_id):
    """Tech Risk edits the oversight answers and optional override."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    if request.method == "GET":
        ctx = _oversight_ctx(initiative, initiative.oversight_answers, initiative.oversight_override,
                             initiative.oversight_override_reason)
        return render(request, "tracker/partials/oversight_modal.html", ctx)
    answers = {key: f"q_{key}" in request.POST for key in QUESTION_KEYS}
    override = request.POST.get("override", "")
    reason = request.POST.get("reason", "")
    sox = "sox_applicable" in request.POST
    try:
        if override and override not in OversightLevel.values:
            raise ValueError("Select Enhanced, Standard or Light.")
        checkin.set_oversight(initiative, request.actor.emp_id, answers, override, reason, sox_applicable=sox)
    except ValueError as exc:
        return render(request, "tracker/partials/oversight_modal.html",
                      _oversight_ctx(initiative, answers, override, reason, str(exc), sox))
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@require_http_methods(["GET", "POST"])
def edit_people(request, entity_id):
    """Tech Risk changes the owner and delegates."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    initiative = get_initiative(request, entity_id)
    people = list(Person.objects.all())
    current = {d.delegate_emp_id: d for d in Delegation.objects.current().filter(initiative_entity=entity_id)}
    if request.method == "POST":
        known = {p.emp_id for p in people}
        owner = request.POST.get("owner", "")
        delegates = {e for e in request.POST.getlist("delegates") if e in known and e != owner}
        if owner in known:
            actor_id = request.actor.emp_id
            if owner != initiative.owner_emp_id:
                initiative.new_version(actor_id, owner_emp_id=owner)
            for emp_id in delegates - current.keys():
                Delegation.create(actor_id, initiative_entity=entity_id, delegate_emp_id=emp_id)
            for emp_id in current.keys() - delegates:
                current[emp_id].retire(actor_id)
            curation.touch(entity_id, actor_id, "owner and delegates reviewed")
            return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return render(request, "tracker/partials/people_modal.html", {
        "initiative": initiative, "eid": entity_id, "people": people, "delegates": set(current),
        "error": "Select an owner." if request.method == "POST" else "",
    })


@require_http_methods(["POST"])
def review_control(request, entity_id, item_entity):
    """Tech Risk accepts a submitted control or sends it back with a comment."""
    denied = require_tech_risk_or_403(request)
    if denied:
        return denied
    get_initiative(request, entity_id)
    impact = ControlImpact.objects.current().filter(initiative_entity=entity_id, entity_id=item_entity).first()
    if impact is None:
        raise Http404("No such control impact")
    action = request.POST.get("action", "")
    emp_id = request.actor.emp_id
    try:
        if action == "accept":
            checkin.accept_control(impact, emp_id)
        elif action == "send_back":
            checkin.send_back_control(impact, emp_id, request.POST.get("comment", ""))
        else:
            raise ValueError("Select Accept or Send back.")
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('initiative', args=[entity_id])}?tab=risks")
    rag.recompute(get_initiative(request, entity_id))
    return redirect(f"{reverse('initiative', args=[entity_id])}?tab=risks")
