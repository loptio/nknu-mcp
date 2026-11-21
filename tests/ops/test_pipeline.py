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

from nknu.ops import home_page, pipeline, pipeline_page, tonight_page
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


TONIGHT = {"now": "2026-11-17T09:00:00+08:00", "default": "2026-11-16", "nights": [], "live": False, "sig": "x",
           "sentence": {"state": "ok", "text": "昨晚正常：換了新庫", "fix": ""}}


def test_the_page_draws_every_step_and_escapes_what_it_prints(where):
    step = Step("x", "某一步", "做<某事>", state=WARN, say="<b>壞</b>", fix=CONTENT,
                into=[("份", 3)], out=[("份", 2)], lost=[("理由<i>", 1)], more=[("看細節", "overview")])
    snap = {"at": "2026-11-16T19:00:00+08:00", "took": "1 秒", "state": WARN, "say": "有 1 處要看：某一步",
            "steps": [asdict_(step)], "running": False}
    page = home_page.home_body(snap, TONIGHT, None, {"enabled": False, "why": ""}, {"overview": "http://127.0.0.1:18879"})
    assert "&lt;b&gt;壞&lt;/b&gt;" in page and "<b>壞</b>" not in page and "理由&lt;i&gt;" in page
    assert "交給" not in page and "http://127.0.0.1:18879" in page  # nobody named to fix it (spec 八.2)
    assert "data-state='warn'" in page and "#step-x'>某一步</a>" in page  # a step not normal is a problem
    # a step the page has no node for is still a node with its panel (nothing a probe reports is left out)
    assert "data-pick='step-x'" in page and "data-panel='step-x'" in page and "repeat(8,minmax(0,1fr))" in page
    reading = home_page.home_body({"steps": [], "running": True}, TONIGHT, None, {"enabled": False}, {})
    assert "正在讀每一步的紀錄" in reading and "location.replace" in reading


def asdict_(step: Step) -> dict:
    from dataclasses import asdict
    return asdict(step)


def test_the_home_lists_only_the_sites_to_look_at_and_leads_to_every_site():
    # 要處理的網站 (panels v3 三.5): what, at which step, who; the most urgent first; every site is on 網站
    snap = {"site_cols": [("docs", "文件", "庫裡的文件")],
            "sites": [{"key": "a.example", "name": "甲處", "q": "甲处 甲處 a.example", "worst": WARN,
                       "href": "/site?site=a.example",
                       "cells": {"docs": {"n": 0, "state": WARN, "tip": "導出有 3 行，庫裡 0 份"}},
                       "why": ["庫裡一份文件也沒有（交給內容 session 看是學校沒東西還是建庫丟了）"]},
                      {"key": "b.example", "name": "乙處", "q": "乙处 b.example", "worst": OK,
                       "cells": {"docs": {"n": 12}}, "why": []}]}
    rows = tonight_page.handle_rows(snap, None)
    assert [r["key"] for r in rows] == ["a.example"]
    assert rows[0]["issues"] == [{"sev": WARN, "what": "庫裡一份文件也沒有（看是學校沒東西還是建庫丟了）", "step": "建庫"}]
    # since 2026-11-18 a site to look at is a row of 問題, once (the separate table said the same sites again)
    html = home_page.problems_section(tonight_page.problems(TONIGHT, {"steps": []}, rows), False)
    assert "甲處" in html and "乙處" not in html and "data-q='3'" in html
    assert "<a href='/site?site=a.example'>甲處</a>" in html
    assert "交給" not in html and "<svg" in html  # the mark has its icon
    assert "沒有問題" in home_page.problems_section([], False)
    many = [{**rows[0], "key": f"s{i}.example", "name": f"站{i}"} for i in range(13)]
    capped = home_page.problems_section(tonight_page.problems(TONIGHT, {"steps": []}, many), False)
    assert capped.count("<li data-sev=") == 13 and "還有 1 個" in capped  # the rest is folded, never dropped
    assert "<span class='hm-count num'>13</span>" in capped


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
    assert pipeline.site_table(where)[0]["href"] == "/site?site=school.example"
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
    page = pipeline_page.site_steps(detail)
    assert "導出清單" in page and "<h3>爬取</h3>" in page and "<h3>建庫</h3>" in page


def _step(page: str, name: str) -> str:
    """One step's section of site_steps' page."""
    return page.split(f"<h3>{name}</h3>", 1)[1].split("</section>", 1)[0]


