# Design addendum — decisions from the clickable demo (Nov 2026)

Supplements `SPEC.md`. Where the two differ, this file wins.

## Stack
Python 3.12 / Django 5.2, server-rendered templates, Bootstrap 5 static CSS, HTMX for small partial swaps, no custom JavaScript. Postgres via `DATABASE_URL`. US English throughout.

## Exec summary
- Header: three story cards, not counters. **Portfolio** (n active · Red/Amber/on-track with a proportional bar, plus "Since <last snapshot>: worse — …; better — …" with initiative names). **Go-lives in the next 90 days** (each named, with date, days out, controls ready/not). **Flags** (open High risks across N initiatives, named with counts; stale initiatives named with days).
- **Needs attention**: tiles only for Amber/Red active initiatives, Red first. No RAG badge — the card's left edge carries the color. Tile lines: name (+ Stale badge; + "Overridden: Red → Amber ›" badge for Tech Risk/Auditor only); comparison line on its own ("▲ Increased to Red from Amber" / "▼ Decreased…" / "— Unchanged (Amber)" / "New since the last snapshot"), hover tooltip "Compared with the last snapshot: <label>"; grey line = owner · last update date (days ago); then 1–3 Tech Risk highlights. If no highlights, muted fallback "flagged automatically: <rule>".
- **On track**: plain table (no color): initiative (owner · phase) · since last snapshot (shows "▼ Decreased to Green from Amber" when improved) · next key milestone + days · go-live + days · control readiness (x of y ready) · last owner update.
- **Timeline** (portfolio swimlane) comes before **Closed since <last snapshot>** (name, owner, closed date, closure outcome one-liner), then a muted one-line intake note (hidden from Senior Management).
- Section headers styled as rules flush on their table. Counts in parentheses.
- Counts respect the viewer's visibility (Senior Management's High-risk count excludes internal-only risks).

## Initiative page
Stacked, full width: headline card → Curate card (Tech Risk) or Commentary card (others) → Lifecycle stage checklist → timeline → milestones → risks/issues/dependencies → control impact → post update (owner) → update log → field-level history (Tech Risk, Auditor).
- Headline: effective RAG; derived + override (Tech Risk/Auditor); "rule that fired"; phase with lifecycle stage; owner, sponsor; target go-live with baseline and slip; last posted update.
- **Override** is set inline from the headline (Tech Risk) and requires: reasoning, supporting evidence, review-by date. Detail is a pop-out (modal) opened from the badge: derived + rule, override value, set by/when, review-by (overdue flag), reasoning, support, auto-expiry rule, override history, Clear (Tech Risk).
- Override auto-expires when the derived RAG becomes **worse than it was when the override was set** (not worse than the override value).
- **Curate card** (Tech Risk only): live exec-tile preview; highlights list with visibility, reorder, remove, add; commentary and internal notes with a visibility select each; add commentary/internal note. Risk rows get a visibility select; non-exec rows are greyed with "hidden from exec". Every curation action records a curation touch ("last curated").
- Owner view: same page with edit affordances, stale nudge above the fold when > early-warning threshold, "Post update" form distinct from field edits.

## Lifecycle
Seven stages, fixed numbers and colors: 0 Intake, 1 Mobilize & Design, 2 Build & Configure, 3 Test & Validate, 4 Cutover & Go-live, 5 Hypercare & Stabilize, 6 Close. Owners name their own phases; each maps to one stage. Timeline bars are colored by stage.
- **Guidance prose lives in TRAC** (decided Nov 8, 2026): exit gate, controls to consider, evidence to retain, the auditor's question and method notes. Handoff: `docs/TRAC_HANDOFF_lifecycle.md`.
- **The tracker keeps per stage (versioned, edited in-app):** stage name, expected gate approver, checklist items, and a TRAC link. The Lifecycle page and each initiative's stage panel show "Full guidance in TRAC →" (or "not linked yet").
- **Checklist confirmation** (Tech Risk): each tick requires a basis (reviewed artifact / observed or tested independently / owner attestation only) and an evidence reference; records who, when, guidance version. Unticking is logged. Visible read-only to owner and auditor.
- **Gate approval** (recorded by Tech Risk): approver name/role, approval date, approval evidence. Expected approver shown from guidance.
- Past stage with unconfirmed items or no recorded approval → "incomplete gate" marker on the page and a Confirm row in Triage.
- Closure requires the Stage 6 checklist complete and gate approval recorded.
- Intake form pre-fills stages 1–5 as editable phases with planned dates.

