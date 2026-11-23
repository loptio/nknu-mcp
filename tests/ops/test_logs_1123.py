"""紀錄 (nknu.ops.logs, the 11-23 port of the design copy's logview.py / lvfix.py / content_night.py): every step's log
merged by time with its level and source; a window, its chips' counts and its 72-column histogram; pages of 500 newest
first with a cursor; an export line once (export.log's, not update.log's copy, critic CR-06); the ledger's events
with their own level (CR-07); a lane line's time from its end; secrets masked; school words never converted. Made-up
logs in the shapes the programs write (update.log's logging lines, export_sites.sh say(), lanes.sh, build.sh,
nightly_ingest.say)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nknu.core.timeutil import TAIPEI
from nknu.ops import logs
from nknu.ops.pipeline import EXPORTS, PIPE, Where

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=TAIPEI)
UPDATE = """2026-11-23 01:00:02,100 INFO 定期更新开始
2026-11-23 01:00:05,200 INFO q-ip:140.127.62.110 HTTP Request: GET https://c.nknu.edu.tw/aca/a.html "HTTP/1.1 200 OK"
2026-11-23 01:00:06,300 WARNING 重试 3 次的网址：https://c.nknu.edu.tw/x?key=AIzaSyA1234567890abcdefXYZ
2026-11-23 01:00:07,000 DEBUG 细节
2026-11-23 01:30:00 开始 c.nknu.edu.tw_aca → 导出
Traceback (most recent call last):
  File "x.py", line 1
2026-11-23 01:40:00,000 ERROR 导出失败 2 站
2026-11-23 01:45:00,000 INFO 张岳印老师的网页 2026-11-23 01:45:00
"""
EXPORT_LOG = """2026-11-23 01:30:00 开始 c.nknu.edu.tw_aca → 导出
  nknu archive export：写了 120 行
