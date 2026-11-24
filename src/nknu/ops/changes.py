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
     分类 is 学校那边已经不存在 (build.SCHOOL_GONE, as the build's swap guard reads it), another size of the same Wix
     picture kept by rule when the new library holds that picture (build's same_image, 11-18), else ours (结果 says why)
  6. no row for it: when the site's export changed, the crawler's new export does not list it; when it did not, the
     input was the same and the build changed (its program or its settings)
  7. else 查不出原因: the export lists it, the crawler got it, and the build left it out
A document that 5 or 6 found no file or row for, whose whole text is still in a document of its site in the new
library (the same Google Sites picture at the address the export writes now, 11-19), left for that reason instead; so
does a picture whose row has a file and whose words were read into the page that shows it (from 10 characters).
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
import re
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from nknu.ops.names import rename
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
# 11-19: a page whose content is its button rows' (a course outline several teachers give shows only the teachers' menu;
# each choice is a row of its own). The export says so in 缺什么 and the build leaves the page out (corpus.IN_BUTTONS)
try:
    from nknu.content.corpus import IN_BUTTONS
except ImportError:
    IN_BUTTONS = "内容在按钮行"
# the export's 分类 of a row with no file because the school took the address away: the build's swap guard counts it
# as the school's (nknu.content.build._departures), and so does this comparison (review 2026-11-17)
try:
    from nknu.content.build import SCHOOL_GONE
except ImportError:
    SCHOOL_GONE = "学校那边已经不存在"
# a Wix picture's other sizes: the export keeps one and lists the rest without a file (分类 规则不抓); a document of such
# a row left for a reason when the new library holds a document of the same picture (nknu.content.build, 11-18)
try:
    from nknu.content.build import RULE_DECLINED
except ImportError:
    RULE_DECLINED = "规则不抓"
WIX_PICTURE = re.compile(r"^(?:https?:)?//static\.wixstatic\.com/media/([^/?#]+)")
# 11-23: an album's photos (原因代码 album) leave the library whatever other row of the export gives a file at their
# address, unless a page links them or it is an honor roll's (nknu.content.build.DECLINED_OUT, KEEP_DECLINED,
# _leave_declined); the build counts that leaving as explained, and so does this, for any row at that address
try:
    from nknu.content.build import DECLINED_OUT, KEEP_DECLINED
except ImportError:
    DECLINED_OUT = {"album": ("(相簿不收)", "decided_album")}
    KEEP_DECLINED = ("/honorroll/",)
# 11-24: the build knows a document by its identity (nknu.content.ids.location_key: http and https are one address, a
# c.nknu notice's newsview and newsdetail of one nid are one notice), reads it from the row of that identity it fetched
# last (documents.one_per_id), and declines an album's picture by its address without the scheme (build._address); so
# does this. Without it the night of 11-24 said 查不出原因 of 8 notices the school moved (their newsview row said so,
# the newsdetail one did not) and of an album picture at http:// whose declined row is at https://
try:
    from nknu.content import ids as _ids
    from nknu.content.build import _address
    from nknu.content.documents import fetched_at
except ImportError:
    _ids = None

    def _address(url: str | None) -> str:
        return re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", (url or "").strip(), count=1)

    def fetched_at(stamp: str) -> float:
        return 0.0
# a kind decided out of the library (11-18 night: teachers' publications in full) leaves for a reason: the export names
# the rule in 原因代码 and the build counts the leaving as explained (nknu.content.build.DECIDED_OUT); so does this
try:
    from nknu.content.build import DECIDED_OUT
except ImportError:
    DECIDED_OUT = {"out:skip_path:teacher_fulltext": "decided_teacher_fulltext",
                   "out:skip_path:proceedings": "decided_proceedings"}
# the kind, said after the reason's name; a kind the build adds and no line here knows is said in the build's own
# words for it (LEFT_WORDS, made Traditional), so a new kind needs no change here
DECIDED_WORDS = {"decided_teacher_fulltext": "教師著作全文", "decided_proceedings": "研討會論文集全文",
                 "decided_teaching_materials": "中小學教材下載區", "decided_album": "相簿照片"}


def decided_words(kind: str) -> str:
    if kind in DECIDED_WORDS:
        return DECIDED_WORDS[kind]
    try:
        from nknu.content.build import LEFT_WORDS
    except ImportError:
        return kind
    return tw(str(LEFT_WORDS.get(kind) or kind)).removeprefix("定了不收：")


CALENDAR_SINCE = date(2023, 1, 1)  # documents.CALENDAR_SINCE: the library holds events from this day on
# 11-19: a document whose address the new export does not list or whose row has no file, while every word of it is in
# a document of the same site of the new library: the same picture at the address the export now writes for it (Google
# Sites gives a picture another address each time a page is read), or a picture now read into its page. The build
# counts it as explained (nknu.content.build._moved_words, its 'moved'); so does this. Whole texts of MOVED_MIN
# characters or more only, so that a few common words are no proof
try:
    from nknu.content.build import MOVED_MIN, _moved_words
except ImportError:
    MOVED_MIN, _moved_words = 20, None
MOVED_FROM = ("not_exported", "not_fetched", "same_export")  # the reasons the words still being there replace
# a picture whose row has a file and that is no document of its own any more (its words read into the page that shows
# it, 11-19 afternoon): its words are looked for too, down to the least a picture was a document with (the build's),
# and only in the pages its row says link to it (the build's _linked_from)
try:
    from nknu.content.build import PICTURE_MOVED_MIN, PICTURE_ROW, _linked_from
except ImportError:
    PICTURE_MOVED_MIN, PICTURE_ROW, _linked_from = 10, "图片", None

# key: (the short name the page shows, the sentence its floating card holds, its kind, who fixes it when it is ours)
REASONS = {
    "gone": ("學校刪了", "爬蟲隔一天以上看了兩次都說不存在，導出標了「學校已刪除」，建庫照標記不收", OK_KIND, ""),
    "moved": ("學校轉到別頁", "這則公告的網址被學校轉到別的頁（多半是刪了），導出標了，建庫照標記不收", OK_KIND, ""),
    "no_data": ("學校回答沒資料", "學校這一頁回答「沒有資料」，建庫照標記不收", OK_KIND, ""),
    "shell": ("只剩空殼的公告", "學校把公告的內容拿掉了，只剩網頁的框，建庫不收", OK_KIND, ""),
    "gone_file": ("跟著刪掉的附件", "連到它的網頁都被學校刪了，附件一起不收", OK_KIND, ""),
    "school_gone": ("學校那邊已經不存在", "爬蟲這一次去抓，學校那邊已經沒有這個網址了（錯誤頁、跳回首頁），導出沒有檔，建庫不收",
                    OK_KIND, ""),
    "same_image": ("同一張圖留了別的尺寸", "Wix 網站同一張圖有好幾個尺寸，導出只留一個；新庫裡有這張圖的另一份", OK_KIND, ""),
    "decided_out": ("定了不收", "這一類資料決定不收，名單寫明不抓，導出不給檔，建庫不收；是哪一類寫在後面", OK_KIND, ""),
    "menu_only": ("只有選單的頁", "這一頁只有「請先選擇教師」這類選單，內容在按了以後的各頁（另外收了），建庫不收這一頁", OK_KIND, ""),
    "pager": ("公告列表的分頁", "列表翻頁的網頁不進庫，每一則公告另外收了", OK_KIND, ""),
    "copy": ("併成副本", "別處有一模一樣的一份，合成一份；留下的那份見它的編號", OK_KIND, ""),
    "format": ("其他格式", "同一個檔的另一種格式（例如 PDF 和 Word），只留一份", OK_KIND, ""),
    "duplicate": ("同一網址重複", "同一個網址導出了兩次，只留一份", OK_KIND, ""),
    "renumbered": ("換了編號", "同一個網址還在新庫裡，只是文件的編號換了", OK_KIND, ""),
    "words_kept": ("字都還在庫裡", "這一份的字全都還在新庫同一個網站的文件裡：換了網址（Google 協作平台的圖每次讀網頁都換）或併進了網頁",
                   OK_KIND, ""),
    "series": ("併進行事曆系列", "每週重複的行事曆合成一個系列，這一次是其中一次", OK_KIND, ""),
    "calendar_gone": ("行事曆上沒有了", "學校的行事曆這一次沒有這一筆（學校改了行事曆）", OK_KIND, ""),
    "before_2023": ("2023 年以前的行事曆", "庫只收 2023 年起的行事曆", OK_KIND, ""),
    "not_fetched": ("這次沒抓到", "導出清單裡有它，可是這一次沒有存下檔案（爬蟲的結果寫在後面）", LOOK, CRAWLER),
    "not_exported": ("導出沒列它", "這個網站新的導出清單裡沒有這個網址", LOOK, CRAWLER),
    "site_gone": ("網站不在新庫", "這個網站整個不在新庫的網站表裡", LOOK, CONTENT),
    "same_export": ("導出沒換", "這個網站用的導出和上一份庫一樣，離開是建庫的程式或設定改了", LOOK, CONTENT),
    "unknown": ("查不出原因", "導出清單裡有它、也抓到了，卻沒有進新庫", UNKNOWN, CONTENT),
}
# the short names in the 11-23 words (names.py, owner 11-23: 「名字取的太弱…」): 校方已刪除, 併為副本, 原因不明 …; the keys stay,
# so a page or a drawer that filters by a reason's key never depends on its word
REASONS = {k: (rename(label), tip, kind, fix) for k, (label, tip, kind, fix) in REASONS.items()}
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
    doc_id}, 'pictures': the Wix pictures it holds a document of, any size}. The text is hashed, never kept."""
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
    return {"docs": docs, "meta": meta, "sites": sites, "aliases": aliases, "urls": urls, "pictures": pictures_of(urls)}


def pictures_of(urls) -> set[str]:
    """The Wix pictures among addresses, by the name every size of one shares."""
    return {m.group(1) for u in urls if (m := WIX_PICTURE.match(u))}


# ------------------------------------------------------------------------------------------------ the exports

class Manifests:
    """The export folders' rows by address, read once a folder (an export folder is written once)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._rows: dict[str, dict | None] = {}
        self._declined: dict[str, dict[str, str]] = {}
        self._idents: dict[str, dict[str, list[dict]]] = {}
        self._calendars: dict[str, dict | None] = {}

    def rows(self, written: str) -> dict | None:
        """{address: row} of the folder sites.export writes (both 网址 and 最后落地网址, and the address without
        &amp; or a #fragment), None when its manifest cannot be read."""
        if written in self._rows:
            return self._rows[written]
        out: dict | None = None
        declined: dict[str, str] = {}
        idents: dict[str, list[dict]] = {}
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
                if row.get("分类") == RULE_DECLINED and (code := str(row.get("原因代码") or "")) in DECLINED_OUT:
                    for k in _forms(str(row.get("网址") or "")):
                        declined[_address(k)] = code
                if _ids is not None and (address := str(row.get("网址") or "")):
                    idents.setdefault(_ids.location_key(address), []).append(row)
        except (OSError, ValueError):
            out = None
        self._rows[written] = out
        self._declined[written] = declined
        self._idents[written] = idents
        return out

    def declined(self, written: str, url: str) -> str:
        """The DECLINED_OUT code the folder's rows decline url by (any of its rows, not only the first; http and https
        alike, as build._address compares), else ''."""
        self.rows(written)
        by = self._declined.get(written) or {}
        return next((by[a] for a in map(_address, _forms(url)) if a in by), "")

    def row_kept(self, written: str, url: str) -> dict | None:
        """The row the build reads url's document from: of the rows whose 网址 is of url's identity
        (ids.location_key), the one documents.one_per_id keeps (written without &amp;, then fetched last), url's own
        row first on a tie; url's own row when its identity has no row. None: no row, or no readable manifest."""
        rows = self.rows(written)
        if rows is None:
            return None
        own = _row_of(rows, url)
        group = (self._idents.get(written) or {}).get(_ids.location_key(url)) if _ids is not None and url else None
        if not group:
            return own
        candidates = group if own is None or any(r is own for r in group) else [*group, own]
        return min(candidates, key=lambda r: ("&amp;" in str(r.get("网址") or ""),
                                              -fetched_at(str(r.get("抓取时间") or "")), r is not own))

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
    code = manifests.declined(after["export"], url)
    if code and not any(p in (url or "").lower() for p in KEEP_DECLINED):
        return "decided_out", decided_words(DECLINED_OUT[code][1])
    row = manifests.row_kept(after["export"], url)
    if row is None:
        return ("same_export", "") if same_folder else ("not_exported", "")
    lacks = str(row.get("缺什么") or "")
    if GONE in lacks:
        return "gone", ""
    if MOVED in lacks:
        return "moved", ""
    if IN_BUTTONS in lacks:
        return "menu_only", ""
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
        picture = WIX_PICTURE.match(url or "")
        if picture and str(row.get("分类") or "") == RULE_DECLINED:
            held = new["pictures"] if "pictures" in new else pictures_of(new["urls"])
            if picture.group(1) in held:
                return "same_image", ""
        if str(row.get("分类") or "") == RULE_DECLINED and (kind := DECIDED_OUT.get(str(row.get("原因代码") or ""))):
            return "decided_out", decided_words(kind)
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