def test_a_steps_list_shows_five_of_each_kind_of_loss_and_folds_the_rest():
    bad = [("failed", "壞了", "x/broken1.pdf"), ("failed", "壞了", "x/broken2.pdf")]
    bad += [("empty", "OCR 沒認出字", f"images/p{i}.png") for i in range(28)]  # the accounting lists up to 30
    refills = [(f"https://school.example/r{i}", f"補的頁 {i}", "10-24 旧存档", "") for i in range(12)]
    detail = {"site": {"key": "a", "name": "範例處", "docs": 3},
              "export": {"rows": 50, "outcomes": {"抓到了": 50}, "examples": {}, "exported_at": "2026-11-16T01:20:00+08:00",
                         "folder": "exp/a-1", "paging": 0, "missing": {}, "refill": {"10-24 旧存档": 12},
                         "refills": refills},
              "text": {"status": {"ok": 10, "empty": 41, "failed": 2}, "bad": bad}}
    page = pipeline_page.site_steps(detail)
    text = _step(page, "轉文字")
    seen, folded = text.split("<details", 1)
    assert seen.count("轉文字失敗：") == 2 and seen.count("讀不出文字：") == 5
    assert seen.index("轉文字失敗：") < seen.index("讀不出文字：")  # failures first
    assert "<summary>" in folded and "還有 36 個" in folded and folded.count("讀不出文字：") == 23
    assert "其餘 13 個沒有列出來" in folded  # 41 empty, 28 listed by the accounting
    refill = _step(page, "導出")
    seen, folded = refill.split("<details", 1)
    assert seen.count("/check?ref=") == 5 and "還有 7 個" in folded and folded.count("/check?ref=") == 7
    small = pipeline_page.site_steps({**detail, "text": {"status": {"ok": 1, "failed": 1}, "bad": bad[:1]}})
    assert "<details" not in _step(small, "轉文字")  # nothing to fold: no fold row


def test_what_an_export_says_is_missing_is_counted_by_the_words_it_holds():
    assert pipeline.missing_parts("连接失败（1 个）、缺图片（1 个）") == ["連線失敗", "圖片沒抓到"]
    # a part with its own 「、」 is one kind, never split on it
    assert pipeline.missing_parts("要人看、内文区是空的（公告的标题栏和内文栏都没有内容）") == ["內文區是空的", "待確認"]
    # a page left out by rule (the crawler of 11-18) is no kind of what is missing; with a real lack beside it, only that
    assert pipeline.missing_parts("按规则不抓：整页跳到校外页（用户 11-17 决定不抓）") == []
    assert pipeline.missing_parts("缺附件（2 个）、按规则不抓：整页跳到校外页（用户 11-17 决定不抓）") == ["附件沒抓到"]
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


def test_the_home_draws_the_steps_as_nodes_and_shows_first_the_one_to_look_at():
    # 2026-11-18: the pipeline is the page's second section, a node a step, a picked node's detail beside the row
    steps = [asdict_(Step("crawl", "爬取", "", state=OK, figure="5", unit="個")),
             asdict_(Step("build", "建庫", "", state=WARN, figure="3", unit="份"))]
    snap = {"at": "", "took": "", "state": WARN, "say": "有 1 處要留意：建庫", "steps": steps, "running": False}
    page = home_page.home_body(snap, TONIGHT, None, {"enabled": False}, {})
    assert "pl-flowmap" not in page and "各步驟細節" not in page
    for key in ("crawl", "build"):
        assert f"data-pick='step-{key}'" in page and f"<article class='hm-panel' data-panel='step-{key}'" in page
        assert f"id='step-{key}'" not in page  # no id of the address's name: the browser must not jump to a panel
    assert "data-first='step-build'" in page  # 留意 before what is normal
    assert page.index("data-q='2'") < page.index("data-q='3'") < page.index("data-q='4'")


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


@pytest.mark.parametrize("judge", [True, False])
def test_a_held_export_is_a_warning_for_the_crawler_and_a_real_failure_stays_red(where, ledger, monkeypatch, judge):
    # 11-17 (the crawler session): a site whose new export dropped a row the 10-24 copy filled is held, listed in
    # failed too, and its old export stays in use
    from nknu.archive import progress
    if not judge:
        monkeypatch.delattr(progress, "update_state", raising=False)  # an older crawler: the same rules read here
    reports = where.archive / "reports"
    reports.mkdir()
    started = str(NOW.timestamp() - 3600)
    meta = {"update:sites": json.dumps(["school.example"]), "update:night_started": started,
            "update:night_reviewed": started}
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": []}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    status = reports / "update-status.json"
    held = {"b.example": "1 行用 10-24 舊存檔補的，新導出沒補上"}
    status.write_text(json.dumps({"export": {"exported": ["a"], "failed": ["b.example"], "held": held}}),
                      encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == WARN and step.fix == pipeline.CRAWLER and "導出攔下 1 站" in step.say
    assert any("b.example（1 行用 10-24 舊存檔補的，新導出沒補上）" in n and "照舊用前一份導出" in n for n in step.notes)
    status.write_text(json.dumps({"export": {"exported": ["a"], "failed": ["b.example", "c.example"], "held": held}}),
                      encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == BAD and "1 站導出失敗" in step.say and "攔下 1 站" in step.say


@pytest.mark.parametrize("judge", [True, False])
def test_a_manual_hold_is_said_in_the_crawl_step_and_never_a_warning(where, ledger, monkeypatch, judge):
    from nknu.archive import progress
    if not judge:
        monkeypatch.delattr(progress, "update_state", raising=False)
    reports = where.archive / "reports"
    reports.mkdir()
    started = str(NOW.timestamp() - 3600)
    meta = {"update:sites": json.dumps(["school.example"]), "update:night_started": started,
            "update:night_reviewed": started}
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": []}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    (reports / "update-status.json").write_text(json.dumps({"export": {
        "exported": ["a"], "failed": ["sso.example/vita"], "held": {"sso.example/vita": "人工暂缓：按钮行太多"}}}),
        encoding="utf-8")
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert step.state == OK and not step.fix and "跑完了" in step.say
    assert any("人工暫緩的站：sso.example/vita（按鈕行太多）" in n for n in step.notes)


