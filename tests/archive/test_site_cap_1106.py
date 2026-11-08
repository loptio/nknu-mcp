"""SSO's activity detail pages and the site cap (11-06).

The shared module's activity detail pages (/services/academicactivities/actdetail.aspx?unid=…, and campusactivities')
are a platform's detail pages that no platform file describes: since 11-03 (P02) free of the variant cap, since 11-06
of every page cap, as a profile's detail pages are (design §7.4 detail_paths: 免上限, §9), the depth limit aside. On the
lab machine services had its 4,000 pages and 7,467 activity pages held cap:site, no other page of it
(【用户同意 Claude 的建议】: Claude's 11-04 fix 「让这两种活动页也不受整站上限；重开时，被挡下的会自动放回去抓」; the
owner 11-04 「待会再修」, 11-06 the button Claude wrote, 「先修三件再一起重开」). A run's start lets go of a row held by
any cap the crawler gives (crawler.CAP_REASONS) that the rules of today no longer hold, in the order the rows were
found; Google's own caps (cap:drive …, google_api's) stay as they are.

Everything runs on tests/archive/test_crawler_round1.py's fake network and fake clock; no address leaves this
machine."""

from __future__ import annotations

import pytest

from nknu.archive import crawler as mod
from nknu.archive import scope
from tests.archive.test_crawler_round1 import events, rows
from tests.archive.test_run_only import run
from tests.archive.test_sso_scope_1103 import ACT, CAMP, OTHER, sso_crawler

SERVICES = "sso.nknu.edu.tw/services"
# the school's broken relative links on activity pages (the lab's ledger 11-06: 8 such rows held cap:site)
JUNK = "https://sso.nknu.edu.tw/services/campusactivities/import/ascx/www.performer.org.tw"


