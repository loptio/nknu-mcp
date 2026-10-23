"""Layout-change guard. Any violation voids the run: nothing is written and the old values stay."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from selectolax.lexbor import LexborHTMLParser

from nknu.core.records import DocRecord, EventRecord, FactRecord, ParseIssue, Record
from nknu.crawl.adapter import RunHistory, Violation

if TYPE_CHECKING:
    from nknu.crawl.adapter import Adapter
    from nknu.crawl.fetch import Snapshot


def selectors_present(snaps: list[Snapshot], default: list[str], pages_are_html: bool = True) -> list[Violation]:
    out = []
    for s in snaps:
        selectors = s.target.meta.get("selectors", default)
        if not pages_are_html or s.status != 200 or not selectors:
            continue
        if not s.text:  # an empty body, or a 200 that is not a text page at all
            out.append(Violation("selector_missing", f"{s.url} answered 200 without an HTML page"))
            continue
        tree = LexborHTMLParser(s.text)
        out += [Violation("selector_missing", f"{sel} missing on {s.url}") for sel in selectors if tree.css_first(sel) is None]
    return out


def count_in_band(n: int, history: RunHistory, lo: float = 0.5, hi: float = 2.0) -> list[Violation]:
    if len(history.counts_7d) < 3:
        return []
    med = statistics.median(history.counts_7d)
    if med and not lo * med <= n <= hi * med:
        return [Violation("count_out_of_band", f"{n} records vs 7-day median {med}")]
    return []


def not_collapsed(n: int, last: int | None) -> list[Violation]:
    if last and n < 0.1 * last:
        return [Violation("collapsed", f"{n} records vs {last} last time: treated as a failed fetch, not deletions")]
    return []


def date_parse_rate(records: list[Record], floor: float = 0.95) -> list[Violation]:
    """Share of records whose dates were read. A list row (DocRecord) without a publication date counts as a failure
    even when the adapter raised no ParseIssue: date cells that moved or were renamed leave every row undated
    (F-sso-row-fields-null)."""
    ok = sum(1 for r in records if isinstance(r, EventRecord)
             or (isinstance(r, FactRecord) and (r.valid_from is not None or r.valid_basis == "term"))
             or (isinstance(r, DocRecord) and not r.partial and r.published_on is not None))
    undated = {r.key for r in records
               if isinstance(r, DocRecord) and not r.partial and not r.gone and r.published_on is None}
    bad = len(undated) + sum(1 for r in records if isinstance(r, ParseIssue) and r.code == "date" and r.key not in undated)
    if ok + bad and ok / (ok + bad) < floor:
        return [Violation("date_parse_rate", f"dates parsed {ok}/{ok + bad}")]
    return []


def record_count(records: list[Record]) -> int:
    return sum(1 for r in records if isinstance(r, (FactRecord, EventRecord)) or (isinstance(r, DocRecord) and not r.partial))


def standard(adapter: Adapter, records: list[Record], history: RunHistory, snaps: list[Snapshot]) -> list[Violation]:
    if snaps and not any(s.status == 200 for s in snaps):
        return [Violation("no_content", "no page answered 200")]
    n = record_count(records)
    return (selectors_present(snaps, adapter.required_selectors, adapter.pages_are_html) + count_in_band(n, history)
            + not_collapsed(n, history.last_count) + date_parse_rate(records))
