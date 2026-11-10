"""Shared helpers for views."""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme

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


SCOPES = ("sox", "all")


def resolve_scope(request, actor, default=None):
    """Scope from ?scope=; defaults to sox for auditors who aren't Tech Risk, else all."""
    raw = request.GET.get("scope", "")
    if raw in SCOPES:
        return raw
    if default:
        return default
    return "sox" if actor.is_auditor and not actor.is_tech_risk else "all"


def scope_context(request, scope):
    """Context for the scope toggle: the active scope and a link per option that keeps other query params."""
    links = []
    for value, label in (("all", "All initiatives"), ("sox", "SOX applicable")):
        params = request.GET.copy()
        params["scope"] = value
        links.append({"label": label, "url": f"{request.path}?{params.urlencode()}", "active": value == scope})
    return {"scope": scope, "scope_links": links}


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def close_modal_response():
    from django.http import HttpResponse

    return HttpResponse("")


def safe_next(request) -> str:
    """The `next` URL from the request when it stays on this site, else empty."""
    nxt = request.POST.get("next") or request.GET.get("next", "")
    ok = nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return nxt if ok else ""
