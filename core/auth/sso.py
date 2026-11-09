"""Entitlement add-on integration point — the only file to change when wiring real SSO.

Modes (settings.AUTH_MODE):
  open  Every request is a Tech Risk user. For the build/test environment only. No sign-in, no gating.
  dev   Local development: pick any employee ID from the user switcher.
  sso   Production: the two hooks below supply identity and (optionally) roles.

Hook 1 — emp_id_from_request(request) -> str | None
  Return the employee ID the add-on asserts for this request, or None when unauthenticated.
  Default implementation reads a trusted header named by settings.SSO_EMP_ID_HEADER.

Hook 2 — roles_from_request(request) -> set[str] | None
  Optional. Return the set of application roles the add-on grants (values from core.models.Role),
  or None to rely on the in-app RoleAssignment table alone. When both exist they are merged, so the
  add-on can own the global roles (Tech Risk, Senior Management, Auditor) while initiative ownership
  stays in the app. Default: map header groups through settings.SSO_GROUP_ROLE_MAP if configured.
"""

from django.conf import settings


class SsoNotConfigured(RuntimeError):
    pass


def _header(request, name):
    if not name:
        return None
    value = request.META.get("HTTP_" + name.upper().replace("-", "_"))
    return value.strip() if value else None


def emp_id_from_request(request) -> str | None:
    header = getattr(settings, "SSO_EMP_ID_HEADER", None)
    if header:
        return _header(request, header)
    raise SsoNotConfigured(
        "AUTH_MODE=sso but no identity resolver is configured. Implement core.auth.sso.emp_id_from_request "
        "or set SSO_EMP_ID_HEADER to the trusted header injected by the entitlement add-on."
    )


def roles_from_request(request) -> set[str] | None:
    groups_header = getattr(settings, "SSO_GROUPS_HEADER", None)
    mapping = getattr(settings, "SSO_GROUP_ROLE_MAP", None) or {}
    raw = _header(request, groups_header)
    if raw is None or not mapping:
        return None
    groups = {g.strip() for g in raw.split(",") if g.strip()}
    return {mapping[g] for g in groups if g in mapping}
