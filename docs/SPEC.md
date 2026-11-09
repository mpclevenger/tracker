# IT Initiative Risk Tracker — v1 specification

## 0. Confirm before starting (ask me)
- Internal developer platform: runtime/language constraints, how apps are deployed, database provisioning (Postgres assumed), allowed libraries.
- Entra ID SSO integration pattern on that platform, and how the organisational add-on exposes the employee ID claim.
- Repo location and branching conventions.
- Whether a UI framework/design system is mandated.

## 1. Purpose
Standalone web app tracking ≤10 major IT initiatives (the "book of work") that affect risk and controls. Initiative owners update continuously; Tech Risk adds an independent overlay and curates what leadership and auditors see; Tech Risk takes immutable monthly snapshots for the leadership pack. Mental model: headline exec page → drill-down initiative pages. No silent edits: everything versioned.

## 2. Roles (Entra ID SSO; role = employee ID → RoleAssignment table, managed in-app by Tech Risk)
- Initiative Owner / Delegate: submit intake; edit own initiative's phases, milestones, risks/issues/dependencies, control-impact statements; post updates. Sees own initiative only; sees only the effective RAG (override if present, else derived); sees overlay items marked owner-visible; never sees internal notes or the derived-vs-override pair.
- Tech Risk: accept/reject intake; write overlay (commentary, highlights, internal notes); set visibility per item; override RAG with mandatory reason; take snapshots; maintain control library (Excel upload) and control mapping; edit RAG thresholds; manage roles; close/archive initiatives. Sees everything.
- Senior Management: read-only; exec summary + initiative pages filtered to exec/audit-visible content; live or snapshot.
- Auditor: as Senior Management plus snapshot picker, as-of date picker, control mapping page, field-level change history of visible items, derived RAG + override + reason, access-model page.
Visibility layers on overlay items and owner content: internal → owner → exec/audit. Owner content defaults to owner+exec-visible; Tech Risk may hide it (hiding is logged; content stays in history).

## 3. Screens
3.1 Exec Summary (landing for leadership/auditors)
- Header strip: count by RAG; go-lives in next 90 days; open High risks; initiatives flagged stale. Counts include active initiatives only (not closed/archived).
- One tile per initiative: name, owner, effective RAG (override badge for Tech Risk/auditor views), stale badge ("Stale · Nd") separate from RAG colour, phase, next milestone + date, trend vs last snapshot, 1–3 Tech Risk highlights.
- Timeline swimlane: one lane per initiative, month granularity; phase bars planned (outline) vs forecast/actual (filled); milestones as diamonds coloured on-track/slipped/done.
- Snapshot selector: Live or dated snapshot; banner shows which is displayed. Read-only share link to live or a chosen snapshot.
3.2 Initiative page: headline (effective RAG + rule that fired; override reason where role permits; phase, owner, sponsor, go-live, last posted update); Tech Risk commentary (per visibility); timeline for this initiative; milestones table (status, planned, forecast, actual, % complete, note, slippage days); risks/issues/dependencies (type, title, rating H/M/L, owner, target date, status, mitigation, source owner|tech risk, visibility); control impact (control id/name/framework, impact type new|modified|retired|interim, compensating control, readiness not started|in progress|ready|not ready, evidence link); update log (reverse-chron, author, timestamp, diff); history/diff between two points in time (Tech Risk + Auditor).
3.3 Tech Risk workbench: intake queue (accept / return with comments); needs-attention list (stale >21d early warning, RAG changed since last snapshot, new High risks, go-live ≤60d with controls not ready); curation panel per initiative (visibility toggles, highlight picker, RAG override); snapshot control (take with label/note; list past); control library upload; threshold config; role management.
3.4 Owner view: own initiative page with edit controls; "last posted update N days ago" nudge; "Post update" action distinct from field edits.
3.5 Intake form: name, description, sponsor, owner, delegates, business driver, target go-live, phases with planned dates, initial milestones, known risks, expected systems/controls affected, why it matters to risk/controls → Tech Risk queue.

## 4. Data model (Postgres; all mutable entities append-only versioned with valid_from/valid_to; author + timestamp on every row)
Initiative(id, name, description, sponsor, owner_emp_id, status proposed|active|closed|archived, phase, target_go_live, created_at, accepted_at, closed_at, closure_control_outcome)
Phase(initiative_id, name, order, planned_start/end, forecast_start/end, actual_start/end)
Milestone(initiative_id, phase_id, name, planned, forecast, actual, pct_complete, status, is_key)
RiskItem(initiative_id, type risk|issue|dependency, title, rating, owner, target_date, status, mitigation, source, visibility)
Control(control_id, name, framework, control_owner, in_scope) — loaded from Tech Risk Excel upload; uploads versioned and logged
ControlImpact(initiative_id, control_id, impact_type, compensating_control, readiness, evidence_link)
Update(initiative_id, author, posted_at, text, change_diff json) — "posted update" resets the staleness clock; plain field edits do not
Overlay(initiative_id, author, type commentary|highlight|internal_note, text, visibility, timestamp)
RagOverride(initiative_id, value, reason, author, effective_from, cleared_at)
Snapshot(id, taken_at, taken_by, label, note); SnapshotRow(snapshot_id, initiative_id, frozen_json of exec/audit-visible state) — immutable
RoleAssignment(emp_id, role, initiative_id nullable)
ThresholdConfig(key, value, changed_by, changed_at) — editable by any Tech Risk user, logged
Delegation(initiative_id, delegate_emp_id)

## 5. Derived RAG (recompute on every save; worst rule wins; thresholds from ThresholdConfig with these defaults)
Amber: key milestone slip >30d; any open High risk; go-live ≤90d and any control "in progress"; stale >30d.
Red: key milestone slip >60d or go-live slipped >30d; open High risk past target date; go-live ≤60d and any control "not started"/"not ready"; stale >60d.
Green otherwise. Stale = days since last *posted* update. Stale badge displayed independently of RAG. Early-warning stale flag in workbench at >21d (no RAG effect).
Override: Tech Risk sets value + mandatory reason; auto-expires if derived RAG becomes worse than the override; manual clear otherwise.

## 6. Workflow
Continuous edits → version written → RAG recomputed → needs-attention refreshed. Tech Risk curates on a rolling basis. Before each leadership meeting Tech Risk takes a snapshot; auditors can view any snapshot ("what was presented") or any as-of date ("what the system held"). Closing an initiative requires Tech Risk to record the control-readiness outcome; archived initiatives leave the exec summary but remain in snapshots, history and control mapping.

## 7. Audit-facing requirements
Field-level who/when history; hiding content is logged; control mapping page (control → initiatives, initiative → controls); access-model page describing roles, employee-ID mapping, and what each role can/can't see; data flow description.

## 8. Explicitly out of v1
Email/Teams reminders; PDF/Excel export; automated control-library feed; integration with any other system.

## 9. Delivery expectations
- Propose phases (suggested: 1 auth+roles+data model, 2 intake+owner editing+RAG engine, 3 exec summary+timeline, 4 workbench+curation+snapshots, 5 audit views+history). Wait for approval per phase.
- Tests for the RAG engine, versioning/as-of queries, visibility filtering, and snapshot immutability.
- Maintain WORK_NOTES.md: goal/status, decisions, files changed, checks run, open items.
- Smallest correct change; no unrelated refactors; ask before anything destructive.