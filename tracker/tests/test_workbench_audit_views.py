import io

import pytest
from django.utils.html import escape
from scripts.generate_fake_data import seed as generate_fake_data
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from core.models import Role, RoleAssignment
from django.urls import NoReverseMatch, reverse

from tracker.models import Control, Initiative, Snapshot, SnapshotRow, ThresholdConfig
from tracker.services import triage
from tracker.services.bundle import load_bundle

pytestmark = pytest.mark.django_db

TR, OWNER, CTO, AUDITOR = "E00001", "E00101", "E00201", "E00301"


@pytest.fixture
def seeded(db):
    generate_fake_data(stdout=lambda *a: None)


@pytest.fixture
def overdue(seeded):
    """An active initiative accepted 40 days ago with no check-in, so Chase offers a reminder."""
    from datetime import timedelta

    from core import clock

    return Initiative.create(
        TR, name="Quiet Overdue", owner_emp_id=OWNER, status=Initiative.Status.ACTIVE, phase="Intake",
        accepted_at=clock.now() - timedelta(days=40),
    )


def as_user(client, emp_id):
    client.post("/auth/switch/", {"emp_id": emp_id})
    return client


def sailpoint():
    return Initiative.objects.current().get(name="SailPoint Onboarding")


def xlsx_upload(rows, name="controls.xlsx"):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="application/vnd.ms-excel")


