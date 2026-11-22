"""Panels made for the long run (spec docs/superpowers/specs/2026-11-17-panels-v3-questions.md 八; owner 11-17:
「為什麼要把交給爬蟲session 這種東西放進去？」「你覺得每晚遇到的問題你窮舉的完 然後放在dashboard裡面嗎？」「那個不叫"搜索試用"，叫
搜索就好」): no who-fixes-it on any page, one fixed headline, a generic list of problems with each source's own reason,
an unknown code shown as it is and never as normal, every run of a night drawn as runs, and the 搜尋 page built from
whatever tools the service lists. Made-up status files and a made-up service only."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from selectolax.lexbor import LexborHTMLParser
from starlette.datastructures import QueryParams

from nknu.ops import home_page, nightly, panorama, site_page, todo, tonight_page
from nknu.ops.pipeline import PIPE, Where
from nknu.ops.try_page import TryPage
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, library, run
from tests.ops.test_panels_v3 import hist, row_and_detail, snap

RO = {"readOnlyHint": True}
ASK = {"name": "ask", "description": "問一個問題。", "annotations": RO, "inputSchema": {
    "type": "object", "required": ["words"], "properties": {
        "words": {"type": "string", "description": "要問的話"},
        "shape": {"type": "string", "enum": ["thin", "wide"], "default": "wide",
                  "description": "wide（預設）：每筆給長一點。thin：每筆給短一點。"},
        "count": {"type": "integer", "minimum": 2, "maximum": 9, "default": 4, "description": "幾筆"},
        "office": {"type": "string", "default": "", "description": "只看某個單位，例如「甲處」「乙學系」"}}}}
READ = {"name": "readone", "description": "讀一份。", "annotations": RO, "inputSchema": {
    "type": "object", "required": ["key"], "properties": {"key": {"type": "string", "description": "結果的 key"},
                                                          "bit": {"type": "string", "default": "",
                                                                  "description": "留空讀全文；填 toc 只看目錄"}}}}
HELLO = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "made-up"},
         "instructions": "開場說明。\n\n收錄的單位：\n行政：甲處（一組、二組）、丙處、丁中心\n系所：乙學系、戊研究所\n\n查詢：用繁體中文。"}
ANSWER = {"found": True, "results": [{"rank": 1, "key": "k1", "title": "第一則", "content": "內文"}]}


class _Mcp(BaseHTTPRequestHandler):
    tools: ClassVar[list] = [ASK, READ]

    def log_message(self, *_args):
        pass

    def do_POST(self):
        sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in sent:
            self.send_response(202)
            self.end_headers()
            return
        result: object = (HELLO if sent["method"] == "initialize" else {"tools": type(self).tools}
                          if sent["method"] == "tools/list" else
                          {"content": [{"type": "text", "text": json.dumps(ANSWER, ensure_ascii=False)}]})
        data = json.dumps({"jsonrpc": "2.0", "id": sent["id"], "result": result}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def mcp():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Mcp)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def where(tmp_path) -> Where:
    db = library(tmp_path / PIPE / "aca.db")
    (tmp_path / "crawl").mkdir()
    return Where(root=tmp_path, db=db, archive=tmp_path / "crawl")


def todo_rows(t: dict) -> list:
    """總覽's 要處理 rows of the night once the steps are read (the problems' list moved there from 每晚運行)."""
    got = todo.collect(None, tonight_page.problems(t, {"steps": []}, []), [], None, {})
    return LexborHTMLParser(panorama.todo_card(got, False)).css(".pn-tlist li")


def test_no_page_says_who_should_fix_anything(where, mcp):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("failed", step="build", error="build_exit_1")], SAID[:4],
           build="2026-11-17 03:00:00 failed step 5 (exit 1)")
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    home = home_page.home_body(snap(), t, None, hist(), {})
    row, detail = row_and_detail()
    site = site_page.site_body("a.example", name="甲處", unit="", home="", now=site_page.now_line(row, None, {}, read=True),
                               detail=detail, changes={"state": "none", "words": ""}, changes_title="", view=None,
                               archive=False, kinds={}, tables="", row=row, hist={"enabled": False, "why": "x"})
    _title, search = TryPage(mcp).page(QueryParams(""))
    # 總覽's 要處理, where the problems are listed since 2026-11-22 evening (snap's reasons name a fixer)
    rows = tonight_page.problems(t, snap(), tonight_page.handle_rows(snap(), None))
    front = panorama.todo_card(todo.collect(None, rows, [], None, {}), False)
    assert "甲處" in front and "導出" in front
    for page in (home, site, search, front):
        assert "交給" not in page and "session" not in page.lower()
        for gone in ("爬蟲可補", "建庫可補", "排程那次"):
            assert gone not in page


def test_the_headline_is_one_template_and_the_problems_carry_each_sources_own_reason(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("failed", step="build", error="build_exit_1")], SAID[:4],
           build="2026-11-17 03:00:00 failed step 5 (exit 1)")
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    html = home_page.home_body({"steps": []}, t, None, {"enabled": False, "why": ""}, {})
    tree = LexborHTMLParser(html)
    assert tree.css_first(".v3-say").text() == "11-16 夜：出錯（建庫）；有 1 個問題"
    rows = [li.text() for li in todo_rows(t)]  # listed on 總覽's 要處理 since 2026-11-22 evening
    assert len(rows) == 1 and "建庫" in rows[0] and "出錯" in rows[0] and "build_exit_1" in rows[0]


def test_an_unknown_code_is_shown_as_it_is_and_lights_the_night(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("exploded", error="mystery_code_7")], SAID)
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    assert t["shown"]["state"] != "ok"
    html = home_page.home_body({"steps": []}, t, None, {"enabled": False, "why": ""}, {})
    tree = LexborHTMLParser(html)
    assert tree.css_first(".v3-state").attributes["data-state"] in ("warn", "bad")
    rows = todo_rows(t)  # listed on 總覽's 要處理 since 2026-11-22 evening
    text = " ".join(li.text() for li in rows)
    assert "exploded" in text and "mystery_code_7" in text and "沒見過" in text
    assert {li.attributes["data-sev"] for li in rows} <= {"warn", "bad"}  # never drawn as normal


def test_a_night_with_three_attempts_draws_three_runs_and_says_nothing_special(where):
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    paused = run("paused", step="convert", started="2026-11-17T02:10:00+08:00", finished="2026-11-17T02:20:00+08:00")
    failed = run("failed", step="build", error="build_exit_2", started="2026-11-17T02:30:00+08:00",
                 finished="2026-11-17T02:50:00+08:00")
    swapped = run("swapped", started="2026-11-17T03:51:00+08:00", finished="2026-11-17T04:30:00+08:00")
    ingest(where, [paused, failed, swapped], [("2026-11-17 03:51:01", "步骤 repoint"),
                                              ("2026-11-17 03:55:00", "步骤 keep"), ("2026-11-17 04:29:00", "步骤 done")])
    t = nightly.tonight(where, now=at("2026-11-17T09:00:00"))
    data = tonight_page.timeline_data(t, at("2026-11-17T09:00:00"))
    segs = data["nights"][0]["segs"]
    assert sorted({s["run"] for s in segs if s.get("run")}) == [1, 2, 3]
    states = {s["run"]: s["tone"] for s in segs if s.get("run") in (1, 2)}
    assert states == {1: "warn", 2: "bad"}
    page = json.dumps(data, ensure_ascii=False) + home_page.home_body({"steps": []}, t, None, {"enabled": False}, {})
    assert "排程" not in page and "手動" not in page and "補上" not in page


def test_the_search_page_builds_its_controls_from_whatever_tools_are_listed(mcp):
    title, body = TryPage(mcp).page(QueryParams(""))
    assert title == "搜尋"
    tree = LexborHTMLParser(body)
    form = tree.css_first("form.sr-form")
    assert form.attributes["data-tool"] == "ask"
    assert form.css_first("input.sr-q").attributes["name"] == "a.words"  # the big box: the first required text
    seg = [(i.attributes["value"], "checked" in i.attributes) for i in form.css(".sr-seg input[name='a.shape']")]
    assert seg == [("thin", False), ("wide", True)]  # an enum is buttons, its default picked
    tips = [lab.attributes.get("data-tip") for lab in form.css(".sr-seg label")]
    assert tips == ["thin：每筆給短一點。", "wide（預設）：每筆給長一點。"]
    num = form.css_first("input[name='a.count']")
    assert (num.attributes["type"], num.attributes["min"], num.attributes["max"]) == ("number", "2", "9")
    office = form.css_first("input[name='a.office']")
    options = [o.attributes["value"] for o in tree.css(f"datalist#{office.attributes['list']} option")]
    assert {"甲處", "丙處", "丁中心", "乙學系", "戊研究所", "一組"} <= set(options)
    assert not tree.css("form[data-tool='readone']")  # the tool that reads one result is reached from a result
    _title, called = TryPage(mcp).page(QueryParams("call=ask&a.words=x&a.shape=wide&a.count=4"))
    card = LexborHTMLParser(called).css_first(".tr-card")
    hrefs = [a.attributes["href"] for a in card.css(".tr-acts a")]
    assert "/try?call=readone&a.key=k1#result" in hrefs and "/try?call=readone&a.key=k1&a.bit=toc#result" in hrefs
