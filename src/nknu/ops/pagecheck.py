"""網頁核查's data (design docs/superpowers/specs/2026-11-16-page-check-design.md; the facts it rests on:
research/ops_2026-11-16/pagecheck_facts.md): one archived page of the crawl as it was stored, its regions marked as
the library reads them, and every link, image and button on it with what the crawler's ledger says of it. The panel
(nknu.ops.check_page) draws what this returns and computes nothing.

Owner 11-16: 「我想做一个核查网页有没有漏抓的面板（不需要额外抓 CSS，直接只读现有的存档 WARC、账本 `frontier.db` 和
`profiles.py` 的正文区规则，不连学校服务器）」, and before going to sleep: 「你直接动手吧，不用我点头」. Every way of doing it
below that names no other source is 【Claude】's.

Read only, everywhere (design section 四): the ledger is opened by nknu.archive.progress.connect (mode=ro, the result
views in the connection's temp schema, query_only) or, only when that is refused as read-only and no -wal is left on a
read-only mount, with immutable=1 (facts section 7: right while the crawler is not running; the page then says it read
a snapshot); a record is read by
nknu.archive.postback.read_response; nothing of nknu.archive is changed, only its pure functions are called. Nothing
here connects anywhere: the stored page is shown as a document that can load nothing but our own archived images
(sanitize below, and the policy nknu.ops.web serves it under)."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser, LexborNode, SelectolaxError

from nknu.archive import google_api, links, outcome, postback, profiles, progress, scope
from nknu.archive.fetch import DecodeError, decode_body
from nknu.archive.links import _base as page_base  # the crawler's <base href>, so hrefs join as the crawler joined them
from nknu.totext import pagetext

log = logging.getLogger(__name__)

LEDGER = "frontier.db"  # the crawler's ledger in its archive folder; url.warc is relative to that folder (facts §3)
MAX_HOPS = outcome.MAX_HOPS  # a redirect chain is followed this far, as the crawler's views follow it

# ---------------------------------------------------------------- the buckets (design section 二)

# (key, name on the panel, what it means, floating over the name). Their order is the legend's. 【Claude】 names, each
# from the reader's side and none an echo of another (the owner 11-16 named 「要人看」 among the names that say nothing)
BUCKETS = (
    ("got", "抓到了", "帳本說抓到了（沒做瀏覽器檢查的也算）"),
    ("look", "內容可疑", "抓到了，但內容可疑：幾乎是空的、亂碼、像已刪除"),
    ("rule", "照規則不抓", "抓取範圍的規則不抓：校外、社群、版面圖、太大、撞上限……"),
    ("album", "相簿跳過", "相簿的照片，照用戶的決定不抓"),
    ("login", "要登入", "要登入才看得到，不抓"),
    ("gone", "學校已刪除", "學校那邊已經不存在：連結壞了、錯誤頁、短網址失效"),
    ("next", "下一輪", "留到第二輪（含要 JavaScript 的）"),
    ("review", "還沒決定", "爬蟲還沒決定怎麼處理的寫法、Google 讀不到、轉址沒有終點……"),
    ("failed", "失敗", "抓了但失敗：連不上、被拒絕、4xx／5xx、轉址繞圈、回來的不是檔"),
    ("missing", "帳本沒有", "帳本裡沒有這個網址：爬蟲沒發現它，或發現了卻沒留下紀錄"),
    ("notlink", "不是連結", "javascript:、mailto:、tel:、# 這類，沒有網址可查"),
)
BUCKET_NAME = {key: name for key, name, _ in BUCKETS}
BUCKET_TIP = {key: tip for key, _, tip in BUCKETS}
# what the panel lists one by one (design section 一: 「没抓到、失败的逐条列出来」), each with who acts on it, in the
# words of the no-date page (爬蟲可補 / 要查); the next round's are counted, not listed: every off-campus link is one
# (the review of 11-16 found them 70% of the list). 【Claude】
PROBLEMS = ("missing", "failed", "review", "look")
WHO = {"missing": "fix", "failed": "fix", "review": "check", "look": "check"}
WHO_WORDS = {"fix": ("爬蟲可補", "爬蟲這邊能補：沒發現的去發現，失敗的重抓（有的重抓也拿不到，是學校那邊的問題）"),
             "check": ("要查", "要有人看一下：內容可疑，或爬蟲還沒決定怎麼處理")}
ZONE_ORDER = ("in", "files", "out")  # a miss in the content area first, then the attachment area, then the menus
WHATS = (("link", "連結"), ("image", "圖片"), ("button", "按鈕"))

NO_AREA = "no_area"  # outcome.FLAGS (11-17): a page that came back without its content area
# outcome.user_bucket's categories as ours, then the three changes of design section 二 and facts §1.5
_FROM_OUTCOME = {"got": "got", "queued": "next", "failed": "failed", "review": "review", "login": "login",
                 "gone": "gone", outcome.SCHOOL_SIDE: "gone", "pending_check": "got", "next_round": "next",
                 "rule": "rule"}
_OVERRIDE = {
    "unchecked": "got",  # stored; only the browser check (not run in round one, owner) is missing
    "thin": "look", "decode": "look", "looks_gone": "look", "unresolved": "look", "mismatch": "look",
    "held": "rule", "too_large": "rule",  # the owner 11-08 on the 43 held by a cap: 「保持现状」
    "bad_link": "gone", "short_dead": "gone",
    "parked": "failed", "api_quota": "failed",
    NO_AREA: "look",  # read thin by outcome: the export keeps its last good copy; never missing nor failed
    "not_discovered": "missing",
}
# the ledger's result codes in Traditional words (outcome.RESULT_LABEL, said again for the panel)
RESULT_WORDS = {
    "ok": "抓到了", "unchecked": "抓到了（沒做瀏覽器檢查）", "gone": "學校那邊已經不存在", "looks_gone": "看起來像不存在",
    "api_error": "Google 讀不到", "api_quota": "Google 下載次數用完", "login": "要登入", "redirect": "轉到別的網址",
    "rule": "照規則不抓", "later": "下一輪再抓", "todo": "還沒抓", "retry": "等重試", "lab": "連不上",
    "js": "內容要 JavaScript", "click": "點了才載入的內容", "unresolved": "瀏覽器也判定不了", "thin": "頁面幾乎是空的",
    "refused": "被拒絕（401、403、451）", "http_4xx": "學校回了別的錯誤", "decode": "編碼有問題", "too_large": "檔案太大",
    "failed": "試了 3 次還是失敗", "server_error": "學校伺服器出錯", "not_file": "應該是檔案，回來不是",
    "held": "撞到上限沒抓", "unknown": "程式不認得的組合", "mismatch": "按到的可能是別的公告",
    "not_discovered": "發現了，卻沒有留下紀錄", "unhandled": "還沒決定怎麼處理的寫法", "bad_link": "學校的連結壞了",
    "short_dead": "短網址已失效", "parked": "目錄連續被拒", outcome.SCHOOL_SIDE: "學校那邊的問題（人看過）",
    "superseded": "被後來那次取代，後來那次不見了", "gone_home": "跳回學校首頁（學校那邊已經不存在）",
    NO_AREA: "缺內容區（學校回了空頁）",
}
# url.reason, page_res.why and link.verdict in words: the whole code, else its prefix before the colon 【Claude】
REASON_WORDS = {
    "round2:offsite": "校外網站，第二輪", "round2:other_site": "別的網站，第二輪", "round2:offsite_unit": "校外的本校單位，第二輪",
    "round2:js_site": "整站要 JavaScript，第二輪", "round2:offsite_file": "校外的檔，第二輪", "round2:click": "點了才有，第二輪",
    "album:drive": "雲端相簿", "album": "相簿", "moved_scheme": "換了 http／https", "decor:repeat": "很多頁共用的版面圖",
    "decor_name": "檔名像版面圖", "tiny": "太小的圖", "chrome": "選單、頁首裡的圖", "data": "data: 圖", "captcha": "驗證碼",
    "out:alias": "同一個網站的別名", "out:loopback": "指向本機的網址", "out:error_page": "學校的錯誤頁", "error_page": "學校的錯誤頁",
    "out:login": "要登入", "out:social": "社群平台", "out:not_content": "不是內容的連結", "out:system": "系統頁",
    "out:trap": "爬蟲陷阱", "out:search": "搜尋頁", "out:media": "影音", "out:refuses": "網站不給抓", "out:too_long": "網址太長",
    "out:never_host": "不抓的網站", "out:bad_host": "網址寫壞了", "cap:depth": "撞到深度上限", "cap:site": "撞到整站上限",
    "out:internal_service": "學校內部的服務", "out:scheme": "不是網頁的網址",
    "out:skip_path": "名單寫明這條路徑不往下翻",  # round1.Site's skip paths (11-17): a rule, never missing
    # 11-19 (the crawler's rules batch): a skip path with its own name, a site's pictures past its cap declined by
    # the list, and the other sizes of a Wix picture already kept: all three are rules, never missing
    "out:skip_path:teacher_fulltext": "教師著作全文，定了不收", "out:skip_path:proceedings": "研討會論文集全文，定了不收",
    "out:skip_path:teaching_materials": "中小學教材下載區，定了不收",
    "out:cap_image": "這一站超過圖片上限，名單寫明不抓",
    "out:wix_size": "同一張圖的另一個尺寸",
    "media:drive_video": "雲端影片，只記檔名和大小", "media:drive_audio": "雲端聲音檔，只記檔名和大小",
    "media:drive_installer": "安裝檔，只記檔名和大小", "media": "影音或安裝檔，只記檔名和大小",
    "too_large": "檔案太大", "media_type": "影音或安裝檔", "gsimg_stale": "協作平台的舊圖", "server_down": "伺服器一直連不上",
    "tls": "加密連線失敗", "tls_cert": "憑證有問題", "dns": "查不到主機", "denied_dir": "目錄被拒", "has_login_box": "頁面上有登入框",
    "unhandled:never": "照規則不按", "unhandled:ignore": "照規則不按", "unhandled:unknown": "還沒決定按不按",
    "unhandled:unpressed": "沒按到", "dup": "", "queued": "", "scheme_switch": "",
    # 11-16 evening: the codes the first whole sweep still showed as code on the site pages (api:notFound 70 rows,
    # empty_detail 29, not_file:no_name:html 9, unhandled:google 6, net_RemoteProtocolError 2, …), in the reader's
    # words (owner 11-16 on names that are code names: name from the reader's side) 【Claude】
    "unhandled:google": "Google 連結還沒決定怎麼讀", "round2:unhandled": "還沒決定的寫法，第二輪",
    "round2:google_published": "Google 發佈的文件，第二輪", "empty_detail": "內文區是空的",
    "click:plain_href": "有一般連結可走，不用按", "click:shown_on_load": "一打開就顯示的分頁，不用按",
    "select:auto": "網頁自己會選", "press_redirect": "按了被轉走", "restart": "重開時沒按完",
    "conn_pause": "連不上，這台伺服器先暫停", "net_down": "學校網路斷了", "timeout": "等太久沒回應",
    "connect": "連不上",
    "not_file:empty_body": "應該是檔案，回來是空的", "not_file:redirect": "應該是檔案，卻被轉走",
    "not_file:saying:gone_page": "學校說檔案不存在", "not_file:saying:bad_link": "學校說連結壞了",
    "not_file:no_name:html": "應該是檔案，回來是網頁", "not_file:no_name": "應該是檔案，回來不是",
    "not_file": "應該是檔案，回來不是",
    "api:notFound": "Google 找不到（沒公開或已刪除）", "api:cannotDownloadFile": "Google 不給下載",
    "api:downloadQuotaExceeded": "Google 下載次數用完", "api:automated_queries": "Google 擋了自動查詢",
    "api:unreadable": "Google 的回答讀不懂", "api": "Google 讀不到",
    "http_4xx": "學校回了別的錯誤", "http_5xx": "學校伺服器出錯",
    NO_AREA: "缺內容區，學校回了空頁",  # the reason of a row put back to be asked again (crawler._no_area_again)
}
# an HTTP status in words, the number after them 【Claude】
HTTP_WORDS = {400: "學校說請求不對", 401: "學校要求登入", 403: "被拒絕", 404: "找不到", 405: "學校不收這種請求",
              408: "等太久沒回應", 410: "已移除", 429: "太頻繁被擋", 500: "學校伺服器出錯", 502: "學校伺服器沒回應",
              503: "學校伺服器忙", 504: "學校伺服器沒回應"}
# what a list's or a form's buttons are, by the action kinds (outcome.ACTION_KINDS)
ACTION_WORDS = {"pager": "翻頁", "detail": "按出公告", "download": "按出檔案", "click": "按鈕", "data": "資料"}
_HTTP = re.compile(r"http_(\d{3})")
NOT_LINK = ("javascript:", "mailto:", "tel:", "sms:", "#", "data:", "about:", "vbscript:")


def reason_words(code: str | None) -> str:
    """A reason, verdict or why code in words ('' for none or one that says nothing to the owner)."""
    code = (code or "").strip()
    if not code:
        return ""
    if code in REASON_WORDS:
        return REASON_WORDS[code]
    if m := _HTTP.fullmatch(code):
        status = int(m.group(1))
        said = HTTP_WORDS.get(status) or ("學校伺服器出錯" if status >= 500 else "學校回了錯誤")
        return f"{said}（{status}）"
    if code.startswith("net_"):  # crawler.py: a transport error by its exception's name
        return "連線中途出錯"
    head = code.split(":", 1)[0]
    for key in (code.rsplit(":", 1)[0], head):
        if REASON_WORDS.get(key):
            return REASON_WORDS[key]
    return RESULT_WORDS.get(code) or RESULT_WORDS.get(head) or code


def own_result(row: sqlite3.Row) -> str:
    """A url_result row's result, with a web page that came back without its platform's content area said as such:
    flag no_area (11-17, the crawler session: a teacher's CV page with no card, asked again up to three times, then
    read thin by outcome, and exported from its last good copy) is NO_AREA, not 頁面幾乎是空的."""
    result = row["result"]
    flags = row["flags"] if "flags" in row.keys() else None  # noqa: SIM118  a Row's `in` looks at its values
    return NO_AREA if result == "thin" and NO_AREA in (flags or "").split(",") else result


def bucket_of(result: str | None, reason: str | None = None) -> str:
    """Our bucket of a ledger result (outcome.user_bucket with design section 二's changes); a skipped row of an album
    is 相簿跳過 though the ledger says rule."""
    result = result or "unknown"
    if result == "rule" and (reason or "").startswith("album"):
        return "album"
    if result in _OVERRIDE:
        return _OVERRIDE[result]
    return _FROM_OUTCOME.get(outcome.user_bucket(result), "review")


def verdict_bucket(verdict: str | None) -> str:
    """A link verdict's bucket when no url row holds the target (outcome._kept_out's rule, facts §2.3 step 6)."""
    v = verdict or ""
    if v in (*outcome.BROKEN_OUT, "error_page"):
        return "gone"
    if v.startswith("out:login"):
        return "login"
    if v.startswith(("out:", "cap:")):
        return "rule"
    if v.startswith("round2:"):
        return "next"
    if v.startswith("unhandled:"):
        return "review"
    return "missing"  # dup / queued / scheme_switch and no row: found, never kept (not_discovered)


def res_bucket(need: str | None, why: str | None) -> str | None:
    """A page_res row's bucket by its need and why alone, or None when its url row decides (need must)."""
    why = why or ""
    if need == "no":
        return "album" if why.startswith("album") else "login" if why == "captcha" else "rule"
    if need == "round2" or why.startswith("round2:"):
        return "next"
    if why.startswith(("unhandled:never", "unhandled:ignore")):
        return "rule"
    if why.startswith("unhandled:"):
        return "review"
    if why.startswith("out:"):
        return verdict_bucket(why)
    return None


