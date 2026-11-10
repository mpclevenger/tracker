# WORK_NOTES

## Goal / status
v1 of the IT Initiative Risk Tracker (spec: `docs/SPEC.md`). Internal demo first, built from the
Tech Risk view; role splitting is already enforced in code and demonstrable via the dev user switcher.

| Phase | Scope | Status |
|---|---|---|
| 1 | Skeleton, auth switch, roles, versioned data model, tests | done |
| 2 | Data model extensions + domain services (RAG, curation, lifecycle, snapshots, triage, timeline, exec) | done |
| 3 | Pages: exec summary, initiative + lifecycle, owner + intake, workbench + audit | done |
| 4 | Service tests, integration pass, handoff tarball | done (Docker build still unverified — no daemon here) |
| 5 | Guidance: `name` on GuidancePage (rename stages without code), wiki import | pending |

Design is frozen in `docs/DESIGN.md` (from the clickable demo, Nov 5). Page build contract: `docs/UI_CONTRACT.md`.

## Decisions
- Demo-first: the clickable demo (artifact) is the visual spec; `docs/DESIGN.md` records every decision from it.
- The application ships with no data. `manage.py bootstrap` seeds guidance v1.0 and the first Tech Risk
  user. All fake data (people, controls, the SailPoint Onboarding example) lives in
  `scripts/generate_fake_data.py`, used by tests and local development only; `.dockerignore` excludes
  `scripts/`, `docs/`, tests and these notes from the image.
- Lifecycle: 7 fixed stage numbers/colors in `tracker/lifecycle.py`; all guidance *content* (gate, approver,
  method, controls, evidence, checklist) lives in versioned `GuidancePage` rows, edited in-app.
- Checklist confirmations (`StageCheck`) require basis + evidence ref and record the guidance version;
  gate approvals (`GateApproval`) record approver/date/evidence; both versioned. Closure requires Stage 6 complete.
- Override requires reasoning + supporting evidence + review-by; auto-expires when derived gets worse than
  `derived_at_set` (DESIGN.md), not worse than the override value.
- `home` renders the exec summary in place for exec roles (tests expect 200 on `/`).
- Auth modes: `open` (default outside tests; everyone is Tech Risk, for the build/test env), `dev` (switcher),
  `sso` (two hooks in `core/auth/sso.py`: identity header; optional groups→roles map merged with
  RoleAssignment). Teammate wires the entitlement add-on in that one file.
- Stack: Python 3.12 / Django 5.2, server-rendered templates, Bootstrap 5 static CSS, HTMX for
  small partial swaps, no custom JS, Postgres via `DATABASE_URL`, gunicorn + whitenoise, one Dockerfile.
- Versioning is hand-rolled (`core/versioning.py`), not a third-party history lib: every mutable
  entity is row-per-version with `entity_id` (stable UUID), `[valid_from, valid_to)`, `author_emp_id`,
  tombstone `is_deleted`. Cross-entity references use `entity_id`, never row pk. `objects.current()`,
  `objects.as_of(t)`, `objects.history(entity_id)`. Rows are immutable; `new_version()` supersedes.
- Immutable event tables (not versioned): `Update`, `RagEvaluation`, `Snapshot`, `SnapshotRow`,
  `ControlUpload`. Snapshot models block `save()` on existing rows and `delete()`.
- Auth: `AUTH_MODE=dev` (session user switcher at `/auth/switch/`, default `DEV_DEFAULT_EMP_ID`)
  or `AUTH_MODE=sso` (`core/auth/sso.py::emp_id_from_request`; set `SSO_EMP_ID_HEADER` for a trusted
  header, or replace the function). Authorisation always comes from `RoleAssignment` + ownership,
  never IdP groups. Django's contrib.auth/admin are not used.
- Visibility is one field with nested layers `internal ⊂ owner ⊂ exec`. `Actor.visible_layers()`
  and `tracker/visibility.py` are the only places that decide what a role may read.
