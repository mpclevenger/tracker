"""Views: meetings log, meeting form and follow-up close/reopen."""

from uuid import UUID

from django.contrib import messages
from django.db import transaction
from django.forms import formset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core import clock
from core.auth.decorators import forbidden
from meetings import services
from meetings.forms import CloseForm, FollowUpForm, MeetingForm
from meetings.models import FollowUp, Meeting, MeetingInitiative
from tracker.models import Initiative
from tracker.services import lifecycle as lifecycle_services
from tracker.services.bundle import load_bundle
from tracker.views.common import require_tech_risk_or_403
from tracker.visibility import initiatives_for

FollowUpFormSet = formset_factory(FollowUpForm, extra=3, can_delete=True)


def _parse_uuid(raw):
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def _initiative_choices(extra_ids=()):
    """(entity_id, name) for current non-archived initiatives plus any already-linked ones."""
    current = Initiative.objects.current()
    rows = current.exclude(status=Initiative.Status.ARCHIVED) | current.filter(entity_id__in=list(extra_ids))
    return [(str(i.entity_id), i.name) for i in sorted(rows.distinct(), key=lambda i: i.name.lower())]


def _stage_choices():
    return [(i.number, f"{i.number}. {i.name}") for i in lifecycle_services.stage_infos()]


def initiative_tab_context(request, initiative):
    """Context for the initiative page's Meetings tab: meetings newest first with follow-ups and stage."""
    eid = initiative.entity_id
    meetings = services.meetings_for_initiative(eid)
    guidance = lifecycle_services.current_guidance()
    followups = {}
    for f in FollowUp.objects.current().filter(meeting_entity__in=[m.entity_id for m in meetings]):
        if f.initiative_entity in (None, eid):
            followups.setdefault(f.meeting_entity, []).append(f)
    return {
        "meetings_tab": [{
            "meeting": m,
            "stage": lifecycle_services.stage_info(m.stage, guidance) if m.stage is not None else None,
            "followups": _by_due(followups.get(m.entity_id, [])),
        } for m in meetings],
        "can_log_meeting": request.actor.is_tech_risk,
        "log_meeting_url": f"{reverse('meetings:new')}?initiative={eid}",
    }


def _by_due(followups):
    return sorted(followups, key=lambda f: (f.due_date is None, f.due_date or clock.today()))


@require_http_methods(["GET"])
def meeting_list(request):
    actor = request.actor
    if not (actor.is_tech_risk or actor.is_auditor):
        return forbidden(request)
    visible = {i.entity_id: i for i in initiatives_for(actor)}
    selected = _parse_uuid(request.GET.get("initiative"))
    open_only = request.GET.get("open") == "1"

    meetings = (
        services.meetings_for_initiative(selected) if selected
        else list(Meeting.objects.current().order_by("-held_on", "-valid_from"))
    )
    ids = [m.entity_id for m in meetings]
    links, followups = {}, {}
    for link in MeetingInitiative.objects.current().filter(meeting_entity__in=ids):
        if link.initiative_entity in visible:
            links.setdefault(link.meeting_entity, []).append(visible[link.initiative_entity])
    for f in FollowUp.objects.current().filter(meeting_entity__in=ids):
        if not open_only or f.status == FollowUp.Status.OPEN:
            followups.setdefault(f.meeting_entity, []).append(f)
    guidance = lifecycle_services.current_guidance()
    cards = []
    for m in meetings:
        fus = _by_due(followups.get(m.entity_id, []))
        if open_only and not fus:
            continue
        cards.append({
            "meeting": m,
            "stage": lifecycle_services.stage_info(m.stage, guidance) if m.stage is not None else None,
            "initiatives": sorted(links.get(m.entity_id, []), key=lambda i: i.name.lower()),
            "followups": fus,
        })
    filter_choices = sorted(
        (i for i in visible.values() if i.status != Initiative.Status.ARCHIVED), key=lambda i: i.name.lower()
    )
    return render(request, "meetings/list.html", {
        "nav": "meetings", "cards": cards, "filter_choices": filter_choices,
        "selected": selected, "open_only": open_only,
    })


def _initial_followups(meeting):
    rows = FollowUp.objects.current().filter(meeting_entity=meeting.entity_id).order_by("valid_from")
    return [{
        "entity_id": f.entity_id, "text": f.text, "owner": f.owner, "due_date": f.due_date,
        "initiative": str(f.initiative_entity) if f.initiative_entity else "",
    } for f in rows]


