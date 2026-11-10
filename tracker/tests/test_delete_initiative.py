import pytest
from django.urls import reverse

from meetings.models import FollowUp, Meeting, MeetingInitiative
from scripts.generate_fake_data import seed as generate_fake_data
from tracker.models import CurationEvent, Initiative, Snapshot
from tracker.services import snapshots

pytestmark = pytest.mark.django_db

TR, OWNER, CTO, AUD = "E00001", "E00101", "E00201", "E00301"


@pytest.fixture
def seeded(db):
    generate_fake_data(stdout=lambda *a: None)
    return Initiative.objects.current().get(name="SailPoint Onboarding")


@pytest.fixture
def as_user(client):
    def _as(emp_id):
        client.post("/auth/switch/", {"emp_id": emp_id})
        return client

    return _as


def delete(client, init, reason="Test entry", name=None):
    return client.post(
        reverse("delete_initiative", args=[init.entity_id]),
        {"reason": reason, "confirm_name": init.name if name is None else name},
    )


def still_current(init):
    return Initiative.objects.current().filter(entity_id=init.entity_id).exists()


def test_delete_happy_path(seeded, as_user):
    c = as_user(TR)
    r = delete(c, seeded, name=f"  {seeded.name.upper()} ")
    assert r.status_code == 302 and r.url == reverse("workbench")
    assert [str(m) for m in r.wsgi_request._messages] == [f'Deleted "{seeded.name}".']
    assert not still_current(seeded)
    assert Initiative.objects.filter(entity_id=seeded.entity_id).count() > 1
    assert CurationEvent.objects.filter(initiative_entity=seeded.entity_id, what__contains="Test entry").exists()


def test_reason_and_name_required(seeded, as_user):
    c = as_user(TR)
    for kwargs in ({"reason": "  "}, {"name": "wrong"}, {"name": "  "}, {"reason": "x" * 181}):
        r = delete(c, seeded, **kwargs)
        assert r.status_code == 302 and r.url == reverse("initiative", args=[seeded.entity_id])
        assert still_current(seeded)


def test_forbidden_and_get_not_allowed(seeded, as_user):
    for emp in (OWNER, CTO, AUD):
        assert delete(as_user(emp), seeded).status_code == 403
        assert still_current(seeded)
    assert as_user(TR).get(reverse("delete_initiative", args=[seeded.entity_id])).status_code == 405


def test_open_followups_retired_and_meeting_kept(seeded, as_user):
    m = Meeting.objects.current().first()
    MeetingInitiative.create(TR, meeting_entity=m.entity_id, initiative_entity=seeded.entity_id)
    open_fu = FollowUp.create(TR, meeting_entity=m.entity_id, initiative_entity=seeded.entity_id, text="Open one")
    done_fu = FollowUp.create(
        TR, meeting_entity=m.entity_id, initiative_entity=seeded.entity_id, text="Done one", status=FollowUp.Status.DONE
    )
    delete(as_user(TR), seeded)
    assert not FollowUp.objects.current().filter(entity_id=open_fu.entity_id).exists()
    assert FollowUp.objects.current().filter(entity_id=done_fu.entity_id).exists()
    assert Meeting.objects.current().filter(entity_id=m.entity_id).exists()


def test_page_404_and_gone_from_summary_and_triage(seeded, as_user):
    delete(as_user(TR), seeded)
    for emp in (TR, OWNER, CTO, AUD):
        assert as_user(emp).get(reverse("initiative", args=[seeded.entity_id])).status_code == 404
    as_user(TR).get(reverse("workbench"))  # consume the flash message
    for emp, url in ((TR, "workbench"), (TR, "workbench_snapshots"), (CTO, "exec_summary"), (AUD, "exec_summary")):
        r = as_user(emp).get(reverse(url))
        assert r.status_code == 200 and seeded.name not in r.content.decode()


def test_history_shows_delete_line(seeded, as_user):
    delete(as_user(TR), seeded, reason="Mistaken entry")
    url = reverse("initiative_history", args=[seeded.entity_id])
    for emp in (TR, AUD):
        r = as_user(emp).get(url)
        body = r.content.decode()
        assert r.status_code == 200
        assert seeded.name in body and "Deleted" in body and "Mistaken entry" in body
    assert as_user(CTO).get(url).status_code == 403


def test_history_404_for_never_existing(seeded, as_user):
    r = as_user(TR).get(reverse("initiative_history", args=["00000000-0000-0000-0000-000000000000"]))
    assert r.status_code == 404


def test_earlier_snapshot_keeps_initiative(seeded, as_user):
    snap = snapshots.take_snapshot(TR, "Before", "")
    delete(as_user(TR), seeded)
    assert seeded.name in [r.frozen_json["name"] for r in Snapshot.objects.get(pk=snap.pk).rows.all()]
    for emp in (TR, AUD):
        r = as_user(emp).get(reverse("snapshot_record", args=[snap.pk]))
        assert r.status_code == 200 and seeded.name in r.content.decode()


def test_pages_render_with_deleted_initiative_on_meeting(seeded, as_user):
    m = Meeting.objects.current().first()
    MeetingInitiative.create(TR, meeting_entity=m.entity_id, initiative_entity=seeded.entity_id)
    FollowUp.create(
        TR, meeting_entity=m.entity_id, initiative_entity=seeded.entity_id, text="Closed", status=FollowUp.Status.DONE
    )
    delete(as_user(TR), seeded)
    urls = [reverse("meetings:list"), f"{reverse('meetings:list')}?initiative={seeded.entity_id}&open=1",
            reverse("meetings:edit", args=[m.entity_id]), reverse("workbench"), reverse("exec_summary"),
            reverse("workbench_admin"), reverse("audit")]
    for emp in (TR, AUD):
        for url in urls:
            assert as_user(emp).get(url).status_code in (200, 403), (emp, url)
    assert as_user(TR).get(urls[0]).status_code == 200
