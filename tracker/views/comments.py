"""Views: post a comment (anyone who can view the page) and Tech Risk review."""

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from tracker.models import Comment
from tracker.services import comments as svc
from tracker.views.common import get_initiative, require_tech_risk_or_403
from tracker.views.watch import _get_item, _viewer_or_403

THANKS = "Thanks. Tech Risk will review your comment before it's shown."


class CommentForm(forms.Form):
    text = forms.CharField(max_length=Comment.MAX_LENGTH, error_messages={
        "required": "Enter a comment.", "max_length": f"Keep comments to {Comment.MAX_LENGTH} characters."})


def _submit(request, entity_id):
    form = CommentForm(request.POST)
    if form.is_valid() and form.cleaned_data["text"].strip():
        svc.post(request.actor, entity_id, form.cleaned_data["text"])
        messages.success(request, THANKS)
    else:
        messages.error(request, next(iter(form.errors.get("text", [])), "Enter a comment."))
    return redirect(svc.page_url(entity_id))


@require_POST
def comment_initiative(request, entity_id):
    """Post a comment on an initiative."""
    get_initiative(request, entity_id)
    return _submit(request, entity_id)


@require_POST
def comment_watch(request, entity_id):
    """Post a comment on a watch item."""
    if (denied := _viewer_or_403(request)):
        return denied
    _get_item(entity_id)
    return _submit(request, entity_id)


@require_POST
def review_comment(request, entity_id, action):
    """Tech Risk publishes or dismisses a pending comment."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    status = {"publish": Comment.Status.PUBLISHED, "dismiss": Comment.Status.DISMISSED}.get(action)
    comment = Comment.objects.current().filter(entity_id=entity_id).first()
    if status is None or comment is None:
        raise Http404("No such comment")
    if comment.status == Comment.Status.PENDING:
        svc.review(comment, request.actor.emp_id, status)
    return redirect(svc.page_url(comment.target_entity))
