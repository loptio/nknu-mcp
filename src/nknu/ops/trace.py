"""One page or file followed through the pipeline (plan 2026-11-15 T3; spec 2026-11-15 section 二.4). For a document id,
a merged copy's id or an address, trace reads what each step left of it and never computes a step again:

1. crawl and 2. export: the row of the site's export manifest (manifest.json), which carries what the crawler knew
   (when it was fetched, the outcome, the archive's own id of the address);
3. to text: the cache entry of the exported file's sha256 (result.json, text.md, a page's full.md) and the assembled record
   (corpus/<site>.json: whether a page was read again whole);
4. build: the library's rows (docs, dates, doc_scope, applies_to, doc_refs, doc_alias, sections, chunks) and the passage
   the models read of each chunk;
5. search: where a search for the document's own title finds it.

For what the library holds no document of, the reason is the build's own: nknu.content.documents.build_documents is
asked about that one record 【Claude】, so no second copy of the rules is kept here. Every path is under root, the
folder the database's relative paths start from (/work on the lab PC, the repository on the Mac). Read-only."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from opencc import OpenCC

from nknu.content import ids
from nknu.content.corpus import Record, load_corpus
from nknu.content.documents import build_documents
from nknu.content.embed import chunk_passages
from nknu.ops.explain import WHY
from nknu.ops.lookup import Resolved, as_address, columns, has_table, resolve
from nknu.totext.cache import FULL, RESULT, TEXT, Cache, sha256_file

log = logging.getLogger(__name__)

TOTEXT = Path("research/pipeline_2026-11-02/totext")  # where build.sh keeps the to-text output, under root
# 【Claude】 the six limits below: sizes that keep a report and the service's memory small, none of them measured
MAX_CHUNKS = 200  # chunks a report carries (a 300-page file has thousands); chunks_total says how many there are
MAX_TEXT = 200_000  # characters of text.md and full.md a report carries; text_chars and full_chars say the whole
WITHIN = 50  # how deep the own-title search looks
MANIFESTS, CORPORA = 8, 2  # how many of each stay loaded (the largest manifest is 25 MB, the largest corpus 93 MB)
NEAR = MANIFESTS  # sites of an address's own host whose manifests are opened before every site's is asked (_listed_in)
ROW_COLUMNS = ("序号", "类型", "网址", "最后落地网址", "本地档案", "标题", "网页上写的日期", "日期原文", "日期来源", "最后修改时间",
               "页别", "列表上的类别", "公告单位", "完整度", "缺什么", "结果", "原因", "格式", "链接文字", "来源", "备注",
               "按钮编号", "sha256")
RECORD_FIELDS = ("kind", "source", "under_read", "list_kind", "list_category", "area_mark", "date_source", "page_date",
                 "page_date_raw", "modified", "origin")
# The sentences a report carries are shown on the 檢視台 page, so they are in Traditional Chinese with Taiwan usage
# (owner 11-15: 「可视化dashboard全部换繁体字」); what the code matches stays as the crawler and the build write it:
# each pair's mark below, the export's column names, the build's keys, the crawler's results.
LEFT_OUT = (  # how a left-out record's key reads, by what it ends with or is (documents.build_documents' counts)
    ("(学校回答没有资料)", "學校這一頁回答的是「沒有資料」，所以不收。"),
    ("(列表翻页)", "這是列表的翻頁：它列的每一則都另外收了，這一頁本身不收。"),
    ("(学校删掉)", "這是學校刪掉的公告，只剩空殼，所以不收。"),
    ("(空白)", "它沒有標題，也沒有讀到字，所以不收。"),
    ("(重复)", "同一則公告在同一個網站有另一個網址，留的是那一個。"),
    ("只在选单出现的图片", "它是只在選單上出現的圖片（多半是單位的橫幅），不算一份文件。"),
)
READINGS = {"nothing": "對不上就不讀", "whole": "對不上就整頁讀", "body": "對不上就讀去掉頁首、選單、頁尾的整頁"}
ALONE = {"nothing": "這種網頁不讀字", "whole": "整頁讀", "body": "讀去掉頁首、選單、頁尾的整頁"}
# what the assembled record of a manifest row turned out to be (_record): read; the corpus was read and has none; the
# corpus could not be read; the library names no corpus for the site
FOUND, ABSENT, UNREADABLE, UNNAMED = "found", "absent", "unreadable", "unnamed"
STALE = "這個原因是照現在的紀錄檔問出來的；紀錄檔在建庫以後重新整理過，不一定是建庫當時的原因。"
_S2TW = OpenCC("s2tw")


def _tw(text: str) -> str:
    """What the crawler, to-text or the build wrote in Simplified (a result, a reason, a build key), as a sentence of
    the report shows it: OpenCC s2tw. The report's own fields keep the value as written, for what matches on it."""
    return _S2TW.convert(text) if text else text


