"""Real-data checks of the 教務處 skeleton (plan 2026-11-01, Task E, with the 11-01 later change on attachment
dates), also run on the pipeline build (plan 2026-11-02 T10). Read-only: the built database is opened with
?mode=ro&immutable=1, and the MCP server is started once over stdio with fastmcp.Client. Writes the --out Markdown
(keep it in a git-ignored folder: titles can hold student and staff names).

    .venv/bin/python deploy/lab-pipeline/eval/check_realdata.py --out <scratch>/checks.md \
        [--db research/skeleton_2026-11-01/aca.db] [--search keyword] [--today 2026-11-01]
    uv run --extra pipeline python deploy/lab-pipeline/eval/check_realdata.py \
        --db research/pipeline_2026-11-02/aca.db --out research/pipeline_2026-11-02/checks.md --search hybrid \
        --today 2026-11-01

--search keyword (the default) searches with search_docs and starts the MCP server with --no-dense --no-rerank: no
model loads anywhere. --search hybrid searches with hybrid_search (the server's default, both models). It loads the
real bge-m3 and reranker only for a database with usable vectors of the pinned bge-m3 (built with --embed); any other
database (no chunk_vecs, an empty or partial one, vectors of another model or revision) is ranked by keywords alone
and loads neither (main session 11-03). On an embedded database check 17's server process loads them a second time:
run it there only when the machine has the memory (not beside the to-text run). --out is required (its old default
overwrote the skeleton's checks.md) and refused with exit code 2 inside this folder, ./data or archive*/
(nknu.content.build's own guard).
Checks 8 and 9 know the to-text pipeline's kinds (附件:pdf:含OCR, 附件:pdf:无文字层) as well as the skeleton's; check 13
reads past respond's 〔第 N 節〕 lines (T9); check 1 leaves the folded format siblings (T7a) out of the left-out records.
No series since the owner's 11-03 decision (plan addendum L): the six series checks are gone (the 11-01 list's 5–9 and
25: ties, order, 研究所甄試 editions, 寒假/暑假, generic 附件, the 停修 notices folded under nid=662), so there are 22
checks, numbered on from the old ones (old 10 is 5, 13 is 8, 22 is 17, 28 is 22); check 5 asks for the nid=662 notice
first and lists the other terms' 停修 notices as results of their own; check 17 reads no series id.
Stable ids (plan 2026-11-08 T1): the nid=662 notice is 教務處's (aca_notice: its site and nid exactly, never nid=6620
or another site's 662), and the examples of checks 3 and 20 are found by their address, not by an id of one build.
Many sites (plan 2026-11-08 T5 review; build.sh runs these checks whenever 教务处's export is in its list): in a
database of several exports (T4: meta inputs_json, docs.site) meta corpus and export_dir are the first input's alone.
So check 1 adds up the records of every input's corpus (records merged across inputs are counted (重复) in
excluded_json, so the numbers balance) and check 22 reads the calendar of every input's export, one event per id as
the build keeps them (documents.one_event_per_id). Checks 2-4 count 教务处's documents alone (meta site), so their
numbers stay comparable with a build of 教务处 alone: a document counts for the site of the page it hangs on, else for
docs.site, the export whose record the build kept (the newest 抓取时间), so a 教务处 file that another export fetched
later still counts when it hangs on a 教务处 page (Ctx.on_site) 【Claude】. The searches (checks 5-21) run over every
site: other sites' documents ranking beside 教务处's is what a student meets. A database without inputs_json or
docs.site (the skeleton, the builds before T4) is read as before.
Merged recurring events (plan 2026-11-14 T2): a build since then makes one event document of the instances of one
recurring calendar event ('e' + its recurringEventId), whose text lists every instance's days. Check 22 counts an
instance as there when it is a document of its own or its series' document carries its title and lists its days
(events_held, series_days); a database built without the merge (--no-merge-recurring, and every one before) has no
such document and is checked as before.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import unquote

from nknu.content.build import SKELETON, _protected
from nknu.content.corpus import Event, load_calendar
from nknu.content.documents import BEFORE, CALENDAR_SINCE, EVENT_KIND, FORMAT, one_event_per_id
from nknu.content.ids import location_key
from nknu.content.terms import Term, current_term, find_terms
from nknu.core.timeutil import today_tpe
from nknu.retrieval.respond import NOTE_DOWNGRADED, RESPONSE_BUDGET_BYTES, build_response, to_json
from nknu.retrieval.search import search_docs
from nknu.retrieval.server import aliases_of, default_search, texts_for

# 11-19: the program is in the repository (deploy/lab-pipeline/eval); the skeleton library it checks by default and
# the folder it must never write into stay research/skeleton_2026-11-01, which no commit holds.
REPO = next((d for d in Path(__file__).resolve().parents if (d / "src" / "nknu").is_dir()),
            Path(__file__).resolve().parents[3])
HERE = REPO / "research" / "skeleton_2026-11-01"  # the skeleton's folder: its library, and no place for an --out
PY = REPO / ".venv" / "bin" / "python"
TODAY = today_tpe()  # the default of --today
SEARCHES = ("keyword", "hybrid")
OUT_REFUSED = ("--out {out} 落在骨架的资料夹 research/skeleton_2026-11-01/、./data 或 archive*/ 里，"
               "这些地方一律不写；换一个 --out")
SCAN_FILE ="115-1註冊.pdf"  # check 8: a scanned attachment (the skeleton had no text for it; OCR reads it now)
ACA = "https://c.nknu.edu.tw/aca/"
STOP_NID = 662  # checks 3, 5 and 17: 教務處's 115-1 停修 notice (dp32 in the skeleton)
# check 20's example: the notice's 停修公告 PDF, whose link text ended in its size (da2243 in the skeleton)
STOP_FILE = ACA + "uploadfile/news/2026102185559/115-1%E5%81%9C%E4%BF%AE%E5%85%AC%E5%91%8A.pdf"
# check 9: scanned PDFs that came out with a few words (the skeleton's 10-29 kind) or none even after OCR (to-text)
FEW_WORDS_KINDS = ("附件:pdf:文字很少", "附件:pdf:无文字层")

# 20 questions from research/content_recon_2026-10-29/question_set_draft.md, as a student would type them
SAMPLES = [
    ("Q01", "115-1 什麼時候可以申請停修？截止哪天？"), ("Q02", "停修有科目數限制嗎？會退學分費嗎？"),
    ("Q03", "線上停修從哪裡申請？"), ("Q04", "115-1 大學部網路選課是哪幾天？"), ("Q06", "115-1 加退選到哪天？"),
    ("Q07", "下學期什麼時候選課？"), ("Q08", "大學部每學期最多、最少修幾學分？"),
    ("Q10", "115-1 期中考、期末考是什麼時候？"), ("Q11", "115-1 什麼時候開學、註冊？"), ("Q12", "校慶哪天停課？"),
    ("Q14", "抵免學分有上限嗎？"), ("Q16", "英文畢業門檻是什麼？"), ("Q18", "學士班最低畢業學分是多少？"),
    ("Q20", "休學怎麼申請？最多能休幾學期？"), ("Q25", "115學年轉系什麼時候申請？"),
    ("Q27", "輔系、雙主修什麼時候申請？"), ("Q33", "暑修怎麼選課、繳費？"), ("Q36", "學生證遺失怎麼補辦？"),
    ("Q38", "成績單可以排名嗎？"), ("Q46", "115學年寒假轉學考什麼時候報名？"),
]
# the review's own queries (11-01), for the checks of the fix round
REVIEW_QUERIES = ["轉系標準", "114學年度轉系同意名單", "校定共同必修科目表", "轉系", "抵免學分上限", "115-1 停修", "114-2 停修",
                  "停修截止日期", "畢業學分", "修業年限", "成績", "停修", "選課", "雙主修", "學分學程", "休學", "教室借用",
                  "畢業離校", "學位考試", "選課操作手冊", "停修手冊", "學生證", "成績單", "期中考", "註冊", "暑修", "輔系",
                  "轉學", "研究所甄試", "行事曆"]
QUERIES = [q for _, q in SAMPLES] + REVIEW_QUERIES
CONTENTS_LINE = re.compile(r"(?:[.．…‥·‧・]\s*){4,}\d*$")  # nknu.content.chunks._CONTENTS
GAP = "\n…\n"
# respond's section-number line (T9, addendum E: SECTION_MARK, SECTION_MARK_HEADING), opening each block of 章節
SECTION_MARK_LINE = re.compile(r"^〔第 \d+ 節(?:：[^\n]*)?〕$\n?", re.MULTILINE)
SIZE_TAIL = re.compile(r"[(（]\s*\d+(?:\.\d+)?\s*(?:[KkMmGg]?[Bb]|bytes?)\s*[)）]\s*$")  # 「(619775 Kb)」 at the end
SIZE_WORDS = {"kb", "mb", "gb", "bytes"}


@dataclass
class Check:
    name: str
    ok: bool | None  # None: numbers only, no verdict
    numbers: str
    detail: list[str] = field(default_factory=list)


def search_function(kind: str) -> Callable:
    """keyword: search_docs; hybrid: the MCP server's default search (hybrid_search, imported only then)."""
    return search_docs if kind == "keyword" else default_search()


def server_args(db: Path, kind: str) -> list[str]:
    """The MCP server's arguments: a keyword run starts it with both models off, so the server ranks as Ctx does."""
    off = ["--no-dense", "--no-rerank"] if kind == "keyword" else []
    return ["-m", "nknu.retrieval.server", "--db", str(db), *off]


class Ctx:
    def __init__(self, db: Path, *, today: date = TODAY, search: str = "keyword"):
        self.db, self.today, self.mode = db, today, search
        self.conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        self.aliases = aliases_of(self.conn)
        self.meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        self.search_fn = search_function(search)
        self.site = self.meta.get("site", "")  # 教务处's site key, the build's first input (build.sh puts it first)
        self.many = any(r[1] == "site" for r in self.conn.execute("PRAGMA table_info(docs)"))  # docs.site (T4)

    def on_site(self, d: str = "docs") -> tuple[str, tuple[str, ...]]:
        """(' AND <the site of d> = ?', (meta site,)) for a database with docs.site, else ('', ()): checks 2-4 count
        教务处's documents alone (module docstring). d is the docs table's name or alias in the query; a document's site
        is the site of the document it hangs on (parent_id), else its own docs.site."""
        if not self.many:
            return "", ()
        return f" AND coalesce((SELECT s.site FROM docs s WHERE s.doc_id = {d}.parent_id), {d}.site) = ?", (self.site,)

    def one(self, sql: str, *args):
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args) -> list:
        return self.conn.execute(sql, args).fetchall()

    def search(self, query: str):
        return self.search_fn(self.conn, query, today=self.today, aliases=self.aliases)

    def respond(self, query: str) -> dict:
        # the checks of the 全文/章節 places read the full level (design 2026-11-16-mcp-interface-design §二: the
        # layout these checks were written for is the full level's since 11-17; the default is passage)
        return build_response(self.conn, self.search(query), today=self.today, detail="full")

    def title(self, doc_id: str) -> str:
        row = self.one("SELECT title FROM docs WHERE doc_id=?", doc_id)
        return row[0] if row else "?"

    def responses(self) -> dict[str, dict]:
        """The responses to QUERIES, computed once."""
        if not hasattr(self, "_responses"):
            self._responses = {q: self.respond(q) for q in QUERIES}
        return self._responses


def size(resp: dict) -> int:
    return len(to_json(resp).encode("utf-8"))


def aca_notice(c, nid: int = STOP_NID) -> str | None:
    """The doc_id of 教務處's notice nid (plan 2026-11-08 T1): the notices at '%c.nknu.edu.tw/aca/%nid=<nid>%', then
    the one whose site and nid are exactly those (ids.location_key), so neither nid=6620 nor another site's notice of
    that number; None when the database has none. c: anything with Ctx.all."""
    want = location_key(f"{ACA}newsdetail.aspx?nid={nid}")
    rows = c.all("SELECT doc_id, url FROM docs WHERE kind_class='notice' AND url LIKE ? ORDER BY doc_id",
                 f"%c.nknu.edu.tw/aca/%nid={nid}%")
    return next((doc_id for doc_id, url in rows if location_key(url) == want), None)


def short(s: str, n: int = 50) -> str:
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- documents and dates

