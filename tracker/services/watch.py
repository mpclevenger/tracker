"""Watch list: lightweight, unconfirmed items that may become initiatives."""

from datetime import date

from django.db import transaction

from core import clock
from tracker.lifecycle import QUESTION_KEYS
from tracker.models import Initiative
from tracker.services import curation

Status = Initiative.Status

WATCH_FIELDS = (
    "name", "description", "owner_emp_id", "decision", "decision_due", "key_date", "key_date_label",
    "options_considered", "next_check", "sox_applicable", *QUESTION_KEYS,
)
DECISION_WINDOW_DAYS = 30
KEY_DATE_WINDOW_DAYS = 90


def _clean(fields: dict) -> dict:
    unknown = set(fields) - set(WATCH_FIELDS)
    if unknown:
        raise ValueError(f"Not a watch list field: {', '.join(sorted(unknown))}")
    return fields


def _require_watch(item):
    if item.status != Status.WATCH:
        raise ValueError(f"{item.name} is not on the watch list.")


@transaction.atomic
def create_watch_item(emp_id, **fields):
    """Add an item to the watch list."""
    item = Initiative.create(emp_id, status=Status.WATCH, phase="", **_clean(fields))
    curation.touch(item.entity_id, emp_id, "added to watch list")
    return item


@transaction.atomic
def update_watch_item(item, emp_id, **fields):
    """Save changed fields as a new version; returns the item unchanged when nothing differs."""
    _require_watch(item)
    changes = {k: v for k, v in _clean(fields).items() if v != getattr(item, k)}
    return item.new_version(emp_id, **changes) if changes else item


@transaction.atomic
def confirm_watch_item(item, emp_id):
    """Move to the intake queue as a proposed initiative; Tech Risk accepts it there."""
    _require_watch(item)
    new = item.new_version(emp_id, status=Status.PROPOSED, phase="Intake", submitted_at=clock.now())
    curation.touch(new.entity_id, emp_id, "confirmed from watch list")
    return new


@transaction.atomic
def drop_watch_item(item, emp_id, reason):
    """Close an item with no change needed; the reason is required."""
    _require_watch(item)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Enter a reason.")
    new = item.new_version(emp_id, status=Status.DROPPED, outcome_reason=reason)
    curation.touch(new.entity_id, emp_id, "dropped from watch list")
    return new


def watch_warnings(item, today: date | None = None) -> list[str]:
    """Short plain chips for dates that need attention."""
    today = today or clock.today()
    chips = []
    if item.decision_due:
        n = (item.decision_due - today).days
        if n < 0:
            chips.append(f"Decision overdue {-n}d")
        elif n <= DECISION_WINDOW_DAYS:
            chips.append(f"Decision due in {n}d")
    if item.key_date:
        n = (item.key_date - today).days
        if 0 <= n <= KEY_DATE_WINDOW_DAYS:
            chips.append(f"{item.key_date_label or 'Key date'} in {n}d, no decision")
    if item.next_check and item.next_check < today:
        chips.append("Check due")
    return chips


def open_items(scope: str = "all") -> list:
    """Open watch items sorted by decision due, blanks last; scope "sox" keeps SOX applicable ones."""
    qs = Initiative.objects.watch_items()
    if scope == "sox":
        qs = qs.filter(sox_applicable=True)
    return sorted(qs, key=lambda i: (i.decision_due is None, i.decision_due or date.max, i.name.lower()))


def history(scope: str = "all") -> list[dict]:
    """Items that left the watch list: [{item, outcome, on}] newest first."""
    ids = Initiative.objects.filter(status=Status.WATCH).values("entity_id")
    qs = Initiative.objects.current().filter(entity_id__in=ids).exclude(status=Status.WATCH)
    if scope == "sox":
        qs = qs.filter(sox_applicable=True)
    out = []
    for item in qs:
        versions = list(Initiative.objects.history(item.entity_id))
        left = next((v for v in versions if v.status != Status.WATCH), None)
        dropped = item.status == Status.DROPPED
        out.append({
            "item": item,
            "outcome": f"No change needed: {item.outcome_reason}" if dropped else "Confirmed as initiative",
            "on": left.valid_from if left else item.valid_from,
        })
    return sorted(out, key=lambda r: r["on"], reverse=True)


def freeze(items=None) -> list[dict]:
    """Snapshot form of the open watch items."""
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return [
        {"name": i.name, "decision": i.decision, "decision_due": iso(i.decision_due),
         "key_date_label": i.key_date_label, "key_date": iso(i.key_date), "sox_applicable": i.sox_applicable}
        for i in (open_items() if items is None else items)
    ]


def thaw(rows) -> list[dict]:
    """Frozen rows back to dates, sorted by decision due with blanks last."""
    d = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
    out = [{**r, "decision_due": d(r.get("decision_due")), "key_date": d(r.get("key_date"))} for r in rows or []]
    return sorted(out, key=lambda r: (r["decision_due"] is None, r["decision_due"] or date.max, r["name"].lower()))