def moved_least(key: str, fp: tuple, new: dict, manifests: Manifests) -> tuple:
    """How a document that left for key may be found with its words still in the new library: (MOVED_MIN,) for a
    reason MOVED_FROM, anywhere in its site; (PICTURE_MOVED_MIN, pages) for a picture whose export row has a file (查
    不出原因, or 導出沒換), in the pages its row says link to it only; () when its words explain nothing."""
    site, _kind, _title, url = fp[:4]
    after = new["sites"].get(site)
    rows = manifests.rows(after["export"]) if after else None
    row = _row_of(rows, url) if rows else None
    if key in ("unknown", "same_export") and row and row.get("本地档案") and row.get("类型") == PICTURE_ROW:
        return (PICTURE_MOVED_MIN, _linked_from(row)) if _linked_from is not None else ()
    return (MOVED_MIN,) if key in MOVED_FROM else ()


def words_kept(maybe: list[tuple], pd: dict, nd: dict, dbs: tuple[Path, Path] | None) -> set[str]:
    """The doc_ids of maybe ((doc_id, site, least[, pages]) of prev's documents) whose words are all in a document of
    the same site of new: the same text (the fingerprints' digest; not for one looked for in given pages only), else,
    when both libraries' files are given and the build is importable, inside one (the build's own _moved_words)."""
    if not maybe:
        return set()
    held = {(fp[0], fp[6]) for fp in nd.values()}
    found = {m[0] for m in maybe if len(m) == 3 and (m[1], pd[m[0]][6]) in held}
    rest = [m for m in maybe if m[0] not in found]
    if rest and dbs and _moved_words is not None:
        found |= _moved_words(dbs[0], dbs[1], rest)
    return found


