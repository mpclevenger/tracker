# Runbook

How to run the tracker day to day. Almost everything is done in the app; nothing here needs code.

## Weekly (about 30 minutes)
1. **Workbench › Triage.** Work top to bottom: Intake decisions, Risks to confirm, Reviews due, Overdue.
2. For each initiative you met with this week, open it and post the check-in (**Check in** button): dates, % complete, control readiness, new risks.
3. Accept or send back any submitted evidence (initiative › Lifecycle tab, or the Risks tab for controls).
4. Overdue check-ins: **Copy reminder**, then paste it into email or Teams.

## Monthly leadership pack
1. **Workbench › Snapshots.** Clear the readiness checklist: highlights set, overrides reviewed, missed check-ins followed up.
2. **Preview as Senior Management.** Check what the CTO will see.
3. **Take snapshot**, labeled like "Nov 2026 leadership pack". A snapshot can't be changed afterward. If it's wrong, take another.

## Add an initiative
- **In the app:** **Intake form**, then accept it from Workbench › Triage. On acceptance, the target go-live becomes the baseline.
- **The tracker itself** is prefilled in `scripts/initiatives/risk_tracker.yaml`: replace every `CHANGE_ME`, check the lines marked CONFIRM, then load it as below.
- **From a file** (for one with a lot of history): copy `scripts/initiative_template.yaml`, fill it in, then run `python scripts/load_initiative.py my_initiative.yaml`. It won't overwrite an existing initiative.

## Recurring meetings
- **Meetings › Series › Add series** for a standing touchpoint (e.g. weekly steering): cadence, usual attendees, initiatives.
- From the series page, **Log next meeting** fills in the title, attendees and initiatives; close carried-over follow-ups on the same form.
- If nothing is logged within the cadence, the series shows under Overdue on the Workbench.

## Large initiatives (workstreams)
For one initiative with several parts, such as a data lake with front end, back end, compute and admin.
- Initiative page › **Actions › Workstreams**: add each part and its lead.
- Tag phases, milestones, risks and controls to a workstream in their editors (blank = whole initiative).
- Each lead checks in for their own workstream. The initiative's status is the worst of its workstreams, and the exec tile shows a one-line roll-up.
- Loading from a file: see the `workstreams:` example in `scripts/initiative_template.yaml`.

## Watch list (not confirmed yet)
For things that may become initiatives, such as a contract ending where the team hasn't decided to stay or move.
- **Watch list › Add watch item**: what's being decided, decision due, optional key date (e.g. "Contract ends"), options, SOX applicable, possible impact, next check.
- Warnings show on the Workbench when a decision is due within 30 days, a key date is within 90 days with no decision, or the next check has passed.
- When it's decided: **Confirm as initiative** (it goes to the intake queue with its details) or **No change needed** (enter the outcome, e.g. "Renewed for 3 years").

## Turn modules on or off
**Workbench › Admin › Modules.** Tick or untick, enter a note, then **Save**. Nothing is deleted; turning a module back on restores it as it was.

| Module | Off means |
|---|---|
| Owner access | Owners can't sign in; you enter check-ins and evidence for them |
| Meetings | Meetings log and follow-up reminders hidden |
| Audit access | Auditors have no access; you keep the history pages |
| Read-only links | No shareable exec summary link |
| Watch list | Watch list page, its Workbench group and the exec block hidden |

A small banner tells Tech Risk which modules are off.

## Change settings (all in the app, all versioned)
| What | Where |
|---|---|
| Stage names, checklists, approver, TRAC link per stage | **Lifecycle & guidance › Edit stage** (TRAC link = the stage page URL; item links add `#item-1`, `#item-2`, …) |
| Gate requirements grid and oversight question wording | **Workbench › Admin › Gate requirements**. Record "Approved by" once the Head of Tech Risk agrees |
| RAG thresholds (days late, days without check-in) | **Workbench › Admin › RAG thresholds** |
| When each control must be ready | Initiative › Risks › Edit controls › **Needed by** (a key milestone; blank = go-live) |
| Control library and control TRAC links | **Workbench › Admin › Control library** (Excel upload, or Link per row) |
| Who has which role | **Workbench › Admin › Role management** |
| Whether an initiative is SOX applicable | Initiative page: click the oversight pill, then **SOX applicable**. Auditors see SOX applicable items by default |
| An initiative's oversight level or people | Initiative page: click the oversight pill, or **Change** next to Owner |
| A status you disagree with | Initiative page, **Set override**. A reason and evidence are required; it expires if things get worse |

## Fix a mistake
Edit the record in the app. The old value stays in History; that's intended, and it's what auditors rely on. Records are never deleted: remove a row with **Remove**, and close an initiative rather than deleting it.

## Close, archive or delete an initiative
- **Close** when it's finished: Initiative page › **Close initiative** (needs a control readiness outcome). Archive it later from the same place.
- **Delete** only a test or mistaken entry: Initiative page › **Delete initiative…**. Give a reason, then type the initiative's name to confirm. It disappears from every page; its history (with the reason) and any past snapshots keep it.

## Deploy an update
1. Back up the database first (next section).
2. Push the new code to the platform. The release step runs the database migrations automatically.
3. Open the app and check the exec summary and Workbench load.

If a page shows "relation … does not exist", the migrations didn't run. Run `python manage.py migrate` once on the platform.

## Back up and restore
- Ask the platform team how Postgres backups are taken, how long they're kept, and how to restore one. Do this before putting real data in.
- Manual copy, from any machine with Postgres client tools: `pg_dump "<DATABASE_URL>" > tracker-backup.sql`
- Restore into an empty database: `psql "<DATABASE_URL>" < tracker-backup.sql`

## When something breaks
| Symptom | Fix |
|---|---|
| 400 Bad Request on every page | Add the hostname to `DJANGO_ALLOWED_HOSTS` |
| 403 on every save | Add `https://` plus the hostname to `DJANGO_CSRF_TRUSTED_ORIGINS` |
| App won't start: "DJANGO_SECRET_KEY must be set" | Set the config var |
| "relation … does not exist" | Run `python manage.py migrate` |
| Reminder links point to the wrong address | Set `SITE_URL` to the app's address |
| Someone sees "No access yet" | Give them a role in **Workbench › Admin › Role management**, or make them an initiative's owner (needs Owner access on) |

## Changes that need code
Rare. Make them in a test copy first and run the tests (`pytest -q`) before deploying.
- A new field on an initiative, risk or control
- A new RAG rule, or a change to how an existing rule works (thresholds are in the app)
- The rule that turns the six oversight answers into Enhanced / Standard / Light (the wording is in the app)
- A new page

## Run a test copy on Windows (PowerShell)
```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
$env:DJANGO_SECRET_KEY = "local-only"
$env:DATABASE_URL = "sqlite:///local.sqlite3"
$env:DJANGO_DEBUG = "1"
python manage.py migrate
python manage.py bootstrap
python scripts/generate_fake_data.py
python manage.py runserver
```
Open http://127.0.0.1:8000/. Delete `local.sqlite3` to start again. Never run the fake-data script against the real database.
