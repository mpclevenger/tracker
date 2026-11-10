"""Views: Tech Risk workbench (triage, snapshots, admin) and initiative closure."""

import io
from collections import Counter

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from core import modules as module_switches
from core.models import ModuleSettings, Person, Role, RoleAssignment
from tracker.models import (
    Control,
    ControlImpact,
    ControlUpload,
    GateSettings,
    Initiative,
    OversightLevel,
    Snapshot,
    ThresholdConfig,
)
from tracker import lifecycle as rules
from tracker.services import curation, lifecycle, rag, snapshots, triage as triage_svc
from tracker.services.bundle import load_bundle
from tracker.views.common import get_initiative, require_tech_risk_or_403, resolve_scope, scope_context

CONTROL_COLUMNS = ["control_id", "name", "framework", "control_owner", "in_scope"]
OPTIONAL_COLUMNS = ["trac_url"]
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

THRESH_LABELS = {
    "amber_key_milestone_slip_days": "Amber: key milestone late by more than (days)",
    "amber_go_live_window_days": "Amber: go-live within (days) and control in progress",
    "amber_stale_days": "Amber: no check-in for more than (days)",
    "red_key_milestone_slip_days": "Red: key milestone late by more than (days)",
    "red_go_live_slip_days": "Red: go-live later than baseline by more than (days)",
    "red_go_live_window_days": "Red: go-live within (days) and control not started",
    "red_stale_days": "Red: no check-in for more than (days)",
    "early_warning_stale_days": "Check-in reminder after (days; no status change)",
}


def _wb_context(request, tab, rows=None):
    """Shared context for the three tabs; computes the Triage count for the tab label."""
    if rows is None:
        rows, _quiet = triage_svc.triage(request.modules["meetings"], watch_list=request.modules["watch_list"])
    return {"nav": "workbench", "wb_tab": tab, "wb_count": sum(len(v) for v in rows.values())}


# --- Triage -------------------------------------------------------------------------------------


def triage(request):
    """Triage tab: one table per action group plus the quiet-initiatives footer."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    scope = resolve_scope(request, request.actor)
    rows, quiet = triage_svc.triage(request.modules["meetings"], scope, request.modules["watch_list"])
    groups = [(g, rows[g]) for g in triage_svc.GROUPS if rows.get(g)]
    ctx = _wb_context(request, "triage", rows)
    ctx.update({"groups": groups, "quiet": quiet, **scope_context(request, scope)})
    return render(request, "tracker/workbench_triage.html", ctx)


@require_http_methods(["GET"])
def reminder_modal(request, entity_id):
    """Modal with the check-in reminder text to copy into Teams or email."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    init = get_initiative(request, entity_id)
    text = triage_svc.reminder_text(load_bundle(init), request.build_absolute_uri("/").rstrip("/"))
    return render(request, "tracker/partials/reminder_modal.html", {
        "initiative": init, "text": text, "rows": text.count("\n") + 3,
    })


# --- Snapshots ----------------------------------------------------------------------------------


def snapshots_tab(request):
    """Snapshots tab: readiness table, take-snapshot form and past snapshots."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    readiness = []
    for r in triage_svc.readiness():
        readiness.append({"r": r, "stale_days": rag.stale_days(r.bundle)})
    ctx = _wb_context(request, "snapshots")
    ctx.update({
        "readiness": readiness,
        "last_snapshot": snapshots.last_snapshot(),
        "past_snapshots": Snapshot.objects.order_by("-taken_at"),
    })
    return render(request, "tracker/workbench_snapshots.html", ctx)


@require_POST
def take_snapshot(request):
    """Freeze the exec/audit-visible state under a label."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    label = request.POST.get("label", "").strip()
    note = request.POST.get("note", "").strip()
    if not label:
        messages.error(request, "Enter a snapshot label.")
        return redirect("workbench_snapshots")
    snap = snapshots.take_snapshot(request.actor.emp_id, label[:200], note)
    messages.success(request, f'Snapshot "{snap.label}" taken. It cannot be changed.')
    return redirect("workbench_snapshots")


# --- Admin --------------------------------------------------------------------------------------


