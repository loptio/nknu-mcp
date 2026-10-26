"""`nknu archive export`: one site of the archive as a folder for the content stage (owner 10-27: 「等他爬完导出成一个
资料夹：每页一个档、附件保留原档，再附一份清单（网址、标题、日期、完整度）」; 「不要让我以为资料已经抓好了，但实际漏了不少」).
The folder's shape is Claude's proposal the owner agreed to (【用户同意 Claude 的建议】, 10-27); how it is made here is
Claude's (【Claude 的执行办法】).

What goes out (README.md in the folder says it again, in words):
- pages/: each web page once, as the crawler decoded it (nknu.crawl.fetch.decode_body: the HTTP header's charset, else
  UTF-8, else Big5; never the page's <meta charset>, which on c.nknu says big5 for UTF-8 bytes), saved as UTF-8 with a
  byte order mark (a browser then reads it as UTF-8 whatever the meta says) and otherwise unchanged. A list's further
  pages and a notice brought by a button (the action table's pager and detail answers) are pages too.
- files/, images/: attachments and body pictures, every byte as archived (the sha256 is checked against the archive's
  before anything is written), named by Content-Disposition, the Drive item's name or the address's last part, with a
  running number in front. A layout picture the rules never fetch (links.image_need) has no file: its row says why.
- data/: other structured answers as they came (a Google calendar's events, a platform's data address such as cLv2's
  timeline, a sitemap).
- manifest.csv (UTF-8 with BOM, for Excel) and manifest.json: one row per item, got or not; missing.csv: the rows that
  have no file, worst first. The results are the views' (outcome.py: url_result, action_result, page_resource,
  page_completeness) in the dashboard's words; the site's state is monitor.site_status's. A row no branch of the
  export lists or counts is listed as 要人看 at the end (Builder._account), never left out.
- README.md: the state, what was got (and of that, what still needs a look), what was not with why, what is still
  queued counted from the manifest's rows (other sites' rows the site's pages use too), and the notice pages whose
  text area is missing or empty (the school's deleted notices: 抓到了 by the views, said in 缺什么; review 10-27).

Read only: frontier.db is opened with mode=ro (SQLite's own -wal and -shm files of a reader aside, nothing is written
next to it), the WARC files are only read, and every database read is one transaction, so the export sees one moment of
a run that may be going on (and says so). The folder must be new or empty and outside anything git would track: what
the school publishes holds full names and student numbers, which never go into git (CLAUDE.md).
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import google_api, monitor, outcome, postback, profiles, progress, sniff
from nknu.archive.frontier import _missing
from nknu.crawl.fetch import DecodeError, decode_body

TZ = ZoneInfo("Asia/Taipei")
PAGE, FILE, IMAGE, DATA, BUTTON = "网页", "附件", "图片", "资料", "按钮"
TYPES = (PAGE, FILE, IMAGE, DATA, BUTTON)
FOLDERS = {PAGE: "pages", FILE: "files", IMAGE: "images", DATA: "data"}
COLUMNS = ("序号", "类型", "网址", "最后落地网址", "本地档案", "标题", "网页上写的日期", "日期原文", "完整度", "缺什么", "结果",
           "原因", "分类", "原因代码", "抓取时间", "大小", "sha256", "格式", "链到它的网页", "链接文字", "怎么来的", "备注",
           "存档编号")
# the columns an older round-1 frontier.db may lack that neither the result views nor this export read (the 10-26
# pilot's archive lacks exactly these): read as it is. Any other gap is an archive this version cannot judge: refused
NOT_READ = frozenset({("url", "referrer"), ("action", "next_at"), ("action", "attempts")})
BUSY_MS = 5000
# a row's own answer is exported when it came back with something of its own to keep: the page or file itself, judged
# as the views judge it (a thin page, one that needs JavaScript, one the browser check has not judged yet: kept, with
# its completeness saying what it lacks). A school error page, a sign-in, a redirect, an answer that is no file are not
BODY_RESULTS = frozenset({"ok", "thin", "js", "click", "unchecked", "unresolved", "looks_gone", "decode", "unknown",
                          outcome.SCHOOL_SIDE})
# the user's categories, worst first (missing.csv's order; 抓到了 last)
ORDER = progress.BUCKET_ORDER
_BUCKET_RANK = {b: i for i, b in enumerate(ORDER)}
KIND_TYPE = {"page": PAGE, "login_check": PAGE, "embed": PAGE, "form": PAGE, "shortlink": PAGE, "click": PAGE,
             "file": FILE, "drive": FILE, "image": IMAGE, "calendar": DATA, "drive_list": DATA, "sitemap": DATA}
ROLE_TYPE = {"file": FILE, "download": FILE, "drive": FILE, "img": IMAGE, "calendar": DATA, "data": DATA,
             "iframe": PAGE, "frame": PAGE, "form": PAGE, "shortlink": PAGE, "pager": PAGE, "detail": PAGE,
             "item": PAGE, "click": BUTTON}
ROLE_HOW = {"file": "网页上的附件链接", "img": "网页里的图片", "drive": "Google 云端硬盘的档案（用 Drive API 读）",
            "download": "列表上的下载按钮（回传）", "calendar": "Google 行事历（用 Calendar API 读）",
            "iframe": "网页里的内嵌框（iframe）", "frame": "网页的框架（frame）", "form": "Google 表单",
            "shortlink": "短网址", "click": "要点了才载入的写法", "data": "网页用程式读的资料接口",
            "pager": "列表的翻页链接", "detail": "列表上的详情按钮（回传）"}
# why the rules leave a picture unfetched (links.image_need, page_res.why of an img entry with need no)
IMAGE_WHY = {"chrome": "网站版面的图示（在选单、页首页尾这些版面区）", "decor_name": "网站版面的图示（档名是图示、按钮、线条这类）",
             "tiny": "网站版面的图示（太小：宽或高不到 80 像素）", "captcha": "验证码图片（不碰）", "album": "相簿页的照片（相簿不收）"}
# what a rule leaves out (scope.judge's reasons as url.reason out:<reason>, and the other rule reasons): in words
RULE_WHY = {"decor:repeat": "网站版面的图示（很多页都有同一张）", "album:drive": "云端硬盘相簿里的照片（相簿不收）",
            "media_type": "影音档", "login": "要登录才看得到", "not_content": "不是内容（分享按钮、程式自己的页）",
            "media": "影音档", "system": "校务系统", "search": "站内搜索的结果页", "trap": "会一直产生新网址的页（爬虫陷阱）",
            "never_host": "绝不抓的网站", "social": "社群平台", "refuses": "不让爬虫抓的网站",
            "internal_service": "学校内部的服务", "bad_url": "网址写错了", "too_long": "网址太长",
            "scheme": "不是网页网址（http、https 以外的）", "bad_host": "主机名不对", "error_page": "学校的错误页",
            "media:drive_video": "云端硬盘的影片（只记档名和大小）", "media:drive_audio": "云端硬盘的声音档（只记档名和大小）"}
# how a list walk ended (listwalk.ended_by, postback.ENDS), in words
ENDED_ZH = {"last_page": "翻到最后一页", "no_rows_ok": "列表是空的（网页自己写没有资料）", "walking": "还在翻",
            "mismatch:unknown_button": "没翻完：有程序不认得的翻页按钮或链接",
            "mismatch:no_next": "没翻完：找不到下一页的按钮", "mismatch:page": "没翻完：按下一页回来的不是要的那一页",
            "mismatch:empty": "没翻完：有一页是空的", "mismatch:clv2": "没核对：一次列出的行数刚好是 10 的倍数，可能只列了一部分",
            "mismatch:total": "没翻完：列到的条数和网页写的总数对不上", "max_pages": "没翻完：翻到 500 页的上限",
            "post_failed": "没翻完：按下一页两次都失败", None: "没有翻页纪录"}
# what happens next to what was not got, by the user's category: progress.BUCKET_THEN's, shorter for the README
THEN = {"failed": "连不上或学校回了错误：列进「连接失败」，之后再想办法", "review": "要人看过再决定",
        "pending_check": "这一轮结束后用浏览器测，测完前不算完整", "next_round": "下一轮处理", "queued": "爬虫会自己处理",
        "login": "重试没用", "gone": "重试没用", outcome.SCHOOL_SIDE: "人看过，是学校那边的问题",
        "rule": "照抓取范围决定，不是漏"}
ITEMS_SHOWN = 8  # a category with no more than this many is listed item by item in the README
SNIFF_EXT = {"pdf": "pdf", "ooxml:docx": "docx", "ooxml:xlsx": "xlsx", "ooxml:pptx": "pptx", "ooxml": "zip",
             "odf:odt": "odt", "odf:ods": "ods", "odf:odp": "odp", "odf:odg": "odg", "zip": "zip", "rtf": "rtf",
             "png": "png", "jpeg": "jpg", "gif": "gif", "webp": "webp", "tiff": "tif", "xml": "xml", "json": "json",
             "text": "txt", "html": "html"}
_OLE_EXT = {"application/msword": "doc", "application/vnd.ms-excel": "xls", "application/vnd.ms-powerpoint": "ppt"}
# a server program's name at the end of an address: never the file's own extension
_PROGRAM_EXT = frozenset({"aspx", "asp", "ashx", "php", "jsp", "cgi", "do", "htm", "html", "action"})
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_SLUG_BAD = re.compile(r"[^0-9A-Za-z㐀-鿿豈-﫿._-]+")
NAME_BYTES = 150  # a file name's own part, in UTF-8 bytes (macOS and Linux allow 255 in all)


class ExportRefused(Exception):
    """The export is not made (the message says why, in Chinese, for the command to print)."""


# ---------------------------------------------------------------- the dates a page states


# the labels of the date a page was put up or changed (发布、發佈、公告、更新、張貼、刊登, with 日期 or 時間 after them or
# not; Posted, Published, Updated, with on or at or not): first. 「最新公告」 is a side list's heading, never a label
_STRONG = (r"(?:發\s*[佈布]|发\s*布|(?<!最新)公\s*告|更\s*新|張\s*貼|张\s*贴|刊\s*登)(?:\s*(?:日\s*期|時\s*間|时\s*间))?|"
           r"(?<![A-Za-z])(?:posted|published|updated?)(?:\s+(?:on|at))?(?![A-Za-z])")
# a bare 日期 or Date: only when no label above holds a date (an event page's 活動日期 may come before its 公告日期)
_WEAK = r"日\s*期|(?<![A-Za-z])date(?![A-Za-z])"
_AFTER = (r"[\s:︰\-–—|｜\]】)）]{0,8}(?P<roc>(?:中\s*[華华]\s*)?民\s*[國国]\s*)?"
          r"(?P<y>\d{2,4})\s*(?P<s1>[/.\-年])\s*(?P<m>\d{1,2})\s*(?P<s2>[/.\-月])\s*(?P<d>\d{1,2})(?!\d)\s*(?:日)?")
_DATES = tuple(re.compile(rf"(?P<label>{label}){_AFTER}", re.IGNORECASE) for label in (_STRONG, _WEAK))
# full-width digits and the marks between them as their ASCII twins, one character for one: the words kept beside the
# date are cut from the page's own text at the same places
_HALF = str.maketrans("０１２３４５６７８９／．－：", "0123456789/.-:")
YEARS = (1990, 2100)  # a date outside these is taken for none (a 民国 year read as a western one, a phone number)


def find_date(text: str) -> tuple[str, str] | None:
    """The date the text states after a label: (YYYY-MM-DD, the words as the page writes them), or None. A label of
    putting up or changing (发布、發佈、公告、更新、張貼、刊登, with 日期 or 時間 or not; Posted, Published, Updated) first,
    the first such date in the text; else the first after a bare 日期 or Date. 2025/10/01, 2025-10-01, 2025.10.01,
    2025年10月1日, full-width digits too; a year of two or three digits is a 民国 year (114/10/01, 114.10.01,
    民國114年10月1日, 中華民國114年10月1日: + 1911). A date that does not exist, or falls outside YEARS, is passed over.
    Found by the program: it may be wrong, which is why the words are kept beside it."""
    raw = text or ""
    half = raw.translate(_HALF)
    for pattern in _DATES:
        for m in pattern.finditer(half):
            s1, s2 = m["s1"], m["s2"]
            if (s1 == "年") != (s2 == "月") or (s1 != "年" and s1 != s2):
                continue
            y = int(m["y"])
            if len(m["y"]) == 4:
                if m["roc"]:
                    continue
            else:
                y += 1911
            try:
                got = date(y, int(m["m"]), int(m["d"]))
            except ValueError:
                continue
            if not YEARS[0] <= got.year <= YEARS[1]:
                continue
            return got.isoformat(), re.sub(r"\s+", " ", raw[m.start():m.end()]).strip()
    return None


# ---------------------------------------------------------------- one item of the manifest


@dataclass
class Item:
    key: str  # u<url id>, a<action id>, k<page_res key> (no row), n:<address> (a picture the rules never fetch)
    type: str
    url: str
    final_url: str = ""
    title: str = ""
    result: str = ""
    why: str = ""  # 原因, in words
    codes: str = ""  # 原因代码, as the crawler wrote them
    bucket: str = ""
    level: str = ""
    lacks: str = ""
    fetched_at: str = ""
    size: int | None = None
    sha256: str = ""
    sniff: str = ""
    pages: list[int] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    how: str = ""
    notes: list[str] = field(default_factory=list)
    ref: str = ""
    source: tuple | None = None  # (warc, offset, sha256 as recorded, content type): where its answer is
    name: str = ""  # a file's name (Content-Disposition, Drive), when known before reading
    page_role: str | None = None
    list_id: int | None = None  # a list page's own id; a press's list
    step: int | None = None
    kind: str = ""  # action kind of a press, else ""
    landing: int | None = None  # a redirect: the url row it lands on
    date: str = ""
    date_raw: str = ""
    path: str = ""
    shared: bool = False  # its file is another row's (a redirect landing on an exported page)
    empty_area: str = ""  # a notice page whose platform's title and text areas are missing or empty: in words
    owner: str = ""  # the site the row counts for (url.owner_site; a press: the site of its list)
    order: tuple = ()

    def row(self, n: int, page_url, cap: int | None = None) -> dict[str, str]:
        """The manifest's row. cap: name at most that many pages and link texts (manifest.csv and missing.csv: a
        layout picture on every page of a site of 900 pages would pass the 32767 characters an Excel cell holds),
        saying how many there are; manifest.json names them all."""
        return {"序号": str(n), "类型": self.type, "网址": self.url, "最后落地网址": self.final_url, "本地档案": self.path,
                "标题": self.title, "网页上写的日期": self.date, "日期原文": self.date_raw,
                "完整度": self.completeness(), "缺什么": self.lacks,
                "结果": outcome.RESULT_LABEL.get(self.result, self.result), "原因": self.why,
                "分类": progress.BUCKET_ZH.get(self.bucket, self.bucket), "原因代码": self.codes,
                "抓取时间": self.fetched_at, "大小": "" if self.size is None else str(self.size), "sha256": self.sha256,
                "格式": self.sniff, "链到它的网页": _joined((page_url(p) for p in self.pages), cap, "页"),
                "链接文字": _joined((t for t in self.texts if t), cap, "个"), "怎么来的": self.how,
                "备注": "；".join(dict.fromkeys(self.notes)), "存档编号": self.ref}

    def completeness(self) -> str:
        """A page's level (page_completeness) when it has its file; anything else, and a page with no file (a sign-in
        page is 完整（学校那边缺） by the views, with nothing in the folder): its category."""
        if self.level and self.path:
            return outcome.LEVEL_LABEL.get(self.level, self.level)
        return progress.BUCKET_ZH.get(self.bucket, self.bucket)


CAP = 20  # pages and link texts named in a cell of manifest.csv and missing.csv (Item.row)


def _joined(values, cap: int | None, unit: str) -> str:
    got = list(dict.fromkeys(values))
    if cap is None or len(got) <= cap:
        return " | ".join(got)
    return " | ".join([*got[:cap], f"…等 {len(got)} {unit}（全部在 manifest.json）"])


@dataclass
class Result:
    out: Path
    site: str
    name: str
    state: str
    state_words: str
    running: bool
    left_urls: int  # the manifest's rows still queued (还在排队 / 等重试), url rows and presses: Left
    left_presses: int
    counts: dict[str, dict[str, int]]  # by type: got (a file in the folder), to_do (of those, not settled), missing
    missing_by: Counter
    lines: list[str]
    left_other: int = 0  # of what is still queued (left_urls, left_presses), the rows another site owns


# ---------------------------------------------------------------- the checks before anything is written


def _git_top(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def check_out(root: Path, out: Path) -> None:
    """Refuse an out folder that is not empty, that lies in the archive, or that git would track (ExportRefused)."""
    if out == root or root in out.parents:
        raise ExportRefused(f"{out} 在存档目录里：存档只读，导出要放在存档目录外面（例如 archive-export/）")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ExportRefused(f"{out} 已经存在而且不是空的：换一个新的资料夹名（导出不覆盖、不混进旧的东西）")
    top = _git_top(out)
    if top is None:
        return
    rel = out.relative_to(top)
    hint = ("这份资料有学生全名和学号，绝不能进 git：放到仓库里的 archive-export/（.gitignore 的 /archive*/ 会挡住），"
            "或放到仓库外面")
    if not rel.parts or rel.parts[0] == ".git":
        raise ExportRefused(f"{out} 是 git 仓库本身或它的 .git：{hint}")
    try:
        got = subprocess.run(["git", "-C", str(top), "check-ignore", "-q", "--no-index", "--", f"{rel.as_posix()}/"],
                             capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportRefused(f"{out} 在 git 仓库 {top} 里，却没办法用 git 确认它被挡住（{type(exc).__name__}）：{hint}") from exc
    if got.returncode == 0:
        return
    if got.returncode == 1:
        raise ExportRefused(f"{out} 在 git 会追踪的地方（仓库 {top}）：{hint}")
    raise ExportRefused(f"{out} 在 git 仓库 {top} 里，git 没能确认它被挡住（退出码 {got.returncode}）：{hint}")


def connect(db: Path) -> sqlite3.Connection:
    """frontier.db read only (mode=ro, never immutable: a run may be writing it), with the result views in this
    connection's own temp schema and query_only on. An archive that lacks a table or a column the views or the export
    read is refused (the 10-24 crawler's, or one written by a version too old); the columns of NOT_READ may be missing."""
    if not db.exists():
        raise ExportRefused(f"找不到待抓清单：{db}（目录打错了？）")
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        gaps = [(t, c[1] if c else None) for t, c in _missing(conn)]
        bad = [g for g in gaps if g not in NOT_READ]
        if bad:
            names = "、".join(f"{t}.{c}" if c else t for t, c in bad[:6])
            raise ExportRefused(f"{db} 是旧版程式写的（缺 {names}）：导出只读、不改资料库，没办法照现在的规则判断。"
                                "10-24 的旧存档不导出；第一轮的存档先用这一版跑一次 nknu archive run（或 seed）升级")
        outcome.create_views(conn, temp=True)
        conn.execute("PRAGMA query_only=ON")
        conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    except BaseException:
        conn.close()
        raise
    return conn


# ---------------------------------------------------------------- reading the archive (one transaction)


@dataclass
class Snap:
    """What the export reads from frontier.db, at one moment."""

    plan: dict | None
    rows: dict[int, dict]  # url_result rows: the site's own, and every row its pages' resources rest on
    page_ids: list[int]
    pc: dict[int, dict]  # page_completeness, by page id
    pres: list[dict]  # page_resource rows of the site's pages
    raw_res: list[dict]  # page_res rows of the site's pages (texts, the pictures never fetched, scripts, items)
    actions: dict[int, dict]
    walks: dict[int, dict]
    drive: dict[int, dict]  # drive_item by its metadata row
    drive_of_content: dict[int, dict]  # drive_item by its content row
    landing: dict[int, tuple[int | None, str | None]]  # a redirecting row: (the row it lands on, why it lands nowhere)
    orphans: dict[int, object]
    status: object | None
    status_error: str | None
    videos: int
    other_site_items: int
    press_to: dict[int, int]  # a press: the url row its answer named (action.location)


def _rows_by_id(conn: sqlite3.Connection, ids) -> dict[int, dict]:
    ids, out = sorted(set(ids)), {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT * FROM url_result WHERE id IN ({','.join('?' * len(part))})", part):
            out[r["id"]] = dict(r)
    return out


def _expected_file(r: dict) -> bool:
    return (r.get("kind0") or r.get("kind")) in outcome._EXPECTED_FILE


def read_snapshot(conn: sqlite3.Connection, site: str, now: float) -> Snap:
    conn.execute("BEGIN")
    try:
        plan = conn.execute("SELECT * FROM site_plan WHERE site=?", (site,)).fetchone()
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM url_result WHERE owner_site=?", (site,))}
        if plan is None and not rows:
            raise ExportRefused(f"名单上没有网站 {site}，存档里也没有它的网址：网站键打错了？（例如 c.nknu.edu.tw/aca，"
                                "进度看板上每个网站的网址就是它的键）")
        page_ids = sorted(i for i, r in rows.items() if r["kind"] == "page" and not _expected_file(r))
        pc = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM page_completeness WHERE site=?", (site,))}
        own = "SELECT id FROM url WHERE owner_site=?"
        pres = [dict(r) for r in conn.execute(f"SELECT * FROM page_resource WHERE page_id IN ({own})", (site,))]
        raw_res = [dict(r) for r in conn.execute(f"SELECT * FROM page_res WHERE page_id IN ({own})", (site,))]
        want = set()
        for r in pres:
            if r["res"].startswith("u") and r["res"][1:].isdigit():
                want.add(int(r["res"][1:]))
            if r["via"] is not None:
                want.add(r["via"])
        want |= {r["res_id"] for r in raw_res if r["res_id"] is not None and r["need"] in ("must", "round2")}
        actions = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM action_result WHERE list_id IN ({own}) OR site=?", (site, site))}
        want |= {a["list_id"] for a in actions.values()}
        # the url row a press's answer named (a redirect's Location, the file a file button's POST answered): the
        # press is listed by that row (Builder._presses), which Builder._account then looks for
        press_to: dict[int, int] = {}
        locs = sorted({a["location"] for a in actions.values() if a.get("location")})
        for i in range(0, len(locs), 500):
            part = locs[i:i + 500]
            got = {r["url"]: r["id"] for r in conn.execute(
                f"SELECT id, url FROM url WHERE url IN ({','.join('?' * len(part))})", part)}
            for a in actions.values():
                if a.get("location") in got:
                    press_to[a["id"]] = got[a["location"]]
        want |= set(press_to.values())
        rows.update(_rows_by_id(conn, want - set(rows)))
        walks = {r["list_id"]: dict(r) for r in conn.execute(f"SELECT * FROM listwalk WHERE list_id IN ({own})",
                                                             (site,))}
        f = monitor._frontier(conn)
        landing = {}
        for uid, r in rows.items():
            if r["result"] == "redirect":
                end, nowhere = monitor.landing(f, uid)
                landing[uid] = ((end["id"] if end is not None and nowhere is None else None), nowhere)
        rows.update(_rows_by_id(conn, {t for t, _w in landing.values() if t is not None} - set(rows)))
        # the Drive items these rows are (metadata or content), and the rows of their content: a shortcut's target's
        # metadata, then its content (google_api.settle follows a shortcut once)
        drive, drive_of_content = {}, {}
        todo = set(rows)
        for _round in range(4):
            ids, more = sorted(todo), set()
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                marks = ",".join("?" * len(part))
                for d in conn.execute(f"SELECT * FROM drive_item WHERE url_id IN ({marks}) OR content_id IN ({marks})",
                                      (*part, *part)):
                    drive[d["url_id"]] = dict(d)
                    if d["content_id"] is not None:
                        drive_of_content[d["content_id"]] = dict(d)
                    more |= {x for x in (d["url_id"], d["content_id"]) if x is not None and x not in rows}
            if not more:
                break
            rows.update(_rows_by_id(conn, more))
            todo = more
        status, status_error, orphans = None, None, {}
        try:
            fx = monitor.facts(f)
            status = monitor.site_status(f, now, fx=fx).get(site)
            orphans = {o.id: o for o in fx.orphans.get(site, [])}
        except Exception as exc:  # noqa: BLE001 - said in the README as 没检查, never a state made up here
            status_error = f"{type(exc).__name__}: {exc}"
        # what is still queued is counted from the manifest's rows (Left), never from the site's own rows here: a
        # page of the site may rest on a row another site owns (review 10-27 #3)
        videos = conn.execute(f"SELECT count(DISTINCT video_id) FROM video WHERE page_id IN ({own})",
                              (site,)).fetchone()[0]
        other_site_items = conn.execute(
            f"""SELECT count(DISTINCT pr.res_id) FROM page_res pr JOIN url x ON x.id = pr.res_id
                WHERE pr.page_id IN ({own}) AND pr.role = 'item' AND coalesce(x.owner_site, '') <> ?""",
            (site, site)).fetchone()[0]
    finally:
        conn.execute("COMMIT")
    return Snap(dict(plan) if plan is not None else None, rows, page_ids, pc, pres, raw_res, actions, walks, drive,
                drive_of_content, landing, orphans, status, status_error, videos, other_site_items, press_to)


# ---------------------------------------------------------------- the items


def _taipei(at) -> str:
    """frontier.utc_now()'s text or a wall-clock time, as an ISO time in Taipei ('' for none)."""
    if at is None or at == "":
        return ""
    try:
        dt = (datetime.fromtimestamp(float(at), UTC) if isinstance(at, int | float)
              else datetime.fromisoformat(str(at)))
    except (TypeError, ValueError, OverflowError, OSError):
        return str(at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def _codes(r: dict) -> str:
    bits = [r.get("reason"), r.get("flags"), f"HTTP {r['status']}" if r.get("status") not in (None, 200) else None]
    return " ".join(str(b) for b in bits if b)


def _why(result: str, reason: str | None, status: int | None = None) -> str:
    """原因: what the result came from, in words (the dashboard's words for failures, the rule's or the batch's)."""
    reason = reason or ""
    if result == "rule":
        key = reason.removeprefix("out:")
        return RULE_WHY.get(reason) or RULE_WHY.get(key) or RULE_WHY.get(key.split(":", 1)[0]) or reason
    if result == "later":
        return progress.ROUND2_ZH.get(reason, reason)
    if result in ("failed", "retry", "server_error", "refused", "http_4xx", "lab", "parked", "api_error", "api_quota"):
        key = progress.error_code(reason, None, status)
        return progress.PLAIN[key].split("：", 1)[0] if key in progress.PLAIN else reason
    return ""


def _has_body(r: dict) -> bool:
    return r.get("state") == "done" and r.get("status") == 200 and bool(r.get("warc")) and r.get("warc_offset") is not None


def _type_of_row(r: dict, role: str | None = None) -> str | None:
    """A url row's type: what it came back as when it is a file or a picture, else what the page used it as, else what
    it was expected to be."""
    if r.get("state") == "done" and r.get("kind") in ("file", "image") and r.get("sniff") != "html":
        return KIND_TYPE[r["kind"]]
    if (r.get("kind0") or "") in ("drive_list", "calendar", "sitemap"):
        return DATA
    if role in ROLE_TYPE:
        return ROLE_TYPE[role]
    return KIND_TYPE.get(r.get("kind0") or r.get("kind") or "", FILE)


def _last_part(url: str) -> str:
    """An address's last path part, decoded: what a file not got is called in the manifest."""
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


def _worst(results) -> str:
    return min(results, key=lambda x: (_BUCKET_RANK.get(monitor.bucket(x), -1), x))


class Builder:
    """The items of one site, from one Snap."""

    def __init__(self, snap: Snap, site: str):
        self.s, self.site = snap, site
        self.items: dict[str, Item] = {}
        self.texts = {(r["page_id"], r["res_key"]): r["text"] for r in snap.raw_res if r.get("text")}
        self.whys: dict[str, str] = {}
        for r in snap.raw_res:
            if r.get("why"):
                self.whys.setdefault(r["res_key"], r["why"])
        # a list's rows (page_res item entries: the row each one rests on), and each notice's list: the largest list
        # that holds it (a notice is on the home page's ten newest and on the full list too)
        self.items_by_list: dict[int, set[int]] = defaultdict(set)
        for r in snap.raw_res:
            if r["role"] == "item" and r["res_id"] is not None:
                self.items_by_list[r["page_id"]].add(r["res_id"])
        size = {lst: max(len(ids), (snap.walks.get(lst) or {}).get("rows_unique") or 0)
                for lst, ids in self.items_by_list.items()}
        self.item_of_list: dict[int, int] = {}
        for lst in sorted(self.items_by_list, key=lambda x: (-size[x], x)):
            for rid in self.items_by_list[lst]:
                self.item_of_list.setdefault(rid, lst)
        self.counts = Counter()  # what is not an item of its own (scripts, Drive's bookkeeping, data: pictures)
        self.counted: set[int] = set()  # the url rows counted there (scripts, Drive's bookkeeping): _account

    def add(self, it: Item) -> Item:
        it.order = (len(self.items),)
        self.items[it.key] = it
        return it

    def _data_title(self, r: dict, title: str) -> str:
        """A structured answer's title: a calendar's events, a Drive folder's listing (a page after its first: 续页)."""
        ref = google_api.api_ref(r["url"]) if google_api.is_api(r["url"]) else None
        if ref is not None and ref.calendar:
            return f"Google 行事历 {ref.calendar} 的事件" + ("（续页）" if ref.token else "")
        if (r.get("kind0") or "") == "drive_list":
            d = self.s.drive_of_content.get(r["id"])
            name = f"「{d['name']}」" if d is not None and d.get("name") else ""
            return f"云端硬盘资料夹{name}的清单" + ("（续页）" if ref is not None and ref.token else "")
        return title

    def _drive_content(self, row: dict) -> dict:
        """A Drive item's metadata row stands for its content (a redirect, a short link, a page's entry may rest on
        the metadata: outcome._drive_settled judges them by the content): that content row, a shortcut followed to
        its target's; any other row as it is."""
        seen: set[int] = set()
        while (row.get("kind0") or "") == "drive" and row["id"] not in seen:
            seen.add(row["id"])
            d = self.s.drive.get(row["id"])
            if d is None or d.get("content_id") is None or d["content_id"] not in self.s.rows:
                break
            row = self.s.rows[d["content_id"]]
        return row

    def build(self) -> dict[str, Item]:
        self._pages()
        self._presses()
        covered = self._resources()
        self._others(covered)
        self._unfetched_pictures()
        self._account(covered)
        self._unique_press_urls()
        return self.items

    GAP = "导出程式没处理到"

    def _account(self, covered: set[int]) -> None:
        """Every url row of the site, every row its pages' needed resources rest on or end at, every row a press's
        answer named, and every press of its lists is an item above, or counted in the README (a script, Drive's
        bookkeeping), or a press a newer press of the same row stands for. One that is none of these is listed here as
        要人看 with no file, never left out (review 10-27 #9: the branches above pass rows over in a dozen places; the
        owner, 10-27: 「不要让我以为资料已经抓好了，但实际漏了不少」)."""
        s = self.s
        seen = set(covered) | self.counted
        for it in self.items.values():
            if it.key.startswith("u") and it.key[1:].isdigit():
                seen.add(int(it.key[1:]))
            if it.landing is not None:
                seen.add(it.landing)
        for meta, d in s.drive.items():  # a Drive item's metadata row and its content row stand for each other
            content = d.get("content_id")
            if content is not None and (content in seen or meta in seen):
                seen |= {meta, content}
        want = {uid for uid, r in s.rows.items() if r["owner_site"] == self.site}
        for r in s.pres:
            if r["via"] is not None:
                want.add(r["via"])
            if r["res"].startswith("u") and r["res"][1:].isdigit():
                want.add(int(r["res"][1:]))
        # a press not listed itself (a redirect, a file button's POST) is listed by the row its answer named
        want |= {to for a_id, to in s.press_to.items() if f"a{a_id}" not in self.items}
        for uid in sorted(want - seen):
            r = s.rows.get(uid)
            if r is None:
                continue  # a row the snapshot never read: nothing to list it by (the views name it not_discovered)
            it = Item(f"u{uid}", _type_of_row(r), r["url"], result=r["result"], codes=_codes(r), ref=f"u{uid}",
                      fetched_at=_taipei(r["fetched_at"]), size=r["bytes"], sha256=r["sha256"] or "",
                      sniff=r["sniff"] or "", title=r["title"] or _last_part(r["url"]), owner=r["owner_site"] or "",
                      bucket="review", why=self.GAP, how=monitor.FOUND_VIA_ZH.get(r["found_via"], r["found_via"] or ""))
            it.notes.append(f"{self.GAP}这一行（导出程式的漏洞，不是漏抓），没有导出档案，要人看")
            self.add(it)
            seen.add(uid)
        listed = {a_id for a_id in s.actions if f"a{a_id}" in self.items}
        rows_of = {}
        for a in s.actions.values():
            rows_of.setdefault((a["list_id"], a["kind"], a["row_key"]), []).append(a["id"])
        for a in sorted(s.actions.values(), key=lambda a: a["id"]):
            if a["id"] in listed or s.press_to.get(a["id"]) in seen:
                continue
            if any(o in listed or s.press_to.get(o) in seen
                   for o in rows_of[(a["list_id"], a["kind"], a["row_key"])] if o != a["id"]):
                continue  # a newer press of the same row stands for it (design §7.5 step 9)
            lst = s.rows.get(a["list_id"]) or {"url": ""}
            words = (a["row_text"] or a["row_key"] or "")[:40]
            it = Item(f"a{a['id']}", BUTTON, f"{lst['url'] or a['target'] or ''}#按钮={words}", result=a["result"],
                      codes=_codes(a), ref=f"a{a['id']}", fetched_at=_taipei(a["done"]), size=a["bytes"], sha256=a["sha256"] or "", sniff=a["sniff"] or "", pages=[a["list_id"]],
                      texts=[a["row_text"] or ""], title=a["row_text"] or "", owner=self.site, bucket="review",
                      why=self.GAP, how=f"列表 {lst['url']} 上的按钮（{a['kind']}）")
            it.notes.append(f"{self.GAP}这个按钮（导出程式的漏洞，不是漏抓），没有导出档案，要人看")
            self.add(it)
            listed.add(a["id"])

    def _unique_press_urls(self) -> None:
        """A press brought page or file has no address of its own: its list's with #第N页 or #按钮=<the button's
        words>. Two buttons with the same words (review 10-27 #10) each get their 存档编号 too."""
        by: dict[str, list[Item]] = defaultdict(list)
        for it in self.items.values():
            if it.ref.startswith("a") and "#" in it.url:
                by[it.url].append(it)
        for url, same in by.items():
            if len(same) > 1:
                for it in same:
                    it.url = f"{url}（{it.ref}）"

    # -- the site's web pages
    def _pages(self) -> None:
        s = self.s
        for uid in s.page_ids:
            r = s.rows[uid]
            it = Item(f"u{uid}", PAGE, r["url"], final_url=r["final_url"] if r["final_url"] not in (None, r["url"])
                      else "", title=r["title"] or "", result=r["result"], codes=_codes(r), ref=f"u{uid}",
                      fetched_at=_taipei(r["fetched_at"]), size=r["bytes"], sha256=r["sha256"] or "",
                      sniff=r["sniff"] or "", page_role=r["page_role"], list_id=uid if r["page_role"] == "list" else None,
                      owner=r["owner_site"] or "")
            own = r["result"]
            p = s.pc.get(uid)
            if r["result"] == "redirect":
                to, nowhere = s.landing.get(uid, (None, None))
                it.landing = to
                if to is not None:
                    it.final_url = s.rows[to]["url"]
                    own = s.rows[to]["result"]
                else:
                    it.notes.append(f"跳转没落地：{nowhere}")
                    own = "server_error" if nowhere in ("跳转绕圈", "跳太多次") else "not_discovered"
            if p is not None:
                own = p["own"]
                it.level = p["level"]
                it.lacks = self._lacks(p)
            it.bucket = monitor.bucket(own, page=True)
            it.why = _why(own, r["reason"], r["status"])
            if "in_image" in (r["flags"] or "").split(","):
                it.notes.append(outcome.IN_IMAGE_WORDS)
            if r["result"] in BODY_RESULTS and _has_body(r):
                it.source = (r["warc"], r["warc_offset"], r["sha256"], r["ctype"])
            elif it.landing is not None and f"u{it.landing}" not in self.items and it.landing not in s.page_ids:
                to = s.rows[it.landing]
                if to["result"] in BODY_RESULTS and _has_body(to) and not _expected_file(to):
                    it.source = (to["warc"], to["warc_offset"], to["sha256"], to["ctype"])
                    it.size, it.sha256, it.sniff = to["bytes"], to["sha256"] or "", to["sniff"] or ""
                    it.fetched_at = _taipei(to["fetched_at"])
            it.how = self._page_how(r)
            self.add(it)

    def _lacks(self, p: dict) -> str:
        """缺什么: the page's reasons (page_completeness.reasons) in the dashboard's words, with how many where it
        counts them. 完整（学校那边缺） says what the school lacks: the page's resources it no longer has, keeps behind a
        sign-in, or a person marked as its problem (n_school); a page that is itself the school's (a sign-in page) has
        its 结果 for that, and nothing here."""
        n = {"miss_file": p.get("miss_file"), "miss_img": p.get("miss_img"), "miss_other": p.get("miss_other"),
             "failed": p.get("n_failed"), "review": p.get("n_review")}
        out = []
        for code in (p.get("reasons") or "").split(","):
            if not code:
                continue
            if code == "complete_school":
                if p.get("n_school"):
                    out.append(f"学校那边已不存在、要登录或有问题的资源（{p['n_school']} 个）")
                continue
            zh = progress.LABEL_ZH.get(code, code)
            out.append(f"{zh}（{n[code]} 个）" if n.get(code) else zh)
            if code == "list":
                out[-1] += f"：{ENDED_ZH.get(p.get('ended_by'), p.get('ended_by'))}"
        return "、".join(out)

    def _page_how(self, r: dict) -> str:
        lst = self.item_of_list.get(r["id"])
        if lst is not None and lst in self.s.rows:
            return f"列表 {self.s.rows[lst]['url']} 的一则"
        via = monitor.FOUND_VIA_ZH.get(r["found_via"], r["found_via"] or "")
        frm = self.s.rows.get(r["found_from"]) if r.get("found_from") is not None else None
        return f"{via}（从 {frm['url']}）" if frm is not None and r["found_via"] in ("link", "redirect") else via

    # -- what the list pages' buttons brought (design §7.5): a newer press of a row stands for the older ones
    def _presses(self) -> None:
        best: dict[tuple, dict] = {}
        for a in sorted(self.s.actions.values(), key=lambda a: (a["epoch"], a["id"])):
            key = (a["list_id"], a["kind"], a["row_key"])
            old = best.get(key)
            if old is None or old["state"] != "done" and (a["state"] == "done" or a["state"] != "superseded"
                                                            or old["state"] == "superseded"):
                best[key] = a
        for a in sorted(best.values(), key=lambda a: (a["list_id"], a["kind"] != "pager", a["step"], a["id"])):
            if a["state"] == "superseded" or a["result"] == "redirect":
                continue  # a redirect's target is the url row of its Location, listed as a resource
            if a["kind"] == "click" and a["result"] == "ok":
                continue  # a file button's POST: the file it named is a url row of its own
            lst = self.s.rows.get(a["list_id"]) or {"url": "", "id": a["list_id"]}
            kind = a["kind"]
            typ = {"pager": PAGE, "detail": PAGE, "download": FILE, "data": DATA}.get(kind, BUTTON)
            label = (f"#第{a['step']}页" if kind == "pager" else f"#按钮={(a['row_text'] or a['row_key'])[:40]}")
            it = Item(f"a{a['id']}", typ, (lst["url"] or a["target"]) + label, result=a["result"],
                      codes=_codes(a), ref=f"a{a['id']}", fetched_at=_taipei(a["done"]), size=a["bytes"],
                      sha256=a["sha256"] or "", sniff=a["sniff"] or "", pages=[a["list_id"]],
                      texts=[a["row_text"] or ""], list_id=a["list_id"], step=a["step"], kind=kind,
                      name=a["filename"] or "", owner=self.site)
            it.bucket = monitor.bucket(a["result"])
            it.why = _why(a["result"], a["reason"], a["status"])
            if a["reason"] == outcome.EMPTY_DETAIL:
                it.why = "按出来的内容几乎是空的"
            elif kind == "data" and a["reason"]:
                it.why = outcome.DATA_WORDS.get(a["reason"], a["reason"])
            page_like = kind in ("pager", "detail") and a["sniff"] == "html"
            if (a["result"] in BODY_RESULTS and a["status"] == 200 and a["warc"] and a["warc_offset"] is not None
                    and (page_like or (typ in (FILE, DATA) and a["sniff"] != "html"))):
                it.source = (a["warc"], a["warc_offset"], a["sha256"], a["ctype"])
            words = {"pager": f"列表 {lst['url']} 的第 {a['step']} 页：按翻页按钮「{a['row_text'] or ''}」（回传）",
                     "detail": f"列表 {lst['url']} 第 {a['step']} 页上的按钮「{a['row_text'] or ''}」（回传）",
                     "download": f"列表 {lst['url']} 第 {a['step']} 页上的下载按钮「{a['row_text'] or ''}」（回传）",
                     "data": f"网页用程式读的资料接口 {a['target']}（{a['row_text'] or ''}）",
                     "click": f"下载按钮「{a['row_text'] or ''}」（回传，取档案网址）"}
            it.how = words.get(kind, kind)
            if kind == "detail":
                it.title = a["row_text"] or ""
            elif kind == "pager":
                it.title = f"第 {a['step']} 页"
            elif typ in (FILE, DATA):
                it.title = a["filename"] or a["row_text"] or ""
            self.add(it)

    # -- what the site's pages use (view page_resource), each once: by the row the page's entry rests on first
    def _resources(self) -> set[int]:
        s = self.s
        groups: dict[str, dict] = {}
        for r in s.pres:
            key = f"u{r['via']}" if r["via"] is not None else r["res"]
            g = groups.setdefault(key, {"res": r["res"], "via": r["via"], "roles": Counter(), "results": set(),
                                        "pages": [], "keys": [], "needs": set()})
            g["roles"][r["role"]] += 1
            g["results"].add(outcome.SCHOOL_SIDE if r["marked"] else r["result"])
            g["pages"].append(r["page_id"])
            g["keys"].append((r["page_id"], r["res_key"]))
            g["needs"].add(r["need"])
        covered: set[int] = set()
        for key, g in groups.items():
            role = g["roles"].most_common(1)[0][0]
            result = _worst(g["results"])
            res = g["res"]
            land = int(res[1:]) if res.startswith("u") and res[1:].isdigit() else None
            if g["via"] is not None:
                covered.add(g["via"])
            if land is not None:
                covered.add(land)
            if key in self.items:  # a page of the site, or a press listed above
                if result == "not_file" and self.items[key].type == PAGE:
                    self.items[key].notes.append("有网页把它当成附件或图片来链，回来却是网页（应该是档案，回来不是档案）")
                continue
            if res.startswith("a") and res in self.items:
                continue
            texts = [self.texts.get(k, "") for k in g["keys"]]
            if res.startswith("a") and not key.startswith("u"):  # a press _presses did not list (a click that
                # brought its file's address: the file is a row of its own), or one whose answer leads nowhere
                if result in ("ok", outcome.SCHOOL_SIDE):
                    continue
                a = s.actions.get(int(res[1:])) if res[1:].isdigit() else None
                lst = s.rows.get(a["list_id"]) if a is not None else None
                url = ((lst or {}).get("url") or (a or {}).get("target") or "")
                if a is not None:
                    url += f"#按钮={(a['row_text'] or a['row_key'])[:40]}"
                it = Item(key, ROLE_TYPE.get(role, BUTTON), url, result=result, ref=res, pages=g["pages"], texts=texts,
                          title=(a or {}).get("row_text") or "", codes=_codes(a) if a is not None else "",
                          owner=self.site)
                it.why = _why(result, (a or {}).get("reason"), (a or {}).get("status"))
            elif key.startswith("u"):
                via = s.rows[g["via"]]
                to = s.rows.get(land) if land is not None else via
                to = self._drive_content(to if to is not None else via)
                covered.add(to["id"])
                if role == "click" and result == "ok" and (to.get("kind0") or "") != "click":
                    continue
                typ = _type_of_row(to, role)
                it = Item(key, typ, via["url"], result=result, codes=_codes(via if to is via else to), ref=key,
                          fetched_at=_taipei(to["fetched_at"]), size=to["bytes"], sha256=to["sha256"] or "",
                          sniff=to["sniff"] or "", pages=g["pages"], texts=texts, owner=to["owner_site"] or "")
                if to is not via:
                    it.final_url = to["url"]
                    it.landing = to["id"]
                elif via["final_url"] not in (None, via["url"]):
                    it.final_url = via["final_url"]
                d = s.drive_of_content.get(to["id"]) or s.drive.get(to["id"])
                it.name = (d or {}).get("name") or to["filename"] or via["filename"] or ""
                it.title = (it.name or _last_part(via["url"])) if typ in (FILE, IMAGE) else (to["title"] or "")
                if d is not None and d.get("modified"):
                    it.notes.append(f"云端硬盘的修改时间 {d['modified']}")
                ok_type = (to.get("sniff") != "html") if typ in (FILE, IMAGE, DATA) else not _expected_file(to)
                if result in BODY_RESULTS and _has_body(to) and ok_type:
                    it.source = (to["warc"], to["warc_offset"], to["sha256"], to["ctype"])
                it.why = _why(result, to["reason"], to["status"])
                if typ == DATA:
                    it.title = self._data_title(to, it.title)
            else:  # no row: a press that is not there, or an entry no row was made for (its why says)
                rk = res.removeprefix("k")
                url = rk.removeprefix("google:") if rk.startswith(("http://", "https://", "google:")) else ""
                typ = ROLE_TYPE.get(role, BUTTON)
                it = Item(key, typ, url, result=result, ref=f"{res[:80]}", pages=g["pages"], texts=texts)
                why = self.whys.get(rk, "")
                it.codes = why
                it.why = _why(result, why)
                if not url:
                    it.notes.append(f"网页上的写法：{rk[:120]}")
                it.title = next((t for t in texts if t), "") or ("" if url else f"（写法 {rk[:60]}）")
            it.bucket = monitor.bucket(result)
            it.how = ROLE_HOW.get(role, role)
            self.add(it)
        return covered

    # -- the site's other rows: what no page of it uses (a Common Crawl attachment, a sitemap), round two's
    def _others(self, covered: set[int]) -> None:
        s = self.s
        for uid in sorted(i for i, r in s.rows.items() if r["owner_site"] == self.site):
            r = s.rows[uid]
            if f"u{uid}" in self.items or uid in covered:
                continue
            kind0 = r["kind0"] or r["kind"]
            if kind0 == "script":
                self.counts["script"] += 1
                self.counted.add(uid)
                continue
            d = s.drive.get(uid)
            if kind0 == "drive" and d is not None and d.get("content_id") is not None:
                self.counts["drive_meta"] += 1
                self.counted.add(uid)
                continue
            o = s.orphans.get(uid)
            result = o.result if o is not None else r["result"]
            typ = _type_of_row(r)
            it = Item(f"u{uid}", typ, r["url"], result=result, codes=_codes(r), ref=f"u{uid}",
                      fetched_at=_taipei(r["fetched_at"]), size=r["bytes"], sha256=r["sha256"] or "",
                      sniff=r["sniff"] or "", name=(d or {}).get("name") or r["filename"] or "",
                      owner=r["owner_site"] or "")
            if r["final_url"] not in (None, r["url"]):
                it.final_url = r["final_url"]
            it.why = _why(result, r["reason"], r["status"])
            if d is not None and (d.get("mime") or "").startswith(("video/", "audio/")):
                it.result, it.why = "rule", RULE_WHY["media:drive_" + ("video" if d["mime"].startswith("video/")
                                                                       else "audio")]
            it.bucket = monitor.bucket(it.result)
            it.title = (it.name or _last_part(r["url"])) if typ in (FILE, IMAGE) else (r["title"] or "")
            if typ == DATA:
                it.title = self._data_title(r, it.title)
            ok_type = (r.get("sniff") != "html") if typ in (FILE, IMAGE, DATA) else True
            if it.result in BODY_RESULTS and _has_body(r) and ok_type:
                it.source = (r["warc"], r["warc_offset"], r["sha256"], r["ctype"])
            via = monitor.FOUND_VIA_ZH.get(r["found_via"], r["found_via"] or "")
            it.how = f"{via}（没有网页用到它）" if o is not None else via
            self.add(it)

    # -- the pictures the rules never fetch (links.image_need: need no, no url row): listed, with why
    def _unfetched_pictures(self) -> None:
        for r in self.s.raw_res:
            if r["role"] != "img" or r["need"] != "no" or r["res_id"] is not None:
                continue
            if r["why"] == "data" or r["res_key"].startswith("data:"):
                self.counts["data_img"] += 1
                continue
            key = f"n:{r['res_key']}"
            it = self.items.get(key)
            if it is None:
                it = self.add(Item(key, IMAGE, r["res_key"], title=_last_part(r["res_key"]), result="rule",
                                   bucket="rule", why=IMAGE_WHY.get(r["why"] or "", r["why"] or ""),
                                   codes=r["why"] or "", ref=key[:80], how="网页里的图片（规则不抓）"))
            it.pages.append(r["page_id"])
            if r.get("text"):
                it.texts.append(r["text"])


# ---------------------------------------------------------------- the pages' titles and dates


def _first_text(tree: LexborHTMLParser, css: str) -> str:
    for node in tree.css(css):
        text = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()
        if text:
            return text[:200]
    return ""


def page_title(tree: LexborHTMLParser, url: str, site: str, fallback: str = "") -> str:
    """The page's own title: its platform's detail title (profiles.Detail.title), else its first h1, else its first
    h2, else <title>. c.nknu's <title> is the unit's name on every page, so the headings come first."""
    prof = profiles.profile_for_site(site)
    parts = urlsplit(url)
    where = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    if prof is not None:
        for d in prof.details:
            if d.title and re.search(d.paths, where):
                got = _first_text(tree, d.title)
                if got:
                    return got
    for css in ("h1", "h2", "title"):
        got = _first_text(tree, css)
        if got:
            return got
    return fallback


NO_AREA = "找不到内文区（网页上没有公告的标题栏和内文栏，多半是学校删掉的公告只剩网站模板）"
EMPTY_AREA = "内文区是空的（公告的标题栏和内文栏都没有内容，多半是学校删掉的公告）"


def empty_detail_area(tree: LexborHTMLParser, url: str, site: str) -> str:
    """What a notice page (its platform's detail page: profiles.Detail) lacks in its own areas, in words, or '':
    NO_AREA when neither its title area nor its text area is on the page, EMPTY_AREA when they are and hold nothing
    (no words, no picture, link or frame, no attachment link). c.nknu shows a notice the school deleted as the site's
    template, its DataFormView with neither label in it (10-26 pilot, 体育室: 27 pages from the old archive and Common
    Crawl, every one 15269 bytes, as the page of a notice number that cannot exist). The crawler's own check
    (profiles.empty_detail) claims nothing when the text area is not there, so the views call such a page 完整: the
    export keeps that and says this in 缺什么, and the README names each page (review 10-27, 【Claude 的执行办法】;
    whether the crawler should call it gone is the main session's call)."""
    prof = profiles.profile_for_site(site)
    if prof is None:
        return ""
    parts = urlsplit(url)
    where = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    d = next((d for d in prof.details if d.text and re.search(d.paths, where)), None)
    if d is None:
        return ""
    areas = [*tree.css(d.text), *(tree.css(d.title) if d.title else [])]
    if not areas:
        return NO_AREA
    for node in areas:
        if (node.text() or "").strip() or node.css_first("img[src], a[href], iframe[src], embed, object"):
            return ""
    if d.files and any((n.attributes.get("href") or "").strip() not in ("", "#") for n in tree.css(d.files)):
        return ""
    return EMPTY_AREA


def page_texts(tree: LexborHTMLParser, site: str) -> list[str]:
    """The page's words to look for a date in: its content area first (its platform's content_css), then all of it.
    Scripts and styles are taken out (the tree is changed)."""
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    out = []
    prof = profiles.profile_for_site(site)
    if prof is not None and prof.content_css:
        nodes = tree.css(prof.content_css)
        if nodes:
            out.append("\n".join(n.text(separator="\n", strip=True) for n in nodes))
    body = tree.body or tree.root
    out.append(body.text(separator="\n", strip=True) if body is not None else "")
    return out


# ---------------------------------------------------------------- names


def safe_name(name: str, max_bytes: int = NAME_BYTES) -> str:
    """A file name any of the three systems takes: no path separators, control or reserved characters, no invisible
    format characters (Unicode Cf: a right-to-left override shows gpj.exe as exe.jpg, a zero-width space hides), no dots
    or spaces at the ends, at most max_bytes of UTF-8 with the extension kept. Chinese stays as it is."""
    name = unicodedata.normalize("NFC", name or "")
    name = "".join(c for c in name if unicodedata.category(c) != "Cf")
    name = re.sub(r"\s+", " ", _BAD_CHARS.sub("_", name)).strip(" .")
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not ext or len(ext) > 8:
        stem, ext = name, ""
    room = max_bytes - (len(ext.encode("utf-8")) + 1 if ext else 0)
    stem = stem.encode("utf-8")[:room].decode("utf-8", "ignore").rstrip(" .")
    return f"{stem}.{ext}" if ext else stem


def url_slug(url: str) -> str:
    """A short name from an address: its last path part without the program's extension, and its query's values
    (newsview.aspx?nid=882 -> newsview-nid-882)."""
    parts = urlsplit(url.split("#", 1)[0])
    seg = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or "index"
    stem = seg.rsplit(".", 1)[0] if "." in seg else seg
    q = "-".join(f"{k}-{v}" for k, v in parse_qsl(parts.query, keep_blank_values=True))
    slug = _SLUG_BAD.sub("-", f"{stem}-{q}" if q else stem).strip("-._")
    return slug[:60] or "page"


def file_name(it: Item, url: str, headers: dict[str, str], ctype: str | None) -> str:
    """A file's own name: the Drive item's or the row's (Content-Disposition as the crawler read it), else
    Content-Disposition in the archived answer, else the address's last part; with the sniffed format's extension when
    it has none (a Google document exported as .docx) or only a server program's (download.aspx holding a PDF)."""
    name = it.name or sniff.filename((), headers) or ""
    if not name:
        last = unquote(urlsplit(url.split("#", 1)[0]).path.rstrip("/").rsplit("/", 1)[-1])
        name = last or "file"
    ext = sniff.name_ext(name)
    want = SNIFF_EXT.get(it.sniff) if it.sniff != "ole" else _OLE_EXT.get((ctype or "").split(";")[0].strip(), "")
    if want and (not ext or (ext in _PROGRAM_EXT and ext != want)):
        name = f"{name}.{want}"
    return name


# ---------------------------------------------------------------- writing


class Writer:
    def __init__(self, root: Path, out: Path, site: str, items: dict[str, Item], snap: Snap):
        self.root, self.out, self.site, self.items, self.s = root, out, site, items, snap
        self.numbers = Counter()
        self.width = {t: max(4, len(str(sum(1 for i in items.values() if i.type == t)))) for t in FOLDERS}
        self.by_source: dict[tuple, str] = {}
        self.titles: dict[int, str] = {}

    def run(self) -> None:
        for d in FOLDERS.values():
            (self.out / d).mkdir(parents=True, exist_ok=True)
        rank = {t: i for i, t in enumerate(TYPES)}
        for it in sorted(self.items.values(), key=lambda i: (rank[i.type], i.source is None, i.order)):
            if it.source is not None:
                self._write(it)
        for it in self.items.values():  # a redirect shares the file of the page it lands on
            if not it.path and it.landing is not None:
                to = self.items.get(f"u{it.landing}")
                if to is not None and to.path:
                    # the row says what its file is (review 10-27 #5): the landing page's size, sha256, format and
                    # fetch, and its completeness when the views judge the redirect by no row of its own
                    it.path, it.shared = to.path, True
                    it.title = it.title or to.title
                    it.date, it.date_raw = (it.date, it.date_raw) if it.date else (to.date, to.date_raw)
                    it.size, it.sha256, it.sniff, it.fetched_at = to.size, to.sha256, to.sniff, to.fetched_at
                    if not it.level:
                        it.level, it.lacks = to.level, to.lacks
                    it.notes.append("跳转：内容就是落地那一页的档（大小、sha256、格式、抓取时间都是落地那一页的）")
        for it in self.items.values():
            if not it.path and it.bucket == "got":  # never 抓到了 with nothing in the folder
                it.bucket = "review"
                it.notes.append("存档说抓到了，导出却没有可以存的内容（这种情况导出程式没处理到，要人看）")
            if it.type == PAGE and it.list_id is not None and it.kind == "pager":
                base = self.titles.get(it.list_id)
                if base:
                    it.title = f"{base}（第 {it.step} 页）"

    def _write(self, it: Item) -> None:
        warc, offset, sha, ctype = it.source
        if (warc, offset) in self.by_source:
            it.path, it.shared = self.by_source[(warc, offset)], True
            it.notes.append("和清单里另一行是同一个档")
            return
        try:
            url, _status, headers, body = postback.read_response(self.root, warc, offset)
        except (OSError, ValueError) as exc:
            it.notes.append(f"存档里读不回来（{type(exc).__name__}），没导出")
            it.bucket = "review"
            return
        got = hashlib.sha256(body).hexdigest()
        if sha and got != sha:
            it.notes.append("存档内容和纪录的 sha256 对不上，没导出")
            it.bucket = "review"
            return
        it.sha256, it.size = got, len(body)
        ctype = headers.get("content-type") or ctype
        if it.type == PAGE:
            data, name = self._page(it, url, body, ctype)
        elif it.type == DATA and it.title:
            name, data = f"{it.title}.{SNIFF_EXT.get(it.sniff, 'json')}", body
        else:
            name, data = file_name(it, url, headers, ctype), body
            if it.type in (FILE, IMAGE):
                it.title = name
        folder = FOLDERS[it.type]
        self.numbers[folder] += 1
        fname = f"{self.numbers[folder]:0{self.width[it.type]}d}-{safe_name(name)}"
        (self.out / folder / fname).write_bytes(data)
        it.path = f"{folder}/{fname}"
        self.by_source[(warc, offset)] = it.path

    def _page(self, it: Item, url: str, body: bytes, ctype: str | None) -> tuple[bytes, str]:
        try:
            text = decode_body(body, ctype)
        except DecodeError:
            text = postback.as_text(body, ctype)
            it.notes.append("编码有问题：有些字元读不出来，用 � 代替（原样在存档里）")
        tree = LexborHTMLParser(text)
        if it.kind == "detail":
            it.title = it.title or page_title(tree, url, self.site)
        elif it.kind != "pager":
            it.title = page_title(tree, url, self.site, it.title)
            if it.key.startswith("u"):
                self.titles[int(it.key[1:])] = it.title
            area = empty_detail_area(tree, url, self.site)
            if area:
                it.empty_area = area
                it.lacks = "、".join(x for x in (it.lacks, area) if x)
                it.notes.append("公告的内文区是程序看的；完整度、分类照存档的判断，没有改")
        if it.page_role != "list" and it.kind != "pager":
            for words in page_texts(tree, self.site):
                found = find_date(words)
                if found:
                    it.date, it.date_raw = found
                    break
        if it.kind == "pager":
            short = f"{url_slug(url)}-p{it.step}"
        else:
            short = url_slug(url) if not it.kind else f"{url_slug(url)}-{it.kind}"
            if it.title:
                short += "-" + safe_name(it.title, 90)
        return codecs.BOM_UTF8 + text.encode("utf-8"), f"{short}.html"


def _csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- the export


def export_site(root: Path, site: str, out: Path, *, now: float | None = None) -> Result:
    """Export one site of the archive at root into the new folder out (see the module's docstring)."""
    root = Path(root).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    now = time.time() if now is None else now
    check_out(root, out)
    from nknu.archive.crawler import lock_holder

    pid = lock_holder(root)
    conn = connect(root / "frontier.db")
    try:
        snap = read_snapshot(conn, site, now)
    finally:
        conn.close()
    builder = Builder(snap, site)
    items = builder.build()
    out.mkdir(parents=True, exist_ok=True)
    try:
        writer = Writer(root, out, site, items, snap)
        writer.run()
        result = _finish(root, out, site, items, snap, builder, pid, now)
    except BaseException as exc:
        (out / "导出没有完成.txt").write_text(f"导出到一半出错了，这个资料夹不完整，请删掉重导：{type(exc).__name__}: {exc}\n",
                                           encoding="utf-8")
        raise
    return result


def _finish(root: Path, out: Path, site: str, items: dict[str, Item], snap: Snap, builder: Builder, pid,
            now: float) -> Result:
    rank = {t: i for i, t in enumerate(TYPES)}
    ordered = sorted(items.values(), key=lambda i: (rank[i.type], not i.path, i.path or "~", i.order))
    urls = {uid: r["url"] for uid, r in snap.rows.items()}

    def page_url(pid_: int) -> str:
        return urls.get(pid_, f"u{pid_}")

    rows = [it.row(n, page_url, cap=CAP) for n, it in enumerate(ordered, start=1)]
    missing = sorted(((it, r) for it, r in zip(ordered, rows, strict=True) if not it.path),
                     key=lambda p: (_BUCKET_RANK.get(p[0].bucket, -1), rank[p[0].type], p[0].url))
    missing_items = [it for it, _r in missing]
    _csv(out / "manifest.csv", rows)
    _csv(out / "missing.csv", [r for _it, r in missing])
    name = (snap.plan or {}).get("name") or site
    st = snap.status
    state = st.state if st is not None else "unchecked"
    sign, words, _color = progress.STATE_SHOW.get(state, ("⚠", "没检查", "yellow"))
    state_words = f"{sign} {words}".strip()
    counts = {t: {"got": sum(1 for i in ordered if i.type == t and i.path),
                  "to_do": sum(1 for i in ordered if i.type == t and i.path and i.bucket not in SETTLED),
                  "missing": sum(1 for i in ordered if i.type == t and not i.path)} for t in TYPES}
    missing_by = Counter(progress.BUCKET_ZH.get(it.bucket, it.bucket) for it in missing_items)
    left = Left.of(ordered, site)
    meta = {"网站": site, "名称": name, "导出时间": _taipei(now), "存档": str(root),
            "导出时的状态": state_words, "爬虫正在跑": pid is not None, "还在排队的网址": left.urls,
            "还在排队的按钮": left.presses, "其中归属别的网站": left.other, "栏位": list(COLUMNS),
            "项目": [it.row(n, page_url) for n, it in enumerate(ordered, start=1)]}
    (out / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    readme = _readme(root, site, name, now, snap, state_words, pid, ordered, counts, missing_items, builder, items,
                     left)
    (out / "README.md").write_text(readme, encoding="utf-8")
    running = f"；爬虫正在跑（pid {pid}），清单里还有 {left.words()}" if pid is not None else ""
    by_bucket = Counter(it.bucket for it in missing_items)
    lines = [f"导出 {name}（{site}）到 {out}",
             f"导出时这个网站的状态：{state_words}{running}",
             "；".join(f"{t}：抓到 {c['got']}" + (f"（其中 {c['to_do']} 个有档但还要处理）" if c["to_do"] else "")
                      + f"、没抓到 {c['missing']}" for t, c in counts.items() if c["got"] or c["missing"]),
             "没抓到的按原因：" + "、".join(f"{progress.BUCKET_ZH.get(b, b)} {n}" for b, n in sorted(
                 by_bucket.items(), key=lambda kv: _BUCKET_RANK.get(kv[0], -1))) if by_bucket else "没有没抓到的",
             f"先看 {out / 'README.md'}；清单 manifest.csv，没抓到的 missing.csv"]
    return Result(out, site, name, state, state_words, pid is not None, left.urls, left.presses, counts,
                  missing_by, lines, left.other)


# an item with a file whose category is one of these needs nothing more; any other (要人看, 待判定, 下一轮 …) is counted
# as 有档但还要处理 in the README (review 10-27 #8)
SETTLED = ("got", outcome.SCHOOL_SIDE)


@dataclass
class Left:
    """What is still queued, counted from the manifest's rows (分类 还在排队 / 等重试), not from the site's own frontier
    rows: a page of the site may rest on a row another site owns (a short link, a shared file), which waits in that
    site's turn (review 10-27 #3)."""

    urls: int
    presses: int
    other: int

    @classmethod
    def of(cls, items: list[Item], site: str) -> Left:
        queued = [it for it in items if it.bucket == "queued"]
        presses = sum(1 for it in queued if it.ref.startswith("a"))
        return cls(len(queued) - presses, presses, sum(1 for it in queued if it.owner and it.owner != site))

    def words(self) -> str:
        other = (f"；其中 {self.other} 个归属别的网站，是几个网站共用的资源，跟着那个网站的进度抓" if self.other else "")
        return (f"{self.urls} 个网址、{self.presses} 个按钮在排队（missing.csv 里分类是「还在排队 / 等重试」的"
                f"{other}）")


def _named(it: Item) -> str:
    """One item in the README, by its name, address and result."""
    label = it.title or next((t for t in it.texts if t), "") or unquote(it.url) or it.ref
    where = f" {unquote(it.url)}" if it.url and unquote(it.url) != label else ""
    because = f"（{it.why}）" if it.why else ""
    return f"  - {it.type}「{label[:60]}」{where}：{outcome.RESULT_LABEL.get(it.result, it.result)}{because}"


def _by_bucket(items: list[Item], then: bool = True) -> list[str]:
    """The README's lines for these items by category, worst first: how many of each type, and each one by name when
    a category holds few (规则不抓 never), else its most common reasons."""
    out = []
    by, whys, of = defaultdict(Counter), defaultdict(Counter), defaultdict(list)
    for it in items:
        by[it.bucket][it.type] += 1
        of[it.bucket].append(it)
        if it.why:
            whys[it.bucket][it.why] += 1
    for b in sorted(by, key=lambda x: _BUCKET_RANK.get(x, -1)):
        out.append(f"- **{progress.BUCKET_ZH.get(b, b)} {sum(by[b].values())}**："
                   + "、".join(f"{t} {n}" for t, n in sorted(by[b].items(), key=lambda kv: TYPES.index(kv[0])))
                   + (f"（{THEN.get(b, '')}）" if then else ""))
        if b != "rule" and len(of[b]) <= ITEMS_SHOWN:  # the few that matter most, each by name
            out += [_named(it) for it in of[b]]
        else:
            out += [f"  - {w}：{n}" for w, n in whys[b].most_common(6)]
    return out


EMPTY_SHOWN = 200  # the empty notice pages the README names one by one


def _readme(root: Path, site: str, name: str, now: float, snap: Snap, state_words: str, pid, ordered: list[Item],
            counts: dict, missing_items: list[Item], builder: Builder, items: dict[str, Item], left: Left) -> str:
    st = snap.status
    why = list(getattr(st, "why", []) or []) if st is not None else [f"网站状态算不出来：{snap.status_error}"]
    warnings = list(getattr(st, "warnings", []) or []) if st is not None else []
    queued = left.urls or left.presses
    if pid is not None:
        run = (f"**爬虫正在跑**（pid {pid}）：" + (f"清单里还有 {left.words()}" if queued else "清单里没有还在排队的")
               + "，导出的是这一刻已经抓到的；爬完以后再导一次会更完整")
    elif queued:
        run = f"爬虫没有在跑，但清单里还有 {left.words()}，下一次 nknu archive run 会接着抓"
    else:
        run = "爬虫没有在跑；清单里没有还在排队的"
    out = [f"# {name}（{site}）导出", "",
           ("> ⚠ **这份资料有学生全名和学号**（学校公开的荣誉榜、名单照原文收）：只在自己的电脑和实验室机器上用，"
           "**不能上传到公开的地方**（GitHub、公开的云端硬盘、网站、公开分享的对话），也不能进 git。"), "",
           f"- 导出时间：{datetime.fromtimestamp(now, TZ):%Y-%m-%d %H:%M}（台北时间）",
           f"- 存档：`{root}`（只读，没有改动）",
           f"- **导出时这个网站的状态：{state_words}**（和进度看板同一套判断）"]
    out += [f"  - {w}" for w in why[:8]] + [f"  - 另外：{w}" for w in warnings[:4]]
    out += [f"- 导出时：{run}", "", "## 一共有什么", "",
            "| 类型 | 抓到（已导出） | 其中有档但还要处理 | 没抓到 |", "|---|---|---|---|"]
    out += [f"| {t} | {c['got']} | {c['to_do']} | {c['missing']} |" for t, c in counts.items()
            if t != BUTTON or c["got"] or c["missing"]]
    pressed = sum(1 for i in ordered if i.type == PAGE and i.path and i.kind in ("pager", "detail"))
    shared = sum(1 for i in ordered if i.shared)
    empty = [i for i in ordered if i.empty_area and i.path]
    to_do = [i for i in ordered if i.path and i.bucket not in SETTLED]
    said = []
    if to_do:
        said.append("「其中有档但还要处理」：档案导出了，分类却不是「抓到了」（要人看、待判定、下一轮补……），"
                    "看过或判定以后才算数，见下面「有档但还要处理的」")
    if empty:
        said.append(f"网页「抓到」的里面有 {len(empty)} 页是**空的公告页**（公告的内文区找不到或是空的，多半是学校删掉的"
                    "公告只剩网站模板）：分类、完整度照存档算「抓到了」「完整」，「缺什么」栏写着，下面「空的公告页」"
                    "一节逐页列出")
    if pressed:
        said.append(f"网页里有 {pressed} 页是按按钮才出来的（列表的第 2 页以后、按了才出来的公告内文）")
    if shared:
        said.append(f"有 {shared} 行和另一行是同一个档（跳转的网址、同一个档被几个网址用到），本地档案指向同一个档")
    if counts[BUTTON]["missing"]:
        said.append("「按钮」是网页上要点了才载入的写法，这一轮没办法抓，或还没决定怎么处理")
    if said:
        out += [""] + [f"- {x}" for x in said]
    out += ["", f"**没抓到的 {len(missing_items)} 个**，按原因（每一个都在 missing.csv，原因写在「结果」「原因」栏）：", ""]
    out += _by_bucket(missing_items) or ["- 没有"]
    if to_do:
        out += ["", f"**有档但还要处理的 {len(to_do)} 个**（本地档案有，分类在「分类」栏）：", ""]
        out += _by_bucket(to_do, then=False)
    bad_pages = Counter(it.level for it in ordered if it.type == PAGE and it.path and it.level
                        and it.level not in ("complete", "complete_school"))
    if bad_pages:
        out += ["", "抓到了但**不完整**的网页（「完整度」「缺什么」栏）：" + "、".join(
            f"{outcome.LEVEL_LABEL.get(k, k)} {n} 页" for k, n in bad_pages.items())]
    other = []
    if builder.counts["data_img"]:
        other.append(f"网页里直接内嵌的小图（data: 网址）{builder.counts['data_img']} 张：本来就在网页档里")
    if builder.counts["script"]:
        other.append(f"网站的程式档（.js）{builder.counts['script']} 个：不是内容，没导出")
    if builder.counts["drive_meta"]:
        other.append(f"Google 云端硬盘的档案资讯 {builder.counts['drive_meta']} 笔：程序读档案用的（档名、大小、修改时间），"
                     "没另外导出：档名写在附件那一行，修改时间写在备注")
    if snap.videos:
        other.append(f"网页里的 YouTube 影片 {snap.videos} 部：照规则不抓（影音），只在存档里记了网址和标题")
    if snap.other_site_items:
        other.append(f"列表上链到别的网站的条目 {snap.other_site_items} 则：在那个网站的导出里，这里不重复")
    if other:
        out += ["", "其他没导出的（不算漏）："] + [f"- {x}" for x in other]
    out += ["", "## 列表有没有翻完", ""]
    walks = [w for w in snap.walks.values()]
    if walks:
        out += ["| 列表 | 翻了几页 | 列出几条 | 网页上写的总数 | 条目的内容导出了几条 | 怎么结束的 |",
                "|---|---|---|---|---|---|"]
        for w in sorted(walks, key=lambda w: w["list_id"]):
            lst = items.get(f"u{w['list_id']}")
            label = f"{lst.title}（{lst.url}）" if lst is not None else f"u{w['list_id']}"
            ids = builder.items_by_list.get(w["list_id"], set())
            got = sum(1 for rid in ids if items.get(f"u{rid}") is not None and items[f"u{rid}"].path)
            details = [it for it in items.values() if it.kind == "detail" and it.list_id == w["list_id"]]
            if details:  # a list whose rows open by a button
                got += sum(1 for it in details if it.path)
            elif not ids:  # a list of files whose rows are download buttons (SSO's 书表)
                got += sum(1 for it in items.values()
                           if it.kind == "download" and it.list_id == w["list_id"] and it.path)
            out.append(f"| {label} | {w['pages'] or 0} | {w['rows_unique'] if w['rows_unique'] is not None else ''} | "
                       f"{w['claimed'] if w['claimed'] is not None else '（没写）'} | {got} | "
                       f"{ENDED_ZH.get(w['ended_by'], w['ended_by'])} |")
        out.append("\n「列出几条」是翻完所有页、去掉重复以后的条数；「条目的内容导出了几条」是这些条目点进去的内容页（或附件）"
                   "有几条导出了。两个数字对不上，就是有条目的内容没抓到（在 missing.csv），或条目链到别的网站（在那个网站的"
                   "导出里）。")
    else:
        out.append("这个网站没有列表的翻页纪录（列表页的完整度栏会写「列表没核对」）。")
    if empty:
        out += ["", "## 空的公告页", "",
                (f"这 {len(empty)} 页是平台的公告页，网页上公告的标题栏和内文栏却找不到或是空的：多半是学校删掉的公告，"
                 "只剩网站的模板（旧存档、Common Crawl 记得网址，学校那边已经删了）。是程序看出来的；爬虫现在的规则把它们算成"
                 "「抓到了」「完整」，导出照存档的判断，没有改，只在「缺什么」栏写明。做内容的时候可以跳过。"), "",
                "| 网址 | 标题 | 缺什么 | 怎么来的 |", "|---|---|---|---|"]
        out += [f"| {i.url} | {i.title} | {i.empty_area.split('（', 1)[0]} | {i.how} |" for i in empty[:EMPTY_SHOWN]]
        if len(empty) > EMPTY_SHOWN:
            out.append(f"\n…另有 {len(empty) - EMPTY_SHOWN} 页，manifest.csv「缺什么」栏有「内文区」的就是。")
    out += ["", "## 资料夹里有什么", "",
            ("- `pages/`：每个网页一个档（.html）。列表按「下一页」按钮翻出来的第 N 页、按按钮才出来的公告内文也各自一个档；"
            "这种页没有自己的网址，清单的「网址」栏写列表网址加 `#第N页` 或 `#按钮=…`，「怎么来的」写是哪个列表、哪个按钮。"),
            "- `files/`：附件原档，一个位元组都没改（sha256 和存档一致）。按钮下载的、Google 云端硬盘读来的都在这里。",
            "- `images/`：网页正文里的图片原档。",
            "- `data/`：其他结构化资料原样存（Google 行事历的事件、网页用程式读的资料接口、网站地图）。",
            ("- `manifest.csv` / `manifest.json`：清单，每一行一个项目，抓到的和没抓到的都在（csv 是 UTF-8 带 BOM，Excel "
            "直接打得开；json 是同样的内容）。"),
            ("- `missing.csv`：只列没抓到的（本地档案是空的），最要紧的在前面：连接失败 → 要人看 → 待判定 → 下一轮 → "
            "还在排队 → 要登录 → 学校那边已经不存在 → 规则不抓。"),
            "", ("档名：4 位流水号 + 短名。网页的短名来自网址和标题；附件用学校给的档名（下载时的 Content-Disposition）、"
            "云端硬盘的档名或网址最后一段，中文照留，去掉档名不能用的字元。"), "",
            "## 清单栏位", ""]
    out += [f"- **{k}**：{v}" for k, v in COLUMN_HELP.items()]
    out += ["", "## 日期怎么来的", "",
            ("「网页上写的日期」是**程序找的**：在网页文字里找标签后面紧接的日期。先找「发布、發佈、公告、更新、張貼、刊登」"
            "（后面可以接「日期」或「時間」，例如「更新時間」「公告日期」；英文 Posted、Published、Updated）这类标签，"
            "找不到才找单独的「日期」「Date」（活动页上「活動日期」写在「公告日期」前面时，取的是公告日期）；「最新公告」"
            "是侧栏标题，不算标签。认得 2025/10/01、2025-10-01、2025.10.01、2025年10月1日、全形数字 ２０２５／１０／０１，"
            "以及民国年 114/10/01、114.10.01、民國114年10月1日、中華民國114年10月1日（民国年加 1911 换成西元）。先找正文区，"
            "找不到再找整页，取第一个。**可能不准**（例如抓到的是页尾的「网站更新日期」），所以「日期原文」栏附上它在网页上"
            "的原样，可以核对；找不到就空白。列表页上有很多条、各有各的日期，所以列表页不填。附件、图片看「链到它的网页」"
            "那一页的日期。"), "",
            "「抓取时间」是爬虫抓到它的时间（台北时间）。", "",
            "## 编码", "",
            ("网页档是爬虫解码后的结果，存成 UTF-8（档头有 BOM 记号，浏览器会照 UTF-8 显示），内容没有改。学校有些网页的 "
            "`<meta charset>` 写 big5，实际却是 UTF-8（c.nknu 就是这样），所以爬虫不照 meta 解码；网页档里那行 meta "
            "照原样留着，不用理它，档案本身已经是 UTF-8。清单的「大小」「sha256」是存档里原始内容的（网页档改存成 UTF-8，"
            "档案本身的大小可能不同；附件和图片的完全一样）。"), ""]
    return "\n".join(out)


COLUMN_HELP = {
    "序号": "清单里的顺序：网页、附件、图片、资料、按钮，各自先列抓到的",
    "类型": "网页 / 附件 / 图片 / 资料 / 按钮（网页上要点了才载入、这一轮没办法抓的写法）",
    "网址": ("网页上链的网址；按钮按出来的页是列表网址加 #第N页 或 #按钮=…（两个按钮的文字一样时，后面再加括号里的存档编号，"
           "分得开）"),
    "最后落地网址": "跳转以后真正拿到内容的网址（没有跳转就空白）",
    "本地档案": "导出资料夹里的档案；空白就是没抓到",
    "标题": "网页：平台的标题栏，否则第一个 h1、h2，再不然 <title>；附件和图片：档名；按钮按出来的公告：列表上的标题",
    "网页上写的日期": "程序在网页上找到的日期（西元，YYYY-MM-DD），找不到空白；见下面「日期怎么来的」",
    "日期原文": "找到的日期在网页上的原样（含标签），用来核对",
    "完整度": ("有档的网页：完整、部分完整、不完整、待判定、列表没核对、还没抓完、完整（学校那边缺）；其他（附件、图片、"
            "没有档的网页……）：和「分类」一样（抓到了、连接失败、要登录……）"),
    "缺什么": ("网页缺的东西（缺附件、缺图片、连接失败、要人看、列表没翻完、学校那边已不存在或要登录的资源……），括号里是几个；"
            "公告页的内文区找不到或是空的（多半是学校删掉的公告），也写在这里"),
    "结果": "这一个网址或按钮本身的结果，和进度看板一样的说法",
    "原因": "结果的原因（连不上服务器、按规则不抓的是哪条规则、下一轮的哪一批……）",
    "分类": "抓到了 / 连接失败 / 要人看 / 待判定 / 下一轮 / 还在排队 / 要登录 / 学校那边已经不存在 / 规则不抓",
    "原因代码": "爬虫记的原始代码（给查问题用）",
    "抓取时间": "爬虫抓到它的时间，台北时间",
    "大小": "存档里原始内容的位元组数",
    "sha256": "存档里原始内容的 sha256（附件、图片和导出的档完全一样）",
    "格式": "看档案开头的位元组判断的格式（pdf、ooxml:docx、jpeg、html……）",
    "链到它的网页": ("附件、图片、按钮：用到它的网页（几个就用 | 隔开）。csv 最多列 20 页，后面写「…等 N 页」（Excel 一格放不下"
               "几百页）；manifest.json 全部列出"),
    "链接文字": "那些网页上链接或按钮的文字、图片的替代文字（csv 一样最多 20 个）",
    "怎么来的": "从哪里找到的：列表的哪一则、哪个按钮、哪个网页的附件……",
    "备注": "其他要知道的（跳转、同一个档、云端硬盘的修改时间、编码问题……）",
    "存档编号": "存档里的编号（u 网址、a 按钮），nknu archive browse 看得到",
}