def test_a_manual_hold_is_said_on_its_sites_page_as_plain_news(tmp_path, monkeypatch):
    from nknu.ops import site_page
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    archive = tmp_path / "crawl"
    (archive / "reports").mkdir(parents=True)
    (archive / "reports" / "update-status.json").write_text(json.dumps(
        {"export": {"exported": [], "failed": [made.SITE], "held": {made.SITE: "人工暂缓：按钮行太多"}}}),
        encoding="utf-8")
    where = Where(root=tmp_path, db=w.db, archive=archive, today=lambda: TODAY)
    step = pipeline.export_step(where)
    assert step.state == OK and not step.fix and "人工暫緩 1 站" in step.say
    row = next(r for r in pipeline.site_table(where) if r["key"] == made.SITE)
    assert row["worst"] == OK and row not in [r for r in pipeline.site_table(where) if r["worst"] in (WARN, BAD)]
    state, said = site_page.now_line(row, None, {}, read=True)
    assert state == OK and said.startswith("人工暫緩：按鈕行太多") and "交給" not in said


def test_a_held_site_says_so_on_its_row_and_its_page(tmp_path, monkeypatch):
    from nknu.ops import site_page
    from nknu.retrieval import vectors
    from tests.ops import world as made

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    archive = tmp_path / "crawl"
    (archive / "reports").mkdir(parents=True)
    (archive / "reports" / "update-status.json").write_text(json.dumps(
        {"export": {"exported": [], "failed": [made.SITE], "held": {made.SITE: "1 行用 10-24 舊存檔補的，新導出沒補上"}}}),
        encoding="utf-8")
    where = Where(root=tmp_path, db=w.db, archive=archive, today=lambda: TODAY)
    step = pipeline.export_step(where)
    assert step.state == WARN and step.fix == pipeline.CRAWLER and "攔下 1 站" in step.say
    row = next(r for r in pipeline.site_table(where) if r["key"] == made.SITE)
    state, said = site_page.now_line(row, None, {}, read=True)
    assert state == WARN and said == "新導出被攔下：1 行用 10-24 舊存檔補的，新導出沒補上"  # the crawler's own words


def _batch_ledger(events: list, meta: dict) -> sqlite3.Connection:
    """A made-up ledger whose url_result has the columns round two's open batch is told by (id, added_at) and the
    crawler's round2_promoted (batch.TABLE's first columns)."""
    conn = _ledger(events, meta, [])
    conn.execute("DROP TABLE url_result")
    conn.execute("CREATE TABLE url_result (id INTEGER PRIMARY KEY, owner_site TEXT, result TEXT, added_at REAL)")
    conn.execute("CREATE TABLE round2_promoted (url_id INTEGER PRIMARY KEY, batch TEXT NOT NULL, at REAL NOT NULL)")
    conn.execute("CREATE VIEW url AS SELECT * FROM url_result")
    rows = ([("school.example", "ok", 1000.0)] * 6 + [("school.example", "failed", 1000.0)] * 2  # round one's
            + [("school.example", "todo", 900.0)] * 4  # promoted from later: round2_promoted holds them
            + [("school.example", "todo", 5000.0)] * 3 + [("school.example", "ok", 5000.0)]  # what they brought
            + [("unit.example", "todo", 800.0)] * 5)  # a round-two site of the batch (not in the library)
    for i, (site, result, added) in enumerate(rows, 1):
        conn.execute("INSERT INTO url_result VALUES (?, ?, ?, ?)", (i, site, result, added))
    for i in [*range(9, 13), *range(17, 22)]:
        conn.execute("INSERT INTO round2_promoted VALUES (?, ?, 4000.0)", (i, "2026-11-17"))
    return conn


