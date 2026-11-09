"""The seven lifecycle stages: fixed numbers and colors, plus the defaults used to seed GuidancePage.

Guidance prose (exit gates, controls to consider, evidence, auditor questions) lives in TRAC, the
knowledge base. The tracker keeps only what it needs to operate: stage names, the checklist Tech Risk
confirms per initiative, the expected gate approver, and a link to the stage's TRAC page. After seeding,
names, approvers, checklists and TRAC links are edited in the app (versioned in GuidancePage).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stage:
    number: int
    name: str
    color: str
    approver: str
    checklist: list = field(default_factory=list)


STAGES = [
    Stage(0, "Intake", "#6e7781", "Head of Tech Risk",
          ["Intake accepted by Tech Risk", "Baseline go-live recorded", "Affected systems and controls listed"]),
    Stage(1, "Mobilize & Design", "#6e40c9",
          "Sponsor (design sign-off) and Head of Tech Risk (control impact assessment)",
          ["Design signed off by sponsor",
           "Control impact assessment complete — built from the control library for the affected systems and confirmed with control owners",
           "Segregation of duties (SoD) model agreed", "Migration approach documented",
           "Interim and compensating controls agreed, each with an end condition"]),
    Stage(2, "Build & Configure", "#0969da", "Program manager (build complete)",
          ["Configuration baselined", "Project environment access list retained",
           "Development and production access separated"]),
    Stage(3, "Test & Validate", "#1f883d", "Business owner (UAT) and Change Advisory Board (test exit)",
          ["User acceptance testing (UAT) signed off by business owner",
           "Each new or changed control tested before go-live by someone independent of the build (tester named)",
           "Tech Risk observed at least one control test",
           "Migration rehearsal reconciled (counts and balances)",
           "Defects within tolerance; disposition recorded"]),
    Stage(4, "Cutover & Go-live", "#9a6700", "Go/no-go chair (named) and Change Advisory Board",
          ["Go/no-go approved", "Production change approved", "Access provisioned per design",
           "Final reconciliation complete", "Backup taken; rollback plan ready"]),
    Stage(5, "Hypercare & Stabilize", "#bf3989",
          "Head of Tech Risk (control readiness) and business owner (hypercare exit)",
          ["Every new or changed control operated at least once; evidence reviewed by Tech Risk",
           "Interim controls evidenced as operated for the whole window",
           "Interim controls retired, or extension approved with new end date",
           "Project and elevated access removed (removal evidence retained)",
           "Hypercare incidents closed"]),
    Stage(6, "Close", "#24292f", "Head of Tech Risk",
          ["Control readiness outcome recorded", "Retired controls confirmed",
           "Evidence archive reference recorded", "Business-as-usual (BAU) control owners confirmed"]),
]

STAGE_CHOICES = [(s.number, f"{s.number}. {s.name}") for s in STAGES]
DELIVERY_STAGES = STAGES[1:6]  # the ones an owner plans phases for

def color(number: int) -> str:
    return STAGES[number].color


# --- Oversight level and what each stage needs -----------------------------------------------------

DEFAULT_QUESTION_LABELS = [
    ("affects_tier01_app", "Tier 0 or 1 app affected"),
    ("affects_sox_app", "SOX in-scope app affected"),
    ("changes_key_controls", "New, retired or redesigned key controls"),
    ("migrates_regulated_data", "Financial or regulated data migrated"),
    ("changes_access_model", "Change to how access is provisioned or reviewed"),
    ("moves_to_vendor_or_cloud", "Moves to a vendor or cloud platform"),
]
QUESTION_KEYS = [key for key, _ in DEFAULT_QUESTION_LABELS]

SIGNOFF, REVIEW, OWNER = "signoff", "review", "owner"

REQUIREMENT_LABELS = {
    SIGNOFF: "Approver sign-off",
    REVIEW: "Tech Risk review",
    OWNER: "Owner confirms",
}
REQUIREMENT_TIPS = {
    SIGNOFF: "Tech Risk reviews the evidence, then the named approver signs off",
    REVIEW: "Tech Risk reviews the evidence",
    OWNER: "The owner confirms each item is done",
}

# Used until Tech Risk saves gate settings in the app. Keys are OversightLevel values;
# index = stage number (stage 0 is intake acceptance, the same for all).
DEFAULT_GATE_MATRIX = {
    "enhanced": [None, SIGNOFF, SIGNOFF, SIGNOFF, SIGNOFF, SIGNOFF, SIGNOFF],
    "standard": [None, SIGNOFF, REVIEW, REVIEW, SIGNOFF, REVIEW, SIGNOFF],
    "light": [None, REVIEW, OWNER, REVIEW, SIGNOFF, REVIEW, REVIEW],
}


def gate_settings():
    """The current GateSettings row, or None."""
    from tracker.models import GateSettings

    return GateSettings.objects.current().order_by("-valid_from").first()


def gate_matrix(settings=None) -> dict:
    """The gate requirement matrix in force."""
    settings = settings if settings is not None else gate_settings()
    return settings.matrix if settings else DEFAULT_GATE_MATRIX


def oversight_questions(settings=None) -> list:
    """[(key, label)] in fixed key order, with labels from settings when present."""
    settings = settings if settings is not None else gate_settings()
    saved = settings.question_labels if settings else {}
    return [(key, saved.get(key) or label) for key, label in DEFAULT_QUESTION_LABELS]


def gate_matrix_is_draft(settings=None) -> bool:
    """True until the gate settings record an approver."""
    settings = settings if settings is not None else gate_settings()
    return not (settings and settings.approved_by)


def gate_matrix_version(settings=None) -> str:
    """Version label of the gate settings in force."""
    settings = settings if settings is not None else gate_settings()
    return settings.version_label if settings else "Default"


def derive_oversight(answers: dict) -> str:
    """Enhanced: Tier 0/1, SOX plus one more yes, key controls plus regulated data, or three yes. Standard: any yes. Light: none."""
    yes = sum(1 for key in QUESTION_KEYS if answers.get(key))
    if (answers.get("affects_tier01_app")
            or (answers.get("affects_sox_app") and yes >= 2)
            or (answers.get("changes_key_controls") and answers.get("migrates_regulated_data"))
            or yes >= 3):
        return "enhanced"
    return "standard" if yes else "light"


def requirement(level: str, stage_number: int, matrix: dict | None = None) -> str | None:
    """What a stage exit needs at this oversight level; None for stage 0 (intake acceptance)."""
    return (matrix if matrix is not None else gate_matrix())[level][stage_number]
