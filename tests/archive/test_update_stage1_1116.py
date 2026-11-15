"""Periodic update, stage 1 (design 2026-11-16-periodic-update-design.md §四, §五 row 1, §7.2, §7.3; plan
docs/superpowers/plans/2026-11-16-update-stage-1.md; the owner 11-16: 「接下来按照你的节奏走吧，不用等我点头了」): a
stored answer clears what the previous answer wrote; every list row read is kept (list_row); a look at a list stops
where a whole page holds nothing new, the full walk's record untouched (list_look); growth caps of an update run.
Made-up rows and pages only."""

from __future__ import annotations

import json

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import postback, scope
from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import settle
from tests.archive.test_postback import SSO_LIST, World, fx

HOST = "a.nknu.edu.tw"
QUEUE = f"ip:{HOST}"
URL = f"https://{HOST}/news-a.aspx"
PAGE = ("<html><head><title>測試公告</title></head><body><p>公告內文</p>"
        f"<a href='https://{HOST}/news-b.aspx'>另一則</a></body></html>")
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
ANSWER_COLS = ("flags", "title", "sniff", "filename", "text_chars", "n_links", "n_postbacks")


def _back(f, uid: int) -> None:
    f.x("UPDATE url SET state='pending', reason='chain_restart', prio=0, next_at=0 WHERE id=?", (uid,))


def _twice(tmp_path, second):
    """A page answered 200, then (put back, an hour later) answered `second`: the row after each."""
    answers = [snap(URL, 200, html=PAGE), second]
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/news-a.aspx": lambda u: answers.pop(0)})
    seed(crawler, URL)
    settle(crawler, net, [QUEUE])
    first = crawler.frontier.row(URL)
    crawler.frontier.x("UPDATE url SET flags='thin' WHERE id=?", (first["id"],))  # a mark of answer 1 (reconcile)
    _back(crawler.frontier, first["id"])
    clock.t += 3600
    settle(crawler, net, [QUEUE])
    return first, crawler.frontier.row(URL)


# ---------------------------------------------------------------- Task 1


def test_a_404_after_a_page_leaves_none_of_the_pages_columns(tmp_path):
    first, row = _twice(tmp_path, snap(URL, 404, html="<html><body>不在了</body></html>"))
    assert first["title"] == "測試公告" and first["sniff"] == "html" and first["n_links"] >= 1 and first["text_chars"]
    assert row["status"] == 404 and row["reason"] == "http_404"
    assert {k: row[k] for k in ANSWER_COLS} == dict.fromkeys(ANSWER_COLS)


def test_a_file_after_a_page_keeps_no_page_columns(tmp_path):
    _first, row = _twice(tmp_path, snap(URL, 200, body=PDF, headers={"content-type": "application/pdf"}))
    assert (row["status"], row["sniff"], row["kind"]) == (200, "pdf", "file")
    assert (row["title"], row["n_links"], row["n_postbacks"], row["text_chars"], row["flags"]) == (None,) * 5


# ---------------------------------------------------------------- Task 2: list_row (World: the chains, no crawler)

NEWS_SITE = "sso.nknu.edu.tw/news"
DETAIL2 = scope.canonical("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=AAAA000000000000000000000000000000000002")


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.f.close()


