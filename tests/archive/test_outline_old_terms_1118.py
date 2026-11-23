"""11-18 evening (course schedules design §八): every course outline of the course query links 「114-1該系所日/夜相關
課綱」, the outlines of the same department a year before, whose pages link the year before that: followed, the crawl
walks back term by term with no end a person asked for. A site of the list may name links it does not follow by the
page they stand on (skip_from: pairs of paths, a link on a page under the first to an address under the second), the
rule out:skip_path as its skip paths; the course query names its outline pages' links to outlines. The outlines the
classes' tables name, and the page an outline redirects to, are followed as before. Made-up addresses and words."""

from __future__ import annotations

import pytest

from nknu.archive import round1
from nknu.archive.crawler import Crawler
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_pilot_fixes import SSO_Q, PressNet, work

SITE = "sso.nknu.edu.tw/stu"
BASE = "https://sso.nknu.edu.tw/stu/"
CLASS = BASE + "scheduledepartment.aspx?cdes=abc"
OUTLINE = BASE + "syllabusforenro.aspx?syllabusno=1150000001"
LANDING = BASE + "18weekssyllabusforenro.aspx?syllabusno=1150000001"
OLDER = BASE + "syllabusforenro.aspx?syllabusno=1140000009"
OLDER_LANDING = BASE + "18weekssyllabusforenro.aspx?syllabusno=1140000009"
PAIR = ("/stu/18weekssyllabusforenro.aspx", "/stu/syllabusforenro.aspx")


@pytest.fixture(scope="module")
def plan():
    return round1.load()


def _site(**more) -> round1.Site:
    return round1.parse_site({"site": "x.nknu.edu.tw", "round": 1, "imp": "本系重要", "home": "https://x.nknu.edu.tw/",
                              **more})


def test_a_site_entry_may_name_links_it_does_not_follow_by_the_page_they_stand_on():
    s = _site(skip_from=[["/A/Outline.aspx", "/a/older.aspx"]])
    assert s.skip_from == (("/a/outline.aspx", "/a/older.aspx"),)
    on, to = "https://x.nknu.edu.tw/a/Outline.aspx?no=2", "https://x.nknu.edu.tw/a/older.aspx?no=1"
    assert s.skips(to, on)
    assert not s.skips(to)  # found nowhere the rule names: a seed, a list, an old archive's row
    assert not s.skips(to, "https://x.nknu.edu.tw/a/class.aspx?c=1")  # another page's link to it
    assert not s.skips("https://x.nknu.edu.tw/a/other.aspx", on)  # the page's other links
    assert not s.skips(to, "https://y.nknu.edu.tw/a/outline.aspx")  # a page of another site


def test_a_skip_from_entry_is_a_pair_of_paths():
    for bad in (["/a"], [["/a"]], [["/a", "/b", "/c"]], [["/a", 3]], "/a"):
        with pytest.raises(ValueError):
            _site(skip_from=bad)


def test_the_skip_paths_of_a_site_hold_wherever_the_link_stands():
    s = _site(skip=["/a/tags"], skip_from=[["/a/outline.aspx", "/a/older.aspx"]])
    assert s.skips("https://x.nknu.edu.tw/a/tags/b") and s.skips("https://x.nknu.edu.tw/a/tags/b",
                                                                 "https://x.nknu.edu.tw/a/class.aspx")


def test_the_course_query_does_not_follow_an_outline_s_links_to_outlines(plan):
    site = plan.site(SITE)
    assert site.skip_from == (PAIR,)
    row = lambda url: {"url": url, "owner_site": SITE, "tier": site.tier, "id": 1}
    older = round1.verdict(plan, OLDER, row(LANDING), "link")
    assert (older.verdict, older.reason, older.state) == ("out", "out:skip_path", None)
    assert round1.verdict(plan, OUTLINE, row(CLASS), "link").verdict == "r1"  # the outlines a class's table names
    assert round1.verdict(plan, LANDING, row(OUTLINE), "link").verdict == "r1"  # the page an outline answers with
    assert round1.verdict(plan, OLDER, None, "link").verdict == "r1"  # no page behind it: as before
    assert round1.verdict(plan, CLASS, row(LANDING), "link").verdict == "r1"  # an outline's other links


