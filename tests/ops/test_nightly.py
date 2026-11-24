"""今晚 and 昨晚變了什麼 on the home page (nknu.ops.nightly, nknu.ops.changes; spec docs/superpowers/specs/
2026-11-17-panels-structure-design.md 三.2-三.4). Made-up status files in the shapes the crawler's nightly script, the
nightly ingest and build.sh write, and two tiny made-up libraries: no real data."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from nknu.content import build
from nknu.ops import changes as ch
from nknu.ops import home_page, nightly, tonight_page, web
from nknu.ops.pipeline import BAD, CONTENT, CRAWLER, OK, PIPE, TAIPEI, WARN, Where
from tests.ops.library import models
from tests.retrieval.test_search import TODAY


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TAIPEI)


# ---------------------------------------------------------------- made-up libraries and files

def library(path: Path, *, built: str = "2026-11-17T02:40:00+08:00", swapped: str = "2026-11-17T03:05:00+08:00",
            docs: list[tuple] = (), sites: list[tuple] = (), aliases: list[tuple] = ()) -> Path:
    """A library with only what the two readers ask: meta, sites, docs (doc_id, kind, title, url, text, date, site),
    doc_alias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE sites (site TEXT PRIMARY KEY, name TEXT, docs INTEGER, export TEXT)")
    conn.execute("CREATE TABLE docs (doc_id TEXT PRIMARY KEY, kind_class TEXT, title TEXT, url TEXT, text TEXT,"
                 " ident_date TEXT, site TEXT)")
    conn.execute("CREATE TABLE doc_alias (alias TEXT PRIMARY KEY, doc_id TEXT NOT NULL, why TEXT NOT NULL)")
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [("built_at", built), ("vec_built_at", swapped)])
    conn.executemany("INSERT INTO sites VALUES (?, ?, ?, ?)", sites)
    conn.executemany("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?)", docs)
    conn.executemany("INSERT INTO doc_alias VALUES (?, ?, ?)", aliases)
    conn.commit()
    conn.close()
    return path


def crawler_log(where: Where, lines: list[tuple[str, str]], moved: datetime) -> None:
    """update.log as the nightly script writes it (its note() lines among a run's own noise), last changed at moved."""
    path = where.archive / "update.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = []
    for stamp, words in lines:
        text.append(f"{stamp} {words}")
        text.append(f"{stamp},123 INFO httpx 請求 https://school.example/a 200")  # a run's own line: never a mark
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    os.utime(path, (moved.timestamp(), moved.timestamp()))


NIGHT = [("2026-11-17 01:00:02", "定期更新开始"), ("2026-11-17 01:01:30", "备份好了"), ("2026-11-17 01:01:40", "放回好了"),
         ("2026-11-17 01:49:00", "run 结束（退出码 0）"), ("2026-11-17 01:51:00", "变动清单算好了（退出码 0）"),
         ("2026-11-17 02:05:00", "导出好了（35 个网站；每站的结果在 x）"),
         ("2026-11-17 02:06:00", "定期更新结束（导出 35 个网站；seen.tsv 写好了）")]


def crawler_status(where: Where, **more) -> None:
    status = {"started": "2026-11-17T01:00:02+08:00", "night_started": "2026-11-17T01:00:02+08:00",
              "reviewed": "2026-11-17T01:51:00+08:00", "failed_step": None,
              "counts": {"new": 135, "changed": 139, "same": 2671}, "alarms": {}, "sites_changed": ["a", "b"],
              "export": {"at": "2026-11-17T02:06:00+08:00", "exported": ["a", "b"], "unchanged": [], "failed": [],
                         "owed": []}}
    status.update(more)
    (where.archive / "reports").mkdir(parents=True, exist_ok=True)
    (where.archive / "reports" / "update-status.json").write_text(json.dumps(status), encoding="utf-8")


