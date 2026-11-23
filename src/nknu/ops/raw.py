"""原始紀錄: every record behind a step of the night, a page at a time (owner 11-23: 「用右边出现一个侧边栏显示吧」,
「我看到了有問題，但我看不到細節」, 「剩下的四个步骤也跟上吧」). The design copy (research/design_2026-11-23; its builder
scripts rawview.js and raw_steps.py) baked a sample of each step's rows into the page; here every row is read from the
step's own live source and served a page at a time through GET /api/raw, with the label and facet counts under the
filters in force:

  爬取    the crawler's ledger (frontier.db, read-only through pagecheck.open_ledger): every address of the library's
          sites, round two's open batch apart, in outcome.user_bucket's buckets as pipeline.crawl_buckets counts them
          (待確認 split by its result code); each address's last requests looked up per page
  導出    the manifests of the exports the library was built from (sites.export -> manifest.json 项目)
  轉文字  the build's to-text accounting (totext/accounting.jsonl), a line a file or an archive member
  建庫    the library's documents (docs) and the records merged into them (doc_alias); what the build only counted
          (meta excluded_json) is a count with no rows
  換庫    the last swap's comparison (nknu.ops.changes: new, changed, left with its reason; its lists are capped per
          site, which the drawer says)

An index of a step (compact numpy columns of codes, a lowercase haystack for the search box, and where each row is) is
built in a background thread the first time its drawer opens, kept by its source's identity, made again when the source
changes (the ledger at most every pipeline.EVERY seconds: it changes all night) and dropped when nobody asked for it
for IDLE seconds; the one before is served while the next is made. A page's rows are read back by reference (a ledger
id, an export row kept compressed beside the index, a line's offset, a doc_id), so no list of thousands of rows is
ever baked into a page and no manifest is parsed again for a page. The ledger is read the way the crawler can live
with at 01:00: read-only, in statements of at most CHUNK rows each (no read holds the WAL's checkpoints back for
long), only the library's sites, and its request table (it only grows) from where the last reading left off. The
library's documents are read without their text (docs.site lies after it: a document's site comes from an index on
site when the build wrote one, else is read only when a page needs it, never for the night view's counts).

Labels are stable keys (a bucket, review:<result>, a 缺什么 part, file, nofile:<结果>, a status, doc:<kind>,
alias:<why>, left:<group> as build_step keys it, new, changed, left:<reason>) shown in names.py's words; a preset may
pass a key, the key a pipeline step gives the same row (build_step's left:copy, swap_flow's bare reason) or a word (an
older word is renamed first).
Every string served is masked (pipeline._SECRETS).

tw() is applied to the programs' own words only (结果, 原因, 缺什么, 备注, 类型, 分类, 怎么来的, 日期来源, a reason),
never to a school's text (a title, a link's words): it turned 張岳印 into 張嶽印 in the design copy. Those columns
hold a few dozen words, so each is converted once (the converter is pure Python: once a row was 80% of a build)."""

from __future__ import annotations

import functools
import json
import logging
import math
import posixpath
import re
import sqlite3
import threading
import time
import zlib
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np

from nknu.core.timeutil import TAIPEI
from nknu.ops import pipeline
from nknu.ops.names import rename
from nknu.ops.pipeline import Where, folder_of, library_facts, missing_parts, open_ro, when_of

# GET /api/raw for web.py to wire (port contract 11-23): the handler returns (status, body) for a JSONResponse with
# Cache-Control no-store. changes: the last swap's comparison as /api/tonight serves it (night_changes.snapshot()).
ROUTES_TO_WIRE = [
    {"path": "/api/raw", "method": "GET", "handler": "api",
     "args": "where, request.query_params, changes=night_changes.snapshot()",
     "returns": "(status: int, body: dict) -> JSONResponse(body, status_code=status, headers={'Cache-Control': "
                "'no-store'}); body as page() below, {'error': ...} with 404 for an unknown step"},
]

log = logging.getLogger(__name__)

STEPS = {"crawl": "爬取", "export": "導出", "totext": "轉文字", "build": "建庫", "swap": "換庫"}
PER, PER_MAX = 50, 200  # rows a page holds by default, and at most 【Claude】
IDLE = 1800.0  # an index nobody asked for this long is dropped (the ledger's holds ~20 MB) 【Claude】
RETRY = 60.0  # a source that could not be read is tried again after this long 【Claude】
TOP = 40  # values a facet lists at most; the one picked is always among them 【Claude】
REASON_TOP = 30  # the ledger's reasons listed at most (the design's cut)
CHUNK = 20_000  # ledger rows one statement reads at most: each read of the live ledger stays short 【Claude】
BLOCK = 64  # export rows kept compressed together beside the index: a page opens only its rows' blocks 【Claude】

# the ledger's words for its own fields (the design's rawview.js ST, VIA, KIND): the facets and the rows say them
STATE_WORDS = {"done": "抓到了", "failed": "失敗", "skipped": "照規則不抓", "later": "留到第二輪", "held": "壓著",
               "pending": "還在排"}
VIA_WORDS = {"link": "頁面連結", "old_frontier": "舊帳本", "commoncrawl": "Common Crawl 索引", "drive": "Google 雲端硬碟",
             "redirect": "跳轉", "rejudge": "重新判定", "v1_known": "第一版已知", "scheme_switch": "http/https 互換",
             "seed": "網站清單起點", "click": "按按鈕", "reconcile": "核對補的", "js_redirect": "網頁腳本跳轉"}
KIND_WORDS = {"page": "網頁", "image": "圖片", "file": "檔案", "drive": "Google 雲端硬碟", "embed": "嵌入內容",
              "shortlink": "短網址"}
BUCKET_ORDER = ("review", "failed", "queued", "gone", "login", "school_side", "pending_check", "got", "next_round",
                "rule")
# 只看沒通過的: every bucket but got and what this round is not to fetch, as crawl_step counts a site's lost and its
# whole (RD-04: school_side and pending_check too, so the count is the 通過率 bar's the drawer opened from)
MISS_BUCKETS = tuple(b for b in BUCKET_ORDER if b != "got" and b not in pipeline.NOT_MEANT)


def missed(bucket: str) -> bool:
    return bucket != "got" and bucket not in pipeline.NOT_MEANT
# the library's kinds of documents (KC of raw_steps.py) and their icons in the drawer
KIND_CLASS = {"attachment": "附件", "page": "網頁", "notice": "公告", "event": "行事曆", "link": "連結"}
KIND_ICON = {"attachment": "檔案", "page": "網頁", "notice": "公告", "event": "行事曆", "link": "短網址"}
TYPE_ICON = {"网页": "網頁", "附件": "檔案", "图片": "圖片", "按钮": "按按鈕"}  # an export row's 类型
STATUS_DOT = {"ok": "ok", "empty": "warn", "partial": "warn", "failed": "bad"}
HOW = (("Google API", "Google API"), ("jsfetch", "瀏覽器開"), ("postback", "按按鈕"), ("page", "直接請求"))
OUT = {"crawl": "got", "export": "file", "totext": "ok", "build": "doc:*", "swap": "new"}  # what a step put out
# the file and table each step's rows come from; key: the file's key in the step lineage (nknu.ops.lineage)
SRC = {
    "crawl": {"key": "frontier", "file": "frontier.db", "table": "url 表", "side": "輸入兼輸出"},
    "export": {"key": "manifest", "file": "manifest.json", "table": "項目", "side": "輸出"},
    "totext": {"key": "acc_rows", "file": "accounting.jsonl", "table": "一行一個檔", "side": "輸出",
               "tip": "轉文字的逐檔記帳：每個檔怎麼轉、轉出多少字、為什麼讀不出"},
    "build": {"key": "aca", "file": "aca.db", "table": "docs · doc_alias", "side": "輸出",
              "tip": "建好的新庫：每份文件一行（docs），併掉的記在 doc_alias；其他沒成為文件的只記了數量"},
    "swap": {"key": "aca", "file": "aca.db ↔ aca.prev.db", "table": "逐份比對", "side": "換前換後",
             "tip": "換上的新庫和上一份庫一份一份比：新進、改了、離開（離開的都說得出原因）"},
}
# what a log or a record should never carry, and a page never shows if one does (pipeline._SECRETS: one list)
_SECRETS = getattr(pipeline, "_SECRETS", (
    (re.compile(r"AIza[0-9A-Za-z_\-]{10,}"), "（遮掉的金鑰）"),
    (re.compile(r"(?i)([?&](?:key|api_key|access_token|token)=)[^&\s\"']+"), r"\1（遮掉）"),
    (re.compile(r"(?i)((?:authorization|x-goog-api-key|cookie)\s*[:=]\s*)\S.*"), r"\1（遮掉）")))


@functools.lru_cache(maxsize=4096)
def tw(text: str) -> str:
    """pipeline.tw (our programs' Simplified words as the panels show them), each word converted once: the columns it
    is used on hold a few dozen words over a hundred thousand rows, and the converter is pure Python."""
    return pipeline.tw(text)


