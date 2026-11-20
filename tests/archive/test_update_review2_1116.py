"""Periodic updates, stage 2: the fix pass after the stage's review (wf_f19f388a-236, research/crawler_design_2026-11-16/
review2_1116.md). The night's review: counters written in English, 'deleted' said once and never of a page that was
never good, a server down all night seen, a page first answered inside the window new, rows that only moved between
the pages of a GET list no news, a list whose rows no platform reads compared by its text, a script redirect judged
by where it leads, list pages in the one-body alarm, an alarm that lasts while the site stays down (its rows judged
against the answer before the alarm, its new rows held too). The review's windows follow one another, its files are
written before it counts the night. The selection: a whole walk that reached its end is not walked again the next
night whatever the end, a GET whole walk walked again only when its first page did not come, standing pages by a
good answer (flags too), the sites go-live named. The script: a stop before the run starts no run. Made-up pages."""

from __future__ import annotations

import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import update, watch
from nknu.archive.frontier import Frontier
from tests.archive.test_postback import World
from tests.archive.test_update_rotations_1116 import _days
from tests.archive.test_update_select_1116 import R1, _site, _url, _walk
from tests.archive.test_update_stage2_1116 import SCRIPT, SITE, _answer, _fake_python, _html

DAY = 86400.0


@pytest.fixture
def w(tmp_path):
    w = World(tmp_path)
    w.f.set_host(SITE, queue="ip:test")
    yield w
    w.f.close()


def _change(n, uid):
    return next((c.change for c in n.rows if c.url_id == uid), None)


def _second() -> float:
    """A night that starts in a later second than every answer so far (fetched_at is whole seconds)."""
    time.sleep(1.05)
    return time.time()


# ---------------------------------------------------------------- the fingerprint

ELEC = "https://elec.nknu.edu.tw/en/news_ii.html?type=10&id=534"


def _elec(views: int) -> str:
    return ('<html><body><div id="top"><a href="/">Home</a></div><div id="right"><p id="path">Home / '
            '<a href="news.html?type=10">General</a> / Article</p><div id="ne_content"><p id="ne_title"></p>'
            '<p id="ne_list_d">2026/09/14</p>'
            f'<p id="ne_list_d">Views:{views}</p><div id="ne_c"></div></div></div></body></html>')


def test_an_english_view_counter_is_no_change():
    assert watch.fingerprint(_elec(570), ELEC) == watch.fingerprint(_elec(571), ELEC)
    url = f"https://{SITE}/p.htm"
    assert watch.fingerprint(_html("<p>Hits: 12</p>"), url) == watch.fingerprint(_html("<p>Hits: 13</p>"), url)
    assert watch.fingerprint(_html("<p>Seats: 12</p>"), url) != watch.fingerprint(_html("<p>Seats: 13</p>"), url)


# ---------------------------------------------------------------- 'deleted'

def _gone_nights(w, url: str, nights: int):
    uid = w.f.row(url)["id"]
    t0 = time.time()
    got = []
    for night in range(nights):
        since = _second()
        _answer(w, url, "<html>找不到</html>", status=404)
        n = watch.night(w.f, w.root, since, now=t0 + night * (DAY + 60))
        got.append((_change(n, uid), n.sites_changed))
    return got


def test_a_page_that_stays_gone_is_deleted_once(w):
    url = f"https://{SITE}/gone.htm"
    _answer(w, url, _html("<p>公告</p>"))
    got = _gone_nights(w, url, 4)
    assert [c for c, _s in got] == ["not_seen", "deleted", "still_gone", "still_gone"]
    assert [s for _c, s in got] == [[], [SITE], [], []]


def test_a_page_never_good_is_never_deleted_by_the_school(w):
    url = f"https://{SITE}/dead-seed.htm"
    _answer(w, url, "<html>找不到</html>", status=404)  # round one: 404 already
    assert all(c != "deleted" and s == [] for c, s in _gone_nights(w, url, 3))


# ---------------------------------------------------------------- what the window takes

