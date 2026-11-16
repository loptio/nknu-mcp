"""The response an agent reads (plan Task D; spec decision 6): today and what is collected, then per hit an identity
line put together by the program, every date with its source, a status only from written periods, and the school's
own text: whole when it fits, else the hit's sections with a table of contents, or a summary of the best chunk.
The 全文 places go to the first results with text; a short hit section comes with the whole sections around it, and
a part cut out of a long one is marked 「…」 and said to be a part. Copies of a document at other addresses are listed
under it, never shown twice (review 11-01). Every string is a template or the school's text (no model-written
sentence); whitespace is tidied, never words. A response stays within RESPONSE_BUDGET_BYTES of compact JSON: search
gives up content in a fixed order (_fit), read pages through long sections (part 'N:offset').

Plan 2026-11-02 T9 and its addendum: the ranking note says when vectors and the reranker shaped the order, the
coverage line names OCR for a to-text build; a summary is the stretch of the best chunk with the most query words
and says how to read on (more, and part with heading for a long text: E); each block of 章節 opens with a 〔第 N
節〕 line and parts lists the sections shown (E); a summary, window or read page that starts inside a table shows
the table's header row above it (H2); read lists the pages that reference a file from their text (D, H3); format
copies are told apart from address copies (I5). Addendum M2 (owner 11-08): a background result (an archive member,
journal or exam, ranked after the primary documents; since 11-08 10:16 a file of meeting minutes too) carries
background: true, and NOTE_BACKGROUND says what it is.

No series (owner 11-03, 「可以，这样砍」; plan addendum L): each result is one document, no answer has a series field,
a folded list, a series part in its identity line or a series note, and read takes an id that starts with s (a series
id of an older build) as an unknown id. A file-name date (檔名) that a build before then wrote is never shown.

Stable ids (plan 2026-11-08 T1): each copy a result lists carries its own id, and read of an id the build left out
under another document (doc_alias: a copy, another format, a duplicate) answers with the kept document, NOTE_ALIAS
first. NOTE_COPIES and NOTE_FORMATS say so; copies without ids (a build before) keep the notes of then (*_NO_ID).

Dates per site (plan 2026-11-08 T3): a page, and a notice no date field dates (another platform's detail page), have
the date the export read on the page, shown 「發布 YYYY-MM-DD（網頁上寫的日期）」 by its dates row's source (a page's
without a row too: _date_part); an attachment of such a notice shows 「所屬公告發布 YYYY-MM-DD（網頁上寫的日期）」.

Many sites (plan 2026-11-08 T4): a database with rows in its sites table (one per export) answers with texts that
name no unit: the coverage line (COVERAGE_SITES*) gives the number of sites, what is collected (行事曆 only when there
are events), the span of their 資料截至 and the summed missing counts, and says each result carries its own site's
data_as_of, which each result and read then do (no site list in the head: 111 names would take the budget); the
notes say 已收錄的網站 for 教務處. A database without sites rows (the skeleton, the 11-04 build, a hand-made one)
answers with the texts of before, the head's data_as_of keyed by 教務處.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import unquote, urlsplit

from nknu.content.chunks import table_header
from nknu.content.dates import FIELD_SOURCE, INHERITED_SOURCE, PAGE_SOURCE
from nknu.content.documents import series_days
from nknu.content.numbering import strip_decor
from nknu.core.textnorm import norm_text
from nknu.retrieval.types import DocHit, SearchResult

# design 2026-11-16-mcp-interface-design §二 (第四版; the owner 11-17 「可以」 to the levels, the 500 and the 5 × 3,000
# his own): how much of each result's text a search answer carries. snippet: every result a 片段 of the best chunk;
# passage: every result a 段落 around it, reaching past the chunk; full: the first FULL_PLACES results with text
# 全文 or 章節 as before (FULL_CAP_CHARS), the rest 片段. The 10-31 「前三个直接给全文」 is the full level's, and no
# longer the default (owner 11-17: 「之前所以定过的东西都是为了这个项目更好的结果，不要那么死板」)
DETAILS = ("snippet", "passage", "full")
DEFAULT_DETAIL = "passage"
SNIPPET_CHARS = 200
PASSAGE_CHARS = 500
FULL_PLACES = 5
# about 20,000 tokens (design §二 limit: the full level's five places fit, under Claude Code's 25,000-token default);
# counted in bytes of compact JSON as before (36,000 until 11-17) 【Claude】
RESPONSE_BUDGET_BYTES = 50_000
READ_BUDGET_BYTES = 36_000  # fetch keeps the budget of before: one read page is at most READ_CAP_CHARS
FULL_CAP_CHARS = 3_000
CONTEXT_CHARS = FULL_CAP_CHARS // 3  # hit sections shorter than this get their neighbours (review 11-01)
# a result shown as a 片段 carries only these of its header (design §二: 「只给片段的那几笔，附带的资料精简」); the rest
# (dates, applies_to, parent, copies) is in fetch
LEAN_KEYS = ("id", "background", "identity", "title", "url", "data_as_of", "status")
COPIES_MAX = 5  # copies a search result lists (read lists them all); the budget shrinks it to 2, then 0
READ_CAP_CHARS = 10_000
TOC_MAX = 60
HEADING_MAX = 40
QUERY_ECHO_MAX = 300  # the server refuses longer queries; search reads only this much
HEADER_CHARS = 150  # an attachment's title block, where its own date usually is (plan 11-01 later change)
MIN_PAGE_CHARS = 500  # read never shrinks a page below this to fit the budget
PARTIAL_CHARS = 600  # search shows at most this much of the few words a scanned PDF gave (read shows them all)
PARTIAL_KINDS = ("附件:pdf:",)  # kind_raw prefixes whose 無文字 text is a PDF text layer's few words, not binary junk

UNIT_NAME = "教務處"  # the one site a database without sites rows holds (the export's own name is written 教务处)
WEEKDAYS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")
DATE_LABELS = {"published": "發布", "inherited": "所屬公告發布", "server": "伺服器檔案時間",
               "period_start": "期間開始", "period_end": "期間結束", "event_start": "開始", "event_end": "結束",
               "issued": "發文", "revised": "修訂", "form_version": "改版"}  # the last three: builds before 11-01
_KIND_ORDER = {k: i for i, k in enumerate(DATE_LABELS)}  # identity kinds first, in IDENTITY_ORDER
_SOURCES = {"published": FIELD_SOURCE, "issued": "發文日期", "revised": "沿革", "form_version": "表單改版",
            "inherited": INHERITED_SOURCE, "server": "伺服器檔案時間"}  # ident_date without its row; a page's published
#   date is the date the export read on it (plan 2026-11-08 T3: 「發布 2025-09-17（網頁上寫的日期）」, dates.PAGE_SOURCE)
#   【Claude】
KIND_LABELS = {"notice": "公告", "page": "網頁", "attachment": "附件", "event": "行事曆", "link": "外部連結"}
_PAGE_EXT = frozenset({"aspx", "ashx", "asp", "php", "htm", "html", "jsp"})

COVERAGE = ("目前只收高師大教務處網站（c.nknu.edu.tw/aca）的公開網頁、公告附件與教務處行事曆（2023 年起），"
            "資料截至 {data_as_of}；另有 {missing_pages} 個網頁、{missing_files} 個附件沒抓到；"
            "掃描檔還沒轉成文字，只能依標題找到。其他單位與系所網站尚未收錄。")
# a to-text build (meta text_pipeline 'totext', plan 2026-11-02 T7a); COVERAGE stays true for a skeleton build
COVERAGE_OCR = ("目前只收高師大教務處網站（c.nknu.edu.tw/aca）的公開網頁、公告附件（含壓縮檔裡的文件）、網頁上的圖片與"
                "教務處行事曆（2023 年起），資料截至 {data_as_of}；"
                "另有 {missing_pages} 個網頁、{missing_files} 個附件沒抓到；"
                "掃描檔和圖片的文字是程式辨識（OCR）的，可能有錯字；無法轉成文字的檔案只能依標題找到。"
                "其他單位與系所網站尚未收錄。")
NOTE_RANKING = ("【系統】結果依關鍵字比對排序；問題沒有指定學期時，日期較近的文件稍微排前面。"
                "只含教務處公開資料。若下列原文都不能回答問題，"
                "請告訴使用者：目前收錄的資料裡查無相關內容，不要推測。")
# SearchResult.ranking 'hybrid' (plan 2026-11-02 T8): dense hits or rerank scores shaped the order
NOTE_RANKING_HYBRID = ("【系統】結果依關鍵字比對和語意相近程度排序（排序模型只計分、不寫字）；"
                       "問題沒有指定學期時，日期較近的文件稍微排前面。只含教務處公開資料。"
                       "若下列原文都不能回答問題，請換幾個說法再查；"
                       "仍然沒有，請告訴使用者：目前收錄的資料裡查無相關內容，不要推測。")
# addendum M2 (owner 11-08): background material comes after the primary documents (search.py step 3); 教务处's
# old 校刊 and 校訊 named since owner 11-09 (blueprint item 17, 「照建议定」: build._PAPER_DIRS)
# spec 2026-11-16 §四: no longer 「排在主要文件後面；需要時再看」, a background document now stands where its score puts it
# (search.mixed)
NOTE_BACKGROUND = "【系統】background 為 true 的是背景資料（會議紀錄、壓縮檔裡的文件、學報、舊校刊和校訊、歷屆考題）。"
# plan 2026-11-08 T1: each copy carries its own id, which read answers with the kept document (NOTE_ALIAS). Plan
# 2026-11-14 T2: the build merges documents of one text whatever their titles, so the note says the content is the same
# (the plan's 「內容相同」) and no longer the title; that a title may differ is said too 【Claude】. NOTE_COPIES_NO_ID,
# of a build before T1 (one title and text then), stays as it was
NOTE_COPIES = ("【系統】copies 是同一份文件（內容相同，標題可能不同）在別的網址的副本，列出各自的 id、網址、所屬網頁和日期；"
               "用 fetch 讀副本的 id 會得到這一筆。內容和該筆相同。")
# The owner, 11-14 23:55 (nknu.content.build's platform twins: 「公告平台和单位网站各一份」 merged): a copy of a notice whose
# words are not the kept one's to the letter says wording_differs; what that means, said once per response
NOTE_WORDING = ("【系統】copies 中 wording_differs 為 true 的是同一則公告在公告平台或單位網站上的另一份：它的每一行都在該筆裡，"
                "排版、標點可能不同，也可能比該筆少幾行。")
# I5: format siblings (the doc and odt beside a pdf on one row, T7a) have their own id, url and format, no date of
# their own
NOTE_FORMATS = ("【系統】copies 中有 format 的是同一份文件的其他格式：各有自己的 id、網址和格式，沒有自己的日期；"
                "內容以該筆為準。")
# the notes of a build before plan 2026-11-08 T1, whose copies have no id (nor doc_alias): as they were
NOTE_COPIES_NO_ID = ("【系統】copies 是同一份文件（標題和內容都相同）在別的網址的副本，只列網址、所屬網頁和日期；"
                     "內容和該筆相同。")
NOTE_FORMATS_NO_ID = ("【系統】copies 中有 format 的是同一份文件的其他格式：各有自己的網址和格式，沒有自己的日期；"
                      "內容以該筆為準。")
NOTE_SECTIONS = ("【系統】長文件只給命中的章節和目錄；〔第 N 節〕是程式加的節號標記，N 就是 toc 的 part；"
                 "用 fetch 讀其他章節或全文。")
NOTE_DATES = ("【系統】每個日期後面括號是它的來源。附件的日期是所屬網頁的日期或伺服器檔案時間，"
              "不一定是文件本身的修訂日；文件自己寫的日期請看原文（開頭或沿革）。")
NOTE_NOT_FOUND = "【系統】查無相關資料：目前收錄的教務處資料裡沒有符合的內容，請勿推測。"
# a merged recurring event's status (_status): its first and last day, how many times, then where today falls
SERIES_STATUS = "行事曆寫的日期 {first}～{last}（重複事件，共 {n} 次）：{phase}"
# plan 2026-11-08 T4: the texts of a database whose sites table has rows (many sites): no unit named, no site list
COVERAGE_SITES = ("目前收錄高師大 {sites} 個網站的公開網頁、公告、附件{calendar}，資料截至 {span}，"
                  "每筆結果附所屬網站的資料截至日期（data_as_of）；另有 {missing_pages} 個網頁、{missing_files} 個附件沒抓到；"
                  "掃描檔還沒轉成文字，只能依標題找到。其他網站尚未收錄。")
COVERAGE_SITES_OCR = ("目前收錄高師大 {sites} 個網站的公開網頁、公告附件（含壓縮檔裡的文件）、網頁上的圖片{calendar}，"
                      "資料截至 {span}，每筆結果附所屬網站的資料截至日期（data_as_of）；"
                      "另有 {missing_pages} 個網頁、{missing_files} 個附件沒抓到；"
                      "掃描檔和圖片的文字是程式辨識（OCR）的，可能有錯字；無法轉成文字的檔案只能依標題找到。"
                      "其他網站尚未收錄。")
CALENDAR_PART = "與行事曆（2023 年起）"  # documents.CALENDAR_SINCE; only when the database holds events
NOTE_RANKING_SITES = ("【系統】結果依關鍵字比對排序；問題沒有指定學期時，日期較近的文件稍微排前面。"
                      "只含已收錄的網站上的公開資料。若下列原文都不能回答問題，"
                      "請告訴使用者：目前收錄的資料裡查無相關內容，不要推測。")
NOTE_RANKING_HYBRID_SITES = ("【系統】結果依關鍵字比對和語意相近程度排序（排序模型只計分、不寫字）；"
                             "問題沒有指定學期時，日期較近的文件稍微排前面。只含已收錄的網站上的公開資料。"
                             "若下列原文都不能回答問題，請換幾個說法再查；"
                             "仍然沒有，請告訴使用者：目前收錄的資料裡查無相關內容，不要推測。")
NOTE_NOT_FOUND_SITES = "【系統】查無相關資料：已收錄的網站的資料裡沒有符合的內容，請勿推測。"
NOTE_DOWNGRADED = "【系統】回應太長，後面幾筆只給片段；需要時照各筆 more 用 fetch 讀。"
NOTE_REFS = ("【系統】referenced_by 是引用此檔的頁面（在正文或附件區連到它的網頁和公告），由新到舊；"
             "只出現在選單的連結不列。")
NOTE_UNKNOWN_ID = "【系統】查無此 id"
# plan 2026-11-08 T1: read of an id doc_alias keeps (schema.ALIAS_WHYS), answered with the document kept for it
NOTE_ALIAS = "【系統】這個編號是 {kept} 的{why_label}，以下是保留的那份。"
ALIAS_LABELS = {"copy": "副本", "format": "其他格式", "duplicate": "重複"}
NOTE_BAD_PART = "【系統】沒有這一節；請從 toc 選節號。"
NOTE_TOC_CUT = "【系統】目錄只列前 {shown} 節（共 {total} 節）；節號依序遞增，可直接用 fetch 的 part=節號 讀其他節。"
NO_TEXT = "這份是掃描檔或無法轉成文字的檔案，目前只能依標題找到；內容請開原檔網址查看。"
NO_TEXT_LINK = "這是外部網站的連結，內容沒有收錄；請開網址查看。"
PARTIAL = "（只轉出少量文字：{text}）"
PARTIAL_MORE = '轉出的文字共 {chars} 字；用 fetch 讀全部：fetch(id="{id}")'
MORE = '全文共 {chars} 字；用 fetch 讀其他章節：fetch(id="{id}", part="節號")'
MORE_PART = '第 {sec} 節共 {chars} 字，這裡只給其中命中的一段（「…」處有截斷）；讀整節：fetch(id="{id}", part="{sec}")；'
MORE_AROUND = "這裡只給命中處前後約 {cap} 字（「…」處有截斷）；"
NEXT = '接著讀：fetch(id="{id}", part="{next}")'
MORE_PAGE = "第 {sec} 節共 {chars} 字，這裡到第 {upto} 字；" + NEXT
# a summary's way on (addendum E): the whole text when read gives it in one page, else the hit's section and the toc
SUMMARY_MORE = '全文共 {chars} 字；用 fetch 讀全文：fetch(id="{id}")'
SUMMARY_MORE_PART = ('這段在第 {sec} 節（全文共 {chars} 字）；讀這一節：fetch(id="{id}", part="{part}")；'
                     '看目錄：fetch(id="{id}", part="toc")')
SUMMARY_MORE_START = '全文共 {chars} 字；用 fetch 讀：fetch(id="{id}")'  # nothing matched in a long text
SECTION_MARK = "〔第 {sec} 節〕"  # opens a block of 章節 (addendum E); N is the toc's part
SECTION_MARK_HEADING = "〔第 {sec} 節：{heading}〕"  # ... when the block does not open with that section's heading
ARCHIVE_MEMBER = "附件（{archive} 壓縮檔內的 {member}）"  # an archive member: <archive url>#<its path> (T7a)
REF_ZONES = ("content", "attachments", "unknown")  # doc_refs zones that reference a file (H3); 'nav' is a menu
GAP = "\n…\n"  # between sections that are not next to each other

_BREAKS = frozenset("。！？；\n")
_TRAILING_SPACE = re.compile(r"[^\S\n]+\n")
_BLANK_RUN = re.compile(r"\n{3,}")
_EXT = re.compile(r"\.([A-Za-z0-9]{2,5})$")
_PART = re.compile(r"(\d+)(?::(\d+))?")
_MARK_LINE = re.compile(r"〔第 \d+ 節(?:：[^\n]*)?〕\n")  # a SECTION_MARK line at the start of a content
_DOC_COLS = ("doc_id, kind_class, kind_raw, title, url, unit, grp, parent_id, has_text, period_ay, period_sem,"
             " ident_date, ident_kind")
COPIES_KEY = "copies:"  # meta: the kept document's copies at other addresses (nknu.content.build, review 11-01)


def to_json(resp: dict) -> str:
    return json.dumps(resp, ensure_ascii=False, separators=(",", ":"))


def _size(resp: dict) -> int:
    return len(to_json(resp).encode("utf-8"))


def _compact(d: dict) -> dict:
    """Optional keys are left out when empty (0 and False stay)."""
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v != {}}


def today_block(today: date) -> dict:
    # 學年度 and semester: the same rule as nknu.content.terms.current_term (Aug–Jan semester 1, Feb–Jul semester 2)
    ay, sem = (today.year - 1911, 1) if today.month >= 8 else (today.year - 1912, 1 if today.month == 1 else 2)
    # roc_date as the school writes dates (115年11月3日), so the agent reads a notice's ROC dates against today directly
    return {"date": today.isoformat(), "roc_date": f"{today.year - 1911}年{today.month}月{today.day}日",
            "weekday": WEEKDAYS[today.weekday()], "term": f"{ay}-{sem}"}


# ---------------------------------------------------------------- rows

@dataclass(frozen=True)
class _Date:
    kind: str
    date: str
    raw: str
    source: str


@dataclass(frozen=True)
class _Sec:
    sec_id: int
    heading: str
    start: int
    end: int


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM meta WHERE key NOT LIKE ?", (COPIES_KEY + "%",)).fetchall())


def _copies(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """The document's copies at other addresses, newest first: id (a build since plan 2026-11-08 T1 writes it; read
    resolves it to this document), url, date with its type, parent; then its other formats (T7a's format siblings):
    id, url, format, parent, no date of their own."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (COPIES_KEY + doc_id,)).fetchone()
    out = []
    for c in json.loads(row[0]) if row else []:
        out.append(_compact({"id": c.get("id"), "url": c.get("url"), "format": str(c.get("format") or "").upper(),
                             "date": c.get("date"),
                             "date_type": DATE_LABELS.get(c.get("date_kind") or "") if c.get("date") else None,
                             "wording_differs": bool(c.get("wording_differs")) or None,
                             "parent": _compact({"id": c.get("parent_id"), "title": c.get("parent_title")})}))
    return out


def _copies_part(copies: list[dict], cap: int | None) -> dict:
    shown = copies if cap is None else copies[:cap]
    return _compact({"copies": shown, "copies_total": len(copies) if len(shown) < len(copies) else None})


def _copies_notes(copies: list[dict]) -> list[str]:
    """NOTE_COPIES for the same file at other addresses, NOTE_FORMATS for other formats (I5); their _NO_ID forms when
    a copy lists no id (a build before plan 2026-11-08 T1)."""
    kinds = {"format" in c for c in copies}
    notes = (NOTE_COPIES, NOTE_FORMATS) if all(c.get("id") for c in copies) else (NOTE_COPIES_NO_ID, NOTE_FORMATS_NO_ID)
    out = [note for note, kind in zip(notes, (False, True), strict=True) if kind in kinds]
    return out + [NOTE_WORDING] if any(c.get("wording_differs") for c in copies) else out


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _sites(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """(data_as_of, missing_pages, missing_files) of each row of the sites table (plan 2026-11-08 T4); [] in a database
    without one, or with an empty one: those answer with the texts of before."""
    if not _has_table(conn, "sites"):
        return []
    return conn.execute("SELECT coalesce(data_as_of, ''), coalesce(missing_pages, 0), coalesce(missing_files, 0)"
                        " FROM sites").fetchall()


def _data_as_of(conn: sqlite3.Connection, doc_id: str) -> str | None:
    """The 資料截至 of the site the document came from (docs.site, plan 2026-11-08 T4); None in a database without the
    sites table or when it has no row of that site."""
    if not _has_table(conn, "sites"):
        return None
    row = conn.execute("SELECT s.data_as_of FROM docs d JOIN sites s ON s.site = d.site WHERE d.doc_id = ?",
                       (doc_id,)).fetchone()
    return row[0] if row and row[0] else None


def _refs(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """The pages that reference the document from their text, attachment area or a button (H3: REF_ZONES; doc_refs
    also keeps the menu pages, which reference nothing), newest first, no date last: id, title, url, date with its
    type. [] for a database without doc_refs (built before addendum B)."""
    if not _has_table(conn, "doc_refs"):
        return []
    sql = ("SELECT d.doc_id, d.title, d.url, d.ident_date, d.ident_kind"
           " FROM doc_refs r JOIN docs d ON d.doc_id = r.ref_id"
           f" WHERE r.doc_id = ? AND r.zone IN ({_marks(list(REF_ZONES))})"
           " ORDER BY d.ident_date IS NULL, d.ident_date DESC, d.doc_id")
    return [_compact({"id": i, "title": title, "url": url, "date": day,
                      "date_type": DATE_LABELS.get(kind or "") if day else None})
            for i, title, url, day, kind in conn.execute(sql, (doc_id, *REF_ZONES))]


def _refs_part(refs: list[dict], shown: int) -> dict:
    return _compact({"referenced_by": refs[:shown], "referenced_by_total": len(refs) if shown < len(refs) else None})


def _doc(conn: sqlite3.Connection, doc_id: str, *, text: bool = False) -> sqlite3.Row | None:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(f"SELECT {_DOC_COLS}{', text' if text else ''} FROM docs WHERE doc_id=?", (doc_id,)).fetchone()


def _dates(conn: sqlite3.Connection, doc_id: str) -> list[_Date]:
    """The document's date facts in the build's order, but a file-name date (kind filename, source 檔名), which a build
    before 11-03 wrote: file-name dates went with the series (owner 11-03, addendum L)."""
    sql = "SELECT kind, date, raw, source FROM dates WHERE doc_id=? AND kind != 'filename' ORDER BY rowid"
    return [_Date(*r) for r in conn.execute(sql, (doc_id,))]


def _sections(conn: sqlite3.Connection, doc_id: str, text: str) -> list[_Sec]:
    sql = "SELECT sec_id, heading, start, end FROM sections WHERE doc_id=? ORDER BY sec_id"
    return [_Sec(*r) for r in conn.execute(sql, (doc_id,))] or ([_Sec(0, "", 0, len(text))] if text else [])


def _chunks(conn: sqlite3.Connection, doc_id: str, chunk_ids: list[int]) -> list[tuple[int, int, int]]:
    """(sec_id, start, end) of the hit's own chunks, best first."""
    if not chunk_ids:
        return []
    sql = f"SELECT chunk_id, sec_id, start, end FROM chunks WHERE doc_id=? AND chunk_id IN ({_marks(chunk_ids)})"
    rows = {r[0]: r[1:] for r in conn.execute(sql, (doc_id, *chunk_ids))}
    return [rows[c] for c in chunk_ids if c in rows]


def _marks(values: list) -> str:
    return ",".join("?" * len(values))


# ---------------------------------------------------------------- identity, dates, status

def _identity(doc: sqlite3.Row, dates: list[_Date]) -> str:
    """unit｜組別｜kind｜identity date with its source (no series part since 11-03)."""
    parts = (doc["unit"], doc["grp"], _kind_label(doc), _date_part(doc, dates))
    return "｜".join(p for p in parts if p)


def _format_of(path: str) -> str:
    m = _EXT.search(path)
    return m.group(1).upper() if m and m.group(1).lower() not in _PAGE_EXT else ""


def _kind_label(doc: sqlite3.Row) -> str:
    if doc["kind_class"] != "attachment":
        return KIND_LABELS.get(doc["kind_class"], "")
    parts = urlsplit(doc["url"])
    fmt = _format_of(parts.path)
    member = _format_of(unquote(parts.fragment)) if parts.fragment else ""
    if fmt and member:  # an archive member (T7a): <archive url>#<its path in the archive>
        return ARCHIVE_MEMBER.format(archive=fmt, member=member)
    if not fmt:  # e.g. a Drive API address: the corpus kind says 附件:pdf:…
        sub = doc["kind_raw"].split(":")[1:2]
        fmt = sub[0].upper() if sub and sub[0].isascii() and sub[0].isalpha() else ""
    return f"附件（{fmt}）" if fmt else "附件"


def _date_part(doc: sqlite3.Row, dates: list[_Date]) -> str:
    kind, value = doc["ident_kind"], doc["ident_date"]
    if value:
        fallback = PAGE_SOURCE if (kind, doc["kind_class"]) == ("published", "page") else _SOURCES.get(kind, "")
        source = next((d.source for d in dates if d.kind == kind and d.date == value), fallback)
        label = DATE_LABELS.get(kind, "日期")
        return f"{label} {value}（{source}）" if source else f"{label} {value}"
    # an event has no identity date (IDENTITY_ORDER has no event kinds): its calendar days stand in
    starts = [d for d in dates if d.kind == "event_start"]
    ends = [d for d in dates if d.kind == "event_end"]
    if not (starts or ends):
        return "無日期"
    start = min(d.date for d in starts) if starts else ""
    end = max(d.date for d in ends) if ends else ""
    span = start if end in ("", start) else f"{start}～{end}"
    return f"{span}（{(starts or ends)[0].source}）"


def _date_items(dates: list[_Date]) -> list[dict]:
    seen, out = set(), []
    for d in sorted(dates, key=lambda d: (_KIND_ORDER.get(d.kind, len(_KIND_ORDER)), d.date)):
        if (d.kind, d.date, d.source) not in seen:
            seen.add((d.kind, d.date, d.source))
            out.append(_compact({"type": DATE_LABELS.get(d.kind, d.kind), "date": d.date, "source": d.source,
                                 "raw": d.raw}))
    return out


def _pairs(dates: list[_Date], start_kind: str, end_kind: str) -> list[tuple[str | None, str | None]]:
    """Written periods as (start, end): a start and an end written as one raw text first, then each start with the
    earliest unused end on or after it; what is left stands alone (a lone 截止 is an end)."""
    starts = [d for d in dates if d.kind == start_kind]
    ends = sorted((d for d in dates if d.kind == end_kind), key=lambda d: d.date)
    pairs, rest = [], []
    for s in starts:
        e = next((e for e in ends if s.raw and e.raw == s.raw), None)
        if e is None:
            rest.append(s)
        else:
            ends.remove(e)
            pairs.append((s.date, e.date))
    for s in sorted(rest, key=lambda d: d.date):
        e = next((e for e in ends if e.date >= s.date), None)
        if e is not None:
            ends.remove(e)
        pairs.append((s.date, e.date if e else None))
    pairs += [(None, e.date) for e in ends]
    return sorted(set(pairs), key=lambda p: (p[0] or p[1], p[1] or p[0]))


def _phase(start: str | None, end: str | None, today: str) -> str:
    if start and today < start:
        return "尚未開始"
    if end and today > end:
        return "已結束"
    if start and end:
        return "進行中"
    return "尚未截止" if end else "已開始"


def _span(day: tuple[date, date]) -> str:
    return day[0].isoformat() if day[1] == day[0] else f"{day[0].isoformat()}～{day[1].isoformat()}"


def _series_phase(days: list[tuple[date, date]], today: date) -> str:
    """Where today falls among a recurring event's instances (days: documents.series_days, earliest first)."""
    last = max(days, key=lambda d: d[1])
    if today < days[0][0]:
        return f"尚未開始，第一次 {_span(days[0])}"
    if today > last[1]:
        return f"已結束，最後一次 {_span(last)}"
    coming = next((d for d in days if d[0] > today), None)
    if (now := next((d for d in days if d[0] <= today <= d[1]), None)) is not None:
        return f"今天有一次（{_span(now)}）" + (f"，下一次 {_span(coming)}" if coming else "")
    before = max((d for d in days if d[1] < today), key=lambda d: d[1])
    return f"上一次 {_span(before)}，下一次 {_span(coming)}"


def _status(dates: list[_Date], today: date, series: str = "") -> str:
    """Absolute status only from dates the text or the calendar writes (never from 學年度 or publication).
    series: the text of a calendar event. When it is a merged recurring event's (plan 2026-11-14 T2: one document,
    every instance's days listed in its text), the calendar wrote those days, not one period from the first to the
    last: the status says how many times it occurs and which instance is the last before and the next after today
    (main session 11-14, after the review: a yearly event read 進行中 in the months between two of its days)."""
    t = today.isoformat()
    parts = [f"正文寫的期間 {s or ''}～{e or ''}：{_phase(s, e, t)}"
             for s, e in _pairs(dates, "period_start", "period_end")]
    if days := series_days(series) if series else []:
        last = max(d[1] for d in days)
        parts.append(SERIES_STATUS.format(first=days[0][0].isoformat(), last=last.isoformat(), n=len(days),
                                          phase=_series_phase(days, today)))
    else:
        parts += [f"行事曆寫的日期 {s or ''}～{e or ''}：{_phase(s, e, t)}"
                  for s, e in _pairs(dates, "event_start", "event_end")]
    return "；".join(parts)


def _applies(conn: sqlite3.Connection, doc: sqlite3.Row) -> list[dict]:
    """The 適用於 rows that say something: a cohort, or a unit other than the document's own."""
    sql = "SELECT unit, cohort_from, cohort_exact, raw FROM applies_to WHERE doc_id=? ORDER BY rowid"
    return [_compact({"unit": unit, "cohort_from": c_from, "cohort_exact": c_exact, "raw": raw})
            for unit, c_from, c_exact, raw in conn.execute(sql, (doc["doc_id"],))
            if c_from is not None or c_exact is not None or unit != doc["unit"]]


def _parent(conn: sqlite3.Connection, doc: sqlite3.Row) -> dict | None:
    if not doc["parent_id"]:
        return None
    row = conn.execute("SELECT doc_id, title, url FROM docs WHERE doc_id=?", (doc["parent_id"],)).fetchone()
    return {"id": row[0], "title": row[1], "url": row[2]} if row else None


def _header(conn: sqlite3.Connection, doc: sqlite3.Row, today: date) -> dict:
    """identity, title, url, data_as_of (its site's, plan 2026-11-08 T4: none in a database without sites), dates,
    status, applies_to, parent."""
    dates = _dates(conn, doc["doc_id"])
    return _compact({"identity": _identity(doc, dates), "title": doc["title"], "url": doc["url"],
                     "data_as_of": _data_as_of(conn, doc["doc_id"]), "dates": _date_items(dates),
                     "status": _status(dates, today, _event_text(doc)), "applies_to": _applies(conn, doc),
                     "parent": _parent(conn, doc)})


def _event_text(doc: sqlite3.Row) -> str:
    """A calendar event's text when the row was read with it, else ''."""
    # a sqlite3.Row's `in` looks among its values: its keys are asked for
    return (doc["text"] or "") if doc["kind_class"] == "event" and "text" in doc.keys() else ""  # noqa: SIM118


# ---------------------------------------------------------------- text

def _tidy(s: str) -> str:
    """Whitespace only: no spaces before a line break, at most one blank line in a row, none at either end."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANK_RUN.sub("\n\n", _TRAILING_SPACE.sub("\n", s)).strip()


def _shown_text(doc: sqlite3.Row) -> str | None:
    """The text a reader gets; None for a 無文字 document. An event's text is its title when it has none."""
    text = doc["text"]
    if doc["kind_class"] == "event":
        return text if text.strip() else doc["title"]
    return text if doc["has_text"] and text.strip() else None


def _no_text(doc: sqlite3.Row, limit: int) -> dict:
    """無文字: the template, then the few words a scanned PDF's text layer gave, at most limit characters (cut after
    a line or sentence end and marked 「…」, with more pointing at read). Never 全文: the words are not the document."""
    if doc["kind_class"] == "link":
        return {"content_type": "無文字", "content": NO_TEXT_LINK}
    few = _tidy(doc["text"]) if doc["kind_raw"].startswith(PARTIAL_KINDS) else ""
    if not few:
        return {"content_type": "無文字", "content": NO_TEXT}
    if len(few) <= limit:
        return {"content_type": "無文字", "content": NO_TEXT + PARTIAL.format(text=few)}
    cut = _after_last_break(few, limit // 2, limit) or limit
    return {"content_type": "無文字", "content": NO_TEXT + PARTIAL.format(text=few[:cut].rstrip() + "…"),
            "more": PARTIAL_MORE.format(chars=len(few), id=doc["doc_id"])}


def _token_spans(text: str, lo: int, hi: int, tokens: list[str]) -> list[tuple[int, int, str]]:
    """(start, end, token) of every place a query token stands in text[lo:hi], in text order. The tokens are search's
    (norm_text bigrams: NFKC, lower case, simplified), so the text is folded the same way one character at a time,
    which keeps each place's offsets."""
    want = {t for t in tokens if t}
    if not want or hi <= lo:
        return []
    folded: list[str] = []
    where: list[int] = []
    for i in range(lo, hi):
        for ch in norm_text(text[i]):
            folded.append(ch)
            where.append(i)
    s = "".join(folded)
    out = []
    for t in want:
        k = s.find(t)
        while k >= 0:
            out.append((where[k], where[k + len(t) - 1] + 1, t))
            k = s.find(t, k + 1)
    return sorted(out)


def _focus(text: str, cs: int, ce: int, tokens: list[str], cap: int) -> tuple[int, int] | None:
    """The span of the query tokens in the cap-long stretch of text[cs:ce] that holds the most distinct tokens (then
    the most places, then the earliest); None when no token stands in it (a meaning-only match of the dense path)."""
    spans = _token_spans(text, cs, ce, tokens)
    best: tuple[tuple[int, int], int, int] | None = None
    for i, (a, _, _) in enumerate(spans):
        seen, places, last = set(), 0, a
        for b0, b1, t in spans[i:]:
            if b0 >= a + cap:
                break
            if b1 <= a + cap:
                seen.add(t)
                places += 1
                last = max(last, b1)
        if best is None or (len(seen), places) > best[0]:
            best = ((len(seen), places), a, last)
    return (best[1], best[2]) if best else None


def _after_first_break(text: str, lo: int, hi: int) -> int | None:
    for i in range(max(lo, 0), min(hi, len(text))):
        if text[i] in _BREAKS:
            return i + 1
    return None


def _after_last_break(text: str, lo: int, hi: int) -> int | None:
    for i in range(min(hi, len(text)) - 1, max(lo, 0) - 1, -1):
        if text[i] in _BREAKS:
            return i + 1
    return None


def _window(text: str, cs: int, ce: int, cap: int, lo: int = 0, hi: int | None = None) -> tuple[int, int]:
    """At most cap chars of text[lo:hi] centred on the chunk cs..ce, each end moved to a line or sentence end within
    cap/4 that does not cut into the chunk."""
    hi = len(text) if hi is None else hi
    if hi - lo <= cap:
        return lo, hi
    ws = max(lo, min((cs + ce) // 2 - cap // 2, hi - cap))
    we = ws + cap
    if ws > lo:
        ws = _after_first_break(text, ws - 1, min(cs, ws + cap // 4)) or ws
    if we < hi:
        we = _after_last_break(text, max(ce, we - cap // 4), we) or we
    return ws, we


def _table_lead(text: str, at: int, body: str) -> str | None:
    """H2: shown text that starts on a row below a Markdown table's header row (chunks.table_header, any offset) gets
    that header row (with its separator line) above it, then 「…」 when rows between them are left out. None outside
    a table, on the header row itself, or when the shown body already holds the header row (a table that repeats it)."""
    span = table_header(text, at)
    if span is None:
        return None
    head = _tidy(text[span[0] : span[1]])
    if not head or head.split("\n", 1)[0] in body:
        return None
    return head + ("\n…\n" if text[span[1] : at].strip() else "\n")


def _lead(text: str, at: int, lo: int, body: str) -> str:
    """What goes above text shown from at: a table's header row (_table_lead), else 「…」 when text of lo..at is cut
    off."""
    lead = _table_lead(text, at, body)
    if lead is not None:
        return lead
    return "…\n" if text[lo:at].strip() else ""


def _trail(text: str, end: int, hi: int) -> str:
    return "\n…" if text[end:hi].strip() else ""


def _shown(text: str, ws: int, we: int, lo: int, hi: int) -> str:
    """text[ws:we] tidied, marked 「…」 on each side where text of lo..hi is cut off (whitespace is no text), with a
    table's header row above it when it starts inside a table."""
    body = _tidy(text[ws:we])
    return _lead(text, ws, lo, body) + body + _trail(text, we, hi)


def _page(text: str, start: int, end: int) -> str:
    """A read page: text[start:end] tidied, with a table's header row above it when it starts inside a table (H2);
    next and more say where the section goes on."""
    body = _tidy(text[start:end])
    return (_table_lead(text, start, body) or "") + body


def _page_end(text: str, start: int, end: int, cap: int) -> int:
    """End of a page of at most cap chars from start inside [start, end): after the last line or sentence end in the
    page's second half, else a hard cut."""
    hard = min(end, start + cap)
    if hard >= end:
        return end
    return _after_last_break(text, start + cap // 2, hard) or hard


def _first_line(s: str) -> str:
    return next((line.strip() for line in s.splitlines() if line.strip()), "")


def _toc(text: str, sections: list[_Sec], anchor: int | None = None, limit: int | None = None) -> list[dict]:
    """Table of contents (part = sec_id); over limit, the limit entries around the anchor section (default: from the
    start). A section without a heading of its own shows its first line."""
    secs = sections
    if limit is not None and len(secs) > limit:
        i = next((k for k, s in enumerate(secs) if s.sec_id == anchor), 0)
        lo = max(0, min(i - limit // 2, len(secs) - limit))
        secs = secs[lo : lo + limit]
    return [_compact({"part": str(s.sec_id), "heading": _heading(text, s)}) for s in secs]


def _grow(sections: list[_Sec], chosen: dict[int, _Sec], best: int, total: int) -> int:
    """Add whole sections around the best one, a round at a time (the one before it first: the heading and context
    a one-line 「一、」 section belongs to are usually above it, then the one after), until CONTEXT_CHARS are there;
    never past FULL_CAP_CHARS. Returns the new total."""
    lo = hi = next(k for k, s in enumerate(sections) if s.sec_id == best)
    while total < CONTEXT_CHARS:
        grew = False
        for j in (lo - 1, hi + 1):
            if 0 <= j < len(sections):
                s = sections[j]
                n = 0 if s.sec_id in chosen else s.end - s.start
                if total + n <= FULL_CAP_CHARS:
                    chosen[s.sec_id] = s
                    total += n
                    lo, hi, grew = min(lo, j), max(hi, j), True
        if not grew:
            break
    return total


def _heading(text: str, s: _Sec) -> str:
    """A section's heading as the toc gives it: its own, else its first line; at most HEADING_MAX."""
    return (s.heading or _first_line(text[s.start : s.end]))[:HEADING_MAX]


def _mark(text: str, s: _Sec, at: int) -> str:
    """The line that opens section s's text shown from at (addendum E): 〔第 N 節〕, or 〔第 N 節：heading〕 when the
    shown text does not open with the section's heading line (a window inside the section, a title block above its
    heading, a heading joined from two lines)."""
    heading = _heading(text, s)
    first = _first_line(text[at : s.end])
    opens = not text[s.start : at].strip() and (first.startswith(heading) or strip_decor(first)[0].startswith(heading))
    if opens or not heading:
        return SECTION_MARK.format(sec=s.sec_id)
    return SECTION_MARK_HEADING.format(sec=s.sec_id, heading=heading)


def _window_content(text: str, sections: list[_Sec], best: _Sec, ws: int, we: int, lo: int,
                    hi: int) -> tuple[str, list[str]]:
    """A 章節 window's content and the parts it reaches: a line naming the section it starts in, the cut marks and
    a table's header row (_lead, _trail: lo..hi is the text the window is cut from), and the best section's line
    where that section starts inside the window."""
    if ws < best.start < we and not text[ws : best.start].strip():
        ws = best.start  # only blank text before the best section: start on its heading
    reached = [s for s in sections if s.start < we and s.end > ws] or [best]
    first = next((s for s in reached if s.start <= ws < s.end), best)
    split = first is not best and ws < best.start < we
    body = _tidy(text[ws : best.start if split else we])
    out = _mark(text, first, ws) + "\n" + _lead(text, ws, lo, body) + body
    if split:
        out += "\n" + _mark(text, best, best.start) + "\n" + _tidy(text[best.start : we])
    return out + _trail(text, we, hi), [str(s.sec_id) for s in reached]


def _hit_sections(text: str, sections: list[_Sec], chunks: list[tuple[int, int, int]],
                  doc_id: str) -> tuple[str, int | None, str, list[str]]:
    """The 章節 content: the sections holding the hit's chunks, taken best chunk first while they fit FULL_CAP_CHARS
    and shown whole in document order, each block opened by a line naming its first section (addendum E). Under
    CONTEXT_CHARS of them (a one-line section says nothing on its own; review 11-01), the whole sections around the
    best one join; still under it, a window around the best chunk. When even the best section does not fit, a window
    inside it (the text's start when nothing matched). A window is marked 「…」 where it cuts (_window_content).
    Returns the content, the section the toc centres on, what more says first and the parts shown, in order."""
    by_id = {s.sec_id: s for s in sections}
    order = list(dict.fromkeys(c[0] for c in chunks if c[0] in by_id)) or [s.sec_id for s in sections]
    if not order:
        return "", None, "", []
    best = by_id[order[0]]
    cs, ce = chunks[0][1:] if chunks else (best.start, best.start)
    chosen: dict[int, _Sec] = {}
    total = 0
    for sid in order:
        n = by_id[sid].end - by_id[sid].start
        if total + n > FULL_CAP_CHARS:
            break
        chosen[sid] = by_id[sid]
        total += n
    if not chosen:  # the best section alone is over the cap: a part of it, said to be a part
        ws, we = _window(text, cs, ce, FULL_CAP_CHARS, best.start, best.end)
        note = MORE_PART.format(sec=best.sec_id, chars=best.end - best.start, id=doc_id)
        content, parts = _window_content(text, sections, best, ws, we, best.start, best.end)
        return content, best.sec_id, note, parts
    if total < CONTEXT_CHARS and _grow(sections, chosen, best.sec_id, total) < CONTEXT_CHARS:
        ws, we = _window(text, cs, ce, FULL_CAP_CHARS)
        content, parts = _window_content(text, sections, best, ws, we, 0, len(text))
        return content, best.sec_id, MORE_AROUND.format(cap=FULL_CAP_CHARS), parts
    shown = sorted(chosen.values(), key=lambda s: s.start)
    blocks: list[tuple[int, int, _Sec]] = []
    for s in shown:
        if blocks and blocks[-1][1] == s.start:
            blocks[-1] = (blocks[-1][0], s.end, blocks[-1][2])
        else:
            blocks.append((s.start, s.end, s))
    content = GAP.join(f"{_mark(text, s, a)}\n{body}" for a, b, s in blocks if (body := _tidy(text[a:b])))
    return content, best.sec_id, "", [str(s.sec_id) for s in shown]


def _full_body(doc: sqlite3.Row, text: str, sections: list[_Sec], chunks: list[tuple[int, int, int]]) -> dict:
    """A full place (design §二): 全文 when the text fits FULL_CAP_CHARS, else 章節 with the parts shown, toc and more."""
    if len(text) <= FULL_CAP_CHARS:
        return {"content_type": "全文", "content": _tidy(text)}
    content, anchor, note, parts = _hit_sections(text, sections, chunks, doc["doc_id"])
    return {"content_type": "章節", "content": content, "parts": parts, "toc": _toc(text, sections, anchor, TOC_MAX),
            "more": note + MORE.format(chars=len(text), id=doc["doc_id"])}


def attachment_header(text: str) -> str:
    """The start of an attachment's text, at most HEADER_CHARS: cut after its last line end there (its title block),
    else after a sentence end, else hard with 「…」."""
    if len(text) <= HEADER_CHARS:
        return _tidy(text)
    head = text[:HEADER_CHARS]
    if (i := head.rfind("\n")) > 0:
        return _tidy(head[:i])
    end = _after_last_break(head, 1, HEADER_CHARS)
    return _tidy(head[:end]) if end else _tidy(head) + "…"


def _with_header(doc: sqlite3.Row, text: str, body: dict | None) -> dict | None:
    """An attachment shown as 章節 or 段落 also gets its header, unless the content (after its 〔第 N 節〕 line)
    already starts with it (a 片段 does not: it is the short form, design §二)."""
    if body is None or doc["kind_class"] != "attachment" or body.get("content_type") not in ("章節", "段落"):
        return body
    header = attachment_header(text)
    content = body.get("content", "")
    if m := _MARK_LINE.match(content):
        content = content[m.end() :]
    if not header or content.startswith(header.rstrip("…")):
        return body
    return {"content_type": body["content_type"], "header": header,
            **{k: v for k, v in body.items() if k != "content_type"}}


def _summary_body(text: str, chunks: list[tuple[int, int, int]], sections: list[_Sec], doc_id: str,
                  tokens: list[str], cap: int | None = None, *, wide: bool = False) -> dict:
    """片段 (addendum E; design §二): the cap-long stretch of the best chunk that holds the most query tokens, its ends
    moved to line or sentence ends (_window) and marked 「…」 where the text goes on, a table's header row above it when
    it starts inside a table (H2). wide: a 段落, the cap-long stretch around those tokens taken from the whole text,
    past the chunk's ends. No token in the chunk (a meaning-only match): the chunk's start; nothing matched: the text's
    start. 全文 when it is the whole text, with nothing more; else more says how to read on: the whole text when one
    read page holds it, else the hit's section (part, opening at the hit's line inside a section longer than a read
    page; heading as in the toc) and the toc. cap None: SNIPPET_CHARS."""
    cap = SNIPPET_CHARS if cap is None else cap
    best = chunks[0] if chunks and text[chunks[0][1] : chunks[0][2]].strip() else None
    if best is None:
        ws, we = _window(text, 0, 0, cap)
    else:
        _, cs, ce = best
        a, b = _focus(text, cs, ce, tokens, cap) or (cs, cs)
        ws, we = _window(text, a, b, cap) if wide else _window(text, a, b, cap, cs, ce)
    body = {"content_type": "段落" if wide else "片段", "content": _shown(text, ws, we, 0, len(text))}
    if not (text[:ws].strip() or text[we:].strip()):
        return {**body, "content_type": "全文"}
    chars = len(text)
    if chars <= READ_CAP_CHARS:
        return {**body, "more": SUMMARY_MORE.format(chars=chars, id=doc_id)}
    sec = None if best is None else next((s for s in sections if s.sec_id == best[0]), None)
    if sec is None:
        return {**body, "more": SUMMARY_MORE_START.format(chars=chars, id=doc_id)}
    part = str(sec.sec_id)
    paged = sec.end - sec.start > READ_CAP_CHARS and sec.start <= ws < sec.end  # read pages it: open at the hit's line
    if paged and (offset := max(text.rfind("\n", 0, ws) + 1, sec.start) - sec.start):
        part = f"{sec.sec_id}:{offset}"
    return _compact({**body, "part": part, "heading": _heading(text, sec),
                     "more": SUMMARY_MORE_PART.format(sec=sec.sec_id, chars=chars, id=doc_id, part=part)})


# ---------------------------------------------------------------- search response

@dataclass
class _Item:
    head: dict                  # id, background, identity, title, url, data_as_of, dates, status, applies_to, parent
    rich: dict | None           # 全文 or 章節 (a full place) or 段落 (passage); None: the 片段 is all it has
    snippet: dict               # 片段 (全文 when that is the whole text), or 無文字
    chars: int
    level: str = DEFAULT_DETAIL  # what it was given: 'full' (a full place), 'passage' or 'snippet'
    copies: list[dict] = field(default_factory=list)  # the same document at other addresses (at most copies_cap)
    short: bool = False         # True once the budget made it a 片段 row
    copies_cap: int = COPIES_MAX  # copies shown
    trimmed: dict | None = None  # rank 1's body after the last budget step

    def lean(self) -> bool:
        """A 片段 row: the short header, no copies (design §二): a snippet result, or one the budget shortened."""
        return self.trimmed is None and (self.level == "snippet" or self.short)

    def body(self) -> dict:
        if self.trimmed is not None:
            return self.trimmed
        return self.snippet if self.lean() or self.rich is None else self.rich

    def row(self, rank: int) -> dict:
        if self.lean():
            return {"rank": rank, **{k: self.head[k] for k in LEAN_KEYS if k in self.head}, **self.snippet}
        return {"rank": rank, **self.head, **_copies_part(self.copies, self.copies_cap), **self.body()}


def _item(conn: sqlite3.Connection, doc: sqlite3.Row, h: DocHit, level: str, today: date,
          tokens: list[str]) -> _Item:
    """level: what this result gets, 'full' (a full place), 'passage' or 'snippet' (design §二; a 無文字 result never
    takes a full place: review 11-01). tokens: the query's own tokens, which 片段 and 段落 centre on."""
    text = _shown_text(doc)
    chunks = _chunks(conn, doc["doc_id"], h.chunk_ids)
    rich = None
    if text is None:
        snippet = _no_text(doc, PARTIAL_CHARS)
    else:
        sections = _sections(conn, doc["doc_id"], text)
        snippet = _summary_body(text, chunks, sections, doc["doc_id"], tokens)
        if level == "full":
            rich = _with_header(doc, text, _full_body(doc, text, sections, chunks))
        elif level == "passage":
            rich = _with_header(doc, text, _summary_body(text, chunks, sections, doc["doc_id"], tokens,
                                                         PASSAGE_CHARS, wide=True))
        if rich is not None and rich.get("content") == snippet.get("content") and "header" not in rich:
            rich = None  # a text the 片段 already shows whole
    head = {"id": doc["doc_id"], **({"background": True} if h.background else {}), **_header(conn, doc, today)}
    return _Item(head=head, rich=rich, snippet=snippet, chars=len(text or ""), level=level,
                 copies=_copies(conn, doc["doc_id"]))


def _render(head: dict, items: list[_Item], downgraded: bool, note: str) -> dict:
    """note: the ranking note, first (NOTE_RANKING or NOTE_RANKING_HYBRID)."""
    results = [it.row(rank) for rank, it in enumerate(items, 1)]
    notes = [note]
    if any(r.get("background") for r in results):
        notes.append(NOTE_BACKGROUND)
    if any(r["content_type"] == "章節" for r in results):
        notes.append(NOTE_SECTIONS)
    notes += _copies_notes([c for it in items if not it.lean() for c in it.copies[: it.copies_cap]])
    notes.append(NOTE_DATES)
    if downgraded:
        notes.append(NOTE_DOWNGRADED)
    return {**head, "found": True, "results": results, "notes": notes}


def _fit(head: dict, items: list[_Item], note: str) -> dict:
    """Over budget, a step at a time until it fits (design §二 limit): from the last result up to the second, a result
    becomes a 片段 row (its 片段 and the short header); copies shrink to 2 then 0 (the totals stay); results go from the end
    (at least one stays); last, rank 1's content is cut. note goes to every render."""
    down = False
    resp = _render(head, items, down, note)
    for it in reversed(items[1:]):
        if _size(resp) <= RESPONSE_BUDGET_BYTES:
            return resp
        if not it.lean():
            it.short, down = True, True
            resp = _render(head, items, down, note)
    for cap in (2, 0):
        if _size(resp) <= RESPONSE_BUDGET_BYTES:
            return resp
        if any(len(it.copies) > cap for it in items):
            for it in items:
                it.copies_cap = min(it.copies_cap, cap)
            down = True
            resp = _render(head, items, down, note)
    while _size(resp) > RESPONSE_BUDGET_BYTES and len(items) > 1:
        items.pop()
        down = True
        resp = _render(head, items, down, note)
    if _size(resp) > RESPONSE_BUDGET_BYTES:
        _trim_first(head, items, note)
        resp = _render(head, items, True, note)
    return resp


def _trim_first(head: dict, items: list[_Item], note: str) -> None:
    """Cut rank 1's content to the longest prefix that fits (it then is a part: 章節 with more; parts goes, since
    the cut may leave some of them out)."""
    it = items[0]
    body = it.body()
    if body["content_type"] == "無文字":
        return
    content = body["content"]
    more = body.get("more") or MORE.format(chars=it.chars, id=it.head["id"])
    kept = {k: v for k, v in body.items() if k != "parts"}

    def cut(n: int) -> None:
        it.trimmed = {**kept, "content_type": "章節", "content": content[:n].rstrip() + "…", "more": more}

    lo, hi = 0, len(content)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cut(mid)
        if _size(_render(head, items, True, note)) <= RESPONSE_BUDGET_BYTES:
            lo = mid
        else:
            hi = mid - 1
    cut(lo)
    if _size(_render(head, items, True, note)) > RESPONSE_BUDGET_BYTES:
        it.trimmed.pop("toc", None)


def _span(days: list[str]) -> str:
    """The 資料截至 of the sites: one day, or the first and the last 「A～B」; ? when none is known."""
    days = sorted(d for d in days if d)
    if not days:
        return "?"
    return days[0] if days[0] == days[-1] else f"{days[0]}～{days[-1]}"


def _head(conn: sqlite3.Connection, today: date) -> dict:
    """today and the coverage line, and before them in a database without sites rows its data_as_of. A database with
    sites rows (plan 2026-11-08 T4): COVERAGE_SITES_OCR for a to-text build (meta text_pipeline 'totext'), else
    COVERAGE_SITES, from the sites table and meta counts_json (the calendar part when it holds events); without:
    COVERAGE_OCR, else COVERAGE (a skeleton build: still true for it)."""
    meta = _meta(conn)
    ocr = meta.get("text_pipeline") == "totext"
    if sites := _sites(conn):
        events = json.loads(meta.get("counts_json") or "{}").get("docs", {}).get("event", 0)
        coverage = (COVERAGE_SITES_OCR if ocr else COVERAGE_SITES).format(
            sites=len(sites), calendar=CALENDAR_PART if events else "", span=_span([s[0] for s in sites]),
            missing_pages=sum(s[1] for s in sites), missing_files=sum(s[2] for s in sites))
        return {"today": today_block(today), "coverage": coverage}
    coverage = COVERAGE_OCR if ocr else COVERAGE
    return {"today": today_block(today), "data_as_of": {UNIT_NAME: meta.get("data_as_of", "")},
            "coverage": coverage.format(data_as_of=meta.get("data_as_of", "?"),
                                        missing_pages=meta.get("missing_pages", "?"),
                                        missing_files=meta.get("missing_files", "?"))}


def build_response(conn: sqlite3.Connection, result: SearchResult, *, today: date, detail: str = DEFAULT_DETAIL,
                   applied: dict | None = None) -> dict:
    """The search answer. Its first note is NOTE_RANKING_HYBRID when result.ranking is 'hybrid' (vectors or the
    reranker shaped the order), else NOTE_RANKING (their *_SITES forms in a database with sites rows: plan 2026-11-08
    T4); 片段 and 段落 centre on the query's own tokens (understood.tokens). detail: one of DETAILS (design §二);
    applied: what the server says of its optional parameters (design §四), in the head when given."""
    if detail not in DETAILS:
        raise ValueError(f"detail must be one of {DETAILS}: {detail!r}")
    u = result.understood
    understood = _compact({"terms": list(u.terms), "cohorts": list(u.cohorts), "aliases": list(u.aliases)})
    head = {**_head(conn, today), "query": result.query[:QUERY_ECHO_MAX], **_compact({"understood": understood}),
            **({"applied": applied} if applied else {})}
    many = "data_as_of" not in head  # sites rows (plan 2026-11-08 T4): each result has its own, the head none
    hybrid = result.ranking == "hybrid"
    note = (NOTE_RANKING_HYBRID_SITES if hybrid else NOTE_RANKING_SITES) if many else (
        NOTE_RANKING_HYBRID if hybrid else NOTE_RANKING)
    tokens = list(u.tokens)
    items: list[_Item] = []
    places = 0
    for h in result.hits:
        doc = _doc(conn, h.doc_id, text=True)
        if doc is not None:  # a hit the database no longer holds is skipped, never an error
            level = detail
            if detail == "full":
                place = places < FULL_PLACES and _shown_text(doc) is not None
                places += place
                level = "full" if place else "snippet"
            items.append(_item(conn, doc, h, level, today, tokens))
    if not items:
        return {**head, "found": False, "notes": [NOTE_NOT_FOUND_SITES if many else NOTE_NOT_FOUND]}
    return _fit(head, items, note)


# ---------------------------------------------------------------- read

def _alias(conn: sqlite3.Connection, id: str) -> tuple[str, str] | None:
    """(the document kept, why) for an id the build left out under another document (doc_alias, plan 2026-11-08 T1);
    None for any other id and in a database built before."""
    if not _has_table(conn, "doc_alias"):
        return None
    row = conn.execute("SELECT doc_id, why FROM doc_alias WHERE alias=?", (id,)).fetchone()
    return (row[0], row[1]) if row else None


def _by_url(conn: sqlite3.Connection, url: str) -> str | None:
    """design §三: the document a URL the library holds is the address of, http or https alike (an agent may pass the
    page or file it has instead of an id); None for any other text."""
    if not url.startswith(("http://", "https://")):
        return None
    rest = url.split("://", 1)[1]
    row = conn.execute("SELECT doc_id FROM docs WHERE url IN (?, ?) ORDER BY url = ? DESC LIMIT 1",
                       (f"http://{rest}", f"https://{rest}", url)).fetchone()
    return row[0] if row else None


def read_response(conn: sqlite3.Connection, id: str, part: str, *, today: date) -> dict:
    """One document by its id. An id the build left out under another document (a copy at another address, another
    format of it, a duplicate: doc_alias) gives the document kept for it, NOTE_ALIAS first; any other id (an
    s-prefixed series id of a build before 11-03 too) is unknown."""
    head = {k: v for k, v in _head(conn, today).items() if k != "coverage"}  # today, and data_as_of before T4
    resp, id = None, id.strip()
    doc = _doc(conn, id, text=True) if id else None
    if doc is None and id and (by_url := _by_url(conn, id)) is not None:
        doc = _doc(conn, by_url, text=True)
    lead: tuple[str, ...] = ()
    if doc is None and id and (alias := _alias(conn, id)) is not None:
        kept, why = alias
        doc = _doc(conn, kept, text=True)
        lead = (NOTE_ALIAS.format(kept=kept, why_label=ALIAS_LABELS.get(why, why)),)
    if doc is not None:
        resp = _read_doc(conn, doc, str(part).strip(), head, today, lead)
    return resp or {**head, "found": False, "notes": [NOTE_UNKNOWN_ID]}


def _read_doc(conn: sqlite3.Connection, doc: sqlite3.Row, part: str, head: dict, today: date,
              lead: tuple[str, ...] = ()) -> dict:
    """part '' : 全文 within READ_CAP_CHARS, else the first sections, toc (TOC_MAX) and more; 'toc': the whole toc;
    'N' / 'N:offset': section N from offset, paged by READ_CAP_CHARS with next. referenced_by lists the pages that
    reference the document (addendum D, H3). Over budget the page shrinks first, then the toc, then referenced_by
    (newest kept, its total stays). lead: notes that go first (NOTE_ALIAS)."""
    copies, refs = _copies(conn, doc["doc_id"]), _refs(conn, doc["doc_id"])
    header = _header(conn, doc, today)

    def frame(shown: int) -> dict:
        return {**head, "found": True, "id": doc["doc_id"], **header, **_copies_part(copies, None),
                **_refs_part(refs, shown)}

    tail = [*_copies_notes(copies), *([NOTE_REFS] if refs else []), NOTE_DATES]
    text = _shown_text(doc)
    sections = [] if text is None else _sections(conn, doc["doc_id"], text)
    cap, toc_limit, shown = READ_CAP_CHARS, None, len(refs)
    while True:
        if text is None:
            body, notes = _no_text(doc, READ_CAP_CHARS), []
        else:
            body, notes = _read_body(doc["doc_id"], text, sections, part, cap, toc_limit)
            body = _with_header(doc, text, body)
        resp = {**frame(shown), **body, "notes": [*lead, *notes, *tail]}
        over = _size(resp) - READ_BUDGET_BYTES
        if over <= 0:
            return resp
        content, toc = body.get("content", ""), body.get("toc", [])
        if text is not None and content and cap > MIN_PAGE_CHARS:
            per_char = len(content.encode("utf-8")) / len(content)
            cap = max(MIN_PAGE_CHARS, min(cap, len(content)) - math.ceil(over / per_char) - 20)
        elif text is not None and len(toc) > 1:
            per_entry = len(to_json(toc).encode("utf-8")) / len(toc)
            toc_limit = max(1, len(toc) - math.ceil(over / per_entry) - 1)
        elif shown > 0:
            per_ref = len(to_json(refs[:shown]).encode("utf-8")) / shown
            shown = max(0, shown - math.ceil(over / per_ref) - 1)
        else:
            return resp


def _read_body(doc_id: str, text: str, sections: list[_Sec], part: str, cap: int,
               toc_limit: int | None) -> tuple[dict, list[str]]:
    def toc_of(limit: int | None) -> tuple[list[dict], list[str]]:
        toc = _toc(text, sections, None, limit)
        cut = [NOTE_TOC_CUT.format(shown=len(toc), total=len(sections))] if len(toc) < len(sections) else []
        return toc, cut

    short = TOC_MAX if toc_limit is None else min(TOC_MAX, toc_limit)
    if part == "toc":
        toc, notes = toc_of(toc_limit)
        return {"toc": toc}, notes
    if part == "":
        if len(text) <= cap:
            return {"content_type": "全文", "content": _tidy(text)}, []
        chosen, total = [], 0
        for s in sections:
            if total + s.end - s.start > cap:
                break
            chosen.append(s)
            total += s.end - s.start
        body = {"content_type": "章節"}
        more = MORE.format(chars=len(text), id=doc_id)
        if chosen:
            body["content"] = _tidy(text[chosen[0].start : chosen[-1].end])
        else:  # the first section alone is over the page: its first page
            s = sections[0]
            end = _page_end(text, s.start, s.end, cap)
            body["content"] = _tidy(text[s.start : end])
            body["next"] = f"{s.sec_id}:{end - s.start}"
            more += "；" + NEXT.format(id=doc_id, next=body["next"])
        body["toc"], notes = toc_of(short)
        body["more"] = more
        return _compact(body), notes
    m = _PART.fullmatch(part)
    sec = next((s for s in sections if s.sec_id == int(m.group(1))), None) if m else None
    offset = int(m.group(2) or 0) if m else 0
    if sec is None or (offset and offset >= sec.end - sec.start):
        toc, notes = toc_of(short)
        return {"toc": toc}, [NOTE_BAD_PART, *notes]
    start = sec.start + offset
    end = _page_end(text, start, sec.end, cap)
    body = {"content_type": "章節", "content": _page(text, start, end)}
    if end < sec.end:
        body["next"] = f"{sec.sec_id}:{end - sec.start}"
        body["more"] = MORE_PAGE.format(sec=sec.sec_id, chars=sec.end - sec.start, upto=end - sec.start, id=doc_id,
                                        next=body["next"])
    return _compact(body), []
