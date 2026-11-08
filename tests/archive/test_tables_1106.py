"""FIX 3 (11-06): a list grid with no link and no button is a table of content, not a list (【用户同意 Claude 的建
议】: Claude's 11-04 fix, one judgement and no exception page by page; the owner 11-04 「待会再修」, 11-06 the button
Claude wrote, 「先修三件再一起重开」). Offline: inline fixtures of the shapes the lab's ledger shows (affair's
forumsview.aspx, tecs's activeorder.aspx, aca's member.aspx, tg's honor.aspx; names O-masked, text made up); nothing
is sent."""

from __future__ import annotations

import pytest

from nknu.archive import postback, profiles, reconcile
from nknu.archive.frontier import Frontier
from nknu.archive.postback import list_end, read_page
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_postback import CNKNU_LIST, SPE_LIST, World, fx

AFFAIR = "c.nknu.edu.tw/affair"
FORUM = "https://c.nknu.edu.tw/affair/forumsview.aspx?gid=7"
TECS = "https://tecs.nknu.edu.tw/activeorder.aspx?aid=12"
HONOR = "https://c.nknu.edu.tw/tg/honor.aspx?pn=70&pclass=0006"


def _page(grid: str, action: str = "./x.aspx") -> str:
    return (f"<html><body><form method='post' action='{action}' id='form1'><input type='hidden' name='__VIEWSTATE' "
            f"value='VS'><table id='MasterGrid'>{grid}</table></form></body></html>")


# a discussion thread: a header cell with no text, a row of three cells and a nested table that repeats the text
FORUM_HTML = _page("<tr><th scope='col'>&nbsp;</th></tr><tr><td>王O明</td><td>2026/10/01 10:00</td><td>"
                   "請問場地借用的流程要怎麼申請？謝謝。<table><tr><td>回覆</td><td>請至總務處網站下載申請表。</td></tr>"
                   "</table></td></tr>", "./forumsview.aspx?gid=7")
TECS_HTML = _page("<tr><th>序號</th><th>姓名</th><th>單位</th><th>職稱</th><th>報名時間</th><th>備註</th></tr>"
                  + "".join(f"<tr><td>{i}</td><td>陳O華</td><td>某國小</td><td>教師</td><td>2026/09/0{i}</td><td></td>"
                            "</tr>" for i in range(1, 4)), "./activeorder.aspx?aid=12")


def _honor(n: int, last: int) -> str:
    nums = "".join(f"<td><span>{k}</span></td>" if k == n else
                   f"<td><a href=\"javascript:__doPostBack('MasterGrid','Page${k}')\">{k}</a></td>"
                   for k in range(1, last + 1))
    rows = "".join(f"<tr><td>第{n}頁第{i}名</td><td>林O君</td><td>全國賽第一名</td></tr>" for i in range(2))
    return _page(f"<tr><th>年度</th><th>姓名</th><th>名次</th></tr>{rows}"
                 f"<tr class='PagerStyle'><td colspan='3'><table><tr>{nums}</tr></table></td></tr>",
                 "./honor.aspx?pn=70&pclass=0006")


@pytest.mark.parametrize("url, site, html", [(FORUM, AFFAIR, FORUM_HTML), (TECS, "tecs.nknu.edu.tw", TECS_HTML)])
def test_a_table_with_no_link_and_no_pager_is_no_list(url, site, html):
    pr = read_page(profiles.CNKNU, url, html, site=site, list_url=url)
    assert (pr.rowset, pr.walk, pr.content) == (None, "none", True)
    assert not pr.unknown and not pr.unplanned and not pr.presses


def test_a_table_of_content_stored_by_get_writes_no_walk(tmp_path):
    w = World(tmp_path)
    row, got = w.stored(FORUM, AFFAIR, FORUM_HTML)
    assert (got.page_role, got.content, got.presses) == (None, True, 0)
    assert w.walk(row["id"]) is None and not w.actions(row["id"])
    w.sink.close()
    w.f.close()


def test_a_paged_table_of_content_is_walked_to_its_last_page(tmp_path):
    w = World(tmp_path)
    one = read_page(profiles.CNKNU, HONOR, _honor(1, 2), site="c.nknu.edu.tw/tg", list_url=HONOR)
    assert (one.walk, one.content, one.rows, one.next.argument) == ("postback", True, (), "Page$2")
    row, got = w.stored(HONOR, "c.nknu.edu.tw/tg", _honor(1, 2))
    assert got.page_role == "list" and not got.content and w.walk(row["id"])["ended_by"] == "walking"
    a, target = w.press()
    w.answer(a, target.url, _honor(2, 2))
    lw = w.walk(row["id"])
    assert (lw["ended_by"], lw["ok"], lw["pages"], lw["rows_unique"]) == ("last_page", 1, 2, 0)
    w.sink.close()
    w.f.close()


