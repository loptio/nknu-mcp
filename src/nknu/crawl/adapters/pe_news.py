"""體育室最新消息 (c.nknu.edu.tw/reality/Newslist.aspx + NewsView.aspx?Nid=N).

Every list row becomes a DocRecord (document layer, not facts). Detail pages add the excerpt and attachments; an
announcement that says a venue 暫停開放 on explicitly written dates also yields closure FactRecords (to review).
v1 does not extract opening hours from announcements (design §13.4). 暫停借用 (bookings suspended) is not a closure.
Honor-roll style posts (獲獎名單 …) keep only the masked title: no detail fetch, no excerpt.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import ClassVar
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.core.pii import mask_honor_names
from nknu.core.records import DocRecord, FactRecord, ParseIssue, Record, SourceRef
from nknu.core.rocdate import parse_roc_date, parse_roc_range
from nknu.crawl.adapter import Adapter, RunContext, Target, adapter
from nknu.crawl.fetch import Snapshot
from nknu.crawl.htmltext import element_text
from nknu.crawl.webforms import find_postbacks, postback

BASE = "https://c.nknu.edu.tw/reality/"
LIST_URL = BASE + "Newslist.aspx"
UNIT = "體育室"
DETAIL_CAP = 15
MAX_PAGES = 20
LIST_META = {"kind": "list", "selectors": ["#MasterGrid"]}
DETAIL_META = {"kind": "detail", "selectors": ["#DataFormView"]}

CLOSURE_WORDS = re.compile(r"暫停開放|停止開放|不開放|閉館|不予開放")
HONOR_TITLE = re.compile(r"名單|榮獲|得獎|獲獎|恭賀|賀[!！]|錄取")
# venue mentions -> {campus: (entity id, propagates to contained places)}
VENUES = [
    ("重量訓練室", {"heping": ("nknu.heping.gym.weight-room", False), "yanchao": ("nknu.yanchao.gym.weight-room", False)}),
    ("室外運動場地", {"heping": ("nknu.heping.gym.outdoor", False), "yanchao": ("nknu.yanchao.gym.outdoor", False)}),
    ("游泳池", {"heping": ("nknu.heping.gym.pool", False)}),
    ("桌球", {"yanchao": ("nknu.yanchao.gym.table-tennis", False)}),
    ("撞球", {"yanchao": ("nknu.yanchao.gym.table-tennis", False)}),
    ("體育館", {"heping": ("nknu.heping.gym.main", True)}),
]
_CAMPUS = {"和平": "heping", "燕巢": "yanchao"}
_W = r"(?:\([^)]{1,4}\))?"
RANGE_RE = re.compile(
    rf"(?:\d{{2,3}}年)?\d{{1,2}}月\d{{1,2}}日{_W}(?:\s*(?:至|到|~|-)\s*(?:\d{{2,3}}年)?(?:\d{{1,2}}月)?\d{{1,2}}日{_W})?"
    rf"|\d{{1,2}}/\d{{1,2}}{_W}(?:\s*(?:~|至|-)\s*\d{{1,2}}/\d{{1,2}}{_W})?")


def detail_url(nid: str) -> str:
    return f"{BASE}NewsView.aspx?Nid={nid}"


def _nid(url: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get("Nid") or [None])[0]


def needs_detail(title: str, key: str, known: set[str], full_scan: bool) -> bool:
    if HONOR_TITLE.search(title):
        return False
    return key not in known or (full_scan and bool(CLOSURE_WORDS.search(title)))


def page_targets(html: str, url: str) -> list[Target]:
    out = []
    for target, arg, _ in find_postbacks(html):
        if target == "MasterGrid" and arg.startswith("Page$"):
            action, fields = postback(html, url, target, arg)
            out.append(Target(action, "POST", fields, dict(LIST_META, page=arg)))
    return out


def closure_ranges(text: str, published: date) -> list[tuple[date, date, str] | ParseIssue]:
    """Explicit date ranges in a closure announcement, with the text that follows each as the reason.
    A date without a year takes the announcement's year, or the next year if that lands before publication."""
    matches = list(RANGE_RE.finditer(text))
    out: list[tuple[date, date, str] | ParseIssue] = []
    for i, m in enumerate(matches):
        tail = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        reason = re.split(r"[,，。;；]", tail.strip(" ,，、:："), maxsplit=1)[0].strip()[:40]
        reason = CLOSURE_WORDS.sub("", reason).strip(" ,，、") or "公告暫停開放"
        expr = m.group(0)
        explicit_year = "年" in expr
        parsed = None
        for year in ([None] if explicit_year else [published.year, published.year + 1]):
            try:
                lo, hi = parse_roc_range(expr, default_year=year)
            except ValueError:
                continue
            if explicit_year or lo >= published - timedelta(days=30):
                parsed = (lo, hi)
                break
        if parsed is None:
            out.append(ParseIssue(f"range:{expr}", "date", f"停開日期「{expr}」無法解析或星期不符"))
        else:
            out.append((parsed[0], parsed[1], reason))
    return out