2026-11-23 01:35:00 这一批结束：1 站
"""
LANE_A = """[lane A] 开线
[lane A] run 1 start 2026-11-23 02:10:00
转文字：导出里 36 个档，选了 3 个
[lane A] run 1 exit 1 2026-11-23 02:11:00
"""
BUILD_LOG = """[build 2026-11-23 02:20:00] 步骤 5 建库
-rw-r--r-- 1 root root 611 Nov 22 02:57 research/x.json
total 12
Authorization: Bearer abc.def
"""
INGEST_LOG = """[ingest 2026-11-23 02:05:00] 步骤 convert
[ingest 2026-11-23 02:50:00] 结束：swapped
"""


@pytest.fixture(autouse=True)
def fresh():
    logs.reset()
    yield
    logs.reset()


def make(tmp_path: Path, *, export: bool = True, events: list[tuple] | None = None) -> Where:
    archive = tmp_path / "crawl"
    archive.mkdir()
    (archive / "update.log").write_text(UPDATE, encoding="utf-8")
    kit = tmp_path / PIPE / "lab_pc"
    kit.mkdir(parents=True)
    (kit / "laneA.log").write_text(LANE_A, encoding="utf-8")
    (kit / "build.log").write_text(BUILD_LOG, encoding="utf-8")
    (kit / "ingest.log").write_text(INGEST_LOG, encoding="utf-8")
    if export:
        (tmp_path / EXPORTS).mkdir(parents=True)
        (tmp_path / EXPORTS / "export.log").write_text(EXPORT_LOG, encoding="utf-8")
    if events is not None:
        conn = sqlite3.connect(archive / "frontier.db")
        conn.execute("CREATE TABLE event(at TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, host TEXT, url TEXT,"
                     " message TEXT)")
        conn.executemany("INSERT INTO event VALUES (?, ?, ?, ?, ?, ?)", events)
        conn.commit()
        conn.close()
    return Where(root=tmp_path, db=tmp_path / PIPE / "aca.db", archive=archive)


def every(where: Where, **kw) -> dict:
    return logs.window(where, levels={"error", "warn", "info", "debug"}, now=NOW, **kw)


def test_every_log_is_one_list_newest_first_with_level_and_source(tmp_path, monkeypatch):
    monkeypatch.setattr(logs.pl, "open_ledger", lambda a: (_ for _ in ()).throw(sqlite3.OperationalError("none")))
    got = every(make(tmp_path))
    rows = got["rows"]
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    by = {r[3]: (r[1], r[2]) for r in rows}
    assert by["定期更新开始"] == ("info", "crawl")
    # the request line without its q-ip: and 「HTTP Request: 」 prefixes
    assert by['GET https://c.nknu.edu.tw/aca/a.html "HTTP/1.1 200 OK"'] == ("info", "crawl")
    assert by["细节"] == ("debug", "crawl") and by["导出失败 2 站"] == ("error", "crawl")
    # a line with no time of its own takes the one before it; a traceback is an error
    assert ("Traceback (most recent call last):", "error") in [(r[3], r[1]) for r in rows]
    assert next(r for r in rows if r[3].startswith("  File"))[0] == "2026-11-23T01:30:00"
    # school words as written (no conversion: 张岳印 never becomes 張嶽印); the trailing time cut
    assert "张岳印老师的网页" in by and by["张岳印老师的网页"] == ("info", "crawl")
    # the lane's time from the end of its line, or the last before it; the first line, with none yet, is dropped
    lane = [r for r in rows if r[2] == "totext"]
    assert [(r[0], r[1], r[3]) for r in lane] == [
        ("2026-11-23T02:11:00", "error", "[lane A] run 1 exit 1"),
        ("2026-11-23T02:10:00", "info", "转文字：导出里 36 个档，选了 3 个"),
        ("2026-11-23T02:10:00", "info", "[lane A] run 1 start")]
    # build.log without its file listings; ingest.log by its stamps
    # (build.sh's own stamp 「[build …]」; a line with none takes the one before it)
    assert [r[3] for r in rows if r[2] == "build"] == ["Authorization: （遮掉）", "步骤 5 建库"]
    assert [r[3] for r in rows if r[2] == "ingest"] == ["结束：swapped", "步骤 convert"]
    assert got["total"] == len(rows) and not got["more"] and got["next"] is None


def test_an_export_line_is_shown_once_from_export_log(tmp_path):
    got = every(make(tmp_path))
    export = [r[3] for r in got["rows"] if r[2] == "export"]
    assert export == ["这一批结束：1 站", "  nknu archive export：写了 120 行", "开始 c.nknu.edu.tw_aca → 导出"]
    assert sum(1 for r in got["rows"] if "开始 c.nknu.edu.tw_aca" in r[3]) == 1  # update.log's copy left out


def test_without_export_log_the_export_lines_of_update_log_are_shown_as_export(tmp_path):
    got = every(make(tmp_path, export=False))
    assert [r[3] for r in got["rows"] if r[2] == "export"] == ["开始 c.nknu.edu.tw_aca → 导出"]


# every line export_sites.sh say()s, as it tees them into export.log and (through nightly_update.sh) update.log
SAY = ["2026-11-23 01:30:00 这一批 4 个网站，现在可以导出的 2 个",
       "2026-11-23 01:30:30 暂缓 c.nknu.edu.tw_v：export-hold.txt 写着（没写原因），这一次不导，现用的不动",
       "2026-11-23 01:31:00 开始 c.nknu.edu.tw_aca → c.nknu.edu.tw_aca-20261123-0131",
       "2026-11-23 01:32:00 c.nknu.edu.tw_x 还没爬完，跳过（还有网址或按钮在排队）",
       "2026-11-23 01:33:00 拦下 c.nknu.edu.tw_y：少了一半；现用的还是 c.nknu.edu.tw_y-1",
       "2026-11-23 01:34:00 失败 c.nknu.edu.tw_w：资料夹 out 留着看原因，下次开工时清掉",
       "2026-11-23 01:35:00 这一批结束：成功 1，失败 1，跳过 1，拦下 1（current.tsv 是每站现用的资料夹）"]


def test_every_line_the_export_says_is_shown_once_whatever_it_says(tmp_path):
    """CR-06: say() writes each line into both logs, so an update.log line export.log also holds (the same second and
    words) is left out, not only the ones the export's words pattern knows; the chips and the histogram count it once."""
    where = make(tmp_path)
    (where.archive / "update.log").write_text("2026-11-23 01:00:00,000 INFO 定期更新开始\n" + "\n".join(SAY) + "\n"
                                              "2026-11-23 01:36:00,000 INFO 定期更新结束\n", encoding="utf-8")
    (tmp_path / EXPORTS / "export.log").write_text(
        "\n".join(SAY[:3]) + "\n  nknu archive export：写了 120 行\n" + "\n".join(SAY[3:]) + "\n", encoding="utf-8")
    got = every(where, sources={"crawl", "export"})
    for said in SAY:
        assert sum(1 for r in got["rows"] if r[3] == said[20:]) == 1, said
    assert {r[2] for r in got["rows"] if r[3] in {s[20:] for s in SAY}} == {"export"}
    level = {r[3]: r[1] for r in got["rows"]}
    assert level[SAY[5][20:]] == "error" and level[SAY[4][20:]] == "warn"  # a site failed, a site held: never 資訊
    assert level[SAY[0][20:]] == "info" and level[SAY[3][20:]] == "info"
    assert (got["counts"]["sources"]["crawl"], got["counts"]["sources"]["export"]) == (2, len(SAY) + 1)
    assert sum(sum(col[k] for k in ("error", "warn", "info", "debug")) for col in got["hist"]) == len(SAY) + 3
    # without export.log the nightly log's copies are the export's lines, once, under 導出
    (tmp_path / EXPORTS / "export.log").unlink()
    alone = every(where, sources={"crawl", "export"})
    assert [r[2] for r in alone["rows"] if r[3] in {s[20:] for s in SAY}] == ["export"] * len(SAY)
    assert alone["counts"]["sources"]["export"] == len(SAY) and alone["counts"]["sources"]["crawl"] == 2