@pytest.fixture(autouse=True)
def _workers_leave_when_idle(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_EXIT_S", -1.0)


def _held(f, url, reason, *, depth=2, kind="page", host="sso.nknu.edu.tw", owner=SERVICES):
    f.add(url, host=host, site=scope.site_key(url), unit=None, tier=1, kind=kind, depth=depth, found_via="link",
          found_from=None, state="held", reason=reason, owner_site=owner, prio=1, kind0=kind)
    return f.row(url)["id"]


@pytest.mark.parametrize("url", [ACT + "MjAwOTAwMDg0", CAMP + "MjAyMzAwMTIz",
                                 "https://sso.nknu.edu.tw/Services/CampusActivities/ActDetail.aspx?UNID=x"])
def test_ssos_activity_detail_pages_are_platform_detail_pages(url):
    assert scope.is_platform_detail(url) and scope.is_platform_path(url)


@pytest.mark.parametrize("url", ["https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&menuid=390", JUNK,
                                 OTHER + "1", "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=1", "http://[::1"])
def test_no_other_path_is_a_platform_detail_page(url):
    assert not scope.is_platform_detail(url)


def test_every_platform_detail_path_is_a_platform_path():
    assert scope.PLATFORM_DETAIL_PATHS <= scope.PLATFORM_PATHS  # no variant cap, no trap warning either


def test_activity_detail_pages_pass_the_site_cap_and_other_pages_still_do_not(tmp_path):
    crawler, _, _ = sso_crawler(tmp_path, site_cap=2)
    assert [crawler.enqueue(f"{OTHER}{i}", depth=1, found_via="link") for i in range(3)] == [
        "queued", "queued", "cap:site"]
    got = [crawler.enqueue(f"{ACT}a{i}", depth=2, found_via="link") for i in range(3)]
    got += [crawler.enqueue(f"{CAMP}c{i}", depth=2, found_via="link") for i in range(3)]
    assert set(got) == {"queued"}
    assert crawler.enqueue(JUNK, depth=2, found_via="link") == "cap:site"
    assert crawler.enqueue(ACT + "deep", depth=mod.MAX_DEPTH + 1, found_via="link") == "cap:depth"  # depth first


def test_activity_details_the_site_cap_held_are_fetched_at_the_next_run_and_other_held_rows_stay(tmp_path):
    seeder, _, _ = sso_crawler(tmp_path, site_cap=2)
    for i in range(2):
        seeder.enqueue(f"{OTHER}{i}", depth=1, found_via="link")  # the site is full
    f = seeder.frontier
    for url in (ACT + "h1", CAMP + "h2"):  # held by the crawler before 11-06
        _held(f, url, "cap:site")
    _held(f, JUNK, "cap:site")
    _held(f, ACT + "deep", "cap:depth", depth=mod.MAX_DEPTH + 1)
    f.close()
    crawler, net, _ = sso_crawler(tmp_path, site_cap=2)
    run(crawler)
    got = rows(crawler)
    for url in (ACT + "h1", CAMP + "h2"):
        assert got[url]["state"] == "done" and url in net.seen
    assert (got[JUNK]["state"], got[JUNK]["reason"]) == ("held", "cap:site") and JUNK not in net.seen
    assert (got[ACT + "deep"]["state"], got[ACT + "deep"]["reason"]) == ("held", "cap:depth")
    assert ACT + "deep" not in net.seen
    assert events(crawler, "caps_released") == ["2 rows held by a cap are free of it now (cap:site 2)"]


def test_a_row_the_site_cap_held_goes_only_while_its_site_has_room_the_first_found_first(tmp_path):
    seeder, _, _ = sso_crawler(tmp_path, site_cap=3)
    for i in range(2):
        seeder.enqueue(f"{OTHER}{i}", depth=1, found_via="link")
    f = seeder.frontier
    ids = [_held(f, f"{OTHER}h{i}", "cap:site") for i in range(3)]  # room for one of them
    f.close()
    crawler, _, _ = sso_crawler(tmp_path, site_cap=3)
    assert crawler._release_caps() == 1
    got = [crawler.frontier.row_by_id(i) for i in ids]
    assert [(r["state"], r["reason"]) for r in got] == [("pending", None), ("held", "cap:site"), ("held", "cap:site")]


def test_the_file_and_image_caps_are_judged_again_too(tmp_path):
    seeder, _, _ = sso_crawler(tmp_path, file_cap=1, image_cap=1)
    f = seeder.frontier
    pdf = _held(f, "https://sso.nknu.edu.tw/services/other/a.pdf", "cap:file", kind="file")
    jpg = _held(f, "https://sso.nknu.edu.tw/services/other/a.jpg", "cap:image", kind="image")
    f.close()
    crawler, _, _ = sso_crawler(tmp_path, file_cap=1, image_cap=1)
    assert crawler._release_caps() == 2  # nothing else counts for the site: room for one of each
    assert {crawler.frontier.row_by_id(i)["state"] for i in (pdf, jpg)} == {"pending"}


@pytest.mark.parametrize("reason", ["cap:drive", "cap:drive_depth", "cap:drive_list", "cap:calendar"])
def test_googles_own_caps_are_never_the_crawlers_to_let_go(tmp_path, reason):
    seeder, _, _ = sso_crawler(tmp_path)
    uid = _held(seeder.frontier, "https://www.googleapis.com/drive/v3/files/abc?alt=media", reason, kind="file",
                host="www.googleapis.com")
    seeder.frontier.close()
    crawler, _, _ = sso_crawler(tmp_path)
    assert crawler._release_caps() == 0
    r = crawler.frontier.row_by_id(uid)
    assert (r["state"], r["reason"]) == ("held", reason)


def test_a_run_given_only_lets_no_row_the_site_cap_held_go(tmp_path):
    """--only fetches the listed rows pending at its start (crawler._narrow_only, before _release_caps): a held row,
    listed or not, stays, whichever cap held it."""
    seeder, _, _ = sso_crawler(tmp_path, site_cap=1)
    seeder.enqueue(f"{OTHER}0", depth=1, found_via="link")
    f = seeder.frontier
    listed = _held(f, ACT + "h1", "cap:site")
    _held(f, ACT + "h2", "cap:site")
    f.close()
    crawler, net, _ = sso_crawler(tmp_path, site_cap=1, only=frozenset({listed}))
    run(crawler)
    got = rows(crawler)
    for url in (ACT + "h1", ACT + "h2"):
        assert (got[url]["state"], got[url]["reason"]) == ("held", "cap:site") and url not in net.seen