@require_http_methods(["GET", "POST"])
def meeting_edit(request, entity_id=None):
    if (denied := require_tech_risk_or_403(request)):
        return denied
    meeting = get_object_or_404(Meeting.objects.current(), entity_id=entity_id) if entity_id else None
    linked = services.linked_initiatives(meeting.entity_id) if meeting else set()
    choices = _initiative_choices(linked)
    kwargs = {"initiative_choices": choices}
    form_kwargs = {**kwargs, "stage_choices": _stage_choices()}

    if request.method == "POST":
        form = MeetingForm(request.POST, **form_kwargs)
        formset = FollowUpFormSet(request.POST, prefix="fu", form_kwargs=kwargs)
        if form.is_valid() and formset.is_valid():
            picked = {UUID(v) for v in form.cleaned_data["initiatives"]}
            existing = (
                {f.entity_id for f in FollowUp.objects.current().filter(meeting_entity=meeting.entity_id)}
                if meeting else set()
            )
            rows = []
            for f in formset:
                cd = f.cleaned_data
                if not cd:
                    continue
                if cd.get("entity_id") and cd["entity_id"] not in existing:
                    raise Http404("Unknown follow-up")
                eid = UUID(cd["initiative"]) if cd.get("initiative") else None
                if eid and eid not in picked and not cd.get("DELETE"):
                    f.add_error("initiative", "Select a linked initiative above, or leave blank.")
                rows.append({
                    "entity_id": cd.get("entity_id"), "delete": cd.get("DELETE", False), "text": cd.get("text", ""),
                    "owner": cd.get("owner", ""), "due_date": cd.get("due_date"), "initiative_entity": eid,
                })
            if not any(f.errors for f in formset):
                with transaction.atomic():
                    saved = services.save_meeting(request.actor.emp_id, meeting, form.cleaned_data, picked)
                    services.save_followups(request.actor.emp_id, saved, rows)
                messages.success(request, "Meeting saved.")
                return redirect("meetings:list")
    else:
        preset = _parse_uuid(request.GET.get("initiative"))
        initial = {"initiatives": [str(e) for e in linked] or ([str(preset)] if preset else [])}
        if meeting:
            initial.update({k: getattr(meeting, k) for k in services.MEETING_FIELDS})
        else:
            initial["held_on"] = clock.today()
            target = Initiative.objects.current().filter(entity_id=preset).first() if preset else None
            if target:
                initial["stage"] = load_bundle(target).current_stage()
        form = MeetingForm(initial=initial, **form_kwargs)
        formset = FollowUpFormSet(
            initial=_initial_followups(meeting) if meeting else None, prefix="fu", form_kwargs=kwargs
        )
    return render(request, "meetings/form.html", {
        "nav": "meetings", "form": form, "formset": formset, "meeting": meeting,
    })


def _followup_or_404(entity_id):
    return get_object_or_404(FollowUp.objects.current(), entity_id=entity_id)


@require_http_methods(["GET", "POST"])
def followup_close(request, entity_id):
    if (denied := require_tech_risk_or_403(request)):
        return denied
    fu = _followup_or_404(entity_id)
    if fu.status != FollowUp.Status.OPEN:
        messages.warning(request, "This follow-up is already closed.")
        return redirect("meetings:list")
    form = CloseForm(request.POST or None, initial={"closed_on": clock.today()})
    if request.method == "POST" and form.is_valid():
        services.close_followup(
            request.actor.emp_id, fu, form.cleaned_data["closed_on"], form.cleaned_data["closure_note"]
        )
        messages.success(request, "Follow-up closed.")
        return redirect("meetings:list")
    meeting = Meeting.objects.current().filter(entity_id=fu.meeting_entity).first()
    return render(request, "meetings/close.html", {"nav": "meetings", "form": form, "followup": fu, "meeting": meeting})


@require_http_methods(["POST"])
def followup_reopen(request, entity_id):
    if (denied := require_tech_risk_or_403(request)):
        return denied
    fu = _followup_or_404(entity_id)
    if fu.status == FollowUp.Status.DONE:
        services.reopen_followup(request.actor.emp_id, fu)
        messages.success(request, "Follow-up reopened.")
    return redirect("meetings:list")