def test_an_export_line_export_log_no_longer_holds_stays_once_under_export(tmp_path):
    """export.log reaching back less far than update.log (read from its end, or begun later): the older copies in
    update.log are the only ones and stay, under 導出; a line said twice in the same second is two lines."""
    where = make(tmp_path)
    old = "2026-11-22 01:31:00 开始 c.nknu.edu.tw_aca → c.nknu.edu.tw_aca-20261122-0131"
    twice = "2026-11-23 01:31:00 删掉这个网站更旧的一份 a-1"
    (where.archive / "update.log").write_text(f"{old}\n{twice}\n{twice}\n", encoding="utf-8")
    (tmp_path / EXPORTS / "export.log").write_text(f"{twice}\n", encoding="utf-8")
    rows = every(where, since="2026-11-21T00:00:00", until="2026-11-24T00:00:00")["rows"]
    assert [(r[0], r[2]) for r in rows if r[3] == old[20:]] == [("2026-11-22T01:31:00", "export")]
    assert sum(1 for r in rows if r[3] == twice[20:]) == 2  # one from each file: export.log held it once


def test_nothing_secret_is_shown(tmp_path):
    text = " ".join(r[3] for r in every(make(tmp_path))["rows"])
    assert "AIza" not in text and "abc.def" not in text and "?key=（遮掉" in text and "Authorization: （遮掉）" in text


def test_a_tools_terminal_colours_and_its_own_logger_head_are_not_shown():
    """Live 11-23: RapidOCR's warnings came through the text step's log as "\\x1b[33m[WARNING] 2026-11-23 02:42:25,405
    [RapidOCR] …\\x1b[0m" and were shown with the escape codes and a second time."""
    raw = "\x1b[33m[WARNING] 2026-11-23 02:42:25,405 [RapidOCR] main.py:164: The text detection result is empty\x1b[0m"
    level, msg = logs.level_of(raw)
    assert level == "warn" and msg == "[RapidOCR] main.py:164: The text detection result is empty"
    assert "\x1b" not in logs.shown(raw)