def _threshold_log():
    log = []
    for row in ThresholdConfig.objects.all().order_by("-valid_from"):
        prev = row.previous_version()
        old = prev.value if prev else settings.RAG_THRESHOLD_DEFAULTS.get(row.key)
        log.append({"row": row, "label": THRESH_LABELS.get(row.key, row.key), "old": old})
    return log


def admin_tab(request):
    """Admin tab: control library, RAG thresholds with change log, gate requirements, role management."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    usage = Counter(ControlImpact.objects.current().values_list("control_entity", flat=True))
    controls = [{"c": c, "used": usage.get(c.entity_id, 0)} for c in Control.objects.current()]
    current_t = rag.thresholds()
    thresholds = [
        {"key": k, "label": THRESH_LABELS.get(k, k), "value": current_t[k], "default": d}
        for k, d in settings.RAG_THRESHOLD_DEFAULTS.items()
    ]
    initiatives = {i.entity_id: i for i in Initiative.objects.current().listed()}
    roles = []
    for ra in RoleAssignment.objects.current().order_by("role", "emp_id"):
        roles.append({
            "ra": ra,
            "scope": initiatives[ra.initiative_entity].name if ra.initiative_entity in initiatives else None,
        })
    ctx = _wb_context(request, "admin")
    ctx.update(_gate_ctx())
    ctx.update(_modules_ctx())
    ctx.update({
        "controls": controls,
        "uploads": ControlUpload.objects.all(),
        "thresholds": thresholds,
        "threshold_log": _threshold_log(),
        "roles": roles,
        "role_choices": Role.choices,
        "initiative_choices": sorted(initiatives.values(), key=lambda i: i.name.lower()),
    })
    return render(request, "tracker/workbench_admin.html", ctx)


@require_http_methods(["GET", "POST"])
def control_trac_link(request, entity_id):
    """Set or clear the TRAC guidance link for a control (modal)."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    control = Control.objects.current().filter(entity_id=entity_id).first()
    if control is None:
        raise Http404("No such control")
    error = ""
    if request.method == "POST":
        url = request.POST.get("trac_url", "").strip()
        try:
            if url:
                URLValidator(schemes=["http", "https"])(url)
        except ValidationError:
            error = "Enter a full link starting with https://"
        else:
            if url != control.trac_url:
                control.new_version(request.actor.emp_id, trac_url=url)
            return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return render(request, "tracker/partials/trac_link_modal.html", {"control": control, "error": error})


GATE_STAGES = range(1, 7)


def _cell_name(level, stage):
    return f"m_{level}_{stage}"


class GateSettingsForm(forms.Form):
    version_label = forms.CharField(max_length=20, label="Version")
    change_note = forms.CharField(max_length=300, label="Change note")
    approved_by = forms.CharField(max_length=200, required=False, label="Approved by")
    approved_on = forms.DateField(required=False, label="Approved on")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(rules.REQUIREMENT_LABELS.items())
        for level in OversightLevel.values:
            for stage in GATE_STAGES:
                self.fields[_cell_name(level, stage)] = forms.ChoiceField(choices=choices)
        for key in rules.QUESTION_KEYS:
            self.fields[f"q_{key}"] = forms.CharField(max_length=200)

    def clean(self):
        data = super().clean()
        if bool((data.get("approved_by") or "").strip()) != bool(data.get("approved_on")):
            raise forms.ValidationError("Enter both Approved by and Approved on, or neither.")
        return data

    def matrix(self):
        d = self.cleaned_data
        return {level: [None] + [d[_cell_name(level, s)] for s in GATE_STAGES] for level in OversightLevel.values}

    def question_labels(self):
        return {key: self.cleaned_data[f"q_{key}"].strip() for key in rules.QUESTION_KEYS}


def _gate_ctx():
    current = rules.gate_settings()
    matrix = rules.gate_matrix(current)
    return {
        "gate_settings": current,
        "gate_version": rules.gate_matrix_version(current),
        "gate_is_draft": rules.gate_matrix_is_draft(current),
        "gate_stages": lifecycle.stage_infos()[1:7],
        "gate_rows": [
            {"label": label, "cells": [(_cell_name(level, s), matrix[level][s]) for s in GATE_STAGES]}
            for level, label in OversightLevel.choices
        ],
        "gate_questions": [(f"q_{key}", label) for key, label in rules.oversight_questions(current)],
        "requirement_choices": list(rules.REQUIREMENT_LABELS.items()),
    }