@functools.lru_cache(maxsize=4096)
def lacks(said: str) -> tuple[str, ...]:
    """pipeline.missing_parts of an export row's 缺什么, each text read once: a few hundred texts over every row, and
    a part it does not know is converted (tw) each time it is read."""
    return tuple(missing_parts(said))


def mask(text: object) -> object:
    """A string with every secret pattern masked; anything else as it is."""
    if not isinstance(text, str):
        return text
    for pattern, word in _SECRETS:
        text = pattern.sub(word, text)
    return text


def mask_all(value: object) -> object:
    """Every string inside a JSON value masked, its keys too (FD-05: every row, look and view served)."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, (list, tuple)):
        return [mask_all(v) for v in value]
    if isinstance(value, dict):
        return {mask(k) if isinstance(k, str) else k: mask_all(v) for k, v in value.items()}
    return value


# ------------------------------------------------------------------------------------------------ an index


class _Col:
    """A column of values as small integer codes and the values they stand for."""

    def __init__(self) -> None:
        self.codes: list[int] = []
        self.vocab: list[str] = []
        self._ix: dict[str, int] = {}

    def code(self, value: object) -> int:
        value = "" if value is None else str(value)
        i = self._ix.get(value)
        if i is None:
            i = self._ix[value] = len(self.vocab)
            self.vocab.append(value)
        return i

    def add(self, value: object) -> None:
        self.codes.append(self.code(value))

    def done(self) -> tuple[np.ndarray, list[str]]:
        return np.asarray(self.codes, dtype=np.int32), self.vocab


class Table:
    """One step's rows as columns. label: each row's main label; also: more labels a row is found under (an export
    row missing several things); hay: the text the search box looks in; ref: where each row is in its source; info:
    the step's words and numbers (label_words, facets, sites, head, …)."""

    def __init__(self, cols: dict[str, _Col], labels: list[str], also: list[list[str]], hay: list[str],
                 ref: object, miss: list[bool], info: dict) -> None:
        self.n = len(labels)
        self.cols = {k: c.done() for k, c in cols.items()}
        lab = _Col()
        for x in labels:
            lab.add(x)
        rows, codes = list(range(self.n)), list(lab.codes)
        for i, more in enumerate(also):
            for x in more:
                rows.append(i)
                codes.append(lab.code(x))
        self.label_vocab = lab.vocab
        self.label = np.asarray(lab.codes, dtype=np.int32)
        self.pair_rows = np.asarray(rows, dtype=np.int64)
        self.pair_codes = np.asarray(codes, dtype=np.int32)
        parts = [t.lower().encode("utf-8", "replace") for t in hay]
        self.hay = b"\n".join(parts)
        lengths = np.fromiter((len(p) + 1 for p in parts), dtype=np.int64, count=len(parts))
        self.starts = np.concatenate(([0], np.cumsum(lengths)[:-1])) if len(parts) else np.zeros(0, np.int64)
        self.ref = ref
        self.miss = np.asarray(miss, dtype=bool)
        self.info = info

    # the filters, each a mask of rows
    def q_mask(self, q: str) -> np.ndarray:
        out = np.zeros(self.n, dtype=bool)
        needle = q.lower().encode("utf-8", "replace")
        if not needle or not self.n:
            return out if needle else ~out
        pos = self.hay.find(needle)
        while pos >= 0:
            i = int(np.searchsorted(self.starts, pos, "right")) - 1
            out[i] = True
            pos = self.hay.find(needle, int(self.starts[i + 1]) if i + 1 < self.n else len(self.hay))
        return out

    def label_mask(self, keys: set[str]) -> np.ndarray:
        codes = [i for i, k in enumerate(self.label_vocab) if k in keys]
        out = np.zeros(self.n, dtype=bool)
        if codes:
            out[self.pair_rows[np.isin(self.pair_codes, codes)]] = True
        return out

    def col_mask(self, name: str, value: str) -> np.ndarray:
        codes, vocab = self.cols[name]
        try:
            return codes == vocab.index(value)
        except ValueError:
            return np.zeros(self.n, dtype=bool)

    def counts(self, name: str, mask: np.ndarray) -> list[tuple[str, int]]:
        """(value, rows) of a column under a mask, the most first, zeros left out."""
        codes, vocab = self.cols[name]
        got = np.bincount(codes[mask], minlength=len(vocab))
        return sorted(((vocab[i], int(n)) for i, n in enumerate(got) if n), key=lambda vn: -vn[1])

    def label_counts(self, mask: np.ndarray) -> dict[str, int]:
        got = np.bincount(self.pair_codes[mask[self.pair_rows]], minlength=len(self.label_vocab))
        return {self.label_vocab[i]: int(n) for i, n in enumerate(got) if n}


class _Slot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key: object = None
        self.table: object = None  # a Table (a step's index) or what a helper slot keeps (建庫's sites)
        self.built = 0.0
        self.used = 0.0
        self.thread: threading.Thread | None = None
        self.error = ""


_slots: dict[tuple[str, str], _Slot] = {}
_slots_lock = threading.Lock()


def _stamp(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_size, st.st_mtime_ns)


def _index(step: str, place: str, key: object, build: Callable[[object], object], *, every: float = 0.0,
           wait: float | None = None) -> tuple[object, str, str]:
    """(the table, its state, words) of a step's index at place: built in the background when missing or when its
    source's key changed (no sooner than every seconds after the last build), the one before served meanwhile and
    handed to the build (None the first time: the ledger's index reads on from where it left off); indexes nobody
    asked for in IDLE seconds are dropped."""
    now = time.monotonic()
    with _slots_lock:
        for k, s in list(_slots.items()):
            if now - s.used > IDLE and (s.thread is None or not s.thread.is_alive()):
                del _slots[k]
        slot = _slots.setdefault((step, place), _Slot())
    with slot.lock:
        slot.used = now
        retry = not slot.error or now - slot.built >= RETRY
        stale = retry and (slot.table is None or (slot.key != key and now - slot.built >= every))
        running = slot.thread is not None and slot.thread.is_alive()
        if stale and not running:
            prev = slot.table

            def run() -> None:
                try:
                    table = build(prev)
                except Exception as exc:  # the drawer says so; the log has the whole
                    log.exception("原始纪录：读不了 %s 的来源", step)
                    with slot.lock:
                        slot.error = f"{type(exc).__name__}: {exc}"
                        slot.built = time.monotonic()
                    return
                with slot.lock:
                    slot.table, slot.key, slot.built, slot.error = table, key, time.monotonic(), ""

            slot.thread = threading.Thread(target=run, name=f"raw-{step}", daemon=True)
            slot.thread.start()
        thread = slot.thread
    if wait and thread is not None and thread.is_alive():
        thread.join(wait)
    with slot.lock:
        if slot.table is not None:
            return slot.table, "ready", ""
        if slot.error and not (slot.thread and slot.thread.is_alive()):
            return None, "error", slot.error
    return None, "computing", "正在讀取（第一次要幾秒到幾十秒）"


def forget() -> None:
    """Drop every index (tests; a source the panel should read again at once)."""
    with _slots_lock:
        _slots.clear()


# ------------------------------------------------------------------------------------------------ small helpers


def _size(b: object) -> str:
    try:
        b = int(b)
    except (TypeError, ValueError):
        return ""
    return f"{b / 1e6:.1f} MB" if b >= 1e6 else f"{b / 1e3:.0f} KB" if b >= 1e3 else f"{b} B"


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _item(i: object, key: str, word: str, dot: str, main: str, **kw) -> dict:
    return _clean({"id": i, "lk": key, "label": word, "dot": dot, "main": main, **kw})


def _tpe(value: object, fmt: str = "%m-%d %H:%M") -> str:
    at = when_of(value if isinstance(value, (int, float)) else str(value or ""))
    return at.strftime(fmt) if at else ""


def _run_day(value: object) -> str:
    at = when_of(str(value or ""))
    return at.date().isoformat() if at else ""


def _library_sites(where: Where) -> tuple[dict, dict[str, str]]:
    facts = library_facts(where.library)
    return facts, {s["site"]: s.get("name") or s["site"] for s in facts["sites"] if s.get("site")}


def night_key(run_day: str) -> str:
    """The night key the stores used before 11-23 (nightly.night_of: the Taipei day twelve hours before a time) of a
    run day: the day before it (CR-03), so a page naming nights either way can compare."""
    try:
        return (date.fromisoformat(run_day) - timedelta(days=1)).isoformat()
    except ValueError:
        return ""


def _library_day(facts: dict) -> str:
    return _run_day(facts.get("swapped_at") or facts.get("built_at"))


