"""Offline ranking check (spec 2026-11-01, 排序 v2; plan 2026-11-02 T10): where the gold sources of the 33 answerable
questions rank, whether the answer's own words reach the agent, and what the 9 no-answer questions bring up. No agent,
no LLM, no network; the database is opened read-only (mode=ro, immutable).

    python deploy/lab-pipeline/eval/offline_eval.py gold --out research/pipeline_2026-11-02/gold_docs.json \
        [--db …] [--corpus …]               # the gold doc_ids of core20.json + noanswer22.json in one database
    python deploy/lab-pipeline/eval/offline_eval.py gold-diff --gold …/gold_docs.json --db …/aca.db \
        --out …/gold_diff.txt [--base-gold …] [--base-db …]   # two golds, each with its database, by address
    python deploy/lab-pipeline/eval/offline_eval.py run --out …/offline_v2.json      # search_docs; a table on stdout
    python deploy/lab-pipeline/eval/offline_eval.py run --rank '{"recency": false, "tier": false}' --out …
    uv run --extra pipeline python deploy/lab-pipeline/eval/offline_eval.py run --search hybrid \
        [--hybrid '{"rerank": false}'] [--totext research/pipeline_2026-11-02/totext] --db … --gold … --out …
    python deploy/lab-pipeline/eval/offline_eval.py compare --base A.json --new B.json --out report.md

--search keyword (the default) runs nknu.retrieval.search.search_docs. --search hybrid runs nknu.retrieval.hybrid.
hybrid_search with HybridConfig(**--hybrid) and asks for POOL_DOCS + 2 x dense_candidates results, so every candidate of
either pool gets a rank; its two models are the process's shared ones behind a per-run cache (MemoEmbedder,
MemoReranker), loaded only when the database's vectors and the query need them. With the dense path on (the
default), a database without usable vectors of the pinned bge-m3 (built without --embed, partial, or written by
another model or revision) is ranked by keywords alone and neither model loads (main session 11-03); --hybrid
'{"dense": false}' is the rerank-only mode, which loads the reranker whatever the vectors. Run a model-backed hybrid
run only when the machine has the memory. --rank is JSON handed to the search's rank=…, turned into the dataclass
that parameter is annotated with (RankConfig); without --rank no rank is passed and the search uses its own default,
which since 11-01 22:12 is ranking v2 (RankConfig()). RankConfig(recency=False, tier=False) ranks as before ranking v2
(the 11-01 status quo) but for the keyword pool's per-document cap; tier replaced type_prior, w_journal and w_exam on
11-08 (plan addendum M2), so a --rank that names those fails.

Each question is searched twice over: (a) as written; (b) with every search query the agent sent in runs/r3 (or
--runs), keeping the best rank. A gold document is shown at rank k (1-based); since the owner's 11-03 decision no
document folds under another (no series, plan addendum L). The metrics count ranks (top 1/3/10, MRR@10, per group:
core20 and the N questions that have an answer); a question missing the top 10 is counted once as past 10 or missing.
Beside them (plan 2026-11-14 T1) the same numbers with exact copies counted as hits (metrics question_copies,
agent_copies; rank_copies/doc_copies per query, gold_copies and agent_best_copies per question): a document of the same
kind_class whose text without whitespace equals a gold's and has at least 200 characters (exact_copies), since the
school posts one file on several sites; the reports label them 「副本也算」.
run stops with exit code 3 before searching when a gold doc_id is not in the database (regenerate gold for that
database; --allow-missing-gold runs anyway).

Delivery (T10): each question's quotes (core20 sources, noanswer22 evidence, NOT_GOLD files left out, each once) are
cut at 「…」, 「...」 and 「／」 into fragments. A quote is checkable when one document's title and text hold all its
fragments, and delivered when the response the server would send holds them: build_response over the top 10, ranking
kept (the server's note and its 36 KB fit). Everything is compared on letters and digits only (NFKC), so Docling's
table pipes and emphasis never decide a match. The 9 no-answer questions are listed with their top 3 and the best
rerank score of each query, without a threshold (11-01: 先不设门槛). A hybrid run lists the queries whose ranking came
back "keyword" (every query that finds something when the dense path is on and the database has no usable vectors of
the pinned bge-m3; otherwise a model failed and hybrid swallowed it) and, unless --no-scope-effect, reruns the
questions whose text or agent queries name a unit, campus or level without the unit scope (W_SCOPE = W_MENTION = 0;
addendum F): the others rank the same by construction. Every run reports the database's own numbers (build_stats:
file-name titles and sections under 40 characters; addendum G4, G7; the series numbers of D and I1 went with the series
on 11-03); with --totext, the to-text cache's pages per OCR path and numbering marks per 1,000 characters of each text
source (totext_stats; D, G1). compare puts two run reports side by side in Simplified Chinese.

gold-diff (build.sh's step 6) lists the questions whose gold documents changed between two golds, each made for its
own database (the base: the skeleton's gold and database by default). Ids cannot be compared: they moved with every
renumbered export (da4140 became da4142) and all changed with plan 2026-11-08 T1's stable ids (dp32 is du… now). A
document is known by its addresses instead (gold_docs: the location key of its url and of every copy and format listed
under it, after an id left out under another document is resolved through doc_alias or a copies list), and two that
share one are the same.

Every command needs --out, and refuses one inside research/skeleton_2026-11-01/, ./data or archive*/ with exit code 2
(nknu.content.build's own guard): a mistyped --out never overwrites the skeleton's 11-01 results.

Tests: uv run --extra pipeline python -m pytest deploy/lab-pipeline/eval/offline_eval.py -q
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import inspect
import json
import re
import sqlite3
import statistics
import sys
import typing
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from nknu.content import numbering
from nknu.content.build import COPIES_KEY, SKELETON, _protected
from nknu.content.ids import URL as URL_KEY
from nknu.content.ids import location_key
from nknu.content.schema import create_schema
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import CANDIDATES, POOL_DOCS, RankConfig, search_docs
from nknu.retrieval.types import DocHit, SearchResult, Understood

if TYPE_CHECKING:  # imported at run time only by --search hybrid: a keyword run needs neither torch nor the models
    from nknu.retrieval.hybrid import HybridConfig

# 11-19: the program is in the repository (deploy/lab-pipeline/eval; on the lab PC /work/deploy/lab-pipeline/eval);
# its questions, gold and the agents' recorded queries stay in research/eval_2026-11-01, which no commit holds
# (school text), and the baseline library in research/skeleton_2026-11-01.
REPO = next((d for d in Path(__file__).resolve().parents if (d / "src" / "nknu").is_dir()),
            Path(__file__).resolve().parents[3])
HERE = REPO / "research" / "eval_2026-11-01"  # the data folder the defaults below are read from
DB = REPO / "research" / "skeleton_2026-11-01" / "aca.db"
TODAY = date(2026, 11, 1)  # fixed for every run (spec 2026-11-01)
TOP = 10  # what the agent sees; MRR is MRR@10
DEEP = POOL_DOCS  # results asked for: the keyword pool's documents (11-08, addendum M1), so every candidate gets a rank
GROUPS = ("core20", "N")
EXPORT_PREFIX = "archive-export/aca-20261027/"
SEARCHES = ("keyword", "hybrid")
OUT_REFUSED = ("--out {out} 落在骨架的资料夹 research/skeleton_2026-11-01/、./data 或 archive*/ 里，"
               "这些地方一律不写；换一个 --out")
QUOTE_SPLIT = re.compile(r"…|\.\.\.|／")  # where the evaluators left words out of a quote
SHORT_SECTION = 40  # G7: a "crumb" section has fewer characters than this
# G4: a title that is only a file name (the export's name for an attachment no page names)
FILE_TITLE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|od[tsp]|rtf|txt|csv|zip|rar|7z|jpe?g|png|gif|bmp|tiff?)$",
                        re.IGNORECASE)
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
NUMBER_SOURCES = ("ocr_pages", "docling_ocr", "text_layer", "image")  # G1, in the order the tables show them
# G1: what nknu.content.numbering.marks splits sections on, without its per-text group choice: every numbering pattern
# but the English group's; a mark written with Han numerals (一、 （一） 第一條 壹、) is what PP-OCRv6 small misses
_MARK_PATTERNS = tuple(re.compile(p) for name, patterns in numbering.GROUPS if name != "en" for p in patterns)
_NOT_MARK = tuple(re.compile(p) for p in numbering.NOT_BULLET)
_HAN_NUMERAL = re.compile(f"[{numbering._N}{numbering._FORMAL}]")  # the module's own numeral sets

# A calendar source names an event by its title, which recurs every term; each entry is the event of the term the
# expected answer uses (checked by hand against docs and dates; the note written to gold_docs.json shows term and days).
CALENDAR: dict[str, dict[str, list[str]]] = {
    "Q01": {"申辦停修": ["e10kt6s1i12424pqmif1bkmifcm"]},
    "Q07": {"大學部網路選課": ["e1kg189cua7ioikprb2jb3u4b8c"]},
    "Q13": {"申辦抵免學分": ["e4tlhukkj3kcpr2sb1o4vik0uup"]},
    "Q15": {"申辦抵免學分": ["e4tlhukkj3kcpr2sb1o4vik0uup"]},
    "Q21": {"休學截止": ["e5gfs2pne0d5dkc6o0q8drggj2u"], "退費1/3": ["e143qdhcefn3vjc2jmoqsa85no3"]},
    "Q25": {"轉系": ["e4ng5uoo8c761dk6l0lvvpaqjuo"]},
    "Q45": {"論文考試": ["e3o5fce0fjo11oqfnaefclkcc33", "e0j5j45i766o9nvu39t1gcc570d"]},
    "N21": {"學科能力測驗": ["e32pnprp8a6fkiovaiqskffpj9k"]},  # the only one since 2023 left: 2026-01, already past
}
# Listed sources that the question's own expected answer or note rules out (a trap, a pre-reform procedure, another
# term or cohort): named in the note, not counted. Keyed by the export file's number.
NOT_GOLD: dict[str, dict[str, str]] = {
    "Q07": {"pages/0816": "nid=574 是 114-2 的选课注意事项，预期答案点名是陷阱"},
    "Q36": {"files/0097": "旧的「學生證發給及使用注意事項」，预期答案说是 115/5/1 以前的流程、不能排在前面",
            "pages/0094": "Q&A 的传真挂失说明，预期答案说是 115/5/1 以前的流程、不能排在前面"},
    "Q39": {"pages/0661": "nid=656 是 114-2 的前三名公告，不是题目问的 113-2，导出里也没有名单"},
    "Q50": {"files/0408": "115 学年度入学版的开课系统表，预期答案说 114 入学生不适用",
            "files/0372": "115 学年度入学版的会议议程，预期答案说 114 入学生不适用"},
    "N10": {"files/2440": "2021 年的旧申请表（大一英文 B1＝多益 550），note 点名是版本陷阱"},
}


@dataclass(frozen=True)
class Place:
    rank: int | None = None  # 1-based position of the first gold hit
    doc: str | None = None


def _rel(path: str) -> str:
    return unicodedata.normalize("NFC", path.removeprefix(EXPORT_PREFIX))


def _short(rel: str) -> str:
    """'pages/0265-album_images-…' -> 'pages/0265'."""
    return rel.split("-", 1)[0]


def _nid(url: str) -> str | None:
    parts = urlsplit(html.unescape(url))
    return (parse_qs(parts.query).get("nid") or [None])[0] if parts.path.lower().endswith("newsdetail.aspx") else None


def _notice_key(url: str) -> str | None:
    """The identity of a notice page with an nid (ids.location_key: on a c.nknu-template host one site and nid, plan
    2026-11-08 T1), None for any other address: Mapper.nid_doc's key, so 教務處's nid 578 never maps to another site's
    notice 578, as the build's dedupe (documents._link_key) keeps them apart."""
    return location_key(url) if _nid(url) else None


def is_calendar(rel: str) -> bool:
    return rel.startswith("data/") and ("行事历" in rel or "行事曆" in rel)


class Mapper:
    """Gold sources (export files, calendar quotes) -> doc_ids of one built database; corpus: the 10-29 corpus.json
    rows the database was built from (doc_id = 'd' + id)."""

    def __init__(self, conn: sqlite3.Connection, corpus: list[dict], *, calendar: dict = CALENDAR,
                 not_gold: dict = NOT_GOLD) -> None:
        self.calendar_table, self.not_gold = calendar, not_gold
        self.docs = {r[0]: r[1:] for r in conn.execute(
            "SELECT doc_id, kind_class, title, period_ay, period_sem FROM docs")}
        self.nid_doc = {key: r[0] for r in conn.execute("SELECT doc_id, url FROM docs WHERE kind_class = 'notice'")
                        if (key := _notice_key(r[1]))}  # site-aware since plan 2026-11-08 T1 (was nid alone)
        self.days = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT doc_id, MIN(CASE WHEN kind = 'event_start' THEN date END),"
            " MAX(CASE WHEN kind = 'event_end' THEN date END) FROM dates GROUP BY doc_id")}
        self.copy_of = {c["url"]: key.removeprefix(COPIES_KEY) for key, value in conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ?", (COPIES_KEY + "%",)) for c in json.loads(value)
            if c.get("url")}
        self.by_file: dict[str, list[dict]] = {}
        self.by_url: dict[str, list[dict]] = {}
        for r in corpus:
            self.by_file.setdefault(_rel(r.get("file") or ""), []).append(r)
            self.by_url.setdefault(r.get("url") or "", []).append(r)

    def _record(self, r: dict, rel: str) -> tuple[str | None, str]:
        doc_id = "d" + str(r["id"])
        if doc_id in self.docs:
            return doc_id, ""
        if (kept := self.copy_of.get(r.get("url") or "")) is not None:
            return kept, f"{_short(rel)}（{doc_id}）是 {kept} 的副本，建库时合并了，算 {kept}"
        if (key := _notice_key(r.get("url") or "")) and (kept := self.nid_doc.get(key)):
            return kept, f"{_short(rel)}（{doc_id}）是 nid={_nid(r.get('url') or '')} 的重复记录，算 {kept}"
        return None, f"{_short(rel)}（{doc_id}，{r.get('kind')}）没进资料库，对应不到"

    def file(self, path: str, url: str | None = None) -> tuple[list[str], str]:
        rel = _rel(path)
        records, notes = (self.by_file.get(rel) or []) if rel else [], []
        if not records and url and (records := self.by_url.get(url) or []):
            notes.append(f"{_short(rel)} 按网址对应")
        if not records:
            return [], f"{_short(rel) or url} 在 corpus 里没有，对应不到"
        ids: list[str] = []
        for r in records:
            doc_id, note = self._record(r, rel)
            ids += [doc_id] if doc_id and doc_id not in ids else []
            notes += [note] if note else []
        if len(ids) > 1:
            notes.append(f"{_short(rel)} 在 corpus 里有 {len(records)} 笔（{'、'.join(ids)}），都算")
        return ids, "；".join(notes)

    def calendar(self, qid: str, quote: str) -> tuple[list[str], str]:
        entry = self.calendar_table.get(qid, {})
        key = next((k for k in entry if k in quote), None)
        if key is None:
            return [], f"行事历「{quote[:24]}」没有对应的事件表，对应不到"
        parts = []
        for e in entry[key]:
            kind, title, ay, sem = self.docs.get(e, (None, "", None, None))
            if kind != "event" or key not in title:
                raise ValueError(f"{qid}: {e} is not a calendar event titled …{key}…")
            start, end = self.days.get(e, (None, None))
            parts.append(f"{e}（{ay}-{sem}，{start}～{end}）")
        return list(entry[key]), f"行事历「{key}」取 {'、'.join(parts)}"

    def question(self, q: dict) -> dict:
        """{'docs': [...], 'note': '...'}: every source mapped (copies to the kept one), ruled-out ones left out."""
        rules, used, seen = self.not_gold.get(q["id"], {}), set(), set()
        docs: list[str] = []
        notes: list[str] = []
        for s in q["sources"]:
            rel = _rel(s.get("file") or "")
            if is_calendar(rel):
                ids, note = self.calendar(q["id"], s.get("quote") or "")
            elif rel in seen:
                continue
            else:
                seen.add(rel)
                ids, note = self.file(rel, s.get("url"))
                if (rule := next((p for p in rules if rel.startswith(p + "-")), None)) is not None:
                    used.add(rule)
                    notes.append(f"{_short(rel)}（{'、'.join(ids) or '没对应'}）不算：{rules[rule]}")
                    continue
            docs += [i for i in ids if i not in docs]
            notes += [note] if note and note not in notes else []
        if unused := sorted(set(rules) - used):
            raise ValueError(f"{q['id']}: NOT_GOLD names no source: {unused}")
        return {"docs": docs, "note": "；".join(notes)}


def agent_queries(summary: list[dict]) -> dict[str, list[str]]:
    """question id -> the search queries the agent sent (runs/<run>/summary.json), each once, in order."""
    return {row["id"]: list(dict.fromkeys(
        c["input"]["query"] for c in row.get("calls") or []
        if c.get("name", "").endswith("__search") and (c.get("input") or {}).get("query")))
        for row in summary}


def _rank_class(search: Callable) -> type | None:
    """The dataclass search's rank parameter is annotated with (also inside `X | None`), if any."""
    try:
        hint = typing.get_type_hints(search).get("rank")
    except (NameError, TypeError, AttributeError, SyntaxError):  # unresolvable: hand the setting over as it is
        return None
    return next((t for t in (hint, *typing.get_args(hint)) if isinstance(t, type) and dataclasses.is_dataclass(t)),
                None)


def rank_kwargs(search: Callable, rank: object) -> tuple[dict, bool]:
    """({'rank': rank}, True) when a setting is given and search takes rank (a dict becomes the dataclass rank is
    annotated with); ({}, False) otherwise: then search runs with its own default."""
    if rank is None or "rank" not in inspect.signature(search).parameters:
        return {}, False
    cls = _rank_class(search)
    return {"rank": cls(**rank) if cls is not None and isinstance(rank, dict) else rank}, True


def hybrid_fn(config: HybridConfig, *, embedder=None, reranker=None) -> Callable:
    """nknu.retrieval.hybrid.hybrid_search with config (a HybridConfig) and stand-in models, as a function the way
    search_docs is called: (conn, query, *, today, aliases, limit=10, rank: RankConfig | None = None). A closure, so
    rank_kwargs and _rank_class still see the RankConfig annotation; .config names the settings (run asks for
    POOL_DOCS + 2 x config.dense_candidates results). Imported here: a keyword run needs neither torch nor the models."""
    from nknu.retrieval.hybrid import hybrid_search

    def search(conn, query, *, today, aliases, limit=10, rank: RankConfig | None = None):
        return hybrid_search(conn, query, today=today, aliases=aliases, limit=limit, rank=rank, config=config,
                             embedder=embedder, reranker=reranker)

    search.config = config
    return search


class MemoEmbedder:
    """The process's shared bge-m3 behind a per-run cache of query vectors, so the scope-free rerun encodes nothing
    twice. model_id and revision are the pins (hybrid compares them with the index's before anything loads); the
    model loads on the first encode, inside hybrid's own try, so a missing model still means keyword ranking."""

    def __init__(self, load: Callable | None = None) -> None:
        from nknu.core import models

        self.model_id, self.revision, self.dim = models.EMBED_ID, models.EMBED_REVISION, models.EMBED_DIM
        self._load = load or models.shared_embedder
        self._vecs: dict[str, object] = {}

    def encode(self, texts: list[str], *, batch_size: int = 16):
        import numpy as np

        texts = list(texts)
        missing = [t for t in dict.fromkeys(texts) if t not in self._vecs]
        if missing:
            self._vecs.update(zip(missing, self._load().encode(missing, batch_size=batch_size), strict=True))
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vecs[t] for t in texts])


