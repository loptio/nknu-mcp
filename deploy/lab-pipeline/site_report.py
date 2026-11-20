"""Per-site report (lab_pc kit; plan 2026-11-08 T5; spec 2026-11-08 §二「怎么看好不好」): one row per site of one built
database: its documents by kind, its pages without text, its documents without a date, and how many of SAMPLE sampled
documents a keyword search for their own title finds among the first TOP results. It flags what looks wrong and never
blocks: exit 0 whatever it finds (2 only for a database it cannot read or an --out it must not write). No model, no
network; the database is opened read-only.

Sites are docs.site and the sites table of plan 2026-11-08 T4 (name, data_as_of, missing pages and files); a database
without docs.site (the skeleton, the 11-04 and 11-08 builds) is one site, meta site. Counted per site:
- documents by kind_class, and how many of them are background material (docs.tier);
- pages without text: pages and notices whose text is empty (chars 0), found by their title only;
- documents without a date: notices, pages and attachments with no identity date, which results show as 無日期 (an
  event's calendar days stand in for its date; an outside link has none to show);
- the title self-search: SAMPLE notices, pages and attachments (those whose title has a word to search for), drawn by
  random.Random(f"{SEED}:{site}") so every build of the same documents draws the same ones, each searched by its title
  with nknu.retrieval.search.search_docs (aliases from meta, as the server searches); found when it is among the first
  TOP. A miss is listed with its place within the first DEEP;
- the school's deleted notices the build left out (sites.deleted_notices, pipeline-6; owner 11-09, blueprint item 15,
  「照建议定」: 「建议不收、在每站报告里记个数」): shown as 学校删掉的公告（不收）, never flagged; a database built before
  it has no such column and shows —;
- the documents read again whole (sites.under_read, pipeline-7; owner 11-09, blueprint item 22, 「照这个做」: 「每站报告加
  『读到的字远少于整页』的页数」): their content area gave much less than their own words (nknu.content.assemble's
  under_read), shown as 读到的字远少于整页（改读整页）, never flagged: a site with many is one whose platform profile's
  content area misses its pages (the crawler session's to fix); a database built before it shows —.

The title searches are most of the report's time (about 0.8 s each over 765k chunks: 146 sites of 20 make forty
minutes in one process, 11-18), so they are shared among --jobs processes of this script (--worker: the picks on
stdin, their places on stdout), each with its own read-only connection; --jobs 1 searches in this process as before.
What is drawn and what is found do not depend on the number of jobs (11-19).

    python research/pipeline_2026-11-02/lab_pc/site_report.py --db research/pipeline_2026-11-02/aca.db \\
        --out research/pipeline_2026-11-02/site_report.md [--sample 20] [--top 10] [--today YYYY-MM-DD] [--jobs N]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from nknu.core.textnorm import tokenize
from nknu.core.timeutil import today_tpe
from nknu.retrieval.search import search_docs
from nknu.totext.cache import write_atomic

KIT = Path(__file__).resolve().parent  # deploy/lab-pipeline (11-19: the programs are in the repository)
REPO = next((d for d in KIT.parents if (d / "src" / "nknu").is_dir()), KIT.parents[1])  # /work on the lab PC
SAMPLE = 20  # documents a site's title self-search draws (plan 2026-11-08 T5)
TOP = 10  # found: among the first TOP results (plan 2026-11-08 T5)
DEEP = 50  # 【Claude】 how far a miss's own place is looked for
SEED = "1108"  # 【Claude】
SEARCHED = ("notice", "page", "attachment")  # 【Claude】 a site's own documents: not the calendar's events (one
#                                              calendar, 教务处's) nor outside links (no text of their own)
PAGES = ("notice", "page")
BACKGROUND = "background"  # docs.tier (nknu.content.schema.BACKGROUND)
SELF_FLAG = 0.7  # 【Claude】 flagged: fewer than this share of the sample found
EMPTY_FLAG = 0.2  # 【Claude】 flagged: more than this share of the pages without text
NO_DATE_FLAG = 0.5  # 【Claude】 flagged: more than this share of the documents without a date
MIN_SHARE = 5  # 【Claude】 a share is flagged over at least this many documents
MISSES_SHOWN = 5
# spec 2026-11-16 §五 (plan 2026-11-16 T4), four more things a site's row says, all 【Claude】, measured on the official
# library of 11-15 (research/pipeline_2026-11-02/etl_review_1115/check.md): one title on SAME_TITLE or more of a
# site's own documents (91 titles, 4,533 documents, 33 sites: 「檔案連結」 395, 「相關檔案」 233); more than
# OPENING_FLAG of its pages with text opening with the same OPENING_CHARS characters, over at least OPENING_PAGES
# pages (a platform's menu read as text: one site, 72 of 153); FRAGMENT_MIN or more files read as scattered
# characters (over FRAGMENT_FLAG of at least FRAGMENT_LINES lines hold one character: 1,028 files, a diagram or a
# rotated table); and how many of its files the build left undated for a day too many of them share (only counted).
SAME_TITLE = 15
OPENING_CHARS = 80
OPENING_MIN = 40  # an opening shorter than this is no opening to compare
OPENING_FLAG = 0.3
OPENING_PAGES = 20
FRAGMENT_FLAG = 0.35
FRAGMENT_LINES = 20
FRAGMENT_MIN = 5
FLAG_WORDS = {"none": "没有文件", "self": "标题自搜", "empty": "网页没文字", "no_date": "没日期", "same_title": "同名标题",
              "same_opening": "开头相同", "fragments": "碎字附件"}  # a reminder's kind -> its name in the report
KIND_WORDS = {"notice": "公告", "page": "网页", "attachment": "附件", "event": "行事历", "link": "外部链接"}


@dataclass
class SiteRow:
    site: str
    name: str = ""
    unit: str = ""
    data_as_of: str = ""
    missing_pages: int | None = None
    missing_files: int | None = None
    deleted_notices: int | None = None  # sites.deleted_notices (pipeline-6); None in a database before it
    under_read: int | None = None  # sites.under_read (pipeline-7, item 22); None in a database before it
    kinds: dict[str, int] = field(default_factory=dict)  # kind_class -> documents
    background: int = 0
    pages: int = 0  # pages and notices
    empty_pages: int = 0
    dated: int = 0  # notices, pages and attachments
    no_date: int = 0
    picked: list[str] = field(default_factory=list)  # the doc_ids searched by their titles, in that order
    found: int = 0
    misses: list[tuple[str, str, int | None]] = field(default_factory=list)  # doc_id, title, place (None: past DEEP)
    flags: list[str] = field(default_factory=list)
    flag_kinds: list[str] = field(default_factory=list)  # FLAG_WORDS' keys, one per flag (spec 2026-11-16 §五)
    same_title: int = 0  # its notices, pages and attachments under a title SAME_TITLE or more of them share
    same_title_top: tuple[str, int] | None = None  # the most shared of those titles
    opening: tuple[int, int] | None = None  # (pages of the most common opening, pages with an opening); None: none
    fragments: int = 0  # attachments read as scattered characters
    spike_dates: int | None = None  # files the build left undated (meta date_spikes_json); None in a database before

    @property
    def sampled(self) -> int:
        return len(self.picked)


@dataclass(frozen=True)
class _Doc:
    doc_id: str
    kind: str
    title: str
    chars: int
    ident: str | None
    tier: str


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {k: v for k, v in conn.execute("SELECT key, value FROM meta")}


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _picks(row: SiteRow, docs: list[_Doc], sample: int, seed: str) -> list[_Doc]:
    """The documents of a site whose titles are searched: the same ones for the same documents, whatever else runs."""
    candidates = [d for d in docs if d.kind in SEARCHED and tokenize(d.title)]  # in doc_id order
    return random.Random(f"{seed}:{row.site}").sample(candidates, min(max(sample, 0), len(candidates)))


def places_here(conn: sqlite3.Connection, picks: list[tuple[str, str]], *, today: date, aliases: list,
                top: int) -> dict[str, int | None]:
    """{doc_id: the place of the document among the first max(top, DEEP) results of a search for its title, or None}."""
    out: dict[str, int | None] = {}
    for doc_id, title in picks:
        hits = [h.doc_id for h in search_docs(conn, title, today=today, aliases=aliases, limit=max(top, DEEP)).hits]
        out[doc_id] = hits.index(doc_id) + 1 if doc_id in hits else None
    return out


def places_by_workers(db: Path, picks: list[tuple[str, str]], *, today: date, top: int, jobs: int) -> dict:
    """places_here shared among jobs processes of this script (--worker), every jobs-th pick to each; a worker that
    fails raises: the report is never written from half the searches."""
    groups = [g for g in (picks[i::jobs] for i in range(max(jobs, 1))) if g]
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--db", str(db), "--today", today.isoformat(),
           "--top", str(top)]

    def run(group: list[tuple[str, str]]) -> dict:
        done = subprocess.run(cmd, input=json.dumps(group, ensure_ascii=False), capture_output=True, text=True,
                              encoding="utf-8", check=False)
        if done.returncode != 0:
            raise RuntimeError(f"a title-search worker ended with {done.returncode}: {done.stderr[-300:]}")
        return json.loads(done.stdout)

    out: dict = {}
    with ThreadPoolExecutor(max_workers=len(groups) or 1) as pool:
        for part in pool.map(run, groups):
            out.update(part)
    return out


def _flags(row: SiteRow) -> list[tuple[str, str]]:
    """(kind, words) of each reminder of a site, kind one of FLAG_WORDS."""
    if not sum(row.kinds.values()):
        return [("none", "没有文件")]
    out = []
    if row.sampled and row.found / row.sampled < SELF_FLAG:
        out.append(("self", f"标题自搜只找到 {_part(row.found, row.sampled)}"))
    if row.pages >= MIN_SHARE and row.empty_pages / row.pages > EMPTY_FLAG:
        out.append(("empty", f"网页没文字 {_part(row.empty_pages, row.pages)}"))
    if row.dated >= MIN_SHARE and row.no_date / row.dated > NO_DATE_FLAG:
        out.append(("no_date", f"没日期 {_part(row.no_date, row.dated)}"))
    if row.same_title and row.same_title_top:
        title, n = row.same_title_top
        out.append(("same_title", f"同名标题 {row.same_title:,} 份（最多的「{' '.join(title.split())}」{n:,} 份）"))
    if row.opening and row.opening[1] >= OPENING_PAGES and row.opening[0] / row.opening[1] > OPENING_FLAG:
        out.append(("same_opening", f"开头相同 {_part(*row.opening)}"))
    if row.fragments >= FRAGMENT_MIN:
        out.append(("fragments", f"碎字附件 {row.fragments:,} 份"))
    return out


def _fragmented(text: str) -> bool:
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    return len(lines) >= FRAGMENT_LINES and sum(len(x) == 1 for x in lines) / len(lines) > FRAGMENT_FLAG


def _text_checks(conn: sqlite3.Connection, rows: dict[str, SiteRow], site_col: str, args: tuple) -> None:
    """Each site's opening and fragments, in one pass over the texts of its pages, notices and attachments."""
    openings: dict[str, Counter[str]] = {}
    for site, kind, text in conn.execute(
            f"SELECT {site_col}, kind_class, text FROM docs WHERE text != '' AND kind_class IN ('page', 'notice', "
            "'attachment')", args):
        if site not in rows:
            continue
        if kind == "attachment":
            rows[site].fragments += _fragmented(text)
        elif len(key := "".join(text[:400].split())[:OPENING_CHARS]) >= OPENING_MIN:
            openings.setdefault(site, Counter())[key] += 1
    for site, seen in openings.items():
        rows[site].opening = (seen.most_common(1)[0][1], sum(seen.values()))


