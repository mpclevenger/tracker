"""Generate FAKE data for local development and tests. Not part of the application.

Creates fictional people, a fictional control library, lifecycle guidance (via the app's bootstrap
service) and one fictional initiative with history, so every page has something to show.

    python scripts/generate_fake_data.py            # from the repo root, with DATABASE_URL etc. exported

Everything created here is invented. Do not run against a production database.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
from django.apps import apps  # noqa: E402

if not apps.ready:
    django.setup()

from django.utils import timezone  # noqa: E402
from core import clock
from core.models import Person, Role, RoleAssignment
from meetings.models import FollowUp, Meeting, MeetingInitiative
from tracker.models import (
    Control,
    ControlImpact,
    ControlUpload,
    CurationEvent,
    GateApproval,
    Initiative,
    Milestone,
    Overlay,
    Phase,
    RiskItem,
    StageCheck,
    Update,
    Visibility,
)
from tracker.services import lifecycle, rag

SYSTEM = "SYSTEM"
TR = "E00001"

PEOPLE = [
    ("E00001", "Jane", Role.TECH_RISK),
    ("E00101", "John", None),
    ("E00201", "CTO", Role.SENIOR_MANAGEMENT),
    ("E00301", "Auditor View", Role.AUDITOR),
]

CONTROLS = [
    ("ITGC-CM-01", "Change approval before production deployment", "SOX ITGC", "Change Mgmt"),
    ("ITGC-LA-01", "Joiner / mover / leaver provisioning approval", "SOX ITGC", "IAM"),
    ("ITGC-LA-02", "Quarterly user access review", "SOX ITGC", "IAM"),
    ("ITGC-LA-04", "Privileged access provisioning approval", "SOX ITGC", "IAM"),
    ("ITGC-CO-03", "Batch job monitoring and failure follow-up", "SOX ITGC", "IT Ops"),
    ("FR-REC-07", "Daily sub-ledger to GL reconciliation", "Financial reporting", "Finance Ops"),
    ("ITGC-BK-01", "Backup completion and restore test", "SOX ITGC", "IT Ops"),
    ("SEC-VM-02", "Critical vulnerability remediation within SLA", "Cyber", "Cyber"),
]


def seed_meeting(stdout=print):
    """Add one fictional meeting with two follow-ups to the sample initiative; skipped if any meeting exists."""
    init = Initiative.objects.current().filter(name="SailPoint Onboarding").first()
    if init is None or Meeting.objects.current_including_deleted().exists():
        return
    today = clock.today()
    meeting = Meeting.create(
        SYSTEM,
        held_on=today - timedelta(days=14),
        what="Connector defects checkpoint",
        who="Jane (Tech Risk), John (program lead)",
        result="Build exit is forecast two weeks late because of connector "
               "defects. Agreed to share the defect burn-down and confirm the certification report walkthrough date.",
        stage=2,
    )
    MeetingInitiative.create(SYSTEM, meeting_entity=meeting.entity_id, initiative_entity=init.entity_id)
    FollowUp.create(
        SYSTEM, meeting_entity=meeting.entity_id, initiative_entity=init.entity_id,
        text="Share the connector defect burn-down chart", owner="John", due_date=today - timedelta(days=7),
    )
    FollowUp.create(
        SYSTEM, meeting_entity=meeting.entity_id, initiative_entity=init.entity_id,
        text="Confirm the certification report walkthrough date", owner="Jane",
        due_date=today - timedelta(days=3), status=FollowUp.Status.DONE, closed_on=today - timedelta(days=2),
        closure_note="Walkthrough booked for next month.",
    )
    stdout("Fake meeting generated.")


def seed_watch(stdout=print):
    """Add one fictional watch list item; skipped if the name already exists."""
    name = "Expense management platform renewal"
    if Initiative.objects.current().filter(name=name).exists():
        return
    today = clock.today()
    Initiative.create(
        SYSTEM,
        name=name,
        status=Initiative.Status.WATCH,
        owner_emp_id="E00101",
        decision="Renew with current vendor or move to a new platform",
        decision_due=today + timedelta(days=60),
        key_date_label="Contract ends",
        key_date=today + timedelta(days=150),
        options_considered="Renew for 3 years; migrate to the ERP module",
        sox_applicable=True,
        affects_sox_app=True,
        moves_to_vendor_or_cloud=True,
        next_check=today + timedelta(days=14),
    )
    stdout("Fake watch item generated.")


def seed(stdout=print):
    """Idempotent: safe to run more than once."""
    if True:
        for emp_id, name, role in PEOPLE:
            Person.objects.get_or_create(pk=emp_id, defaults={"display_name": name})
            if role and not RoleAssignment.objects.current().filter(
                emp_id=emp_id, role=role, initiative_entity__isnull=True
            ).exists():
                RoleAssignment.create(SYSTEM, emp_id=emp_id, role=role)

        lifecycle.seed_guidance(TR)

        if not Control.objects.current().exists():
            upload = ControlUpload.objects.create(
                uploaded_by=TR, filename="control_library_2026Q4.xlsx", row_count=len(CONTROLS), note="Q4 refresh"
            )
            for cid, name, fw, owner in CONTROLS:
                Control.create(TR, control_id=cid, name=name, framework=fw, control_owner=owner, upload=upload)
        controls = {c.control_id: c for c in Control.objects.current()}

        if Initiative.objects.current().exists():
            stdout("Initiatives already present; skipping.")
            seed_meeting(stdout)
            seed_watch(stdout)
            return

        today = date.today()
        now = timezone.now()
        D = lambda s: date.fromisoformat(s)  # noqa: E731
        T = lambda s: timezone.make_aware(timezone.datetime.fromisoformat(s + "T12:00:00"))  # noqa: E731

        init = Initiative.create(
            SYSTEM,
            name="SailPoint Onboarding",
            owner_emp_id="E00101",
            sponsor="CISO",
            status=Initiative.Status.ACTIVE,
            phase="Build",
            description="Onboard SailPoint as the identity governance platform: joiner/mover/leaver provisioning "
                        "and quarterly access certifications. Wave 1 covers the 12 in-scope SOX applications.",
            why_it_matters="Replaces manual user access reviews and provisioning approvals for every SOX "
                           "application; the certification evidence auditors rely on changes shape.",
            expected_systems="12 in-scope SOX applications; HR feed; Entra ID",
            target_go_live=D("2027-01-19"),
            baseline_go_live=D("2026-12-15"),
            submitted_at=T("2026-07-01"),
            accepted_at=T("2026-07-14"),
            affects_tier01_app=True,
            affects_sox_app=True,
            sox_applicable=True,
            changes_key_controls=True,
            changes_access_model=True,
        )
        eid = init.entity_id

        phases = [
            ("Design", 1, "2026-07-01", "2026-08-15", "2026-07-01", "2026-08-20"),
            ("Build", 2, "2026-08-16", "2026-10-31", "2026-08-21", "2026-11-20"),
            ("Test", 3, "2026-11-01", "2026-12-05", "2026-11-21", "2027-01-05"),
            ("Cutover", 4, "2026-12-06", "2026-12-15", "2027-01-06", "2027-01-19"),
            ("Hypercare", 5, "2026-12-16", "2027-01-31", "2027-01-20", "2027-03-05"),
        ]
        for order, (name, stage, ps, pe, fs, fe) in enumerate(phases):
            Phase.create(
                SYSTEM, initiative_entity=eid, name=name, order=order, stage=stage,
                planned_start=D(ps), planned_end=D(pe), forecast_start=D(fs), forecast_end=D(fe),
                actual_start=D(fs) if D(fs) <= today else None, actual_end=D(fe) if D(fe) < today else None,
            )

        milestones = {}
        for name, p, f, a, pct, st, key, note in [
            ("Connector design sign-off", "2026-08-15", "2026-08-20", "2026-08-20", 100, "done", True, ""),
            ("Wave 1 connectors built", "2026-10-31", "2026-11-20", None, 85, "slipped", True, "ERP and HR connector defects"),
            ("Certification dry run (UAT exit)", "2026-12-05", "2027-01-05", None, 20, "slipped", True, ""),
            ("Go-live (Wave 1)", "2026-12-15", "2027-01-19", None, 0, "slipped", True, ""),
        ]:
            milestones[name] = Milestone.create(
                SYSTEM, initiative_entity=eid, name=name, planned=D(p), forecast=D(f), actual=D(a) if a else None,
                pct_complete=pct, status=st, is_key=key, note=note,
            )

        for t, title, r, owner, tgt, st, mit, src, vis in [
            ("risk", "Certification evidence format not yet agreed with Internal Audit", "H", "John", "2026-11-25",
             "open", "Walkthrough of SailPoint certification reports booked with Internal Audit; manual UAR runs in "
             "parallel for Q4.", "owner", Visibility.EXEC),
            ("issue", "ERP and HR connector defects delaying build exit", "M", "Vendor PM", "2026-11-20",
             "mitigating", "Weekly defect triage with vendor.", "owner", Visibility.EXEC),
            ("dependency", "HR feed must supply employee ID for all contractors", "M", "HR Systems", "2026-12-01",
             "open", "", "tech_risk", Visibility.OWNER),
            ("risk", "Go-live overlaps the Q4 access certification cycle — risk of two certifications or none",
             "H", "Tech Risk", "2026-12-01", "open",
             "Challenge raised with sponsor; February go-live under discussion.", "tech_risk", Visibility.INTERNAL),
        ]:
            RiskItem.create(
                SYSTEM, initiative_entity=eid, type=t, title=title, rating=r, owner=owner, target_date=D(tgt),
                status=st, mitigation=mit, source=src, visibility=vis,
            )

        needed_by = {"ITGC-CM-01": "Connector design sign-off", "ITGC-LA-04": "Certification dry run (UAT exit)"}
        for cid, itype, comp, ready, ev in [
            ("ITGC-LA-02", "modified", "Manual Q4 user access review runs in parallel", "submitted",
             "https://evidence.example/sailpoint/uar"),
            ("ITGC-LA-04", "modified", "", "not_started", ""),
            ("ITGC-LA-01", "new", "Manual provisioning approvals continue until cutover", "in_progress", ""),
            ("ITGC-CM-01", "modified", "", "ready", "https://evidence.example/sailpoint/cm"),
        ]:
            linked = milestones.get(needed_by.get(cid))
            ControlImpact.create(
                SYSTEM, initiative_entity=eid, control_entity=controls[cid].entity_id, impact_type=itype,
                compensating_control=comp, readiness=ready, evidence_link=ev,
                needed_by_milestone=linked.entity_id if linked else None,
            )

        for order, (t, text, vis) in enumerate([
            ("commentary", "Program has re-planned twice. Our concern is the Q4 certification cycle and the audit "
                           "evidence it produces, not the connectors.", Visibility.EXEC),
            ("highlight", "January go-live overlaps the Q4 access certification cycle; decision needed on which "
                          "tool certifies Q4", Visibility.EXEC),
            ("highlight", "Certification evidence format still to be agreed with Internal Audit", Visibility.EXEC),
            ("highlight", "Privileged access provisioning through SailPoint not started — manual approvals continue",
             Visibility.EXEC),
            ("internal_note", "Sponsor pushing for January to hit the OKR. Escalate to CTO if no decision on the Q4 "
                              "cycle by Nov 15.", Visibility.INTERNAL),
        ]):
            Overlay.create(TR, initiative_entity=eid, type=t, text=text, visibility=vis, order=order)

        for at, text, diff in [
            ("2026-09-18", "Build 60% complete, on plan. 8 of 12 connectors configured.", {}),
            ("2026-10-09", "ERP and HR connector defects slowing build exit; forecast Nov 10. Certification report "
                           "walkthrough scheduled with Internal Audit.",
             {"Wave 1 connectors built · forecast": ["2026-10-31", "2026-11-10"]}),
            ("2026-10-30", "Build exit re-forecast to Nov 20. Connector defect count down from 41 to 18. Go-live "
                           "moved to Jan 19 after steering committee.",
             {"target_go_live": ["2026-12-15", "2027-01-19"]}),
        ]:
            Update.objects.create(initiative_entity=eid, author_emp_id="E00101", posted_at=T(at), text=text, change_diff=diff)

        guidance = lifecycle.current_guidance()
        gv = guidance[0].version_label
        for stage, idx, when, basis, ev in [
            (0, 0, "2026-07-14", "reviewed", "Intake acceptance record"),
            (0, 1, "2026-07-14", "reviewed", "Intake acceptance record"),
            (0, 2, "2026-07-14", "reviewed", "Intake form"),
            (1, 0, "2026-08-22", "reviewed", "https://evidence.example/sailpoint/design-signoff"),
            (1, 1, "2026-08-29", "reviewed", "https://evidence.example/sailpoint/control-impact-assessment"),
            (1, 2, "2026-09-05", "reviewed", "SoD matrix v3 (program SharePoint)"),
            (1, 3, "2026-09-12", "reviewed", "https://evidence.example/sailpoint/data-migration-plan"),
            (1, 4, "2026-10-15", "attested", "Owner confirmation in Oct 9 update"),
            (2, 0, "2026-10-20", "reviewed", "Config baseline CB-2026-11"),
        ]:
            StageCheck.create(
                TR, initiative_entity=eid, stage=stage, item_index=idx, item_text=guidance[stage].checklist[idx],
                basis=basis, evidence_ref=ev, guidance_version=gv, valid_from=T(when),
            )
        StageCheck.create(  # awaiting Tech Risk review
            "E00101", initiative_entity=eid, stage=2, item_index=1, item_text=guidance[2].checklist[1],
            status=StageCheck.Status.SUBMITTED, evidence_ref="https://evidence.example/sailpoint/project-access",
            guidance_version=gv, valid_from=T("2026-11-03"),
        )
        for stage, approver, on, ev in [
            (0, "Head of Tech Risk", "2026-07-14", "Intake acceptance record"),
            (1, "Adam Brown (Sponsor) and Mary Jones (Head of Tech Risk)", "2026-10-16",
             "https://evidence.example/sailpoint/design-signoff"),
        ]:
            GateApproval.create(TR, initiative_entity=eid, stage=stage, approver=approver, approved_on=D(on),
                                evidence_ref=ev, valid_from=T(on))
        CurationEvent.objects.create(initiative_entity=eid, author_emp_id=TR, at=T("2026-11-02"), what="seeded")

        rag.recompute(init)
        seed_meeting(stdout)
        seed_watch(stdout)
        stdout("Fake data generated: SailPoint Onboarding.")


if __name__ == "__main__":
    seed()