def ingest(where: Where, runs: list[dict], said: list[tuple[str, str]] = (), build: str = "") -> None:
    """ingest-history.jsonl (the runs that ended), ingest-status.json (the last), ingest.log and build.status."""
    pipe = where.pipe
    (pipe / "lab_pc").mkdir(parents=True, exist_ok=True)
    ended = [r for r in runs if r.get("outcome") != "running"]
    (pipe / "ingest-history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ended), encoding="utf-8")
    if runs:
        (pipe / "ingest-status.json").write_text(json.dumps(runs[-1]), encoding="utf-8")
    (pipe / "lab_pc" / "ingest.log").write_text("".join(f"[ingest {t}] {w}\n" for t, w in said), encoding="utf-8")
    if build:
        (pipe / "lab_pc" / "build.status").write_text(build + "\n", encoding="utf-8")


def run(outcome: str, *, night: str = "2026-11-16", started: str = "2026-11-17T02:06:30+08:00",
        finished: str | None = "2026-11-17T03:40:10+08:00", step: str = "done", error: str | None = None,
        **more) -> dict:
    return {"night": night, "started_at": started, "finished_at": finished, "outcome": outcome, "step": step,
            "error": error, "sites_new": [{"site": "a", "folder": "x"}], **more}


SAID = [("2026-11-17 02:06:31", "步骤 repoint"), ("2026-11-17 02:06:32", "步骤 convert"),
        ("2026-11-17 02:31:00", "步骤 keep"), ("2026-11-17 02:31:01", "步骤 build"),
        ("2026-11-17 03:40:00", "步骤 done"), ("2026-11-17 03:40:10", "结束：swapped")]


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db")
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def cells(t: dict) -> dict:
    return {c["key"]: c for c in t["shown"]["cells"]}


# ---------------------------------------------------------------- 今晚: the five parts

def test_a_whole_night_is_five_parts_done_each_with_its_time_and_duration(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("swapped")], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    c = cells(t)
    assert t["default"] == "2026-11-16" and t["shown"]["label"] == "11-16 夜" and t["shown"]["state"] == OK
    assert all(x["state"] == nightly.DONE for x in c.values())
    assert c["crawl"]["when"] == "01:51 · 51 分鐘" and c["export"]["when"] == "02:06 · 15 分鐘"
    assert c["export"]["say"] == "導了 35 站" and "新的 135" in c["crawl"]["tip"]
    # the build ends when the new library took the old one's place (its vec_built_at, inside build step 5); the
    # checks after it belong to 換庫
    assert c["totext"]["when"] == "02:31 · 24 分鐘" and c["build"]["when"] == "03:05 · 34 分鐘"
    assert c["swap"]["when"] == "03:40 · 35 分鐘" and c["swap"]["say"] == "換上了"
    assert t["sentence"] == {"state": OK, "text": "11-16 夜正常：換了新庫", "fix": ""}


def test_while_the_crawler_runs_the_night_says_so_and_how_long(where):
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    t = nightly.tonight(where, now=at("2026-11-17T01:40:00"))
    c = cells(t)
    assert t["default"] == "2026-11-16" and t["shown"]["label"] == "11-16 夜" and t["live"]
    assert c["crawl"]["state"] == nightly.RUN and c["crawl"]["say"] == "抓網頁" and c["crawl"]["when"] == "已經 40 分鐘"
    assert [x["state"] for x in c.values()][1:] == [nightly.WAIT] * 4
    assert t["sentence"]["text"] == "11-16 夜正在跑：爬取（抓網頁），已經 40 分鐘"


def test_a_crawler_whose_log_went_quiet_may_be_stuck_and_after_two_hours_ten_it_stopped(where):
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    t = nightly.tonight(where, now=at("2026-11-17T03:00:00"))  # 90 minutes without a line
    crawl = cells(t)["crawl"]
    assert crawl["state"] == nightly.RUN and crawl["sev"] == WARN and crawl["word"] == "可能卡住"
    assert crawl["say"] == "01:30 以後沒有動靜" and crawl["fix"] == CRAWLER and "03:40" in crawl["tip"]
    assert t["live"] and t["shown"]["state"] == "run"  # the page keeps asking
    assert t["sentence"] == {"state": WARN, "text": "11-16 夜爬取可能卡住：01:30 以後沒有動靜", "fix": CRAWLER}
    t = nightly.tonight(where, now=at("2026-11-17T03:41:00"))  # past 2 h 10 min without a line
    crawl = cells(t)["crawl"]
    assert crawl["state"] == nightly.STOP and crawl["sev"] == BAD and "沒有收尾" in crawl["say"]
    said = t["sentence"]["text"]
    assert not t["live"] and t["sentence"]["state"] == BAD and said == "11-16 夜出錯：爬取沒有收尾（紀錄停在 01:01）"


def test_an_export_waiting_on_its_lock_may_be_stuck_and_the_page_keeps_asking(where):
    crawler_log(where, NIGHT[:5], at("2026-11-17T01:52:00"))  # export_sites.sh waits on flock -w 7200 silently
    t = nightly.tonight(where, now=at("2026-11-17T02:40:00"))
    c = cells(t)
    assert c["crawl"]["state"] == nightly.DONE
    assert c["export"]["state"] == nightly.RUN and c["export"]["sev"] == WARN and c["export"]["word"] == "可能卡住"
    assert c["export"]["say"] == "01:52 以後沒有動靜" and "04:02" in c["export"]["tip"]
    assert t["live"] and t["sentence"]["state"] == WARN and t["sentence"]["fix"] == CRAWLER
    # panels v3: on the night clock the export is drawn up to now in the warning tone (not as a plain run), and the
    # top says it with who to tell; the page keeps asking (data-live)
    seg = {x["key"]: x for x in tonight_page.timeline_data(t, at("2026-11-17T02:40:00"))["nights"][0]["segs"]}
    assert seg["export"]["tone"] == "warn" and seg["export"]["to"] == pytest.approx(2 + 40 / 60, abs=0.01)
    rows = tonight_page.problems(t, {"steps": []}, [])  # spec 八.5: where, state, the source's own words, when
    assert [(x["where"], x["state"]) for x in rows] == [("導出", "可能卡住")] and "01:52" in rows[0]["reason"]
    page = home_page.status_section({"steps": []}, t, None, rows, tonight_page.mark_state(t, rows),
                                    "/api/tonight?brief=1", at("2026-11-17T02:40:00"))
    assert "data-live" in page and "交給" not in page and "data-state='warn'" in page
    assert "導出可能卡住（01:52 以後沒有動靜）" in page  # the line on top names the part that went silent
    later = nightly.tonight(where, now=at("2026-11-17T04:03:00"))
    export = cells(later)["export"]
    assert export["state"] == nightly.STOP and export["sev"] == BAD and export["say"] == "沒有收尾（紀錄停在 01:51）"
    assert not later["live"]


def test_an_older_night_left_without_its_end_never_runs_while_tonights_writes_the_log(where):
    older = [(s.replace("11-17", "11-15"), w) for s, w in NIGHT[:3]]  # 11-14's, cut off after its put-back
    crawler_log(where, [*older, *NIGHT[:3]], at("2026-11-17T01:29:00"))
    now = at("2026-11-17T01:30:00")
    marks = {n["night"]: n["state"] for n in nightly.tonight(where, now=now)["nights"]}
    assert marks["2026-11-16"] == "run" and marks["2026-11-14"] == BAD
    crawl = nightly.tonight(where, night="2026-11-14", now=now)["shown"]["cells"][0]
    assert crawl["state"] == nightly.STOP and crawl["say"] == "沒有收尾（紀錄停在 01:01）" and crawl["sev"] == BAD
    # the next night's script found it still running: that older night is still the one the log's moving is about
    nightly._logs.clear()
    crawler_log(where, [*older, ("2026-11-16 01:00:02", "上一晚的定期更新还在跑：这一晚不开")], at("2026-11-16T01:19:00"))
    t = nightly.tonight(where, night="2026-11-14", now=at("2026-11-16T01:20:00"))
    assert t["shown"]["cells"][0]["state"] == nightly.RUN
    assert {n["night"]: n["state"] for n in t["nights"]}["2026-11-15"] == WARN


def test_where_the_crawler_stopped_decides_which_part_is_red(where):
    crawler_log(where, [*NIGHT[:3], ("2026-11-17 01:20:00", "停在：run（有人停了它）（后面的步骤没做）")],
                at("2026-11-17T01:20:00"))
    c = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))
    assert c["crawl"]["state"] == nightly.STOP and c["crawl"]["say"] == "停在「抓網頁」（有人停了它）"
    assert c["export"]["word"] == "沒跑到"
    nightly._logs.clear()  # update.log only grows; a test that writes it anew starts the reading over
    crawler_log(where, [*NIGHT[:5], ("2026-11-17 01:59:00", "停在：export（后面的步骤没做）")], at("2026-11-17T01:59:00"))
    c = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))
    assert c["crawl"]["state"] == nightly.DONE and c["export"]["state"] == nightly.STOP
    assert c["export"]["say"] == "停在「導出」" and c["export"]["fix"] == CRAWLER


def test_failed_exports_of_the_night_are_the_crawlers(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where, export={"at": "2026-11-17T02:06:00+08:00", "exported": ["a"], "failed": ["b", "c"]})
    export = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["export"]
    assert export["state"] == nightly.STOP and export["say"] == "有 2 站失敗（下一晚再導）" and export["tip"] == "b、c"


HELD = {"b": "1 行用 10-24 舊存檔補的，新導出沒補上"}