@pytest.mark.parametrize("html, url", [
    # c.nknu's Page$N pager in the grid, the page shown a number in a span of its own cell, no row (a page lost)
    (_page("<tr><th>標題</th></tr><tr class='PagerStyle'><td colspan='3'><table><tr><td><a href=\"javascript:"
           "__doPostBack('MasterGrid','Page$1')\">1</a></td><td><span>2</span></td></tr></table></td></tr>"),
     CNKNU_LIST),
    # spe's 「下一頁」 pager on its last page: 下一頁 and 尾頁 are <a> with no href
    (fx("spe_list_last.html").replace(fx("spe_list_last.html").split("<table id=\"MasterGrid\">")[1].split(
        "<tr class=\"PagerStyle\">")[0], ""), SPE_LIST),
])
def test_a_page_that_shows_only_its_pager_is_still_mismatch_empty(html, url):
    site = "spe.nknu.edu.tw" if "spe." in url else "c.nknu.edu.tw/aca"
    pr = read_page(profiles.CNKNU, url, html, site=site, list_url=url)
    assert pr.rowset == "MasterGrid" and pr.has_pager and not pr.content and not pr.rows
    assert list_end(pr) == "mismatch:empty"


@pytest.mark.parametrize("cell", [
    "<a href='javascript:openDetail(5)'>詳細</a>",  # a script nobody reads opens it
    "<a href='#'>詳細</a>",
    "<span onclick='go(5)'>詳細</span>",
    "<input type='button' value='報名'>",
    "<select name='s'><option>一</option></select>",
    "<a href=\"javascript:__doPostBack('btnDetail','5')\">詳細</a>",  # a postback nobody decided
])
def test_anything_that_may_open_something_keeps_a_grid_a_list(cell):
    html = _page(f"<tr><th>標題</th></tr><tr><td>一段文字</td><td>{cell}</td></tr>")
    pr = read_page(profiles.CNKNU, CNKNU_LIST, html, site="c.nknu.edu.tw/aca", list_url=CNKNU_LIST)
    assert pr.rowset == "MasterGrid" and not pr.content and list_end(pr) == "mismatch:empty"


def test_mailto_and_tel_links_do_not_make_a_list():
    html = _page("<tr><td>王O明 分機 1000 <a href='mailto:o@example.test'>o@example.test</a> "
                 "<a href='tel:+886000'>電話</a></td></tr>")
    pr = read_page(profiles.CNKNU, CNKNU_LIST, html, site="c.nknu.edu.tw/aca", list_url=CNKNU_LIST)
    assert (pr.walk, pr.content) == ("none", True)


@pytest.mark.parametrize("grid", [
    "<tr><th>標題</th><th>日期</th></tr>",  # a header and nothing else
    "<tr><th>標題</th></tr><tr><td>&nbsp;</td><td> </td></tr>",  # cells with nothing in them
])
def test_an_empty_list_that_does_not_say_so_keeps_mismatch_empty(grid):
    pr = read_page(profiles.CNKNU, CNKNU_LIST, _page(grid), site="c.nknu.edu.tw/aca", list_url=CNKNU_LIST)
    assert pr.rowset == "MasterGrid" and not pr.content and list_end(pr) == "mismatch:empty"


def test_a_list_that_says_it_is_empty_stays_an_empty_list():
    # tsi's filesdownload.aspx: a wrapper row around a nested table, then 查無資料
    html = _page("<tr><th>類別</th><th>檔案名稱</th><th>類型</th><th>檔案大小</th><td colspan='2'><table><tr><td>類別"
                 "</td><td>檔案名稱</td><td>類型</td><td>檔案大小</td></tr><tr><td>查無資料</td></tr></table></td></tr>")
    pr = read_page(profiles.CNKNU, CNKNU_LIST, html, site="c.nknu.edu.tw/tsi", list_url=CNKNU_LIST)
    assert pr.rowset == "MasterGrid" and not pr.content and list_end(pr) == "no_rows_ok"


def test_a_stated_total_keeps_a_grid_a_list():
    html = fx("sso_list_p1.html").replace("linkSubject_short", "linkSubject_x")  # rows no title selector finds
    html = html.split("<table id=\"gvFull\">")[0] + ("<table id=\"gvFull\"><tr><th>標題</th></tr><tr><td>一段文字</td>"
                                                    "</tr></table></div></form></body></html>")
    pr = read_page(profiles.SSO_NEWS, "https://sso.nknu.edu.tw/news/subjectlist.aspx?cat=X", html,
                   site="sso.nknu.edu.tw/news")
    assert pr.claimed == 4 and pr.rowset == "gvFull" and not pr.content and list_end(pr) == "mismatch:empty"