- Time: calendar days in America/New_York (`core/clock.py`). `USE_TZ=True`, DB stores UTC.
- Slip baseline: `Initiative.baseline_go_live` is fixed at acceptance; RAG "go-live slipped" rule
  compares against it. Trend vs last snapshot is a display concern (Phase 3).
- Snapshots will freeze per-initiative exec/audit-visible state plus header counts and thresholds
  in force (`Snapshot.frozen_summary`).
- Control Excel template: `control_id, name, framework, control_owner, in_scope` (Phase 4).
- Exec/audit roles do not see `proposed` initiatives; owners see their own at any status.

## v1 build summary (Nov 5)
- 337 tests pass: versioning/as-of, actor/auth, visibility, RAG rules at thresholds, override expiry,
  snapshots (immutability, frozen content, comparison), triage groups, readiness, lifecycle checks/gates,
  exec context per role, timelines, and view tests for every page and action.
- Smoke-rendered every GET page as Tech Risk, owner, CTO, auditor and a no-role user: no 500s; access
  pattern matches the spec (owner/CTO/auditor 403 on Tech Risk pages; unknown user 404 on initiatives).
- Exercised: take snapshot → snapshot view; set override → modal + exec badge; close refused while Stage 6
  incomplete; owner posts update → staleness reset.
- Integration fixes after the parallel page build: `MESSAGE_TAGS` error→danger; Tech Risk actions
  (Accept/Return, Close, Archive) added to the initiative page; accept/return honour `next`; go-live RAG
  rules now also fire once the go-live date has passed without closure; snapshot-mode tiles compute
  stale/days from the snapshot date and frozen thresholds.

- `scripts/load_initiative.py` loads one initiative from a YAML file (template: `scripts/initiative_template.yaml`,
  SailPoint-shaped). Needs PyYAML (`requirements-dev.txt`). Refuses duplicates by name. YAML `on:` is a
  boolean in YAML 1.1, so date keys are `date:`; long text fields use block style to avoid flow-mapping commas.

## Nov 8 changes
- **Lifecycle split with TRAC (option A).** Guidance prose removed from the tracker (migration 0003 drops the
  columns after filling stage names). `GuidancePage` now holds name, approver, checklist, `trac_url`. Content
  for TRAC exported to `docs/TRAC_HANDOFF_lifecycle.md`; once TRAC publishes the seven pages, paste each URL
  via Lifecycle & guidance → Edit stage → TRAC link. Stage names are editable in-app (timeline legend and panels
  read them from the DB; colors stay fixed in code).
- **Tracker stays its own app** (not moved into TRAC). TRAC chat must drop "move the initiative tracker in".
- **Meetings**: flat log (date, title, attendees, key points, linked initiatives) with follow-ups (owner, due,
  open/done + closure note). Tech Risk-only edits. Overdue follow-ups highlighted (Triage Chase, exec Flags,
  initiative card) — no RAG effect. Built in the `meetings` app.
- Professional wording polish is planned as a final pass across both apps; current text is working copy.

## Round 2 build (Nov 8–9) — demo turned into the real app
- Design captured in `docs/DESIGN.md` → "Round 2 decisions"; demo refreshed in `docs/demo/`.
- Data model (migrations tracker 0004, meetings 0002): oversight inputs + override on `Initiative`; `StageCheck.status`
  (submitted / sent back / accepted / confirmed / N/A) with `comment`; `GateApproval.decision` + `conditions`;
  `ControlImpact.Readiness` adds submitted / sent back (old "not ready" mapped to not started) + `review_comment`;
  `Control.trac_url`; meetings renamed to what / who / result + `stage`.
- Services: RAG rules emit plain chips + brief detail (`fired = [(level, chip, detail)]`); `services/checkin.py`
  (check-in, control review, oversight, check-in due at `CHECKIN_INTERVAL_DAYS = 14`); lifecycle evidence flow and
  sign-off decisions; triage Review rows for submitted evidence and Chase "Check-in overdue" with a reminder text
  (`SITE_URL` setting for the link); exec Pulse check data (like-for-like deltas, movers) and Next 90 days controls.
