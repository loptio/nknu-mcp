"""Periodic updates, stage 1: the fix pass after the review of 7f12e29..8474164 (workflow wf_e0ee1463-d6e, verdicts in
research/crawler_design_2026-11-16/review1_1116.md). Each test reproduces one finding the skeptics confirmed; made-up
lists in the shape of the platforms' (test_postback.TEST_PROFILE: rows pressed, pages pressed)."""

from __future__ import annotations

import pytest

from nknu.archive import postback, profiles, scope
from nknu.archive.links import extract
from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import settle
from tests.archive.test_postback import CNKNU_LIST, TEST_PROFILE, World, fx
from tests.archive.test_tables_1106 import _page as table_page

URL = "https://test.nknu.edu.tw/list.aspx"
SITE = "test.nknu.edu.tw"
Q = "ip:140.127.40.173"


def page(titles: list[str], cur: int, last: int) -> str:
    """A page of a list whose rows are pressed (detail) and whose pages are pressed (Page$N)."""
    rows = "".join(f"<tr><td><a class='t' href=\"javascript:__doPostBack('g$ctl{i + 2:02d}$lnk','')\">{t}</a></td></tr>"
                   for i, t in enumerate(titles))
    cells = "".join(f"<td><span>{p}</span></td>" if p == cur else
                    f"<td><a href=\"javascript:__doPostBack('g','Page${p}')\">{p}</a></td>" for p in range(1, last + 1))
    return ("<form id='form1' method='post' action='./list.aspx'><input type='hidden' name='__VIEWSTATE' value='v'>"
            f"<table id='g'>{rows}<tr><td><table><tr>{cells}</tr></table></td></tr></table></form>")


ROUND1 = {1: ["甲甲甲", "乙乙乙"], 2: ["丙丙丙", "丁丁丁"], 3: ["戊戊戊"]}
NEWER = {1: ["新新新", "甲甲甲"], 2: ["乙乙乙", "丙丙丙"], 3: ["丁丁丁", "戊戊戊"]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", (*profiles.PROFILES, TEST_PROFILE))
    w = World(tmp_path)
    yield w
    w.f.close()


def _answer(world, a, pages):
    t = world.chains.target_for(a)
    if a["kind"] == "pager":
        world.answer(a, t.url, page(pages[a["step"]], a["step"], len(pages)))
    else:
        world.answer(a, t.url, f"<html><body><h1>{a['row_text']}</h1></body></html>")


def drain(world, pages, fail=frozenset()):
    """Press what is pending (rows before pages); a row whose title is in `fail` answers 503; a restart's GET is
    answered with page 1 as the crawler would. Returns (kind, step, title) pressed."""
    done = []
    for _ in range(60):
        a = world.chains.next_action(Q)
        if a is None:
            return done
        done.append((a["kind"], a["step"], a["row_text"]))
        if a["kind"] == "detail" and a["row_text"] in fail:
            if world.chains.failed(a, "http_503").restart:
                world.stored(URL, SITE, page(pages[1], 1, len(pages)))
            continue
        _answer(world, a, pages)
    raise AssertionError("did not drain")


def whole(world) -> int:
    row, _ = world.stored(URL, SITE, page(ROUND1[1], 1, 3))
    drain(world, ROUND1)
    assert world.walk(row["id"])["ended_by"] == "last_page"
    return row["id"]


def look(world, lid: int, pages: dict) -> None:
    world.now += 86400
    assert world.chains.look(lid)
    world.stored(URL, SITE, page(pages[1], 1, len(pages)))


def _look_row(world, lid):
    return world.f.q("SELECT * FROM list_look WHERE list_id=?", (lid,))[0]


# ---------------------------------------------------------------- critical: one reading as a table of content


def test_a_looks_reading_as_a_table_of_content_keeps_the_walk(world):
    """crawler._page calls not_a_list for a Stored(content): never for a look's reading, whose one answer proves
    nothing (the list page answered a maintenance line in its grid)."""
    row, _ = world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_list_p1.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("cnknu_list_p2.html"))
    lid = row["id"]
    world.f.x("UPDATE url SET page_role='list' WHERE id=?", (lid,))
    world.now += 86400
    assert world.chains.look(lid)
    html = table_page("<tr><th>標題</th></tr><tr><td>系統維護中，請稍後再試</td></tr>", "./newslist.aspx?gi=1")
    row2, got_snap = world.page(CNKNU_LIST, "c.nknu.edu.tw/aca", html)
    links = extract(html, CNKNU_LIST, profiles.content_css(CNKNU_LIST))
    got = world.chains.page_stored(row2, html, world.warc, got_snap.warc_offset, links)
    assert not got.content  # so the crawler never calls not_a_list for it
    assert _look_row(world, lid)["ended_by"] == "no_list"
    assert world.walk(lid)["ended_by"] == "last_page" and world.f.row_by_id(lid)["page_role"] == "list"