# ---------------------------------------------------------------- the ledger

class Snapshot(sqlite3.Connection):
    """A ledger read with immutable=1 (open_ledger): rows still in a -wal are not seen, so the page says so."""


_READ_ONLY = re.compile(r"(?i)readonly|read-only|unable to open")


def _wal_gone(db: Path) -> bool:
    """No -wal beside the ledger, or an empty one: no WAL connection holds rows the main file lacks."""
    wal = db.with_name(db.name + "-wal")
    try:
        return wal.stat().st_size == 0
    except FileNotFoundError:
        return True


def open_ledger(archive: Path) -> sqlite3.Connection:
    """The ledger of an archive folder, read-only. progress.connect first (mode=ro). Only when that is refused as
    read-only (「attempt to write a readonly database」: a read-only mount whose -wal and -shm the last writer removed,
    facts section 7) and no -wal holds rows does it fall back to immutable=1, with the same temp views and query_only:
    then the main file is the whole ledger and an immutable read is right. Any other error (busy, locked, a recovery
    under way) is raised: an immutable read beside a live -wal would miss its rows and could read pages half old half
    new (the review of 11-16). The fallback's connection is a Snapshot. FileNotFoundError when there is no ledger."""
    db = Path(archive) / LEDGER
    try:
        return progress.connect(db)
    except sqlite3.OperationalError as exc:
        if not (_READ_ONLY.search(str(exc)) and _wal_gone(db)):
            raise
        log.info("账本用 mode=ro 打不开（%s），也没有 -wal：改用 immutable=1", exc)
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?immutable=1", uri=True, isolation_level=None,
                           check_same_thread=False, factory=Snapshot)
    conn.row_factory = sqlite3.Row
    try:
        outcome.create_views(conn, temp=True)
        conn.execute("PRAGMA query_only=ON")
    except BaseException:
        conn.close()
        raise
    return conn