def test_round_twos_open_batch_is_said_and_kept_out_of_round_ones_whole(where, ledger):
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": ["school.example", "unit.example"]}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    meta = {"round2:open": json.dumps({"sites": ["school.example", "unit.example"]})}
    ledger(_batch_ledger(events, meta))
    step = pipeline.crawl_step(where)
    assert step.into == [("個這一輪該抓的網址（1 站）", 8)] and step.bar == [6, 8]
    assert "第二輪這一批在抓（2 站）" in step.say
    assert step.notes[0] == "第二輪這一批在抓（2 站）：抓到 1 / 13 個網址，不算在上面的比例裡"
    # the batch closed: its rows are this round's
    ledger(_batch_ledger(events, {}))
    step = pipeline.crawl_step(where)
    assert "第二輪" not in step.say and step.into == [("個這一輪該抓的網址（1 站）", 16)]
    # a ledger without round2_promoted cannot tell them apart: the batch is said, the number left as it is
    ledger(_ledger(events, meta, RESULTS))
    step = pipeline.crawl_step(where)
    assert "第二輪這一批在抓（2 站）" in step.say and step.into == [("個這一輪該抓的網址（1 站）", 10)]
    assert step.notes[0].startswith("第二輪這一批在抓（2 站）：帳本分不出")


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
    assert step.notes[0].startswith("這次沒抓好、用舊存檔頂替的 1 筆") and "1 筆這次沒抓好、用舊存檔頂替" in step.flow_words
    cell = where.sites[made.SITE]["cells"]["refill"]
    assert cell["n"] == 1 and cell["state"] == OK and "10-24 舊存檔：1" in cell["tip"]
    page = pipeline_page.site_steps(pipeline.site_detail(where, made.SITE))
    assert "這次沒抓好、用舊存檔頂替的 1 筆" in page and "頁面幾乎是空的" in page and "/check?ref=" in page


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
    assert step.state == WARN and step.fix == pipeline.CRAWLER and "用舊存檔頂替的變多了" in step.say
    assert where.sites[made.SITE]["cells"]["refill"]["state"] == WARN


# ---------------------------------------------------------------- the page's layout (redesign 2026-11-17)

def test_a_normal_step_is_one_closed_row_and_a_step_to_look_at_opens_on_what_is_wrong():
    ok = pipeline_page.stage_html(asdict_(Step("a", "甲步", "做甲", state=OK, say="都好", fix=CONTENT,
                                               notes=["細一", "細二"])), {})
    warn = pipeline_page.stage_html(asdict_(Step("b", "乙步", "做乙", state=WARN, say="少了", fix=CONTENT,
                                                 lost=[("某理由", 3)])), {})
    bad = pipeline_page.stage_html(asdict_(Step("c", "丙步", "做丙", state=BAD, say="壞了", fix=CONTENT)), {})
    assert "<details class='pl-stage' id='step-a' data-state='ok'>" in ok  # closed
    assert "data-state='warn' open>" in warn and "data-state='bad' open>" in bad
    assert "某理由 <b>3</b>" in warn and "少了" in warn
    assert "交給" not in ok + warn + bad  # no step names anyone to fix it (spec 八.2)
    # the state once: a mark with an icon and a word; no pill, no 「細節（滑過去看）」; the notes float
    for row in (ok, warn, bad):
        assert "class='pill" not in row and "滑過去看" not in row and row.count("class='pl-mark'") == 1
    assert "正常</span>" in ok and "<svg" in ok and "data-tip='細一\n細二'" in ok


def test_a_sites_issues_name_the_step_and_a_reading_without_them_still_works():
    row = {"key": "a.example", "name": "甲處", "worst": WARN, "href": "/site?site=a",
           "cells": {"crawl": {"n": 4, "state": OK}, "refill": {"n": 3, "state": WARN}},
           "why": ["新導出用舊資料補的從 0 筆變成 3 筆（最近抓壞的變多；交給爬蟲 session）"]}
    old = tonight_page.handle_rows({"sites": [row]}, None)  # a reading from before cells kept their issues
    new = tonight_page.handle_rows({"sites": [{**row, "issues": [{"col": "refill", "state": WARN, "why": row["why"][0]}]}]},
                                   None)
    for rows in (old, new):
        assert rows[0]["issues"] == [{"sev": WARN, "what": "新導出用舊資料補的從 0 筆變成 3 筆（最近抓壞的變多）",
                                      "step": "導出"}]
    # the last swap's departures nobody can explain make a site the most urgent, the build's to look at
    changes = {"state": "ready", "reasons": {}, "sites": [{"site": "b.example", "name": "乙處", "unexplained": 2,
                                                            "look": 0, "reasons": {}}]}
    both = tonight_page.handle_rows({"sites": [row]}, changes)
    assert [r["key"] for r in both] == ["b.example", "a.example"] and both[0]["sev"] == BAD
    assert both[0]["issues"][0] == {"sev": BAD, "what": "上次換庫說不出原因地離開 2 份", "step": "換庫"}


# ---------------------------------------------------------------- one site's page (structure design 2026-11-17 二)