@require_POST
def set_gate_settings(request):
    """Save a new version of the gate requirements and oversight question labels."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    form = GateSettingsForm(request.POST)
    if not form.is_valid():
        errors = [e for errs in form.errors.values() for e in errs]
        messages.error(request, "Gate requirements not saved. " + " ".join(dict.fromkeys(errors)))
        return redirect("workbench_admin")
    d = form.cleaned_data
    fields = {
        "matrix": form.matrix(), "question_labels": form.question_labels(),
        "version_label": d["version_label"].strip(), "change_note": d["change_note"].strip(),
        "approved_by": d["approved_by"].strip(), "approved_on": d["approved_on"],
    }
    current = rules.gate_settings()
    if current:
        current.new_version(request.actor.emp_id, **fields)
    else:
        GateSettings.create(request.actor.emp_id, **fields)
    messages.success(request, f"Gate requirements saved as {fields['version_label']}.")
    return redirect("workbench_admin")


class ModulesForm(forms.Form):
    change_note = forms.CharField(max_length=300, label="Change note")


def _modules_ctx():
    row = module_switches.current()
    state = module_switches.states(row)
    return {
        "module_rows": [{"key": k, "label": label, "desc": desc, "on": state[k]} for k, label, desc in module_switches.MODULES],
        "module_row": row,
    }


@require_POST
def set_modules(request):
    """Save a new version of the module switches."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    form = ModulesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Modules not saved. Enter a change note.")
        return redirect("workbench_admin")
    row = module_switches.current()
    old = module_switches.states(row)
    new = {k: k in request.POST for k in module_switches.KEYS}
    changed = [k for k in module_switches.KEYS if new[k] != old[k]]
    if not changed:
        messages.info(request, "No modules changed.")
        return redirect("workbench_admin")
    fields = {**new, "change_note": form.cleaned_data["change_note"].strip()}
    if row:
        row.new_version(request.actor.emp_id, **fields)
    else:
        ModuleSettings.create(request.actor.emp_id, **fields)
    summary = "; ".join(f"{module_switches.LABELS[k]} {'on' if new[k] else 'off'}" for k in changed)
    messages.success(request, f"{summary}.")
    return redirect("workbench_admin")


def _truthy(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return True  # blank defaults to in scope
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "x", "in scope", "in_scope"}
    return bool(value)


def _cell(value):
    return "" if value is None else str(value).strip()


def _parse_controls(upload_file):
    """Return a list of dicts from the workbook, or raise ValueError with a user-facing message."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(upload_file, read_only=True, data_only=True)
    except Exception as exc:  # unreadable or not an .xlsx file
        raise ValueError("Could not read the file as an .xlsx workbook.") from exc
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError("The workbook is empty.")
    names = [_cell(h).lower() for h in header]
    missing = [c for c in CONTROL_COLUMNS if c not in names]
    if missing:
        raise ValueError("Header row is missing column(s): " + ", ".join(missing) + ".")
    idx = {c: names.index(c) for c in CONTROL_COLUMNS + OPTIONAL_COLUMNS if c in names}
    parsed = []
    for n, row in enumerate(rows, start=2):
        if row is None or all(v is None or _cell(v) == "" for v in row):
            continue
        get = lambda c: row[idx[c]] if c in idx and idx[c] < len(row) else None  # noqa: E731
        cid = _cell(get("control_id"))
        if not cid:
            raise ValueError(f"Row {n} has no control_id.")
        name = _cell(get("name"))
        if not name:
            raise ValueError(f"Row {n} ({cid}) has no name.")
        entry = {
            "control_id": cid,
            "name": name,
            "framework": _cell(get("framework")),
            "control_owner": _cell(get("control_owner")),
            "in_scope": _truthy(get("in_scope")),
        }
        if "trac_url" in idx:
            entry["trac_url"] = _cell(get("trac_url"))
        parsed.append(entry)
    return parsed


@require_POST
def upload_controls(request):
    """Load the control library from an Excel file; versions changed controls, keeps absent ones."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Select an .xlsx file to upload.")
        return redirect("workbench_admin")
    emp = request.actor.emp_id
    try:
        parsed = _parse_controls(f)
        with transaction.atomic():
            upload = ControlUpload.objects.create(
                uploaded_by=emp, filename=f.name[:300], row_count=len(parsed),
                note=request.POST.get("note", "").strip(),
            )
            current = {c.control_id: c for c in Control.objects.current()}
            created = updated = unchanged = 0
            seen = set()
            for row in parsed:
                seen.add(row["control_id"])
                existing = current.get(row["control_id"])
                if existing is None:
                    Control.create(emp, upload=upload, **row)
                    created += 1
                    continue
                changes = {k: v for k, v in row.items() if k != "control_id" and getattr(existing, k) != v}
                if changes:
                    existing.new_version(emp, upload=upload, **changes)
                    updated += 1
                else:
                    unchanged += 1
            absent = len([cid for cid in current if cid not in seen])
    except ValueError as exc:
        messages.error(request, f"Upload failed: {exc}")
        return redirect("workbench_admin")
    except Exception as exc:  # validation errors from model cleaning roll the upload back
        messages.error(request, f"Upload failed and nothing was saved: {exc}")
        return redirect("workbench_admin")
    msg = f"Control library uploaded: {created} created, {updated} updated, {unchanged} unchanged."
    if absent:
        msg += f" {absent} existing control{'s' if absent != 1 else ''} not in the file were left as-is."
    messages.success(request, msg)
    return redirect("workbench_admin")