# ---------------------------------------------------------------- important: what a look's failures leave


def test_a_looks_failed_row_press_stays_failed_and_visible(world):
    """A new notice whose detail press fails (twice) keeps its failures (the school's error, retried by the rules of
    any walk), never superseded at the look's close (read as a crawler loss)."""
    lid = whole(world)
    look(world, lid, NEWER)
    drain(world, NEWER, fail={"新新新"})
    assert _look_row(world, lid)["ended_by"] == postback.SEEN_PAGE
    states = {r["state"] for r in world.f.q("SELECT state FROM action WHERE list_id=? AND row_text='新新新'", (lid,))}
    assert states == {"failed"}
    res = {r["result"] for r in world.f.q("SELECT result FROM page_resource WHERE page_id=?", (lid,))}
    assert "not_discovered" not in res


def test_a_press_the_look_left_pending_restarts_the_look_never_a_whole_walk(world):
    """The look ended (page 2 brought nothing) with a new row's press still waiting; that press fails: the chain
    starts again as the same look (reopened), never a whole walk that overwrites the last one's record."""
    lid = whole(world)
    walk_before = tuple(world.walk(lid))
    look(world, lid, NEWER)
    pager = world.f.q("SELECT * FROM action WHERE list_id=? AND kind='pager' AND state='pending'", (lid,))[0]
    _answer(world, pager, NEWER)  # page 2 first: nothing new there, the look ends
    assert _look_row(world, lid)["ended_by"] == postback.SEEN_PAGE
    row_press = world.f.q("SELECT * FROM action WHERE list_id=? AND kind='detail' AND state='pending'", (lid,))[0]
    assert world.chains.failed(row_press, "http_503").restart
    assert tuple(world.walk(lid)) == walk_before
    assert _look_row(world, lid)["ended_at"] is None  # the look again, from its GET


def test_a_run_stopped_with_a_looks_press_waiting_resumes_the_look(world):
    lid = whole(world)
    walk_before = tuple(world.walk(lid))
    look(world, lid, NEWER)
    pager = world.f.q("SELECT * FROM action WHERE list_id=? AND kind='pager' AND state='pending'", (lid,))[0]
    _answer(world, pager, NEWER)
    world.now += 3600
    assert world.chains.resume() == 1  # the row press left waiting
    assert tuple(world.walk(lid)) == walk_before and _look_row(world, lid)["ended_at"] is None


def test_a_get_look_closed_short_keeps_the_pages_failed_presses_of_round_one(tmp_path):
    w = World(tmp_path)
    try:
        site, base = "sso.nknu.edu.tw/web/31", ("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&"
                                                "selpage=MoreNews&sort=date")
        p1, _ = w.stored(base + "&pageNum=1", site, _webidx([(9, "甲", "2026-10-20")], 1))
        w.stored(base + "&pageNum=2", site, _webidx([(5, "乙", "2026-09-01")], 2))
        w.f.x("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
              "form_src_offset, target, row_key, state, reason) VALUES (?, 1, 1, 'download', 'q', 'sso.nknu.edu.tw', "
              "?, 'sso_webidx/x', 'w', 1, 't', 'k', 'failed', 'http_503')", (p1["id"], site))
        assert w.chains.look_get(p1["id"])
        w.f.finish(p1["id"], state="done", status=404, reason="http_404")
        assert w.chains.close_stale_looks() == 1
        assert w.f.q("SELECT state, reason FROM action WHERE row_key='k'")[0][:] == ("failed", "http_503")
    finally:
        w.f.close()