def site_rows(conn: sqlite3.Connection, *, today: date, sample: int = SAMPLE, top: int = TOP, seed: str = SEED,
              places: Callable[[list[tuple[str, str]]], dict] | None = None) -> list[SiteRow]:
    """One row per site, by site key: every site of the sites table and of docs.site (or meta site alone). places:
    how the picked titles are searched ((doc_id, title) pairs -> {doc_id: place}); None: in this process."""
    meta, cols = _meta(conn), _columns(conn, "docs")
    per_site = "site" in cols
    rows: dict[str, SiteRow] = {}
    if not per_site:
        site = meta.get("site") or ""
        rows[site] = SiteRow(site, meta.get("name") or "", "", meta.get("data_as_of") or "",
                             _int(meta.get("missing_pages")), _int(meta.get("missing_files")))
    elif site_cols := _columns(conn, "sites"):
        deleted = "deleted_notices" if "deleted_notices" in site_cols else "NULL"  # pipeline-6 (owner 11-09, item 15)
        whole = "under_read" if "under_read" in site_cols else "NULL"  # pipeline-7 (owner 11-09, item 22)
        for site, name, unit, as_of, pages, files, gone, again in conn.execute(
                f"SELECT site, name, unit, data_as_of, missing_pages, missing_files, {deleted}, {whole} FROM sites"):
            rows[site] = SiteRow(site, name or "", unit or "", as_of or "", _int(pages), _int(files), _int(gone),
                                 _int(again))
    tier = "tier" if "tier" in cols else "''"
    site_col, args = ("site", ()) if per_site else ("?", (next(iter(rows)),))
    by_site: dict[str, list[_Doc]] = {}
    for doc_id, kind, title, chars, ident, t, site in conn.execute(
            f"SELECT doc_id, kind_class, title, chars, ident_date, {tier}, {site_col} FROM docs ORDER BY doc_id", args):
        by_site.setdefault(site, []).append(_Doc(doc_id, kind, title or "", chars or 0, ident, t or ""))
        rows.setdefault(site, SiteRow(site))
    aliases = json.loads(meta.get("aliases_json") or "[]")
    _text_checks(conn, rows, site_col, args)
    try:
        spikes = json.loads(meta["date_spikes_json"]).get("by_site", {}) if "date_spikes_json" in meta else None
    except (ValueError, AttributeError):
        spikes = None
    picks: dict[str, list[_Doc]] = {}
    for site, row in rows.items():
        docs = by_site.get(site, [])
        shared = [(t, n) for t, n in Counter(d.title for d in docs if d.kind in SEARCHED and d.title.strip()).most_common()
                  if n >= SAME_TITLE]
        row.same_title, row.same_title_top = sum(n for _, n in shared), shared[0] if shared else None
        row.spike_dates = int(spikes.get(site, 0)) if spikes is not None else None
        row.kinds = dict(Counter(d.kind for d in docs))
        row.background = sum(d.tier == BACKGROUND for d in docs)
        pages = [d for d in docs if d.kind in PAGES]
        row.pages, row.empty_pages = len(pages), sum(not d.chars for d in pages)
        dated = [d for d in docs if d.kind in SEARCHED]
        row.dated, row.no_date = len(dated), sum(not d.ident for d in dated)
        picks[site] = _picks(row, docs, sample, seed)
    flat = [(d.doc_id, d.title) for chosen in picks.values() for d in chosen]
    place = places(flat) if places is not None else places_here(conn, flat, today=today, aliases=aliases, top=top)
    for site, row in rows.items():
        for d in picks[site]:
            at = place.get(d.doc_id)
            row.picked.append(d.doc_id)
            if at is not None and at <= top:
                row.found += 1
            else:
                row.misses.append((d.doc_id, d.title, at))
        found = _flags(row)
        row.flag_kinds, row.flags = [k for k, _ in found], [words for _, words in found]
    return [rows[site] for site in sorted(rows)]