OwnTitleSearch = Callable[[sqlite3.Connection, str], tuple[list[str], str]]


@dataclass
class _Manifest:
    site: str
    name: str
    rows_of: dict[str, list[dict]]  # the document id the build gives a row -> its rows in the manifest's order (an
    #                                 address spelt twice is one id and two rows)
    by_url: dict[str, dict]
    id_of: dict[str, str]  # 序号 -> that id (a row a button brought: its button's, never its address's)


@dataclass
class _Corpus:
    records: dict[str, Record]
    sha256: str  # of the file as read: the build wrote the one it read into meta inputs_json


_manifests: OrderedDict[tuple[str, int], _Manifest] = OrderedDict()
_corpora: OrderedDict[tuple[str, int], _Corpus] = OrderedDict()
# one entry: (root, the sites and their exports) -> (document id -> site, the export folders that could not be read)
_everywhere: dict[tuple, tuple[dict[str, str], list[str]]] = {}


def _cached(store: OrderedDict, path: Path, keep: int, load: Callable[[Path], object]):
    """load(path), kept by the file's name and time of change; the least lately used beyond keep is dropped."""
    key = (str(path), path.stat().st_mtime_ns)
    if key in store:
        store.move_to_end(key)
        return store[key]
    store[key] = load(path)
    while len(store) > keep:
        store.popitem(last=False)
    return store[key]


def _load_manifest(path: Path) -> _Manifest:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in manifest.get("项目", []) if isinstance(r, dict) and str(r.get("序号") or "").isdecimal()]
    keys = ids.manifest_keys(rows)
    rows_of: dict[str, list[dict]] = {}
    id_of: dict[str, str] = {}
    by_url: dict[str, dict] = {}
    for r in rows:
        doc_id = "d" + ids.record_id(keys[int(r["序号"])])
        rows_of.setdefault(doc_id, []).append(r)
        id_of[str(r["序号"])] = doc_id
        by_url.setdefault(r.get("网址") or "", r)
        by_url.setdefault(r.get("最后落地网址") or "", r)
    return _Manifest(manifest.get("网站") or "", manifest.get("名称") or "", rows_of, by_url, id_of)


def _load_corpus(path: Path) -> _Corpus:
    return _Corpus({r.id: r for r in load_corpus(path)}, sha256_file(path))


def site_exports(conn: sqlite3.Connection) -> dict[str, str]:
    """site -> the export folder the build read; {} for a database without the sites table (built before 11-08)."""
    if not has_table(conn, "sites"):
        return {}
    return {site: export for site, export in conn.execute("SELECT site, export FROM sites") if export}


def _near(sites: dict[str, str], address: str) -> list[str]:
    """The sites an address may belong to: those of its host, the one whose key begins the address first."""
    bare = address.split("://", 1)[-1]
    try:
        host = (urlsplit("//" + bare).hostname or "").lower()
    except ValueError:
        return []
    same = [s for s in sites if s.split("/", 1)[0].lower() == host]
    return sorted(same, key=lambda s: (not bare.lower().startswith(s.lower()), -len(s), s))


