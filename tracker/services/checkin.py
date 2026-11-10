"""Owner check-ins, control evidence review, oversight level and check-in due dates."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import clock
from tracker.lifecycle import QUESTION_KEYS
from tracker.models import ControlImpact, Initiative, Milestone, RiskItem, Update, Visibility
from tracker.services import rag
from tracker.services.curation import touch

Readiness = ControlImpact.Readiness
OWNER_CONTROL_CHOICES = (Readiness.NOT_STARTED, Readiness.IN_PROGRESS, Readiness.SUBMITTED)


def _fmt(value):
    return f"{value:%b} {value.day}, {value.year}" if hasattr(value, "year") else ("—" if value in (None, "") else str(value))


# --- check-in due -----------------------------------------------------------------------------------

def check_in_due(bundle, ws=None):
    """(due date, days overdue) from the last posted update, else acceptance; (None, 0) when not active.

    With `ws`, the workstream's own check-in clock; otherwise the initiative's own (untagged) one.
    """
    init = bundle.initiative
    if init.status != Initiative.Status.ACTIVE:
        return None, 0
    if ws is not None:
        last = bundle.ws_checkin_base(ws)
    else:
        own = bundle.own_last_update
        last = own.posted_at if own else init.accepted_at
    if last is None:
        return None, 0
    due = clock.local_date(last) + timedelta(days=settings.CHECKIN_INTERVAL_DAYS)
    return due, max((clock.today() - due).days, 0)


# --- oversight level ----------------------------------------------------------------------------------

@transaction.atomic
def set_oversight(initiative, author_emp_id, answers, override="", reason="", sox_applicable=None):
    """Tech Risk updates the intake answers, the SOX flag (None keeps it) and/or overrides the level (a reason is required to override)."""
    if override and not reason.strip():
        raise ValueError("Enter a reason for the oversight override.")
    changes = {key: bool(answers.get(key)) for key in QUESTION_KEYS}
    changes["oversight_override"] = override
    changes["oversight_override_reason"] = reason.strip() if override else ""
    if sox_applicable is not None:
        changes["sox_applicable"] = bool(sox_applicable)
    if all(getattr(initiative, k) == v for k, v in changes.items()):
        return initiative
    new = initiative.new_version(author_emp_id, **changes)
    touch(new.entity_id, author_emp_id, f"oversight level: {new.oversight}")
    return new


# --- control evidence review ----------------------------------------------------------------------------

@transaction.atomic
def accept_control(impact, author_emp_id):
    if impact.readiness != Readiness.SUBMITTED:
        raise ValueError("Only submitted controls can be accepted")
    new = impact.new_version(author_emp_id, readiness=Readiness.READY, review_comment="")
    touch(impact.initiative_entity, author_emp_id, "control accepted")
    return new


@transaction.atomic
def send_back_control(impact, author_emp_id, comment):
    if impact.readiness != Readiness.SUBMITTED:
        raise ValueError("Only submitted controls can be sent back")
    if not comment.strip():
        raise ValueError("Enter a comment to send the control back.")
    new = impact.new_version(author_emp_id, readiness=Readiness.SENT_BACK, review_comment=comment)
    touch(impact.initiative_entity, author_emp_id, "control sent back")
    return new


# --- the check-in -------------------------------------------------------------------------------------

@transaction.atomic
def post_check_in(initiative, author_emp_id, text, *, go_live=None, milestone=None, forecast=None,
                  pct_complete=None, controls=None, new_risk=None, workstream=None):
    """Apply an owner check-in in one step and post the update with a diff of what changed.

    workstream: the Workstream this check-in is for, or None for the whole initiative.

    controls: {impact_entity: (readiness, evidence_link)} with readiness from OWNER_CONTROL_CHOICES.
    new_risk: (title, rating) or None.
    """
    if not text.strip():
        raise ValueError("Describe what has changed.")
    diff = {}
    ws_entity = workstream.entity_id if workstream else None
    if go_live and go_live != initiative.target_go_live:
        diff["Go-live"] = [_fmt(initiative.target_go_live), _fmt(go_live)]
        initiative = initiative.new_version(author_emp_id, target_go_live=go_live)
    if milestone is not None:
        ms_changes = {}
        if forecast and forecast != milestone.forecast:
            diff[f"{milestone.name} · forecast"] = [_fmt(milestone.forecast), _fmt(forecast)]
            ms_changes["forecast"] = forecast
        if pct_complete is not None and pct_complete != milestone.pct_complete:
            diff[f"{milestone.name} · % complete"] = [f"{milestone.pct_complete}%", f"{pct_complete}%"]
            ms_changes["pct_complete"] = pct_complete
        if ms_changes:
            if forecast and milestone.planned and milestone.status != Milestone.Status.DONE:
                ms_changes["status"] = Milestone.Status.SLIPPED if forecast > milestone.planned else Milestone.Status.ON_TRACK
            milestone.new_version(author_emp_id, **ms_changes)
    for impact in ControlImpact.objects.current().filter(initiative_entity=initiative.entity_id):
        if impact.entity_id not in (controls or {}):
            continue
        readiness, evidence = controls[impact.entity_id]
        if readiness not in OWNER_CONTROL_CHOICES or impact.readiness == Readiness.READY:
            continue
        if readiness == Readiness.SUBMITTED and not (evidence or "").strip():
            raise ValueError("Enter an evidence link to submit the control for review.")
        changes = {}
        if readiness != impact.readiness:
            changes["readiness"] = readiness
        if readiness == Readiness.SUBMITTED and evidence != impact.evidence_link:
            changes["evidence_link"] = evidence
        if changes:
            label = impact.get_readiness_display()
            changes.setdefault("review_comment", impact.review_comment if readiness == impact.readiness else "")
            new = impact.new_version(author_emp_id, **changes)
            diff[f"Control {_control_ref(impact)}"] = [label, new.get_readiness_display()]
    if new_risk and new_risk[0].strip():
        title, rating = new_risk
        RiskItem.create(
            author_emp_id, initiative_entity=initiative.entity_id, type=RiskItem.Type.RISK, title=title.strip(),
            rating=rating, owner="", source=RiskItem.Source.OWNER, visibility=Visibility.EXEC,
            workstream_entity=ws_entity,
        )
        diff["New risk"] = ["—", f"{title.strip()} ({rating})"]
    update = Update.objects.create(
        initiative_entity=initiative.entity_id, author_emp_id=author_emp_id, posted_at=timezone.now(),
        text=text.strip(), change_diff=diff, workstream_entity=ws_entity,
    )
    rag.recompute(initiative)
    return update


def _control_ref(impact):
    from tracker.models import Control

    control = Control.objects.current().filter(entity_id=impact.control_entity).first()
    return control.control_id if control else "?"
