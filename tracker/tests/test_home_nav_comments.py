import pytest
from django.urls import reverse

from core.models import Person, Role
from tracker.models import Comment, Initiative
from tracker.services import comments as comments_svc, triage

pytestmark = pytest.mark.django_db

TR, OWN, AUD, CTO, OTHER, PROP = "TR1", "OWN1", "AUD1", "CTO1", "OTH1", "PROP1"


@pytest.fixture
def world(make_initiative, grant):
    for emp, name in [(TR, "Tessa Risk"), (OWN, "Olive Owner"), (AUD, "Audie"), (CTO, "Cto"), (OTHER, "Ollie Other"), (PROP, "Pat Proposer")]:
        Person.objects.create(emp_id=emp, display_name=name)
    grant(TR, Role.TECH_RISK)
    grant(AUD, Role.AUDITOR)
    grant(CTO, Role.SENIOR_MANAGEMENT)
    active = make_initiative(name="Alpha Platform", owner=OWN)
    other = make_initiative(name="Beta Billing", owner="ZZZ", sox_applicable=True)
    proposed = Initiative.create(PROP, name="Gamma Idea", owner_emp_id=PROP, status="proposed", submitted_at=None)
    return {"active": active, "other": other, "proposed": proposed}


def get(client, emp, url, **kw):
    client.post("/auth/switch/", {"emp_id": emp})
    return client.get(url, **kw)


def post(client, emp, url, data=None):
    client.post("/auth/switch/", {"emp_id": emp})
    return client.post(url, data or {})


# --- home ---


def test_owner_sees_my_initiatives_not_all(client, world):
    html = get(client, OWN, "/").content.decode()
    assert "<h1>Initiatives</h1>" in html and "My initiatives" in html
    assert "Alpha Platform" in html and "Beta Billing" not in html
    assert "All initiatives" not in html
    assert "Propose an initiative" in html and "Post a check-in" in html
    assert reverse("check_in", args=[world["active"].entity_id]) in html


def test_tech_risk_sees_all_search_and_waiting(client, world):
    html = get(client, TR, "/").content.decode()
    assert "All initiatives" in html and "Alpha Platform" in html and "Beta Billing" in html
    assert "Waiting for Tech Risk" in html and "Gamma Idea" in html
    assert reverse("intake_accept", args=[world["proposed"].entity_id]) in html
    assert ">New initiative<" in html and "Add watch item" in html and "Log a meeting" in html
    filtered = get(client, TR, "/", data={"q": "beta"}).content.decode()
    assert ">Beta Billing</a>" in filtered and ">Alpha Platform</a>" not in filtered


def test_sox_scope_applies_to_all_table(client, world):
    html = get(client, TR, "/", data={"scope": "sox"}).content.decode()
    assert ">Beta Billing</a>" in html and ">Alpha Platform</a>" not in html


def test_proposer_sees_own_waiting_item(client, world):
    html = get(client, PROP, "/").content.decode()
    assert "Waiting for Tech Risk" in html and "Gamma Idea" in html
    assert "Tech Risk will accept it or send it back." in html
    assert "intake/" not in html.replace(reverse("intake"), "")
    other = get(client, OWN, "/").content.decode()
    assert "Gamma Idea" not in other


def test_no_role_page_has_propose_button(client, world):
    html = get(client, "NOBODY", "/").content.decode()
    assert "No role is assigned" in html and "Propose an initiative" in html
    assert "All initiatives" not in html


def test_home_toggles_follow_modules(client, world):
    from core.models import ModuleSettings

    row = ModuleSettings.objects.current().first()
    kwargs = {"meetings": False, "watch_list": False}
    if row:
        row.new_version(TR, **kwargs)
    else:
        ModuleSettings.create(TR, owner_access=True, audit=True, share_links=True, **kwargs)
    html = get(client, TR, "/").content.decode()
    assert "Log a meeting" not in html and "Add watch item" not in html


# --- nav ---


def test_nav_top_level_and_more_menu(client, world):
    tr = get(client, TR, "/").content.decode()
    nav = tr.split("</nav>")[0]
    assert ">Home<" in nav and ">Exec summary<" in nav and ">Workbench<" in nav and "More ▾" in nav
    more = nav.split('class="nav-more-panel"')[1]
    for label in ("Watch list", "Meetings", "Audit", "Lifecycle &amp; guidance"):
        assert label in more
    assert "My initiative" not in nav and "New initiative" not in nav
    own_nav = get(client, OWN, "/").content.decode().split("</nav>")[0]
    assert "Exec summary" not in own_nav and "Workbench" not in own_nav
    assert "Watch list" not in own_nav and "Lifecycle &amp; guidance" in own_nav