class MemoReranker:
    """The process's shared reranker behind a per-run cache of (query, passage) scores, loaded on the first score."""

    def __init__(self, load: Callable | None = None) -> None:
        from nknu.core import models

        self._load = load or models.shared_reranker
        self._scores: dict[tuple[str, str], float] = {}

    def score(self, query: str, passages: list[str], *, batch_size: int = 16) -> list[float]:
        passages = list(passages)
        missing = [p for p in dict.fromkeys(passages) if (query, p) not in self._scores]
        if missing:
            got = list(self._load().score(query, missing, batch_size=batch_size))
            if len(got) != len(missing):
                raise RuntimeError(f"重排模型给了 {len(got)} 个分数，应该是 {len(missing)} 个")
            self._scores.update(((query, p), float(s)) for p, s in zip(missing, got, strict=True))
        return [self._scores[(query, p)] for p in passages]


def _uses_models(search: Callable) -> bool:
    """search is a hybrid_fn whose settings turn a model on (only then is a "keyword" ranking a fallback)."""
    config = getattr(search, "config", None)
    return bool(getattr(config, "dense", False) or getattr(config, "rerank", False))


def place(result: SearchResult, gold: set[str]) -> Place:
    return Place(*next(((i, h.doc_id) for i, h in enumerate(result.hits, 1) if h.doc_id in gold), (None, None)))


def _in_top(rank: int | None, k: int = TOP) -> bool:
    return rank is not None and rank <= k


def _why_missed(p: Place) -> str:
    return "past10" if p.rank is not None else "missing"


def metrics(places: list[Place]) -> dict:
    """Top 1/3/10 counts and MRR@10; each question outside the top 10 is past10 (shown lower down) or missing, so
    top10 + past10 + missing = n."""
    missed = Counter(_why_missed(p) for p in places if not _in_top(p.rank))
    mrr = sum(1 / p.rank for p in places if _in_top(p.rank)) / len(places) if places else 0.0
    return {"n": len(places), **{f"top{k}": sum(_in_top(p.rank, k) for p in places) for k in (1, 3, TOP)},
            "mrr": round(mrr, 4), **{k: missed[k] for k in ("past10", "missing")}}


def _run(result: SearchResult, text: str, gold: set[str], widened: set[str] | None = None) -> dict:
    """One query's line in the report: where the gold stands, the top 10, and how the result was ranked. With widened
    (the gold and its exact copies, plan 2026-11-14 T1) also rank_copies and doc_copies: the first hit in that set."""
    run = {"query": text, **dataclasses.asdict(place(result, gold)), "top": [h.doc_id for h in result.hits[:TOP]],
           "ranking": result.ranking, "best_rerank": result.best_rerank}
    if widened is not None:
        wide = place(result, widened)
        run.update({"rank_copies": wide.rank, "doc_copies": wide.doc})
    return run


def _best(runs: list[dict], suffix: str = "") -> dict:
    """The best shown rank over the agent's queries, with its gold document and query; suffix '_copies' reads the
    widened ranks (plan 2026-11-14 T1)."""
    rank, doc = "rank" + suffix, "doc" + suffix
    found = [r for r in runs if r.get(rank) is not None]
    shown = min(found, key=lambda r: r[rank]) if found else None
    return {"rank": shown and shown[rank], "doc": shown and shown[doc], "query": shown and shown["query"]}


def _as_place(run: dict) -> Place:
    return Place(run["rank"], run["doc"])


# ---------------------------------------------------------------- exact copies count (plan 2026-11-14 T1)

COPY_MIN = 200  # plan 2026-11-14 T1: a copy's whitespace-free text has at least this many characters
COPY_SPREAD = 2  # 【Claude】 prefilter: a copy's chars (whitespace counted) is at most this times its whitespace-free length
_SPACE = re.compile(r"\s+")


def _bare(text: str) -> str:
    """The text with all whitespace (str.isspace's: newlines, tabs, U+3000 and the like) removed."""
    return _SPACE.sub("", text or "")


def exact_copies(conn: sqlite3.Connection | None, gold_ids: list[str]) -> dict[str, list[str]]:
    """gold id -> the doc_ids of its exact copies (plan 2026-11-14 T1): same kind_class, the same text once all
    whitespace is removed, at least COPY_MIN such characters; the gold itself is not its own copy. A gold the database
    lacks, or a short one, has none. One pass over (doc_id, kind_class, chars) keeps the documents whose chars could
    hold that bare text (bare length <= chars <= COPY_SPREAD x bare length); only those texts are read, one at a time,
    and compared."""
    out: dict[str, list[str]] = {g: [] for g in dict.fromkeys(gold_ids)}
    if conn is None or not out:
        return out
    bare: dict[str, tuple[str, str]] = {}  # gold id -> (kind_class, bare text)
    for g in out:
        row = conn.execute("SELECT kind_class, text FROM docs WHERE doc_id = ?", (g,)).fetchone()
        if row and len(text := _bare(row[1])) >= COPY_MIN:
            bare[g] = (row[0], text)
    if not bare:
        return out
    wanted: dict[str, list[tuple[int, str]]] = {}  # kind_class -> (bare length, gold id)
    for g, (kind, text) in bare.items():
        wanted.setdefault(kind, []).append((len(text), g))
    kinds = list(wanted)
    candidates: list[tuple[str, list[str]]] = []
    for doc_id, kind, chars in conn.execute(
            f"SELECT doc_id, kind_class, chars FROM docs WHERE kind_class IN ({','.join('?' * len(kinds))})", kinds):
        golds = [g for n, g in wanted[kind] if g != doc_id and n <= chars <= COPY_SPREAD * n]
        if golds:
            candidates.append((doc_id, golds))
    for doc_id, golds in candidates:
        row = conn.execute("SELECT text FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
        text = _bare(row[0] if row else "")
        for g in golds:
            if text == bare[g][1]:
                out[g].append(doc_id)
    return {g: sorted(ids) for g, ids in out.items()}


# ---------------------------------------------------------------- delivery (T10)

def norm_quote(s: str) -> str:
    """NFKC, then only the characters of Unicode category L* or N*: Docling's table pipes, ** emphasis, punctuation
    and whitespace never decide a match."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", s) if unicodedata.category(ch)[0] in "LN")


def fragments(quote: str) -> list[str]:
    """The QUOTE_SPLIT pieces of a quote, normalised; pieces under 2 characters dropped (they would match anywhere)."""
    return [f for piece in QUOTE_SPLIT.split(quote) if len(f := norm_quote(piece)) >= 2]


def strings_of(obj: object) -> str:
    """Every str value of a JSON-like object (dict values and list items, nested), joined by "\\n"; keys left out."""
    out: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list | tuple):
            for v in value:
                walk(v)

    walk(obj)
    return "\n".join(out)


def question_quotes(q: dict, not_gold: dict = NOT_GOLD) -> list[str]:
    """The quotes of a question's sources (core20 sources, noanswer22 evidence), minus those of the files NOT_GOLD rules
    out for it, each once, in order."""
    rules = not_gold.get(q["id"], {})
    out: list[str] = []
    for s in q.get("sources") or []:
        quote, rel = (s.get("quote") or "").strip(), _rel(s.get("file") or "")
        if quote and quote not in out and not any(rel.startswith(p + "-") for p in rules):
            out.append(quote)
    return out


def _holds(normalised: str, quote: str) -> bool:
    frags = fragments(quote)
    return bool(frags) and all(f in normalised for f in frags)


def delivered(resp: dict, quote: str) -> bool:
    """Every fragment of the quote is inside norm_quote(strings_of(resp)); a quote with no fragment is never
    checkable, so never delivered."""
    return _holds(norm_quote(strings_of(resp)), quote)


def load_noanswer(noanswer: Path) -> list[dict]:
    """noanswer22.json's questions that have no answer in the data (use_as_noanswer true; the 9 true no-answer ones)."""
    return [{"id": q["id"], "group": "noanswer", "question": q["question"], "sources": q.get("evidence") or []}
            for q in json.loads(Path(noanswer).read_text(encoding="utf-8")) if q.get("use_as_noanswer") is True]


def checkable_quotes(conn, quotes: list[str]) -> dict[str, bool]:
    """quote -> one document's norm_quote(title + "\\n" + text) holds all its fragments: one pass over docs, one
    document in memory at a time. Without a database nothing is checkable."""
    frags = {q: fragments(q) for q in dict.fromkeys(quotes)}
    pending = {q for q, f in frags.items() if f}
    found: set[str] = set()
    if conn is not None and pending:
        for title, text in conn.execute("SELECT title, text FROM docs"):
            normalised = norm_quote(f"{title}\n{text}")
            hit = {q for q in pending if all(f in normalised for f in frags[q])}
            found |= hit
            pending -= hit
            if not pending:
                break
    return {q: q in found for q in frags}


def _response_text(conn, result: SearchResult, today: date) -> str:
    """What the server would send for this result (its top 10, ranking kept), letters and digits only."""
    return norm_quote(strings_of(build_response(conn, dataclasses.replace(result, hits=result.hits[:TOP]),
                                                today=today)))


def _delivery(rows: list[dict]) -> dict:
    """Per mode: delivered checkable quotes, checkable quotes, questions with at least one delivered checkable quote,
    questions, and all quotes (checkable or not)."""
    out = {}
    for mode in ("question", "agent"):
        flags = [[bool(d and c) for d, c in zip(r.get(f"{mode}_delivered") or [], r.get("checkable") or [],
                                                strict=False)] for r in rows]
        out[mode] = {"quotes": sum(map(sum, flags)), "checkable": sum(sum(r.get("checkable") or []) for r in rows),
                     "questions_any": sum(map(any, flags)), "n": len(rows),
                     "total": sum(len(r.get("quotes") or []) for r in rows)}
    return out


def evaluate(conn, questions: list[dict], gold: dict, queries: dict[str, list[str]], *, today: date, aliases: list,
             rank: object = None, search: Callable = search_docs, deep: int = DEEP, deliver: bool = True,
             noanswer: list[dict] | tuple = ()) -> dict:
    """Ranks of each question's gold documents, (a) for the question as written, (b) best over the agent's queries;
    metrics per group and mode. Questions without a gold document are only listed (no_gold). deliver: which of each
    question's quotes the replayed responses hold (metrics["delivery"]). noanswer: those questions' top 3 and best
    rerank scores (report["noanswer"], metrics["best_rerank"]). ranking_fallbacks: a model-backed hybrid run's
    queries whose ranking came back "keyword" although something was found."""
    kw, applied = rank_kwargs(search, rank)
    fallbacks: list[str] = []
    models_on = _uses_models(search)

    def ask(text: str) -> SearchResult:
        result = search(conn, text, today=today, aliases=aliases, limit=deep, **kw)
        if models_on and result.candidates and result.ranking != "hybrid" and text not in fallbacks:
            fallbacks.append(text)
        return result

    quotes_of = {q["id"]: question_quotes(q) for q in questions} if deliver else {}
    checkable = checkable_quotes(conn, [x for qs in quotes_of.values() for x in qs]) if deliver else {}
    copies = exact_copies(conn, [d for q in questions for d in (gold.get(q["id"]) or {}).get("docs") or []])
    rows, no_gold = [], []
    for q in questions:
        docs = list((gold.get(q["id"]) or {}).get("docs") or [])
        if not docs:
            no_gold.append(q["id"])
            continue
        found_copies = sorted({c for d in docs for c in copies.get(d, [])} - set(docs))
        widened = set(docs) | set(found_copies)  # plan 2026-11-14 T1: exact copies count as hits
        asked = ask(q["question"])
        agent = [(t, ask(t)) for t in queries.get(q["id"], [])]
        runs = [_run(r, t, set(docs), widened) for t, r in agent]
        row = {"id": q["id"], "group": q["group"], "question": q["question"], "gold": docs,
               "question_query": _run(asked, q["question"], set(docs), widened), "agent_queries": runs,
               "agent_best": _best(runs), "gold_copies": found_copies, "agent_best_copies": _best(runs, "_copies")}
        if deliver:
            quotes = quotes_of[q["id"]]
            texts = ([_response_text(conn, asked, today)], [_response_text(conn, r, today) for _, r in agent]
                     ) if quotes else ([], [])
            row.update({"quotes": quotes, "checkable": [checkable.get(x, False) for x in quotes],
                        "question_delivered": [any(_holds(t, x) for t in texts[0]) for x in quotes],
                        "agent_delivered": [any(_holds(t, x) for t in texts[1]) for x in quotes]})
        rows.append(row)
    unanswerable = []
    for q in noanswer:
        asked = ask(q["question"])
        unanswerable.append({"id": q["id"], "question": q["question"],
                             "question_top3": [h.doc_id for h in asked.hits[:3]],
                             "question_best_rerank": asked.best_rerank,
                             "agent": [{"query": t, "top3": [h.doc_id for h in r.hits[:3]],
                                        "best_rerank": r.best_rerank}
                                       for t in queries.get(q["id"], []) for r in (ask(t),)]})
    table: dict = {mode: {grp: metrics([_as_place(r[field]) for r in rows if grp in ("all", r["group"])])
                          for grp in (*GROUPS, "all")}
                   for mode, field in (("question", "question_query"), ("agent", "agent_best"))}
    # plan 2026-11-14 T1: the same numbers with exact copies of a gold document counted as hits
    table["question_copies"] = {grp: metrics([Place(r["question_query"]["rank_copies"],
                                                    r["question_query"]["doc_copies"])
                                              for r in rows if grp in ("all", r["group"])]) for grp in (*GROUPS, "all")}
    table["agent_copies"] = {grp: metrics([_as_place(r["agent_best_copies"]) for r in rows
                                           if grp in ("all", r["group"])]) for grp in (*GROUPS, "all")}
    if deliver:
        table["delivery"] = _delivery(rows)
    table["best_rerank"] = {
        "answerable": sorted(r["question_query"]["best_rerank"] for r in rows
                             if r["question_query"]["best_rerank"] is not None),
        "noanswer": sorted(n["question_best_rerank"] for n in unanswerable if n["question_best_rerank"] is not None)}
    return {"rank_applied": applied, "metrics": table, "questions": rows, "no_gold": no_gold,
            "noanswer": unanswerable, "ranking_fallbacks": fallbacks}


def load_questions(core: Path, noanswer: Path) -> list[dict]:
    """core20.json (all, group 'core20') and the noanswer22.json questions that have an answer (use_as_noanswer
    false, group 'N'); sources: core20's sources, noanswer22's evidence."""
    out = [{"id": q["id"], "group": "core20", "question": q["question"], "sources": q.get("sources") or []}
           for q in json.loads(Path(core).read_text(encoding="utf-8"))]
    return out + [{"id": q["id"], "group": "N", "question": q["question"], "sources": q.get("evidence") or []}
                  for q in json.loads(Path(noanswer).read_text(encoding="utf-8")) if q.get("use_as_noanswer") is False]


def connect_ro(path: Path) -> sqlite3.Connection:
    """Read-only and immutable: nothing is written beside the file, not even a -shm or -wal (the skeleton's database
    is never touched)."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def missing_gold(conn: sqlite3.Connection, gold: dict) -> list[str]:
    """The gold doc_ids this database does not hold (gold made for another build), sorted."""
    ids = sorted({d for v in gold.values() for d in (v or {}).get("docs") or []})
    if not ids:
        return []
    have = {r[0] for r in conn.execute(f"SELECT doc_id FROM docs WHERE doc_id IN ({','.join('?' * len(ids))})", ids)}
    return [d for d in ids if d not in have]


# ---------------------------------------------------------------- two golds by address (plan 2026-11-08 T1)

NOT_THERE = "?"  # GoldDoc.addresses of an id its database does not hold: '?' + the id, which matches only itself
GOLD_TITLE = 30  # characters of a title gold-diff shows


@dataclass(frozen=True)
class GoldDoc:
    """A gold doc_id as its own database knows it (gold_docs)."""

    doc_id: str  # as the gold file writes it
    kept: str | None  # the document the database keeps for it: itself, or the one an alias points at; None: not there
    title: str
    address: str  # ids.location_key of the kept document's url; '' when the database has no such document
    addresses: frozenset[str]  # its own address, those of the copies and formats listed under it, an event's id


def _doc_row(conn: sqlite3.Connection, doc_id: str) -> tuple[str, str, str] | None:
    return conn.execute("SELECT kind_class, title, url FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()


def gold_docs(conn: sqlite3.Connection, doc_ids: list[str]) -> dict[str, GoldDoc]:
    """Each doc_id (once) as conn's database knows it: a document it holds, else the one its doc_alias row (a build
    since plan 2026-11-08 T1) or a copies:<kept> entry with that id points at. The addresses are ids.location_key of
    the kept document's url (no scheme, &amp; undone, a c.nknu notice by its site and nid) and of each copy and format
    meta copies:<kept> lists, so a build that kept another copy of a file still has the same document; an event's id
    ('e' + the Google event id, the same in every id scheme) counts as one too."""
    aliased = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_alias'").fetchone()
    copies = {key.removeprefix(COPIES_KEY): [c for c in json.loads(value) if isinstance(c, dict)]
              for key, value in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (COPIES_KEY + "%",))}
    listed = {c["id"]: kept for kept, entries in copies.items() for c in entries if c.get("id")}
    out: dict[str, GoldDoc] = {}
    for doc_id in dict.fromkeys(doc_ids):
        kept, row = doc_id, _doc_row(conn, doc_id)
        if row is None:
            alias = aliased and conn.execute("SELECT doc_id FROM doc_alias WHERE alias = ?", (doc_id,)).fetchone()
            kept = alias[0] if alias else listed.get(doc_id)
            row = _doc_row(conn, kept) if kept else None
        if row is None:
            out[doc_id] = GoldDoc(doc_id, None, "", "", frozenset({NOT_THERE + doc_id}))
            continue
        kind, title, url = row
        own = location_key(url)
        found = {own, *(location_key(c["url"]) for c in copies.get(kept, []) if c.get("url"))}
        out[doc_id] = GoldDoc(doc_id, kept, title, own, frozenset(found | ({kept} if kind == "event" else set())))
    return out


def _gold_label(g: GoldDoc) -> str:
    """doc_id「title」address, the address readable (percent escapes undone, no url: prefix)."""
    if g.kept is None:
        return f"{g.doc_id}（资料库里没有）"
    title = g.title if len(g.title) <= GOLD_TITLE else g.title[:GOLD_TITLE] + "…"
    via = f"（即 {g.kept}）" if g.kept != g.doc_id else ""
    return f"{g.doc_id}{via}「{title}」{unquote(g.address.removeprefix(URL_KEY))}"


def gold_diff(base: dict, base_conn: sqlite3.Connection, new: dict, conn: sqlite3.Connection) -> list[str]:
    """The questions whose gold documents changed from base (a gold of base_conn's database) to new (one of conn's),
    compared by address: a document of one that shares an address with a document of the other is the same (gold_docs).
    Lines: how many questions each maps, how many changed; the ids a database does not hold; then each changed
    question (or one only one gold has) with what it lost and gained and the new gold's note."""
    def docs_of(gold: dict, qid: str) -> list[str]:
        return (gold.get(qid) or {}).get("docs") or []

    old = gold_docs(base_conn, [d for qid in base for d in docs_of(base, qid)])
    now = gold_docs(conn, [d for qid in new for d in docs_of(new, qid)])
    changed = []
    for qid in [*base, *(q for q in new if q not in base)]:
        before, after = [old[d] for d in docs_of(base, qid)], [now[d] for d in docs_of(new, qid)]
        lost = [g for g in before if not any(g.addresses & h.addresses for h in after)]
        gained = [h for h in after if not any(h.addresses & g.addresses for g in before)]
        if not lost and not gained and (qid in base) == (qid in new):
            continue
        where = "" if (qid in base) == (qid in new) else "（只在新的 gold 里）" if qid in new else "（新的 gold 里没有这题）"
        parts = [f"{word} {'、'.join(_gold_label(g) for g in docs)}" for word, docs in (("少了", lost), ("多了", gained))
                 if docs]
        changed.append(f"{qid}{where} {'；'.join(parts)}｜{(new.get(qid) or {}).get('note') or ''}")
    mapped = [sum(bool(docs_of(gold, qid)) for qid in gold) for gold in (base, new)]
    lines = [(f"标准出处：基准 {mapped[0]}/{len(base)} 题对应到，新库 {mapped[1]}/{len(new)} 题对应到；"
              f"按网址比（编号换过也比得了），{len(changed)} 题的标准出处有变")]
    for name, docs in (("基准", old), ("新的", now)):
        if gone := [d for d, g in docs.items() if g.kept is None]:
            lines.append(f"{name} gold 里有 {len(gone)} 个编号在它的资料库里找不到（gold 不是给这个资料库做的？）："
                         + "、".join(gone))
    return lines + changed