def left_out_records(excluded: dict[str, int]) -> int:
    """Corpus records the build left out: every excluded count but the calendar's (events are not corpus records)
    and the format siblings (documents.FORMAT, T7a: assemble folds them into another record, so none of them is a
    record of corpus.json)."""
    return sum(n for k, n in excluded.items() if not k.startswith(EVENT_KIND) and not k.endswith(FORMAT))


def _inputs(meta: dict[str, str]) -> list[dict]:
    """Every input of the build, in order (meta inputs_json, plan 2026-11-08 T4); [] for a database built before."""
    return json.loads(meta.get("inputs_json") or "[]")


def input_corpora(meta: dict[str, str]) -> list[Path]:
    """Every input's corpus.json; meta corpus alone for a database built before plan 2026-11-08 T4."""
    return [Path(i["corpus"]) for i in _inputs(meta)] or [Path(meta["corpus"])]


def input_exports(meta: dict[str, str]) -> list[Path]:
    """Every input's export folder; meta export_dir alone for a database built before plan 2026-11-08 T4."""
    return [Path(i["export"]) for i in _inputs(meta)] or [Path(meta["export_dir"])]


def check_counts(c: Ctx) -> Check:
    docs = dict(c.all("SELECT kind_class, count(*) FROM docs GROUP BY 1"))
    excluded = json.loads(c.meta["excluded_json"])
    corpora = input_corpora(c.meta)  # every input's (plan 2026-11-08 T5 review): meta corpus is the first one's alone
    records = sum(len(json.loads(path.read_text(encoding="utf-8"))) for path in corpora)
    left_out = left_out_records(excluded)  # records merged across inputs are in it, counted (重复)
    ok = sum(docs.values()) - docs.get("event", 0) == records - left_out
    return Check("文件数（按种类）和排除数", ok,
                 f"{sum(docs.values())} 份：" + "、".join(f"{k} {v}" for k, v in sorted(docs.items(), key=lambda x: -x[1]))
                 + f"；排除 {sum(excluded.values())}",
                 [(f"corpus 记录 {records} 条{f'（{len(corpora)} 份输入加起来）' if len(corpora) > 1 else ''}，排除"
                   f"（不算行事历；「{FORMAT}」并在别的记录里，也不算）{left_out} 条，非行事历文件 {records - left_out} 份"),
                  "排除明细：" + "、".join(f"{k} {v}" for k, v in sorted(excluded.items(), key=lambda x: -x[1]))])


def check_identity_dates(c: Ctx) -> Check:
    only, site = c.on_site()  # 教务处's documents alone in a database of many sites (module docstring)
    d_only, _ = c.on_site("d")
    rows = c.all("SELECT kind_class, coalesce(ident_kind, '-'), count(*) FROM docs WHERE 1" + only
                 + " GROUP BY 1, 2 ORDER BY 1, 3 DESC", *site)
    by: dict[str, dict[str, int]] = {}
    for kc, kind, n in rows:
        by.setdefault(kc, {})[kind] = n
    bad_kind = c.one("SELECT count(*) FROM docs WHERE ident_kind NOT IN ('published', 'inherited', 'server')" + only,
                     *site)[0]
    notices = c.one("SELECT count(*), sum(ident_kind = 'published') FROM docs WHERE kind_class='notice'" + only, *site)
    events = c.one("SELECT count(*), count(DISTINCT e.doc_id) FROM docs d LEFT JOIN dates e ON e.doc_id = d.doc_id"
                   " AND e.kind = 'event_start' WHERE d.kind_class='event'" + d_only, *site)
    no_date = c.one("SELECT count(*) FROM docs WHERE ident_date IS NULL AND kind_class != 'event'" + only, *site)[0]
    text_kinds = c.one("SELECT count(*) FROM dates t JOIN docs d ON d.doc_id = t.doc_id"
                       " WHERE t.kind IN ('issued', 'revised', 'form_version')" + d_only, *site)[0]
    ok = notices[0] == notices[1] and events[0] == events[1] and bad_kind == 0 and text_kinds == 0
    return Check("身份日期的来源、无日期数", ok,
                 f"公告 {notices[1]}/{notices[0]} 有页面日期；行事历 {events[1]}/{events[0]} 有日期；无日期 {no_date}",
                 ["身份日期种类（按文件种类）：" + "；".join(f"{kc}: " + "、".join(f"{k} {n}" for k, n in d.items())
                                                for kc, d in by.items()),
                  (f"身份日期不在 published/inherited/server 里的：{bad_kind}；"
                   f"从附件内文读出的日期（issued/revised/form_version）：{text_kinds}（11-01 稍晚的改动：一律不读）"),
                  # plan 2026-11-08 T3: a page now has the date the export read on it, when there is one
                  "无日期 = 网页（导出没在网页上读到日期的）、外部连结，和挂在没日期网页上、又没有服务器时间的附件"])


def check_inherits(c: Ctx) -> Check:
    only, site = c.on_site("a")  # attachments hung on 教务处's notices (module docstring)
    rows = c.all("SELECT a.doc_id, a.ident_kind, a.ident_date, p.ident_date, p.doc_id FROM docs a"
                 " JOIN docs p ON p.doc_id = a.parent_id WHERE a.kind_class='attachment' AND p.kind_class='notice'"
                 " AND p.ident_kind='published'" + only, *site)
    bad = [r for r in rows if r[1] != "inherited" or r[2] != r[3]]
    # 11-15 (session 4bd91945; the owner 11-14 23:55 had the platform twins merged, nknu.content.build): a file whose
    # notice was merged into another one hangs on the notice kept. The two notices are of one day, so the file's date
    # is its notice's still, but the words the date was read from are the first notice's (「2026/8/5 上午 08:31:00」,
    # the kept one's 「公告時間： 2026-08-05」). Such a file is no failure: counted apart (moved). A notice kept for
    # others is one a copy's id points at (doc_alias why 'copy'; a database before plan 2026-11-08 T1 has no such table)
    aliased = c.one("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'doc_alias'")[0]
    kept_for_others = ("EXISTS (SELECT 1 FROM doc_alias x WHERE x.doc_id = a.parent_id AND x.why = 'copy')"
                       if aliased else "0")
    differs = ("FROM dates i JOIN docs a ON a.doc_id = i.doc_id JOIN dates p ON p.doc_id = a.parent_id AND"
               " p.kind = 'published' WHERE i.kind = 'inherited' AND (i.raw != p.raw OR i.date != p.date)")
    moved = c.one(f"SELECT count(*) {differs} AND i.date = p.date AND {kept_for_others}" + only, *site)[0]
    raw_ok = c.one(f"SELECT count(*) {differs}" + only, *site)[0] - moved
    stop = aca_notice(c)  # the nid=662 notice: an example every build has (dp32 in the skeleton)
    sample = next((r for r in rows if r[4] == stop), rows[0] if rows else None)
    detail = [f"不符的：{len(bad)}；继承日期的原文或日期和所属公告不同的：{raw_ok}"
              + (f"；所属公告合并过副本、附件改挂到留下的那份，日期同一天、只是原文写法不同的：{moved}（不算不符）"
                 if moved else "")]
    if sample:
        detail.append(f"例：{sample[0]}（{short(c.title(sample[0]))}）挂在 {sample[4]}（{short(c.title(sample[4]))}），"
                      f"身份日期 {sample[2]}（{sample[1]}），公告日期 {sample[3]}")
    return Check("挂在有日期公告上的附件继承那则公告的日期", not bad and raw_ok == 0 and bool(rows),
                 f"{len(rows) - len(bad)}/{len(rows)} 份", detail)


SERVER_TRIES = 12  # searches check_server_time spends on finding an example row that carries its dates


def check_server_time(c: Ctx) -> Check:
    only, site = c.on_site("a")  # 教务处's attachments alone (module docstring)
    rows = c.all("SELECT a.doc_id, a.ident_kind, a.has_text, a.title FROM docs a LEFT JOIN docs p ON p.doc_id ="
                 " a.parent_id WHERE a.kind_class='attachment' AND (p.doc_id IS NULL OR p.kind_class != 'notice' OR"
                 " p.ident_kind IS NULL)" + only + " ORDER BY a.doc_id", *site)
    has_server = {r[0] for r in c.all("SELECT doc_id FROM dates WHERE kind='server'")}
    with_server = [r for r in rows if r[0] in has_server]
    bad = [r for r in with_server if r[1] != "server"]
    none = [r for r in rows if r[0] not in has_server]
    detail = [(f"这类附件 {len(rows)} 份：有服务器档案时间 {len(with_server)}（身份日期不是 server 的 {len(bad)}），"
               f"没有的 {len(none)}（身份日期为空：{sum(r[1] is None for r in none)}）")]
    # 11-19: since the interface of 11-17 (design 2026-11-16-mcp-interface-design §二) a result outside the full
    # places is a 片段 row with the short header (respond.LEAN_KEYS): its identity line, no dates list. The example is
    # one whose row carries the dates when the first SERVER_TRIES searches find such a one (the identity line and
    # the date's type both say 伺服器檔案時間); else the first one found at all, judged by its identity line alone,
    # which is what an agent is shown of such a row.
    shown_ok, lean = False, None
    tried = 0
    for doc_id, _, has_text, title in with_server:
        stem = title.rsplit(".", 1)[0]
        if not has_text or len(re.findall(r"[一-鿿]", stem)) < 4:
            continue
        if tried >= SERVER_TRIES:
            break
        tried += 1
        resp = c.respond(stem)
        hit = next((r for r in resp.get("results", []) if r["id"] == doc_id), None)
        if hit is None:
            continue
        in_line = "伺服器檔案時間" in hit["identity"] and "發布" not in hit["identity"]
        if "dates" not in hit:
            lean = lean or (stem, doc_id, hit["rank"], hit["identity"], in_line)
            continue
        types = [d["type"] for d in hit["dates"]]
        shown_ok, lean = in_line and "伺服器檔案時間" in types, None
        detail.append(f"例：搜「{stem}」，{doc_id} 第 {hit['rank']} 名，身份行「{hit['identity']}」，日期种类 {types}")
        break
    if lean is not None:
        stem, doc_id, rank, identity, shown_ok = lean
        detail.append(f"例（片段那一层，只有身份行、不带日期栏；前 {tried} 个例子都没排进给全文的名次）："
                      f"搜「{stem}」，{doc_id} 第 {rank} 名，身份行「{identity}」")
    return Check("挂在没日期网页上的附件显示「伺服器檔案時間」", not bad and shown_ok,
                 f"{len(with_server) - len(bad)}/{len(with_server)} 份身份日期是伺服器檔案時間；回传例子"
                 f"{'对' if shown_ok else '不对'}", detail)


# ---------------------------------------------------------------- search

def event_states_period(kind: str, event_days: tuple, period_days: tuple) -> bool:
    """A calendar event whose first and last day are the application period the notice states (正文期間)."""
    return kind == "event" and None not in period_days and tuple(event_days) == tuple(period_days)


def stop_verdict(rank: int | None, first_event_period: bool) -> tuple[bool, str]:
    """Check 5 (check 10 of the 11-01 list): the notice first ('first'). Until 11-03 its older versions also had to be
    folded under it; with no series they are results of their own and not part of the verdict. Task 11-01 night: when
    the first hit is the event that states the notice's application period, the notice in the top 3 is enough
    ('top3'; missed: 'top3_missed')."""
    if rank == 1:
        return True, "first"
    if not first_event_period:
        return False, "first"
    return (True, "top3") if rank is not None and rank <= 3 else (False, "top3_missed")


