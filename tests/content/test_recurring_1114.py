"""Plan 2026-11-14 T2 item 3 (direction 2026-11-13 第 2 件 3): the instances of one recurring calendar event are one
event document.

The calendar export holds the Calendar API's items expanded with singleEvents=true: every instance of a recurring
event carries recurringEventId, and its own id is <that id>_<its start>. documents.one_event_per_series makes one
event of the instances of a series that would be documents and share its title: id the series id (so the document is
'e' + it), the first day and the last, and every instance's days, which the document's text lists (nothing dropped).
A single event, an instance with a title of its own and the only instance of a series stay as they are.
The titles are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nknu.content import build, ids
from nknu.content.corpus import Event, load_calendar
from nknu.content.documents import (
    BEFORE,
    BLANK,
    DUPLICATE,
    EVENT_KIND,
    SERIES,
    SERIES_NOTE,
    build_documents,
    one_event_per_series,
    series_text,
)
from nknu.retrieval.respond import read_response
from tests.content import test_multisite_1108 as tm

TODAY = date(2026, 11, 14)
OLD, NEW = "2026-10-27T07:00:00+08:00", "2026-11-07T07:00:00+08:00"
ACA_SITE, TG_SITE = "c.nknu.edu.tw/aca", "c.nknu.edu.tw/tg"
# the calendar id is written without its @ in the file name: the build finds the file by 行事历 alone, and an
# address-shaped string in a test is what nknu check stops (the owner, 11-15: 「有一个像电邮的测试字串被 nknu check
# 拦下，请顺手改掉」)
CAL_FILE = "data/0001-Google 行事历 o.example.invalid 的事件.json"
CAL_URL = "https://www.googleapis.com/calendar/v3/calendars/o%40example.invalid/events"
LINK = "https://www.google.com/calendar/event?eid="


def day(iso: str) -> date:
    return date.fromisoformat(iso)


def ev(eid: str, title: str, start: str, end: str = "", series: str = "", site: str = "", fetched: str = "") -> Event:
    return Event(eid, title, day(start), day(end or start), LINK + eid, site=site, fetched=fetched, series=series)


def item(eid: str, title: str, start: str, end: str, series: str = "", timed: bool = False) -> dict:
    """One Calendar API item: an all-day one ends the day after its last day (end), a timed one at 12:00 of it."""
    when = ({"start": {"dateTime": f"{start}T09:00:00+08:00"}, "end": {"dateTime": f"{end}T12:00:00+08:00"}} if timed
            else {"start": {"date": start}, "end": {"date": end}})
    more = {"recurringEventId": series, "originalStartTime": when["start"]} if series else {}
    return {"id": eid, "status": "confirmed", "summary": title, "htmlLink": LINK + eid, **when, **more}


# ---------------------------------------------------------------- the calendar file

def calendar_input(root: Path, site: str, name: str, items: list[dict], fetched: str = OLD) -> tuple[Path, Path]:
    """An export with one page and a calendar file, and its corpus written by hand (stable ids)."""
    export = root / "export"
    (export / "data").mkdir(parents=True)
    url = f"https://{site}/page.aspx?pn=1"
    key = ids.location_key(url)
    rows = [{"序号": "1", "类型": "网页", "网址": url, "最后落地网址": "", "本地档案": "pages/0001.html", "抓取时间": fetched},
            {"序号": "2", "类型": "资料", "网址": CAL_URL, "本地档案": CAL_FILE, "抓取时间": fetched}]
    (export / "manifest.json").write_text(json.dumps({"网站": site, "名称": name, "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    (export / CAL_FILE).write_text(json.dumps({"kind": "calendar#events", "items": items}, ensure_ascii=False),
                                   encoding="utf-8")
    records = [{"id": ids.record_id(key), "key": key, "kind": "page", "url": url, "file": "pages/0001.html",
                "title": f"{name}辦公時間", "text": "上午八時至下午五時", "site": site, "fetched": fetched}]
    (root / "corpus.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return root / "corpus.json", export


def test_the_calendar_reader_keeps_the_series_id(tmp_path):
    _, export = calendar_input(tmp_path, ACA_SITE, "教务处", [
        item("s1", "期末考試", "2026-12-21", "2026-12-26"),
        item("wk_20260907T010000Z", "族語課", "2026-09-07", "2026-09-07", "wk", timed=True),
        item("yr_20260914", "校慶", "2026-09-14", "2026-09-15", "yr")])
    events = load_calendar(export)
    assert [(e.id, e.series, e.start, e.end_inclusive, e.days) for e in events] == [
        ("s1", "", day("2026-12-21"), day("2026-12-25"), ()),
        ("wk_20260907T010000Z", "wk", day("2026-09-07"), day("2026-09-07"), ()),
        ("yr_20260914", "yr", day("2026-09-14"), day("2026-09-14"), ())]
    assert Event("x", "甲", day("2026-01-01"), day("2026-01-01"), "") == Event(
        "x", "甲", day("2026-01-01"), day("2026-01-01"), "", series="", days=())  # a single event, as before


# ---------------------------------------------------------------- one event per series

def week(series: str = "wk", title: str = "族語課", **more) -> list[Event]:
    """Three instances of one series, the middle one first."""
    return [ev(f"{series}_20260914", title, "2026-09-14", series=series, **more),
            ev(f"{series}_20260907", title, "2026-09-07", series=series, **more),
            ev(f"{series}_20260921", title, "2026-09-21", "2026-09-22", series=series, **more)]


def test_the_instances_of_one_series_are_one_event():
    single = ev("s1", "期末考試", "2026-12-21", "2026-12-25")
    early = ev("wk_20221226", "族語課", "2022-12-26", series="wk")  # before CALENDAR_SINCE: no document, counted there
    other = ev("wk_20260928", "族語課（停課一次）", "2026-09-28", series="wk")  # a title of its own
    blank = ev("wk_20261005", " ", "2026-10-05", series="wk")
    lone = ev("mt_20260910", "系務會議", "2026-09-10", series="mt")  # the only instance of its series
    out, stats = one_event_per_series([single, early, *week(), other, blank, lone])
    one = Event("wk", "族語課", day("2026-09-07"), day("2026-09-22"), LINK + "wk_20260907", series="wk",
                days=((day("2026-09-07"), day("2026-09-07")), (day("2026-09-14"), day("2026-09-14")),
                      (day("2026-09-21"), day("2026-09-22"))))
    assert out == [single, early, one, other, blank, lone]  # in the place of its first instance
    assert stats == {"series": 1, "instances": 3, "other_titles": 1, "id_taken": 0}
    assert one_event_per_series([single]) == ([single], {"series": 0, "instances": 0, "other_titles": 0, "id_taken": 0})
    assert one_event_per_series([])[0] == []


def test_the_title_most_instances_share_names_the_series_and_spaces_do_not_tell_titles_apart():
    odd = [ev("wk_20260928", "族語課  ", "2026-09-28", series="wk"), ev("wk_20261005", "停課", "2026-10-05", series="wk"),
           ev("wk_20261012", "停課", "2026-10-12", series="wk")]
    out, stats = one_event_per_series([*odd[1:], *week(), odd[0]])
    assert [(e.id, e.title, len(e.days)) for e in out] == [("wk_20261005", "停課", 0), ("wk_20261012", "停課", 0),
                                                            ("wk", "族語課", 4)]
    assert stats == {"series": 1, "instances": 4, "other_titles": 2, "id_taken": 0}


def test_the_newest_fetch_gives_the_site_and_the_earliest_day_the_address():
    older = week(site=ACA_SITE, fetched=OLD)
    newer = ev("wk_20260928", "族語課", "2026-09-28", series="wk", site=TG_SITE, fetched=NEW)
    [one], _ = one_event_per_series([*older, newer])
    assert (one.site, one.fetched, one.url, len(one.days)) == (TG_SITE, NEW, LINK + "wk_20260907", 4)
    [tie], _ = one_event_per_series(week(site=ACA_SITE, fetched=OLD))  # one fetch: the first in the events' order
    assert (tie.site, tie.fetched) == (ACA_SITE, OLD)


def test_a_series_whose_id_another_event_has_is_left_as_it_is():
    taken = ev("wk", "別的活動", "2026-10-01")
    events = [taken, *week()]
    assert one_event_per_series(events) == (events, {"series": 0, "instances": 0, "other_titles": 0, "id_taken": 1})


# ---------------------------------------------------------------- the text

def test_the_text_lists_every_day_by_month():
    days = tuple((day(a), day(b)) for a, b in (
        ("2026-09-07", "2026-09-07"), ("2026-09-14", "2026-09-14"), ("2026-09-29", "2026-10-01"),
        ("2026-12-30", "2027-01-02"), ("2027-01-11", "2027-01-12"), ("2027-01-11", "2027-01-12"),
        ("2027-01-20", "2027-01-20")))
    assert SERIES_NOTE.startswith("【系統】")
    assert series_text("族語課", days).split("\n") == [
        "族語課",
        "【系統】共 7 次，日期（年-月：日）：",
        "2026-09：07、14、29～10-01",  # into the next month: its month and day
        "2026-12：30～2027-01-02",  # into the next year: the whole date
        "2027-01：11～12（2 次）、20"]  # two instances on the same days


def test_a_series_is_one_document_and_everything_else_is_as_before():
    early = ev("wk_20221226", "族語課", "2022-12-26", series="wk")
    blank = ev("wk_20261005", " ", "2026-10-05", series="wk")
    single = ev("s1", "期末考試", "2026-12-21", "2026-12-25", site=ACA_SITE)
    merged, _ = one_event_per_series([single, early, *week(site=TG_SITE), blank])
    docs, excluded = build_documents([], {}, merged)
    assert excluded == {EVENT_KIND + BEFORE: 1, EVENT_KIND + BLANK: 1}
    assert [(d.doc_id, d.kind_class, d.kind_raw, d.title, d.unit, d.site, d.has_text) for d in docs] == [
        ("es1", "event", EVENT_KIND, "期末考試", "教務處", ACA_SITE, True),
        ("ewk", "event", EVENT_KIND, "族語課", "成人教育研究所", TG_SITE, True)]
    assert docs[0].text == "期末考試"
    assert docs[1].text == "族語課\n【系統】共 3 次，日期（年-月：日）：\n2026-09：07、14、21～22"
    assert docs[1].url == LINK + "wk_20260907"


# ---------------------------------------------------------------- the build

ITEMS = [item("s1", "期末考試", "2026-12-21", "2026-12-26"),
         item("wk_20221226", "族語課", "2022-12-26", "2022-12-27", "wk"),
         item("wk_20260914", "族語課", "2026-09-14", "2026-09-15", "wk"),
         item("wk_20260907", "族語課", "2026-09-07", "2026-09-08", "wk"),
         item("wk_20260921", "族語課", "2026-09-21", "2026-09-23", "wk"),
         item("wk_20260928", "族語課（停課一次）", "2026-09-28", "2026-09-29", "wk"),
         item("wk_20261005", "", "2026-10-05", "2026-10-06", "wk"),
         item("mt_20260910", "系務會議", "2026-09-10", "2026-09-11", "mt"),
         # a series that runs into the next semester
         item("tu_20261215T010000Z", "導師會議", "2026-12-15", "2026-12-15", "tu", timed=True),
         item("tu_20270316T010000Z", "導師會議", "2027-03-16", "2027-03-16", "tu", timed=True)]
WK_TEXT = "族語課\n【系統】共 3 次，日期（年-月：日）：\n2026-09：07、14、21～22"
TU_TEXT = "導師會議\n【系統】共 2 次，日期（年-月：日）：\n2026-12：15\n2027-03：16"


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "db" / "x.db"
    inputs = [calendar_input(tmp_path / "aca", ACA_SITE, "教务处", ITEMS)]
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=inputs)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn, inputs
    conn.close()


def test_the_build_writes_one_event_per_series(built):
    report, conn, _ = built
    events = {r[0]: r[1:] for r in conn.execute(
        "SELECT doc_id, title, text, period_ay, period_sem, has_text FROM docs WHERE kind_class = 'event'")}
    assert events == {"es1": ("期末考試", "期末考試", 115, 1, 1), "ewk": ("族語課", WK_TEXT, 115, 1, 1),
                      "ewk_20260928": ("族語課（停課一次）", "族語課（停課一次）", 115, 1, 1),
                      "emt_20260910": ("系務會議", "系務會議", 115, 1, 1), "etu": ("導師會議", TU_TEXT, 115, 1, 1)}
    # the first day and the last: what dates an event and says when it is over
    assert conn.execute("SELECT kind, date, source FROM dates WHERE doc_id = 'ewk' ORDER BY rowid").fetchall() == [
        ("event_start", "2026-09-07", "行事曆"), ("event_end", "2026-09-22", "行事曆")]
    assert conn.execute("SELECT kind, date FROM dates WHERE doc_id = 'etu' ORDER BY rowid").fetchall() == [
        ("event_start", "2026-12-15"), ("event_end", "2027-03-16")]
    assert report.excluded == {EVENT_KIND + BEFORE: 1, EVENT_KIND + BLANK: 1, EVENT_KIND + SERIES: 3}
    assert report.counts["docs"]["event"] == 5
    assert report.dedupe["recurring"] == {"on": True, "series": 2, "instances": 5, "removed": 3, "other_titles": 1,
                                          "id_taken": 0, "events_before": 8, "events_after": 5,
                                          "by_site": {ACA_SITE: {"series": 2, "instances": 5}}}
    assert conn.execute("SELECT value FROM meta WHERE key = 'recurring_events'").fetchone() == ("on",)
    assert not [k for (k,) in conn.execute("SELECT key FROM meta") if "series" in k]  # test_build's guard on meta keys


def test_a_series_is_found_by_every_term_it_runs_in(built):
    _, conn, _ = built
    tokens = {doc: set(title.split()) for doc, title in conn.execute(
        "SELECT c.doc_id, f.title_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
        " WHERE c.doc_id IN ('etu', 'ewk', 'es1')")}
    assert {"term115s1", "term115s2", "ay115"} <= tokens["etu"]
    assert "term115s1" in tokens["ewk"] and "term115s2" not in tokens["ewk"]
    assert "term115s1" in tokens["es1"] and "term115s2" not in tokens["es1"]  # a single event: its own term, as before


def test_read_shows_the_days_and_one_span(built):
    _, conn, _ = built
    resp = read_response(conn, "ewk", "", today=TODAY)
    assert resp["found"] and resp["content"] == WK_TEXT
    assert [(d["type"], d["date"]) for d in resp["dates"]] == [("開始", "2026-09-07"), ("結束", "2026-09-22")]
    assert "2026-09-07～2026-09-22（行事曆）" in resp["identity"]


def test_the_command_line_counts_the_series_it_merged(built, tmp_path, capsys):
    _, _, inputs = built
    paths = tm.tree_files(tmp_path / "tree2")
    (corpus, export), = inputs
    rest = ["--input", f"{corpus}::{export}", "--aliases", str(paths["aliases"]), "--server-dates",
            str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*rest, "--out", str(tmp_path / "cli" / "on.db")]) == 0
    assert "行事历重复系列 2 个各合成一份（原 5 个单次事件；事件 8 → 5 份）" in capsys.readouterr().out
    saved = json.loads((tmp_path / "cli" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["dedupe"]["recurring"]["on"] is True and saved["dedupe"]["recurring"]["series"] == 2


def test_one_calendar_in_two_exports_is_deduplicated_first_then_merged(tmp_path):
    # each export brings every instance: one per id stays (the newest fetch), then one event per series
    inputs = [calendar_input(tmp_path / "aca", ACA_SITE, "教务处", ITEMS, OLD),
              calendar_input(tmp_path / "tg", TG_SITE, "成人教育研究所", ITEMS[:5], NEW)]
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=inputs)
    assert report.excluded == {EVENT_KIND + DUPLICATE: 5, EVENT_KIND + BEFORE: 1, EVENT_KIND + BLANK: 1,
                               EVENT_KIND + SERIES: 3}
    assert report.dedupe["recurring"]["by_site"] == {TG_SITE: {"series": 1, "instances": 3},
                                                     ACA_SITE: {"series": 1, "instances": 2}}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        sites = dict(conn.execute("SELECT doc_id, site FROM docs WHERE kind_class = 'event'"))
    finally:
        conn.close()
    # the series 成教所's export fetched later is its; the one only 教务处's holds stays 教务处's
    assert sites == {"es1": TG_SITE, "ewk": TG_SITE, "ewk_20260928": ACA_SITE, "emt_20260910": ACA_SITE,
                     "etu": ACA_SITE}