def control_template(request):
    """Download an .xlsx template with the expected header row and an example."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Controls"
    ws.append(CONTROL_COLUMNS + OPTIONAL_COLUMNS)
    ws.append(["ITGC-XX-01", "Example control description", "SOX ITGC", "Control owner team", True,
               "https://trac.example.com/controls/itgc-xx-01"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type=XLSX_TYPE)
    resp["Content-Disposition"] = 'attachment; filename="control_library_template.xlsx"'
    return resp


@require_POST
def set_threshold(request):
    """Save changed RAG thresholds (versioned) and recompute every active initiative."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    emp = request.actor.emp_id
    effective = rag.thresholds()
    changed, bad = [], []
    for key in settings.RAG_THRESHOLD_DEFAULTS:
        raw = request.POST.get(key)
        if raw is None or raw.strip() == "":
            continue
        try:
            value = int(raw)
        except ValueError:
            bad.append(key)
            continue
        if value < 0 or value == effective[key]:
            if value < 0:
                bad.append(key)
            continue
        row = ThresholdConfig.objects.current().filter(key=key).first()
        if row:
            row.new_version(emp, value=value)
        else:
            ThresholdConfig.create(emp, key=key, value=value)
        changed.append(THRESH_LABELS.get(key, key))
    if bad:
        messages.error(request, "Ignored invalid value for: " + ", ".join(THRESH_LABELS.get(k, k) for k in bad) + ".")
    if changed:
        for init in Initiative.objects.current().filter(status=Initiative.Status.ACTIVE):
            rag.recompute(init)
        messages.success(request, f"Threshold{'s' if len(changed) != 1 else ''} changed and logged: "
                                  + "; ".join(changed) + ".")
    elif not bad:
        messages.info(request, "No thresholds changed.")
    return redirect("workbench_admin")