def test_one_sites_changes_show_its_figures_reasons_and_lists_or_why_there_are_none():
    reasons = {"gone": {"label": "學校刪了", "tip": "", "kind": "ok", "fix": ""},
               "lost": {"label": "查不出原因", "tip": "", "kind": "unknown", "fix": pipeline.CONTENT}}
    ready = {"state": "ready", "reasons": reasons, "prev": {"swapped_at": "2026-11-16T15:03:00+08:00"},
             "new": {"swapped_at": "2026-11-17T00:49:00+08:00"},
             "sites": [{"site": "a.example", "name": "甲處", "new": 2, "changed": 1, "left": 3, "unexplained": 1,
                        "look": 0, "reasons": {"gone": 2, "lost": 1},
                        "lists": {"new": [{"id": "d1", "title": "新的一則"}], "changed": [],
                                  "left": {"gone": [{"id": "d9", "url": "https://a.example/9", "title": "刪掉的"}]}}}]}
    html = pipeline_page.one_site_changes(ready, "a.example")
    assert "<span>新進</span><b>2</b>" in html and "<span>離開</span><b>3</b>" in html
    assert html.index("查不出原因") < html.index("學校刪了")  # what nobody can explain first
    assert "新的一則" in html and "/check?ref=https%3A%2F%2Fa.example%2F9" in html and "11-16 15:03 換上的庫" in html
    assert "這個網站沒有變動" in pipeline_page.one_site_changes(ready, "b.example")
    assert "計算中" in pipeline_page.one_site_changes({"state": "computing"}, "a.example")
    assert "沒有前一份" in pipeline_page.one_site_changes({"state": "none", "words": "沒有前一份庫可以比"}, "a.example")


def test_a_sites_line_says_whether_it_is_fine_and_what_is_not():
    from selectolax.lexbor import LexborHTMLParser

    from nknu.ops.site_page import kinds_html, now_line, site_problems
    reasons = {"miss": {"kind": "look", "fix": pipeline.CRAWLER}}
    assert now_line({"worst": OK, "why": []}, None, {}, read=True) == (OK, "每一步都正常")
    assert now_line(None, None, {}, read=False)[0] == pipeline.NONE
    assert now_line(None, None, {}, read=True)[0] == pipeline.NONE  # not in 流水線's table: no green mark
    row = {"worst": WARN, "why": ["導出少了（交給爬蟲 session）"]}
    mine = {"unexplained": 2, "look": 1, "reasons": {"miss": 1}}
    assert now_line(row, mine, reasons, read=True) == (BAD, "有 3 個問題")  # the rows are in 問題, the worst first
    assert [(x["where"], x["state"], x["reason"]) for x in site_problems(row, mine)] == [
        ("上次換庫", "出錯", "說不出原因地離開 2 份"), ("這個網站", "留意", "導出少了"),
        ("上次換庫", "留意", "可能是我們這邊漏掉的 1 份")]
    assert now_line(row, None, reasons, read=True) == (WARN, "導出少了")  # one problem: in its own words
    html = kinds_html("a.example", {"notice": [3, 1], "event": [2, 2]}, "http://127.0.0.1:18001")
    assert "公告" in html and "行事曆" in html and "共 5 份" in html
    assert "/aca/site_undated_why?site=a.example" in html and "var(--k0)" in html and "var(--k3)" in html
    assert "<b>1</b> 份" in html and "公告、網頁、附件共 3 份" in html  # 沒日期: of those kinds only
    assert "%" not in LexborHTMLParser(html).text()  # counts, no shares
    counting = kinds_html("a.example", None, "")
    assert "計算中" in counting and "/aca/" not in counting


def test_an_export_counts_a_file_several_rows_share_once(tmp_path):
    # 11-19: a Google Sites picture several pages show is a row a page, all of one file (as a redirect's row shares
    # its landing page's): the export summary's files are files, its rows stay rows
    rows = [{"类型": "图片", "结果": "抓到了", "网址": f"https://lh3.example/{i}", "本地档案": "images/1.png"} for i in range(3)]
    rows.append({"类型": "网页", "结果": "抓到了", "网址": "https://g.example/", "本地档案": "pages/1.html"})
    (tmp_path / "manifest.json").write_text(json.dumps({"网站": "g", "项目": rows}, ensure_ascii=False), encoding="utf-8")
    summary = pipeline.manifest_summary(tmp_path)
    assert summary["rows"] == 4 and summary["files"] == 2 and summary["kinds"]["图片"] == 3


# ---------------------------------------------------------------- 對帳、爬取裡另外跑的、待確認（11-21）


def test_what_needs_a_look_says_what_it_is_about():
    parts = pipeline.missing_parts
    assert parts("要人看：页面几乎是空的") == ["這一頁幾乎是空的"]
    assert parts("要人看（2 个）、缺内嵌页或按钮（1 个）") == ["頁面裡的內嵌頁或按鈕沒抓到", "頁面連到的東西裡有待確認的"]
    assert parts("要人看") == ["待確認"]


def _rows_export(folder: Path) -> None:
    rows = [{"类型": "网页", "结果": "抓到了", "网址": "https://s.example/a", "本地档案": "pages/a.html", "存档编号": "u1",
             "缺什么": "要人看（1 个）、缺内嵌页或按钮（1 个）"},
            {"类型": "网页", "结果": "抓到了", "网址": "https://s.example/b#按钮=1", "本地档案": "pages/b.html",
             "存档编号": "a7"},
            {"类型": "附件", "结果": "按规则不抓", "网址": "https://s.example/c.pdf", "存档编号": "u2"},
            {"类型": "图片", "结果": "抓到了", "网址": "https://s.example/i.png", "本地档案": "pages/a.html",
             "存档编号": "u3"}]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"网站": "s", "项目": rows}, ensure_ascii=False), encoding="utf-8")


