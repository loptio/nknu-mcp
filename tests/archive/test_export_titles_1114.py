"""The export's 标题 of three kinds of page whose own title sits in one cell of the page (the owner 11-14 「先补标题栏」
and, shown how, 「可以，开工」; the ask is the direction paper's item 4, docs/superpowers/specs/2026-11-13-direction.md:
「先取网页上现成的栏位：services 活动的名称、讨论区每则提问的标题、所友的名字」). Round one holds 11,449 activity pages of
sso.nknu.edu.tw/services, 655 forum threads of 总务处 and 1,976 所友名錄 pages of 成教所, every page of a kind titled
alike by the site's <title> or <h2>, since no platform describes a title cell of theirs. A rule the export alone reads
(profiles.PageTitle, by the page's own address) names the cell; a cell with no words leaves the old reading. Nothing of
the crawl, of the platform profiles, or of the content stage's page version reads it (design
docs/superpowers/specs/2026-11-14-page-titles-design.md; how, 【Claude 的执行办法】). The cells were measured on round
one's archive 11-14, read only: [id$='_ActivityContent_lbSubject'] on all 11,449 (3 empty), #lbl_PoTitle on all 655,
#lbl_aname on all 1,976. Made-up pages in the templates' shapes, names O-masked, no school text."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import export, profiles, scope
from tests.archive.test_areas_1109 import ALUMNUS, forum
from tests.archive.test_export_1027 import BASE, SITE, make_archive
from tests.archive.test_export_no_data_1109 import _export_at

FORUM_URL = "https://c.nknu.edu.tw/affair/forumsview.aspx?fid=7"
ALUMNUS_URL = "https://c.nknu.edu.tw/tg/student_detail.aspx?aid=12"
ACADEMIC_URL = "https://sso.nknu.edu.tw/services/academicactivities/actdetail.aspx?unid=0A1B"
CAMPUS_URL = "https://sso.nknu.edu.tw/services/campusactivities/actdetail.aspx?unid=0C2D"
ACTIVITY_CELL = "[id$='_ActivityContent_lbSubject']"
AFFAIR, TG, SERVICES = "c.nknu.edu.tw/affair", "c.nknu.edu.tw/tg", "sso.nknu.edu.tw/services"
SITE_TITLE = "單一登入平台（測試）"  # the <title> every page of the shared module carries (made up)
PAGE_TITLE = "測試網站"  # the <title> of test_areas_1109's c.nknu pages, which have no h1 or h2


def activity(name: str = "程式設計工作坊") -> str:
    """An activity page of SSO's shared module (made up, in the template's shape as round one's pages have it: no h1
    or h2, the site's <title>, the activity's fields in the spans of one table, its name in
    ctl00_phMain_ActivityContent_lbSubject)."""
    def row(n: int, label: str, field: str, value: str) -> str:
        return (f'<tr id="ctl00_phMain_ActivityContent_trr{n}"><td>{label}</td><td>'
                f'<span id="ctl00_phMain_ActivityContent_{field}">{value}</span></td></tr>')

    return (f'<html><head><title>{SITE_TITLE}</title></head><body><form method="post" id="aspnetForm">'
            '<div id="mainFrame"><table id="ctl00_phMain_ActivityContent_TBEdit">'
            + row(0, "活動類型", "lbType", "專題演講") + row(2, "活動名稱*", "lbSubject", name)
            + row(7, "活動地點*", "lbPlace", "綜合大樓 1 樓") + row(11, "活動內容*", "lbContext", "歡迎報名參加。")
            + "</table></div></form></body></html>")


def thread(title: str, before: str = "") -> str:
    """test_areas_1109's forum thread with this in its title cell (and `before` ahead of the question's table)."""
    return forum(question=f'{before}<table><tr><td>主題：</td><td><span id="lbl_PoTitle">{title}</span></td></tr>'
                 '<tr><td colspan="2"><span id="lbl_PoText">請問何時開放申請？</span></td></tr></table>')


def _title(html: str, url: str, site: str) -> str:
    return export.page_title(LexborHTMLParser(html), url, site)


# ---------------------------------------------------------------- the rules


@pytest.mark.parametrize(("url", "cell"), [
    (FORUM_URL, "#lbl_PoTitle"),
    # a c.nknu-template site with its own domain, its path in the case IIS answers to as well
    ("https://tecs.nknu.edu.tw/ForumsView.aspx?fid=1", "#lbl_PoTitle"),
    (ALUMNUS_URL, "#lbl_aname"),
    (ACADEMIC_URL, ACTIVITY_CELL),
    ("https://sso.nknu.edu.tw/Services/CampusActivities/ActDetail.aspx?unid=0C2D", ACTIVITY_CELL),
])
def test_a_page_of_the_three_kinds_names_its_title_cell_by_its_own_address(url, cell):
    assert profiles.page_title_rule(url).title == cell


@pytest.mark.parametrize("url", [
    "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=1",  # a notice: its platform's detail title, no rule of these
    "https://c.nknu.edu.tw/affair/forums.aspx",  # the forum's list
    "https://c.nknu.edu.tw/tg/student.aspx?pclass=1",  # the alumni list
    "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=592",  # a unit site: another site key
    "https://sso.nknu.edu.tw/services/other/actdetail.aspx",  # another module of the shared site
    "https://sso.nknu.edu.tw/news/forumsview.aspx",  # the path under a site of another template
    "https://www.example.test/forumsview.aspx",
    "",
])
def test_no_other_address_names_a_title_cell(url):
    assert profiles.page_title_rule(url) is None


def test_check_profiles_stays_empty_and_names_a_title_rule_that_is_wrong(monkeypatch):
    assert profiles.check_profiles() == []
    bad = profiles.PageTitle("(", "[", "", "made up")
    monkeypatch.setattr(profiles, "PAGE_TITLES", (bad,))
    got = profiles.check_profiles()
    assert len(got) == 3 and all(x.startswith("page title ") for x in got), got


# ---------------------------------------------------------------- the title the export reads


@pytest.mark.parametrize(("url", "site", "html", "want"), [
    # white space in the cell made one
    pytest.param(ACADEMIC_URL, SERVICES, activity("程式設計\n      工作坊"), "程式設計 工作坊", id="academic"),
    pytest.param(CAMPUS_URL, SERVICES, activity("社團幹部訓練"), "社團幹部訓練", id="campus"),
    pytest.param(FORUM_URL, AFFAIR, forum(), "停車證何時開放申請", id="thread"),
    pytest.param(ALUMNUS_URL, TG, ALUMNUS, "王O明", id="alumnus"),
    # by the page's own address, whatever site's export lists it (a redirect landing on it, another site's row)
    pytest.param(FORUM_URL, "sso.nknu.edu.tw/web/592", forum(), "停車證何時開放申請", id="thread-in-another-export"),
])
def test_a_page_of_the_three_kinds_is_titled_by_its_own_cell(url, site, html, want):
    assert _title(html, url, site) == want


@pytest.mark.parametrize("cell", ["", "  \n\t ", "&nbsp;", "<br>"])
def test_a_cell_with_no_words_leaves_the_old_reading(cell):
    """3 of round one's 11,449 activity pages have nothing in the cell: the page's first h1, then h2, then <title>,
    as before, never an empty title."""
    assert _title(activity(cell), ACADEMIC_URL, SERVICES) == SITE_TITLE  # the shared module's pages: no h1, no h2
    assert _title(thread(cell, before="<h2>討論區</h2>"), FORUM_URL, AFFAIR) == "討論區"
    assert _title(thread(cell), FORUM_URL, AFFAIR) == PAGE_TITLE


@pytest.mark.parametrize(("url", "site", "html", "want"), [
    pytest.param("https://c.nknu.edu.tw/affair/page.aspx?pn=3", AFFAIR, forum(), PAGE_TITLE, id="another-page"),
    pytest.param("https://c.nknu.edu.tw/tg/student.aspx?pclass=1", TG, ALUMNUS, PAGE_TITLE, id="the-alumni-list"),
    pytest.param("https://www.example.test/forumsview.aspx?fid=7", "www.example.test", forum(), PAGE_TITLE,
                 id="another-template"),
    pytest.param("https://sso.nknu.edu.tw/services/other.aspx?unid=0A1B", SERVICES, activity(), SITE_TITLE,
                 id="another-module"),
])
def test_the_same_cell_at_another_address_is_not_the_title(url, site, html, want):
    assert _title(html, url, site) == want


def test_a_notices_own_title_field_comes_before_a_title_rule(monkeypatch):
    """The order: the platform's detail title (profiles.Detail.title), then a title rule, then h1, h2, <title>."""
    notice = (f'<html><head><title>{PAGE_TITLE}</title></head><body><h2>最新消息</h2><span id="lbl_Title">停車場整修公告'
              '</span><span id="lbl_PoTitle">別的字</span><span id="lbl_Contents">整修期間請改道。</span></body></html>')
    url = "https://c.nknu.edu.tw/affair/newsdetail.aspx?nid=5"
    rule = profiles.PageTitle(profiles.CNKNU.sites, r"/newsdetail\.aspx$", "#lbl_PoTitle", "made up")
    monkeypatch.setattr(profiles, "PAGE_TITLES", (rule,))
    assert _title(notice, url, AFFAIR) == "停車場整修公告"


