from functools import wraps

from django.shortcuts import render


def forbidden(request, message="You do not have access to this page."):
    return render(request, "403.html", {"message": message}, status=403)


def require_login(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.actor.is_authenticated:
            return forbidden(request, "Sign in to continue.")
        return view(request, *args, **kwargs)

    return wrapper


def require_roles(*roles):
    """Allow any actor holding at least one of the given global roles."""

    def decorator(view):
        @wraps(view)
        @require_login
        def wrapper(request, *args, **kwargs):
            if not (request.actor.roles & set(roles)):
                return forbidden(request)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def require_tech_risk(view):
    from core.models import Role

    return require_roles(Role.TECH_RISK)(view)
