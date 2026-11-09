"""Optional module switches. Data stays in the database while a module is off."""

MODULES = [
    ("owner_access", "Owner access",
     "Owners sign in to see their initiatives, post check-ins, submit evidence and use the intake form. "
     "Off: Tech Risk enters everything."),
    ("meetings", "Meetings", "Meetings log, follow-ups and their Workbench reminders."),
    ("audit", "Audit access",
     "Auditor role and the Audit page. Off: auditors have no access; Tech Risk keeps the history pages."),
    ("share_links", "Read-only links", "Shareable read-only link to the exec summary."),
]
KEYS = [key for key, _label, _desc in MODULES]
LABELS = {key: label for key, label, _desc in MODULES}


def current():
    """The current ModuleSettings row, or None."""
    from core.models import ModuleSettings

    return ModuleSettings.objects.current().order_by("-valid_from").first()


def states(row=None) -> dict:
    """{key: bool} for every module; all True when there is no row."""
    row = row or current()
    return {key: True if row is None else getattr(row, key) for key in KEYS}


def enabled(key) -> bool:
    """True when the module is on (or no settings row exists)."""
    return states()[key]


def off_labels(modules) -> list:
    """Labels of the modules that are switched off in a {key: bool} dict."""
    return [LABELS[key] for key in KEYS if not modules.get(key, True)]