@pytest.mark.parametrize("name, url, prof, site", [
    ("cnknu_list_p1.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca"),
    ("cnknu_list_p2.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca"),
    ("reality_download.html", "https://c.nknu.edu.tw/reality/download.aspx?pn=7", profiles.CNKNU,
     "c.nknu.edu.tw/reality"),
    ("spe_list_p1.html", SPE_LIST, profiles.CNKNU, "spe.nknu.edu.tw"),
    ("spe_list_last.html", SPE_LIST, profiles.CNKNU, "spe.nknu.edu.tw"),
    ("sso_list_p1.html", "https://sso.nknu.edu.tw/news/subjectlist.aspx?cat=SCHOOLADMIN", profiles.SSO_NEWS,
     "sso.nknu.edu.tw/news"),
    ("clv2_index.html", "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=105000", profiles.SSO_CLV2,
     "sso.nknu.edu.tw/site/105000"),
    ("cnknu_empty.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca"),
])
def test_lists_stay_lists(name, url, prof, site):
    before = read_page(prof, url, fx(name), site=site, list_url=url)
    assert before.walk != "none" and not before.content


# ---------------------------------------------------------------- what the 11-04 crawl left (rejudge)

def _left_as_a_list(w: World, list_id: int, *, pages: int = 1, epoch: int = 1) -> None:
    """The rows the 11-04 code wrote for a table it took for a list: page_role list, a walk ended mismatch:empty."""
    w.f.x("UPDATE url SET page_role='list' WHERE id=?", (list_id,))
    w.f.x("INSERT OR REPLACE INTO listwalk(list_id, site, profile, epoch, pages, rows, rows_unique, claimed, ended_by, "
          "ok, note, at) VALUES (?,?,?,?,?,0,0,NULL,'mismatch:empty',0,NULL,1)",
          (list_id, AFFAIR, "cnknu", epoch, pages))


def _level(f: Frontier, list_id: int) -> tuple:
    got = f.q("SELECT list_bad, list_unchecked FROM page_completeness WHERE id=?", (list_id,))
    return tuple(got[0])


def test_rejudge_takes_the_list_marks_off_a_table_of_content(tmp_path):
    w = World(tmp_path)
    row, _got = w.stored(FORUM, AFFAIR, FORUM_HTML)
    _left_as_a_list(w, row["id"])
    assert _level(w.f, row["id"]) == (1, 0)  # 列表没翻完
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [AFFAIR], plan=make_plan(make_site(AFFAIR, home=FORUM)))
    f = Frontier(tmp_path / "frontier.db")
    assert f.row_by_id(row["id"])["page_role"] is None
    assert not f.q("SELECT 1 FROM listwalk WHERE list_id=?", (row["id"],))
    assert _level(f, row["id"]) == (0, 0)  # neither 列表没翻完 nor 列表没核对
    f.close()


def test_rejudge_judges_a_paged_table_again_from_its_last_page(tmp_path):
    w = World(tmp_path)
    tg = "c.nknu.edu.tw/tg"
    row, _got = w.stored(HONOR, tg, _honor(1, 2))
    a, target = w.press()
    w.answer(a, target.url, _honor(2, 2))
    w.f.x("UPDATE listwalk SET ended_by='mismatch:empty', ok=0 WHERE list_id=?", (row["id"],))  # as 11-04 ended it
    actions = [dict(x) for x in w.actions(row["id"])]
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [tg], plan=make_plan(make_site(tg, home=HONOR)))
    f = Frontier(tmp_path / "frontier.db")
    lw = f.q("SELECT * FROM listwalk WHERE list_id=?", (row["id"],))[0]
    assert (lw["ended_by"], lw["ok"], lw["pages"], lw["note"]) == ("last_page", 1, 2, "rejudge: was mismatch:empty")
    assert [dict(x) for x in f.q("SELECT * FROM action WHERE list_id=? ORDER BY id", (row["id"],))] == actions
    assert f.row_by_id(row["id"])["page_role"] == "list"
    f.close()


def test_rejudge_leaves_a_list_that_is_really_empty(tmp_path):
    w = World(tmp_path)
    html = _page("<tr><th>標題</th><th>日期</th></tr>", "./newslist.aspx?gi=1")
    row, _got = w.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", html)
    walk = dict(w.walk(row["id"]))
    assert walk["ended_by"] == "mismatch:empty"
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, ["c.nknu.edu.tw/aca"], plan=make_plan(make_site("c.nknu.edu.tw/aca",
                                                                                home=CNKNU_LIST)))
    f = Frontier(tmp_path / "frontier.db")
    assert dict(f.q("SELECT * FROM listwalk WHERE list_id=?", (row["id"],))[0]) == walk
    assert f.row_by_id(row["id"])["page_role"] == "list"
    f.close()