def test_nav_highlights_active_item(client, world):
    assert 'class="nav-link active" href="/"' in get(client, TR, "/").content.decode()
    assert 'class="nav-link active" href="/workbench/"' in get(client, TR, "/workbench/").content.decode()
    lifecycle = get(client, TR, reverse("lifecycle")).content.decode()
    assert "More ▾" in lifecycle and 'class="active" href="/lifecycle/"' in lifecycle


# --- edit buttons and next ---


def tab_html(client, emp, init, tab):
    return get(client, emp, reverse("initiative", args=[init.entity_id]), data={"tab": tab}).content.decode()


def test_edit_buttons_only_for_editors(client, world):
    init = world["active"]
    for tab, names in {"overview": ["edit_initiative"], "plan": ["edit_phases", "edit_milestones", "edit_workstreams"],
                       "risks": ["edit_risks", "edit_controls"]}.items():
        owner = tab_html(client, OWN, init, tab)
        for n in names:
            assert reverse(n, args=[init.entity_id]) + "?next=" in owner
        viewer = tab_html(client, CTO, init, tab)
        for n in names:
            assert reverse(n, args=[init.entity_id]) not in viewer


def test_empty_sections_offer_add(client, world):
    html = tab_html(client, OWN, world["active"], "risks")
    assert "No risks yet. <a" in html and "No controls yet. <a" in html
    assert "No risks yet" not in tab_html(client, CTO, world["active"], "risks")


def test_edit_link_carries_tab_and_save_returns_there(client, world):
    init = world["active"]
    url = reverse("edit_initiative", args=[init.entity_id])
    nxt = f"{reverse('initiative', args=[init.entity_id])}?tab=overview"
    html = get(client, OWN, url, data={"next": nxt}).content.decode()
    assert f'name="next" value="{nxt}"' in html and f'href="{nxt}">Cancel' in html
    resp = post(client, OWN, url, {"name": "Alpha Renamed", "next": nxt})
    assert resp.status_code == 302 and resp["Location"] == nxt


def test_rows_editor_save_returns_to_tab(client, world):
    init = world["active"]
    nxt = f"{reverse('initiative', args=[init.entity_id])}?tab=risks"
    data = {"rows-TOTAL_FORMS": "1", "rows-INITIAL_FORMS": "0", "rows-MIN_NUM_FORMS": "0", "rows-MAX_NUM_FORMS": "1000",
            "rows-0-type": "risk", "rows-0-title": "New risk", "rows-0-rating": "H", "next": nxt}
    resp = post(client, OWN, reverse("edit_risks", args=[init.entity_id]), data)
    assert resp.status_code == 302 and resp["Location"] == nxt


def test_unsafe_next_ignored(client, world):
    init = world["active"]
    url = reverse("edit_initiative", args=[init.entity_id])
    for bad in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)"):
        resp = post(client, OWN, url, {"name": "Alpha Platform", "next": bad})
        assert resp["Location"] == reverse("initiative", args=[init.entity_id])
        assert bad not in get(client, OWN, url, data={"next": bad}).content.decode()


def test_no_next_falls_back(client, world):
    init = world["active"]
    resp = post(client, OWN, reverse("edit_initiative", args=[init.entity_id]), {"name": "Alpha Platform"})
    assert resp["Location"] == reverse("initiative", args=[init.entity_id])


# --- comments ---


def comment_url(init):
    return reverse("comment_initiative", args=[init.entity_id])


def test_anyone_with_page_access_can_post(client, world):
    init = world["active"]
    for emp in (OWN, CTO, AUD, TR):
        resp = post(client, emp, comment_url(init), {"text": f"From {emp}"})
        assert resp.status_code == 302 and resp["Location"].endswith("#comments")
    assert Comment.objects.current().filter(target_entity=init.entity_id, status="pending").count() == 4
    page = get(client, OWN, resp["Location"].split("#")[0], follow=True).content.decode()
    assert "Thanks. Tech Risk will review your comment before it&#x27;s shown." in page or "before it's shown" in page


def test_no_access_cannot_post(client, world):
    assert post(client, OTHER, comment_url(world["active"]), {"text": "hi"}).status_code == 404
    assert not Comment.objects.exists()


def test_pending_visible_to_author_and_tech_risk_only(client, world):
    init = world["active"]
    post(client, CTO, comment_url(init), {"text": "Secret pending words"})
    page = lambda emp: tab_html(client, emp, init, "overview")
    assert "Secret pending words" in page(CTO) and "Waiting for review" in page(CTO)
    assert "Secret pending words" in page(TR)
    assert "Secret pending words" not in page(OWN) and "Secret pending words" not in page(AUD)
    assert "Publish" not in page(CTO)