def test_the_ledgers_events_come_with_their_own_level_and_host(tmp_path):
    events = [("2026-11-22T17:05:00+00:00", "warn", "dns_gave_up", "old.nknu.edu.tw", None,
               "did not resolve 3 times: 12 URLs failed"),
              ("2026-11-22T17:06:00+00:00", "error", "disk_low", None, None, "E: 2 GB left"),
              ("2026-11-22T17:07:00+00:00", "info", "run_end", None, None, '{"reason": "finished"}')]
    got = every(make(tmp_path, events=events), sources={"crawl"})
    rows = {r[3]: r for r in got["rows"]}
    dns = rows["[dns_gave_up] old.nknu.edu.tw did not resolve 3 times: 12 URLs failed"]
    assert dns[0] == "2026-11-23T01:05:00" and dns[1] == "warn"  # UTC in the ledger, Taipei here
    assert rows["[disk_low] E: 2 GB left"][1] == "error"
    assert rows['[run_end] {"reason": "finished"}'][1] == "info"


def test_the_chips_count_as_the_design_did_and_the_histogram_holds_what_is_shown(tmp_path):
    where = make(tmp_path)
    got = logs.window(where, now=NOW)  # default levels: no 除錯
    assert all(r[1] != "debug" for r in got["rows"])
    c = got["counts"]
    assert c["levels"]["debug"] == 1  # a level's count is under the sources and words chosen, whatever the levels
    assert c["sources"]["crawl"] == sum(1 for r in got["rows"] if r[2] == "crawl")  # a source's under the levels
    assert len(got["hist"]) == logs.BINS
    assert sum(col["error"] + col["warn"] + col["info"] + col["debug"] for col in got["hist"]) == got["matched"]
    assert got["hist"][0]["t"] == got["first"][:19] and got["hist"][-1]["error"] >= 0
    only = logs.window(where, now=NOW, levels={"error", "warn"}, sources={"totext", "build"})
    assert [r[3] for r in only["rows"]] == ["[lane A] run 1 exit 1"]
    assert only["counts"]["levels"]["info"] == 4  # 資訊 under 轉文字 + 建庫: the lane's two, the build's two
    found = logs.window(where, now=NOW, q="TRACEBACK")
    assert [r[3] for r in found["rows"]] == ["Traceback (most recent call last):"]


def test_a_window_and_pages_of_the_newest_first(tmp_path):
    where = make(tmp_path)
    got = every(where, since="2026-11-23T02:00:00", until="2026-11-23T02:30:00")
    assert got["from"] == "2026-11-23T02:00:00" and got["to"] == "2026-11-23T02:30:00"
    assert {r[2] for r in got["rows"]} == {"totext", "build", "ingest"}
    first = every(where, limit=4)
    assert len(first["rows"]) == 4 and first["more"] and first["next"]
    rest = every(where, limit=4, before=first["next"])
    seen = first["rows"] + rest["rows"]
    while rest["more"]:
        rest = every(where, limit=4, before=rest["next"])
        seen += rest["rows"]
    assert seen == every(where, limit=500)["rows"]  # every line once, in order, whatever the ties in time
    nothing = every(where, since="2026-11-20T00:00:00", until="2026-11-20T01:00:00")
    assert nothing["rows"] == [] and nothing["total"] == 0 and nothing["hist"] == []


def test_a_night_by_its_run_day_is_its_span_with_ten_minutes_each_side(tmp_path, monkeypatch):
    from nknu.ops import nightly

    where = make(tmp_path)
    night = {"state": "ok", "cells": [{"since": "2026-11-23T02:05:00+08:00", "until": "2026-11-23T02:12:00+08:00"},
                                      {"since": None, "until": None}], "runs": [{"until": "2026-11-23T02:15:00+08:00"}]}
    seen = []
    monkeypatch.setattr(nightly, "Reading", lambda w, now: None)
    monkeypatch.setattr(nightly, "one_night", lambda r, d: seen.append(d) or night)
    got = every(where, run_day="2026-11-23")
    assert str(seen[0]) == "2026-11-22"  # ops.db's night key: the run day − 1
    assert (got["from"], got["to"]) == ("2026-11-23T01:55:00", "2026-11-23T02:25:00")
    assert {r[0][:16] for r in got["rows"]} <= {"2026-11-23T02:05", "2026-11-23T02:10", "2026-11-23T02:11",
                                                 "2026-11-23T02:20"}
    monkeypatch.setattr(nightly, "one_night", lambda r, d: {"cells": [{"since": None}], "runs": []})
    empty = every(where, run_day="2026-11-24")  # a night that has not started: no lines
    assert empty["rows"] == [] and empty["from"] is None