def _webidx(rows, n, last=2):
    base = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
    body = "".join(f"<div class='news-row'><div class='col-date'>{d}</div><div class='col-cat'>公告</div>"
                   f"<div class='col-title'><a href='default.aspx?webidx=31&newsidx={i}'>{t}</a></div></div>"
                   for i, t, d in rows)
    nums = "".join(f"<a href='{base}&pageNum={p}'>{p}</a>" for p in range(1, last + 1) if p != n)
    nxt = f"<a href='{base}&pageNum={n + 1}'>下一頁</a>" if n < last else ""
    return ('<html><head><title>單位網站</title></head><body><form id="form1" method="post" '
            'action="/services/web/default.aspx?webidx=31"><main><div class="news-view-container">'
            f"<div class='news-list-wrapper'>{body}</div><div class='news-pagination'>{nums}{nxt}</div></div>"
            "</main></form></body></html>")


def test_an_edited_notice_moved_to_another_page_of_a_get_list_is_fetched_again(tmp_path):
    """A list paged by GET keeps its rows page by page: a notice the unit edits (new title, new date) and that so
    moves from page 2 to page 1 is the same row changed, its detail fetched again."""
    w = World(tmp_path)
    try:
        site = "sso.nknu.edu.tw/web/31"
        base = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
        detail = scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&newsidx=5")
        p1, _ = w.stored(base + "&pageNum=1", site, _webidx([(9, "新公告甲", "2026-10-20")], 1))
        p2, _ = w.stored(base + "&pageNum=2", site, _webidx([(5, "舊公告", "2026-09-01")], 2))
        for src, dst, text in ((p1, p2, "2"), (p2, p1, "1")):  # the pager links the crawler writes (_parts)
            w.f.add_page_res(src["id"], [{"res_key": dst["url"], "how": "get", "role": "pager", "need": "must",
                                          "res_id": dst["id"], "text": text}])
        w.f.add(detail, host="sso.nknu.edu.tw", site=site, unit=None, tier=1, kind="page", depth=1,
                found_via="link", found_from=None, owner_site=site, kind0="page")
        did = w.f.row(detail)["id"]
        w.f.finish(did, state="done", status=200, kind="page", warc="warc/x.warc.gz", warc_offset=1)
        w.now += 86400
        w.stored(base + "&pageNum=1", site, _webidx([(5, "舊公告（更新）", "2026-11-16"), (9, "新公告甲", "2026-10-20")], 1))
        assert w.f.row_by_id(did)["state"] == "pending"
    finally:
        w.f.close()


# ---------------------------------------------------------------- re-graded by effect (a whole list walked, or a gap)


def test_a_new_pinned_row_is_news_on_its_first_page_only(world):
    """A new pinned notice repeats on every page: news on page 1, an old row of this walk on the next pages."""
    lid = whole(world)
    pinned = {n: ["置頂新", *rows] for n, rows in ROUND1.items()}
    look(world, lid, pinned)
    pressed = drain(world, pinned)
    assert [s for k, s, _t in pressed if k == "pager"] == [2]  # page 2 pressed, page 3 not
    assert _look_row(world, lid)["ended_by"] == postback.SEEN_PAGE


def test_a_look_whose_next_page_failed_twice_before_ends_at_once(world):
    """_press leaves a page that failed twice to a person: the look ends there (pager_left), never left open with
    nothing to press (then closed 'not got' and a warning every night)."""
    lid = whole(world)
    for epoch in (7, 8):
        world.f.x("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
                  "form_src_offset, target, argument, row_key, state, reason) VALUES (?, ?, 2, 'pager', ?, "
                  "'test.nknu.edu.tw', ?, 'test/g', 'w', 1, 'g', 'Page$2', 'p2', 'failed', 'http_503')",
                  (lid, epoch, Q, SITE))
    walk_before = tuple(world.walk(lid))
    look(world, lid, NEWER)
    assert _look_row(world, lid)["ended_by"] == "pager_left"
    assert tuple(world.walk(lid)) == walk_before
    assert not world.f.q("SELECT 1 FROM action WHERE list_id=? AND kind='pager' AND state='pending'", (lid,))


