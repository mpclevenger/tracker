import pytest

from core.models import Person, Role, RoleAssignment


@pytest.mark.django_db
def test_open_mode_everyone_is_tech_risk(client, settings):
    settings.AUTH_MODE = "open"
    settings.OPEN_MODE_EMP_ID = "tech-risk"
    r = client.get("/workbench/")
    assert r.status_code == 200
    assert b"Open mode" in r.content


@pytest.mark.django_db
def test_open_mode_hides_user_switcher(client, settings):
    settings.AUTH_MODE = "open"
    assert client.get("/auth/switch/").status_code == 404
    assert b"switch user" not in client.get("/").content


@pytest.mark.django_db
def test_sso_roles_hook_merges_group_roles(client, settings):
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    settings.SSO_GROUPS_HEADER = "X-Groups"
    settings.SSO_GROUP_ROLE_MAP = {"APP-TechRisk": Role.TECH_RISK, "APP-Auditor": Role.AUDITOR}
    Person.objects.create(emp_id="E9", display_name="Nine")
    r = client.get("/workbench/", HTTP_X_EMP_ID="E9", HTTP_X_GROUPS="APP-Auditor,APP-TechRisk")
    assert r.status_code == 200
    r = client.get("/workbench/", HTTP_X_EMP_ID="E9", HTTP_X_GROUPS="APP-Auditor")
    assert r.status_code == 403


@pytest.mark.django_db
def test_sso_without_groups_falls_back_to_role_table(client, settings):
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    settings.SSO_GROUPS_HEADER = ""
    settings.SSO_GROUP_ROLE_MAP = {}
    RoleAssignment.create("SYSTEM", emp_id="E8", role=Role.TECH_RISK)
    assert client.get("/workbench/", HTTP_X_EMP_ID="E8").status_code == 200
    assert client.get("/workbench/", HTTP_X_EMP_ID="E7").status_code == 403