def _sha256(path: str | Path | None) -> str | None:
    try:
        return hashlib.sha256(Path(path or "").read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def _search_info(search: Callable) -> dict:
    """What ran: the function, its signature and its source file's sha256; for hybrid_fn, hybrid_search with
    hybrid.py's and search.py's sha256 and the HybridConfig."""
    config = getattr(search, "config", None)
    if config is not None:
        from nknu.retrieval import hybrid
        from nknu.retrieval import search as keyword

        return {"function": "nknu.retrieval.hybrid.hybrid_search", "signature": str(inspect.signature(
            hybrid.hybrid_search)), "sha256": _sha256(hybrid.__file__), "search_py_sha256": _sha256(keyword.__file__),
            "config": dataclasses.asdict(config)}
    return {"function": f"{search.__module__}.{search.__qualname__}", "signature": str(inspect.signature(search)),
            "sha256": _sha256(inspect.getsourcefile(search))}


def _jsonable(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def run_offline(db: Path, *, gold: Path, core: Path, noanswer: Path, runs: Path, today: date = TODAY,
                rank: object = None, search: Callable = search_docs, deep: int = DEEP, deliver: bool = True,
                unscoped: Callable | None = None, totext: Path | None = None) -> dict:
    """evaluate() on one database (read-only, aliases from its meta), with what was run, every doc's title, the gold
    ids the database lacks, the database's numbers (build_stats); with unscoped (the same hybrid search without the
    unit scope), scope_effect; with totext (the to-text folder), totext_stats."""
    gold_map = json.loads(Path(gold).read_text(encoding="utf-8"))
    questions = load_questions(core, noanswer)
    queries = agent_queries(json.loads(Path(runs).read_text(encoding="utf-8")))
    conn = connect_ro(db)
    try:
        aliases = json.loads(_meta(conn, "aliases_json") or "[]")
        report = evaluate(conn, questions, gold_map, queries, today=today, aliases=aliases, rank=rank, search=search,
                          deep=deep, deliver=deliver, noanswer=load_noanswer(noanswer))
        report["missing_gold"] = missing_gold(conn, gold_map)
        if unscoped is not None:
            report["scope_effect"] = scope_effect(conn, questions, gold_map, queries, report["questions"], today=today,
                                                  aliases=aliases, rank=rank, search=unscoped, deep=deep)
        report["build"] = build_stats(conn)
        ids = sorted({d for r in report["questions"] for run in (r["question_query"], *r["agent_queries"])
                      for d in (*r["gold"], *r.get("gold_copies", ()), *run["top"])}
                     | {d for n in report["noanswer"] for d in (*n["question_top3"], *(x for a in n["agent"]
                                                                                      for x in a["top3"]))})
        titles = dict(conn.execute(f"SELECT doc_id, title FROM docs WHERE doc_id IN ({','.join('?' * len(ids))})"
                                   " ORDER BY doc_id", ids).fetchall()) if ids else {}
        built = _meta(conn, "built_at")
    finally:
        conn.close()
    if totext is not None:
        report["totext"] = totext_stats(Path(totext))
    head = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "today": today.isoformat(),
            "db": str(db), "db_built_at": built, "gold_file": str(gold), "runs_file": str(runs),
            "search": _search_info(search), "rank": _jsonable(rank), "deep": deep, "top": TOP}
    return {**head, **report, "titles": titles}


# ---------------------------------------------------------------- unit scope (addendum F)

def _scope_names(conn: sqlite3.Connection) -> Callable[[str], str | None]:
    """text -> what it names below the school (units, campus, levels: units.detect through hybrid.query_scope), joined
    by 、; None when it names nothing or the database has no unit tables."""
    from nknu.retrieval.hybrid import query_scope

    def named(text: str) -> str | None:
        got = query_scope(conn, text)
        if got is None:
            return None
        scope, units = got
        roots = {k for k, u in units.items() if u.parent_id is None}
        return "、".join([*sorted(scope.units - roots), *(f"{c}校區" for c in sorted(scope.campus)),
                         *sorted(scope.levels)]) or None

    return named


def _slim(run: dict) -> dict:
    return {k: run.get(k) for k in ("rank", "doc")}


def scope_effect(conn: sqlite3.Connection, questions: list[dict], gold: dict, queries: dict[str, list[str]],
                 rows: list[dict], *, today: date, aliases: list, rank: object, search: Callable, deep: int,
                 named: Callable[[str], str | None] | None = None) -> dict:
    """Addendum F's T10 addition: the gold ranks again without the unit scope (search: the same hybrid settings with
    w_scope and w_mention 0), from the same database, for the questions whose text or an agent query names a unit,
    campus or level; the other questions rank the same by construction (a query that names nothing gets no scope).
    rows: the questions of the run with the scope. Returns the per-question ranks with and without, the questions whose
    rank changed and the metrics without the scope (the unchanged questions counted as they were)."""
    from nknu.retrieval.vectors import has_table

    # a unit tree and document scopes (T7d); a build without the site list leaves both tables empty (the schema has
    # them since T7d), a skeleton has neither: then no query gets a scope
    tables = all(has_table(conn, t) and conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() is not None
                 for t in ("units", "doc_scope"))
    named = named or _scope_names(conn)
    by_id = {r["id"]: r for r in rows}
    picked, names = [], {}
    for q in questions:
        if q["id"] not in by_id:
            continue
        found = {t: n for t in dict.fromkeys([q["question"], *queries.get(q["id"], [])]) if (n := named(t))}
        if found:
            picked.append(q)
            names[q["id"]] = {"question": found.get(q["question"]),
                              "agent": sum(t != q["question"] for t in found), "queries": found}
    again = evaluate(conn, picked, gold, queries, today=today, aliases=aliases, rank=rank, search=search,
                     deep=deep, deliver=False)["questions"] if picked else []
    without = {r["id"]: r for r in again}
    out, changed = [], []
    for r in rows:
        if r["id"] not in without:
            continue
        w = without[r["id"]]
        entry = {"id": r["id"], "group": r["group"], "question": r["question"], "named": names[r["id"]],
                 "with": {"question": _slim(r["question_query"]), "agent": _slim(r["agent_best"])},
                 "without": {"question": _slim(w["question_query"]), "agent": _slim(w["agent_best"])}}
        out.append(entry)
        if entry["with"] != entry["without"]:
            changed.append(r["id"])
    merged = [without.get(r["id"], r) for r in rows]
    table = {mode: {grp: metrics([_as_place(r[field]) for r in merged if grp in ("all", r["group"])])
                    for grp in (*GROUPS, "all")}
             for mode, field in (("question", "question_query"), ("agent", "agent_best"))}
    config = getattr(search, "config", None)
    return {"scope_tables": tables, "config": dataclasses.asdict(config) if config is not None else None,
            "questions_named": len(picked), "queries_named": sum(len(v["queries"]) for v in names.values()),
            "questions": out, "changed": changed, "metrics_without": table}


# ---------------------------------------------------------------- the database's numbers (addendum G4, G7)

def _file_name_titles(conn: sqlite3.Connection) -> dict:
    """G4: documents whose title is a bare file name (year grids keep them this round), by kind, and those whose name
    holds no Han character (u112.pdf); plus the build's own count (meta counts_json: attachments no page names)."""
    rows = conn.execute("SELECT kind_class, title FROM docs ORDER BY doc_id").fetchall()
    names = [(k, t.strip()) for k, t in rows if FILE_TITLE.search(t.strip())]
    opaque = [t for _, t in names if not HAN.search(FILE_TITLE.sub("", t))]
    counts = json.loads(_meta(conn, "counts_json") or "{}")
    return {"docs": len(rows), "file_name": len(names), "by_kind": dict(Counter(k for k, _ in names)),
            "opaque": len(opaque), "opaque_examples": opaque[:10],
            "build_attachments": counts.get("attachments") if isinstance(counts, dict) else None}


def _short_sections(conn: sqlite3.Connection) -> dict:
    """G7: sections under SHORT_SECTION characters, per kind_class and per kind_raw."""
    def grouped(column: str) -> dict:
        return {k: {"docs": docs, "sections": n, "short": short or 0, "docs_with_short": with_short}
                for k, docs, n, short, with_short in conn.execute(
                    f"SELECT d.{column}, count(DISTINCT s.doc_id), count(*), sum(s.end - s.start < ?),"
                    " count(DISTINCT CASE WHEN s.end - s.start < ? THEN s.doc_id END) FROM sections s"
                    f" JOIN docs d ON d.doc_id = s.doc_id GROUP BY d.{column} ORDER BY d.{column}",
                    (SHORT_SECTION, SHORT_SECTION))}

    by_kind = grouped("kind_class")
    total = {k: sum(v[k] for v in by_kind.values()) for k in ("docs", "sections", "short", "docs_with_short")}
    return {"under": SHORT_SECTION, "total": total, "by_kind": by_kind, "by_kind_raw": grouped("kind_raw")}


def build_stats(conn: sqlite3.Connection) -> dict:
    """The database's own numbers for the owner report (no search): file_name_titles (G4), short_sections (G7). The
    series numbers (D's series_embed, I1's audience_series) went with the series on 11-03."""
    return {"file_name_titles": _file_name_titles(conn), "short_sections": _short_sections(conn)}


# ---------------------------------------------------------------- the to-text cache (addendum D, G1)

def numbering_marks(text: str) -> tuple[int, int]:
    """(lines that start with a numbering mark, those whose mark is written with Han numerals). The marks are
    nknu.content.numbering's patterns of every group but the English one, with its filters (Markdown decoration
    stripped; contents lines, NOT_BULLET lines and a bare list number with no text on the next line are no marks), but
    without its per-text choice of one group: a mark counts whatever group T6 would pick."""
    lines = [c for c, _ in map(numbering.strip_decor, text.split("\n")) if c]
    total = han = 0
    for i, clean in enumerate(lines):
        if numbering.CONTENTS_LINE.search(clean) or any(p.match(clean) for p in _NOT_MARK):
            continue
        if numbering._BARE_NUMBER.match(clean) and not (i + 1 < len(lines) and numbering._TEXT.search(lines[i + 1])):
            continue
        if m := next((m for p in _MARK_PATTERNS if (m := p.match(clean))), None):
            total += 1
            han += bool(_HAN_NUMERAL.search(m.group()))
    return total, han


def _count_marks(acc: dict, text: str) -> None:
    marks, han = numbering_marks(text)
    acc["texts"] += 1
    acc["chars"] += len(norm_quote(text))
    acc["marks"] += marks
    acc["han_marks"] += han


def _page_ints(values) -> set[int]:
    return {int(v.get("page") if isinstance(v, dict) else v) for v in values or ()
            if (v.get("page") if isinstance(v, dict) else v) is not None}


def totext_stats(root: Path) -> dict:
    """The to-text cache (root: the to-text folder, or its cache/), read only, one entry at a time; results not at the
    current PIPELINE_VERSION are counted as stale. D: PDF pages per path (our whole-page OCR by reason, Docling's
    region OCR, kept where our OCR did not replace it, layers Docling re-read whole for being broken, text layer only,
    OCR failed). G1: numbering marks per 1,000 characters (letters and digits) of each text source: the pages our OCR
    read (text.md sliced by page_starts), Docling's region-OCR lines (docling_ocr.json), the text-layer-only pages, and
    image files (our OCR, for comparison)."""
    from nknu.totext.cache import PIPELINE_VERSION, RESULT, TEXT

    root = Path(root)
    cache = root / "cache" if (root / "cache").is_dir() else root
    pdf = dict.fromkeys(("files", "pages", "ocr_pages", "few_chars", "misread", "docling_ocr", "docling_kept",
                         "broken", "text_layer", "failed", "unsplit"), 0)
    numbers = {k: {"texts": 0, "chars": 0, "marks": 0, "han_marks": 0} for k in NUMBER_SOURCES}
    routes: Counter = Counter()
    entries = stale = unreadable = 0
    for path in sorted(cache.glob(f"*/*/{RESULT}")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(result, dict) or result.get("version") != PIPELINE_VERSION:
            stale += 1
            continue
        entries += 1
        route = result.get("route") or ""
        routes[route] += 1
        if route not in ("pdf", "image"):
            continue
        try:  # written before result.json (cache.save), so a finished entry has it
            text = (path.parent / TEXT).read_text(encoding="utf-8")
        except OSError:
            text = ""
        if route == "image":
            if text.strip():
                _count_marks(numbers["image"], text)
            continue
        pages = int(result.get("pages") or 0)
        ours = {int(p["page"]): p.get("why") or "" for p in result.get("ocr_pages") or []
                if isinstance(p, dict) and p.get("page") is not None}
        docling, broken = _page_ints(result.get("docling_ocr_pages")), _page_ints(result.get("broken_pages"))
        failed = _page_ints(result.get("failed_pages"))
        layer = [n for n in range(1, pages + 1) if n not in ours and n not in docling and n not in failed]
        pdf["files"] += 1
        pdf["pages"] += pages
        pdf["ocr_pages"] += len(ours)
        pdf["few_chars"] += sum(w == "few_chars" for w in ours.values())
        pdf["misread"] += sum(w == "misread" for w in ours.values())
        pdf["docling_ocr"] += len(docling)
        pdf["docling_kept"] += len(docling - set(ours))
        pdf["broken"] += len(broken)
        pdf["text_layer"] += len(layer)
        pdf["failed"] += len(failed)
        starts = result.get("page_starts") or []
        if len(starts) == pages:
            bounds = [*starts, len(text)]
            for n in ours:
                _count_marks(numbers["ocr_pages"], text[bounds[n - 1]:bounds[n]])
            for n in layer:
                _count_marks(numbers["text_layer"], text[bounds[n - 1]:bounds[n]])
        elif pages and len(ours) == pages:
            _count_marks(numbers["ocr_pages"], text)
        else:
            pdf["unsplit"] += 1
        try:
            region = json.loads((path.parent / "docling_ocr.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            region = {}
        for lines in (region.values() if isinstance(region, dict) else ()):
            _count_marks(numbers["docling_ocr"], "\n".join(map(str, lines)))
    for acc in numbers.values():
        acc["per_1000"] = round(acc["marks"] * 1000 / acc["chars"], 2) if acc["chars"] else None
        acc["han_per_1000"] = round(acc["han_marks"] * 1000 / acc["chars"], 2) if acc["chars"] else None
    summary = root / "summary.json"
    try:
        paths = (json.loads(summary.read_text(encoding="utf-8")).get("ocr") or {}).get("paths")
    except (OSError, ValueError):
        paths = None
    return {"root": str(root), "version": PIPELINE_VERSION, "entries": entries, "stale": stale,
            "unreadable": unreadable, "by_route": dict(sorted(routes.items())), "pdf": pdf, "numbering": numbers,
            "summary_paths": paths}


def _md(s: object) -> str:
    return str(s).replace("|", "｜").replace("\n", " ")


def _cell(run: dict) -> str:
    """The gold document's rank (in the top 10 or further down), or — when it is not among the results."""
    return str(run["rank"]) if run.get("rank") is not None else "—"


def _behind(run: dict, field: str = "doc") -> str | None:
    """The gold document (field 'doc') or the query (field 'query') behind _cell(run)."""
    return run.get(field)


def _search_words(report: dict) -> str:
    search = report.get("search") or {}
    if search.get("config") is not None:
        on = [name for name, key in (("向量", "dense"), ("重排", "rerank")) if search["config"].get(key)]
        return f"hybrid_search（{'、'.join(on) or '两个模型都关'}；{json.dumps(search['config'], ensure_ascii=False)}）"
    return str(search.get("function") or "?").rsplit(".", 1)[-1]


def _runs_name(report: dict) -> str:
    return Path(report.get("runs_file") or "r3/summary.json").parent.name or "r3"


def format_table(report: dict) -> str:
    """Markdown: the metrics by group and mode, then each question's ranks, then the T10 blocks (delivery, the
    no-answer questions, rerank scores, fallbacks, missing gold, unit scope, the database's and to-text numbers)."""
    rank = report.get("rank")
    setting = "没传 rank（search_docs 自己的默认）" if rank is None else "rank=" + json.dumps(rank, ensure_ascii=False)
    if rank is not None and not report.get("rank_applied"):
        setting += "，但 search_docs 没有 rank 参数，这组设定没用上"
    head = ["前1", "前3", "前10", "MRR", "10名外", "没有"]
    lines = [f"今天 {report['today']}　资料库建于 {report.get('db_built_at')}　排序：{setting}　搜索：{_search_words(report)}",
             "", "| 题组 | 题数 | " + " | ".join(["原文 " + head[0], *head[1:], "agent " + head[0], *head[1:]]) + " |",
             "|" + "---|" * (2 + 2 * len(head))]
    for grp, name in (("core20", "core20"), ("N", "N"), ("all", "全部")):
        q, a = report["metrics"]["question"][grp], report["metrics"]["agent"][grp]
        cells = [str(m[k]) if k != "mrr" else f"{m[k]:.3f}" for m in (q, a)
                 for k in ("top1", "top3", "top10", "mrr", "past10", "missing")]
        lines.append("| " + " | ".join([name, str(q["n"]), *cells]) + " |")
    lines += ["", "| 题号 | 原文 | agent | 命中的出处（原文 / agent） | agent 最好的查询 | 题目 |", "|---|---|---|---|---|---|"]
    for r in report["questions"]:
        qq, best = r["question_query"], r["agent_best"]
        lines.append(f"| {r['id']} | {_cell(qq)} | {_cell(best)} | {_behind(qq) or '—'} / {_behind(best) or '—'}"
                     f" | {_md(_behind(best, 'query') or '—')} | {_md(r['question'])} |")
    pool = ("关键词和向量两路的候选都没有它" if (report.get("search") or {}).get("config") is not None
            else f"全文检索取的前 {report.get('deep', DEEP)} 份文件都不是它")
    lines += ["", (f"数字 = 标准出处排第几；— = 不在候选里（{pool}，改排序救不回来）。"
                   f"agent 栏是 {_runs_name(report)} 里 agent 送出的各个查询中最好的名次。")]
    if report.get("no_gold"):
        lines.append(f"没有对应到出处、没算进指标的题：{'、'.join(report['no_gold'])}")
    return "\n".join(lines + _copies_lines(report) + _report_blocks(report))


_COPIES_WORDS = "副本也算"  # plan 2026-11-14 T1: the widened numbers' label in every report


def _copies_lines(report: dict) -> list[str]:
    """format_table's block of the widened numbers (plan 2026-11-14 T1): the metrics table with exact copies counted,
    then the questions that have copies; dashes for a report written before T1."""
    head = ["前1", "前3", "前10", "MRR", "10名外", "没有"]
    keys = ("top1", "top3", "top10", "mrr", "past10", "missing")
    m = report.get("metrics") or {}
    out = ["", f"## {_COPIES_WORDS}（标准出处一字不差的副本也算答对：同一种文件、去掉空白后全文相同、至少 {COPY_MIN} 字）", "",
           "| 题组 | 题数 | " + " | ".join(["原文 " + head[0], *head[1:], "agent " + head[0], *head[1:]]) + " |",
           "|" + "---|" * (2 + 2 * len(head))]
    for grp, name in (("core20", "core20"), ("N", "N"), ("all", "全部")):
        q, a = ((m.get(mode) or {}).get(grp) for mode in ("question_copies", "agent_copies"))
        cells = [("—" if not x else f"{x[k]:.3f}" if k == "mrr" else str(x[k])) for x in (q, a) for k in keys]
        out.append("| " + " | ".join([name, str(q["n"]) if q else "—", *cells]) + " |")
    rows = [r for r in report.get("questions") or [] if r.get("gold_copies")]
    if "question_copies" not in m:
        return out + ["", "这份报告是旧的，没有算副本（—）。"]
    if rows:
        out += ["", "| 题号 | 原文 | agent | 命中的（原文 / agent） | 标准出处的副本 |", "|---|---|---|---|---|"]
        for r in rows:
            qq, best = r["question_query"], r.get("agent_best_copies") or {}
            out.append(f"| {r['id']} | {_cell({'rank': qq.get('rank_copies')})} | {_cell(best)} | "
                       f"{qq.get('doc_copies') or '—'} / "
                       f"{best.get('doc') or '—'} | {'、'.join(r['gold_copies'])} |")
    else:
        out += ["", "没有一题的标准出处有副本，两组数字一样。"]
    return out


# ---------------------------------------------------------------- the T10 blocks, shared by run and compare

def _num(value: object, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def distribution(values: list[float]) -> dict:
    """n, min, median and max of a list of scores (None for an empty list)."""
    return {"n": len(values), "min": min(values, default=None), "median": statistics.median(values) if values else None,
            "max": max(values, default=None)}


def _dist_cells(values: list[float] | None) -> list[str]:
    if values is None:
        return ["—"] * 4
    d = distribution(values)
    return [str(d["n"]), _num(d["min"]), _num(d["median"]), _num(d["max"])]


def _delivery_cells(d: dict | None) -> list[str]:
    """(delivered/checkable quotes, questions with one, all quotes) of one mode; dashes for an old report."""
    if not d:
        return ["—"] * 3
    return [f"{d['quotes']}/{d['checkable']}", f"{d['questions_any']}/{d['n']}", str(d.get("total", "—"))]


def _titles(report: dict, ids: list[str], n: int = 3) -> str:
    titles = report.get("titles") or {}
    return "；".join(f"{d} {_md(titles.get(d, '?'))[:24]}" for d in ids[:n]) or "（没有结果）"


def _scope_lines(effect: dict | None, where: str = "") -> list[str]:
    if not effect:
        return []
    out = ["", f"## 单位范围的影响{where}（addendum F：同一个资料库，有 / 没有 W_SCOPE、W_MENTION）", ""]
    if not effect.get("scope_tables"):
        return out + ["资料库没有单位范围（没有 units、doc_scope 这两张表，或是空的），没有可比的：所有查询都不加减分。"]
    out.append(f"点名单位、校区或学制的题 {effect['questions_named']} 题（这些题的查询里点名的 {effect['queries_named']} 个）；"
               f"名次变了的：{'、'.join(effect['changed']) or '没有'}。其余的题一个都不点名，名次本来就一样。")
    if effect["questions"]:
        out += ["", "| 题号 | 问题原文点名 | agent 查询里点名的 | 原文 有 → 没有 | agent 有 → 没有 |", "|---|---|---|---|---|"]
        for q in effect["questions"]:
            w, wo = q["with"], q["without"]
            out.append(f"| {q['id']} | {_md(q['named'].get('question') or '—')} | {q['named'].get('agent', 0)} | "
                       f"{_cell(w['question'])} → {_cell(wo['question'])} | "
                       f"{_cell(w['agent'])} → {_cell(wo['agent'])} |")
    m = effect.get("metrics_without") or {}
    if m:
        out.append("")
        out.append("没有单位范围时（没点名的题照原样算）：" + "；".join(
            f"{name} 前 3 {a['top3']}/{a['n']}、前 10 {a['top10']}/{a['n']}"
            for mode, name in (("question", "问题原文"), ("agent", "agent")) for a in (m[mode]["all"],)))
    return out


def _build_rows(stats: dict | None) -> dict[str, str]:
    """label -> value of the database's numbers (build_stats), the same labels for every report (an old report's series
    numbers are not shown: no series since 11-03)."""
    if not stats:
        return {}
    names, short = stats["file_name_titles"], stats["short_sections"]
    built = names.get("build_attachments") or {}
    total = short["total"]
    return {"标题是裸档名的文件（G4）": f"{names['file_name']}/{names['docs']}（"
                                   + ("、".join(f"{k} {v}" for k, v in sorted(names["by_kind"].items())) or "没有") + "）",
            "其中档名没有汉字（如 u112.pdf）": str(names["opaque"]),
            "建库记的附件标题：网页给的 / 仍是档名": (f"{built.get('title_from_page')} / {built.get('title_file_name')}"
                                         if built else "—"),
            f"不到 {short['under']} 字的节（G7）": f"{total['short']}/{total['sections']}（{total['docs_with_short']} 份文件有）"}


def _short_rows(stats: dict | None) -> dict[str, str]:
    if not stats:
        return {}
    return {k: f"{v['short']}/{v['sections']}（{v['docs_with_short']}/{v['docs']} 份）"
            for k, v in stats["short_sections"]["by_kind"].items()}


def _build_lines(stats: dict | None) -> list[str]:
    if not stats:
        return []
    out = ["", "## 资料库的数字（addendum G4、G7）", "", "| 项目 | 数字 |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in _build_rows(stats).items()]
    out += ["", f"| 文件种类 | 不到 {stats['short_sections']['under']} 字的节 / 全部节（有短节的文件 / 有节的文件） |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in _short_rows(stats).items()]
    return out


_PDF_ROWS = (("files", "PDF 档"), ("pages", "PDF 总页数"), ("ocr_pages", "我们的整页 OCR"),
             ("few_chars", "　其中没有可用的文字层（few_chars）"), ("misread", "　其中文字层有认不得的字（misread）"),
             ("docling_ocr", "Docling 区域 OCR 出了字"), ("docling_kept", "　其中保留 Docling 的字（没被整页 OCR 取代）"),
             ("broken", "　其中文字层坏掉、Docling 整页重认"), ("text_layer", "只用文字层（没有 OCR）"), ("failed", "OCR 失败的页"),
             ("unsplit", "没法按页切开的档"))
_SOURCE_WORDS = {"ocr_pages": "我们的整页 OCR（ocr_pages）", "docling_ocr": "Docling 区域 OCR（docling_ocr.json）",
                 "text_layer": "文字层（没有 OCR 的页）", "image": "图片档 OCR（对照）"}


def _totext_lines(stats: dict | None) -> list[str]:
    if not stats:
        return []
    routes = "、".join(f"{k or '-'} {v}" for k, v in stats["by_route"].items()) or "没有"
    out = ["", f"## 转文字（addendum D、G1；{stats['root']}）", "",
           (f"缓存里 {stats['version']} 的结果 {stats['entries']} 个（旧版本 {stats['stale']} 个、读不了 {stats['unreadable']} 个，"
            f"都没算）；路线：{routes}。同样内容的档只算一次。"), "", "| PDF 页走哪条路 | 页数 |", "|---|---|"]
    out += [f"| {words} | {stats['pdf'][key]} |" for key, words in _PDF_ROWS]
    out += ["", "| 文字来源 | 段数 | 字数（字母和数字） | 编号行 | 每千字 | 汉字编号行 | 每千字 |", "|---|---|---|---|---|---|---|"]
    for key in NUMBER_SOURCES:
        n = stats["numbering"][key]
        out.append(f"| {_SOURCE_WORDS[key]} | {n['texts']} | {n['chars']} | {n['marks']} | {_num(n['per_1000'], 2)} | "
                   f"{n['han_marks']} | {_num(n['han_per_1000'], 2)} |")
    out += ["", ("编号行 = 以 nknu.content.numbering 的编号（不含英文那组）开头的行；汉字编号 = 一、（一）第一條 壹、这类，"
                 "PP-OCRv6 small 常认不出这些笔画少的数字（G1）。段数：我们的 OCR 和文字层按页算，Docling 按页算它的区域 OCR 行，"
                 "图片按档算。")]
    return out


def _report_blocks(report: dict) -> list[str]:
    out = []
    m = report.get("metrics") or {}
    if d := m.get("delivery"):
        out += ["", "## 原句送到了吗（回传里有没有标准出处的原句）", "",
                "| 查询 | 送到的原句 / 能查的原句 | 至少送到一句的题 | 原句总数（去掉 NOT_GOLD） |", "|---|---|---|---|"]
        out += [f"| {name} | " + " | ".join(_delivery_cells(d.get(mode))) + " |"
                for mode, name in (("question", "问题原文"), ("agent", f"agent（{_runs_name(report)}）"))]
        out += ["", "能查 = 有一份文件的标题和全文含这句原句的全部片段（只比字母和数字）；送到 = 前 10 名的回传（和服务器一样的 36 KB）含这些片段。"]
    if report.get("noanswer") is not None:
        out += ["", f"## 查无题（{len(report['noanswer'])} 题：前 3 名和重排最高分，不设门槛）", "",
                "| 题号 | 问题 | 原文前 3 | 原文重排最高分 | agent 查询的重排最高分 |", "|---|---|---|---|---|"]
        for n in report["noanswer"]:
            agent = "、".join(_num(a["best_rerank"]) for a in n["agent"]) or "—"
            out.append(f"| {n['id']} | {_md(n['question'])} | {_titles(report, n['question_top3'])} | "
                       f"{_num(n['question_best_rerank'])} | {agent} |")
    if (br := m.get("best_rerank")) is not None:
        if br["answerable"] or br["noanswer"]:
            out += ["", "重排最高分（问题原文；不设门槛）：" + "；".join(
                f"{name} " + "、".join(f"{k} {v}" for k, v in zip(("n", "最小", "中位数", "最大"), _dist_cells(br[key]),
                                                                strict=True))
                for key, name in (("answerable", "有答案的题"), ("noanswer", "查无题")))]
        else:
            out += ["", "重排最高分：这次没有重排（关键词排序，或模型没用上）。"]
    if (report.get("search") or {}).get("config") is not None:
        fb = report.get("ranking_fallbacks") or []
        out += ["", f"排序退回关键词的查询（模型或向量出了错、hybrid 吞掉了；应该是 0）：{len(fb)} 个"
                + (f"：{'、'.join(_md(x) for x in fb[:10])}" if fb else "")]
    if report.get("missing_gold"):
        out += ["", f"注意：资料库里没有的标准出处 id（gold 不是给这个资料库做的）：{'、'.join(report['missing_gold'])}"]
    return out + _scope_lines(report.get("scope_effect")) + _build_lines(report.get("build")) + _totext_lines(
        report.get("totext"))


# ---------------------------------------------------------------- compare

_METRIC_ROWS = (("top1", "前 1"), ("top3", "前 3"), ("top10", "前 10"), ("mrr", "MRR@10"), ("past10", "10 名外"),
                ("missing", "没有"))


def _metric_cell(m: dict | None, key: str) -> str:
    if not m:
        return "—"
    if key == "mrr":
        return f"{m[key]:.3f}"
    return f"{m[key]}/{m['n']}" if key in ("top1", "top3", "top10") else str(m[key])


def _order(run: dict | None) -> tuple:
    """Better first: shown in the top 10 (by rank), shown further down, not found."""
    if not run:
        return (3, 0)
    if _in_top(run.get("rank")):
        return (0, run["rank"])
    return (1, run["rank"]) if run.get("rank") is not None else (2, 0)


def _arrow(base: dict | None, new: dict | None) -> str:
    a, b = _order(base), _order(new)
    return "=" if a == b else "↑" if b < a else "↓"


def _quote_flags(report: dict, mode: str) -> dict[tuple[str, str], tuple[bool, bool]]:
    """(question id, quote) -> (checkable, delivered) in one report; {} for an old report."""
    out = {}
    for r in report.get("questions") or []:
        for quote, c, d in zip(r.get("quotes") or [], r.get("checkable") or [], r.get(f"{mode}_delivered") or [],
                               strict=False):
            out[(r["id"], quote)] = (bool(c), bool(d))
    return out


def _wide(row: dict | None, field: str) -> dict | None:
    """A question's widened run (plan 2026-11-14 T1) in _cell's shape: {'rank': …}; None when the report (written
    before T1) or the question lacks it."""
    if not row:
        return None
    if field == "question_query":
        run = row.get("question_query") or {}
        return {"rank": run["rank_copies"]} if "rank_copies" in run else None
    return row.get("agent_best_copies")


def _copies_rank_lines(base_rows: dict, new_rows: dict, ids: list[str]) -> list[str]:
    """compare's per-question ranks with exact copies counted (plan 2026-11-14 T1): — and no arrow where a report has
    no widened rank; the moves in and out of the top 3 and top 10 only when both reports have them."""
    out = []
    for field, title in (("question_query", "问题原文"), ("agent_best", "agent")):
        out += ["", f"## 每题名次（{title}，{_COPIES_WORDS}；基准 → 新）", ""]
        for i in ids:
            b, n = _wide(base_rows.get(i), field), _wide(new_rows.get(i), field)
            arrow = f" {_arrow(b, n)}" if b and n else ""
            out.append(f"- {i}：{_cell(b) if b else '—'} → {_cell(n) if n else '—'}{arrow}")
        if not any(_wide(r, field) for r in base_rows.values()) or not any(_wide(r, field) for r in new_rows.values()):
            out += ["", "有一边是旧报告，没有算副本，不列进出前 3、前 10。"]
            continue
        moves = []
        for k in (3, TOP):
            came = [i for i in ids if not _in_top((_wide(base_rows.get(i), field) or {}).get("rank"), k)
                    and _in_top((_wide(new_rows.get(i), field) or {}).get("rank"), k)]
            left = [i for i in ids if _in_top((_wide(base_rows.get(i), field) or {}).get("rank"), k)
                    and not _in_top((_wide(new_rows.get(i), field) or {}).get("rank"), k)]
            moves.append(f"进入前 {k}：{'、'.join(came) or '没有'}；掉出前 {k}：{'、'.join(left) or '没有'}")
        out += ["", "；".join(moves)]
    return out


def compare_reports(base: dict, new: dict) -> str:
    """Markdown, Simplified Chinese: two run reports side by side (python offline_eval.py compare). The rank metrics
    (question text and replayed agent queries; core20, N, all); delivery over each report's own checkable quotes and
    over the quotes checkable in both; each question's rank base → new (↑ better, ↓ worse, = same) and the questions
    entering or leaving the top 3 and top 10; the no-answer questions' top 3 and best rerank scores; the two
    best-rerank distributions, with no threshold (11-01: 先不设门槛); the unit scope, the databases' numbers and the
    to-text numbers when a report has them."""
    def side(report: dict) -> str:
        return (f"`{report.get('db')}`（建于 {report.get('db_built_at')}；{_search_words(report)}；今天 {report.get('today')}；"
                f"agent 查询 {_runs_name(report)}）")

    out = ["# 离线检查对照", "", f"- 基准：{side(base)}", f"- 新：{side(new)}"]
    for report, name in ((base, "基准"), (new, "新")):
        if report.get("missing_gold"):
            out.append(f"- 注意：{name}的资料库里没有这些标准出处 id：{'、'.join(report['missing_gold'])}")
        if (report.get("search") or {}).get("config") is not None:
            out.append(f"- {name}：排序退回关键词的查询 {len(report.get('ranking_fallbacks') or [])} 个（应该是 0）")
    for mode, title in (("question", "问题原文"), ("question_copies", f"问题原文，{_COPIES_WORDS}"),
                        ("agent", "agent 查询重放（每题取最好的名次）"),
                        ("agent_copies", f"agent 查询重放（每题取最好的名次），{_COPIES_WORDS}")):
        out += ["", f"## 名次：{title}"]
        if mode.endswith("_copies"):  # plan 2026-11-14 T1
            out += ["", f"标准出处一字不差的副本（同一种文件、去掉空白后全文相同、至少 {COPY_MIN} 字）也算答对；旧报告没有这组，写 —。"]
        for grp, name in (("all", "全部"), ("core20", "core20"), ("N", "N")):
            b, n = ((base["metrics"].get(mode) or {}).get(grp), (new["metrics"].get(mode) or {}).get(grp))
            out += ["", f"| {name} | 基准 | 新 |", "|---|---|---|"]
            out += [f"| {label} | {_metric_cell(b, key)} | {_metric_cell(n, key)} |" for key, label in _METRIC_ROWS]
    bd, nd = (base.get("metrics") or {}).get("delivery") or {}, (new.get("metrics") or {}).get("delivery") or {}
    out += ["", "## 原句送到了吗（回传里有没有标准出处的原句）", "", "| 指标 | 基准 | 新 |", "|---|---|---|"]
    for mode, name in (("question", "问题原文"), ("agent", "agent")):
        b, n = _delivery_cells(bd.get(mode)), _delivery_cells(nd.get(mode))
        out += [f"| {name}：送到的原句 / 能查的原句 | {b[0]} | {n[0]} |", f"| {name}：至少送到一句的题 | {b[1]} | {n[1]} |"]
    totals = [_delivery_cells(d.get("question"))[2] for d in (bd, nd)]
    out.append(f"| 原句总数（去掉 NOT_GOLD） | {totals[0]} | {totals[1]} |")
    both_line, shared = [], 0
    for mode, name in (("question", "问题原文"), ("agent", "agent")):
        bf, nf = _quote_flags(base, mode), _quote_flags(new, mode)
        both = [k for k in bf if k in nf and bf[k][0] and nf[k][0]]
        shared = len(both)
        both_line.append(f"{name}送到 {sum(bf[k][1] for k in both)} → {sum(nf[k][1] for k in both)}")
    old = "（有一边是旧报告，没有逐句的纪录。）" if not (_quote_flags(base, "question") and _quote_flags(new, "question")) else ""
    out += ["", f"两边都能查的原句：{shared} 句；" + "，".join(both_line) + "。" + old]
    base_rows = {r["id"]: r for r in base.get("questions") or []}
    new_rows = {r["id"]: r for r in new.get("questions") or []}
    ids = [*base_rows, *(i for i in new_rows if i not in base_rows)]
    for mode, field, title in (("question", "question_query", "问题原文"), ("agent", "agent_best", "agent")):
        out += ["", f"## 每题名次（{title}；基准 → 新）", ""]
        for i in ids:
            b, n = (base_rows.get(i) or {}).get(field), (new_rows.get(i) or {}).get(field)
            out.append(f"- {i}：{_cell(b) if b else '（没有这题）'} → {_cell(n) if n else '（没有这题）'} {_arrow(b, n)}")
        moves = []
        for k in (3, TOP):
            came = [i for i in ids if not _in_top((base_rows.get(i) or {}).get(field, {}).get("rank"), k)
                    and _in_top((new_rows.get(i) or {}).get(field, {}).get("rank"), k)]
            left = [i for i in ids if _in_top((base_rows.get(i) or {}).get(field, {}).get("rank"), k)
                    and not _in_top((new_rows.get(i) or {}).get(field, {}).get("rank"), k)]
            moves.append(f"进入前 {k}：{'、'.join(came) or '没有'}；掉出前 {k}：{'、'.join(left) or '没有'}")
        out += ["", "；".join(moves)]
    out += _copies_rank_lines(base_rows, new_rows, ids)
    bn = {n["id"]: n for n in base.get("noanswer") or []}
    nn = {n["id"]: n for n in new.get("noanswer") or []}
    out += ["", f"## 查无题（{len(set(bn) | set(nn))} 题：问题原文的前 3 名和重排最高分，不设门槛）", "",
            "| 题号 | 问题 | 基准前 3 | 基准重排分 | 新前 3 | 新重排分 |", "|---|---|---|---|---|---|"]
    for i in [*bn, *(x for x in nn if x not in bn)]:
        b, n = bn.get(i) or {}, nn.get(i) or {}
        cells = [f"{_titles(report, row.get('question_top3') or []) if row else '—'} | "
                 f"{_num(row.get('question_best_rerank'))}" for report, row in ((base, b), (new, n))]
        out.append(f"| {i} | {_md((b or n).get('question', ''))} | {cells[0]} | {cells[1]} |")
    out += ["", "## 重排最高分的分布（问题原文；不设门槛）", "", "| 报告 | 题组 | 题数 | 最小 | 中位数 | 最大 |",
            "|---|---|---|---|---|---|"]
    for report, name in ((base, "基准"), (new, "新")):
        br = (report.get("metrics") or {}).get("best_rerank")
        for key, group in (("answerable", "有答案"), ("noanswer", "查无")):
            out.append(f"| {name} | {group} | " + " | ".join(_dist_cells(br.get(key) if br else None)) + " |")
    out += _scope_lines(base.get("scope_effect"), "：基准") + _scope_lines(new.get("scope_effect"), "：新")
    rows_b, rows_n = _build_rows(base.get("build")), _build_rows(new.get("build"))
    if rows_b or rows_n:
        out += ["", "## 资料库的数字（addendum G4、G7）", "", "| 项目 | 基准 | 新 |", "|---|---|---|"]
        out += [f"| {k} | {rows_b.get(k, '—')} | {rows_n.get(k, '—')} |" for k in dict.fromkeys([*rows_b, *rows_n])]
        sb, sn = _short_rows(base.get("build")), _short_rows(new.get("build"))
        out += ["", f"| 文件种类：不到 {SHORT_SECTION} 字的节 / 全部节 | 基准 | 新 |", "|---|---|---|"]
        out += [f"| {k} | {sb.get(k, '—')} | {sn.get(k, '—')} |" for k in dict.fromkeys([*sb, *sn])]
    for report, name in ((base, "基准"), (new, "新")):
        if report.get("totext"):
            out += [line.replace("## 转文字（", f"## 转文字：{name}（", 1) for line in _totext_lines(report["totext"])]
    return "\n".join(out) + "\n"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="offline_eval.py",
                                 description="离线量测：标准出处排第几、原句有没有送到、查无题出来什么（不调用语言模型、不连网、资料库只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gold", help="把题目的标准出处对应到资料库的 doc_id，写 gold JSON")
    r = sub.add_parser("run", help="跑搜索，算名次、原句送达和指标，写 JSON 并印出表")
    c = sub.add_parser("compare", help="把两份 run 的 JSON 并排比，写 Markdown")
    d = sub.add_parser("gold-diff", help="按网址比两份 gold（各配自己的资料库），写出标准出处有变的题")
    d.add_argument("--base-gold", type=Path, default=HERE / "gold_docs.json", help="基准的 gold（默认骨架的）")
    d.add_argument("--base-db", type=Path, default=DB, help="基准 gold 的资料库（默认骨架的；只读）")
    d.add_argument("--gold", type=Path, required=True, help="新的 gold（gold 子命令写的）")
    d.add_argument("--db", type=Path, required=True, help="新 gold 的资料库（只读）")
    d.add_argument("--out", type=Path, required=True, help="输出的文字档（build.sh 写 gold_diff.txt）")
    for p in (g, r):
        p.add_argument("--db", type=Path, default=DB, help="资料库（默认 research/skeleton_2026-11-01/aca.db）")
        p.add_argument("--core", type=Path, default=HERE / "core20.json")
        p.add_argument("--noanswer", type=Path, default=HERE / "noanswer22.json")
    g.add_argument("--corpus", type=Path, default=None, help="建这个资料库的 corpus.json（默认用资料库 meta 记的路径）")
    g.add_argument("--out", type=Path, required=True,
                   help="输出 JSON（必给：以前默认写回 research/eval_2026-11-01/gold_docs.json，会盖掉骨架的 gold）")
    r.add_argument("--gold", type=Path, default=HERE / "gold_docs.json")
    r.add_argument("--runs", type=Path, default=HERE / "runs" / "r3" / "summary.json", help="真 agent 的查询纪录")
    r.add_argument("--today", type=date.fromisoformat, default=TODAY)
    r.add_argument("--rank", type=json.loads, default=None,
                   help="一组排序设定（JSON），传给搜索的 rank=…；不给就不传 rank，用搜索自己的默认")
    r.add_argument("--search", choices=SEARCHES, default="keyword",
                   help="keyword：search_docs（默认）；hybrid：hybrid_search（要 uv run --extra pipeline）")
    r.add_argument("--hybrid", type=json.loads, default=None,
                   help='HybridConfig 的设定（JSON，例如 \'{"rerank": false}\'），只配 --search hybrid')
    r.add_argument("--totext", type=Path, default=None,
                   help="转文字资料夹（例如 research/pipeline_2026-11-02/totext）：加上各 OCR 路径的页数和编号密度")
    r.add_argument("--allow-missing-gold", action="store_true",
                   help="资料库里没有某些标准出处 id 也照跑（默认先停下，退出码 3）")
    r.add_argument("--no-scope-effect", action="store_true",
                   help="hybrid 时不重跑「没有单位范围」那一组（默认重跑点名单位、校区、学制的题）")
    r.add_argument("--out", type=Path, required=True, help="输出 JSON（每组设定一个档，免得互相盖掉）")
    c.add_argument("--base", type=Path, required=True, help="基准的 run JSON")
    c.add_argument("--new", type=Path, required=True, help="新的 run JSON")
    c.add_argument("--out", type=Path, required=True, help="输出 Markdown")
    return ap


def _gold(args: argparse.Namespace) -> int:
    conn = connect_ro(args.db)
    try:
        corpus = args.corpus or Path(_meta(conn, "corpus") or "")
        if not corpus.is_file():
            print(f"找不到 corpus.json：{corpus}", file=sys.stderr)
            return 2
        mapper = Mapper(conn, json.loads(corpus.read_text(encoding="utf-8")))
        gold = {q["id"]: mapper.question(q) for q in load_questions(args.core, args.noanswer)}
    finally:
        conn.close()
    args.out.write_text(json.dumps(gold, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    empty = [k for k, v in gold.items() if not v["docs"]]
    print(f"{len(gold)} 题写入 {args.out}，其中 {len(gold) - len(empty)} 题对应到出处"
          + (f"；一份都没对应到：{'、'.join(empty)}" if empty else ""))
    return 0


def _gold_diff(args: argparse.Namespace) -> int:
    for path, what in ((args.base_gold, "基准 gold"), (args.base_db, "基准资料库"), (args.gold, "新的 gold"),
                       (args.db, "新资料库")):
        if not Path(path).is_file():
            print(f"找不到{what}：{path}", file=sys.stderr)
            return 2
    base, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.base_gold, args.gold))
    base_conn, conn = connect_ro(args.base_db), connect_ro(args.db)
    try:
        lines = gold_diff(base, base_conn, new, conn)
    finally:
        base_conn.close()
        conn.close()
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def searches(kind: str, settings: dict | None, *, scope_effect: bool = True) -> tuple[Callable, Callable | None, int]:
    """(search, the same search without the unit scope or None, results to ask for). keyword: search_docs, DEEP.
    hybrid: hybrid_fn(HybridConfig(**settings)) with the shared models behind one per-run cache for both searches,
    POOL_DOCS + 2 x dense_candidates (the keyword pool and the vector pool of each tier, addendum M7); the scope-free
    twin only when the settings give the scope a weight."""
    if kind == "keyword":
        return search_docs, None, DEEP
    from nknu.retrieval.hybrid import HybridConfig

    config = HybridConfig(**(settings or {}))
    embedder, reranker = MemoEmbedder(), MemoReranker()
    unscoped = None
    if scope_effect and (config.w_scope or config.w_mention):
        unscoped = hybrid_fn(dataclasses.replace(config, w_scope=0.0, w_mention=0.0), embedder=embedder,
                             reranker=reranker)
    return hybrid_fn(config, embedder=embedder, reranker=reranker), unscoped, POOL_DOCS + 2 * config.dense_candidates


def _compare(args: argparse.Namespace) -> int:
    base, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.base, args.new))
    args.out.write_text(compare_reports(base, new), encoding="utf-8")
    print(f"写入 {args.out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if _protected(args.out, SKELETON):  # the build's guard: the skeleton's folder, ./data and archive*/ (the exports)
        ap.error(OUT_REFUSED.format(out=args.out))
    if args.cmd == "gold":
        return _gold(args)
    if args.cmd == "gold-diff":
        return _gold_diff(args)
    if args.cmd == "compare":
        return _compare(args)
    if args.hybrid is not None and args.search != "hybrid":
        ap.error("--hybrid 只配 --search hybrid")
    if not args.allow_missing_gold:
        conn = connect_ro(args.db)
        try:
            missing = missing_gold(conn, json.loads(args.gold.read_text(encoding="utf-8")))
        finally:
            conn.close()
        if missing:
            print(f"资料库 {args.db} 里没有这些标准出处 id：{'、'.join(missing)}；gold 不是给这个资料库做的，先用 gold 子命令重做"
                  "（或加 --allow-missing-gold 照跑）", file=sys.stderr)
            return 3
    search, unscoped, deep = searches(args.search, args.hybrid, scope_effect=not args.no_scope_effect)
    report = run_offline(args.db, gold=args.gold, core=args.core, noanswer=args.noanswer, runs=args.runs,
                         today=args.today, rank=args.rank, search=search, deep=deep, unscoped=unscoped,
                         totext=args.totext)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(format_table(report))
    if args.rank is not None and not report["rank_applied"]:
        print("注意：搜索没有 rank 参数，这组设定没用上", file=sys.stderr)
    if report["ranking_fallbacks"]:
        print(f"注意：{len(report['ranking_fallbacks'])} 个查询的排序退回关键词（模型或向量出错，见上面的 log）", file=sys.stderr)
    print(f"写入 {args.out}", file=sys.stderr)
    return 0


# ---- tests ------------------------------------------------------------------------------------------------------


def test_metrics_counts_top_k_mrr_and_why_the_rest_missed():
    # changed 11-03 on purpose: no document folds under another any more (addendum L), so no place is folded and the
    # metrics have no folded count
    places = [Place(1, "a"), Place(3, "b"), Place(12, "c"), Place()]
    assert metrics(places) == {"n": 4, "top1": 1, "top3": 2, "top10": 2, "mrr": round((1 + 1 / 3) / 4, 4),
                               "past10": 1, "missing": 1}
    assert metrics([]) == {"n": 0, "top1": 0, "top3": 0, "top10": 0, "mrr": 0.0, "past10": 0, "missing": 0}


def test_no_series_in_places_or_metrics():
    # owner 11-03 (addendum L): a place is a rank and its document; each question outside the top 10 is past 10 or
    # missing, so top10 + past10 + missing = n
    assert [f.name for f in dataclasses.fields(Place)] == ["rank", "doc"]
    m = metrics([Place(1, "a"), Place(15, "b"), Place()])
    assert "folded" not in m and m["top10"] + m["past10"] + m["missing"] == m["n"] == 3


def _result(*hits) -> SearchResult:
    """hits: doc_ids, best first (each its own hit: no series since 11-03)."""
    return SearchResult("q", Understood([], [], [], [], []), [DocHit(d, 1.0, 1.0, -1.0, []) for d in hits],
                        len(hits))


def test_place_is_the_first_gold_hit():
    assert place(_result("x", "g2", "g1"), {"g1", "g2"}) == Place(2, "g2")


def test_place_without_gold():
    assert place(_result("x"), {"g"}) == Place()
    assert place(_result(), {"g"}) == Place()


def test_agent_queries_keeps_each_search_query_once_in_order():
    summary = [{"id": "Q01", "calls": [{"name": "mcp__nknu_aca__search", "input": {"query": "停修 115-1"}},
                                       {"name": "mcp__nknu_aca__read", "input": {"id": "dp32"}},
                                       {"name": "mcp__nknu_aca__search", "input": {"query": "停修"}},
                                       {"name": "mcp__nknu_aca__search", "input": {"query": "停修 115-1"}}]},
               {"id": "N04", "calls": []}]
    assert agent_queries(summary) == {"Q01": ["停修 115-1", "停修"], "N04": []}


def _search_plain(conn, query, *, today, aliases, limit=10):
    raise AssertionError("not called")


def _search_rank(conn, query, *, today, aliases, limit=10, rank=None):
    raise AssertionError("not called")


@dataclass(frozen=True)
class _Rank:
    w_recent: float = 0.0
    tau: float = 365.0


def _search_typed(conn, query, *, today, aliases, limit=10, rank: _Rank | None = None):
    raise AssertionError("not called")


def test_rank_kwargs_runs_the_status_quo_when_search_has_no_rank():
    assert rank_kwargs(_search_plain, None) == ({}, False)
    assert rank_kwargs(_search_plain, {"w_recent": 0.3}) == ({}, False)
    assert rank_kwargs(_search_rank, None) == ({}, False)


def test_rank_kwargs_passes_the_setting_through():
    assert rank_kwargs(_search_rank, {"w_recent": 0.3}) == ({"rank": {"w_recent": 0.3}}, True)
    assert rank_kwargs(_search_rank, "v2") == ({"rank": "v2"}, True)


def test_rank_kwargs_builds_the_dataclass_rank_is_annotated_with():
    assert rank_kwargs(_search_typed, {"w_recent": 0.3}) == ({"rank": _Rank(0.3, 365.0)}, True)
    assert rank_kwargs(_search_typed, _Rank(0.1)) == ({"rank": _Rank(0.1)}, True)


_X = "https://x.test/aca/"
_CORPUS = [
    {"id": "p1", "kind": "page", "url": _X + "page.aspx?pn=44", "file": "pages/0001-Q & A.html"},
    {"id": "a2", "kind": "附件:pdf:有文字层", "url": _X + "f/b.pdf", "file": "files/0002-b.pdf"},
    {"id": "a3", "kind": "附件:pdf:有文字层", "url": _X + "f/copy/b.pdf", "file": "files/0003-b.pdf"},
    {"id": "p4", "kind": "newsdetail", "url": _X + "newsdetail.aspx?nid=10&amp;gi=2", "file": "pages/0004-停修.html"},
    {"id": "p5", "kind": "newsdetail", "url": _X + "newsdetail.aspx?nid=10&gi=2", "file": "pages/0005-停修.html"},
    {"id": "p6", "kind": "album_images", "url": _X + "album_images.aspx?aid=1", "file": "pages/0006-相簿.html"},
    {"id": "p7", "kind": "page", "url": _X + "page.aspx?pn=64", "file": "pages/0007-交通.html"},
    {"id": "p8", "kind": "外站:reurl.cc", "url": "https://reurl.test/abc", "file": "pages/0007-交通.html"},
    {"id": "p9", "kind": "page", "url": _X + "page.aspx?pn=9", "file": "pages/0009-café.html"},
]
_CAL_FILE = "archive-export/aca-20261027/data/0001-Google 行事历 nknubf@gmail.com 的事件.json"


def _content_db(path) -> sqlite3.Connection:
    """The docs, dates and meta rows of a tiny build of _CORPUS: a3 merged into a2, p4 a duplicate of p5, p6 left out."""
    if path.exists():
        return sqlite3.connect(path)
    conn = sqlite3.connect(path)
    create_schema(conn)
    rows = [("dp1", "page", "Q & A", _X + "page.aspx?pn=44", None, None),
            ("da2", "attachment", "b.pdf", _X + "f/b.pdf", None, None),
            ("dp5", "notice", "停修公告", _X + "newsdetail.aspx?nid=10&gi=2", None, None),
            ("dp7", "page", "交通", _X + "page.aspx?pn=64", None, None),
            ("dp8", "link", "交通", "https://reurl.test/abc", None, None),
            ("dp9", "page", "café", _X + "page.aspx?pn=9", None, None),
            ("eA", "event", "申辦停修Application for course withdrawal", "https://cal.test/a", 115, 1),
            ("eB", "event", "申辦停修Application for course withdrawal", "https://cal.test/b", 114, 1)]
    conn.executemany("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars, period_ay,"
                     " period_sem) VALUES (?, ?, ?, ?, ?, '教務處', 1, 0, ?, ?)",
                     [(d, k, k, t, u, ay, sem) for d, k, t, u, ay, sem in rows])
    conn.executemany("INSERT INTO dates VALUES (?, ?, ?, '', '行事曆')",
                     [("eA", "event_start", "2026-11-03"), ("eA", "event_end", "2026-11-16"),
                      ("eB", "event_start", "2025-11-17"), ("eB", "event_end", "2025-11-28")])
    conn.execute("INSERT INTO meta VALUES ('copies:da2', ?)", (json.dumps([{"url": _X + "f/copy/b.pdf"}]),))
    conn.commit()
    return conn


def _mapper(tmp_path, **kw) -> Mapper:
    return Mapper(_content_db(tmp_path / "c.db"), _CORPUS, **{"calendar": {"Q01": {"申辦停修": ["eA"]}},
                                                               "not_gold": {}, **kw})


def test_file_maps_a_corpus_record_to_its_document(tmp_path):
    assert _mapper(tmp_path).file("archive-export/aca-20261027/pages/0001-Q & A.html") == (["dp1"], "")
    assert _mapper(tmp_path).file("pages/0009-café.html") == (["dp9"], "")  # NFC either way


def test_file_maps_a_merged_copy_to_the_kept_document(tmp_path):
    ids, note = _mapper(tmp_path).file("files/0003-b.pdf")
    assert ids == ["da2"] and "副本" in note and "da2" in note


def test_file_maps_a_duplicate_notice_to_the_kept_one(tmp_path):
    ids, note = _mapper(tmp_path).file("pages/0004-停修.html")
    assert ids == ["dp5"] and "nid=10" in note


def test_a_duplicate_notice_maps_to_its_own_sites_notice(tmp_path):
    # plan 2026-11-08 T1: notices are numbered per site, so the &amp; spelling of 教務處's nid 578 is 教務處's 578,
    # never ccee's (Mapper.nid_doc was keyed by the nid alone)
    conn = sqlite3.connect(tmp_path / "two.db")
    create_schema(conn)
    aca = "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=578"
    ccee = "https://c.nknu.edu.tw/ccee/newsdetail.aspx?nid=578"
    conn.executemany("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars)"
                     " VALUES (?, 'notice', 'newsdetail', ?, ?, '教務處', 1, 0)",
                     [("daca", "學分學程申請", aca + "&gi=2"), ("dccee", "專題演講", ccee + "&gi=2")])  # nid alone: ccee's
    corpus = [{"id": "p9", "kind": "newsdetail", "url": aca + "&amp;gi=2", "file": "pages/0009-學程.html"}]
    mapper = Mapper(conn, corpus, calendar={}, not_gold={})
    assert mapper.file("pages/0009-學程.html")[0] == ["daca"]
    conn.close()


def test_file_with_two_records_maps_to_both(tmp_path):
    ids, note = _mapper(tmp_path).file("pages/0007-交通.html")
    assert ids == ["dp7", "dp8"] and "2 笔" in note


def test_file_left_out_of_the_database_is_not_forced(tmp_path):
    ids, note = _mapper(tmp_path).file("pages/0006-相簿.html")
    assert ids == [] and "album_images" in note and "pages/0006" in note
    ids, note = _mapper(tmp_path).file("files/9999-x.pdf")
    assert ids == [] and "corpus" in note


def test_file_falls_back_to_the_url(tmp_path):
    ids, note = _mapper(tmp_path).file("files/0099-renamed.pdf", _X + "f/b.pdf")
    assert ids == ["da2"] and "网址" in note


def test_calendar_quote_maps_to_the_listed_event(tmp_path):
    ids, note = _mapper(tmp_path).calendar("Q01", "申辦停修Application for course withdrawal")
    assert ids == ["eA"] and "eA" in note and "115-1" in note and "2026-11-03" in note
    ids, note = _mapper(tmp_path).calendar("Q99", "申辦停修")
    assert ids == [] and "没有对应" in note


def test_calendar_entry_must_name_an_event_with_that_title(tmp_path):
    import pytest

    for wrong in ({"申辦停修": ["dp1"]}, {"申辦停修": ["eZ"]}, {"休學": ["eA"]}):
        with pytest.raises(ValueError):
            _mapper(tmp_path, calendar={"Q01": wrong}).calendar("Q01", "申辦停修 休學")


def test_question_joins_its_sources_and_leaves_out_ruled_out_ones(tmp_path):
    q = {"id": "Q01", "sources": [{"file": "archive-export/aca-20261027/pages/0005-停修.html", "quote": "a"},
                                  {"file": "archive-export/aca-20261027/pages/0005-停修.html", "quote": "b"},
                                  {"file": _CAL_FILE, "quote": "申辦停修Application for course withdrawal"},
                                  {"file": "archive-export/aca-20261027/pages/0001-Q & A.html", "quote": "c"},
                                  {"file": "archive-export/aca-20261027/pages/0006-相簿.html", "quote": "d"}]}
    gold = _mapper(tmp_path, not_gold={"Q01": {"pages/0001": "旧流程"}}).question(q)
    assert gold["docs"] == ["dp5", "eA"]
    assert all(s in gold["note"] for s in ("pages/0001", "dp1", "旧流程", "pages/0006", "album_images", "eA"))


def test_question_rule_that_names_no_source_is_an_error(tmp_path):
    import pytest

    q = {"id": "Q01", "sources": [{"file": "archive-export/aca-20261027/pages/0005-停修.html"}]}
    with pytest.raises(ValueError):
        _mapper(tmp_path, not_gold={"Q01": {"pages/0099": "x"}}).question(q)


# gold-diff (plan 2026-11-08 T1): a base database with ids of its own (the skeleton's shape: no doc_alias, copies by
# url only) and a new one with stable ids, which kept the other copy of b.pdf and lists ids for its copies and formats
_ACA = "https://c.nknu.edu.tw/aca/"
_BASE_DOCS = [("dp32", "notice", "停修公告", _ACA + "newsdetail.aspx?nid=662&gi=2"),
              ("da2", "attachment", "b.pdf", _X + "f/b.pdf"), ("da3", "attachment", "舊表.pdf", _X + "f/old.pdf"),
              ("da4140", "attachment", "新生手冊.pdf", _X + "f/%E6%96%B0%E7%94%9F%E6%89%8B%E5%86%8A.pdf"),
              ("eA", "event", "申辦停修", "https://cal.test/a")]
_NEW_DOCS = [("du1", "notice", "停修公告", "http://c.nknu.edu.tw/aca/newsdetail.aspx?nid=662&amp;gi=2"),
             ("du2", "attachment", "b.pdf", _X + "f/copy/b.pdf"), ("du5", "attachment", "新表.pdf", _X + "f/new.pdf"),
             ("eA", "event", "申辦停修", "https://cal.test/a")]


def _gold_db(path, docs, copies: dict, aliases: list) -> sqlite3.Connection:
    """docs: (doc_id, kind_class, title, url); copies: kept id -> its meta copies list; aliases: doc_alias rows, None
    for a database without the table (the skeleton's and the 11-04 builds)."""
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.executemany("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars)"
                     " VALUES (?, ?, ?, ?, ?, '教務處', 1, 0)", [(d, k, k, t, u) for d, k, t, u in docs])
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [(COPIES_KEY + kept, json.dumps(entries, ensure_ascii=False))
                                                        for kept, entries in copies.items()])
    if aliases is None:
        conn.execute("DROP TABLE doc_alias")
    else:
        conn.executemany("INSERT INTO doc_alias VALUES (?, ?, ?)", aliases)
    conn.commit()
    return conn


def _gold_dbs(tmp_path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    base = _gold_db(tmp_path / "base.db", _BASE_DOCS, {"da2": [{"url": _X + "f/copy/b.pdf"}]}, None)
    new = _gold_db(tmp_path / "new.db", _NEW_DOCS, {"du2": [{"id": "du9", "url": _X + "f/b.pdf"},
                                                            {"id": "du8", "url": _X + "f/b.doc", "format": "doc"}]},
                   [("du9", "du2", "copy")])  # the format du8 has no doc_alias row here: found in the copies list
    return base, new


def test_gold_docs_resolve_aliases_and_list_every_address(tmp_path):
    base, new = _gold_dbs(tmp_path)
    got = gold_docs(new, ["du2", "du9", "du8", "eA", "dzz", "du2"])
    assert list(got) == ["du2", "du9", "du8", "eA", "dzz"]
    assert (got["du9"].kept, got["du8"].kept, got["du9"].title) == ("du2", "du2", "b.pdf")  # doc_alias; a copies list
    assert got["du2"].addresses == {location_key(_X + u) for u in ("f/copy/b.pdf", "f/b.pdf", "f/b.doc")}
    assert got["eA"].addresses == {location_key("https://cal.test/a"), "eA"}  # an event's id is stable
    assert got["dzz"] == GoldDoc("dzz", None, "", "", frozenset({NOT_THERE + "dzz"}))
    old = gold_docs(base, ["dp32", "da2"])  # a notice by its site and nid, whatever its scheme or &amp; spelling
    assert old["dp32"].address == gold_docs(new, ["du1"])["du1"].address == "cnknu:c.nknu.edu.tw/aca:662"
    assert old["da2"].addresses & got["du2"].addresses  # each build kept another copy: one document
    base.close()
    new.close()


def test_gold_diff_compares_by_address(tmp_path):
    # every id changed (the id scheme, plan 2026-11-08 T1): compared by id, all of Q01 would differ; by address only
    # what a question really lost or gained is listed
    base_gold = {"Q01": {"docs": ["dp32", "da2", "eA"], "note": ""}, "Q02": {"docs": ["da3"], "note": ""},
                 "N03": {"docs": ["da4140", "dp32"], "note": ""}, "Q04": {"docs": ["dp77"], "note": ""}}
    new_gold = {"Q01": {"docs": ["du1", "du2", "eA"], "note": ""}, "Q02": {"docs": ["du5"], "note": "換了新表"},
                "N03": {"docs": ["du1"], "note": "files/2110 在 corpus 里没有，对应不到"}, "Q04": {"docs": [], "note": ""},
                "Q05": {"docs": ["du9"], "note": ""}}
    base, new = _gold_dbs(tmp_path)
    assert gold_diff(base_gold, base, new_gold, new) == [
        "标准出处：基准 4/4 题对应到，新库 4/5 题对应到；按网址比（编号换过也比得了），4 题的标准出处有变",
        "基准 gold 里有 1 个编号在它的资料库里找不到（gold 不是给这个资料库做的？）：dp77",
        "Q02 少了 da3「舊表.pdf」x.test/aca/f/old.pdf；多了 du5「新表.pdf」x.test/aca/f/new.pdf｜換了新表",
        "N03 少了 da4140「新生手冊.pdf」x.test/aca/f/新生手冊.pdf｜files/2110 在 corpus 里没有，对应不到",
        "Q04 少了 dp77（资料库里没有）｜",
        "Q05（只在新的 gold 里） 多了 du9（即 du2）「b.pdf」x.test/aca/f/copy/b.pdf｜"]
    assert gold_diff(base_gold, base, base_gold, base)[0].endswith("0 题的标准出处有变")
    base.close()
    new.close()


def test_main_gold_diff_writes_the_lines(tmp_path, capsys):
    for conn in _gold_dbs(tmp_path):
        conn.close()
    base_gold = _write(tmp_path / "base.json", {"Q02": {"docs": ["da3"], "note": ""}})
    new_gold = _write(tmp_path / "new.json", {"Q02": {"docs": ["du5"], "note": ""}})
    out = tmp_path / "gold_diff.txt"
    argv = ["gold-diff", "--base-gold", str(base_gold), "--base-db", str(tmp_path / "base.db"), "--gold",
            str(new_gold), "--db", str(tmp_path / "new.db"), "--out", str(out)]
    assert main(argv) == 0
    assert out.read_text(encoding="utf-8").splitlines()[1].startswith("Q02 少了 da3「舊表.pdf」")
    assert "Q02 少了" in capsys.readouterr().out
    assert main([*argv[:5], "--gold", str(new_gold), "--db", str(tmp_path / "none.db"), "--out", str(out)]) == 2
    assert "新资料库" in capsys.readouterr().err and not (tmp_path / "none.db").exists()


def _fake_search(table: dict):
    """A search_docs stand-in: query -> hits as _result takes them; records (query, today, aliases, limit)."""
    calls = []

    def search(conn, query, *, today, aliases, limit=10):
        calls.append((query, today, aliases, limit))
        return _result(*table.get(query, []))

    search.calls = calls
    return search


_DAY = date(2026, 11, 1)


def test_evaluate_ranks_gold_for_the_question_and_the_best_agent_query():
    questions = [{"id": "Q01", "group": "core20", "question": "停修何時", "sources": []},
                 {"id": "N01", "group": "N", "question": "校車", "sources": []},
                 {"id": "Q02", "group": "core20", "question": "沒有出處", "sources": []}]
    gold = {"Q01": {"docs": ["g1", "g2"], "note": ""}, "N01": {"docs": ["bus"], "note": ""},
            "Q02": {"docs": [], "note": "对应不到"}}
    queries = {"Q01": ["停修 115-1", "停修"], "N01": ["校車 時刻表", "交通車"]}
    # changed 11-03 on purpose: "bus" was folded under a hit for 校車 and 交通車; with no series it is a hit of its own
    search = _fake_search({"停修何時": ["x", "y", "g2"], "停修 115-1": ["g1"], "停修": ["x"],
                           "校車": ["new", "bus"], "校車 時刻表": ["a", "b"], "交通車": ["a", "c", "bus"]})
    out = evaluate(None, questions, gold, queries, today=_DAY, aliases=[["停修", "棄選"]], search=search)
    q01, n01 = out["questions"]
    assert (q01["question_query"]["rank"], q01["question_query"]["doc"]) == (3, "g2")
    assert q01["question_query"]["top"] == ["x", "y", "g2"]
    assert [a["rank"] for a in q01["agent_queries"]] == [1, None]
    assert (q01["agent_best"]["rank"], q01["agent_best"]["query"]) == (1, "停修 115-1")
    assert n01["question_query"]["rank"] == 2
    assert n01["agent_best"] == {"rank": 3, "doc": "bus", "query": "交通車"}
    assert out["no_gold"] == ["Q02"]
    m = out["metrics"]
    assert (m["question"]["core20"]["top3"], m["question"]["core20"]["top1"], m["agent"]["core20"]["top1"]) == (1, 0, 1)
    assert (m["question"]["N"]["top3"], m["agent"]["N"]["top1"], m["question"]["all"]["n"]) == (1, 0, 2)
    assert out["rank_applied"] is False
    assert {(c[1], c[3]) for c in search.calls} == {(_DAY, DEEP)} and search.calls[0][2] == [["停修", "棄選"]]


def test_evaluate_passes_a_rank_setting_when_search_takes_one():
    seen = []

    def search(conn, query, *, today, aliases, limit=10, rank=None):
        seen.append(rank)
        return _result("g")

    out = evaluate(None, [{"id": "Q01", "group": "core20", "question": "q", "sources": []}], {"Q01": {"docs": ["g"]}},
                   {"Q01": ["a"]}, today=_DAY, aliases=[], rank={"w_recent": 0.3}, search=search)
    assert out["rank_applied"] is True and seen == [{"w_recent": 0.3}] * 2


def _search_db(path) -> sqlite3.Connection:
    """Two documents indexed the way nknu.content.build indexes them, and the meta rows run reads."""
    from nknu.content.terms import canonical_tokens
    from nknu.core.textnorm import tokenize

    conn = sqlite3.connect(path)
    create_schema(conn)
    for doc_id, title, text in (("dp1", "線上申請停修", "停修申請期間"), ("dp2", "交通車時刻表", "和平往燕巢")):
        conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars) VALUES"
                     " (?, 'notice', 'newsdetail', ?, ?, '教務處', 1, ?, ?)", (doc_id, title, doc_id, text, len(text)))
        cur = conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, 0, 0, ?)", (doc_id, len(text)))
        conn.execute("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens) VALUES (?, ?, ?)",
                     (cur.lastrowid, *(" ".join(tokenize(s) + canonical_tokens(s)) for s in (title, text))))
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [("aliases_json", json.dumps([["停修", "棄選"]])),
                                                        ("built_at", "2026-11-01T20:27:00+08:00")])
    conn.commit()
    return conn


def test_evaluate_with_the_real_search(tmp_path):
    out = evaluate(_search_db(tmp_path / "s.db"), [{"id": "Q01", "group": "core20", "question": "停修申請",
                                                    "sources": []}],
                   {"Q01": {"docs": ["dp1"]}}, {"Q01": ["交通車"]}, today=_DAY, aliases=[])
    assert out["questions"][0]["question_query"]["rank"] == 1
    assert out["questions"][0]["agent_best"]["rank"] is None


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _question_files(tmp_path, sources=()):
    core = _write(tmp_path / "core.json", [{"id": "T01", "question": "停修申請", "sources": list(sources)}])
    noanswer = _write(tmp_path / "n.json", [
        {"id": "T02", "question": "校車", "use_as_noanswer": False,
         "evidence": [{"file": "archive-export/aca-20261027/files/0002-b.pdf"}]},
        {"id": "T03", "question": "捐贈", "use_as_noanswer": True, "evidence": []}])
    return core, noanswer


def test_load_questions_keeps_core_and_the_answerable_n_questions(tmp_path):
    core, noanswer = _question_files(tmp_path, [{"file": "x"}])
    qs = load_questions(core, noanswer)
    assert [(q["id"], q["group"]) for q in qs] == [("T01", "core20"), ("T02", "N")]
    assert qs[1]["sources"] == [{"file": "archive-export/aca-20261027/files/0002-b.pdf"}]


def test_connect_ro_cannot_write(tmp_path):
    import pytest

    _search_db(tmp_path / "s.db").close()
    with pytest.raises(sqlite3.OperationalError):
        connect_ro(tmp_path / "s.db").execute("CREATE TABLE x(a)")


def test_format_table_shows_metrics_and_each_question():
    # changed 11-03 on purpose: the question's run was folded (折2); with no series it is not among the results (—)
    run = {"query": "q", "rank": None, "doc": None, "top": []}
    best = {"rank": 12, "doc": "g", "query": "a"}
    report = {"today": "2026-11-01", "db_built_at": "b", "rank": None, "rank_applied": False,
              "metrics": {m: {g: metrics([Place(1, "g")]) for g in (*GROUPS, "all")} for m in ("question", "agent")},
              "questions": [{"id": "Q01", "group": "core20", "question": "停修？", "gold": ["g"],
                             "question_query": run, "agent_queries": [], "agent_best": best}],
              "no_gold": ["Q02"]}
    text = format_table(report)
    assert "| core20 | 1 | 1 | 1 | 1 | 1.000 |" in text
    assert "| Q01 | — | 12 |" in text and "Q02" in text and "没传 rank" in text and "现状" not in text
    assert "折" not in text  # no 折N cell, no 折叠 column
    assert "不在候选里" in text and DEEP == POOL_DOCS
    status_quo = {**report, "rank": {"recency": False, "type_prior": False}, "rank_applied": True}
    assert 'rank={"recency": false, "type_prior": false}' in format_table(status_quo)
    assert "没用上" in format_table({**status_quo, "rank_applied": False})


def test_main_gold_writes_the_mapping(tmp_path):
    db = tmp_path / "c.db"
    _content_db(db).close()
    corpus = _write(tmp_path / "corpus.json", _CORPUS)
    core, noanswer = _question_files(tmp_path, [{"file": "archive-export/aca-20261027/pages/0004-停修.html"}])
    out = tmp_path / "gold.json"
    assert main(["gold", "--db", str(db), "--corpus", str(corpus), "--core", str(core), "--noanswer", str(noanswer),
                 "--out", str(out)]) == 0
    gold = json.loads(out.read_text(encoding="utf-8"))
    assert list(gold) == ["T01", "T02"] and gold["T01"]["docs"] == ["dp5"] and gold["T02"] == {"docs": ["da2"],
                                                                                                 "note": ""}


def test_main_run_writes_the_report(tmp_path, capsys):
    db = tmp_path / "s.db"
    _search_db(db).close()
    core, noanswer = _question_files(tmp_path)
    gold = _write(tmp_path / "gold.json", {"T01": {"docs": ["dp1"], "note": ""}, "T02": {"docs": ["dp2"], "note": ""}})
    runs = _write(tmp_path / "summary.json", [{"id": "T02", "calls": [
        {"name": "mcp__nknu_aca__search", "input": {"query": "交通車 時刻表"}}]}])
    out = tmp_path / "report.json"
    assert main(["run", "--db", str(db), "--gold", str(gold), "--core", str(core), "--noanswer", str(noanswer),
                 "--runs", str(runs), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["today"] == "2026-11-01" and report["rank"] is None and report["db_built_at"].startswith("2026")
    assert report["metrics"]["question"]["core20"]["top1"] == 1 and report["metrics"]["agent"]["N"]["top1"] == 1
    assert report["titles"]["dp2"] == "交通車時刻表" and len(report["search"]["sha256"]) == 64
    assert "| core20 |" in capsys.readouterr().out


def test_main_run_always_names_its_out_file(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as stop:
        main(["run", "--rank", '{"w_recent": 0.3}', "--db", str(tmp_path / "none.db")])
    assert stop.value.code == 2


# ---- tests: T10 (plan 2026-11-02; addendum D, F, G1, G4, G7, I1) ---------------------------------------------------


def _no_models(monkeypatch):
    """No real model in these tests (tests/conftest.py's guard covers tests/ only): loading either one raises, as an
    uncached model does."""
    from nknu.core import models

    def refuse(*args, **kwargs):
        raise models.ModelUnavailable("offline_eval 的测试不载入真模型")

    monkeypatch.setattr(models, "load_embedder", refuse)
    monkeypatch.setattr(models, "load_reranker", refuse)
    monkeypatch.setattr(models, "_SHARED", {})


def test_fragments_and_norm():
    assert fragments("停修以一科為限…不予退費") == ["停修以一科為限", "不予退費"] and norm_quote("１ 科") == "1科"
    assert fragments("甲…停修申請") == ["停修申請"]  # a one-character piece checks nothing
    assert fragments("申請書...繳交／退費") == ["申請書", "繳交", "退費"]
    assert fragments("…") == [] and norm_quote("| **一、** 選課 |") == "一選課"


def test_strings_of_takes_every_string_value():
    assert strings_of({"a": "x", "b": [{"c": "y"}, 3, None, "z"], "d": {"e": "w"}}) == "x\ny\nz\nw"


def test_delivered():
    resp = {"results": [{"content": "第二條 停修以一科為限，\n不予退費。"}], "notes": ["【系統】…"]}
    assert delivered(resp, "停修以一科為限，不予退費") and not delivered(resp, "可退費")
    assert not delivered(resp, "…")  # no fragment: never checkable, never delivered


def test_delivered_across_table_cells_and_emphasis():  # Docling Markdown (Q35's tables, DOCX bold)
    assert delivered({"content": "| 軟體工程學系 | 16,815 | 10,775 |"}, "軟體工程學系 … 16,815  10,775")
    assert delivered({"content": "**一、選課期間**：9月1日起"}, "一、選課期間：9月1日起")


def test_question_quotes_leave_out_not_gold():
    q = {"id": "N10", "sources": [{"file": "archive-export/aca-20261027/files/2440-x.pdf", "quote": "550"},
                                  {"file": "archive-export/aca-20261027/files/0001-y.pdf", "quote": "B1"}]}
    assert question_quotes(q) == ["B1"]
    twice = {"id": "Q99", "sources": [{"file": "a", "quote": "甲乙"}, {"file": "b", "quote": " 甲乙 "}, {"file": "c"}]}
    assert question_quotes(twice) == ["甲乙"]


def test_load_noanswer_keeps_the_true_no_answer_questions(tmp_path):
    _, noanswer = _question_files(tmp_path)
    assert [(q["id"], q["question"], q["group"]) for q in load_noanswer(noanswer)] == [("T03", "捐贈", "noanswer")]


def test_replayed_response_keeps_the_ranking(monkeypatch):
    seen = []

    def fake_build(conn, result, *, today):
        seen.append(result)
        return {"results": [{"content": "停修申請期間"}]}

    monkeypatch.setattr(sys.modules[__name__], "build_response", fake_build)

    def search(conn, query, *, today, aliases, limit=10):
        return dataclasses.replace(_result(*(f"d{i}" for i in range(12))), ranking="hybrid", best_rerank=0.9)

    q = {"id": "Q01", "group": "core20", "question": "停修", "sources": [{"file": "f", "quote": "停修申請期間"}]}
    out = evaluate(None, [q], {"Q01": {"docs": ["d0"]}}, {}, today=_DAY, aliases=[], search=search)
    assert len(seen) == 1 and seen[0].ranking == "hybrid" and len(seen[0].hits) == TOP == 10
    assert out["questions"][0]["question_delivered"] == [True] and out["questions"][0]["checkable"] == [False]


def test_uncheckable_quotes_are_not_counted(tmp_path):
    conn = _search_db(tmp_path / "s.db")
    q = {"id": "Q01", "group": "core20", "question": "停修申請",
         "sources": [{"file": "a", "quote": "停修申請期間"}, {"file": "b", "quote": "沒有一份文件寫這句話"}]}
    out = evaluate(conn, [q], {"Q01": {"docs": ["dp1"]}}, {"Q01": ["交通車"]}, today=_DAY, aliases=[])
    row = out["questions"][0]
    assert row["checkable"] == [True, False] and row["question_delivered"] == [True, False]
    assert row["agent_delivered"] == [False, False]
    assert out["metrics"]["delivery"] == {
        "question": {"quotes": 1, "checkable": 1, "questions_any": 1, "n": 1, "total": 2},
        "agent": {"quotes": 0, "checkable": 1, "questions_any": 0, "n": 1, "total": 2}}


def test_noanswer_report(tmp_path):
    def search(conn, query, *, today, aliases, limit=10):
        return dataclasses.replace(_result("a", "b", "c", "d"), ranking="hybrid", best_rerank=0.2)

    _, noanswer = _question_files(tmp_path)
    out = evaluate(None, [], {}, {"T03": ["捐款 收據"]}, today=_DAY, aliases=[], search=search,
                   noanswer=load_noanswer(noanswer))
    row = out["noanswer"][0]
    assert (row["id"], row["question"], row["question_top3"], row["question_best_rerank"]) == (
        "T03", "捐贈", ["a", "b", "c"], 0.2)
    assert row["agent"] == [{"query": "捐款 收據", "top3": ["a", "b", "c"], "best_rerank": 0.2}]
    assert out["metrics"]["best_rerank"] == {"answerable": [], "noanswer": [0.2]}


def test_best_rerank_is_empty_in_a_keyword_run(tmp_path):
    out = evaluate(_search_db(tmp_path / "s.db"),
                   [{"id": "Q01", "group": "core20", "question": "停修申請", "sources": []}], {"Q01": {"docs": ["dp1"]}},
                   {}, today=_DAY, aliases=[], noanswer=[{"id": "N04", "group": "noanswer", "question": "交通車",
                                                          "sources": []}])
    assert out["metrics"]["best_rerank"] == {"answerable": [], "noanswer": []}
    assert out["noanswer"][0]["question_top3"] == ["dp2"] and out["ranking_fallbacks"] == []
    assert out["questions"][0]["question_query"]["ranking"] == "keyword"


def test_ranking_fallbacks_list_the_queries_a_hybrid_run_ranked_by_keywords_only():
    from types import SimpleNamespace

    def search(conn, query, *, today, aliases, limit=10):
        if query == "!!!":  # nothing to search for: no hit, no fallback
            return SearchResult(query, Understood([], [], [], [], []), [], 0)
        result = _result("g")
        return result if query == "壞了" else dataclasses.replace(result, ranking="hybrid")

    q = [{"id": "Q01", "group": "core20", "question": "好的", "sources": []}]
    args = ({"Q01": {"docs": ["g"]}}, {"Q01": ["壞了", "!!!"]})
    search.config = SimpleNamespace(dense=True, rerank=False)
    assert evaluate(None, q, *args, today=_DAY, aliases=[], search=search)["ranking_fallbacks"] == ["壞了"]
    search.config = SimpleNamespace(dense=False, rerank=False)  # keyword ranking is what this run asked for
    assert evaluate(None, q, *args, today=_DAY, aliases=[], search=search)["ranking_fallbacks"] == []


def test_hybrid_fn_keeps_the_rank_annotation():
    from nknu.retrieval.hybrid import HybridConfig

    search = hybrid_fn(HybridConfig(rerank=False))
    assert _rank_class(search) is RankConfig and search.config == HybridConfig(rerank=False)


def test_hybrid_fn_with_both_models_off_ranks_as_search_docs(tmp_path, monkeypatch):
    from nknu.retrieval.hybrid import HybridConfig

    _no_models(monkeypatch)
    conn = _search_db(tmp_path / "s.db")
    q = [{"id": "Q01", "group": "core20", "question": "停修申請", "sources": []}]
    kw = evaluate(conn, q, {"Q01": {"docs": ["dp1"]}}, {"Q01": ["交通車"]}, today=_DAY, aliases=[])
    hy = evaluate(conn, q, {"Q01": {"docs": ["dp1"]}}, {"Q01": ["交通車"]}, today=_DAY, aliases=[],
                  search=hybrid_fn(HybridConfig(dense=False, rerank=False)), deep=CANDIDATES + 300)
    assert hy["metrics"]["question"] == kw["metrics"]["question"] and hy["ranking_fallbacks"] == []


def test_memo_models_compute_each_text_once():
    import numpy as np
    import pytest

    from nknu.core import models

    class Embedder:
        def __init__(self):
            self.calls = []

        def encode(self, texts, *, batch_size=16):
            self.calls.append(list(texts))
            return np.stack([np.full(3, len(t), dtype=np.float32) for t in texts])

    class Reranker:
        def __init__(self):
            self.calls = []

        def score(self, query, passages, *, batch_size=16):
            self.calls.append((query, list(passages)))
            return [float(len(p)) for p in passages]

    def boom():
        raise AssertionError("loaded")

    e, r = Embedder(), Reranker()
    memo = MemoEmbedder(lambda: e)
    assert (MemoEmbedder(boom).model_id, MemoEmbedder(boom).revision) == (models.EMBED_ID, models.EMBED_REVISION)
    assert memo.encode(["ab", "abc", "ab"]).shape == (3, 3) and memo.encode(["abc"])[0][0] == 3
    assert e.calls == [["ab", "abc"]] and memo.encode([]).shape == (0, models.EMBED_DIM)
    rerank = MemoReranker(lambda: r)
    assert rerank.score("q", ["x", "yy", "x"]) == [1.0, 2.0, 1.0] and rerank.score("q", ["yy", "zzz"]) == [2.0, 3.0]
    assert rerank.score("q2", ["x"]) == [1.0] and rerank.score("q", []) == []
    assert r.calls == [("q", ["x", "yy"]), ("q", ["zzz"]), ("q2", ["x"])]

    def unavailable():
        raise models.ModelUnavailable("not cached")

    with pytest.raises(models.ModelUnavailable):  # hybrid catches it inside its own try and ranks by keywords
        MemoReranker(unavailable).score("q", ["p"])


def _models_mark(fn):
    """@pytest.mark.models (deselected by default: pyproject's addopts) without importing pytest when the script runs
    on its own."""
    try:
        import pytest
    except ImportError:  # the script itself needs no pytest
        return fn
    return pytest.mark.models(fn)


@_models_mark
def test_real_models_behind_the_memo_give_the_shared_scores():
    """The per-run cache changes nothing: the same vectors and rerank scores as the shared models (real bge-m3 and
    bge-reranker-v2-m3 from the local cache at the pins, offline; skipped when either is not cached)."""
    import numpy as np
    import pytest

    from nknu.core import models

    if not all(models.is_cached(m, r) for m, r in models.PINS):
        pytest.skip("bge-m3 or the reranker is not in the local cache")
    texts = ["停修以一科為限", "多益 550 分", "停修以一科為限"]
    assert np.allclose(MemoEmbedder().encode(texts), models.shared_embedder().encode(texts), atol=1e-3)
    query, passages = "停修有科目數限制嗎", ["停修以一科為限，不予退費。", "交通車時刻表", "停修以一科為限，不予退費。"]
    got, want = MemoReranker().score(query, passages), models.shared_reranker().score(query, passages)
    assert np.allclose(got, want, atol=1e-3) and got[0] == got[2] and got[0] > got[1]


_SCOPE_DDL = """
CREATE TABLE IF NOT EXISTS units (unit_id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, parent_id TEXT,
                                  campus TEXT);
CREATE TABLE IF NOT EXISTS unit_aliases (alias TEXT NOT NULL, unit_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doc_scope (doc_id TEXT NOT NULL, relation TEXT NOT NULL, unit_id TEXT, campus TEXT,
                                      level TEXT, cohort_from INTEGER);
"""
_UNITS = (("國立高雄師範大學", "学校", None, None, ()), ("科技學院", "学院", "國立高雄師範大學", "燕巢", ()),
          ("理學院", "学院", "國立高雄師範大學", "燕巢", ()),
          ("軟體工程與管理學系", "系所", "科技學院", "燕巢", ("軟體系", "軟工系")),
          ("化學系", "系所", "理學院", "燕巢", ()))


def _index_db(path, rows) -> sqlite3.Connection:
    """rows (doc_id, title, text, identity date) indexed the way nknu.content.build indexes them, one section and one
    chunk each, with the meta rows run reads."""
    from nknu.content.terms import canonical_tokens
    from nknu.core.textnorm import tokenize

    conn = sqlite3.connect(path)
    create_schema(conn)
    for doc_id, title, text, ident in rows:
        conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars,"
                     " ident_date) VALUES (?, 'notice', 'newsdetail', ?, ?, '教務處', 1, ?, ?, ?)",
                     (doc_id, title, "https://x.test/" + doc_id, text, len(text), ident))
        conn.execute("INSERT INTO sections VALUES (?, 0, '', 0, ?)", (doc_id, len(text)))
        cur = conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, 0, 0, ?)", (doc_id, len(text)))
        conn.execute("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens) VALUES (?, ?, ?)",
                     (cur.lastrowid, *(" ".join(tokenize(s) + canonical_tokens(s)) for s in (title, text))))
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [("aliases_json", "[]"),
                                                        ("built_at", "2026-11-02T20:00:00+08:00")])
    conn.commit()
    return conn


def _scope_db(path) -> sqlite3.Connection:
    """Two department rules alike in words; the chemistry one newer (so it leads without the unit scope)."""
    conn = _index_db(path, [("dsoft", "軟體工程與管理學系碩士班修業規定", "碩士班畢業學分三十學分", "2025-10-01"),
                            ("dchem", "化學系碩士班修業規定", "碩士班畢業學分三十學分", "2026-10-01")])
    conn.executescript(_SCOPE_DDL)
    for unit_id, kind, parent, campus, aliases in _UNITS:
        conn.execute("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)",
                     (unit_id, unit_id, kind, parent, campus))
        conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)",
                         [(a, unit_id) for a in (unit_id, *aliases)])
    conn.executemany("INSERT INTO doc_scope(doc_id, relation, unit_id, campus, level, cohort_from) VALUES"
                     " (?, ?, ?, ?, ?, ?)", [("dsoft", "applies_to", "軟體工程與管理學系", None, "碩士", None),
                                             ("dchem", "applies_to", "化學系", None, "碩士", None)])
    conn.commit()
    return conn


