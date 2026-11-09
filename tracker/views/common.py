"""Shared helpers for views."""

from django.http import Http404
from django.shortcuts import get_object_or_404

from core.auth.decorators import forbidden
from tracker.models import Initiative
from tracker.visibility import initiatives_for


def get_initiative(request, entity_id) -> Initiative:
    """Current version of an initiative the actor may open, else 404 (never leaks existence)."""
    qs = initiatives_for(request.actor)
    return get_object_or_404(qs, entity_id=entity_id)


def require_tech_risk_or_403(request):
    if not request.actor.is_tech_risk:
        return forbidden(request)
    return None


def require_editor_or_403(request, initiative):
    if not request.actor.can_edit_initiative(initiative.entity_id):
        return forbidden(request, "Only the initiative owner, a delegate or Tech Risk can edit this initiative.")
    return None


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def close_modal_response():
    from django.http import HttpResponse

    return HttpResponse("")