- Gate matrix by oversight level is a code constant (`tracker/lifecycle.py::GATE_MATRIX`), marked draft pending
  Head of Tech Risk approval — change in code only, then set `GATE_MATRIX_IS_DRAFT = False`.
- Pages: exec summary (Portfolio, Next 90 days, Pulse check), initiative tabs (`?tab=`), check-in modal, oversight
  and people modals, lifecycle item actions (submit / accept / send back / N/A / confirm / reopen) and sign-off with
  decision, guidance page with level pills, workbench Review rows and reminder modal, meetings with stage tag.
- Removed: access model and control mapping pages (now TRAC links), guidance change history, old post-update and
  stage-confirm flows, `PRINCIPLES`.
- Checks (Nov 9): `manage.py check` clean, `makemigrations --check` clean, pytest 440 passed; smoke GET of 44 routes
  (all initiative tabs) as Tech Risk, owner, CTO, auditor and unknown on sqlite + fake data: no server errors.
- Migration 0004 maps control readiness "not ready" to "not started"; meetings 0002 renames title/attendees/key
  points to what/who/result.

## Round 3 (Nov 9): open items, role reviews, wording
- Fixed: as-of date shows the latest snapshot on or before it; guidance edits move item records with their item
  (`services/lifecycle.save_guidance`); reminder link falls back to the request host when `SITE_URL` is unset;
  history shows readable field names; exec tiles show "Why red/amber"; control tooltips show name and readiness.
- Removed dead `tracker/views.py` and `templates/tracker/home.html` (shadowed by the `tracker/views/` package).
- Role reviews (CTO, Internal Audit, Head of Tech Risk, owner) run; decisions in `docs/DESIGN.md` "Round 3".
- Gate requirements and question labels editable and versioned (`GateSettings`, Workbench > Admin); Draft until
  approver recorded. New defaults: Light stages 3, 5, Close = Tech Risk review; Tier 0/1 alone = Enhanced.
- Snapshots freeze oversight, stages, sign-offs, controls and versions; Audit page links snapshot records.
- Wording pass: US spelling, plain terms (No check-in, Status summary, Reviews due), actionable error messages.
- Migrations 0005 (US stage names, as new versions), 0006 (labels), 0007 (gate settings); core 0002 (labels).
- Checks: pytest 464 passed; check and makemigrations clean; smoke GET of 45 routes x 5 roles: no server errors.
- Deferred by decision: self-review blocking (same person submits and accepts); DB-level append-only triggers.
- Seeded checklist wording changes apply to new installs only; existing installs edit them in the app.

## Module switches + runbook (Nov 9)
- `core.models.ModuleSettings` (versioned singleton, no row = all on), `core/modules.py`; middleware caches
  `request.modules`, wraps the actor (owner/auditor rights dropped when off) and 404s gated URLs.
- Admin card "Modules"; Tech Risk banner lists modules that are off. Migration core 0003.
- `docs/RUNBOOK.md`: weekly routine, leadership pack, settings map, deploy, backup, symptoms, Windows test copy.
- Checks: pytest 482 passed; check and makemigrations clean; smoke 46 routes x 5 roles with all modules on and
  all off: no server errors.

## Controls needed by (Nov 9)
- `ControlImpact.needed_by_milestone` (migration 0008), `Bundle.control_due`; RAG control rules use each
  control's due date; Controls lane on the initiative timeline; editor select; loader `needed_by:`; snapshots
  freeze needed-by and due date. Checks: pytest 506 passed; check/makemigrations clean; smoke: no errors.

## Delete + TRAC item anchors (Nov 9)
- Tech Risk "Delete initiative…" retires the initiative and its open follow-ups (reason in CurationEvent, name
  typed to confirm); history and snapshots keep it. Item "Guidance" links open `<stage TRAC URL>#item-N`.
- Checks: pytest 515 passed; check/makemigrations clean; delete flow verified on fake data.