_SCOPE_QUESTIONS = [{"id": "Q35", "group": "core20", "question": "軟工系碩士班畢業學分", "sources": []},
                    {"id": "Q08", "group": "core20", "question": "畢業學分幾學分", "sources": []}]
_SCOPE_GOLD = {"Q35": {"docs": ["dsoft"]}, "Q08": {"docs": ["dchem"]}}
_SCOPE_QUERIES = {"Q35": ["畢業學分"], "Q08": ["修業規定"]}


def test_scope_effect_reruns_the_questions_that_name_a_unit(tmp_path, monkeypatch):
    from nknu.retrieval.hybrid import HybridConfig

    _no_models(monkeypatch)
    conn = _scope_db(tmp_path / "u.db")
    # addendum F's weights (0 since 11-08); scope_factor 1: the audience factor of plan 2026-11-14 off, as it was
    off = HybridConfig(dense=False, rerank=False, w_scope=0.3, w_mention=0.1, scope_factor=1.0)
    # 軟工系 brings 軟體工程與管理學系 since 11-08 10:16 (unit names, addendum M9), which puts dsoft first by itself:
    # off here, so the scope's own effect shows
    args = {"today": _DAY, "aliases": [], "rank": {"unit_names": False}, "deep": CANDIDATES + 300}
    rows = evaluate(conn, _SCOPE_QUESTIONS, _SCOPE_GOLD, _SCOPE_QUERIES, search=hybrid_fn(off), **args)["questions"]
    assert [r["question_query"]["rank"] for r in rows] == [1, 1]  # the scope puts the named department first
    effect = scope_effect(conn, _SCOPE_QUESTIONS, _SCOPE_GOLD, _SCOPE_QUERIES, rows,
                          search=hybrid_fn(dataclasses.replace(off, w_scope=0.0, w_mention=0.0)), **args)
    assert effect["scope_tables"] is True and effect["changed"] == ["Q35"]
    assert [q["id"] for q in effect["questions"]] == ["Q35"]  # Q08 names nothing: it ranks the same by construction
    q35 = effect["questions"][0]
    assert "軟體工程與管理學系" in q35["named"]["question"] and "碩士" in q35["named"]["question"]
    assert (q35["with"]["question"]["rank"], q35["without"]["question"]["rank"]) == (1, 2)
    assert q35["named"]["agent"] == 0 and effect["queries_named"] == 1
    assert effect["metrics_without"]["question"]["all"]["top1"] == 1 and effect["config"]["w_scope"] == 0.0
    plain = evaluate(conn, _SCOPE_QUESTIONS[:1], _SCOPE_GOLD, _SCOPE_QUERIES, **{**args, "rank": None},
                     search=hybrid_fn(dataclasses.replace(off, w_scope=0.0, w_mention=0.0)))["questions"]
    assert plain[0]["question_query"]["rank"] == 1  # with the unit names, without the scope