def _venues(title: str) -> list[tuple[str, bool]] | None:
    hits = [(word, by_campus) for word, by_campus in VENUES if word in title]
    if not hits:
        return None
    campus = next((c for w, c in _CAMPUS.items() if w in title), None)
    if campus is None:  # infer from unambiguous co-mentions, e.g. 體育館(含室外運動場地) -> 和平
        sure = {next(iter(m)) for _, m in hits if len(m) == 1}
        campus = sure.pop() if len(sure) == 1 else None
    out = []
    for _, by_campus in hits:
        pick = by_campus.get(campus) if campus else (next(iter(by_campus.values())) if len(by_campus) == 1 else None)
        if pick and pick not in out:
            out.append(pick)
    return out or None


@adapter("pe_news", hosts=["c.nknu.edu.tw"])
class PeNews(Adapter):
    required_selectors: ClassVar[list[str]] = []  # per page kind, via Target.meta["selectors"]

    def fetch_all(self, ctx: RunContext) -> list[Snapshot]:
        first = ctx.fetch(Target(LIST_URL, meta=LIST_META))
        snaps = [first]
        if ctx.full_scan and first.text:
            seen, html, url = {"Page$1"}, first.text, first.url
            for _ in range(MAX_PAGES):
                nxt = [t for t in page_targets(html, url) if t.meta["page"] not in seen]
                if not nxt:
                    break
                s = ctx.fetch(nxt[0])
                seen.add(nxt[0].meta["page"])
                snaps.append(s)
                if not s.text:
                    break
                html, url = s.text, s.url
        rows = [r for s in snaps for r in self.parse(s) if isinstance(r, DocRecord)]
        wanted = [r for r in rows if needs_detail(r.title, r.key, ctx.known_keys, ctx.full_scan)][:DETAIL_CAP]
        snaps += [ctx.fetch(Target(r.url, meta=DETAIL_META)) for r in wanted]
        return snaps

    def parse(self, snap: Snapshot) -> list[Record]:
        if snap.target.meta.get("kind") == "detail":
            return self._detail(snap)
        if snap.status != 200 or not snap.text:
            return []
        grid = LexborHTMLParser(snap.text).css_first("#MasterGrid")
        if grid is None:
            return []
        out: list[Record] = []
        for tr in grid.css("tr"):
            a = tr.css_first("a[href*='NewsView']")
            if a is None:
                continue
            nid = _nid(urljoin(BASE, a.attributes.get("href") or ""))
            cells = table_grid_row(tr)
            title = a.text(strip=True)
            honor = bool(HONOR_TITLE.search(title))
            try:
                published = parse_roc_date(cells[-1]) if cells else None
            except ValueError:
                published = None
                out.append(ParseIssue(f"pe_news:{nid}", "date", f"公告日期「{cells[-1] if cells else ''}」無法解析"))
            out.append(DocRecord(f"pe_news:{nid}", nid, mask_honor_names(title) if honor else title, detail_url(nid), UNIT,
                                 cells[2] if len(cells) > 2 else None, published, pii_level="personal" if honor else "none"))
        return out

    def _detail(self, snap: Snapshot) -> list[Record]:
        nid = _nid(snap.target.url) or _nid(snap.url) or ""
        key = f"pe_news:{nid}"
        if snap.status == 404:
            return [DocRecord(key, nid, "", detail_url(nid), gone=True)]
        if snap.status != 200 or not snap.text:
            return []
        tree = LexborHTMLParser(snap.text)
        if tree.css_first("#DataFormView") is None:
            return []
        title_n, body_n = tree.css_first("#DataFormView_TitleLabel"), tree.css_first("#DataFormView_ContentsLabel")
        when_n, files_n = tree.css_first("#DataFormView_AddTimeLabel"), tree.css_first("#DataFormView_FileLinkLabel")
        title = element_text(title_n, BASE).replace("\n", " ") if title_n else ""
        body = element_text(body_n, BASE).replace("\n", " ") if body_n else ""
        try:
            published = parse_roc_date((when_n.text(strip=True) if when_n else "").split(" ")[0])
        except ValueError:
            return [ParseIssue(key, "date", "公告日期欄位無法解析"), DocRecord(key, nid, title, detail_url(nid), partial=True,
                                                                              excerpt=body[:200] or None)]
        files = tuple(re.split(r"\s*\(", unicodedata.normalize("NFKC", a.text(strip=True)))[0] for a in files_n.css("a")) \
            if files_n else ()
        out: list[Record] = [DocRecord(key, nid, title, detail_url(nid), partial=True, excerpt=body[:200] or None,
                                       attachments=files)]
        if not CLOSURE_WORDS.search(title + body):
            return out
        venues = _venues(title)
        if venues is None:
            out.append(ParseIssue(key, "closure_unlinked", "停開公告對應不到已知場館，只存成公告"))
            return out
        src = SourceRef(detail_url(nid), title, "nknu.org.pe", "announcement", snap.sha256, published, f"pe_news:{nid}")
        for item in closure_ranges(body, published):
            if isinstance(item, ParseIssue):
                out.append(ParseIssue(f"{key}:{item.key}", item.code, item.message))
                continue
            lo, hi, reason = item
            for eid, propagates in venues:
                out.append(FactRecord(f"{key}:{eid}:{lo}", eid, "hours", {"closed": True, "reason": reason}, lo, hi,
                                      "explicit", 2, propagates, src, f"{lo}~{hi} {reason}"[:200], "rule"))
        return out


def table_grid_row(tr) -> list[str]:
    """Cell texts of one <tr> (the list grid has no row/col spans inside data rows)."""
    return [element_text(td, BASE).replace("\n", " ") for td in tr.iter() if td.tag in ("td", "th")]

