"""11-19 (the wrap-up's 每学期初自动重查课表): the course query shows one term at a time, the page's own. The nightly
update reads the term off the query page it stored (the site's home, fetched every night) and, when it is no longer the
term whose classes the ledger holds, walks the form again and hands the new classes to the crawler, with no person in
between. While the new term's pages come in, the rows of this one site that the nightly growth cap holds are let go, a
bounded number a night; once nothing waits the site is under the cap again. A page that cannot be read, or a walk
that did not end, changes nothing and says so. Made-up departments, classes and courses only."""

from __future__ import annotations

import json

import pytest

from nknu.archive import crawler as crawl_mod
from nknu.archive import schedules
from nknu.archive.frontier import Frontier
from tests.archive.test_postback import World
from tests.archive.test_schedules_1118 import BASE, SITE, School, _page

HOME = schedules.PAGE
FIRST, SECOND = "115 第一學期", "115 第二學期"


def _second(html: str) -> str:
    return html.replace('value="1">第一學期', 'value="2">第二學期')


class SecondTerm(School):
    """The school once its query shows the second term."""

    def __call__(self, url, data):
        return _second(super().__call__(url, data))


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    yield w
    w.f.close()


def _home(w, html: str) -> None:
    w.page(HOME, SITE, html)


def _night(w, monkeypatch, school=None, *, today: str = "2027-02-01", limit: int = schedules.LIMIT) -> int:
    """The nightly step, the school behind `_school` (None: any request fails the test)."""
    asked = school if school is not None else _no_school
    monkeypatch.setattr(schedules, "_school", lambda gap: (asked, lambda: None))
    w.f.close()
    try:
        return schedules.night(w.root, gap=(1.0, 2.0), limit=limit, today=today)
    finally:
        w.f = Frontier(w.root / "frontier.db")


def _no_school(url, data):
    raise AssertionError(f"the school was asked: {url}")


def _status(w) -> dict:
    return json.loads((w.root / "reports" / schedules.TERM_REPORT).read_text(encoding="utf-8"))


def _held(w, n: int, site: str = SITE, start: int = 0) -> None:
    host = site.split("/")[0]
    w.f.set_host(host, queue="ip:test")
    for i in range(start, start + n):
        w.f.add(f"https://{site}/syllabusforenro.aspx?syllabusno=9{i:05d}", host=host, site=host, unit=None, tier=1,
                kind="page", depth=2, found_via="link", found_from=None, owner_site=site, kind0="page", added_at=1.0)
    w.f.x("UPDATE url SET state='held', reason='cap:run_page' WHERE owner_site=? AND state='pending'", (site,))


def test_the_page_s_own_term_is_read_as_the_walk_reads_it():
    assert schedules.page_term(_page("D", "1", None, None, None)) == FIRST
    assert schedules.page_term(_second(_page("D", "1", None, None, None))) == SECOND
    assert schedules.page_term("<html><body>維護中</body></html>") == ""


def test_the_same_term_sends_nothing(world, monkeypatch, capsys):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    assert _night(world, monkeypatch) == 0
    assert "没变" in capsys.readouterr().out
    got = _status(world)
    assert (got["page_term"], got["ledger_term"], got["changed"], got["walk"]) == (FIRST, FIRST, False, None)
    assert world.f.meta(schedules.OPEN_KEY) is None


def test_a_ledger_with_no_term_on_record_takes_the_last_whole_walk_s(world, monkeypatch):
    """The lab's ledger was walked on 11-18, before the term was kept in the ledger: the walk's own report says it."""
    _home(world, _page("D", "1", None, None, None))
    (world.root / "reports").mkdir(exist_ok=True)
    (world.root / "reports" / schedules.REPORT).write_text(
        json.dumps({"complete": True, "term": FIRST, "urls": {}, "classes": []}), encoding="utf-8")
    assert _night(world, monkeypatch) == 0
    assert world.f.meta(schedules.TERM_KEY) == FIRST  # kept in the ledger from now on


