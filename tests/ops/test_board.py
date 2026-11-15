"""The test questions' scoreboard (nknu.ops.board; owner 11-15 on the panels: 「信息太雜…沒有用一些視覺化方法點綴」):
where the best gold answer of every question stands, worked out once per library in the background from the explained
search itself, so the first thing the search page shows is how the whole set is doing."""

from __future__ import annotations

import threading

import pytest

from nknu.ops import board
from nknu.ops.explain import IN_TOP, NO_CANDIDATE, NOT_FOUND, NOT_IN_HEAD, RANKED


def _report(*followed: tuple[str, int | None], limit: int = 10) -> dict:
    """An explained search as far as the board reads it: the followed documents' verdicts and final places."""
    return {"limit": limit, "follow": [{"verdict": verdict, "row": None if rank is None and verdict in (
        NO_CANDIDATE, NOT_FOUND) else {"rank": rank}} for verdict, rank in followed]}


@pytest.mark.parametrize(("followed", "rank", "grade", "stop"), [
    ([(IN_TOP, 1), (NOT_IN_HEAD, 64)], 1, board.FIRST, IN_TOP),
    ([(IN_TOP, 3), (IN_TOP, 2)], 2, board.TOP3, IN_TOP),
    ([(IN_TOP, 8), (NO_CANDIDATE, None)], 8, board.TOP, IN_TOP),
    ([(RANKED, 24), (NOT_IN_HEAD, 108)], 24, board.OUT, RANKED),
    ([(NOT_IN_HEAD, 554), (NO_CANDIDATE, None)], 554, board.OUT, NOT_IN_HEAD),
    ([(NO_CANDIDATE, None), (NOT_FOUND, None)], None, board.OUT, NO_CANDIDATE),
    ([(NOT_FOUND, None)], None, board.OUT, NOT_FOUND),
    ([], None, board.OUT, NOT_FOUND),
])
def test_a_question_is_graded_by_its_best_gold_answer(followed, rank, grade, stop):
    got = board.outcome(_report(*followed))
    assert (got["rank"], got["grade"], got["stop"], got["gold"]) == (rank, grade, stop, len(followed))


def test_the_grades_follow_the_searchs_own_limit():
    assert board.outcome(_report((RANKED, 12), limit=20))["grade"] == board.TOP
    assert board.outcome(_report((RANKED, 12), limit=10))["grade"] == board.OUT


QUESTIONS = [{"id": "Q01", "question": "停修", "gold": ["d3"]}, {"id": "Q02", "question": "退選", "gold": ["dq"]},
             {"id": "N01", "question": "校車", "gold": ["dz"]}]


def _board(explain, state=lambda: ("lib", 1)):
    return board.Board(QUESTIONS, explain, state)


def test_every_question_is_searched_once_and_in_the_files_order():
    asked: list[str] = []

    def explain(question):
        asked.append(question["id"])
        return _report((IN_TOP, {"Q01": 1, "Q02": 3, "N01": 30}[question["id"]]))

    b = _board(explain)
    first = b.snapshot()
    assert first["total"] == 3
    b.wait(5)
    got = b.snapshot()
    assert asked == ["Q01", "Q02", "N01"] and got["done"] == 3 and not got["running"]
    assert [(i["id"], i["rank"], i["grade"]) for i in got["items"]] == [
        ("Q01", 1, board.FIRST), ("Q02", 3, board.TOP3), ("N01", 30, board.OUT)]
    assert got["counts"] == {board.FIRST: 1, board.TOP3: 1, board.TOP: 0, board.OUT: 1, board.FAILED: 0}
    b.snapshot()
    b.wait(5)
    assert asked == ["Q01", "Q02", "N01"]  # nothing is searched again while the library is the same


def test_a_snapshot_that_only_looks_starts_nothing():
    asked: list[str] = []
    b = _board(lambda q: asked.append(q["id"]) or _report((IN_TOP, 1)))
    got = b.snapshot(start=False)
    assert got["done"] == 0 and not got["running"] and asked == []
    assert [i["grade"] for i in got["items"]] == [board.WAIT] * 3 and got["items"][0]["question"] == "停修"


def test_a_question_whose_search_fails_is_said_and_the_rest_go_on():
    def explain(question):
        if question["id"] == "Q02":
            raise RuntimeError("model went away")
        return _report((IN_TOP, 1))

    b = _board(explain)
    b.snapshot()
    b.wait(5)
    got = b.snapshot()
    assert got["done"] == 3 and got["counts"][board.FAILED] == 1 and got["counts"][board.FIRST] == 2
    failed = got["items"][1]
    assert failed["grade"] == board.FAILED and "model went away" in failed["error"] and failed["rank"] is None


def test_a_new_library_is_graded_again():
    state = {"key": ("lib", 1)}
    asked: list[str] = []
    b = _board(lambda q: asked.append(q["id"]) or _report((IN_TOP, 1)), lambda: state["key"])
    b.snapshot()
    b.wait(5)
    state["key"] = ("lib", 2)
    fresh = b.snapshot()
    assert fresh["done"] < 3 or fresh["running"] or len(asked) == 6  # the old grades are not shown as the new library's
    b.wait(5)
    assert len(asked) == 6 and b.snapshot()["done"] == 3


def test_the_grades_of_a_library_that_was_swapped_mid_way_are_dropped():
    """A search that was running when the library changed belongs to neither: its grade is not kept."""
    state = {"key": ("lib", 1)}
    gate, entered = threading.Event(), threading.Event()

    def explain(question):
        if question["id"] == "Q01" and state["key"] == ("lib", 1):
            entered.set()
            gate.wait(5)
        return _report((IN_TOP, 1 if state["key"] == ("lib", 2) else 9))

    b = _board(explain, lambda: state["key"])
    b.snapshot()
    assert entered.wait(5)
    state["key"] = ("lib", 2)
    gate.set()
    b.wait(5)
    b.snapshot()
    b.wait(5)
    got = b.snapshot()
    assert got["done"] == 3 and {i["rank"] for i in got["items"]} == {1}


def test_no_questions_is_an_empty_board_that_runs_nothing():
    b = board.Board([], lambda q: _report(), lambda: ("lib", 1))
    got = b.snapshot()
    assert got == {"total": 0, "done": 0, "running": False, "limit": board.LIMIT, "items": [],
                   "counts": {board.FIRST: 0, board.TOP3: 0, board.TOP: 0, board.OUT: 0, board.FAILED: 0}}