def test_a_page_that_lost_its_grid_keeps_its_walk(tmp_path):
    # no grid at all is no proof of a table of content: what the last reading said stays (保留旧值)
    w = World(tmp_path)
    aca = "c.nknu.edu.tw/aca"
    row, _got = w.stored(CNKNU_LIST, aca, fx("cnknu_list_p2.html"))
    w.f.x("UPDATE url SET page_role='list' WHERE id=?", (row["id"],))  # as the crawler leaves a list page
    walk = dict(w.walk(row["id"]))
    w.page(CNKNU_LIST, aca, "<html><body><p>系統維護中</p></body></html>")  # its stored answer, read again, has no grid
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [aca], plan=make_plan(make_site(aca, home=CNKNU_LIST)))
    f = Frontier(tmp_path / "frontier.db")
    assert dict(f.q("SELECT * FROM listwalk WHERE list_id=?", (row["id"],))[0]) == walk
    assert f.row_by_id(row["id"])["page_role"] == "list"
    f.close()
    assert not postback.Stored(None).content


def test_a_table_with_get_pager_links_is_still_walked_by_get(tmp_path):
    # GET pager links on the page (links.extract's pagers) make it a list walked by GET whatever its grid holds
    from nknu.archive import links

    html = FORUM_HTML.replace("</form>", "<div><a href='forumsview.aspx?gid=7&amp;page=2'>2</a> "
                                         "<a href='forumsview.aspx?gid=7&amp;page=2'>下一頁</a></div></form>")
    w = World(tmp_path)
    row, _snap = w.page(FORUM, AFFAIR, html)
    page = links.extract(html, row["url"], profiles.content_css(row["url"]))
    assert page.pagers
    got = w.chains.page_stored(row, html, w.warc, _snap.warc_offset, page)
    assert (got.page_role, got.content) == ("list", False)
    w.sink.close()
    w.f.close()


def test_rejudge_ends_a_spe_table_whose_next_stayed_on_its_page(tmp_path):
    # spe's 「下一頁」 answered with the page it was on (_pager_answer: next stayed): the walk's last answer is at step
    # pages + 1, and it still shows a 下一頁 press; judged again from that answer, with no next
    spe = "spe.nknu.edu.tw"
    first = fx("spe_list_p1.html")
    rows = first.split('<tr class="HeaderStyle">')[1].split('<tr class="PagerStyle">')[0]
    text = first.replace(rows, "<th>年度</th><th>姓名</th><th>名次</th></tr><tr><td>2026</td><td>王O明</td>"
                               "<td>第一名</td></tr>")
    text = text.replace("<a href=\"javascript:__doPostBack(&#39;MasterGrid$ctl23$ctl00&#39;,&#39;&#39;)\">2</a>", "")
    assert "ctl00" not in text  # one page: its pager names no page 2
    w = World(tmp_path)
    one = read_page(profiles.CNKNU, SPE_LIST, text, site=spe, list_url=SPE_LIST)
    assert (one.walk, one.content, one.pager_how, one.current) == ("postback", True, "next", 1)
    row, _got = w.stored(SPE_LIST, spe, text)
    a, target = w.press()
    w.answer(a, target.url, text)  # 下一頁 brought page 1 again
    lw = w.walk(row["id"])
    assert (lw["ended_by"], lw["pages"]) == ("last_page", 1)
    w.f.x("UPDATE listwalk SET ended_by='mismatch:empty', ok=0 WHERE list_id=?", (row["id"],))  # as 11-04 ended it
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [spe], plan=make_plan(make_site(spe, home=SPE_LIST)))
    f = Frontier(tmp_path / "frontier.db")
    assert tuple(f.q("SELECT ended_by, ok FROM listwalk WHERE list_id=?", (row["id"],))[0]) == ("last_page", 1)
    f.close()


def test_the_last_page_of_a_spe_table_ends_it():
    # spe's last page writes 下一頁 and 尾頁 as <a> with no href: no link, so a table of text there ends its walk
    last = fx("spe_list_last.html")
    row = last.split('<table id="MasterGrid">')[1].split('<tr class="PagerStyle">')[0]
    html = last.replace(row, "<tr><td>2026.05.13</td><td>校隊名單</td><td>王O明</td></tr>")
    pr = read_page(profiles.CNKNU, SPE_LIST, html, site="spe.nknu.edu.tw", list_url=SPE_LIST)
    assert (pr.walk, pr.content, pr.current, pr.next) == ("postback", True, 2, None)
    assert list_end(pr) == "last_page"
