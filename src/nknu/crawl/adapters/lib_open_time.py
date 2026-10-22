"""圖書資訊處「詳細開館時間」(lis.nknu.edu.tw/open-time): regular-semester hours for both libraries and both
愛閱館 I-Read Spaces (one tab pane each), plus the year's closure days listed above the panes.

Regular hours carry no dates on the page: valid_basis='term' (valid until the current term ends, from the calendar).
Vacation hours are "另訂之" (announced separately) and are not guessed.
"""

from __future__ import annotations

import re
from typing import ClassVar

from selectolax.lexbor import LexborHTMLParser

from nknu.core.records import EntityHint, FactRecord, HoursRule, ParseIssue, Record, SourceRef
from nknu.core.rocdate import parse_dows, parse_roc_range, roc_year
from nknu.crawl.adapter import Adapter, ParseError, Target, adapter
from nknu.crawl.fetch import Snapshot
from nknu.crawl.htmltext import element_text, table_grid

URL = "https://lis.nknu.edu.tw/open-time"
TITLE = "詳細開館時間"
SPACES = [("和平圖書館", "nknu.heping.library.main"), ("燕巢圖書館", "nknu.yanchao.library.main"),
          ("和平愛閱館", "nknu.heping.library.iread"), ("燕巢愛閱館", "nknu.yanchao.library.iread")]
_SPAN = re.compile(r"(\d{1,2}:\d{2})~(\d{1,2}:\d{2})")
_CLOSED_LIST = re.compile(r"(\d{3})年閉館日[:：](.*?)(?:[,，]?\s*及其他|$)")
_CLOSED_ITEM = re.compile(r"([^、()（）,，]+)[(（]([\d/~\-]+)[)）]")


def _regular_rules(grid: list[list[str]]) -> tuple[HoursRule, ...] | None:
    header = grid[0]
    days = {}
    for j, h in enumerate(header):
        zh = re.split(r"[A-Za-z]", h, maxsplit=1)[0]
        try:
            days[j] = parse_dows(zh)
        except ValueError:
            continue
    row = next((r for r in grid[1:] if any("Regular Semester" in c for c in r)), None)
    if row is None or not days:
        return None
    by_span: dict[tuple[str, str], list[int]] = {}
    for j, dows in days.items():
        m = _SPAN.search(row[j]) if j < len(row) else None
        if m:
            by_span.setdefault((m.group(1), m.group(2)), []).extend(dows)
    rules = [HoursRule(tuple(sorted(set(d))), o, c) for (o, c), d in by_span.items()]
    return tuple(sorted(rules, key=lambda r: r.dows))


@adapter("lib_open_time", hosts=["lis.nknu.edu.tw"])
class LibOpenTime(Adapter):
    required_selectors: ClassVar[list[str]] = ["main#content"]

    def discover(self, ctx) -> list[Target]:
        return [Target(URL)]

    def parse(self, snap: Snapshot) -> list[Record]:
        if snap.status != 200 or not snap.text:
            return []
        root = LexborHTMLParser(snap.text).css_first("main#content")
        if root is None:
            return []
        src = SourceRef(URL, TITLE, "nknu.org.lis", "table_page", snap.sha256)
        text = element_text(root, URL)
        notes = [ln for ln in text.splitlines() if ln.startswith("※") and "閉館日" not in ln]
        out: list[Record] = []
        found: list[str] = []
        panes = root.css("div.tab-pane")
        if not panes:
            raise ParseError("no tab panes (div.tab-pane) on the open-time page")
        for pane in panes:
            h = pane.css_first("h2")
            heading = (h.text(strip=True) if h else "") or (pane.attributes.get("id") or "")
            eid = next((e for name, e in SPACES if name in heading), None)
            table = pane.css_first("table")
            if eid is None:
                out.append(EntityHint(f"lib_open_time:hint:{heading[:30]}", heading[:60], heading, URL))
                continue
            if table is None:
                raise ParseError(f"pane {heading!r} has no table")
            rules = _regular_rules(table_grid(table))
            if not rules:
                raise ParseError(f"pane {heading!r}: regular-semester row not understood")
            quote = f"{heading} 開館時間 Open Hours of Regular Semester " + "；".join(
                f"{list(r.dows)} {r.opens}~{r.closes}" for r in rules)
            value = {"closed": False} | ({"notes": notes} if notes else {})
            out.append(FactRecord(f"lib_open_time:{eid}:regular", eid, "hours", value, None, None, "term", 1, False, src,
                                  quote[:200], "rule", rules))
            found.append(eid)
        m = _CLOSED_LIST.search(text.replace("\n", " "))
        if m is None:
            out.append(ParseIssue("lib_open_time:closed_list", "date", "找不到「XXX年閉館日」清單"))
            return out
        year = roc_year(int(m.group(1)))
        for name, dates in _CLOSED_ITEM.findall(m.group(2)):
            name = name.strip()
            try:
                lo, hi = parse_roc_range(dates.replace("-", "~"), default_year=year)
            except ValueError:
                out.append(ParseIssue(f"lib_open_time:closed:{name}", "date", f"閉館日「{name}({dates})」無法解析"))
                continue
            for eid in found:
                out.append(FactRecord(f"lib_open_time:{eid}:closed:{lo}", eid, "hours", {"closed": True, "reason": name}, lo,
                                      hi, "explicit", 2, False, src, f"{m.group(1)}年閉館日 {name}({dates})", "rule"))
        return out
