"""Workbench Triage rows and snapshot readiness (DESIGN.md → Tech Risk workbench)."""

from dataclasses import dataclass

from django.conf import settings

from core import clock
from meetings import services as meetings_services
from tracker.models import ControlImpact, Initiative, RAG_ORDER, RiskItem, Visibility
from tracker.services import checkin, lifecycle, rag, snapshots, watch
from tracker.services.bundle import Bundle, load_bundles

GROUPS = ["Decide", "Confirm", "Review", "Chase", "Watch"]
GROUP_LABELS = {"Decide": "Intake decisions", "Confirm": "Risks to confirm", "Review": "Reviews due", "Chase": "Overdue",
                "Watch": "Watch list"}


@dataclass
class WatchRef:
    """Stands in for a bundle on watch list rows."""

    initiative: Initiative
    last_curated: object = None

    @property
    def entity_id(self):
        return self.initiative.entity_id


@dataclass
class Row:
    bundle: Bundle
    what: str
    since: object = None
    severity: str = "grey"  # red | amber | green | grey
    action: str = "curate"  # curate | decide | review | remind
    tab: str = "overview"  # initiative page tab the row opens
    reminder: str = ""  # prefilled reminder text for "remind" rows
    watch: bool = False  # watch list row: links to the watch item page


def triage(followups: bool = True, scope: str = "all", watch_list: bool = True) -> tuple[dict, list]:
    """Returns ({group: [Row]}, [quiet bundles]); followups=False drops follow-up rows; scope="sox" keeps SOX applicable ones; watch_list=False drops watch rows."""
    rows = {g: [] for g in GROUPS if g != "Watch"}  # "Watch" is added only when it has rows
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
        for f in followups_by_initiative.get(b.entity_id, []):
            rows["Chase"].append(Row(b, f"Follow-up overdue {f.days_overdue}d: {f.text[:80]}", f.due_date, "red"))

    if watch_list:
        for item in watch.open_items(scope):
            chips = watch.watch_warnings(item)
            if chips:
                rows.setdefault("Watch", [])
                overdue = any("overdue" in c for c in chips)
                rows["Watch"].append(Row(WatchRef(item), ", ".join(chips), None,
                                         "red" if overdue else "amber", "review", watch=True))

    for g in GROUPS:
        for r in rows.get(g, []):
            flagged.add(r.bundle.entity_id)
    quiet = [b for b in active if b.entity_id not in flagged]
    return rows, quiet


def reminder_text(bundle, base_url: str = "") -> str:
    """Two-line nudge Tech Risk can paste into Teams or email."""
    from core.models import Person

    owner = Person.objects.filter(pk=bundle.initiative.owner_emp_id).first()
    first = owner.display_name.split()[0] if owner else "there"
    return (f"Hello {first}, your check-in for {bundle.initiative.name} is overdue.\n"
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