def _listed_in(root: Path, sites: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """(the id the build gives each row of every site's manifest -> the first site that lists it, the export folders
    whose manifest could not be read). Read once per process and set of exports (every manifest is parsed: about 4
    seconds on the lab PC, 11-15), for what the sites of its own host do not list: a file on another host is exported
    with the site whose page links it, and a copy may be another site's page."""
    key = (str(root), tuple(sorted(sites.items())))
    if key not in _everywhere:
        found: dict[str, str] = {}
        unreadable: list[str] = []
        for site, export in sites.items():
            try:
                manifest = _load_manifest(root / export / "manifest.json")
            except (OSError, ValueError):
                unreadable.append(export)
                continue
            for doc_id in manifest.rows_of:
                found.setdefault(doc_id, site)
        _everywhere.clear()
        _everywhere[key] = (found, unreadable)
    return _everywhere[key]


def _pick(rows: list[dict], urls: list[str]) -> dict:
    """Of the rows of one id: the one at one of urls (the address asked for, then the document's own); else the first
    with a file; else the first. The build keeps one record of an id (documents.one_per_id); the address is how the
    owner tells the rows apart."""
    for url in urls:
        for row in rows:
            if url and row.get("网址") == url:
                return row
    return next((row for row in rows if row.get("本地档案")), rows[0])


def _find_row(root: Path, sites: dict[str, str], order: list[str], doc_ids: list[str], urls: list[str],
              gone: list[str]) -> tuple[str, _Manifest, dict] | None:
    """(the site, its manifest, the row) of the first site of order whose manifest has one of doc_ids, else one of
    urls; None when none has. A manifest that cannot be read is skipped and named in gone."""
    for site in order:
        path = root / sites[site] / "manifest.json"
        try:
            manifest = _cached(_manifests, path, MANIFESTS, _load_manifest)
        except (OSError, ValueError) as exc:
            gone.append(f"{sites[site]}（{type(exc).__name__}）")
            continue
        for doc_id in doc_ids:
            if doc_id in manifest.rows_of:
                return site, manifest, _pick(manifest.rows_of[doc_id], urls)
        for url in urls:
            if url and url in manifest.by_url:
                return site, manifest, manifest.by_url[url]
    return None


def _crawl(row: dict) -> dict:
    size = str(row.get("大小") or "")
    return {"fetched": row.get("抓取时间") or "", "size": int(size) if size.isdecimal() else None,
            "result": row.get("结果") or "", "reason": row.get("原因") or "", "complete": row.get("完整度") or "",
            "lacks": row.get("缺什么") or "", "how": row.get("怎么来的") or "",
            "linked_from": row.get("链到它的网页") or "", "archive_id": row.get("存档编号") or ""}


def _reading(page_kind: str) -> str:
    """How the page rule read a page, in words, from a page route's page_kind (kind|reading|version)."""
    parts = page_kind.rsplit("|", 2)
    if len(parts) != 3:
        return ""
    area, _, rest = parts[1].rpartition(":")
    if area:
        return f"先照平台（{area}）設定的正文區讀，{READINGS.get(rest, rest)}"
    return ALONE.get(rest, rest)


def _totext(cache: Cache, sha: str, missing: list[str]) -> dict | None:
    try:
        folder = cache.dir(sha)
        result = json.loads((folder / RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        missing.append(f"轉文字的快取裡沒有這個檔的結果（{sha[:12] or '清單上沒有它的 sha256'}）。")
        return None
    text = cache.extra(sha, TEXT)
    full = cache.extra(sha, FULL) if (folder / FULL).exists() else None
    # ocr_pages: one entry per page our OCR read ({"page": 1, "why": ..., "reason": ..., "lines": ..., "mean_score": ...},
    # pages from 1); docling_ocr_pages: the page numbers Docling's own OCR read. A page in both is one page
    ours = [p for p in result.get("ocr_pages") or [] if isinstance(p, dict)]
    ocr = {p.get("page") for p in ours} | {p for p in result.get("ocr_pages") or [] if isinstance(p, int)}
    ocr |= {p for p in result.get("docling_ocr_pages") or [] if isinstance(p, int)}
    reasons = [r for r in dict.fromkeys(str(p.get("reason") or p.get("why") or "") for p in ours) if r]
    page_kind = str(result.get("page_kind") or "")
    return {"sha256": sha, "version": result.get("version"), "route": result.get("route"),
            "status": result.get("status"), "reason": result.get("reason") or "", "pages": result.get("pages") or 0,
            "ocr_pages": len(ocr), "ocr_reasons": reasons, "chars": result.get("chars") or 0,
            "ocr_lines": result.get("ocr_lines") or 0,
            "ocr_mean_score": result.get("ocr_mean_score"), "seconds": result.get("seconds"),
            "page_kind": page_kind, "reading": _reading(page_kind) if result.get("route") == "page" else "",
            "tools": result.get("tools") or {}, "text": text[:MAX_TEXT], "text_chars": len(text),
            "full": None if full is None else full[:MAX_TEXT], "full_chars": None if full is None else len(full)}


def _record(conn: sqlite3.Connection, root: Path, site: str, doc_id: str) -> tuple[Record | None, str, bool, list[str]]:
    """(the assembled record of doc_id, what was found, whether the corpus file is no longer the one the build read,
    what to tell the owner) from the corpus file the build read for site (meta inputs_json: its path and sha256)."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'inputs_json'").fetchone()
    try:
        named = next((i for i in json.loads(row[0]) if i.get("site") == site), {}) if row else {}
        corpus, built = named.get("corpus") or "", named.get("corpus_sha256") or ""
    except (ValueError, TypeError, AttributeError):
        corpus, built = "", ""
    if not corpus:
        return None, UNNAMED, False, []
    try:
        loaded: _Corpus = _cached(_corpora, root / corpus, CORPORA, _load_corpus)
    except (OSError, ValueError) as exc:
        return None, UNREADABLE, False, [f"整理好的紀錄檔讀不了：{corpus}（{type(exc).__name__}）。"]
    stale = bool(built) and loaded.sha256 != built
    notes = [f"紀錄檔 {corpus} 在建庫以後重新整理過（和庫讀的不是同一版）：「整理成的紀錄」不一定是建庫當時的。"] if stale else []
    record = loaded.records.get(doc_id[1:])
    return record, FOUND if record is not None else ABSENT, stale, notes


def _words_for(key: str) -> str:
    for mark, words in LEFT_OUT:
        if key.endswith(mark) or key == mark:
            return words
    return f"建庫把它歸在「{_tw(key)}」這一類，這一類不收（列表頁、相簿、網站地圖這類頁面只是通往別的網頁）。"


def _left_out(row: dict, record: Record | None, state: str, stale: bool) -> dict:
    """Why the library holds no document for a manifest row. A reason is claimed only for what was read: the build's
    own rules asked about the record; the manifest row for a row without a file; nothing when the record could not
    be read."""
    if record is None:
        if not row.get("本地档案"):
            labels = (("结果", "結果"), ("原因", "原因"), ("缺什么", "缺"))  # the manifest's column, the words' label
            got = "、".join(f"{label}「{_tw(row[name])}」" for name, label in labels if row.get(name))
            return {"key": "", "words": f"匯出清單上這一行沒有檔案（{got or '沒寫原因'}）。沒有檔就沒有文字，所以沒進庫。"}
        if state == ABSENT:
            return {"key": "", "words": "匯出清單上有這一行，整理成紀錄時沒有留下它"
                                        "（嵌在網頁裡的圖片、壓縮檔本身這類，不會自己成為一條紀錄）。" + (STALE if stale else "")}
        return {"key": "", "words": "匯出清單上這一行有檔，但整理成的紀錄讀不到，所以說不出建庫為什麼沒收它。"}
    try:
        docs, excluded = build_documents([record], {}, [], site=record.site)
    except Exception as exc:  # noqa: BLE001 - whatever the build's rules raise on one record never fails the trace
        return {"key": "", "words": f"問不出建庫會怎麼判這一條：{type(exc).__name__}: {exc}"}
    if docs or not excluded:
        return {"key": "", "words": "照規則它會成為一份文件，但庫裡沒有：多半是這個庫建好以後才匯出，"
                                    "或它和別的文件一字不差、被併成了副本。" + (STALE if stale else "")}
    key = next(iter(excluded))
    return {"key": key, "words": _words_for(key) + (STALE if stale else "")}


def _dicts(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    cur = conn.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur]


def _library(conn: sqlite3.Connection, doc_id: str, max_chunks: int, missing: list[str]) -> dict:
    have = columns(conn, "docs")
    doc = _dicts(conn, f"SELECT {', '.join(c for c in sorted(have) if c != 'text')} FROM docs WHERE doc_id = ?",
                 (doc_id,))[0]
    out: dict = {"doc": doc, "site": None, "scope": [], "applies_to": [], "parent": None, "refs": [], "copies": []}
    if doc.get("site") and has_table(conn, "sites"):
        out["site"] = next(iter(_dicts(conn, "SELECT * FROM sites WHERE site = ?", (doc["site"],))), None)
    out["dates"] = _dicts(conn, "SELECT kind, date, raw, source FROM dates WHERE doc_id = ? ORDER BY kind, date",
                          (doc_id,))
    if has_table(conn, "doc_scope"):
        out["scope"] = _dicts(conn, "SELECT relation, unit_id, campus, level, cohort_from FROM doc_scope"
                                    " WHERE doc_id = ? ORDER BY relation, unit_id", (doc_id,))
    out["applies_to"] = _dicts(conn, "SELECT unit, cohort_from, cohort_exact, raw FROM applies_to WHERE doc_id = ?",
                               (doc_id,))
    if doc.get("parent_id"):
        out["parent"] = {"doc_id": doc["parent_id"], "title": doc.get("parent_title") or ""}
    if has_table(conn, "doc_refs"):
        out["refs"] = _dicts(conn, "SELECT r.ref_id, r.zone, r.context, COALESCE(d.title, '') AS title FROM doc_refs r"
                                   " LEFT JOIN docs d ON d.doc_id = r.ref_id WHERE r.doc_id = ? ORDER BY r.ref_id",
                             (doc_id,))
    out["children"] = _dicts(conn, "SELECT doc_id, kind_class, title FROM docs WHERE parent_id = ? ORDER BY doc_id"
                                   " LIMIT 50", (doc_id,))
    out["children_total"] = conn.execute("SELECT count(*) FROM docs WHERE parent_id = ?", (doc_id,)).fetchone()[0]
    if has_table(conn, "doc_alias"):
        listed = conn.execute("SELECT value FROM meta WHERE key = ?", ("copies:" + doc_id,)).fetchone()
        try:
            urls = {c.get("id"): c.get("url") or "" for c in json.loads(listed[0])} if listed else {}
        except (ValueError, TypeError, AttributeError):
            urls = {}
        out["copies"] = [{"alias": alias, "why": why, "url": urls.get(alias, "")} for alias, why in conn.execute(
            "SELECT alias, why FROM doc_alias WHERE doc_id = ? ORDER BY why, alias", (doc_id,))]
    out["sections"] = _dicts(conn, "SELECT sec_id, heading, start, end FROM sections WHERE doc_id = ? ORDER BY sec_id",
                             (doc_id,))
    heading = {s["sec_id"]: s["heading"] for s in out["sections"]}
    text = conn.execute("SELECT text FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()[0] or ""
    out["chunks_total"] = conn.execute("SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    rows = conn.execute("SELECT chunk_id, sec_id, start, end FROM chunks WHERE doc_id = ? ORDER BY chunk_id LIMIT ?",
                        (doc_id, max(max_chunks, 0))).fetchall()
    passages: dict[int, str] = {}
    if rows:
        try:
            passages = chunk_passages(conn, [r[0] for r in rows])
        except (sqlite3.Error, ValueError) as exc:  # a layout older than the table-header columns (the skeleton's)
            missing.append(f"這個庫的版面比較舊，讀不出每一小段「模型實際讀到的字」（{type(exc).__name__}: {exc}）。")
    out["chunks"] = [{"chunk_id": c, "sec_id": sec, "heading": heading.get(sec, ""), "start": start, "end": end,
                      "text": text[start:end], "passage": passages.get(c, "")} for c, sec, start, end in rows]
    return out


def keyword_title_search(today: date) -> OwnTitleSearch:
    """The own-title search without models: the keyword search, as far as WITHIN."""
    from nknu.retrieval.search import search_docs
    from nknu.retrieval.server import aliases_of

    def run(conn: sqlite3.Connection, title: str) -> tuple[list[str], str]:
        found = search_docs(conn, title, today=today, aliases=aliases_of(conn), limit=WITHIN)
        return [h.doc_id for h in found.hits], "keyword"

    return run


def _db(conn: sqlite3.Connection) -> dict:
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'schema_version')"))
    return {"built_at": meta.get("built_at"), "schema": meta.get("schema_version")}


def _own_row(root: Path, sites: dict[str, str], res: Resolved, doc: dict, copies: list[dict],
             missing: list[str]) -> tuple[tuple[str, _Manifest, dict] | None, str]:
    """(the manifest row steps 1 to 3 show, whose it is). The reference's own row first: a document's in its own
    site's manifest (and nowhere else: one it lacks is an archive member or a calendar event, which no manifest lists
    by its id); an address's or a merged copy's among the sites of its host, the nearest NEAR, then in the one
    manifest of all that lists it (a copy may be another site's page, a file may sit on a host that is no site: it
    is exported with the site whose page links it). 'kept': for a merged copy whose own row no manifest has, the row
    of the document kept for it, and the report says so (review 11-15: the kept row was shown as the reference's)."""
    address = as_address(res.ref)
    own = [] if not address else ["d" + ids.record_id(ids.location_key(address))]
    own += [res.alias] if res.alias else []
    own += [res.doc_id] if res.doc_id and res.via != "alias" else []
    own = list(dict.fromkeys(own))
    copy_url = next((c["url"] for c in copies if c["alias"] == res.alias and c["url"]), "") if res.via == "alias" else ""
    urls = [u for u in (address and res.ref, copy_url, "" if res.via == "alias" else doc.get("url")) if u]
    order = [doc["site"]] if res.via != "alias" and doc.get("site") in sites else []
    for hint in (address, copy_url):
        order += _near(sites, hint)[:NEAR] if hint else []
    order = list(dict.fromkeys(order))
    if not own:
        missing.append("庫裡找不到這個編號；它也不是網址，沒辦法再到匯出清單裡找。")
        return None, "reference"
    gone: list[str] = []
    found = _find_row(root, sites, order, own, urls, gone) if order else None
    if found is None and (res.doc_id is None or res.via == "alias"):
        listed, unreadable = _listed_in(root, sites)
        if (site := next((listed[i] for i in own if i in listed), None)) is not None and site not in order:
            found = _find_row(root, sites, [site], own, urls, gone)
        if found is None and res.doc_id is None:
            looked = (f"{len(sites)} 個網站的清單都找過了" if not unreadable else
                      f"{len(sites)} 個網站裡，讀得到的 {len(sites) - len(unreadable)} 個都找過了；讀不到的："
                      f"{'、'.join(unreadable[:3])}{' 等' if len(unreadable) > 3 else ''}")
            missing.append(f"匯出清單裡找不到它（{looked}）。")
    of = "reference"
    if found is None and res.via == "alias" and doc.get("site") in sites:
        found, of = _find_row(root, sites, [doc["site"]], [res.doc_id], [doc.get("url") or ""], gone), "kept"
        if found is not None:
            missing.append("你給的那一份（併掉的副本）自己的清單行找不到，①～③ 顯示的是留下來的那一份。")
    if found is None and res.doc_id is not None and not gone:
        missing.append("它來自的網站的匯出清單上沒有它自己的一行（壓縮檔裡的檔、行事曆事件是這樣），"
                       "所以匯出和轉文字這兩步沒有紀錄。")
    if gone:
        missing.append("匯出資料夾不在了或讀不了：" + "、".join(dict.fromkeys(gone))
                       + "（多半是這個網站後來重新匯出、舊的那份被清掉了）。")
    return found, of


def trace(conn: sqlite3.Connection, ref: str, *, root: Path, today: date,
          own_title_search: OwnTitleSearch | None = None, totext: Path | None = None,
          max_chunks: int = MAX_CHUNKS) -> dict:
    """What each step left of ref: the module docstring. A part that cannot be read is None and 'missing' says why, in
    Traditional Chinese (the 檢視台 page shows it); the dict is JSON. totext: the to-text output folder (default
    root / TOTEXT)."""
    root = Path(root)
    res: Resolved = resolve(conn, ref)
    missing: list[str] = []
    report: dict = {"ref": res.ref, "resolved": {"doc_id": res.doc_id, "via": res.via, "why": res.why,
                                                 "alias": res.alias},
                    "db": _db(conn), "export": None, "crawl": None, "totext": None, "record": None, "library": None,
                    "left_out": None, "search": None, "missing": missing}
    if res.doc_id is not None:
        report["library"] = _library(conn, res.doc_id, max_chunks, missing)
    library = report["library"] or {}
    doc = library.get("doc", {})
    sites = site_exports(conn)
    found = None
    if not res.ref:
        missing.append("找不到：沒有給網址或文件編號。")
    elif not sites:
        missing.append("這個庫沒有記下每個網站的匯出資料夾（建得比較早），所以匯出和轉文字這兩步沒有紀錄。")
    else:
        found, of = _own_row(root, sites, res, doc, library.get("copies", []), missing)
    if found is not None:
        site, manifest, row = found
        report["export"] = {"folder": sites[site], "site": site, "name": manifest.name, "of": of,
                            "file": row.get("本地档案") or "", "row": {c: row.get(c) or "" for c in ROW_COLUMNS}}
        report["crawl"] = _crawl(row)
        if row.get("本地档案"):
            # the cache is keyed by the exported file's own sha256 (totext.accounting): the manifest's is the archived
            # original's, and a page is re-saved with a BOM on export, so the two differ for every page
            try:
                sha = sha256_file(root / sites[site] / row["本地档案"])
            except OSError:
                sha = row.get("sha256") or ""
            report["totext"] = _totext(Cache((totext or root / TOTEXT) / "cache"), sha, missing)
        # the record of the row shown, under the id the build gives that row (a button's row: its button's)
        record, state, stale, notes = _record(conn, root, site, manifest.id_of[str(row["序号"])])
        missing += notes
        if record is not None:
            report["record"] = {name: getattr(record, name) for name in RECORD_FIELDS}
        if res.doc_id is None:
            report["left_out"] = _left_out(row, record, state, stale)
    if res.doc_id is not None and doc.get("title"):
        search = own_title_search or keyword_title_search(today)
        try:
            found_ids, how = search(conn, doc["title"])
            place = found_ids.index(res.doc_id) + 1 if res.doc_id in found_ids[:WITHIN] else None
            report["search"] = {"query": doc["title"], "rank": place, "within": WITHIN, "how": how}
        except Exception as exc:
            log.exception("拿标题去搜失败")
            missing.append(f"拿它的標題去搜沒有成功：{type(exc).__name__}: {exc}")
    report["missing"] = list(dict.fromkeys(missing))
    return report


# ---------------------------------------------------------------- as text (the command's output)

# the words below and what size_words ... cohort_words return are the 檢視台 page's too (nknu.ops.web): Traditional
ROUTES = {"pdf": "PDF", "page": "網頁", "image": "圖片", "office": "Office 檔", "archive": "壓縮檔", "text": "文字檔"}
KINDS = {"notice": "公告", "page": "網頁", "attachment": "附件", "event": "行事曆", "link": "校外連結"}
DATE_KINDS = {"published": "公告日期", "inherited": "跟所屬公告", "server": "伺服器檔案時間", "period_start": "期間開始",
              "period_end": "期間結束", "event_start": "事件開始", "event_end": "事件結束"}
HOW = {"keyword": "只用關鍵字", "hybrid": "關鍵字＋向量＋重排"}


def ident_words(doc: dict, dates: list[dict]) -> str:
    """What a document's own date is, said by the fact it came from: a 「published」 date by where it was read (the
    date's source: 公告頁日期欄, 列表上的日期, 網頁上寫的日期; since the library of 11-16 a file is dated by its own row
    on a file table too, spec 2026-11-16-data-fixes §二, and 公告日期 would misname it), any other by its kind."""
    kind = doc.get("ident_kind") or ""
    if kind == "published":
        for x in dates:
            if x.get("kind") == kind and x.get("date") == doc.get("ident_date") and x.get("source"):
                return str(x["source"])
    return DATE_KINDS.get(kind, kind)


def when(stamp: str) -> str:
    """An ISO time as the owner reads it: 2026-11-08T03:12:00+08:00 -> 2026-11-08 03:12."""
    return stamp[:16].replace("T", " ") if len(stamp) >= 16 and stamp[10:11] == "T" else stamp


def size_words(n: int | None) -> str:
    if n is None:
        return ""
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} 位元組"


def how_converted(t: dict) -> str:
    """One line on how a file became text. OCR is 掃描認字 here, the word 資料總覽 uses for it (owner 11-15, of that
    page's names: say it the way a person would, no jargon on the page)."""
    route = ROUTES.get(t["route"], str(t["route"]))
    if t["status"] != "ok":
        return f"{route}　沒轉成（{t['status']}：{_tw(t['reason']) or '沒寫原因'}）"
    if t["route"] == "page":
        how = t["reading"]
    elif t["route"] == "image":
        how = f"掃描認字（{t['ocr_lines']} 行）"
    elif not t["ocr_pages"]:
        how = "直接讀（不用認字）"
    elif t["ocr_pages"] >= t["pages"]:
        how = f"全部 {t['pages']} 頁掃描認字"
    else:
        how = f"{t['pages']} 頁裡 {t['ocr_pages']} 頁掃描認字"
    if t["route"] != "page" and t.get("ocr_reasons"):
        how += f"（{'、'.join(_tw(r) for r in t['ocr_reasons'])}）"
    score = f"　認字信心 {t['ocr_mean_score']:.2f}" if isinstance(t["ocr_mean_score"], int | float) else ""
    pages = f"　{t['pages']} 頁" if t["pages"] else ""
    return f"{route}　{how}{pages}　{t['text_chars']:,} 字{score}"


def under_read_words(value: bool | None) -> str:
    if value is None:
        return ""
    return "正文區讀到的字太少，建庫時改讀了整頁" if value else "照正文區讀到的字建庫"


def merged_words(report: dict) -> str:
    """For a merged copy's reference: whose rows the steps show."""
    if report["export"] is not None and report["export"].get("of") == "reference":
        return "①～③ 是你給的那一份，④ 和 ⑤ 是留下來的那一份。"
    if report["export"] is not None:
        return "你給的那一份自己的清單行找不到，所以 ①～③ 是留下來的那一份，④ 和 ⑤ 也是。"
    return "④ 和 ⑤ 是留下來的那一份。"


def cohort_words(applies: list[dict]) -> str:
    """The cohorts a document says it applies to: 113 學年度起入學、110 學年度入學."""
    said = [f"{a['cohort_from']} 學年度起入學" if a.get("cohort_from") is not None else
            f"{a['cohort_exact']} 學年度入學" if a.get("cohort_exact") is not None else str(a.get("raw") or "")
            for a in applies]
    return "、".join(dict.fromkeys(x for x in said if x))


def trace_text(report: dict, chunks: int = 3) -> str:
    """The report as the command prints it; the first chunks of the document's chunks. Its own frame stays in
    Simplified Chinese, the command line's (CLAUDE.md); the words it shares with the 檢視台 page are Traditional."""
    r = report["resolved"]
    out = [f"追踪  {report['ref'] or '（空白）'}"]
    if r["via"] == "alias":
        out.append(f"这个编号并进了 {r['doc_id']}（{WHY.get(r['why'], r['why'])}）：{merged_words(report)}")
    if report["db"]["built_at"]:
        out.append(f"读的库：{report['db']['built_at']} 建的")
    pad = "\n          "
    c, e, t, lib = report["crawl"], report["export"], report["totext"], report["library"]
    if c:
        size = f"　{size_words(c['size'])}" if c["size"] is not None else ""
        why = f"（{c['reason']}）" if c["reason"] else ""
        out.append(f"① 抓取    {when(c['fetched']) or '没有时间'}{size}　结果：{c['result']}{why}　"
                   f"存档编号 {c['archive_id'] or '—'}")
    else:
        out.append("① 抓取    （看不到）")
    if e:
        row = e["row"]
        lines = [f"网站 {e['site']}（{e['name']}）　{row['类型']}　标题「{row['标题']}」",
                 f"档案 {e['file'] or '没有'}　资料夹 {e['folder']}"]
        extra = [f"{label} {row[name]}" for name, label in (("网页上写的日期", "网页上写的日期"), ("日期来源", "日期来源"),
                                                           ("最后修改时间", "最后修改时间"), ("页别", "页别"),
                                                           ("列表上的类别", "列表上的类别"), ("缺什么", "缺")) if row[name]]
        if extra:
            lines.append("　".join(extra))
        out.append("② 导出    " + pad.join(lines))
    else:
        out.append("② 导出    （看不到）")
    if t:
        lines = [how_converted(t)]
        if t["full_chars"] is not None:
            lines.append(f"整页读法有 {t['full_chars']:,} 字")
        if report["record"] and (words := under_read_words(report["record"]["under_read"])):
            lines.append(words)
        out.append("③ 转文字  " + pad.join(lines))
    else:
        out.append("③ 转文字  （看不到）")
    if lib:
        d = lib["doc"]
        tier = "背景" if d.get("tier") == "background" else "主料"
        lines = [f"{d['doc_id']}　{KINDS.get(d['kind_class'], d['kind_class'])}　{tier}　标题「{d['title']}」　{d['chars']:,} 字"]
        day = f"{d['ident_date']}（{ident_words(d, lib['dates'])}）" if d["ident_date"] else "没有日期"
        lines.append(f"日期 {day}" + "".join(
            f"；{DATE_KINDS.get(x['kind'], x['kind'])} {x['date']}「{x['raw']}」来自{x['source']}" for x in lib["dates"]))
        unit = [f"单位 {d['unit']}"] + ([f"组别 {d['grp']}"] if d.get("grp") else [])
        unit += [f"{'谁发的' if s['relation'] == 'publisher' else '适用于' if s['relation'] == 'applies_to' else '提到'} "
                 f"{s['unit_id']}" for s in lib["scope"]]
        lines.append("　".join(unit))
        if cohorts := cohort_words(lib["applies_to"]):
            lines.append(f"适用的入学届：{cohorts}")
        if "expires" in d:
            lines.append(f"会不会过期：{'会' if d['expires'] else '不会'}"
                         + (f"（算 {d['valid_ay']} 学年的）" if d.get("valid_ay") else ""))
        if lib["parent"]:
            lines.append(f"挂在「{lib['parent']['title']}」底下（{lib['parent']['doc_id']}）")
        if lib["children_total"]:
            lines.append(f"底下挂着 {lib['children_total']} 个附件")
        if lib["copies"]:
            lines.append(f"并进它的副本 {len(lib['copies'])} 份：" + "、".join(
                f"{x['alias']}（{WHY.get(x['why'], x['why'])}）" for x in lib["copies"][:5]))
        lines.append(f"{len(lib['sections'])} 节 {lib['chunks_total']} 小段"
                     + (f"，前 {min(chunks, len(lib['chunks']))} 段：" if lib["chunks"] and chunks > 0 else ""))
        out.append("④ 建库    " + pad.join(lines))
        for i, chunk in enumerate(lib["chunks"][: max(chunks, 0)], start=1):
            out.append(f"          [{i}] " + chunk["text"].replace("\n", "\n              "))
    elif report["left_out"]:
        out.append(f"④ 建库    没进库：{report['left_out']['words']}")
    else:
        out.append("④ 建库    库里找不到这一份。")
    if (s := report["search"]) is not None:
        place = f"第 {s['rank']} 名" if s["rank"] else f"前 {s['within']} 名里没有"
        out.append(f"⑤ 检索    拿它的标题去搜（{HOW.get(s['how'], s['how'])}）：{place}")
    else:
        out.append("⑤ 检索    （没有搜）")
    if report["missing"]:
        out += ["", "看不到的："] + [f"- {m}" for m in report["missing"]]
    return "\n".join(out)
