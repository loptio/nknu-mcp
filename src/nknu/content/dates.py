"""Typed dates of one document, read by fixed rules (no model). Every fact keeps the text it was read from and its
source, so an agent sees where a date came from.

Owner decision 11-01 (changed later that day): an attachment only inherits the date of the page it hangs on, else the
server's Last-Modified: its export row's 最后修改时间 (documents.Document.server_date; the step-3 export's, sub-step 4,
spec 2026-11-08 §二 3), else for an export made before 3c33a79 load_server_dates' (written by
nknu.content.serverdates); nothing is read from its own text
(發文日期, 沿革 and 改版 notes are left to the agent, who reads the original). The identity date ("which day is this
document") is the first kind in IDENTITY_ORDER. Periods (受理期間 …) in a notice body never name the document; they give
its status at query time. No date is read from a file name (owner 11-03, with the series those dates ordered; plan
addendum L).

Patterns run on NFKC text: rocdate.norm removes whitespace and so glues 「111.12.21 111學年度」 into one number
(review 11-01 §3.4).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from nknu.core.timeutil import today_tpe


@dataclass(frozen=True)
class DateFact:
    kind: str     # published|inherited|server|period_start|period_end|event_start|event_end
    date: date    # Asia/Taipei calendar date
    raw: str      # the text it was read from, whitespace collapsed
    source: str   # 公告頁日期欄|網頁上寫的日期|列表上的日期|所屬公告|伺服器檔案時間|正文期間|行事曆


IDENTITY_ORDER = ("published", "inherited", "server")
# The export's 日期来源 (since the step-3 export, 3c33a79): which of its rules found a page's 网页上写的日期, spelt as
# archive.export spells them (OWN_FIELD, LIST_ROW, PAGE_WORDS); content keeps its own copy, as ids.PRESS_MARK does.
# '' on a row with no date, and on every row of an export made before 3c33a79 (sub-step 4, spec 2026-11-08 §四)
OWN_FIELD = "详细页的日期栏"  # the platform's own date field on a notice or detail page
# the date the row of a list linking the page states: a notice's row on its list (P75, SSO WebIdx) and, since the
# crawler's 53cf31b (blueprint item 22, part 2), the row of any list, this site's or another site's, that links any
# page (sso.nknu.edu.tw/services's 11,186 activity pages take the dates of the 25 unit sites' 書表 lists linking them; a
# c.nknu row, with no date column, gives its first western date). Trusted for every page and notice as before
# 【Claude】: 12,287 pages and 13 notices newly dated, none losing or changing its date (documents._stated has the
# evidence)
LIST_ROW = "列表上的日期"
PAGE_WORDS = "网页上的日期字眼"  # a labelled date in the page's words
# the sources of a published date: a notice's own date field, c.nknu's header field (#lbl_AddTime, newsview's
# #DataFormView_AddTimeLabel) or, since sub-step 4, any platform's date field the export read on a detail page (日期来源
# OWN_FIELD: the crawler's platform rule chose the field, spec 2026-11-08 §二 2); the date a notice's row states on its
# list (LIST_SOURCE, 日期来源 LIST_ROW); else the date the export read in the page's words (manifest 网页上写的日期,
# kept only after a label of putting the page up or changing it, documents._stated) for a notice no field dates and for
# a page (plan 2026-11-08 T3)
FIELD_SOURCE = "公告頁日期欄"
LIST_SOURCE = "列表上的日期"  # shown to agents: the same characters in Traditional Chinese
PAGE_SOURCE = "網頁上寫的日期"
# the source of an attachment's inherited date when its notice's date is a field's (the notice's date field, or its list
# row's, LIST_SOURCE: sub-step 4); one inherited from a notice the export dated by its page's words keeps PAGE_SOURCE
# (plan 2026-11-08 T3 review: build._facts)
INHERITED_SOURCE = "所屬公告"

_EARLIEST = date(1990, 1, 1)
_AHEAD = timedelta(days=400)
# Every \s* below is followed by a fixed token, never by another optional \s*: PDF text has long runs of whitespace,
# and two \s* side by side backtrack cubically on a run that then fails to match.
_TIME = r"(?:(?:上午|下午|中午|晚上)\s*)?\d{1,2}\s*(?:[:：]\s*\d{2}|時(?:\s*\d{1,2}\s*分)?)"  # 上午9時, 09:00, 23 時 59 分

# periods, on whitespace-collapsed text: a range with one of _PERIOD_KW in the 20 chars before it, or a lone date after
# 截止 (an end only). An end without a year or month takes the start's, rolled forward when it would fall before it.
_PERIOD_KW = re.compile(r"受理|申請|報名|期間|時間|截止")
_KW_SPAN = 20
_SPLIT_YEAR = re.compile(r"(?<!\d)(?<!\d )(1 ?\d ?\d)(?= ?年)")  # 「11 3年」「1 1 3 年」: a ROC year letter-spaced by PDF
_TOKEN = re.compile(
    r"(?<!\d)(?P<y>\d{2,4}) ?年 ?(?P<m>\d{1,2}) ?月 ?(?P<d>\d{1,2}) ?日"
    r"|(?<![\d./])(?P<y2>\d{2,4}) ?(?P<s>[./]) ?(?P<m2>\d{1,2}) ?(?P=s) ?(?P<d2>\d{1,2})(?![\d./])"
    r"|(?<![\d./])(?P<m3>\d{1,2}) ?月 ?(?P<d3>\d{1,2}) ?日"
    r"|(?<![\d./])(?P<m4>\d{1,2}) ?/ ?(?P<d4>\d{1,2})(?![\d./])"
    r"|(?<![\d./])(?P<d5>\d{1,2}) ?日")
_FILL = rf"(?: |[(（][^()（）]{{0,12}}[)）]|上午|下午|中午|晚上|早上|凌晨|當日|{_TIME})"
_GAP = re.compile(rf"(?:{_FILL})*(?:起 ?)?(?:至|到|~|〜|-|—|–)(?: ?起)? ?")
_TAIL = re.compile(rf"(?:{_FILL})*(?:為止|止)?")


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def kept_range() -> tuple[date, date]:
    """The dates worth keeping: 1990-01-01 … today (Asia/Taipei) + 400 days; anything outside is a misread."""
    return _EARLIEST, today_tpe() + _AHEAD


def _latest() -> date:
    return kept_range()[1]


def _make(y: int, m: int, d: int, latest: date) -> date | None:
    """A calendar date in 1990-01-01 … latest (ROC years moved to Gregorian), else None."""
    try:
        got = date(y + 1911 if y < 1911 else y, m, d)
    except ValueError:
        return None
    return got if _EARLIEST <= got <= latest else None


def _ymd(m: re.Match) -> tuple[int | None, int | None, int]:
    for y, mo, d in (("y", "m", "d"), ("y2", "m2", "d2"), (None, "m3", "d3"), (None, "m4", "d4"), (None, None, "d5")):
        if m.group(d):
            return (int(m.group(y)) if y else None), (int(m.group(mo)) if mo else None), int(m.group(d))
    raise AssertionError(m.group())


def _range(a: re.Match, b: re.Match, latest: date) -> tuple[date, date] | None:
    (ya, ma, da), (yb, mb, db) = _ymd(a), _ymd(b)
    if ya is None and yb is None:
        return None
    if mb is None:  # 「9月7日至15日」: the end names a day only (so the start has a year and a month)
        mb, yb = ma, ya
        if db < da:
            mb, yb = (1, ya + 1) if ma == 12 else (ma + 1, ya)
    if yb is None:
        yb = ya + 1 if (mb, db) < (ma, da) else ya
    if ya is None:
        ya = yb - 1 if (ma, da) > (mb, db) else yb
    start, end = _make(ya, ma, da, latest), _make(yb, mb, db, latest)
    return (start, end) if start and end and start <= end else None


def extract_periods(text: str) -> list[DateFact]:
    """period_start / period_end from a date range led by 受理|申請|報名|期間|時間|截止 (source 正文期間); a lone date
    after 截止 gives period_end only. A date without any year in reach is skipped, never guessed."""
    t = _SPLIT_YEAR.sub(lambda m: m.group(1).replace(" ", ""), " ".join(_nfkc(text).split()))
    latest, toks, out, i = _latest(), list(_TOKEN.finditer(t)), [], 0
    while i < len(toks):
        a, b = toks[i], toks[i + 1] if i + 1 < len(toks) else None
        if b is not None and a.group("d5") is None and _GAP.fullmatch(t, a.end(), b.start()):
            i += 2
            if _PERIOD_KW.search(t, max(0, a.start() - _KW_SPAN), a.start()) and (span := _range(a, b, latest)):
                raw = t[a.start(): _TAIL.match(t, b.end()).end()].strip()
                out += [DateFact("period_start", span[0], raw, "正文期間"), DateFact("period_end", span[1], raw, "正文期間")]
            continue
        i += 1
        y, mo, d = _ymd(a)  # a year always comes with a month
        if y is not None and "截止" in t[max(0, a.start() - _KW_SPAN): a.start()] and (when := _make(y, mo, d, latest)):
            out.append(DateFact("period_end", when, t[a.start(): _TAIL.match(t, a.end()).end()].strip(), "正文期間"))
    return list(dict.fromkeys(out))


def load_server_dates(path: Path | None) -> dict[str, date]:
    """url -> Last-Modified day, from the file nknu.content.serverdates writes; {} when there is none (a build without
    the WARC scan). A value that is not an ISO date is skipped."""
    if path is None or not path.is_file():
        return {}
    out = {}
    for url, iso in json.loads(path.read_text(encoding="utf-8")).items():
        try:
            out[url] = date.fromisoformat(iso)
        except (TypeError, ValueError):
            continue
    return out


def identity_date(facts: list[DateFact]) -> DateFact | None:
    """Which day the document is: the first fact of the first kind in IDENTITY_ORDER it has."""
    for kind in IDENTITY_ORDER:
        if same := [f for f in facts if f.kind == kind]:
            return same[0]
    return None
