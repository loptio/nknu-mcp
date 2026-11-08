"""Round one's gap ⑤ (docs/round1-gaps-2026-11-08.md; the owner 11-08 asked for the fill-in of ①～⑥, how each is done
【Claude 的执行办法】):

⑤-1 c.nknu's 「下一頁」 pager (MasterGrid$ctlNN$lbnNext) writes ten page links and the page shown; past page 5 of a list
   longer than 15 pages the page shown is outside its links, so the answer names no page. It is taken as the page asked
   for when it is a page of the list asked (it holds the list, its form posts back to the address pressed), still has
   its 「下一頁」, its pager reaches that page and at least half its rows are new (postback.unnumbered_next);
⑤-2 an answer that holds no list, or that is another page (tecs's publish.aspx answered by Default.aspx), says so in the
   walk's note (postback.answer_elsewhere), and a 「下一頁」 answer from another page never ends a walk as its last page;
and `nknu archive retry mismatch:page` walks the lists that ended so again from their GET (Chains.retry_walks).
All offline: a temporary archive, pages made from tests/fixtures/archive_postback with made-up titles, dates and
numbers; no person's name."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import profiles
from nknu.archive.crawler import hold_run_lock
from nknu.archive.frontier import Frontier
from nknu.archive.postback import UNNUMBERED, Press, answer_elsewhere, list_end, read_page, unnumbered_next
from tests.archive.test_postback import CNKNU_LIST, SPE_LIST, SSO_LIST, World, fx

SPE = "spe.nknu.edu.tw"
P1 = fx("spe_list_p1.html")  # page 1 of spe's 最新消息: its pager shows 1 and links 2
P1_ROWS = ('<tr class="RowStyle"><td>2026.07.30</td><td>最新消息</td><td><a href="NewsDetail.aspx?Nid=1969">'
           '鑑定說明手冊公告</a></td></tr>\n<tr class="AlternatingRowStyle"><td>2026.07.08</td><td>最新消息</td><td>'
           '<a href="NewsDetail.aspx?Nid=1955">暑假辦公時間調整</a></td></tr>')
SHOWN_1 = ("<span style='color: deeppink; font-weight: bold;'>1</span>\n"
           '<a href="javascript:__doPostBack(&#39;MasterGrid$ctl23$ctl00&#39;,&#39;&#39;)">2</a>')
OLDER_ROWS = ('<tr class="RowStyle"><td>2025.12.03</td><td>最新消息</td><td><a href="NewsDetail.aspx?Nid=1910">'
              '寒假開放時間公告</a></td></tr>\n<tr class="AlternatingRowStyle"><td>2025.11.27</td><td>最新消息</td><td>'
              '<a href="NewsDetail.aspx?Nid=1904">期末考試注意事項</a></td></tr>')
NEXT = '<a id="MasterGrid_lbnNext" href="javascript:__doPostBack(&#39;MasterGrid$ctl23$lbnNext&#39;,&#39;&#39;)">下一頁</a>'
POSTED = "https://spe.nknu.edu.tw/NewsList.aspx?NC=1"  # where page 1's form posts: every press of this list goes there
QUEUE = "ip:140.127.40.173"


def window(first: int = 26, last: int = 35, rows: str = OLDER_ROWS, state: str = "VS-SPE-P6") -> str:
    """A page of spe's list past page 5 of a 35-page list, as c.nknu's template writes it: its pager links the pages
    first … last (ctl00 …) and shows no page as the page shown (no <span>)."""
    numbers = "\n".join(f'<a href="javascript:__doPostBack(&#39;MasterGrid$ctl23$ctl{i:02d}&#39;,&#39;&#39;)">{n}</a>'
                        for i, n in enumerate(range(first, last + 1)))
    html = P1.replace("VS-SPE-P1", state).replace(P1_ROWS, rows).replace(SHOWN_1, numbers)
    assert state in html and numbers in html and (rows in html or not rows)
    return html


def read(html: str, url: str = SPE_LIST, step: int | None = 6):
    return read_page(profiles.CNKNU, url, html, site=SPE, list_url=url, step=step)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.sink.close()
    w.f.close()


# ---------------------------------------------------------------- reading one answer (no database)

def test_p1_fixture_pieces_are_there():
    assert P1_ROWS in P1 and SHOWN_1 in P1 and NEXT in P1


def test_window_page_names_no_page_but_its_next_control():
    pr = read(window())
    assert (pr.pager_how, pr.current, pr.max_seen, pr.next) == ("next", None, 35, None)
    assert pr.next_control == ("MasterGrid$ctl23$lbnNext", "", "下一頁", "form1")
    assert pr.posts_to == "./NewsList.aspx?NC=1" and len(pr.rows) == 2
    assert list_end(pr) == "mismatch:page"  # read alone, a page that names none is no page of the walk
    first = read(P1, step=None)  # a page that names itself keeps its next press, and the control too
    assert first.current == 1 and first.next.row_key == "p2" and first.next_control == pr.next_control
    last = read(fx("spe_list_last.html"), step=None)  # the last page's 「下一頁」 is disabled: no control
    assert last.current == 2 and last.next_control is None


def test_unnumbered_next_rules():
    pr = read(window())
    keys = [r.key for r in pr.rows]
    taken, why = unnumbered_next(pr, 6, frozenset(), POSTED)
    assert why == "" and taken.current == 6
    assert taken.next == Press("pager", "MasterGrid$ctl23$lbnNext", "", "p7", "下一頁", 7, "form1")
    assert unnumbered_next(pr, 6, {keys[0]}, POSTED)[0] is not None  # half the rows new: a pinned row passes
    assert unnumbered_next(pr, 6, set(keys), POSTED) == (None, "shows no page number and 2 of 2 rows walked already")
    short = "shows no page number and its pager names pages up to 35 only"
    assert unnumbered_next(pr, 36, set(), POSTED) == (None, short)
    assert unnumbered_next(pr, 6, set(), "https://spe.nknu.edu.tw/default.aspx") == (
        None, "is another page (/newslist.aspx)")
    no_next = read(window().replace(NEXT, "<a id=\"MasterGrid_lbnNext\" disabled=\"disabled\">下一頁</a>"))
    assert unnumbered_next(no_next, 6, set(), POSTED) == (None, "shows no page number and no 「下一頁」 to press")
    no_rows = read(window(rows=""))
    assert unnumbered_next(no_rows, 6, set(), POSTED) == (None, "shows no page number and no rows")
    # a page that names its page, and a page_n pager's answer (ASP.NET's numeric pager always marks the page shown),
    # are not judged here
    assert unnumbered_next(read(P1, step=2), 2, set(), POSTED) == (None, "")
    page_n = read_page(profiles.CNKNU, CNKNU_LIST, fx("cnknu_list_p2.html").replace("<span>2</span>", "2"),
                       site="c.nknu.edu.tw/aca", list_url=CNKNU_LIST, step=2)
    assert (page_n.pager_how, page_n.current) == ("page_n", None)
    assert unnumbered_next(page_n, 2, set(), CNKNU_LIST) == (None, "")


def test_answer_elsewhere_compares_with_the_address_pressed():
    pr = read(window())
    assert answer_elsewhere(pr, POSTED) is None
    assert answer_elsewhere(pr, "https://spe.nknu.edu.tw/newslist.aspx?nc=1") is None  # IIS: any case
    # tecs answers some list pages' GETs with Default.aspx's page (its form posts there from page 1 on): a press of it
    # goes to Default.aspx, whose answer posting back there is that same list's page
    moved = read(window().replace("./NewsList.aspx?NC=1", "./Default.aspx?pn=79&amp;sn=38"),
                 url="https://tecs.nknu.edu.tw/page.aspx?pn=79&sn=38")
    assert answer_elsewhere(moved, "https://tecs.nknu.edu.tw/Default.aspx?pn=79&sn=38") is None
    assert answer_elsewhere(moved, "https://tecs.nknu.edu.tw/page.aspx?pn=79&sn=38") == "is another page (/default.aspx)"
    home = read("<html><body><form id='form1' action='./default.aspx'><p>首頁</p></form></body></html>")
    assert answer_elsewhere(home, POSTED) == "holds no list"


# ---------------------------------------------------------------- the walk (§7.5)

def test_walk_goes_on_through_pages_without_their_number(world):
    row, _got = world.stored(SPE_LIST, SPE, P1)
    a, target = world.press(QUEUE)
    assert (a["step"], a["row_key"], target.url) == (2, "p2", POSTED)
    world.answer(a, target.url, window())
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["pages"], lw["rows_unique"]) == ("walking", 2, 4)
    assert lw["note"] == "page 2 shows no page number: taken as asked (下一頁)"
    assert world.f.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0] == UNNUMBERED
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"
    b, target = world.press(QUEUE)
    assert (b["step"], b["row_key"], b["target"]) == (3, "p3", "MasterGrid$ctl23$lbnNext")
    assert b["form_src_offset"] == world.actions(row["id"], kind="pager")[0]["warc_offset"]  # built from that answer
    assert dict(target.data)["__VIEWSTATE"] == "VS-SPE-P6"
    world.answer(b, target.url, fx("spe_list_last.html").replace(">2</span>", ">3</span>"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"], lw["pages"], lw["rows_unique"]) == ("last_page", 1, 3, 5)
    assert world.f.q("SELECT reason FROM action WHERE id=?", (b["id"],))[0][0] is None  # it named its page


def test_a_list_whose_get_posts_to_another_program_walks_on_there(world):
    """tecs answers some list pages' GETs with Default.aspx's page (round one: 12 lists, 32 of their pages, e.g.
    page.aspx?pn=79&sn=38): their presses go to Default.aspx, and an answer that names no page is still that list's."""
    url = "https://tecs.nknu.edu.tw/page.aspx?pn=79&sn=38"
    row, _got = world.stored(url, "tecs.nknu.edu.tw", P1.replace("./NewsList.aspx?NC=1", "Default.aspx?pn=79&amp;sn=38"))
    a, target = world.press(QUEUE)
    assert target.url == "https://tecs.nknu.edu.tw/Default.aspx?pn=79&sn=38"
    world.answer(a, target.url, window().replace("./NewsList.aspx?NC=1", "./Default.aspx?pn=79&amp;sn=38"))
    assert world.walk(row["id"])["ended_by"] == "walking"
    assert world.f.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0] == UNNUMBERED


