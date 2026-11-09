from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.auth.middleware import DEV_SESSION_KEY
from core.models import Person, RoleAssignment


@require_http_methods(["GET", "POST"])
def switch_user(request):
    """Dev-only user switcher so any role can be demonstrated. Not routed when AUTH_MODE=sso."""
    if settings.AUTH_MODE != "dev":
        raise Http404
    if request.method == "POST":
        emp_id = request.POST.get("emp_id", "").strip()
        if emp_id:
            Person.objects.get_or_create(
                pk=emp_id, defaults={"display_name": request.POST.get("display_name") or emp_id}
            )
            request.session[DEV_SESSION_KEY] = emp_id
        return redirect(request.POST.get("next") or "/")
    roles = {}
    for ra in RoleAssignment.objects.current().filter(initiative_entity__isnull=True):
        roles.setdefault(ra.emp_id, []).append(ra.get_role_display())
    people = [(p, roles.get(p.emp_id, [])) for p in Person.objects.all()]
    return render(request, "auth/switch_user.html", {"people": people})