def _walk_whole(world, p1: str | None = None) -> int:
    """The SSO list walked whole: page 1 by GET, page 2 by its pager."""
    row, _got = world.stored(SSO_LIST, NEWS_SITE, p1 or fx("sso_list_p1.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("sso_list_p2.html"))
    return row["id"]


def _detail(world, url: str, **cols) -> int:
    world.f.add(url, host="sso.nknu.edu.tw", site=NEWS_SITE, unit=None, tier=0, kind="page", depth=1,
                found_via="link", found_from=None, owner_site=NEWS_SITE, kind0="page")
    uid = world.f.row(url)["id"]
    world.f.finish(uid, **{"state": "done", "status": 200, "kind": "page", "warc": "warc/x.warc.gz",
                           "warc_offset": 1, **cols})
    return uid


def _rows(world, list_id):
    return {r["ident"]: r for r in world.f.q("SELECT * FROM list_row WHERE list_id=?", (list_id,))}


def test_the_rows_of_a_walk_are_kept(world):
    list_id = _walk_whole(world)
    rows = _rows(world, list_id)
    assert len(rows) == world.walk(list_id)["rows_unique"] == 4  # the pinned row, on both pages, once
    row2 = rows[DETAIL2]
    assert (row2["title"], row2["date"], row2["href"]) == ("停修申請時間公告", "2026-10-21", DETAIL2)
    assert (row2["first_seen"], row2["last_seen"], row2["changed_at"]) == (world.now, world.now, None)


def test_a_changed_row_puts_its_fetched_detail_back(world):
    list_id = _walk_whole(world)
    d2 = _detail(world, DETAIL2, attempts=2)  # got on its third try in round one
    d1 = _detail(world, scope.canonical("https://sso.nknu.edu.tw/News/lastNews.aspx?"
                                        "UNID=AAAA000000000000000000000000000000000001"))
    world.now += 86400
    world.stored(SSO_LIST, NEWS_SITE, fx("sso_list_p1.html").replace("2026-10-21", "2026-10-28"))
    row2 = _rows(world, list_id)[DETAIL2]
    assert (row2["date"], row2["changed_at"], row2["first_seen"]) == ("2026-10-28", world.now, world.now - 86400)
    got = world.f.row_by_id(d2)
    assert (got["state"], got["reason"], got["attempts"], got["next_at"]) == ("pending", "row_changed", 0, 0)
    assert world.f.row_by_id(d1)["state"] == "done"  # unchanged rows: nothing put back


def test_a_changed_row_on_a_host_that_answers_one_scheme_puts_back_the_row_fetched(world):
    """The list writes the link in http, the host answers on https only (crawler._scheme_rewrite): the row the
    crawler fetched is the https one."""
    world.f.set_host("sso.nknu.edu.tw", queue="ip:140.127.40.173", scheme_only="https")
    http2 = "http://sso.nknu.edu.tw/News/lastNews.aspx?UNID=AAAA000000000000000000000000000000000002"
    p1 = fx("sso_list_p1.html").replace(
        "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=AAAA000000000000000000000000000000000002", http2)
    list_id = _walk_whole(world, p1)
    d2 = _detail(world, DETAIL2)
    world.now += 86400
    world.stored(SSO_LIST, NEWS_SITE, p1.replace("2026-10-21", "2026-10-28"))
    assert _rows(world, list_id)[scope.canonical(http2)]["changed_at"] == world.now
    assert world.f.row_by_id(d2)["state"] == "pending"


def test_the_rows_are_read_again_from_the_archive(world):
    """For an archive walked before list_row (the lab's): every stored list page and every pager answer the walk took,
    oldest first, each row's times its answer's; nothing put back."""
    list_id = _walk_whole(world)
    want = {k: (r["title"], r["date"], r["category"]) for k, r in _rows(world, list_id).items()}
    d2 = _detail(world, DETAIL2)
    world.f.x("DELETE FROM list_row")
    n = postback.backfill_list_rows(world.f, world.root)
    got = _rows(world, list_id)
    assert n == len(got) == 4 and {k: (r["title"], r["date"], r["category"]) for k, r in got.items()} == want
    assert all(r["first_seen"] is not None and r["last_seen"] >= r["first_seen"] for r in got.values())
    assert world.f.row_by_id(d2)["state"] == "done"


# ---------------------------------------------------------------- Task 3: a look at a list walked by button

NEW_P1 = fx("sso_list_p1.html").replace("AAAA000000000000000000000000000000000002",
                                        "AAAA000000000000000000000000000000000009")  # row 2 replaced by a new notice


def _walk_and_res(world, list_id):
    walk = tuple(world.f.q("SELECT * FROM listwalk WHERE list_id=?", (list_id,))[0])
    res = sorted((r["res_key"], r["how"], r["role"], r["need"], r["res_id"], r["action_id"])
                 for r in world.f.q("SELECT * FROM page_res WHERE page_id=?", (list_id,)))
    return walk, res


def _look(world, list_id):
    got = world.f.q("SELECT * FROM list_look WHERE list_id=?", (list_id,))
    return got[0] if got else None


def _pending(world):
    return world.f.q("SELECT * FROM action WHERE state IN ('pending','active') ORDER BY id")


def _night(world, list_id, p1: str | None = None):
    world.now += 86400
    assert world.chains.look(list_id)
    world.stored(SSO_LIST, NEWS_SITE, p1 or fx("sso_list_p1.html"))


def test_a_look_with_nothing_new_stops_at_page_1_and_keeps_the_whole_walk(world):
    list_id = _walk_whole(world)
    before = _walk_and_res(world, list_id)
    _night(world, list_id)
    assert not _pending(world)  # page 2 not pressed
    look = _look(world, list_id)
    assert (look["mode"], look["pages"], look["ended_by"]) == ("look", 1, postback.SEEN_PAGE)
    assert look["ended_at"] == world.now
    assert _walk_and_res(world, list_id) == before  # listwalk and the list page's page_res as the whole walk left them


def test_a_look_with_a_new_row_goes_on_and_reaching_the_end_is_a_whole_walk(world):
    list_id = _walk_whole(world)
    _night(world, list_id, NEW_P1)
    a, target = world.press()
    assert (a["kind"], a["step"]) == ("pager", 2)
    world.answer(a, target.url, fx("sso_list_p2.html"))
    look = _look(world, list_id)
    assert (look["pages"], look["ended_by"]) == (2, "last_page")
    lw = world.walk(list_id)
    assert (lw["ended_by"], lw["pages"], lw["at"]) == ("last_page", 2, world.now)  # written as any whole walk


def test_a_look_whose_pager_fails_keeps_the_walk_and_spends_nothing(world):
    """A look's failed page press counts neither in the list's one automatic restart (_may_restart) nor in _press's
    two failures: two failed looks, and the third look still presses page 2."""
    list_id = _walk_whole(world)
    before = _walk_and_res(world, list_id)
    for _ in range(2):
        _night(world, list_id, NEW_P1 if _ == 0 else NEW_P1.replace("000000009", "000000008"))
        a, _target = world.press()
        assert a["kind"] == "pager"
        world.chains.failed(a, "HTTP 503")
        look = _look(world, list_id)
        assert look["ended_by"] == "post_failed" and _walk_and_res(world, list_id) == before
        assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "superseded"
    assert world.chains._may_restart(list_id)
    _night(world, list_id, NEW_P1.replace("000000009", "000000007"))
    assert [p["kind"] for p in _pending(world)] == ["pager"]


def test_a_look_started_again_still_sees_its_new_rows(world):
    """resume() at the next run's start (presses left pending): the look goes on, its start kept, the whole walk's
    record untouched; the new rows of its first pass are still news, so page 2 is pressed again."""
    list_id = _walk_whole(world)
    walk_before = _walk_and_res(world, list_id)[0]
    _night(world, list_id, NEW_P1)
    started = _look(world, list_id)["started"]
    assert [p["kind"] for p in _pending(world)] == ["pager"]
    world.now += 3600
    assert world.chains.resume() == 1
    assert _walk_and_res(world, list_id)[0] == walk_before and _look(world, list_id)["ended_at"] is None
    assert _look(world, list_id)["started"] == started and world.f.row_by_id(list_id)["state"] == "pending"
    world.stored(SSO_LIST, NEWS_SITE, NEW_P1)
    assert [p["kind"] for p in _pending(world)] == ["pager"]


def test_a_look_opens_only_on_a_walk_that_ended(world):
    row, _got = world.stored(SSO_LIST, NEWS_SITE, fx("sso_list_p1.html"))  # page 2 still to press: walking
    assert world.walk(row["id"])["ended_by"] == "walking"
    assert not world.chains.look(row["id"])  # a press waits: resume()'s to finish
    world.f.x("UPDATE action SET state='superseded' WHERE list_id=?", (row["id"],))  # cut off, nothing queued
    assert not world.chains.look(row["id"])  # still walking: a whole walk's to finish, never a look
    assert _look(world, row["id"]) is None


def test_a_look_whose_page_never_came_back_is_closed_at_the_next_start(world):
    """Page 1 answered 404 (or a sign-in page, or nothing three times): no page_stored, so nothing closed the look;
    close_stale_looks does, the whole walk's record as it was."""
    list_id = _walk_whole(world)
    before = _walk_and_res(world, list_id)
    world.now += 86400
    assert world.chains.look(list_id)
    world.f.set_page_res(list_id, [])  # what the crawler's reading of the 404 did to the page's entries
    world.f.finish(list_id, state="done", status=404, reason="http_404")
    assert world.chains.close_stale_looks() == 1
    look = _look(world, list_id)
    assert look["ended_by"] == "not_got:done/404" and _walk_and_res(world, list_id) == before
    assert world.chains.close_stale_looks() == 0


def test_a_persons_retry_closes_an_open_look_and_walks_whole(world):
    list_id = _walk_whole(world)
    world.f.x("UPDATE listwalk SET ended_by='post_failed', ok=0 WHERE list_id=?", (list_id,))  # a whole walk ended so
    world.now += 86400
    assert world.chains.look(list_id)
    world.chains.retry_walks("post_failed")  # `nknu archive retry post_failed`: a person asks for the whole walk
    assert _look(world, list_id)["ended_by"] == "retried"
    assert world.walk(list_id)["ended_by"] == "walking"


def test_a_look_whose_page_1_says_the_list_is_empty_keeps_a_walk_with_rows(world):
    list_id = _walk_whole(world)
    before = _walk_and_res(world, list_id)
    p1 = fx("sso_list_p1.html")
    empty = (p1[:p1.index('<tr><td><span id="gvFull_ctl02_Category">')].replace("共4筆", "共0筆")
             + '<tr><td colspan="6">查無資料</td></tr>\n</table>\n</div>\n</form>\n</body></html>')
    _night(world, list_id, empty)
    look = _look(world, list_id)
    assert look["ended_by"] == "no_rows_ok" and _walk_and_res(world, list_id)[0] == before[0]
    assert world.f.q("SELECT 1 FROM event WHERE code='look_ended'")


# ---------------------------------------------------------------- Task 4: a look at a list paged by GET (the crawler)

LIST = f"https://{HOST}/news/list.aspx"


def _list_page(n: int, notices: list[int], last: int = 3) -> str:
    """A made-up list paged by GET (no platform rule): its notices, then its pager (numbers, 下一頁)."""
    items = "".join(f"<li><a href='https://{HOST}/news/view.aspx?id={i}'>公告 {i}</a></li>" for i in notices)
    pager = "".join(f"<a href='{LIST}{'' if p == 1 else f'?page={p}'}'>{p}</a> " for p in range(1, last + 1) if p != n)
    if n < last:
        pager += f"<a href='{LIST}?page={n + 1}'>下一頁</a>"
    return f"<html><head><title>最新消息</title></head><body><ul>{items}</ul><div class='pager'>{pager}</div></body></html>"


class School:
    """The list's three pages and its notices, page by page for each night; a page may answer 404."""

    def __init__(self):
        self.pages = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9]}
        self.gone: set[int] = set()

    def routes(self):
        def page(url):
            n = int(url.rsplit("=", 1)[1]) if "?page=" in url else 1
            if n in self.gone:
                return snap(url, 404, html="<html><body>找不到</body></html>")
            return snap(url, 200, html=_list_page(n, self.pages[n]))
        return {"robots.txt": snap("x", 404), "/news/view.aspx": lambda u: snap(u, 200, html="<html><body>內文</body></html>"),
                "/news/list.aspx": page}


