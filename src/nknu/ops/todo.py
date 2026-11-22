"""要處理: what 總覽 asks the owner to look at, in one list (2026-11-22 evening; owner: 「你先全部做」 after the review
that the panels showed the same things in several places and lit old problems like new ones every morning).

Its rows come from what the panels already know, each kept where it is computed:

  the night and the steps   tonight_page.problems (a part that stopped, a run that failed, a step not normal, a site
                            to look at), split by standing.split into new since the night before and standing
  this machine              a drive or a memory past its warning line (pipeline's machine numbers)

(The student questions' nightly measurement gave rows too for an evening; the owner dropped that measurement the same
night, 2026-11-22: the question bank checks the whole project, never the night.)

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




KIND_OF = (("part:", "每晚流程"), ("run:", "每晚流程"), ("step:", "每晚流程"), ("site:", "網站"))


def kind_of(row: dict) -> str:
    key = ((row.get("issues") or [{}])[0].get("key")) or ""
    return next((w for p, w in KIND_OF if key.startswith(p)), "每晚流程")


def collect(parts: dict | None, rows: list[dict], meters: list[dict], known: dict[str, str]) -> dict:
    """{'open': rows to handle, the new first; 'known': rows the owner said are known, each with its note; 'night':
    the night compared with ('' when none)}. parts: standing.split of rows (None: no night before kept, every row is
    listed as it is)."""
    listed: list[dict] = []
    if parts is not None:
        listed += [{**x, "tag": "新出現", "kind": kind_of(x)} for x in parts["new"]]
        listed += [{**x, "tag": x.get("age") or "", "kind": kind_of(x)} for x in parts["old"]]
    else:
        listed += [{**x, "tag": "", "kind": kind_of(x)} for x in rows]
    listed += machine_rows(meters)
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
