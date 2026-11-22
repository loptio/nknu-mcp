"""要處理: what 總覽 asks the owner to look at, in one list (2026-11-22 evening; owner: 「你先全部做」 after the review
that the panels showed the same things in several places and lit old problems like new ones every morning).

Its rows come from what the panels already know, each kept where it is computed:

  the night and the steps   tonight_page.problems (a part that stopped, a run that failed, a step not normal, a site
                            to look at), split by standing.split into new since the night before and standing
  this machine              a drive or a memory past its warning line (pipeline's machine numbers)
  the search                the student questions whose answer dropped out of the top 10 since the night before
                            (the nightly measurement, deploy/lab-pipeline/eval/student_eval.py)

A problem the owner has said is known is not counted: it is listed folded, with the note, so it can be found and
taken off again. He marks it on 總覽 itself (a button on the row, a note if he likes; 取消 takes it off), kept in the
panel's own ops.db (Known); until 2026-11-22 night it was a file edited by hand on his word, which made him ask
someone for every row.

Rows name what is wrong and where, never who fixes it (spec 2026-11-17-panels-v3-questions.md 八.2). 【Claude】
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from nknu.ops.nightly import TONIGHT
from nknu.ops.pipeline import BAD, WARN

RANK = {BAD: 0, WARN: 1}
TOP = 10  # a question whose answer leaves the first ten results is a drop worth a row


class Known:
    """The problems the owner marked known: issue key -> (note, when), in ops.db's table known. path None: none kept
    (nothing is known, marking does nothing)."""

    SCHEMA = "CREATE TABLE IF NOT EXISTS known (key TEXT PRIMARY KEY, note TEXT NOT NULL, at TEXT NOT NULL)"

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute(self.SCHEMA)
        return conn

    def load(self) -> dict[str, str]:
        """{issue key: the owner's note}; {} without the file (every problem counts: the safe side)."""
        if self.path is None or not self.path.exists():
            return {}
        conn = self._connect()
        try:
            return {k: n for k, n in conn.execute("SELECT key, note FROM known")}
        finally:
            conn.close()

    def mark(self, keys: list[str], note: str) -> None:
        if self.path is None:
            return
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            with conn:
                conn.executemany("INSERT OR REPLACE INTO known (key, note, at) VALUES (?, ?, ?)",
                                 [(k, note[:200], at) for k in keys if k])
        finally:
            conn.close()

    def unmark(self, keys: list[str]) -> None:
        if self.path is None or not self.path.exists():
            return
        conn = self._connect()
        try:
            with conn:
                conn.executemany("DELETE FROM known WHERE key = ?", [(k,) for k in keys])
        finally:
            conn.close()


def _known_note(row: dict, known: dict[str, str]) -> str | None:
    """The note when every issue of the row is known (an issue's own key, or its site's key: site:<site>), else None."""
    keys = [it.get("key") or "" for it in row.get("issues") or []]
    if not keys or not known:
        return None
    notes = []
    for key in keys:
        site = ":".join(key.split(":")[:2]) if key.startswith("site:") else ""
        note = known.get(key) if key in known else known.get(site) if site and site in known else None
        if note is None:
            return None
        notes.append(note)
    return next((x for x in notes if x), "")


def machine_rows(meters: list[dict]) -> list[dict]:
    """A drive or a memory past its warning line (machine_meters' tone), as a row."""
    out = []
    for m in meters:
        if m.get("tone") not in (WARN, BAD):
            continue
        out.append({"sev": m["tone"], "kind": "這台電腦", "where": m.get("label") or "", "href": "",
                    "reason": f"用了 {m.get('pct') or 0:.0f}%：{m.get('detail') or ''}".rstrip("："), "tag": "",
                    "issues": [{"key": f"machine:{m.get('label')}"}]})
    return out


def drops(student: dict | None) -> list[dict]:
    """The open half's questions whose answer was in the top 10 the night before and is not now (as typed, or as the
    agent searched), the worst first; [] when there is no night before to compare with."""
    out = []
    for q in (student or {}).get("questions") or []:
        for way, word in (("raw", "學生原話"), ("agent", "AI 換說法")):
            before, now = q.get(f"prev_{way}"), q.get(way)
            if before is not None and before <= TOP and (now is None or now > TOP):
                out.append({**q, "way": way, "way_word": word, "before": before, "now": now})
    return sorted(out, key=lambda x: (x["before"], x["id"]))


def search_rows(student: dict | None) -> list[dict]:
    """One row for the questions that dropped out of the top 10 since the night before (they are listed on 搜尋)."""
    got = drops(student)
    if not got:
        return []
    ids = sorted({x["id"] for x in got})
    said = [f"{x['id']}（{x['way_word']} 第 {x['before']} → {rank_words(x['now'])}）" for x in got[:3]]
    return [{"sev": WARN, "kind": "搜尋", "where": f"{len(ids)} 題掉出前 10", "href": "/try#drops",
             "reason": "、".join(said) + ("…" if len(got) > 3 else ""),
             "tag": "", "issues": [{"key": "search:drops:" + ",".join(ids)}]}]


def rank_words(rank: int | None) -> str:
    """A rank as the rows say it; None: not among the results the measurement reads."""
    return "沒搜到" if rank is None else f"第 {rank}"


MEASURE_WORDS = {"failed": "沒量成", "timeout": "超過時間被停掉", "unreadable": "結果讀不出來"}


def measure_rows(runs: list[dict]) -> list[dict]:
    """The student questions' measurement after the last swap (build.sh 5b, recorded by nightly_ingest.py in the
    run's student_eval since 2026-11-22 night), as a row when it did not end well, or did not run in a build that
    swapped (the run has the field, left empty). Runs from before the field existed say nothing."""
    run = next((r for r in reversed(runs) if r.get("outcome") == "swapped" and "student_eval" in r), None)
    if run is None:
        return []
    got, night = run.get("student_eval"), str(run.get("night") or run.get("_night") or "")
    if isinstance(got, dict) and got.get("outcome") == "ok":
        return []
    if isinstance(got, dict):
        said = MEASURE_WORDS.get(str(got.get("outcome")), str(got.get("outcome") or "沒量成"))
        reason = f"{night[5:]} 夜換庫後量學生題{said}" + (f"（退出碼 {got['exit']}）" if got.get("exit") else "")
    else:
        reason = f"{night[5:]} 夜換了庫，卻沒有量學生題（題庫檔或量測程序不在）"
    return [{"sev": WARN, "kind": "搜尋", "where": "學生題的量測", "href": "/try#students", "reason": reason,
             "tag": "", "issues": [{"key": f"measure:{night}"}]}]


KIND_OF = (("part:", "每晚流程"), ("run:", "每晚流程"), ("step:", "每晚流程"), ("site:", "網站"))


def kind_of(row: dict) -> str:
    key = ((row.get("issues") or [{}])[0].get("key")) or ""
    return next((w for p, w in KIND_OF if key.startswith(p)), "每晚流程")


def collect(parts: dict | None, rows: list[dict], meters: list[dict], student: dict | None,
            known: dict[str, str], runs: list[dict] | None = None) -> dict:
    """{'open': rows to handle, the new first; 'known': rows the owner said are known, each with its note; 'night':
    the night compared with ('' when none)}. parts: standing.split of rows (None: no night before kept, every row is
    listed as it is)."""
    listed: list[dict] = []
    if parts is not None:
        listed += [{**x, "tag": "新出現", "kind": kind_of(x)} for x in parts["new"]]
        listed += [{**x, "tag": x.get("age") or "", "kind": kind_of(x)} for x in parts["old"]]
    else:
        listed += [{**x, "tag": "", "kind": kind_of(x)} for x in rows]
    listed += machine_rows(meters) + search_rows(student) + measure_rows(runs or [])
    open_, kept = [], []
    for x in listed:
        note = _known_note(x, known)
        # a night's part or a step links into 每晚運行 (#nights, #step-build): on 總覽 that is /tonight's anchor
        href = x.get("href") or ""
        href = TONIGHT + href if href.startswith("#") else href
        (kept if note is not None else open_).append({**x, "href": href, "note": note or ""})
    new_first = {"新出現": 0}
    open_.sort(key=lambda x: (new_first.get(x.get("tag"), 1), RANK.get(x.get("sev"), 2)))
    return {"open": open_, "known": kept, "night": (parts or {}).get("night") or ""}
