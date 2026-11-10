# TRAC handoff — initiative lifecycle guidance pages

Paste this file into the TRAC chat. It moves the initiative lifecycle guidance out of the IT Initiative Risk Tracker and into TRAC, which becomes the single home for it.

## Decisions behind this handoff (Nov 8, 2026)

- **The initiative tracker stays its own app.** It is no longer being moved into TRAC. Drop "move the initiative tracker in" from TRAC's plan.
- **TRAC owns the guidance.** One page per lifecycle stage: what the stage is for, the exit gate, controls to consider, evidence to retain, the question an auditor will ask, and method notes.
- **The tracker keeps only what it needs to operate:** stage names, the per-initiative checklist items Tech Risk confirms, the expected gate approver, and a link to the matching TRAC page. The tracker's Lifecycle page shows "Full guidance in TRAC →" for each stage.
- **Nothing is duplicated.** Checklist items live only in the tracker; guidance prose lives only in TRAC. The TRAC page can say "Tech Risk confirms these items per initiative in the tracker" without restating them.
- **Wording:** the content below is working text. A professional wording polish happens at the end, across both apps.

## What TRAC needs to build

1. Seven pages, one per stage, in "Best practices and guides" (suggested section: Build your controls, or a dedicated "Initiative lifecycle" group if that fits TRAC's navigation better — TRAC's call).
2. **Stable URLs** for each page (they must not change when a page is edited or republished). After publishing, send the seven URLs back so they can be entered in the tracker (Lifecycle & guidance → Edit → TRAC link).
3. Follow TRAC's own conventions: Tech Risk-only editing, draft until approved, comment box for everyone else, sentence case, US English, no "standard" wording.
4. Optional: a short overview page linking the seven stages in order, with the principles below.
5. **A page: "Oversight levels"** (content below). Use TRAC's own guides and wording — no external framework citations.
6. **Anchors for checklist items.** On each stage page, give every checklist item its own anchor `item-1`, `item-2`, … numbered from 1 in the same order as the tracker's checklist. The tracker's "Guidance" link next to an item opens `<stage page URL>#item-N`. If an anchor is missing, the link still opens the top of the stage page.

## Oversight levels (new page)

Every initiative gets an oversight level of **Enhanced**, **Standard** or **Light** when Tech Risk accepts it. The owner answers factual questions at intake; the level comes from those answers, and Tech Risk confirms it or overrides it with a reason. Tech Risk re-checks it at each stage exit. The level sets what's needed to move to the next stage.

**Questions** (answered by the owner at intake):
- Tier 0 or 1 app affected
- SOX in-scope app affected
- New, retired or redesigned key controls
- Financial or regulated data migrated
- Change to how access is provisioned or reviewed
- Moves to a vendor or cloud platform

**Result:** Enhanced when a Tier 0/1 or SOX app is affected plus at least one other yes, or three or more yes answers; Standard for one or two yes answers; Light for none.

**What's needed to move on, by oversight level**

| Stage exit | Enhanced | Standard | Light |
|---|---|---|---|
| 1 Mobilize & Design | Approver sign-off | Approver sign-off | Tech Risk review |
| 2 Build & Configure | Approver sign-off | Tech Risk review | Owner confirms |
| 3 Test & Validate | Approver sign-off | Tech Risk review | Owner confirms |
| 4 Cutover & Go-live | Approver sign-off | Approver sign-off | Approver sign-off |
| 5 Hypercare & Stabilize | Approver sign-off | Tech Risk review | Owner confirms |
| 6 Close | Approver sign-off | Approver sign-off | Approver sign-off |

- **Approver sign-off:** the program submits evidence for each checklist item, Tech Risk accepts it, and a named approver's decision is recorded: Proceed, Proceed with conditions, or Hold.
- **Tech Risk review:** evidence submitted and accepted by Tech Risk; no sign-off decision.
- **Owner confirms:** the owner confirms each item is done.
- Items that don't apply are marked N/A with a reason.
- Go-live and Close always need approver sign-off.

## Principles (overview page)

- **Every tick needs a basis and an evidence reference.** Basis is one of: reviewed the artifact, observed/tested independently, or owner attestation only. Attestation-only confirmations are visible as such to auditors.
- **Every gate needs a named approver.** Tech Risk records who approved the stage exit, when, and where the approval evidence lives. Tech Risk does not approve gates it did not own.
- **Independence.** Control dry runs and first-operation evidence are reviewed by someone who did not build the control. Tech Risk observes at least one dry run per new control.
- **Completeness.** Control impact assessments start from the control library for the affected systems and are confirmed with control owners.
- **Interim controls** carry an end condition from stage 1 and must show operation evidence for the whole window before retirement.
- **Evidence stays in the system of record.** The tracker holds the reference and the date it was confirmed.

---

## Stage 0 — Intake

**Exit gate:** Tech Risk accepted; baseline go-live fixed; initiative on the book of work.

**Expected gate approver:** Head of Tech Risk *(also stored in the tracker; keep the two consistent)*

**Controls to consider**

- Scoping: in-scope systems, reports and controls touched

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Intake form (versioned)
- Acceptance record
- Baseline go-live

**The auditor will ask:** "How did you decide this was in scope for risk oversight, and when?"

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Intake accepted by Tech Risk
2. Baseline go-live recorded
3. Affected systems/controls listed

---

## Stage 1 — Mobilize & Design