@pytest.mark.parametrize("term", ["1151", "1152", "1161", "1202"])
def test_the_rule_names_no_term(plan, term):
    """爬虫judge 11-18: 「规则要窄：只挡「大纲页 → 别的大纲」这一种链接。班级课表页链到的大纲一律照抓。下学期初重走表单时，
    新学期的大纲要照样抓得到」: a class's table brings its outlines and an outline its page, whatever term the numbers
    name; only an outline page's own link to an outline is left."""
    site = plan.site(SITE)
    row = lambda url: {"url": url, "owner_site": SITE, "tier": site.tier, "id": 1}
    outline = BASE + f"syllabusforenro.aspx?syllabusno={term}000001"
    landing = BASE + f"18weekssyllabusforenro.aspx?syllabusno={term}000001"
    assert round1.verdict(plan, CLASS, None, "link").verdict == "r1"  # a class's address the walk adds
    got = round1.verdict(plan, outline, row(CLASS), "link")
    assert (got.verdict, got.owner_site) == ("r1", SITE)
    assert round1.verdict(plan, landing, row(outline), "link").verdict == "r1"
    assert round1.verdict(plan, BASE + "files/a.pdf", row(landing), "link").verdict in ("r1", "resource")
    for other in (OUTLINE, OLDER, BASE + "syllabusforenro.aspx?syllabusno=1211000001"):
        assert round1.verdict(plan, other, row(landing), "link").reason == "out:skip_path"


def test_a_row_that_came_by_such_a_link_is_known_by_the_pages_it_was_found_from():
    """Rows got before the rule came (the one-time marking, 11-18): the older outline an outline page linked, the page
    it redirects to, what that page links in turn; never an outline a class's table named, nor what it leads to."""
    s = round1.Site(site=SITE, round="1", imp="本系重要", home=BASE, skip_from=(PAIR,))
    rows = {1: {"id": 1, "url": CLASS, "found_from": None},
            2: {"id": 2, "url": OUTLINE, "found_from": 1},
            3: {"id": 3, "url": LANDING, "found_from": 2},
            4: {"id": 4, "url": OLDER, "found_from": 3},
            5: {"id": 5, "url": OLDER_LANDING, "found_from": 4},
            6: {"id": 6, "url": BASE + "files/a.pdf", "found_from": 5},
            7: {"id": 7, "url": BASE + "files/b.pdf", "found_from": 3},
            8: {"id": 8, "url": BASE + "syllabusforenro.aspx?syllabusno=1130000002", "found_from": 5}}
    got = {i for i, r in rows.items() if round1.came_by_skipped_link(s, r, rows.get)}
    assert got == {4, 5, 6, 8}
    loop = {1: {"id": 1, "url": CLASS, "found_from": 2}, 2: {"id": 2, "url": OUTLINE, "found_from": 1}}
    assert not round1.came_by_skipped_link(s, loop[1], loop.get)  # a ring of rows ends
    plain = round1.Site(site=SITE, round="1", imp="本系重要", home=BASE)
    assert not round1.came_by_skipped_link(plain, rows[4], rows.get)  # a site with no such rule


def test_the_crawler_leaves_the_older_outlines_an_outline_page_links(tmp_path):
    clock = Clock()
    page = ('<html><body><form id="uForm"><a href="syllabusForEnro.aspx?SyllabusNo=1140000009">114-1該系所日/夜相關課綱'
            '</a> <a href="scheduleDepartment.aspx?cDES=abc">班級課表</a></form></body></html>')
    net = PressNet(clock, {("GET", "/robots.txt"): snap("x", 404),
                           ("GET", "18weekssyllabusforenro.aspx"): snap(LANDING, html=page),
                           ("GET", "scheduledepartment.aspx"): snap(CLASS, html="<html><body><p>課表</p></body></html>")})
    stu = round1.Site(site=SITE, round="1", imp="本系重要", home=BASE, skip_from=(PAIR,))
    crawler = Crawler(tmp_path, plan=OpenPlan(stu), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    seed(crawler, LANDING)
    work(crawler, net, SSO_Q, rounds=20)
    f = crawler.frontier
    assert f.row(LANDING)["state"] == "done"
    assert not f.q("SELECT 1 FROM url WHERE url LIKE '%syllabusno=1140000009%'")  # no row: nothing to fetch, to hold
    assert [r["verdict"] for r in f.q("SELECT verdict FROM link WHERE dst LIKE '%syllabusno=1140000009%'")] == [
        "out:skip_path"]
    assert f.q("SELECT state FROM url WHERE url LIKE '%scheduledepartment.aspx%'")[0]["state"] == "done"
    assert not [u for _m, u in net.seen if "1140000009" in u]
    f.close()