def test_tech_risk_publishes_and_dismisses(client, world):
    init = world["active"]
    post(client, CTO, comment_url(init), {"text": "Good one"})
    post(client, CTO, comment_url(init), {"text": "Bad one"})
    good, bad = sorted(Comment.objects.current(), key=lambda c: c.text)[::-1]
    resp = post(client, TR, reverse("review_comment", args=[good.entity_id, "publish"]))
    assert resp.status_code == 302 and resp["Location"].endswith("#comments")
    post(client, TR, reverse("review_comment", args=[bad.entity_id, "dismiss"]))
    cur = {c.text: c for c in Comment.objects.current()}
    assert cur["Good one"].status == "published" and cur["Good one"].reviewed_by == TR and cur["Good one"].reviewed_at
    assert cur["Bad one"].status == "dismissed"
    assert Comment.objects.filter(entity_id=good.entity_id).count() == 2
    owner_page = tab_html(client, OWN, init, "overview")
    assert "Good one" in owner_page and "Bad one" not in owner_page and "Cto" in owner_page
    assert "Bad one" in tab_html(client, TR, init, "overview")
    assert "Bad one" not in tab_html(client, CTO, init, "overview")


def test_published_newest_first(client, world):
    init = world["active"]
    for t in ("First note", "Second note"):
        c = comments_svc.post(type("A", (), {"emp_id": CTO})(), init.entity_id, t)
        comments_svc.review(c, TR, "published")
    html = tab_html(client, OWN, init, "overview")
    assert html.index("Second note") < html.index("First note")


def test_non_tech_risk_cannot_review(client, world):
    c = comments_svc.post(type("A", (), {"emp_id": OWN})(), world["active"].entity_id, "Please")
    for emp in (OWN, CTO, AUD):
        assert post(client, emp, reverse("review_comment", args=[c.entity_id, "publish"])).status_code == 403
    assert Comment.objects.get_current(c.entity_id).status == "pending"


def test_length_limit_and_empty(client, world):
    init = world["active"]
    post(client, OWN, comment_url(init), {"text": "x" * 1001})
    post(client, OWN, comment_url(init), {"text": "   "})
    assert not Comment.objects.exists()
    post(client, OWN, comment_url(init), {"text": "x" * 1000})
    assert Comment.objects.count() == 1


def test_triage_comment_group(client, world):
    init = world["active"]
    post(client, OWN, comment_url(init), {"text": "y" * 100})
    rows, _ = triage.triage()
    assert triage.GROUP_LABELS["Comments"] == "Comments to review"
    [row] = rows["Comments"]
    assert row.what == "Alpha Platform: " + "y" * 60 and row.action == "review" and row.anchor == "#comments"
    html = get(client, TR, "/workbench/").content.decode()
    assert "Comments to review" in html and f'{reverse("initiative", args=[init.entity_id])}?tab=overview#comments' in html
    c = Comment.objects.current().get()
    post(client, TR, reverse("review_comment", args=[c.entity_id, "publish"]))
    assert "Comments" not in triage.triage()[0] or not triage.triage()[0]["Comments"]


def test_comments_not_on_exec_summary(client, world):
    c = comments_svc.post(type("A", (), {"emp_id": OWN})(), world["active"].entity_id, "Hidden from execs")
    comments_svc.review(c, TR, "published")
    assert "Hidden from execs" not in get(client, TR, "/exec/").content.decode()


# --- watch item comments ---


def test_watch_item_comments(client, world):
    from tracker.services import watch

    item = watch.create_watch_item(TR, name="Vendor renewal", owner_emp_id=OWN)
    url = reverse("comment_watch", args=[item.entity_id])
    page_url = reverse("watch_item", args=[item.entity_id])
    assert post(client, OWN, url, {"text": "nope"}).status_code == 403
    resp = post(client, CTO, url, {"text": "Watch remark"})
    assert resp["Location"] == page_url + "#comments"
    assert "Watch remark" in get(client, CTO, page_url).content.decode()
    assert "Watch remark" not in get(client, AUD, page_url).content.decode()
    rows, _ = triage.triage()
    [row] = rows["Comments"]
    assert row.watch and row.what == "Vendor renewal: Watch remark"
    assert f"{page_url}#comments" in get(client, TR, "/workbench/").content.decode()
    c = Comment.objects.current().get()
    post(client, TR, reverse("review_comment", args=[c.entity_id, "publish"]))
    assert "Watch remark" in get(client, AUD, page_url).content.decode()