class LedgerCache:
    """One read-only ledger connection kept between requests, used by one request at a time: a panel view and each of
    its archived images used to open the ledger again (about 0.03 s each, the review of 11-16). Opened again when the
    ledger's files change (the main file or its -wal), so a snapshot read never outlives what it read. 【Claude】"""

    def __init__(self, archive: Path) -> None:
        self.archive = Path(archive)
        self.lock = threading.Lock()
        self.conn: sqlite3.Connection | None = None
        self.stamp: tuple | None = None

    def stamped(self) -> tuple:
        """What changes when the ledger does: the size and time of the main file and of its -wal."""
        db = self.archive / LEDGER
        out = []
        for f in (db, db.with_name(db.name + "-wal")):
            try:
                st = f.stat()
                out.append((st.st_size, st.st_mtime_ns))
            except FileNotFoundError:
                out.append(None)
        return tuple(out)

    @contextmanager
    def use(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            stamp = self.stamped()
            if self.conn is None or stamp != self.stamp:
                self.close_now()
                self.conn, self.stamp = open_ledger(self.archive), stamp
            yield self.conn

    def close_now(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self.conn, self.stamp = None, None


@contextmanager
def _ledger(archive: Path, cache: LedgerCache | None) -> Iterator[sqlite3.Connection]:
    """The ledger for one check: the kept connection of cache, else one opened and closed here."""
    if cache is not None:
        with cache.use() as conn:
            yield conn
        return
    conn = open_ledger(archive)
    try:
        yield conn
    finally:
        conn.close()


def other_scheme(url: str) -> str:
    """url with http and https swapped ('' for anything else): the crawler writes a host that answers on one scheme
    only in that one (crawler._scheme_rewrite; facts §2.3 step 4)."""
    for a, b in (("http://", "https://"), ("https://", "http://")):
        if url.startswith(a):
            return b + url[len(a):]
    return ""


def _expects_file(row: sqlite3.Row) -> bool:
    """Whether a row was found as a file, a picture or the like (outcome's hop CTE: exp, its _EXPECTED_FILE)."""
    return (row["kind0"] or row["kind"]) in outcome._EXPECTED_FILE


@dataclass
class Landing:
    """Where a url row's redirects end (outcome's hop / landing / moved, for one row)."""

    row: sqlite3.Row | None  # the row it ends on; None when it ends on no row
    hops: list[str]  # the addresses passed through after the first
    at: str = ""  # the last address reached
    last_id: int | None = None  # the last row that redirected: its link row to `at` says why `at` has no row
    expected_file: bool = False  # a row along the redirects was found as a file or a picture
    why: str = ""  # '' it landed | no_row | loop (a loop, or longer than MAX_HOPS) | no_address


class Ledger:
    """The rows one check asks of the ledger, each asked once."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._url: dict[str, sqlite3.Row | None] = {}
        self._id: dict[int, sqlite3.Row | None] = {}

    def by_id(self, row_id: int | None) -> sqlite3.Row | None:
        if row_id is None:
            return None
        if row_id not in self._id:
            self._id[row_id] = self.conn.execute("SELECT * FROM url_result WHERE id = ?", (row_id,)).fetchone()
        return self._id[row_id]

    def by_url(self, url: str | None, *, schemes: bool = True) -> sqlite3.Row | None:
        """The url row of an address as the crawler spells it, or of its other scheme."""
        if not url:
            return None
        if url not in self._url:
            self._url[url] = self.conn.execute("SELECT * FROM url_result WHERE url = ?", (url,)).fetchone()
        row = self._url[url]
        if row is None and schemes and (other := other_scheme(url)):
            return self.by_url(other, schemes=False)
        return row

    def land(self, row: sqlite3.Row) -> Landing:
        """Where a row leads, followed by final_url at most MAX_HOPS hops (outcome's page_resource does the same for
        every page; here for one page's). A row that is no redirect is its own landing."""
        hops: list[str] = []
        seen = {row["id"]}
        expected, last = False, row
        cur: sqlite3.Row | None = row
        while cur is not None and cur["result"] == "redirect":
            expected = expected or _expects_file(cur)
            last = cur
            if len(hops) >= MAX_HOPS:
                return Landing(None, hops, hops[-1], last["id"], expected, "loop")
            nxt = scope.canonical(cur["final_url"] or "") if cur["final_url"] else other_scheme(cur["url"])
            if not nxt:
                return Landing(None, hops, "", last["id"], expected, "no_address")
            hops.append(nxt)
            cur = self.by_url(nxt)
            if cur is not None and cur["id"] in seen:
                return Landing(None, hops, nxt, last["id"], expected, "loop")  # a loop never lands
            if cur is not None:
                seen.add(cur["id"])
        return Landing(cur, hops, hops[-1] if hops else "", last["id"] if hops else None, expected,
                       "" if cur is not None else "no_row")

    def link(self, page_id: int | None, dst: str) -> sqlite3.Row | None:
        if page_id is None or not dst:
            return None
        return self.conn.execute("SELECT verdict, text FROM link WHERE src = ? AND dst = ?", (page_id, dst)).fetchone()

    def res(self, page_id: int, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM page_res WHERE page_id = ? AND res_key = ?", (page_id, key)).fetchone()

    _BEST = "ORDER BY state = 'done' DESC, state <> 'superseded' DESC, epoch DESC, id DESC LIMIT 1"

    def press_of_row(self, list_id: int, kind: str, row_key: str) -> sqlite3.Row | None:
        """A row's press as PostbackChains.rebuild picks it: a done one, else the newest that is not superseded, else
        the newest."""
        return self.conn.execute(f"SELECT * FROM action_result WHERE list_id = ? AND kind = ? AND row_key = ? "
                                 f"{self._BEST}", (list_id, kind, row_key)).fetchone()

    def action(self, list_id: int, warc: str | None, offset: int | None, target: str, argument: str,
               press: postback.Press | None = None) -> sqlite3.Row | None:
        """The press of this button on this stored page (facts §4: list, the form's archived record, target and
        argument: 16 of 16 in the sample). Else, for a row's button (press: what postback.read_page plans for it on
        this page), its row's press by (list, kind, row_key): a done detail or download is never pressed again when
        the list is fetched again (postback._press), so its form record is an older one, and ctlNN shifts when the
        school adds a notice, so target and argument may be another row's (the review of 11-16). Target and argument
        alone only for what is the button itself, not a row: a pager step, a click, a data press. A superseded press
        gives way to its row's later one (outcome's press CTE)."""
        found = None
        if warc is not None:
            found = self.conn.execute(
                "SELECT * FROM action_result WHERE list_id = ? AND form_src_warc = ? AND form_src_offset = ? "
                f"AND target = ? AND argument = ? {self._BEST}", (list_id, warc, offset, target, argument)).fetchone()
        if found is None and press is not None and press.kind != "pager":
            found = self.press_of_row(list_id, press.kind, press.row_key)
        elif found is None:
            found = self.conn.execute(
                "SELECT * FROM action_result WHERE list_id = ? AND target = ? AND argument = ? "
                f"AND kind IN ('pager', 'click', 'data') {self._BEST}", (list_id, target, argument)).fetchone()
        if found is not None and found["state"] == "superseded":
            found = self.press_of_row(list_id, found["kind"], found["row_key"]) or found
        return found

    def press_answer(self, list_id: int, label: str) -> sqlite3.Row | None:
        """The press an export address's fragment names (export.py: <list>#第N页 for a pager step, <list>#按钮=<the
        row's text, 40 characters> for a row's press, with （a<id>） after it when two rows have the same text)."""
        if m := _PAGE_N.fullmatch(label):
            return self.conn.execute(f"SELECT * FROM action_result WHERE list_id = ? AND kind = 'pager' AND step = ? "
                                     f"{self._BEST}", (list_id, int(m.group(1)))).fetchone()
        words = label.removeprefix(PRESS_MARK)
        if m := _PRESS_ID.search(words):
            got = self.conn.execute("SELECT * FROM action_result WHERE id = ? AND list_id = ?",
                                    (int(m.group(1)), list_id)).fetchone()
            if got is not None:
                return got
            words = words[:m.start()]
        return self.conn.execute(
            "SELECT * FROM action_result WHERE list_id = ? AND kind <> 'pager' "
            f"AND substr(coalesce(nullif(row_text, ''), row_key), 1, 40) = ? {self._BEST}", (list_id, words)).fetchone()

    def drive_res(self, page_id: int, url: str) -> sqlite3.Row | None:
        """The page's drive:<id> resource of a Drive or Docs link (facts §2.2 point 5), or None."""
        got = google_api.parse_link(url)
        return self.res(page_id, f"drive:{got.file_id}") if got.file_id else None

    def drive(self, page_id: int, url: str) -> sqlite3.Row | None:
        """A Drive or Docs link's row: the page's drive:<id> resource's, else drive_item's metadata row."""
        got = google_api.parse_link(url)
        if not got.file_id:
            return None
        res = self.res(page_id, f"drive:{got.file_id}")
        if res is not None and res["res_id"] is not None:
            return self.by_id(res["res_id"])
        item = self.conn.execute("SELECT url_id FROM drive_item WHERE file_id = ? LIMIT 1", (got.file_id,)).fetchone()
        return self.by_id(item[0]) if item else None

    def drive_settled(self, meta: sqlite3.Row, depth: int = 0) -> tuple[str, str]:
        """(result, reason) of a Drive item whose metadata row is ok, by outcome._drive_settled's rules: a video, a
        sound or an installer listed only is a rule's (只记档名和大小); a Google type nobody can export, unhandled; no
        content row, not_discovered; else its content row's result (a shortcut's target once)."""
        item = self.conn.execute("SELECT * FROM drive_item WHERE url_id = ? LIMIT 1", (meta["id"],)).fetchone()
        if item is None:
            return "not_discovered", ""
        mime = item["mime"] or ""
        if mime.startswith(("video/", "audio/")):
            return "rule", "media:drive_video" if mime.startswith("video/") else "media:drive_audio"
        if not mime.startswith(outcome.GOOGLE_APPS) and outcome.installer_name(item["name"] or ""):
            return "rule", "media:drive_installer"
        content = self.by_id(item["content_id"])
        if content is None:
            if mime.startswith(outcome.GOOGLE_APPS) and mime not in outcome.DRIVE_READ_TYPES:
                return "unhandled", ""
            return "not_discovered", ""
        if (content["kind0"] or content["kind"]) == "drive":  # a shortcut: its target's metadata row, once
            if depth:
                return "not_discovered", ""
            if content["result"] != "ok":
                return content["result"], content["reason"] or ""
            return self.drive_settled(content, 1)
        return content["result"], content["reason"] or ""


_PAGE_N = re.compile(r"#第(\d+)页")
PRESS_MARK = "#按钮="  # nknu.content.ids.PRESS_MARK, the export's writing of a row a button brought
_PRESS_ID = re.compile(r"[（(]a(\d+)[）)]\s*$")


def press_label(url: str) -> tuple[str, str]:
    """(the address without an export's press fragment, the fragment: #第N页 or #按钮=…, '' when none). The fragment
    may come percent-encoded from a browser."""
    base, sep, frag = url.partition("#")
    if not sep:
        return url, ""
    frag = "#" + unquote(frag)
    if frag.startswith(PRESS_MARK) or _PAGE_N.fullmatch(frag):
        return base, frag
    return base, ""


TAIPEI = ZoneInfo("Asia/Taipei")


def taipei(text: str | None) -> str:
    """A stored time in Asia/Taipei (url rows write theirs in UTC, +00:00; one with no zone is kept as written)."""
    if not text:
        return ""
    try:
        when = datetime.fromisoformat(str(text))
    except ValueError:
        return str(text)
    return when.astimezone(TAIPEI).isoformat(timespec="seconds") if when.tzinfo is not None else str(text)


# ---------------------------------------------------------------- one item on the page

@dataclass
class Item:
    """One link, image, button or framed page on the stored page."""

    n: int  # its number on the page (data-nk)
    what: str  # link | image | button
    bucket: str
    href: str  # as written ('' for a button)
    target: str  # the address it leads to, as the crawler spells it ('' when none)
    text: str
    zone: str  # in (the library's content area) | files (the attachment area) | out
    result: str = ""  # the ledger's result, or the verdict that kept it out
    reason: str = ""  # in words
    fetched_at: str = ""
    status: int | None = None
    landing: str = ""  # where a redirect chain ended
    hops: int = 0
    kind: str = ""  # a button's action kind
    image: str = ""  # an image's archived address, when the archive holds its bytes

    def tip(self) -> str:
        """The floating card's sentence, Traditional (the panels' cards: design section 二). Never a bare code: a
        result nobody has words for is said as the ledger's own code only after 「帳本代碼」."""
        lines = []
        if self.target:
            lines.append(unquote(self.target)[:300])
        elif self.what == "button":
            lines.append("按鈕" + (f"（{ACTION_WORDS.get(self.kind, self.kind)}）" if self.kind else ""))
        if self.result:
            said = RESULT_WORDS.get(self.result) or reason_words(self.result)
            if not said or said == self.result:
                said = f"帳本代碼 {self.result}"
            lines.append("帳本：" + said + (f"（{self.reason}）" if self.reason and self.reason != said else ""))
        elif self.bucket == "missing":
            lines.append("帳本：沒有紀錄")
        if self.landing:
            lines.append(f"轉到 {unquote(self.landing)[:200]}" + (f"（{self.hops} 跳）" if self.hops > 1 else ""))
        if self.status:
            lines.append(f"HTTP {self.status}")
        if self.fetched_at:
            lines.append(f"抓取 {self.fetched_at[:16].replace('T', ' ')}")
        lines.append(ZONE_WORDS[self.zone])
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"n": self.n, "what": self.what, "bucket": self.bucket, "href": self.href, "target": self.target,
                "text": self.text, "zone": self.zone, "result": self.result, "reason": self.reason,
                "fetched_at": self.fetched_at, "status": self.status, "landing": self.landing, "hops": self.hops,
                "kind": self.kind, "who": WHO.get(self.bucket, ""), "tip": self.tip()}


ZONE_WORDS = {"in": "在正文區（進庫）", "files": "在附件區（進庫）", "out": "在選單、頁首、頁尾等不進庫的地方"}


def _off_file(verdict: str | None) -> bool:
    """A link verdict that says no file can come from there (outcome._off_file): every out:<rule> but a sign-in and
    media."""
    v = verdict or ""
    return v.startswith("out:") and not v.startswith("out:login") and v != "out:media"


def _kept_out(item: Item, verdict: str | None) -> None:
    """item, which has no url row, by the verdict that kept its address out (outcome._kept_out: a broken link, a
    sign-in, a rule, the next round, a writing nobody decided; anything else found and never kept)."""
    v = verdict or ""
    item.bucket = verdict_bucket(v)
    if item.bucket == "missing":
        item.result, item.reason = "not_discovered", ""
    else:
        item.result, item.reason = v, reason_words(v)


def _set(item: Item, bucket: str, result: str, reason: str) -> None:
    item.bucket, item.result, item.reason = bucket, result, reason


def _apply(item: Item, row: sqlite3.Row | None, ledger: Ledger, *, file_like: bool = False) -> None:
    """item's bucket and facts from its url row, followed to where its redirects land, by outcome's resource rules
    (the review of 11-16): a chain that ends on no row is what the last hop's link verdict says; a loop or a chain of
    more than MAX_HOPS a server error; a file-like resource (its role is a file's or an image's, or a row along its
    redirects was found as one) that ends on a web page that came back is not_file, and one sent to a school host's
    home page is gone (gone_home); a Drive metadata row that is ok is what its item came to."""
    if row is None:
        item.bucket = "missing"
        return
    ln = ledger.land(row) if row["result"] == "redirect" else Landing(row, [])
    if ln.hops:
        item.landing, item.hops = ln.hops[-1], len(ln.hops)
    file_like = file_like or ln.expected_file
    home = file_like and bool(ln.hops) and outcome.school_home(ln.at)
    land = ln.row
    if land is None:
        if ln.why == "loop":
            return _set(item, "failed", "server_error", "轉址繞圈，或超過 20 跳")
        if ln.why == "no_address":
            return _set(item, "review", "redirect", "轉址沒有終點")
        found = ledger.link(ln.last_id, ln.at)
        verdict = found["verdict"] if found is not None else ""
        if home and _off_file(verdict):
            return _set(item, "gone", "gone_home", "")
        if file_like and _off_file(verdict):
            return _set(item, "failed", "not_file", "應該是檔案，卻轉到" + reason_words(verdict))
        return _kept_out(item, verdict)
    result, reason = own_result(land), land["reason"] or ""
    item.status = land["status"]
    item.fetched_at = taipei(land["fetched_at"])
    if (land["kind0"] or land["kind"]) == "drive" and result == "ok":
        result, reason = ledger.drive_settled(land)
    came_back = land["kind"] == "page" and result in outcome.PAGE_CAME_BACK
    if home and (result == "not_file" or came_back):
        return _set(item, "gone", "gone_home", "")
    if file_like and came_back:
        return _set(item, "failed", "not_file", "應該是檔案，回來是網頁")
    _set(item, bucket_of(result, reason), result, "" if result == NO_AREA else reason_words(reason))


def _file_like_role(res: sqlite3.Row | None) -> bool:
    return res is not None and res["role"] in outcome.FILE_LIKE_ROLES


def _google(item: Item, page_id: int, ledger: Ledger) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    """(the page's drive:<id> resource, the row that stands for it) of a Drive or Docs address (a link, a frame, a
    thumbnail of the file): the resource decides alone when it says need no (a video listed only: item is set then),
    else its row, or drive_item's."""
    drive = ledger.drive_res(page_id, item.target)
    if drive is not None and drive["need"] == "no":
        _set(item, res_bucket(drive["need"], drive["why"]) or "rule", drive["why"] or "rule", reason_words(drive["why"]))
        return drive, None
    return drive, ledger.drive(page_id, item.target)


def _link(item: Item, page_id: int, ledger: Ledger) -> None:
    """A link's bucket (facts §2.3): the target's url row (or its other scheme), followed; a Drive link by its page's
    drive:<id> resource (a video listed only is a rule's, whatever its metadata row says) or its item; else the page's
    resource of that address (an <a> straight to a picture is written there only, crawler._parts: an album's photo has
    no url row); else the link row's verdict; else what scope.judge says of the address (a link to an internal service
    is never written to the link table); else not in the ledger. Whether it is file-like (outcome.FILE_LIKE_ROLES) is
    the page's resource of it."""
    res = ledger.res(page_id, item.target)
    row = ledger.by_url(item.target)
    if row is None and scope.is_google_link(item.target):
        drive, row = _google(item, page_id, ledger)
        if drive is not None and drive["need"] == "no":
            return
        res = res or drive
    if row is not None:
        return _apply(item, row, ledger, file_like=_file_like_role(res))
    if res is not None:
        decided = res_bucket(res["need"], res["why"])
        if decided is not None:
            return _set(item, decided, res["why"] or res["need"], reason_words(res["why"]))
        if res["res_id"] is not None:
            return _apply(item, ledger.by_id(res["res_id"]), ledger, file_like=_file_like_role(res))
    found = ledger.link(page_id, item.target)
    if found is not None:
        return _kept_out(item, found["verdict"])
    judged = scope.judge(item.target)
    if not judged.ok and judged.reason not in scope.SOFT_OUT:
        verdict = f"out:{judged.reason}"
        return _set(item, verdict_bucket(verdict), verdict, reason_words(verdict))
    item.bucket = "missing"


def _image(item: Item, page_id: int, key: str, ledger: Ledger) -> None:
    """An image's bucket (facts §6): its page_res row (need, why, res_id), else a url row of its address."""
    res = ledger.res(page_id, key)
    if res is not None:
        decided = res_bucket(res["need"], res["why"])
        if decided is not None and res["res_id"] is None:
            item.bucket, item.result, item.reason = decided, res["why"] or res["need"], reason_words(res["why"])
            return
        row = ledger.by_id(res["res_id"]) if res["res_id"] is not None else ledger.by_url(item.target)
        if row is None and decided is not None:
            item.bucket, item.result, item.reason = decided, res["why"] or res["need"], reason_words(res["why"])
            return
        _apply(item, row, ledger, file_like=True)
    else:
        row = ledger.by_url(item.target)
        if row is None and scope.is_google_link(item.target):  # a Drive file's thumbnail: the file's own row
            drive, row = _google(item, page_id, ledger)
            if drive is not None and drive["need"] == "no":
                return
        _apply(item, row, ledger, file_like=True)
        host = urlsplit(item.target).hostname or ""
        if item.bucket == "missing" and (host.endswith("googleusercontent.com")
                                         or links.is_google_sites_image(item.target)):
            item.bucket, item.reason = "review", "協作平台的圖每次網址都不同，對不到帳本"
    if item.bucket in ("got", "look"):
        land = ledger.by_url(item.landing) if item.landing else ledger.by_url(item.target)
        if land is None and res is not None and res["res_id"] is not None:
            land = ledger.by_id(res["res_id"])
            land = ledger.land(land).row if land is not None and land["result"] == "redirect" else land
        if land is not None and land["warc"] and land["warc_offset"] is not None:
            item.image = land["url"]


def _button(item: Item, page: dict, target: str, argument: str, ledger: Ledger,
            press: postback.Press | None) -> None:
    """A button's bucket (facts §4): the press made of it on this stored page (or its row's press), else its
    unhandled page_res row. page: the list's url row, its warc and offset those of the record shown."""
    act = ledger.action(page["id"], page["warc"], page["warc_offset"], target, argument, press)
    if act is not None:
        item.kind = act["kind"]
        item.result = act["result"]
        item.status = act["status"]
        item.fetched_at = _when(act["done"])
        item.reason = reason_words(act["reason"])
        file_like = act["kind"] == "download"
        if act["state"] == "superseded":  # and no later press of its row: outcome's not_discovered
            item.bucket, item.result, item.reason = "missing", "superseded", ""
        elif act["result"] == "redirect" and act["location"]:
            loc = scope.canonical(act["location"]) or act["location"]
            item.target = loc
            row = ledger.by_url(loc)
            if row is not None:
                _apply(item, row, ledger, file_like=file_like)
            else:
                found = ledger.link(page["id"], loc) or ledger.link(page["id"], act["location"])
                verdict = found["verdict"] if found is not None else ""
                if file_like and _off_file(verdict):
                    _set(item, "failed", "not_file", "應該是檔案，卻轉到" + reason_words(verdict))
                else:
                    _kept_out(item, verdict)
        else:
            item.bucket = bucket_of(act["result"], act["reason"])
        return
    res = ledger.res(page["id"], postback.button_key(target, argument))
    if res is not None:
        item.result = res["why"] or res["need"]
        item.reason = reason_words(res["why"])
        item.bucket = res_bucket(res["need"], res["why"]) or "review"
        return
    item.bucket = "missing"


def _when(epoch: float | None) -> str:
    """An action's done time (seconds since 1970), Asia/Taipei."""
    if not epoch:
        return ""
    return datetime.fromtimestamp(float(epoch), TAIPEI).isoformat(timespec="seconds")


# ---------------------------------------------------------------- the page

@dataclass
class Check:
    """What one check returns: the summary (also the JSON of /api/check) and the stored page made safe to show."""

    summary: dict
    html: str = ""
    items: list[Item] = field(default_factory=list)


STATES = {  # the summary's state, and what the panel says of it (Traditional)
    "ok": "",
    "no_archive": "沒有掛上爬蟲存檔",
    "not_in_ledger": "帳本裡沒有這個網址",
    "not_fetched": "帳本裡有，但沒有抓下來",
    "not_html": "存的不是網頁",
    "unreadable": "存檔讀不出來",
    "busy": "帳本正在寫入",
    "empty": "沒有給網址",
}


def _page_facts(row: sqlite3.Row, hops: list[str]) -> dict:
    url = row["url"]
    prof = profiles.profile_for(url)
    return {"id": row["id"], "url": url, "result": row["result"], "bucket": bucket_of(row["result"], row["reason"]),
            "result_words": RESULT_WORDS.get(own_result(row), row["result"]), "state": row["state"],
            "reason": reason_words(row["reason"]), "status": row["status"], "fetched_at": taipei(row["fetched_at"]),
            "ctype": row["ctype"] or "", "kind": row["kind"], "site": row["owner_site"] or row["site"],
            "profile": prof.name if prof is not None else "", "platform": prof.platform if prof is not None else "",
            "reading": pagetext.page_reading(url), "hops": hops,
            "flags": [f for f in (row["flags"] or "").split(",") if f], "page_type_verdict": row["type_verdict"]}


def _is_html(ctype: str, kind: str | None, body: bytes) -> bool:
    ctype = (ctype or "").lower()
    if "html" in ctype:
        return True
    if ctype and not ctype.startswith("text/plain"):
        return False
    head = body[:2048].lstrip().lower()
    return kind in ("page", "embed", None) and (head.startswith((b"<!doctype html", b"<html")) or b"<body" in head)


def decode(body: bytes, ctype: str | None) -> str:
    """The stored page as text, as the export reads it: strict decoding (the HTTP charset, UTF-8, Big5), else
    export.redecode (the page's own label, losing the fewest characters; facts §3)."""
    try:
        return decode_body(body, ctype)
    except DecodeError:
        from nknu.archive.export import redecode  # heavier: only for the 116 pages strict decoding refuses
        return redecode(body, ctype)[0]


def resolve_ref(ref: str, library: sqlite3.Connection | None) -> tuple[str, str]:
    """(the address to check, the library document id it came from or ''): an address as given (a scheme added when
    there is none), else a document id of the library turned into its address. A document a button brought keeps
    its address's #按钮=… or #第N页 (design section 四: check shows that press's own record)."""
    ref = (ref or "").strip()
    if not ref:
        return "", ""
    if ref.startswith(("http://", "https://")):
        return ref, ""
    if library is not None:
        from nknu.ops.lookup import resolve
        found = resolve(library, ref)
        if found.doc_id is not None:
            row = library.execute("SELECT url FROM docs WHERE doc_id = ?", (found.doc_id,)).fetchone()
            if row and str(row[0] or "").startswith(("http://", "https://")):
                return str(row[0]), found.doc_id
    if "/" in ref or "." in ref:
        return "https://" + ref, ""
    return ref, ""


def library_doc(library: sqlite3.Connection | None, *refs: str) -> dict | None:
    """The library's document of the first of these (a document id, or an address) that names one, {doc_id, title},
    or None."""
    if library is None:
        return None
    from nknu.ops.lookup import resolve
    for ref in refs:
        if not ref:
            continue
        found = resolve(library, ref)
        if found.doc_id is not None:
            row = library.execute("SELECT title FROM docs WHERE doc_id = ?", (found.doc_id,)).fetchone()
            return {"doc_id": found.doc_id, "title": row[0] if row else ""}
    return None


def check(archive: Path | None, ref: str, *, library: sqlite3.Connection | None = None,
          ledgers: LedgerCache | None = None) -> Check:
    """One page checked: ref is an address or a library document id. Never raises for a page that is not there, not
    fetched, not HTML, or a ledger busy being written: the summary's state says so. ledgers: a kept connection
    (nknu.ops.web keeps one), else the ledger is opened and closed here.

    An export address of a press (<list>#第N页, <list>#按钮=…, from a document id or pasted) is checked as that press's
    own archived answer (its action row's record), its buttons by the presses made on it; the list's first page
    stands in, said so, when the ledger has no such press (the review of 11-16)."""
    url, from_doc = resolve_ref(ref, library)
    base_url, label = press_label(url)
    summary: dict = {"ref": ref, "url": url, "raw_ref": url, "from_doc": from_doc, "press": None, "state": "ok",
                     "page": None, "doc": None, "counts": {w: {} for w, _ in WHATS}, "problems": [], "caveats": [],
                     "regions": {}, "snapshot": False}

    def said(state: str) -> Check:
        summary["state"] = state
        summary["state_words"] = STATES[state]
        return Check(summary)

    if not url:
        return said("empty")
    canon = scope.canonical(base_url) or base_url
    summary["url"] = canon
    if archive is None or not (Path(archive) / LEDGER).exists():
        summary["doc"] = library_doc(library, from_doc, url, canon)
        return said("no_archive")
    try:
        with _ledger(Path(archive), ledgers) as conn:
            summary["snapshot"] = isinstance(conn, Snapshot)
            return _check(Path(archive), Ledger(conn), summary, said, library, url, canon, from_doc, label)
    except sqlite3.OperationalError as exc:
        if not _BUSY.search(str(exc)):
            raise
        log.warning("账本读不了：%s", exc)
        summary["read_error"] = str(exc)
        return said("busy")


_BUSY = re.compile(r"(?i)locked|busy|readonly|read-only|unable to open|disk i/o|malformed")
# a site's home as people type it (c.nknu.edu.tw/aca) is in the ledger only as the page the crawler reached
# (…/aca/default.aspx): 5 of the 27 c.nknu homes have no bare spelling at all (trial 2 of 11-16) 【Claude】
HOME_PAGES = ("default.aspx", "index.aspx", "index.html", "index.htm", "index.php", "index.asp")


def home_spelling(ledger: Ledger, canon: str) -> sqlite3.Row | None:
    """The ledger row of the same address with a trailing / or a home page's file name, for an address that ends in
    a folder (no file name, no query): None when there is none."""
    parts = urlsplit(canon)
    last = parts.path.rsplit("/", 1)[-1]
    if parts.query or "." in last:
        return None
    folder = canon.split("#", 1)[0].rstrip("/") + "/"
    for spelt in (folder, *(folder + name for name in HOME_PAGES)):
        if spelt != canon and (row := ledger.by_url(scope.canonical(spelt) or spelt)) is not None:
            return row
    return None


def _check(archive: Path, ledger: Ledger, summary: dict, said, library: sqlite3.Connection | None, url: str,
           canon: str, from_doc: str, label: str) -> Check:
    asked = ledger.by_url(canon)
    if asked is None and (alt := home_spelling(ledger, canon)):
        asked, summary["spelled"] = alt, alt["url"]
    if asked is None:
        summary["doc"] = library_doc(library, from_doc, url, canon)
        return said("not_in_ledger")
    ln = ledger.land(asked) if asked["result"] == "redirect" else Landing(asked, [])
    row = ln.row
    if row is None:
        summary["page"] = _page_facts(asked, ln.hops)
        summary["doc"] = library_doc(library, from_doc, url, asked["url"])
        return said("not_fetched")
    summary["page"] = page = _page_facts(row, ln.hops)
    summary["doc"] = library_doc(library, from_doc, url, row["url"], asked["url"])
    shown: dict = dict(zip(row.keys(), row, strict=True))  # the record shown: the row's, or a press's answer
    if label:
        act = ledger.press_answer(row["id"], label)
        summary["press"] = {"label": label[1:], "found": act is not None}
        if act is not None:
            summary["press"].update({"id": act["id"], "kind": act["kind"], "result": act["result"],
                                     "result_words": RESULT_WORDS.get(act["result"], act["result"])})
            page.update({"bucket": bucket_of(act["result"], act["reason"]), "result": act["result"],
                         "result_words": RESULT_WORDS.get(act["result"], act["result"]),
                         "reason": reason_words(act["reason"]), "status": act["status"],
                         "fetched_at": _when(act["done"])})
            shown.update({"warc": act["warc"], "warc_offset": act["warc_offset"], "ctype": act["ctype"],
                          "sniff": act["sniff"], "kind": None})
    if not shown["warc"] or shown["warc_offset"] is None:
        return said("not_fetched")
    try:
        _url, _status, headers, body = postback.read_response(archive, shown["warc"], shown["warc_offset"])
    except (ValueError, OSError) as exc:
        log.warning("存档读不出来：%s@%s（%s）", shown["warc"], shown["warc_offset"], exc)
        page["read_error"] = str(exc)
        return said("unreadable")
    ctype = headers.get("content-type") or shown["ctype"] or ""
    if not _is_html(ctype, shown["kind"], body):
        page["stored_type"] = ctype or (shown["sniff"] or "")
        page["bytes"] = len(body)
        return said("not_html")
    made = Marked(decode(body, ctype), shown, ledger)
    summary.update(made.summary())
    summary["caveats"] = caveats(summary, made, ledger)
    summary["state_words"] = ""
    return Check(summary, made.html(), made.items)


_MARKER = re.compile(r"\[[a-z_-]+\]")  # a link row's text the crawler writes for what is no <a>: [refresh], [click]…


def caveats(summary: dict, made: Marked, ledger: Ledger) -> list[str]:
    """The design's section 四 warnings that hold for this page, each a key of check_page's words."""
    page = summary["page"]
    out = []
    if summary.get("snapshot"):
        out.append("snapshot")
    if summary.get("spelled"):
        out.append("spelled")
    press = summary.get("press")
    if press is not None:
        out.append("press_answer" if press["found"] else "press_unfound")
    if page["hops"]:
        out.append("redirected")
    n_actions = ledger.conn.execute("SELECT count(*) FROM action WHERE list_id = ? AND kind IN ('pager', 'detail')",
                                    (page["id"],)).fetchone()[0]
    if n_actions and press is None:
        out.append("pressed")
    # the link table holds every fetch's links (and the presses' answers'): those this stored page has, the crawler's
    # own reading of it (links.extract: onclick, data-href, frames, script-opened addresses…), are no older ones
    seen = made.found_targets()
    linked = ledger.conn.execute("SELECT dst, text FROM link WHERE src = ?", (page["id"],)).fetchall()
    older = sum(r[0] not in seen and not _MARKER.fullmatch(r[1] or "") for r in linked)
    if older:
        out.append("older_links")
        summary["older_links"] = older
    flags = set(page["flags"])
    if flags & {"js_hint", "js_redirect"} or page.get("page_type_verdict") == "js" or \
            (page["reason"] and "JavaScript" in page["reason"]):
        out.append("js")
    if page["profile"] in ("elec", "dataopen2search"):
        out.append("zone_changed")
    if made.how == pagetext.NOTHING:
        out.append("nothing_read")
    # no 「可能整頁改讀」: assemble.read_again's rule needs the site's template lines, so a guess from one page was
    # wrong on 199 of 400 pages (the trial of 11-16); 文件追蹤 shows what the library holds
    if made.frameset:
        out.append("frameset")
    return out


# ---------------------------------------------------------------- marking and making the page safe

# elements shown as they are (attributes filtered by ATTRS), and those dropped with all they hold; any other element
# is left out but its children are shown (a custom element, an unknown tag). 【Claude】 lists, whitelist first: what
# is not named never reaches the page.
_SHOWN = """a abbr acronym address article aside b bdi bdo big blockquote br button caption center cite code
col colgroup dd del details dfn dialog div dl dt em fieldset figcaption figure font footer form h1 h2 h3 h4 h5 h6 header
hgroup hr i img input ins kbd label legend li main mark menu nav ol optgroup option output p pre q rp rt ruby s samp
section select small span strike strong sub summary sup table tbody td textarea tfoot th thead time tr tt u ul var
wbr"""
_DROPPED = """script style noscript template object embed applet param base link meta title svg math source
track audio video canvas map area frameset noframes portal head"""
FRAMES = ("iframe", "frame")
EMBEDS = ("embed", "object")  # dropped, but one the crawler read as a framed page is drawn as a box like a frame
_VOID = "br col hr img input wbr"
_ATTRS = """class id lang dir align valign width height border cellpadding cellspacing colspan rowspan bgcolor
color face size nowrap scope headers alt type start value span summary checked selected disabled readonly placeholder
rows cols multiple label for datetime abbr axis frame rules hspace vspace clear noshade compact hidden open
reversed"""
# no name: a <form name> or <img name> is a named property of the frame's document and would hide the very methods the
# panel's script calls there (document.querySelectorAll, body…: the review of 11-16)
SHOWN, DROPPED, VOID, ATTRS = (frozenset(words.split()) for words in (_SHOWN, _DROPPED, _VOID, _ATTRS))
_CSS_URL = re.compile(r"url\s*\([^)]*\)", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_CSS_BAD = re.compile(r"expression\s*\(|javascript:|behavior\s*:|-moz-binding|\\", re.IGNORECASE)
# image-set() names its pictures as bare strings, no url(): a function nobody knows makes the declaration void
_CSS_IMAGE_SET = re.compile(r"(?:-webkit-)?image-set\s*\(", re.IGNORECASE)

# the marks, inside the stored page (it keeps the school's white whatever the panel's mode: design section 二's
# 「原样白底」); the colours are theme.css's light stroke steps (--good-s, --warn-s, --bad-s, --mute-s: each 3.5:1 or
# more on white as a 2px line; until 2026-11-21 the fills' amber, 2.9:1, and a grey of 3.3:1), never --acc nor violet
MARK_CSS = """
html{color-scheme:light;background:#fff}body{margin:8px;color:#161615;background:#fff;
font:14px/1.5 system-ui,-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif}
[data-b]{cursor:pointer;--c:#55534e}
[data-b=got],[data-b=look]{--c:#2e9a5a}[data-b=next],[data-b=review]{--c:#b57600}
[data-b=failed],[data-b=missing]{--c:#a42d15}[data-b=notlink]{cursor:default}
a[data-b]{color:#161615;text-decoration:underline 2px var(--c);text-underline-offset:3px}
a[data-b=review],a[data-b=missing]{text-decoration-style:dashed}a[data-b=gone]{text-decoration:line-through 2px #55534e}
a[data-b]:not([data-b=notlink])::after{content:"";display:inline-block;width:6px;height:6px;margin:0 1px 0 3px;
border-radius:50%;background:var(--c);vertical-align:middle}
a[data-b=look]::after{background:#b57600}a[data-b=notlink]{text-decoration:underline 1px #cfcec6}
img[data-b],input[data-b],button[data-b],.nk-box[data-b],.nk-noimg[data-b],.nk-bg[data-b],.nk-act[data-b]{outline:3px
solid var(--c);outline-offset:-1px}.nk-act[data-b]{outline-width:2px;outline-style:dotted}
img[data-b=review],img[data-b=missing],.nk-noimg[data-b=review],.nk-noimg[data-b=missing],input[data-b=review],
input[data-b=missing],button[data-b=missing],button[data-b=review],.nk-box[data-b=missing],.nk-box[data-b=review],
.nk-bg[data-b=missing],.nk-bg[data-b=review]{outline-style:dashed}
.nk-bg{background-size:cover;background-repeat:no-repeat}
img[data-b=look]{outline-color:#2e9a5a;box-shadow:0 0 0 6px #b57600}
.nk-noimg{display:inline-block;box-sizing:border-box;min-width:48px;min-height:32px;vertical-align:middle;
background:repeating-linear-gradient(45deg,#f3f2ee 0 6px,#e7e6e0 6px 12px);border:1px solid #cfcec6}
.nk-box{display:block;margin:6px 0;padding:10px 12px;border:1px solid #cfcec6;border-radius:6px;background:#f3f2ee;
color:#5c5b57;font-size:13px;overflow-wrap:anywhere}
.nk-in{outline:1.5px dashed #161615;outline-offset:3px}.nk-files{outline:1.5px dashed #5c5b57;outline-offset:2px}
.nk-out{background-color:rgba(120,118,112,.13)!important;color:#8d8b85!important}.nk-out img{opacity:.75}
.nk-label{position:absolute;z-index:5;padding:1px 7px;border-radius:5px;background:#161615;color:#fff;font:600 11px/17px
system-ui,sans-serif;pointer-events:none;white-space:nowrap}
.nk-flash{animation:nkflash .5s ease-in-out 3 alternate}
@keyframes nkflash{from{box-shadow:0 0 0 0 rgba(196,141,37,0)}to{box-shadow:0 0 0 6px rgba(196,141,37,.75)}}
"""

# After the page's own <style> blocks and the marks: nothing is drawn over anything else. The archive holds no style
# sheet files, so a page comes with only the scraps of style written into it (a bar's fixed height, a block pulled up
# by a negative margin, a menu placed absolutely) and its menus lay over one another (工業設計學系's pages, 2026-11-18).
# Every element of the page goes back into the plain flow with the browser's own margins; the marks keep theirs. The
# two :not(#…) only raise the rule over a page's own rules that name an id.
FLOW_CSS = """
body *:not(#nk-a):not(#nk-b):not(.nk-label):not(.nk-box):not(.nk-noimg):not(.nk-bg):not(img){position:static!important;
float:none!important;height:auto!important;max-height:none!important;min-height:0!important;transform:none!important;
margin:revert!important}
"""


def clean_css(text: str) -> str:
    """A style sheet of the school's that loads nothing by itself: url(…) as none, image-set(…) made void (its
    pictures are bare strings), @import gone; '' for one that hides a load behind an escape or an expression. The
    page's policy (design section 四) stays the backstop."""
    if _CSS_BAD.search(text):
        return ""
    text = _CSS_IMAGE_SET.sub("nk-blocked(", text)
    return _CSS_IMPORT.sub("", _CSS_URL.sub("none", text)).replace("</", "<\\/")


def _attr(name: str, value: object) -> str:
    return f' {name}="{html.escape("" if value is None else str(value), quote=True)}"'


def res_address(url: str) -> str:
    """Where the panel's page gets an archived image: our own /check/res."""
    return "/check/res?u=" + quote(url, safe="")


def _first_files_containers(tree: LexborHTMLParser, url: str) -> tuple[set[int], set[int]]:
    """(the <a> nodes the attachment-area selectors of the page's profile match, the elements around them those
    selectors name): content.links._file_areas' rule; a selector ending in ' a' names its container without it."""
    p = profiles.profile_for(url)
    if p is None:
        return set(), set()
    sels = [d.files for d in p.details if d.files] + [r.detail_files for r in p.rows if r.detail_files]
    anchors: set[int] = set()
    boxes: set[int] = set()
    for sel in sels:
        try:
            anchors |= {n.mem_id for n in tree.css(sel)}
            outer = ", ".join(s.strip()[:-2].strip() for s in sel.split(",") if s.strip().endswith(" a"))
            if outer:
                boxes |= {n.mem_id for n in tree.css(outer)}
        except (SelectolaxError, ValueError):  # a selector this lexbor cannot read: no attachment area from it
            log.warning("附件区的选择器读不了：%s", sel)
    return anchors, boxes


# elements never an item of their own (what they hold is, when shown)
_NEVER_ITEM = frozenset({"html", "head", "script", "style", "meta", "link", "title", "base", "noscript", "template",
                         "param", "source", "track"})


def _picture_src(attrs: dict) -> str:
    """The picture an <img> shows, as the crawler reads it (links._images): a lazy loader's address first (the src
    is then a placeholder), else src, else the largest of a srcset."""
    for name in (*links._LAZY, "src"):
        v = (attrs.get(name) or "").strip()
        if v and not v.lower().startswith(("about:", "javascript:")):
            return v
    return links.largest_in_srcset(attrs.get("srcset") or attrs.get("data-srcset") or "") or ""


def _script_address(node: LexborNode) -> str:
    """An address a click on this element opens, as the crawler reads it: one in an on* handler (links._handler) or
    in data-href, data-url, data-link (links._attr_links); '' when none."""
    for _name, code in links._handlers(node):
        for u in links._calls(code)[0]:
            if u.strip():
                return u.strip()
    attrs = node.attributes
    if node.tag not in links._MEDIA_TAGS and "data-code" not in attrs:
        for name in links._ATTR_LINKS:
            v = (attrs.get(name) or "").strip()
            if v and links._ATARI not in v and links.URLISH.search(v):
                return v
    return ""


def _background_src(node: LexborNode) -> str:
    """A background picture of this element, as the crawler reads it (links._images): in its style, its data-bg or
    data-background, or the background attribute of a body or a table's parts; '' when none."""
    attrs = node.attributes
    style = attrs.get("style") or ""
    if "background" in style.lower():
        for _q, src in links._BG_URL.findall(style):
            if src.strip():
                return src.strip()
    v = (attrs.get("data-bg") or attrs.get("data-background") or "").strip()
    if v:
        return v
    if node.tag in ("body", "table", "td", "th", "tr"):
        return (attrs.get("background") or "").strip()
    return ""


class Marked:
    """One stored page parsed, its regions and items found and looked up (the work of check, kept to draw).

    row: the list's (or page's) url row as a dict, its warc and warc_offset those of the record shown."""

    def __init__(self, text: str, row: dict, ledger: Ledger) -> None:
        self.url = row["url"]
        self.row = row
        self.profile = profiles.profile_for(self.url)
        self.tree = LexborHTMLParser(text)
        self.frameset = self.tree.body is None or self.tree.css_first("frameset") is not None
        how, _ = pagetext._parts(self.tree, self.url)  # how page_roots reads it: area / nothing / whole / body
        self.how = how
        roots = pagetext.page_roots(self.tree, self.url)
        self.in_ids = {n.mem_id for n in roots}
        self.holders: set[int] = set()
        for node in roots:
            up = node.parent
            while up is not None and up.mem_id not in self.holders:
                self.holders.add(up.mem_id)
                up = up.parent
        self.file_links, self.file_boxes = _first_files_containers(self.tree, self.url)
        self.area_chars = sum(len("".join((n.text(deep=True) or "").split())) for n in roots)
        body = self.tree.body
        self.body_chars = len("".join((body.text(deep=True) or "").split())) if body is not None else 0
        base_node = self.tree.css_first("base[href]")
        self.base = page_base(self.url, base_node.attributes.get("href") if base_node else None)
        # the crawler's own reading of the page (crawler._parts reads links.extract): what it wrote to the ledger
        try:
            self.read = links.extract(text, self.url, profiles.content_css(self.url))
        except Exception as exc:  # noqa: BLE001 - a page the crawler's reader trips on is still drawn
            log.warning("爬虫的读法读不了这一页：%s（%s）", self.url, exc)
            self.read = None
        self.presses = self._presses(text)
        self.items: list[Item] = []
        self.marks: dict[int, Item] = {}
        self._find(ledger)

    def _presses(self, text: str) -> dict[tuple[str, str], postback.Press]:
        """What the platform plans for each button of this page (postback.read_page, pure: nothing pressed), by
        (target, argument): a row's button is matched to its row's press by its kind and row_key."""
        if self.profile is None:
            return {}
        try:
            got = postback.read_page(self.profile, self.url, text, site=self.row.get("owner_site") or "",
                                     list_url=self.url, page=self.read)
        except Exception as exc:  # noqa: BLE001 - a page the platform's reader trips on: buttons by target only
            log.warning("平台读不了这一页的按钮：%s（%s）", self.url, exc)
            return {}
        out = {(p.target, p.argument): p for p in got.presses}
        if got.next is not None:
            out.setdefault((got.next.target, got.next.argument), got.next)
        return out

    def found_targets(self) -> set[str]:
        """Every address this stored page links, as the crawler read it (links.extract: onclick, data-href, frames,
        script-opened addresses, meta refresh…) and as the panel marked it."""
        out = {i.target for i in self.items if i.target}
        if self.read is not None:
            base = self.read.base
            out |= {t for href, _ in self.read.links if (t := scope.canonical(href, base))}
            out |= {t for f in self.read.frames if (t := scope.canonical(f.src, base))}
        return out

    def _zone(self, node: LexborNode) -> str:
        if node.mem_id in self.file_links:
            return "files"
        up: LexborNode | None = node
        while up is not None:
            if up.mem_id in self.in_ids:
                return "in"
            if up.mem_id in self.file_boxes:
                return "files"
            up = up.parent
        return "out"

    def _dropped(self, node: LexborNode) -> bool:
        """Whether node sits in an element the page never shows (script, template, an object's fallback…): not
        counted."""
        up = node.parent
        while up is not None:
            if up.tag in ("script", "template", "noscript", "style", "head", "svg", "math", "object", "embed",
                          "applet"):
                return True
            up = up.parent
        return False

    def _add(self, node: LexborNode, what: str, href: str, target: str, text: str) -> Item:
        item = Item(len(self.items) + 1, what, "missing", href, target, " ".join(text.split())[:120], self._zone(node))
        self.items.append(item)
        self.marks[node.mem_id] = item
        return item

    def _find(self, ledger: Ledger) -> None:
        """Each element the crawler reads a link, a picture, a frame or a button from (links.extract's readings, one
        item per element, the first that applies): a postback, an <a>/<area> href (a javascript: one by the address
        its script opens), a frame, an embed or object, a picture (its lazy source first, else src, else the largest
        of a srcset), an address in an on* handler or a data-href, a background picture."""
        page_id = self.row["id"]
        buttons: dict[int, tuple[str, str]] = {}
        for node, target, argument in postback._postbacks(self.tree, self.profile):
            buttons.setdefault(node.mem_id, (target, argument))
        gs_keys = links.google_sites_keys(self.read, page_id) if self.read is not None else {}
        for node in self.tree.root.traverse(include_text=False):
            tag = node.tag or ""
            if not tag[:1].isalpha() or tag in _NEVER_ITEM:
                continue
            attrs = node.attributes
            if node.mem_id in buttons:
                if self._dropped(node):
                    continue
                target, argument = buttons[node.mem_id]
                label = node.text(deep=True) or attrs.get("value") or attrs.get("alt") or target
                item = self._add(node, "button", "", "", label or "")
                _button(item, self.row, target, argument, ledger, self.presses.get((target, argument)))
            elif tag in ("a", "area") and "href" in attrs:
                if not self._dropped(node):
                    self._href(node, attrs.get("href") or "", "link",
                               node.text(deep=True) or attrs.get("alt") or "", page_id, ledger)
            elif tag in FRAMES or tag in EMBEDS:
                src = links._frame_src(node)
                if src and not self._dropped(node):
                    self._href(node, src, "link", f"[{tag}]", page_id, ledger)
            elif tag == "img" or (tag == "input" and (attrs.get("type") or "").lower() == "image"):
                src = _picture_src(attrs)
                if src and not self._dropped(node):
                    self._picture(node, src, attrs.get("alt") or "", page_id, gs_keys, ledger)
            elif (said := _script_address(node)) and not self._dropped(node):
                self._href(node, said, "link", links._label(node) or said, page_id, ledger)
            elif (bg := _background_src(node)) and not self._dropped(node):
                if scope.extension(bg) in scope.ATTACHMENT_EXT:  # links._background: a document is a link
                    self._href(node, bg, "link", "[background]", page_id, ledger)
                else:
                    self._picture(node, bg, "", page_id, gs_keys, ledger)

    def _picture(self, node: LexborNode, src: str, alt: str, page_id: int, gs_keys: dict[str, str],
                 ledger: Ledger) -> None:
        if src.lower().startswith("data:"):
            item = self._add(node, "image", src[:60], "", alt)
            _image(item, page_id, src[:links.DATA_SRC_MAX], ledger)
            return
        target = scope.canonical(src, self.base)
        if target is None:
            item = self._add(node, "image", src, "", alt)
            item.bucket = "notlink"
            return
        item = self._add(node, "image", src, target, alt)
        # a Google Sites runtime picture is written by where it sits, not its address (links.google_sites_keys)
        _image(item, page_id, gs_keys.get(src, target), ledger)

    def _href(self, node: LexborNode, href: str, what: str, text: str, page_id: int, ledger: Ledger) -> None:
        """A link's item. A javascript: href is a link to the address its script opens (window.open, location…:
        links._calls, as links._anchor reads it), and no link when it opens none."""
        low = href.strip().lower()
        if low.startswith("javascript:"):
            urls = [u.strip() for u in links._calls(href.strip())[0] if u.strip()]
            target = next((t for u in urls if (t := scope.canonical(u, self.base))), None)
        elif low.startswith(NOT_LINK):
            target = None
        else:
            target = scope.canonical(href, self.base)
        item = self._add(node, what, href, target or "", text)
        if target is None:
            item.bucket = "notlink"
            return
        _link(item, page_id, ledger)

    # ------------------------------------------------ what check returns

    def summary(self) -> dict:
        counts: dict[str, dict[str, int]] = {w: {} for w, _ in WHATS}
        for i in self.items:
            counts[i.what][i.bucket] = counts[i.what].get(i.bucket, 0) + 1
        problems = sorted((i for i in self.items if i.bucket in PROBLEMS),
                          key=lambda i: (ZONE_ORDER.index(i.zone), PROBLEMS.index(i.bucket), i.n))
        verdict = {"fix": {z: 0 for z in ZONE_ORDER}, "check": 0, "next": 0}
        for i in self.items:
            if WHO.get(i.bucket) == "fix":
                verdict["fix"][i.zone] += 1
            elif WHO.get(i.bucket) == "check":
                verdict["check"] += 1
            elif i.bucket == "next":
                verdict["next"] += 1
        return {"counts": counts, "problems": [i.as_dict() for i in problems], "verdict": verdict,
                "items": [i.as_dict() for i in self.items],
                "regions": {"how": self.how, "in": len(self.in_ids), "files": len(self.file_boxes) or len(self.file_links),
                            "area_chars": self.area_chars, "body_chars": self.body_chars},
                "title": " ".join((t.text() or "").split()) if (t := self.tree.css_first("title")) else ""}

    def html(self) -> str:
        """The stored page, safe to show (design section 四): only the elements of SHOWN with the attributes of ATTRS,
        no link that navigates (an href becomes data-nk-href: the panel's script follows a click itself), images from
        our own /check/res or an empty box, the school's style sheets without what they load, each item marked."""
        styles = "".join(f"<style>{css}</style>" for node in self.tree.css("style")
                         if (css := clean_css(node.text(deep=True) or "")))
        title = self.tree.css_first("title")
        head = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title.text() if title else '')}"
                f"</title>{styles}<style>{MARK_CSS}{FLOW_CSS}</style></head>")
        if self.tree.body is None:  # a frameset: its frames as boxes to click
            frames = "".join(self._open(n)[0] for n in self.tree.css("frame, iframe") if n.mem_id in self.marks)
            return head + f"<body class='nk-page'>{frames}</body></html>"
        return head + self._serialize(self.tree.body) + "</html>"

    def _open(self, node: LexborNode, extra: tuple[str, ...] = (), label: str = "") -> tuple[str, bool]:
        """(the start tag of a shown element with its marks, whether that already closes it: an image shown as an
        empty box, a frame shown as a box that names it)."""
        tag = node.tag
        attrs = node.attributes
        classes = [c for c in (attrs.get("class") or "").split() if not c.startswith("nk-")] + list(extra)
        out_attrs, style = "", ""
        item = self.marks.get(node.mem_id)
        for name, value in attrs.items():
            if value is None or "javascript:" in str(value).lower():
                continue
            if name in ATTRS and name != "class":
                out_attrs += _attr(name, value)
            elif name == "style":
                style = clean_css(value)
        if node.mem_id in self.file_boxes:
            classes.append("nk-files")
            label = label or "附件區（進庫）"
        if label:
            out_attrs += _attr("data-nk-label", label)
        if item is not None:
            out_attrs += _attr("data-nk", item.n) + _attr("data-b", item.bucket)
            out_attrs += _attr("data-tip-h", BUCKET_NAME[item.bucket]) + _attr("data-tip", item.tip())
            if item.target and item.target.startswith(("http://", "https://")):
                out_attrs += _attr("data-nk-href", item.target)
        closed, inner = False, ""
        if tag in FRAMES or tag in EMBEDS:
            tag, closed = "div", True
            classes.append("nk-box")
            inner = "內嵌頁：" + html.escape(unquote(item.target if item and item.target else links._frame_src(node)))
        elif item is not None and item.what == "image" and tag not in ("img", "input"):  # a background picture
            classes.append("nk-bg")
            if item.image:
                style += f";background-image:url({res_address(item.image)})"
        elif item is not None and item.what == "link" and tag not in ("a", "area"):  # an onclick, a data-href
            classes.append("nk-act")
        elif tag == "img" or (tag == "input" and (attrs.get("type") or "").lower() == "image"):
            if item is not None and item.image:
                tag = "img"
                out_attrs += _attr("src", res_address(item.image))
            else:
                tag, closed = "span", True
                classes.append("nk-noimg")
                style += "".join(f";{k}:{v}px" for k, v in (("width", attrs.get("width")),
                                                            ("height", attrs.get("height"))) if v and str(v).isdigit())
        if style.strip(" ;"):
            out_attrs += _attr("style", style.strip(" ;"))
        if classes:
            out_attrs = _attr("class", " ".join(dict.fromkeys(classes))) + out_attrs
        return f"<{tag}{out_attrs}>{inner}" + (f"</{tag}>" if closed else ""), closed

    def _serialize(self, body: LexborNode) -> str:
        """body as safe markup, walked without recursion (a broken page nests deeply). state: in (inside the
        library's content area), holder (holds a part of it), out (washed grey, not read)."""
        parts: list[str] = []
        labelled = False
        start = "in" if body.mem_id in self.in_ids else "holder"
        if start == "in":
            opened, _ = self._open(body, ("nk-page", "nk-in"), "正文區（進庫）")
            labelled = True
        else:
            opened, _ = self._open(body, ("nk-page",))
        parts.append(opened)
        stack: list[tuple[str, LexborNode | str, str]] = [("close", "body", start)]
        stack.extend(("node", c, start) for c in reversed(list(body.iter(include_text=True))))
        while stack:
            op, node, state = stack.pop()
            if op == "close":
                parts.append(f"</{node}>")
                continue
            assert isinstance(node, LexborNode)
            tag = node.tag
            if tag == "-text":
                text = html.escape(node.text(deep=False) or "", quote=False)
                if state == "holder" and text.strip():
                    inside = node.mem_id in self.in_ids
                    label = _attr("data-nk-label", "正文區（進庫）") if inside and not labelled else ""
                    labelled = labelled or inside
                    text = f"<span class='{'nk-in' if inside else 'nk-out'}'{label}>{text}</span>"
                parts.append(text)
                continue
            if tag.startswith("-") or (tag in DROPPED and not (tag in EMBEDS and node.mem_id in self.marks)):
                continue
            # a hidden field shows nothing and its value can be a whole __VIEWSTATE (16 MB on c.nknu affair pages:
            # trial 2 of 11-16); buttons are matched from the parsed tree, never from this copy
            if tag == "input" and (node.attributes.get("type") or "").lower() == "hidden" \
                    and node.mem_id not in self.marks:
                continue
            here, extra, label = state, (), ""
            if state == "holder":
                if node.mem_id in self.in_ids:
                    here, extra = "in", ("nk-in",)
                    if not labelled:
                        label, labelled = "正文區（進庫）", True
                elif node.mem_id not in self.holders:
                    here, extra = "out", ("nk-out",)
            if tag in FRAMES or tag in EMBEDS:
                if node.mem_id in self.marks:
                    parts.append(self._open(node, extra)[0])
                continue
            if tag not in SHOWN:  # unknown or custom: its children shown in its place
                stack.extend(("node", c, here) for c in reversed(list(node.iter(include_text=True))))
                continue
            opened, closed = self._open(node, extra, label)
            parts.append(opened)
            if closed or tag in VOID:
                continue
            stack.append(("close", tag, here))
            stack.extend(("node", c, here) for c in reversed(list(node.iter(include_text=True))))
        return "".join(parts)


# ---------------------------------------------------------------- an archived image

IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
               (b"GIF89a", "image/gif"), (b"BM", "image/bmp"), (b"\x00\x00\x01\x00", "image/x-icon"))