def _get_world(tmp_path):
    school = School()
    crawler, net, clock = make(tmp_path, school.routes())
    seed(crawler, LIST)
    settle(crawler, net, [QUEUE])
    return school, crawler, net, clock


def _group_walks(crawler):
    return sorted(tuple(r) for r in crawler.frontier.q("SELECT * FROM listwalk ORDER BY list_id"))


def _night_get(crawler, net, clock):
    clock.t += 86400
    top = crawler.frontier.row(LIST)["id"]
    assert crawler.chains.look_get(top)
    seen = len(net.seen)
    settle(crawler, net, [QUEUE])
    return [u for u in net.seen[seen:] if "/news/list.aspx" in u]


def test_the_get_list_is_walked_whole_the_first_time(tmp_path):
    _school, crawler, _net, _clock = _get_world(tmp_path)
    ends = {r["ended_by"] for r in crawler.frontier.q("SELECT ended_by FROM listwalk")}
    assert ends == {"last_page"} and len(_group_walks(crawler)) == 3


def test_a_get_look_with_nothing_new_reads_page_1_only(tmp_path):
    _school, crawler, net, clock = _get_world(tmp_path)
    before = _group_walks(crawler)
    fetched = _night_get(crawler, net, clock)
    assert fetched == [LIST]
    look = crawler.frontier.q("SELECT * FROM list_look WHERE list_id=?", (crawler.frontier.row(LIST)["id"],))[0]
    assert (look["mode"], look["ended_by"]) == ("look_get", postback.SEEN_PAGE)
    assert [w[:7] + w[8:10] for w in _group_walks(crawler)] == [w[:7] + w[8:10] for w in before]  # all but `at`