def check_stop(c: Ctx) -> Check:
    target = aca_notice(c)
    result = c.search("115-1 停修")
    ids = [h.doc_id for h in result.hits]
    rank = ids.index(target) + 1 if target in ids else None
    days = "SELECT MIN(CASE WHEN kind=? THEN date END), MAX(CASE WHEN kind=? THEN date END) FROM dates WHERE doc_id=?"
    first = ids[0] if ids else None
    first_kind = c.one("SELECT kind_class FROM docs WHERE doc_id=?", first)[0] if first else ""
    first_days = c.one(days, "event_start", "event_end", first) if first else (None, None)
    period = c.one(days, "period_start", "period_end", target)
    first_event_period = event_states_period(first_kind, first_days, period)
    ok, criterion = stop_verdict(rank, first_event_period)
    name = ("「115-1 停修」nid=662 公告在前 3 名（第 1 名是写着受理期间的行事历事件）" if criterion == "top3"
            else "「115-1 停修」第 1 名是 nid=662 公告")
    detail = [f"nid=662 是 {target}（{short(c.title(target), 60)}）：第 {rank} 名"]
    for i, h in enumerate(result.hits[:5], 1):
        kind, has_text = c.one("SELECT kind_class, has_text FROM docs WHERE doc_id=?", h.doc_id)
        detail.append(f"{i}. {h.doc_id} {kind}{'' if has_text else '（无文字）'} score={h.score:.3f} "
                      f"coverage={h.coverage:.2f} bm25={h.bm25:.2f} {short(c.title(h.doc_id), 40)}")
    others = c.all("SELECT doc_id, title FROM docs WHERE kind_class='notice' AND title LIKE '%停修%'"
                   " AND period_ay IS NOT NULL AND doc_id != ? ORDER BY period_ay DESC, period_sem DESC", target)
    detail.append("其他学期的停修公告（11-03 起没有系列，各自一笔）：" + "；".join(
        f"{d} {short(t, 24)} → {f'第 {ids.index(d) + 1} 名' if d in ids else '不在前 10 名'}" for d, t in others[:8]))
    detail.append(
        f"放宽条件（11-01 晚上的任务）：第 1 名是写着受理期间的行事历事件、而且 nid=662 公告在前 3 名，就改判「公告在前 3 名」。"
        f"第 1 名 {first}（{first_kind}，{first_days[0]}～{first_days[1]}）"
        + (f"正是公告正文写的受理期间（{period[0]}～{period[1]}）" if first_event_period else "不是写着受理期间的行事历事件")
        + {"first": "；公告排第 1，用原来的判定。",
           "top3": f"；公告排第 {rank}，在前 3 名，所以改判「公告在前 3 名」：第 1 名的事件本身就答了「什麼時候」，公告紧跟在后。",
           "top3_missed": f"；但公告排第 {rank}，不在前 3 名，放宽条件也不满足，照旧判不过（不为了过关硬改）。"}[criterion])
    if not ok:
        resp = c.respond("115-1 停修")
        got = next((r for r in resp.get("results", []) if r["id"] == target), None)
        detail += [
            ("原因：前几名的 coverage 都是 1.0、学期都对上（+0.5），名次只差在 bm25。FTS5 的 bm25 按整行长度做长度正规化，"
             "而每个小段那行都带标题词；附件的标题栏 = 自己的档名 + 所属公告标题 + 连结文字，所以「停修」出现 3 次"
             "（公告自己的是 2 次）；扫描档那行没有正文，行很短，标题命中几乎不被正规化，所以分数最高。"),
            (f"修复那一轮做了 (a)：前 3 个全文名额只给有文字的结果，所以 {target} 虽然排第 {rank}，现在回传"
             f"「{got['content_type'] if got else '-'}」（审查时只有 180 字摘要）。"),
            ("排序 v2（11-01 晚上；默认越新越优先 0.3、τ=365 天，学报和考题各 −0.3，见 research/eval_2026-11-01/ranking_v2.md）"
             "对这个查询不起作用：查询点名了 115-1，日期加分只在没点名学期时才加；前 5 名里也没有学报或考题。所以名次和改排序以前一样。"),
            ("还没做的改法（要主 session 定）：(b) 每份文件的标题单独一行、正文行不带标题，标题命中在等长的行之间比；"
             "(d) 公告自己的附件收在公告下面。原来列的 (c) 资料种类权重、(e) 没点名学期时给新的加分，"
             "排序 v2 已经做了，但如上所说碰不到这个查询；(f) 行事历同名事件进系列，11-03 起没有系列，不再考虑。"),
        ]
        events = [h.doc_id for h in result.hits[:5] if c.one("SELECT kind_class FROM docs WHERE doc_id=?",
                                                             h.doc_id)[0] == "event"]
        if events:
            detail.append(
                f"第二轮修正（11-01）后：行事历事件都有了学期栏和学期记号，事件那行只有标题、很短，所以前 5 名里有 {len(events)} 个"
                "事件（" + "、".join(short(c.title(e), 20) for e in events) + "）；「加退選」的事件也在里面，是因为别名表把「退選」"
                "当停修的别名，而「加退選」含「退選」两个字（查询那边有防，文件那边没有）。")
    numbers = f"nid=662 第 {rank} 名"
    if criterion == "top3_missed":
        numbers += "；第 1 名是写着受理期间的行事历事件，但公告不在前 3 名"
    return Check(name, ok, numbers, detail)


def check_same_top3(c: Ctx) -> Check:
    a = [h.doc_id for h in c.search("115學年度第1學期 選課").hits[:3]]
    b = [h.doc_id for h in c.search("115-1 選課").hits[:3]]
    return Check("「115學年度第1學期 選課」和「115-1 選課」前 3 名相同", a == b and len(a) == 3, f"{a} vs {b}",
                 [f"{i}. {d} {short(c.title(d))}" for i, d in enumerate(a, 1)])


def check_withdraw(c: Ctx) -> Check:
    result = c.search("退選")
    stops = [h.doc_id for h in result.hits if (c.one("SELECT kind_class FROM docs WHERE doc_id=?", h.doc_id)[0]
                                               == "notice" and "停修" in c.title(h.doc_id))]
    return Check("「退選」找得到停修公告", bool(stops), f"前 10 名里停修公告 {len(stops)} 则；别名 {result.understood.aliases}",
                 [f"{d} {short(c.title(d), 60)}" for d in stops[:5]])


def scanned_verdict(kind_raw: str, has_text: bool, content_type: str) -> bool:
    """Check 8 (plan 2026-11-02 T10; check 13 of the 11-01 list): a scanned PDF the OCR read (附件:pdf:含OCR) is shown with its words (全文, 章節
    or 摘要); one without text (the skeleton's scans, or nothing read) is shown as 無文字."""
    if kind_raw.startswith("附件:pdf:含OCR") and content_type in ("全文", "章節", "段落", "片段"):
        return True
    return not has_text and content_type == "無文字"


def few_words(kind_raw: str) -> bool:
    """Check 9's documents: a scanned PDF with a few words (the skeleton's 10-29 kind) or none even after OCR."""
    return kind_raw.startswith(FEW_WORDS_KINDS)