def image_type(body: bytes) -> str | None:
    """An image's media type by its first bytes (never by what the server said), or None for what is no image."""
    for magic, kind in IMAGE_MAGIC:
        if body.startswith(magic):
            return kind
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    head = body[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in body[:2048].lower()):
        return "image/svg+xml"
    return None


def archived_image(archive: Path | None, url: str, *, ledgers: LedgerCache | None = None) -> tuple[bytes, str] | None:
    """(bytes, media type) of an archived image at url (followed through its redirects), or None: not in the ledger,
    never fetched, unreadable, or no image by its bytes. ledgers: a kept connection, else one opened here."""
    if archive is None or not url or not (Path(archive) / LEDGER).exists():
        return None
    with _ledger(Path(archive), ledgers) as conn:
        ledger = Ledger(conn)
        row = ledger.by_url(scope.canonical(url) or url)
        if row is not None and row["result"] == "redirect":
            row = ledger.land(row).row
        if row is None or not row["warc"] or row["warc_offset"] is None:
            return None
        warc, offset = row["warc"], row["warc_offset"]
    try:
        _u, _status, _headers, body = postback.read_response(Path(archive), warc, offset)
    except (ValueError, OSError):
        return None
    kind = image_type(body)
    return (body, kind) if kind else None


_ROW_REF = re.compile(r"[up]?(\d{1,12})")  # the ledger's number of an address, as the export (存档编号 u<id>) writes it