def test_a_held_export_is_a_warning_for_the_crawler_and_never_a_failure(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped")], SAID)
    # the crawler lists a held site among the failed too (update.py seen: failed is everything not ok or unchanged)
    crawler_status(where, export={"at": "2026-11-17T02:06:00+08:00", "exported": ["a"], "unchanged": [],
                                  "failed": ["b"], "held": HELD, "owed": ["b"]})
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    export = cells(t)["export"]
    assert export["state"] == nightly.DONE and export["sev"] == WARN and export["say"] == "攔下 1 站"
    assert export["fix"] == CRAWLER and export["fixer"] == "交給爬蟲 session"
    assert "b：1 行用 10-24 舊存檔補的，新導出沒補上" in export["tip"] and "照舊用前一份導出" in export["tip"]
    assert t["shown"]["state"] == WARN
    assert t["sentence"] == {"state": WARN, "text": "11-16 夜導出攔下 1 站", "fix": CRAWLER}
    # what the swap's comparison found is said when it is worse
    got = {"state": "ready", "night": "2026-11-16",
           "totals": {"new": 3, "changed": 0, "left": 2, "unexplained": 2, "look": 0, "look_fix": ""}}
    said = nightly.tonight(where, changes=got, now=at("2026-11-17T09:00:00"))["sentence"]
    assert said["state"] == BAD and "2 份查不出原因" in said["text"]
    # a site that failed for real beside it: still red
    crawler_status(where, export={"at": "2026-11-17T02:06:00+08:00", "exported": ["a"], "unchanged": [],
                                  "failed": ["b", "c"], "held": HELD, "owed": ["b", "c"]})
    export = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["export"]
    assert export["state"] == nightly.STOP and export["sev"] == BAD and export["say"] == "有 1 站失敗（下一晚再導）"
    assert export["tip"].startswith("c") and "攔下 1 站" in export["tip"]


MANUAL = {"sso.example/vita": "人工暂缓：按钮行太多"}  # export-hold.txt, as the crawler writes the reason (11-17)


def test_a_manual_hold_is_said_plainly_and_never_raises_the_night(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped")], SAID)
    crawler_status(where, export={"at": "2026-11-17T02:06:00+08:00", "exported": ["a"], "unchanged": [],
                                  "failed": ["sso.example/vita"], "held": MANUAL, "owed": ["sso.example/vita"]})
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    export = cells(t)["export"]
    assert export["state"] == nightly.DONE and export["sev"] == OK and export["say"] == "導了 35 站；人工暫緩 1 站"
    assert "sso.example/vita：按鈕行太多" in export["tip"] and not export["fixer"]
    assert t["shown"]["state"] == OK and t["sentence"]["text"] == "11-16 夜正常：換了新庫"
    # with a copy-guard hold beside it: both said, only the guard's in the first sentence
    crawler_status(where, export={"at": "2026-11-17T02:06:00+08:00", "exported": ["a"], "unchanged": [],
                                  "failed": ["b", "sso.example/vita"], "held": {**HELD, **MANUAL}, "owed": []})
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    export = cells(t)["export"]
    assert export["sev"] == WARN and export["say"] == "攔下 1 站；人工暫緩 1 站"
    assert "b：1 行用 10-24 舊存檔補的" in export["tip"] and "sso.example/vita：按鈕行太多" in export["tip"]
    assert t["sentence"]["text"] == "11-16 夜導出攔下 1 站"


def test_a_night_with_nothing_by_two_is_missed_and_before_that_it_waits(where):
    crawler_log(where, [(s.replace("11-17", "11-16"), w) for s, w in NIGHT], at("2026-11-16T02:06:00"))  # 11-15's
    t = nightly.tonight(where, now=at("2026-11-17T01:30:00"))
    assert t["shown"]["night"] == "2026-11-16" and cells(t)["crawl"]["state"] == nightly.WAIT
    t = nightly.tonight(where, now=at("2026-11-17T02:30:00"))
    assert cells(t)["crawl"]["word"] == "沒跑" and t["shown"]["state"] == "missed"
    assert t["sentence"]["text"].startswith("11-16 夜沒跑：01:00") and t["sentence"]["fix"] == CRAWLER
    # the nights before the crawler's nightly went live are 還沒上線, never 沒跑
    marks = {n["night"]: n["state"] for n in t["nights"]}
    assert marks["2026-11-14"] == "off" and marks["2026-11-15"] in (OK, "run", WARN, BAD)


def test_the_page_opens_on_last_night_until_tonights_hour_comes(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    assert nightly.tonight(where, now=at("2026-11-17T15:00:00"))["default"] == "2026-11-16"
    t = nightly.tonight(where, now=at("2026-11-18T01:10:00"))
    assert t["default"] == "2026-11-17" and cells(t)["crawl"]["say"] == "該開始了"
    marks = t["nights"]
    # 11-23: a night opens in 內容's night view, named by its run day (/tonight only sends on there)
    assert len(marks) == 7 and marks[-1]["night"] == "2026-11-17" and marks[-1]["href"] == "/sites?date=2026-11-18"
    assert marks[-2]["href"] == "/sites?date=2026-11-17" and marks[-2]["run"] == "2026-11-17"


def test_a_night_can_be_picked(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    t = nightly.tonight(where, night="2026-11-16", now=at("2026-11-18T09:00:00"))
    assert t["default"] == "2026-11-17" and t["shown"]["night"] == "2026-11-16" and t["shown"]["label"] == "11-16 夜"
    assert nightly.tonight(where, night="nonsense", now=at("2026-11-18T09:00:00"))["shown"]["night"] == "2026-11-17"


def test_a_night_out_of_reach_is_ignored(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    now = at("2026-11-18T09:00:00")
    for far in ("9999-12-31", "0001-01-01", "2026-08-01", "2026-11-21"):
        assert nightly.tonight(where, night=far, now=now)["shown"]["night"] == "2026-11-17"
    for near in ("2026-10-01", "2026-11-18"):
        assert nightly.tonight(where, night=near, now=now)["shown"]["night"] == near


def test_a_far_night_in_the_address_never_breaks_a_page(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        for path in ("/tonight?night=9999-12-31", "/api/tonight?night=9999-12-31", "/site?site=nope&night=9999-12-31"):
            assert client.get(path).status_code in (200, 404)


# ---------------------------------------------------------------- 今晚: the ingest's parts

def test_a_build_running_says_its_step_and_after_the_swap_the_checks_are_the_swaps(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("running", finished=None, step="build")], SAID[:4],
           build="2026-11-17 02:40:00 running step 4 since 2026-11-17 02:40:00")
    library(where.library, swapped="2026-11-16T15:03:00+08:00")  # the library in use is still yesterday's
    t = nightly.tonight(where, now=at("2026-11-17T02:52:00"))
    c = cells(t)
    assert c["totext"]["state"] == nightly.DONE and c["build"]["state"] == nightly.RUN
    assert c["build"]["say"] == "第 4 步（組裝）" and t["sentence"]["text"] == "11-16 夜正在跑：建庫第 4 步（組裝），已經 21 分鐘"
    library(where.library, swapped="2026-11-17T03:05:00+08:00")
    ingest(where, [run("running", finished=None, step="build")], SAID[:4],
           build="2026-11-17 03:10:00 running step 7 since 2026-11-17 03:10:00")
    c = cells(nightly.tonight(where, now=at("2026-11-17T03:20:00")))
    assert c["build"]["state"] == nightly.DONE and c["build"]["when"] == "03:05 · 34 分鐘"
    assert c["swap"]["state"] == nightly.RUN and c["swap"]["say"] == "已換上，正在檢查：第 7 步（33 題測試）"


def test_a_build_that_failed_names_its_step_and_the_content_session(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("failed", step="build", error="build_exit_1", finished="2026-11-17T02:50:00+08:00")], SAID[:4],
           build="2026-11-17 02:49:00 failed step 4 (exit 1)")
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    build = cells(t)["build"]
    assert build["state"] == nightly.STOP and build["say"] == "停在第 4 步（組裝）" and build["fix"] == CONTENT
    assert t["sentence"] == {"state": BAD, "text": "11-16 夜出錯：建庫停在第 4 步（組裝）",
                             "fix": CONTENT}


@pytest.mark.parametrize(("outcome", "where_", "say", "sev"), [
    ("paused", "build", "暫停中（有 ingest.pause），只轉文字、沒建庫", WARN),
    ("held", "swap", "把關沒過，新庫沒換上（有網站的文件數掉到一半以下）", WARN),
    ("deferred", "totext", "兩小時內沒轉完，這晚不建庫（下一晚接著轉）", WARN),
])
def test_a_night_that_stopped_on_purpose_is_a_warning_for_the_content_session(where, outcome, where_, say, sev):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    said = SAID[:3] if outcome != "held" else SAID[:5]
    ingest(where, [run(outcome, step="convert" if outcome != "held" else "build")], said)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    c = cells(t)[where_]
    assert c["state"] == nightly.STOP and c["say"] == say and c["sev"] == sev and c["fix"] == CONTENT
    assert t["shown"]["state"] == WARN and t["sentence"]["text"].startswith("11-16 夜停了：")
    # the night's mark says 留意 (the 11-23 design, map NV04): 停了 is the part's, said in the sentence
    assert t["shown"]["word"] == nightly.NIGHT_WORDS[WARN] == "留意"


def test_a_build_run_by_hand_is_said_beside_the_nights_build_not_as_its_state(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("paused", step="convert", started="2026-11-17T02:41:57+08:00", finished="2026-11-17T02:47:00+08:00")],
           build="2026-11-17 02:52:00 running step 5 since 2026-11-17 02:52:00")
    build = cells(nightly.tonight(where, now=at("2026-11-17T03:30:00")))["build"]
    assert build["state"] == nightly.STOP and build["say"].startswith("暫停中")
    assert "另外有一次建庫在跑：第 5 步（建庫和算向量），02:52 開始" in build["tip"]
    t = nightly.tonight(where, now=at("2026-11-17T03:30:00"))
    seg = {x["key"]: x for x in tonight_page.timeline_data(t, at("2026-11-17T03:30:00"))["nights"][0]["segs"]}
    assert "另外有一次建庫在跑" in seg["build"]["say"]  # said in the segment's floating card, not as its state


def test_nothing_new_is_a_normal_night_without_a_swap(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("nothing_new", step="plan", finished="2026-11-17T02:06:40+08:00")])
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert [c["state"] for c in t["shown"]["cells"]] == [nightly.DONE] * 5
    assert t["sentence"]["text"] == "11-16 夜正常：沒有新資料，庫沒換"


def test_the_first_run_after_the_crawler_is_the_nights_and_the_others_are_aside(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    evening = run("swapped", started="2026-11-16T22:42:00+08:00", finished="2026-11-17T00:49:00+08:00")
    first = run("failed", step="convert", error="lane_A_exit_1", finished="2026-11-17T02:20:00+08:00")
    ingest(where, [evening, first, run("paused", started="2026-11-17T02:41:57+08:00", step="convert",
                                       finished="2026-11-17T02:47:00+08:00")])
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    c = cells(t)
    assert c["totext"]["state"] == nightly.STOP and c["totext"]["say"] == "轉 PDF 和 Office 檔的程序出錯（退出碼 1）"
    # every run of the night is one (spec 八.3), the evening's before the crawler is not the night's
    assert [x["outcome"] for x in t["shown"]["runs"]] == ["failed", "paused"] and c["build"]["word"] == "沒跑到"


def busy_line(started: str) -> dict:
    """The history line of an ingest that found another one still running (nightly_ingest.night)."""
    return {"night": "2026-11-16", "outcome": "busy", "started_at": started, "finished_at": started}


def test_a_second_ingest_that_found_the_nights_busy_never_hides_it(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    busy = busy_line("2026-11-17T02:30:00+08:00")  # one started by hand while the night's converted
    ingest(where, [busy, run("running", finished=None, step="convert")], SAID[:2])
    early = nightly.tonight(where, now=at("2026-11-17T02:35:00"))
    totext = cells(early)["totext"]
    assert totext["state"] == nightly.RUN and [x["outcome"] for x in early["shown"]["runs"]] == ["running", "busy"]
    ingest(where, [busy, run("swapped")], SAID)  # the night's line comes after the busy one in the history
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert [x["state"] for x in t["shown"]["cells"]] == [nightly.DONE] * 5
    assert t["sentence"]["text"] == "11-16 夜正常：換了新庫"


def test_a_night_with_nothing_but_a_busy_ingest_says_it_did_not_start(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [busy_line("2026-11-17T02:06:30+08:00")])
    totext = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["totext"]
    assert totext["state"] == nightly.STOP and totext["say"] == "沒開（前一晚的進庫還在跑）" and totext["sev"] == WARN


def test_a_manual_ingest_the_morning_after_never_wins_over_the_nights_swap(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    morning = run("nothing_new", step="plan", started="2026-11-17T10:00:00+08:00", finished="2026-11-17T10:01:00+08:00")
    ingest(where, [run("swapped"), morning], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T11:00:00"))
    c = cells(t)
    assert c["swap"]["say"] == "換上了" and [x["outcome"] for x in t["shown"]["runs"]] == ["swapped", "nothing_new"]
    assert t["sentence"]["text"] == "11-16 夜正常：換了新庫"


MANUAL_SAID = [("2026-11-17 02:41:58", "步骤 convert"), ("2026-11-17 02:47:00", "结束：paused"),
               ("2026-11-17 03:51:01", "步骤 repoint"), ("2026-11-17 03:51:02", "步骤 convert"),
               ("2026-11-17 03:55:00", "步骤 keep"), ("2026-11-17 03:55:01", "步骤 build"),
               ("2026-11-17 04:29:00", "步骤 done"), ("2026-11-17 04:30:00", "结束：swapped")]


def test_a_night_whose_scheduled_ingest_paused_and_a_manual_one_swapped_shows_the_swap(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    library(where.library, swapped="2026-11-17T04:09:00+08:00")
    paused = run("paused", step="convert", started="2026-11-17T02:41:57+08:00", finished="2026-11-17T02:47:00+08:00")
    manual = run("swapped", started="2026-11-17T03:51:00+08:00", finished="2026-11-17T04:30:00+08:00")
    ingest(where, [paused, manual], MANUAL_SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    c = cells(t)
    assert [x["state"] for x in t["shown"]["cells"]] == [nightly.DONE] * 5 and t["shown"]["state"] == OK
    assert c["build"]["when"] == "04:09 · 14 分鐘" and c["swap"]["say"] == "換上了"
    # two runs, each drawn as itself; nothing written about why there were two (spec 八.3)
    runs = t["shown"]["runs"]
    assert [(x["run"], x["outcome"], x["shown"]) for x in runs] == [(1, "paused", False), (2, "swapped", True)]
    assert t["sentence"] == {"state": OK, "text": "11-16 夜正常：換了新庫", "fix": ""}
    # a run by hand the morning after on top: the swap is still the night's; the scheduled one is said, the other counted
    morning = run("nothing_new", step="plan", started="2026-11-17T10:00:00+08:00", finished="2026-11-17T10:01:00+08:00")
    ingest(where, [paused, manual, morning], MANUAL_SAID)
    later = nightly.tonight(where, now=at("2026-11-17T11:00:00"))
    assert cells(later)["swap"]["say"] == "換上了" and len(later["shown"]["runs"]) == 3
    got = {"state": "ready", "night": "2026-11-16",
           "totals": {"new": 3, "changed": 0, "left": 0, "unexplained": 0, "look": 0, "look_fix": ""}}
    said = nightly.tonight(where, changes=got, now=at("2026-11-17T11:00:00"))["sentence"]
    assert said == {"state": OK, "text": "11-16 夜正常：新進 3 份、離開 0 份", "fix": ""}


def rollback_line(started: str, night: str = "2026-11-16") -> dict:
    return {"night": night, "started_at": started, "finished_at": started, "outcome": "rolled_back", "step": "done",
            "error": None}


def test_a_rollback_after_the_nights_swap_is_found_past_a_later_run_and_on_the_next_day(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    morning = run("nothing_new", step="plan", started="2026-11-17T10:00:00+08:00", finished="2026-11-17T10:01:00+08:00")
    ingest(where, [run("swapped"), rollback_line("2026-11-17T09:00:00+08:00"), morning], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T11:00:00"))
    c = cells(t)
    assert c["swap"]["state"] == nightly.STOP and c["swap"]["say"] == "換上後又退回前一份庫（09:00）"
    assert [x["outcome"] for x in t["shown"]["runs"]] == ["swapped", "nothing_new"]
    # rolled back in the afternoon: the line is named the next night, and still undoes this night's swap
    ingest(where, [run("swapped"), rollback_line("2026-11-17T14:00:00+08:00", night="2026-11-17")], SAID)
    swap = cells(nightly.tonight(where, now=at("2026-11-17T15:00:00")))["swap"]
    assert swap["state"] == nightly.STOP and swap["say"] == "換上後又退回前一份庫（14:00）"


def test_the_morning_sentence_names_the_worst_stop_not_the_first(where):
    crawler_log(where, [("2026-11-17 01:00:02", "有 update.pause：这一晚不更新（什么都没动）")], at("2026-11-17T01:00:02"))
    ingest(where, [run("failed", step="convert", error="lane_A_exit_1", started="2026-11-17T01:00:30+08:00",
                       finished="2026-11-17T01:10:00+08:00")])
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    c = cells(t)
    assert c["crawl"]["sev"] == WARN and c["totext"]["sev"] == BAD
    assert t["sentence"] == {"state": BAD, "text": "11-16 夜出錯：轉文字轉 PDF 和 Office 檔的程序出錯（退出碼 1）",
                             "fix": CONTENT}


def test_an_ingest_that_never_followed_the_crawler_is_said(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped", night="2026-11-15", started="2026-11-16T02:06:30+08:00",
                       finished="2026-11-16T03:40:00+08:00")])
    assert cells(nightly.tonight(where, now=at("2026-11-17T02:20:00")))["totext"]["state"] == nightly.WAIT
    totext = cells(nightly.tonight(where, now=at("2026-11-17T03:00:00")))["totext"]
    assert totext["state"] == nightly.STOP and totext["fix"] == CONTENT and totext["word"] == "沒跑"


def test_lane_and_build_errors_read_as_words():
    assert nightly.error_words("lanes_timeout") == "兩小時內沒轉完"
    assert nightly.error_words("lane_A_exit_1") == "轉 PDF 和 Office 檔的程序出錯（退出碼 1）"
    assert nightly.error_words("checks_exit_2") == "換庫後的檢查出錯（退出碼 2）"
    assert nightly.night_of(at("2026-11-17T01:00:00")).isoformat() == "2026-11-16"
    assert nightly.night_of(at("2026-11-16T18:55:00")).isoformat() == "2026-11-16"


def test_the_log_is_read_on_from_where_it_stopped(where):
    crawler_log(where, NIGHT[:2], at("2026-11-17T01:02:00"))
    first, _ = nightly.log_events(where.archive / "update.log")
    with (where.archive / "update.log").open("a", encoding="utf-8") as f:
        f.write("2026-11-17 01:01:40 放回好了\n2026-11-17 01:49:00 run 结")  # the last line is still being written
    more, _ = nightly.log_events(where.archive / "update.log")
    assert [w for _a, w, _t in first] == ["start", "backup"] and [w for _a, w, _t in more] == ["start", "backup", "putback"]


def test_the_log_is_measured_inside_the_lock_so_a_racing_read_never_answers_old_marks(where, monkeypatch):
    nightly._logs.clear()
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    path = where.archive / "update.log"
    nightly.log_events(path)  # the cache holds the three marks

    class Racing:
        """The lock: as this request enters it, another one appends to the log and reads it first."""

        def __init__(self) -> None:
            self.real, self.then = threading.Lock(), None

        def __enter__(self):
            then, self.then = self.then, None
            if then is not None:
                then()
            self.real.acquire()

        def __exit__(self, *exc):
            self.real.release()

    def other() -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write("2026-11-17 01:49:00 run 结束（退出码 0）\n")
        nightly.log_events(path)

    lock = Racing()
    lock.then = other
    monkeypatch.setattr(nightly, "_logs_lock", lock)
    got, _ = nightly.log_events(path)
    assert [w for _a, w, _t in got] == ["start", "backup", "putback", "ran"]


# ---------------------------------------------------------------- 昨晚變了什麼

def manifest(root: Path, folder: str, rows: list[dict], calendar: list[dict] | None = None) -> str:
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps({"网站": "a", "项目": rows}, ensure_ascii=False), encoding="utf-8")
    if calendar is not None:
        (path / "data").mkdir(exist_ok=True)
        (path / "data" / "校行事历.json").write_text(json.dumps({"items": calendar}), encoding="utf-8")
    return folder


def row(url: str, *, lacks: str = "", saved: bool = True, kind: str = "", linked: str = "", result: str = "抓到了") -> dict:
    return {"网址": url, "缺什么": lacks, "本地档案": "pages/x.html" if saved else "", "页别": kind, "链到它的网页": linked,
            "结果": result}


U = "https://school.example/"


@pytest.fixture
def two(tmp_path):
    """The library before and after a night: one of every way a document can leave, new and changed ones."""
    old_a = manifest(tmp_path, "exp/a-1", [])
    new_a = manifest(tmp_path, "exp/a-2", [
        row(U + "gone", lacks="学校已删除（11-15 起说不存在）"),
        row(U + "moved", lacks="学校把这则公告转到别的页（多半是删了）"), row(U + "nodata", lacks="学校回答没有资料（無）"),
        row(U + "pager", kind="列表翻页"), row(U + "shell", lacks="内文区是空的"),
        row(U + "deadpage", lacks="学校已删除"), row(U + "file", linked=U + "deadpage"),
        row(U + "broken", saved=False, result="连接失败"), row(U + "kept"), row(U + "lost")],
        calendar=[{"id": "v2", "recurringEventId": "s1"}])
    same_b = manifest(tmp_path, "exp/b-1", [row(U + "b/kept")])
    prev = [("d1", "notice", "停修公告", U + "gone", "x", "2026-11-01", "a"),
            ("d2", "notice", "移走的", U + "moved", "x", "2026-11-01", "a"),
            ("d3", "page", "沒資料", U + "nodata", "x", None, "a"),
            ("d4", "page", "分頁", U + "pager", "x", None, "a"),
            ("d5", "notice", "空殼", U + "shell", "x", None, "a"),
            ("d6", "attachment", "附件", U + "file", "x", None, "a"),
            ("d7", "attachment", "沒抓到的", U + "broken", "x", None, "a"),
            ("d8", "notice", "沒列的", U + "notlisted", "x", None, "a"),
            ("d9", "notice", "併掉的", U + "copy", "x", None, "a"),
            ("d10", "notice", "換號的", U + "renum", "x", None, "a"),
            ("d11", "notice", "說不出", U + "lost", "x", None, "a"),
            ("ev1", "event", "停課", "https://cal.example/1", "", "2026-12-01", "a"),
            ("ev2", "event", "週會", "https://cal.example/2", "", "2026-12-02", "a"),
            ("d20", "notice", "同導出", U + "b/gone", "x", None, "b"),
            ("d30", "page", "整站", U + "c/1", "x", None, "c"),
            ("d40", "notice", "標題舊", U + "kept", "same", "2026-11-01", "a")]
    new = [("d40", "notice", "標題新", U + "kept", "same", "2026-11-01", "a"),
           ("d50", "notice", "新公告", U + "new", "y", "2026-11-16", "a"),
           ("d51", "notice", "換號的", U + "renum", "x", None, "a"),
           ("d60", "notice", "留下的", U + "kept2", "x", None, "a")]
    p = library(tmp_path / "prev.db", built="2026-11-16T14:56:00+08:00", swapped="2026-11-16T15:03:00+08:00", docs=prev,
                sites=[("a", "範例處", 14, old_a), ("b", "乙系", 1, same_b), ("c", "丙所", 1, "exp/c-1")])
    n = library(tmp_path / "new.db", docs=new, sites=[("a", "範例處", 4, new_a), ("b", "乙系", 0, same_b)],
                aliases=[("d9", "d60", "copy")])
    return p, n


def test_every_document_that_left_has_its_reason(two, tmp_path):
    prev, new = (ch.read_library(p) for p in two)
    got = ch.compare(prev, new, tmp_path)
    left = {d["id"]: k for s in got["sites"] for k, docs in s["lists"]["left"].items() for d in docs}
    assert left == {"d1": "gone", "d2": "moved", "d3": "no_data", "d4": "pager", "d5": "shell", "d6": "gone_file",
                    "d7": "not_fetched", "d8": "not_exported", "d9": "copy", "d10": "renumbered", "d11": "unknown",
                    "ev1": "calendar_gone", "ev2": "series", "d20": "same_export", "d30": "site_gone"}
    t = got["totals"]
    assert (t["new"], t["changed"], t["left"], t["unexplained"]) == (3, 1, 15, 1)
    assert t["look"] == 4 and t["look_fix"] == CRAWLER  # not fetched, not exported (crawler); same export, site (content)
    # the one with no reason first, with its site; a changed document says what changed
    assert got["sites"][0]["site"] == "a" and got["unexplained"][0]["id"] == "d11"
    assert got["unexplained"][0]["site_name"] == "範例處"
    changed = got["sites"][0]["lists"]["changed"][0]
    assert changed["id"] == "d40" and changed["what"] == ["標題"]
    notes = {d["id"]: d.get("note") for d in got["sites"][0]["lists"]["left"]["not_fetched"]}
    assert notes["d7"] == "連接失敗"


def test_a_file_the_school_took_away_is_explained_as_the_builds_swap_guard_counts_it(tmp_path):
    folder = manifest(tmp_path, "exp/a-2", [
        {"网址": U + "away.pdf", "分类": build.SCHOOL_GONE, "结果": "学校那边已经不存在", "本地档案": "", "缺什么": ""},
        row(U + "broken", saved=False, result="连接失败")])
    new = {"aliases": {}, "urls": {}, "sites": {"a": {"name": "範例處", "export": folder, "docs": 1}}, "docs": {}}
    prev = {"sites": {"a": {"name": "範例處", "export": "exp/a-1", "docs": 2}}}
    m = ch.Manifests(tmp_path)
    away = ("a", "attachment", "附件", U + "away.pdf", "2026-11-01", 100, "x")
    assert ch.why_left("d1", away, new, prev, m) == ("school_gone", "")
    label, _tip, kind, fix = ch.REASONS["school_gone"]
    assert label == "校方已移除" and kind == ch.OK_KIND and not fix  # the 11-23 words (names.py)
    broken = ("a", "attachment", "沒抓到的", U + "broken", "", 100, "y")
    assert ch.why_left("d2", broken, new, prev, m)[0] == "not_fetched"  # no file for another reason: still ours
    assert ch.SCHOOL_GONE == build.SCHOOL_GONE


def test_another_size_of_a_picture_the_new_library_holds_left_for_a_reason(tmp_path):
    # 11-18: the export keeps one size of a Wix picture; the build counts the other sizes' leaving as explained
    # (same_image) when the new library holds the picture, and so does the panels' own comparison
    wix = "https://static.wixstatic.com/media/"
    declined = {"分类": build.RULE_DECLINED, "结果": "按规则不抓", "本地档案": "", "缺什么": ""}
    folder = manifest(tmp_path, "exp/a-2", [{"网址": wix + "pic1.jpg/v1/fill/w_200/pic1.jpg", **declined},
                                             {"网址": wix + "pic2.jpg/v1/fill/w_200/pic2.jpg", **declined},
                                             {"网址": U + "declined.pdf", **declined}])
    urls = {wix + "pic1.jpg/v1/fill/w_980/pic1.jpg": "d9"}
    new = {"aliases": {}, "urls": urls, "pictures": ch.pictures_of(urls),
           "sites": {"a": {"name": "範例所", "export": folder, "docs": 1}}, "docs": {}}
    prev = {"sites": {"a": {"name": "範例所", "export": "exp/a-1", "docs": 3}}}
    m = ch.Manifests(tmp_path)

    def left(url: str) -> str:
        return ch.why_left("d1", ("a", "attachment", "圖", url, "", 100, "x"), new, prev, m)[0]

    assert left(wix + "pic1.jpg/v1/fill/w_200/pic1.jpg") == "same_image"
    assert left(wix + "pic2.jpg/v1/fill/w_200/pic2.jpg") == "not_fetched"  # no size of this picture is in the library
    assert left(U + "declined.pdf") == "not_fetched"  # declined by rule, and no picture: still looked at
    assert ch.REASONS["same_image"][2] == ch.OK_KIND and ch.RULE_DECLINED == build.RULE_DECLINED
    assert build._WIX_PICTURE.match(build._address(wix + "pic1.jpg/x")).group(1) == "pic1.jpg"  # one name both ways


def test_a_kind_decided_out_left_for_a_reason(tmp_path):
    # 11-19: a rule with its own name (teachers' publications in full) says so in the export's 原因代码; the build
    # counts those leavings as explained (DECIDED_OUT), and so does the panels' own comparison
    declined = {"分类": build.RULE_DECLINED, "结果": "按规则不抓", "本地档案": "", "缺什么": ""}
    code = next(iter(build.DECIDED_OUT))
    folder = manifest(tmp_path, "exp/a-2", [{"网址": U + "works/1.pdf", "原因代码": code, **declined},
                                             {"网址": U + "other.pdf", "原因代码": "out:skip_path", **declined}])
    new = {"aliases": {}, "urls": {}, "sites": {"a": {"name": "範例處", "export": folder, "docs": 0}}, "docs": {}}
    prev = {"sites": {"a": {"name": "範例處", "export": "exp/a-1", "docs": 2}}}
    m = ch.Manifests(tmp_path)

    def left(url: str) -> tuple[str, str]:
        return ch.why_left("d1", ("a", "attachment", "檔", url, "", 100, "x"), new, prev, m)

    assert left(U + "works/1.pdf") == ("decided_out", "教師著作全文")
    assert left(U + "other.pdf")[0] == "not_fetched"  # a path rule with no name explains nothing: still looked at
    assert ch.REASONS["decided_out"][2] == ch.OK_KIND and ch.DECIDED_OUT == build.DECIDED_OUT
    # every kind decided out is said in words: this page's own, else the build's (a new kind needs no change here)
    assert all(ch.decided_words(k) and ch.decided_words(k) != k for k in build.DECIDED_OUT.values())
    assert ch.decided_words("decided_proceedings") == "研討會論文集全文"
    build.LEFT_WORDS["decided_made_up"] = "定了不收：编的一类"
    try:
        assert ch.decided_words("decided_made_up") == "編的一類"
    finally:
        del build.LEFT_WORDS["decided_made_up"]


def test_an_album_photo_left_for_a_reason_whichever_row_of_its_address_comes_first(tmp_path):
    # 11-23: the export declines an album's photos (原因代码 album) and may still give a file for one on a row of its
    # own; the build leaves every such picture out (DECLINED_OUT, _leave_declined) and counts it as explained, and so
    # does the panels' own comparison, whichever of the address's rows the manifest lists first
    album = {"分类": build.RULE_DECLINED, "结果": "按规则不抓", "本地档案": "", "缺什么": "", "原因代码": "album"}
    saved = {"类型": "图片", "本地档案": "files/1.jpg", "缺什么": ""}
    pic1, pic2 = U + "case/1/thumbnail/a_s.jpg", U + "case/2/thumbnail/b_s.jpg"
    honor = U + "honorroll/case/3/c_s.jpg"
    folder = manifest(tmp_path, "exp/a-2", [{"网址": pic1, **album}, {"网址": pic1, **saved},
                                             {"网址": pic2, **saved}, {"网址": pic2, **album},
                                             {"网址": honor, **album}])
    new = {"aliases": {}, "urls": {}, "sites": {"a": {"name": "範例系", "export": folder, "docs": 0}}, "docs": {}}
    prev = {"sites": {"a": {"name": "範例系", "export": "exp/a-1", "docs": 3}}}
    m = ch.Manifests(tmp_path)

    def left(url: str) -> tuple[str, str]:
        return ch.why_left("d1", ("a", "attachment", "圖", url, "", 36, "x"), new, prev, m)

    assert left(pic1) == ("decided_out", "相簿照片") == left(pic2)
    assert left(honor)[0] == "not_fetched"  # an honor roll's picture the build keeps: if it left, it is still looked at
    assert ch.DECLINED_OUT == build.DECLINED_OUT and ch.KEEP_DECLINED == build.KEEP_DECLINED
    assert all(ch.decided_words(why) == ch.tw(build.LEFT_WORDS[why]).removeprefix("定了不收：")
               for _suffix, why in build.DECLINED_OUT.values())


def test_a_document_left_for_what_the_row_of_its_identity_the_build_reads_says(tmp_path):
    # 11-24: the build knows a document by its identity (ids.location_key: a c.nknu notice's newsview and newsdetail of
    # one nid are one, http and https are one) and reads the row of it fetched last (documents.one_per_id); on the
    # night of 11-24 the panels said 查不出原因 of 8 notices whose newsview row said the school moved them, and of an
    # album picture at http:// whose declined row was at https://
    base = "https://c.nknu.edu.tw/ula/"
    detail, view = base + "newsdetail.aspx?nid=139", base + "newsview.aspx?nid=139"
    pic = "tecs.nknu.edu.tw/uploadfile/adj/adj_1.jpg"
    album = {"分类": build.RULE_DECLINED, "结果": "按规则不抓", "本地档案": "", "缺什么": "", "原因代码": "album"}
    moved = ch.MOVED + "（多半是删了）"

    def why(rows: list[dict], url: str, folder: str) -> tuple[str, str]:
        exp = manifest(tmp_path, folder, rows)
        new = {"aliases": {}, "urls": {}, "sites": {"a": {"name": "範例系", "export": exp, "docs": 0}}, "docs": {}}
        prev = {"sites": {"a": {"name": "範例系", "export": "exp/a-1", "docs": 3}}}
        return ch.why_left("d1", ("a", "notice", "公告", url, "", 36, "x"), new, prev, ch.Manifests(tmp_path))

    later = [{**row(detail), "抓取时间": "2026-11-20T01:00:00+08:00"},
             {**row(view, lacks=moved), "抓取时间": "2026-11-24T01:30:00+08:00"}]
    assert why(later, detail, "exp/a-2") == ("moved", "")
    earlier = [{**row(detail), "抓取时间": "2026-11-24T01:30:00+08:00"},
               {**row(view, lacks=moved), "抓取时间": "2026-11-20T01:00:00+08:00"}]
    assert why(earlier, detail, "exp/a-3")[0] != "moved"  # the notice's own row is the one read: not the school's
    pictures = [{"网址": "https://" + pic, **album}, {**row("http://" + pic), "类型": "图片"}]
    assert why(pictures, "http://" + pic, "exp/a-4") == ("decided_out", "相簿照片")


def test_a_picture_whose_words_are_still_in_the_library_left_for_a_reason(tmp_path):
    # 11-19: Google Sites gives a picture another address each time a page is read, and the export now writes the
    # page's; the old address leaves while the same words come in under the new one (or inside the page). The build
    # counts that as explained ('moved'), and so does the panels' comparison; a few common words prove nothing
    gs = "https://lh3.googleusercontent.com/"
    words = "輔導中心本學期團體諮商活動報名時間與地點說明，請同學踴躍參加"
    page = "首頁 最新消息 " + "輔導中心本學期團體諮商活動報名時間與地點說明，請同學踴躍參加" + " 聯絡我們"
    folder = manifest(tmp_path, "exp/g-2", [{"网址": gs + "new-1", "本地档案": "files/1.png", "缺什么": ""},
                                             # still fetched, no document of its own: its words went into the page
                                             {"网址": gs + "kept-1", "类型": "图片", "本地档案": "files/2.png",
                                              "链到它的网页": "https://g.example/2/"},
                                             {"网址": gs + "kept-2", "类型": "图片", "本地档案": "files/3.png",
                                              "链到它的网页": "https://g.example/2"},
                                             # its words are in a page, but not in the one that shows it
                                             {"网址": gs + "kept-3", "类型": "图片", "本地档案": "files/4.png",
                                              "链到它的网页": "https://g.example/"},
                                             # 20 characters or more: anywhere in its site, like any other
                                             {"网址": gs + "kept-4", "类型": "图片", "本地档案": "files/5.png",
                                              "链到它的网页": "https://g.example/2"}])
    old_docs = [("d-old", "picture", "圖", gs + "old-1", words, "", "g"),
                ("d-in", "picture", "圖", gs + "old-2", words[:30] + "，在網頁裡", "", "g"),
                ("d-short", "picture", "圖", gs + "old-3", "報名", "", "g"),
                ("d-gone", "picture", "圖", gs + "old-4", "這張圖的字新庫裡哪裡都沒有，是真的不見了要看一下", "", "g"),
                ("d-pic", "picture", "圖", gs + "kept-1", "第二階段 初步構想計畫申請", "", "g"),  # 12 characters
                ("d-tiny", "picture", "圖", gs + "kept-2", "聯絡我們", "", "g"),  # under the picture's 10
                ("d-else", "picture", "圖", gs + "kept-3", "第二階段 初步構想計畫申請", "", "g"),
                ("d-long", "picture", "圖", gs + "kept-4", words[:30] + "，在網頁裡", "", "g")]
    new_docs = [("d-new", "picture", "圖", gs + "new-1", words, "", "g"),
                ("p1", "page", "頁", "https://g.example/", page.replace(words, words[:30] + "，在網頁裡"), "", "g"),
                ("p2", "page", "頁", "https://g.example/2", "報名 第二階段 初步構想計畫申請 截止", "", "g")]
    prev_db = library(tmp_path / "aca.prev.db", docs=old_docs, sites=[("g", "輔導中心", 4, "exp/g-1")])
    new_db = library(tmp_path / "aca.db", docs=new_docs, sites=[("g", "輔導中心", 3, folder)])
    prev, new = ch.read_library(prev_db), ch.read_library(new_db)
    done = ch.compare(prev, new, tmp_path, dbs=(prev_db, new_db))
    left = done["sites"][0]["lists"]["left"]
    assert {e["id"] for e in left["words_kept"]} == {"d-old", "d-in", "d-pic", "d-long"}
    assert {e["id"] for e in left["not_exported"]} == {"d-short", "d-gone"}
    assert {e["id"] for e in left["unknown"]} == {"d-tiny", "d-else"}
    assert done["totals"]["look"] == 2 and done["totals"]["unexplained"] == 2
    assert ch.REASONS["words_kept"][2] == ch.OK_KIND
    # without the libraries' files, the same text is still recognised by its fingerprint alone
    alone = ch.compare(prev, new, tmp_path)["sites"][0]["lists"]["left"]
    assert {e["id"] for e in alone["words_kept"]} == {"d-old"}


def test_the_comparison_runs_in_the_background_and_never_writes_the_libraries(two, tmp_path):
    prev, new = two
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in two}
    job = ch.Changes(new, tmp_path, prev_db=prev)
    first = job.snapshot()
    assert first["state"] in ("computing", "ready")
    job.wait(30)
    done = job.snapshot()
    assert done["state"] == "ready" and done["totals"]["left"] == 15 and done["night"] == "2026-11-16"
    assert done["prev"]["swapped_at"] == "2026-11-16T15:03:00+08:00"
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in two} == before
    assert not list(tmp_path.glob("*.db-journal")) and not list(tmp_path.glob("*.db-wal"))


def test_no_library_before_or_the_same_one_is_said(two, tmp_path):
    _prev, new = two
    assert ch.Changes(new, tmp_path, prev_db=tmp_path / "none.db").snapshot()["state"] == "none"
    os.link(new, tmp_path / "linked.db")
    assert ch.Changes(new, tmp_path, prev_db=tmp_path / "linked.db").snapshot()["state"] == "same"


def test_the_first_sentence_counts_what_came_and_left_and_flags_the_unexplained(where, two, tmp_path):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped")], SAID)
    prev, new = (ch.read_library(p) for p in two)
    got = {**ch.compare(prev, new, tmp_path), "state": "ready", "night": "2026-11-16"}
    t = nightly.tonight(where, changes=got, now=at("2026-11-17T09:00:00"))
    assert t["sentence"]["state"] == BAD and "其中 1 份查不出原因" in t["sentence"]["text"]
    got["totals"] = {**got["totals"], "unexplained": 0}
    t = nightly.tonight(where, changes=got, now=at("2026-11-17T09:00:00"))
    assert t["sentence"]["state"] == WARN and "4 份可能是我們這邊漏的" in t["sentence"]["text"]
    got["totals"] = {**got["totals"], "look": 0}
    t = nightly.tonight(where, changes=got, now=at("2026-11-17T09:00:00"))
    assert t["sentence"]["text"] == "11-16 夜正常：新進 3 份、離開 15 份（都有原因）"


# ---------------------------------------------------------------- the page

def test_the_home_draws_seven_nights_and_lists_what_left_for_no_reason(where, two, tmp_path):
    # panels v3: the nights on the night clock (question 2), and under 每晚進出 the documents nobody can explain
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped")], SAID)
    prev, new = (ch.read_library(p) for p in two)
    got = {**ch.compare(prev, new, tmp_path), "state": "ready", "night": "2026-11-16", "took": "1 秒",
           "prev": {"swapped_at": "2026-11-16T15:03:00+08:00", "docs": 16},
           "new": {"swapped_at": "2026-11-17T03:05:00+08:00", "docs": 4}}
    t = nightly.tonight(where, changes=got, now=at("2026-11-17T09:00:00"))
    data = tonight_page.timeline_data(t, at("2026-11-17T09:00:00"))
    # only the nights since the chain went live have a row (owner 11-17): this made-up one went live on 11-16
    assert len(data["nights"]) == 1 and data["nights"][0]["title"] == "11-16 夜" and data["max"] == 8
    assert [x["name"] for x in data["nights"][0]["segs"]] == ["爬取", "導出", "轉文字", "建庫", "換庫"]
    assert {x["tone"] for x in data["nights"][0]["segs"]} == {"done"}  # no hue for a step that is done
    hist = {"enabled": False, "why": "沒有設定紀錄檔（--ops-db）"}
    page = home_page.home_body({"steps": []}, t, got, hist, {}, now=at("2026-11-17T09:00:00"))
    assert "查不出原因" in page and "/check?ref=" + "https%3A%2F%2Fschool.example%2Flost" in page
    assert "沒有在記每晚的紀錄" in page


def test_the_page_escapes_what_it_prints(where):
    crawler_log(where, NIGHT[:3], at("2026-11-17T01:30:00"))
    t = nightly.tonight(where, now=at("2026-11-17T01:40:00"))
    t["nights"][-1]["cells"][0]["say"] = "<b>x</b>"
    t["nights"][-1]["label"] = "<i>y</i>"
    page = home_page.home_body({"steps": []}, t, {"state": "none", "words": "<i>z</i>"},
                               {"enabled": False, "why": "<u>w</u>"}, {}, now=at("2026-11-17T01:40:00"))
    for raw in ("<b>x</b>", "<i>y</i>", "<u>w</u>"):
        assert raw not in page
    assert "&lt;i&gt;y&lt;/i&gt;" in page and "&lt;u&gt;w&lt;/u&gt;" in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "&lt;i&gt;z&lt;/i&gt;" in page and "<i>z</i>" not in page
    assert "data-live" in page  # the night runs: the page keeps asking
    later = nightly.tonight(where, now=at("2026-11-17T01:45:00"))
    assert later["sig"] == nightly.tonight(where, now=at("2026-11-17T01:40:00"))["sig"]  # minutes never reload


def test_the_api_and_the_night_view_carry_tonight(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        got = client.get("/api/tonight")
        moved = client.get("/tonight", follow_redirects=False)
        old = client.get("/tonight", params={"night": "2026-11-21"}, follow_redirects=False)
        night = client.get("/api/night")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    data = got.json()
    assert len(data["nights"]) == 7 and data["sentence"]["text"] and data["changes"]["state"] == "none"
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        brief = client.get("/api/tonight?brief=1").json()
    assert brief["sig"] == data["sig"] and set(brief["changes"]) <= {"state", "words", "keys", "night", "totals"}
    # 11-23: 每晚運行 is 內容's night view (/sites?date=<run day>); an old night key lands on its run day
    assert moved.status_code == 307 and moved.headers["location"] == "/sites?date="
    assert old.status_code == 307 and old.headers["location"].startswith("/sites?date=2026-11-22")
    assert night.status_code == 200 and night.headers["cache-control"] == "no-store" and "run" in night.json()


def test_a_page_of_a_menu_only_left_for_a_reason(tmp_path):
    # 11-19: the export marks a page whose content is in its button rows (缺什么 starts with corpus.IN_BUTTONS); the
    # build leaves it out under in_button_rows, and the panels' own comparison says so too
    from nknu.content import corpus
    folder = manifest(tmp_path, "exp/a-2", [
        {"网址": U + "outline?c=1", "分类": "抓到了", "结果": "抓到了", "本地档案": "pages/1.html",
         "缺什么": corpus.IN_BUTTONS + "（3 位老師）"}])
    new = {"aliases": {}, "urls": {}, "sites": {"a": {"name": "範例處", "export": folder, "docs": 0}}, "docs": {}}
    prev = {"sites": {"a": {"name": "範例處", "export": "exp/a-1", "docs": 1}}}
    page = ("a", "page", "課程大綱", U + "outline?c=1", "", 100, "x")
    assert ch.why_left("d1", page, new, prev, ch.Manifests(tmp_path)) == ("menu_only", "")
    assert ch.REASONS["menu_only"][2] == ch.OK_KIND and ch.IN_BUTTONS == corpus.IN_BUTTONS


def test_sites_not_all_converted_in_time_are_said_on_the_night(where):
    # 11-19: the ingest leaves a site whose files were not all converted on its old export and builds the rest
    # (sites_waiting in its status and history line); the night says which sites wait, and for how many files
    waiting = [{"site": "ie", "files": 12}, {"site": "math", "files": 3}]
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped", sites_waiting=waiting)], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    totext = cells(t)["totext"]
    said = "2 個網站到點還沒轉完（ie、math，還有 15 個檔），這一晚沒換它們的新資料，下一晚接著轉"
    assert totext["state"] == nightly.DONE and totext["say"] == said
    assert totext["sev"] == WARN and totext["fix"] == CONTENT
    assert cells(t)["swap"]["state"] == nightly.DONE  # the rest of the sites went in
    assert said in nightly.run_reason(run("swapped", sites_waiting=waiting))["reason"]
    # nothing else new: the night is deferred, and says the same sites
    ingest(where, [run("deferred", step="convert", error="lanes_timeout", sites_waiting=waiting)], SAID[:3])
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert cells(t)["totext"]["say"] == said + "；沒有別的新資料，這晚不建庫"
    assert nightly.waiting_words(run("swapped")) == "" and nightly.waiting_words({"sites_waiting": "x"}) == ""


def test_an_outcome_and_its_error_that_say_the_same_thing_say_it_once():
    got = nightly.run_reason(run("deferred", step="convert", error="lanes_timeout"))
    assert got["reason"] == "兩小時內沒轉完" and got["code"] == "deferred · lanes_timeout" and not got["unknown"]


def test_sites_the_guard_set_back_or_did_not_try_again_are_said_on_the_swap(where):
    # 11-19: a library the swap guard stops is rebuilt without the sites it stopped for (sites_set_back), and a site
    # whose newest export is still the one set back is not tried again (sites_refused); the swap went ahead, and
    # the night says which sites kept their old data and why
    back = [{"site": "a.example", "why": "unexplained_departures", "count": 232, "folder": "exp/a-3"}]
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    ingest(where, [run("swapped", sites_set_back=back, sites_refused=["b.example"])], SAID)
    swap = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["swap"]
    said_back = "把關退回 1 個網站：a.example（說不出原因的離開太多，232 份），這一晚沒換它們的新資料"
    said_refused = "1 個網站最新的導出還是上次被退回的那一份，這一晚沒再試（b.example），等爬蟲給新的導出"
    assert swap["state"] == nightly.DONE and swap["say"] == f"換上了；{said_back}；{said_refused}"
    assert swap["sev"] == WARN and swap["fix"] == CONTENT
    # only sites not tried again: the crawler's to give a new export
    ingest(where, [run("swapped", sites_refused=["b.example"])], SAID)
    swap = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))["swap"]
    assert swap["say"] == f"換上了；{said_refused}" and swap["sev"] == WARN and swap["fix"] == CRAWLER
    assert nightly.set_back_words(run("swapped")) == ("", "")
    assert said_back in nightly.run_reason(run("held", sites_set_back=back))["reason"]


def test_the_sites_a_browser_opens_are_crawl_time_not_export_time(where):
    # 11-21: the browser-opened sites run after the change list and before the export; the script notes their start
    # and end, and until now their three quarters of an hour were counted as 導出's
    night = [*NIGHT[:5], ("2026-11-17 01:52:00", "JS 网站开始（w3.nknu.edu.tw,ord.nknu.edu.tw）"),
             ("2026-11-17 02:35:00", "JS 网站结束（都好）"),
             ("2026-11-17 02:50:00", "导出好了（35 个网站；每站的结果在 x）"),
             ("2026-11-17 02:51:00", "定期更新结束（导出 35 个网站；seen.tsv 写好了）")]
    crawler_log(where, night, at("2026-11-17T02:51:00"))
    crawler_status(where)
    c = cells(nightly.tonight(where, now=at("2026-11-17T09:00:00")))
    assert c["crawl"]["when"] == "02:35 · 1 小時 35 分" and c["export"]["when"] == "02:51 · 16 分鐘"
    assert "其中要用瀏覽器開的網站 01:52–02:35" in c["crawl"]["tip"] and "新的 135" in c["crawl"]["tip"]
    # while the browser is still opening them the night says so
    crawler_log(where, night[:6], at("2026-11-17T02:10:00"))
    c = cells(nightly.tonight(where, now=at("2026-11-17T02:12:00")))
    assert c["export"]["state"] == nightly.RUN and "開要用瀏覽器開的網站" in c["export"]["say"]