def test_triage_tech_risk(client, overdue):
    as_user(client, TR)
    resp = client.get("/workbench/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Risks to confirm" in body and "Open →" in body
    assert "Open →" in body and "?tab=" in body
    assert "Copy reminder" in body


def test_triage_group_headers_are_name_and_count_only(client, seeded):
    as_user(client, TR)
    body = client.get("/workbench/").content.decode()
    rows, _quiet = triage.triage()
    for group, items in rows.items():
        if items:
            assert f"{triage.GROUP_LABELS[group]} <span" in body and f"· {len(items)}</span>" in body
    assert "—" not in body.split("<h2 class=\"sect\">", 2)[1].split("</h2>")[0]


def test_triage_rows_link_to_their_tab(client, seeded):
    as_user(client, TR)
    body = client.get("/workbench/").content.decode()
    rows, _quiet = triage.triage()
    for items in rows.values():
        for row in items:
            if row.series:
                continue
            if row.action == "review":
                assert f'/initiative/{row.bundle.entity_id}/?tab={row.tab}">Open →' in body
            elif row.action == "curate":
                assert f'/initiative/{row.bundle.entity_id}/?tab={row.tab}">Open →' in body


def test_triage_remind_row_has_copy_button_not_link(client, overdue):
    as_user(client, TR)
    rows, _quiet = triage.triage()
    row = next(r for r in rows["Chase"] if r.action == "remind" and r.bundle.entity_id == overdue.entity_id)
    assert row.what.startswith("Check-in overdue")
    body = client.get("/workbench/").content.decode()
    assert f'hx-get="/workbench/remind/{row.bundle.entity_id}/"' in body


def test_reminder_modal_shows_text_in_readonly_textarea(client, overdue):
    as_user(client, TR)
    rows, _quiet = triage.triage()
    row = next(r for r in rows["Chase"] if r.action == "remind" and r.bundle.entity_id == overdue.entity_id)
    resp = client.get(reverse("reminder_modal", args=[row.bundle.entity_id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "<textarea" in body and "readonly" in body
    assert "Copy this message" in body and "Close" in body
    assert escape(triage.reminder_text(load_bundle(row.bundle.initiative), "http://testserver")) in body
    assert "<script" not in body


def test_reminder_modal_tech_risk_only(client, seeded):
    init = sailpoint()
    as_user(client, OWNER)
    assert client.get(reverse("reminder_modal", args=[init.entity_id])).status_code == 403
    as_user(client, TR)
    assert client.get(reverse("reminder_modal", args=["00000000-0000-0000-0000-000000000000"])).status_code == 404


def test_triage_forbidden_for_exec(client, seeded):
    as_user(client, CTO)
    assert client.get("/workbench/").status_code == 403
    assert client.get("/workbench/admin/").status_code == 403


def test_take_snapshot(client, seeded):
    as_user(client, TR)
    resp = client.post("/workbench/snapshots/take/", {"label": "Nov pack", "note": "n"})
    assert resp.status_code == 302
    snap = Snapshot.objects.get(label="Nov pack")
    assert SnapshotRow.objects.filter(snapshot=snap).exists()
    body = client.get("/workbench/snapshots/").content.decode()
    assert "Nov pack" in body and f"snapshot={snap.pk}" in body


def test_take_snapshot_requires_label(client, seeded):
    as_user(client, TR)
    client.post("/workbench/snapshots/take/", {"label": " "})
    assert not Snapshot.objects.exists()


def test_thresholds_and_log(client, seeded):
    as_user(client, TR)
    resp = client.post("/workbench/admin/thresholds/", {"amber_stale_days": "35", "red_stale_days": "60"})
    assert resp.status_code == 302
    row = ThresholdConfig.objects.current().get(key="amber_stale_days")
    assert row.value == 35
    assert not ThresholdConfig.objects.filter(key="red_stale_days").exists()  # unchanged -> no row
    body = client.get("/workbench/admin/").content.decode()
    assert "amber_stale_days" in body and "<del>30</del>" in body and "<ins>35</ins>" in body
    client.post("/workbench/admin/thresholds/", {"amber_stale_days": "40"})
    log = client.get("/workbench/admin/").content.decode()
    assert "<del>35</del>" in log and "<ins>40</ins>" in log


def test_control_template(client, seeded):
    as_user(client, TR)
    resp = client.get("/workbench/admin/controls/template.xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp["Content-Type"]
    assert "control_library_template.xlsx" in resp["Content-Disposition"]
    ws = load_workbook(io.BytesIO(resp.content)).active
    assert [c.value for c in ws[1]] == ["control_id", "name", "framework", "control_owner", "in_scope", "trac_url"]


def test_upload_controls(client, seeded):
    as_user(client, TR)
    before = Control.objects.current().count()
    f = xlsx_upload([
        ["Control_ID", "Name", "Framework", "Control_Owner", "In_Scope"],
        ["NEW-01", "A brand new control", "SOX ITGC", "IAM", True],
        ["ITGC-LA-02", "Quarterly user access review (revised)", "SOX ITGC", "IAM", "yes"],
    ])
    resp = client.post("/workbench/admin/controls/upload/", {"file": f, "note": "test"})
    assert resp.status_code == 302
    assert Control.objects.current().count() == before + 1
    la02 = Control.objects.current().get(control_id="ITGC-LA-02")
    assert la02.name.endswith("(revised)") and la02.upload is not None
    assert Control.objects.filter(entity_id=la02.entity_id).count() == 2
    assert Control.objects.current().filter(control_id="ITGC-CM-01").exists()  # absent controls kept
    msg = client.get("/workbench/admin/").content.decode()
    assert "1 created, 1 updated" in msg and "not in the file" in msg
    assert "controls.xlsx" in msg


def test_upload_trac_url_column_sets_link(client, seeded):
    as_user(client, TR)
    f = xlsx_upload([
        ["control_id", "name", "framework", "control_owner", "in_scope", "TRAC_URL"],
        ["ITGC-LA-02", "Quarterly user access review", "SOX ITGC", "IAM", True, "https://trac.example.com/la-02"],
    ])
    client.post("/workbench/admin/controls/upload/", {"file": f})
    assert Control.objects.current().get(control_id="ITGC-LA-02").trac_url == "https://trac.example.com/la-02"


def test_upload_without_trac_url_column_keeps_links(client, seeded):
    as_user(client, TR)
    ctl = Control.objects.current().get(control_id="ITGC-LA-02")
    ctl.new_version(TR, trac_url="https://trac.example.com/keep")
    f = xlsx_upload([
        ["control_id", "name", "framework", "control_owner", "in_scope"],
        ["ITGC-LA-02", ctl.name, ctl.framework, ctl.control_owner, ctl.in_scope],
    ])
    client.post("/workbench/admin/controls/upload/", {"file": f})
    assert Control.objects.current().get(control_id="ITGC-LA-02").trac_url == "https://trac.example.com/keep"


def test_control_library_shows_trac_guidance_column(client, seeded):
    as_user(client, TR)
    ctl = Control.objects.current().get(control_id="ITGC-LA-02")
    body = client.get("/workbench/admin/").content.decode()
    assert "TRAC guidance" in body and "Add link" in body
    ctl.new_version(TR, trac_url="https://trac.example.com/la-02")
    body = client.get("/workbench/admin/").content.decode()
    assert 'href="https://trac.example.com/la-02" target="_blank"' in body


def test_control_trac_link_modal_and_save(client, seeded):
    as_user(client, TR)
    ctl = Control.objects.current().get(control_id="ITGC-LA-02")
    url = reverse("control_trac_link", args=[ctl.entity_id])
    assert 'name="trac_url"' in client.get(url).content.decode()
    resp = client.post(url, {"trac_url": "https://trac.example.com/la-02"})
    assert resp.status_code == 204 and resp["HX-Refresh"] == "true"
    cur = Control.objects.current().get(control_id="ITGC-LA-02")
    assert cur.trac_url == "https://trac.example.com/la-02"
    assert Control.objects.filter(entity_id=ctl.entity_id).count() == 2
    client.post(url, {"trac_url": "https://trac.example.com/la-02"})  # unchanged -> no new version
    assert Control.objects.filter(entity_id=ctl.entity_id).count() == 2
    client.post(url, {"trac_url": ""})
    assert Control.objects.current().get(control_id="ITGC-LA-02").trac_url == ""


def test_control_trac_link_rejects_bad_url_and_non_tech_risk(client, seeded):
    ctl = Control.objects.current().get(control_id="ITGC-LA-02")
    url = reverse("control_trac_link", args=[ctl.entity_id])
    as_user(client, TR)
    resp = client.post(url, {"trac_url": "javascript:alert(1)"})
    assert resp.status_code == 200 and "full link" in resp.content.decode()
    assert Control.objects.current().get(control_id="ITGC-LA-02").trac_url == ""
    as_user(client, AUDITOR)
    assert client.post(url, {"trac_url": "https://x.example.com"}).status_code == 403
    assert client.get(url).status_code == 403


def test_upload_bad_header(client, seeded):
    as_user(client, TR)
    before = Control.objects.count()
    f = xlsx_upload([["id", "name"], ["X", "Y"]])
    client.post("/workbench/admin/controls/upload/", {"file": f})
    assert Control.objects.count() == before
    assert "missing column" in client.get("/workbench/admin/").content.decode()


def test_roles_add_and_retire(client, seeded):
    as_user(client, TR)
    client.post("/workbench/admin/roles/", {"emp_id": "E09999", "display_name": "Newbie", "role": Role.AUDITOR})
    ra = RoleAssignment.objects.current().get(emp_id="E09999")
    assert ra.role == Role.AUDITOR
    assert "Newbie" in client.get("/workbench/admin/").content.decode()
    client.post(f"/workbench/admin/roles/{ra.entity_id}/retire/")
    assert not RoleAssignment.objects.current().filter(emp_id="E09999").exists()


def test_cannot_retire_last_tech_risk(client, seeded):
    as_user(client, TR)
    ra = RoleAssignment.objects.current().get(emp_id=TR, role=Role.TECH_RISK)
    client.post(f"/workbench/admin/roles/{ra.entity_id}/retire/")
    assert RoleAssignment.objects.current().filter(emp_id=TR, role=Role.TECH_RISK).exists()


def test_close_refused_while_stage6_incomplete(client, seeded):
    as_user(client, TR)
    init = sailpoint()
    resp = client.post(f"/initiative/{init.entity_id}/close/", {"closure_control_outcome": "All ready"}, follow=False)
    assert resp.status_code == 302
    assert sailpoint().status == Initiative.Status.ACTIVE
    from django.contrib.messages import get_messages
    msgs = [str(m) for m in get_messages(resp.wsgi_request)]
    assert any("Stage 6" in m and "not complete" in m for m in msgs)


def test_archive_requires_closed(client, seeded):
    as_user(client, TR)
    client.post(f"/initiative/{sailpoint().entity_id}/archive/")
    assert sailpoint().status == Initiative.Status.ACTIVE


def test_audit_home_is_pickers_only(client, seeded):
    as_user(client, AUDITOR)
    resp = client.get("/audit/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'name="snapshot"' in body and 'name="as_of"' in body
    assert 'action="/exec/"' in body
    assert "SailPoint Onboarding" not in body
    assert "Reference pages" not in body and "Initiative history" not in body
    as_user(client, OWNER)
    assert client.get("/audit/").status_code == 403


def test_audit_home_snapshot_picker_lists_snapshots(client, seeded):
    as_user(client, TR)
    client.post("/workbench/snapshots/take/", {"label": "Nov pack"})
    snap = Snapshot.objects.get(label="Nov pack")
    body = client.get("/audit/").content.decode()
    assert f'<option value="{snap.pk}">Nov pack' in body


@pytest.mark.parametrize("path", ["/audit/controls/", "/audit/access-model/"])
def test_removed_audit_pages_are_gone(client, seeded, path):
    as_user(client, AUDITOR)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("name", ["control_mapping", "access_model"])
def test_removed_audit_url_names_do_not_resolve(name):
    with pytest.raises(NoReverseMatch):
        reverse(name)


def test_history_auditor_vs_tech_risk(client, seeded):
    init = sailpoint()
    url = f"/initiative/{init.entity_id}/history/"
    internal = "Go-live overlaps the Q4 access certification cycle"
    as_user(client, AUDITOR)
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Target go live" in body
    assert "Certification evidence format not yet agreed" in body  # exec-visible risk
    assert internal not in body
    assert "HR feed must supply employee ID" not in body  # owner-layer risk
    assert "Sponsor pushing" not in body  # internal note
    as_user(client, TR)
    body = client.get(url).content.decode()
    assert internal in body and "Sponsor pushing" in body


def test_history_forbidden_for_owner_and_two_point_diff(client, seeded):
    init = sailpoint()
    url = f"/initiative/{init.entity_id}/history/"
    as_user(client, OWNER)
    assert client.get(url).status_code == 403
    as_user(client, AUDITOR)
    today = __import__("core.clock", fromlist=["today"]).today().isoformat()
    resp = client.get(url, {"from": today, "to": today})
    assert resp.status_code == 200
    assert "No differences" in resp.content.decode()
    resp = client.get(url, {"from": "2020-01-01", "to": today})
    assert "<ins>added</ins>" in resp.content.decode()


def test_close_and_archive_when_stage6_complete(client, seeded):
    from datetime import date

    from tracker.services import lifecycle
    from tracker.services.bundle import load_bundle

    init = sailpoint()
    n = len(lifecycle.current_guidance()[6].checklist)
    for i in range(n):
        lifecycle.accept_item(load_bundle(init), TR, 6, i, evidence_ref="ref")
    lifecycle.record_gate(load_bundle(init), TR, 6, "CTO", date.today(), "ref")
    as_user(client, TR)
    client.post(f"/initiative/{init.entity_id}/close/", {"closure_control_outcome": "All controls ready"})
    closed = sailpoint()
    assert closed.status == Initiative.Status.CLOSED and closed.closed_at
    assert closed.closure_control_outcome == "All controls ready"
    client.post(f"/initiative/{init.entity_id}/archive/")
    assert sailpoint().status == Initiative.Status.ARCHIVED


def test_history_shows_choice_labels_for_stage_checks(client, seeded):
    as_user(client, TR)
    body = client.get(f"/initiative/{sailpoint().entity_id}/history/").content.decode()
    assert "Stage item" in body and "Submitted" in body
    assert "<ins>submitted</ins>" not in body


def test_snapshots_and_admin_tabs_have_no_explanatory_sentences(client, seeded):
    as_user(client, TR)
    snaps = client.get("/workbench/snapshots/").content.decode()
    assert "Nothing here blocks a snapshot" not in snaps and "Immutable once taken" not in snaps
    admin = client.get("/workbench/admin/").content.decode()
    assert "Editable by any Tech Risk user" not in admin and "versions the library" not in admin


def test_snapshot_record_page(client, seeded):
    from tracker.services import snapshots
    snap = snapshots.take_snapshot(TR, "Record check")
    as_user(client, TR)
    html = client.get(reverse("snapshot_record", args=[snap.pk])).content.decode()
    assert "Snapshot record: Record check" in html and "View as presented" in html
    assert reverse("snapshot_record", args=[snap.pk]) in client.get(reverse("audit")).content.decode()
    as_user(client, "E00101")
    assert client.get(reverse("snapshot_record", args=[snap.pk])).status_code == 403
