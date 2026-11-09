import pytest

from core.auth.sso import SsoNotConfigured
from core.models import Person, Role

pytestmark = pytest.mark.django_db


def test_no_identity_is_forbidden(client, settings):
    settings.DEV_DEFAULT_EMP_ID = ""
    assert client.get("/").status_code == 403


def test_dev_default_emp_id_is_used(client, settings, grant):
    grant("D1", Role.TECH_RISK)
    settings.DEV_DEFAULT_EMP_ID = "D1"
    assert client.get("/").status_code == 200


def test_switch_sets_session_and_grants_access(client, settings, grant):
    settings.DEV_DEFAULT_EMP_ID = ""
    grant("E1", Role.TECH_RISK)
    resp = client.post("/auth/switch/", {"emp_id": "E1"})
    assert resp.status_code == 302
    assert resp["Location"] == "/"
    assert client.session["dev_emp_id"] == "E1"
    assert Person.objects.filter(pk="E1").exists()
    assert client.get("/").status_code == 200


def test_switch_with_empty_emp_id_does_not_sign_in(client, settings):
    settings.DEV_DEFAULT_EMP_ID = ""
    client.post("/auth/switch/", {"emp_id": "  "})
    assert "dev_emp_id" not in client.session
    assert client.get("/").status_code == 403


def test_switch_user_without_role_can_open_home(client, settings):
    settings.DEV_DEFAULT_EMP_ID = ""
    client.post("/auth/switch/", {"emp_id": "NOROLE"})
    assert client.get("/").status_code == 200


def test_switch_get_lists_people(client, settings):
    settings.DEV_DEFAULT_EMP_ID = ""
    Person.objects.create(emp_id="E9", display_name="Nine")
    resp = client.get("/auth/switch/")
    assert resp.status_code == 200
    assert b"Nine" in resp.content


def test_sso_without_header_config_raises(client, settings):
    settings.AUTH_MODE = "sso"
    if hasattr(settings, "SSO_EMP_ID_HEADER"):
        del settings.SSO_EMP_ID_HEADER
    with pytest.raises(SsoNotConfigured):
        client.get("/")


def test_sso_header_resolves_actor(client, settings, grant):
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    grant("E1", Role.TECH_RISK)
    assert client.get("/", HTTP_X_EMP_ID="E1").status_code == 200


def test_sso_missing_header_value_is_forbidden(client, settings):
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    assert client.get("/").status_code == 403


def test_sso_ignores_dev_session_and_default(client, settings, grant):
    grant("E1", Role.TECH_RISK)
    client.post("/auth/switch/", {"emp_id": "E1"})
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    settings.DEV_DEFAULT_EMP_ID = "E1"
    assert client.get("/").status_code == 403


def test_switch_is_404_in_sso_mode(client, settings):
    settings.AUTH_MODE = "sso"
    settings.SSO_EMP_ID_HEADER = "X-Emp-Id"
    assert client.get("/auth/switch/").status_code == 404
    assert client.post("/auth/switch/", {"emp_id": "E1"}).status_code == 404