def test_a_night_still_running_reaches_to_now_so_the_running_parts_lines_are_its(tmp_path, monkeypatch):
    """NV28: the crawl ended at 01:40 and the export runs (a cell with a start and no end): at 03:00 the window runs to
    now + 10 min, so the export's lines, its errors among them, are the night's; never past the next night's start."""
    from nknu.ops import nightly

    where = make(tmp_path)
    (where.archive / "update.log").write_text("2026-11-23 01:00:00,000 INFO 定期更新开始\n"
                                              "2026-11-23 01:40:00,000 INFO 爬完了\n", encoding="utf-8")
    (tmp_path / EXPORTS / "export.log").write_text("2026-11-23 01:41:00 开始 a → b\n2026-11-23 02:30:00 开始 c → d\n"
                                                   "2026-11-23 02:55:00 出错：c 的清单读不出来\n", encoding="utf-8")
    night = {"state": "run", "cells": [
        {"since": "2026-11-23T01:00:00+08:00", "until": "2026-11-23T01:40:00+08:00", "state": "done"},
        {"since": "2026-11-23T01:40:00+08:00", "until": "", "state": "run"},
        {"since": "", "until": "", "state": "wait"}], "runs": []}
    monkeypatch.setattr(nightly, "Reading", lambda w, now: None)
    monkeypatch.setattr(nightly, "one_night", lambda r, d: night)
    at3 = datetime(2026, 11, 23, 3, 0, tzinfo=TAIPEI)
    got = logs.window(where, run_day="2026-11-23", levels={"error", "warn", "info"}, now=at3)
    assert (got["from"], got["to"]) == ("2026-11-23T00:50:00", "2026-11-23T03:10:00")
    said = [r[3] for r in got["rows"] if r[2] == "export"]
    assert said == ["出错：c 的清单读不出来", "开始 c → d", "开始 a → b"] and got["rows"][0][1] == "error"
    # a part that stopped with no end, on a night long over: up to the next night's start, not to today
    night["state"] = "bad"
    night["cells"][1]["state"] = "stop"
    later = logs.window(where, run_day="2026-11-23", now=datetime(2026, 11, 30, 9, 0, tzinfo=TAIPEI))
    assert later["to"] == "2026-11-24T01:00:00" and any(r[3] == "出错：c 的清单读不出来" for r in later["rows"])
    # an ended night keeps its own span
    night["state"], night["cells"][1]["until"] = "ok", "2026-11-23T02:56:00+08:00"
    done = logs.window(where, run_day="2026-11-23", now=datetime(2026, 11, 30, 9, 0, tzinfo=TAIPEI))
    assert done["to"] == "2026-11-23T03:06:00"


def test_a_log_that_grows_is_read_from_where_it_stopped_and_one_replaced_is_read_again(tmp_path):
    where = make(tmp_path)
    before = every(where)["total"]
    update = where.archive / "update.log"
    with update.open("a", encoding="utf-8") as f:
        f.write("2026-11-23 03:00:00,000 INFO 定期更新结束\n2026-11-23 03:00:01,000 INFO 半行")  # the last is unfinished
    got = every(where)
    assert got["total"] == before + 1 and got["rows"][0][3] == "定期更新结束"
    with update.open("a", encoding="utf-8") as f:
        f.write("还没写完的那行\n")
    assert every(where)["rows"][0][3] == "半行还没写完的那行"
    update.write_text("2026-11-23 04:00:00,000 INFO 新的一份\n", encoding="utf-8")  # rotated: a new, shorter file
    rows = [r for r in every(where)["rows"] if r[2] == "crawl"]
    assert [r[3] for r in rows] == ["新的一份"]


