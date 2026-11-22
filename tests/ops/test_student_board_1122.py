"""搜尋's scoreboard since 2026-11-22 evening (nknu.ops.student_board): the student questions measured each night on the
library students got (deploy/lab-pipeline/eval/student_eval.py → student_eval.json), in place of the 33 questions.
What these tests keep: the totals as typed and as an agent searched, the move since the night before, the sealed half
said as totals only, the questions that dropped out of the top 10, nothing drawn without a measurement. Made-up
measurements only (the file's keys as student_eval.py writes them, version 1)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.ops import student_board

OPEN = [
    {"id": "O01", "q": "怎麼申請停修", "raw": 1, "agent": 1, "prev_raw": 1, "prev_agent": 2},
    {"id": "O02", "q": "宿舍什麼時候開放", "raw": None, "agent": 4, "prev_raw": 2, "prev_agent": 4},
    {"id": "O03", "q": "畢業門檻<b>英文</b>", "raw": 14, "agent": 9, "prev_raw": 6, "prev_agent": 11},
    {"id": "O04", "q": "抵免學分", "raw": 3, "agent": 2, "prev_raw": 3, "prev_agent": 2},
]


def measured(**over) -> dict:
    """A measurement with a night before: 83 questions, the open half's 4 listed, the sealed half's 42 as totals."""
    rep = {"version": 1, "measured": "2026-11-22T04:10:00",
           "library": {"path": "aca.db", "built": "2026-11-22T03:01:46+08:00", "docs": 89073},
           "n": 83, "totals": {"raw": {"top1": 30, "top3": 50, "top10": 70},
                               "agent": {"top1": 40, "top3": 60, "top10": 75}},
           "halves": {"open": {"n": 41, "raw": {"top1": 16, "top3": 25, "top10": 37},
                               "agent": {"top1": 19, "top3": 30, "top10": 37}},
                      "sealed": {"n": 42, "raw": {"top1": 14, "top3": 25, "top10": 33},
                                 "agent": {"top1": 21, "top3": 30, "top10": 38}}},
           "questions": [dict(q) for q in OPEN], "depth": 50,
           "prev": {"measured": "2026-11-21T04:05:00", "library": {"built": "2026-11-21T03:00:00+08:00"},
                    "totals": {"raw": {"top1": 28, "top3": 49, "top10": 71},
                               "agent": {"top1": 41, "top3": 60, "top10": 75}}},
           "source": "research/eval_2026-11-18/questions.json",
           "skipped": {"expired": {"open": ["O90"], "sealed": 1}, "not_in_library": {"open": ["O91"], "sealed": 2}},
           "seconds": 120}
    rep.update(over)
    return rep


def tree_of(rep: dict) -> LexborHTMLParser:
    return LexborHTMLParser(student_board.board(rep))


def test_the_totals_as_typed_and_as_an_agent_searched_with_the_move_since_the_night_before():
    tree = tree_of(measured())
    head = tree.css_first("h2#students")
    assert head.text() == "學生題的名次83 題 · 11-22 04:10 量 · 11-22 03:01 建的庫"
    rows = tree.css(".sb-row")
    assert [r.css_first("b").text() for r in rows] == ["學生原話", "AI 換說法"]
    raw, agent = (r.css_first(".sb-nums").text() for r in rows)
    assert "第 1 名 30（36%）" in raw and "前 3 50" in raw and "前 10 70" in raw and "比前一晚 +2 題" in raw
    assert "第 1 名 40（48%）" in agent and "前 10 75" in agent and "比前一晚 -1 題" in agent
    # the bars nest: the top 10 widest, first place narrowest, each a share of all the questions
    widths = [float(re.search(r"width:([\d.]+)%", i.attributes["style"]).group(1)) for i in rows[0].css(".sb-track i")]
    assert widths == [84.3, 60.2, 36.1]
    first = tree_of(measured(prev=None))
    assert "比前一晚" not in first.html and "第一次量，還沒有前一晚可以比" in first.css_first(".sb-none").text()


def test_the_sealed_half_is_said_as_totals_and_never_lists_a_question_or_an_id():
    html = student_board.board(measured())
    tree = LexborHTMLParser(html)
    sealed = tree.css_first(".sb-sealed").text()
    assert sealed.startswith("封存的 42 題只看總數")
    assert "學生原話第 1 名 14、前 10 33" in sealed and "AI 換說法第 1 名 21、前 10 38" in sealed
    # every id on the board is one of the open half's the file lists; the sealed half has none to list, and what
    # the measurement skipped (its ids and the sealed half's counts) is not drawn
    shown = {x.text() for x in tree.css(".sb-id")} - {""}
    assert shown == {q["id"] for q in OPEN} and "O90" not in html and "O91" not in html
    assert tree.css_first(".sb-sealed").css("a, li") == []
    listed = tree.css("ul.sb-list")[-1].css("li:not(.sb-head)")
    assert len(listed) == len(OPEN) and "平時看的 41 題" in tree.text()
    assert student_board.board(measured(halves={"open": {"n": 41}})).count("sb-sealed") == 1  # its style, no line


def test_the_questions_that_dropped_out_of_the_top_ten_are_listed_with_a_link_to_their_search():
    tree = tree_of(measured())
    drops = tree.css("ul.sb-list")[0].css("li")
    got = [(li.css_first(".sb-id").text(), li.css("span")[1].text()) for li in drops]
    assert got == [("O02", "學生原話"), ("O03", "學生原話")]  # in the top 10 the night before, not now; the first first
    a = drops[1].css_first("a")
    assert a.text() == "畢業門檻<b>英文</b>" and "<b>英文</b>" not in tree.html  # a question's words are text
    asked = urlsplit(a.attributes["href"])
    assert asked.path == "/try" and asked.fragment == "result"
    assert parse_qs(asked.query) == {"tab": ["why"], "q": ["畢業門檻<b>英文</b>"]}
    rank = drops[0].css_first(".sb-rank")
    assert rank.attributes["data-r"] == "far" and rank.text() == "沒搜到↓2"
    assert rank.css_first("small").attributes["data-d"] == "down" and "前一晚 第 2" in rank.css_first("small").attributes["title"]
    steady = [{**q, "prev_raw": q["raw"], "prev_agent": q["agent"]} for q in OPEN]
    calm = tree_of(measured(questions=steady))
    assert calm.css_first(".sb-none").text() == "沒有掉出前 10 的題" and "↓" not in calm.html and "↑" not in calm.html


def test_the_open_half_is_listed_the_worst_first_with_how_each_rank_moved():
    tree = tree_of(measured())
    rows = tree.css("ul.sb-list")[-1].css("li:not(.sb-head)")
    assert [li.css_first(".sb-id").text() for li in rows] == ["O02", "O03", "O04", "O01"]  # 沒搜到, 14, 3, 1
    ranks = [[r.text() for r in li.css(".sb-rank")] for li in rows]
    assert ranks[1] == ["第 14↓6", "第 9↑11"] and ranks[3] == ["第 1", "第 1↑2"] and ranks[2] == ["第 3", "第 2"]
    assert [r.attributes["data-r"] for r in rows[1].css(".sb-rank")] == ["far", "in"]


def test_nothing_is_drawn_without_a_measurement():
    assert student_board.board(None) == "" and student_board.board({}) == ""
    assert student_board.board({**measured(), "n": 0}) == ""


def test_a_measurement_of_another_library_says_so():
    """A night whose swap was held keeps the last measurement (the content session's note, 11-22): 搜尋 says it is
    not of the library in use; the same library says nothing."""
    from nknu.ops import student_board

    got = {"n": 2, "measured": "2026-11-22T21:27:01", "library": {"built": "2026-11-22T03:01:46+08:00"},
           "totals": {"raw": {"top1": 1, "top3": 1, "top10": 2}, "agent": {"top1": 1, "top3": 2, "top10": 2}},
           "questions": [], "prev": None}
    assert student_board.stale(got, "2026-11-23T03:05:00+08:00") and not student_board.stale(got, "2026-11-22T03:01:46")
    assert "不是現在這份庫的成績" in student_board.board(got, "2026-11-23T03:05:00+08:00")
    assert "不是現在這份庫的成績" not in student_board.board(got, "2026-11-22T03:01:46+08:00")
    assert not student_board.stale(got, "") and not student_board.stale(None, "2026-11-23T03:05")


def test_the_nightly_sample_shows_each_group_and_opens_a_question_with_its_right_documents():
    """Version 2 (the owner's sample of 11-22 night): B as typed and as an AI rewords it, A and C as typed; every
    sampled question listed under its group, opening its explanation by id (its right documents followed); the
    sealed half said to be left to the occasional full run; expired questions named, not counted."""
    from nknu.ops import student_board

    got = {"version": 2, "n": 2, "measured": "2026-11-22T22:40:00", "seconds": 241.5,
           "library": {"built": "2026-11-22T03:01:46+08:00"},
           "sample": {"version": "s1-20261122", "groups": {"B": 2, "A": 1, "C": 1}},
           "totals": {"raw": {"top1": 1, "top3": 1, "top10": 2}, "agent": {"top1": 2, "top3": 2, "top10": 2}},
           "groups": {"B": {"n": 2, "raw": {"top1": 1, "top3": 1, "top10": 2}, "agent": {"top1": 2, "top3": 2, "top10": 2}},
                      "A": {"n": 1, "raw": {"top1": 1, "top3": 1, "top10": 1}},
                      "C": {"n": 1, "raw": {"top1": 0, "top3": 0, "top10": 1}}},
           "questions": [{"id": "B001", "group": "B", "q": "暑修怎么报名", "raw": 15, "agent": 5, "gold": ["d1"]},
                         {"id": "B013", "group": "B", "q": "水墨老師", "raw": 1, "agent": 1, "gold": ["d2"]},
                         {"id": "A007", "group": "A", "q": "某公告", "raw": 1, "agent": None, "gold": ["d3"]},
                         {"id": "C004", "group": "C", "q": "課程大綱", "raw": 4, "agent": None, "gold": ["d4"]}],
           "expired": ["C050"], "prev": None}
    html = student_board.board(got, "2026-11-22T03:01:46+08:00")
    assert "學生角度 · 原話" in html and "學生角度 · AI 換說法" in html and "從文件出的題 · 原話" in html
    assert "從文件出的題 · AI 換說法" not in html and "新進庫的內容 · 原話" in html
    assert "qid=B001" in html and "抽樣 s1-20261122" in html and "封存的那一半不在每晚量" in html
    assert "C050" in html and "第一次量這份抽樣" in html
    assert html.index("B001") < html.index("B013")  # the worst first
