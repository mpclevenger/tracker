"""Meetings log: reads and writes for meetings, links and follow-ups."""

from django.db import transaction
from django.db.models import Q

from core import clock
from meetings.models import FollowUp, Meeting, MeetingInitiative, MeetingSeries
from tracker.services import curation

MEETING_FIELDS = ("held_on", "what", "who", "result", "stage", "series_entity")
SERIES_FIELDS = ("name", "cadence", "usual_attendees", "initiative_entities", "default_stage", "description")
FOLLOWUP_FIELDS = ("text", "owner", "due_date", "initiative_entity")


def _sorted_by_due(followups):
    return sorted(followups, key=lambda f: (f.due_date is None, f.due_date or clock.today(), f.valid_from))


def linked_initiatives(meeting_entity):
    """Initiative entity ids currently linked to a meeting."""
    return set(
        MeetingInitiative.objects.current()
        .filter(meeting_entity=meeting_entity)
        .values_list("initiative_entity", flat=True)
    )


def meetings_for_initiative(entity_id):
    """Current meetings linked to an initiative, newest first."""
    ids = MeetingInitiative.objects.current().filter(initiative_entity=entity_id).values("meeting_entity")
    return list(Meeting.objects.current().filter(entity_id__in=ids).order_by("-held_on", "-valid_from"))


def followups_for_initiative(entity_id):
    """Follow-ups tagged to the initiative, plus untagged ones from meetings linked to it."""
    linked = MeetingInitiative.objects.current().filter(initiative_entity=entity_id).values("meeting_entity")
    qs = FollowUp.objects.current().filter(
        Q(initiative_entity=entity_id) | Q(initiative_entity__isnull=True, meeting_entity__in=linked)
    )
    return _sorted_by_due(qs)


def open_followups(entity_ids=None):
    """Open follow-ups, optionally limited to the given initiative entity ids."""
    qs = FollowUp.objects.current().filter(status=FollowUp.Status.OPEN)
    if entity_ids is not None:
        qs = qs.filter(initiative_entity__in=list(entity_ids))
    return _sorted_by_due(qs)


def overdue_followups(entity_ids=None):
    """Open follow-ups past their due date, optionally limited to the given initiative entity ids."""
    return [f for f in open_followups(entity_ids) if f.is_overdue]


def _touch_all(author, entity_ids, what):
    for eid in sorted(entity_ids, key=str):
        curation.touch(eid, author, what)


@transaction.atomic
def save_meeting(author, meeting, data, initiative_entities):
    """Create or edit a meeting and reconcile its initiative links; returns the current version."""
    wanted = set(initiative_entities)
    values = {k: data[k] for k in MEETING_FIELDS if k != "series_entity" or k in data}
    if meeting is None:
        meeting = Meeting.create(author, **values)
    elif any(getattr(meeting, k) != v for k, v in values.items()):
        meeting = meeting.new_version(author, **values)
    current = {
        link.initiative_entity: link
        for link in MeetingInitiative.objects.current().filter(meeting_entity=meeting.entity_id)
    }
    for eid in wanted - current.keys():
        MeetingInitiative.create(author, meeting_entity=meeting.entity_id, initiative_entity=eid)
    for eid in current.keys() - wanted:
        current[eid].retire(author)
    _touch_all(author, wanted | current.keys(), "meeting logged")
    return meeting


@transaction.atomic
def save_followups(author, meeting, rows):
    """Apply follow-up rows (dicts with entity_id, delete and the editable fields) to a meeting."""
    existing = {f.entity_id: f for f in FollowUp.objects.current().filter(meeting_entity=meeting.entity_id)}
    linked = list(
        MeetingInitiative.objects.current().filter(meeting_entity=meeting.entity_id)
        .values_list("initiative_entity", flat=True)
    )
    only_initiative = linked[0] if len(linked) == 1 else None
    touched = set()
    for row in rows:
        fu = existing.get(row.get("entity_id"))
        values = {k: row[k] for k in FOLLOWUP_FIELDS}
        if values["initiative_entity"] is None:
            values["initiative_entity"] = only_initiative  # a single-initiative meeting tags its follow-ups
        if fu is None:
            if row.get("delete"):
                continue
            fu = FollowUp.create(author, meeting_entity=meeting.entity_id, **values)
        elif row.get("delete"):
            fu.retire(author)
        elif any(getattr(fu, k) != v for k, v in values.items()):
            old = fu.initiative_entity
            fu = fu.new_version(author, **values)
            touched.add(old)
        touched.add(fu.initiative_entity)
    _touch_all(author, touched - {None}, "meeting logged")


@transaction.atomic
def close_followup(author, followup, closed_on, note):
    """Mark an open follow-up done; the closure note is required."""
    if not (note or "").strip():
        raise ValueError("Enter a closure note to close this follow-up.")
    if followup.status != FollowUp.Status.OPEN:
        raise ValueError("This follow-up is already closed.")
    closed = followup.new_version(
        author, status=FollowUp.Status.DONE, closed_on=closed_on, closure_note=note.strip()
    )
    if closed.initiative_entity:
        curation.touch(closed.initiative_entity, author, "follow-up closed")
    return closed


@transaction.atomic
def reopen_followup(author, followup):
    """Return a done follow-up to open, clearing the closure fields."""
    if followup.status != FollowUp.Status.DONE:
        raise ValueError("This follow-up is not closed.")
    reopened = followup.new_version(author, status=FollowUp.Status.OPEN, closed_on=None, closure_note="")
    if reopened.initiative_entity:
        curation.touch(reopened.initiative_entity, author, "follow-up reopened")
    return reopened


def series_meetings(series_entity):
    """Current meetings in a series, newest first."""
    return list(Meeting.objects.current().filter(series_entity=series_entity).order_by("-held_on", "-valid_from"))


def series_open_followups(series_entity, exclude_meeting=None):
    """Open follow-ups across a series, optionally leaving out one meeting's own."""
    ids = Meeting.objects.current().filter(series_entity=series_entity).values("entity_id")
    qs = FollowUp.objects.current().filter(status=FollowUp.Status.OPEN, meeting_entity__in=ids)
    if exclude_meeting is not None:
        qs = qs.exclude(meeting_entity=exclude_meeting)
    return _sorted_by_due(qs)


def series_summaries(series_list):
    """{series entity_id: (meeting count, last held date, open follow-up count)}."""
    out = {s.entity_id: [0, None, 0] for s in series_list}
    meeting_series = {}
    for m in Meeting.objects.current().filter(series_entity__in=list(out)):
        row = out[m.series_entity]
        row[0] += 1
        row[1] = max(row[1], m.held_on) if row[1] else m.held_on
        meeting_series[m.entity_id] = m.series_entity
    for f in FollowUp.objects.current().filter(status=FollowUp.Status.OPEN, meeting_entity__in=list(meeting_series)):
        out[meeting_series[f.meeting_entity]][2] += 1
    return {k: tuple(v) for k, v in out.items()}


@transaction.atomic
def save_series(author, series, data, initiative_entities):
    """Create or edit a meeting series; returns the current version."""
    values = {k: data[k] for k in SERIES_FIELDS if k != "initiative_entities"}
    values["initiative_entities"] = sorted(str(e) for e in initiative_entities)
    if series is None:
        return MeetingSeries.create(author, **values)
    if any(getattr(series, k) != v for k, v in values.items()):
        return series.new_version(author, **values)
    return series


@transaction.atomic
def remove_series(author, series):
    """Retire a series and un-link its meetings, which are kept."""
    for m in Meeting.objects.current().filter(series_entity=series.entity_id):
        m.new_version(author, series_entity=None)
    return series.retire(author)