def test_the_crawlers_reading_of_these_pages_is_as_before():
    """Export only: the shared module has no platform profile still, and none of the three kinds became a platform's
    detail page (whose empty areas the crawler flags and the export says in 缺什么)."""
    assert profiles.profile_for(ACADEMIC_URL) is None and profiles.profile_for(CAMPUS_URL) is None
    for url, html in ((FORUM_URL, forum()), (ALUMNUS_URL, ALUMNUS), (ACADEMIC_URL, activity())):
        assert profiles.detail_of(url) is None and profiles.empty_detail(url, html) is None, url
        assert export.empty_detail_area(LexborHTMLParser(html), url, scope.site_key(url)) == "", url


def test_the_content_stages_page_version_does_not_read_the_rules(monkeypatch):
    """PAGE_VERSION (nknu.totext.pagetext, read only here) digests what the content stage's page reading takes of the
    platform profiles; the title rules are in none, so no page is read again for them."""
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    with_rules = pagetext.page_version(profiles.PROFILES)
    monkeypatch.setattr(profiles, "PAGE_TITLES", ())
    assert pagetext.page_version(profiles.PROFILES) == with_rules == pagetext.PAGE_VERSION


# ---------------------------------------------------------------- the export


def _three_kinds(arch) -> None:
    """On top of 教务处's fixture (a c.nknu-template site): a thread, a thread whose title cell is empty and an
    alumnus's page; the shared module's site: two activity pages, one with no name, and a page of another module of
    it that carries the same cell."""
    arch.page("thread", BASE + "forumsview.aspx?fid=7", forum())
    arch.page("blank", BASE + "forumsview.aspx?fid=8", thread(""))
    arch.page("alumnus", BASE + "student_detail.aspx?aid=12", ALUMNUS)
    sso = {"host": "sso.nknu.edu.tw", "site": SERVICES, "owner_site": SERVICES, "title": SITE_TITLE}
    arch.page("academic", ACADEMIC_URL, activity("程式設計工作坊"), **sso)
    arch.page("campus", CAMPUS_URL, activity("社團幹部訓練"), **sso)
    arch.page("unnamed", ACADEMIC_URL.replace("0A1B", "0E3F"), activity(""), **sso)
    arch.page("other", "https://sso.nknu.edu.tw/services/other.aspx?unid=0A1B", activity("別的模組"), **sso)


