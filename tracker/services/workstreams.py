"""Workstream changes that touch other rows."""

from django.db import transaction

from tracker.models import ControlImpact, Milestone, Phase, RiskItem, Workstream

TAGGED = (Phase, Milestone, RiskItem, ControlImpact)


@transaction.atomic
def remove(ws: Workstream, author_emp_id):
    """Retire a workstream and move its tagged items back to the whole initiative (new versions)."""
    for model in TAGGED:
        for item in model.objects.current().filter(initiative_entity=ws.initiative_entity, workstream_entity=ws.entity_id):
            item.new_version(author_emp_id, workstream_entity=None)
    return ws.retire(author_emp_id)