def checks(rows: list[SiteRow], *, db: Path, meta: dict[str, str], today: date, new: list[tuple[str, str]]) -> dict:
    """The report as data (--checks: site_checks.json; spec 2026-11-16 §五: what a dashboard reads and what the next
    build compares with): each site's reminder kinds and the numbers behind them, and what is new against --prev."""
    return {"db": str(db), "built_at": meta.get("built_at") or "", "today": today.isoformat(),
            "sites": {r.site: {"flags": r.flag_kinds, "docs": sum(r.kinds.values()), "same_title": r.same_title,
                               "same_opening": list(r.opening) if r.opening else None, "fragments": r.fragments,
                               "spike_dates": r.spike_dates, "no_date": [r.no_date, r.dated],
                               "empty_pages": [r.empty_pages, r.pages], "self": [r.found, r.sampled]} for r in rows},
            "new": [list(pair) for pair in new]}


def new_flags(rows: list[SiteRow], prev: dict | None) -> list[tuple[str, str]]:
    """(site, kind) of each reminder the previous checks file did not hold for that site, in the rows' order; [] with
    no previous file. A site the previous file lacks is new with all its reminders."""
    if prev is None:
        return []
    before = prev.get("sites") or {}
    return [(r.site, k) for r in rows for k in r.flag_kinds if k not in (before.get(r.site) or {}).get("flags", [])]


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _part(n: int, total: int) -> str:
    return f"{n:,} / {total:,}" if total else "—"


