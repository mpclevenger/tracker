"""Workbench Triage rows and snapshot readiness (DESIGN.md → Tech Risk workbench)."""

from dataclasses import dataclass
from uuid import UUID

from django.conf import settings

from core import clock
from meetings import services as meetings_services
from tracker.models import ControlImpact, Initiative, RAG_ORDER, RiskItem, Visibility
from tracker.services import checkin, comments, lifecycle, rag, snapshots, watch
from tracker.services.bundle import Bundle, load_bundles

GROUPS = ["Decide", "Confirm", "Review", "Chase", "Comments", "Watch"]
SERIES_DUE_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 31}
GROUP_LABELS = {"Decide": "Intake decisions", "Confirm": "Risks to confirm", "Review": "Reviews due", "Chase": "Overdue",
                "Watch": "Watch list", "Comments": "Comments to review"}


@dataclass
class WatchRef:
    """Stands in for a bundle on watch list rows."""

    initiative: Initiative
    last_curated: object = None

    @property
    def entity_id(self):
        return self.initiative.entity_id


@dataclass
class SeriesRef:
    """Stands in for a bundle on meeting series rows."""

    series: object

    @property
    def entity_id(self):
        return self.series.entity_id


@dataclass
class Row:
    bundle: Bundle
    what: str
    since: object = None
    severity: str = "grey"  # red | amber | green | grey
    action: str = "curate"  # curate | decide | review | remind
    tab: str = "overview"  # initiative page tab the row opens
    reminder: str = ""  # prefilled reminder text for "remind" rows
    person: str = ""  # emp id shown instead of the owner (a workstream lead)
    ws: object = None  # workstream entity for per-workstream rows
    watch: bool = False  # watch list row: links to the watch item page
    series: object = None  # meeting series row: links to the series page
    anchor: str = ""  # page anchor appended to the row link


def triage(followups: bool = True, scope: str = "all", watch_list: bool = True) -> tuple[dict, list]:
    """Returns ({group: [Row]}, [quiet bundles]); followups=False drops follow-up rows; scope="sox" keeps SOX applicable ones; watch_list=False drops watch rows."""
    rows = {g: [] for g in GROUPS if g not in ("Watch", "Comments")}  # these two are added only when they have rows
    t = rag.thresholds()
    snap = snapshots.last_snapshot()
    snap_rows = snapshots.rows_by_entity(snap) if snap else {}
    guidance = lifecycle.current_guidance()

    current = Initiative.objects.current().listed()
    if scope == "sox":
        current = current.filter(sox_applicable=True)

    for b in load_bundles(current.filter(status=Initiative.Status.PROPOSED)):
        rows["Decide"].append(Row(b, "Intake submitted", b.initiative.submitted_at, "grey", "decide"))

    active = load_bundles(current.filter(status=Initiative.Status.ACTIVE))
    flagged = set()
    followups_by_initiative = {}
    for f in meetings_services.overdue_followups([b.entity_id for b in active]) if followups else []:
        followups_by_initiative.setdefault(f.initiative_entity, []).append(f)
    for b in active:
        ev = rag.evaluate(b, t, persist=False)
        init = b.initiative

        if ev.days_to_go_live is not None and ev.days_to_go_live <= t["red_go_live_window_days"]:
            nr = [i for i in b.impacts if i.readiness != "ready"]
            if nr:
                codes = ", ".join(b.control_for(i).control_id for i in nr if b.control_for(i))
                rows["Confirm"].append(Row(
                    b, f"Go-live in {ev.days_to_go_live}d; {len(nr)} control{'s' if len(nr) > 1 else ''} not ready ({codes})",
                    None, "red"))
        for r in b.risks:
            if r.rating == RiskItem.Rating.HIGH and r.is_open:
                past = r.target_date and r.target_date < clock.today()
                rows["Confirm"].append(Row(
                    b, f"High {r.get_type_display().lower()}: {r.title}{' — past target' if past else ''}",
                    r.target_date, "red" if past else "amber"))
        stages = lifecycle.stage_statuses(b, guidance)
        cur = b.current_stage()
        for s in stages:
            if s.number < cur and not s.complete:
                missing = [] if s.items_done else [f"{s.done}/{s.total} items done"]
                if s.needs_signoff and not s.signed_off:
                    missing.append("sign-off not recorded")
                rows["Confirm"].append(Row(b, f"{s.name}: {', '.join(missing)}", None, "amber", tab="lifecycle"))
            for c in s.submitted:
                rows["Review"].append(Row(b, f"Evidence submitted: {c.item_text[:80]}", c.valid_from, "grey",
                                          "review", tab="lifecycle"))
        for i in b.impacts:
            if i.readiness == ControlImpact.Readiness.SUBMITTED:
                ref = b.control_for(i).control_id if b.control_for(i) else "control"
                rows["Review"].append(Row(b, f"Evidence submitted: {ref}", i.valid_from, "grey", "review", tab="risks"))

        prev = snap_rows.get(init.entity_id)
        if prev and prev["effective"] != ev.effective:
            worse = RAG_ORDER[ev.effective] > RAG_ORDER[prev["effective"]]
            rows["Review"].append(Row(
                b, f"RAG changed since {snap.label}: {prev['effective']} → {ev.effective}", snap.taken_at,
                "amber" if worse else "green"))
        if ev.override:
            overdue = ev.override.review_by and ev.override.review_by < clock.today()
            rows["Review"].append(Row(
                b, f"Override {ev.override.value} in place (derived {ev.derived}) — review by "
                   f"{ev.override.review_by:%b} {ev.override.review_by.day}, {ev.override.review_by.year}" if ev.override.review_by else
                   f"Override {ev.override.value} in place (derived {ev.derived})",
                ev.override.effective_from, "red" if overdue else "grey"))

        due, overdue = checkin.check_in_due(b)
        if overdue:
            rows["Chase"].append(Row(
                b, f"Check-in overdue {overdue}d", due, "red" if ev.is_stale else "amber", "remind",
                reminder=reminder_text(b)))
        for ws in b.workstreams:
            due, overdue = checkin.check_in_due(b, ws)
            if overdue:
                stale = rag.stale_days(b, ws) or 0
                rows["Chase"].append(Row(
                    b, f"{ws.name}: check-in overdue {overdue}d", due,
                    "red" if stale > t["red_stale_days"] else "amber", "remind",
                    reminder=reminder_text(b, ws=ws), person=ws.lead_emp_id or init.owner_emp_id, ws=ws.entity_id))
        for f in followups_by_initiative.get(b.entity_id, []):
            rows["Chase"].append(Row(b, f"Follow-up overdue {f.days_overdue}d: {f.text[:80]}", f.due_date, "red"))

    if followups:
        rows["Chase"].extend(_missed_series_rows(set(current.values_list("entity_id", flat=True)), scope))

    if watch_list:
        for item in watch.open_items(scope):
            chips = watch.watch_warnings(item)
            if chips:
                rows.setdefault("Watch", [])
                overdue = any("overdue" in c for c in chips)
                rows["Watch"].append(Row(WatchRef(item), ", ".join(chips), None,
                                         "red" if overdue else "amber", "review", watch=True))

    for row in _comment_rows(current, watch_list, scope):
        rows.setdefault("Comments", []).append(row)

    for g in GROUPS:
        for r in rows.get(g, []):
            flagged.add(r.bundle.entity_id)
    quiet = [b for b in active if b.entity_id not in flagged]
    return rows, quiet


