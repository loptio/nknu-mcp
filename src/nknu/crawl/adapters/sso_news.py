"""SSO 公告中心 (sso.nknu.edu.tw/News/SubjectList.aspx?cat=…), 7 categories, ASP.NET WebForms paging.

Paging replays the page's own form state with __EVENTTARGET=gvFull / __EVENTARGUMENT=Page$N (no browser).
Normal runs read the first 3 pages of every category; the first run of each day reads every page, and only those
full scans count towards "missing 3 times = withdrawn". Rows are keyed by the stable 40-hex UNID.
Honor-roll content keeps only the masked title, no detail fetch, no excerpt: every GOOD (榮譽典範) row, and a row of any
other category whose title reads as honor content (core.pii.is_honor_title: the same 恭賀 post cross-posted under
徵才就業, F-sso-crosspost-honor).

Detail pages (the excerpt) are read for every listed row that has none stored yet, at most DETAIL_CAP per run: rows
seen for the first time first, then the backlog newest first (F-excerpt-backlog). A detail page that fails costs only
its own excerpt (a ParseIssue) and is asked for again after the others; a failing list page still voids the run.
"""

from __future__ import annotations

import re
from datetime import date
from typing import ClassVar
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.core.pii import is_honor_title, mask_honor_names
from nknu.core.records import DocRecord, ParseIssue, Record
from nknu.core.rocdate import parse_roc_date
from nknu.crawl.adapter import Adapter, ParseError, RunContext, Target, adapter
from nknu.crawl.fetch import Snapshot
from nknu.crawl.htmltext import element_text
from nknu.crawl.webforms import find_postbacks, postback

BASE = "https://sso.nknu.edu.tw/News/SubjectList.aspx"
DETAIL = "https://sso.nknu.edu.tw/News/lastNews.aspx"
CATEGORIES = ["SCHOOLADMIN", "ENTRANCEEXAM", "CAMPUS", "Lights", "LEARNING", "GOOD", "JOB"]
HONOR_CATEGORY = "GOOD"
PARTIAL_PAGES = 3
MAX_PAGES = 50  # a pager that never ends means the site changed: void the run instead of guessing
DETAIL_CAP = 30
DETAIL_META = {"kind": "detail"}  # no run-voiding selector: _detail checks its own page (F-detail-void-freeze)
DETAIL_BODY = "#ctl00_phMain_lbContent"
_UNID = re.compile(r"UNID=([0-9A-Fa-f]{40})")


def list_meta(cat: str, page: int = 1) -> dict:
    return {"kind": "list", "selectors": ["#gvFull"], "cat": cat, "page": page}


def detail_url(unid: str) -> str:
    return f"{DETAIL}?UNID={unid}"


def next_page_target(html: str, url: str, cat: str, current: int) -> Target | None:
    want = f"Page${current + 1}"
    if not any(t == "gvFull" and a == want for t, a, _ in find_postbacks(html)):
        return None
    action, fields = postback(html, url, "gvFull", want)
    return Target(action, "POST", fields, list_meta(cat, current + 1))


def _date(s: str | None) -> date | None:
    return parse_roc_date(s) if s else None