def test_unnumbered_answer_repeating_walked_rows_is_mismatch_page(world):
    row, _got = world.stored(SPE_LIST, SPE, P1)
    a, target = world.press(QUEUE)
    world.answer(a, target.url, window(rows=P1_ROWS))  # page 1's rows again, no page number: not page 2
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"]) == ("mismatch:page", 0)
    assert lw["note"] == "asked for page 2, the answer shows no page number and 2 of 2 rows walked already"
    assert world.chains.next_action(QUEUE) is None
    assert world.f.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0] is None


def test_answer_from_another_page_says_so(world):
    row, _got = world.stored(SPE_LIST, SPE, P1)
    a, target = world.press(QUEUE)
    world.answer(a, target.url, window().replace("./NewsList.aspx?NC=1", "Default.aspx?NC=1"))
    lw = world.walk(row["id"])
    elsewhere = "asked for page 2, the answer is another page (/default.aspx)"
    assert (lw["ended_by"], lw["note"]) == ("mismatch:page", elsewhere)
    # tecs's publish.aspx: page 2 answered with Default.aspx's own list, which shows its page 1. Its 「下一頁」 one page
    # short of the page asked for is another list's, never this list's last page (it used to end mismatch:no_next)
    url2 = "https://spe.nknu.edu.tw/newslist.aspx?nc=2"
    row2, _got = world.stored(url2, SPE, P1.replace("NC=1", "NC=2"))
    b, target = world.press(QUEUE)
    world.answer(b, target.url, P1.replace("./NewsList.aspx?NC=1", "Default.aspx?NC=2"))
    lw = world.walk(row2["id"])
    assert (lw["ended_by"], lw["ok"], lw["note"]) == ("mismatch:page", 0, f"{elsewhere}, showing 1")