def compare(prev: dict, new: dict, root: Path, hold: set[str] | None = None,
            dbs: tuple[Path, Path] | None = None) -> dict:
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
    left = {doc_id: why_left(doc_id, fp, new, prev, manifests) for doc_id, fp in pd.items() if doc_id not in nd}
    maybe = [(doc_id, pd[doc_id][0], *how) for doc_id, (key, _note) in left.items()
             if (how := moved_least(key, pd[doc_id], new, manifests)) and pd[doc_id][5] >= how[0]]
    for doc_id in words_kept(maybe, pd, nd, dbs):
        left[doc_id] = ("words_kept", "")
    for doc_id, (key, note) in left.items():
        fp = pd[doc_id]
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
            "reasons": reason_dicts()}


def reason_dicts() -> dict[str, dict]:
    """REASONS as the pages read them ({key: {label, tip, kind, fix}}), in today's words."""
    return {k: {"label": v[0], "tip": v[1], "kind": v[2], "fix": v[3]} for k, v in REASONS.items()}


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

    def __init__(self, new_db: Path, root: Path, *, prev_db: Path | None = None, hold: Path | None = None,
                 kept: object | None = None) -> None:
        self.new_db = new_db
        self.prev_db = prev_db or new_db.with_name(f"{new_db.stem}.prev{new_db.suffix}")
        self.root = root
        self.hold = hold or new_db.with_name("ingest-hold.txt")
        self._lock = threading.Lock()
        self._done: dict | None = None
        self._keys: tuple | None = None
        self._thread: threading.Thread | None = None
        self._libs: dict[tuple, dict] = {}  # file key -> read_library (at most three kept)
        self.kept = kept  # nknu.ops.keep.Kept: the comparison of the same two files survives a restart (2026-11-22)

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
            result = compare(prev, new, self.root, read_hold(self.hold), (self.prev_db, self.new_db))
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
        if self.kept is not None and result.get("state") == "ready":
            self.kept.put("changes", keys, result)

    def snapshot(self) -> dict:
        prev_key, new_key = file_key(self.prev_db), file_key(self.new_db)
        if new_key is None:
            return {"state": "none", "words": "沒有正式庫"}
        if prev_key is None:
            return {"state": "none", "words": "沒有前一份庫可以比（aca.prev.db 不在：每晚進庫換過一次庫才會有）"}
        if prev_key[:2] == new_key[:2]:
            return {"state": "same", "words": "前一份庫和現在用的是同一份（還沒換上新庫：每晚進庫在建庫前先留一份，換上以後才有得比）"}
        keys = (prev_key, new_key)
        if self._done is None and self.kept is not None:  # after a restart: the same two files, the same comparison
            got = self.kept.get("changes", keys)
            if isinstance(got, dict) and got.get("state") == "ready":
                with self._lock:
                    self._done, self._keys = got, keys
        with self._lock:
            if self._keys == keys and self._done is not None:
                if self._done.get("state") == "ready":  # a result kept before a restart says today's words (11-23)
                    return {**self._done, "reasons": reason_dicts()}
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