def _file_key(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


# ------------------------------------------------------------------------------------------------ 爬取


def bucket_words() -> dict[str, str]:
    """The crawler's buckets in the panel's words (pipeline.bucket_words, renamed by names.py)."""
    return {k: rename(v) for k, v in pipeline.bucket_words().items()}


def review_word(code: str) -> str:
    """待確認 split by its result: the crawler's own words of the result, renamed (pipeline.crawl_step's split)."""
    from nknu.archive import outcome
    said = tw(str(getattr(outcome, "RESULT_LABEL", {}).get(code) or code)).split("；")[0]
    return rename(f"待確認：{said}")


def how_of(kind: str, reason: str, purposes: str | None) -> str:
    """How an address was fetched (the design's how(r)): Google's API, a browser, a button, a plain request, only a
    check, or no request in the ledger."""
    if kind == "drive" or str(reason or "").startswith("api:"):
        return "Google API"
    got = set((purposes or "").split(",")) - {""}
    for mark, word in HOW[1:]:
        if mark in got:
            return word
    return "檢查用" if got else "沒發請求"


class _Asked:
    """Each address's requests as the ledger's request table holds them so far, kept beside the ledger's index and read
    on from the last id seen at each build (the table only grows: the crawler appends, nothing deletes), never the whole
    table again: per address (request.url, as the ledger joins it to url.url) the bits of its purposes and the start of
    its last request. ident: the ledger file's identity; another file, or a table that shrank, is read anew."""

    def __init__(self, ident: object) -> None:
        self.ident = ident
        self.last_id = 0
        self.bits: dict[str, int] = {}
        self.of: dict[str, list] = {}

    def read(self, conn: sqlite3.Connection) -> None:
        """The requests after the last id seen, CHUNK at a time (each statement a short read of its own)."""
        while True:
            got = conn.execute("SELECT id, url, purpose, started FROM request WHERE id > ? ORDER BY id LIMIT ?",
                               (self.last_id, CHUNK)).fetchall()
            for _rid, url, purpose, started in got:
                bit = self.bits.get(purpose)
                if bit is None:
                    bit = self.bits[purpose] = 1 << len(self.bits)
                had = self.of.get(url)
                if had is None:
                    self.of[url] = [bit, started]
                else:
                    had[0] |= bit
                    if started is not None and (had[1] is None or started > had[1]):
                        had[1] = started
            if got:
                self.last_id = got[-1][0]  # merging is idempotent: a reading cut short is read again from here
            if len(got) < CHUNK:
                return

    def purposes(self, bits: int) -> str:
        return ",".join(p for p, b in self.bits.items() if bits & b)


def _ident(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _ledger_rows(conn: sqlite3.Connection, wanted: list[str], open_sites: list[str]) -> list[tuple]:
    """The ledger's addresses of the sites wanted (url_result, its id order), CHUNK ids a statement (a rowid range:
    each read is short, none spans the whole table), with a last column saying whether the row is round two's open
    batch, kept apart as crawl_buckets keeps it."""
    if not wanted:
        return []
    apart, extra = "0", ()
    if open_sites:
        oq = ",".join("?" * len(open_sites))
        try:
            start = conn.execute(f"SELECT min(k.at) FROM round2_promoted k JOIN url u ON u.id = k.url_id"
                                 f" WHERE u.owner_site IN ({oq})", tuple(open_sites)).fetchone()[0]
            apart = (f"owner_site IN ({oq}) AND (id IN (SELECT url_id FROM round2_promoted) OR added_at >= ?)")
            extra = (*open_sites, start if start is not None else float("inf"))
        except sqlite3.Error:  # an older crawler: no round2_promoted, nothing told apart (crawl_buckets does the same)
            apart, extra = "0", ()
    top = conn.execute("SELECT max(id) FROM url").fetchone()[0] or 0
    # +owner_site: the site test is made on the row, so the statement walks its rowid range, not a site index
    sql = (f"SELECT id, owner_site, result, state, reason, kind, found_via, url, title, {apart} FROM url_result"
           f" WHERE id > ? AND id <= ? AND +owner_site IN ({','.join('?' * len(wanted))})")
    rows: list[tuple] = []
    for lo in range(0, top, CHUNK):
        rows += conn.execute(sql, (*extra, lo, lo + CHUNK, *wanted)).fetchall()
    return rows


def _crawl_build(where: Where, prev: object = None) -> Table:
    from nknu.archive import outcome
    from nknu.ops import pagecheck

    archive = where.archive
    _facts, sites = _library_sites(where)
    words = bucket_words()
    ident = _ident(archive / "frontier.db")
    asked = prev.info.get("asked") if isinstance(prev, Table) else None
    if not isinstance(asked, _Asked) or asked.ident != ident:
        asked = _Asked(ident)
    # read-only (mode=ro, query_only), autocommit: every statement below is its own short read, so the crawler's
    # writes and checkpoints at 01:00 never wait on this reading
    conn = pagecheck.open_ledger(archive)
    conn.row_factory = None  # plain tuples: a few hundred thousand rows
    try:
        open_sites = pipeline.round2_open(conn)
        wanted = sorted(set(sites) | set(open_sites))
        rows = _ledger_rows(conn, wanted, open_sites)
        # the other sites' addresses are only counted (the head's 庫外網站), through the url_owner index
        outside = conn.execute(f"SELECT count(*) FROM url WHERE owner_site IS NULL OR owner_site NOT IN"
                               f" ({','.join('?' * len(wanted))})", wanted).fetchone()[0]
        try:  # each address's requests: what they were for and the last one's time (per page, its last few)
            top = conn.execute("SELECT max(id) FROM request").fetchone()[0] or 0
            if top < asked.last_id:  # the table is shorter than what was read: another ledger put in place
                asked = _Asked(ident)
            asked.read(conn)
        except sqlite3.Error:
            log.info("原始纪录：账本的 request 表读不了，怎么抓的先照上一次的")
    finally:
        conn.close()
    kept = []
    apart = 0
    for r in rows:
        if r[9]:
            apart += 1
        elif r[1] not in sites:
            outside += 1  # a site of round two's open batch that the library does not have, outside the batch
        else:
            kept.append(r)
    rank = {b: i for i, b in enumerate(BUCKET_ORDER)}
    bucket_of: dict[str, str] = {}  # outcome.user_bucket by result code (a few dozen codes)
    for r in kept:
        if r[2] not in bucket_of:
            bucket_of[r[2]] = outcome.user_bucket(r[2])
    buckets = [bucket_of[r[2]] for r in kept]
    order = sorted(range(len(kept)), key=lambda i: (rank.get(buckets[i], len(rank)), kept[i][1] or "", kept[i][0]))
    cols = {k: _Col() for k in ("site", "bucket", "state", "reason", "kind", "via", "how", "result")}
    labels, hay, ids, last, miss = [], [], [], [], []
    label_words: dict[str, str] = {b: w for b, w in words.items() if b != "review"}
    hows: dict[tuple[bool, int], str] = {}  # how_of by (Google's API, the purposes' bits)
    for i in order:
        uid, site, result, state, reason, kind, via, url, title, _apart = kept[i]
        bucket = buckets[i]
        key = f"review:{result}" if bucket == "review" else bucket
        if key not in label_words:
            label_words[key] = review_word(result) if bucket == "review" else words.get(bucket, bucket)
        bits, at = asked.of.get(url) or (0, None)
        api = kind == "drive" or str(reason or "").startswith("api:")
        how = hows.get((api, bits))
        if how is None:
            how = hows[(api, bits)] = how_of(kind, reason or "", asked.purposes(bits))
        for name, value in (("site", site), ("bucket", bucket), ("state", state), ("reason", reason or ""),
                            ("kind", kind), ("via", via), ("how", how), ("result", result)):
            cols[name].add(value)
        labels.append(key)
        hay.append(f"{url} {title or ''}")
        ids.append(uid)
        last.append(at if at is not None else math.nan)
        miss.append(missed(bucket))
    status = pipeline.read_json(archive / "reports" / "update-status.json")
    night = status.get("night_started") if isinstance(status, dict) else None
    now = datetime.now(TAIPEI)
    left = "、".join(x for x in (f"第二輪這一批 {apart:,}" if apart else "", f"庫外網站 {outside:,}" if outside else "")
                    if x)
    info = {
        "label_words": label_words, "sites": sites, "bucket_words": words,
        "facet_words": {"state": STATE_WORDS, "via": VIA_WORDS, "kind": KIND_WORDS, "bucket": words},
        "facets": [("state", "帳本狀態"), ("reason", "原因"), ("how", "怎麼抓"), ("via", "怎麼找到"), ("kind", "種類")],
        "head": f"{len(kept):,} 個網址（庫裡 {len(sites)} 站" + (f"；不含{left}" if left else "") + "）",
        "unit": "個網址", "as_of": now.strftime("%m-%d %H:%M"), "run_day": _run_day(night),
        "tip": f"開跑先讀它、邊抓邊改；這裡是帳本 {now.strftime('%m-%d %H:%M')} 的樣子",
        "last": np.asarray(last, dtype=np.float64),
        "asked": asked,  # the next build reads the request table on from here
    }
    return Table(cols, labels, [[] for _ in labels], hay, np.asarray(ids, dtype=np.int64), miss, info)


_LEDGER_FIELDS = ("id", "url", "site", "kind", "depth", "found_via", "found_from", "state", "reason", "flags", "attempts",
                  "status", "ctype", "bytes", "fetched_at", "elapsed_ms", "title", "warc", "final_url", "host")


def _crawl_rows(where: Where, t: Table, sel: np.ndarray) -> list[dict]:
    from nknu.ops import pagecheck

    if not len(sel):
        return []
    ids = [int(x) for x in t.ref[sel]]
    vocab = {k: t.cols[k][1] for k in ("site", "bucket", "result", "how")}
    conn = pagecheck.open_ledger(where.archive)
    try:
        qs = ",".join("?" * len(ids))
        got = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT {', '.join(_LEDGER_FIELDS)} FROM url WHERE id IN ({qs})", ids)}
        froms = {g["found_from"] for g in got.values() if g.get("found_from")}
        from_url = {}
        if froms:
            fq = ",".join("?" * len(froms))
            from_url = {r[0]: r[1] for r in conn.execute(f"SELECT id, url FROM url WHERE id IN ({fq})", tuple(froms))}
        hosts = {g["host"] for g in got.values() if g.get("host")}
        queues = {}
        if hosts:
            hq = ",".join("?" * len(hosts))
            try:
                queues = {r[0]: r[1] for r in conn.execute(f"SELECT host, queue FROM host WHERE host IN ({hq})",
                                                           tuple(hosts))}
            except sqlite3.Error:
                queues = {}
        out = []
        for pos, i in zip(sel, ids, strict=True):
            g = got.get(i)
            if g is None:  # gone from the ledger since the index was read
                continue
            row = {k: mask(v) for k, v in g.items() if k != "host"}
            code = {name: vocab[name][int(t.cols[name][0][pos])] for name in vocab}
            row["site"] = code["site"]  # the site the panel counts it for (owner_site), as crawl_buckets does
            row["bucket"], row["result"], row["how"] = code["bucket"], code["result"], code["how"]
            key = t.label_vocab[int(t.label[pos])]
            row["lk"], row["label"] = key, t.info["label_words"].get(key, key)
            row["at"] = _tpe(g.get("fetched_at"))
            if g.get("found_from") in from_url:
                row["from_url"] = mask(from_url[g["found_from"]])
            row["req"] = _requests(conn, queues.get(g.get("host") or ""), g["url"], float(t.info["last"][pos]))
            out.append(row)
        return out
    finally:
        conn.close()


def _requests(conn: sqlite3.Connection, queue: str | None, url: str, last: float) -> list[dict]:
    """An address's last requests (up to six, the newest first), looked up in the quarter hour before its last one
    through the request table's (queue, started) index; the table has no index on url (RD-05)."""
    if math.isnan(last):
        return []
    cols = "purpose, method, status, error, round((ended - started) * 1000), bytes, campus, started"
    try:
        if queue:
            got = conn.execute(f"SELECT {cols} FROM request WHERE queue = ? AND started BETWEEN ? AND ? AND url = ?"
                               " ORDER BY started DESC LIMIT 6", (queue, last - 900, last + 1, url)).fetchall()
        else:
            got = conn.execute(f"SELECT {cols} FROM request WHERE url = ? ORDER BY started DESC LIMIT 6",
                               (url,)).fetchall()
    except sqlite3.Error:
        return []
    return [_clean({"purpose": p, "method": m, "status": s, "error": mask(e), "ms": int(ms) if ms is not None else None,
                    "bytes": b, "campus": c, "at": _tpe(at, "%Y-%m-%d %H:%M:%S")}) for p, m, s, e, ms, b, c, at in got]


# ------------------------------------------------------------------------------------------------ 導出


def nofile_word(result: str) -> str:
    return rename(f"沒有檔案：{tw(result)}")


def part_key(part: str) -> str:
    """A kind of what an export row lacks as a stable key: its mark in 缺什么 (pipeline.MISSING_KEYS), else its
    words."""
    return getattr(pipeline, "MISSING_KEYS", {}).get(part, part)


class _Packed:
    """Records kept beside an index, BLOCK of them to a zlib-compressed block of compact JSON, so a page reads its rows
    back without parsing a manifest again (one is up to megabytes); a page decompresses only the blocks its rows are
    in (a few KB each). The blocks of 146 manifests take a fraction of the manifests' size."""

    def __init__(self) -> None:
        self.blocks: list[bytes] = []
        self._open: list = []

    def add(self, record: object) -> tuple[int, int]:
        """Keep a record; (its block, its place in the block)."""
        at = (len(self.blocks), len(self._open))
        self._open.append(record)
        if len(self._open) >= BLOCK:
            self._close()
        return at

    def _close(self) -> None:
        if self._open:
            data = json.dumps(self._open, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.blocks.append(zlib.compress(data, 1))
            self._open = []

    def done(self) -> _Packed:
        self._close()
        return self

    def get(self, block: int, line: int, opened: dict[int, list]) -> object:
        """A record back; opened: the blocks one page has decompressed already."""
        rows = opened.get(block)
        if rows is None:
            rows = opened[block] = json.loads(zlib.decompress(self.blocks[block]))
        return rows[line]


def _export_build(where: Where, _prev: object = None) -> Table:
    facts, sites = _library_sites(where)
    cols = {k: _Col() for k in ("site", "kind", "cls", "via")}
    labels, also, hay, folders, idx, blocks, lines, miss = [], [], [], [], [], [], [], []
    packed = _Packed()
    words: dict[str, str] = {"file": rename("存下了檔案")}
    places: list[tuple[str, str]] = []  # (site, sites.export as written)
    unread = []
    for site in facts["sites"]:
        key, written = site.get("site") or "", site.get("export") or ""
        if not key or not written:
            continue
        try:
            manifest = json.loads((folder_of(where.root, written) / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unread.append(key)
            continue
        places.append((key, written))
        f = len(places) - 1
        for n, r in enumerate(manifest.get("项目") or []):
            if not isinstance(r, dict):
                continue
            parts = list(lacks(str(r.get("缺什么") or "")))
            keys = [part_key(p) for p in parts]
            saved = bool(r.get("本地档案"))
            result = str(r.get("结果") or "")
            first = keys[0] if keys else "file" if saved else f"nofile:{result}"
            more = keys[1:] + (["file"] if keys and saved else [])
            for k, p in zip(keys, parts, strict=True):
                words.setdefault(k, rename(p))
            if first not in words:
                words[first] = nofile_word(result)
            labels.append(first)
            also.append(more)
            for name, value in (("site", key), ("kind", tw(str(r.get("类型") or ""))),
                                ("cls", tw(str(r.get("分类") or ""))), ("via", tw(str(r.get("怎么来的") or "")))):
                cols[name].add(value)
            hay.append(f"{r.get('网址') or ''} {r.get('标题') or ''} {r.get('本地档案') or ''}")
            folders.append(f)
            idx.append(n)
            block, line = packed.add(r)
            blocks.append(block)
            lines.append(line)
            miss.append(bool(parts) or not saved)
    info = {"label_words": words, "sites": sites, "places": places, "packed": packed.done(),
            "facet_words": {}, "facets": [("kind", "種類"), ("cls", "分類"), ("via", "怎麼來的"), ("site", "網站")],
            "head": f"{len(labels):,} 筆（一筆可能缺好幾樣）", "unit": "筆",
            "tip": f"每站一個導出資料夾裡的清單；這裡是庫現在用的 {len(places)} 份合起來"
                   + (f"（讀不到 {len(unread)} 份：{'、'.join(unread[:5])}）" if unread else ""),
            "as_of": _tpe(facts.get("built_at")), "run_day": _library_day(facts)}
    ref = (np.asarray(folders, dtype=np.int32), np.asarray(idx, dtype=np.int32), np.asarray(blocks, dtype=np.int32),
           np.asarray(lines, dtype=np.int16))
    return Table(cols, labels, also, hay, ref, miss, info)


def _export_rows(_where: Where, t: Table, sel: np.ndarray) -> list[dict]:
    """A page of export rows, read back from the records kept beside the index (no manifest is opened)."""
    out = []
    folders, idx, blocks, lines = t.ref
    places, packed = t.info["places"], t.info["packed"]
    opened: dict[int, list] = {}
    for pos in sel:
        site, written = places[int(folders[pos])]
        n = int(idx[pos])
        r = packed.get(int(blocks[pos]), int(lines[pos]), opened)
        out.append(export_row(r, t.label_vocab[int(t.label[pos])], t.info["label_words"], site, written,
                              t.info["sites"].get(site, site), f"{site}#{n + 1}"))
    return out


def export_row(r: dict, key: str, words: Mapping[str, str], site: str, written: str, name: str, rid: str) -> dict:
    """One manifest row in the drawer's shape (the design's raw_steps.py 導出)."""
    url, title = str(r.get("网址") or ""), str(r.get("标题") or "")
    parts = list(lacks(str(r.get("缺什么") or "")))
    word = words.get(key, key)
    dot = "bad" if any(w in word for w in ("連線失敗", "失敗")) else "warn" if parts else "ok" if r.get("本地档案") else ""
    typ = str(r.get("类型") or "")
    dl: list[list] = []
    if title and url:
        dl.append(["網址", url, {"href": url, "mono": 1}])
    if r.get("最后落地网址") and r.get("最后落地网址") != url:
        dl.append(["轉到", r["最后落地网址"], {"href": r["最后落地网址"], "mono": 1}])
    res = tw(str(r.get("结果") or "")) + (f"：{tw(str(r['原因']))}" if r.get("原因") else "")
    dl.append(["結果", res, {"tip": str(r.get("原因代码") or "")}])
    if parts:
        dl.append(["缺", "", {"chips": [[rename(p), tw(str(r.get("缺什么") or ""))] for p in parts]}])
    if r.get("本地档案"):
        dl.append(["檔案", r["本地档案"], {"mono": 1, "tip": posixpath.basename(written.rstrip("/"))}])
    via = tw(str(r.get("怎么来的") or ""))
    if r.get("链到它的网页"):
        dl.append(["來路", via, {"from": r["链到它的网页"], "tip": str(r.get("链接文字") or "")}])
    elif via:
        dl.append(["來路", via, {}])
    if r.get("网页上写的日期"):
        said = f"{r.get('日期原文') or ''} {tw(str(r.get('日期来源') or ''))}".strip()
        dl.append(["日期", r["网页上写的日期"], {"tip": said}])
    if r.get("备注"):
        dl.append(["備註", tw(str(r["备注"])), {}])
    dl.append(["網站", name, {"tip": site}])
    ref = str(r.get("存档编号") or "")
    go = [["原樣", "原樣", "/check?url=" + quote(url, safe=""), ""]] if ref[:1] == "u" and url else []
    if r.get("本地档案"):
        go.append(["檔案", "檔案", f"/file?folder={quote(written, safe='')}&path={quote(str(r['本地档案']), safe='')}",
                   "導出存下的檔"])
    if url:
        go.append(["學校", "學校網頁", url, ""])
    meta = [[TYPE_ICON.get(typ, "嵌入內容"), "", "種類：" + tw(typ)]]
    if r.get("大小"):
        meta.append(["", _size(r["大小"]), ""])
    if r.get("格式"):
        meta.append(["", str(r["格式"]), "格式", 1])
    return _item(rid, key, word, dot, title or url, tip=url, mono=0 if title else 1,
                 time=str(r.get("抓取时间") or "")[5:16].replace("T", " "), meta=meta, dl=dl, go=go,
                 also=[rename(p) for p in parts[1:]], raw=r)


# ------------------------------------------------------------------------------------------------ 轉文字


def status_word(status: str) -> str:
    return rename(pipeline.STATUS_WORDS.get(status, status))


def accounting_path(where: Where) -> Path:
    return where.pipe / "totext" / "accounting.jsonl"


def _totext_build(where: Where, _prev: object = None) -> Table:
    facts, sites = _library_sites(where)
    prefixes = {k.replace("/", "_"): k for k in sites}
    cols = {k: _Col() for k in ("site", "reason", "route", "kind")}
    labels, hay, offsets, miss = [], [], [], []
    words: dict[str, str] = {s: status_word(s) for s in pipeline.STATUS_WORDS}
    names = dict(sites)
    path = accounting_path(where)
    with path.open("rb") as f:
        while True:
            at = f.tell()
            line = f.readline()
            if not line:
                break
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            status = str(r.get("status") or "")
            prefix = str(r.get("key") or "").split(":", 1)[0]
            site = prefixes.get(prefix, prefix)
            names.setdefault(site, site)
            if status not in words:
                words[status] = status_word(status)
            labels.append(status)
            for name, value in (("site", site), ("reason", tw(str(r.get("reason") or "")) or "—"),
                                ("route", str(r.get("route") or "")), ("kind", tw(str(r.get("type") or "")))):
                cols[name].add(value)
            hay.append(f"{r.get('member') or r.get('file') or ''} {r.get('key') or ''}")
            offsets.append(at)
            miss.append(status != "ok")
    info = {"label_words": words, "sites": names, "facet_words": {},
            "facets": [("reason", "原因"), ("route", "轉法"), ("kind", "種類"), ("site", "網站")],
            "head": f"{len(labels):,} 個檔（壓縮檔裡的檔各算一個）", "unit": "個檔",
            "as_of": _tpe(path.stat().st_mtime), "run_day": _library_day(facts)}
    return Table(cols, labels, [[] for _ in labels], hay, np.asarray(offsets, dtype=np.int64), miss, info)


_PAGE_WORDS = (("ocr_pages", "OCR", "用 OCR 認字的頁"), ("few_chars_pages", "字少", "字很少的頁"),
               ("misread_pages", "認錯", "認錯字的頁"), ("broken_pages", "壞頁", "打不開的頁"))


def _totext_rows(where: Where, t: Table, sel: np.ndarray) -> list[dict]:
    out = []
    with accounting_path(where).open("rb") as f:
        for pos in sel:
            f.seek(int(t.ref[pos]))
            try:
                r = json.loads(f.readline())
            except ValueError:
                continue
            site = t.cols["site"][1][int(t.cols["site"][0][pos])]
            out.append(totext_row(r, t.info["label_words"], site, t.info["sites"].get(site, site), f"t{int(t.ref[pos])}"))
    return out


def _n(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def totext_row(r: dict, words: Mapping[str, str], site: str, name: str, rid: str) -> dict:
    """One line of the to-text accounting in the drawer's shape (raw_steps.py 轉文字)."""
    path = str(r.get("member") or r.get("file") or "")
    status = str(r.get("status") or "")
    pages = [[f"{r['pages']} 頁", "頁數"]] if r.get("pages") else []
    for k, w, tip in _PAGE_WORDS:
        if _n(r.get(k)):
            pages.append([f"{w} {_n(r[k])}", tip])
    dl: list[list] = [["檔案", str(r.get("file") or ""), {"mono": 1}]]
    if r.get("member"):
        dl.append(["拆出", r["member"], {"mono": 1, "tip": "壓縮檔裡的檔"}])
    dl.append(["轉法", str(r.get("route") or ""), {"mono": 1}])
    if r.get("reason"):
        dl.append(["原因", tw(str(r["reason"])), {}])
    dl.append(["字數", f"{_n(r.get('chars')):,}", {}])
    if pages:
        dl.append(["頁", "", {"chips": pages, "plain": 1}])
    if r.get("ocr_mean_score") is not None:
        try:
            dl.append(["OCR", f"{float(r['ocr_mean_score']):.2f}", {"tip": "OCR 的平均把握（0–1）"}])
        except (TypeError, ValueError):
            pass
    if r.get("fixes"):
        fixes = r["fixes"] if isinstance(r["fixes"], list) else [r["fixes"]]
        dl.append(["修正", "、".join(map(str, fixes)), {}])
    try:
        if float(r.get("seconds") or 0) >= 0.01:
            dl.append(["花了", f"{float(r['seconds']):.2f} 秒", {}])
    except (TypeError, ValueError):
        pass
    dl.append(["網站", name, {"tip": site}])
    meta = [[TYPE_ICON.get(str(r.get("type") or ""), "檔案"), "", "種類：" + tw(str(r.get("type") or ""))],
            ["", str(r.get("route") or ""), "轉法", 1], ["", f"{_n(r.get('chars')):,} 字", ""]]
    return _item(rid, status, words.get(status, status), STATUS_DOT.get(status, ""), posixpath.basename(path),
                 tip=path, mono=1, meta=meta, dl=dl, raw=r)


# ------------------------------------------------------------------------------------------------ 建庫


def alias_word(why: str) -> str:
    if hasattr(pipeline, "alias_word"):  # one word on both sides (build_step's lost rows)
        return pipeline.alias_word(why)
    said = {"copy": "x(副本)", "format": "x(其他格式)", "duplicate": "x(重复)"}.get(why)
    return rename(pipeline.left_out_words(said)) if said else why


def doc_word(kind: str) -> str:
    return rename("成為文件：" + KIND_CLASS.get(kind, kind))


def left_keys(excluded: object, whys: set[str]) -> dict[str, str]:
    """{word: key} of what the build left out, as build_step keys its lost rows (pipeline.build_left_keys: one scheme
    on both sides, RD-13): a merge the library lists one by one is alias:<why>, any other left:<the first of the
    build's own keys with that word>."""
    if hasattr(pipeline, "build_left_keys"):
        return pipeline.build_left_keys(excluded, frozenset(whys))
    out = {alias_word(w): f"alias:{w}" for w in sorted(whys)}
    for key, n in (excluded if isinstance(excluded, dict) else {}).items():
        if isinstance(n, int) and n:
            out.setdefault(rename(pipeline.left_out_words(key)), f"left:{key}")
    return out


def site_index(conn: sqlite3.Connection) -> str | None:
    """The name of an index on the library's docs whose first column is site (not a partial one): through it every
    document's site is read without touching the text (docs.site lies after it); None when the build wrote none."""
    try:
        for name, partial in conn.execute("SELECT name, partial FROM pragma_index_list('docs')").fetchall():
            first = conn.execute("SELECT name FROM pragma_index_info(?) WHERE seqno = 0", (name,)).fetchone()
            if not partial and first is not None and first[0] == "site":
                return name
    except sqlite3.Error:
        return None
    return None


def _doc_sites(where: Where, key: object) -> tuple[object, dict[str, str]]:
    """(the library's key, each document's site by doc_id) for a library with no index on site: docs.site lies after
    the text, so this one statement walks every document's text. It is read only when a page needs the sites (a site
    picked, a page of rows and its 網站 facet), once a library version, never for the night view's counts (per=0)."""
    if key in _SITES_OF:  # kept past the index's IDLE drop: the walk is once a library version (review 11-23)
        return key, _SITES_OF[key]
    conn = open_ro(where.library)
    try:
        got = {d: s or "" for d, s in conn.execute("SELECT doc_id, site FROM docs")}
    finally:
        conn.close()
    _SITES_OF.clear()  # only the library in use (about 10 MB for 89k documents)
    _SITES_OF[key] = got
    return key, got


_SITES_OF: dict[object, dict[str, str]] = {}


def with_sites(t: Table, by_doc: Mapping[str, str]) -> None:
    """建庫's index given its site column (a merged record: its document's site) from a doc_id -> site map."""
    col = _Col()
    for ref in t.ref:
        parts = ref.split("\t")
        col.add(by_doc.get(parts[1] if parts[0] == "d" else parts[2], ""))
    t.cols["site"] = col.done()  # one assignment: a request reading the table sees the column whole or not at all


def _build_build(where: Where, lib_key: object = None) -> Table:
    """建庫's index from the columns before docs.text (doc_id, kind_class, title, url): never the text. Each
    document's site comes from an index on site when the library has one (rows then in site order, as the design
    lists them); else the index has no site column until a page needs it (with_sites, _doc_sites)."""
    facts, sites = _library_sites(where)
    conn = open_ro(where.library)
    try:
        docs = conn.execute("SELECT rowid, doc_id, kind_class, title, url FROM docs").fetchall()
        on_site = site_index(conn)
        site_of: dict[int, str] | None = None
        if on_site is not None:
            quoted = on_site.replace('"', '""')
            site_of = {r[0]: r[1] or "" for r in conn.execute(f'SELECT rowid, site FROM docs INDEXED BY "{quoted}"')}
        try:
            aliases = conn.execute("SELECT alias, doc_id, why FROM doc_alias").fetchall()
        except sqlite3.Error:  # a library built before doc_alias
            aliases = []
    finally:
        conn.close()
    # (site, merged, i, d|a, id, kind, title, url, extra)
    kept = {r[1]: ((site_of or {}).get(r[0], ""), r[2] or "", r[3] or "", r[4] or "") for r in docs}
    rows = [(*kept[r[1]][:1], 0, i, "d", r[1], *kept[r[1]][1:], "") for i, r in enumerate(docs)]
    for i, (alias, doc_id, why) in enumerate(aliases):
        site, kind, title, url = kept.get(doc_id) or ("", "", "", "")
        rows.append((site, 1, i, "a", alias, kind, title, url, f"{doc_id}\t{why or ''}"))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    cols = {k: _Col() for k in (("site", "kind") if site_of is not None else ("kind",))}
    labels, hay, refs, miss = [], [], [], []
    words: dict[str, str] = {}
    for site, _a, _i, what, ident, kind, title, url, extra in rows:
        key_of = f"doc:{kind}" if what == "d" else f"alias:{extra.split(chr(9))[1]}"
        if key_of not in words:
            words[key_of] = doc_word(kind) if what == "d" else alias_word(extra.split("\t")[1])
        labels.append(key_of)
        if site_of is not None:
            cols["site"].add(site)
        cols["kind"].add(kind)
        hay.append(f"{url} {title} {ident}")
        refs.append(f"{what}\t{ident}\t{extra}")
        miss.append(what != "d")
    # what the build only counted, a label a word, keyed as build_step keys its lost rows, so a row of the night view
    # finds its label (RD-13)
    only: dict[str, int] = {}
    shown = set(words.values())
    groups: dict[str, list[str]] = {}
    counted: dict[str, int] = {}
    for key, n in (facts["excluded"] if isinstance(facts["excluded"], dict) else {}).items():
        if isinstance(n, int) and n:
            word = rename(pipeline.left_out_words(key))
            groups.setdefault(word, []).append(key)
            counted[word] = counted.get(word, 0) + n
    keyed = left_keys(facts["excluded"], {str(a[2]) for a in aliases if a[2]})
    for word, keys in groups.items():
        if word in shown:
            continue  # merged records listed one by one (doc_alias)
        lk = keyed.get(word) or f"left:{keys[0]}"
        if lk in words and words[lk] != word:
            lk = f"left:{keys[0]}"
        only[lk] = only.get(lk, 0) + counted[word]
        words.setdefault(lk, word)
    # the other names a preset may use for one of these labels: every one of the build's own keys of a word (a word
    # is keyed by its first) and, where the pipeline names them, its groups' keys (left:<group>)
    others = {f"left:{g}": rename(w) for w, g in getattr(pipeline, "LEFT_KEYS", {}).items()}
    for word, keys in groups.items():
        others.update({f"left:{k}": word for k in keys})
    others.update({k: w for w, k in keyed.items()})
    n_docs = sum(1 for r in rows if r[3] == "d")
    not_docs = len(rows) - n_docs + sum(only.values())
    info = {"label_words": words, "sites": sites, "only": only, "aliases": others,
            "facet_words": {"kind": KIND_CLASS}, "facets": [("kind", "種類"), ("site", "網站")],
            "head": f"{n_docs:,} 份文件，另有 {not_docs:,} 筆沒成為文件", "unit": "筆",
            "as_of": _tpe(facts.get("built_at")), "run_day": _library_day(facts),
            "key": lib_key}  # the library version it was read from: its sites are attached from that version only
    return Table(cols, labels, [[] for _ in labels], hay, refs, miss, info)


def _build_rows(where: Where, t: Table, sel: np.ndarray) -> list[dict]:
    refs = [t.ref[int(p)].split("\t") for p in sel]
    wanted = {r[1] if r[0] == "d" else r[2] for r in refs}
    if not wanted:
        return []
    conn = open_ro(where.library)
    try:
        qs = ",".join("?" * len(wanted))
        docs = {r["doc_id"]: dict(r) for r in conn.execute(
            f"SELECT doc_id, site, kind_class, kind_raw, title, url, unit, ident_date, ident_kind, chars,"
            f" substr(text, 1, 160) AS head FROM docs WHERE doc_id IN ({qs})", tuple(wanted))}
        chunks = {r[0]: r[1] for r in conn.execute(
            f"SELECT doc_id, count(*) FROM chunks WHERE doc_id IN ({qs}) GROUP BY doc_id", tuple(wanted))}
    finally:
        conn.close()
    out = []
    for pos, ref in zip(sel, refs, strict=True):
        key = t.label_vocab[int(t.label[pos])]
        word = t.info["label_words"].get(key, key)
        if ref[0] == "d":
            d = docs.get(ref[1])
            if d is not None:
                out.append(build_doc_row(d, chunks.get(ref[1], 0), key, word, t.info["sites"]))
        else:
            d = docs.get(ref[2]) or {"doc_id": ref[2]}
            out.append(build_alias_row(ref[1], ref[3], d, key, word, t.info["sites"]))
    return out


def build_doc_row(d: dict, n_chunks: int, key: str, word: str, sites: Mapping[str, str]) -> dict:
    """A document of the library in the drawer's shape (raw_steps.py 建庫)."""
    kind, site, url = d.get("kind_class") or "", d.get("site") or "", d.get("url") or ""
    dl: list[list] = [["網址", url, {"href": url, "mono": 1}], ["網站", sites.get(site, site), {"tip": site}]]
    if d.get("unit"):
        dl.append(["單位", d["unit"], {}])
    if d.get("ident_date"):
        dl.append(["日期", d["ident_date"], {"tip": tw(str(d.get("ident_kind") or ""))}])
    dl.append(["種類", d.get("kind_raw") or "", {"mono": 1}])
    if d.get("head"):
        dl.append(["開頭", d["head"], {"quote": 1}])
    meta = [[KIND_ICON.get(kind, "網頁"), "", KIND_CLASS.get(kind, kind)], ["", f"{_n(d.get('chars')):,} 字", ""],
            ["", f"{n_chunks} 小段", "檢索時比對的單位"]]
    go = [["文件", "文件頁", "/trace?ref=" + quote(d["doc_id"], safe=""), ""]]
    if url:
        go.append(["學校", "學校網頁", url, ""])
    raw = {k: v for k, v in d.items() if k != "head"}
    return _item(d["doc_id"], key, word, "ok", d.get("title") or d["doc_id"], tip=url, time=d.get("ident_date") or "",
                 meta=meta, dl=dl, go=go, raw=raw)


def build_alias_row(alias: str, why: str, d: dict, key: str, word: str, sites: Mapping[str, str]) -> dict:
    """A record merged into another document (doc_alias) in the drawer's shape."""
    kind, url = d.get("kind_class") or "", d.get("url") or ""
    site = d.get("site") or ""
    dl = [["被併", alias, {"mono": 1, "tip": "被併掉的那筆的編號"}],
          ["併進", d.get("title") or d["doc_id"], {"doc": d["doc_id"]}], ["網址", url, {"href": url, "mono": 1}],
          ["網站", sites.get(site, site), {"tip": site}]]
    return _item(f"a:{alias}", key, word, "", d.get("title") or alias, tip=url,
                 meta=[[KIND_ICON.get(kind, "網頁"), "", KIND_CLASS.get(kind, kind)]], dl=dl,
                 go=[["文件", "留下的文件", "/trace?ref=" + quote(d["doc_id"], safe=""), ""]],
                 raw={"alias": alias, "doc_id": d["doc_id"], "why": why, "site": site, "kind_class": kind,
                      "title": d.get("title") or "", "url": url})


# ------------------------------------------------------------------------------------------------ 換庫

LEFT_DOT = {"ok": "", "look": "warn", "unknown": "bad"}


def _swap_build(changes: dict) -> Table:
    reasons = changes.get("reasons") or {}
    words = {"new": "新進", "changed": "改了"}
    for k, v in reasons.items():
        words[f"left:{k}"] = rename(str((v or {}).get("label") or k))
    cols = {k: _Col() for k in ("site", "kind")}
    labels, hay, entries, miss = [], [], [], []
    sites: dict[str, str] = {}
    shown: dict[str, int] = {}
    for s in changes.get("sites") or []:
        site = s.get("site") or ""
        sites[site] = s.get("name") or site
        lists = s.get("lists") or {}
        groups = [("new", x) for x in lists.get("new") or []] + [("changed", x) for x in lists.get("changed") or []]
        for reason, xs in (lists.get("left") or {}).items():
            groups += [(f"left:{reason}", x) for x in xs]
        for key, x in groups:
            words.setdefault(key, key)
            labels.append(key)
            cols["site"].add(site)
            cols["kind"].add(x.get("kind") or "")
            hay.append(f"{x.get('url') or ''} {x.get('title') or ''} {x.get('id') or ''}")
            entries.append((site, key, x))
            miss.append(key.startswith("left:"))
            shown[key] = shown.get(key, 0) + 1
    totals = changes.get("totals") or {}
    counts = {"new": int(totals.get("new") or 0), "changed": int(totals.get("changed") or 0)}
    for k, n in (changes.get("by_reason") or {}).items():
        counts[f"left:{k}"] = int(n or 0)
    swapped = (changes.get("new") or {}).get("swapped_at")
    # the night view's 離開 rows are keyed by the bare reason (night_view.swap_flow): left:<reason> here (RD-13)
    aliases = {k[len("left:"):]: w for k, w in words.items() if k.startswith("left:")}
    info = {"label_words": words, "sites": sites, "counts": counts, "shown": shown, "reasons": reasons,
            "aliases": aliases,
            "facet_words": {"kind": KIND_CLASS}, "facets": [("site", "網站"), ("kind", "種類")],
            "head": f"新進 {counts['new']:,}、改了 {counts['changed']:,}、離開 {int(totals.get('left') or 0):,} 份",
            "unit": "份文件", "as_of": _tpe(swapped), "run_day": _run_day(swapped), "night": changes.get("night") or ""}
    return Table(cols, labels, [[] for _ in labels], hay, entries, miss, info)


def _swap_rows(_where: Where, t: Table, sel: np.ndarray) -> list[dict]:
    out = []
    reasons = t.info["reasons"]
    for pos in sel:
        site, key, x = t.ref[int(pos)]
        rs = reasons.get(key[len("left:"):]) if key.startswith("left:") else None
        dot = "in" if rs is None else LEFT_DOT.get(str((rs or {}).get("kind") or ""), "")
        dl: list[list] = [["網址", x.get("url") or "", {"href": x.get("url"), "mono": 1}],
                          ["網站", t.info["sites"].get(site, site), {"tip": site}]]
        if x.get("date"):
            dl.append(["日期", x["date"], {}])
        if x.get("what"):
            dl.append(["改了", "", {"chips": [[w, ""] for w in x["what"]], "plain": 1}])
        if rs is not None:
            dl.append(["原因", t.info["label_words"].get(key, key), {"tip": str(rs.get("tip") or "")}])
        if x.get("note"):
            dl.append(["備註", tw(str(x["note"])), {}])
        go = [["文件", "文件頁", "/trace?ref=" + quote(str(x.get("id") or ""), safe=""), ""]] if rs is None else []
        if x.get("url"):
            go.append(["學校", "學校網頁", x["url"], ""])
        kind = str(x.get("kind") or "")
        meta = [[KIND_ICON.get({"file": "attachment"}.get(kind, kind), "網頁"), "", KIND_CLASS.get(kind, kind)],
                ["", f"{_n(x.get('chars')):,} 字", ""]]
        out.append(_item(f"s{int(pos)}", key, t.info["label_words"].get(key, key), dot, x.get("title") or x.get("id"),
                         tip=x.get("url") or "", meta=meta, dl=dl, go=go, raw={**x, "site": site}))
    return out


# ------------------------------------------------------------------------------------------------ one page


def _param(params: Mapping[str, str], name: str) -> str:
    return str(params.get(name) or "").strip()


def _int(text: str, default: int, low: int, high: int) -> int:
    return max(low, min(high, int(text))) if text.isascii() and text.isdigit() and len(text) <= 6 else default


def label_keys(t: Table, value: str) -> set[str]:
    """The label keys a preset names: a key, a key ending in * (every key it starts), another name of a label (a
    pipeline step's key for a lost row, info['aliases']), or a word (an older word renamed first); every key showing
    the same word comes along (two old words that are one now)."""
    words = {**t.info["label_words"]}
    if value.endswith("*"):
        return {k for k in words if k.startswith(value[:-1])}
    aliases = t.info.get("aliases") or {}
    if value in words:
        word = words[value]
    elif value in aliases:
        word = aliases[value]
    else:
        word = rename(value)
    return {k for k, w in words.items() if w in (word, value)}


def alias_keys(t: Table) -> dict[str, list[str]]:
    """Every other name of a label that finds one (info['aliases']): {name: [keys]} for the drawer's hooks."""
    words = t.info["label_words"]
    out = {}
    for name, word in (t.info.get("aliases") or {}).items():
        keys = sorted(k for k, w in words.items() if w == word)
        if keys and name not in words:
            out[name] = keys
    return out


def _filters(t: Table, step: str, params: Mapping[str, str]) -> tuple[dict, dict[str, np.ndarray], str]:
    """The filters a request asks for, as they apply (label keys, site key, …), their masks, and the word of a label
    that has only a count (no rows). label may come with alt, the words to try when label finds no key (a lost row's
    key and its words, RD-13)."""
    f: dict = {}
    masks: dict[str, np.ndarray] = {}
    only_word = ""
    label = _param(params, "label")
    alt = _param(params, "alt")
    bucket = _param(params, "bucket")
    if label:
        keys = label_keys(t, label)
        if not keys and alt:
            keys = label_keys(t, alt)
        only = {k for k in keys if k in (t.info.get("only") or {})}
        if step == "crawl" and not keys:  # a bucket or its word (a site's 未取得 rows): the bucket
            bw = t.info.get("bucket_words") or {}
            hit = [k for x in (label, alt) if x for k, w in bw.items() if x in (k, w) or rename(x) == w]
            if hit:
                bucket = bucket or hit[0]
                label = ""
        if label:
            f["label"] = sorted(keys)
            masks["label"] = t.label_mask(keys - only)
            if only and not (keys - only):
                only_word = t.info["label_words"].get(next(iter(only)), "")
    site = _param(params, "site")
    if site:
        sites = t.info.get("sites") or {}
        if site not in sites:
            site = next((k for k, n in sites.items() if n == site), site)
        f["site"] = site
        masks["site"] = t.col_mask("site", site) if "site" in t.cols else np.zeros(t.n, dtype=bool)
    if bucket and "bucket" in t.cols:
        f["bucket"] = bucket
        masks["bucket"] = t.col_mask("bucket", bucket)
    if _param(params, "miss") in ("1", "true", "yes"):
        f["miss"] = True
        masks["miss"] = t.miss
    for name, _title in t.info["facets"]:
        if name == "site":
            continue
        value = str(params.get(f"f_{name}") or params.get(name) or "")
        if value and name in t.cols:
            f[name] = value
            masks[name] = t.col_mask(name, value)
    q = _param(params, "q")[:200]
    if q:
        f["q"] = q
        masks["q"] = t.q_mask(q)
    return f, masks, only_word


def _combine(t: Table, masks: dict[str, np.ndarray], skip: str = "") -> np.ndarray:
    out = np.ones(t.n, dtype=bool)
    for name, m in masks.items():
        if name != skip:
            out &= m
    return out


def _fetchers() -> dict[str, Callable[[Where, Table, np.ndarray], list[dict]]]:
    return {"crawl": _crawl_rows, "export": _export_rows, "totext": _totext_rows, "build": _build_rows,
            "swap": _swap_rows}


def _source(where: Where, step: str, changes: dict | None
            ) -> tuple[str, object, Callable[[object], Table], float] | dict:
    """(place, key, build, every) of a step's index, or a body saying why there is none. The ledger's key is its
    files' size and time (the -wal's too): it is read again only when it changed, and no sooner than pipeline.EVERY
    seconds after the last reading (the one before served meanwhile, its request reading carried on)."""
    if step == "crawl":
        archive = where.archive
        if archive is None or not (archive / "frontier.db").exists():
            return {"state": "none", "words": "檢視台沒有掛上爬蟲的帳本"}
        ledger = archive / "frontier.db"
        key = (_stamp(ledger), _stamp(ledger.with_name("frontier.db-wal")), _file_key(where.library))
        return str(archive), key, lambda prev: _crawl_build(where, prev), pipeline.EVERY
    if not where.library.exists():
        return {"state": "none", "words": "沒有正式庫"}
    if step == "export":
        return str(where.library), _file_key(where.library), lambda prev: _export_build(where, prev), 0.0
    if step == "totext":
        path = accounting_path(where)
        if not path.exists():
            return {"state": "none", "words": "讀不到建庫時的轉文字記帳（totext/accounting.jsonl）"}
        return str(path), (_stamp(path), _file_key(where.library)), lambda prev: _totext_build(where, prev), 0.0
    if step == "build":
        key = _file_key(where.library)
        return str(where.library), key, lambda _prev: _build_build(where, key), 0.0
    changes = changes or {}
    if changes.get("state") != "ready":
        return {"state": "computing" if changes.get("state") == "computing" else "none",
                "words": str(changes.get("words") or "還沒有上一次換庫的比對")}
    return "swap:" + str(where.library), tuple(changes.get("keys") or ()), lambda _prev: _swap_build(changes), 0.0


def _sites_for(where: Where, t: Table, place: str, key: object, wait: float | None) -> tuple[str, str]:
    """建庫's index given its site column when it has none yet (a library the build wrote no index on site for): the
    sites read in the background, once a library version, and attached only to the index of that same version.
    (state, words) of the reading: ready, or computing / error with what to say."""
    if t.info.get("key") != key:  # the index served is the version before; the next one is being read
        return "computing", "正在讀新換上的庫"
    got, state, words = _index("build-sites", place, key, lambda _prev: _doc_sites(where, key), wait=wait)
    if got is not None and got[0] == key:
        with_sites(t, got[1])
        return "ready", ""
    if got is None and state == "error":
        return state, words
    # still reading (or the sites of the version before served meanwhile: not these)
    return "computing", "正在讀每份文件屬於哪個網站（庫裡沒有網站的索引，要把每份文件讀過一遍，第一次要幾十秒）"


def page(where: Where, step: str, params: Mapping[str, str], *, changes: dict | None = None,
         wait: float | None = None) -> dict:
    """One page of a step's raw records under the filters params names (label: a key, another name of one or a word;
    alt: words to try when label finds none; site: a site key; bucket (爬取): a bucket; miss=1: only what did not go
    on; q: words in an address, a title or a file name; f_<facet>: a facet's value; page (from 1) and per (or limit)
    rows a page). Returns {step, name, state, words, as_of, run_day, src, head, unit, out, labels: [[key, word, n,
    only]], label_on, facets: [[name, title, [[value, word, n]]]], filters, site_name, bucket_word, buckets, matched,
    scope, page, per, rows, note, only (the label picked has a count and no rows), aliases ({another name: [keys]})};
    per=0 asks for the counts only. Every string is masked (pipeline._SECRETS). changes: the last swap's comparison
    (換庫's rows). wait: seconds to wait for an index being read (tests)."""
    name = STEPS[step]
    base = {"step": step, "name": name, "src": dict(SRC[step]), "out": {"key": OUT[step]}}
    got = _source(where, step, changes)
    if isinstance(got, dict):
        return {**base, **got, "labels": [], "facets": [], "rows": [], "matched": 0}
    place, key, build, every = got
    if step == "swap":
        table, state, words = _index(step, place, key, build, wait=wait or 5.0)
    else:
        table, state, words = _index(step, place, key, build, every=every, wait=wait)
    if table is None:
        return {**base, "state": state, "words": words, "labels": [], "facets": [], "rows": [], "matched": 0}
    t = table
    info = t.info
    per = _int(_param(params, "per") or _param(params, "limit"), PER, 0, PER_MAX)
    if step == "build" and "site" not in t.cols and (per or _param(params, "site")):
        # a site picked waits for the sites; a page without one is served at once, its 網站 facet once they are read
        s_state, s_words = _sites_for(where, t, place, key, wait)
        if s_state != "ready" and _param(params, "site"):
            return {**base, "state": s_state, "words": s_words, "labels": [], "facets": [], "rows": [], "matched": 0}
    f, masks, only_word = _filters(t, step, params)
    hit = _combine(t, masks)
    page_no = _int(_param(params, "page"), 1, 1, 10 ** 6)
    idx = np.flatnonzero(hit)
    sel = idx[(page_no - 1) * per:page_no * per] if per else idx[:0]
    rows = _fetchers()[step](where, t, sel) if len(sel) else []
    # the label chips under every other filter; a count without rows (建庫's) only with no other filter on
    under = _combine(t, masks, "label")
    counts = t.label_counts(under)
    if step == "swap":  # the comparison's own totals: its lists are capped
        others = {k for k in masks if k != "label"}
        if not others:
            counts = {k: n for k, n in info["counts"].items() if n}
    labels = [[k, info["label_words"].get(k, k), n, False] for k, n in counts.items()]
    if not any(k != "label" for k in masks):
        labels += [[k, info["label_words"].get(k, k), n, True] for k, n in (info.get("only") or {}).items()]
    labels.sort(key=lambda x: -x[2])
    facets = []
    for col, title in info["facets"]:
        if col not in t.cols:
            continue
        vals = t.counts(col, _combine(t, masks, col))
        if col == "reason" and step == "crawl":  # the ledger's reasons that are written (the design's cut)
            vals = [v for v in vals if v[0]][:REASON_TOP]
        elif len(vals) > TOP:
            picked = f.get(col)
            vals = vals[:TOP] + [v for v in vals[TOP:] if v[0] == picked]
        wmap = (info.get("facet_words") or {}).get(col) or {}
        if col == "site":
            wmap = info.get("sites") or {}
        facets.append([col, title, [[v, wmap.get(v, v) or "（沒寫）", n] for v, n in vals]])
    src = {**base["src"]}
    src["tip"] = info.get("tip") or src.get("tip", "")
    out = {"key": OUT[step], "word": "文件" if step == "build" else info["label_words"].get(OUT[step], OUT[step])}
    note = ""
    if only_word:
        n = sum((info.get("only") or {}).get(k, 0) for k in f.get("label", []))
        note = f"{name}只記了這一類的數量（{n:,} 筆），沒有留下逐筆紀錄。"
    elif step == "swap":
        listed = int(hit.sum())
        want = sum(info["counts"].get(k, 0) for k in (f.get("label") or info["counts"]))
        if not (set(masks) - {"label"}) and listed < want:
            note = f"比對每站每一類只留前 200 份：共 {want:,} 份，這裡列 {listed:,} 份"
    body = {**base, "state": state, "src": src, "out": out, "as_of": info.get("as_of", ""),
            "run_day": info.get("run_day", ""), "night": info.get("night") or night_key(info.get("run_day", "")),
            "head": info.get("head", ""),
            "unit": info.get("unit", "筆"), "labels": labels, "label_on": f.get("label", []), "facets": facets,
            "filters": f, "site_name": (info.get("sites") or {}).get(f.get("site", ""), f.get("site", "")),
            "matched": len(idx), "scope": t.n, "page": page_no, "per": per, "rows": rows, "note": note,
            "only": bool(only_word), "aliases": alias_keys(t)}
    if step == "crawl":
        bw = info.get("bucket_words") or {}
        body["buckets"] = [[k, bw.get(k, k), n] for k, n in t.counts("bucket", under)]
        body["bucket_word"] = bw.get(f.get("bucket", ""), f.get("bucket", ""))
    return mask_all(body)  # every row of every step, the facets' values and the whole record under 全部欄位 (FD-05)


def api(where: Where, params: Mapping[str, str], *, changes: dict | None = None) -> tuple[int, dict]:
    """GET /api/raw: (status, body); 404 for a step that is none of STEPS."""
    step = _param(params, "step")
    if step not in STEPS:
        return 404, {"error": "沒有這一步"}
    try:
        return 200, page(where, step, params, changes=changes)
    except (OSError, sqlite3.Error, ValueError) as exc:  # a source being renamed into place, a busy ledger
        log.exception("原始纪录：%s 这一页读不了", step)
        return 200, {"step": step, "name": STEPS[step], "state": "error", "words": f"這一次讀不到：{type(exc).__name__}",
                     "labels": [], "facets": [], "rows": [], "matched": 0}