def archived_record(archive: Path | None, ref: str, *, ledgers: LedgerCache | None = None) -> dict | None:
    """The stored answer of one address, as the crawler kept it (the crawler's viewer showed it at /page, /source,
    /text and /raw until the panels became one, 2026-11-21): ref is the ledger's number of the address (u<id>, or the
    bare number) or an address, which is followed through its redirects; a number is taken as it is, the row asked
    for. {id, url, status, ctype, title, fetched_at, body}, or None: not in the ledger, never fetched, unreadable."""
    ref = (ref or "").strip()
    if archive is None or not ref or not (Path(archive) / LEDGER).exists():
        return None
    with _ledger(Path(archive), ledgers) as conn:
        ledger = Ledger(conn)
        if m := _ROW_REF.fullmatch(ref):
            row = ledger.by_id(int(m.group(1)))
        else:
            row = ledger.by_url(scope.canonical(ref) or ref)
            if row is not None and row["result"] == "redirect":
                row = ledger.land(row).row
        if row is None or not row["warc"] or row["warc_offset"] is None:
            return None
        found = {k: row[k] for k in ("id", "url", "ctype", "title", "fetched_at")}
        warc, offset = row["warc"], row["warc_offset"]
    try:
        _u, status, headers, body = postback.read_response(Path(archive), warc, offset)
    except (ValueError, OSError):
        return None
    return {**found, "status": status, "ctype": headers.get("content-type") or found["ctype"] or "", "body": body}