@adapter("sso_news", hosts=["sso.nknu.edu.tw"])
class SsoNews(Adapter):
    required_selectors: ClassVar[list[str]] = []  # per page kind, via Target.meta["selectors"]

    def fetch_all(self, ctx: RunContext) -> list[Snapshot]:
        snaps: list[Snapshot] = []
        limit = MAX_PAGES if ctx.full_scan else PARTIAL_PAGES
        for cat in CATEGORIES:
            s = ctx.fetch(Target(f"{BASE}?cat={cat}", meta=list_meta(cat)))
            snaps.append(s)
            page = 1
            while s.text and page < limit:
                nxt = next_page_target(s.text, s.url, cat, page)
                if nxt is None:
                    break
                s = ctx.fetch(nxt)
                snaps.append(s)
                page += 1
            else:
                if ctx.full_scan and s.text and next_page_target(s.text, s.url, cat, page) is not None:
                    raise ParseError(f"{cat}: pager did not end within {MAX_PAGES} pages")
        rows: dict[str, DocRecord] = {}
        for r in (r for s in snaps for r in self.parse(s) if isinstance(r, DocRecord)):
            rows.setdefault(r.key, r)
        todo = [r for r in rows.values() if r.pii_level != "personal" and r.key not in ctx.down_keys
                and r.key not in ctx.detailed_keys]
        # first seen this run, then the backlog, then pages that failed last time; newest first within each
        todo.sort(key=lambda r: (r.key in ctx.failed_detail_keys, r.key in ctx.known_keys,
                                 -(r.published_on or date.min).toordinal()))
        return snaps + ctx.fetch_details([Target(r.url, meta=DETAIL_META) for r in todo[:DETAIL_CAP]])

    def parse(self, snap: Snapshot) -> list[Record]:
        if snap.target.meta.get("kind") == "detail":
            return self._detail(snap)
        if snap.status != 200 or not snap.text:
            return []
        tree = LexborHTMLParser(snap.text)
        grid = tree.css_first("#gvFull")
        if grid is None:
            return []
        good = snap.target.meta.get("cat") == HONOR_CATEGORY
        out: list[Record] = []
        for a in grid.css("a[id$='_linkSubject_short']"):
            prefix = a.attributes["id"][: -len("linkSubject_short")]

            def field(name: str, prefix=prefix) -> str | None:
                n = tree.css_first(f"#{prefix}{name}")
                return n.text(strip=True) if n is not None else None

            href = urljoin(snap.url, a.attributes.get("href") or "")
            m = _UNID.search(href)
            if m is None:
                raise ParseError(f"row {prefix} has no UNID link: the list layout changed")
            unid = m.group(1).upper()
            title = a.text(strip=True) or (a.attributes.get("title") or "")
            honor = good or is_honor_title(title)
            key = f"sso_news:{unid}"
            try:
                published, until = _date(field("StartDate")), _date(field("EndDate"))
            except ValueError:
                published = until = None
                out.append(ParseIssue(key, "date", f"公告日期「{field('StartDate')}~{field('EndDate')}」無法解析"))
            out.append(DocRecord(key, unid, mask_honor_names(title) if honor else title, detail_url(unid), field("DeptName"),
                                 field("Category"), published, until, pii_level="personal" if honor else "none"))
        return out

    def _detail(self, snap: Snapshot) -> list[Record]:
        unid = ((parse_qs(urlsplit(snap.target.url).query).get("UNID") or [""])[0]).upper()
        key = f"sso_news:{unid}"
        if snap.status == 404:
            return [DocRecord(key, unid, "", detail_url(unid), gone=True)]
        if snap.status != 200 or not snap.text:
            why = snap.target.meta.get("error") or (f"HTTP {snap.status}" if snap.status != 200 else "空白頁")
            return [ParseIssue(key, "detail", f"公告詳情頁沒讀到（{why}），摘錄下一輪再抓")]
        tree = LexborHTMLParser(snap.text)
        body = tree.css_first(DETAIL_BODY)
        if body is None:
            return [ParseIssue(key, "detail", f"公告詳情頁找不到內文欄位 {DETAIL_BODY}（公告已刪除，或詳情頁改版），摘錄下一輪再抓")]
        files = tree.css_first("#ctl00_phMain_lbFileLink")
        text = element_text(body, snap.url).replace("\n", " ")
        attachments = tuple(a.text(strip=True) for a in files.css("a")) if files else ()
        # "" = read, nothing to quote (attachment-only notices): not asked for again
        return [DocRecord(key, unid, "", detail_url(unid), partial=True, excerpt=text[:200] if text.strip() else "",
                          attachments=attachments)]
