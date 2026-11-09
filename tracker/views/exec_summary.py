"""Views: landing redirect, exec summary (live or snapshot) and the read-only shared link."""

from datetime import date

from django.contrib import messages
from django.core import signing
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.auth.actor import ANONYMOUS, Actor
from core.auth.decorators import forbidden
from core.models import Role
from tracker.models import Snapshot
from tracker.services import exec_summary as exec_svc
from tracker.services import timeline


def home(request):
    """Land each actor on their page: exec roles on the summary, owners on their initiative."""
    actor = request.actor
    if not actor.is_authenticated:
        return forbidden(request, "Sign in to continue.")
    if actor.sees_exec_pages:
        # Rendered in place (no redirect) so the landing URL answers 200 for exec roles.
        return exec_summary(request)
    if actor.is_any_owner:
        return redirect("my_initiative")
    return render(request, "tracker/no_role.html", {"nav": ""})


def _parse_as_of(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _render(request, viewer, snapshot, as_of, shared=False, share_token=""):
    ctx = exec_svc.build(viewer, snapshot, followups=request.modules["meetings"])
    timeline_svg = ""
    if ctx.mode == "live":
        timeline_svg = timeline.portfolio_svg(ctx.timeline_bundles, ctx.evaluations)
    share_url = ""
    if not shared and request.modules["share_links"]:
        token = signing.dumps({"snapshot": snapshot.pk if snapshot else None})
        share_url = request.build_absolute_uri(reverse("exec_shared", args=[token]))
    context = {
        "nav": "exec",
        "ctx": ctx,
        "viewer": viewer,
        # Shared links hide the app navigation by presenting the layout as anonymous.
        "actor": ANONYMOUS if shared else viewer,
        "shared": shared,
        "snapshot": snapshot,
        "as_of": as_of,
        "snapshots": [] if shared else list(Snapshot.objects.order_by("-taken_at")),
        "timeline_svg": timeline_svg,
        "share_url": share_url,
        "n_flagged": len(ctx.attention),
        "n_active": ctx.counts.get("active", 0),
        "mon": ctx.comparison_label.split()[0] if ctx.comparison_snapshot else "",
        "pulse": [{"rag": r, "count": ctx.counts.get(r, 0), "delta": ctx.delta(r), "movers": ctx.movers.get(r, [])}
                  for r in ("red", "amber", "green")],
    }
    return render(request, "tracker/exec_summary.html", context)


def exec_summary(request):
    """Exec summary for Tech Risk, Auditor and Senior Management; ?snapshot=<id>, or ?as_of=YYYY-MM-DD for the latest snapshot on or before that date."""
    actor = request.actor
    if not actor.sees_exec_pages:
        return forbidden(request)
    snapshot = None
    raw = request.GET.get("snapshot", "").strip()
    if raw:
        if not raw.isdigit():
            raise Http404("Unknown snapshot")
        snapshot = Snapshot.objects.filter(pk=int(raw)).first()
        if snapshot is None:
            raise Http404("Unknown snapshot")
    as_of = None if snapshot else _parse_as_of(request.GET.get("as_of"))
    if as_of:
        snapshot = Snapshot.objects.filter(taken_at__date__lte=as_of).order_by("-taken_at").first()
        if snapshot is None:
            messages.info(request, f"No snapshot on or before {as_of:%b} {as_of.day}, {as_of.year}. Showing live data.")
            as_of = None
    return _render(request, actor, snapshot, as_of)


def shared(request, token):
    """Read-only exec summary reachable by signed token, rendered as Senior Management."""
    try:
        payload = signing.loads(token)
        snap_id = payload["snapshot"]
    except (signing.BadSignature, KeyError, TypeError):
        raise Http404("Invalid link")
    snapshot = None
    if snap_id is not None:
        snapshot = Snapshot.objects.filter(pk=snap_id).first()
        if snapshot is None:
            raise Http404("Unknown snapshot")
    viewer = Actor(emp_id="shared", display_name="Read-only link", roles=frozenset({Role.SENIOR_MANAGEMENT}))
    return _render(request, viewer, snapshot, None, shared=True)