def test_scope_effect_without_scope_tables_compares_nothing(tmp_path, monkeypatch):
    from nknu.retrieval.hybrid import HybridConfig

    _no_models(monkeypatch)
    conn = _search_db(tmp_path / "s.db")
    q = [{"id": "Q01", "group": "core20", "question": "停修申請", "sources": []}]
    args = {"today": _DAY, "aliases": [], "rank": None, "deep": CANDIDATES + 300}
    search = hybrid_fn(HybridConfig(dense=False, rerank=False, w_scope=0.0, w_mention=0.0))
    rows = evaluate(conn, q, {"Q01": {"docs": ["dp1"]}}, {}, search=search, **args)["questions"]
    effect = scope_effect(conn, q, {"Q01": {"docs": ["dp1"]}}, {}, rows, search=search, **args)
    assert effect["scope_tables"] is False and effect["questions"] == [] and effect["changed"] == []


def _stats_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    create_schema(conn)
    docs = [("da1", "attachment", "附件:pdf:有文字层", "u112.pdf", "碩士班招生簡章", "一、報名"),
            ("da2", "attachment", "附件:pdf:有文字层", "選課攻略.pdf", "", "選課"),
            ("da3", "attachment", "附件:pdf:含OCR", "錄取名單", "115學年度學士班申請入學錄取名單", "名單"),
            ("da4", "attachment", "附件:pdf:含OCR", "錄取名單", "115學年度碩士班甄試錄取名單", "名單"),
            ("dp1", "page", "page", "選課須知", "", "選課"), ("e1", "event", "行事历", "開學", "", "開學")]
    conn.executemany("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, parent_title, has_text, text,"
                     " chars) VALUES (?, ?, ?, ?, ?, '教務處', ?, 1, ?, ?)",
                     [(d, k, kr, t, "https://x.test/" + d, pt, x, len(x)) for d, k, kr, t, pt, x in docs])
    conn.executemany("INSERT INTO sections VALUES (?, ?, '', ?, ?)",
                     [("dp1", 0, 0, 10), ("dp1", 1, 10, 100), ("da1", 0, 0, 39), ("da1", 1, 39, 80), ("e1", 0, 0, 2)])
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("counts_json", json.dumps({"attachments": {"title_from_page": 2, "title_file_name": 2, "parent": 3,
                                                    "no_parent": 1}}))])
    conn.commit()
    return conn