def test_answer_from_another_page_that_shows_the_page_asked_for_ends_the_walk_too(world):
    """A Server.Transfer whose other program applies the posted Page$N to its own grid shows the very number asked for
    (review of 11-09): its rows are that program's list, never page 2 of this one. The walk ends mismatch:page, its
    note saying why, as an answer from elsewhere that shows another number does; nothing of it counts."""
    row, _got = world.stored(SPE_LIST, SPE, P1)
    a, target = world.press(QUEUE)
    last = fx("spe_list_last.html")  # shows page 2 (its <span>), 「下一頁」 disabled
    assert "./NewsList.aspx?NC=1" in last and read(last, step=2).current == 2
    world.answer(a, target.url, last.replace("./NewsList.aspx?NC=1", "./Default.aspx?NC=1"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"], lw["pages"], lw["rows"]) == ("mismatch:page", 0, 1, 2)
    assert lw["note"] == "asked for page 2, the answer is another page (/default.aspx), showing 2"


def test_answer_without_the_list_says_so(world):
    """pe's album list answered with the site's home page (no form, no grid), tecs's activeviewlist.aspx answered
    with itself but no list drawn: both hold no list."""
    no_grid = re.sub(r'<table id="MasterGrid">.*?</table>', "", window(), flags=re.DOTALL)
    assert "MasterGrid" not in no_grid
    home = "<html><body><div id='lbl_ADJHtml'><p>歡迎光臨本系網站</p></div></body></html>"
    for n, answer in enumerate((no_grid, home), 1):
        url = f"https://spe.nknu.edu.tw/newslist.aspx?nc={n}"
        row, _got = world.stored(url, SPE, P1.replace("NC=1", f"NC={n}"))
        a, target = world.press(QUEUE)
        world.answer(a, target.url, answer)
        lw = world.walk(row["id"])
        assert (lw["ended_by"], lw["ok"], lw["note"]) == ("mismatch:page", 0, "asked for page 2, the answer holds no list"), n


def test_page_n_answer_without_its_number_stays_mismatch_page(world):
    row, _got = world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_list_p1.html"))
    a, target = world.press(QUEUE)
    assert dict(target.data)["__EVENTARGUMENT"] == "Page$2"
    world.answer(a, target.url, fx("cnknu_list_p2.html").replace("<span>2</span>", "2"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["note"]) == ("mismatch:page", "asked for page 2, the answer shows no page number")
    assert not world.f.q("SELECT 1 FROM action WHERE reason=?", (UNNUMBERED,))
    # SSO's list answered with its page 1 again: the page it shows, as before
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    a, target = world.press(QUEUE)
    world.answer(a, target.url, fx("sso_list_p1.html"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["note"]) == ("mismatch:page", "asked for page 2, the answer shows 1")


# ---------------------------------------------------------------- putting walks back

def _three_walks(w: World) -> tuple[int, int, int]:
    """Three lists: one ended mismatch:page, one post_failed, one last_page."""
    page, _got = w.stored(SPE_LIST, SPE, P1)
    a, target = w.press(QUEUE)
    w.answer(a, target.url, window(rows=P1_ROWS))
    failed, _got = w.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    b, _target = w.press(QUEUE)
    assert w.chains.failed(b, "timeout").restart
    again, snap = w.page(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    w.chains.page_stored(again, fx("sso_list_p1.html"), w.warc, snap.warc_offset)
    c, target = w.press(QUEUE)
    w.answer(c, target.url, "busy", status=503)
    done, _got = w.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_list_p1.html"))
    d, target = w.press(QUEUE)
    w.answer(d, target.url, fx("cnknu_list_p2.html"))
    ends = [w.walk(i)["ended_by"] for i in (page["id"], failed["id"], done["id"])]
    assert ends == ["mismatch:page", "post_failed", "last_page"]
    return page["id"], failed["id"], done["id"]


def test_retry_walks_puts_back_only_that_end(world):
    page, failed, done = _three_walks(world)
    before = {i: (world.f.row_by_id(i)["state"], world.walk(i)["ended_by"]) for i in (failed, done)}
    assert world.chains.retry_walks("mismatch:page") == 1
    back = world.f.row_by_id(page)
    assert (back["state"], back["reason"], back["prio"], back["next_at"]) == ("pending", "chain_restart", 0, 0)
    lw = world.walk(page)
    assert (lw["ended_by"], lw["ok"], lw["note"]) == ("walking", None, "restart: retry mismatch:page")
    assert {i: (world.f.row_by_id(i)["state"], world.walk(i)["ended_by"]) for i in (failed, done)} == before
    # its GET again opens epoch 2 and presses page 2 again
    row, snap = world.page(SPE_LIST, SPE, P1)
    world.chains.page_stored(row, P1, world.warc, snap.warc_offset)
    pager = world.actions(page, epoch=2, kind="pager")
    assert [(x["row_key"], x["state"]) for x in pager] == [("p2", "pending")]
    assert world.chains.retry_post_failed() == 1  # the other end, as before
    assert world.f.row_by_id(failed)["state"] == "pending" and world.f.row_by_id(done)["state"] == "done"


def test_the_retry_mismatch_page_command(tmp_path):
    w = World(tmp_path)
    page, failed, _done = _three_walks(w)
    w.sink.close()
    w.f.close()
    run = CliRunner()
    lock = hold_run_lock(tmp_path)
    try:
        got = run.invoke(app, ["archive", "retry", "mismatch:page", "--dir", str(tmp_path)])
    finally:
        lock.close()
    assert got.exit_code == 1 and "archive run 正在用这个目录" in got.output
    got = run.invoke(app, ["archive", "retry", "mismatch:page", "--sites", SPE, "--dir", str(tmp_path)])
    assert got.exit_code == 1 and "--sites 只给 unpressed 用" in got.output
    f = Frontier(tmp_path / "frontier.db")
    assert f.row_by_id(page)["state"] == "done"  # nothing was put back while refused
    f.close()
    got = run.invoke(app, ["archive", "retry", "mismatch:page", "--dir", str(tmp_path)])
    assert got.exit_code == 0 and "从第 1 页重走：1 个" in got.output, got.output
    f = Frontier(tmp_path / "frontier.db")
    assert f.row_by_id(page)["state"] == "pending" and f.row_by_id(failed)["state"] == "done"
    assert f.q("SELECT ended_by FROM listwalk WHERE list_id=?", (page,))[0][0] == "walking"
    f.close()
