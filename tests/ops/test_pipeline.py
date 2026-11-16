"""流水線 (owner 11-16: 「让我知道整个项目是否正常运作，出错的话是哪里错了 能帮助我精准定位」): every step read from what
it left behind, each with a verdict and, when it is not normal, who fixes it; a probe that fails says so on its own step
and never hides the others."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from nknu.ops import pipeline, pipeline_page
from nknu.ops.pipeline import BAD, CONTENT, INTERFACE, NONE, OK, PANEL, WARN, Pipeline, Step, Where

TODAY = date(2026, 11, 16)
PIPE = pipeline.PIPE


def _library(path: Path, built_at: str = "2026-11-16T14:56:00+08:00") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE sites (site TEXT PRIMARY KEY, name TEXT, unit TEXT, data_as_of TEXT,"
                 " missing_pages INTEGER, missing_files INTEGER, docs INTEGER, export TEXT, deleted_notices INTEGER,"
                 " under_read INTEGER)")
    meta = {"built_at": built_at, "schema_version": "pipeline-8",
            "counts_json": json.dumps({"docs": {"notice": 3, "page": 2, "total": 5}, "chunks": 40}),
            "excluded_json": json.dumps({"网页(列表翻页)": 7, "公告(学校删掉)": 2, "附件(副本)": 1}),
            "inputs_json": json.dumps([{"site": "school.example"}, {"site": "school.example/board"}])}
    conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    conn.execute("INSERT INTO sites VALUES ('school.example', '範例處', '範例處', '2026-11-15', 0, 0, 5,"
                 " 'archive-export/sites/school.example-1', 2, 0)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def where(tmp_path) -> Where:
    db = _library(tmp_path / PIPE / "aca.db")
    return Where(root=tmp_path, db=db, today=lambda: TODAY)


# ---------------------------------------------------------------- 建庫


def test_the_build_says_what_went_in_what_came_out_and_what_it_left_out(where):
    step = pipeline.build_step(where)
    assert step.state == OK and "11-16 14:56" in step.say
    # what went in: the documents plus what the build left out (5 + 7 + 2 + 1), two sites
    assert ("份文件", 5) in step.out and ("個小段（搜尋時比對的單位）", 40) in step.out
    assert step.into == [("筆整理前的網頁、檔案和行事曆（2 站）", 15)] and step.bar == [5, 15]
    reasons = dict(step.lost)
    assert reasons["公告列表的分頁（各則另外收了）"] == 7 and reasons["學校刪掉的公告，只剩空殼"] == 2


def test_a_held_library_newer_than_the_one_in_use_is_for_the_content_session(where):
    held = where.db.with_name("aca.held.db")
    held.write_bytes(b"")
    later = where.db.stat().st_mtime + 60
    import os
    os.utime(held, (later, later))
    step = pipeline.build_step(where)
    assert step.state == WARN and step.fix == CONTENT and "被擋下" in step.say
    os.utime(held, (later - 3600, later - 3600))  # older than the library in use: an old one, nothing to say
    assert pipeline.build_step(where).state == OK


# ---------------------------------------------------------------- 檢查

BUILT = "2026-11-16T14:56:00+08:00"  # the made-up library's built_at


def _checks(folder: Path, *, top10: int = 27, fails: tuple[str, ...] = ("7", "13"), built: str = BUILT) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    metrics = {"question_copies": {"all": {"n": 33, "top1": 12, "top3": 20, "top10": top10, "mrr": 0.499}},
               "agent_copies": {"all": {"n": 33, "top1": 12, "top3": 24, "top10": 30, "mrr": 0.56}}}
    (folder / "offline_new_r3.json").write_text(json.dumps({"db_built_at": built, "metrics": metrics}),
                                                encoding="utf-8")
    rows = "\n".join(f"| {k} | 第 {k} 項的名字 | FAIL | 0 |" for k in fails)
    (folder / "checks.md").write_text(
        f"# 真资料检查\n- 资料库：`aca.db`（3727 MB，建于 {built}，资料截至 2026-11-15）\n"
        f"- 结果：{22 - len(fails)} 过、{len(fails)} 不过\n\n| # | 检查 | 结果 | 数字 |\n|---|---|---|---|\n{rows}\n",
        encoding="utf-8")


def test_the_checks_are_the_builds_own_results(where):
    _checks(where.pipe)
    step = pipeline.check_step(where)
    assert step.state == OK and ("題答案排進前 10", 27) in step.out and "真資料檢查 22 項過了 20 項" in step.say
    assert ("真資料檢查第 7 項：第 7 項的名字", 1) in step.lost


def test_checks_of_the_library_before_are_no_verdict(where):
    _checks(where.pipe, built="2026-11-15T06:35:00+08:00")
    step = pipeline.check_step(where)
    assert step.state == NONE and "上一份庫的結果" in step.say


def test_doing_worse_than_the_build_before_is_for_the_content_session(where):
    _checks(where.pipe / "lab_pc" / "previous" / "20261116-144845", top10=28, fails=("7",))
    _checks(where.pipe / "lab_pc" / "previous" / "20261116-150000-trial", top10=10)  # a trial's: never the baseline
    _checks(where.pipe, top10=27, fails=("7", "13"))
    step = pipeline.check_step(where)
    assert step.state == WARN and step.fix == CONTENT
    assert "從 28 題掉到 27 題" in step.say and "第 13 項" in step.say and "第 7 項" not in step.say


def test_no_check_results_yet(where):
    assert pipeline.check_step(where).state == NONE


# ---------------------------------------------------------------- 服務


class _Answer(BaseHTTPRequestHandler):
    built = BUILT
    asked: ClassVar[list[str]] = []

    def log_message(self, *_args):
        pass

    def _send(self, code: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/aca.json"):  # Datasette: the built_at of the library it has open
            assert self.headers["Host"].startswith("127.0.0.1:")
            self._send(200, [{"value": self.built}])
        elif self.path == "/stamp":  # 資料總覽
            self._send(200, {"built": self.built[:16].replace("T", " "), "state": "ok", "error": ""})
        else:
            self._send(403, {})  # the crawler's viewer refuses a name not this machine's, at once

    def do_POST(self):
        asked = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).asked.append(asked["method"])
        assert self.headers["Host"].startswith("127.0.0.1:")
        if asked["method"] == "initialize":
            self._send(200, {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "nknu"}}})
        else:
            text = json.dumps({"results": [{"id": "d1"}, {"id": "d2"}]})
            self._send(200, {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}})


@pytest.fixture
def server():
    _Answer.asked = []
    _Answer.built = BUILT
    pipeline._searched.clear()
    srv = HTTPServer(("127.0.0.1", 0), _Answer)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _services(url: str) -> tuple:
    return (("搜尋服務（MCP）", f"{url}/mcp", "mcp"), ("資料庫", url, "datasette"), ("資料總覽", url, "overview"),
            ("爬蟲看板", f"{url}/", "http"))


def test_services_that_answer_are_normal_and_the_search_runs_once_an_hour(where, server):
    _library(where.library, built_at=BUILT)
    where.services = _services(server)
    step = pipeline.serve_step(where)
    assert step.state == OK and "回 2 筆" in step.say
    pipeline.serve_step(where)
    assert _Answer.asked.count("tools/call") == 1 and _Answer.asked.count("initialize") == 2


def test_a_panel_still_on_the_library_before_says_to_restart_it(where, server):
    _library(where.library, built_at=BUILT)
    _Answer.built = "2026-11-15T06:35:00+08:00"
    where.services = _services(server)
    step = pipeline.serve_step(where)
    assert step.state == WARN and "資料庫還開著舊庫" in step.say and "資料總覽還開著舊庫" in step.say


def test_a_service_that_does_not_answer_is_an_error_with_who_fixes_it(where, server):
    closed = HTTPServer(("127.0.0.1", 0), _Answer)
    port = closed.server_port
    closed.server_close()
    where.services = (("資料庫", server, "datasette"), ("搜尋服務（MCP）", f"http://127.0.0.1:{port}/mcp", "mcp"))
    step = pipeline.serve_step(where)
    assert step.state == BAD and step.fix == INTERFACE and "搜尋服務（MCP） 沒有回應" in step.say


# ---------------------------------------------------------------- 每晚更新


def _night(where: Where, **status) -> None:
    pipe = where.root / PIPE
    base = {"night": "2026-11-15", "started_at": "2026-11-16T02:31:00+08:00",
            "finished_at": "2026-11-16T03:40:00+08:00", "outcome": "swapped", "step": "done", "error": None,
            "sites_new": [{"site": "school.example"}], "library": {"before": {"docs": 10}, "after": {"docs": 12}},
            "held": None, "new_reminders": []}
    base.update(status)
    (pipe / "ingest-status.json").write_text(json.dumps(base), encoding="utf-8")
    with (pipe / "ingest-history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(base) + "\n")


def test_no_nightly_status_yet_is_no_verdict(where):
    step = pipeline.nightly_step(where)
    assert step.state == NONE and "還沒跑過" in step.say


def test_last_night_swapped_is_normal(where, monkeypatch):
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 16, 9, 0, tzinfo=pipeline.TAIPEI))
    _night(where)
    step = pipeline.nightly_step(where)
    assert step.state == OK and "換上了新庫" in step.say and step.took == "69 分鐘"
    assert ("份文件（換後）", 12) in step.out


def test_a_failed_night_and_a_missed_night_are_errors(where, monkeypatch):
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 16, 9, 0, tzinfo=pipeline.TAIPEI))
    _night(where, outcome="failed", error="build.sh 退出码 1")
    step = pipeline.nightly_step(where)
    assert step.state == BAD and step.fix == CONTENT and "build.sh 退出码 1" in step.say
    _night(where, night="2026-11-13", outcome="swapped")  # nothing since: last night did not run
    step = pipeline.nightly_step(where)
    assert step.state == BAD and "之後沒有再跑" in step.say


def test_a_held_night_lists_the_sites_that_fell(where, monkeypatch):
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 16, 9, 0, tzinfo=pipeline.TAIPEI))
    _night(where, outcome="held", held={"detail": [{"site": "school.example", "before": 100, "after": 30}]})
    step = pipeline.nightly_step(where)
    assert step.state == WARN and step.lost == [("school.example：100 → 30 份", 1)]


# ---------------------------------------------------------------- the snapshot and the page


def test_a_probe_that_fails_says_so_on_its_own_step_and_the_others_still_show(where):
    def broken(_where):
        raise RuntimeError("壞了")

    flow = Pipeline(where, [("build", "建庫", pipeline.build_step), ("odd", "某一步", broken)])
    flow.snapshot()
    flow.wait(10)
    snap = flow.snapshot()
    steps = {s["key"]: s for s in snap["steps"]}
    assert steps["build"]["state"] == OK
    assert steps["odd"]["state"] == WARN and steps["odd"]["fix"] == PANEL and "壞了" in steps["odd"]["say"]
    assert snap["state"] == WARN and "某一步" in snap["say"]


def test_the_overall_verdict_is_the_worst_step():
    steps = [Step("a", "甲", "", state=OK), Step("b", "乙", "", state=NONE), Step("c", "丙", "", state=BAD)]
    assert pipeline.overall(steps) == (BAD, "有 1 處出錯：丙")
    assert pipeline.overall(steps[:2]) == (OK, "每一步都正常")


def test_the_snapshot_is_kept_and_read_again_when_old(where):
    calls = []

    def counted(w):
        calls.append(1)
        return Step("x", "某一步", "", state=OK, say="好")

    flow = Pipeline(where, [("x", "某一步", counted)], every=3600)
    assert flow.snapshot()["steps"] == [] or True  # the first reading runs in the background
    flow.wait(10)
    flow.snapshot()
    flow.wait(10)
    assert len(calls) == 1
    flow.snapshot(fresh=True)
    flow.wait(10)
    assert len(calls) == 2


def test_the_page_draws_every_step_and_escapes_what_it_prints(where):
    step = Step("x", "某一步", "做<某事>", state=WARN, say="<b>壞</b>", fix=CONTENT,
                into=[("份", 3)], out=[("份", 2)], lost=[("理由<i>", 1)], more=[("看細節", "overview")])
    snap = {"at": "2026-11-16T19:00:00+08:00", "took": "1 秒", "state": WARN, "say": "有 1 處要看：某一步",
            "steps": [asdict_(step)], "running": False}
    page = pipeline_page.pipeline_body(snap, {"overview": "http://127.0.0.1:18879"})
    assert "&lt;b&gt;壞&lt;/b&gt;" in page and "<b>壞</b>" not in page and "理由&lt;i&gt;" in page
    assert "交給內容（建庫）session" in page and "http://127.0.0.1:18879" in page
    assert "data-state='warn'" in page and "有 1 處要看：某一步" in page
    assert "讀取中" in pipeline_page.pipeline_body({"steps": [], "running": True}, {})


def asdict_(step: Step) -> dict:
    from dataclasses import asdict
    return asdict(step)


def test_the_sites_table_puts_problems_first_and_can_be_filtered():
    snap = {"site_cols": [("docs", "文件", "庫裡的文件")],
            "sites": [{"key": "a.example", "name": "甲處", "q": "甲处 甲處 a.example", "worst": WARN,
                       "cells": {"docs": {"n": 0, "state": WARN, "tip": "導出有 3 行，庫裡 0 份"}},
                       "why": ["導出有東西，庫裡沒有"]},
                      {"key": "b.example", "name": "乙處", "q": "乙处 b.example", "worst": OK,
                       "cells": {"docs": {"n": 12}}, "why": []}]}
    html = pipeline_page.sites_html(snap)
    assert html.index("甲處") < html.index("乙處") and "1 個要留意" in html and "id='sitefind'" in html
    # one number per cell; a step to look at is a small mark with an icon, never a coloured number (redesign 11-17)
    assert "<td data-v='0' data-tip='導出有 3 行，庫裡 0 份'><span class='pl-m warn'" in html and "<svg" in html
    assert "class='warn'" not in html
    assert pipeline_page.sites_html({"sites": [], "site_cols": []}) == ""


def test_times_and_spans_read_as_people_say_them():
    assert pipeline.stamp("2026-11-16T14:56:30+08:00") == "11-16 14:56"
    assert pipeline.stamp(None) == "" and pipeline.span(45) == "45 秒" and pipeline.span(13 * 60) == "13 分鐘"
    assert pipeline.span(125 * 60) == "2 小時 5 分"
    assert pipeline.ago("2026-11-16T14:00:00+08:00", datetime(2026, 11, 16, 15, 0, tzinfo=pipeline.TAIPEI)) == 3600
    assert pipeline.ago("not a time") is None
    assert timedelta(seconds=pipeline.EVERY) <= timedelta(minutes=10)


# ---------------------------------------------------------------- 網頁核查's numbers


def test_the_page_checks_numbers_go_to_each_sites_row_without_an_alarm(where):
    view = {"progress": {"phase": "done", "done": 30, "total": 30},
            "totals": {"pages": 30, "checked": 30, "gap": 4, "menu_links": 1, "check": 2, "nofetch": 1},
            "sites": [{"key": "school.example", "name": "範例處", "pages": 30, "gap": 4, "nofetch": 1, "check": 2,
                       "menu_links": 1}]}
    where.sweep = lambda: view
    step = Step("crawl", "爬取", "")
    pipeline.page_check_cells(where, step)
    row = where.sites["school.example"]
    assert row["cells"]["check"]["n"] == 5 and row["worst"] == OK
    assert pipeline.site_table(where)[0]["href"] == "/pipeline/site?site=school.example"
    assert any("正文區有漏 4 頁" in n for n in step.notes)
    where.sites = {}
    where.sweep = lambda: {"progress": {"phase": "idle"}, "totals": None, "sites": []}
    step = Step("crawl", "爬取", "")
    pipeline.page_check_cells(where, step)
    assert where.sites == {} and any("還沒跑過" in n for n in step.notes)


# ---------------------------------------------------------------- one site's page


def test_one_site_shows_its_export_as_the_library_read_it(tmp_path, monkeypatch):
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    where = Where(root=tmp_path, db=w.db, today=lambda: TODAY)
    detail = pipeline.site_detail(where, made.SITE)
    ex = detail["export"]
    assert detail["site"]["name"] == made.NAMES[made.SITE] and ex["rows"] == len(w.rows[made.SITE])
    assert ex["outcomes"]["抓到了"] >= 1 and ex["exported_at"].startswith("2026-11-09")
    assert pipeline.site_detail(where, "nowhere.example") is None
    page = pipeline_page.site_body(detail, {"tables": "http://127.0.0.1:18001"})
    assert "網頁核查這個網站" in page and "/aca/docs?site=school.example" in page and "導出清單" in page


def test_what_an_export_says_is_missing_is_counted_by_the_words_it_holds():
    assert pipeline.missing_parts("连接失败（1 个）、缺图片（1 个）") == ["連線失敗", "圖片沒抓到"]
    # a part with its own 「、」 is one kind, never split on it
    assert pipeline.missing_parts("要人看、内文区是空的（公告的标题栏和内文栏都没有内容）") == ["內文區是空的", "要人看"]
    assert pipeline.missing_parts("") == []


def test_the_export_step_sums_the_exports_the_library_read(tmp_path, monkeypatch):
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    where = Where(root=tmp_path, db=w.db, today=lambda: TODAY)
    step = pipeline.export_step(where)
    total = sum(len(r) for r in w.rows.values())
    assert step.state == OK and step.into == [("筆（每筆一個網頁、檔案或圖片）", total)] and not step.bar
    assert where.sites[made.SITE]["cells"]["export"]["n"] == len(w.rows[made.SITE])
    (w.exports[made.BOARD] / "manifest.json").unlink()  # the export service cleaned a folder the library uses
    where.sites = {}
    step = pipeline.export_step(where)
    assert step.state == WARN and step.fix == CONTENT and made.BOARD in step.say
    assert where.sites[made.BOARD]["worst"] == WARN


# ---------------------------------------------------------------- 爬取


def _ledger(events: list[tuple[str, str, dict]], meta: dict, results: list[tuple[str, str, int]]) -> sqlite3.Connection:
    """A made-up ledger with only what the crawl step reads: event, meta and the url_result view's two columns."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE event (at TEXT, code TEXT, message TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE url_result (owner_site TEXT, result TEXT)")
    conn.executemany("INSERT INTO event VALUES (?, ?, ?)", [(a, c, json.dumps(m)) for a, c, m in events])
    conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    for site, result, n in results:
        conn.executemany("INSERT INTO url_result VALUES (?, ?)", [(site, result)] * n)
    return conn