def test_a_server_down_all_night_is_no_answer_and_an_alarm(w):
    ids = [_answer(w, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(6)]
    watch.night(w.f, w.root, time.time() - 5, now=time.time())
    since = _second()
    for uid in ids:  # taken (its copy kept), no answer: crawler._failed with tries left
        w.f.activate(uid)
        w.f.finish(uid, state="pending", attempts=1, reason="timeout", fetched_at=None, next_at=time.time() + 1800)
    n = watch.night(w.f, w.root, since, now=time.time())
    assert [_change(n, i) for i in ids] == ["no_answer"] * 6 and SITE in n.alarms


def test_a_page_first_answered_inside_the_window_and_again_is_new(w):
    since = time.time()
    time.sleep(1.05)
    url = f"https://{SITE}/new.htm"
    uid = _answer(w, url, _html("<p>新公告</p>"))  # a night not reviewed: its first answer
    _answer(w, url, _html("<p>新公告</p>"))  # the next night: fetched again, the same
    n = watch.night(w.f, w.root, since, now=time.time())
    assert _change(n, uid) == "new" and n.sites_changed == [SITE]


# ---------------------------------------------------------------- list pages

def _row(i: int):
    return SimpleNamespace(href=f"https://{SITE}/n{i}.htm", key=f"k{i}", title=f"公告{i}", date="2026-11-01",
                           category=None)


def test_a_get_list_page_whose_rows_only_moved_is_the_same(w):
    p1 = _answer(w, f"https://{SITE}/board.php", _html("<p>一 二</p>"))
    p2 = _answer(w, f"https://{SITE}/board.php?page=2", _html("<p>三 四</p>"))
    for a, b, t in ((p1, p2, "2"), (p2, p1, "1")):
        w.f.add_page_res(a, [{"res_key": f"https://x/{b}", "how": "get", "role": "pager", "need": "must",
                              "res_id": b, "text": t}])
    old = time.time() - 30 * DAY
    w.chains._keep_rows(p1, SimpleNamespace(rows=[_row(1), _row(2)], walk="get"), f"https://{SITE}/board.php", old,
                        put_back=False)
    w.chains._keep_rows(p2, SimpleNamespace(rows=[_row(3), _row(4)], walk="get"), f"https://{SITE}/board.php?page=2",
                        old, put_back=False)
    since = _second()
    _answer(w, f"https://{SITE}/board.php", _html("<p>二 三</p>"))  # notice 1 removed, notice 3 moved up
    w.chains._keep_rows(p1, SimpleNamespace(rows=[_row(2), _row(3)], walk="get"), f"https://{SITE}/board.php",
                        time.time(), put_back=False)
    n = watch.night(w.f, w.root, since, now=time.time())
    assert _change(n, p1) == "same" and n.sites_changed == []


def test_a_list_page_with_no_rows_read_is_compared_by_its_text(w):
    p1 = _answer(w, f"https://{SITE}/news.php", _html("<p>甲公告 2026-11-01</p>"))
    p2 = _answer(w, f"https://{SITE}/news.php?page=2", _html("<p>乙</p>"))
    w.f.add_page_res(p1, [{"res_key": "x", "how": "get", "role": "pager", "need": "must", "res_id": p2, "text": "2"}])
    since = _second()
    _answer(w, f"https://{SITE}/news.php", _html("<p>甲公告（延期至 11/30）2026-11-01</p>"))
    assert _change(watch.night(w.f, w.root, since, now=time.time()), p1) == "changed"


def test_a_script_redirect_is_judged_by_where_it_leads(w):
    url = f"https://{SITE}/"
    uid = _answer(w, url, '<html><head><meta http-equiv="refresh" content="0;url=/zh/"></head><body></body></html>')
    w.f.x("UPDATE url SET flags='js_redirect', final_url=? WHERE id=?", (f"https://{SITE}/zh/", uid))
    since = _second()
    _answer(w, url, '<html><head><meta http-equiv="refresh" content="0;url=/en/"></head><body></body></html>')
    w.f.x("UPDATE url SET flags='js_redirect', final_url=? WHERE id=?", (f"https://{SITE}/en/", uid))
    assert _change(watch.night(w.f, w.root, since, now=time.time()), uid) == "changed"


# ---------------------------------------------------------------- alarms

MAINT = "<html><body><h1>系統維護中，請稍後再試</h1></body></html>"


def test_a_maintenance_page_on_every_list_holds_the_site(w):
    lists = [_answer(w, f"https://{SITE}/list{i}.aspx", _html(f"<p>列表 {i}</p>")) for i in range(8)]
    for i in lists:
        _walk(w.f, i, "last_page", 10)
        w.chains._keep_rows(i, SimpleNamespace(rows=[_row(i)], walk="postback"), f"https://{SITE}/list{i}.aspx",
                            time.time() - 30 * DAY, put_back=False)  # rows read: judged by its rows
    standing = [_answer(w, f"https://{SITE}/about{i}.htm", _html(f"<p>簡介 {i}</p>")) for i in range(3)]
    watch.night(w.f, w.root, time.time() - 5, now=time.time())
    since = _second()
    for uid in lists + standing:
        _answer(w, w.f.row_by_id(uid)["url"], MAINT)
    n = watch.night(w.f, w.root, since, now=time.time())
    assert SITE in n.alarms and n.sites_changed == []


def _alarm_night(w, ids, body=MAINT):
    since = _second()
    for i in ids:
        _answer(w, w.f.row_by_id(i)["url"], body)
    return watch.night(w.f, w.root, since, now=time.time())


def test_a_second_maintenance_night_still_holds_the_site(w):
    ids = [_answer(w, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(6)]
    watch.night(w.f, w.root, time.time() - 5, now=time.time())
    assert SITE in _alarm_night(w, ids).alarms
    since = _second()
    for i in ids:
        _answer(w, w.f.row_by_id(i)["url"], MAINT)
    _answer(w, f"https://{SITE}/n1.htm", _html("<p>新公告</p>"))  # and a new notice found tonight
    n2 = watch.night(w.f, w.root, since, now=time.time() + DAY)
    assert SITE in n2.alarms and n2.sites_changed == []


def test_the_rows_an_alarm_held_are_judged_against_the_answer_before_it_and_let_go(w):
    ids = [_answer(w, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(6)]
    watch.night(w.f, w.root, time.time() - 5, now=time.time())
    assert SITE in _alarm_night(w, ids).alarms
    assert set(ids) <= set(json.loads(w.f.meta(watch.HELD_KEY))["ids"])
    since = _second()
    for k, i in enumerate(ids):
        _answer(w, w.f.row_by_id(i)["url"], _html(f"<p>{k}</p>"))  # as it was before the maintenance
    n2 = watch.night(w.f, w.root, since, now=time.time() + DAY)
    assert [_change(n2, i) for i in ids] == ["same"] * 6 and not n2.alarms
    assert all(w.f.q("SELECT changes FROM page_watch WHERE url_id=?", (i,))[0][0] == 0 for i in ids)
    assert w.f.meta(watch.HELD_KEY) is None  # let go


def test_an_alarmed_sites_new_rows_are_held_too(w):
    ids = [_answer(w, f"https://{SITE}/p{i}.htm", _html(f"<p>{i}</p>")) for i in range(6)]
    watch.night(w.f, w.root, time.time() - 5, now=time.time())
    since = _second()
    for i in ids:
        _answer(w, w.f.row_by_id(i)["url"], MAINT)
    new = _answer(w, f"https://{SITE}/n9.htm", MAINT)  # a new notice's detail answered by the maintenance page
    n = watch.night(w.f, w.root, since, now=time.time())
    assert SITE in n.alarms and new in n.again


def test_the_held_rows_go_back_each_night_until_let_go(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        _site(f, R1, "1")
        held = [_url(f, f"/about/p{i}.htm") for i in range(2)]
        f.meta(watch.HELD_KEY, json.dumps({"since": 1.0, "ids": held}))
        for d in _days(3):
            t = update.select(f, tmp_path, today=d)
            assert sorted(t.again) == sorted(held) and not set(held) & set(t.standing)
    finally:
        f.close()


# ---------------------------------------------------------------- the review's window and its files

def _ran(w) -> None:
    w.f.event("info", "run_start", json.dumps({"sites": [SITE]}))
    w.f.event("info", "run_end", json.dumps({"reason": "finished"}))


def _main(w, *argv) -> int:
    w.f.close()
    try:
        return update.main(["--dir", str(w.root), *argv])
    finally:
        w.f = Frontier(w.root / "frontier.db")


def _csv(w) -> str:
    status = json.loads((w.root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    return (w.root / "reports" / status["changes_file"]).read_text(encoding="utf-8")


def test_what_is_fetched_between_a_review_and_the_next_night_is_in_the_next_review(w):
    w.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    w.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    _answer(w, f"https://{SITE}/a.htm", _html("<p>乙</p>"))
    assert _main(w, "put-back", "--today", "2026-11-20") == 0
    _ran(w)
    assert _main(w, "review") == 0
    time.sleep(1.05)
    _answer(w, f"https://{SITE}/a.htm", _html("<p>丙</p>"))  # a person's run in the day, before the next night
    time.sleep(1.05)
    assert _main(w, "put-back", "--today", "2026-11-21") == 0
    _ran(w)
    assert _main(w, "review") == 0
    assert "a.htm,page,changed" in _csv(w)


def test_a_review_that_cannot_write_its_list_does_not_count_the_night(w):
    w.f.meta(update.NIGHT_KEY, repr(time.time() - 5))
    uid = _answer(w, f"https://{SITE}/a.htm", _html("<p>甲</p>"))  # tonight's first answer: a page_watch row
    _ran(w)
    (w.root / "reports").mkdir(parents=True, exist_ok=True)
    night = float(w.f.meta(update.NIGHT_KEY))
    name = f"changes-{update.datetime.fromtimestamp(night, update.TAIPEI):%Y%m%d-%H%M}.csv"
    (w.root / "reports" / name).mkdir()  # the list cannot be written
    with pytest.raises(OSError):
        update.review(w.f, w.root)
    assert w.f.meta(update.REVIEWED_KEY) is None  # not counted: the next review counts it
    assert not w.f.q("SELECT 1 FROM page_watch WHERE url_id=?", (uid,))  # nothing of it written
    (w.root / "reports" / name).rmdir()
    status, done = update.review(w.f, w.root)
    assert done and status["counts"] == {"new": 1}


def test_a_put_back_that_failed_starts_a_status_of_its_own(w):
    w.f.meta(update.NIGHT_KEY, repr(time.time() - 5))
    _ran(w)
    assert _main(w, "review") == 0
    assert _main(w, "fail", "--step", "put-back") == 0  # tonight never began: last night's review is not tonight's
    status = json.loads((w.root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["failed_step"] == "put-back" and "counts" not in status


# ---------------------------------------------------------------- the selection

@pytest.fixture
def site(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    _site(f, R1, "1")
    ids = {"list": _url(f, "/news/list.htm")}
    _walk(f, ids["list"], "mismatch:page", 40)
    yield f, ids, tmp_path
    f.close()


def test_a_whole_walk_that_reached_its_end_is_not_walked_again_the_next_night(site):
    """A list whose walk ends mismatch:page (the school answers page 2 with page 1): a whole walk that ends so again
    reached the list's end as it always does: next month, not tomorrow."""
    f, ids, root = site
    f.x("INSERT INTO list_look(list_id, mode, started, ended_at, ended_by, whole) VALUES (?, 'look', 1, 2, "
        "'mismatch:page', 1)", (ids["list"],))
    assert sum(ids["list"] in update.select(f, root, today=d).walks for d in _days(30)) == 1
    f.x("UPDATE list_look SET ended_by='post_failed' WHERE list_id=?", (ids["list"],))  # a page that did not come
    assert all(ids["list"] in update.select(f, root, today=d).walks for d in _days(3))


def test_a_get_whole_walk_is_walked_again_only_when_its_first_page_did_not_come(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        _site(f, R1, "1")
        g1, g2 = _url(f, "/board/list.php"), _url(f, "/board/list.php?page=2")
        for i in (g1, g2):
            _walk(f, i, "last_page", None)
        for a, b, t in ((g1, g2, "2"), (g2, g1, "1")):
            f.add_page_res(a, [{"res_key": f"https://x/{b}", "how": "get", "role": "pager", "need": "must",
                                "res_id": b, "text": t}])
        for i, end in ((g1, "went_on"), (g2, "not_got:done/404")):  # page 2 always fails
            f.x("INSERT INTO list_look(list_id, mode, started, ended_at, ended_by, whole) VALUES (?, 'look_get', 5, 6, "
                "?, 1)", (i, end))
        assert sum(bool(update.select(f, tmp_path, today=d).walks_get) for d in _days(30)) == 1
        f.x("UPDATE list_look SET ended_by='not_got:done/500' WHERE list_id=?", (g1,))
        assert all(update.select(f, tmp_path, today=d).walks_get == [g1] for d in _days(3))
    finally:
        f.close()


def test_a_page_that_only_ever_answered_a_sign_in_page_is_no_standing_page(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        _site(f, R1, "1")
        good, login = _url(f, "/about/a.htm"), _url(f, "/about/b.htm")
        f.x("UPDATE url SET flags='login' WHERE id=?", (login,))
        seen = [i for d in _days(7) for i in update.select(f, tmp_path, today=d).standing]
        assert seen == [good]
    finally:
        f.close()


def test_the_selection_takes_the_sites_go_live_named(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        _site(f, R1, "1")
        _site(f, "b.nknu.edu.tw", "1")  # named round one after go-live: the run does not fetch it
        mine = _url(f, "/about/a.htm")
        other = _url(f, "/about/b.htm", site="b.nknu.edu.tw")
        f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([R1]))
        seen = {i for d in _days(7) for i in update.select(f, tmp_path, today=d).standing}
        assert mine in seen and other not in seen
    finally:
        f.close()


# ---------------------------------------------------------------- the script

def test_a_stop_before_the_run_starts_no_run(tmp_path):
    py = _fake_python(tmp_path, None)
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"), "STOP_AFTER": "put-back",
           "TZ": "Asia/Taipei", "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    # the fake python sends the script a TERM when it is asked for put-back (a person's docker stop)
    py.write_text(py.read_text().replace('exit 0\n', '[ "$step" = "$STOP_AFTER" ] && kill -TERM $PPID\nexit 0\n'))
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    steps = [line.split()[0] for line in (tmp_path / "calls.log").read_text().splitlines()]
    assert got.returncode == 1 and steps == ["backup", "put-back", "fail"]  # stopped right after put-back
