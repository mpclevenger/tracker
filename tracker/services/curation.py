"""Tech Risk curation actions. Every action is a new version plus a CurationEvent."""

from django.db import transaction
from django.utils import timezone

from tracker.models import CurationEvent, Overlay, RagOverride, Visibility
from tracker.services import rag
from tracker.services.bundle import load_bundle


DELETE_PREFIX = "deleted initiative: "
MAX_DELETE_REASON = 180


def touch(initiative_entity, author_emp_id, what):
    CurationEvent.objects.create(
        initiative_entity=initiative_entity, author_emp_id=author_emp_id, at=timezone.now(), what=what[:200]
    )


@transaction.atomic
def set_visibility(item, author_emp_id, visibility):
    """Works for any versioned item with a `visibility` field (RiskItem, Overlay, ControlImpact)."""
    if item.visibility == visibility:
        return item
    new = item.new_version(author_emp_id, visibility=visibility)
    label = getattr(item, "title", None) or getattr(item, "text", "")[:40]
    touch(item.initiative_entity, author_emp_id, f"visibility {item.visibility}→{visibility}: {label}")
    return new


@transaction.atomic
def add_overlay(initiative_entity, author_emp_id, type_, text, visibility=None, order=None):
    if visibility is None:
        visibility = Visibility.INTERNAL if type_ == Overlay.Type.INTERNAL_NOTE else Visibility.EXEC
    if order is None:
        existing = Overlay.objects.current().filter(initiative_entity=initiative_entity, type=type_)
        order = (max((o.order for o in existing), default=-1)) + 1
    o = Overlay.create(
        author_emp_id, initiative_entity=initiative_entity, type=type_, text=text, visibility=visibility, order=order
    )
    touch(initiative_entity, author_emp_id, f"added {type_}")
    return o


@transaction.atomic
def remove_overlay(overlay, author_emp_id):
    overlay.retire(author_emp_id)
    touch(overlay.initiative_entity, author_emp_id, f"removed {overlay.type}")


@transaction.atomic
def move_highlight(overlay, author_emp_id, direction):
    """Swap `order` with the neighbouring highlight. direction: -1 up, +1 down."""
    siblings = list(
        Overlay.objects.current()
        .filter(initiative_entity=overlay.initiative_entity, type=Overlay.Type.HIGHLIGHT)
        .order_by("order", "valid_from")
    )
    idx = next(i for i, o in enumerate(siblings) if o.entity_id == overlay.entity_id)
    j = idx + direction
    if j < 0 or j >= len(siblings):
        return
    other = siblings[j]
    a, b = overlay.order, other.order
    if a == b:
        b = a + direction
    overlay.new_version(author_emp_id, order=b)
    other.new_version(author_emp_id, order=a)
    touch(overlay.initiative_entity, author_emp_id, "reordered highlights")


@transaction.atomic
def set_override(initiative, author_emp_id, value, reason, support, review_by):
    bundle = load_bundle(initiative)
    derived = rag.derive(bundle).derived
    if bundle.override:
        bundle.override.new_version(
            author_emp_id, cleared_at=timezone.now(), cleared_by=RagOverride.ClearedBy.MANUAL
        )
    ov = RagOverride.create(
        author_emp_id,
        initiative_entity=initiative.entity_id,
        value=value,
        reason=reason,
        support=support,
        review_by=review_by,
        derived_at_set=derived,
        effective_from=timezone.now(),
    )
    touch(initiative.entity_id, author_emp_id, f"override set to {value}")
    rag.recompute(initiative)
    return ov


@transaction.atomic
def clear_override(initiative, author_emp_id):
    bundle = load_bundle(initiative)
    if bundle.override:
        bundle.override.new_version(
            author_emp_id, cleared_at=timezone.now(), cleared_by=RagOverride.ClearedBy.MANUAL
        )
        touch(initiative.entity_id, author_emp_id, "override cleared")
    rag.recompute(initiative)


def override_history(initiative_entity):
    return list(RagOverride.objects.filter(initiative_entity=initiative_entity).order_by("-valid_from"))


@transaction.atomic
def delete_initiative(initiative, emp_id, reason):
    """Retire an initiative and its open follow-ups, recording the reason; history stays."""
    from meetings.models import FollowUp

    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter a reason for deleting the initiative.")
    if len(reason) > MAX_DELETE_REASON:
        raise ValueError(f"Keep the reason to {MAX_DELETE_REASON} characters or fewer.")
    open_followups = FollowUp.objects.current().filter(
        initiative_entity=initiative.entity_id, status=FollowUp.Status.OPEN
    )
    for followup in open_followups:
        followup.retire(emp_id)
    initiative.retire(emp_id)
    touch(initiative.entity_id, emp_id, DELETE_PREFIX + reason)


def deletion_record(initiative_entity):
    """(tombstone version, CurationEvent) for a deleted initiative, else None."""
    from tracker.models import Initiative

    tomb = Initiative.objects.filter(entity_id=initiative_entity, is_deleted=True).order_by("-valid_from").first()
    if tomb is None:
        return None
    event = CurationEvent.objects.filter(
        initiative_entity=initiative_entity, what__startswith=DELETE_PREFIX
    ).order_by("-at").first()
    return tomb, event