## SOX applicable + tracker as an initiative (Nov 9)
- `Initiative.sox_applicable` (migration 0009, backfilled from `affects_sox_app`), SOX pill, `?scope=sox|all`
  on exec summary, triage and audit (auditors default to sox). `scripts/initiatives/risk_tracker.yaml` tracks
  this app (CHANGE_ME / CONFIRM placeholders for the user). Checks: pytest 535 passed; YAML loads; smoke clean.

## Watch list (Nov 10)
- Status watch/dropped, fields decision/decision_due/key_date(+label)/options_considered/next_check/
  outcome_reason (migration tracker 0010), module switch `watch_list` (core 0004), `services/watch.py`,
  `views/watch.py`, `Initiative.objects.listed()` excludes watch/dropped everywhere. Exec block frozen in
  snapshot summary. Checks: pytest 568 passed; smoke 54 routes x 5 roles clean; confirm/drop verified.

## UI fixes, page polish, workstreams (Nov 10)
- Name fields relabeled; New initiative button and Tech Risk redirect to the new initiative; intake form
  trimmed (essentials + "More details"); timeline names link to initiative pages (not on shared links).
- Initiative page redesign (header + Actions menu, read-only Overview, Exec view tab, tidier Risks & controls).
- Workstreams (migration 0011, `services/workstreams.py`): per-workstream status and check-ins, worst-of roll-up,
  lead rights, exec tile roll-up line, stage-span bars on the portfolio timeline, loader `workstreams:`.
- Fake data adds "Data lake rollout" with four workstreams. Checks: pytest 596 passed; screenshots reviewed.
- Watch: `test_sox_scope::test_pill_on_tiles_and_initiative_page` reported as intermittently order-dependent.

## Known gaps / follow-ups
- `override_history` returns all version rows on purpose (full audit trail); the modal shows one row per override.
- Shared read-only link hides nav by passing an anonymous actor; a `{% block nav %}` would be cleaner.
- Docker image not built here; verify `docker build .` on a machine with Docker.

## Files / components
- `config/` settings, urls, wsgi. `Dockerfile`, `requirements.txt`, `.env.example`.
- `core/` versioning base, clock, `Person`, `RoleAssignment`, `auth/` (actor, middleware,
  decorators, sso hook, dev switcher).
- `tracker/` business models, `visibility.py`, services, views, `bootstrap` command.
- `templates/` base layout, 403, dev switcher, home. `static/vendor/` Bootstrap 5.3.3, HTMX 2.0.4.

## Checks run
- `manage.py check`, `makemigrations`, `migrate`, `bootstrap`, `scripts/generate_fake_data.py` on local Postgres 16: OK.
- Manual role check on `/`: tech risk sees all 3 seeded initiatives; auditor sees 2 (not proposed);
  owner sees own 1; unknown ID sees none.
- pytest: 86 passed (versioning/as-of, actor resolution, auth modes, visibility filtering,
  snapshot immutability). Whitenoise warns about a missing `staticfiles/` dir in tests; harmless.
- Production mode (DEBUG=0) under gunicorn with `collectstatic`: 200 on `/`, hashed CSS served.
  Bootstrap's trailing `sourceMappingURL` comment was removed from the vendored CSS because the
  manifest storage fails on a missing `.map` file.
- Docker image: Dockerfile written but not built here (no Docker daemon in the sandbox).
- Services smoke-tested against the seed: derived Red (go-live slipped 35d vs baseline), triage Confirm rows
  for two High risks and the incomplete Stage 1 gate, snapshot taken, exec context per role.
- Postgres in the sandbox stops between sessions: `service postgresql start`.

## Open items / risks
- SSO wiring is a stub; teammate to implement `emp_id_from_request` against the add-on.
- `Initiative.phase` is a free-text current phase set by the owner; may later be derived from
  `Phase` rows if that proves more reliable.
- `RoleAssignment` scoped owner rows and `Delegation` overlap in purpose; keep both for now
  (Delegation is the intake-facing concept), revisit if confusing.