def test_build_stats(tmp_path):
    stats = build_stats(_stats_db(tmp_path / "b.db"))
    names = stats["file_name_titles"]
    assert (names["file_name"], names["by_kind"], names["opaque"], names["docs"]) == (2, {"attachment": 2}, 1, 6)
    assert names["opaque_examples"] == ["u112.pdf"] and names["build_attachments"]["title_file_name"] == 2
    short = stats["short_sections"]
    assert short["under"] == 40 and short["by_kind"]["page"] == {"docs": 1, "sections": 2, "short": 1,
                                                                 "docs_with_short": 1}
    assert short["by_kind"]["attachment"]["short"] == 1 and short["by_kind"]["event"]["short"] == 1
    assert short["total"] == {"docs": 3, "sections": 5, "short": 3, "docs_with_short": 3}
    assert short["by_kind_raw"]["附件:pdf:有文字层"]["sections"] == 2
    # changed 11-03 on purpose: the series numbers (series_embed, D; audience_series, I1) went with the series
    assert set(stats) == {"file_name_titles", "short_sections"}


def test_build_stats_of_a_skeleton_database(tmp_path):
    # the skeleton's database (skeleton-2) still has the series tables and no parent_title: the stats read neither
    conn = _stats_db(tmp_path / "b.db")
    conn.execute("ALTER TABLE docs DROP COLUMN parent_title")
    conn.execute("CREATE TABLE series_members (series_id TEXT NOT NULL, doc_id TEXT NOT NULL, ord INTEGER NOT NULL)")
    conn.execute("INSERT INTO series_members VALUES ('s1', 'da3', 0)")
    conn.execute("DELETE FROM meta")
    stats = build_stats(conn)
    assert set(stats) == {"file_name_titles", "short_sections"}
    assert stats["file_name_titles"]["build_attachments"] is None