NOW = datetime(2026, 11, 16, 19, 0, tzinfo=pipeline.TAIPEI)


@pytest.fixture
def ledger(where, tmp_path, monkeypatch):
    archive = tmp_path / "crawl"
    archive.mkdir()
    (archive / "frontier.db").write_bytes(b"")
    where.archive = archive
    monkeypatch.setattr(pipeline, "now_tpe", lambda: NOW)

    def use(conn):
        monkeypatch.setattr(pipeline, "open_ledger", lambda _archive: conn)

    return use


RESULTS = [("school.example", "ok", 5), ("school.example", "failed", 2), ("school.example", "pending_check", 3),
           ("elsewhere.example", "ok", 9)]  # a host outside the library's sites: never counted


def test_a_run_going_on_with_a_fresh_heartbeat_is_normal(where, ledger):
    start = {"sites": ["school.example"], "pid": 19}
    ledger(_ledger([("2026-11-16T08:28:38+00:00", "run_start", start)],
                   {"run:alive_at": str(NOW.timestamp() - 30)}, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == OK and step.say.startswith("在跑：11-16 16:28 開始、1 站")
    assert step.into == [("個這一輪該抓的網址（1 站）", 10)] and step.out == [("個抓到了", 5)]
    row = where.sites["school.example"]["cells"]["crawl"]
    assert row["n"] == 5 and row["state"] == OK and "失敗：2" in row["tip"]


def test_a_run_without_its_end_and_without_a_heartbeat_stopped(where, ledger):
    ledger(_ledger([("2026-11-16T08:28:38+00:00", "run_start", {"sites": []})],
                   {"run:alive_at": str(NOW.timestamp() - 3600)}, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == BAD and step.fix == pipeline.CRAWLER and "沒有收尾" in step.say


def test_how_a_run_ended_is_the_crawlers_own_words(where, ledger):
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": ["school.example"]}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    ledger(_ledger(events, {}, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == OK and "抓完了" in step.say and step.took == "2 分鐘"
    events[-1] = ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "disk_low"})
    ledger(_ledger(events, {}, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == BAD and "硬碟空間不夠" in step.say


def test_a_running_run_far_longer_than_a_night_is_worth_a_look(where, ledger):
    ledger(_ledger([("2026-11-16T00:00:00+00:00", "run_start", {"sites": []})],
                   {"run:alive_at": str(NOW.timestamp() - 10)}, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == WARN and "比平常久很多" in step.say


def test_the_nightly_update_read_from_its_status_file_when_the_crawler_code_is_older(where, ledger, monkeypatch):
    from nknu.archive import progress
    monkeypatch.delattr(progress, "update_state", raising=False)  # the copy the tools run: an 11-08 crawler
    reports = where.archive / "reports"
    reports.mkdir()
    (reports / "update-status.json").write_text(json.dumps({"failed_step": "review", "counts": {"new": 4}}),
                                                encoding="utf-8")
    meta = {"update:sites": json.dumps(["school.example"]), "update:night_started": str(NOW.timestamp() - 7200),
            "run:alive_at": "0"}
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": []}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == BAD and "停在「算有哪些變動」這一步" in step.say


# ---------------------------------------------------------------- 導出: the crawler's newer exports


def test_a_newer_export_waits_and_after_a_day_is_worth_a_look(tmp_path, monkeypatch):
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    where = Where(root=tmp_path, db=w.db, today=lambda: TODAY)
    current = tmp_path / pipeline.EXPORTS / "current.tsv"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(f"{made.SITE}\t/work/archive-export/sites/school.example-20261116-013100\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 16, 9, 0, tzinfo=pipeline.TAIPEI))
    step = pipeline.export_step(where)
    assert step.state == OK and "1 站的新導出等著進庫" in step.say
    assert "還沒進庫" in where.sites[made.SITE]["cells"]["export"]["sub"]
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 18, 9, 0, tzinfo=pipeline.TAIPEI))
    where.sites = {}
    step = pipeline.export_step(where)
    assert step.state == WARN and step.fix == CONTENT and where.sites[made.SITE]["worst"] == WARN


# ---------------------------------------------------------------- 轉文字


def _totext(where: Where, *, pending: int = 0, inflight: str = "") -> None:
    folder = where.pipe / "totext"
    folder.mkdir(parents=True, exist_ok=True)
    by_status = {"ok": 7, "empty": 2, "failed": 1, **({"pending": pending} if pending else {})}
    (folder / "summary.json").write_text(json.dumps({
        "files": sum(by_status.values()), "by_status": by_status,
        "top_reasons": [{"status": "empty", "reason": "OCR 没认出字", "files": 2}]}), encoding="utf-8")
    lines = [{"key": "school.example:1", "status": "ok"}] * 7 + [
        {"key": "school.example:8", "status": "empty", "reason": "OCR 没认出字", "file": "files/scan.pdf"},
        {"key": "school.example:9", "status": "empty", "reason": "OCR 没认出字", "file": "files/scan2.pdf"},
        {"key": "school.example:10", "status": "failed", "reason": "LibreOffice 超时（120 秒）", "file": "files/a.doc"}]
    (folder / "accounting.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                            encoding="utf-8")
    kit = where.pipe / "lab_pc"
    kit.mkdir(parents=True, exist_ok=True)
    for lane in "ABC":
        (kit / f"lane{lane}.log").write_text(
            f"[lane {lane}] 开线\n[lane {lane}] 这条线的档都走过一遍了（导出 1 份）\n[lane {lane}] done 2026-11-16 19:00\n",
            encoding="utf-8")
    if inflight:
        (kit / f"lane{inflight}.inflight").write_text("1794826841", encoding="utf-8")


def test_the_to_text_step_counts_what_the_library_used(where):
    _totext(where)
    step = pipeline.totext_step(where)
    assert step.state == OK and step.out == [("個轉成文字", 7)] and ("轉文字失敗", 1) in step.lost
    assert "轉 PDF 和 Office 檔的程序 11-16 19:00 走完了" in step.notes[0] and step.say == "建庫時文字都已轉完"
    text = where.sites["school.example"]["cells"]["text"]
    assert text["n"] == 7 and text["sub"] == "讀不出字 2" and "LibreOffice" in text["tip"]


def test_files_left_unconverted_while_every_lane_stopped_are_for_the_content_session(where):
    _totext(where, pending=3)
    step = pipeline.totext_step(where)
    assert step.state == WARN and step.fix == CONTENT and "3 個沒轉" in step.say
    _totext(where, pending=3, inflight="B")
    assert pipeline.totext_step(where).state == OK  # a lane is converting them


# ---------------------------------------------------------------- 每晚進庫 before it runs at night


def test_a_nightly_ingest_only_run_by_hand_is_not_yet_judged_for_missed_nights(where, monkeypatch):
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 19, 9, 0, tzinfo=pipeline.TAIPEI))
    where.today = lambda: date(2026, 11, 19)
    _night(where, started_at="2026-11-16T18:55:00+08:00", night="2026-11-16", outcome="nothing_new")
    step = pipeline.nightly_step(where)
    assert step.state == NONE and "還沒在夜裡跑過" in step.notes[0]


def test_the_first_line_counts_the_sites_that_need_a_look():
    steps = [Step("a", "甲", "", state=OK)]
    assert pipeline.overall(steps, [{"worst": WARN}, {"worst": OK}]) == (WARN, "每一步都正常；1 個網站要留意")
    assert pipeline.overall(steps, []) == (OK, "每一步都正常")


def test_the_flow_map_shows_each_steps_state_by_an_icon_and_its_number():
    steps = [asdict_(Step("crawl", "爬取", "", state=OK, figure="74,962", unit="個抓到了")),
             asdict_(Step("nightly", "每晚進庫", "", state=NONE))]
    html = pipeline_page.flow_map(steps)
    assert html.count("class='pl-node'") == 2 and html.count("class='pl-wire'") == 1
    assert "href='#step-crawl'" in html and "74,962" in html and "<svg" in html  # the state is never colour alone
    assert "還沒結果" in html  # a step without its number says its state


def test_a_step_bar_is_drawn_only_for_one_unit_and_the_rest_of_the_reasons_float():
    step = asdict_(Step("x", "某一步", "", state=OK, into=[("個網址", 200)], out=[("個抓到了", 150)], bar=[150, 200],
                        lost=[(f"理由{i}", 10 - i) for i in range(6)]))
    html = pipeline_page.stage_html(step, {})
    assert "width:75.0%" in html and "75%" in html
    assert html.count("class='tag'") == 4 and "還有 2 種" in html and "理由5：5" in html
    words = pipeline_page.stage_html(asdict_(Step("y", "另一步", "", state=OK, into=[("個網站", 3)], out=[("份文件", 9)])), {})
    assert "pl-track" not in words and "進 3<" in words and "<b>9</b> 份文件" in words
    assert "進 3 個網站" in words  # the whole noun floats over the numbers


def test_what_the_rules_leave_or_keep_for_round_two_is_no_loss_of_the_crawl(where, ledger):
    results = [("school.example", "ok", 6), ("school.example", "failed", 2), ("school.example", "rule", 30),
               ("school.example", "later", 2)]
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": ["school.example"]}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    ledger(_ledger(events, {}, results))
    step = pipeline.crawl_step(where)
    assert step.bar == [6, 8] and step.into == [("個這一輪該抓的網址（1 站）", 8)]
    assert [why for why, _n in step.lost] == ["連接失敗"] and step.notes[0].startswith("不算在內：")


def test_the_nightly_export_read_from_the_status_file_as_the_crawler_reads_it(where, ledger, monkeypatch):
    # the crawler's export batch (eb2b831): review writes "export": null, the seen step its record; a site that failed
    # to export is red; the sites owed to the next night are said
    from nknu.archive import progress
    monkeypatch.delattr(progress, "update_state", raising=False)  # the copy the tools run may be an older crawler
    reports = where.archive / "reports"
    reports.mkdir()
    started = str(NOW.timestamp() - 3600)
    meta = {"update:sites": json.dumps(["school.example"]), "update:night_started": started,
            "update:night_reviewed": started}
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": []}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    status = reports / "update-status.json"
    status.write_text(json.dumps({"counts": {"new": 2}, "export": None}), encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    assert "還在導出" in pipeline.crawl_step(where).say
    status.write_text(json.dumps({"export": {"exported": ["a"], "failed": ["b.example"]}}), encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == BAD and "1 站導出失敗" in step.say
    status.write_text(json.dumps({"export": {"exported": ["a", "c"], "failed": []}}), encoding="utf-8")
    (reports / "export-owed.json").write_text(json.dumps(["b.example"]), encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == OK and "跑完了" in step.say and any("導了 2 站" in n for n in step.notes)
    assert step.notes[0].startswith("變動了、還沒導出的網站 1 個")
    status.write_text(json.dumps({"failed_step": "export-list"}), encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    assert "停在「列出要導出的網站」這一步" in pipeline.crawl_step(where).say


# ---------------------------------------------------------------- rows filled from an older answer (owner 11-17)


def test_rows_filled_from_an_older_answer_are_counted_shown_and_listed(tmp_path, monkeypatch):
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    manifest = w.exports[made.SITE] / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["项目"][0]["来源"] = "10-24 旧存档"
    data["项目"][0]["备注"] = "这一轮：页面几乎是空的；用 10-24 旧存档的副本补上"
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    where = Where(root=tmp_path, db=w.db, today=lambda: TODAY)
    step = pipeline.export_step(where)
    assert step.notes[0].startswith("用舊資料補的 1 筆") and "1 筆抓壞了、用舊資料補上" in step.flow_words
    cell = where.sites[made.SITE]["cells"]["refill"]
    assert cell["n"] == 1 and cell["state"] == OK and "10-24 舊存檔：1" in cell["tip"]
    page = pipeline_page.site_body(pipeline.site_detail(where, made.SITE), {})
    assert "改用舊資料補上的 1 筆" in page and "頁面幾乎是空的" in page and "/check?ref=" in page


def test_a_newer_export_with_more_refilled_rows_is_for_the_crawler(tmp_path, monkeypatch):
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    data = json.loads((w.exports[made.SITE] / "manifest.json").read_text(encoding="utf-8"))
    for row in data["项目"][:2]:
        row["来源"] = "上一次抓到的"
    newer = tmp_path / pipeline.EXPORTS / "school.example-20261117-013000"
    newer.mkdir(parents=True)
    (newer / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / pipeline.EXPORTS / "current.tsv").write_text(f"{made.SITE}\t/work/{pipeline.EXPORTS}/{newer.name}\n",
                                                            encoding="utf-8")
    monkeypatch.setattr(pipeline, "now_tpe", lambda: datetime(2026, 11, 17, 9, 0, tzinfo=pipeline.TAIPEI))
    where = Where(root=tmp_path, db=w.db, today=lambda: TODAY)
    step = pipeline.export_step(where)
    assert step.state == WARN and step.fix == pipeline.CRAWLER and "用舊資料補的變多了" in step.say
    assert where.sites[made.SITE]["cells"]["refill"]["state"] == WARN


# ---------------------------------------------------------------- the page's layout (redesign 2026-11-17)

def test_a_normal_step_is_one_closed_row_and_a_step_to_look_at_opens_on_who_fixes_it():
    ok = pipeline_page.stage_html(asdict_(Step("a", "甲步", "做甲", state=OK, say="都好", fix=CONTENT,
                                               notes=["細一", "細二"])), {})
    warn = pipeline_page.stage_html(asdict_(Step("b", "乙步", "做乙", state=WARN, say="少了", fix=CONTENT,
                                                 lost=[("某理由", 3)])), {})
    bad = pipeline_page.stage_html(asdict_(Step("c", "丙步", "做丙", state=BAD, say="壞了", fix=CONTENT)), {})
    assert "<details class='pl-stage' id='step-a' data-state='ok'>" in ok  # closed
    assert "data-state='warn' open>" in warn and "data-state='bad' open>" in bad
    assert "交給內容（建庫）session" in warn and "某理由 <b>3</b>" in warn and "少了" in warn
    assert "交給" not in ok  # a normal step names no one to fix it
    # the state once: a mark with an icon and a word; no pill, no 「細節（滑過去看）」; the notes float
    for row in (ok, warn, bad):
        assert "class='pill" not in row and "滑過去看" not in row and row.count("class='pl-mark'") == 1
    assert "正常</span>" in ok and "<svg" in ok and "data-tip='細一\n細二'" in ok


def test_the_flow_map_is_the_overview_and_a_click_opens_that_steps_row():
    steps = [asdict_(Step("crawl", "爬取", "", state=OK, figure="5", unit="個")),
             asdict_(Step("build", "建庫", "", state=WARN, figure="3", unit="份"))]
    page = pipeline_page.pipeline_body({"at": "", "took": "", "state": WARN, "say": "有 1 處要留意：建庫",
                                        "steps": steps, "running": False}, {})
    for key in ("crawl", "build"):
        assert f"href='#step-{key}'" in page and f"id='step-{key}'" in page
    assert "pl-node" in page and "d.open=true" in page  # PIPE_JS opens the row it scrolls to
    assert page.index("class='pl-flowmap'") < page.index("class='pl-chain")


def test_a_site_cells_second_line_and_the_sites_address_float():
    snap = {"site_cols": [("crawl", "抓到", "抓到的網址")],
            "sites": [{"key": "a.example", "name": "甲處", "q": "a.example", "worst": OK, "href": "/pipeline/site?site=a",
                       "cells": {"crawl": {"n": 4, "state": OK, "tip": "連接失敗：2", "sub": "共 6 個網址"}}, "why": []}]}
    html = pipeline_page.sites_html(snap)
    assert "<td data-v='4' data-tip='共 6 個網址\n連接失敗：2'>4</td>" in html
    assert "pl-sub" not in html and "data-tip='a.example'>甲處</a>" in html

