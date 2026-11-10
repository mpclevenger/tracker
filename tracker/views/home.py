"""Views: the Initiatives home page and the check-in picker."""

from uuid import UUID

from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.auth.decorators import forbidden
from tracker.models import Initiative
from tracker.services import checkin, rag
from tracker.services import lifecycle as lc
from tracker.services.bundle import load_bundle
from tracker.views.common import is_htmx, resolve_scope, scope_context
from tracker.views.owner import check_in
from tracker.visibility import initiatives_for
from core import clock
from core.models import Person

Status = Initiative.Status


def _waiting(actor):
    """Proposed initiatives: all for Tech Risk, otherwise the actor's own."""
    qs = Initiative.objects.current().filter(status=Status.PROPOSED)
    if not actor.is_tech_risk:
        mine = Initiative.objects.filter(author_emp_id=actor.emp_id, status=Status.PROPOSED).values("entity_id")
        qs = qs.filter(Q(owner_emp_id=actor.emp_id) | Q(entity_id__in=actor.owned_initiatives) | Q(entity_id__in=mine))
    return list(qs.order_by("submitted_at", "name"))


def _row(init, actor, owners, thresholds, guidance):
    """One display row: status, stage, owner and next check-in for an initiative."""
    bundle = load_bundle(init)
    row = {"init": init, "owner": owners.get(init.owner_emp_id, init.owner_emp_id), "closed": init.status != Status.ACTIVE,
           "can_check_in": actor.can_check_in(init.entity_id) and init.status == Status.ACTIVE}
    if not row["closed"]:
        ev = rag.evaluate(bundle, thresholds, persist=False)
        due, overdue = checkin.check_in_due(bundle)
        row.update({
            "ev": ev, "due": due, "overdue": overdue,
            "due_soon": bool(due and not overdue and (due - clock.today()).days <= 3),
        })
    row["stage"] = lc.stage_info(bundle.current_stage(), guidance).name
    return row


def _context(request):
    actor, mods = request.actor, request.modules
    owners = {p.emp_id: p.display_name for p in Person.objects.all()}
    thresholds, guidance = rag.thresholds(), lc.current_guidance()
    visible = initiatives_for(actor).exclude(status__in=[Status.PROPOSED, Status.ARCHIVED])
    ctx = {"nav": "home", "owners": owners}

    mine = []
    if actor.is_any_owner:
        mine = [_row(i, actor, owners, thresholds, guidance)
                for i in visible.filter(entity_id__in=actor.owned_initiatives, status=Status.ACTIVE).order_by("name")]
    ctx["mine"] = mine

    ctx["show_all"] = actor.sees_exec_pages
    if ctx["show_all"]:
        scope = resolve_scope(request, actor)
        q = request.GET.get("q", "").strip()
        qs = visible.filter(sox_applicable=True) if scope == "sox" else visible
        if q:
            qs = qs.filter(name__icontains=q)
        ctx.update(scope_context(request, scope))
        ctx.update({"q": q, "scope": scope,
                    "all_rows": [_row(i, actor, owners, thresholds, guidance) for i in qs.order_by("name")]})

    can_propose = actor.is_tech_risk or mods["owner_access"]
    pickable = [i for i in initiatives_for(actor).filter(status=Status.ACTIVE).order_by("name")
                if actor.can_check_in(i.entity_id)]
    ctx.update({
        "can_propose": can_propose,
        "pickable": pickable,
        "can_log_meeting": mods["meetings"] and actor.is_tech_risk,
        "can_add_watch": mods["watch_list"] and actor.is_tech_risk,
        "waiting": _waiting(actor),
        "linkable": set(actor.owned_initiatives) if not actor.is_tech_risk else None,
    })
    return ctx


def home(request):
    """Initiatives home: quick actions, my initiatives, all initiatives and the intake queue."""
    actor = request.actor
    if not actor.is_authenticated:
        return forbidden(request, "Sign in to continue.")
    ctx = _context(request)
    if not actor.sees_exec_pages and not actor.is_any_owner:
        return render(request, "tracker/no_role.html", ctx)
    return render(request, "tracker/home.html", ctx)


def check_in_pick(request):
    """Open the check-in modal for the chosen initiative, or send a plain request to its page with the modal open."""
    try:
        eid = UUID(request.GET.get("initiative", ""))
    except ValueError:
        raise Http404("Choose an initiative")
    if is_htmx(request):
        return check_in(request, eid)
    return redirect(f"{reverse('initiative', args=[eid])}?checkin=1")