## Tech Risk workbench
Three tabs. **Triage** (default): one table per action group — Decide (intake: Accept/Return inline), Confirm (go-live ≤60d with controls not ready; open High risks; incomplete past gates), Review (RAG changed since last snapshot; override in place with review-by, red when overdue), Chase (stale > early-warning, > amber threshold). Columns: initiative · what · since · owner · last Tech Risk touch · action ("Curate →" to the initiative page). Footer: "N active initiatives need nothing right now: …". Tab label carries the count.
**Snapshots**: readiness table per active initiative (highlights 1–3 set; curated since last snapshot; override reviewed; stale chased) with ✓/✗; "Preview as Senior Management"; take snapshot (label, note); past snapshots.
**Admin**: stacked, full width — control library (upload, template download, current library with usage, upload log), RAG thresholds (+ change log), role management.
Curation panel does **not** live on the workbench.

## Timelines (SVG, server-rendered)
- Portfolio: one lane per initiative, name colored by effective RAG (no RAG chip), month axis Jun 2026 → 20 months, 26px bars, planned outline + forecast/actual filled, phase name drawn only if it fits, key milestone diamonds (done green / on track blue / slipped red). Today line.
- Initiative: scaled to the initiative's own dates ± 1 month. One lane per phase: name + stage on the left; planned bar (outline) with "Planned <start> – <end>"; forecast/actual bar (filled) beneath with dates and slip "(+31d)" in red; labels inside the bar if they fit, else after. Key-milestone lane at the bottom with staggered labels (up to 3 levels), "(was <planned>)" and a hollow marker at the original date when slipped.

## Snapshots
Freeze per-initiative exec/audit-visible state, header figures and thresholds in force. "Closed since prior view" in snapshot mode compares against the snapshot before the one displayed.

## Spelling and naming
US English. Fake data for development lives in `scripts/generate_fake_data.py`; the application ships with none.

---

# Round 2 decisions (Nov 8, 2026) — supersede anything above that conflicts

The clickable demo (`docs/demo/index.html`, refreshed) is the visual reference for everything below.

## Principles
- As concise as possible. No explanatory paragraphs or banners (only the snapshot/as-of banner). Tooltips are one short phrase.
- Plain words on chips: "Go-live slipped 35d", "Milestone slipped 31d", "High risk open", "High risk past due", "Stale 44d",
  "Control not ready, go-live in 30d", "Control in progress, go-live in 75d". Tooltip = one brief detail ("Versus baseline Dec 15, 2026").
- Professional wording; no "owner said so" style labels. US English.

## Exec summary
- Header: two cards, **Pulse check** (tooltip "Active initiatives") and **Next 90 days** (tooltip "Go-lives in the next 90 days"). No Flags card. No counts after card titles.
- Pulse check: three solid blocks (red, amber, green): big count + "▲ N since Oct" / "▼ N since Oct" / "No change"; deltas compare the same initiatives (open now and at the last snapshot; new ones counted in their current color). A grey fourth block: "N closed since Oct". Initiatives whose RAG changed appear as small white pills inside the block of their current color, "▲ Name" / "▼ Name", tooltip "Amber → Red since Oct". No color words in the blocks (color name as tooltip).
- Next 90 days: one row per go-live, soonest first: countdown block (big number, "days"), name + date, one small square per control (green ready; amber in progress / submitted / sent back; red not started) and "2 of 3 controls ready" or "Controls ready". Empty: "No go-lives in the next 90 days".
- Needs attention: equal-width tiles filling the row (`auto-fit`). Tile: name + Stale/Override badges, comparison line, owner · last update, highlights (fallback: the chip).
- On track table removed: green active lanes on the timeline show "✓ On track" under the name.
- Order: header cards → Needs attention → Timeline → Closed since → intake line.

