import pytest
from django.urls import reverse
from scripts.generate_fake_data import seed as generate_fake_data

from tracker.lifecycle import oversight_questions
from tracker.models import Delegation, Initiative, Milestone, Phase, RiskItem

pytestmark = pytest.mark.django_db

OWNER, TR, NEW = "E00101", "E00001", "E00999"

INTAKE = {
    "name": "Vault Rollout", "sponsor": "CISO", "description": "Secrets vault", "owner_emp_id": NEW,
    "delegates": "E00555, E00556", "business_driver": "Audit finding", "target_go_live": "2027-06-30",
    "expected_systems": "CI, prod", "why_it_matters": "Privileged credentials",
    "milestones": "Design sign-off | 2027-02-26 | key\nPilot | 2027-04-01\nTraining 2027-05-01 (key)",
    "risks": "Vendor slips | H\nStaffing | low\nNo rating",
    "ph0_name": "Design", "ph0_start": "2027-01-04", "ph0_end": "2027-02-26",
    "ph1_name": "Build", "ph1_start": "2027-03-01", "ph1_end": "2027-04-30",
    "ph2_name": "", "ph2_start": "", "ph2_end": "", "ph3_name": "Cutover", "ph3_start": "", "ph3_end": "",
}


@pytest.fixture(autouse=True)
def seeded():
    generate_fake_data(stdout=lambda *a: None)


def switch(client, emp_id):
    client.post("/auth/switch/", {"emp_id": emp_id})


def vault():
    return Initiative.objects.current().get(name="Vault Rollout")


def test_intake_by_new_user_creates_proposed_initiative(client):
    switch(client, NEW)
    assert client.get(reverse("intake")).status_code == 200
    resp = client.post(reverse("intake"), INTAKE)
    assert resp.status_code == 302 and resp["Location"] == reverse("my_initiative")
    init = vault()
    assert init.status == "proposed" and init.phase == "Intake" and init.submitted_at
    assert init.owner_emp_id == NEW
    phases = list(Phase.objects.current().filter(initiative_entity=init.entity_id).order_by("order"))
    assert [(p.name, p.stage) for p in phases] == [("Design", 1), ("Build", 2)]
    ms = {m.name: m for m in Milestone.objects.current().filter(initiative_entity=init.entity_id)}
    assert ms["Design sign-off"].is_key and str(ms["Design sign-off"].planned) == "2027-02-26"
    assert not ms["Pilot"].is_key and ms["Training"].is_key
    ratings = {r.title: r.rating for r in RiskItem.objects.current().filter(initiative_entity=init.entity_id)}
    assert ratings == {"Vendor slips": "H", "Staffing": "L", "No rating": "M"}
    assert set(Delegation.objects.current().filter(initiative_entity=init.entity_id).values_list(
        "delegate_emp_id", flat=True)) == {"E00555", "E00556"}
    # The submitter is now an owner and can open it.
    assert client.get(reverse("my_initiative"))["Location"] == reverse("initiative", args=[init.entity_id])


def test_intake_form_shows_oversight_questions(client):
    switch(client, NEW)
    body = client.get(reverse("intake")).content.decode()
    assert "About this initiative" in body
    assert "Select all that apply. Tech Risk sets the oversight level from your answers." in body
    for key, label in oversight_questions():
        assert f'name="{key}"' in body and label in body


def test_intake_without_ticks_saves_light_oversight(client):
    switch(client, NEW)
    client.post(reverse("intake"), INTAKE)
    init = vault()
    assert not any(init.oversight_answers.values())
    assert init.oversight == "light"


def test_intake_saves_oversight_answers(client):
    switch(client, NEW)
    ticked = {"affects_sox_app": "on", "changes_key_controls": "on"}
    client.post(reverse("intake"), dict(INTAKE, **ticked))
    init = vault()
    assert {k for k, v in init.oversight_answers.items() if v} == set(ticked)
    assert init.derived_oversight == "enhanced"


def test_intake_requires_why_and_tech_risk_redirects_to_workbench(client):
    switch(client, NEW)
    bad = dict(INTAKE, why_it_matters="")
    assert client.post(reverse("intake"), bad).status_code == 200
    assert not Initiative.objects.current().filter(name="Vault Rollout").exists()
    switch(client, TR)
    resp = client.post(reverse("intake"), dict(INTAKE, owner_emp_id=TR))
    assert resp["Location"] == reverse("workbench")


def test_accept_and_return(client):
    switch(client, NEW)
    client.post(reverse("intake"), INTAKE)
    init = vault()
    switch(client, OWNER)
    assert client.post(reverse("intake_accept", args=[init.entity_id])).status_code in (403, 404)
    switch(client, TR)
    assert client.post(reverse("intake_return", args=[init.entity_id]), {"comments": " "}).status_code == 302
    assert Initiative.objects.get_current(init.entity_id).intake_comments == ""
    client.post(reverse("intake_return", args=[init.entity_id]), {"comments": "Add control owners."})
    cur = Initiative.objects.get_current(init.entity_id)
    assert cur.status == "proposed" and cur.intake_comments == "Add control owners."
    resp = client.post(reverse("intake_accept", args=[init.entity_id]))
    assert resp["Location"] == reverse("workbench")
    cur = Initiative.objects.get_current(init.entity_id)
    assert cur.status == "active" and cur.accepted_at
    assert cur.baseline_go_live == cur.target_go_live and str(cur.baseline_go_live) == "2027-06-30"
    assert cur.phase == "Design"
