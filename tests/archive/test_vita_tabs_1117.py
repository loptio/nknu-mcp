"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.3, §八): a teacher's CV
page (dataopen2search/vita.aspx) shows one tab of 16 at a time (學經歷 by default); each tab is a postback of the
page's menu (ctl00$phMain$Menu1, argument a number or a name). Its platform rule presses every tab as a row's detail
(the row set vita_tabs: the menu's items; the answer's selected tab must be the one pressed). The report control's
toolbar is never pressed (the tabs hold its words). The school now and then answers a CV page with no card at all
(#ctl00_phMain_divVita missing: the content session 11-17, unid=1732 and 4906, never the same page twice): a pressed
tab answered so is no answer of that tab (title mismatch: the chain starts again once, else 要人看), never a tab with
nothing in it. Made-up pages, O-masked names."""

from __future__ import annotations

from nknu.archive import postback, profiles

URL = "https://sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=1"
SITE = "sso.nknu.edu.tw/dataopen2search"
TABS = [("0", "學經歷"), ("1", "校內職務"), ("Journalpaper", "期刊論文"), ("10", "授課課程")]


def _page(selected: str = "0", card: bool = True) -> str:
    items = "".join(
        f"<li><a class=\"level1{' selected' if arg == selected else ''}\" href=\"#\" "
        f"onclick=\"__doPostBack('ctl00$phMain$Menu1','{arg}')\">{word}</a></li>" for arg, word in TABS)
    inner = (f"<div id=\"ctl00_phMain_divVita\"><div class=\"form-group\">中文姓名 王ＯＯ</div>"
             f"<div id=\"ctl00_phMain_updpatePanel2\"><div id=\"ctl00_phMain_Menu1\" class=\"mb-3\">"
             f"<ul class=\"level1 menuTab\">{items}</ul></div><table><tr><td>教學內容一則</td></tr></table></div></div>"
             if card else "<div class=\"container\">系統訊息</div>")
    return ("<html><head><title>教師資料</title></head><body><form id=\"uForm\" method=\"post\" action=\"vita.aspx\">"
            "<input type=\"hidden\" name=\"__VIEWSTATE\" value=\"x\"/>"
            f"{inner}<a href=\"#\" onclick=\"__doPostBack('ctl00$phMain$ReportViewer1$ctl03','')\">匯出</a>"
            "<a href=\"#\" onclick=\"__doPostBack('ctl00$btnLangE','')\">Change To English</a></form></body></html>")


def test_every_tab_of_a_cv_page_is_pressed_as_a_detail():
    pr = postback.read_page(profiles.DATAOPEN, URL, _page(), site=SITE)
    got = [(p.kind, p.target, p.argument, p.text, p.rowset) for p in pr.presses]
    assert got == [("detail", "ctl00$phMain$Menu1", arg, word, "vita_tabs") for arg, word in TABS]
    assert pr.rowset is None and pr.walk == "none"  # no list: the page is no list for its tabs
    assert not pr.unknown and not pr.unplanned  # the report's toolbar and the language button are decided


def test_a_tab_answer_shows_the_tab_pressed():
    rs = next(r for r in profiles.DATAOPEN.rows if r.name == "vita_tabs")
    assert postback.title_ok("期刊論文", _page("Journalpaper"), rs.detail_title, exclude=rs.grid)
    assert not postback.title_ok("期刊論文", _page("1"), rs.detail_title, exclude=rs.grid)  # another tab came back


def test_a_tab_answered_with_no_card_is_no_answer_of_that_tab():
    rs = next(r for r in profiles.DATAOPEN.rows if r.name == "vita_tabs")
    assert not postback.title_ok("期刊論文", _page(card=False), rs.detail_title, exclude=rs.grid)


def test_a_cv_page_needs_its_card_and_other_dataopen_pages_do_not():
    assert profiles.area_missing(URL, _page(card=False)) is True
    assert profiles.area_missing(URL, _page()) is False
    lost = "https://sso.nknu.edu.tw/dataopen2search/lostandfound.aspx"
    assert profiles.area_missing(lost, "<html><body>失物招領</body></html>") is None  # no card required there
    assert profiles.area_missing("https://c.nknu.edu.tw/aca/about.aspx", "<html></html>") is None


# ---------------------------------------------------------------- a CV page answered with no card, by GET


def _crawl(tmp_path, answers: list[str]):
    from tests.archive.test_archive import seed, snap
    from tests.archive.test_pilot_fixes import make, work

    pages = [snap(URL, 200, html=h) for h in answers]
    crawler, net, clock = make(tmp_path, {("GET", "/dataopen2search/vita.aspx"): lambda target: pages.pop(0)})
    seed(crawler, URL)
    queue = "ip:sso.nknu.edu.tw"
    f = crawler.frontier
    for _ in answers:
        work(crawler, net, queue)
        clock.t += 7200
    return f, f.row(URL)


def test_a_cv_page_with_no_card_is_tried_again_later(tmp_path):
    _f, row = _crawl(tmp_path, [_page(card=False)])
    assert (row["state"], row["reason"], row["attempts"]) == ("pending", "no_area", 1)
    assert "no_area" in (row["flags"] or "").split(",") and row["next_at"] > 0


def test_a_cv_page_good_again_is_got(tmp_path):
    _f, row = _crawl(tmp_path, [_page(card=False), _page()])
    assert row["state"] == "done" and "no_area" not in (row["flags"] or "")


def test_a_cv_page_with_no_card_three_times_is_kept_and_judged_thin(tmp_path):
    from nknu.archive import outcome

    f, row = _crawl(tmp_path, [_page(card=False)] * 3)
    assert row["state"] == "done" and "no_area" in (row["flags"] or "").split(",")
    outcome.create_views(f.conn, temp=True)
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == "thin"


def test_the_export_falls_back_on_the_last_good_answer_of_a_page_that_came_back_without_its_area(tmp_path):
    from nknu.archive import export
    from tests.archive.test_export_1027 import BASE
    from tests.archive.test_export_prev_1116 import _again, _content
    from tests.archive.test_export_step3_1108 import _with

    def build(arch):
        _again(arch, "about", body="<html><body>系統訊息</body></html>", text_chars=4, flags="no_area")

    _arch, _got, out, rows, _readme = _with(tmp_path, build)
    r = rows[BASE + "about.aspx"]
    assert r["来源"] == export.ORIGIN_PREV and "教務處簡介" in _content(out, r).decode("utf-8-sig")


def test_no_area_back_marks_and_puts_back_a_page_the_school_answered_without_its_area(tmp_path):
    """The pages a night stored as got while the school answered them with no card (unid=1732, 4906 on the lab, 11-17):
    flagged no_area and put back for the next run; a good one and a page no profile requires an area of are left."""
    from nknu.archive import batch
    from tests.archive.test_export_1027 import Arch

    a = Arch(tmp_path).open()
    try:
        own = {"host": "sso.nknu.edu.tw", "site": "english.nknu.edu.tw", "owner_site": "english.nknu.edu.tw", "tier": 1}
        bad = a.page("bad", URL, _page(card=False), **own)
        good = a.page("good", URL.replace("unid=1", "unid=2"), _page(), **own)
        a.page("aca", "https://c.nknu.edu.tw/aca/x.aspx", "<html><body></body></html>")
        assert batch.no_area_back(a.f, tmp_path, write=False) == {"english.nknu.edu.tw": 1}
        assert a.f.row_by_id(bad)["state"] == "done"
        assert batch.no_area_back(a.f, tmp_path, write=True) == {"english.nknu.edu.tw": 1}
        r = a.f.row_by_id(bad)
        assert (r["state"], r["reason"], r["attempts"]) == ("pending", "no_area", 0)
        assert "no_area" in (r["flags"] or "").split(",")
        assert a.f.row_by_id(good)["state"] == "done"
        assert batch.no_area_back(a.f, tmp_path, write=True) == {}  # once
    finally:
        a.close()


def test_update_no_area_counts_then_puts_back(tmp_path, capsys):
    from nknu.archive import update
    from tests.archive.test_export_1027 import Arch

    a = Arch(tmp_path).open()
    bad = a.page("bad", URL, _page(card=False), host="sso.nknu.edu.tw", site="english.nknu.edu.tw",
                 owner_site="english.nknu.edu.tw", tier=1)
    a.close()
    out = tmp_path / "sites.txt"
    assert update.main(["--dir", str(tmp_path), "no-area", "--dry-run", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "english.nknu.edu.tw\n"
    assert update.main(["--dir", str(tmp_path), "no-area"]) == 0
    from nknu.archive.frontier import Frontier

    g = Frontier(tmp_path / "frontier.db")
    try:
        assert g.row_by_id(bad)["state"] == "pending"
    finally:
        g.close()


# ---------------------------------------------------------------- the tabs of an answer are no new presses (11-17)


def test_a_cv_pages_tabs_are_pressed_once_each_and_the_walk_ends(tmp_path):
    """The lab 11-17: each tab's answer shows the 16 tabs again; read as the answer's own buttons, keyed by the tab
    pressed, every answer planned 15 presses more (unid=3131: 50,521 presses planned in 100 minutes, 3,367 sent). A
    detail's answer never plans again the row set its press was of: the page planned every row of it."""
    from tests.archive.test_archive import seed, snap
    from tests.archive.test_pilot_fixes import make, work

    def press(target):
        return snap(target.url, 200, html=_page(dict(target.data)["__EVENTARGUMENT"]))

    crawler, net, clock = make(tmp_path, {("GET", "/robots.txt"): snap("x", 404),
                                          ("GET", "/dataopen2search/vita.aspx"): snap(URL, 200, html=_page()),
                                          ("POST", "/dataopen2search/vita.aspx"): press})
    seed(crawler, URL)
    for _ in range(30):  # a press waits its gap: the clock moves on between the worker's turns
        work(crawler, net, "ip:sso.nknu.edu.tw", rounds=5)
        clock.t += 60
    f = crawler.frontier
    posts = [u for m, u in net.seen if m == "POST"]
    assert len(posts) == len(TABS), posts
    acts = f.q("SELECT argument, state FROM action WHERE kind='detail' ORDER BY id")
    assert sorted((a["argument"], a["state"]) for a in acts) == sorted((arg, "done") for arg, _w in TABS)
    from nknu.archive import postback as pb
    assert not f.q("SELECT 1 FROM page_res WHERE why=?", (pb.UNPRESSED,))  # the tabs the page planned are no new ones


def test_a_tab_only_an_answer_shows_is_written_unpressed_never_dropped_unseen(tmp_path):
    """爬虫judge 11-17: 「被丢掉的新键至少要留个纪录，不能悄悄消失」. A button of the pressed row set that the page itself
    never planned (here: a fifth tab only the answers show) is not pressed from the answer; it is written on the page as
    a button nobody presses (要人看). The ledger held no such case before 11-17 (sibling_check_1117.py: 0)."""
    from nknu.archive import postback as pb
    from tests.archive.test_archive import seed, snap
    from tests.archive.test_pilot_fixes import make, work

    extra = ("Patent2", "新專利")
    first = list(TABS)
    try:
        def press(target):
            TABS[:] = [*first, extra]  # every answer shows the fifth tab, the page itself does not
            try:
                return snap(target.url, 200, html=_page(dict(target.data)["__EVENTARGUMENT"]))
            finally:
                TABS[:] = first

        crawler, net, clock = make(tmp_path, {("GET", "/robots.txt"): snap("x", 404),
                                              ("GET", "/dataopen2search/vita.aspx"): snap(URL, 200, html=_page()),
                                              ("POST", "/dataopen2search/vita.aspx"): press})
        seed(crawler, URL)
        for _ in range(30):
            work(crawler, net, "ip:sso.nknu.edu.tw", rounds=5)
            clock.t += 60
        f = crawler.frontier
        assert len([u for m, u in net.seen if m == "POST"]) == len(first)
        key = pb.button_key("ctl00$phMain$Menu1", extra[0])
        got = f.q("SELECT why, text FROM page_res WHERE res_key=?", (key,))
        assert [(r["why"], r["text"]) for r in got] == [(pb.UNPRESSED, extra[1])]
    finally:
        TABS[:] = first