def test_without_any_log_the_window_is_empty_and_says_its_parts(tmp_path):
    got = logs.window(Where(root=tmp_path, db=tmp_path / "aca.db"), now=NOW)
    assert got["rows"] == [] and got["total"] == 0 and got["kept_from"] == {} and got["short"] == []
    assert got["sources"][0] == ["crawl", "爬取"] and got["levels"][0] == ["error", "錯誤"]


def test_the_api_reads_its_query_and_answers_json(tmp_path, monkeypatch):
    import json

    where = make(tmp_path)
    monkeypatch.setattr(logs.pl, "now_tpe", lambda: NOW)
    got = logs.api_window(where, {"lv": "error, warn", "src": "crawl,totext,nonsense", "n": "2", "q": ""})
    assert [r[1] for r in got["rows"]] == ["error", "error"] and got["more"] and got["next"]
    assert json.loads(json.dumps(got, ensure_ascii=False)) == got
    window = logs.api_window(where, {"since": "2026-11-23T02:00:00", "until": "2026-11-23T02:30:00", "n": "x",
                                     "date": "not a day"})
    assert window["from"] == "2026-11-23T02:00:00" and {r[2] for r in window["rows"]} <= {"totext", "build", "ingest"}
    assert any(r["path"] == "/api/logs" and r["handler"].endswith("api_window") for r in logs.ROUTES_TO_WIRE)


def test_the_api_reads_both_pages_spellings(tmp_path, monkeypatch):
    """One handler for /api/logs: 內容's night.js sends lv, src and n; 總覽's home.js sends levels, sources and limit
    (and run_day as well as date). Either way the chips it sends are the ones applied."""
    where = make(tmp_path)
    monkeypatch.setattr(logs.pl, "now_tpe", lambda: NOW)
    night = logs.api_window(where, {"lv": "error", "src": "totext", "n": "5"})
    home = logs.api_window(where, {"levels": "error", "sources": "totext", "limit": "5", "q": ""})
    assert night["rows"] == home["rows"] == [["2026-11-23T02:11:00", "error", "totext", "[lane A] run 1 exit 1"]]
    assert logs.api_window(where, {"levels": "debug", "sources": "crawl"})["rows"][0][3] == "细节"
    assert len(logs.api_window(where, {"limit": "2"})["rows"]) == 2
    assert logs.api_window(where, {"q": "x" * 500})["rows"] == []  # words looked for are cut at 200 characters


def test_a_long_log_is_read_from_its_end_and_says_from_when_it_holds_lines(tmp_path, monkeypatch):
    where = make(tmp_path)
    lines = "".join(f"2026-11-2{d} 01:00:{s:02d},000 INFO 第 {d}-{s} 行\n" for d in (1, 2, 3) for s in range(20))
    (where.archive / "update.log").write_text(lines, encoding="utf-8")
    monkeypatch.setattr(logs, "FIRST_READ", len(lines.encode()) // 3 + 5)  # about the last night, begun mid-line
    got = every(where, sources={"crawl"}, since="2026-11-20T00:00:00", until="2026-11-24T00:00:00")
    held = [r[3] for r in got["rows"]]
    assert held[0] == "第 3-19 行" and all(r[0] >= "2026-11-23" for r in got["rows"]) and len(held) < 21
    assert all(h.startswith("第 3-") for h in held)  # the cut line at the start is dropped, never half shown
    # said per source: 爬取 is held only from 11-23 (its log was cut), the logs read whole are not cut
    assert got["kept_from"]["crawl"] >= "2026-11-23" and got["cut_from"]["crawl"] == got["kept_from"]["crawl"]
    assert got["kept_from"]["build"] == "2026-11-23T02:20:00" and "build" not in got["cut_from"]
    assert got["short"] == [["crawl", got["cut_from"]["crawl"]]]  # the window began 11-20: 爬取 does not reach it
    assert every(where, sources={"build"}, since="2026-11-20T00:00:00")["short"] == []  # a log read whole: never short
    inside = every(where, sources={"crawl"}, since="2026-11-23T01:00:10")
    assert inside["short"] == []  # a window inside what is held
