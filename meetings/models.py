from datetime import date

from django.db import models

from core import clock
from core.versioning import VersionedModel
from tracker.lifecycle import STAGE_CHOICES


class Meeting(VersionedModel):
    """What was discussed, who attended, and the result; tagged to one lifecycle stage."""

    held_on = models.DateField()
    what = models.CharField(max_length=200, help_text="Topic or purpose")
    who = models.TextField(help_text="Attendees")
    result = models.TextField(help_text="Outcome and decisions")
    stage = models.PositiveSmallIntegerField(choices=STAGE_CHOICES, null=True, blank=True)

    class Meta(VersionedModel.Meta):
        ordering = ["-held_on"]

    def __str__(self):
        return self.what


class MeetingInitiative(VersionedModel):
    meeting_entity = models.UUIDField(db_index=True)
    initiative_entity = models.UUIDField(db_index=True)


class FollowUp(VersionedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"

    meeting_entity = models.UUIDField(db_index=True)
    initiative_entity = models.UUIDField(null=True, blank=True, db_index=True)
    text = models.TextField()
    owner = models.CharField(max_length=200, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    closed_on = models.DateField(null=True, blank=True)
    closure_note = models.TextField(blank=True)

    def __str__(self):
        return self.text[:60]

    @property
    def is_overdue(self):
        return self.days_overdue > 0

    @property
    def days_overdue(self):
        """Whole days past the due date while open; 0 otherwise."""
        if self.status != self.Status.OPEN or self.due_date is None:
            return 0
        today: date = clock.today()
        return max((today - self.due_date).days, 0)