def test_a_get_look_with_a_new_notice_reads_on_until_a_page_brings_nothing(tmp_path):
    school, crawler, net, clock = _get_world(tmp_path)
    school.pages = {1: [10, 1, 2], 2: [3, 4, 5], 3: [6, 7, 8, 9]}  # one new notice: the others move down a page
    fetched = _night_get(crawler, net, clock)
    assert fetched == [LIST, f"{LIST}?page=2"]  # page 2 brought nothing new (its notices moved, none is new)
    assert crawler.frontier.row(f"https://{HOST}/news/view.aspx?id=10")["state"] == "done"
    assert {r["ended_by"] for r in crawler.frontier.q("SELECT ended_by FROM listwalk")} == {"last_page"}
    assert not crawler.frontier.q("SELECT 1 FROM list_look WHERE ended_at IS NULL")


def test_a_get_look_whose_next_page_did_not_come_keeps_the_walks(tmp_path):
    school, crawler, net, clock = _get_world(tmp_path)
    before = _group_walks(crawler)
    school.pages[1] = [10, 1, 2]
    school.gone = {2}
    fetched = _night_get(crawler, net, clock)
    assert fetched == [LIST, f"{LIST}?page=2"]
    walks = [w[:7] + w[8:10] for w in _group_walks(crawler)]
    assert walks == [w[:7] + w[8:10] for w in before]  # never written while the look is open
    assert crawler.chains.close_stale_looks() == 1  # as the next run's start does
    page2 = crawler.frontier.row(f"{LIST}?page=2")["id"]
    look = crawler.frontier.q("SELECT * FROM list_look WHERE list_id=?", (page2,))[0]
    assert look["ended_by"].startswith("not_got:done/404")
    assert [w[:7] + w[8:10] for w in _group_walks(crawler)] == [w[:7] + w[8:10] for w in before]


