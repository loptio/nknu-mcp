"""每晚運行 drawn (2026-11-22, owner: 「每晚运行 一定要改 全部都是文字 哪些重要 哪些重要都看不出来」): the nights on their
clock instead of a table of durations, and the few numbers worth seeing first under the sentence on top; and since that
evening 總覽's first screen, the night in a sentence and the last nights, each opening its own on 每晚運行."""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from nknu.ops import home_page, panorama
from tests.ops.test_panels_v3 import hist, tonight


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
    # 2026-11-22 evening: only the swap's three; the problems are counted on 總覽's 要處理, not here again
    ready = {"state": "ready", "totals": {"new": 129, "changed": 240, "left": 22, "unexplained": 0}}
    calm = home_page.top_figures(ready, {"new": [], "old": [{}] * 5, "gone": []}, [{}] * 5)
    assert "新進 <b>129</b>" in calm and "離開 <b>22</b>" in calm and "說不出原因 <b>0</b>" in calm
    assert "問題" not in calm and calm.count("</b></span>") == 3
    assert calm.count(home_page.mark("ok")) == 1 and home_page.mark("warn") not in calm  # 說不出原因 0
    worry = home_page.top_figures({**ready, "totals": {**ready["totals"], "unexplained": 3}},
                                  {"new": [{}], "old": [], "gone": []}, [{}])
    assert "說不出原因 <b>3</b>" in worry and "新問題" not in worry
    assert home_page.mark("bad") in worry and home_page.mark("warn") not in worry
    assert home_page.top_figures({"state": "computing"}, None, []) == ""
    assert home_page.top_figures({"state": "computing"}, {"new": [{}], "old": [], "gone": []}, [{}]) == ""


def test_the_front_page_says_the_night_in_a_sentence_and_each_night_opens_its_own():
    # 總覽's first screen since 2026-11-22 evening: the night shown in one sentence linking 每晚運行, and the last
    # nights, each label opening that night there (the side panel 每晚運行 draws for it)
    card = LexborHTMLParser(panorama.nights_card(tonight(), hist()))
    labels = card.css("a.pn-glbl")
    assert [a.text() for a in labels][:2] == ["11-16 夜", "11-15 夜"]
    assert [a.attributes["href"] for a in labels][:2] == ["/tonight?night=2026-11-16", "/tonight?night=2026-11-15"]
    assert card.css_first("h2").text() == f"最近 {len(labels)} 晚"
    assert panorama.night_sentence(tonight(), hist()) == ("ok", "11-16 夜正常")
    broke = tonight()
    broke["default"] = "2026-11-14"  # the night whose build failed (test_panels_v3.tonight: nights[4])
    state, said = panorama.night_sentence(broke, hist())
    assert said == "11-14 夜出錯（建庫），沒換庫：學生搜到的還是前一晚的庫" and state == "bad"
    assert panorama.night_sentence({"nights": []}, {}) == ("none", "還沒有每晚的紀錄")
    top = LexborHTMLParser(panorama.header_bar({"steps": [{"key": "x"}]}, broke, hist(), {"open": []}))
    say = top.css_first("a.pn-say")
    assert say.attributes["href"] == "/tonight" and say.text() == said