def test_numbering_marks_count_han_and_digit_marks():
    text = "一、選課期間\n內容\n（一）加選\n1. 退選\n選課期間 一\n0 元\n| 一、 | x |\n## 二、注意事項\n第三條 退費"
    assert numbering_marks(text) == (5, 4)
    assert numbering_marks("1.\n報名") == (1, 0) and numbering_marks("1.\n2.\n3.") == (0, 0)  # a column of row numbers
    assert numbering_marks("壹、總則 ........ 1\n") == (0, 0)  # a contents line


def _cache_entry(cache, sha: str, result: dict, text: str, extras: dict | None = None) -> None:
    cache.save(sha, result, text, {k: json.dumps(v, ensure_ascii=False).encode() for k, v in (extras or {}).items()})


def test_totext_stats(tmp_path):
    from nknu.totext.cache import Cache

    cache = Cache(tmp_path / "totext" / "cache")
    pages = ["一、報名資格\n本校學生\n（二）繳費\n", "選課期間 一\n1. 繳費方式\n說明文字\n", "## 選課期間\n文字\n", ""]
    starts = [sum(len(p) for p in pages[:i]) for i in range(len(pages))]
    _cache_entry(cache, "a" * 64, {"route": "pdf", "status": "partial", "pages": 4, "page_starts": starts,
                                   "ocr_pages": [{"page": 2, "why": "few_chars"}], "docling_ocr_pages": [3],
                                   "broken_pages": [], "failed_pages": [4]},
                 "".join(pages), {"docling_ocr.json": {"3": ["選課期間", "二、上課"]}})
    _cache_entry(cache, "b" * 64, {"route": "pdf", "status": "ok", "pages": 1, "page_starts": [0],
                                   "ocr_pages": [{"page": 1, "why": "misread"}], "docling_ocr_pages": [1],
                                   "broken_pages": [1], "failed_pages": []}, "五、規定\n")
    _cache_entry(cache, "c" * 64, {"route": "image", "status": "ok"}, "三、注意事項\n請攜帶證件")
    _cache_entry(cache, "d" * 64, {"route": "page", "status": "ok"}, "一、網頁上的編號不算")
    _cache_entry(cache, "e" * 64, {"route": "pdf", "status": "ok", "version": "totext-0", "pages": 9}, "")
    stats = totext_stats(tmp_path / "totext")
    assert (stats["entries"], stats["stale"], stats["by_route"]) == (4, 1, {"image": 1, "page": 1, "pdf": 2})
    assert stats["pdf"] == {"files": 2, "pages": 5, "ocr_pages": 2, "few_chars": 1, "misread": 1, "docling_ocr": 2,
                            "docling_kept": 1, "broken": 1, "text_layer": 1, "failed": 1, "unsplit": 0}
    num = stats["numbering"]
    assert (num["text_layer"]["texts"], num["text_layer"]["marks"], num["text_layer"]["han_marks"]) == (1, 2, 2)
    assert (num["ocr_pages"]["texts"], num["ocr_pages"]["marks"], num["ocr_pages"]["han_marks"]) == (2, 2, 1)
    assert (num["docling_ocr"]["texts"], num["docling_ocr"]["marks"], num["docling_ocr"]["han_marks"]) == (1, 1, 1)
    assert (num["image"]["texts"], num["image"]["marks"]) == (1, 1)
    assert num["text_layer"]["chars"] == len("一報名資格本校學生二繳費")
    assert num["text_layer"]["per_1000"] == round(2 * 1000 / num["text_layer"]["chars"], 2)


