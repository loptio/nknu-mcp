"""每晚運行 (nknu.ops.home_page, rebuilt 2026-11-18: the state now, the pipeline's seven nodes with a picked one's
detail beside them, what is not normal, a row a night, the last swap's changes) and a site's page (panels v3: docs/
superpowers/specs/2026-11-17-panels-v3-questions.md 四; nknu.ops.site_page, tonight_page, charts): the funnel shows
pipeline.py's numbers, ECharts comes from one pinned address, colours come only from theme.css. Made-up steps, sites
and nights: no real data."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser
from starlette.testclient import TestClient

from nknu.ops import charts, history, home_page, panorama, site_page, todo, tonight_page, web
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
    return home_page.home_body(snap(), tonight(), None, hist(), {}, "http://127.0.0.1:18879/")


def json_of(html: str, tag_id: str) -> dict:
    m = re.search(rf"<script type='application/json' id='{tag_id}'>(.*?)</script>", html)
    assert m, tag_id
    return json.loads(m.group(1))


# ---------------------------------------------------------------- 每晚運行 (nknu.ops.home_page, 2026-11-18)

CHANGES = {"state": "ready", "night": "2026-11-16", "totals": {"new": 5, "changed": 2, "left": 3, "unexplained": 1, "look": 0},
           "prev": {"swapped_at": "2026-11-16T04:05:00+08:00", "docs": 68},
           "new": {"swapped_at": "2026-11-17T03:05:00+08:00", "docs": 70},
           "sites": [{"site": "b.example", "name": "乙處", "new": 1, "changed": 0, "left": 0, "unexplained": 0, "look": 0},
                     {"site": "a.example", "name": "甲處", "new": 4, "changed": 2, "left": 3, "unexplained": 1, "look": 0}]}


def test_the_home_answers_the_five_questions_in_order_without_a_chart_library():
    html = page()
    tree = LexborHTMLParser(html)
    secs = tree.css("section.v3-q")
    # the third question (有什麼不正常的？) is answered on 總覽's 要處理 since 2026-11-22 evening
    assert [s.attributes["data-q"] for s in secs] == ["1", "2", "4", "5"]
    assert [s.attributes["data-question"] for s in secs] == [home_page.QUESTIONS[q] for q in (1, 2, 4, 5)]
    titles = [(s.css_first(".v3-h h2").text() if s.css_first(".v3-h h2") else "") for s in secs]
    assert titles == ["", "流程", "每晚紀錄", "上次換庫的變動"]  # plain nouns
    assert tree.css_first("#problems") is None
    assert not re.findall(r"<script[^>]* src=", html)  # nothing fetched from outside: the page draws with HTML alone
    assert "pl-flowmap" not in html and "v3-sites" not in html and "各步驟細節" not in html


def test_the_top_says_one_state_the_library_students_search_and_the_pause():
    tree = LexborHTMLParser(page())
    state = tree.css_first(".v3-state")
    assert state.attributes["data-state"] == WARN and state.css_first("b").text() == "完成"  # amber: problems
    assert tree.css_first(".hm-fresh") is None and tree.css_first(".hm-paused") is None  # nothing known, nothing said
    s = snap()
    s["steps"][-1]["paused"] = True
    top = LexborHTMLParser(home_page.home_body(s, tonight(), CHANGES, hist(), {}, now=at("2026-11-17T09:00:00")))
    fresh = top.css_first(".hm-fresh")
    assert fresh.css_first("b").text() == "11-17 03:05" and "70 份文件" in fresh.text()
    age = fresh.css_first(".hm-age")
    assert age.text() == "6 小時前" and "data-tone" not in age.attributes  # a night old: not said in a state's colour
    late = LexborHTMLParser(home_page.home_body(s, tonight(), CHANGES, hist(), {}, now=at("2026-11-18T12:00:00")))
    assert late.css_first(".hm-age").attributes["data-tone"] == "warn" and late.css_first(".hm-age").text() == "33 小時前"
    assert "進庫暫停中" in top.css_first(".hm-paused").text()
    calm = LexborHTMLParser(home_page.home_body({**snap(), "steps": [snap()["steps"][0]], "sites": []}, tonight(), None,
                                                hist(), {}))
    assert "沒有問題" in calm.css_first(".v3-say").text() and calm.css_first(".v3-state").attributes["data-state"] == "ok"
    # while the steps are still being read the top is not green and does not say 沒有問題: nothing is known yet
    unread = LexborHTMLParser(home_page.home_body({"steps": [], "running": True}, tonight(), None, hist(), {}))
    assert unread.css_first(".v3-state").attributes["data-state"] == "none"
    assert unread.css_first(".v3-say").text() == "11-16 夜：完成；正在讀每一步的紀錄"
    running = tonight()
    running["nights"][6]["cells"][3].update(state="run", word="跑中", sev="")
    assert tonight_page.mark_state(running, []) == "run"
    assert tonight_page.mark_state(running, [{"sev": BAD}]) == BAD


def test_a_part_that_runs_but_went_silent_is_named_on_top():
    t = tonight()
    t["nights"][6]["cells"][0].update(state="run", word="可能卡住", sev="warn", say="03:03 以後沒有動靜", until="",
                                      reason="update.log 03:03 以後沒有動靜")
    for c in t["nights"][6]["cells"][1:]:
        c.update(state="wait", word="還沒開始", since="", until="")
    tree = LexborHTMLParser(home_page.home_body({"steps": []}, t, None, hist(), {}))
    assert tree.css_first(".v3-state b").text() == "可能卡住"
    assert tree.css_first(".v3-say").text() == "11-16 夜：爬取可能卡住（03:03 以後沒有動靜）；有 1 個問題"
    node = tree.css_first(".hm-node")
    assert node.attributes["data-tone"] == "warn" and node.css_first(".hm-line").text() == "03:03 以後沒有動靜"
    assert node.css_first(".hm-line").attributes["data-tone"] == "warn"
    waiting = tree.css(".hm-node")[1].css_first(".hm-line")  # a step not begun is said in grey, whatever its node's mark
    assert waiting.text() == "還沒開始" and waiting.attributes["data-tone"] == ""


def test_the_pipeline_is_seven_nodes_and_a_picked_one_shows_its_in_out_and_reasons_beside_them():
    tree = LexborHTMLParser(home_page.home_body(snap(), tonight(), CHANGES, hist(), {"crawl": "http://c.example/progress"},
                                                "http://127.0.0.1:18879/"))
    nodes = tree.css(".hm-nodes .hm-node")
    assert [x.css_first(".hm-nm b").text() for x in nodes] == ["爬取", "導出", "轉文字", "建庫", "品質檢查", "換庫", "查詢服務"]
    assert [x.css_first("a").attributes["data-pick"] for x in nodes][:2] == ["step-crawl", "step-export"]
    assert [x.attributes["data-tone"] for x in nodes][:4] == ["ok", "warn", "ok", "ok"]
    assert nodes[0].css_first(".hm-line").text() == "01:00–01:51"  # its part of the latest night: the hours it ran
    swap = nodes[5]  # 換庫 holds when the library in use was swapped in
    assert swap.css_first(".hm-fig").text() == "03:05" and swap.css_first(".hm-unit").text() == "11-17 換上"
    assert nodes[6].attributes["data-tone"] == "none" and nodes[6].css_first(".hm-fig").text() == "—"  # no probe read
    # the side: one panel a node and one a night, the first to show is the node to look at
    home = tree.css_first("[data-home]")
    assert home.attributes["data-first"] == "step-export"
    panels = {p.attributes["data-panel"]: p for p in tree.css(".hm-side .hm-panel")}
    assert {f"step-{k}" for k, _n in home_page.NODES} <= set(panels) and "night-2026-11-16" in panels
    crawl = panels["step-crawl"]
    assert [dd.text() for dd in crawl.css(".hm-io dd")] == ["120 個這一輪該抓的網址（3 站）", "100 個抓到了"]
    parts = crawl.css(".hm-split i")  # what came out, then each reason of what did not: the step's own numbers add up
    assert [p.attributes["style"] for p in parts] == ["flex:100 0 0", "flex:12 0 0", "flex:8 0 0"]
    assert parts[1].attributes["data-tip"] == "學校那邊已經不存在：12"
    named = {li.css_first(".rs-w").text(): li.css_first(".rs-n").text() for li in crawl.css(".rs li")}
    assert named == {"學校那邊已經不存在": "12", "連接失敗": "8"}
    export = panels["step-export"]  # its reasons are marks on rows, not a split of what went in: no bar, no percentage
    assert not export.css(".hm-split") and "%" not in export.text() and "交給" not in export.text()
    assert export.css_first(".pill").text() == "留意" and export.css_first(".hm-said").text().startswith("有 1 站")
    build = panels["step-build"]
    assert [p.attributes["style"] for p in build.css(".hm-split i")] == ["flex:70 0 0", "flex:30 0 0", "flex:4 0 0"]
    many = snap()
    many["steps"][0].update(bar=[100, 120], lost=[(f"理由{i}", c) for i, c in enumerate((6, 5, 3, 2, 1, 1, 1, 1))])
    parts = LexborHTMLParser(home_page.split_bar(many["steps"][0])).css("i")  # the small reasons as one part
    assert len(parts) == 1 + home_page.SPLIT_PARTS and parts[-1].attributes["data-tip"] == "其他 3 種：3"
    no_swap = LexborHTMLParser(home_page.home_body(snap(), tonight(), None, hist(), {})).css(".hm-node")[5]
    assert no_swap.css_first(".hm-fig").text() == "—"  # nothing known of the swap: no figure, never the last run's words


def front_todo(s: dict, t: dict | None = None) -> LexborHTMLParser:
    """總覽's 要處理 of a reading (the problems' list moved there from 每晚運行 on 2026-11-22 evening)."""
    t = t or tonight()
    rows = tonight_page.problems(t, s, [] if not s.get("steps") else tonight_page.handle_rows(s, None))
    return LexborHTMLParser(panorama.todo_card(todo.collect(None, rows, [], {}), not s.get("steps")))


def test_every_problem_is_listed_once_where_it_is():
    tree = front_todo(snap())
    items = tree.css("#todo > .pn-tlist li")
    rows = [li.css_first(".pn-twhat").text() for li in items]
    # 流水線's export step and the site it names: one row each, where · what the source says (spec 八.5), on 總覽
    assert len(rows) == 2 and rows[0].startswith("導出有 1 站") and rows[1].startswith("甲處新導出")
    assert [li.attributes["data-sev"] for li in items] == [WARN, WARN]
    assert not any("交給" in r for r in rows) and tree.css_first("#todo .pn-tcount").text() == "2"
    step, site = (li.css_first("a") for li in items)
    assert site.attributes["href"] == "/site?site=a.example" and site.text() == "甲處"
    assert step.attributes["href"] == "/tonight#step-export"  # a step opens its panel on 每晚運行
    # 每晚運行 counts them in its sentence and sends there
    say = LexborHTMLParser(page()).css_first(".v3-say")
    assert say.css_first("a").attributes["href"] == "/#todo" and say.text().endswith("有 2 個問題 列在總覽的要處理")
    calm = front_todo({**snap(), "steps": [], "sites": []})
    assert calm.css_first(".pn-tnone") is None and "正在讀各步驟的紀錄" in calm.css_first("#todo").text()
    none = front_todo({**snap(), "steps": [snap()["steps"][0]], "sites": []})
    assert "沒有要處理的" in none.css_first("#todo .pn-tnone").text() and none.css_first("#todo .pn-tcount") is None
    assert LexborHTMLParser(home_page.home_body({**snap(), "steps": [snap()["steps"][0]], "sites": []}, tonight(), None,
                                                hist(), {})).css_first(".v3-say a") is None  # nothing to send to


def test_a_row_a_night_shows_each_steps_time_and_what_came_in_and_left():
    tree = LexborHTMLParser(page())
    rows = tree.css("table.hm-nights tbody tr")
    assert [r.css_first("th a").text() for r in rows][:2] == ["11-16 夜", "11-15 夜"]
    assert [th.text() for th in tree.css("table.hm-nights thead th")] == [
        "夜", "爬取", "導出", "轉文字", "建庫", "換庫", "新進", "離開", "說不出原因"]
    first = rows[0]
    assert first.attributes["data-row"] == "night-2026-11-16" and first.css_first("th a").attributes["data-pick"] == "night-2026-11-16"
    cells = first.css("td")
    assert cells[0].css_first(".hm-dl").text() == "51 分" and cells[0].attributes["data-tone"] == "done"
    assert "爬取 01:00–01:51" in cells[0].attributes["data-tip"]
    assert [td.text() for td in cells[5:]] == ["5", "3", "✕ 1"]  # the record's counts of that night
    assert [td.text() for td in rows[1].css("td")[5:]] == ["4", "0", "0"]
    assert [td.text() for td in rows[2].css("td")[5:]] == ["—", "—", "—"]  # a night that was not counted
    bad = rows[2].css("td")[3]  # the state's colour and word only where it failed
    assert bad.attributes["data-tone"] == "bad" and bad.css_first(".hm-dl").text().startswith("出錯")
    assert {td.attributes["data-tone"] for td in rows[2].css("td[data-tone]")} == {"done", "bad"}
    missed = rows[4]
    assert missed.css_first("td").attributes["colspan"] == "5" and missed.css_first("td").text() == "✕ 沒跑"
    # one scale for every bar: the longest step of the table is the widest
    widths = [float(re.search(r"width:([\d.]+)%", i.attributes["style"]).group(1)) for i in first.css(".hm-tr i")]
    assert max(widths) == 100.0 and widths[0] == 100.0 and widths[1] < widths[0]
    # the night's own panel: each part's hours, the counts, its sites (what left for no reason first)
    panel = tree.css_first("[data-panel='night-2026-11-16']")
    assert panel.css_first("h3").text() == "11-16 夜" and "01:00–01:51（51 分鐘）" in panel.css_first(".hm-parts").text()
    assert [a.text() for a in panel.css(".hm-nsites a")] == ["甲處", "乙處"] and "✕ 1" in panel.css_first(".hm-nsites li").text()
    assert [dt.text() for dt in panel.css(".hm-counts dt")] == ["新進", "改了", "離開", "說不出原因", "庫裡文件"]


def test_the_last_swap_is_three_figures_and_its_sites_the_unexplained_first():
    tree = LexborHTMLParser(home_page.home_body(snap(), tonight(), CHANGES, hist(), {}))
    sec = tree.css_first("#changes")
    assert sec.css_first(".v3-h .faint").text() == "11-16 04:05 的庫 → 11-17 03:05 的庫"
    assert [(f.css_first("span").text(), f.css_first("b").text()) for f in sec.css(".hm-figure")] == [
        ("新進", "5"), ("改了", "2"), ("離開", "3"), ("說不出原因", "1")]
    rows = sec.css("table.hm-t tbody tr")
    assert [r.css_first("a").text() for r in rows] == ["甲處", "乙處"]
    assert [td.text() for td in rows[0].css("td")[1:]] == ["4", "2", "3", "✕ 1"]
    assert rows[0].css_first("a").attributes["href"] == "/site?site=a.example"
    # the night these changes are of lists its sites here, not again in its side panel (2026-11-22 evening)
    night = tree.css_first("[data-panel='night-2026-11-16']")
    assert night is not None and night.css_first(".hm-nsites") is None and night.css_first(".hm-counts") is not None
    assert LexborHTMLParser(page()).css_first("[data-panel='night-2026-11-16'] .hm-nsites") is not None  # none below
    quiet = {**CHANGES, "totals": {**CHANGES["totals"], "unexplained": 0}}
    assert [th.text() for th in LexborHTMLParser(home_page.home_body(snap(), tonight(), quiet, hist(), {})).css(
        "#changes table.hm-t thead th")] == ["網站", "新進", "改了", "離開"]  # no column of zeros
    none = home_page.changes_section({"state": "none", "words": "沒有前一份庫可以比"}, tonight())
    assert "沒有前一份庫可以比" in none and "hm-figure" not in none
    assert "data-computing" in home_page.changes_section({"state": "computing", "words": "計算中"}, tonight())


def test_echarts_comes_from_one_pinned_address_and_draws_only_with_theme_colours():
    srcs = re.findall(r"<script[^>]* src='([^']+)'", charts.scripts())  # a site's page draws its nights with it
    assert srcs == [charts.ECHARTS] and re.search(r"/echarts/\d+\.\d+\.\d+/echarts\.min\.js$", charts.ECHARTS)
    assert "onload='nkCharts.ready()'" in charts.scripts() and "onerror='nkCharts.fail()'" in charts.scripts()
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
    tree = LexborHTMLParser(tonight_page.funnel_html(stages))  # a site's page draws these rows
    rows = tree.css(".fn-row")
    assert [(r.attributes["data-stage"], r.attributes["data-value"], r.attributes["data-into"]) for r in rows] == [
        ("crawl", "100", "120"), ("export", "110", "200"), ("totext", "104", "110"), ("build", "70", "104")]
    # each row says its own drop, in the row (not between two bars), and opens on its reasons
    drops = [r.css_first(".fn-drop").text() for r in rows]
    assert drops == ["沒抓到 20", "沒存檔 90", "沒轉成文字 6", "沒成為文件 34"]
    assert all(r.tag == "details" for r in rows) and rows[0].css_first(".fn-pct").text() == "83%"
    assert rows[0].css_first(".rs-head").text() == "少 20 個網址，都分了類"
    named = {li.css_first(".rs-w").text(): li.css_first(".rs-n").text() for li in tree.css(".rs li")}
    assert named["學校那邊已經不存在"] == "12" and named["讀不出文字"] == "5" and not tree.css(".v3-tag")
    table = tonight_page.funnel_table(stages)
    assert "120" in table and "100" in table and "20" in table


def test_a_reason_list_shows_eight_and_folds_the_rest_into_other():
    rows = [[f"理由{i}", 20 - i] for i in range(11)]
    html = tonight_page.reason_rows(rows)
    tree = LexborHTMLParser(html)
    names = [li.css_first(".rs-w").text() for li in tree.css("li")]
    assert names == [f"理由{i}" for i in range(8)] + ["其他"]
    other = tree.css("li")[-1]
    assert other.css_first(".rs-n").text() == str(12 + 11 + 10) and "理由10：10" in other.attributes["data-tip"]


def test_no_record_or_none_yet_is_said_under_the_nights():
    off = home_page.home_body(snap(), tonight(), None, {"enabled": False, "why": "沒有設定紀錄檔（--ops-db）"}, {})
    note = LexborHTMLParser(off).css_first("#nights .hm-note")
    assert "沒有在記每晚的紀錄：沒有設定紀錄檔（--ops-db）" in note.text()
    assert len(LexborHTMLParser(off).css("table.hm-nights tbody tr")) == 7  # the nights the chain ran still show
    empty = {"enabled": True, "days": history.window(date(2026, 11, 16)), "nights": [None] * 14, "sites": {}}
    nothing = home_page.home_body({"steps": []}, {"now": "2026-11-17T09:00:00+08:00", "default": "2026-11-16", "nights": []},
                                  None, empty, {})
    assert "還沒有紀錄：第一晚跑完以後就有" in nothing and "hm-note" not in nothing.split("</style>", 1)[1]


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
    nodes = site_page.site_nodes(stages, row, detail)
    assert [(x["id"], x["tone"], x["figure"], x["unit"]) for x in nodes] == [
        ("step-crawl", "ok", "90", "個抓到了"), ("step-export", "warn", "95", "筆存下了檔案"),
        ("step-totext", "none", "88", "個轉成文字"), ("step-build", "none", "50", "份文件")]
    assert site_page.first_pick(nodes) == "step-export"  # the step to look at shows first
    panels = [LexborHTMLParser(site_page.step_panel(x, detail)) for x in nodes]
    crawl, export = panels[0], panels[1]
    # a step's reasons the largest first, on the step's own scale; the split bar only where out + reasons = in
    assert [r.text() for r in crawl.css(".rs-w")] == ["學校那邊已經不存在", "連接失敗", "要登入"]
    assert len(crawl.css(".hm-split i")) == 4 and not export.css(".hm-split") and len(panels[2].css(".hm-split i")) == 3
    assert [d.text() for d in crawl.css(".hm-io dd")] == ["100 個這一輪該抓的網址", "90 個抓到了"]
    assert "新導出用舊資料補的從 0 筆變成 2 筆" in export.css_first(".hm-said").text()
    assert "交給" not in export.text() and not export.css(".v3-tag")  # no who-can-fix-it (spec 八.2)
    # a reason with a list opens on it: the export's examples open 網頁核查 on that page; one without is a plain row
    att = next(li for li in export.css("ul.rsd > li") if li.css_first(".rs-w").text().startswith("附件沒抓到"))
    assert att.css_first("details .rs-items a").attributes["href"] == "/check?ref=https%3A%2F%2Fa.example%2Ff1"
    assert not crawl.css("ul.rsd details") and all("%" not in p.text() for p in panels)


def test_a_sites_page_has_its_sections_its_steps_beside_them_and_its_nights():
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
    head = tree.css_first("header.st-head")
    assert head.attributes["data-q"] == "1" and head.css_first(".v3-state").attributes["data-state"] == "warn"
    assert not head.css(".v3-who") and "交給" not in head.text() and "50 份文件" in head.text()
    assert [(s.attributes["id"], s.attributes["data-q"]) for s in tree.css("section.st-q")] == [
        ("flow", "2"), ("problems", "3"), ("docs", "4"), ("nights", "5"), ("changes", "6")]
    # the steps are the page: four nodes, a panel each beside them, the one to look at first; no chart library
    home = tree.css_first("[data-home]")
    assert home.attributes["data-first"] == "step-export" and len(tree.css(".hm-nodes .hm-node")) == 4
    assert [p.attributes["data-panel"] for p in tree.css(".hm-side .hm-panel")] == [
        "step-crawl", "step-export", "step-totext", "step-build"]
    assert "echarts" not in html and "<script src" not in html and "%" not in tree.css_first(".hm-side").text()
    problem = tree.css_first("#problems .v3-problems li")
    assert problem.css_first("a").attributes["href"] == "#step-export" and "新導出用舊資料補的" in problem.text()
    # a row a night the record has, the newest first: no axis of fourteen nights for two
    rows = [[c.text() for c in tr.css("th,td")] for tr in tree.css("#nights tbody tr")]
    assert rows == [["11-16 夜", "0", "0", "0", "50", "—"], ["11-15 夜", "2", "1", "0", "49", "—"]]
    kinds = tree.css_first("#docs")
    assert kinds.css_first(".st-kbar").css("i")[0].attributes["style"].startswith("flex-grow:30;background:var(--k0)")
    assert "<b>10</b> 份" in html and "公告、網頁、附件共 50 份" in html and "%" not in kinds.text()
    off = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=(OK, "每一步都正常"), detail=detail,
        changes={"state": "none", "words": ""}, changes_title="", view=None, archive=False, kinds={}, tables="",
        row=row, hist={"enabled": False, "why": "沒有設定紀錄檔（--ops-db）"})
    assert "沒有在記每晚的紀錄" in off and "沒有設定紀錄檔" in off and "#nights tbody" not in off
    assert not LexborHTMLParser(off).css("#nights tbody tr") and "庫裡沒有這個網站的文件" in off
    # a site 流水線's reading does not hold (the library has not taken it in) is said so once: no green mark, and the
    # page does not keep looking again (2026-11-18: 研究發展處's page reloaded itself every five seconds)
    gone = site_page.site_body(
        "b.example", name="b.example", unit="", home="", now=site_page.now_line(None, None, {}, read=True), detail=None,
        changes={"state": "none", "words": ""}, changes_title="", view=None, archive=False, kinds={}, tables="",
        row=None, hist={"enabled": True, "days": [], "nights": []}, read=True)
    assert "location.reload" not in gone and "正式庫還沒有這個網站" in gone
    assert LexborHTMLParser(gone).css_first(".st-head .v3-state").attributes["data-state"] == "none"
    wait = site_page.site_body(
        "a.example", name="甲處", unit="", home="", now=site_page.now_line(None, None, {}, read=False), detail=detail,
        changes={"state": "none", "words": ""}, changes_title="", view=None, archive=False, kinds={}, tables="",
        row=None, hist=None, read=False)
    assert "location.reload" in wait and "正在讀每一步的紀錄" in wait  # no reading yet: it looks again by itself


# ---------------------------------------------------------------- the app


def test_the_app_keeps_the_record_where_it_is_told_and_says_when_it_cannot(db, tmp_path, tmp_path_factory):
    where_db = tmp_path / "t.db"
    ops = tmp_path_factory.mktemp("ops") / "ops.db"
    app = web.create_app(where_db, tmp_path, models=models(), today_fn=lambda: TODAY, ops_db=ops, record=False)
    assert app.state.store.enabled and ops.exists()
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        got = client.get("/api/history").json()
        home = client.get("/tonight").text
    assert got["enabled"] and len(got["days"]) == 14 and got["file"] == str(ops)
    assert "還沒有紀錄：第一晚跑完以後就有" in home
    inside = web.create_app(where_db, tmp_path, models=models(), today_fn=lambda: TODAY, ops_db=tmp_path / "o.db",
                            record=False)
    assert not inside.state.store.enabled and not (tmp_path / "o.db").exists()  # never under --root (/work)
    with TestClient(inside, base_url="http://127.0.0.1:8878") as client:
        assert "紀錄檔不能放在資料或爬蟲存檔的資料夾裡" in client.get("/tonight").text


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
    tree = LexborHTMLParser(home_page.home_body(snap(), tonight(), None, hist(), {}))
    assert tree.css_first(".v3-state b").text() == "完成"
    # the template, then where the problems are listed (總覽's 要處理, since 2026-11-22 evening)
    assert tree.css_first(".v3-say").text() == "11-16 夜：完成；有 2 個問題 列在總覽的要處理"
    fine = {**snap(), "steps": [snap()["steps"][0]], "sites": []}  # one step read, and normal
    calm = LexborHTMLParser(home_page.home_body(fine, tonight(), None, hist(), {}))
    assert calm.css_first(".v3-state b").text() == "完成" and calm.css_first(".v3-say").text() == "11-16 夜：完成；沒有問題"
    running = tonight()
    running["nights"][6]["cells"][3].update(state="run", word="跑中", sev="")
    said = LexborHTMLParser(home_page.home_body(fine, running, None, hist(), {})).css_first(".v3-say")
    assert said.text() == "11-16 夜：跑中（建庫）；沒有問題"
    changes = {"state": "ready", "night": "2026-11-16", "totals": {"new": 48, "left": 20, "unexplained": 0}}
    counted = LexborHTMLParser(home_page.home_body(fine, tonight(), changes, hist(), {}))
    assert counted.css_first(".v3-say").text() == "11-16 夜：完成；新進 48 份、離開 20 份（說不出原因 0 份）；沒有問題"
    # a site's line: a mark that is not 正常 never sits beside 「每一步都正常」
    state, said = site_page.now_line({"worst": WARN, "why": []}, None, {}, read=True)
    assert state == WARN and "正常" not in said
