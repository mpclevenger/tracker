# IT Initiative Risk Tracker

Tracks the book of work of major IT initiatives that affect risk and controls. Initiative owners update
continuously; Tech Risk curates what leadership and auditors see, overrides RAG with recorded reasoning,
confirms lifecycle gates with evidence, and takes immutable monthly snapshots. Everything is versioned;
nothing is edited in place.

Stack: Python 3.12 · Django 5.2 · Postgres · Bootstrap 5 (static) · HTMX (static) · no custom JavaScript.

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then export the variables, or let your shell/platform inject them
export $(grep -v '^#' .env | xargs)
python manage.py migrate
python manage.py bootstrap            # lifecycle guidance v1.0 (add --tech-risk EMP_ID --name "Name" for dev/sso modes)
python manage.py runserver
```

Open http://127.0.0.1:8000/. The default `AUTH_MODE=open` makes every visitor Tech Risk with no sign-in —
right for the build/test environment while entitlements are being wired, wrong for production. `AUTH_MODE=dev`
adds a top-right **switch user** link to view as any employee ID / role.

The application ships with no initiatives, people or controls. Two ways to get data in:

- **Your first real initiative**: copy `scripts/initiative_template.yaml`, edit the values, then
  `pip install -r requirements-dev.txt` and `python scripts/load_initiative.py my_initiative.yaml`. One run,
  one initiative; it refuses to overwrite an existing one. From then on, edit in the app.
- **Fictional data for development**: `python scripts/generate_fake_data.py`. Never run it against production.

Tests: `pytest -q` (needs the Postgres from `DATABASE_URL`).

## Deploy

Two supported routes; both need the same config vars.

**Buildpack (Heroku-style platform).** The repo root has `Procfile` (web + release), `.python-version` (3.12)
and `requirements.txt`. The build runs `collectstatic` automatically (no config vars needed for that step);
the release phase runs `migrate`; the web process is gunicorn on `$PORT`.

**Container.** The `Dockerfile` does the same in one image.

Config vars:

| Var | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string |
| `DATABASE_URL` | from the Postgres add-on (`postgres://…`); set `DATABASE_SSL_REQUIRE=1` if the add-on requires TLS |
| `DJANGO_ALLOWED_HOSTS` | the app's hostname(s), comma-separated, e.g. `risk-tracker.internal.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://` + the same hostname(s) |
| `AUTH_MODE` | `open` for the test environment, `sso` once entitlements are wired |
| `DJANGO_DEBUG` | leave unset (defaults to 0) |

Symptoms map: `DisallowedHost` / 400 → `DJANGO_ALLOWED_HOSTS`; 403 on every POST → `DJANGO_CSRF_TRUSTED_ORIGINS`;
`DJANGO_SECRET_KEY must be set` at boot → the var is missing; `relation … does not exist` → the release
phase did not run `migrate` (run it once by hand).

## Entitlements / SSO

Three modes, chosen by `AUTH_MODE`:

| Mode | Who you are | Use |
|---|---|---|
| `open` | Everyone is Tech Risk; no sign-in | Build/test environment only |
| `dev` | Pick any employee ID from the user switcher | Local development |
| `sso` | Identity (and optionally roles) from the entitlement add-on | Production |

Wiring the add-on touches **one file**, `core/auth/sso.py`, which has two hooks:

1. `emp_id_from_request(request)` — the employee ID the add-on asserts. Default reads the trusted header named
   by `SSO_EMP_ID_HEADER`.
2. `roles_from_request(request)` — optional. Return the application roles the add-on grants
   (`tech_risk`, `senior_management`, `auditor`, `owner`), or `None` to use the in-app `RoleAssignment` table
   only. Default maps groups from `SSO_GROUPS_HEADER` through `SSO_GROUP_ROLE_MAP`
   (e.g. `APP-RiskTracker-TechRisk=tech_risk,APP-RiskTracker-Auditor=auditor`). Roles from both sources are
   merged, so the add-on can own global roles while initiative ownership stays in the app.

Application roles the add-on needs to know about: **Tech Risk**, **Senior Management**, **Auditor**;
**Initiative Owner / Delegate** is derived from the initiative record and `Delegation` rows, so it needs no
entitlement group. `manage.py bootstrap --tech-risk EMP_ID --name "Name"` creates the first Tech Risk
assignment if you want the in-app table as the source of truth.

## Where things are

| Area | Location |
|---|---|
| Specification and design decisions | `docs/SPEC.md`, `docs/DESIGN.md` (DESIGN wins on conflict) |
| Clickable demo that fixed the design | `docs/demo/index.html` (open in a browser; fictional data) |
| Load one initiative from YAML | `scripts/load_initiative.py`, `scripts/initiative_template.yaml` |
| Fake data for development/tests | `scripts/generate_fake_data.py` (excluded from the Docker image) |
| Versioning base (append-only, as-of queries) | `core/versioning.py` |
| Identity, roles, visibility | `core/auth/`, `core/models.py`, `tracker/visibility.py` |
| Business models | `tracker/models.py` |
| Lifecycle stages | `tracker/lifecycle.py` (defaults; then versioned in `GuidancePage`). Guidance prose lives in TRAC |
| Handoff of lifecycle guidance to TRAC | `docs/TRAC_HANDOFF_lifecycle.md` |
| Domain services (RAG, curation, lifecycle, snapshots, triage, timelines, exec summary) | `tracker/services/` |
| Pages | `tracker/views/`, `templates/tracker/` |
| Day-to-day operation | `docs/RUNBOOK.md` |
| Working notes for maintainers | `WORK_NOTES.md` |
