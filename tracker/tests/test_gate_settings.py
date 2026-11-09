from datetime import date

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from scripts.generate_fake_data import seed as generate_fake_data

from tracker import lifecycle as rules
from tracker.models import GateSettings

pytestmark = pytest.mark.django_db

TR, OWNER = "E00001", "E00101"
KEYS = rules.QUESTION_KEYS


def answers(*yes):
    return {key: key in yes for key in KEYS}


# --- derivation --------------------------------------------------------------------------


@pytest.mark.parametrize("yes, level", [
    ((), "light"),
    (("changes_access_model",), "standard"),
    (("affects_sox_app",), "standard"),
    (("changes_key_controls",), "standard"),
    (("changes_access_model", "moves_to_vendor_or_cloud"), "standard"),
    (("changes_key_controls", "changes_access_model"), "standard"),
    (("affects_tier01_app",), "enhanced"),
    (("affects_sox_app", "moves_to_vendor_or_cloud"), "enhanced"),
    (("changes_key_controls", "migrates_regulated_data"), "enhanced"),
    (("changes_access_model", "moves_to_vendor_or_cloud", "migrates_regulated_data"), "enhanced"),
])
def test_derive_oversight(yes, level):
    assert rules.derive_oversight(answers(*yes)) == level


# --- settings and accessors --------------------------------------------------------------


def test_defaults_without_settings():
    assert rules.gate_settings() is None
    assert rules.gate_matrix() == rules.DEFAULT_GATE_MATRIX
    assert rules.oversight_questions() == rules.DEFAULT_QUESTION_LABELS
    assert rules.gate_matrix_is_draft() is True and rules.gate_matrix_version() == "Default"
    assert rules.requirement("light", 6) == rules.REVIEW


def test_settings_are_versioned():
    matrix = {**rules.DEFAULT_GATE_MATRIX, "light": [None] + [rules.OWNER] * 6}
    first = GateSettings.create(TR, matrix=matrix, question_labels={"affects_sox_app": "SOX app"}, version_label="v2.0")
    assert rules.requirement("light", 6) == rules.OWNER and rules.gate_matrix_is_draft()
    assert rules.oversight_questions()[1] == ("affects_sox_app", "SOX app")
    assert [k for k, _ in rules.oversight_questions()] == KEYS

    second = first.new_version(TR, version_label="v2.1", approved_by="Head of Tech Risk", approved_on=date(2026, 11, 9))
    assert GateSettings.objects.current().count() == 1 and rules.gate_settings() == second
    assert rules.gate_matrix_version() == "v2.1" and not rules.gate_matrix_is_draft()
    assert GateSettings.objects.history(first.entity_id).count() == 2
    with pytest.raises(ValueError):
        first.save()


# --- admin form --------------------------------------------------------------------------


@pytest.fixture
def client_as(client):
    generate_fake_data(stdout=lambda *a: None)

    def _as(emp_id):
        client.post("/auth/switch/", {"emp_id": emp_id})
        return client

    return _as


def form_data(**overrides):
    data = {f"m_{level}_{s}": rules.DEFAULT_GATE_MATRIX[level][s] for level in rules.DEFAULT_GATE_MATRIX for s in range(1, 7)}
    data.update({f"q_{k}": label for k, label in rules.DEFAULT_QUESTION_LABELS})
    data.update({"version_label": "v2.0", "change_note": "Head of Tech Risk review", "approved_by": "", "approved_on": ""})
    data.update(overrides)
    return data


def test_admin_card_and_permissions(client_as):
    assert client_as(OWNER).post(reverse("set_gate_settings"), form_data()).status_code == 403
    html = client_as(TR).get(reverse("workbench_admin")).content.decode()
    assert "Gate requirements" in html and "Current: Default" in html and 'name="m_light_6"' in html
    assert 'name="q_affects_tier01_app" value="Tier 0 or 1 app affected"' in html


def test_admin_save_changes_requirement(client_as):
    c = client_as(TR)
    r = c.post(reverse("set_gate_settings"), form_data(m_light_6=rules.OWNER, q_affects_sox_app="SOX app affected (in scope)"))
    assert r.status_code == 302 and r["Location"] == reverse("workbench_admin")
    assert "Gate requirements saved as v2.0." in [str(m) for m in get_messages(r.wsgi_request)]
    assert rules.requirement("light", 6) == rules.OWNER and rules.gate_matrix_is_draft()
    assert dict(rules.oversight_questions())["affects_sox_app"] == "SOX app affected (in scope)"

    c.post(reverse("set_gate_settings"), form_data(version_label="v2.1", approved_by="Head of Tech Risk",
                                                    approved_on="2026-11-09"))
    assert GateSettings.objects.count() == 2 and rules.requirement("light", 6) == rules.REVIEW
    html = c.get(reverse("workbench_admin")).content.decode()
    assert "Current: v2.1" in html and "Approved by Head of Tech Risk on Nov 9, 2026" in html


@pytest.mark.parametrize("overrides", [
    {"approved_by": "Head of Tech Risk"},
    {"approved_on": "2026-11-09"},
    {"version_label": ""},
    {"change_note": ""},
    {"m_light_2": "bogus"},
])
def test_admin_save_rejects_invalid(client_as, overrides):
    r = client_as(TR).post(reverse("set_gate_settings"), form_data(**overrides))
    assert r.status_code == 302 and not GateSettings.objects.exists()
    assert any("not saved" in str(m) for m in get_messages(r.wsgi_request))