def _kinds(row: SiteRow) -> str:
    order = [k for k in KIND_WORDS if row.kinds.get(k)] + sorted(k for k in row.kinds if k not in KIND_WORDS)
    words = "、".join(f"{KIND_WORDS.get(k, k)} {row.kinds[k]:,}" for k in order) or "没有"
    return words + (f"（背景 {row.background:,}）" if row.background else "")


def _missing(row: SiteRow) -> str:
    if row.missing_pages is None and row.missing_files is None:
        return "—"
    return f"网页 {row.missing_pages if row.missing_pages is not None else '?'}、附件 " \
           f"{row.missing_files if row.missing_files is not None else '?'}"


def render(rows: list[SiteRow], *, db: Path, meta: dict[str, str], today: date, sample: int, top: int,
           new: list[tuple[str, str]] | None = None, prev_note: str = "") -> str:
    """new: new_flags' pairs when a previous checks file was given (None: none was, and the section is left out);
    prev_note: why the previous file could not be compared with."""
    flagged = sum(bool(r.flags) for r in rows)
    out = ["# 每个网站的检查（只提醒，不挡建库）", "",
           (f"- 资料库：`{db}`（建于 {meta.get('built_at') or '?'}）；今天 {today.isoformat()}；网站 {len(rows)} 个，"
            f"有提醒的 {flagged} 个。"),
           (f"- 标题自搜：每个网站抽 {sample} 份公告、网页、附件（标题里要有字可搜），拿它的标题做关键词搜索（不用模型），"
            f"看它自己在不在前 {top} 名。"),
           "- 网页没文字：正文是空的网页和公告，只能按标题找到。没日期：结果里显示「無日期」的公告、网页、附件。",
           ("- 学校删掉的公告（不收）：学校删掉、只剩网站模板的公告，建库时不收（用户 11-09 定）；只记个数，不算提醒。"
            "旧的资料库没有这一栏，写「—」。"),
           ("- 读到的字远少于整页（改读整页）：按平台设定对上的正文区几乎没字、网页上却有很多它自己的字（不算全站每页都有的"
            "页首、选单、页尾），建库时改读整页、去掉全站每页都有的行（用户 11-09 定）；只记个数，不算提醒。数字多的网站，"
            "平台设定的正文区要补（爬虫 session 的档）。旧的资料库没有这一栏，写「—」。"),
           (f"- 同名标题：同一个网站 {SAME_TITLE} 份以上的公告、网页、附件共用一个标题（「相關檔案」「檔案連結」这类）。"
            f"开头相同：有字的网页里，开头 {OPENING_CHARS} 个字一样的那一批占多少（平台的选单被读成正文时会这样）。"
            f"碎字附件：{FRAGMENT_LINES} 行以上、超过 {FRAGMENT_FLAG:.0%} 的行只有一个字的附件（图表、转了方向的表）。"
            "作废的扎堆日期：同站同一天太多附件的档案时间相同，建库时不当日期用的份数；只记个数，不算提醒，旧的资料库写「—」。"),
           (f"- ⚠ 提醒：标题自搜找到不到 {SELF_FLAG:.0%}；网页没文字超过 {EMPTY_FLAG:.0%}；没日期超过 "
            f"{NO_DATE_FLAG:.0%}（比例至少要 {MIN_SHARE} 份才算）；没有文件；有同名标题；开头相同的超过 "
            f"{OPENING_FLAG:.0%}（至少 {OPENING_PAGES} 页）；碎字附件 {FRAGMENT_MIN} 份以上。只是提醒，不挡建库。"), ""]
    if new is not None or prev_note:
        out += ["## 这次新出现的提醒", ""]
        if prev_note:
            out.append(prev_note)
        elif not new:
            out.append("没有：和上一次建库比，每个网站的提醒种类都一样或更少。")
        names = {r.site: r.name for r in rows}
        words = {(r.site, k): w for r in rows for k, w in zip(r.flag_kinds, r.flags, strict=True)}
        for site, kind in new or []:
            out.append(f"- {site}" + (f"（{names[site]}）" if names.get(site) else "") + f"：{words[(site, kind)]}")
        out.append("")
    out += ["## 每个网站", "",
            ("| 网站 | 名称 | 文件 | 网页没文字 | 没日期 | 标题自搜 | 缺漏 | 学校删掉的公告（不收） | "
             "读到的字远少于整页（改读整页） | 同名标题 | 开头相同 | 碎字附件 | 作废的扎堆日期 | 资料截至 | 提醒 |"),
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        flags = "⚠ " + "；".join(r.flags) if r.flags else ""
        out.append(f"| {_cell(r.site) or '（没写）'} | {_cell(r.name)} | {_kinds(r)} | {_part(r.empty_pages, r.pages)} | "
                   f"{_part(r.no_date, r.dated)} | {_part(r.found, r.sampled)} | {_missing(r)} | "
                   f"{r.deleted_notices if r.deleted_notices is not None else '—'} | "
                   f"{r.under_read if r.under_read is not None else '—'} | "
                   f"{r.same_title or '—'} | {_part(*r.opening) if r.opening else '—'} | {r.fragments or '—'} | "
                   f"{r.spike_dates if r.spike_dates is not None else '—'} | "
                   f"{_cell(r.data_as_of) or '—'} | {flags} |")
    missed = [r for r in rows if r.misses]
    out += ["", f"## 标题自搜没找到的（每个网站最多 {MISSES_SHOWN} 份）", ""]
    if not missed:
        out.append("没有。")
    for r in missed:
        out += [f"### {r.site}" + (f"（{r.name}）" if r.name else ""), ""]
        for doc_id, title, place in r.misses[:MISSES_SHOWN]:
            where = f"排第 {place}" if place is not None else f"前 {DEEP} 名没有"
            out.append(f"- {doc_id}「{' '.join(title.split())}」：{where}")
        if len(r.misses) > MISSES_SHOWN:
            out.append(f"- ……另外 {len(r.misses) - MISSES_SHOWN} 份")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _protected(out: Path) -> bool:
    """archive*/ (archive-export/ among them), ./data and the skeleton's folder are never written (CLAUDE.md)."""
    target, repo = out.resolve(), REPO.resolve()
    if repo not in target.parents:
        return False
    top = target.relative_to(repo).parts[0]
    skeleton = repo / "research" / "skeleton_2026-11-01"
    return top.startswith("archive") or top == "data" or target.is_relative_to(skeleton)


def _worker(db: Path, today: date, top: int) -> int:
    """--worker: (doc_id, title) pairs as JSON on stdin, {doc_id: place} as JSON on stdout."""
    picks = [(str(a), str(b)) for a, b in json.load(sys.stdin)]
    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        aliases = json.loads(_meta(conn).get("aliases_json") or "[]")
        json.dump(places_here(conn, picks, today=today, aliases=aliases, top=top), sys.stdout)
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="site_report.py", description="每个网站一行的检查（只提醒，不挡建库；不用模型，资料库只读）")
    ap.add_argument("--db", type=Path, required=True, help="建好的资料库")
    ap.add_argument("--out", type=Path, default=None, help="写出的 Markdown（build.sh 写 site_report.md）")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                    help="标题自搜分给几个行程一起搜（默认最多 8 个；1＝就在这个行程里搜）。抽哪些、找不找得到都和这个数无关")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)  # places_by_workers' other end
    ap.add_argument("--sample", type=int, default=SAMPLE, help=f"每个网站抽几份用标题自搜（默认 {SAMPLE}）")
    ap.add_argument("--top", type=int, default=TOP, help=f"前几名算找到（默认 {TOP}）")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help="当作今天的日期（默认台北的今天）")
    ap.add_argument("--checks", type=Path, default=None,
                    help="另写一份程序读的检查档（build.sh 写 site_checks.json）：每站的提醒种类和数字，给资料总览和下一次建库比")
    ap.add_argument("--prev", type=Path, default=None,
                    help="上一次建库的检查档；给了就在报告开头列出「这次新出现的提醒」（读不了只说明，不算失败）")
    args = ap.parse_args(argv)
    if args.worker:
        return _worker(args.db, args.today or today_tpe(), args.top)
    if args.out is None:
        ap.error("--out 一定要给")
    if _protected(args.out):
        print(f"不写到 archive*/、./data 或 research/skeleton_2026-11-01/ 里：{args.out}", file=sys.stderr)
        return 2
    if not args.db.is_file():
        print(f"找不到资料库：{args.db}", file=sys.stderr)
        return 2
    today = args.today or today_tpe()
    try:
        conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
        try:
            meta = _meta(conn)
            shared = None
            if args.jobs > 1:
                def shared(picks: list[tuple[str, str]]) -> dict:
                    return places_by_workers(args.db, picks, today=today, top=args.top, jobs=args.jobs)
            rows = site_rows(conn, today=today, sample=args.sample, top=args.top, places=shared)
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"读不了资料库 {args.db}：{e}", file=sys.stderr)
        return 2
    prev, note = None, ""
    if args.prev is not None:
        try:
            prev = json.loads(args.prev.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            prev, note = None, f"读不了上一次的检查（{args.prev}：{e}），这次不比。"
        else:
            if not isinstance(prev, dict):
                prev, note = None, f"读不了上一次的检查（{args.prev}：不是一份检查档），这次不比。"
    new = new_flags(rows, prev)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.out, render(rows, db=args.db, meta=meta, today=today, sample=args.sample, top=args.top,
                                  new=new if prev is not None else None, prev_note=note).encode("utf-8"))
    if args.checks is not None and not _protected(args.checks):
        args.checks.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(args.checks, json.dumps(checks(rows, db=args.db, meta=meta, today=today, new=new), ensure_ascii=False,
                                             indent=1).encode("utf-8"))
    flagged = [r.site for r in rows if r.flags]
    print(f"每个网站的检查：{len(rows)} 个网站，有提醒的 {len(flagged)} 个"
          + (f"（{'、'.join(flagged[:10])}{' 等' if len(flagged) > 10 else ''}）" if flagged else "")
          + f"；写在 {args.out}（只是提醒，不挡建库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