def test_an_export_counts_its_rows_with_a_file_its_button_rows_and_the_ledgers_ids(tmp_path):
    _rows_export(tmp_path)
    summary = pipeline.manifest_summary(tmp_path)
    assert (summary["rows"], summary["file_rows"], summary["files"]) == (4, 3, 2)
    assert summary["got_buttons"] == 1 and summary["both"] == 1
    assert summary["got_no_file"] == 0 and summary["file_else"] == {}
    assert pipeline.manifest_ids(tmp_path) == (frozenset({1, 3}), {2: "按规则不抓"})
    json.dumps(summary)  # the summary goes out as JSON: the ids are kept apart from it


def _address_ledger(rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE url_result (id INTEGER, owner_site TEXT, kind TEXT, kind0 TEXT, found_via TEXT, result TEXT)")
    conn.executemany("INSERT INTO url_result VALUES (?, ?, ?, ?, ?, ?)", rows)
    return conn


def test_the_export_follows_from_the_crawl_term_by_term(where, ledger):
    site = "school.example"
    ledger(_address_ledger([
        (1, site, "page", None, "link", "ok"),         # fetched, and the export says so
        (2, site, "file", None, "link", "ok"),         # on the export under another result
        (3, site, "file", "script", "link", "ok"),     # a site's .js: kind says file, kind0 script; no row by design
        (4, site, "image", None, "redirect", "ok"),    # a redirect's landing: the row is the first address's
        (5, site, "page", None, "link", "failed"),     # the export (older) says fetched, the ledger no longer
        (6, "elsewhere.example", "page", None, "link", "ok"),  # another site's: never counted
        (7, site, "drive", "drive", "redirect", "ok")]))       # a Drive record: the file itself has its row
    step = Step("export", "導出", "")
    # the exports' fetched rows: u1, u1 again in a second export, u5, and a button's row
    assert pipeline.export_link(where, step, 4, {1, 5}, {2: "按规则不抓"}, 1) == 0
    by_words = {words: (sign, n, tip) for sign, words, n, tip in step.link}
    assert step.link[0][:3] == ["", "爬取抓到的網址", 5] and step.link[-1][:3] == ["=", "清單上「抓到了」的", 4]
    off = by_words["抓到了、照設計不另開一筆的（不是內容，或記在別的筆上）"]
    assert off[:2] == ("−", 3) and "程式檔（.js：是網頁的零件，不是內容）：1" in off[2] and "跳轉後的落點" in off[2]
    assert "Google 雲端硬碟的檔案資訊和資料夾清單（檔案本身那一筆在清單上）：1" in off[2]
    assert by_words["抓到了、清單上沒有，也說不出原因的"][1] == 0
    assert by_words["清單上有，記的是別的結果"][:2] == ("−", 1) and "按規則不抓：1" in by_words["清單上有，記的是別的結果"][2]
    assert by_words["同一個網址在清單上不只一筆（幾個網站都連到它）"][1] == 1
    assert by_words["按鈕按出來的內容（清單一筆，爬蟲不算網址）"][1] == 1 and step.link_left == 0
    # a fetched file on no export that is none of those kinds may be content left out: counted apart and returned
    ledger(_address_ledger([(1, site, "page", None, "link", "ok"), (8, site, "file", None, "link", "ok")]))
    step = Step("export", "導出", "")
    assert pipeline.export_link(where, step, 1, {1}, {}, 0) == 1 and step.link_left == 0
    stray = {words: (n, tip) for _s, words, n, tip in step.link}["抓到了、清單上沒有，也說不出原因的"]
    assert stray == (1, "檔案：1")
    # round two's open batch is not in the crawl step's own figure: the link starts at that figure and adds the batch
    where.sites = {site: {"cells": {"crawl": {"n": 3}}}}
    ledger(_address_ledger([(i, site, "page", None, "link", "ok") for i in range(1, 5)]))
    step = Step("export", "導出", "")
    pipeline.export_link(where, step, 4, {1, 2, 3, 4}, {}, 0)
    assert [row[:3] for row in step.link[:2]] == [["", "爬取抓到的網址", 3], ["+", "第二輪這一批抓到的（爬取那一步另外算）", 1]]
    assert step.link_left == 0


def test_the_to_text_run_takes_the_exports_rows_with_a_file_and_the_files_inside_archives(where):
    _totext(where)
    _rows_export(where.root / "archive-export" / "sites" / "school.example-1")
    path = where.pipe / "totext" / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**summary, "files": 5, "members": 2}), encoding="utf-8")
    step = pipeline.totext_step(where)
    assert [row[:3] for row in step.link] == [["", "導出存下檔案的（清單上一筆算一個）", 3], ["+", "壓縮檔裡拆出來的檔", 2],
                                              ["=", "轉文字經手的", 5]] and step.link_left == 0
    path.write_text(json.dumps({**summary, "files": 9, "members": 2}), encoding="utf-8")
    assert pipeline.totext_step(where).link_left == 4  # four files the export's rows do not account for