def test_reports_show_scope_build_and_totext(tmp_path, monkeypatch):
    from nknu.retrieval.hybrid import HybridConfig
    from nknu.totext.cache import Cache

    _no_models(monkeypatch)
    _scope_db(tmp_path / "u.db").close()
    _cache_entry(Cache(tmp_path / "totext" / "cache"), "a" * 64,
                 {"route": "pdf", "status": "ok", "pages": 1, "page_starts": [0], "ocr_pages": [],
                  "docling_ocr_pages": [], "broken_pages": [], "failed_pages": []}, "一、報名\n")
    core = _write(tmp_path / "core.json", [{**q, "sources": []} for q in _SCOPE_QUESTIONS])
    noanswer = _write(tmp_path / "n.json", [{"id": "N04", "question": "捐贈", "use_as_noanswer": True,
                                             "evidence": []}])
    runs = _write(tmp_path / "summary.json", [
        {"id": k, "calls": [{"name": "mcp__nknu_aca__search", "input": {"query": t}} for t in v]}
        for k, v in _SCOPE_QUERIES.items()])
    gold = _write(tmp_path / "gold.json", _SCOPE_GOLD)
    # addendum F's weights (0 since 11-08); scope_factor 1: the audience factor of plan 2026-11-14 off, as it was
    off = HybridConfig(dense=False, rerank=False, w_scope=0.3, w_mention=0.1, scope_factor=1.0)
    report = run_offline(tmp_path / "u.db", gold=gold, core=core, noanswer=noanswer, runs=runs,
                         rank={"unit_names": False},  # the scope's own effect (the unit names put dsoft first too)
                         search=hybrid_fn(off), deep=CANDIDATES + 300,
                         unscoped=hybrid_fn(dataclasses.replace(off, w_scope=0.0, w_mention=0.0)),
                         totext=tmp_path / "totext")
    assert report["scope_effect"]["changed"] == ["Q35"] and report["totext"]["pdf"]["text_layer"] == 1
    assert report["search"]["function"] == "nknu.retrieval.hybrid.hybrid_search"
    assert report["search"]["config"]["dense"] is False and len(report["search"]["sha256"]) == 64
    assert report["noanswer"][0]["id"] == "N04" and report["missing_gold"] == []
    text = format_table(report)
    assert all(s in text for s in ("单位范围", "Q35", "不到 40 字的节", "每千字", "查无题", "送到"))
    md = compare_reports(report, report)
    assert all(s in md for s in ("单位范围", "不到 40 字的节", "每千字", "Q35：1 → 1 ="))


def _cmp(top3: int, q08: int, built: str, delivered: list[bool], score: float | None) -> dict:
    """A run report as run_offline writes it, cut to what compare reads."""
    def table(top3: int) -> dict:
        return {"n": 33, "top1": 10, "top3": top3, "top10": 26, "mrr": 0.5, "past10": 5, "missing": 2}

    run = {"query": "q", "rank": q08, "doc": "g", "top": []}
    best = {"rank": q08, "doc": "g", "query": "q"}
    metrics_ = {mode: {g: table(top3) for g in (*GROUPS, "all")} for mode in ("question", "agent")}
    metrics_["delivery"] = {mode: {"quotes": sum(delivered), "checkable": 2, "questions_any": int(any(delivered)),
                                   "n": 1, "total": 3} for mode in ("question", "agent")}
    metrics_["best_rerank"] = {"answerable": [] if score is None else [score],
                               "noanswer": [] if score is None else [score / 2]}
    return {"db": "a.db", "db_built_at": built, "today": "2026-11-01", "search": {"function": "f"}, "rank": None,
            "metrics": metrics_, "questions": [{"id": "Q08", "group": "core20", "question": "學分", "gold": ["g"],
                                                "question_query": run, "agent_queries": [], "agent_best": best,
                                                "quotes": ["甲乙", "丙丁", "戊己"], "checkable": [True, True, False],
                                                "question_delivered": [*delivered, False],
                                                "agent_delivered": [*delivered, False]}],
            "noanswer": [{"id": "N04", "question": "捐贈", "question_top3": ["dx"], "question_best_rerank": score,
                          "agent": []}],
            "titles": {"dx": "捐贈辦法"}, "ranking_fallbacks": [], "missing_gold": []}


def test_compare_reports_lines():
    md = compare_reports(_cmp(21, 12, "2026-11-01T22:32", [True, False], None),
                         _cmp(24, 3, "2026-11-03T08:00", [True, True], 0.4))
    assert "| 前 3 | 21/33 | 24/33 |" in md and "Q08：12 → 3 ↑" in md
    assert "两边都能查的原句" in md and "查无题" in md and "捐贈辦法" in md
    assert "2026-11-01T22:32" in md and "2026-11-03T08:00" in md and "进入前 3：Q08" in md


def test_compare_reports_takes_an_old_report():
    old = _cmp(21, 12, "b", [True, False], None)
    for key in ("noanswer", "ranking_fallbacks", "missing_gold"):
        old.pop(key)
    old["metrics"].pop("delivery")
    old["metrics"].pop("best_rerank")
    for key in ("quotes", "checkable", "question_delivered", "agent_delivered"):
        old["questions"][0].pop(key)
    # a report written before 11-03 also counts folds and records folded places: compare reads past them
    for grp in old["metrics"]["question"].values():
        grp["folded"] = 1
    old["questions"][0]["question_query"] |= {"folded_under": 2, "folded_doc": "g"}
    md = compare_reports(old, _cmp(24, 3, "n", [True, True], 0.4))
    assert "| 前 3 | 21/33 | 24/33 |" in md and "Q08：12 → 3 ↑" in md and "—" in md and "折" not in md


# ---- exact copies count (plan 2026-11-14 T1)

_LONG = "本校學生申請停修課程，應於規定期間內上網填寫申請表，經任課教師及系主任同意後送教務處辦理。" * 6  # > 200


def _copies_db(path) -> sqlite3.Connection:
    """Gold dg (a notice on site aca) with a whitespace-only variant on another site, a page with the same text, a
    short gold and its short twin, and an unrelated notice."""
    conn = sqlite3.connect(path)
    create_schema(conn)
    spaced = "\n".join(_LONG[i:i + 40] for i in range(0, len(_LONG), 40)).replace("。", "。 　")
    rows = [("dg", "notice", _LONG, "aca"), ("dc", "notice", spaced, "osa"), ("dk", "page", _LONG, "osa"),
            ("ds", "notice", "短公告 甲乙丙", "aca"), ("dt", "notice", "短公告甲乙丙", "osa"),
            ("dx", "notice", _LONG[:-1] + "！", "osa")]
    conn.executemany("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars, site)"
                     " VALUES (?, ?, ?, '停修', ?, '教務處', 1, ?, ?, ?)",
                     [(d, k, k, "https://x.test/" + d, t, len(t), s) for d, k, t, s in rows])
    conn.commit()
    return conn


def test_exact_copies_by_kind_and_whitespace_free_text(tmp_path):
    conn = _copies_db(tmp_path / "c.db")
    out = exact_copies(conn, ["dg", "ds", "dmissing"])
    # dc differs only in whitespace (another site): a copy; dk is a page, not a notice; dx differs in one character
    assert out == {"dg": ["dc"], "ds": [], "dmissing": []}  # ds/dt: under 200 characters; dg is not its own copy
    assert exact_copies(None, ["dg"]) == {"dg": []}


def test_evaluate_counts_copies_only_in_the_widened_numbers(tmp_path):
    conn = _copies_db(tmp_path / "c.db")
    questions = [{"id": "Q01", "group": "core20", "question": "停修", "sources": []},
                 {"id": "N01", "group": "N", "question": "短", "sources": []}]
    gold = {"Q01": {"docs": ["dg"]}, "N01": {"docs": ["ds"]}}
    search = _fake_search({"停修": ["dk", "dc", "dx", "dg"], "停修 申請": ["dc"], "短": ["dt", "ds"]})
    out = evaluate(conn, questions, gold, {"Q01": ["停修 申請"]}, today=_DAY, aliases=[], search=search,
                   deliver=False)
    q01, n01 = out["questions"]
    assert q01["gold_copies"] == ["dc"] and n01["gold_copies"] == []
    qq = q01["question_query"]
    assert (qq["rank"], qq["doc"], qq["rank_copies"], qq["doc_copies"]) == (4, "dg", 2, "dc")
    assert q01["agent_best"]["rank"] is None
    assert q01["agent_best_copies"] == {"rank": 1, "doc": "dc", "query": "停修 申請"}
    # found directly: the same rank in both sets
    assert (n01["question_query"]["rank"], n01["question_query"]["rank_copies"]) == (2, 2)
    m = out["metrics"]
    assert (m["question"]["core20"]["top3"], m["question_copies"]["core20"]["top3"]) == (0, 1)
    assert (m["agent"]["all"]["top1"], m["agent_copies"]["all"]["top1"]) == (0, 1)
    assert m["question_copies"]["N"] == m["question"]["N"]
    assert set(m["question_copies"]["all"]) == set(m["question"]["all"])


def test_reports_show_the_widened_numbers_and_take_an_old_report():
    new = _cmp(24, 3, "n", [True, True], 0.4)
    for mode in ("question_copies", "agent_copies"):
        new["metrics"][mode] = {g: {"n": 33, "top1": 12, "top3": 25, "top10": 27, "mrr": 0.6, "past10": 4,
                                    "missing": 2} for g in (*GROUPS, "all")}
    new["questions"][0]["question_query"] |= {"rank_copies": 1, "doc_copies": "gc"}
    new["questions"][0] |= {"gold_copies": ["gc"], "agent_best_copies": {"rank": 1, "doc": "gc", "query": "q"}}
    old = _cmp(21, 12, "b", [True, False], None)
    md = compare_reports(old, new)
    assert "副本也算" in md and "| 前 3 | 21/33 | 24/33 |" in md and "| 前 3 | — | 25/33 |" in md
    assert "Q08：— → 1" in md
    assert "副本也算" in compare_reports(old, old) and "| 前 3 | — | — |" in compare_reports(old, old)
    report = {**new, "rank_applied": False, "no_gold": []}
    text = format_table(report)
    assert "副本也算" in text and "| core20 | 33 | 12 | 25 | 27 | 0.600 |" in text and "gc" in text
    old_text = format_table({**old, "rank_applied": False, "no_gold": []})
    assert "副本也算" in old_text and "| core20 | — | — |" in old_text


def test_run_reports_gold_ids_missing_from_the_db(tmp_path):
    _search_db(tmp_path / "s.db").close()
    core, noanswer = _question_files(tmp_path)
    gold = _write(tmp_path / "gold.json", {"T01": {"docs": ["dp1", "dmissing"]}, "T02": {"docs": ["dp2"]}})
    runs = _write(tmp_path / "summary.json", [])
    report = run_offline(tmp_path / "s.db", gold=gold, core=core, noanswer=noanswer, runs=runs)
    assert report["missing_gold"] == ["dmissing"]


def test_main_exits_3_on_gold_ids_missing_from_the_db(tmp_path, capsys):
    _search_db(tmp_path / "s.db").close()
    core, noanswer = _question_files(tmp_path)
    gold = _write(tmp_path / "gold.json", {"T01": {"docs": ["dmissing"]}})
    runs = _write(tmp_path / "summary.json", [])
    out = tmp_path / "report.json"
    argv = ["run", "--db", str(tmp_path / "s.db"), "--gold", str(gold), "--core", str(core), "--noanswer",
            str(noanswer), "--runs", str(runs), "--out", str(out)]
    assert main(argv) == 3 and not out.exists() and "dmissing" in capsys.readouterr().err
    assert main([*argv, "--allow-missing-gold"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["missing_gold"] == ["dmissing"]


def test_main_run_hybrid_names_hybrid_search_and_its_config(tmp_path, monkeypatch):
    _no_models(monkeypatch)
    _search_db(tmp_path / "s.db").close()
    core, noanswer = _question_files(tmp_path)
    gold = _write(tmp_path / "gold.json", {"T01": {"docs": ["dp1"]}, "T02": {"docs": ["dp2"]}})
    runs = _write(tmp_path / "summary.json", [])
    out = tmp_path / "report.json"
    assert main(["run", "--search", "hybrid", "--hybrid", '{"dense": false, "rerank": false}', "--db",
                 str(tmp_path / "s.db"), "--gold", str(gold), "--core", str(core), "--noanswer", str(noanswer),
                 "--runs", str(runs), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["search"]["function"] == "nknu.retrieval.hybrid.hybrid_search"
    assert report["search"]["config"]["dense"] is False and report["deep"] == POOL_DOCS + 2 * 300
    # the unit weights are 0 by default since 11-08 (addendum M5): no scope rerun, so no scope_effect
    assert "scope_effect" not in report and report["metrics"]["question"]["core20"]["top1"] == 1


def test_main_rejects_hybrid_settings_without_hybrid_search(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as stop:
        main(["run", "--hybrid", '{"rerank": false}', "--out", str(tmp_path / "r.json")])
    assert stop.value.code == 2


def test_main_gold_always_names_its_out_file(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as stop:  # its old default overwrote research/eval_2026-11-01/gold_docs.json
        main(["gold", "--db", str(tmp_path / "none.db")])
    assert stop.value.code == 2


def test_main_compare_writes_the_markdown(tmp_path):
    base = _write(tmp_path / "a.json", _cmp(21, 12, "b", [True, False], None))
    new = _write(tmp_path / "b.json", _cmp(24, 3, "n", [True, True], 0.4))
    out = tmp_path / "report.md"
    assert main(["compare", "--base", str(base), "--new", str(new), "--out", str(out)]) == 0
    assert "| 前 3 | 21/33 | 24/33 |" in out.read_text(encoding="utf-8")


def test_main_refuses_an_out_file_in_the_skeleton_folder(tmp_path, capsys):
    import pytest

    missing = tmp_path / "missing"  # nothing to read: without the guard, each command fails before it could write
    assert not missing.exists()
    skeleton = DB.parent  # research/skeleton_2026-11-01: the skeleton's database and its 11-01 results
    for out in (skeleton / "checks.md", skeleton / "gold-by-test.json", skeleton / "sub" / ".." / "run-by-test.json"):
        before = out.stat().st_mtime_ns if out.exists() else None
        for argv in (["gold", "--db", str(missing / "aca.db")],
                     ["run", "--db", str(missing / "aca.db"), "--gold", str(missing / "gold.json")],
                     ["compare", "--base", str(missing / "a.json"), "--new", str(missing / "b.json")],
                     ["gold-diff", "--gold", str(missing / "gold.json"), "--db", str(missing / "aca.db")]):
            with pytest.raises(SystemExit) as stop:
                main([*argv, "--out", str(out)])
            assert stop.value.code == 2, argv
        assert (out.stat().st_mtime_ns if out.exists() else None) == before, out
    assert "research/skeleton_2026-11-01" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(main())
