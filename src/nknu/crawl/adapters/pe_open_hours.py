"""體育室「場地開放時間」table (c.nknu.edu.tw/reality/Page.aspx?PN=8): term hours per venue, both campuses.

The rowspan grid is cross-checked against pandas.read_html; any disagreement voids the run (fail closed).
Rows go to review (high impact); unknown venue names only raise an EntityHint.
"""

from __future__ import annotations

import io
import re
from typing import ClassVar

import pandas as pd
from selectolax.lexbor import LexborHTMLParser

from nknu.core.records import EntityHint, FactRecord, HoursRule, ParseIssue, Record, SourceRef
from nknu.core.rocdate import norm, parse_hours_segments, parse_roc_range
from nknu.core.textnorm import alias_norm
from nknu.crawl.adapter import Adapter, ParseError, RunHistory, Target, Violation, adapter
from nknu.crawl.fetch import Snapshot
from nknu.crawl.htmltext import element_text, leaf_tables, table_grid
from nknu.crawl.invariants import standard

URL = "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"
TITLE = "場地開放時間"
CAMPUS = {"和平": "heping", "燕巢": "yanchao"}
VENUE = {
    ("heping", alias_norm("體育館")): "nknu.heping.gym.main",
    ("heping", alias_norm("室外運動場地")): "nknu.heping.gym.outdoor",
    ("heping", alias_norm("重量訓練室")): "nknu.heping.gym.weight-room",
    ("heping", alias_norm("游泳池")): "nknu.heping.gym.pool",
    ("yanchao", alias_norm("室外運動場地")): "nknu.yanchao.gym.outdoor",
    ("yanchao", alias_norm("桌球、撞球室")): "nknu.yanchao.gym.table-tennis",
    ("yanchao", alias_norm("重量訓練室(歸燕食巢3樓)")): "nknu.yanchao.gym.weight-room",
    ("yanchao", alias_norm("重量訓練室")): "nknu.yanchao.gym.weight-room",
}
CORE_VENUE = "nknu.heping.gym.weight-room"
_CLOSED_WORDS = re.compile(r"不開放|整修|暫停|停止開放")
_NOTE_LINE = re.compile(r"^\d+[.、]")


def _venue_table(root) -> tuple[str, list[list[str]]]:
    for t in leaf_tables(root):
        grid = table_grid(t)
        if grid and grid[0] and "校區" in grid[0][0] and len(grid[0]) >= 4:
            return t.html, grid
    raise ParseError("venue table (校區 / 運動場地 / 開放日期 / 開放時間) not found")


def _notes(root, base: str) -> list[str]:
    lines = element_text(root, base).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("附註"))
    except StopIteration:
        return []
    return [ln for ln in lines[start + 1:] if _NOTE_LINE.match(ln)]


def _cross_check(table_html: str, grid: list[list[str]]) -> None:
    """Parse the same <table> with lxml (pandas) and compare cell by cell with the lexbor grid."""
    try:
        dfs = pd.read_html(io.StringIO(table_html))
    except ValueError as exc:
        raise ParseError(f"cross_check: pandas found no tables ({exc})") from exc
    want = [norm(c) for c in grid[0][:4]]
    for df in dfs:
        rows = [["" if pd.isna(v) else str(v) for v in r] for r in df.itertuples(index=False)]
        if rows and [norm(c) for c in rows[0][:4]] == want:
            if len(rows) != len(grid):
                raise ParseError(f"cross_check_mismatch: {len(rows)} rows (pandas) vs {len(grid)} (grid)")
            for i, (a, b) in enumerate(zip(rows, grid, strict=True)):
                for j in range(4):
                    if norm(a[j]) != norm(b[j]):
                        raise ParseError(f"cross_check_mismatch at row {i} col {j}: {a[j]!r} vs {b[j]!r}")
            return
    raise ParseError("cross_check: pandas did not find the venue table")


@adapter("pe_open_hours", hosts=["c.nknu.edu.tw"])
class PeOpenHours(Adapter):
    required_selectors: ClassVar[list[str]] = ["#lbl_Contact"]

    def discover(self, ctx) -> list[Target]:
        return [Target(URL)]

    def parse(self, snap: Snapshot) -> list[Record]:
        if snap.status != 200 or not snap.text:
            return []
        root = LexborHTMLParser(snap.text).css_first("#lbl_Contact")
        if root is None:
            return []  # selector_missing is reported by invariants
        table_html, grid = _venue_table(root)
        _cross_check(table_html, grid)
        notes = _notes(root, snap.url)
        src = SourceRef(URL, TITLE, "nknu.org.pe", "table_page", snap.sha256)
        out: list[Record] = []
        for row in grid[1:]:
            campus_txt, venue_txt, dates, hours = row[0], row[1], row[2], row[3]
            campus = next((v for k, v in CAMPUS.items() if k in campus_txt), None)
            eid = VENUE.get((campus, alias_norm(venue_txt)))
            if eid is None:
                out.append(EntityHint(f"pe_open_hours:hint:{alias_norm(campus_txt + venue_txt)}", f"{campus_txt}{venue_txt}",
                                      " | ".join(row), URL))
                continue
            # one lineage per venue and term: the key carries the term's first day (a term-long closure without dates
            # gets it from the academic calendar, pipeline fill_validity), so next term's table never closes this one's
            key, quote = f"pe_open_hours:{eid}:hours", f"{venue_txt} {dates} {hours}"[:200]
            base = {"notes": notes} if notes else {}
            try:
                lo, hi = parse_roc_range(dates)
            except ValueError:
                if _CLOSED_WORDS.search(dates):
                    out.append(FactRecord(key, eid, "hours", {"closed": True, "reason": dates, **base}, None, None, "term", 1,
                                          False, src, quote, "rule"))
                else:
                    out.append(ParseIssue(key, "date", f"{venue_txt}：開放日期「{dates}」無法解析"))
                continue
            key = f"{key}:{lo}"
            try:
                segs = parse_hours_segments(hours)
            except ValueError:
                out.append(ParseIssue(key, "hours", f"{venue_txt}：開放時間「{hours}」無法解析"))
                continue
            rules = tuple(HoursRule(tuple(s["dows"]), s["opens"], s["closes"]) for s in segs)
            out.append(FactRecord(key, eid, "hours", {"closed": False, **base}, lo, hi, "explicit", 1, False, src, quote, "rule",
                                  rules))
        return out

    def invariants(self, records: list[Record], history: RunHistory, snaps: list[Snapshot]) -> list[Violation]:
        out = standard(self, records, history, snaps)
        if not any(isinstance(r, FactRecord) and r.entity_id == CORE_VENUE for r in records):
            out.append(Violation("missing_core_venue", f"{CORE_VENUE} row not found"))
        return out