def _assembled(where: Where, **counts) -> None:
    conn = sqlite3.connect(where.library)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'inputs_json'",
                 (json.dumps([{"site": "school.example", "corpus": f"{PIPE}/corpus/school.example.json"}]),))
    conn.commit()
    conn.close()
    folder = where.pipe / "corpus"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "school.example.assemble_report.json").write_text(json.dumps(counts), encoding="utf-8")


def test_the_build_follows_from_the_to_text_run_through_the_records(where):
    # the library of the fixture: 5 documents and 10 left out; 20 files handled, 5 of them folded away by assemble
    _totext(where)
    path = where.pipe / "totext" / "summary.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "files": 20}), encoding="utf-8")
    _assembled(where, records=15, images_inlined=3, containers_dropped=1, data_rows={"4": "行事历"}, images_no_text=0)
    step = pipeline.build_step(where)
    assert step.into[0] == ("筆記錄（轉文字的結果整理成的）", 15) and step.link_left == 0
    words = [row[1] for row in step.link]
    assert "圖的字併進了顯示它的網頁" in words and "圖沒讀出夠多的字，也沒有收進庫的網頁顯示它" not in words
    assert [row[2] for row in step.link if row[0] == "="] == [15, 15]
    _assembled(where, records=14, images_inlined=3, containers_dropped=1, data_rows={"4": "行事历"})
    assert pipeline.build_step(where).link_left == 2  # a record short of the files, and one short of the documents
    # a library whose inputs name no corpus (built before the reports were kept) has no link to show
    (where.pipe / "corpus" / "school.example.assemble_report.json").unlink()
    step = pipeline.build_step(where)
    assert step.link == [] and step.link_left is None and step.into[0][1] == 15


def _side_ledger(archive: Path, *, status: dict | None, term: dict | None, events: list, urls: list = ()):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE event (at TEXT, level TEXT, code TEXT, message TEXT)")
    conn.execute("CREATE TABLE url_result (owner_site TEXT, state TEXT, reason TEXT, fetched_at TEXT)")
    conn.executemany("INSERT INTO event VALUES (?, ?, ?, ?)", [(a, lv, c, json.dumps(m)) for a, lv, c, m in events])
    conn.executemany("INSERT INTO url_result VALUES (?, ?, ?, ?)", urls)
    (archive / "reports").mkdir(parents=True, exist_ok=True)
    for name, what in (("update-status.json", status), ("schedule-term.json", term)):
        if what is not None:
            (archive / "reports" / name).write_text(json.dumps(what, ensure_ascii=False), encoding="utf-8")
    return conn


SAT = datetime(2026, 11, 21, 9, 0, tzinfo=pipeline.TAIPEI)
JS = {"opened": 30, "incomplete": 1, "requests": 150, "skipped": 0}


def test_the_crawls_three_side_runs_are_each_said_from_the_crawlers_own_files(tmp_path):
    status = {"night_started": "2026-11-21T01:03:34+08:00", "revisit_tonight": ["out.example", "two.example"],
              "revisit_idle": ["wix.example"], "put_back": {"revisit": 446}}
    term = {"at": "2026-11-21T01:03:38+08:00", "page_term": "115 第一學期", "ledger_term": "115 第一學期",
            "changed": False, "walk": None, "open": None, "let_go": 0, "held_left": 0}
    events = [("2026-11-19T18:03:48+00:00", "info", "jsfetch_start", {"sites": ["js.example"]}),
              ("2026-11-19T18:18:08+00:00", "info", "jsfetch_end", {"end": "finished", "sites": {"js.example": JS}}),
              ("2026-11-20T18:41:47+00:00", "info", "jsfetch_start", {"sites": ["js.example"]}),
              ("2026-11-20T18:56:03+00:00", "info", "jsfetch_end", {"end": "finished", "sites": {"js.example": JS}}),
              ("2026-11-20T18:57:33+00:00", "info", "jsfetch_start", {"sites": ["js.example"]}),
              ("2026-11-20T19:11:01+00:00", "info", "jsfetch_end", {"end": "finished", "sites": {"js.example": JS}})]
    urls = [("out.example", "done", None, "2026-11-20T17:30:00+00:00")] * 5 + [
        ("out.example", "failed", "http_500", "2026-11-20T17:31:00+00:00"),
        ("out.example", "done", None, "2026-11-13T17:30:00+00:00"),   # the Saturday before: not this night's
        ("two.example", "pending", "update_revisit", None)]           # put back and not reached
    sides = {name: (tone, words) for name, tone, words in pipeline.crawl_sides(
        _side_ledger(tmp_path, status=status, term=term, events=events, urls=urls), tmp_path, SAT)}
    tone, words = sides["校外網站每週重看"]
    assert tone == WARN and "輪到 2 站，排了 446 頁" in words and "抓了 5 個網址、失敗 1" in words and "還有 1 頁沒輪到抓" in words
    tone, words = sides["要用瀏覽器開的網站"]  # the two passes of this night, never the night before's
    assert tone == OK and "開了 2 趟、1 站：共開 60 頁、送了 300 個請求、2 頁沒開完整" in words and "都開完了" in words
    assert sides["課表學期"] == (OK, "沒變：115 第一學期（11-21 01:03 看的，沒送請求）")