# ---------------------------------------------------------------- GET looks: news read from the page's own rows


HOST = "a.nknu.edu.tw"
LIST = f"https://{HOST}/news/list.aspx"


class School:
    def __init__(self, scheme: str = "https", sidebar: int | None = None):
        self.pages = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9]}
        self.scheme, self.sidebar = scheme, sidebar

    def html(self, n: int) -> str:
        link = lambda i: f"{self.scheme}://{HOST}/news/view.aspx?id={i}"
        items = "".join(f"<li><a href='{link(i)}'>公告 {i}</a></li>" for i in self.pages[n])
        side = f"<aside><a href='{link(self.sidebar)}'>最新公告</a></aside>" if self.sidebar else ""
        pager = "".join(f"<a href='{LIST}{'' if p == 1 else f'?page={p}'}'>{p}</a> " for p in (1, 2, 3) if p != n)
        if n < 3:
            pager += f"<a href='{LIST}?page={n + 1}'>下一頁</a>"
        return f"<html><head><title>消息</title></head><body><ul>{items}</ul>{side}<div>{pager}</div></body></html>"

    def routes(self):
        def lst(url):
            return snap(url, 200, html=self.html(int(url.rsplit("=", 1)[1]) if "?page=" in url else 1))
        return {"robots.txt": snap("x", 404), "/news/view.aspx": lambda u: snap(u, 200, html="<html>內文</html>"),
                "/news/list.aspx": lst}


def _get_night(tmp_path, school: School, change) -> list[str]:
    crawler, net, clock = make(tmp_path, school.routes())
    if school.scheme == "http":
        crawler.frontier.set_host(HOST, queue=f"ip:{HOST}", scheme_only="https")
    seed(crawler, LIST)
    settle(crawler, net, [f"ip:{HOST}"])
    change(school)
    clock.t += 86400
    assert crawler.chains.look_get(crawler.frontier.row(LIST)["id"])
    seen = len(net.seen)
    settle(crawler, net, [f"ip:{HOST}"])
    return [u for u in net.seen[seen:] if "/news/list.aspx" in u]


def _new_notice(school: School) -> None:
    school.pages = {1: [10, 1, 2], 2: [3, 4, 5], 3: [6, 7, 8, 9]}


def test_a_new_notice_linked_from_every_page_of_the_template_is_not_news_everywhere(tmp_path):
    school = School(sidebar=3)

    def change(s):
        _new_notice(s)
        s.sidebar = 10  # tonight's notice also in a box on every page
    assert _get_night(tmp_path, school, change) == [LIST, f"{LIST}?page=2"]


def test_a_get_look_on_a_host_that_answers_one_scheme_still_reads_on(tmp_path):
    """The page writes its links in http, the host answers on https only: the rows of the links are the https ones."""
    assert _get_night(tmp_path, School(scheme="http"), _new_notice) == [LIST, f"{LIST}?page=2"]


# ---------------------------------------------------------------- page types: a strong sign counted once


def test_a_pages_strong_sign_is_counted_once_across_a_404(tmp_path):
    answers = [snap(LIST.replace("list", "spa"), 200, html="<html><body><div id='app'></div></body></html>"),
               snap(LIST.replace("list", "spa"), 404, html="<html></html>"),
               snap(LIST.replace("list", "spa"), 200, html="<html><body><div id='app'></div></body></html>")]
    url = LIST.replace("list", "spa")
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/news/spa.aspx": lambda u: answers.pop(0)})
    seed(crawler, url)
    for _ in range(3):
        settle(crawler, net, [f"ip:{HOST}"])
        crawler.frontier.put_back(crawler.frontier.row(url)["id"], "again")
        clock.t += 3600
    key = crawler.frontier.row(url)["page_type"]
    assert crawler.frontier.q("SELECT n_strong FROM page_type WHERE key=?", (key,))[0][0] == 1
