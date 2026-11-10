from django.db import models

from core.versioning import VersionedModel, VersionedQuerySet
from tracker.lifecycle import STAGE_CHOICES


class Visibility(models.TextChoices):
    """Who may read an item. Layers nest: internal ⊂ owner ⊂ exec."""

    INTERNAL = "internal", "Tech Risk only"
    OWNER = "owner", "Owner and Tech Risk"
    EXEC = "exec", "Executives and audit"


class Rag(models.TextChoices):
    GREEN = "green", "Green"
    AMBER = "amber", "Amber"
    RED = "red", "Red"


RAG_ORDER = {Rag.GREEN: 0, Rag.AMBER: 1, Rag.RED: 2}


class OversightLevel(models.TextChoices):
    ENHANCED = "enhanced", "Enhanced"
    STANDARD = "standard", "Standard"
    LIGHT = "light", "Light"


class InitiativeQuerySet(VersionedQuerySet):
    def listed(self):
        """Real initiatives only: excludes watch list items and dropped ones."""
        return self.exclude(status__in=Initiative.WATCH_STATUSES)

    def watch_items(self):
        """Current watch list items (still open)."""
        return self.current().filter(status=Initiative.Status.WATCH)


class Initiative(VersionedModel):
    class Status(models.TextChoices):
        WATCH = "watch", "Watch list"
        DROPPED = "dropped", "No change needed"
        PROPOSED = "proposed", "Proposed"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"

    WATCH_STATUSES = (Status.WATCH, Status.DROPPED)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sponsor = models.CharField(max_length=200, blank=True)
    owner_emp_id = models.CharField(max_length=32, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    phase = models.CharField(max_length=100, blank=True, help_text="Current phase name")
    business_driver = models.TextField(blank=True)
    why_it_matters = models.TextField(blank=True, help_text="Why it matters to risk and controls")
    expected_systems = models.TextField(blank=True, help_text="Expected systems and controls affected")
    target_go_live = models.DateField(null=True, blank=True)
    baseline_go_live = models.DateField(
        null=True, blank=True, help_text="Target go-live at acceptance; slip is measured from here"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_control_outcome = models.TextField(blank=True)
    intake_comments = models.TextField(blank=True, help_text="Tech Risk comments when returning intake")
    # Oversight level inputs (answered at intake) and Tech Risk's optional override.
    affects_tier01_app = models.BooleanField(default=False)
    affects_sox_app = models.BooleanField(default=False)
    sox_applicable = models.BooleanField(
        default=False, help_text="Relevant to SOX: affects a SOX in-scope app or a SOX control"
    )
    changes_key_controls = models.BooleanField(default=False)
    migrates_regulated_data = models.BooleanField(default=False)
    changes_access_model = models.BooleanField(default=False)
    moves_to_vendor_or_cloud = models.BooleanField(default=False)
    oversight_override = models.CharField(max_length=10, choices=OversightLevel.choices, blank=True)
    oversight_override_reason = models.TextField(blank=True)
    # Watch list fields.
    decision = models.CharField(max_length=300, blank=True, help_text="What is being decided")
    decision_due = models.DateField(null=True, blank=True)
    key_date = models.DateField(null=True, blank=True)
    key_date_label = models.CharField(max_length=60, blank=True, help_text="For example, Contract ends")
    options_considered = models.TextField(blank=True)
    next_check = models.DateField(null=True, blank=True)
    outcome_reason = models.TextField(blank=True, help_text="Why no change is needed")

    objects = InitiativeQuerySet.as_manager()

    class Meta(VersionedModel.Meta):
        pass

    def __str__(self):
        return self.name

    @property
    def is_live(self):
        return self.status in (self.Status.PROPOSED, self.Status.ACTIVE)

    @property
    def oversight_answers(self) -> dict:
        from tracker.lifecycle import QUESTION_KEYS

        return {key: getattr(self, key) for key in QUESTION_KEYS}

    @property
    def derived_oversight(self) -> str:
        from tracker.lifecycle import derive_oversight

        return derive_oversight(self.oversight_answers)

    @property
    def oversight(self) -> str:
        """Effective oversight level: Tech Risk's override if set, else derived from the intake answers."""
        return self.oversight_override or self.derived_oversight

    @property
    def oversight_label(self) -> str:
        return OversightLevel(self.oversight).label


class Phase(VersionedModel):
    initiative_entity = models.UUIDField(db_index=True)
    name = models.CharField(max_length=100)
    order = models.PositiveSmallIntegerField(default=0)
    stage = models.PositiveSmallIntegerField(choices=STAGE_CHOICES, default=2)
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    forecast_start = models.DateField(null=True, blank=True)
    forecast_end = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    workstream_entity = models.UUIDField(null=True, blank=True, help_text="Workstream this belongs to; blank means the whole initiative")

    class Meta(VersionedModel.Meta):
        ordering = ["order"]

    def __str__(self):
        return self.name


class Workstream(VersionedModel):
    """A part of a larger initiative with its own lead, dates and status. Optional."""

    initiative_entity = models.UUIDField(db_index=True)
    name = models.CharField(max_length=100)
    lead_emp_id = models.CharField(max_length=32, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=300, blank=True)

    class Meta(VersionedModel.Meta):
        ordering = ["order"]

    def __str__(self):
        return self.name


class Milestone(VersionedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ON_TRACK = "on_track", "On track"
        SLIPPED = "slipped", "Slipped"
        DONE = "done", "Done"

    initiative_entity = models.UUIDField(db_index=True)
    phase_entity = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=200)
    planned = models.DateField(null=True, blank=True)
    forecast = models.DateField(null=True, blank=True)
    actual = models.DateField(null=True, blank=True)
    pct_complete = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    is_key = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    workstream_entity = models.UUIDField(null=True, blank=True, help_text="Workstream this belongs to; blank means the whole initiative")

    class Meta(VersionedModel.Meta):
        ordering = ["planned"]

    def __str__(self):
        return self.name

    @property
    def slippage_days(self):
        """Calendar days the milestone is later than planned (actual, else forecast)."""
        if self.planned is None:
            return None
        compare = self.actual or self.forecast
        if compare is None:
            return None
        return (compare - self.planned).days


class RiskItem(VersionedModel):
    class Type(models.TextChoices):
        RISK = "risk", "Risk"
        ISSUE = "issue", "Issue"
        DEPENDENCY = "dependency", "Dependency"

    class Rating(models.TextChoices):
        HIGH = "H", "High"
        MEDIUM = "M", "Medium"
        LOW = "L", "Low"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MITIGATING = "mitigating", "Mitigating"
        CLOSED = "closed", "Closed"

    class Source(models.TextChoices):
        OWNER = "owner", "Owner"
        TECH_RISK = "tech_risk", "Tech Risk"

    initiative_entity = models.UUIDField(db_index=True)
    type = models.CharField(max_length=16, choices=Type.choices)
    title = models.CharField(max_length=300)
    rating = models.CharField(max_length=1, choices=Rating.choices)
    owner = models.CharField(max_length=200, blank=True)
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    mitigation = models.TextField(blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.OWNER)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.EXEC)
    workstream_entity = models.UUIDField(null=True, blank=True, help_text="Workstream this belongs to; blank means the whole initiative")

    class Meta(VersionedModel.Meta):
        pass

    def __str__(self):
        return self.title

    @property
    def is_open(self):
        return self.status != self.Status.CLOSED


class ControlUpload(models.Model):
    """Log of control-library Excel uploads. One row per upload; Control versions reference it."""

    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.CharField(max_length=32)
    filename = models.CharField(max_length=300)
    row_count = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at"]


class Control(VersionedModel):
    control_id = models.CharField(max_length=64, db_index=True, help_text="Business key from the library")
    name = models.CharField(max_length=300)
    framework = models.CharField(max_length=100, blank=True)
    control_owner = models.CharField(max_length=200, blank=True)
    in_scope = models.BooleanField(default=True)
    trac_url = models.URLField(max_length=500, blank=True, help_text="Matching guidance page in TRAC")
    upload = models.ForeignKey(ControlUpload, null=True, blank=True, on_delete=models.PROTECT)

    class Meta(VersionedModel.Meta):
        ordering = ["control_id"]

    def __str__(self):
        return f"{self.control_id} {self.name}"


class ControlImpact(VersionedModel):
    class ImpactType(models.TextChoices):
        NEW = "new", "New"
        MODIFIED = "modified", "Modified"
        RETIRED = "retired", "Retired"
        INTERIM = "interim", "Interim"

    class Readiness(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted for review"
        SENT_BACK = "sent_back", "Sent back"
        READY = "ready", "Ready"

    initiative_entity = models.UUIDField(db_index=True)
    control_entity = models.UUIDField(db_index=True)
    impact_type = models.CharField(max_length=16, choices=ImpactType.choices)
    compensating_control = models.TextField(blank=True)
    readiness = models.CharField(max_length=16, choices=Readiness.choices, default=Readiness.NOT_STARTED)
    evidence_link = models.URLField(blank=True, max_length=500)
    review_comment = models.TextField(blank=True, help_text="Tech Risk comment when sending back")
    needed_by_milestone = models.UUIDField(
        null=True, blank=True, help_text="Key milestone the control must be ready by; blank means go-live"
    )
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.EXEC)
    workstream_entity = models.UUIDField(null=True, blank=True, help_text="Workstream this belongs to; blank means the whole initiative")

    class Meta(VersionedModel.Meta):
        pass


class Update(models.Model):
    """A posted narrative update. Immutable event; resets the staleness clock."""

    initiative_entity = models.UUIDField(db_index=True)
    author_emp_id = models.CharField(max_length=32)
    posted_at = models.DateTimeField(db_index=True)
    text = models.TextField()
    change_diff = models.JSONField(default=dict, blank=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.EXEC)
    workstream_entity = models.UUIDField(null=True, blank=True, help_text="Workstream this belongs to; blank means the whole initiative")

    class Meta:
        ordering = ["-posted_at"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Updates are immutable")
        return super().save(*args, **kwargs)


class Overlay(VersionedModel):
    class Type(models.TextChoices):
        COMMENTARY = "commentary", "Commentary"
        HIGHLIGHT = "highlight", "Highlight"
        INTERNAL_NOTE = "internal_note", "Internal note"

    initiative_entity = models.UUIDField(db_index=True)
    type = models.CharField(max_length=16, choices=Type.choices)
    text = models.TextField()
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.INTERNAL)
    order = models.PositiveSmallIntegerField(default=0, help_text="Highlight ordering on exec tile")

    class Meta(VersionedModel.Meta):
        ordering = ["order", "valid_from"]


class RagOverride(VersionedModel):
    class ClearedBy(models.TextChoices):
        MANUAL = "manual", "Cleared manually"
        AUTO = "auto_expired", "Expired (calculated status worsened)"

    initiative_entity = models.UUIDField(db_index=True)
    value = models.CharField(max_length=8, choices=Rag.choices)
    reason = models.TextField(help_text="Why the derived RAG overstates the position")
    support = models.TextField(blank=True, default="", help_text="Tickets, test plans, evidence links, owner updates")
    review_by = models.DateField(null=True, blank=True)
    derived_at_set = models.CharField(max_length=8, choices=Rag.choices, default="green")
    effective_from = models.DateTimeField()
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.CharField(max_length=16, choices=ClearedBy.choices, blank=True)

    class Meta(VersionedModel.Meta):
        pass

    @property
    def is_active(self):
        return self.cleared_at is None


class RagEvaluation(models.Model):
    """Append-only log of derived/effective RAG per recompute. Latest row per initiative is current."""

    initiative_entity = models.UUIDField(db_index=True)
    computed_at = models.DateTimeField(db_index=True)
    derived = models.CharField(max_length=8, choices=Rag.choices)
    effective = models.CharField(max_length=8, choices=Rag.choices)
    rule_fired = models.CharField(max_length=200, blank=True)
    override_entity = models.UUIDField(null=True, blank=True)
    stale_days = models.IntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-computed_at"]


class Snapshot(models.Model):
    taken_at = models.DateTimeField()
    taken_by = models.CharField(max_length=32)
    label = models.CharField(max_length=200)
    note = models.TextField(blank=True)
    frozen_summary = models.JSONField(default=dict, help_text="Header counts and thresholds in force")

    class Meta:
        ordering = ["-taken_at"]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Snapshots are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Snapshots are immutable")


class SnapshotRow(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.PROTECT, related_name="rows")
    initiative_entity = models.UUIDField(db_index=True)
    frozen_json = models.JSONField(help_text="Exec/audit-visible state at the time of the snapshot")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "initiative_entity"], name="one_row_per_initiative")
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Snapshot rows are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Snapshot rows are immutable")


