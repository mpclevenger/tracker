from django.conf import settings

from core import modules as module_switches


def actor(request):
    return {
        "actor": getattr(request, "actor", None),
        "AUTH_MODE": settings.AUTH_MODE,
    }


def modules(request):
    """Module switches as `modules.<key>`, plus labels of the ones that are off."""
    states = getattr(request, "modules", None) or dict.fromkeys(module_switches.KEYS, True)
    return {"modules": states, "modules_off": module_switches.off_labels(states)}
