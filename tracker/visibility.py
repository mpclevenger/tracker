"""Row-level filtering by actor. Every list shown to a user goes through here."""

from django.db.models import Q

from tracker.models import Initiative

EXEC_VISIBLE_STATUSES = [
    Initiative.Status.ACTIVE,
    Initiative.Status.CLOSED,
    Initiative.Status.ARCHIVED,
]


def initiatives_for(actor, queryset=None):
    """Initiatives the actor may open. Tech Risk: all. Exec/audit: all accepted. Owner: own."""
    qs = (queryset if queryset is not None else Initiative.objects.current()).exclude(
        status__in=Initiative.WATCH_STATUSES
    )
    if actor.is_tech_risk:
        return qs
    cond = Q(pk__in=[])
    if actor.is_auditor or actor.is_senior_management:
        cond |= Q(status__in=EXEC_VISIBLE_STATUSES)
    if actor.owned_initiatives:
        cond |= Q(entity_id__in=actor.owned_initiatives)
    return qs.filter(cond)


def items_for(actor, initiative_entity, queryset):
    """Filter any queryset with a `visibility` field down to the layers the actor may read."""
    layers = actor.visible_layers(initiative_entity)
    if not layers:
        return queryset.none()
    return queryset.filter(visibility__in=layers)