class ThresholdConfig(VersionedModel):
    key = models.CharField(max_length=64, db_index=True)
    value = models.IntegerField()

    class Meta(VersionedModel.Meta):
        pass

    def __str__(self):
        return f"{self.key}={self.value}"


class Delegation(VersionedModel):
    initiative_entity = models.UUIDField(db_index=True)
    delegate_emp_id = models.CharField(max_length=32, db_index=True)

    class Meta(VersionedModel.Meta):
        pass


class StageCheck(VersionedModel):
    """The state of one lifecycle checklist item for one initiative. Retire (tombstone) to reopen."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        SENT_BACK = "sent_back", "Sent back"
        ACCEPTED = "accepted", "Accepted"
        CONFIRMED = "confirmed", "Confirmed by owner"
        NOT_APPLICABLE = "na", "Not applicable"

    DONE = (Status.ACCEPTED, Status.CONFIRMED, Status.NOT_APPLICABLE)

    class Basis(models.TextChoices):
        REVIEWED = "reviewed", "Evidence reviewed"
        OBSERVED = "observed", "Observed by Tech Risk"
        ATTESTED = "attested", "Owner attestation"

    initiative_entity = models.UUIDField(db_index=True)
    stage = models.PositiveSmallIntegerField(choices=STAGE_CHOICES)
    item_index = models.PositiveSmallIntegerField()
    item_text = models.CharField(max_length=300, help_text="Checklist wording at the time of confirmation")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACCEPTED)
    basis = models.CharField(max_length=16, choices=Basis.choices, blank=True)
    evidence_ref = models.CharField(max_length=500, blank=True)
    note = models.TextField(blank=True)
    comment = models.TextField(blank=True, help_text="Tech Risk comment when sending back, or the N/A reason")
    guidance_version = models.CharField(max_length=20)

    @property
    def is_done(self):
        return self.status in self.DONE

    class Meta(VersionedModel.Meta):
        pass


class GateApproval(VersionedModel):
    """A gate review: who signed off a stage exit, the decision, and where the evidence lives."""

    class Decision(models.TextChoices):
        PROCEED = "proceed", "Proceed"
        CONDITIONS = "conditions", "Proceed with conditions"
        HOLD = "hold", "Hold (do not proceed)"

    initiative_entity = models.UUIDField(db_index=True)
    stage = models.PositiveSmallIntegerField(choices=STAGE_CHOICES)
    approver = models.CharField(max_length=200, help_text="Name and role")
    approved_on = models.DateField()
    evidence_ref = models.CharField(max_length=500)
    decision = models.CharField(max_length=12, choices=Decision.choices, default=Decision.PROCEED)
    conditions = models.TextField(blank=True)

    class Meta(VersionedModel.Meta):
        pass


class GuidancePage(VersionedModel):
    """Versioned per-stage settings: name, expected approver, checklist items and the stage's TRAC page.

    Guidance prose lives in TRAC; `trac_url` links to it. Checklist items are a JSON list of strings.
    """

    stage = models.PositiveSmallIntegerField(choices=STAGE_CHOICES)
    name = models.CharField(max_length=100)
    version_label = models.CharField(max_length=20)
    change_note = models.CharField(max_length=300, blank=True)
    approver = models.CharField(max_length=300)
    checklist = models.JSONField(default=list)
    trac_url = models.URLField(max_length=500, blank=True, help_text="Stage guidance page in TRAC")

    class Meta(VersionedModel.Meta):
        ordering = ["stage"]


class GateSettings(VersionedModel):
    """Versioned singleton: the gate requirement matrix and the oversight question labels."""

    matrix = models.JSONField(help_text="{level: [None, stage 1..6 requirement]}")
    question_labels = models.JSONField(default=dict, blank=True, help_text="{field key: label}")
    version_label = models.CharField(max_length=20)
    change_note = models.CharField(max_length=300, blank=True)
    approved_by = models.CharField(max_length=200, blank=True)
    approved_on = models.DateField(null=True, blank=True)

    class Meta(VersionedModel.Meta):
        pass

    @property
    def is_approved(self):
        return bool(self.approved_by)


class CurationEvent(models.Model):
    """Append-only record of Tech Risk touching an initiative; drives 'last curated' and readiness."""

    initiative_entity = models.UUIDField(db_index=True)
    author_emp_id = models.CharField(max_length=32)
    at = models.DateTimeField(db_index=True)
    what = models.CharField(max_length=200)

    class Meta:
        ordering = ["-at"]
