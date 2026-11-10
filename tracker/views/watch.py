"""Views: watch list. Early-stage items that may become initiatives."""

from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from core import clock
from core.auth.decorators import forbidden
from core.models import Person
from tracker.lifecycle import QUESTION_KEYS, oversight_questions
from tracker.models import Initiative, OversightLevel
from tracker.services import comments as comments_svc, curation, watch as svc
from tracker.views.common import require_tech_risk_or_403, resolve_scope, scope_context
from tracker.views.owner import date_field, style_fields

FIELD_ORDER = ["name", "description", "owner_emp_id", "owner_name", "decision", "decision_due",
               "key_date_label", "key_date", "options_considered", "next_check"]


def _module_or_404(request):
    if not request.modules["watch_list"]:
        raise Http404("Watch list is off")


def _viewer_or_403(request):
    _module_or_404(request)
    return None if request.actor.sees_exec_pages else forbidden(request)


def _editor_or_403(request):
    _module_or_404(request)
    return require_tech_risk_or_403(request)


def _get_item(entity_id, statuses=Initiative.WATCH_STATUSES):
    item = Initiative.objects.current().filter(entity_id=entity_id, status__in=statuses).first()
    if item is None:
        raise Http404("No such watch item")
    return item


class WatchForm(forms.Form):
    name = forms.CharField(max_length=200, label="Item name",
                           widget=forms.TextInput(attrs={"placeholder": "e.g. Expense platform renewal"}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    owner_emp_id = forms.CharField(max_length=32, label="Owner (employee ID)")
    owner_name = forms.CharField(required=False, max_length=200, label="Owner name")
    decision = forms.CharField(required=False, max_length=300, label="Decision to make")
    decision_due = date_field(label="Decision due")
    key_date_label = forms.CharField(required=False, max_length=60, label="Key date label",
                                     widget=forms.TextInput(attrs={"placeholder": "Contract ends"}))
    key_date = date_field(label="Key date")
    options_considered = forms.CharField(required=False, label="Options considered",
                                         widget=forms.Textarea(attrs={"rows": 2}))
    next_check = date_field(label="Next check")
    sox_applicable = forms.BooleanField(required=False, label="SOX applicable")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in oversight_questions():
            self.fields[key] = forms.BooleanField(required=False, label=label)
        style_fields(self)

    def main_fields(self):
        return [self[name] for name in FIELD_ORDER]

    def impact_fields(self):
        return [self[key] for key in QUESTION_KEYS]


def _save_owner(data):
    name = data.pop("owner_name", "").strip()
    emp = data["owner_emp_id"].strip()
    data["owner_emp_id"] = emp
    if name:
        person, made = Person.objects.get_or_create(pk=emp, defaults={"display_name": name})
        if not made and person.display_name != name:
            person.display_name = name
            person.save(update_fields=["display_name"])
    return data


def _scope(request):
    scope = resolve_scope(request, request.actor)
    return scope, scope_context(request, scope)


def watch_list(request):
    """Open watch items by decision due, plus the dropped and confirmed history."""
    if (denied := _viewer_or_403(request)):
        return denied
    scope, ctx = _scope(request)
    today = clock.today()
    rows = [{"item": i, "chips": svc.watch_warnings(i, today)} for i in svc.open_items(scope)]
    return render(request, "tracker/watch_list.html", {
        "nav": "watch", "rows": rows, "history": svc.history(scope), **ctx,
    })


def watch_item(request, entity_id):
    """One watch item with its warnings and Tech Risk actions."""
    if (denied := _viewer_or_403(request)):
        return denied
    item = _get_item(entity_id)
    labels = dict(oversight_questions())
    return render(request, "tracker/watch_item.html", {
        "nav": "watch", "item": item, "chips": svc.watch_warnings(item) if item.status == "watch" else [],
        "likely": OversightLevel(item.derived_oversight).label,
        "impacts": [labels[k] for k in QUESTION_KEYS if getattr(item, k)],
        "can_edit": request.actor.is_tech_risk and item.status == "watch",
        "can_history": request.actor.is_tech_risk or request.actor.is_auditor,
        "comments": comments_svc.for_viewer(request.actor, entity_id),
        "comment_post_url": reverse("comment_watch", args=[entity_id]),
    })


@require_http_methods(["GET", "POST"])
def watch_add(request):
    """Add a watch item."""
    if (denied := _editor_or_403(request)):
        return denied
    form = WatchForm(request.POST or None, initial={"owner_emp_id": request.actor.emp_id})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item = svc.create_watch_item(request.actor.emp_id, **_save_owner(dict(form.cleaned_data)))
        messages.success(request, "Added to the watch list")
        return redirect("watch_item", entity_id=item.entity_id)
    return render(request, "tracker/watch_form.html", {"nav": "watch", "form": form, "item": None})


@require_http_methods(["GET", "POST"])
def watch_edit(request, entity_id):
    """Edit a watch item."""
    if (denied := _editor_or_403(request)):
        return denied
    item = _get_item(entity_id, [Initiative.Status.WATCH])
    person = Person.objects.filter(pk=item.owner_emp_id).first()
    initial = {k: getattr(item, k) for k in WatchForm.base_fields if hasattr(item, k)}
    initial.update({k: getattr(item, k) for k in QUESTION_KEYS})
    initial["owner_name"] = person.display_name if person else ""
    form = WatchForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            svc.update_watch_item(item, request.actor.emp_id, **_save_owner(dict(form.cleaned_data)))
        messages.success(request, "Saved")
        return redirect("watch_item", entity_id=item.entity_id)
    return render(request, "tracker/watch_form.html", {"nav": "watch", "form": form, "item": item})


@require_POST
def watch_confirm(request, entity_id):
    """Confirm as an initiative: it goes to the intake queue."""
    if (denied := _editor_or_403(request)):
        return denied
    item = _get_item(entity_id, [Initiative.Status.WATCH])
    svc.confirm_watch_item(item, request.actor.emp_id)
    messages.success(request, f"{item.name} is in the intake queue")
    return redirect("workbench")


@require_POST
def watch_drop(request, entity_id):
    """Mark no change needed, with a required reason."""
    if (denied := _editor_or_403(request)):
        return denied
    item = _get_item(entity_id, [Initiative.Status.WATCH])
    try:
        svc.drop_watch_item(item, request.actor.emp_id, request.POST.get("reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("watch_item", entity_id=item.entity_id)
    messages.success(request, f"{item.name}: no change needed")
    return redirect("watch_list")


@require_POST
def watch_delete(request, entity_id):
    """Delete an item entered by mistake; history stays."""
    if (denied := _editor_or_403(request)):
        return denied
    item = _get_item(entity_id)
    try:
        curation.delete_initiative(item, request.actor.emp_id, request.POST.get("reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("watch_item", entity_id=item.entity_id)
    messages.success(request, f'Deleted "{item.name}"')
    return redirect("watch_list")