def test_a_new_term_is_walked_and_its_classes_handed_to_the_crawler(world, monkeypatch, capsys):
    _home(world, _second(_page("D", "1", None, None, None)))
    world.f.meta(schedules.TERM_KEY, FIRST)
    school = SecondTerm()
    assert _night(world, monkeypatch, school) == 0
    assert len(school.asked) == 11  # the whole form, once
    rows = world.f.q("SELECT url, state, depth FROM url WHERE owner_site=? AND url LIKE '%cdes=%' ORDER BY url", (SITE,))
    assert len(rows) == 5 and {(r["state"], r["depth"]) for r in rows} == {("pending", 1)}
    assert rows[0]["url"].startswith(BASE.lower() + "scheduledepartment.aspx?cdes=TOK")
    assert world.f.meta(schedules.TERM_KEY) == SECOND
    assert json.loads(world.f.meta(schedules.OPEN_KEY)) == {"term": SECOND, "opened": "2027-02-01"}
    said = world.f.q("SELECT level, message FROM event WHERE code='schedule_term'")
    assert len(said) == 1 and said[0]["level"] == "warn" and FIRST in said[0]["message"] and SECOND in said[0]["message"]
    got = _status(world)
    assert (got["changed"], got["walk"], got["open"]) == (True, "done", SECOND)
    assert "变了" in capsys.readouterr().out


def test_a_walk_that_did_not_end_changes_nothing_and_the_next_night_walks_again(world, monkeypatch, capsys):
    _home(world, _second(_page("D", "1", None, None, None)))
    world.f.meta(schedules.TERM_KEY, FIRST)
    assert _night(world, monkeypatch, SecondTerm(), limit=4) == 1
    assert world.f.meta(schedules.TERM_KEY) == FIRST and world.f.meta(schedules.OPEN_KEY) is None
    assert world.f.q("SELECT count(*) FROM url WHERE url LIKE '%cdes=%'")[0][0] == 0
    assert _status(world)["walk"] == "not whole" and "没走完" in capsys.readouterr().out
    school = SecondTerm()
    assert _night(world, monkeypatch, school, today="2027-02-02") == 0
    assert world.f.meta(schedules.TERM_KEY) == SECOND and len(school.asked) == 11


def test_a_walk_that_meets_another_term_than_the_page_stored_keeps_the_walk_s(world, monkeypatch):
    """The term on record is the one the form was sent with, whatever the stored page of a night ago showed."""
    _home(world, _second(_page("D", "1", None, None, None)))
    world.f.meta(schedules.TERM_KEY, "114 第二學期")
    assert _night(world, monkeypatch, School()) == 0  # the live page still shows the first term
    assert world.f.meta(schedules.TERM_KEY) == FIRST


@pytest.mark.parametrize("html", [None, "<html><body>系統維護中</body></html>"])
def test_a_query_page_that_cannot_be_read_is_said_and_nothing_is_sent(world, monkeypatch, capsys, html):
    if html is not None:
        _home(world, html)
    world.f.meta(schedules.TERM_KEY, FIRST)
    assert _night(world, monkeypatch) == 1
    assert "读不到" in capsys.readouterr().out
    got = _status(world)
    assert got["page_term"] is None and got["changed"] is None and world.f.meta(schedules.TERM_KEY) == FIRST


def test_while_a_term_is_open_the_site_s_rows_the_growth_cap_holds_are_let_go_a_bounded_number_a_night(
        world, monkeypatch):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    world.f.meta(schedules.OPEN_KEY, json.dumps({"term": FIRST, "opened": "2027-01-30"}))
    monkeypatch.setattr(schedules, "LET_GO", 3)
    _held(world, 5)
    _held(world, 2, site="a.nknu.edu.tw")
    assert _night(world, monkeypatch) == 0
    by = dict(world.f.q("SELECT state, count(*) FROM url WHERE owner_site=? AND url LIKE '%syllabusno=9%' GROUP BY 1",
                        (SITE,)))
    assert by == {"pending": 3, "held": 2}
    assert world.f.q("SELECT count(*) FROM url WHERE owner_site='a.nknu.edu.tw' AND state='held'")[0][0] == 2
    got = _status(world)
    assert (got["let_go"], got["held_left"], got["open"]) == (3, 2, FIRST)
    assert world.f.q("SELECT count(*) FROM event WHERE code='schedule_let_go'")[0][0] == 1