def test_only_the_titles_and_file_names_of_the_three_kinds_change(tmp_path, monkeypatch):
    """The same archive exported with the rules and without them: of the pages whose cell has words, 标题 is the cell's
    words and the file keeps its folder, its number and its bytes under a name ending in the new title; every other
    column of theirs, every other row, missing.csv, the README and the command's lines are the same."""
    arch = make_archive(tmp_path / "archive").open()
    _three_kinds(arch)
    arch.close()
    sites = (SITE, SERVICES)
    on = {s: _export_at(arch, tmp_path / f"on{n}", s) for n, s in enumerate(sites)}
    monkeypatch.setattr(profiles, "PAGE_TITLES", ())
    off = {s: _export_at(arch, tmp_path / f"off{n}", s) for n, s in enumerate(sites)}
    want = {SITE: {BASE + "forumsview.aspx?fid=7": ("停車證何時開放申請", PAGE_TITLE),
                   BASE + "student_detail.aspx?aid=12": ("王O明", PAGE_TITLE)},
            SERVICES: {ACADEMIC_URL: ("程式設計工作坊", SITE_TITLE), CAMPUS_URL: ("社團幹部訓練", SITE_TITLE)}}
    for s in sites:
        a, b = on[s], off[s]
        assert [r["网址"] for r in a.rows] == [r["网址"] for r in b.rows]
        renamed = {}
        for x, y in zip(a.rows, b.rows, strict=True):
            if x["网址"] not in want[s]:
                assert x == y, x["网址"]
                continue
            new, old = want[s][x["网址"]]
            assert (x["标题"], y["标题"]) == (new, old), x["网址"]
            # pages/<number>-<short name>.html: the same folder and number, the title at the end of the name
            assert x["本地档案"].startswith("pages/") and x["本地档案"].split("-")[0] == y["本地档案"].split("-")[0]
            assert x["本地档案"].endswith(f"-{new}.html") and new not in y["本地档案"], x["网址"]
            assert a.files[x["本地档案"]] == b.files[y["本地档案"]]
            assert {k: v for k, v in x.items() if k not in ("标题", "本地档案")} == {
                k: v for k, v in y.items() if k not in ("标题", "本地档案")}, x["网址"]
            renamed[x["本地档案"]] = y["本地档案"]
        assert len(renamed) == len(want[s])
        assert {renamed.get(p, p): data for p, data in a.files.items()} == b.files
        assert a.missing == b.missing and a.readme == b.readme and a.lines == b.lines