**Exit gate:** Solution design signed off; control impact assessment complete; every affected control has an impact type.

**Expected gate approver:** Sponsor (design sign-off) and Head of Tech Risk (control impact assessment) *(also stored in the tracker; keep the two consistent)*

**Method:** Completeness of the control impact assessment: start from the control library filtered to the systems named at intake, then confirm the list with each control owner. Record who was consulted.

**Controls to consider**

- Control design for new/modified controls
- SoD model
- Data migration approach (completeness & accuracy)
- Interfaces
- Reports used in controls (IPE)
- Compensating / interim controls

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Design sign-off
- Control impact assessment
- Risk assessment
- SoD matrix
- Migration strategy
- Vendor SOC report review (if applicable)

**The auditor will ask:** "Show me you identified control impact before build, not after."

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Design signed off by sponsor
2. Control impact assessment complete — built from the control library for the affected systems and confirmed with control owners
3. SoD model agreed
4. Migration approach documented
5. Interim/compensating controls agreed, each with an end condition

---

## Stage 2 — Build & Configure

**Exit gate:** Build complete in non-prod; configuration baselined.

**Expected gate approver:** Program manager (build complete) *(also stored in the tracker; keep the two consistent)*

**Controls to consider**

- Change management on project environments
- Developer access vs production
- Configuration documentation
- Security baseline

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Build/config baseline
- Access lists for project environments
- Change log

**The auditor will ask:** "Who had access to what during build, and could they also move it to production?"

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Configuration baselined
2. Project environment access list retained
3. Dev/prod access segregation confirmed

---

## Stage 3 — Test & Validate

**Exit gate:** Test exit approved; defects within tolerance; control test complete; migration rehearsal reconciled.

**Expected gate approver:** Business owner (UAT) and Change Advisory Board (test exit) *(also stored in the tracker; keep the two consistent)*

**Method:** Independence: the person who dry-runs a control must not have built or configured it. Record the tester's name and role; Tech Risk observes at least one dry run for each new control.

**Controls to consider**

- Test strategy and traceability
- UAT sign-off
- Control operating-effectiveness dry run
- Migration reconciliation (counts, balances)
- Performance / DR test

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Test plans and results
- Defect log with disposition
- UAT sign-off
- Migration reconciliation
- Control walkthrough evidence

**The auditor will ask:** "Did you test the controls, not just the functionality? Did migration reconcile?"

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. UAT signed off by business owner
2. Each new/modified control dry-run tested by someone independent of build (tester named)
3. Tech Risk observed at least one control dry run
4. Migration rehearsal reconciled (counts and balances)
5. Defects within tolerance; disposition recorded

---

## Stage 4 — Cutover & Go-live

**Exit gate:** Go/no-go approved; production change approved; access provisioned per design; rollback ready.

**Expected gate approver:** Go/no-go chair (named) and Change Advisory Board *(also stored in the tracker; keep the two consistent)*

**Controls to consider**

- Go/no-go checklist
- Production change record
- Access provisioning approvals
- Final data reconciliation
- Backup before cutover
- Rollback plan

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Go/no-go minutes
- Change ticket(s)
- Access approvals
- Final reconciliation
- Backup confirmation
- Rollback plan

**The auditor will ask:** "Show me the approval to go live and evidence the migrated data was complete and accurate."

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Go/no-go approved
2. Production change approved
3. Access provisioned per design
4. Final reconciliation complete
5. Backup taken; rollback plan ready

---

## Stage 5 — Hypercare & Stabilize

**Exit gate:** First operation of each new/modified control evidenced; hypercare incidents closed; interim controls retired or extended.

**Expected gate approver:** Head of Tech Risk (control readiness) and business owner (hypercare exit) *(also stored in the tracker; keep the two consistent)*

**Method:** Independence: first-operation evidence is reviewed by Tech Risk, not self-certified by the program. Interim controls must show operation evidence for the whole window before they are retired.

**Controls to consider**

- First-run evidence for every new/modified control
- Monitoring
- Elevated access removed
- Interim control exit

**Evidence to retain** (in the system of record; the tracker holds the reference)

- First control operation evidence
- Hypercare incident log
- Access de-provisioning evidence
- Interim-control closure

**The auditor will ask:** "Prove the new controls actually operated once. Was project access removed?"

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Every new/modified control operated at least once; evidence reviewed by Tech Risk
2. Interim controls evidenced as operated for the whole window
3. Interim controls retired, or extension approved with new end date
4. Project / elevated access removed (de-provisioning evidence)
5. Hypercare incidents closed

---

## Stage 6 — Close

**Exit gate:** Control readiness outcome recorded; retired controls confirmed; evidence archived; handover to BAU owners.

**Expected gate approver:** Head of Tech Risk *(also stored in the tracker; keep the two consistent)*

**Controls to consider**

- Decommissioning (retention, access removal)
- Control library updated
- BAU ownership confirmed

**Evidence to retain** (in the system of record; the tracker holds the reference)

- Closure record (versioned)
- Evidence archive reference
- Updated control mapping
- Decommission confirmation

**The auditor will ask:** "Where is everything, and who owns these controls now?"

*Checklist items Tech Risk confirms in the tracker for this stage (for reference only — do not duplicate on the TRAC page):*

1. Control readiness outcome recorded
2. Retired controls confirmed
3. Evidence archive reference recorded
4. BAU control owners confirmed