def test_with_no_term_open_the_growth_cap_s_rows_wait_for_a_person_as_before(world, monkeypatch):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    _held(world, 4)
    assert _night(world, monkeypatch) == 0
    assert world.f.q("SELECT count(*) FROM url WHERE state='held'")[0][0] == 4 and _status(world)["let_go"] == 0


def test_a_term_closes_once_nothing_of_the_site_waits_and_never_on_the_night_it_opened(world, monkeypatch):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    world.f.meta(schedules.OPEN_KEY, json.dumps({"term": FIRST, "opened": "2027-02-01"}))
    assert _night(world, monkeypatch, today="2027-02-01") == 0
    assert world.f.meta(schedules.OPEN_KEY) is not None  # its class pages are fetched by the run that follows
    assert _night(world, monkeypatch, today="2027-02-02") == 0
    assert world.f.meta(schedules.OPEN_KEY) is None and _status(world)["open"] is None


def test_a_term_still_open_after_three_weeks_is_closed_and_said(world, monkeypatch):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    world.f.meta(schedules.OPEN_KEY, json.dumps({"term": FIRST, "opened": "2027-01-01"}))
    monkeypatch.setattr(schedules, "LET_GO", 1)
    _held(world, 3)
    assert _night(world, monkeypatch, today="2027-01-23") == 0
    assert world.f.meta(schedules.OPEN_KEY) is None
    assert world.f.q("SELECT count(*) FROM url WHERE state='held'")[0][0] == 3  # left for a person, as any other site's
    assert world.f.q("SELECT level FROM event WHERE code='schedule_term_closed'")[0][0] == "warn"


def test_the_step_does_nothing_while_a_run_holds_the_ledger(world, monkeypatch, capsys):
    _home(world, _second(_page("D", "1", None, None, None)))
    world.f.meta(schedules.TERM_KEY, FIRST)
    monkeypatch.setattr(crawl_mod, "lock_holder", lambda root: 4242)
    assert _night(world, monkeypatch) == 1
    assert "run.lock" in capsys.readouterr().out and world.f.meta(schedules.TERM_KEY) == FIRST


def test_the_command_line_has_the_night_s_step(world, monkeypatch):
    _home(world, _page("D", "1", None, None, None))
    world.f.meta(schedules.TERM_KEY, FIRST)
    monkeypatch.setattr(schedules, "_school", lambda gap: (_no_school, lambda: None))
    world.f.close()
    try:
        assert schedules.main(["--dir", str(world.root), "night"]) == 0
    finally:
        world.f = Frontier(world.root / "frontier.db")


def test_the_nightly_script_asks_between_its_put_back_and_its_run_and_goes_on_whatever_the_step_says(tmp_path):
    import os
    import subprocess

    from tests.archive.test_update_stage2_1116 import SCRIPT, _fake_python

    if not SCRIPT.is_file():
        pytest.skip("deploy/lab-crawler/lab/ is not in this checkout")
    py = _fake_python(tmp_path, "night")  # the step fails: a page that could not be read, a walk that did not end
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "backups"),
           "LOCKS": str(tmp_path / "locks"), "TZ": "Asia/Taipei",
           "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert got.returncode == 0
    assert [c.split()[0] for c in calls] == ["warc-there", "backup", "put-back", "night", "sites", "run", "review", "export-list",
                                             "seen"]
    asked = next(c for c in calls if c.startswith("night "))
    assert "nknu.archive.schedules" in asked and "--gap 1-2" in asked
    assert "下一晚再看" in (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
