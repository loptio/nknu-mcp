"""The test questions' scoreboard: where the best gold answer of every question stands in the library the service
reads. The search page opens with it, so the owner sees how the whole set is doing before reading any one search
(owner 11-15 on the panels: 「整個面版給人的信息太雜了 沒有很人性化的讓人看到 也沒有用一些視覺化方法點綴」; the
scoreboard itself is 【Claude】).

Nothing is scored here: each question is the explained search itself (nknu.ops.explain.explain_search with the
question's gold documents followed), run once per library and day in a background thread, one question at a time, so a
search the owner asks for in between waits for one question at most. The grades are kept in memory only; a library
that was rebuilt, or a new day (the date parts of a score follow today), is graded again when the page is next opened.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Hashable

from nknu.ops.explain import IN_TOP, NO_CANDIDATE, NOT_FOUND, NOT_IN_HEAD, RANKED
from nknu.retrieval.search import TOP_N

log = logging.getLogger(__name__)

# a question's grade, by the final place of its best gold answer 【Claude】: the three cuts the content session's
# reports already use (進前 1／3／10 名), then everything else; WAIT: not searched yet; FAILED: its search raised
FIRST, TOP3, TOP, OUT, WAIT, FAILED = "first", "top3", "top", "out", "wait", "failed"
GRADES = (FIRST, TOP3, TOP, OUT, FAILED)
LIMIT = TOP_N  # how many results a student's AI gets: the cut of TOP
_FAR = (IN_TOP, RANKED, NOT_IN_HEAD, NO_CANDIDATE, NOT_FOUND)  # how far a document got, furthest first


def outcome(report: dict) -> dict:
    """What an explained search says of the documents it followed: {'rank': the best final place among them (None:
    none was ranked), 'grade', 'stop': how far the best one got (a verdict of nknu.ops.explain), 'gold': how many were
    followed}."""
    limit = report.get("limit") or LIMIT
    followed = report.get("follow") or []
    places = [e["row"]["rank"] for e in followed if e.get("row") and e["row"].get("rank")]
    rank = min(places) if places else None
    if rank == 1:
        grade = FIRST
    elif rank is not None and rank <= 3:
        grade = TOP3
    elif rank is not None and rank <= limit:
        grade = TOP
    else:
        grade = OUT
    got = {e.get("verdict") for e in followed}
    stop = next((v for v in _FAR if v in got), NOT_FOUND)
    return {"rank": rank, "grade": grade, "stop": stop, "gold": len(followed)}


class Board:
    """The grades of questions ([{id, question, gold}], web.load_questions), for the library state() names.

    explain(question) is the explained search of one question (it raises when the search fails); state() is whatever
    changes when the answers may: the library file's time and size, the day."""

    def __init__(self, questions: list[dict], explain: Callable[[dict], dict], state: Callable[[], Hashable]) -> None:
        self.questions = list(questions)
        self._explain = explain
        self._state = state
        self._lock = threading.Lock()
        self._key: Hashable | None = None
        self._items: dict[str, dict] = {}
        self._thread: threading.Thread | None = None

    def _run(self, key: Hashable) -> None:
        for question in self.questions:
            with self._lock:
                if self._key != key:
                    return  # the library changed: the next snapshot starts over
                if question["id"] in self._items:
                    continue
            try:
                item = outcome(self._explain(question))
            except Exception as exc:  # one question's failure is shown on its tile, the rest go on
                log.exception("成绩板：%s 没搜成", question["id"])
                item = {"rank": None, "grade": FAILED, "stop": "", "gold": len(question.get("gold") or []),
                        "error": f"{type(exc).__name__}: {exc}"}
            with self._lock:
                if self._key == key and self._state() == key:  # else it was searched over a library that is gone
                    self._items[question["id"]] = item

    def snapshot(self, start: bool = True) -> dict:
        """The board as it stands: {'total', 'done', 'running', 'limit', 'counts': grade -> questions, 'items': one
        per question in the files' order: id, question, rank, grade, stop, gold, error}. start: begin grading what
        is not graded yet (False: only look)."""
        key = self._state()
        with self._lock:
            if key != self._key:
                self._key, self._items = key, {}
            running = self._thread is not None and self._thread.is_alive()
            if start and not running and len(self._items) < len(self.questions):
                self._thread = threading.Thread(target=self._run, args=(key,), name="board", daemon=True)
                self._thread.start()
                running = True
            items = []
            for question in self.questions:
                got = self._items.get(question["id"])
                items.append({"id": question["id"], "question": question["question"], "rank": None, "grade": WAIT,
                              "stop": "", "gold": len(question.get("gold") or []), "error": "", **(got or {})})
            done = len(self._items)
        counts = {grade: sum(1 for i in items if i["grade"] == grade) for grade in GRADES}
        return {"total": len(items), "done": done, "running": running and done < len(items), "limit": LIMIT,
                "items": items, "counts": counts}

    def wait(self, timeout: float | None = None) -> None:
        """Until the grading that is running has ended (the tests' way to a settled board)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
