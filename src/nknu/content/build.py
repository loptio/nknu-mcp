"""Build the content database (plan 2026-11-01, Task E; plan 2026-11-02 T7a): a corpus (the one nknu.content.assemble
writes from the to-text run, or the 10-29 corpus) and one site's export become documents, typed dates, terms,
cohorts, sections, chunks (with their table headers) and the FTS index in one SQLite file. The inputs are only read;
the file is written next to its final name and moved over it whole, so a server reading the old one never
sees half a build, and a failed build leaves the old one. No LLM, no network.

References (addendum 2026-11-02 B): doc_refs lists every page document linking an attachment, with the zone (content,
attachments, unknown, nav) and what that page calls it; an attachment's parent and title come from them
(documents.py). Format siblings assemble folded into one record are listed as its copies, with their formats.

Dates (owner decision 11-01, changed later that day): a notice has the date its page shows (公告頁日期欄) and the
periods its body writes (正文期間); an attachment only inherits the published date of the notice it hangs on (所屬公告)
and has the server's Last-Modified when the crawl kept one (伺服器檔案時間); nothing is read from an attachment's own
text, nor since 11-03 from its file name. An event has its calendar days (行事曆). Plan 2026-11-08 T3: a notice no date
field dates (another platform's detail page) and a page have the date the export read on the page (網頁上寫的日期, the
manifest's 网页上写的日期, read after a label of putting the page up or changing it: documents._stated), the page's
as its own date only, and an attachment inherits such a notice's date with that source (_facts); the export's site
gives the unit of a record or event that names no site of its own (a corpus assembled before T3).

Terms (period_ay, period_sem): the first one a title names. A calendar event, whose title seldom names one, always has
one: its title's, else its first day's (terms.event_term; fix round 2, 11-01).

Copies (review 11-01): a page or attachment at several addresses with the same title and text (the same file bytes
when it has no text) is kept once; the others are counted as left out (<kind>(副本)) and listed with their id,
address, date and parent in meta under copies:<kept id>, which respond shows as `copies`. An attachment's title here
is its file name (a title from a page may differ between the addresses); the kept one takes the others' references
and keeps their titles findable in its linktext.

Ids (plan 2026-11-08 T1): a document's id is 'd' + its record's id, which assemble derives from the record's identity
(nknu.content.ids; meta id_scheme). The build stops (BuildError 「编号撞了」) when one id stands for two keys or one key
has two ids (a member's and an address's); records of one key are one document (documents.one_per_id, (重复)).
doc_alias keeps every id left out under another document, the copies (copy), the format siblings (format) and the
notices of one site and nid at another id (duplicate, a corpus with ids of its own), each pointing at the document
kept; read resolves them. docs.tier says background (an archive member, a journal, an exam: tier_of) or primary,
which search reads.

Calendar (owner 11-01): an event that starts before documents.CALENDAR_SINCE (2023-01-01, a fixed date that does not
roll) is left out and counted as 行事历(2023年以前); the export keeps it. Fix round 3's staff-schedule filter is gone.

Doc types (spec 「排序 v2」, 11-01): doc_type says journal (學報) or exam (past exam papers), only where the title, the
parent's title or the address says so plainly (doc_type); everything else is ''. Lists and brochures are never typed.
tier_of makes both background in docs.tier, which search ranks after the primary documents (addendum M2; plan
2026-11-08 T1); search reads doc_type only in a database built before docs.tier (search.background_ids).

Vectors (plan 2026-11-02 T7b): with embed, every chunk's bge-m3 vector goes into chunk_vecs of the .tmp file before it
is moved into place (nknu.content.embed), so a failed or unavailable model leaves the old database; the model loads
before anything is written. A vector cache file (vec_cache) makes a rebuild encode only passages that changed.

No series (owner 11-03, 「可以，这样砍」; plan addendum L): editions of one thing are not grouped, there is no
correction table and no document gets a vector of its own; each document is its own search result, ranked by score.

Units (addendum 2026-11-02 F, T7d): with a site list (the command line always passes one), the unit tree that
nknu.content.units builds from it and config/units.yaml goes into units and unit_aliases (each unit's name, then its
aliases), and every document gets doc_scope rows: its publisher (the section of its unit that its 組別 names, else
its unit, the site table's since plan 2026-11-08 T3: nknu.content.sites; the section's campus is not the document's,
I2), applies_to (the publisher's reach, narrowed by what the title and the text's first SCOPE_HEADER characters name;
its first SCOPE_BODY characters only tell a list the header cuts off, I3; one row per unit x campus x level, NULL
where none is named, the cohort on each) and mentions.
build_report.json and meta scope_json count them. Without a site list the tables stay empty, and search treats every
document as neutral.

Many sites (plan 2026-11-08 T4; spec 2026-11-08 §二 5): the build reads any number of inputs, each a corpus and the
export it was assembled from (--input CORPUS::EXPORT, repeatable; --corpus/--export is the one-pair form). Per input
it reads the export's info, calendar, notice header fields and files (each record in its own export); one server-dates
map, keyed by address, serves them all. Records of one id from several inputs are one document
(documents.one_per_id: the newest 抓取时间, with what every record says) and events of one id are one
(documents.one_event_per_id); a corpus with ids of its own builds alone (BuildError). Each export is a row of the
sites table (its name, unit, 资料截至, missing pages and files, documents, folder; two exports of one site are one row,
the newer one's, with a warning), and docs.site says which export a document came from, so each result shows its own
site's 资料截至 (respond). meta keeps the first input in the keys of one input (corpus, export_dir, site, data_as_of
...; readers before T4 read them: gold's default corpus, check_realdata) and lists every input in inputs_json.

The unit patch (owner 11-08 04:08, plan 2026-11-08 T4): 「请把文件的单位名、组别（以及单位简称）加进索引和 passage，免得
搜『某系 + 某事』时搜不到对应系所的网页」. unit_words gives each document its unit's name and short names (the unit
tree's aliases), a section's office before it (學生事務處（學務處） 生活輔導組（生輔組）: T4 review), and its 組別 (with
the other names of the section it names: build.publisher_of); they go into every chunk's title_tokens, and
docs.unit_line (單位：<unit>（<short names>） <組別>) is the second line of every passage (nknu.content.embed.passage,
PASSAGE_VERSION 2), so the vector cache misses once for every chunk (BuildReport.passage). The school's own words are
left out: it is every unit's root (unit_words). The report lists the site table's units the tree gives no short name
(scope.site_units_no_alias). --no-unit-words leaves the whole patch out (meta unit_words "off"), passages as version
1's: built twice from one set of inputs, the two databases measure the patch alone (T4 review; Review Focus 5).

    python -m nknu.content.build [--corpus research/pipeline_2026-11-02/corpus.json] [--export …] [--out …]
        [--site-list research/site_probe_2026-10-23/data.json] [--units config/units.yaml] [--no-unit-words]
    python -m nknu.content.build --input <corpus.json>::<export folder> [--input …] [--out …] …
    uv run --extra pipeline python -m nknu.content.build --embed [--vec-cache <out folder>/vec_cache.sqlite]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from nknu.content import ids, sites
from nknu.content.chunks import Chunk, make_chunks, split_sections
from nknu.content.corpus import Event, Record, export_info, load_calendar, load_corpus, read_metas
from nknu.content.dates import (
    FIELD_SOURCE,
    INHERITED_SOURCE,
    PAGE_SOURCE,
    DateFact,
    extract_periods,
    identity_date,
    load_server_dates,
)
from nknu.content.documents import LINK_SEP, Document, build_documents, one_event_per_id, one_per_id
from nknu.content.embed import PAGE_SIZE, PASSAGE_VERSION, EmbedStats, VecCache, embed_chunks
from nknu.content.schema import ALIAS_WHYS, BACKGROUND, EXAM, JOURNAL, PRIMARY, create_schema
from nknu.content.terms import Term, canonical_tokens, event_term, find_cohorts, find_terms, term_tokens
from nknu.content.units import CAMPUSES, LEVELS, Unit, ancestors, doc_scope, load_units, reach, resolve
from nknu.core import models
from nknu.core.textnorm import norm_text, tokenize
from nknu.core.timeutil import TAIPEI, today_tpe
from nknu.retrieval.aliases import load_aliases
from nknu.settings import REPO_ROOT

SKELETON = REPO_ROOT / "research" / "skeleton_2026-11-01"  # the skeleton's build: never written again
PIPELINE = REPO_ROOT / "research" / "pipeline_2026-11-02"
CORPUS = REPO_ROOT / "research" / "content_recon_2026-10-29" / "scratchpad_copy" / "content" / "aca_work" / "corpus.json"
CORPUS_NEW = PIPELINE / "corpus.json"  # nknu.content.assemble's output (CORPUS, the 10-29 text, is its fallback)
EXPORT = REPO_ROOT / "archive-export" / "aca-20261102"
OUT = PIPELINE / "aca.db"
SERVER_DATES = SKELETON / "server_dates.json"
ALIASES = REPO_ROOT / "config" / "aliases.yaml"
VEC_CACHE = "vec_cache.sqlite"  # the CLI's vector cache with --embed, beside the database unless --vec-cache
COHORT_ZONE = 300  # a cohort the title does not name is looked for in the text's first characters
EARLIEST, AHEAD = date(1990, 1, 1), timedelta(days=400)
EVENT_SOURCE = "行事曆"
NO_DATE = "none"
BATCH = 5_000  # FTS rows written at a time
COPY = "(副本)"  # excluded-count suffix: the same page or file at another address, kept once (review 11-01)
COPY_KINDS = ("page", "attachment")
COPIES_KEY = "copies:"  # meta key prefix: the kept document's id -> JSON list of its copies (url, date, parent;
#                          format for a format sibling)
SOURCES = ("totext", "fallback-1029", "none", "corpus")  # BuildReport.text_sources, in the order the CLI prints
# the unit stage (addendum 2026-11-02 F, T7d)
SITE_LIST = REPO_ROOT / "research" / "site_probe_2026-10-23" / "data.json"  # the site probe's list: the units' names,
#                                                                             aliases, parents and kinds (read only)
UNITS = REPO_ROOT / "config" / "units.yaml"  # what that list lacks: campus, short names (units.load_units)
SCOPE_HEADER = 150  # doc_scope's header: the text's first characters (an attachment's title block, respond's 150)
SCOPE_BODY = 3_000  # the text doc_scope reads only to tell a list the header cuts off (main session, addendum I3)
RELATIONS = ("applies_to", "publisher", "mentions")  # doc_scope.relation, in the order a document's rows go in
SCOPE_KEY = "scope_json"  # meta: BuildReport.scope as JSON
SCOPE_SKIPPED = {"skipped": "没给单位名单（--site-list）：没有单位树，也没有每份文件的单位范围；搜索不按单位加减分"}
# doc_type patterns, on norm_text (simplified, lowercase); the address is its percent-decoded path
_PAPER_DIRS = ("/nknupaper/",)  # the site's 學報 folder: issue contents, abstracts, papers
_ISSUE = re.compile(r"\d+\s*期\s*学报")  # 55期學報總檔, 上網-52期學報-目錄
_JOURNAL_PAGE = re.compile(r"学报(?![到考名])")  # 高雄師大學報; not 入學報到 / 入學報考 / 入學報名
_WHOLE_ISSUE = re.compile(r"总档|全上传")  # 1111228-53期-總檔, 51全上傳: a whole issue, on the 學報 page only
_ABOUT_JOURNAL = re.compile(r"征稿|投稿|撰稿|审查|格式|须知|启事|名单")  # forms and lists for authors: never papers
_EXAM = re.compile(r"考古题|历届[考试]题")  # 歷屆考題 folder; 歷屆簡章 is brochures, a bare 試題 can be a form
# an archive member's doc_id in a corpus assembled before 11-08 (a<序号>m<k>): still background (tier_of) 【Claude】
_OLD_MEMBER = re.compile(r"da\d+m\d+")
INPUT_SEP = "::"  # --input CORPUS::EXPORT (plan 2026-11-08 T4)
UNIT_LABEL = "單位："  # the unit line's label (plan 2026-11-08 T4's format); a passage's words, never an index token
OFFICE_KIND, SECTION_KIND = "行政单位", "行政组别"  # the unit tree's kinds of an office and of its sections (office_of)
# BuildReport.passage (plan 2026-11-08 T4: 「Note that in the build report」): what changed in this PASSAGE_VERSION
PASSAGE_NOTE = ("passage 第 2 版（计划 2026-11-08 T4，用户 11-08 04:08 要的单位补丁）：重排读的 passage 第二行多了单位行"
                "「單位：<单位>（<简称>） <组别>」（单位是全校、又没有组别的文件没有这一行），关键词索引也有这些词；"
                "向量默认不放单位行（11-08 实验室电脑量过：放进向量，点名组别的问题会把同组别的文件都拉近），所以向量快取照常能用；"
                "加 --unit-words-in-vectors 才放进向量，那时第 1 版的向量快取对不上，第一次 --embed 每个小段都要重算")
# the same with --no-unit-words (plan 2026-11-08 T4 review: the patch measured alone, Review Focus 5)
PASSAGE_NOTE_OFF = ("这次没加单位词（--no-unit-words，用来单独量单位补丁）：没有单位行，title_tokens 里也没有单位名、组别、简称，"
                    "passage 和第 1 版一样，所以第 1 版的向量快取照样命中；和同样输入、加了单位词的库比，差的就只是单位补丁")


class BuildError(ValueError):
    """The inputs cannot be built as they are; nothing is written, and the command line prints the message (exit code
    2). Plan 2026-11-08 T1: an id that stands for two keys, a key with two ids (check_ids, across every input since
    T4). T4: a corpus paired with another site's export (_read_input: --input's two halves must pair, or its files and
    notice fields would be read in the wrong folder), and a corpus with serial ids (no keys: the 10-29 one, one
    assembled before T1) beside another input (its p1 is not another export's p1)."""


@dataclass
class BuildReport:
    counts: dict  # docs by kind_class (and total), sections, chunks, date facts by kind, cohort rows, doc types
    excluded: dict  # left-out records by kind (documents.build_documents)
    date_sources: dict  # kind_class -> identity-date kind (行事曆 for events, none) -> documents
    no_date: int  # documents shown as 無日期
    no_text: int  # found by title only
    warnings: list[str]
    text_sources: dict = field(default_factory=dict)  # kept docs by Document.source ('corpus' when it names none)
    vectors: dict = field(default_factory=dict)  # asdict(embed.EmbedStats) when the build embedded its chunks, else {}
    # T7d: site_list, units_config (paths and sha256), units, kinds, aliases, docs and rows per relation, narrowed
    # (total, title, header), named (units, campus, levels, cohort), publishers, grp_unplaced, seconds (unit_scope);
    # SCOPE_SKIPPED without a site list
    scope: dict = field(default_factory=dict)
    # plan 2026-11-08 T4: the sites table's rows (site, name, unit, data_as_of, missing_pages, missing_files, docs,
    # export), the inputs in order (corpus, export, site, records, events) and what this passage version changed
    # (version, unit_words: whether the build added them, note)
    sites: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    passage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class UnitWords:
    """A document's unit words (plan 2026-11-08 T4, the owner's unit patch): its unit's name and short names, its 組別
    as written and the other names of the section the 組別 names; for a unit that is a section of an office, the
    office's name and short names first (T4 review). Empty parts are left out."""

    name: str = ""
    aliases: tuple[str, ...] = ()
    group: str = ""
    group_aliases: tuple[str, ...] = ()
    office: str = ""
    office_aliases: tuple[str, ...] = ()

    def _parts(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (self.office, self.office_aliases), (self.name, self.aliases), (self.group, self.group_aliases)

    @property
    def line(self) -> str:
        """單位：[<office>（<its short names>） ]<unit>（<short names joined by 、>） <組別>（<its other names>）, the
        plan's format; '' when empty."""
        parts = [w + (f"（{'、'.join(more)}）" if more else "") for w, more in self._parts() if w]
        return UNIT_LABEL + " ".join(parts) if parts else ""

    @property
    def words(self) -> tuple[str, ...]:
        """Each name once, in the line's order: what title_tokens gets, a word at a time (no bigram across two)."""
        return tuple(dict.fromkeys(w for name, more in self._parts() for w in (name, *more) if w))


@dataclass
class _Row:
    doc: Document
    facts: list[DateFact]
    ident: DateFact | None
    ay: int | None
    sem: int | None
    cohorts: list[tuple[int, bool, str]]  # (學年度, onwards, as written)
    dtype: str = ""  # doc_type
    unit: UnitWords = field(default_factory=UnitWords)  # plan 2026-11-08 T4 (unit_words)


def check_ids(records: list[Record]) -> None:
    """One key per id and one id per key, over the records that carry their key (plan 2026-11-08 T1). Raises
    BuildError naming both keys when two keys give one id (a collision of the 60-bit hash), and naming the key and its
    two ids when one key has a member's id and an address's. Records of one key and one id are one identity
    (documents.one_per_id keeps one)."""
    key_of: dict[str, str] = {}
    id_of: dict[str, str] = {}
    for r in records:
        if not r.key:
            continue
        if key_of.setdefault(r.id, r.key) != r.key:
            raise BuildError(f"编号撞了：{r.id} 是「{key_of[r.id]}」，也是「{r.key}」；不建库（换编号规则或回报）")
        if id_of.setdefault(r.key, r.id) != r.id:
            raise BuildError(f"编号撞了：「{r.key}」有两个编号 {id_of[r.key]} 和 {r.id}（压缩档成员和网址用了同一个身份）；不建库")


def id_scheme(records: list[Record]) -> str:
    """meta id_scheme: ids.ID_SCHEME when every record carries the key its id is derived from, else ids.SERIAL_SCHEME
    (the 10-29 corpus and hand-made ones: ids of their own, not stable across exports)."""
    return ids.ID_SCHEME if records and all(r.key for r in records) else ids.SERIAL_SCHEME


def tier_of(doc_id: str, dtype: str) -> str:
    """docs.tier (plan 2026-11-08 T1; addendum M2 by source): BACKGROUND for an archive member (its record id starts
    with m, ids.record_id; an older corpus's a<序号>m<k>) and for a journal or an exam (doc_type); PRIMARY else."""
    if doc_id.startswith("dm") or _OLD_MEMBER.fullmatch(doc_id) or dtype in (JOURNAL, EXAM):
        return BACKGROUND
    return PRIMARY


def _protected(out: Path, export_dir: Path | Sequence[Path]) -> bool:
    """The exports (one, or every input's: plan 2026-11-08 T4), ./data, archive*/ and the skeleton's folder are never
    written."""
    exports = [export_dir] if isinstance(export_dir, str | Path) else list(export_dir)
    target = Path(out).resolve()
    roots = [*(Path(e).resolve() for e in exports), (REPO_ROOT / "data").resolve(), SKELETON.resolve(),
             *(p.resolve() for p in REPO_ROOT.glob("archive*"))]
    return any(target == r or r in target.parents for r in roots)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _server_dates(path: Path | None, today: date, warnings: list[str]) -> dict[str, date]:
    if path is None or not Path(path).is_file():
        warnings.append(f"没有伺服器檔案時間（{path or '没给 server_dates.json'}）：挂在没日期网页上的附件都没有日期")
        return {}
    return {u: d for u, d in load_server_dates(Path(path)).items() if EARLIEST <= d <= today + AHEAD}


def _facts(d: Document, parent: Document | None, event: Event | None, server: dict[str, date]) -> list[DateFact]:
    """A document's dates. A notice: the date it states (its date field, 公告頁日期欄; else the date the export read on
    its page, 網頁上寫的日期: plan 2026-11-08 T3) and the periods its body writes. An attachment: its notice's date, the
    server's time. An event: its days. A page: the date the export read on it, as its identity date (plan 2026-11-08
    T3); its files never take it.

    An attachment's inherited date has the source 所屬公告 when its notice's date is the notice's date field, and
    keeps 網頁上寫的日期 when the export read it on the notice's page (another platform's detail page), so one step
    down a date read from a page's words is never shown as a notice's own field (plan 2026-11-08 T3 review)."""
    if d.kind_class == "notice":
        own = [DateFact("published", d.added, d.added_raw, d.added_source or FIELD_SOURCE)] if d.added else []
        return own + extract_periods(d.text)
    if d.kind_class == "page" and d.page_date:
        return [DateFact("published", d.page_date, d.page_date_raw, PAGE_SOURCE)]
    if d.kind_class == "attachment":
        out = []
        if parent is not None and parent.kind_class == "notice" and parent.added:
            source = PAGE_SOURCE if parent.added_source == PAGE_SOURCE else INHERITED_SOURCE
            out.append(DateFact("inherited", parent.added, parent.added_raw, source))
        if (day := server.get(d.url.split("#", 1)[0])) is not None:  # an archive member: its archive's time
            out.append(DateFact("server", day, day.isoformat(), "伺服器檔案時間"))
        return out
    if d.kind_class == "event" and event is not None:
        return [DateFact("event_start", event.start, event.start.isoformat(), EVENT_SOURCE),
                DateFact("event_end", event.end_inclusive, event.end_inclusive.isoformat(), EVENT_SOURCE)]
    return []


def _cohort_raw(src: str, ay: int, onwards: bool) -> str:
    """The words naming the cohort as written (whitespace squashed); a template when they cannot be found."""
    m = re.search(rf"(?<!\d){ay}(?!\d)\D{{0,12}}?(?:入學|入学|新生)", unicodedata.normalize("NFKC", src))
    return " ".join(m.group().split()) if m else f"{ay}學年度{'起' if onwards else ''}入學"


def _cohorts(d: Document) -> list[tuple[int, bool, str]]:
    for src in (d.title, d.text[:COHORT_ZONE]):
        if found := find_cohorts(src):
            return [(ay, onwards, _cohort_raw(src, ay, onwards)) for ay, onwards in found]
    return []


def _term(d: Document, event: Event | None) -> Term | None:
    """The first term the title names; a calendar event always has one (terms.event_term: else its first day's)."""
    if d.kind_class == "event" and event is not None:
        return event_term(d.title, event.start)
    terms = find_terms(d.title)
    return terms[0] if terms else None


def doc_type(title: str, parent_title: str, url: str) -> str:
    """JOURNAL, EXAM or '' (spec 「排序 v2」: only what the title, the parent's title or the address says plainly;
    rather too few than one wrong). exam: 考古題 / 歷屆考題 / 歷屆試題 in any of the three. journal: the site's 學報
    folder; a title naming an issue (55期學報); or a 總檔 / 全上傳 file whose parent is the 學報 page. A title about
    the journal's forms and lists (徵稿, 撰稿須知, 審查, 委員名單 ...) is never a paper."""
    t, p = norm_text(title), norm_text(parent_title)
    path = norm_text(unquote(urlsplit(url).path))
    if any(_EXAM.search(s) for s in (t, p, path)):
        return EXAM
    if any(d in path for d in _PAPER_DIRS):
        return JOURNAL
    if not _ABOUT_JOURNAL.search(t) and (_ISSUE.search(t) or (_JOURNAL_PAGE.search(p) and _WHOLE_ISSUE.search(t))):
        return JOURNAL
    return ""


def _rows(docs: list[Document], events: dict[str, Event], server: dict[str, date]) -> list[_Row]:
    by_id = {d.doc_id: d for d in docs}
    out = []
    for d in docs:
        event = events.get(d.doc_id)
        facts = _facts(d, by_id.get(d.parent_id or ""), event, server)
        term = _term(d, event)
        out.append(_Row(d, facts, identity_date(facts), term.ay if term else None, term.sem if term else None,
                        _cohorts(d), doc_type(d.title, d.parent_title, d.url)))
    return out


def _file_digest(export_dir: Path, records: list[Record]) -> Callable[[str], str | None]:
    """doc_id -> sha256 of the export file the corpus record came from (read-only), None when it is missing: in the
    record's own export (Record.export, plan 2026-11-08 T4), else in export_dir."""
    files = {"d" + r.id: (Path(r.export or export_dir).resolve(), r.file) for r in records if r.file}

    def digest(doc_id: str) -> str | None:
        root, file = files.get(doc_id, (Path(export_dir).resolve(), ""))
        path = (root / file).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return digest


def _copy_groups(rows: list[_Row], digest: Callable[[str], str | None]) -> list[list[_Row]]:
    """Pages or attachments that are one document at several addresses: same kind and title (an attachment's file
    name), and the same text; when there is no text, the same file bytes (looked at only for documents that share a
    kind and a title). Titles that differ never merge, so every title stays findable."""
    by_text: dict[tuple, list[_Row]] = {}
    blank: dict[tuple, list[_Row]] = {}
    for r in rows:
        d = r.doc
        if d.kind_class not in COPY_KINDS:
            continue
        name = d.file_title or d.title
        if d.text.strip():
            by_text.setdefault((d.kind_class, name, hashlib.sha256(d.text.encode()).hexdigest()), []).append(r)
        else:
            blank.setdefault((d.kind_class, name), []).append(r)
    groups = [g for g in by_text.values() if len(g) > 1]
    for same_title in blank.values():
        by_file: dict[str, list[_Row]] = {}
        for r in same_title if len(same_title) > 1 else ():
            if (h := digest(r.doc.doc_id)) is not None:
                by_file.setdefault(h, []).append(r)
        groups += [g for g in by_file.values() if len(g) > 1]
    return groups


def _inherited(r: _Row) -> date | None:
    return next((f.date for f in r.facts if f.kind == "inherited"), None)


def _take_over(keep: Document, other: Document) -> None:
    """keep stands for other from now on: other's references join keep's, and other's title stays findable in keep's
    linktext."""
    known = {ref for ref, _, _ in keep.refs}
    keep.refs += tuple(ref for ref in other.refs if ref[0] not in known and ref[0] != keep.doc_id)
    if other.title != keep.title and other.title not in keep.linktext.split(LINK_SEP):
        keep.linktext = LINK_SEP.join(p for p in (keep.linktext, other.title) if p)


def _merge_copies(rows: list[_Row], digest: Callable[[str], str | None],
                  excluded: dict[str, int]) -> tuple[list[_Row], dict[str, list[_Row]]]:
    """Keep one document of each copy group (review 11-01: copies took half the places in the top ten): the one on the
    latest dated notice (the school still posts the file then), else the latest identity date, else the first in
    corpus order. The others are counted in excluded as <kind>(副本) and returned, newest first, under the kept id;
    the kept one takes their references (_take_over); a document hanging on a dropped page hangs on the kept one
    (same title and text), and a reference to a dropped page points at the kept one."""
    order = {r.doc.doc_id: i for i, r in enumerate(rows)}
    kept_of: dict[str, str] = {}
    copies: dict[str, list[_Row]] = {}
    for group in _copy_groups(rows, digest):
        keep = max(group, key=lambda r: (_inherited(r) is not None, _inherited(r) or date.min,
                                         r.ident.date if r.ident else date.min, -order[r.doc.doc_id]))
        others = sorted((r for r in group if r is not keep),
                        key=lambda r: (r.ident is None, -(r.ident.date.toordinal() if r.ident else 0),
                                       order[r.doc.doc_id]))
        copies[keep.doc.doc_id] = others
        for r in others:
            kept_of[r.doc.doc_id] = keep.doc.doc_id
            excluded[r.doc.kind_raw + COPY] = excluded.get(r.doc.kind_raw + COPY, 0) + 1
            _take_over(keep.doc, r.doc)
    kept = [r for r in rows if r.doc.doc_id not in kept_of]
    for r in [*kept, *(c for cs in copies.values() for c in cs)]:
        r.doc.parent_id = kept_of.get(r.doc.parent_id or "", r.doc.parent_id)
    present = {r.doc.doc_id for r in kept}
    for r in kept:  # references to a dropped page go to the page kept for it; the best zone wins, as in documents
        refs: dict[str, tuple[str, str]] = {}
        for ref, zone, context in r.doc.refs:
            ref = kept_of.get(ref, ref)
            if ref in present and ref != r.doc.doc_id and ref not in refs:
                refs[ref] = (zone, context)
        r.doc.refs = tuple((ref, zone, context) for ref, (zone, context) in refs.items())
    return kept, copies


def _copies_meta(rows: list[_Row], copies: dict[str, list[_Row]]) -> dict[str, str]:
    """copies:<kept id> for every kept document with copies: the same document at other addresses (newest first),
    then the format siblings folded into it or into one of those (url and format; their page is the kept one's). Each
    has its own id (plan 2026-11-08 T1: doc_alias reads it back to the kept one)."""
    def entry(r: _Row) -> dict:
        out = {"id": r.doc.doc_id, "url": r.doc.url, "date": r.ident.date.isoformat() if r.ident else None,
               "date_kind": r.ident.kind if r.ident else None, "parent_id": r.doc.parent_id,
               "parent_title": r.doc.parent_title}
        return {k: v for k, v in out.items() if v}

    def formats(keep: Document, d: Document) -> list[dict]:
        return [{k: v for k, v in {"id": _format_id(f), "url": f.get("url"), "format": f.get("format"),
                                   "parent_id": keep.parent_id, "parent_title": keep.parent_title}.items() if v}
                for f in d.formats]

    out = {}
    for r in rows:
        others = copies.get(r.doc.doc_id, [])
        listed = [entry(c) for c in others] + formats(r.doc, r.doc) + [f for c in others for f in formats(r.doc, c.doc)]
        if listed:
            out[COPIES_KEY + r.doc.doc_id] = json.dumps(listed, ensure_ascii=False)
    return out


def _format_id(f: dict) -> str | None:
    """The doc_id a format sibling would have had (its record id from assemble), None when the corpus names none."""
    return "d" + str(f["id"]) if f.get("id") else None


def _aliases(rows: list[_Row], copies: dict[str, list[_Row]]) -> list[tuple[str, str, str]]:
    """doc_alias rows (alias, doc_id, why), plan 2026-11-08 T1: the copies _merge_copies left out (copy), the format
    siblings assemble folded into a kept document or into one of its copies (format) and the notices documents left
    out as (重复) of one of them (duplicate), each pointing at the document kept. An id the database holds as a
    document is never an alias, and the first reason found for an id stays."""
    present = {r.doc.doc_id for r in rows}
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        kept = r.doc.doc_id
        others = copies.get(kept, [])
        found = [(c.doc.doc_id, "copy") for c in others]
        for d in (r.doc, *(c.doc for c in others)):
            found += [(i, "format") for f in d.formats if (i := _format_id(f))]
            found += [(dup, "duplicate") for dup in d.duplicates]
        for alias, why in found:
            if alias not in present:
                out.setdefault(alias, (kept, why))
    return [(alias, kept, why) for alias, (kept, why) in out.items()]


def _tokens(text: str, extra: list[str] | None = None) -> str:
    """Bigrams and term tokens of text, then the extra tokens it does not already have."""
    toks = tokenize(text) + canonical_tokens(text)
    return " ".join(toks + [t for t in extra or [] if t not in toks])


def _chunks(d: Document) -> tuple[list, list[Chunk]]:
    """Sections and chunks of whatever text the document has, has_text or not (a mostly scanned PDF's few words still
    find it; review 11-01); a blank text gets one empty chunk, found by its title tokens."""
    sections = split_sections(d.text)
    return sections, make_chunks(d.doc_id, d.text, sections) or [Chunk(d.doc_id, 0, 0, 0)]


def _write_index(conn: sqlite3.Connection, rows: list[_Row]) -> tuple[int, int]:
    """sections, chunks (with the offsets of the table header a chunk starts under) and chunk_fts (rowid = chunk_id);
    title_tokens: title, parent title, link text, the section heading and the unit words (plan 2026-11-08 T4: the
    unit's name and short names, the 組別; unit_words), each on a line of its own; body_tokens: the chunk, after its
    table header when it has one (T7a: a row far down a table still finds the column names). Both carry the term
    tokens (term115s1 ...); an event's title column also carries its own term's, which its title seldom writes (fix
    round 2, 11-01: 「下學期 選課」 finds next term's 選課)."""
    n_sec = chunk_id = 0
    chunk_rows, fts_rows = [], []

    def flush() -> None:
        conn.executemany("INSERT INTO chunks(chunk_id, doc_id, sec_id, start, end, head_start, head_end)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", chunk_rows)
        conn.executemany("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens) VALUES (?, ?, ?)", fts_rows)
        chunk_rows.clear()
        fts_rows.clear()

    for r in rows:
        d = r.doc
        sections, chunks = _chunks(d)
        conn.executemany("INSERT INTO sections(doc_id, sec_id, heading, start, end) VALUES (?, ?, ?, ?, ?)",
                         [(d.doc_id, s.sec_id, s.heading, s.start, s.end) for s in sections])
        n_sec += len(sections)
        heading = {s.sec_id: s.heading for s in sections}
        own_term = term_tokens(Term(r.ay, r.sem)) if d.kind_class == "event" and r.ay is not None else None
        titles: dict[int, str] = {}
        for c in chunks:
            chunk_id += 1
            if c.sec_id not in titles:
                titles[c.sec_id] = _tokens("\n".join((d.title, d.parent_title, d.linktext, heading.get(c.sec_id, ""),
                                                      *r.unit.words)), own_term)
            chunk_rows.append((chunk_id, d.doc_id, c.sec_id, c.start, c.end, c.head_start, c.head_end))
            body = d.text[c.start : c.end]
            if c.head_start is not None:
                body = d.text[c.head_start : c.head_end] + "\n" + body
            fts_rows.append((chunk_id, titles[c.sec_id], _tokens(body)))
        if len(fts_rows) >= BATCH:
            flush()
    flush()
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES ('optimize')")
    return n_sec, chunk_id


@dataclass
class _UnitScope:
    """The unit stage's output (addendum 2026-11-02 F, T7d): the tree's rows, every document's doc_scope rows, the
    report."""

    units: list[tuple[str, str, str, str | None, str | None]]  # (unit_id, name, kind, parent_id, campus), school first
    aliases: list[tuple[str, str]]  # (alias, unit_id): each unit's name, then its aliases
    rows: list[tuple[str, str, str, str | None, str | None, int | None]]  # (doc_id, relation, unit_id, campus, level,
    #                                                                        cohort_from)
    report: dict


def publisher_of(unit: str, grp: str, units: Mapping[str, Unit]) -> str | None:
    """The unit that published a document (T7d): the section its 組別 names when that section is its unit or a part of
    it (教務處's 和平教務組, 招生企劃組 ...), else its unit (教務處); None when its unit is not in the tree."""
    site = resolve(unit, units)
    section = resolve(grp, units) if grp else None
    if site is not None and section is not None and site in ancestors(units, section):
        return section
    return site


def office_of(unit: str | None, units: Mapping[str, Unit]) -> str | None:
    """The office a unit is a section of: the parent of a 行政组别 when that parent is a 行政单位 (生活輔導組's
    學生事務處, 檢定與實習組's 師資培育與就業輔導處); None for any other unit and for None (plan 2026-11-08 T4 review)."""
    if unit is None or units[unit].kind != SECTION_KIND:
        return None
    parent = units[unit].parent_id
    return parent if parent is not None and parent in units and units[parent].kind == OFFICE_KIND else None


def _squashed(s: str) -> str:
    return "".join(norm_text(s).split())


def _not_prefixed(names: Sequence[str], base: Sequence[str], prefixes: Sequence[str]) -> tuple[str, ...]:
    """names without those that only write one of prefixes before one of base (the site list's 「總務處 文書組」 for
    文書組, 「師就處 檢定與實習組」 for 檢定與實習組): the unit line says both already 【Claude】."""
    said = {_squashed(p) + _squashed(b) for p in prefixes if p for b in base if b}
    return tuple(n for n in names if _squashed(n) not in said)


def unit_words(d: Document, units: Mapping[str, Unit] | None, school: str | None = None) -> UnitWords:
    """The unit words of a document (plan 2026-11-08 T4; the owner, 11-08 04:08: the unit name, the 組別 and the
    unit's short names, so 「某系 + 某事」 finds the department's pages that never name it): its unit (Document.unit,
    the site table's) by its name in the tree with its aliases (units.yaml and the site probe's), as written when the
    tree does not have it or there is no tree; its 組別 as written, with the other names of the section it names when
    that section is a part of the unit (publisher_of: the publisher, 和平教務組's 和平校區教務組), and said once when it
    is the unit's own name or short name (in any spelling the tree resolves); a 組別 naming the school says nothing.
    The school (the tree's root, else sites.table().school) gives no words 【Claude】: it is every unit's root, and
    its name and short name (高師大) on its own documents (the unsure sites, outside hosts) would draw every query that
    names the school to them (4 of the 293 agent queries of 11-01 named 高師大); a 組別 on them stays.

    A unit that is a section of an office (office_of: 生活輔導組 of 學生事務處) comes after the office's name and short
    names, 「單位：學生事務處（學務處） 生活輔導組（生輔組）」 (plan 2026-11-08 T4 review 【Claude】: a section runs its own
    site, so its documents said only 「單位：生活輔導組」 and 「學務處 獎學金」 found nothing by their words, while
    教務處's say 「單位：教務處 和平教務組」); a 組別 naming that office is said once too. A short name that only writes
    the office (or, for a 組別, the unit) before the name is left out (_not_prefixed)."""
    tree = units or {}
    school = sites.table().school if school is None else school
    unit = resolve(d.unit, tree) if tree else None
    name, aliases = (tree[unit].name, tree[unit].aliases) if unit is not None else (" ".join(d.unit.split()), ())
    if (unit is not None and tree[unit].parent_id is None) or name == school:
        name, aliases = "", ()
    office = office_of(unit, tree)
    head: dict = {}
    if office is not None:
        head = {"office": tree[office].name, "office_aliases": tree[office].aliases}
        aliases = _not_prefixed(aliases, [name], [head["office"], *head["office_aliases"]])
    group = " ".join(d.grp.split())
    named = resolve(group, tree) if tree and group else None  # the unit the 組別 names, in any spelling
    # the unit itself, its office, or the school
    said = named is not None and (named in (unit, office) or tree[named].parent_id is None)
    if not group or group in (name, *aliases) or said:
        return UnitWords(name, aliases, **head)
    section = publisher_of(d.unit, group, tree) if tree else None
    more = ()
    if section is not None and section != unit:
        more = tuple(n for n in dict.fromkeys((tree[section].name, *tree[section].aliases)) if n != group)
        more = _not_prefixed(more, [tree[section].name, group],
                             [name, *aliases, head.get("office", ""), *head.get("office_aliases", ())])
    return UnitWords(name, aliases, group, more, **head)


def _set_unit_words(rows: list[_Row], units: Mapping[str, Unit] | None, on: bool) -> None:
    """Each row's unit words (unit_words), or none when on is False (--no-unit-words, plan 2026-11-08 T4 review): no
    unit line and no unit tokens, so every passage is version 1's (the vector cache of a build before T4 serves it) and
    a build of the same inputs with and without them measures the owner's patch alone (Review Focus 5)."""
    school = sites.table().school
    for r in rows:
        r.unit = unit_words(r.doc, units, school) if on else UnitWords()


def site_units_without_alias(units: Mapping[str, Unit]) -> list[str]:
    """The units of the site table (sites.table(), every round-1 site) that the unit tree gives no short name, in the
    table's order, each once, as the table writes them (one the tree does not have too); the school left out, which
    gives no unit words (unit_words). The build report lists them (scope.site_units_no_alias, plan 2026-11-08 T4
    review), so the gap the unit words leave stays visible: config/units.yaml takes a short name with its evidence."""
    out: dict[str, None] = {}
    for row in sites.table().rows.values():
        unit = resolve(row.unit, units)
        if unit is None or (units[unit].parent_id is not None and not units[unit].aliases):
            out.setdefault(row.unit, None)
    return list(out)


def unit_scope(docs: list[Document], units: dict[str, Unit]) -> _UnitScope:
    """Every document's doc_scope rows (addendum 2026-11-02 F; units.doc_scope, T6c): its publisher (publisher_of),
    applies_to (the publisher's reach narrowed by what the title and the text's first SCOPE_HEADER characters name,
    the first SCOPE_BODY characters telling a list the header cuts off, I3; one row per unit x campus x level in the
    tree's order, NULL where none is named, each with the cohort) and mentions. No campus comes from the publisher
    (I2: 和平教務組 and 燕巢教務組 publish school-wide rules). The report counts documents and rows per relation, the
    documents narrowed (applies_to differs from the publisher's reach: by the title alone, or only once the header is
    read), what they name, the publishers, and the 組別 that name no part of their unit (their unit published them)."""
    started = time.monotonic()
    place = {k: i for i, k in enumerate(units)}

    def tree_order(ids: frozenset[str]) -> list[str]:
        return sorted(ids, key=lambda u: (place.get(u, len(place)), u))

    rows: list[tuple] = []
    docs_with, publishers, unplaced, narrowed, named = Counter(), Counter(), Counter(), Counter(), Counter()
    campus, levels = Counter(), Counter()
    for d in docs:
        pub = publisher_of(d.unit, d.grp, units)
        if d.grp and pub is not None and pub != resolve(d.grp, units):
            unplaced[d.grp] += 1
        applies, mentions = doc_scope(d.title, d.text[:SCOPE_HEADER], pub, units, body=d.text[:SCOPE_BODY])
        own = [(d.doc_id, "applies_to", u, c, lv, applies.cohort_from) for u in tree_order(applies.units)
               for c in sorted(applies.campus, key=CAMPUSES.index) or [None]
               for lv in sorted(applies.levels, key=LEVELS.index) or [None]]
        if pub is not None:
            own.append((d.doc_id, "publisher", pub, None, None, None))
            publishers[pub] += 1
        own += [(d.doc_id, "mentions", u, None, None, None) for u in tree_order(mentions)]
        rows += own
        docs_with.update({r[1] for r in own})
        base = reach(pub, units)
        if applies != base:
            narrowed["title" if doc_scope(d.title, "", pub, units)[0] != base else "header"] += 1
            named["units"] += int(applies.units != base.units)
            named["cohort"] += int(applies.cohort_from is not None)
            campus.update(applies.campus)
            levels.update(applies.levels)
    per_rel = Counter(r[1] for r in rows)
    report = {"units": len(units), "kinds": dict(Counter(u.kind for u in units.values())),
              "aliases": sum(len(u.aliases) for u in units.values()),
              "docs": {rel: docs_with[rel] for rel in RELATIONS}, "rows": {rel: per_rel[rel] for rel in RELATIONS},
              "narrowed": {"total": narrowed["title"] + narrowed["header"], "title": narrowed["title"],
                           "header": narrowed["header"]},
              "named": {"units": named["units"], "campus": {c: campus[c] for c in CAMPUSES if campus[c]},
                        "levels": {lv: levels[lv] for lv in LEVELS if levels[lv]}, "cohort": named["cohort"]},
              "publishers": dict(publishers.most_common()), "grp_unplaced": dict(unplaced.most_common()),
              "seconds": round(time.monotonic() - started, 2)}
    return _UnitScope([(u.unit_id, u.name, u.kind, u.parent_id, u.campus) for u in units.values()],
                      [(a, u.unit_id) for u in units.values() for a in (u.name, *u.aliases)], rows, report)


def _scope_warnings(report: dict, total: int) -> list[str]:
    """What the unit stage could not place, for the build's warnings (nothing goes missing silently)."""
    out = []
    if unplaced := report.get("grp_unplaced"):
        out.append(f"{sum(unplaced.values())} 份文件的組別不是所属单位的组、按所属单位算发布者："
                   + "、".join(f"{g} {n}" for g, n in unplaced.items()))
    if "docs" in report and (missing := total - report["docs"]["publisher"]):
        out.append(f"{missing} 份文件的所属单位不在单位树上：没有发布者，适用范围按全校算")
    return out


SITE_COLUMNS = ("site", "name", "unit", "data_as_of", "missing_pages", "missing_files", "docs", "export")


def _write(path: Path, rows: list[_Row], meta: dict[str, str], counts: dict, scope: _UnitScope | None = None,
           aliases: list[tuple[str, str, str]] | None = None, site_rows: list[dict] | None = None) -> None:
    """The database at path: schema, docs (with their tier, site and unit line), doc_alias (aliases: _aliases' rows),
    doc_refs, dates, applies_to, the unit tables when scope is given, sites (site_rows: _site_rows'), the index,
    meta."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA page_size={PAGE_SIZE}")  # before the first table: room for chunk_vecs' 2 KB rows (T7b)
        conn.execute("PRAGMA journal_mode=OFF")  # a scratch file until it is moved into place
        conn.execute("PRAGMA synchronous=OFF")
        create_schema(conn)
        conn.executemany(
            "INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, grp, parent_id, parent_title, linktext,"
            " has_text, text, chars, period_ay, period_sem, ident_date, ident_kind, doc_type, text_source, tier, site,"
            " unit_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r.doc.doc_id, r.doc.kind_class, r.doc.kind_raw, r.doc.title, r.doc.url, r.doc.unit, r.doc.grp,
              r.doc.parent_id, r.doc.parent_title, r.doc.linktext, int(r.doc.has_text), r.doc.text, len(r.doc.text),
              r.ay, r.sem, r.ident.date.isoformat() if r.ident else None, r.ident.kind if r.ident else None,
              r.dtype, r.doc.source, tier_of(r.doc.doc_id, r.dtype), r.doc.site, r.unit.line) for r in rows])
        conn.executemany("INSERT INTO doc_alias(alias, doc_id, why) VALUES (?, ?, ?)", aliases or [])
        conn.executemany("INSERT INTO doc_refs(doc_id, ref_id, zone, context) VALUES (?, ?, ?, ?)",
                         [(r.doc.doc_id, *ref) for r in rows for ref in r.doc.refs])
        conn.executemany("INSERT INTO dates(doc_id, kind, date, raw, source) VALUES (?, ?, ?, ?, ?)",
                         [(r.doc.doc_id, f.kind, f.date.isoformat(), f.raw, f.source) for r in rows for f in r.facts])
        conn.executemany(
            "INSERT INTO applies_to(doc_id, unit, cohort_from, cohort_exact, raw) VALUES (?, ?, ?, ?, ?)",
            [(r.doc.doc_id, r.doc.unit, ay if onwards else None, None if onwards else ay, raw)
             for r in rows for ay, onwards, raw in r.cohorts]
            + [(r.doc.doc_id, r.doc.unit, None, None, "") for r in rows if not r.cohorts])
        if scope is not None:
            conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)",
                             scope.units)
            conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)", scope.aliases)
            conn.executemany("INSERT INTO doc_scope(doc_id, relation, unit_id, campus, level, cohort_from)"
                             " VALUES (?, ?, ?, ?, ?, ?)", scope.rows)
        conn.executemany(f"INSERT INTO sites({', '.join(SITE_COLUMNS)}) VALUES ({', '.join('?' * len(SITE_COLUMNS))})",
                         [tuple(row[c] for c in SITE_COLUMNS) for row in site_rows or []])
        counts["sections"], counts["chunks"] = _write_index(conn, rows)
        meta["counts_json"] = json.dumps(counts, ensure_ascii=False)
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    finally:
        conn.close()


def _embed(path: Path, embedder, vec_cache: Path | None, unit: bool = False) -> EmbedStats:
    """Every chunk's vector into the database at path (the build's .tmp file), through the cache file if given."""
    cache = VecCache(vec_cache) if vec_cache is not None else None
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA synchronous=OFF")  # a scratch file until it is moved into place
        return embed_chunks(conn, embedder, cache=cache, unit=unit)
    finally:
        conn.close()
        if cache is not None:
            cache.close()


@dataclass(eq=False)
class _Input:
    """One export the build reads (plan 2026-11-08 T4): the corpus assembled from it, its folder and what is read of
    it: its info (corpus.export_info), its records (each with its export, and the export's site when it names none)
    and its calendar's events (each with its site)."""

    corpus: Path
    export: Path
    info: dict
    records: list[Record]
    events: list[Event]


def _read_input(n: int, corpus: Path, export: Path) -> _Input:
    """Input n (1-based, for the messages). A corpus assembled from another site's export stops the build (BuildError
    【Claude】): its files and notice fields would be read in the wrong folder."""
    info = export_info(export)
    records = [dataclasses.replace(r, export=str(export), site=r.site or info["site"]) for r in load_corpus(corpus)]
    if wrong := sorted({r.site for r in records} - {info["site"]}):
        raise BuildError(f"第 {n} 份输入的 corpus（{corpus}）是网站 {'、'.join(wrong)} 的导出拼的，导出资料夹 {export} 却是"
                         f"网站 {info['site'] or '（没写）'} 的；不建库（--input 的两半要配成对）")
    events = [ev if ev.site else dataclasses.replace(ev, site=info["site"]) for ev in load_calendar(export)]
    return _Input(corpus, export, info, records, events)


def _site_rows(inputs: list[_Input], docs: list[Document], warnings: list[str]) -> list[dict]:
    """The sites table (plan 2026-11-08 T4), one row per site in the inputs' order: the export's name, the site
    table's unit, its 资料截至, missing pages and files, the documents whose site it is and its folder. Two exports of
    one site (an old and a new one) are one row: the one with the newer 资料截至 (the first on a tie) gives the date,
    the counts and the folder 【Claude】, and a warning names them all."""
    by_site: dict[str, list[tuple[int, _Input]]] = {}
    for n, inp in enumerate(inputs):
        by_site.setdefault(inp.info["site"], []).append((n, inp))
    per_site = Counter(d.site for d in docs)
    out = []
    for site, numbered in by_site.items():
        best = max(numbered, key=lambda pair: (pair[1].info["data_as_of"], -pair[0]))[1]
        same = [inp for _, inp in numbered]
        if len(same) > 1:
            warnings.append(f"{len(same)} 份导出是同一个网站 {site}：{'、'.join(str(inp.export) for inp in same)}；"
                            f"资料截至和缺漏数用资料截至较新的 {best.export}")
        out.append({"site": site, "name": best.info["name"], "unit": sites.unit_of_site(site),
                    "data_as_of": best.info["data_as_of"], "missing_pages": best.info["missing_pages"],
                    "missing_files": best.info["missing_files"], "docs": per_site[site], "export": str(best.export)})
    return out


def build_db(corpus: Path, export_dir: Path, out: Path, **kw) -> BuildReport:
    """The one-pair form (--corpus/--export): build_sites([(corpus, export_dir)], out, **kw)."""
    return build_sites([(Path(corpus), Path(export_dir))], out, **kw)


def build_sites(inputs: Sequence[tuple[Path, Path]], out: Path, *, server_dates: Path | None = None,
                aliases: Path = ALIASES, today: date | None = None, embed: bool = False, vec_cache: Path | None = None,
                embedder=None, site_list: Path | None = None, units_config: Path = UNITS,
                unit_words: bool = True, unit_in_vectors: bool = False) -> BuildReport:
    """One database from inputs, each (corpus.json, the export folder it was assembled from), in order (plan
    2026-11-08 T4; the module docstring). Read every input first (a broken one stops the build before anything is
    written), then write out + '.tmp' and move it over out. today (default: Asia/Taipei today) bounds the server dates
    kept: 1990-01-01 … today+400 days; server_dates is one map by address for every input.
    embed: also every chunk's vector (T7b), by embedder (default models.load_embedder(), loaded before anything is
    written; ModelUnavailable passes up) through the vec_cache file when given; a failure leaves the old out.
    site_list (the site probe's list; the command line always passes one) and units_config: the unit tree and every
    document's scope (T7d, unit_scope) and unit words (the function unit_words), read first, so a broken config stops
    the build at once; with no site list the unit tables stay empty, the report says so and the unit words are the
    units as written. unit_words=False (--no-unit-words; T4 review): no unit line and no unit tokens anywhere, the
    owner's patch left out to be measured (_set_unit_words; meta unit_words says which). unit_in_vectors
    (--unit-words-in-vectors): the vectors embed the unit line too; off by default (embed_chunks says why), so the
    keyword index and the reranker carry the unit words and the vectors hold the text. BuildError: a corpus with ids
    of its own beside another input, a corpus with another export, an id of two keys or a key of two ids."""
    pairs = [(Path(c), Path(e)) for c, e in inputs]
    if not pairs:
        raise ValueError("至少要一份输入（corpus.json 和它的导出资料夹）")
    out = Path(out)
    vec_cache = Path(vec_cache) if vec_cache is not None else None
    tmp = out.with_name(out.name + ".tmp")
    exports = [e for _, e in pairs]
    for target in (out, vec_cache):
        if target is not None and _protected(target, exports):
            raise ValueError(f"refusing to write {target}: inside an export, ./data, archive*/ or {SKELETON.name}/")
    if vec_cache is not None and vec_cache.resolve() in (out.resolve(), tmp.resolve()):
        raise ValueError(f"refusing to keep the vector cache in the database itself: {vec_cache}")
    warnings: list[str] = []
    units = load_units(Path(site_list), Path(units_config)) if site_list is not None else None
    read = [_read_input(n, c, e) for n, (c, e) in enumerate(pairs, start=1)]
    if len(read) > 1 and (serial := [n for n, inp in enumerate(read, start=1) if not all(r.key for r in inp.records)]):
        raise BuildError(f"第 {'、'.join(map(str, serial))} 份输入的 corpus 是流水号编号（没有 key：10-29 的，或 11-08 "
                         "以前拼的），它的 p1 和别的导出的 p1 是两回事，只能单独建库；先用 python -m nknu.content.assemble 重拼")
    records = [r for inp in read for r in inp.records]
    check_ids(records)  # plan 2026-11-08 T1, across every input (T4): before anything is written
    scheme = id_scheme(records)
    in_inputs: dict[str, set[int]] = {}
    for n, inp in enumerate(read):
        for r in inp.records:
            in_inputs.setdefault(r.id, set()).add(n)
    records, merged = one_per_id(records)
    events, repeated = one_event_per_id([ev for inp in read for ev in inp.events])
    first = read[0]
    docs, excluded = build_documents(records, read_metas(first.export, records), events, site=first.info["site"])
    for kind, n in [*merged.items(), *repeated.items()]:
        excluded[kind] = excluded.get(kind, 0) + n
    groups = load_aliases(aliases)
    server = _server_dates(server_dates, today or today_tpe(), warnings)
    rows, copies = _merge_copies(_rows(docs, {"e" + ev.id: ev for ev in events}, server),
                                 _file_digest(first.export, records), excluded)
    docs = [r.doc for r in rows]
    _set_unit_words(rows, units, unit_words)
    id_aliases = _aliases(rows, copies)  # doc_alias (not config/aliases.yaml, the synonym groups)
    scope = unit_scope(docs, units) if units is not None else None
    if scope is not None:
        scope.report = {"site_list": str(site_list), "site_list_sha256": _sha256(Path(site_list)),
                        "units_config": str(units_config), "units_config_sha256": _sha256(Path(units_config)),
                        **scope.report, "site_units_no_alias": site_units_without_alias(units)}
        warnings += _scope_warnings(scope.report, len(docs))
    site_rows = _site_rows(read, docs, warnings)

    attachments = [d for d in docs if d.kind_class == "attachment"]
    tiers = Counter(tier_of(r.doc.doc_id, r.dtype) for r in rows)
    whys = Counter(why for _, _, why in id_aliases)
    sources: dict[str, Counter] = {}
    for r in rows:
        key = r.ident.kind if r.ident else EVENT_SOURCE if any(f.kind == "event_start" for f in r.facts) else NO_DATE
        sources.setdefault(r.doc.kind_class, Counter())[key] += 1
    counts = {"docs": {**Counter(d.kind_class for d in docs), "total": len(docs)}, "sections": 0, "chunks": 0,
              "dates": dict(Counter(f.kind for r in rows for f in r.facts)),
              "cohort_rows": sum(len(r.cohorts) for r in rows),
              "doc_types": {t: sum(r.dtype == t for r in rows) for t in (JOURNAL, EXAM)},
              "doc_refs": sum(len(d.refs) for d in docs),
              "attachments": {"title_from_page": sum(d.title_from_context for d in attachments),
                              "title_file_name": sum(not d.title_from_context for d in attachments),
                              "parent": sum(d.parent_id is not None for d in attachments),
                              "no_parent": sum(d.parent_id is None for d in attachments)},
              "tiers": {t: tiers[t] for t in (PRIMARY, BACKGROUND) if tiers[t]},  # plan 2026-11-08 T1
              "doc_alias": {w: whys[w] for w in ALIAS_WHYS if whys[w]},
              "shared_ids": sum(len(n) > 1 for n in in_inputs.values())}  # T4: ids more than one input holds
    text_sources = dict(Counter(d.source or "corpus" for d in docs))
    digests = [hashlib.sha256(inp.corpus.read_bytes()).hexdigest() for inp in read]
    meta = {"built_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "corpus": str(first.corpus),
            "corpus_sha256": digests[0], "export_dir": str(first.export),
            "server_dates": str(server_dates) if server else "", "site": first.info["site"],
            "name": first.info["name"], "data_as_of": first.info["data_as_of"],
            "missing_pages": str(first.info["missing_pages"]), "missing_files": str(first.info["missing_files"]),
            "inputs_json": json.dumps([{"corpus": str(inp.corpus), "corpus_sha256": sha, "export": str(inp.export),
                                        "site": inp.info["site"]} for inp, sha in zip(read, digests, strict=True)],
                                      ensure_ascii=False),
            "excluded_json": json.dumps(excluded, ensure_ascii=False),
            "aliases_json": json.dumps(groups, ensure_ascii=False), "id_scheme": scheme,
            "unit_words": "on" if unit_words else "off",  # T4 review: a build with --no-unit-words says so
            "unit_in_vectors": "on" if unit_words and unit_in_vectors else "off",  # 11-08: off unless asked
            "text_pipeline": "totext" if any(r.source == "totext" for r in records) else "",
            SCOPE_KEY: json.dumps(scope.report if scope is not None else SCOPE_SKIPPED, ensure_ascii=False),
            **_copies_meta(rows, copies)}

    if embed and embedder is None:
        embedder = models.load_embedder()  # through the module (the tests' guard); before anything is written
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    vectors = None
    try:
        _write(tmp, rows, meta, counts, scope, id_aliases, site_rows)
        if embed:
            vectors = _embed(tmp, embedder, vec_cache, unit=unit_words and unit_in_vectors)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return BuildReport(counts=counts, excluded=excluded,
                       date_sources={k: dict(v) for k, v in sources.items()},
                       no_date=sum(c[NO_DATE] for c in sources.values()), no_text=sum(not d.has_text for d in docs),
                       warnings=warnings, text_sources=text_sources,
                       vectors=asdict(vectors) if vectors is not None else {},
                       scope=scope.report if scope is not None else dict(SCOPE_SKIPPED), sites=site_rows,
                       inputs=[{"corpus": str(inp.corpus), "export": str(inp.export), "site": inp.info["site"],
                                "records": len(inp.records), "events": len(inp.events)} for inp in read],
                       passage={"version": PASSAGE_VERSION, "unit_words": unit_words,
                                "unit_in_vectors": unit_words and unit_in_vectors,
                                "note": PASSAGE_NOTE if unit_words else PASSAGE_NOTE_OFF})


def _progress_to_stderr() -> Callable[[], None]:
    """embed's progress lines on stderr for one command-line run; returns what undoes it."""
    logger = logging.getLogger("nknu.content.embed")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def undo() -> None:
        logger.removeHandler(handler)
        logger.setLevel(level)

    return undo


def _pairs(args: argparse.Namespace) -> list[tuple[Path, Path]] | str:
    """The inputs the command line names (plan 2026-11-08 T4): every --input CORPUS::EXPORT, else the one pair of
    --corpus and --export (their defaults when neither is given); or what is wrong with them, to print."""
    if args.input and (args.corpus is not None or args.export is not None):
        return "--input 和 --corpus/--export 只能用一种：多份导出都写成 --input CORPUS::EXPORT"
    if not args.input:
        return [(args.corpus or CORPUS_NEW, args.export or EXPORT)]
    pairs = []
    for spec in args.input:
        corpus, sep, export = spec.partition(INPUT_SEP)
        if not (sep and corpus.strip() and export.strip()):
            return f"--input 要写成 CORPUS::EXPORT（corpus.json 和它的导出资料夹，中间用 :: 隔开）：{spec}"
        pairs.append((Path(corpus.strip()), Path(export.strip())))
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.content.build",
                                 description="把拼好的 corpus.json 和它们的导出建成一个资料库（输入只读）")
    ap.add_argument("--input", action="append", default=[], metavar="CORPUS::EXPORT",
                    help="一份导出资料夹和从它拼好的 corpus.json，中间用 :: 隔开；可以给好几次，建成一个资料库"
                         "（计划 2026-11-08 T4）；和 --corpus/--export 二选一")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="只有一份导出时：python -m nknu.content.assemble 写的 corpus.json"
                         "（默认 research/pipeline_2026-11-02/corpus.json）")
    ap.add_argument("--export", type=Path, default=None,
                    help="只有一份导出时：导出资料夹（默认 archive-export/aca-20261102）")
    ap.add_argument("--out", type=Path, default=OUT, help="资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("--server-dates", type=Path, default=None,
                    help="python -m nknu.content.serverdates 写的档，按网址给每份输入用"
                         "（默认 research/skeleton_2026-11-01/server_dates.json，有就用）")
    ap.add_argument("--aliases", type=Path, default=ALIASES, help="别名表（默认 config/aliases.yaml）")
    ap.add_argument("--report", type=Path, default=None, help="建置报告（默认和资料库同一个资料夹的 build_report.json）")
    ap.add_argument("--embed", action="store_true",
                    help="也算每个小段的 bge-m3 向量（要 pipeline 这组套件和本机快取的模型："
                         "uv run --extra pipeline …）")
    ap.add_argument("--vec-cache", type=Path, default=None,
                    help=f"向量快取（默认和资料库同一个资料夹的 {VEC_CACHE}；重建时只算改过的小段；"
                         "只在 --embed 时用）")
    ap.add_argument("--site-list", type=Path, default=SITE_LIST,
                    help="单位名单：网站普查的 data.json（默认 research/site_probe_2026-10-23/data.json，只读）")
    ap.add_argument("--units", type=Path, default=UNITS, help="单位设定：校区、简称（默认 config/units.yaml）")
    ap.add_argument("--unit-words-in-vectors", action="store_true",
                    help="单位行也放进向量（默认只放进关键词索引和重排读的文字：11-08 实验室电脑量过，放进向量会让同组别的文件都靠近点名组别的问题）")
    ap.add_argument("--no-unit-words", action="store_true",
                    help="不加单位词（单位行，和 title_tokens 里的单位名、组别、简称）：passage 和第 1 版一样，"
                         "用来单独量单位补丁（计划 2026-11-08 T4 审查）；平时不要用")
    args = ap.parse_args(argv)
    pairs = _pairs(args)
    if isinstance(pairs, str):
        print(pairs, file=sys.stderr)
        return 2
    needed = [p for corpus, export in pairs for p in ((corpus, "corpus.json"), (export / "manifest.json", "导出资料夹"))]
    for path, what in (*needed, (args.site_list, "单位名单"), (args.units, "单位设定")):
        if not path.is_file():
            print(f"找不到{what}：{path}", file=sys.stderr)
            return 2
    server = args.server_dates if args.server_dates is not None else SERVER_DATES if SERVER_DATES.is_file() else None
    vec_cache = (args.vec_cache or args.out.with_name(VEC_CACHE)) if args.embed else None
    undo = _progress_to_stderr() if args.embed else None
    try:
        report = build_sites(pairs, args.out, server_dates=server, aliases=args.aliases, embed=args.embed,
                             vec_cache=vec_cache, site_list=args.site_list, units_config=args.units,
                             unit_words=not args.no_unit_words, unit_in_vectors=args.unit_words_in_vectors)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except models.ModelUnavailable as exc:
        print(f"算不了向量，资料库没动：{exc}", file=sys.stderr)
        return 2
    finally:
        if undo is not None:
            undo()
    report_path = args.report or args.out.with_name("build_report.json")
    report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=1), encoding="utf-8")
    docs, types = report.counts["docs"], report.counts["doc_types"]
    sources = "、".join(f"{s} {report.text_sources[s]}" for s in SOURCES if report.text_sources.get(s))
    v = report.vectors
    vectors = (f"；向量 {v['chunks']} 小段（快取 {v['cached']}、新算 {v['encoded']}，{v['seconds']:.0f} 秒）"
               if v else "")
    n = report.scope.get("narrowed")
    scoped = (f"；单位范围：缩小 {n['total']} 份（标题 {n['title']}、开头 {n['header']}）、"
              f"提及单位 {report.scope['docs']['mentions']} 份" if n else "")
    bare = report.scope.get("site_units_no_alias")
    scoped += f"；网站的单位没有简称的 {len(bare)} 个（报告 scope.site_units_no_alias）" if bare else ""
    words = "" if report.passage["unit_words"] else "；没加单位词（--no-unit-words）"
    print(f"{args.out}：{docs['total']} 份文件、{report.counts['chunks']} 小段；"
          f"网站 {len(report.sites)} 个（各站资料截至和缺漏见报告 sites）；"
          f"无日期 {report.no_date}、无文字 {report.no_text}；学报 {types[JOURNAL]}、考题 {types[EXAM]}、"
          f"背景资料共 {report.counts['tiers'].get(BACKGROUND, 0)}；"
          f"编号转址 {sum(report.counts['doc_alias'].values())} 笔（副本、其他格式、重复）；"
          f"文字来源 {sources or '（没有）'}{vectors}{scoped}{words}；报告 {report_path}")
    if v and v["encoded"] and not v["cached"]:  # nothing from the cache: the first --embed after a passage change
        if report.passage["unit_words"]:
            print(f"注意：向量快取一个都没中。{report.passage['note']}", file=sys.stderr)
        else:  # the passages are version 1's: a cache of a build before T4 would have served them
            print(f"注意：向量快取一个都没中，可是这次没加单位词、passage 和第 1 版一样，本来应该命中："
                  f"{vec_cache} 是第 1 版建库用的那一份吗？", file=sys.stderr)
    for w in report.warnings:
        print(f"注意：{w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