def check_scanned(c: Ctx) -> Check:
    """Check 8: 「115-1註冊」 finds the scanned 115-1註冊.pdf (by its title, or by its file name in linktext once a page
    names it), judged by scanned_verdict; the title-sampled search of no-text attachments stays."""
    ids = {r[0] for r in c.all("SELECT doc_id FROM docs WHERE title = ? OR linktext LIKE ?", SCAN_FILE,
                               f"%{SCAN_FILE}%")}
    resp = c.respond(SCAN_FILE.rsplit(".", 1)[0])
    hit = next((r for r in resp.get("results", []) if r["id"] in ids), None)
    kind, has_text = c.one("SELECT kind_raw, has_text FROM docs WHERE doc_id=?", hit["id"]) if hit else ("", 0)
    ok = hit is not None and scanned_verdict(kind, bool(has_text), hit["content_type"])
    sample = c.all("SELECT doc_id, title FROM docs WHERE has_text=0 AND kind_class='attachment' ORDER BY doc_id")
    picked = sample[:: max(1, len(sample) // 50)][:50]
    found, missed = 0, []
    for doc_id, title in picked:
        result = c.search(title.rsplit(".", 1)[0])
        if doc_id in [h.doc_id for h in result.hits]:
            found += 1
        else:
            missed.append(title)
    detail = [(f"搜「115-1註冊」：{hit['id']}（{kind}，has_text={has_text}）第 {hit['rank']} 名，{hit['content_type']}，"
               f"内容「{short(hit['content'].replace(chr(10), ' / '), 120)}」") if hit
              else f"搜「115-1註冊」：前 10 名没有 {SCAN_FILE}（资料库里的这份：{sorted(ids) or '没有'}）",
              "判定（T10）：OCR 读出字的（附件:pdf:含OCR）要给原文（全文、章節、段落或片段）；没有文字的要回传「無文字」。",
              (f"另抽 {len(picked)} 份无文字附件，用自己的标题（去掉副档名）搜：{found} 份在前 10 名；"
               f"不在的标题：{'、'.join(missed)}")]
    return Check("扫描档：OCR 读出的给原文、没有文字的回传「無文字」；按标题搜得到", ok,
                 f"「115-1註冊」{hit['content_type'] if hit else '不在前 10 名'}；抽样 {found}/{len(picked)} 在前 10 名",
                 detail)


def check_few_words(c: Ctx) -> Check:
    """Review 11-01 (critical): mostly scanned PDFs with a few words came back as 全文. T10: the to-text pipeline's
    附件:pdf:无文字层 (under 30 characters even after OCR) is checked the same way; no such document: numbers only."""
    few = c.all("SELECT doc_id, title, has_text, length(text), kind_raw FROM docs WHERE kind_raw LIKE ? OR kind_raw"
                " LIKE ? ORDER BY doc_id", *(k + "%" for k in FEW_WORDS_KINDS))
    name = "「文字很少」和「无文字层」的扫描档不当全文、转出的少量文字照样能搜"
    if not few:
        return Check(name, None, "没有这类文件",
                     [f"资料库里没有 {'、'.join(FEW_WORDS_KINDS)} 这两种附件（OCR 都读出了字），没有可查的。"])
    wrong, found, shown = [], 0, None
    for doc_id, title, _, _, _ in few:
        hit = next((r for r in c.respond(title.rsplit(".", 1)[0]).get("results", []) if r["id"] == doc_id), None)
        if hit is None:
            continue
        found += 1
        if hit["content_type"] != "無文字":
            wrong.append((doc_id, hit["content_type"]))
        elif shown is None and "只轉出少量文字" in hit["content"]:
            shown = (doc_id, title, hit["content"])
    for q, resp in c.responses().items():
        wrong += [(r["id"], r["content_type"], q) for r in resp.get("results", []) if r["content_type"] != "無文字"
                  and few_words((c.one("SELECT kind_raw FROM docs WHERE doc_id=?", r["id"]) or ("",))[0])]
    kinds = Counter(k.split("(")[0] for *_, k in few)
    detail = [f"这类附件 {len(few)} 份（" + "、".join(f"{k} {n}" for k, n in kinds.items()) + f"），has_text=0 的 "
              f"{sum(r[2] == 0 for r in few)} 份，用标题搜到 {found} 份；另看 {len(QUERIES)} 个查询的结果。回传不是「無文字」的："
              f"{len(wrong)}" + (f"：{wrong[:5]}" if wrong else "")]
    if shown:
        detail.append(f"例：{shown[0]}（{shown[1]}）回传「{short(shown[2].replace(chr(10), ' / '), 120)}」")
    return Check(name, not wrong, f"{len(few)} 份，回传不是無文字的 {len(wrong)}", detail)


def check_copies(c: Ctx) -> Check:
    """Review 11-01 (major): the same file at two addresses took two places in the top ten."""
    excluded = json.loads(c.meta["excluded_json"])
    merged = {k: n for k, n in excluded.items() if k.endswith("(副本)")}
    listed = c.one("SELECT count(*) FROM meta WHERE key LIKE 'copies:%'")[0]
    twice = []
    for q, resp in c.responses().items():
        seen: dict[tuple, str] = {}
        for r in resp.get("results", []):
            kind, text = c.one("SELECT kind_class, text FROM docs WHERE doc_id=?", r["id"])
            key = (r["title"], hashlib.sha256(text.encode()).hexdigest())
            if kind != "event" and text.strip() and key in seen:  # same-titled events are different days
                twice.append((q, seen[key], r["id"]))
            seen.setdefault(key, r["id"])
    resp = c.respond("轉系")
    example = next((r for r in resp.get("results", []) if r.get("copies")), None)
    detail = [f"并掉的副本 {sum(merged.values())} 份（" + "、".join(f"{k} {n}" for k, n in sorted(merged.items(),
                                                                                key=lambda x: -x[1])) + "）",
              f"留下的那份记着副本网址的：{listed} 份；{len(QUERIES)} 个查询里标题和全文都相同的结果出现两次的：{len(twice)}"
              + (f"：{twice[:5]}" if twice else "")]
    if example:
        detail.append(f"例：搜「轉系」，{example['id']}（{short(example['title'])}）第 {example['rank']} 名，自己的网址 "
                      f"{short(unquote(example['url']), 70)}；copies："
                      + "；".join(f"{short(unquote(x['url']), 70)}（{x.get('date_type', '')} {x.get('date', '无日期')}，"
                                 f"所属：{x.get('parent', {}).get('title', '没有所属网页')}）" for x in example["copies"]))
    return Check("同一份文件放在两个网址只出现一次（副本列在 copies）", not twice and listed > 0,
                 f"并掉 {sum(merged.values())} 份，结果里重复 {len(twice)}", detail)


def check_full_places(c: Ctx) -> Check:
    """Review 11-01 (major): a scanned file in the top three used up a 全文 place."""
    bad, moved = [], []
    for q, resp in c.responses().items():
        res = resp.get("results", [])
        with_text = [r for r in res if r["content_type"] != "無文字"][:3]
        if NOTE_DOWNGRADED not in resp["notes"]:  # the note as respond writes it now (T9 reworded it)
            bad += [(q, r["rank"], r["content_type"]) for r in with_text if r["content_type"] not in ("全文", "章節")]
        if any(r["content_type"] == "無文字" for r in res[:3]):
            moved.append((q, [f"{r['rank']}:{r['content_type']}" for r in res[:4]]))
    detail = [f"{len(QUERIES)} 个查询里，前 3 个有文字的结果没拿到全文或章節的（没被预算降级时）：{len(bad)}"
              + (f"：{bad[:5]}" if bad else ""),
              f"前 3 名里有「無文字」的查询 {len(moved)} 个，名额顺延到后面有文字的结果："
              + "；".join(f"「{short(q, 14)}」{x}" for q, x in moved[:6])]
    return Check("全文名额只给有文字的结果", not bad, f"没拿到名额的 {len(bad)}，名额顺延的查询 {len(moved)}", detail)


def check_crumbs(c: Ctx) -> Check:
    """Review 11-01 (major): a section's last line stayed a chunk of its own ('備') and became the summary."""
    tails, rows = [], c.all("SELECT doc_id, sec_id, start, end FROM chunks WHERE end > start ORDER BY doc_id, sec_id,"
                            " start")
    for i, (doc_id, sec_id, start, end) in enumerate(rows):
        prev = rows[i - 1] if i else None
        if (end - start < 200 and prev and prev[0] == doc_id and prev[1] == sec_id and end - prev[2] <= 800):
            tails.append((doc_id, sec_id, end - start))
    lens = [e - s for _, _, s, e in rows]
    detail = [(f"有文字的小段 {len(lens)} 个：不到 2 字 {sum(n < 2 for n in lens)}、不到 5 字 {sum(n < 5 for n in lens)}、"
               f"不到 20 字 {sum(n < 20 for n in lens)}（审查时 52、1,014、7,092）"),
              f"节里还能并回前一段（并了不超过 800 字）却没并的碎段：{len(tails)}" + (f"：{tails[:5]}" if tails else ""),
              ("剩下的短小段多半是整节只有一行（一行的节、行事历事件、整份只有一两个字的档），"
               "小段不跨节，这些留着；它们在排序上占的便宜属于第 6 项（排序），没动")]
    return Check("小段没有节末碎屑", not tails, f"不到 2 字的小段 {sum(n < 2 for n in lens)}", detail)


def unmarked_content(content: str) -> str:
    """A 章節 content without respond's section-number lines (T9 opens every block with 〔第 N 節〕 or 〔第 N 節：heading〕,
    addendum E): what is left is the school's text and the 「…」 cut marks."""
    return SECTION_MARK_LINE.sub("", content)


def window_marked(content: str) -> bool:
    """A window says where it cuts: 「…」 at its start or end, or on a line of its own (below a table's header row,
    which respond shows above a window that starts inside a table: H2)."""
    content = unmarked_content(content)
    return content.startswith("…") or content.endswith("…") or "\n…\n" in content


def _whole_sections(c: Ctx, doc_id: str, content: str) -> bool:
    """Every 「…」-separated piece of a 章節 content is a run of whole sections (whitespace and respond's section-number
    lines aside)."""
    text = c.one("SELECT text FROM docs WHERE doc_id=?", doc_id)[0]
    parts = [re.sub(r"\s", "", text[s:e]) for s, e in c.all("SELECT start, end FROM sections WHERE doc_id=? ORDER BY"
                                                             " sec_id", doc_id)]
    for piece in (re.sub(r"\s", "", unmarked_content(p)) for p in content.split(GAP)):
        ok = False
        for i, first in enumerate(parts):
            if not first or not piece.startswith(first):
                continue
            acc, j = first, i
            while len(acc) < len(piece) and j + 1 < len(parts):
                j += 1
                acc += parts[j]
            if acc == piece:
                ok = True
                break
        if not ok:
            return False
    return True


def check_windows(c: Ctx) -> Check:
    """Review 11-01 (major): a window cut out of a long section said nothing of the cut."""
    windows, unmarked, partial, sections = [], [], [], 0
    for q, resp in c.responses().items():
        for r in resp.get("results", []):
            if r["content_type"] != "章節":
                continue
            if "這裡只給" in r.get("more", ""):
                windows.append((q, r["id"]))
                if not window_marked(r["content"]):
                    unmarked.append((q, r["id"]))
            else:
                sections += 1
                if not _whole_sections(c, r["id"], r["content"]):
                    partial.append((q, r["id"]))
    example = next((r for r in c.respond("停修截止日期").get("results", []) if "這裡只給" in r.get("more", "")), None)
    detail = [f"{len(QUERIES)} 个查询的章節结果：窗口 {len(windows)} 个（没标「…」的 {len(unmarked)}）；整节 {sections} 个"
              f"（其中不是整节的 {len(partial)}）" + (f"：{(unmarked + partial)[:5]}" if unmarked or partial else "")]
    if example:
        detail.append(f"例：搜「停修截止日期」，{example['id']} 第 {example['rank']} 名，开头「"
                      f"{short(example['content'].replace(chr(10), ' / '), 30)}」，more「{short(example['more'], 110)}」")
    return Check("章節窗口标「…」并说明只给了一段；其余章節都是整节", not unmarked and not partial,
                 f"窗口 {len(windows)}、没标的 {len(unmarked)}、不是整节的 {len(partial)}", detail)


def check_small_sections(c: Ctx) -> Check:
    """Review 11-01 (major): a hit section of one line came alone; contents lines were sections."""
    small = [(q, r["id"], len(r["content"])) for q, resp in c.responses().items() for r in resp.get("results", [])
             if r["content_type"] == "章節" and len(r["content"]) < 200]
    contents = sum(1 for (h,) in c.all("SELECT heading FROM sections") if CONTENTS_LINE.search(h))
    resp = c.respond("畢業學分")
    first = resp["results"][0] if resp.get("results") else None
    detail = [f"{len(QUERIES)} 个查询里不到 200 字的章節结果：{len(small)}" + (f"：{small[:5]}" if small else "")
              + "（审查时 50 个查询里 12 个）",
              (f"标题是目录点线行的节：{contents}（审查时 1,516）；全库不到 10 字的节 "
               f"{c.one('SELECT count(*) FROM sections WHERE end - start < 10')[0]}（审查时 2,941）")]
    if first:
        detail.append(f"例：搜「畢業學分」，第 1 名 {first['id']} {first['content_type']} {len(first['content'])} 字，开头"
                      f"「{short(first['content'].replace(chr(10), ' / '), 80)}」；这一节前面是 1 万字的课程表，系所名称在更前面，"
                      "整节补不到，要等能保留结构的转字工具")
    return Check("命中的节不会只剩一两行；目录行不当章节", not small and contents == 0,
                 f"短章節 {len(small)}、目录行章节 {contents}", detail)


def check_samples(c: Ctx) -> Check:
    rows, sizes = [], []
    for qid, q in SAMPLES:
        start = time.perf_counter()
        resp = c.respond(q)
        ms = (time.perf_counter() - start) * 1000
        n = size(resp)
        sizes.append(n)
        res = resp.get("results", [])
        top = f"{res[0]['id']} {short(res[0]['title'], 28)}" if res else "-"
        types = "/".join(r["content_type"] for r in res[:3])
        rows.append(f"{qid}「{q}」{n:,} bytes，{len(res)} 笔，{ms:.0f} ms，第 1 名 {top}，前 3 名 {types}")
    return Check("20 个样本查询都 ≤ 36,000 bytes", max(sizes) <= RESPONSE_BUDGET_BYTES,
                 f"最大 {max(sizes):,}、最小 {min(sizes):,}、平均 {sum(sizes) // len(sizes):,} bytes", rows)


def check_not_found(c: Ctx) -> Check:
    outs = {q: c.respond(q) for q in ("!!!", "", 'AND OR NEAR * ^ "', "停修" * 200)}
    ok = outs["!!!"]["found"] is False and outs[""]["found"] is False  # the others only must not raise (focus 4)
    return Check("「!!!」→ found=false", ok, "、".join(f"「{short(q, 12)}」found={r['found']}" for q, r in outs.items()),
                 [f"「!!!」的 notes：{outs['!!!']['notes']}",
                  "运算符那句（AND OR NEAR）切出英文词 and/or/near，语料里真的有，所以有结果是对的；计划只要求不出错"])


# ---------------------------------------------------------------- second fix round (11-01)

def _next_term(today) -> Term:
    cur = current_term(today)
    return Term(cur.ay, 2) if cur.sem == 1 else Term(cur.ay + 1, 1)


def _term_days(t: Term) -> tuple[str, str]:
    """The calendar days of a semester as nknu.content.terms.current_term counts them: Aug–Jan, Feb–Jul."""
    y = t.ay + 1911
    return (f"{y}-08-01", f"{y + 1}-01-31") if t.sem == 1 else (f"{y + 1}-02-01", f"{y + 1}-07-31")


def _top(c: Ctx, hits, n: int = 5) -> list[str]:
    out = []
    for i, h in enumerate(hits[:n], 1):
        kind, has_text, ay, sem = c.one("SELECT kind_class, has_text, period_ay, period_sem FROM docs WHERE doc_id=?",
                                        h.doc_id)
        period = f"{ay}-{sem}" if sem else (f"{ay}學年度" if ay else "无学期")
        out.append(f"{i}. {h.doc_id} {kind}{'' if has_text else '（无文字）'} {period} score={h.score:.3f} "
                   f"coverage={h.coverage:.2f} bm25={h.bm25:.2f} {short(c.title(h.doc_id), 40)}")
    return out


def check_next_term_events(c: Ctx) -> Check:
    """Fix round 2, item 3: every calendar event has a 學年度 and 學期 (title, else its first day), and their term
    tokens are in its title column, so 「下學期 選課」 and 「115-2 選課」 find next semester's 選課 events."""
    nxt = _next_term(c.today)
    label = f"{nxt.ay}-{nxt.sem}"
    lo, hi = _term_days(nxt)
    targets = c.all("SELECT d.doc_id, d.title, f.date, d.period_ay, d.period_sem FROM docs d JOIN dates f ON f.doc_id ="
                    " d.doc_id AND f.kind = 'event_start' WHERE d.kind_class = 'event' AND d.title LIKE '%選課%' AND"
                    " f.date BETWEEN ? AND ? ORDER BY f.date, d.doc_id", lo, hi)
    ids = [r[0] for r in targets]
    found: dict[str, dict[str, int]] = {}
    detail = [f"{label}（{lo}～{hi}）开始的、标题有「選課」的行事历事件 {len(targets)} 个："
              + "；".join(f"{d}（{day} {short(t, 24)}，学期栏 {ay if ay else '-'}-{sem if sem else '-'}）"
                         for d, t, day, ay, sem in targets)]
    for q in ("下學期 選課", f"{label} 選課"):
        result = c.search(q)
        got = [h.doc_id for h in result.hits]
        found[q] = {d: got.index(d) + 1 for d in ids if d in got}
        detail.append(f"「{q}」（学期记号 {result.understood.canonical}）前 10 名里的这些事件："
                      + ("、".join(f"{d} 第 {n} 名" for d, n in found[q].items()) or "没有"))
        detail += _top(c, result.hits)
    n_events, n_period = c.one("SELECT count(*), sum(period_ay IS NOT NULL) FROM docs WHERE kind_class='event'")
    detail.append(f"有学年度和学期的行事历事件：{n_period or 0}/{n_events}")
    ok = bool(targets) and all(found.values())
    return Check(f"「下學期 選課」和「{label} 選課」前 10 名有 {label} 选课的行事历事件", ok,
                 "；".join(f"「{q}」{len(r)} 个" for q, r in found.items()) + f"；有学期的事件 {n_period or 0}/{n_events}",
                 detail)


QUESTION_CASES = [  # fix round 3 (11-01): query, words that must be compared, words that must not
    ("申請教室", {"申请", "教室"}, set()), ("怎麼申請教室", {"申请", "教室"}, set()), ("申請問題", {"申请", "问题"}, set()),
    ("請問停修怎麼申請", {"停修", "申请"}, {"请问"}), ("最多能休幾學期", {"休学"}, set()),
]
TERM_ONLY = ["115-1 什麼時候？", "下學期呢？"]  # nothing left but the term: the term alone is searched


def check_question_words(c: Ctx) -> Check:
    """Fix round 2, item 2: question words (什麼時候, 怎麼, 嗎 ...) are not searched for, so a question no longer
    favours the long FAQ page that happens to hold them. Fix round 3, item 1: 請問 / 請教 only where a clause starts
    (申請問題, 申請教室 keep their words), 何時 / 能否 / 幾 are question words too, and a query left with its term
    alone searches the term, not its question words."""
    faq, chars = c.one("SELECT doc_id, chars FROM docs WHERE kind_class='page' AND title='Q & A'")
    q = "下學期什麼時候選課"
    result = c.search(q)
    top3 = [h.doc_id for h in result.hits[:3]]
    ids = [h.doc_id for h in result.hits]
    rank = ids.index(faq) + 1 if faq in ids else None
    where = f"第 {rank} 名" if rank else "不在前 10 名"
    detail = [f"常见问题长页是 {faq}（Q & A，{chars} 字）；「{q}」里它{where}",
              f"拿去比对的词：{result.understood.tokens}；学期记号 {result.understood.canonical}", *_top(c, result.hits)]
    ok = faq not in top3
    if not ok:
        hit = result.hits[rank - 1]
        detail.append(f"原因：{faq} coverage={hit.coverage:.2f}、bm25={hit.bm25:.2f}，没有学期加分也排在前 3")
    words_ok = 0
    for query, must, must_not in QUESTION_CASES:
        res = c.search(query)
        words = set(res.understood.tokens)
        good = must <= words and not (must_not & words)
        words_ok += good
        detail.append(f"「{query}」比对词 {res.understood.tokens}：{'对' if good else '不对'}（要有 {sorted(must)}"
                      + (f"、不要 {sorted(must_not)}" if must_not else "") + "）；前 3 名 "
                      + "、".join(f"{h.doc_id} {short(c.title(h.doc_id), 20)}" for h in res.hits[:3]))
        ok = ok and good
    term_ok = 0
    for query in TERM_ONLY:
        res = c.search(query)
        tops = res.hits[:3]
        periods = [c.one("SELECT period_ay, period_sem FROM docs WHERE doc_id=?", h.doc_id) for h in tops]
        want = {(int(t.split("-")[0]), int(t.split("-")[1])) for t in res.understood.terms if "-" in t}
        good = res.understood.tokens == [] and bool(tops) and all(tuple(p) in want for p in periods) and faq not in ids
        term_ok += good
        detail.append(f"「{query}」比对词 {res.understood.tokens}、学期记号 {res.understood.canonical}："
                      f"{'对' if good else '不对'}（前 3 名都是 {'、'.join(res.understood.terms)} 的、没有常见问题长页）")
        detail += _top(c, res.hits, 3)
        ok = ok and good
    return Check(f"「{q}」前 3 名不再是常见问题长页；问句用词不切进实词；只剩学期时只搜学期", ok,
                 f"{faq} {where}；例子 {words_ok}/{len(QUESTION_CASES)}；只剩学期 {term_ok}/{len(TERM_ONLY)}", detail)


def check_size_noise(c: Ctx) -> Check:
    """Fix round 2, item 4: link texts end in 「(619775 Kb)」; the size is dropped, so the number and kb are no longer
    words of an attachment's title column."""
    rows = c.all("SELECT doc_id, title, linktext FROM docs WHERE kind_class='attachment'")
    titles = {d: t for d, t, _ in rows}
    tails = [(d, lt) for d, _, lt in rows if SIZE_TAIL.search(lt)]
    noisy: dict[str, list[str]] = {}
    for doc_id, tokens in c.all("SELECT k.doc_id, f.title_tokens FROM chunks k JOIN chunk_fts f ON f.rowid = k.chunk_id"
                                " JOIN docs d ON d.doc_id = k.doc_id WHERE d.kind_class = 'attachment'"):
        words = SIZE_WORDS & set(tokens.split())
        if words and not any(w in titles[doc_id].lower() for w in words):  # not from the file name itself
            noisy.setdefault(doc_id, sorted(words))
    detail = [(f"附件 {len(rows)} 份：连结文字结尾还带档案大小的 {len(tails)} 份；标题栏有 {sorted(SIZE_WORDS)} 这类词"
               f"（档名本身没有）的 {len(noisy)} 份")]
    detail += [f"例：{d} 连结文字「{short(lt, 60)}」" for d, lt in tails[:2]]
    if noisy:
        d = next(iter(noisy))
        tokens = c.one("SELECT f.title_tokens FROM chunks k JOIN chunk_fts f ON f.rowid = k.chunk_id WHERE k.doc_id=?"
                       " LIMIT 1", d)[0]
        detail.append(f"例：{d}（{short(titles[d], 30)}）标题栏「{short(tokens, 120)}」")
    sample = c.one("SELECT doc_id, linktext FROM docs WHERE url = ?", STOP_FILE)  # da2243 in the skeleton
    if sample:
        detail.append(f"{sample[0]}（115-1停修公告.pdf）的连结文字现在是「{sample[1]}」")
    return Check("附件标题栏不再有 kb 这类杂讯词", not noisy and not tails,
                 f"带大小的连结文字 {len(tails)}、标题栏有 kb 的附件 {len(noisy)}", detail)


# ---------------------------------------------------------------- third fix round (11-01)

TURN_WORDS = re.compile(r"新學期|次學期|下學期|上學期|春季|秋季|spring|fall(?=\s*semester|\b)|第\s*[一二12]\s*學期", re.IGNORECASE)


def check_event_turn_terms(c: Ctx) -> Check:
    """Fix round 3, item 2: an event whose title names a semester without its year (第1學期, Spring, Fall) gets that
    semester while it is under way on its first day, else the coming one; 新學期 / 下學期 / 次學期 the one after its
    first day's (the timetable posted in January or June is the coming semester's)."""
    rows = c.all("SELECT d.doc_id, d.title, f.date, d.period_ay, d.period_sem FROM docs d JOIN dates f ON f.doc_id ="
                 " d.doc_id AND f.kind = 'event_start' WHERE d.kind_class = 'event' ORDER BY f.date, d.doc_id")
    lines, good, ahead, plain = [], 0, 0, []
    for doc_id, title, day, ay, sem in rows:
        start = date.fromisoformat(day)
        cur, nxt, got = current_term(start), _next_term(start), Term(ay, sem)
        if not TURN_WORDS.search(title) or any(t.sem for t in find_terms(title)):
            if "課表" in title and not find_terms(title):
                plain.append(f"{day} {short(title, 30)} → {ay}-{sem}")
            continue
        if m := re.search(r"第\s*([一二12])\s*學期", title):
            ok = got.sem == (1 if m.group(1) in "一1" else 2) and got in (cur, nxt)
        elif re.search(r"春季|秋季|spring|fall", title, re.IGNORECASE):
            ok = got.sem == (2 if re.search(r"春季|spring", title, re.IGNORECASE) else 1) and got in (cur, nxt)
        elif re.search(r"新學期|次學期|下學期", title):
            ok = got == nxt
        else:  # 上學期: the one before
            ok = got == (Term(cur.ay - 1, 2) if cur.sem == 1 else Term(cur.ay, 1))
        good += ok
        ahead += got != cur
        lines.append(f"{day} {short(title, 46)} → {ay}-{sem}（开始日当下 {cur.ay}-{cur.sem}）{'' if ok else '  不对'}")
    detail = [f"标题写了学期、没写学年度的事件 {len(lines)} 个（标题写完整学期的照写的算，不在这里）：", *lines,
              "没写学期、照旧用开始日的课表公布（规则没碰）：" + ("；".join(plain) or "没有")]
    return Check("学期交界为下一个学期做的事标成下一个学期", good == len(lines) and bool(lines),
                 f"{len(lines)} 个事件，学期对 {good}/{len(lines)}；标成下一个学期的 {ahead} 个", detail)


# ---------------------------------------------------------------- calendar cutoff (owner 11-01)

# a recurring event's document (plan 2026-11-14 T2: nknu.content.documents.series_text): after its title and its
# 【系統】 line, one line per month an instance starts in, 「2026-09：07、14、21～22」; an instance is its first day,
# with 「～」 and its last day when it runs longer (the day alone in the same month, month-day in another month of the
# year, the whole date in another year), and 「（N 次）」 after days N instances share
SERIES_MONTH = re.compile(r"(\d{4})-(\d{2})：(.+)")
SERIES_ITEM = re.compile(r"(\d{2})(?:～(?:(\d{4})-)?(?:(\d{2})-)?(\d{2}))?(?:（(\d+) 次）)?")
SERIES_SEP = "、"


def series_days(text: str) -> Counter:
    """The days a recurring event's document lists (plan 2026-11-14 T2), read back from documents.series_text's month
    lines: (first day, last day) -> how many instances the text says have them. Empty for a single event's text (its
    title alone). An item that is no day of the calendar (2026-02：30) is not read, so an instance it should stand
    for is not found."""
    listed: Counter = Counter()
    for line in text.split("\n")[2:]:
        month = SERIES_MONTH.fullmatch(line)
        for item in month.group(3).split(SERIES_SEP) if month else ():
            m = SERIES_ITEM.fullmatch(item)
            if m is None:
                continue
            year, mon = int(month.group(1)), int(month.group(2))
            try:
                start = date(year, mon, int(m.group(1)))
                end = start if m.group(4) is None else date(int(m.group(2) or year), int(m.group(3) or mon),
                                                            int(m.group(4)))
            except ValueError:
                continue
            listed[(start, end)] += int(m.group(5) or 1)
    return listed


def events_held(events: list[Event], docs: dict[str, tuple[str, str]]) -> tuple[set[str], dict[str, list[Event]],
                                                                               dict[str, int]]:
    """Which of the calendar's events the database holds, and in which form (plan 2026-11-14 T2). events: those that
    should be documents (from CALENDAR_SINCE on, with a title), one per id; docs: the database's event documents,
    doc_id -> (title, text). Returns
    - own: the ids of the events that are documents of their own ('e' + the id), as every event was before the merge;
    - held: series document -> the instances it holds, by day: an instance that is no document of its own, whose
      recurringEventId (Event.series) is that document's id, whose title on one line is the document's and whose days
      the document's text lists (series_days; two instances on the same days need the text to say two);
    - stray: series document -> how many days it lists that no such instance has (a day the export does not have, or
      one before the cutoff: the document says more than the calendar).
    An instance in neither own nor held is missing: its series' document does not list it, or carries another title.
    A database built without the merge has no series document: held and stray are empty and own is what the check
    compared before. 【Claude】 the title and the days are both asked of the document (the main session's brief of
    11-14 leaves the days to choose), and a day too many fails the check like an event too few: the check is about
    what the calendar holds since 2023, and a day listed for no instance could be one from before."""
    own = {e.id for e in events if "e" + e.id in docs}
    pointing: dict[str, list[Event]] = {}
    for e in events:
        if e.id not in own and e.series and "e" + e.series in docs:
            pointing.setdefault("e" + e.series, []).append(e)
    held: dict[str, list[Event]] = {}
    stray: dict[str, int] = {}
    for doc_id, instances in pointing.items():
        title, text = docs[doc_id]
        listed = series_days(text)
        for e in sorted(instances, key=lambda e: (e.start, e.end_inclusive, e.id)):
            days = (e.start, e.end_inclusive)
            if " ".join(e.title.split()) == title and listed[days] > 0:
                listed[days] -= 1
                held.setdefault(doc_id, []).append(e)
        if left := sum(n for n in listed.values() if n > 0):
            stray[doc_id] = left
    return own, held, stray


def check_calendar_since(c: Ctx) -> Check:
    """Owner 11-01, in place of fix round 3's staff-schedule check (that filter is gone): only calendar events that
    start on or after nknu.content.documents.CALENDAR_SINCE (2023-01-01, a fixed date that does not roll) are
    documents; earlier ones stay in the export and are counted as 行事历(2023年以前). So 「106-1」, a semester wholly
    before the cutoff, brings up no calendar event, and 「115-1」, wholly after it, still brings up 115-1 documents.
    The events are those of every input's export, one per id as the build keeps them (plan 2026-11-08 T5 review: an
    export of a site that shows a calendar brings its events too, and meta export_dir is the first input's alone).
    Plan 2026-11-14 T2: an event is there as a document of its own or, an instance of a recurring event, in its
    series' one document (events_held); what is in neither form is missing, a document no event stands for is one too
    many, and a series document that lists a day no instance has fails the check too. With no series document in the
    database (one built without the merge) the numbers and the words are those of before."""
    exports = input_exports(c.meta)
    events, _ = one_event_per_id([e for path in exports for e in load_calendar(path)])
    old = [e for e in events if e.start < CALENDAR_SINCE]
    wanted = [e for e in events if e.start >= CALENDAR_SINCE and " ".join(e.title.split())]
    want = {"e" + e.id for e in wanted}
    counted = json.loads(c.meta["excluded_json"]).get(EVENT_KIND + BEFORE, 0)
    in_db = {r[0]: (r[1], r[2]) for r in c.all("SELECT doc_id, title, text FROM docs WHERE kind_class='event'")}
    in_db_old, first = c.one("SELECT sum(date < ?), min(date) FROM dates WHERE kind='event_start'",
                             CALENDAR_SINCE.isoformat())
    own, held, stray = events_held(wanted, in_db)
    pointed = {"e" + e.series for e in wanted if e.id not in own and e.series and "e" + e.series in in_db}
    there = own | {e.id for instances in held.values() for e in instances}  # the events found, in either form
    lost, extra = sorted("e" + e.id for e in wanted if e.id not in there), sorted(set(in_db) - want - pointed)
    old_years = Counter(e.start.year for e in old)
    kept_years = Counter(e.start.year for e in events if "e" + e.id in in_db or e.id in there)
    lo, hi = _term_days(Term(111, 1))
    boundary = [e for e in events if lo <= e.start.isoformat() <= hi]
    where = f"{len(exports)} 份导出里" if len(exports) > 1 else "导出里"
    once = "，同一个事件只算一次" if len(exports) > 1 else ""
    in_series = sum(len(instances) for instances in held.values())
    if pointed:  # plan 2026-11-14 T2: a database with merged recurring events
        found = (f"资料库里的事件 {len(in_db)} 份，其中 {len(held)} 份是重复系列合成的、各次的日期列在文件里，共 {in_series} 个；"
                 f"连同各自一份的 {len(own)} 个，一共是导出里 {CALENDAR_SINCE} 起开始、标题不空的 {len(want)} 个里的 "
                 f"{len(there)} 个；少了的 {lost or '没有'}；多出来的 {extra or '没有'}")
        number = (f"事件 {len(there)}/{len(want)}（资料库 {len(in_db)} 份，其中重复系列合成的 {len(held)} 份列了 "
                  f"{in_series} 个）")
    else:  # as before the merge, to the word
        found = (f"资料库里的事件 {len(in_db)} 个 = 导出里 {CALENDAR_SINCE} 起开始、标题不空的 {len(want)} 个；"
                 f"少了的 {lost or '没有'}；多出来的 {extra or '没有'}")
        number = f"事件 {len(in_db)}/{len(want)}"
    detail = [(f"{where}没取消的事件 {len(events)} 个（导出档不动{once}）：{CALENDAR_SINCE} 以前开始的 {len(old)} 个，建置报告记"
               f"「{EVENT_KIND + BEFORE}」{counted}；资料库里 {CALENDAR_SINCE} 以前开始的事件 {in_db_old or 0}，"
               f"最早的开始日 {first}"),
              "排除的年份：" + "、".join(f"{y} 年 {n}" for y, n in sorted(old_years.items())),
              "留下的年份：" + "、".join(f"{y} 年 {n}" for y, n in sorted(kept_years.items())),
              found,
              (f"跨截止日的学期 111-1（{lo}～{hi}）：导出里开始在这学期的事件 {len(boundary)} 个，留下的 "
               f"{sum('e' + e.id in in_db or e.id in there for e in boundary)} 个（都是 2023 年 1 月开始的）；"
               "只看数字，不算过不过")]
    if stray:
        detail.append("重复系列的文件列了导出里没有的日子：" + "、".join(f"{doc_id} 多列了 {n} 个"
                                                                    for doc_id, n in sorted(stray.items())))
    top_ok, top_nums = {}, []
    for q, note in (("106-1", "2017 年秋，整个学期在截止日以前：前 5 名不该有行事历事件"),
                    ("115-1", "2026 年秋，整个学期在截止日以后：前 5 名都该是 115-1 的")):
        hits = c.search(q).hits[:5]
        rows = [c.one("SELECT kind_class, period_ay, period_sem FROM docs WHERE doc_id=?", h.doc_id) for h in hits]
        if q == "106-1":
            n = sum(kind == "event" for kind, _, _ in rows)
            top_ok[q] = n == 0
            top_nums.append(f"「{q}」前 {len(rows)} 名里的行事历事件 {n}")
        else:
            n = sum((ay, sem) == (115, 1) for _, ay, sem in rows)
            top_ok[q] = bool(rows) and n == len(rows)
            top_nums.append(f"「{q}」前 {len(rows)} 名是 115-1 的 {n}")
        detail.append(f"「{q}」（{note}）：{'对' if top_ok[q] else '不对'}，前 {len(hits)} 名：")
        detail += _top(c, hits)
    ok = (not in_db_old and counted == len(old) and not lost and not extra and not stray
          and all(top_ok.values()))
    return Check(f"行事历只收 {CALENDAR_SINCE} 起开始的事件（固定日期、不滚动）", ok,
                 f"资料库里更早的 {in_db_old or 0}；排除 {len(old)}（建置报告 {counted}）；{number}；"
                 + "；".join(top_nums), detail)


# ---------------------------------------------------------------- MCP over stdio

def first_lines(text: str, n: int = 14) -> list[str]:
    pretty = json.dumps(json.loads(text), ensure_ascii=False, indent=1).splitlines()
    return [short(line, 150) for line in pretty[:n]]


async def mcp_roundtrip(db: Path, read_id: str, search: str = "keyword") -> dict:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(command=str(PY), args=server_args(db, search), cwd=str(REPO))
    async with Client(transport) as client:
        listed = await client.list_tools()
        tools = sorted(t.name for t in listed)
        instructions = client.instructions
        description = next((t.description for t in listed if t.name == "search"), None)
        start = time.perf_counter()
        s = await client.call_tool("search", {"query": "115-1 停修"}, raise_on_error=False)
        s_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        r = await client.call_tool("fetch", {"id": read_id}, raise_on_error=False)  # read until 11-17
        r_ms = (time.perf_counter() - start) * 1000
    return {"tools": tools, "search": (s.is_error, s.content[0].text, s_ms), "read": (r.is_error, r.content[0].text, r_ms),
            "instructions": instructions, "description": description}


def results_lines(text: str) -> list[str]:
    body = json.loads(text)
    out = [f"{r['rank']}. {r['id']}｜{r['content_type']}｜{short(r['identity'], 90)}" for r in body.get("results", [])[:5]]
    if body.get("content"):
        out.append("content 开头：" + short(body["content"].replace("\n", " / "), 140))
    return out + [f"notes：{n}" for n in body.get("notes", [])]


def check_mcp(c: Ctx) -> Check:
    read_id = aca_notice(c) or ""  # none: read answers 查無此 id, and the check fails below
    got = asyncio.run(mcp_roundtrip(c.db, read_id, c.mode))
    s_err, s_text, s_ms = got["search"]
    r_err, r_text, r_ms = got["read"]
    s_size, r_size = len(s_text.encode("utf-8")), len(r_text.encode("utf-8"))
    ok = (got["tools"] == ["fetch", "search"] and not s_err and not r_err
          and max(s_size, r_size) <= RESPONSE_BUDGET_BYTES and bool(read_id))
    detail = [f"工具：{got['tools']}；指令：{PY} {' '.join(server_args(c.db, c.mode))}",
              (f"服务说明（instructions）：和 nknu.retrieval.server.texts_for 照这个库生成的 "
               f"{'一字不差' if got['instructions'] == texts_for(c.db).instructions else '不同'}"
               f"：「{got['instructions']}」"),
              f"search 工具说明：「{got['description']}」",
              f"search(query=\"115-1 停修\")：{s_size:,} bytes，{s_ms:.0f} ms，错误={s_err}；前几行：", "```",
              *first_lines(s_text), "```", "结果摘要（每笔：名次、id、内容种类、身份行）：", "```",
              *results_lines(s_text), "```",
              f"fetch(id=\"{read_id}\")：{r_size:,} bytes，{r_ms:.0f} ms，错误={r_err}；前几行：", "```",
              *first_lines(r_text, 20), "```", "```", *results_lines(r_text), "```"]
    return Check("MCP 走 stdio 真的调 search 和 read", ok, f"search {s_size:,} bytes、read {r_size:,} bytes", detail)


# ---------------------------------------------------------------- report

def render(c: Ctx, checks: list[Check], seconds: float) -> str:
    mark = {True: "PASS", False: "FAIL", None: "—"}
    mb = c.db.stat().st_size / 1_000_000
    shown = c.db.relative_to(REPO) if c.db.is_relative_to(REPO) else c.db
    search = ("search_docs（关键词）；MCP 服务加 --no-dense --no-rerank" if c.mode == "keyword"
              else "hybrid_search（服务的默认：向量和重排；资料库没有向量时照样只用关键词）")
    lines = [f"# 教务处{'骨架' if c.meta.get('text_pipeline') != 'totext' else '管线'}：真资料检查", "",
             (f"- 资料库：`{shown}`（{mb:.0f} MB，建于 {c.meta.get('built_at')}，资料截至 "
              f"{c.meta.get('data_as_of')}）"),
             f"- 今天（台北）：{c.today}；搜索：{search}",
             f"- 脚本 `deploy/lab-pipeline/eval/check_realdata.py`（只读），跑了 {seconds:.0f} 秒",
             f"- 结果：{sum(ch.ok is True for ch in checks)} 过、{sum(ch.ok is False for ch in checks)} 不过", "",
             "| # | 检查 | 结果 | 数字 |", "|---|---|---|---|"]
    lines += [f"| {i} | {ch.name} | {mark[ch.ok]} | {ch.numbers} |" for i, ch in enumerate(checks, 1)]
    for i, ch in enumerate(checks, 1):
        lines += ["", f"## {i}. {ch.name}：{mark[ch.ok]}", ""]
        fenced = False
        for d in ch.detail:
            if d == "```":
                fenced = not fenced
                lines.append(d)
            else:
                lines.append(d if fenced else f"- {d}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="教务处资料库的真资料检查（只读），结果写进 --out")
    ap.add_argument("--db", type=Path, default=HERE / "aca.db", help="资料库（默认 research/skeleton_2026-11-01/aca.db）")
    ap.add_argument("--out", type=Path, required=True,
                    help="输出的 Markdown（必给：以前默认写回这个资料夹的 checks.md，会盖掉骨架的结果）")
    ap.add_argument("--search", choices=SEARCHES, default="keyword",
                    help="keyword：search_docs（默认），MCP 服务关掉两个模型；hybrid：hybrid_search（要 uv run --extra pipeline）")
    ap.add_argument("--today", type=date.fromisoformat, default=TODAY, help="当作今天的日期（默认台北的今天）")
    args = ap.parse_args(argv)
    if _protected(args.out, SKELETON):  # the build's guard: this folder, ./data and archive*/ (the exports too)
        ap.error(OUT_REFUSED.format(out=args.out))
    start = time.perf_counter()
    c = Ctx(args.db.resolve(), today=args.today, search=args.search)
    try:
        checks = [check_counts(c), check_identity_dates(c), check_inherits(c), check_server_time(c), check_stop(c),
                  check_same_top3(c), check_withdraw(c), check_scanned(c), check_few_words(c), check_copies(c),
                  check_full_places(c), check_crumbs(c), check_windows(c), check_small_sections(c), check_samples(c),
                  check_not_found(c), check_mcp(c), check_next_term_events(c), check_question_words(c),
                  check_size_noise(c), check_event_turn_terms(c), check_calendar_since(c)]
    finally:
        c.conn.close()
    args.out.write_text(render(c, checks, time.perf_counter() - start), encoding="utf-8")
    for i, ch in enumerate(checks, 1):
        print(f"{i:2} {'PASS' if ch.ok else 'FAIL' if ch.ok is False else '—':4} {ch.name}：{ch.numbers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---- tests: .venv/bin/python -m pytest research/skeleton_2026-11-01/check_realdata.py -q -------------------------

def test_stop_verdict_wants_the_notice_first():
    # was test_stop_verdict_wants_the_notice_first_with_older_versions_folded; changed 11-03 on purpose: no series, so
    # nothing folds and the verdict no longer asks about the older versions (they are results of their own)
    assert stop_verdict(1, False) == (True, "first")
    assert stop_verdict(1, True) == (True, "first")
    assert stop_verdict(2, False) == (False, "first")  # behind anything else it still has to be first
    assert stop_verdict(None, False) == (False, "first")


def test_stop_verdict_top3_only_behind_the_event_that_states_the_period():
    # task 11-01 night: when the first hit is the calendar event giving the application period, the notice in the
    # top 3 is enough (11-03: whether older versions were folded no longer counts)
    assert stop_verdict(3, True) == (True, "top3")
    assert stop_verdict(2, True) == (True, "top3")
    assert stop_verdict(6, True) == (False, "top3_missed")
    assert stop_verdict(None, True) == (False, "top3_missed")


def test_no_series_checks():
    # owner 11-03 (addendum L): none of the six series checks is left, and the MCP round trip reads no series id
    import inspect

    gone = {"check_ties", "check_series_order", "check_exam_series", "check_winter_summer",
            "check_generic_attachments", "check_stop_series_folded", "normalize_title"}
    assert not gone & set(globals())
    assert list(inspect.signature(mcp_roundtrip).parameters) == ["db", "read_id", "search"]
    assert list(inspect.signature(stop_verdict).parameters) == ["rank", "first_event_period"]


def test_aca_notice_is_the_teaching_affairs_nid_662_exactly():
    # plan 2026-11-08 T1: the old selector url LIKE '%nid=662%' also took nid=6620 and another site's notice 662
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE docs (doc_id TEXT, kind_class TEXT, url TEXT)")
    conn.executemany("INSERT INTO docs VALUES (?, 'notice', ?)", [
        ("da", ACA + "newsdetail.aspx?nid=6620&gi=2"), ("db", "https://c.nknu.edu.tw/ccee/newsdetail.aspx?nid=662"),
        ("dc", ACA + "newsdetail.aspx?nid=662&amp;gi=2")])

    class Rows:  # what aca_notice reads: Ctx.all
        def all(self, sql, *args):
            return conn.execute(sql, args).fetchall()

    assert aca_notice(Rows()) == "dc"
    conn.execute("DELETE FROM docs WHERE doc_id = 'dc'")
    assert aca_notice(Rows()) is None
    conn.close()


def test_event_states_period_needs_an_event_with_the_same_days():
    days = ("2026-11-03", "2026-11-16")
    assert event_states_period("event", days, days)
    assert not event_states_period("event", ("2026-11-03", "2026-11-17"), days)
    assert not event_states_period("attachment", days, days)
    assert not event_states_period("event", (None, None), (None, None))


# ---- tests: T10 (plan 2026-11-02) ----------------------------------------------------------------------------------

def test_scanned_verdict():
    assert scanned_verdict("附件:pdf:含OCR", True, "全文") and scanned_verdict("附件:pdf:无文字层", False, "無文字")
    assert not scanned_verdict("附件:pdf:无文字层", False, "全文")
    assert scanned_verdict("附件:pdf:含OCR", True, "章節") and scanned_verdict("附件:pdf:含OCR", True, "片段")
    assert scanned_verdict("附件:无文字(pdf)", False, "無文字")  # the skeleton's scan: found by its title alone
    assert not scanned_verdict("附件:pdf:含OCR", True, "無文字")  # OCR read it, so its words must be shown
    assert not scanned_verdict("附件:pdf:有文字层", True, "全文")  # a text layer: not a scan the OCR read


def test_few_words_kinds_old_and_new():
    assert few_words("附件:pdf:文字很少(多半是扫描+少量字)") and few_words("附件:pdf:无文字层")
    assert not few_words("附件:pdf:含OCR") and not few_words("附件:无文字(pdf)") and not few_words("")


def test_server_args_turn_both_models_off_for_a_keyword_run():
    db = Path("/x/aca.db")
    assert server_args(db, "keyword") == ["-m", "nknu.retrieval.server", "--db", "/x/aca.db", "--no-dense",
                                          "--no-rerank"]
    assert server_args(db, "hybrid") == ["-m", "nknu.retrieval.server", "--db", "/x/aca.db"]


def test_search_function_by_name():
    assert search_function("keyword") is search_docs and search_function("hybrid").__name__ == "hybrid_search"


def test_windows_and_whole_sections_read_past_the_section_marks():  # T9 (addendum E) opens each block with one
    window = "〔第 3 節：三、退費〕\n…\n退費標準如下\n…"
    assert unmarked_content(window) == "…\n退費標準如下\n…" and window_marked(window)
    assert window_marked("〔第 2 節〕\n| 系 | 學分 |\n| - | - |\n…\n| 軟體 | 30 |")  # a table's header row above the cut
    assert not window_marked("〔第 2 節〕\n退費標準如下")
    assert unmarked_content("〔第 1 節〕\n一、總則\n…\n〔第 5 節：五、附則〕\n本要點") == "一、總則\n…\n本要點"

    class Rows:  # what _whole_sections reads: the document's text and its sections' spans
        text = "一、總則\n內容\n二、附則\n本要點"

        def one(self, sql, *args):
            return (self.text,)

        def all(self, sql, *args):
            return [(0, 8), (8, len(self.text))]

    assert _whole_sections(Rows(), "d", "〔第 1 節〕\n一、總則\n內容")
    assert _whole_sections(Rows(), "d", "〔第 1 節〕\n一、總則\n內容\n二、附則\n本要點")
    assert not _whole_sections(Rows(), "d", "〔第 2 節：二、附則〕\n本要點")  # half a section


def test_server_time_is_judged_by_what_the_row_shows():  # 11-19: a 片段 row has the identity line and no dates list
    line = "教務處｜附件（PDF）｜伺服器檔案時間 2021-08-16（伺服器檔案時間）"

    class Lib:
        def __init__(self, rows: dict):
            self.rows = rows  # {title stem: the result row its search gives}

        def on_site(self, alias):
            return "", ()

        def all(self, sql, *args):
            if "FROM dates" in sql:
                return [("d1",), ("d2",)]
            return [("d1", "server", 1, "一百零六學年度會議紀錄.pdf"), ("d2", "server", 1, "一百零七學年度會議紀錄.pdf")]

        def respond(self, stem):
            row = self.rows.get(stem)
            return {"results": [row] if row else []}

    full = {"id": "d2", "rank": 2, "identity": line, "dates": [{"type": "伺服器檔案時間", "date": "2021-08-16"}]}
    lean = {"id": "d1", "rank": 6, "identity": line}
    # a row with its dates is preferred, even when a lean one was found first
    got = check_server_time(Lib({"一百零六學年度會議紀錄": lean, "一百零七學年度會議紀錄": full}))
    assert got.ok and "第 2 名" in got.detail[-1] and "日期种类" in got.detail[-1]
    # only lean rows: the identity line decides, and the report says which level it read
    got = check_server_time(Lib({"一百零六學年度會議紀錄": lean}))
    assert got.ok and "片段那一层" in got.detail[-1] and "第 6 名" in got.detail[-1]
    got = check_server_time(Lib({"一百零六學年度會議紀錄": {**lean, "identity": "教務處｜附件（PDF）｜發布 2021-08-16"}}))
    assert not got.ok
    # a row with its dates whose type is another: not shown as the server's time
    got = check_server_time(Lib({"一百零七學年度會議紀錄": {**full, "dates": [{"type": "發布", "date": "2021-08-16"}]}}))
    assert not got.ok
    assert not check_server_time(Lib({})).ok  # no example found at all: not shown


def test_left_out_records_skip_folded_formats():  # T7a: a format sibling is inside its record, never a record
    excluded = {"行事历(2023年以前)": 374, "album": 20, "附件:office(其他格式)": 15, "附件:pdf:有文字层(副本)": 49}
    assert left_out_records(excluded) == 69
    assert left_out_records({"album": 2}) == 2


def test_a_build_of_two_exports_is_checked_as_teaching_affairs(tmp_path):
    # plan 2026-11-08 T5 review: meta corpus and export_dir are the first input's alone (T4), so check 1 compared every
    # site's documents with 教务处's records (2 records less 1 merged against 3 documents) and check 22 every event
    # with 教务处's calendar (eev3 is 生活輔導組's); both failed on any build of 教务处 and another export. Checks 2-4
    # count 教务处's documents alone, the shared form included: it hangs on 教务处's notice, though the build kept
    # 生活輔導組's newer record of it (docs.site). Task 4's two exports, built alone and together; keyword search only
    import sys

    if str(REPO) not in sys.path:  # run alone, pytest puts only this folder on the path
        sys.path.insert(0, str(REPO))
    from tests.content import test_multisite_1108 as tm

    paths = tm.two_exports(tmp_path)
    got = {}
    for name, inputs in (("alone", paths["inputs"][:1]), ("both", paths["inputs"])):
        tm.run(paths, tmp_path / f"{name}.db", inputs=inputs)
        c = Ctx(tmp_path / f"{name}.db", today=tm.TODAY)
        try:
            got[name] = [check(c) for check in (check_counts, check_identity_dates, check_inherits,
                                                check_server_time, check_calendar_since)]
        finally:
            c.conn.close()
    counts, dates, inherits, server, calendar = got["both"]
    site = sqlite3.connect(f"file:{tmp_path / 'both.db'}?mode=ro", uri=True)
    try:
        assert site.execute("SELECT site FROM docs WHERE doc_id = ?", (paths["form"],)).fetchone() == (
            "sso.nknu.edu.tw/web/595",)  # the newer record of 教务处's form: 生活輔導組's export
    finally:
        site.close()
    assert counts.ok and counts.detail[0].startswith("corpus 记录 4 条（2 份输入加起来）")
    assert got["alone"][0].ok and got["alone"][0].detail[0].startswith("corpus 记录 2 条，")
    for i in (1, 2):  # checks 2 and 3: the same numbers as 教务处 alone (公告 1/1, 行事历 2/2; the form 1/1)
        assert (got["both"][i].ok, got["both"][i].numbers, got["both"][i].detail) == (
            got["alone"][i].ok, got["alone"][i].numbers, got["alone"][i].detail)
    assert dates.numbers == "公告 1/1 有页面日期；行事历 2/2 有日期；无日期 0" and inherits.numbers == "1/1 份"
    assert server.numbers == got["alone"][3].numbers  # no file hangs on an undated page here
    assert calendar.ok and got["alone"][4].ok, calendar.detail
    assert "资料库里的事件 3 个 = 导出里 2023-01-01 起开始、标题不空的 3 个；少了的 没有；多出来的 没有" in calendar.detail
    assert calendar.detail[0].startswith("2 份导出里没取消的事件 3 个（导出档不动，同一个事件只算一次）")


# ---- tests: plan 2026-11-14 T2 (a recurring event's instances are one event document) ------------------------------

def _day(iso: str) -> date:
    return date.fromisoformat(iso)


def test_series_days_reads_back_what_series_text_writes():
    from nknu.content.documents import series_text

    days = [(_day(a), _day(b)) for a, b in (
        ("2026-09-07", "2026-09-07"), ("2026-09-14", "2026-09-14"), ("2026-09-29", "2026-10-01"),  # into the next month
        ("2026-12-30", "2027-01-02"),  # into the next year
        ("2027-01-11", "2027-01-12"), ("2027-01-11", "2027-01-12"), ("2027-01-20", "2027-01-20"))]  # two on the same days
    assert series_days(series_text("族語課", days)) == Counter(days)
    assert sum(series_days(series_text("族語課", days)).values()) == 7
    weekly = [(_day("2023-01-02") + n * (_day("2023-01-09") - _day("2023-01-02")),) * 2 for n in range(230)]
    assert series_days(series_text("週會", weekly)) == Counter(weekly)
    assert series_days("期末考試") == Counter() and series_days("") == Counter()  # a single event: its title alone
    assert series_days("族語課\n【系統】共 1 次，日期（年-月：日）：\n2026-02：30") == Counter()  # no such day: not read


def test_events_held_tells_a_documents_own_event_from_an_instance_in_its_series():
    from nknu.content.documents import series_text

    def ev(eid: str, title: str, start: str, end: str = "", series: str = "") -> Event:
        return Event(eid, title, _day(start), _day(end or start), "", series=series)

    week = [ev("wk_1", "族語課", "2026-09-07", series="wk"), ev("wk_2", "族語課", "2026-09-14", series="wk"),
            ev("wk_3", "族語課", "2026-09-21", "2026-09-22", series="wk")]
    single, renamed = ev("s1", "期末考試", "2026-12-21", "2026-12-25"), ev("wk_4", "族語課（停課）", "2026-09-28", series="wk")
    text = series_text("族語課", [(e.start, e.end_inclusive) for e in week])
    docs = {"es1": ("期末考試", "期末考試"), "ewk": ("族語課", text), "ewk_4": ("族語課（停課）", "族語課（停課）")}
    own, held, stray = events_held([single, *week, renamed], docs)
    assert own == {"s1", "wk_4"} and held == {"ewk": week} and stray == {}
    # an instance the document does not list is in neither form; the three it lists still count
    later = ev("wk_5", "族語課", "2026-10-05", series="wk")
    own, held, stray = events_held([single, *week, renamed, later], docs)
    assert held == {"ewk": week} and "wk_5" not in own and stray == {}
    # one with another title is not in a document that does not carry its title, though that lists its day
    other = ev("wk_1", "族語課補課", "2026-09-07", series="wk")
    own, held, stray = events_held([other, *week[1:]], docs)
    assert held == {"ewk": week[1:]} and stray == {"ewk": 1}  # and the day it lists for no instance is counted
    # two instances on the same days need the document to say so
    twin = ev("wk_9", "族語課", "2026-09-14", series="wk")
    assert events_held([*week, twin], docs)[1] == {"ewk": week}
    twice = {"ewk": ("族語課", series_text("族語課", [(e.start, e.end_inclusive) for e in (*week, twin)]))}
    assert events_held([*week, twin], twice)[1] == {"ewk": [week[0], week[1], twin, week[2]]}
    # a database built without the merge: every instance is a document of its own, as before
    each = {"e" + e.id: (e.title, e.title) for e in (single, *week, renamed)}
    assert events_held([single, *week, renamed], each) == ({"s1", "wk_1", "wk_2", "wk_3", "wk_4"}, {}, {})


def test_the_calendar_check_knows_a_merged_series(tmp_path):
    # plan 2026-11-14 T2: the instances of one recurring event are one event document ('e' + the series id) whose text
    # lists their days. Check 22 compared the export's events one by one with the event documents and failed on every
    # database built with the merge (the 111 sites on 11-14: 「事件 1998/12423」). test_recurring_1114's calendar: a
    # single event, a weekly series of three with a renamed fourth instance, the only instance of another series, a
    # series of two across two terms, one instance before 2023 and one without a title
    import sys

    if str(REPO) not in sys.path:  # run alone, pytest puts only this folder on the path
        sys.path.insert(0, str(REPO))
    from tests.content import test_multisite_1108 as tm
    from tests.content import test_recurring_1114 as tr

    inputs = [tr.calendar_input(tmp_path / "aca", tr.ACA_SITE, "教务处", tr.ITEMS)]
    paths = tm.tree_files(tmp_path / "tree")

    def checked(name: str) -> Check:
        c = Ctx(tmp_path / f"{name}.db", today=tm.TODAY)
        try:
            return check_calendar_since(c)
        finally:
            c.conn.close()

    tm.run(paths, tmp_path / "merged.db", inputs=inputs)
    tm.run(paths, tmp_path / "each.db", inputs=inputs, merge_recurring=False)
    merged, each = checked("merged"), checked("each")
    assert merged.ok, merged.detail
    assert "事件 8/8（资料库 5 份，其中重复系列合成的 2 份列了 5 个）" in merged.numbers
    assert ("资料库里的事件 5 份，其中 2 份是重复系列合成的、各次的日期列在文件里，共 5 个；连同各自一份的 3 个，"
            "一共是导出里 2023-01-01 起开始、标题不空的 8 个里的 8 个；少了的 没有；多出来的 没有") in merged.detail
    assert "留下的年份：2026 年 7、2027 年 1" in merged.detail
    # a database built without the merge is checked as before, to the word
    assert each.ok, each.detail
    assert "事件 8/8；" in each.numbers and "重复系列" not in each.numbers + "".join(each.detail)
    assert "资料库里的事件 8 个 = 导出里 2023-01-01 起开始、标题不空的 8 个；少了的 没有；多出来的 没有" in each.detail
    assert each.detail[2] == merged.detail[2] and each.detail[:2] == merged.detail[:2]

    # an event that should be there and is in neither form: a fifth instance of the weekly series the export has now
    cal = tmp_path / "aca" / "export" / tr.CAL_FILE
    items = json.loads(cal.read_text(encoding="utf-8"))["items"]
    more = [*items, tr.item("wk_20261012", "族語課", "2026-10-12", "2026-10-13", "wk")]
    cal.write_text(json.dumps({"kind": "calendar#events", "items": more}, ensure_ascii=False), encoding="utf-8")
    missing = checked("merged")
    assert not missing.ok and "事件 8/9（" in missing.numbers
    assert any("少了的 ['ewk_20261012']；多出来的 没有" in line for line in missing.detail), missing.detail
    assert not checked("each").ok  # nor is it a document of its own there
    # a single event gone from the database, and a document no event of the export stands for
    cal.write_text(json.dumps({"kind": "calendar#events", "items": [
        *(it for it in items if it["id"] != "wk_20260928"), tr.item("s2", "畢業典禮", "2027-06-05", "2027-06-06")]},
        ensure_ascii=False), encoding="utf-8")
    moved = checked("merged")
    assert not moved.ok and any("少了的 ['es2']；多出来的 ['ewk_20260928']" in line for line in moved.detail), moved.detail

    # a series document that lists a day no instance has (or lacks one of theirs) does not hold them all
    cal.write_text(json.dumps({"kind": "calendar#events", "items": items}, ensure_ascii=False), encoding="utf-8")
    assert checked("merged").ok
    conn = sqlite3.connect(tmp_path / "merged.db")
    conn.execute("UPDATE docs SET text = replace(text, '07、14', '07、15') WHERE doc_id = 'ewk'")
    conn.commit()
    conn.close()
    wrong = checked("merged")
    assert not wrong.ok and any("少了的 ['ewk_20260914']" in line for line in wrong.detail), wrong.detail
    assert any("ewk 多列了 1 个" in line for line in wrong.detail), wrong.detail


def test_a_file_whose_notice_was_merged_into_its_twin_still_inherits(tmp_path):
    # the owner 11-14 23:55 had the notices that stand once on the announcement platform and once on a unit's site
    # merged (nknu.content.build._platform_twins). 教务处's own page of the notice is kept; the file the platform's
    # page links hangs on it from then on, so checks 2-4 count it as 教务处's. Check 3 compared the words the two
    # dates were read from and failed on the official database of 11-15 for 43 such files (「2026/8/5 上午 08:31:00」
    # against 「公告時間： 2026-08-05」, one day)
    import sys

    if str(REPO) not in sys.path:  # run alone, pytest puts only this folder on the path
        sys.path.insert(0, str(REPO))
    from tests.content import test_copies_1114 as tc
    from tests.content import test_multisite_1108 as tm

    title = "圖書館暑假開放時間公告"
    words = "圖書館自七月一日起調整開放時間，週一至週五上午九時至下午五時開放。\n借書期限順延至開學後第一週，逾期不另計罰款。"
    own, posted = tc.ACA + "newsdetail.aspx?nid=1&gi=2", tc.NEWS + "AB12"
    own_file = tc.ACA + "uploadfile/news/1/開放時間表.pdf"
    posted_file = "https://sso.nknu.edu.tw/news/uploadfile/AB12/借書須知.pdf"

    def day(raw: str) -> dict:
        return {"page_date": "2026-06-20", "page_date_raw": raw, "date_source": tc.OWN_FIELD}

    def file(url: str, name: str, text: str, notice: str) -> dict:
        return {"kind": tc.PDF, "url": url, "title": name + ".pdf", "text": text, "linked": notice, "linktext": name,
                "contexts": {notice: name}, "zones": {notice: "attachments"}}

    def checked(name: str, **kw) -> Check:
        root = tmp_path / name
        aca = tc.site_input(root / "aca", tc.ACA_SITE, "教务处", [
            {"kind": "newsdetail", "url": own, "title": title, "text": words, **day("公告時間： 2026-06-20")},
            file(own_file, "開放時間表", "週一至週五上午九時至下午五時", own)])
        news = tc.site_input(root / "news", tc.NEWS_SITE, "全校公告中心", [
            {"kind": "detail:sso_news", "url": posted, "title": title, "text": words.replace("，", ","),
             **day("2026/6/20 上午 08:31:00")},
            file(posted_file, "借書須知", "借書期限順延至開學後第一週", posted)])
        tm.run(tm.tree_files(root / "tree"), tmp_path / f"{name}.db", inputs=[aca, news], **kw)
        c = Ctx(tmp_path / f"{name}.db", today=tm.TODAY)
        try:
            return check_inherits(c)
        finally:
            c.conn.close()

    merged = checked("merged")
    assert merged.ok and merged.numbers == "2/2 份", merged.detail
    assert merged.detail[0] == ("不符的：0；继承日期的原文或日期和所属公告不同的：0；所属公告合并过副本、附件改挂到留下的那份，"
                                "日期同一天、只是原文写法不同的：1（不算不符）")
    # built without the merge the platform's file hangs on the platform's page, which is not 教务处's to count, and
    # nothing is said of merged notices
    apart = checked("apart", platform_twins=False)
    assert apart.ok and apart.numbers == "1/1 份", apart.detail
    assert apart.detail[0] == "不符的：0；继承日期的原文或日期和所属公告不同的：0"


def test_instructions_are_the_servers():
    from nknu.retrieval import server

    assert texts_for is server.texts_for  # design 2026-11-16-mcp-interface-design §八: built per database


def test_main_always_names_its_out_file():
    import pytest

    with pytest.raises(SystemExit) as stop:  # its old default overwrote the skeleton's checks.md
        main(["--db", "/nonexistent/aca.db"])
    assert stop.value.code == 2


def test_main_refuses_an_out_file_in_the_skeleton_folder(tmp_path, capsys):
    import pytest

    db = tmp_path / "none.db"  # nothing to read: without the guard, main fails before it could write anything
    assert not db.exists()
    for out in (HERE / "checks.md", HERE / "checks-by-test.md", HERE / "sub" / ".." / "checks-by-test.md"):
        before = out.stat().st_mtime_ns if out.exists() else None
        with pytest.raises(SystemExit) as stop:  # a mistyped --out would overwrite the skeleton's 11-01 results
            main(["--db", str(db), "--out", str(out)])
        assert stop.value.code == 2 and (out.stat().st_mtime_ns if out.exists() else None) == before, out
    assert "research/skeleton_2026-11-01" in capsys.readouterr().err
    with pytest.raises(sqlite3.OperationalError):  # an --out elsewhere is taken: main goes on and finds no database
        main(["--db", str(db), "--out", str(tmp_path / "checks.md")])
    assert not (tmp_path / "checks.md").exists()
