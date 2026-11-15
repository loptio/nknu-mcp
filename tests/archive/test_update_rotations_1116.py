"""Periodic updates, stage 2, Task 3 (plan docs/superpowers/plans/2026-11-16-update-stage-2.md; design 2026-11-16
§7.3): tonight's rotations and speed-up rules (nknu.archive.update.select): standing pages one share a week, every
list walked whole one share a month, notices of the last 30 days one share a week, and the three speed-up rules. A
ledger built by hand (made-up addresses), as in test_update_select_1116."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pytest

from nknu.archive import update
from nknu.archive.frontier import Frontier
from tests.archive.test_update_select_1116 import R1, _pager, _site, _url, _walk

DAY = date(2026, 11, 20)


def _days(n: int = 30):
    return [DAY + timedelta(days=i) for i in range(n)]


def _row(f: Frontier, list_id: int, href: str, when: str) -> None:
    f.x("INSERT INTO list_row(list_id, ident, row_key, href, title, date, category, first_seen, last_seen, changed_at) "
        "VALUES (?,?,?,?,?,?,NULL,1,1,NULL)", (list_id, href, href, href, "公告", when))


@pytest.fixture
def site(tmp_path):
    """A round-one site: its home, eight standing pages, a list walked by button, a list paged by GET in two pages, a
    notice linked by the list's rows, a page the home's menu links."""
    f = Frontier(tmp_path / "frontier.db")
    _site(f, R1, "1")
    ids = {"home": _url(f, "/", depth=0, found_via="seed")}
    f.seed_src(f"https://{R1}/", "site_home")
    ids["standing"] = [_url(f, f"/about/p{i}.htm") for i in range(8)]
    ids["list"] = _url(f, "/news/list.htm")
    _walk(f, ids["list"], "last_page", 40)
    g1, g2 = _url(f, "/board/list.php"), _url(f, "/board/list.php?page=2")
    for i in (g1, g2):
        _walk(f, i, "last_page", None)
    _pager(f, g1, g2, "2")
    _pager(f, g2, g1, "1")
    ids.update(g1=g1, g2=g2)
    ids["notice"] = _url(f, "/news/n1.htm")
    _row(f, ids["list"], f"https://{R1}/news/n1.htm", "2026-11-10")
    ids["old_notice"] = _url(f, "/news/n0.htm")
    _row(f, ids["list"], f"https://{R1}/news/n0.htm", "2026-09-01")
    ids["file"] = _url(f, "/news/a.pdf", kind="file")
    yield f, ids, tmp_path
    f.close()


def test_a_standing_page_is_taken_once_a_week_on_its_share(site):
    f, ids, root = site
    seen: list[int] = []
    for d in _days(7):
        t = update.select(f, root, today=d)
        seen += t.standing
        assert set(t.standing) <= set(ids["standing"])  # never the home, a list, a list's page, a notice, a file
    assert sorted(seen) == sorted(ids["standing"])  # each once in seven days


def test_a_list_is_walked_whole_once_a_month_and_is_no_look_that_night(site):
    f, ids, root = site
    walked = [d for d in _days(30) if ids["list"] in update.select(f, root, today=d).walks]
    assert len(walked) == 1
    t = update.select(f, root, today=walked[0])
    assert ids["list"] not in t.looks
    assert ids["list"] in update.select(f, root, today=walked[0] + timedelta(days=1)).looks


def test_a_list_paged_by_get_is_walked_whole_page_by_page(site):
    f, ids, root = site
    days = [d for d in _days(30) if update.select(f, root, today=d).walks_get]
    assert len(days) == 1
    t = update.select(f, root, today=days[0])
    assert sorted(t.walks_get) == sorted([ids["g1"], ids["g2"]]) and ids["g1"] not in t.looks_get


def test_a_walk_with_no_end_is_walked_whole_on_its_day(site):
    f, ids, root = site
    _walk(f, ids["list"], "walking", 12)
    days = [d for d in _days(30) if ids["list"] in update.select(f, root, today=d).walks]
    assert len(days) == 1 and update.select(f, root, today=days[0] + timedelta(days=1)).skipped["walk_unfinished"] == 1


def test_a_recent_notice_is_read_again_weekly_and_an_old_one_never(site):
    f, ids, root = site
    got = [d for d in _days(7) if ids["notice"] in update.select(f, root, today=d).recent]
    assert len(got) == 1
    assert not any(ids["old_notice"] in update.select(f, root, today=d).recent for d in _days(7))
    later = DAY + timedelta(days=40)  # the notice of 11-10 is no longer recent on 12-30
    assert not any(ids["notice"] in update.select(f, root, today=later + timedelta(days=i)).recent for i in range(7))


def test_rule_one_a_changed_notice_brings_the_standing_page_and_the_file_it_links(site):
    f, ids, root = site
    last = time.time() - 3600
    f.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes) VALUES (?, 'x', ?, ?, 1)",
        (ids["notice"], last + 60, last + 60))
    menu, linked = ids["standing"][0], ids["standing"][1]
    for dst in (menu, linked, ids["file"]):
        f.link(ids["notice"], f.row_by_id(dst)["url"], "看", "take")
    f.link(ids["home"], f.row_by_id(menu)["url"], "選單", "take")  # in the menu: its weekly turn is enough
    for d in _days(7):
        t = update.select(f, root, today=d, last_night=last)
        assert linked in t.standing + t.follow and ids["file"] in t.follow
        assert menu not in t.follow


def test_rule_two_the_offices_standing_pages_every_night_in_their_weeks(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        office = "c.nknu.edu.tw/aca"
        _site(f, office, "1")
        pages = [_url(f, f"/aca/p{i}.aspx", site="c.nknu.edu.tw") for i in range(3)]
        f.x("UPDATE url SET owner_site=? WHERE id IN (?,?,?)", (office, *pages))
        in_term = date(2026, 9, 1)
        assert sorted(update.select(f, tmp_path, today=in_term).standing) == sorted(pages)
        assert len(update.select(f, tmp_path, today=date(2026, 11, 20)).standing) <= 1
    finally:
        f.close()


def test_rule_three_a_page_changed_twice_running_is_read_every_other_night(site):
    f, ids, root = site
    now = time.time()
    often, quiet = ids["standing"][2], ids["standing"][3]
    f.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes) VALUES (?, 'x', ?, ?, 2)",
        (often, now - 2 * 86400 + 600, now - 2 * 86400 + 600))  # two nights ago, a few minutes later in the evening
    f.x("INSERT INTO page_watch(url_id, fp, checked_at, changed_at, changes) VALUES (?, 'x', ?, ?, 2)",
        (quiet, now - 86400, now - 86400))  # last night: not yet
    for d in _days(7):
        t = update.select(f, root, today=d, now=now)
        assert often in t.standing
        assert quiet not in t.standing or quiet % 7 == d.toordinal() % 7


def test_each_url_is_taken_once_a_night(site):
    f, _ids, root = site
    for d in _days(30):
        t = update.select(f, root, today=d)
        assert len(set(t.ids())) == len(t.ids())


def test_put_back_walks_the_months_lists_whole(site):
    f, ids, root = site
    day = next(d for d in _days(30) if ids["list"] in update.select(f, root, today=d).walks)
    f.close()
    assert update.main(["--dir", str(root), "go-live"]) == 0
    assert update.main(["--dir", str(root), "put-back", "--today", day.isoformat()]) == 0
    g = Frontier(root / "frontier.db")
    try:
        assert g.q("SELECT ended_by FROM listwalk WHERE list_id=?", (ids["list"],))[0][0] == "walking"
        assert g.row_by_id(ids["list"])["state"] == "pending"
        assert not g.q("SELECT 1 FROM list_look WHERE list_id=? AND ended_at IS NULL", (ids["list"],))
        assert json.loads(g.meta(update.WALKS_KEY)) == {str(ids["list"]): 40}
    finally:
        g.close()
