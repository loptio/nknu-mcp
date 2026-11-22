"""每晚運行 drawn (2026-11-22, owner: 「每晚运行 一定要改 全部都是文字 哪些重要 哪些重要都看不出来」): the nights on their
clock instead of a table of durations, and the few numbers worth seeing first under the sentence on top."""

from __future__ import annotations

from nknu.ops import home_page


def cell(start: float, end: float, *, tone: str = "done", word: str = "完成", when: str = "") -> dict:
    return {"secs": (end - start) * 3600, "dur": "", "tone": tone, "word": word, "when": when, "say": "", "name": "",
            "from": start, "to": end}


def night(day: str, cells: dict, *, tone: str = "ok", word: str = "正常", new=None, left=None, runs=()) -> dict:
    return {"night": day, "title": f"{day[5:]} 夜", "tip": "", "tone": tone, "word": word, "mark": "", "cells": cells,
            "runs": list(runs), "new": new, "changed": 0 if new is not None else None, "left": left,
            "unexplained": 0 if new is not None else None, "counted": new is not None, "docs": None, "sites": []}


def test_each_night_is_a_row_on_its_clock_with_when_the_library_went_in():
    rows = [
        night("2026-11-21", {"crawl": cell(1, 2.37), "build": cell(2.87, 3.15),
                             "swap": cell(3.15, 3.67, when="03:09–03:40（31 分鐘）")}, new=129, left=22),
        night("2026-11-18", {"crawl": cell(1, 2.6), "swap": cell(9.65, 9.65, tone="warn", when="09:39–09:39（0 秒）")},
              tone="warn", word="停了", new=123, left=19, runs=[cell(3.1, 5.1, word="停了", when="03:05–05:06")]),
        night("2026-11-17", {"crawl": cell(1, 4.2), "build": cell(6.4, 6.4, tone="warn", word="停了", when="06:25")},
              tone="warn", word="停了", new=0, left=0),
        night("2026-11-22", {}, tone="none", word="還沒開始"),
    ]
    html = home_page.nights_chart(rows)
    by_night = {r["night"]: html.split(f"data-row='night-{r['night']}'", 1)[1].split("data-row=", 1)[0] for r in rows}
    assert "03:09 換上" in by_night["2026-11-21"] and "<b>+129</b> <span>−22</span>" in by_night["2026-11-21"]
    assert "09:39 換上" in by_night["2026-11-18"]  # stopped, then swapped by hand: the swap is what students got
    assert "hm-grun" in by_night["2026-11-18"] and "另一次進庫" in html  # the other ingest runs, hatched
    assert "沒換庫" in by_night["2026-11-17"] and "hm-gmark" in by_night["2026-11-17"]  # where it stopped, marked
    assert "還沒開始" in by_night["2026-11-22"] and "hm-gnone" in by_night["2026-11-22"]
    assert "data-pick='night-2026-11-21'" in html  # a row picks its night for the side panel, as the table's did
    assert "good-s" not in html and "var(--q5)" in html  # the steps in the ramp's colours, never a state's
    assert "<span style='left:0.00%'>01:00</span>" in html and "--h:" in html
    assert home_page.nights_chart([night("2026-11-22", {}, tone="none")]) == ""  # nothing ran: the table says so


def test_the_numbers_on_top_mark_only_what_needs_a_look():
    ready = {"state": "ready", "totals": {"new": 129, "changed": 240, "left": 22, "unexplained": 0}}
    calm = home_page.top_figures(ready, {"new": [], "old": [{}] * 5, "gone": []}, [{}] * 5)
    assert "新進 <b>129</b>" in calm and "離開 <b>22</b>" in calm and "之前就有的問題 <b>5</b>" in calm
    assert calm.count(home_page.mark("ok")) == 2 and home_page.mark("warn") not in calm  # 說不出原因 0, 新問題 0
    worry = home_page.top_figures({**ready, "totals": {**ready["totals"], "unexplained": 3}},
                                  {"new": [{}], "old": [], "gone": []}, [{}])
    assert "說不出原因 <b>3</b>" in worry and "新問題 <b>1</b>" in worry
    assert home_page.mark("bad") in worry and home_page.mark("warn") in worry
    assert home_page.top_figures({"state": "computing"}, None, []) == ""