# ---------------------------------------------------------------- Task 5: growth caps of a site under update

SITE = HOST


def _rows_of(crawler, n: int, *, kind: str = "page", added_at: float, state: str = "done", reason=None,
             prefix: str = "old") -> None:
    for i in range(n):
        crawler.frontier.add(f"https://{HOST}/{prefix}/{i}.html", host=HOST, site=SITE, unit=None, tier=1, kind=kind,
                             depth=1, found_via="link", found_from=None, owner_site=SITE, state=state, reason=reason,
                             added_at=added_at)


def _under_update(crawler, sites=(SITE,)) -> None:
    """What update's go-live writes (meta update:sites), then a run's start (Crawler.start_run_caps)."""
    crawler.frontier.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps(sorted(sites)))
    crawler.start_run_caps()


def test_a_site_under_update_is_free_of_its_lifetime_cap(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    _rows_of(crawler, 4000, added_at=clock() - 86400 * 30)
    new = f"https://{HOST}/new/1.html"
    assert crawler._cap(new, SITE, "page", 2) == "cap:site"  # today's rule
    _under_update(crawler)
    assert crawler._cap(new, SITE, "page", 2) is None
    assert crawler._cap(new, SITE, "page", 99) == "cap:depth"  # the depth limit stays


def test_a_run_adding_more_than_its_cap_holds_the_rest_and_never_lets_them_go(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    _under_update(crawler)
    _rows_of(crawler, crawler_mod.RUN_CAPS.pages, added_at=clock(), state="pending", prefix="tonight")
    assert crawler._cap(f"https://{HOST}/new/1.html", SITE, "page", 2) == "cap:run_page"
    assert crawler._cap(f"https://{HOST}/new/1.pdf", SITE, "file", 2) is None  # files count apart
    _rows_of(crawler, 1, added_at=clock(), state="held", reason="cap:run_page", prefix="held")
    clock.t += 86400
    crawler.start_run_caps()
    assert crawler._release_caps() == 0  # a run cap's row waits for a person (`retry cap:run`)
    assert crawler.frontier.row(f"https://{HOST}/held/0.html")["state"] == "held"


def test_letting_go_of_held_rows_stops_at_the_run_cap(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    crawler.queue_of(HOST)
    _rows_of(crawler, crawler_mod.RUN_CAPS.pages + 20, added_at=clock() - 86400, state="held", reason="cap:site")
    _under_update(crawler)
    assert crawler._release_caps() == crawler_mod.RUN_CAPS.pages
    assert crawler.frontier.q("SELECT count(*) FROM url WHERE state='held'")[0][0] == 20
    assert crawler._cap(f"https://{HOST}/new/1.html", SITE, "page", 2) == "cap:run_page"  # the let-go counted


def test_a_site_not_under_update_keeps_the_lifetime_caps(tmp_path):
    crawler, _net, clock = make(tmp_path, {})
    _rows_of(crawler, 4000, added_at=clock() - 86400)
    _under_update(crawler, sites=("another.nknu.edu.tw",))
    assert crawler._cap(f"https://{HOST}/new/1.html", SITE, "page", 2) == "cap:site"