def _comment_rows(current, watch_list, scope):
    """One Review-style row per pending comment on an in-scope initiative or watch item."""
    pending = comments.pending()
    items = {i.entity_id: i for i in current.filter(entity_id__in=[c.target_entity for c in pending])}
    watch_items = {}
    if watch_list:
        found = Initiative.objects.watch_items().filter(entity_id__in=[c.target_entity for c in pending])
        watch_items = {i.entity_id: i for i in (found.filter(sox_applicable=True) if scope == "sox" else found)}
    out = []
    for c in pending:
        item = items.get(c.target_entity) or watch_items.get(c.target_entity)
        if item is None:
            continue
        is_watch = item.status in Initiative.WATCH_STATUSES
        ref = WatchRef(item)
        out.append(Row(ref, f"{item.name}: {c.text[:60]}", c.posted_at, "grey", "review", watch=is_watch, anchor="#comments"))
    return out


def _missed_series_rows(in_scope_ids, scope):
    """Chase rows for non-ad-hoc meeting series whose last meeting is older than the cadence."""
    from meetings.models import MeetingSeries

    series = [s for s in MeetingSeries.objects.current() if s.cadence in SERIES_DUE_DAYS]
    last = {k: v[1] for k, v in meetings_services.series_summaries(series).items()}
    out = []
    for srs in sorted(series, key=lambda x: x.name.lower()):
        if scope == "sox" and not (in_scope_ids & {UUID(e) for e in srs.initiative_entities}):
            continue
        held = last[srs.entity_id]
        since = held or MeetingSeries.objects.history(srs.entity_id).first().valid_from.date()
        if (clock.today() - since).days <= SERIES_DUE_DAYS[srs.cadence]:
            continue
        what = f"{srs.name}: no meeting logged since {since:%b} {since.day}" if held else f"{srs.name}: no meetings logged yet"
        out.append(Row(SeriesRef(srs), what, since, "amber", "review", series=srs))
    return out


def reminder_text(bundle, base_url: str = "", ws=None) -> str:
    """Two-line nudge Tech Risk can paste into Teams or email; for a workstream it addresses the lead."""
    from core.models import Person

    emp_id = (ws.lead_emp_id or bundle.initiative.owner_emp_id) if ws else bundle.initiative.owner_emp_id
    owner = Person.objects.filter(pk=emp_id).first()
    first = owner.display_name.split()[0] if owner else "there"
    what = f"{ws.name} check-in" if ws else "check-in"
    return (f"Hello {first}, your {what} for {bundle.initiative.name} is overdue.\n"
            f"Please complete it here: {settings.SITE_URL or base_url}/initiative/{bundle.entity_id}/")


@dataclass
class Readiness:
    bundle: Bundle
    highlights: int
    highlights_ok: bool
    curated_ok: bool
    override: object
    override_ok: bool
    stale: bool
    stale_ok: bool

    @property
    def ready(self):
        return self.highlights_ok and self.curated_ok and self.override_ok and self.stale_ok


def readiness() -> list[Readiness]:
    snap = snapshots.last_snapshot()
    t = rag.thresholds()
    out = []
    for b in load_bundles(Initiative.objects.current().listed().filter(status=Initiative.Status.ACTIVE)):
        ev = rag.evaluate(b, t, persist=False)
        hl = sum(1 for o in b.highlights if o.visibility == Visibility.EXEC)
        since = snap.taken_at if snap else None
        curated_ok = since is None or (b.last_curated is not None and b.last_curated > since)
        override_ok = ev.override is None or since is None or ev.override.effective_from > since
        stale_ok = (not ev.is_stale) or (
            b.last_curated is not None and (b.last_update is None or b.last_curated > b.last_update.posted_at)
        )
        out.append(Readiness(b, hl, 1 <= hl <= 3, curated_ok, ev.override, override_ok, ev.is_stale, stale_ok))
    return out
