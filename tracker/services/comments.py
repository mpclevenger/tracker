"""Comments on initiatives and watch items: anyone posts, Tech Risk publishes."""

from django.db import transaction
from django.urls import reverse

from core import clock
from core.models import Person
from tracker.models import Comment, Initiative

Status = Comment.Status


def post(actor, target_entity, text):
    """Record a pending comment from the actor."""
    person = Person.objects.filter(pk=actor.emp_id).first()
    return Comment.create(
        actor.emp_id, target_entity=target_entity, commenter_emp_id=actor.emp_id,
        author_name=person.display_name[:120] if person else "", text=text.strip(),
    )


def _with_posted(comments):
    """Attach the original post time (first version) to each comment."""
    first = {}
    for v in Comment.objects.filter(entity_id__in=[c.entity_id for c in comments]).order_by("valid_from"):
        first.setdefault(v.entity_id, v.valid_from)
    for c in comments:
        c.posted_at = first.get(c.entity_id, c.valid_from)
        c.shown_name = c.author_name or c.commenter_emp_id
    return sorted(comments, key=lambda c: c.posted_at, reverse=True)


def for_viewer(actor, target_entity):
    """{published, pending, dismissed} lists the actor may see, newest first."""
    rows = _with_posted(list(Comment.objects.current().filter(target_entity=target_entity)))
    return {
        "published": [c for c in rows if c.status == Status.PUBLISHED],
        "pending": [c for c in rows if c.status == Status.PENDING and (actor.is_tech_risk or c.commenter_emp_id == actor.emp_id)],
        "dismissed": [c for c in rows if c.status == Status.DISMISSED and actor.is_tech_risk],
    }


@transaction.atomic
def review(comment, reviewer_emp_id, status):
    """Publish or dismiss a comment as a new version."""
    if status not in (Status.PUBLISHED, Status.DISMISSED):
        raise ValueError("Unknown review status")
    return comment.new_version(reviewer_emp_id, status=status, reviewed_by=reviewer_emp_id, reviewed_at=clock.now())


def page_url(target_entity):
    """The page showing comments for this target, with the comments anchor."""
    item = Initiative.objects.current().filter(entity_id=target_entity).first()
    if item is not None and item.status in Initiative.WATCH_STATUSES:
        return reverse("watch_item", args=[target_entity]) + "#comments"
    return reverse("initiative", args=[target_entity]) + "#comments"


def pending():
    """Current pending comments, oldest first."""
    return _with_posted(list(Comment.objects.current().filter(status=Status.PENDING)))[::-1]