## Initiative page
- Title + Stale/Override badges. Summary strip of facts: RAG pill + chip · Stage · Oversight (Enhanced/Standard/Light pill; tooltip "Set by Tech Risk" or the override reason; Tech Risk clicks to edit) · Go-live "Jan 19, 2027 · slipped 35d" · Next milestone · Updates "Check-in due Nov 13" (amber ≤3 days, red "Check-in overdue 5d"; tooltip "Last update Oct 30"). **Check in** button for the owner/delegates and Tech Risk.
- Tabs (query string `?tab=`): Overview · Plan · Risks & controls · Lifecycle · Meetings · History. Default Overview.
  - Overview: highlights, latest update, open follow-ups, one muted description line; owner/delegates line (Tech Risk: "Change"). Tech Risk also: RAG rules card (all chips + set/clear override) and the Curate card.
  - Plan: initiative timeline + milestones table; "More edits" link to the detailed edit pages.
  - Risks & controls: risks table and control impact table; control IDs link to the control's TRAC guidance (`Control.trac_url`). Non-Tech-Risk roles don't see Source/Visibility columns. Tech Risk accepts / sends back submitted controls here.
  - Lifecycle: stage panel (below). Meetings: this initiative's meetings + log a meeting. History: update log then field-level history (Tech Risk, Auditor).
- **Check-in** (one form, prefilled): What's changed (required) · Go-live date · next key milestone forecast and % · Controls (only those not Ready): Not started / In progress / Submit for review (evidence link required) with any send-back comment shown · optional new risk (title + H/M/L). One submit posts the update with a diff of only what changed.

## Oversight level (replaces "severity")
- Values Enhanced / Standard / Light. Inputs: six yes/no intake questions under "About this initiative" ("Tech Risk sets the oversight level from your answers"). Derived: Enhanced = Tier 0/1 or SOX app plus one more yes, or 3+ yes; Standard = 1–2; Light = none. Tech Risk can override with a required reason.
- What each stage exit needs, by level (draft, pending Head of Tech Risk approval; set in code, never editable in the app):
  Approver sign-off / Tech Risk review / Owner confirms — see `tracker/lifecycle.py::GATE_MATRIX`. Stage 0 is intake acceptance for all.

## Lifecycle tab
- Each stage line: name, requirement tag (plain label, tooltip), "N/M done", sign-off decision pill (Proceed green, Proceed with conditions amber, Hold red), meeting count. Non-Tech-Risk roles see stages collapsed.
- Items: owner "Submit evidence" (link + optional note) → Submitted; Tech Risk Accept (basis buttons: Evidence reviewed / Observed by Tech Risk / Owner attestation) or Send back (comment required; shown to the owner). Tech Risk can mark N/A with a reason, and reopen. On "Owner confirms" stages the owner confirms directly. Stage 0: Tech Risk accepts directly.
- "Record sign-off" (Tech Risk) once all items are done on a sign-off stage: approver, date, evidence, decision; conditions required for "Proceed with conditions".
- Each item has a "?" linking to the stage's TRAC page (anchor pattern from TRAC later).

## Lifecycle & guidance page
- Title + "Draft" tag (tooltip "Pending Head of Tech Risk approval"). One line: "Tech Risk sets each initiative's oversight level — Enhanced, Standard or Light — when it's accepted. The level sets what's needed to move to the next stage." Owners also see "<their initiative>: <level>." and their level highlighted.
- Stage cards: "To move on" pills per level, gate approver, checklist, "Guidance in TRAC →", Tech Risk "Edit stage". No grid, no version history.