@require_POST
def roles(request):
    """Add a role assignment (global, or scoped to one initiative)."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    emp_id = request.POST.get("emp_id", "").strip()
    name = request.POST.get("display_name", "").strip()
    role = request.POST.get("role", "")
    scope = request.POST.get("initiative_entity", "").strip() or None
    if not emp_id or role not in Role.values:
        messages.error(request, "Employee ID and a valid role are required.")
        return redirect("workbench_admin")
    if scope:
        match = Initiative.objects.current().listed().filter(entity_id=scope).first() if _is_uuid(scope) else None
        if match is None:
            messages.error(request, "Unknown initiative for the scope.")
            return redirect("workbench_admin")
    person, made = Person.objects.get_or_create(pk=emp_id, defaults={"display_name": name or emp_id})
    if not made and name and person.display_name != name:
        person.display_name = name
        person.save(update_fields=["display_name"])
    if RoleAssignment.objects.current().filter(emp_id=emp_id, role=role, initiative_entity=scope).exists():
        messages.info(request, f"{emp_id} already holds that role.")
        return redirect("workbench_admin")
    RoleAssignment.create(request.actor.emp_id, emp_id=emp_id, role=role, initiative_entity=scope)
    messages.success(request, f"Added {Role(role).label} for {person.display_name} ({emp_id}).")
    return redirect("workbench_admin")


def _is_uuid(value):
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


@require_POST
def retire_role(request, entity_id):
    """Retire a role assignment; the last global Tech Risk assignment cannot be removed."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    ra = RoleAssignment.objects.current().filter(entity_id=entity_id).first()
    if ra is None:
        messages.error(request, "Role assignment not found. Reload the page.")
        return redirect("workbench_admin")
    if (ra.role == Role.TECH_RISK and ra.initiative_entity is None
            and RoleAssignment.objects.current().filter(role=Role.TECH_RISK, initiative_entity__isnull=True).count() <= 1):
        messages.error(request, "Cannot retire the last Tech Risk assignment.")
        return redirect("workbench_admin")
    ra.retire(request.actor.emp_id)
    messages.success(request, f"Retired {ra.get_role_display()} for {ra.emp_id}.")
    return redirect("workbench_admin")


# --- Closure ------------------------------------------------------------------------------------


@require_POST
def close_initiative(request, entity_id):
    """Close an active initiative once Stage 6 is complete and the control outcome is recorded."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    init = get_initiative(request, entity_id)
    if init.status != Initiative.Status.ACTIVE:
        messages.error(request, "Only an active initiative can be closed.")
        return redirect("initiative", entity_id=init.entity_id)
    outcome = request.POST.get("closure_control_outcome", "").strip()
    bundle = load_bundle(init)
    stage6 = lifecycle.stage_statuses(bundle)[6]
    problems = []
    if not stage6.complete:
        parts = []
        if stage6.done < stage6.total:
            parts.append(f"{stage6.done} of {stage6.total} checklist items confirmed")
        if stage6.gate is None:
            parts.append("gate approval not recorded")
        problems.append("Stage 6 (Close) is not complete: " + ", ".join(parts or ["guidance missing"]))
    if not outcome:
        problems.append("record the control-readiness outcome at closure")
    if problems:
        messages.error(request, "Cannot close yet. " + "; ".join(problems) + ".")
        return redirect("initiative", entity_id=init.entity_id)
    emp = request.actor.emp_id
    new = init.new_version(emp, status=Initiative.Status.CLOSED, closed_at=timezone.now(),
                           closure_control_outcome=outcome)
    curation.touch(init.entity_id, emp, "closed initiative")
    rag.recompute(new)
    messages.success(request, f'"{init.name}" closed.')
    return redirect("initiative", entity_id=init.entity_id)


@require_POST
def delete_initiative(request, entity_id):
    """Delete (retire) an initiative after a typed-name confirmation and a required reason."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    init = get_initiative(request, entity_id)
    typed = request.POST.get("confirm_name", "").strip().casefold()
    if typed != init.name.strip().casefold():
        messages.error(request, "Type the initiative name exactly to confirm the delete.")
        return redirect("initiative", entity_id=init.entity_id)
    try:
        curation.delete_initiative(init, request.actor.emp_id, request.POST.get("reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("initiative", entity_id=init.entity_id)
    messages.success(request, f'Deleted "{init.name}".')
    return redirect("workbench")


@require_POST
def archive_initiative(request, entity_id):
    """Move a closed initiative to archived (leaves the exec summary, stays in history and mapping)."""
    if (denied := require_tech_risk_or_403(request)):
        return denied
    init = get_initiative(request, entity_id)
    if init.status != Initiative.Status.CLOSED:
        messages.error(request, "Only a closed initiative can be archived.")
        return redirect("initiative", entity_id=init.entity_id)
    emp = request.actor.emp_id
    new = init.new_version(emp, status=Initiative.Status.ARCHIVED)
    curation.touch(init.entity_id, emp, "archived initiative")
    rag.recompute(new)
    messages.success(request, f'"{init.name}" archived.')
    return redirect("initiative", entity_id=init.entity_id)
