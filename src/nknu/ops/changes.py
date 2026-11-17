"""昨晚變了什麼: the library before the last swap (aca.prev.db, the nightly ingest's hard link to the library it
replaced) against the one in use (aca.db), per site: the documents that came in, the ones that changed (the same id
with another title, date or text) and the ones that left, each that left with the reason it left, looked up in data
that exists (spec docs/superpowers/specs/2026-11-17-panels-structure-design.md 三.3; owner context: on 11-16 a swap
almost lost 317 attachments silently, and neither the totals nor the crawler's own comparison showed it, so every
document that leaves gets a reason or is said to have none).

Where a reason comes from, in the order it is looked for (the build's own rules: nknu.content.corpus GONE, MOVED,
NO_DATA, the area marks and the 列表翻页 page kind; documents.GONE_FILE_SUFFIX; doc_alias' whys):
  1. the new library's doc_alias names the id: it was merged into another document (copy, format, duplicate)
  2. a document of the new library has the same address: only its id changed
  3. an event (id 'e…'): the site's calendar file in its new export no longer holds it, it was folded into its
     series, or it is from before 2023
  4. its site is not in the new library at all
  5. the row of its address in the export the new library read (sites.export → manifest.json): what 缺什么 says
     (学校已删除, 学校把这则公告转到别的页, 学校回答没有资料, an empty or missing notice area), 页别 列表翻页, every page
     linking it deleted by the school (a file that went with them), no file saved this time: the school's when its
     分类 is 学校那边已经不存在 (build.SCHOOL_GONE, as the build's swap guard reads it), else ours (结果 says why)
  6. no row for it: when the site's export changed, the crawler's new export does not list it; when it did not, the
     input was the same and the build changed (its program or its settings)
  7. else 查不出原因: the export lists it, the crawler got it, and the build left it out
Reasons 1–5 that the school or the build's merging explain are fine (ok); a file the crawler did not get this time, an
address the new export does not list, a site gone, or the same input built differently may be ours to fix (look);
查不出原因 is red on the page.

The libraries are opened read-only (pipeline.open_ro, mode=ro) and read once a pair: both hold the text, and a
document's columns after it cost a read of its pages (about a gigabyte a library), so the comparison runs in a
background thread (Changes), kept by both files' identity, size and time of change, with each library's
fingerprints kept by its own (the library in use tonight is the one before tomorrow: its hard link keeps them).
The reasons' names and kinds, and the caps, are 【Claude】 (redesign 2026-11-17,
research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from nknu.ops.pipeline import CONTENT, CRAWLER, folder_of, open_ro, span, tw, when_of

log = logging.getLogger(__name__)

LIST_CAP = 200  # documents kept per list (a site's new, changed, one reason's left; the unexplained) 【Claude】
OK_KIND, LOOK, UNKNOWN = "ok", "look", "unknown"
# the crawler's marks in 缺什么 and 页别 as the build reads them (nknu.content.corpus); copied here when the content
# package is not importable in the copy the tools run (it always is on the lab PC: /work/inspect-code/src is /work/src)
try:
    from nknu.content.corpus import AREA_MARKS, GONE, LIST_PAGER, MOVED, NO_DATA
except ImportError:
    GONE, MOVED, NO_DATA, LIST_PAGER = "学校已删除", "学校把这则公告转到别的页", "学校回答没有资料", "列表翻页"
    AREA_MARKS = ("内文区是空的", "找不到内文区")
# the export's 分类 of a row with no file because the school took the address away: the build's swap guard counts it
# as the school's (nknu.content.build._departures), and so does this comparison (review 2026-11-17)
try:
    from nknu.content.build import SCHOOL_GONE
except ImportError:
    SCHOOL_GONE = "学校那边已经不存在"
CALENDAR_SINCE = date(2023, 1, 1)  # documents.CALENDAR_SINCE: the library holds events from this day on

# key: (the short name the page shows, the sentence its floating card holds, its kind, who fixes it when it is ours)
REASONS = {
    "gone": ("學校刪了", "爬蟲隔一天以上看了兩次都說不存在，導出標了「學校已刪除」，建庫照標記不收", OK_KIND, ""),
    "moved": ("學校轉到別頁", "這則公告的網址被學校轉到別的頁（多半是刪了），導出標了，建庫照標記不收", OK_KIND, ""),
    "no_data": ("學校回答沒資料", "學校這一頁回答「沒有資料」，建庫照標記不收", OK_KIND, ""),
    "shell": ("只剩空殼的公告", "學校把公告的內容拿掉了，只剩網頁的框，建庫不收", OK_KIND, ""),
    "gone_file": ("跟著刪掉的附件", "連到它的網頁都被學校刪了，附件一起不收", OK_KIND, ""),
    "school_gone": ("學校那邊已經不存在", "爬蟲這一次去抓，學校那邊已經沒有這個網址了（錯誤頁、跳回首頁），導出沒有檔，建庫不收",
                    OK_KIND, ""),
    "pager": ("公告列表的分頁", "列表翻頁的網頁不進庫，每一則公告另外收了", OK_KIND, ""),
    "copy": ("併成副本", "別處有一模一樣的一份，合成一份；留下的那份見它的編號", OK_KIND, ""),
    "format": ("其他格式", "同一個檔的另一種格式（例如 PDF 和 Word），只留一份", OK_KIND, ""),
    "duplicate": ("同一網址重複", "同一個網址導出了兩次，只留一份", OK_KIND, ""),
    "renumbered": ("換了編號", "同一個網址還在新庫裡，只是文件的編號換了", OK_KIND, ""),
    "series": ("併進行事曆系列", "每週重複的行事曆合成一個系列，這一次是其中一次", OK_KIND, ""),
    "calendar_gone": ("行事曆上沒有了", "學校的行事曆這一次沒有這一筆（學校改了行事曆）", OK_KIND, ""),
    "before_2023": ("2023 年以前的行事曆", "庫只收 2023 年起的行事曆", OK_KIND, ""),
    "not_fetched": ("這次沒抓到", "導出清單裡有它，可是這一次沒有存下檔案（爬蟲的結果寫在後面）", LOOK, CRAWLER),
    "not_exported": ("導出沒列它", "這個網站新的導出清單裡沒有這個網址", LOOK, CRAWLER),
    "site_gone": ("網站不在新庫", "這個網站整個不在新庫的網站表裡", LOOK, CONTENT),
    "same_export": ("導出沒換", "這個網站用的導出和上一份庫一樣，離開是建庫的程式或設定改了", LOOK, CONTENT),
    "unknown": ("查不出原因", "導出清單裡有它、也抓到了，卻沒有進新庫", UNKNOWN, CONTENT),
}
ALIAS_REASON = {"copy": "copy", "format": "format", "duplicate": "duplicate"}


# ------------------------------------------------------------------------------------------------ one library

def file_key(path: Path) -> tuple | None:
    """What says a library file is the same one: its device, inode, size and time of change; None when missing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def read_library(db: Path) -> dict:
    """A library's fingerprint: {'docs': {doc_id: (site, kind, title, url, date, chars, digest)}, 'meta': built_at
    and vec_built_at, 'sites': {site: {name, export, docs}}, 'aliases': {alias: (doc_id, why)}, 'urls': {url:
    doc_id}}. The text is hashed, never kept."""
    conn = open_ro(db)
    try:
        meta = {r[0]: r[1] for r in conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('built_at', 'vec_built_at')")}
        sites = {r["site"]: {"name": r["name"] or "", "export": r["export"] or "", "docs": r["docs"]}
                 for r in conn.execute("SELECT site, name, export, docs FROM sites")}
        try:
            aliases = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT alias, doc_id, why FROM doc_alias")}
        except sqlite3.Error:  # a library built before doc_alias
            aliases = {}
        docs: dict[str, tuple] = {}
        urls: dict[str, str] = {}
        for doc_id, site, kind, title, url, day, text in conn.execute(
                "SELECT doc_id, site, kind_class, title, url, ident_date, text FROM docs"):
            text = text or ""
            digest = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()
            docs[doc_id] = (site or "", kind or "", title or "", url or "", day or "", len(text), digest)
            if url:
                urls.setdefault(url, doc_id)
    finally:
        conn.close()
    return {"docs": docs, "meta": meta, "sites": sites, "aliases": aliases, "urls": urls}


# ------------------------------------------------------------------------------------------------ the exports

class Manifests:
    """The export folders' rows by address, read once a folder (an export folder is written once)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._rows: dict[str, dict | None] = {}
        self._calendars: dict[str, dict | None] = {}

    def rows(self, written: str) -> dict | None:
        """{address: row} of the folder sites.export writes (both 网址 and 最后落地网址, and the address without
        &amp; or a #fragment), None when its manifest cannot be read."""
        if written in self._rows:
            return self._rows[written]
        out: dict | None = None
        try:
            folder = folder_of(self.root, written)
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            out = {}
            for row in manifest.get("项目", []):
                if not isinstance(row, dict):
                    continue
                for key in (row.get("网址"), row.get("最后落地网址")):
                    for k in _forms(str(key or "")):
                        out.setdefault(k, row)
        except (OSError, ValueError):
            out = None
        self._rows[written] = out
        return out

    def calendar(self, written: str) -> dict | None:
        """{event id: item} of the folder's calendar files (data/*行事历*.json, corpus.load_calendar's), None: none."""
        if written in self._calendars:
            return self._calendars[written]
        out: dict | None = None
        try:
            folder = folder_of(self.root, written)
            files = sorted({f for p in ("data/*行事历*.json", "data/*行事曆*.json") for f in folder.glob(p)})
            if files:
                out = {}
                for f in files:
                    for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
                        if isinstance(it, dict) and it.get("id"):
                            out[str(it["id"])] = it
        except (OSError, ValueError):
            out = None
        self._calendars[written] = out
        return out


def _forms(url: str) -> list[str]:
    """An address as it may be written: as is, with &amp; as &, without its #fragment."""
    if not url:
        return []
    forms = [url, url.replace("&amp;", "&")]
    forms += [f.split("#", 1)[0] for f in forms if "#" in f]
    return list(dict.fromkeys(forms))


def _row_of(rows: dict, url: str) -> dict | None:
    return next((rows[f] for f in _forms(url) if f in rows), None)


# ------------------------------------------------------------------------------------------------ the reasons

def why_left(doc_id: str, fp: tuple, new: dict, prev: dict, manifests: Manifests) -> tuple[str, str]:
    """(reason key, a note) of a document of prev that new does not hold (the module docstring's order)."""
    site, _kind, _title, url, day, _chars, _digest = fp
    alias = new["aliases"].get(doc_id)
    if alias is not None:
        return ALIAS_REASON.get(alias[1], "copy"), f"留下的是 {alias[0]}"
    other = new["urls"].get(url) if url else None
    if other and other != doc_id:
        return "renumbered", f"新編號 {other}"
    after = new["sites"].get(site)
    if after is None:
        return "site_gone", ""
    if doc_id.startswith("e"):
        items = manifests.calendar(after["export"])
        item = items.get(doc_id[1:]) if items is not None else None
        if items is not None and (item is None or item.get("status") == "cancelled"):
            return "calendar_gone", ""
        if item is not None and item.get("recurringEventId"):
            return "series", ""
        if day and day < CALENDAR_SINCE.isoformat():
            return "before_2023", ""
    before = prev["sites"].get(site) or {}
    same_folder = bool(before.get("export")) and before.get("export") == after["export"]
    rows = manifests.rows(after["export"])
    if rows is None:
        return "unknown", "讀不到這個網站新導出的清單"
    row = _row_of(rows, url)
    if row is None:
        return ("same_export", "") if same_folder else ("not_exported", "")
    lacks = str(row.get("缺什么") or "")
    if GONE in lacks:
        return "gone", ""
    if MOVED in lacks:
        return "moved", ""
    if NO_DATA in lacks:
        return "no_data", ""
    if str(row.get("页别") or "") == LIST_PAGER:
        return "pager", ""
    if any(m in lacks for m in AREA_MARKS):
        return "shell", ""
    linking = [u.strip() for u in str(row.get("链到它的网页") or "").split(" | ") if u.strip() and "…等" not in u]
    if linking:
        rows_linking = [_row_of(rows, u) for u in linking]
        if all(r is not None and GONE in str(r.get("缺什么") or "") for r in rows_linking):
            return "gone_file", ""
    if not row.get("本地档案"):
        if str(row.get("分类") or "") == SCHOOL_GONE:  # the school's, as the build's swap guard counts it
            return "school_gone", ""
        return "not_fetched", tw(str(row.get("结果") or "")) or "沒有檔案"
    if same_folder:
        return "same_export", ""
    return "unknown", ""


def _entry(doc_id: str, fp: tuple, note: str = "", what: list[str] | None = None) -> dict:
    _site, kind, title, url, day, chars, _digest = fp
    out = {"id": doc_id, "title": title, "url": url, "kind": kind, "date": day, "chars": chars}
    if note:
        out["note"] = note
    if what:
        out["what"] = what
    return out


def compare(prev: dict, new: dict, root: Path, hold: set[str] | None = None) -> dict:
    """The two libraries' differences, per site (the module docstring): {'totals': {new, changed, left, unexplained,
    look, look_fix}, 'sites': [{site, name, new, changed, left, unexplained, look, reasons: {key: n}, held, lists:
    {new, changed, left: {key: [...]}}}], 'unexplained': [... with site], 'reasons': REASONS as dicts}. Sites with
    something unexplained first, then something to look at, then by how much changed."""
    manifests = Manifests(root)
    hold = hold or set()
    per: dict[str, dict] = {}

    def row(site: str) -> dict:
        if site not in per:
            name = (new["sites"].get(site) or prev["sites"].get(site) or {}).get("name") or site
            per[site] = {"site": site, "name": name, "new": 0, "changed": 0, "left": 0, "unexplained": 0, "look": 0,
                         "reasons": {}, "held": site in hold, "lists": {"new": [], "changed": [], "left": {}}}
        return per[site]

    pd, nd = prev["docs"], new["docs"]
    for doc_id, fp in nd.items():
        old = pd.get(doc_id)
        if old is None:
            r = row(fp[0])
            r["new"] += 1
            if len(r["lists"]["new"]) < LIST_CAP:
                r["lists"]["new"].append(_entry(doc_id, fp))
            continue
        what = [w for w, i in (("標題", 2), ("日期", 4), ("內容", 6)) if old[i] != fp[i]]
        if what:
            r = row(fp[0])
            r["changed"] += 1
            if len(r["lists"]["changed"]) < LIST_CAP:
                r["lists"]["changed"].append(_entry(doc_id, fp, what=what))
    unexplained: list[dict] = []
    look_fixers: dict[str, int] = {}
    for doc_id, fp in pd.items():
        if doc_id in nd:
            continue
        key, note = why_left(doc_id, fp, new, prev, manifests)
        r = row(fp[0])
        r["left"] += 1
        r["reasons"][key] = r["reasons"].get(key, 0) + 1
        kind = REASONS[key][2]
        if kind == UNKNOWN:
            r["unexplained"] += 1
            if len(unexplained) < LIST_CAP:
                unexplained.append({**_entry(doc_id, fp, note), "site": fp[0], "site_name": r["name"]})
        elif kind == LOOK:
            r["look"] += 1
            fixer = REASONS[key][3]
            look_fixers[fixer] = look_fixers.get(fixer, 0) + 1
        shown = r["lists"]["left"].setdefault(key, [])
        if len(shown) < LIST_CAP:
            shown.append(_entry(doc_id, fp, note))
    sites = sorted(per.values(), key=lambda s: (-s["unexplained"], -s["look"], -(s["new"] + s["changed"] + s["left"]),
                                                s["name"]))
    totals = {k: sum(s[k] for s in sites) for k in ("new", "changed", "left", "unexplained", "look")}
    totals["look_fix"] = max(look_fixers, key=look_fixers.get) if look_fixers else ""
    reasons_total: dict[str, int] = {}
    for s in sites:
        for k, n in s["reasons"].items():
            reasons_total[k] = reasons_total.get(k, 0) + n
    return {"totals": totals, "sites": sites, "unexplained": unexplained, "by_reason": reasons_total,
            "reasons": {k: {"label": v[0], "tip": v[1], "kind": v[2], "fix": v[3]} for k, v in REASONS.items()}}


# ------------------------------------------------------------------------------------------------ the background

def read_hold(path: Path) -> set[str]:
    """The site keys ingest-hold.txt names (nightly_ingest.read_hold: the first cell of a line; # is a note)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return set()
    lines = (line.split("#", 1)[0].split("\t", 1)[0].strip() for line in text.splitlines())
    return {line for line in lines if line}


class Changes:
    """The last swap's comparison, made in a background thread and kept until either library file changes.
    snapshot(): {'state': none (no library before to compare with), same (the two are one file: the last night did
    not swap), computing, ready, error; 'words'; when ready the comparison and 'prev', 'new' (built_at,
    swapped_at), 'night' (the night the new library was swapped in), 'took'}."""

    def __init__(self, new_db: Path, root: Path, *, prev_db: Path | None = None, hold: Path | None = None) -> None:
        self.new_db = new_db
        self.prev_db = prev_db or new_db.with_name(f"{new_db.stem}.prev{new_db.suffix}")
        self.root = root
        self.hold = hold or new_db.with_name("ingest-hold.txt")
        self._lock = threading.Lock()
        self._done: dict | None = None
        self._keys: tuple | None = None
        self._thread: threading.Thread | None = None
        self._libs: dict[tuple, dict] = {}  # file key -> read_library (at most three kept)

    def _library(self, path: Path, key: tuple) -> dict:
        with self._lock:
            got = self._libs.get(key)
        if got is None:
            got = read_library(path)
            with self._lock:
                self._libs[key] = got
                while len(self._libs) > 3:
                    self._libs.pop(next(iter(self._libs)))
        return got

    def _run(self, keys: tuple) -> None:
        started = time.monotonic()
        try:
            prev = self._library(self.prev_db, keys[0])
            new = self._library(self.new_db, keys[1])
            result = compare(prev, new, self.root, read_hold(self.hold))
            swapped = new["meta"].get("vec_built_at") or new["meta"].get("built_at")
            result.update(state="ready", keys=list(map(str, keys)),
                          prev={"built_at": prev["meta"].get("built_at"),
                                "swapped_at": prev["meta"].get("vec_built_at") or prev["meta"].get("built_at"),
                                "docs": len(prev["docs"])},
                          new={"built_at": new["meta"].get("built_at"), "swapped_at": swapped, "docs": len(new["docs"])},
                          night=night_of_text(swapped), took=span(time.monotonic() - started))
        except Exception as exc:  # the page says it; the log has the whole
            log.exception("昨晚变了什么：比不了两份库")
            result = {"state": "error", "keys": list(map(str, keys)), "words": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            self._done, self._keys = result, keys

    def snapshot(self) -> dict:
        prev_key, new_key = file_key(self.prev_db), file_key(self.new_db)
        if new_key is None:
            return {"state": "none", "words": "沒有正式庫"}
        if prev_key is None:
            return {"state": "none", "words": "沒有前一份庫可以比（aca.prev.db 不在：每晚進庫換過一次庫才會有）"}
        if prev_key[:2] == new_key[:2]:
            return {"state": "same", "words": "前一份庫和現在用的是同一份（還沒換上新庫：每晚進庫在建庫前先留一份，換上以後才有得比）"}
        keys = (prev_key, new_key)
        with self._lock:
            if self._keys == keys and self._done is not None:
                return self._done
            running = self._thread is not None and self._thread.is_alive()
            if not running:
                self._thread = threading.Thread(target=self._run, args=(keys,), name="changes", daemon=True)
                self._thread.start()
        return {"state": "computing", "words": "計算中", "keys": list(map(str, keys))}

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def night_of_text(value: str | None) -> str:
    """The night a library was swapped in (nightly.night_of: the Taipei day twelve hours before), '' for none."""
    at = when_of(value)
    return (at - timedelta(hours=12)).date().isoformat() if at else ""


def when_text(value: str | None) -> str:
    """A library's time as the page writes it (11-17 00:42), '' for none."""
    at = when_of(value)
    return at.strftime("%m-%d %H:%M") if at else ""