## Meetings
- Standard fields: **What** (topic), **Who** (attendees), **Result / next steps** (outcome) + follow-ups (text, owner, due). Each meeting tagged to one lifecycle stage (default: the initiative's current stage). Logged from the initiative's Meetings tab (and the Meetings page).

## Workbench
- Triage group names only (no hint sentences). Review rows for submitted evidence (checklist items and controls) open the right tab. Chase rows "Check-in overdue Nd" with **Copy reminder**: a modal showing the two-line reminder in a read-only textarea to copy (no JavaScript).
- Admin: control library shows a TRAC guidance link per control (editable; also a `trac_url` column in the upload template).

## Audit
- Audit page = point-in-time pickers only. Access model and control mapping pages removed. Field-level history stays on the initiative's History tab.

## Round 3 decisions (Nov 9, after role reviews)
Supersedes earlier wording where they conflict.
- Terms: "No check-in Nd" replaces "Stale"; "Status summary" replaces "Pulse check"; triage groups shown as Intake decisions / Risks to confirm / Reviews due / Overdue (internal keys unchanged); the initiative's "Curate" card is "Exec view"; "Observed by Tech Risk" replaces "Independently tested"; sign-off decision "Hold (do not proceed)"; stage names in US English (Mobilize, Stabilize).
- Exec tiles always show the reason line ("Why red: Go-live slipped 35d") above the highlights.
- Gate requirements and the six question labels are edited in the app (Workbench > Admin > Gate requirements), versioned, Draft until "Approved by" and date are recorded. Derivation rule stays in code: Enhanced = Tier 0/1 app, SOX plus one more yes, key controls plus regulated data, or three yes; Standard = any yes; Light = none. Light stages 3, 5 and Close need Tech Risk review.
- Snapshots also freeze oversight level, per-stage requirement / items done / sign-off, and control readiness, plus the guidance and gate-requirement versions. Audit page lists snapshot records.
- As-of date on the exec summary shows the latest snapshot on or before that date.
- Guidance edits move existing item records with their item when reordered or reworded; records for removed items are closed.
- Self-review (same person submits and accepts) is allowed for now; revisit with Internal Audit.

## Module switches (Nov 9)
The full feature set is the end state; nothing is removed. Workbench › Admin › Modules turns Owner access,
Meetings, Audit access and Read-only links on or off (versioned, change note required, all on by default).
Off hides the nav and returns 404 for the module's pages; data is kept and returns when switched back on.
Tech Risk sees a banner listing what is off. Day-to-day operation is in `docs/RUNBOOK.md`.

## Controls needed by (Nov 9)
Tasks stay in the owner's project plan; the tracker holds key milestones only. Each affected control has an
optional "Needed by" key milestone (blank = go-live). The control readiness rule measures days to that date,
so a control due at test exit warns before go-live. The initiative timeline has a Controls lane (square per
control at its due date, colored by readiness, tooltip and link to the Risks tab). The portfolio timeline is
unchanged; the Next 90 days squares remain the exec summary of controls.

## SOX applicable (Nov 9)
Each initiative has a SOX applicable yes/no, separate from the oversight question "SOX in-scope application
affected" (it defaults from that answer at intake; Tech Risk changes it in the oversight modal). Exec summary,
Workbench triage and Audit page filter with "All initiatives | SOX applicable"; auditors default to SOX
applicable, everyone else to All. Snapshots freeze the flag and filter on it.

## Watch list (Nov 10)
Early-stage, unconfirmed items (e.g. a contract ending, stay or move) are initiatives with status "watch":
name, owner, decision, decision due, optional key date with label, options considered, SOX applicable, the
six oversight questions as possible answers (shown as "likely oversight"), next check. No lifecycle, plan or
RAG; warnings only (decision due within 30 days or overdue, key date within 90 days with no decision, check
due). Tech Risk adds and edits them. "Confirm as initiative" moves the item into the intake queue with its
details; "No change needed" closes it with a reason. Shown on the Watch list page, a Workbench group, and a
short "On the watch list" block on the exec summary (frozen into snapshots). Has its own module switch.
Watch and dropped items are excluded from every initiative listing via `Initiative.objects.listed()`.

## Initiative page layout and workstreams (Nov 10)
- Page: header with name, SOX and oversight pills, status in words with its reason, four facts (stage, go-live,
  next milestone, next check-in), Check in button and an Actions menu (override, oversight, people, workstreams,
  accept/return, close, archive, delete). Overview is read-only and the same for every role (highlights, why
  this status, workstreams, latest check-in, open follow-ups, about). Curation lives in a Tech Risk "Exec view"
  tab. Risks and controls show visibility only as tags; selects sit behind "Edit visibility".
- Workstreams: optional parts of one initiative, each with a lead, its own phases, milestones, risks, controls
  and check-ins, and its own status from the same rules. The initiative's status is the worst of its own rules
  and its workstreams, with the workstream named in the reason ("Compute: High risk past due"). Leads check in
  for their workstream only. One exec tile per initiative with a roll-up line; the portfolio timeline shows one
  bar per stage; the Plan tab groups phases by workstream. Sign-offs and overrides stay at initiative level.