def test_a_side_run_that_did_not_run_or_went_wrong_says_so(tmp_path):
    status = {"night_started": "2026-11-20T01:00:10+08:00", "revisit_tonight": [], "revisit_idle": ["a", "b"]}
    term = {"at": "2026-11-18T01:03:38+08:00", "page_term": "115 第二學期", "ledger_term": "115 第一學期",
            "changed": True, "walk": "not whole", "open": "115 第二學期", "let_go": 1500, "held_left": 3200}
    events = [("2026-11-18T18:41:47+00:00", "info", "jsfetch_start", {"sites": ["js.example"]}),
              ("2026-11-18T18:56:03+00:00", "info", "jsfetch_end", {"end": "finished", "sites": {}})]
    conn = _side_ledger(tmp_path, status=status, term=term, events=events)
    sides = {name: (tone, words) for name, tone, words in pipeline.crawl_sides(conn, tmp_path, SAT)}
    assert sides["校外網站每週重看"][0] == NONE and "沒輪到（每週六凌晨那一晚才看，共 2 站）" in sides["校外網站每週重看"][1]
    assert sides["要用瀏覽器開的網站"][0] == WARN and "這一晚沒開" in sides["要用瀏覽器開的網站"][1]
    tone, words = sides["課表學期"]
    assert tone == WARN and "課表沒走完" in words and "放行 1,500 行、還壓著 3,200 行" in words and "沒再看" in words
    # an error of the browser's run is red, and nothing written yet is no verdict
    bad = [("2026-11-19T18:41:47+00:00", "error", "jsfetch_end", {"end": "error", "detail": "x", "sites": {}})]
    sides = {n: t for n, t, _w in pipeline.crawl_sides(_side_ledger(tmp_path, status=status, term=None, events=bad),
                                                       tmp_path, SAT)}
    assert sides["要用瀏覽器開的網站"] == BAD
    (tmp_path / "reports" / "schedule-term.json").unlink()
    (tmp_path / "reports" / "update-status.json").unlink()
    sides = {n: t for n, t, _w in pipeline.crawl_sides(_side_ledger(tmp_path, status=None, term=None, events=[]),
                                                       tmp_path, SAT)}
    assert set(sides.values()) == {NONE}


def test_the_last_run_is_the_nights_own_and_not_the_browser_sites_after_it(where, ledger):
    events = [("2026-11-16T17:03:39+00:00", "run_start", {"sites": ["school.example", "b.example"]}),
              ("2026-11-16T18:40:56+00:00", "run_end", {"reason": "waiting"}),
              ("2026-11-16T18:41:47+00:00", "jsfetch_start", {"sites": ["js.example"]}),
              ("2026-11-16T18:56:47+00:00", "run_start", {"sites": ["js.example"]}),
              ("2026-11-16T18:57:32+00:00", "run_end", {"reason": "finished"})]
    ledger(_ledger(events, {}, RESULTS))
    step = pipeline.crawl_step(where)
    assert "結束（2 站）" in step.say and step.took == "1 小時 37 分"


def test_what_the_crawl_holds_for_a_look_is_said_cause_by_cause(where, ledger):
    results = [("school.example", "ok", 5), ("school.example", "thin", 3), ("school.example", "too_large", 1)]
    events = [("2026-11-16T06:54:23+00:00", "run_start", {"sites": ["school.example"]}),
              ("2026-11-16T06:55:53+00:00", "run_end", {"reason": "finished"})]
    ledger(_ledger(events, {}, results))
    step = pipeline.crawl_step(where)
    assert ("待確認：頁面幾乎是空的", 3) in step.lost and ("待確認：檔案太大，沒抓", 1) in step.lost
    assert not any(words == "待確認" for words, _n in step.lost)


def test_the_home_draws_a_steps_link_and_its_side_runs():
    from nknu.ops import home_page

    step = {"link": [["", "導出存下檔案的", 3, ""], ["+", "壓縮檔裡拆出來的檔", 2, "各轉<各>的"], ["−", "沒有的", 0, ""],
                     ["=", "轉文字經手的", 5, ""]], "link_left": 0,
            "sides": [["課表學期", "ok", "沒變"], ["要用瀏覽器開的網站", "bad", "出錯 <x>"]]}
    page = home_page.link_html(step)
    assert "對得上" in page and "沒有的" not in page and "data-tip='各轉&lt;各&gt;的'" in page and "class='hm-sum'" in page
    assert "還差 4 對不上" in home_page.link_html({**step, "link_left": -4})
    assert home_page.link_html({}) == "" and home_page.sides_html({}) == ""
    sides = home_page.sides_html(step)
    assert sides.count("<li>") == 2 and sides.count("class='pill") == 1 and "出錯 &lt;x&gt;" in sides
