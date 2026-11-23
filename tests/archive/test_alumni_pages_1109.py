"""成教所's 所友名錄 pages and the page caps (11-09).

c.nknu.edu.tw/tg/student_detail.aspx?aid=N holds one alumnus: the detail page of the c.nknu template's 所友名錄 list
(student.aspx). Round one fetched 128 of them. Walking the list past page 6 (gap ⑤-1, the c.nknu 「下一頁」 window) brings
about 1,850 more, together with about 1,780 of the site's notices, which would put the site past its 4,000 pages
(crawler.SITE_CAP) and the path close to crawler.VARIANT_CAP. The owner 11-09, asked whether to lift the cap for them:
「放开，一起抓」【用户】. They are platform detail pages now (scope.PLATFORM_DETAIL_PATHS), as SSO's activity pages are
since 11-06: free of every page cap but the depth limit, and still counted for their site. They are not CNKNU's
detail_paths, which the content stage reads as notices (pagetext.page_kind): it reads them as pages, as before
【Claude 的执行办法】.

Everything runs on tests/archive/test_crawler_round1.py's fake network and fake clock; no address leaves this
machine."""

from __future__ import annotations

import pytest

from nknu.archive import crawler as mod
from nknu.archive import profiles, scope
from tests.archive.plans import OpenPlan
from tests.archive.test_crawler_round1 import crawl, page
from tests.archive.test_run_only import _known

TG = "https://c.nknu.edu.tw/tg/"
ALUMNI = TG + "student_detail.aspx?aid="


@pytest.fixture(autouse=True)
def _workers_leave_when_idle(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_EXIT_S", -1.0)


def tg_crawler(tmp_path, **kw):
    crawler, net, clock = crawl(tmp_path, {"/tg/": page("<p>成教所</p>")}, plan=OpenPlan(), **kw)
    _known(crawler.frontier, "c.nknu.edu.tw")
    return crawler, net, clock


@pytest.mark.parametrize("url", [ALUMNI + "1848", TG + "Student_Detail.aspx?aid=2662"])
def test_the_alumni_pages_are_platform_detail_pages_and_still_plain_pages_to_the_content_stage(url):
    assert scope.is_platform_detail(url) and scope.is_platform_path(url)
    assert profiles.page_kind(url) is None  # no detail of CNKNU's: pagetext.page_kind's detail:cnknu is a notice


@pytest.mark.parametrize("url", [TG + "student.aspx?pn=71&pclass=0007", TG + "experience_view.aspx?id=1",
                                 "https://c.nknu.edu.tw/aca/student_detail.aspx?aid=1",
                                 TG + "wccafpsfs/album/activity/student_detail.aspx?aid=1"])
def test_no_other_page_is_one(url):
    assert not scope.is_platform_detail(url)


def test_alumni_pages_pass_the_site_cap_and_the_variant_cap_and_other_pages_still_do_not(tmp_path):
    crawler, _, _ = tg_crawler(tmp_path, site_cap=2, variant_cap=2)
    assert [crawler.enqueue(f"{TG}experience_view.aspx?id={i}", depth=1, found_via="link") for i in range(3)] == [
        "queued", "queued", "cap:site"]
    assert {crawler.enqueue(f"{ALUMNI}{i}", depth=2, found_via="link") for i in range(4)} == {"queued"}
    assert crawler.enqueue(TG + "page.aspx?id=1", depth=2, found_via="link") == "cap:site"
    assert crawler.enqueue(ALUMNI + "deep", depth=mod.MAX_DEPTH + 1, found_via="link") == "cap:depth"  # depth first
    assert crawler.frontier.count_site("c.nknu.edu.tw/tg", "page") == 6  # still counted for their site
