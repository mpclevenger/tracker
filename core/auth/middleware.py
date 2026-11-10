from dataclasses import replace

from django.conf import settings
from django.http import Http404

from core import modules
from core.auth import sso
from core.auth.actor import Actor, apply_modules, resolve_actor
from core.models import Role

DEV_SESSION_KEY = "dev_emp_id"

# URL names (or namespaces) that return 404 when their module is off.
GATED_URLS = {"audit": "audit", "snapshot_record": "audit", "exec_shared": "share_links", "meetings": "meetings"}


def _open_actor() -> Actor:
    """AUTH_MODE=open: everyone is Tech Risk. Build/test environments only."""
    emp_id = settings.OPEN_MODE_EMP_ID or "tech-risk"
    return Actor(
        emp_id=emp_id,
        display_name=settings.OPEN_MODE_DISPLAY_NAME or "Tech Risk (open mode)",
        roles=frozenset({Role.TECH_RISK}),
    )


def _actor_for(request) -> Actor:
    mode = settings.AUTH_MODE
    if mode == "open":
        return _open_actor()
    if mode == "sso":
        emp_id = sso.emp_id_from_request(request)
        actor = resolve_actor(emp_id)
        extra = sso.roles_from_request(request) if emp_id else None
        if extra:
            actor = replace(actor, roles=actor.roles | frozenset(extra))
        return actor
    return resolve_actor(request.session.get(DEV_SESSION_KEY) or settings.DEV_DEFAULT_EMP_ID or None)


class ActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.modules = modules.states()
        request.actor = apply_modules(_actor_for(request), request.modules)
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        key = GATED_URLS.get(match.namespace) or GATED_URLS.get(match.url_name)
        if key and not request.modules[key]:
            raise Http404("Module is off")
