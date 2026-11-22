"""Periodic updates, stage 2: the fix pass after the plan check (wf_2249ba4d-a90, research/crawler_design_2026-11-16/
plancheck2_1116.md). A monthly whole walk is a look that does not stop at a page with nothing new, so any end short
of a whole walk keeps the last record (and is walked again the next night); a list with no end is walked every night;
standing pages by their last good answer; rule 1 reads the links of a changed notice's own text; the fingerprint drops
the counters a page marks (an eye icon, an id or class naming a counter, a number after a counter word) and keeps
every other number; the night's review judges each row against the answer it held when the night started, knows a row
taken but not answered, compares redirects by their target, holds a site only on five pages gone bad, judges a list
by its rows, counts the presses of new rows, and covers every night not yet reviewed; the script reviews a failed run
too and stops on a run that did not end well. Made-up pages and rows only."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import date, timedelta

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import postback, update, watch
from nknu.archive.frontier import Frontier
from tests.archive.test_crawler_round1 import settle
from tests.archive.test_postback import SSO_LIST, World, fx
from tests.archive.test_update_rotations_1116 import _days, _row
from tests.archive.test_update_select_1116 import R1, _site, _url, _walk
from tests.archive.test_update_stage1_1116 import (
    LIST,
    NEWS_SITE,
    _get_world,
    _group_walks,
    _look,
    _pending,
    _walk_and_res,
    _walk_whole,
)
from tests.archive.test_update_stage1_1116 import QUEUE as GET_QUEUE
from tests.archive.test_update_stage2_1116 import SCRIPT, SITE, _answer, _fake_python, _html


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.f.close()


# ---------------------------------------------------------------- whole walks are looks that do not stop

def test_a_whole_look_walks_past_a_page_with_nothing_new(world):
    list_id = _walk_whole(world)
    world.now += 86400
    assert world.chains.look(list_id, whole=True)
    world.stored(SSO_LIST, NEWS_SITE, fx("sso_list_p1.html"))  # nothing new on page 1: a look would stop here
    a, target = world.press()
    assert (a["kind"], a["step"]) == ("pager", 2)
    world.answer(a, target.url, fx("sso_list_p2.html"))
    look = _look(world, list_id)
    assert (look["whole"], look["pages"], look["ended_by"]) == (1, 2, "last_page")
    assert world.walk(list_id)["at"] == world.now  # written as any whole walk


def test_a_whole_look_whose_page_never_came_keeps_the_record(world):
    list_id = _walk_whole(world)
    before = _walk_and_res(world, list_id)
    world.now += 86400
    assert world.chains.look(list_id, whole=True)
    world.f.set_page_res(list_id, [])
    world.f.finish(list_id, state="done", status=500, reason="http_500")
    assert world.chains.close_stale_looks() == 1
    assert _walk_and_res(world, list_id) == before and _look(world, list_id)["ended_by"].startswith("not_got")
    assert not _pending(world)


def test_a_whole_get_look_reads_every_page(tmp_path):
    _school, crawler, net, clock = _get_world(tmp_path)
    clock.t += 86400
    assert crawler.chains.look_get(crawler.frontier.row(LIST)["id"], whole=True)
    seen = len(net.seen)
    settle(crawler, net, [GET_QUEUE])
    fetched = [u for u in net.seen[seen:] if "/news/list.aspx" in u]
    assert fetched == [LIST, f"{LIST}?page=2", f"{LIST}?page=3"]  # nothing new anywhere, read to the end
    assert {r["ended_by"] for r in crawler.frontier.q("SELECT ended_by FROM listwalk")} == {"last_page"}
    assert not crawler.frontier.q("SELECT 1 FROM list_look WHERE ended_at IS NULL")
    assert len(_group_walks(crawler)) == 3


# ---------------------------------------------------------------- the selection

@pytest.fixture
def site(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    _site(f, R1, "1")
    ids = {"list": _url(f, "/news/list.htm")}
    _walk(f, ids["list"], "last_page", 40)
    ids["standing"] = [_url(f, f"/about/p{i}.htm") for i in range(8)]
    yield f, ids, tmp_path
    f.close()


def test_put_back_opens_a_whole_look_on_the_months_list_and_keeps_its_record(site):
    f, ids, root = site
    day = next(d for d in _days(30) if ids["list"] in update.select(f, root, today=d).walks)
    f.close()
    assert update.main(["--dir", str(root), "go-live"]) == 0
    assert update.main(["--dir", str(root), "put-back", "--today", day.isoformat()]) == 0
    g = Frontier(root / "frontier.db")
    try:
        lk = g.q("SELECT * FROM list_look WHERE list_id=?", (ids["list"],))[0]
        assert (lk["mode"], lk["whole"], lk["ended_at"]) == ("look", 1, None)
        assert g.q("SELECT ended_by FROM listwalk WHERE list_id=?", (ids["list"],))[0][0] == "last_page"
    finally:
        g.close()


def test_a_list_with_no_end_is_walked_again_every_night(site):
    f, ids, root = site
    _walk(f, ids["list"], "walking", 12)
    for d in _days(3):
        t = update.select(f, root, today=d)
        assert t.redo == [ids["list"]] and ids["list"] not in t.walks + t.looks


def test_a_whole_walk_that_ended_short_is_walked_again_the_next_night(site):
    f, ids, root = site
    f.x("INSERT INTO list_look(list_id, mode, started, ended_at, ended_by, whole) VALUES (?, 'look', 1, 2, "
        "'not_got:done/500', 1)", (ids["list"],))
    for d in _days(3):
        assert update.select(f, root, today=d).walks == [ids["list"]]
    f.x("UPDATE list_look SET ended_by='last_page' WHERE list_id=?", (ids["list"],))  # whole at last
    assert sum(ids["list"] in update.select(f, root, today=d).walks for d in _days(30)) == 1


def test_a_standing_page_that_went_404_is_still_checked_on_its_day(site):
    f, ids, root = site
    gone = ids["standing"][0]
    f.activate(gone)  # its good answer kept
    f.finish(gone, state="done", status=404, reason="http_404", warc="warc/q.warc.gz", warc_offset=99999)
    assert sum(gone in update.select(f, root, today=d).standing for d in _days(7)) == 1


def test_a_page_the_school_deleted_is_checked_once_a_month(site):
    f, ids, root = site
    gone = ids["standing"][1]
    f.x("INSERT INTO page_watch(url_id, fp, checked_at, gone_at, gone) VALUES (?, NULL, 1, 1, 2)", (gone,))
    assert sum(gone in update.select(f, root, today=d).standing for d in _days(30)) == 1


def test_rule_one_follows_the_links_of_a_notices_own_text(world):
    world.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    in_text = _answer(world, f"https://{SITE}/rules.htm", _html("<p>辦法</p>"))
    in_nav = _answer(world, f"https://{SITE}/about.htm", _html("<p>簡介</p>"))
    lst = _answer(world, f"https://{SITE}/list.htm", _html("<p>列表</p>"))
    _walk(world.f, lst, "last_page", 5)
    pdf = _answer(world, f"https://{SITE}/form.pdf", b"%PDF-1.4 made up", ctype="application/pdf", kind="file")
    page = (f"<html><body><nav><a href='https://{SITE}/about.htm'>簡介</a></nav><main><p>修正</p>"
            f"<a href='https://{SITE}/rules.htm'>辦法</a><a href='/form.pdf'>表單</a></main></body></html>")
    notice = _answer(world, f"https://{SITE}/n1.htm", page)
    _row(world.f, lst, f"https://{SITE}/n1.htm", "2026-11-10")
    for dst in ("about.htm", "rules.htm"):
        world.f.link(notice, f"https://{SITE}/{dst}", "看", "take")
    last = time.time() - 3600
    world.f.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes) VALUES (?, 'x', ?, ?, 1)",
              (notice, last + 60, last + 60))
    for d in _days(7):
        t = update.select(world.f, world.root, today=d, last_night=last)
        assert in_text in t.standing + t.follow and pdf in t.follow
        assert in_nav not in t.follow


def test_rule_one_leaves_out_what_the_home_links_on_a_page_with_no_template(world):
    """A notice whose page has no header or nav: its menu is in its text; the home links the same pages."""
    world.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    home = _answer(world, f"https://{SITE}/", _html("<p>首頁</p>"))
    world.f.x("UPDATE url SET depth=0 WHERE id=?", (home,))
    world.f.seed_src(f"https://{SITE}/", "site_home")
    menu = _answer(world, f"https://{SITE}/about.htm", _html("<p>簡介</p>"))
    world.f.link(home, f"https://{SITE}/about.htm", "簡介", "take")
    lst = _answer(world, f"https://{SITE}/list.htm", _html("<p>列表</p>"))
    _walk(world.f, lst, "last_page", 5)
    notice = _answer(world, f"https://{SITE}/n1.htm", "<html><body><div><a href='/about.htm'>簡介</a></div>"
                                                     "<p>公告</p></body></html>")
    _row(world.f, lst, f"https://{SITE}/n1.htm", "2026-11-10")
    last = time.time() - 3600
    world.f.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes) VALUES (?, 'x', ?, ?, 1)",
              (notice, last + 60, last + 60))
    assert all(menu not in update.select(world.f, world.root, today=d, last_night=last).follow for d in _days(7))


# ---------------------------------------------------------------- the fingerprint keeps every number but counters

def _same(a: str, b: str) -> bool:
    url = f"https://{SITE}/p.htm"
    return watch.fingerprint(_html(a), url) == watch.fingerprint(_html(b), url)


def test_a_date_with_dots_or_a_fee_alone_in_a_cell_is_a_change():
    assert not _same("<table><tr><td>截止</td><td>115.11.20</td></tr></table>",
                     "<table><tr><td>截止</td><td>115.11.30</td></tr></table>")
    assert not _same("<table><tr><td>學費</td><td>12,000</td></tr></table>",
                     "<table><tr><td>學費</td><td>13,000</td></tr></table>")


def test_the_counters_a_page_marks_are_no_change():
    assert _same('<div><span><i class="fa-regular fa-eye"></i> 2277</span></div>',
                 '<div><span><i class="fa-regular fa-eye"></i> 2301</span></div>')
    assert _same('<p>觀看次數:655</p>', '<p>觀看次數:701</p>')
    assert _same('<span id="lblClickCount">12</span>', '<span id="lblClickCount">13</span>')
    assert not _same('<p>名額:30</p>', '<p>名額:40</p>')


# ---------------------------------------------------------------- the night's review

@pytest.fixture
def w2(tmp_path):
    w = World(tmp_path)
    w.f.set_host(SITE, queue="ip:test")
    yield w
    w.f.close()


def _fail(w, uid: int) -> None:
    w.f.activate(uid)
    w.f.finish(uid, state="failed", reason="timeout")


def _change(n, uid):
    return next(c.change for c in n.rows if c.url_id == uid)


def test_a_row_taken_but_not_answered_is_no_answer_and_leaves_its_watch(w2):
    url = f"https://{SITE}/a.htm"
    uid = _answer(w2, url, _html("<p>甲</p>"))
    first = watch.night(w2.f, w2.root, time.time() - 5, now=time.time())  # the page watched once
    assert _change(first, uid) == "new"
    before = tuple(w2.f.q("SELECT * FROM page_watch WHERE url_id=?", (uid,))[0])
    since = time.time()
    _fail(w2, uid)
    n = watch.night(w2.f, w2.root, since, now=time.time())
    assert _change(n, uid) == "no_answer"
    assert tuple(w2.f.q("SELECT * FROM page_watch WHERE url_id=?", (uid,))[0]) == before


def test_after_a_night_with_no_answer_the_next_answer_is_judged_against_the_last_good(w2):
    url = f"https://{SITE}/a.htm"
    uid = _answer(w2, url, _html("<p>甲</p>"))
    _fail(w2, uid)  # a week with no answer: its copy is the answer it held
    since = time.time() + 0.001
    while time.time() < since:
        pass
    _answer(w2, url, _html("<p>甲</p>"))  # the next week: the same text
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "same"


def test_a_row_fetched_twice_tonight_is_judged_against_the_answer_before_the_night(w2):
    url = f"https://{SITE}/a.htm"
    uid = _answer(w2, url, _html("<p>甲</p>"))
    since = time.time()
    _answer(w2, url, _html("<p>乙</p>"))
    _answer(w2, url, _html("<p>甲</p>"))  # back to the first text: no change over the night
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "same"


def _redirect(w, url: str, to: str) -> int:
    uid = _answer(w, url, "", status=301)
    w.f.x("UPDATE url SET final_url=? WHERE id=?", (to, uid))
    return uid


def test_a_redirect_is_judged_by_where_it_leads(w2):
    url = f"https://{SITE}/"
    uid = _redirect(w2, url, f"https://{SITE}/zh/")
    since = time.time()
    _redirect(w2, url, f"https://{SITE}/zh/")
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "same"
    since = time.time()
    _redirect(w2, url, f"https://{SITE}/en/")
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "changed"


def test_one_bad_page_of_three_holds_no_site(w2):
    ids = [_answer(w2, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(3)]
    since = time.time()
    _answer(w2, f"https://{SITE}/p0.htm", "<html><body>找不到</body></html>", status=404)
    for i in ids[1:]:
        _answer(w2, w2.f.row_by_id(i)["url"], _html(f"<p>{i} 新</p>"))
    n = watch.night(w2.f, w2.root, since, now=time.time())
    assert not n.alarms and n.sites_changed == [SITE]


def test_five_pages_gone_bad_hold_the_site_its_watch_and_bring_them_back_the_next_night(w2):
    ids = [_answer(w2, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(6)]
    watch.night(w2.f, w2.root, time.time() - 5, now=time.time())
    before = {i: tuple(w2.f.q("SELECT * FROM page_watch WHERE url_id=?", (i,))[0]) for i in ids}
    since = time.time()
    for i in ids[:5]:
        _answer(w2, w2.f.row_by_id(i)["url"], "<html><body>系統維護中</body></html>", status=503)
    n = watch.night(w2.f, w2.root, since, now=time.time())
    assert SITE in n.alarms and n.sites_changed == [] and set(ids[:5]) <= set(n.again)
    assert {i: tuple(w2.f.q("SELECT * FROM page_watch WHERE url_id=?", (i,))[0]) for i in ids[:5]} == \
        {i: before[i] for i in ids[:5]}


def test_a_list_page_is_judged_by_its_rows_not_its_text(w2):
    url = f"https://{SITE}/list.htm"
    uid = _answer(w2, url, _html("<p>公告 一（點閱 10）</p>"))
    _walk(w2.f, uid, "last_page", 1)
    _row(w2.f, uid, f"https://{SITE}/n1.htm", "2026-11-01")  # its rows are read (first seen long ago)
    since = time.time()
    _answer(w2, url, _html("<p>公告 一（點閱 11）</p>"))
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "same"
    since = time.time()
    _answer(w2, url, _html("<p>公告 一（點閱 12）</p>"))
    _row(w2.f, uid, f"https://{SITE}/n2.htm", "2026-11-20")
    w2.f.x("UPDATE list_row SET first_seen=? WHERE list_id=?", (time.time(), uid))
    assert _change(watch.night(w2.f, w2.root, since, now=time.time()), uid) == "changed"


def test_a_new_rows_press_is_news_of_its_site(w2):
    lst = _answer(w2, f"https://{SITE}/list.aspx", _html("<p>列表</p>"))
    since = time.time()
    w2.f.x("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
           "target, row_key, row_text, state, created, done) VALUES (?, 2, 1, 'detail', 'q', ?, ?, 'p', 'w', 1, 't', "
           "'r9', '新公告', 'done', ?, ?)", (lst, SITE, SITE, since + 1, since + 2))
    n = watch.night(w2.f, w2.root, since, now=time.time())
    assert [(c.kind, c.change) for c in n.rows if c.url_id < 0] == [("press:detail", "new")]
    assert n.sites_changed == [SITE]


# ---------------------------------------------------------------- the review's window, the run's end, the script

def _main(w, *argv) -> int:
    w.f.close()
    try:
        return update.main(["--dir", str(w.root), *argv])
    finally:
        w.f = Frontier(w.root / "frontier.db")


def _status(w) -> dict:
    return json.loads((w.root / "reports" / update.STATUS).read_text(encoding="utf-8"))


def _run_end(w, reason: str) -> None:
    w.f.event("info", "run_start", json.dumps({"sites": [SITE]}))
    w.f.event("info", "run_end", json.dumps({"reason": reason}))


def test_a_night_not_reviewed_is_reviewed_with_the_next(w2):
    w2.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    w2.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    a = _answer(w2, f"https://{SITE}/a.htm", _html("<p>甲</p>"))
    b = _answer(w2, f"https://{SITE}/b.htm", _html("<p>乙</p>"))
    assert _main(w2, "put-back", "--today", "2026-11-20") == 0
    _answer(w2, f"https://{SITE}/a.htm", _html("<p>甲 改</p>"))  # the first night: never reviewed (a reboot)
    time.sleep(1.1)  # the answers' fetched_at are whole seconds: the second night starts in a later one
    assert _main(w2, "put-back", "--today", "2026-11-21") == 0
    _answer(w2, f"https://{SITE}/b.htm", _html("<p>乙 改</p>"))
    _run_end(w2, "finished")
    assert _main(w2, "review") == 0
    rows = (w2.root / "reports" / _status(w2)["changes_file"]).read_text(encoding="utf-8")
    assert "a.htm,page,changed" in rows and "b.htm,page,changed" in rows and a != b


@pytest.mark.parametrize("reason", ["db_error", None])
def test_a_run_that_did_not_end_well_is_reviewed_and_named(w2, reason):
    w2.f.meta(update.NIGHT_KEY, repr(time.time() - 5))
    if reason:
        _run_end(w2, reason)
    assert _main(w2, "review") == 3
    status = _status(w2)
    assert status["failed_step"] == "run" and (status["run_end"] or {}).get("reason") == reason


def test_fail_keeps_the_nights_review(w2):
    w2.f.meta(update.NIGHT_KEY, repr(time.time() - 5))
    _run_end(w2, "finished")
    assert _main(w2, "review") == 0
    assert _main(w2, "fail", "--step", "export") == 0
    status = _status(w2)
    assert status["failed_step"] == "export" and "counts" in status


def test_the_script_reviews_a_night_whose_run_failed(tmp_path):
    py = _fake_python(tmp_path, "run")
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"),
           "TZ": "Asia/Taipei", "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    steps = [line.split()[0] for line in (tmp_path / "calls.log").read_text().splitlines()]
    assert got.returncode == 1 and steps == ["warc-there", "backup", "put-back", "night", "sites", "run", "review", "fail"]


def test_a_paused_night_touches_nothing(tmp_path):
    py = _fake_python(tmp_path, None)
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    (tmp_path / "archive" / "update.pause").write_text("")
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"),
           "TZ": "Asia/Taipei", "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    assert got.returncode == 0 and not (tmp_path / "calls.log").exists()


def test_today_and_shares_are_the_taipei_days_number():
    d = date(2027, 2, 27)
    shares = {(d + timedelta(days=i)).toordinal() % update.MONTH for i in range(30)}
    assert shares == set(range(update.MONTH))  # across February: every share once
    assert postback.LOOK == "look"