def address_of(archive: Path | None, ref: str, *, ledgers: LedgerCache | None = None) -> str:
    """The address the ledger keeps under a number (u<id> or the bare number), '' when it has none."""
    m = _ROW_REF.fullmatch((ref or "").strip())
    if archive is None or m is None or not (Path(archive) / LEDGER).exists():
        return ""
    with _ledger(Path(archive), ledgers) as conn:
        row = Ledger(conn).by_id(int(m.group(1)))
    return str(row["url"]) if row is not None else ""


def is_markup(ctype: str | None, body: bytes) -> bool:
    """Whether a stored answer is a page (HTML by its type, or by how it begins when the type says nothing)."""
    kind = (ctype or "").split(";", 1)[0].strip().lower()
    if kind in ("text/html", "application/xhtml+xml"):
        return True
    return not kind and body[:512].lstrip()[:1] == b"<"


def visible_text(page: str) -> str:
    """What a reader sees of a page as plain lines: scripts, styles and templates gone, blank lines dropped."""
    try:
        tree = LexborHTMLParser(page)
    except SelectolaxError:
        return ""
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body
    lines = (line.strip() for line in (body.text(separator="\n") if body is not None else "").splitlines())
    return "\n".join(line for line in lines if line)


def live_url(url: str) -> str:
    """url when it is an http(s) address the owner's browser may open beside the panel, else ''."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return urlunsplit(parts)
