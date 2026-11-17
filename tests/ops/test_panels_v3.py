"""今晚 and a site's page, panels v3 (spec docs/superpowers/specs/2026-11-17-panels-v3-questions.md 三, 四; nknu.ops.
tonight_page, site_page, charts): each section answers one of the owner's questions in the spec's order, a chart
carries its numbers as JSON and its table twin, ECharts comes from one pinned address, the funnel shows pipeline.py's
numbers, colours come only from theme.css. Made-up steps, sites and nights: no real data."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser
from starlette.testclient import TestClient

from nknu.ops import charts, history, site_page, tonight_page, web
from nknu.ops.pipeline import BAD, CRAWLER, OK, WARN, Step
from nknu.ops.theme import bar
from tests.ops.library import models
from tests.ops.test_nightly import NIGHT, SAID, at, crawler_log, crawler_status, ingest, run
from tests.retrieval.test_search import TODAY

REPO = Path(__file__).resolve().parents[2]


def snap() -> dict:
    steps = [
        Step("crawl", "爬取", "抓", state=OK, into=[("個這一輪該抓的網址（3 站）", 120)], out=[("個抓到了", 100)],
             bar=[100, 120], lost=[("學校那邊已經不存在", 12), ("連接失敗", 8)]),
        Step("export", "導出", "導", state=WARN, fix=CRAWLER, say="有 1 站的新導出用舊資料補的變多了：甲處（交給爬蟲 session）",
             into=[("筆（每筆一個網頁、檔案或圖片）", 200)], out=[("筆存下了檔案", 110)],
             lost=[("要人看", 9), ("學校回答「沒有資料」", 3)]),
        Step("totext", "轉文字", "轉", state=OK, into=[("個檔和網頁", 110)], out=[("個轉成文字", 104)], bar=[104, 110],
             lost=[("讀不出文字", 5), ("照規則跳過", 1)]),
        Step("build", "建庫", "建", state=OK, into=[("筆整理前的網頁、檔案和行事曆（3 站）", 104)],
             out=[("份文件", 70), ("個小段（搜尋時比對的單位）", 600)], bar=[70, 104], share=False,
             lost=[("別處有一模一樣的一份，合併了", 30), ("每週重複的行事曆，合成一個系列", 4)]),
        Step("nightly", "每晚進庫", "進", state=OK)]
    sites = [{"key": "a.example", "name": "甲處", "worst": WARN, "href": "/site?site=a.example",
              "cells": {"refill": {"n": 3, "state": WARN}, "docs": {"n": 40, "state": OK}},
              "why": ["新導出用舊資料補的從 0 筆變成 3 筆（最近抓壞的變多；交給爬蟲 session）"],
              "issues": [{"col": "refill", "state": WARN,
                          "why": "新導出用舊資料補的從 0 筆變成 3 筆（最近抓壞的變多；交給爬蟲 session）"}]}]
    return {"at": "2026-11-17T08:00:00+08:00", "took": "1 秒", "state": WARN, "say": "", "running": False,
            "steps": [asdict(s) for s in steps], "sites": sites}


def tonight(where_root: Path | None = None) -> dict:
    nights = []
    for i in range(7):
        day = date(2026, 11, 10 + i)
        cells = [{"key": k, "name": n, "state": "done", "word": "完成", "sev": "ok", "say": "", "fixer": "",
                  "took": "", "since": f"2026-11-{11 + i}T{h0}:00+08:00", "until": f"2026-11-{11 + i}T{h1}:00+08:00"}
                 for k, n, h0, h1 in (("crawl", "爬取", "01:00", "01:51"), ("export", "導出", "01:51", "02:06"),
                                      ("totext", "轉文字", "02:06", "02:31"), ("build", "建庫", "02:31", "03:05"),
                                      ("swap", "換庫", "03:05", "03:40"))]
        nights.append({"night": day.isoformat(), "date": f"{day:%m-%d}", "label": f"{day:%m-%d} 夜",
                       "state": "ok", "word": "正常", "cells": cells})
    nights[2]["state"], nights[2]["cells"] = "missed", [{**c, "since": "", "until": "", "state": "wait"}
                                                       for c in nights[2]["cells"]]
    nights[4]["state"] = "bad"
    nights[4]["cells"][3].update(state="stop", word="出錯", sev="bad", say="停在第 5 步", fixer="交給內容（建庫）session")
    return {"now": "2026-11-17T09:00:00+08:00", "default": "2026-11-16", "nights": nights, "live": False, "sig": "s",
            "sentence": {"state": "ok", "text": "11-16 夜正常：換了新庫", "fix": ""}}


def hist() -> dict:
    days = history.window(date(2026, 11, 16))
    recs = [None] * 14
    recs[-1] = {"night": days[-1], "counted_by": "changes", "new": 5, "changed": 2, "left": 3, "unexplained": 1,
                "look": 0, "docs": 70, "state": "ok", "word": "正常"}
    recs[-2] = {"night": days[-2], "counted_by": "ingest", "new": 4, "changed": None, "left": 0, "unexplained": 0,
                "look": None, "docs": 68, "state": "ok", "word": "正常"}
    recs[-3] = {"night": days[-3], "counted_by": None, "new": None, "changed": None, "left": None, "unexplained": None,
                "look": None, "docs": None, "state": "bad", "word": "出錯"}
    sites = {days[-1]: [{"site": "b.example", "name": "乙處", "new": 1, "left": 0, "unexplained": 0},
                        {"site": "a.example", "name": "甲處", "new": 4, "left": 3, "unexplained": 1}]}
    return {"enabled": True, "why": "", "days": days, "nights": recs, "sites": sites}


def page() -> str:
    return tonight_page.home_body(snap(), tonight(), None, hist(), {}, "http://127.0.0.1:18879/")


def json_of(html: str, tag_id: str) -> dict:
    m = re.search(rf"<script type='application/json' id='{tag_id}'>(.*?)</script>", html)
    assert m, tag_id
    return json.loads(m.group(1))


# ---------------------------------------------------------------- 今晚


def test_the_home_answers_the_five_questions_in_the_specs_order():
    tree = LexborHTMLParser(page())
    secs = tree.css("section.v3-q")
    assert [s.attributes["data-q"] for s in secs] == ["1", "2", "3", "4", "5"]
    assert [s.attributes["data-question"] for s in secs] == [tonight_page.QUESTIONS[q] for q in range(1, 6)]
    titles = [(s.css_first(".v3-h h2").text() if s.css_first(".v3-h h2") else "") for s in secs]
    assert titles == ["", "每晚各步時間", "每晚新進與離開", "整體流程（累計）", "要處理的網站"]  # plain nouns
    # the old flow map and the night's five cells are gone; the steps' detail is folded at the bottom
    html = page()
    assert "pl-flowmap" not in html and "tn-cell" not in html and html.index("data-q='5'") < html.index("各步驟細節")


def test_the_top_says_one_state_and_lists_each_problem_where_it_is():
    tree = LexborHTMLParser(page())
    state = tree.css_first(".v3-state")
    assert state.attributes["data-state"] == WARN and state.css_first("b").text() == "完成"  # amber: problems
    rows = [li.text() for li in tree.css(".v3-problems li")]
    # 流水線's export step and the site it names: one row each, where · state · what the source says (spec 八.5)
    assert len(rows) == 2 and rows[0].startswith("導出留意有 1 站") and rows[1].startswith("甲處留意新導出")
    assert not any("交給" in r for r in rows)
    calm = tonight_page.home_body({**snap(), "steps": [], "sites": []}, tonight(), None, hist(), {})
    assert "沒有問題" in calm and "data-state='ok'" in calm
    running = tonight()
    running["nights"][6]["cells"][3].update(state="run", word="跑中", sev="")
    assert tonight_page.mark_state(running, []) == "run"
    assert tonight_page.mark_state(running, [{"sev": BAD}]) == BAD


def test_each_chart_carries_its_numbers_as_json_and_shows_its_table_until_echarts_draws():
    html = page()
    tree = LexborHTMLParser(html)
    for fig_id, src in (("fig-timeline", "data-timeline"), ("fig-flow", "data-flow")):
        fig = tree.css_first(f"#{fig_id}")
        assert fig.attributes["data-src"] == src and "v3-astable" in fig.attributes["class"]
        assert fig.css_first(".v3-table table.v3-t") is not None  # the twin, and what shows without ECharts
        button = tree.css_first(f".v3-tog[data-for='{fig_id}']")
        assert button.text() == "看表格" and "hidden" in button.attributes
    timeline = json_of(html, "data-timeline")
    assert [n["title"] for n in timeline["nights"]][:2] == ["11-16 夜", "11-15 夜"] and timeline["max"] == 8
    seg = timeline["nights"][0]["segs"][0]
    assert (seg["name"], seg["from"], seg["to"], seg["tone"]) == ("爬取", 1.0, 1.85, "done")
    assert timeline["nights"][4]["mark"] == "✕ 沒跑" and timeline["nights"][4]["markTone"] == "bad"
    assert {s["tone"] for s in timeline["nights"][2]["segs"]} == {"done", "bad"}  # the state hue only where it failed
    table = tree.css_first("#fig-timeline table").text()
    assert "01:00–01:51（51 分鐘）" in table and "出錯" in table
    flow = json_of(html, "data-flow")
    assert [x["counted"] for x in flow["nights"][-3:]] == [False, True, True]
    assert flow["nights"][-1]["unexplained"] == 1 and flow["nights"][-3]["why"] == "這一晚沒有算進出"
    assert [s["site"] for s in flow["sites"]["2026-11-16"]] == ["a.example", "b.example"]  # the unexplained first
    panel = tree.css_first("[data-night-panel]")
    assert panel.css_first("h3").text() == "11-16 有變動的網站" and "✕ 查不出原因 1" in panel.text()


def test_echarts_comes_from_one_pinned_address_and_draws_only_with_theme_colours():
    html = page()
    srcs = re.findall(r"<script[^>]* src='([^']+)'", html)
    assert srcs == [charts.ECHARTS] and re.search(r"/echarts/\d+\.\d+\.\d+/echarts\.min\.js$", charts.ECHARTS)
    assert "onload='nkCharts.ready()'" in html and "onerror='nkCharts.fail()'" in html
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", charts.JS)  # no colour of its own
    used = set(re.findall(r"V\('(--[a-z0-9-]+)'\)", charts.JS))
    defined = set(re.findall(r"(--[a-z0-9-]+):", bar.theme_css()))
    assert used and used <= defined, used - defined
    assert {"--amt", "--q2", "--line2", "--bad", "--warn"} <= used  # amounts violet, done grey, states on marks
    assert "prefers-color-scheme: dark" in charts.JS and "data-theme" in charts.JS  # drawn again on a theme change


def test_the_funnel_shows_pipelines_own_numbers_and_what_each_step_did_not_put_out():
    s = snap()
    stages = tonight_page.funnel_data(s)
    steps = {x["key"]: x for x in s["steps"]}
    assert [x["name"] for x in stages] == ["爬取", "導出", "轉文字", "建庫"]  # four steps, each on its own terms
    for stage in stages:
        step = steps[stage["key"]]
        assert (stage["into"], stage["value"]) == (step["into"][0][1], step["out"][0][1])
    assert [x["drop"] for x in stages] == [20, 90, 6, 34]  # what each step did not put out: its own in − out
    assert [x["unit"] for x in stages] == ["個網址", "筆", "個檔", "筆"]
    tree = LexborHTMLParser(page())
    rows = tree.css("#fig-funnel .fn-row")
    assert [(r.attributes["data-stage"], r.attributes["data-value"], r.attributes["data-into"]) for r in rows] == [
        ("crawl", "100", "120"), ("export", "110", "200"), ("totext", "104", "110"), ("build", "70", "104")]
    # each row says its own drop, in the row (not between two bars), and opens on its reasons
    drops = [r.css_first(".fn-drop").text() for r in rows]
    assert drops == ["沒抓到 20", "沒存檔 90", "沒轉成文字 6", "沒成為文件 34"]
    assert all(r.tag == "details" for r in rows) and rows[0].css_first(".fn-pct").text() == "83%"
    assert rows[0].css_first(".rs-head").text() == "少 20 個網址，都分了類"
    # a click on a row opens its reasons, each with its count, nobody named to fix it (spec 八.2)
    named = {li.css_first(".rs-w").text(): li.css_first(".rs-n").text() for li in tree.css("#fig-funnel .rs li")}
    assert named["學校那邊已經不存在"] == "12" and named["讀不出文字"] == "5" and not tree.css("#fig-funnel .v3-tag")
    table = tree.css_first("#fig-funnel .v3-table").text()
    assert "120" in table and "100" in table and "20" in table


def test_a_reason_list_shows_eight_and_folds_the_rest_into_other():
    rows = [[f"理由{i}", 20 - i] for i in range(11)]
    html = tonight_page.reason_rows(rows)
    tree = LexborHTMLParser(html)
    names = [li.css_first(".rs-w").text() for li in tree.css("li")]
    assert names == [f"理由{i}" for i in range(8)] + ["其他"]
    other = tree.css("li")[-1]
    assert other.css_first(".rs-n").text() == str(12 + 11 + 10) and "理由10：10" in other.attributes["data-tip"]


def test_the_sites_to_handle_are_at_most_ten_and_link_to_their_page():
    tree = LexborHTMLParser(page())
    rows = tree.css("table.v3-sites tbody tr")
    assert len(rows) == 1 and rows[0].css_first("a").attributes["href"] == "/site?site=a.example"
    cells = [td.text().strip() for td in rows[0].css("td")]
    assert cells[1:] == ["新導出用舊資料補的從 0 筆變成 3 筆（最近抓壞的變多）", "導出"]  # no 交給誰 column


def test_no_record_or_none_yet_is_said_in_place_of_the_bars():
    off = tonight_page.flow_section({"enabled": False, "why": "沒有設定紀錄檔（--ops-db）"}, None, {})
    assert "沒有在記每晚的紀錄：沒有設定紀錄檔（--ops-db）" in off and "data-flow" not in off
    empty = {"enabled": True, "days": history.window(date(2026, 11, 16)), "nights": [None] * 14, "sites": {}}
    assert "還沒有紀錄：第一晚跑完以後就有" in tonight_page.flow_section(empty, None, {})


# ---------------------------------------------------------------- the site's page


def row_and_detail() -> tuple[dict, dict]:
    row = {"key": "a.example", "name": "甲處", "worst": WARN,
           "cells": {"crawl": {"n": 90, "state": OK, "whole": 100,
                               "lost": [["gone", "學校那邊已經不存在", 6], ["failed", "連接失敗", 3], ["login", "要登入", 1]]},
                     "refill": {"n": 2, "state": WARN}},
           "why": ["新導出用舊資料補的從 0 筆變成 2 筆（最近抓壞的變多；交給爬蟲 session）"],
           "issues": [{"col": "refill", "state": WARN,
                       "why": "新導出用舊資料補的從 0 筆變成 2 筆（最近抓壞的變多；交給爬蟲 session）"}]}
    detail = {"site": {"site": "a.example", "name": "甲處", "docs": 50, "deleted_notices": 2},
              "export": {"rows": 120, "files": 95, "missing": {"附件沒抓到": 4, "學校回答「沒有資料」": 2},
                         "missing_examples": {"附件沒抓到": [("https://a.example/f1", "表一")]},
                         "outcomes": {"抓到了": 95, "按规则不抓": 25}, "folder": "a.example-20261117-010000",
                         "exported_at": "2026-11-17T01:00:00+08:00", "paging": 0, "refill": {}, "refills": [],
                         "examples": {}},
              "text": {"status": {"ok": 88, "empty": 5, "skipped": 2},
                       "bad": [("empty", "OCR 沒認出字", "files/x.png")]},
              "copies": {"merged": 7}, "errors": [], "flags": []}
    return row, detail


def test_a_sites_funnel_and_reasons_are_its_own_and_say_who_can_fix_each():
    row, detail = row_and_detail()
    stages = site_page.site_stages(row, detail)
    assert [(s["value"], s["into"], s["drop"]) for s in stages] == [(90, 100, 10), (95, 120, 25), (88, 95, 7),
                                                                    (50, None, None)]
    bars, table, count = site_page.reasons_html(stages)
    tree = LexborHTMLParser(bars)
    reasons = tree.css("ul.rsd > li")
    first = [li.css_first(".rs-w").text() for li in reasons]
    # grouped by step (each under what it adds up to), the largest of the step first
    assert count == 9 and first[:3] == ["學校那邊已經不存在", "連接失敗", "要登入"]
    assert [g.attributes["data-stage"] for g in tree.css(".rs-group")] == ["crawl", "export", "totext", "build"]
    assert not tree.css(".v3-tag")  # no who-can-fix-it tags (spec 八.2)
    # opening a reason lists its items: the export's examples open 網頁核查 on that page
    att = next(li for li in reasons if li.css_first(".rs-w").text().startswith("附件沒抓到"))
    assert att.css_first(".rs-items a").attributes["href"] == "/check?ref=https%3A%2F%2Fa.example%2Ff1"
    assert "誰能補" not in table and "學校那邊已經不存在" in table


def test_a_sites_page_has_the_five_sections_its_charts_and_its_nights():
    row, detail = row_and_detail()
    one = {"enabled": True, "days": history.window(date(2026, 11, 16)),
           "nights": [None] * 12 + [{"night": "2026-11-15", "counted": True, "new": 2, "left": 1, "unexplained": 0,
                                    "docs": 49}, {"night": "2026-11-16", "counted": True, "new": 0, "left": 0,
                                                  "unexplained": 0, "docs": 50}]}
    html = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=site_page.now_line(row, None, {}, read=True), detail=detail,
        changes={"state": "none", "words": "沒有前一份庫可以比"}, changes_title="上次換庫變了什麼", view=None,
        archive=False, kinds={"notice": [30, 3], "attachment": [20, 7]}, tables="", row=row,
        hist=one, labels={"2026-11-16": "11-16 夜"})
    tree = LexborHTMLParser(html)
    assert tree.css_first("header.st-head").attributes["data-q"] == "1"
    assert [s.attributes["data-q"] for s in tree.css("section.st-q")] == ["2", "3", "4", "5"]
    now = tree.css_first(".st-now")
    assert not now.css(".v3-who") and "交給" not in now.text()
    nights = json_of(html, "data-site-nights")
    assert [x["counted"] for x in nights["nights"][-2:]] == [True, True] and nights["nights"][-1]["label"] == "夜"
    kinds = tree.css_first("#fig-site-kinds")
    assert kinds.css_first(".st-kbar").css("i")[0].attributes["style"].startswith("flex-grow:30;background:var(--k0)")
    assert "<b>10</b> 份" in html and "20%" in html  # 沒日期 of 公告、網頁、附件
    off = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=(OK, "每一步都正常"), detail=detail,
        changes={"state": "none", "words": ""}, changes_title="", view=None, archive=False, kinds={}, tables="",
        row=row, hist={"enabled": False, "why": "沒有設定紀錄檔（--ops-db）"})
    assert "沒有在記每晚的紀錄" in off and "data-site-nights" not in off


# ---------------------------------------------------------------- the app


def test_the_app_keeps_the_record_where_it_is_told_and_says_when_it_cannot(db, tmp_path, tmp_path_factory):
    where_db = tmp_path / "t.db"
    ops = tmp_path_factory.mktemp("ops") / "ops.db"
    app = web.create_app(where_db, tmp_path, models=models(), today_fn=lambda: TODAY, ops_db=ops, record=False)
    assert app.state.store.enabled and ops.exists()
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        got = client.get("/api/history").json()
        home = client.get("/").text
    assert got["enabled"] and len(got["days"]) == 14 and got["file"] == str(ops)
    assert "還沒有紀錄：第一晚跑完以後就有" in home
    inside = web.create_app(where_db, tmp_path, models=models(), today_fn=lambda: TODAY, ops_db=tmp_path / "o.db",
                            record=False)
    assert not inside.state.store.enabled and not (tmp_path / "o.db").exists()  # never under --root (/work)
    with TestClient(inside, base_url="http://127.0.0.1:8878") as client:
        assert "紀錄檔不能放在資料或爬蟲存檔的資料夾裡" in client.get("/").text


def test_the_recorder_writes_a_finished_night_the_home_then_draws(db, tmp_path, tmp_path_factory):
    from nknu.ops.pipeline import PIPE
    from tests.ops.test_nightly import library

    root = tmp_path_factory.mktemp("root")
    lib = library(root / PIPE / "aca.db", docs=[("d1", "notice", "甲", "https://a.example/1", "內文", None, "a.example")],
                  sites=[("a.example", "甲處", 1, "exp/a")])
    (root / "crawl").mkdir()
    app = web.create_app(lib, root, models=models(), today_fn=lambda: TODAY, archive=root / "crawl",
                         ops_db=tmp_path_factory.mktemp("ops") / "ops.db", record=False)
    where = app.state.recorder.where
    crawler_log(where, NIGHT, at("2026-11-17T02:06:00"))
    crawler_status(where)
    ingest(where, [run("nothing_new", step="plan", finished="2026-11-17T02:06:40+08:00")], SAID[:1])
    assert app.state.recorder.tick(now=at("2026-11-17T09:00:00")) == 1
    got = app.state.store.get("2026-11-16")
    assert got["state"] == OK and got["new"] == 0 and got["counted_by"] == "ingest"


def test_the_headline_is_one_template_whatever_the_night():
    # spec 八.4: 「{哪一晚}：{最後的狀態}；…；有 P 個問題」, never 正常 beside a mark that is not
    tree = LexborHTMLParser(tonight_page.home_body(snap(), tonight(), None, hist(), {}))
    assert tree.css_first(".v3-state b").text() == "完成" and tree.css_first(".v3-say").text() == "11-16 夜：完成；有 2 個問題"
    calm = LexborHTMLParser(tonight_page.home_body({**snap(), "steps": [], "sites": []}, tonight(), None, hist(), {}))
    assert calm.css_first(".v3-state b").text() == "完成" and calm.css_first(".v3-say").text() == "11-16 夜：完成；沒有問題"
    running = tonight()
    running["nights"][6]["cells"][3].update(state="run", word="跑中", sev="")
    said = LexborHTMLParser(tonight_page.home_body({"steps": []}, running, None, hist(), {})).css_first(".v3-say")
    assert said.text() == "11-16 夜：跑中（建庫）；沒有問題"
    changes = {"state": "ready", "night": "2026-11-16", "totals": {"new": 48, "left": 20, "unexplained": 0}}
    counted = LexborHTMLParser(tonight_page.home_body({"steps": []}, tonight(), changes, hist(), {}))
    assert counted.css_first(".v3-say").text() == "11-16 夜：完成；新進 48 份、離開 20 份（說不出原因 0 份）；沒有問題"
    # a site's line: a mark that is not 正常 never sits beside 「每一步都正常」
    state, said = site_page.now_line({"worst": WARN, "why": []}, None, {}, read=True)
    assert state == WARN and "正常" not in said
