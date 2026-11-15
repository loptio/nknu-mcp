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
  text area is missing or empty (the school's deleted notices, said in 缺什么; review 10-27) with the 分类 and 完整度
  their rows have, counted per pair (抓到了, 完整 when the text area is missing, which the crawler does not flag;
  要人看 when it is there and empty, flagged empty_detail; 要人看, 不完整 when the id probe called it gone as well,
  gone_id, as the 教务处 archive's 27: 10-31).

Read only: frontier.db is opened with mode=ro (SQLite's own -wal and -shm files of a reader aside, nothing is written
next to it), the WARC files are only read, and every database read is one transaction, so the export sees one moment of
a run that may be going on (and says so). The folder must be new or empty and outside anything git would track: what
the school publishes holds full names and student numbers, which never go into git (CLAUDE.md).

Step 3's additions (spec 2026-11-08 §四 row 3 and 附 导出; the owner 11-08 【用户】: 「先做导出，别让内容管线等」; how
each is done is 【Claude 的执行办法】):
- four columns at the end of the manifest: 按钮编号 (a press's action.row_key, which the content stage's ids take
  instead of the button's words), 最后修改时间 (the archived answer's Last-Modified, a Drive file's modified), 日期来源
  (which rule found 网页上写的日期) and 来源 ('' for this round's archive, ORIGIN_OLD for a row filled from the 10-24
  one);
- a notice's date per platform (P75 of docs/crawler-plan.md): its platform's own date field (profiles.Detail.date,
  read alone or after its label: own_date; the platform by the page's own address), else the date its row states on
  its list (Writer.list_dates: the rows of the site's list pages and of the pages their pager brought, read as the
  crawler read them), else the first labelled date in its words;
- a page strict decoding refused (结果 编码有问题, item ⑧) read again with an encoding the page or its server names
  (redecode);
- --old-copies: the rows this round got nothing good of take the 10-24 archive's good copy as their file (OldCopies,
  item ⑦ of docs/round1-gaps-2026-11-08.md), their 结果 and 分类 kept;
- whether the site can be exported now and its data time (Ready: `nknu archive ready`, manifest.json's 可以导出 and
  资料截至), for the export service (research/crawler_lab_2026-11-04/export_sites.sh);
- what an export is made of (manifest.json's 导出依据: export_basis), and --skip-same, which writes nothing when the
  site's current export is made of the same things (the service's 没有变动);
- --reuse: the attachments and pictures a previous export of the site holds, hard-linked instead of read again (Reuse).

Blueprint item 22, parts 2 and 3 (the owner 11-09 「照这个做」 to the content session's proposal, relayed by it; the
crawler session's decisions on the read-only survey research/crawler_lab_2026-11-04/item22_1109/lists.md; how each is
done is 【Claude 的执行办法】 where neither says):
- 列表上的日期 for every page a list row links, not only a platform's detail pages (Writer._list_dates; the order of
  Writer._dates stays), from the rows of other sites' lists too: Snap.foreign_items, foreign_lists, foreign_presses
  (sso.nknu.edu.tw/services's 11,186 activity pages are linked from 25 unit sites' 书表 lists only, lists.md §2.3),
  read as the site's own lists are (_read_rows: Writer._foreign_rows), never written as rows of the site, and in
  存档内容 (snapshot_digest: the site is exported again when such a list changes);
- two columns at the end: 页别 (LIST_FIRST, LIST_PAGER or '': Writer._list_kinds) and 列表上的类别 (list_category: the
  categories the rows that link the page state);
- a page row that shares its file with an earlier row takes that row's dates (Writer._write; lists.md §4).

The school's answers of no data (the owner 11-09 「照这样做」 to the crawler session's question 「要在导出把开课课表的空回答
标「学校回答没有资料」，再重导那一站吗？」, 【用户同意 Claude 的建议】; the words are the content session's, which leaves
such rows out by them; how it is done is 【Claude 的执行办法】): a page whose platform's rule finds the school answering
that it has no data for what was asked (profiles.school_no_data: the course tables' pop-up 「系所未提供相關資料」, by the
page's own address, so in a unit site's export that lists one too) says the rule's words in 缺什么 (they begin with
NO_DATA), its 结果, 分类 and 完整度 as the archive judged them; every row whose file is such a page says them too
(Writer._no_data_rows), and the README names each such row. The review of 11-09: the words come last, after the
empty-area words; the README's summary and the command say how many such rows there are, and they, the README's
section and 缺什么's help quote the words as the cells hold them.
"""

from __future__ import annotations

import codecs
import csv
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import google_api, links, monitor, outcome, postback, profiles, progress, scope, sniff
from nknu.archive.frontier import _missing
from nknu.crawl.fetch import DecodeError, decode_body

TZ = ZoneInfo("Asia/Taipei")
PAGE, FILE, IMAGE, DATA, BUTTON = "网页", "附件", "图片", "资料", "按钮"
TYPES = (PAGE, FILE, IMAGE, DATA, BUTTON)
FOLDERS = {PAGE: "pages", FILE: "files", IMAGE: "images", DATA: "data"}
# the content stage reads the rows by these names (manifest.json's 项目, missing.csv by csv.DictReader), never by
# position: a column is only ever added at the end
COLUMNS = ("序号", "类型", "网址", "最后落地网址", "本地档案", "标题", "网页上写的日期", "日期原文", "完整度", "缺什么", "结果",
           "原因", "分类", "原因代码", "抓取时间", "大小", "sha256", "格式", "链到它的网页", "链接文字", "怎么来的", "备注",
           "存档编号", "按钮编号", "最后修改时间", "日期来源", "来源", "页别", "列表上的类别", "公告单位")
# 日期来源: which rule found a page's 网页上写的日期 (Writer._dates, in this order): the platform's date field (own_date),
# the date a list row that links the page states (Writer.list_dates; P75: the notices a WebIdx row's press brought have
# their list's address, which no detail path names; blueprint item 22: any page a row links, the row of this site's
# list or of another site's), the first labelled date in the page's words
OWN_FIELD, LIST_ROW, PAGE_WORDS = "详细页的日期栏", "列表上的日期", "网页上的日期字眼"
# 页别 (blueprint item 22, part 3; the names the content session proposed, the crawler session's rule, lists.md §3.1):
# a list's first page (its platform reads a list grid there), and a page its pager brought or a GET-paged list's later
# page; '' for every other page (a table of content with a pager, a page no platform reads a list grid on, a detail)
LIST_FIRST, LIST_PAGER = "列表", "列表翻页"
# 来源 of a row whose file is the 10-24 archive's copy (--old-copies, item ⑦: a gap row with a good copy there; a
# redirect sharing that file too); '' is this round's archive
ORIGIN_OLD = "10-24 旧存档"
# item ⑧ of the 11-08 gaps: a page strict decoding refused is read again with an encoding the page itself or its
# server names (_LABELS), never with one neither names: gb18030 reads almost any Big5 bytes whole into other
# characters, and Big5-HKSCS gives the character a Taiwanese site made for a rare character in a name (造字, in Big5's
# user-defined area) a Hong Kong one, both with nothing to show for it (review 11-08). None reading it whole, it is read
# with whichever of those, or of the crawler's own two (DEFAULTS: decode_body's UTF-8 and Big5), loses the fewest
# characters, each lost one as � (redecode)
DEFAULTS = ("utf-8", "cp950")
# the label a page or its server gives as a browser reads it (WHATWG Encoding Standard: Shift_JIS is Windows-31J,
# GB2312 and GBK lie within GB18030), the multi-byte ones only: a single-byte label (latin-1 reads any byte) proves
# nothing. 性别所's 10 old pages say Shift_JIS and read whole as cp932, and as gb18030 too, into other characters
# (11-08, the 10-24 archive's copies)
_LABELS = {"shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932", "windows-31j": "cp932",
           "ms_kanji": "cp932", "csshiftjis": "cp932", "euc-jp": "euc_jp", "big5": "cp950", "big-5": "cp950",
           "x-big5": "cp950", "cn-big5": "cp950", "csbig5": "cp950", "x-x-big5": "cp950", "big5-hkscs": "big5hkscs",
           "gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "gb18030": "gb18030", "euc-kr": "cp949",
           "utf-8": "utf-8", "utf8": "utf-8"}
_META_CHARSET = re.compile(rb"<meta\b[^>]*?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
# the encodings in plain words, for 备注 and the README (「按日文 Shift_JIS（cp932）重读」)
ENCODING_ZH = {"utf-8": "UTF-8", "cp950": "繁体中文 Big5", "big5hkscs": "繁体中文 Big5 加香港增补字",
               "gb18030": "简体中文 GB", "cp932": "日文 Shift_JIS", "euc_jp": "日文 EUC-JP", "cp949": "韩文 EUC-KR"}
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
            "media:drive_video": "云端硬盘的影片（只记档名和大小）", "media:drive_audio": "云端硬盘的声音档（只记档名和大小）",
            google_api.INSTALLER_WHY: "云端硬盘的安装档（只记档名和大小）"}
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
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f/\\:*?"<>|]+')  # C0, DEL and C1 controls, separators, reserved
_SLUG_BAD = re.compile(r"[^0-9A-Za-z㐀-鿿豈-﫿._-]+")
NAME_BYTES = 150  # a file name's own part, in UTF-8 bytes (macOS and Linux allow 255 in all)


class ExportRefused(Exception):
    """The export is not made (the message says why, in Chinese, for the command to print)."""


class Unchanged(Exception):
    """--skip-same: the site's current export is made of the same things this one would be (导出依据), so nothing is
    written (the message says so, in Chinese); the command exits with UNCHANGED."""


UNCHANGED = 3  # `nknu archive export --skip-same`'s exit code when nothing would change (export_sites.sh reads it)


# ---------------------------------------------------------------- the dates a page states


# the labels of the date a page was put up or changed (发布、發佈、公告、更新、張貼、刊登, with 日期 or 時間 after them or
# not; Posted, Published, Updated, with on or at or not): first. 「最新公告」 is a side list's heading, never a label
_STRONG = (r"(?:發\s*[佈布]|发\s*布|(?<!最新)公\s*告|更\s*新|張\s*貼|张\s*贴|刊\s*登)(?:\s*(?:日\s*期|時\s*間|时\s*间))?|"
           r"(?<![A-Za-z])(?:posted|published|updated?)(?:\s+(?:on|at))?(?![A-Za-z])")
# a bare 日期 or Date: only when no label above holds a date (an event page's 活動日期 may come before its 公告日期)
_WEAK = r"日\s*期|(?<![A-Za-z])date(?![A-Za-z])"
# a date as it is written: 2025/10/01, 2025-10-01, 2025.10.01, 2025年10月1日, a 民国 year of two or three digits
_DAY = (r"(?P<roc>(?:中\s*[華华]\s*)?民\s*[國国]\s*)?"
        r"(?P<y>\d{2,4})\s*(?P<s1>[/.\-年])\s*(?P<m>\d{1,2})\s*(?P<s2>[/.\-月])\s*(?P<d>\d{1,2})(?!\d)\s*(?:日)?")
_AFTER = r"[\s:︰\-–—|｜\]】)）]{0,8}" + _DAY
_DATES = tuple(re.compile(rf"(?P<label>{label}){_AFTER}", re.IGNORECASE) for label in (_STRONG, _WEAK))
# a date with no label before it (a platform's own date field, a list row's date cell), never inside a longer number
_BARE = (re.compile(r"(?<!\d)" + _DAY),)
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
    return next(_written_dates(text, _DATES), None)


def bare_date(text: str) -> tuple[str, str] | None:
    """The first date the text writes with no label before it, as find_date() reads a date after one (the same
    writings and checks: 年 goes with 月, one mark between the numbers, a 民国 year of two or three digits, a date that
    does not exist or falls outside YEARS passed over), with the words of the date alone: what a platform's own date
    field holds (「2024-08-15」, 「最新消息2026-03-10」, 「2026-10-22 ~ 2026-11-06」: its first) and a list row's date
    cell. None when it writes none."""
    return next(_written_dates(text, _BARE), None)


def _written_dates(text: str, patterns) -> Iterator[tuple[str, str]]:
    """Each date the text writes after one of these patterns, in their order (a pattern's matches in the text's order),
    as (YYYY-MM-DD, the words as the page writes them, white space as one space)."""
    raw = text or ""
    half = raw.translate(_HALF)
    for pattern in patterns:
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
            yield got.isoformat(), re.sub(r"\s+", " ", raw[m.start():m.end()]).strip()


def changed_at(value: str | None) -> str:
    """最后修改时间: an HTTP date (RFC 7231: Last-Modified's IMF-fixdate, or the obsolete RFC 850 and asctime forms;
    one with no zone is UTC, as every HTTP date is) or a Drive item's modified (RFC 3339), as an ISO time in Taipei
    with seconds; '' when there is none, it cannot be read, or its year falls outside YEARS (a server that does not
    know says 1970-01-01, as find_date takes such a year for none)."""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text) if re.match(r"\d{4}-\d\d-\d\dT", text) else parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        local = dt.astimezone(TZ)
    except (ValueError, OverflowError):
        return ""
    return local.isoformat(timespec="seconds") if YEARS[0] <= local.year <= YEARS[1] else ""


def encoding_words(cs: str, said: str = "") -> str:
    """An encoding in plain words, its codec name and who names it after it: 「日文 Shift_JIS（cp932；网页自己写的编码是
    shift_jis）」, 「繁体中文 Big5（cp950）」, 「UTF-8」."""
    plain = ENCODING_ZH.get(cs, cs)
    inside = "；".join(x for x in ("" if plain.lower() == cs.lower() else cs, said) if x)
    return f"{plain}（{inside}）" if inside else plain


def _labelled_charsets(body: bytes, ctype: str | None) -> list[tuple[str, str]]:
    """The encodings a page names (its own <meta> label first, then its HTTP header's charset), as browsers read the
    label (_LABELS: a label not there names none), each with who names it, in words."""
    out = []
    m = _META_CHARSET.search(body[:4096])
    meta = m.group(1).decode("ascii", "replace").lower() if m else ""
    if meta in _LABELS:
        out.append((_LABELS[meta], f"网页自己写的编码是 {meta}"))
    h = _HEADER_CHARSET.search(ctype or "")
    header = h.group(1).lower() if h else ""
    if header in _LABELS:
        out.append((_LABELS[header], f"服务器说的编码是 {header}"))
    return out


def _pair(exc: UnicodeError) -> tuple[str, int]:
    """A double-byte encoding's pair it cannot read as one �, as a browser reads it (WHATWG Encoding Standard: the
    lead byte takes the byte after it along unless that byte is ASCII, which is read again as itself). Python's own
    replace takes the lead byte alone, and the byte after it then pairs with the next character: a 造字 for 王 in
    「王O明」 would take the O with it. UTF-8 is read as Python reads it (each broken sequence one �)."""
    if not isinstance(exc, UnicodeDecodeError):
        raise exc
    data, i = exc.object, exc.start
    double = exc.encoding.replace("_", "-").lower() not in ("utf-8", "utf8")
    if double and data[i] >= 0x81 and i + 1 < len(data) and data[i + 1] >= 0x80:
        return "\ufffd", max(exc.end, i + 2)
    return "\ufffd", exc.end


codecs.register_error("nknu.pair", _pair)


def redecode(body: bytes, ctype: str | None = None) -> tuple[str, str, str, int]:
    """A page strict decoding refused (nknu.crawl.fetch.decode_body: the header's charset, UTF-8, Big5; 结果 编码有问题)
    read again, as (text, encoding, who names it in words or '', how many characters it lost): strictly with an
    encoding the page names (_labelled_charsets: its own <meta> label, then its server's charset), the first that
    reads the whole body (lost 0); else with whichever of those and of DEFAULTS loses the fewest characters (each lost
    one a �: _pair; of two that lose as many, the first). Never with an encoding nothing names (review 11-08: gb18030
    and Big5-HKSCS read a Big5 page with one 造字 whole, into mojibake or a wrong character in a name; a � is better
    than a guess).
    A body that begins with UTF-8's byte order mark is UTF-8 whatever else it says (a browser reads it so): 英语系's
    106 English pages are such, a Big5 comment in each breaking it (11-08, the 10-24 archive's copies), read as UTF-8
    with a � for each broken sequence, as before."""
    bom = body.startswith(codecs.BOM_UTF8)
    named = [("utf-8", "")] if bom else _labelled_charsets(body, ctype)
    for cs, said in named:
        try:
            text = body.decode(cs).removeprefix("\ufeff")
        except UnicodeError:
            continue
        if "\ufffd" not in text:
            return text, cs, said, 0
    best: tuple[str, str, str, int] | None = None
    tried: set[str] = set()
    for cs, said in (named if bom else [*named, *((cs, "") for cs in DEFAULTS)]):
        if cs in tried:
            continue
        tried.add(cs)
        text = body.decode(cs, "nknu.pair").removeprefix("\ufeff")
        lost = text.count("\ufffd")
        if best is None or lost < best[3]:
            best = (text, cs, said, lost)
    assert best is not None  # DEFAULTS holds two
    return best


# ---------------------------------------------------------------- one item of the manifest


@dataclass
class Item:
    key: str  # u<url id>, a<action id>, k<page_res key> (no row), n:<address> (a picture the rules never fetch)
    type: str
    url: str
    final_url: str = ""
    title: str = ""
    result: str = ""
    gone_why: str = ""  # gone for a reason of its own (outcome.GONE_HOME): its 结果 says so instead of gone's words
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
    # a page the school answered with no data for what was asked (profiles.school_no_data), and every row whose file
    # is such a page: the rule, its words in 缺什么 (Writer._no_data; the owner 11-09 「照这样做」)
    no_data: profiles.NoData | None = None
    owner: str = ""  # the site the row counts for (url.owner_site; a press: the site of its list)
    order: tuple = ()
    row_key: str = ""  # 按钮编号: the action row's row_key of an item a press brought (pager: p<step>)
    modified: str = ""  # 最后修改时间: changed_at() of the archived answer's Last-Modified, or of a Drive file's modified
    date_from: str = ""  # 日期来源: OWN_FIELD, LIST_ROW or PAGE_WORDS, '' with no date
    origin: str = ""  # 来源: '' for this round's archive, ORIGIN_OLD for the 10-24 one's copy (a redirect sharing it too)
    filled: bool = False  # it took the 10-24 archive's copy itself (--old-copies, item ⑦)
    decoded: str = ""  # a page strict decoding refused: the encoding redecode() read it with
    lost: int = 0  # of such a page, the characters that encoding could not read (each a � in its file)
    seen_lost: int = 0  # of those, the ones in what a reader sees (its words and title, not comments or link addresses)
    judged: dict | None = None  # page_completeness's row of a page the views judged by an answer the crawler could
    # not decode (own decode): judged again as the page it is once the export reads it whole (Writer._read_whole)
    own: str = ""  # a page's result the views judge it by: its own, or a redirect's landing page's
    linked: bool = False  # its file is a hard link to the previous export's (--reuse)
    list_kind: str = ""  # 页别: LIST_FIRST, LIST_PAGER or '' (Writer._list_kinds; blueprint item 22, part 3)
    list_category: str = ""  # 列表上的类别: list_category() of the rows that link it (Writer._list_dates; part 2)
    unit: str = ""  # 公告单位: own_unit() of the page the row's file is (Writer._page, _unit_rows; the owner 11-15)

    def row(self, n: int, page_url, cap: int | None = None) -> dict[str, str]:
        """The manifest's row. cap: name at most that many pages and link texts (manifest.csv and missing.csv: a
        layout picture on every page of a site of 900 pages would pass the 32767 characters an Excel cell holds),
        saying how many there are; manifest.json names them all."""
        return {"序号": str(n), "类型": self.type, "网址": self.url, "最后落地网址": self.final_url, "本地档案": self.path,
                "标题": self.title, "网页上写的日期": self.date, "日期原文": self.date_raw,
                "完整度": self.completeness(), "缺什么": self.lacks,
                "结果": self.result_words(), "原因": self.why,
                "分类": progress.BUCKET_ZH.get(self.bucket, self.bucket), "原因代码": self.codes,
                "抓取时间": self.fetched_at, "大小": "" if self.size is None else str(self.size), "sha256": self.sha256,
                "格式": self.sniff, "链到它的网页": _joined((page_url(p) for p in self.pages), cap, "页"),
                "链接文字": _joined((t for t in self.texts if t), cap, "个"), "怎么来的": self.how,
                "备注": "；".join(dict.fromkeys(self.notes)), "存档编号": self.ref, "按钮编号": self.row_key,
                "最后修改时间": self.modified, "日期来源": self.date_from, "来源": self.origin,
                "页别": self.list_kind, "列表上的类别": self.list_category, "公告单位": self.unit}

    def result_words(self) -> str:
        """结果: the result in words; a gone with a reason of its own (a file sent to the school's home page, 11-03)
        says that reason. Its category stays gone's (学校那边已经不存在), which the content stage reads."""
        if self.result == "gone" and self.gone_why in outcome.GONE_WHY_LABEL:
            return outcome.GONE_WHY_LABEL[self.gone_why]
        return outcome.RESULT_LABEL.get(self.result, self.result)

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
    ready: Ready | None = None  # whether the site could be exported (可以导出) and its data time (资料截至)
    linked: int = 0  # the attachments and pictures hard-linked from the previous export (--reuse)
    old_copies: int = 0  # the rows whose file is the 10-24 archive's copy (--old-copies, item ⑦)


# ---------------------------------------------------------------- the checks before anything is written


def _git_top(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def check_out(root: Path, out: Path, read: Iterable[tuple[str, Path]] = ()) -> None:
    """Refuse an out folder that is not empty, that lies in the archive or in another folder the export only reads
    (read: (what it is, in words; the folder resolved): the 10-24 copies, the previous export; review 11-08), or that
    git would track (ExportRefused)."""
    if out == root or root in out.parents:
        raise ExportRefused(f"{out} 在存档目录里：存档只读，导出要放在存档目录外面（例如 archive-export/）")
    for what, folder in read:
        if out == folder or folder in out.parents:
            raise ExportRefused(f"{out} 在{what} {folder} 里：它只读，导出要放在它外面")
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


# ---------------------------------------------------------------- whether a site can be exported now (step 3)


@dataclass
class Ready:
    """Whether a site can be exported now (spec 2026-11-08 §二: a site is 爬完 for the content stage when nothing of it
    is left to fetch; `nknu archive ready`, manifest.json's 可以导出): it can when nothing of it waits to be fetched or
    pressed, the url rows it owns that are pending or active (a retry, a row waiting for the lab too) and its presses
    that are, which is progress.site_numbers' left, the ✓ on the site's row of the dashboard (never a rule of its own
    here). Its state is monitor.site_status's, as the dashboard and the export's README say it; as_of (资料截至) is the
    latest fetch among its own rows (url.fetched_at, action.done), Taipei's ISO time, '' with none."""

    site: str
    name: str
    known: bool  # the archive has the site: a site_plan row, or a url row it owns
    urls: int
    presses: int
    as_of: str
    state: str = "unchecked"

    @property
    def can(self) -> bool:
        return self.known and not self.urls and not self.presses

    @property
    def state_words(self) -> str:
        if not self.known:
            return "存档里没有这个网站"
        sign, words, _color = progress.STATE_SHOW.get(self.state, ("⚠", "没检查", "yellow"))
        return f"{sign} {words}".strip()

    def line(self) -> str:
        """The site's line in `nknu archive ready`."""
        site = f"{self.site}（{self.name}）" if self.name else self.site
        if not self.known:
            return f"✗ {site}：存档里没有这个网站（网站键打错了？）"
        verdict = "可以导出" if self.can else f"还没爬完，还有 {self.urls} 个网址、{self.presses} 个按钮在排队"
        return (f"{'✓' if self.can else '…'} {site}：{verdict}；状态 {self.state_words}；"
                f"资料截至 {self.as_of or '（还没抓到东西）'}")

    def as_dict(self) -> dict:
        return {"网站": self.site, "名称": self.name, "可以导出": self.can, "状态": self.state_words,
                "还在排队的网址": self.urls, "还在排队的按钮": self.presses, "资料截至": self.as_of}


def readiness(conn: sqlite3.Connection, now: float, sites: Iterable[str] | None = None) -> dict[str, Ready]:
    """Ready of these sites, by default the dashboard's (progress.run_scope: the sites of the last run_start, every
    round-1 site of the list when no run named any), in that order, read in the caller's transaction; their state is
    left to the caller (monitor.site_status reads every page). A site neither the list nor any row knows (a mistyped
    key) is known False, never one that can be exported."""
    run = progress.run_scope(conn, progress._hosts(conn))
    want = list(dict.fromkeys(sites)) if sites is not None else list(run.sites)
    presses = progress._presses(conn, now)
    nums = progress.site_numbers(conn, now, replace(run, sites=tuple(want)), presses)
    pressing: Counter = Counter()
    for r in presses:
        if r["state"] not in progress.PRESS_FINISHED:
            pressing[r["site"]] += r["n"]
    names = {r[0]: r[1] or "" for r in conn.execute("SELECT site, name FROM site_plan")}
    owned: Counter = Counter()
    last: dict[str, float] = {}
    for i in range(0, len(want), 500):
        part = want[i:i + 500]
        marks = ",".join("?" * len(part))
        # fetched_at is frontier.utc_now()'s ISO time: strftime reads it whatever its offset
        for s, n, at in conn.execute(f"""SELECT owner_site, count(*), max(CAST(strftime('%s', fetched_at) AS INTEGER))
                                         FROM url WHERE owner_site IN ({marks}) GROUP BY owner_site""", part):
            owned[s] = n
            if at is not None:
                last[s] = float(at)
        # a press counts for its list page's site, as the dashboard counts it (progress._presses)
        for s, at in conn.execute(f"""SELECT coalesce(u.owner_site, a.site), max(a.done) FROM action a
                                      LEFT JOIN url u ON u.id = a.list_id
                                      WHERE a.done IS NOT NULL AND coalesce(u.owner_site, a.site) IN ({marks})
                                      GROUP BY 1""", part):
            if at is not None:
                last[s] = max(last.get(s, float(at)), float(at))
    return {s: Ready(s, names.get(s, ""), s in names or bool(owned[s]), nums[s]["left"] - pressing[s], pressing[s],
                     _taipei(last[s]) if s in last else "") for s in want}


def ready(root: Path, sites: Iterable[str] | None = None, *,
          now: float | None = None) -> tuple[list[Ready], int | None]:
    """`nknu archive ready`: Ready of every site the dashboard lists, or of these, each with its state (monitor.
    site_status's; 没检查 when it cannot be had, never a state made up here); read only, as the export is (one
    transaction of a read-only connection: a run may be going on). Also the pid of a run that holds the archive."""
    from nknu.archive.crawler import lock_holder

    root = Path(root).expanduser().resolve()
    now = time.time() if now is None else now
    pid = lock_holder(root)
    conn = connect(root / "frontier.db")
    try:
        conn.execute("BEGIN")
        try:
            got = readiness(conn, now, sites)
            try:
                status = monitor.site_status(monitor._frontier(conn), now)
            except Exception:  # noqa: BLE001 - the state is said as 没检查; whether it can be exported does not need it
                status = {}
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    for r in got.values():
        st = status.get(r.site)
        if st is not None:
            r.state = st.state
    return list(got.values()), pid


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
    ready: Ready | None = None  # whether the site could be exported, and its data time (资料截至)
    ready_error: str | None = None  # why ready is None
    # other sites' lists whose rows link the site's pages (blueprint item 22, part 2; lists.md §2.3): the item entries
    # of their list pages that name a row of the site (page_id, res_key, res_id), those list pages (url_result rows by
    # id) and their pager presses (action_result rows by id). Read for the rows' dates and categories, never rows of
    # the export
    foreign_items: list[dict] = field(default_factory=list)
    foreign_lists: dict[int, dict] = field(default_factory=dict)
    foreign_presses: dict[int, dict] = field(default_factory=dict)


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
        # manifest.json's 可以导出 and 资料截至, as `nknu archive ready` says them; an archive older than the dashboard
        # (NOT_READ: the 10-26 pilot's presses have no next_at, which progress._presses reads) has neither
        site_ready, ready_error = None, None
        try:
            site_ready = readiness(conn, now, [site])[site]
            site_ready.state = status.state if status is not None else site_ready.state
        except sqlite3.OperationalError as exc:
            ready_error = f"{type(exc).__name__}: {exc}"
        foreign_items, foreign_lists, foreign_presses = _foreign(conn, site)
    finally:
        conn.execute("COMMIT")
    return Snap(dict(plan) if plan is not None else None, rows, page_ids, pc, pres, raw_res, actions, walks, drive,
                drive_of_content, landing, orphans, status, status_error, videos, other_site_items, press_to,
                site_ready, ready_error, foreign_items, foreign_lists, foreign_presses)


def _foreign(conn: sqlite3.Connection, site: str) -> tuple[list[dict], dict[int, dict], dict[int, dict]]:
    """The other sites' lists whose rows link the site's rows, in the caller's transaction (blueprint item 22, part 2,
    across sites; the survey's query, lists.md §2.8 item 2, 0.1 to 0.7 seconds on round one's archive): the item
    entries of list pages another site owns (or none) that name a row of this site, in their key's order; those list
    pages' url_result rows; the pager presses of their chains (action_result rows: a row on a page the pager brought
    has its entry on the list page too, postback.Chains._plan)."""
    items = [dict(r) for r in conn.execute(
        """SELECT p.page_id, p.res_key, p.res_id FROM page_res p JOIN url l ON l.id = p.page_id
           WHERE p.role = 'item' AND p.res_key LIKE 'item:%' AND p.res_id IN (SELECT id FROM url WHERE owner_site = ?)
             AND coalesce(l.owner_site, '') != ? ORDER BY p.page_id, p.res_key""", (site, site))]
    lists = _rows_by_id(conn, {r["page_id"] for r in items})
    presses: dict[int, dict] = {}
    ids = sorted(lists)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for a in conn.execute(f"SELECT * FROM action_result WHERE kind = 'pager' AND list_id IN "
                              f"({','.join('?' * len(part))})", part):
            presses[a["id"]] = dict(a)
    return items, lists, presses


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


def unquoted(part: str) -> str:
    """A part of an address decoded (%XX as UTF-8, as unquote reads it), and once more when that gives UTF-8's own
    bytes one character each (a name percent-encoded twice: 146 of 圖資處's addresses in the 10-24 archive, e.g.
    %C3%A5%C2%85%C2%AC… for 公告…, whose names would otherwise hold C1 control characters): every character is then
    below U+0100 and their bytes read whole as UTF-8. A real Latin-1 name (café) does not read so, and stays."""
    name = unquote(part)
    if any("\x80" <= c <= "\xff" for c in name) and all(c <= "\xff" for c in name):
        try:
            return name.encode("latin-1").decode("utf-8")
        except UnicodeDecodeError:
            pass
    return name


def _last_part(url: str) -> str:
    """An address's last path part, decoded (unquoted): what a file not got is called in the manifest."""
    return unquoted(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


def _worst(results) -> str:
    return min(results, key=lambda x: (_BUCKET_RANK.get(monitor.bucket(x), -1), x))


def best_presses(actions: Iterable[dict]) -> list[dict]:
    """The press that stands for each (list, kind, row key) of these presses (design §7.5 step 9: a newer press of a
    row stands for the older ones), one each: of that key's presses in (epoch, id) order, the first done one, else the
    latest one not superseded, else the latest. In the order each key's first press comes in that order, which is no
    order of the chosen ones: the callers sort them. Builder._presses lists them; Writer._foreign_rows reads the pager
    pages of other sites' lists by them (blueprint item 22)."""
    best: dict[tuple, dict] = {}
    for a in sorted(actions, key=lambda a: (a["epoch"], a["id"])):
        key = (a["list_id"], a["kind"], a["row_key"])
        old = best.get(key)
        if old is None or old["state"] != "done" and (a["state"] == "done" or a["state"] != "superseded"
                                                        or old["state"] == "superseded"):
            best[key] = a
    return list(best.values())


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
                      why=self.GAP, how=f"列表 {lst['url']} 上的按钮（{a['kind']}）", row_key=a["row_key"] or "")
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
                if own == "decode":
                    it.judged = p
            it.own, it.bucket = own, monitor.bucket(own, page=True)
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

    @staticmethod
    def _lacks(p: dict) -> str:
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
        for a in sorted(best_presses(self.s.actions.values()),
                        key=lambda a: (a["list_id"], a["kind"] != "pager", a["step"], a["id"])):
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
                      name=a["filename"] or "", owner=self.site, row_key=a["row_key"] or "")
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
            if kind == "detail" and profiles.menu_choice(a.get("argument")) is not None:
                # a menu's form sent with one choice (profiles.Menu: the course tables, gap ⑩ of 11-08)
                it.how = f"网页 {lst['url']} 的选单选「{a['row_text'] or ''}」后按送出（回传）"
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
                                        "pages": [], "keys": [], "needs": set(), "gone_whys": set()})
            g["roles"][r["role"]] += 1
            g["results"].add(outcome.SCHOOL_SIDE if r["marked"] else r["result"])
            g["pages"].append(r["page_id"])
            g["keys"].append((r["page_id"], r["res_key"]))
            g["needs"].add(r["need"])
            if r["result"] == "gone" and not r["marked"]:
                g["gone_whys"].add(r.get("gone_why") or "")
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
                          owner=self.site, row_key=(a or {}).get("row_key") or "")
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
                    it.modified = changed_at(d["modified"])  # before the answer's Last-Modified (Writer._write)
                ok_type = (to.get("sniff") != "html") if typ in (FILE, IMAGE, DATA) else not _expected_file(to)
                if result in BODY_RESULTS and _has_body(to) and ok_type:
                    it.source = (to["warc"], to["warc_offset"], to["sha256"], to["ctype"])
                it.why = _why(result, to["reason"], to["status"])
                if result == "gone" and len(g["gone_whys"]) == 1:  # every entry gone for the same reason ("": gone's own)
                    it.gone_why = next(iter(g["gone_whys"]))
                    it.codes = " ".join(c for c in (it.gone_why, it.codes) if c)
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
            it.modified = changed_at((s.drive_of_content.get(uid) or d or {}).get("modified"))
            it.why = _why(result, r["reason"], r["status"])
            item = s.drive_of_content.get(uid) or d  # a program on Drive: its metadata row, or its content row got
            if item is not None and not (item.get("mime") or "").startswith(google_api.GOOGLE_APPS) \
                    and outcome.installer_name(item.get("name")):  # before the rule (gap ⑪ of 11-08): listed only
                it.result, it.why = "rule", RULE_WHY[google_api.INSTALLER_WHY]
            elif d is not None and (d.get("mime") or "").startswith(("video/", "audio/")):
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


def _profile(url: str, site: str) -> profiles.Profile | None:
    """The platform of a page: by its own address first (profiles.profile_for), then by the site it is exported for.
    A page's address says which template it is: 图资处's pages are SSO WebIdx pages (lis's own profile is its old
    Joomla site's), the /site/ keys' pages answer as WebIdx pages too (gap ② of 11-08), and a redirect may land on
    another platform (c.nknu/uk's newsdetail on the SSO 公告中心)."""
    return profiles.profile_for(url) or profiles.profile_for_site(site)


def page_title(tree: LexborHTMLParser, url: str, site: str, fallback: str = "") -> str:
    """The page's own title: its platform's detail title (profiles.Detail.title), else the first cell with words of
    those the title rules name for its kind of page, in their order (profiles.PageTitle, by the page's own address: an
    activity's name, a forum question's title, an alumnus's name, the owner 11-14 「先补标题栏」; an entry's title, a
    teacher's name, 地理系's member and page names, the owner 11-15), else its first h1, else its first h2, else
    <title>. c.nknu's <title> is the unit's name on every page, so the headings come first; a cell with no words
    leaves the reading to them (3 of round one's 11,449 activity pages)."""
    prof = _profile(url, site)
    parts = urlsplit(url)
    where = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    if prof is not None:
        for d in prof.details:
            if d.title and re.search(d.paths, where):
                got = _first_text(tree, d.title)
                if got:
                    return got
    for rule in profiles.page_title_rules(url):
        got = _first_text(tree, rule.title)
        if got:
            return got
    for css in ("h1", "h2", "title"):
        got = _first_text(tree, css)
        if got:
            return got
    return fallback


NO_AREA = "找不到内文区（网页上没有公告的标题栏和内文栏，多半是学校删掉的公告只剩网站模板）"
EMPTY_AREA = "内文区是空的（公告的标题栏和内文栏都没有内容，多半是学校删掉的公告）"
# what every profiles.NoData's words begin with (the owner 11-09 「照这样做」; the content session's words: it leaves the
# rows whose 缺什么 holds them out and counts them), and what 备注 says once of such a row (Writer._no_data)
NO_DATA = "学校回答没有资料"
NO_DATA_NOTE = "「学校回答没有资料」是程序看网页里学校的讯息标的；完整度、分类、结果照存档的判断，没有改"


def empty_detail_area(tree: LexborHTMLParser, url: str, site: str) -> str:
    """What a notice page (its platform's detail page: profiles.Detail) lacks in its own areas, in words, or '':
    NO_AREA when neither its title area nor its text area is on the page, EMPTY_AREA when they are and hold nothing
    (no words, no picture, link or frame, no attachment link). c.nknu shows a notice the school deleted as the site's
    template, its DataFormView with neither label in it (10-26 pilot, 体育室: 27 pages from the old archive and Common
    Crawl, every one 15269 bytes, as the page of a notice number that cannot exist). The crawler's own check
    (profiles.empty_detail) claims nothing when the text area is not there, so the views call such a page 抓到了,
    完整; a text area there and empty is flagged empty_detail (页面几乎是空的, so 要人看), and gone_id as well when the
    host's id probe found its number empty (看起来像不存在, so 要人看, 不完整: the 教务处 archive's 27, 10-27). The export keeps the views' judgement, whatever it is, says this in 缺什么, and the
    README names each page and says the 分类 and 完整度 their rows have (_judged) (review 10-27, 10-31,
    【Claude 的执行办法】; whether the crawler should call every such page gone is the main session's call)."""
    prof = _profile(url, site)
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


def own_unit(tree: LexborHTMLParser, url: str, site: str) -> str:
    """The unit that posted a notice, as its platform's detail page states it in its own field (profiles.Detail.unit;
    the platform by the page's address first: _profile): the field's words on one line, '' for a site no profile
    describes, a page that is not such a detail, a platform that names no such field, and a field with no words. The
    owner 11-15 「这 5 件全部照你的设计做。」 (the content session's item 5: 「公告平台上的公告是谁发的（发布单位栏）：
    目前内容那边读不出来」): the SSO 公告中心's 「公告單位：」, its words as the page has them (a unit's full name there:
    「教務處註冊組」), which the content stage maps to its own unit tree."""
    prof = _profile(url, site)
    if prof is None:
        return ""
    parts = urlsplit(url)
    where = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    return next((_first_text(tree, d.unit) for d in prof.details if d.unit and re.search(d.paths, where)), "")


def own_date(tree: LexborHTMLParser, url: str, site: str) -> tuple[str, str] | None:
    """The date a platform's detail page states in its own date field (profiles.Detail.date; the platform by the
    page's address first: _profile): the date the first of the field's matches holds, as find_date() reads it after a
    label there or, with none (most platforms write the date alone: SSO WebIdx's <span><i class="fa-calendar"></i>
    2024-08-15</span>, 总务处's newsview, 国际处, the departments' sites), as bare_date() reads it. Its words are those
    of a label that states the same day in the field's parent, or in the element above when the parent only wraps
    the field (_labelled: c.nknu's newsdetail, <h5>發佈人員：…｜公告日期：<span id="lbl_AddTime">2026/10/22 上午 10:49:00
    </span></h5> → 公告日期：2026/10/22; the SSO 公告中心's 公告時間： two levels up), else the field's own. A label there
    that states another day is no label of the field's (tecs's parent holds the notice's title: 「…榜單公告115年12月30
    日」).
    A field that shows the day and the month only is dated by the platform's address when the two agree
    (Detail.date_path: WordPress's 「30 6 月」 and /2026/06/30/). None for a site no profile describes, a page that is
    not such a detail, and a field that is empty (a notice the school deleted) or holds no date. Writer._page reads it
    before the notice's list row and the page's text, where a notice states other dates after labels too: an exam's
    考試日期, 「榜單公告115年12月30日」 (79 of the 501 教务处 notices whose field holds a date got such a date; 技术总览
    §7, plan 11-02 T1; 11-08: 36 SSO WebIdx, 150 公告中心 and 52 c.nknu notices of round one)."""
    prof = _profile(url, site)
    if prof is None:
        return None
    parts = urlsplit(url)
    where = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    for d in prof.details:
        if not d.date or not re.search(d.paths, where):
            continue
        for node in tree.css(d.date):
            own = " ".join((node.text() or "").split())
            if not own:
                continue
            mine = find_date(own) or bare_date(own)
            if mine is not None:
                return _labelled(node, own, mine[0]) or mine
            if d.date_path and (got := _path_date(d.date_path, own, parts.path.lower())) is not None:
                return got
    return None


LABEL_UP = 2  # how far above its date field a label of the field is looked for: its parent, then the element above


def _labelled(node, own: str, day: str) -> tuple[str, str] | None:
    """The words of a label that states this day (find_date's labels: 公告日期, 公告時間, 發佈日期 …) in the date
    field's parent, or, when the parent holds the field's words alone (a wrapper: the SSO 公告中心's <span><span
    id="…lbStartEndDate">), in the element above it, up to LABEL_UP; None when the first of them that states labelled
    dates states none of this day, or holds more than the field and no labelled date (SSO WebIdx's meta: the title
    above it may end in 「公告」, no label of the date's). White space is taken as one space (the 公告中心 writes about
    25 between its label and its date). own: the field's words, white space as one space."""
    up = node.parent
    for _level in range(LABEL_UP):
        if up is None:
            return None
        text = " ".join((up.text() or "").split())
        said = list(_written_dates(text, _DATES))
        if said:
            return next((got for got in said if got[0] == day), None)
        if text != own:
            return None
        up = up.parent
    return None


def _path_date(pattern: str, own: str, path: str) -> tuple[str, str] | None:
    """A date field that shows a day and a month (WordPress's 「30 6 月」) dated by the page's address (Detail.
    date_path's groups y, m, d): that date when the field's first two numbers are its day and its month, in either
    order; the words are the field's and the part of the address. None otherwise."""
    m = re.search(pattern, path)
    nums = [int(n) for n in re.findall(r"\d+", own.translate(_HALF))][:2]
    if m is None or len(nums) != 2:
        return None
    try:
        day = date(int(m["y"]), int(m["m"]), int(m["d"]))
    except (IndexError, ValueError):
        return None
    if not YEARS[0] <= day.year <= YEARS[1] or sorted(nums) != sorted((day.day, day.month)):
        return None
    return day.isoformat(), f"{own}（网址 {m.group(0)}）"


def page_texts(tree: LexborHTMLParser, site: str, url: str = "") -> list[str]:
    """The page's words to look for a date in: its content area first (its platform's content_css; the platform by
    the page's address first when it is given: _profile), then all of it. Scripts and styles are taken out (the tree
    is changed)."""
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    out = []
    prof = _profile(url, site) if url else profiles.profile_for_site(site)
    if prof is not None and prof.content_css:
        nodes = tree.css(prof.content_css)
        if nodes:
            out.append("\n".join(n.text(separator="\n", strip=True) for n in nodes))
    body = tree.body or tree.root
    out.append(body.text(separator="\n", strip=True) if body is not None else "")
    return out


# ---------------------------------------------------------------- names


def safe_name(name: str, max_bytes: int = NAME_BYTES) -> str:
    """A file name any of the three systems takes: no path separators, control (C0, DEL, C1) or reserved characters,
    no invisible format characters (Unicode Cf: a right-to-left override shows gpj.exe as exe.jpg, a zero-width space
    hides), no dots or spaces at the ends, at most max_bytes of UTF-8 with the extension kept. Chinese stays as it
    is."""
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
    seg = unquoted(parts.path.rstrip("/").rsplit("/", 1)[-1]) or "index"
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
        name = _last_part(url.split("#", 1)[0]) or "file"
    ext = sniff.name_ext(name)
    want = SNIFF_EXT.get(it.sniff) if it.sniff != "ole" else _OLE_EXT.get((ctype or "").split(";")[0].strip(), "")
    if want and (not ext or (ext in _PROGRAM_EXT and ext != want)):
        name = f"{name}.{want}"
    return name


# ---------------------------------------------------------------- writing


class Reuse:
    """A previous export of the same site (--reuse): one export of every site is about 60 GB and the lab PC has room
    for one (11-08), so an attachment or a picture whose sha256 a row of that export's manifest.json has, its file
    there at the size the row says and with that sha256 (read from the file itself, review 11-08: a file changed in
    place at the same size would be carried on under the archive's sha256), is hard-linked to its new place (os.link:
    the two folders hold one file) instead of read out of the WARC and written again. Its record's headers are still
    read (its name, Last-Modified), its body is not. A folder of another site (its manifest's 网站), or one whose
    manifest cannot be read, is not used at all (why says why); a missing or changed file, or a link the file system
    refuses (another one, no hard links), is read from the archive as usual. Pages are always written again.
    【Claude 的执行办法】"""

    def __init__(self, folder: Path, site: str):
        self.folder = Path(folder).expanduser().resolve()
        self.files: dict[str, list[tuple[str, int]]] = defaultdict(list)  # sha256 -> (本地档案, 大小) of its rows
        self.why = ""
        try:
            prev = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.why = f"读不到它的 manifest.json（{type(exc).__name__}）"
            return
        if not isinstance(prev, dict) or prev.get("网站") != site:
            self.why = f"它是别的网站（{(prev.get('网站') if isinstance(prev, dict) else '') or '没写'}）的导出"
            return
        for r in prev.get("项目") or []:
            if (isinstance(r, dict) and r.get("类型") in (FILE, IMAGE) and r.get("本地档案") and r.get("sha256")
                    and str(r.get("大小") or "").isdigit()):
                self.files[r["sha256"]].append((r["本地档案"], int(r["大小"])))

    def find(self, sha: str, size: int | None) -> Path | None:
        """The previous export's file with this sha256, when it is there, inside its folder, at the size its row says
        (and the archive records, when it does) and its bytes hash to that sha256; None otherwise."""
        for rel, want in self.files.get(sha, ()):
            if size is not None and size != want:
                continue
            try:
                path = (self.folder / rel).resolve(strict=True)
                if not (self.folder in path.parents and path.is_file() and path.stat().st_size == want):
                    continue
                with open(path, "rb") as fh:
                    if hashlib.file_digest(fh, "sha256").hexdigest() == sha:
                        return path
            except (OSError, RuntimeError):
                continue
        return None


# item ⑦ of the 11-08 gaps (--old-copies): the results (url_result) of this round's rows that came to nothing good of
# their own. A row with no file in the export whose result is one of OLD_NO_FILE takes its copy from the 10-24
# archive (a row waiting for the lab, lab, too), and so does a row whose result is one of OLD_ALWAYS: a redirect the
# old-copies folder holds (old_copies_1108.py keeps only those that tell the item is gone: back to the school's home
# page, to a 查无资料 page), a page that came back thin, one the id probe calls gone (looks_gone: its body is the
# template of a number that does not exist), and one a person marked as the school's problem (school_side: what this
# round got of it is the school's broken answer; every result a person may mark is one of these, or one with no file,
# outcome.SCHOOL_SIDE_RESULTS). A row whose own answer this round is good never does. The folder's builder takes
# exactly these (old_copies_1108.TAKE, a redirect as said): an index row the export would not take is never made, and
# one whose row came to something else since is counted (review 11-08: a row of the index neither taken nor counted
# is a silent gap)
OLD_NO_FILE = frozenset({"gone", "not_file", "failed", "server_error", "login", "refused", "short_dead", "api_error",
                         "lab"})
OLD_ALWAYS = frozenset({"redirect", "thin", "looks_gone", outcome.SCHOOL_SIDE})
_SHA = re.compile(r"[0-9a-f]{64}")
_IMAGE_SNIFFS = frozenset({"png", "jpeg", "gif", "webp", "tiff"})


class OldCopies:
    """The 10-24 archive's good copies of round one's gaps (item ⑦ of docs/round1-gaps-2026-11-08.md; the owner 11-08
    【用户】: 「⑦（10-24 旧存档里的 870 个正常副本，含图资处 337 个真档）……在导出或整理时一并补上」; CLAUDE.md: 抓取失败或
    网站改版时，保留旧值): the folder research/crawler_lab_2026-11-04/old_copies_1108.py makes, its index.json (rows:
    this round's url id -> the old answer's url, sha256, ctype, fetched_at, last_modified, content_disposition …) and
    files/<sha256>. A folder whose index cannot be read is not used at all (why says why); a copy whose file is not
    there or has another sha256 is not used for its row (read). 【Claude 的执行办法】"""

    def __init__(self, folder: Path):
        self.folder = Path(folder).expanduser().resolve()
        self.rows: dict[int, dict] = {}
        self.why = ""
        self.digest = ""
        try:
            raw = (self.folder / "index.json").read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            self.why = f"读不了它的 index.json（{type(exc).__name__}）"
            return
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, dict) or not rows:
            self.why = "它的 index.json 里没有 rows（不是 old_copies_1108.py 做的索引）"
            return
        self.rows = {int(k): v for k, v in rows.items() if str(k).isdigit() and isinstance(v, dict)}
        # what the folder holds, for 导出依据's 旧存档副本: its index and the names of its files
        h = hashlib.sha256(raw)
        try:
            h.update("\n".join(sorted(os.listdir(self.folder / "files"))).encode())
        except OSError:
            h.update(b"no files/")
        self.digest = h.hexdigest()[:16]

    def read(self, row: dict) -> tuple[bytes | None, str]:
        """A copy's bytes when its file is in files/ with the sha256 its index row says; else None and why, in words."""
        sha = str(row.get("sha256") or "")
        if not _SHA.fullmatch(sha):
            return None, "索引里没有它的 sha256"
        try:
            body = (self.folder / "files" / sha).read_bytes()
        except OSError:
            return None, "副本资料夹里没有它的档"
        if hashlib.sha256(body).hexdigest() != sha:
            return None, "档案的 sha256 和索引对不上"
        return body, ""


def _plain(url: str | None) -> str:
    """An address without its scheme, its host in lower case (http and https are one page: old_copies_1108.plain)."""
    try:
        parts = urlsplit(url or "")
        parts.hostname  # noqa: B018 - a host written wrong ([…]) raises here
    except ValueError:
        return url or ""
    return f"{(parts.hostname or '').lower()}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")


_PRESS_N = re.compile(r"/\d+$")  # a row's second button of one kind: <row key>/<n> (postback._rows)


def list_date(rows: Iterable[tuple[int, postback.Row]]) -> tuple[str, str, str] | None:
    """The date the list rows that point to one notice state (Row.date: find_date's labelled date, else bare_date's),
    each row once ((its list, its key): a row a list shows on each of its pages is one), as (YYYY-MM-DD, that row's
    date words, a note for 备注): their date when they agree (note ''); when they do not, the date most rows give, of
    those the earliest when as many give another, the note saying so. None when no row states a date."""
    seen: set[tuple[int, str]] = set()
    votes: list[tuple[str, str]] = []
    for lid, row in rows:
        if (lid, row.key) in seen:
            continue
        seen.add((lid, row.key))
        got = (find_date(row.date) or bare_date(row.date)) if row.date else None
        if got is not None:
            votes.append((got[0], row.date))
    if not votes:
        return None
    counts = Counter(day for day, _raw in votes)
    top = max(counts.values())
    tied = sorted(d for d, n in counts.items() if n == top)
    day = tied[0]
    raw = next(r for d, r in votes if d == day)
    if len(counts) == 1:
        return day, raw, ""
    said = "、".join(f"{d}（{n} 行）" for d, n in sorted(counts.items()))
    return day, raw, (f"列表上有 {len(votes)} 行指到它，写的日期不一样：{said}，"
                      + ("取多数的" if len(tied) == 1 else "几个日期一样多，取最早的"))


def list_category(rows: Iterable[tuple[int, postback.Row]]) -> tuple[str, str] | None:
    """列表上的类别 (blueprint item 22, part 2; the rule of lists.md §2.5, the crawler session's): the categories the
    list rows that point to one page state (Row.category: what the platform's category cell holds; c.nknu's lists
    name none), each row once ((its list, its key), as list_date counts them), each category once in the spelling first
    seen (two compared as postback.norm reads them), in the order seen, joined by 「 | 」, as (that, a note for 备注):
    the note '' when there is one, else it names them all and counts the rows that state one (the note's words:
    【Claude 的执行办法】). None when no row states a category. On round one's archive 6 notices of sso.nknu.edu.tw/news
    have two: their own site's lists state one, a list of news.nknu.edu.tw or of a unit site (web/619, web/49) another,
    each in its own site's words; Writer._list_dates gives the page's own site's rows first, so its category comes first
    (the replay review of 11-09; lists.md §2.5 had counted none)."""
    seen: set[tuple[int, str]] = set()
    cats: dict[str, str] = {}
    stated = 0
    for lid, row in rows:
        if (lid, row.key) in seen:
            continue
        seen.add((lid, row.key))
        key = postback.norm(row.category)
        if not key:
            continue
        stated += 1
        cats.setdefault(key, row.category)
    if not cats:
        return None
    said = list(cats.values())
    if len(said) == 1:
        return said[0], ""
    return " | ".join(said), f"列表上有 {stated} 行指到它，写的类别不一样：{'、'.join(said)}，都列出"


def _read_rows(lst: dict, page_url: str, text: str, step: int | None) -> postback.PageRead | None:
    """The rows of a list page or of a page its pager brought, read as the crawler read them for their presses
    (postback.read_page with the list's site and the list's address, which every row key is of) with the platform of
    the list's site, else of the page's own address (图资处's WebIdx lists: lis's own profile has no rows): the first
    reading that finds the list grid (PageRead.rowset; its content says whether the grid is a table of content), None
    when none does. page_url: a list page's own address, the address a pager press's answer was archived at. An error
    of the reading is the caller's to say. Writer._rows_of reads the site's own lists by it, Writer._foreign_rows other
    sites' lists that link its pages (blueprint item 22, part 2; lists.md §2.8 item 5)."""
    owner = lst.get("owner_site") or scope.site_key(lst["url"])
    tried: set[str] = set()
    for prof in (profiles.profile_for_site(owner), profiles.profile_for(page_url)):
        if prof is None or prof.name in tried:
            continue
        tried.add(prof.name)
        pr = postback.read_page(prof, page_url, text, site=owner, list_url=lst["url"],
                                page=links.PageLinks(page_url), step=step)
        if pr.rowset is not None:
            return pr
    return None


class Writer:
    def __init__(self, root: Path, out: Path, site: str, items: dict[str, Item], snap: Snap,
                 reuse: Reuse | None = None, old: OldCopies | None = None):
        self.root, self.out, self.site, self.items, self.s = root, out, site, items, snap
        self.numbers = Counter()
        self.width = {t: max(4, len(str(sum(1 for i in items.values() if i.type == t)))) for t in FOLDERS}
        self.by_source: dict[tuple, Item] = {}
        self.titles: dict[int, str] = {}
        self.reuse = reuse if reuse is not None and not reuse.why else None
        # the date each notice's row states on its list, by item key (日期来源 LIST_ROW: after the page's own date
        # field, before its words; P75 of docs/crawler-plan.md): the rows of the site's list pages and of the pages
        # their pager brought, read as each is written (_rows_of), matched once every page is (_list_dates)
        self.list_dates: dict[str, tuple[str, str]] = {}
        self.list_notes: dict[str, str] = {}  # rows that disagree on a notice's date: what 备注 says
        self.list_rows: list[tuple[int, str, tuple[postback.Row, ...]]] = []  # (list id, the page's address, rows)
        # 页别 (blueprint item 22, part 3): each list page's and pager page's reading as written (_read_rows: its list
        # grid and whether that is a table of content), by item key; None when no platform reads a list grid there
        self.reads: dict[str, postback.PageRead | None] = {}
        # other sites' list pages and pager pages read for their rows (_foreign_rows), by kind (列表页, 翻页), and
        # those that could not be read, by why: the README and the command say them
        self.foreign_got: Counter = Counter()
        self.foreign_missed: Counter = Counter()
        # what each page written states of its date: its date field's (own_date), else the first labelled date in
        # its words (None when its field has one: never needed then)
        self.found: dict[str, tuple[tuple[str, str] | None, tuple[str, str] | None]] = {}
        self.old = old if old is not None and not old.why else None
        self.by_old: dict[str, Item] = {}  # a copy's sha256 -> the row it was first written for
        self.old_skipped: Counter = Counter()  # the copies a row could have taken and did not, by why
        self.old_taken: set[int] = set()  # the url rows whose copy a row took
        self.old_unneeded: dict[int, str] = {}  # an index row looked at whose row this round needs no copy: its result
        self.old_not_needed: Counter = Counter()  # of those, the ones no row took, by this round's 结果 (in words)

    def run(self) -> None:
        for d in FOLDERS.values():
            (self.out / d).mkdir(parents=True, exist_ok=True)
        rank = {t: i for i, t in enumerate(TYPES)}
        for it in sorted(self.items.values(), key=lambda i: (rank[i.type], i.source is None, i.order)):
            if self._old_copy(it):
                continue
            if it.source is not None:
                self._write(it)
        if self.old is not None:
            self._old_unneeded_counted()
        self._dates()
        for it in self.items.values():  # a redirect shares the file of the page it lands on
            if not it.path and it.landing is not None:
                to = self.items.get(f"u{it.landing}")
                if to is not None and to.path:
                    # the row says what its file is (review 10-27 #5): the landing page's size, sha256, format and
                    # fetch, and its completeness when the views judge the redirect by no row of its own
                    it.path, it.shared = to.path, True
                    it.title = it.title or to.title
                    if not it.date:
                        # the landing page's own date field, else the date the redirect's own row states on its
                        # list (the notice as its unit lists it: c.nknu's link items), else the landing page's other
                        if to.date_from != OWN_FIELD and it.key in self.list_dates:
                            self._take_list_date(it)
                        else:
                            it.date, it.date_raw, it.date_from = to.date, to.date_raw, to.date_from
                    it.size, it.sha256, it.sniff, it.fetched_at = to.size, to.sha256, to.sniff, to.fetched_at
                    it.modified = it.modified or to.modified
                    it.origin = to.origin
                    if not it.level:
                        it.level, it.lacks = to.level, to.lacks
                    it.notes.append("跳转：内容就是落地那一页的档（大小、sha256、格式、抓取时间、最后修改时间都是落地那一页的）")
        self._read_whole()
        self._no_data_rows()
        self._unit_rows()
        for it in self.items.values():
            if not it.path and it.bucket == "got":  # never 抓到了 with nothing in the folder
                it.bucket = "review"
                it.notes.append("存档说抓到了，导出却没有可以存的内容（这种情况导出程式没处理到，要人看）")
            if it.type == PAGE and it.list_id is not None and it.kind == "pager":
                base = self.titles.get(it.list_id)
                if base:
                    it.title = f"{base}（第 {it.step} 页）"
        self._list_kinds()

    def _list_kinds(self) -> None:
        """页别 of each page row (blueprint item 22, part 3: the owner 11-09 「照这个做」 to the content session's
        proposal; the crawler session's rule on lists.md §3.1), once every file is written (reads):
        - LIST_FIRST: a page of the site the crawler calls a list (page_role list) whose reading as written finds the
          platform's list grid (reads: _read_rows) that is no table of content, that is no later page of a GET-paged
          list (postback.later_get_pages), and that is no platform's detail page (profiles.detail_of none, or neither
          its title area nor its text area on the page, NO_AREA: then it is the list it shows);
        - LIST_PAGER: a page a pager press brought (kind pager) unless its own reading, or with none its list's, is a
          table of content (a press with neither reading is a pager's still: the crawler pressed it as one, 【Claude 的
          执行办法】); and such a list page a GET-paged list numbers 2 or more;
        - '': every other row. A table of content with a pager (成教所's honor roll: its words are only there) and a
          page no platform reads a list grid on (电机系's: the crawler calls it a list by its GET pager links alone; its
          words may be only there) stay blank, as does a list a redirect stands for and one whose file another row
          wrote (neither is read: _rows_of, _write)."""
        later = postback.later_get_pages(self.s.raw_res)
        for it in self.items.values():
            if it.type != PAGE:
                continue
            if it.kind == "pager":
                pr = self.reads.get(it.key)
                if pr is None and it.list_id is not None:
                    pr = self.reads.get(f"u{it.list_id}")
                if pr is None or not pr.content:
                    it.list_kind = LIST_PAGER
            elif it.page_role == "list" and it.key[:1] == "u" and it.key[1:].isdigit():
                pr = self.reads.get(it.key)
                if pr is None or pr.content:
                    continue
                if int(it.key[1:]) in later:
                    it.list_kind = LIST_PAGER
                elif profiles.detail_of(it.url) is None or it.empty_area == NO_AREA:
                    it.list_kind = LIST_FIRST

    def _read_whole(self) -> None:
        """Item ⑧ (11-08, asked by the content session): a page the crawler could not decode (结果 编码有问题, 要人看)
        that the export read again with an encoding the page or its server names, all a reader sees read (seen_lost
        0), is the page the school sent: its 结果 抓到了, its 分类 and 完整度 and 缺什么 as the views judge such a page
        (outcome.judge_page with own ok: what it lacks still counts), and 备注 says so. So is a row whose file is
        that page (a redirect landing on it, judged by it). The dashboard still says 编码有问题: the
        crawler's own decoding is unchanged. Not a row that took the 10-24 archive's copy (its 结果 and 分类 stay
        this round's, item ⑦), nor a page a reader would see a � in."""
        whole = {i.path for i in self.items.values() if i.decoded and i.path and not i.seen_lost and not i.filled}
        note = "看板上的结果是「编码有问题」：导出时读通了，结果、分类、完整度照读通的网页算"
        for it in self.items.values():
            if it.judged is None or it.filled or it.path not in whole:
                continue
            judged = outcome.judge_page(it.judged, own="ok")
            it.result, it.bucket = "ok", monitor.bucket("ok", page=True)
            it.level, it.lacks = judged["level"], Builder._lacks({**it.judged, **judged})
            it.notes.append(note)
        # a redirect the views judge by no row of its own (its landing page is judged in its own right): its category
        # and level are its landing page's (Builder; run: 跳转), judged again with it. Its 结果 stays its own: 跳转
        for it in self.items.values():
            to = self.items.get(f"u{it.landing}") if it.landing is not None else None
            if (it.judged is None and it.own == "decode" and not it.filled and to is not None and to.judged is not None
                    and to.result == "ok" and it.path == to.path):
                it.bucket, it.level, it.lacks = to.bucket, to.level, to.lacks
                it.notes.append(note.replace("看板上的结果", "看板上落地那一页的结果"))

    @staticmethod
    def _no_data(it: Item, rule: profiles.NoData) -> None:
        """A row whose file is a page the school answered with no data for what was asked (profiles.school_no_data;
        the owner 11-09 「照这样做」 to the crawler session's question, the content session's words): the rule kept,
        its words added to 缺什么 after what the row lacks besides as the empty-area words are (joined by 、, never
        twice), 备注 saying once that the program marked it; its 结果, 分类 and 完整度 as the archive judged them."""
        it.no_data = rule
        if rule.words not in it.lacks:
            it.lacks = "、".join(x for x in (it.lacks, rule.words) if x)
        if NO_DATA_NOTE not in it.notes:
            it.notes.append(NO_DATA_NOTE)

    def _no_data_rows(self) -> None:
        """Every row whose file is a page _page marked as the school's answer of no data says so too, once, after
        every file is written and every 缺什么 judged (the mark is what the file says, as the dates a page states are:
        lists.md §4; 【Claude 的执行办法】): a redirect that took that page's file (run: its 缺什么 the landing page's,
        words and all, when the views judge it by no row of its own, as the empty-area words go; else its own), a row
        that shares the file (_write, _old_copy: such a row takes none of the first row's 缺什么, nor the empty-area
        words), and a page whose 缺什么 _read_whole wrote anew."""
        by_file = {it.path: it.no_data for it in self.items.values() if it.no_data is not None and it.path}
        for it in self.items.values():
            if it.path in by_file:
                self._no_data(it, by_file[it.path])

    def _unit_rows(self) -> None:
        """Every row whose file is a notice page that states its posting unit states it too (公告单位 is what the file
        says, as the school's answer of no data is: _no_data_rows): a redirect to a notice that is a row of the same
        export, and a row that shares another row's file (a second address sent to one notice). A redirect that
        reads its landing page itself (the first address of another site sent to the platform's notice: 85 rows of
        11 sites' exports in round one land on such a notice) has the unit from _page already 【Claude 的执行办法】."""
        by_file = {it.path: it.unit for it in self.items.values() if it.unit and it.path}
        for it in self.items.values():
            if not it.unit and it.path in by_file:
                it.unit = by_file[it.path]

    def _put(self, it: Item, name: str, data: bytes) -> None:
        """A row's file, numbered in turn in its type's folder."""
        folder = FOLDERS[it.type]
        self.numbers[folder] += 1
        fname = f"{self.numbers[folder]:0{self.width[it.type]}d}-{safe_name(name)}"
        (self.out / folder / fname).write_bytes(data)
        it.path = f"{folder}/{fname}"

    def _write(self, it: Item) -> None:
        warc, offset, sha, ctype = it.source
        first = self.by_source.get((warc, offset))
        if first is not None:
            it.path, it.shared = first.path, True
            it.modified = it.modified or first.modified
            if it.type == PAGE and it.page_role != "list" and it.kind != "pager":
                # what the page states of its date is the same page's (blueprint item 22, the crawler session's
                # decision 5 on lists.md §4: such rows had no date at all, 105 of them on 18 sites, as uk's link items
                # to one page; _old_copy does the same), and its own list rows still come before its words (_dates).
                # A file a list's row wrote has nothing kept of it (a list states no date of its own): the row has its
                # own list rows' date alone, never that page's words (the rules review of 11-09). Never a list's row
                # 【Claude 的执行办法】
                self.found[it.key] = self.found.get(first.key, (None, None))
            it.notes.append("和清单里另一行是同一个档")
            return
        if self._link(it):
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
        it.modified = it.modified or changed_at(headers.get("last-modified"))  # a Drive file's own modified first
        ctype = headers.get("content-type") or ctype
        if it.type == PAGE:
            data, name = self._page(it, url, body, ctype)
        elif it.type == DATA and it.title:
            name, data = f"{it.title}.{SNIFF_EXT.get(it.sniff, 'json')}", body
        else:
            name, data = file_name(it, url, headers, ctype), body
            if it.type in (FILE, IMAGE):
                it.title = name
        self._put(it, name, data)
        self.by_source[(warc, offset)] = it

    def _link(self, it: Item) -> bool:
        """An attachment or a picture the previous export holds (--reuse: Reuse.find by the sha256 the archive
        recorded): its record's headers read for its name and Last-Modified, its body not, and that file hard-linked
        to its place here under the name it would be written with. False, and nothing here changed, when there is no
        such file or anything fails: the caller reads it from the archive as usual."""
        warc, offset, sha, ctype = it.source
        if self.reuse is None or it.type not in (FILE, IMAGE) or not sha:
            return False
        src = self.reuse.find(sha, it.size)
        if src is None:
            return False
        try:
            url, _status, headers, _none = postback.read_response(self.root, warc, offset, payload=False)
            name = file_name(it, url, headers, headers.get("content-type") or ctype)
            folder = FOLDERS[it.type]
            fname = f"{self.numbers[folder] + 1:0{self.width[it.type]}d}-{safe_name(name)}"
            size = src.stat().st_size
            os.link(src, self.out / folder / fname)
        except (OSError, ValueError):
            return False
        self.numbers[folder] += 1
        it.path, it.title, it.linked = f"{folder}/{fname}", name, True
        it.sha256, it.size = sha, size
        it.modified = it.modified or changed_at(headers.get("last-modified"))
        self.by_source[(warc, offset)] = it
        return True

    # -- item ⑦: the 10-24 archive's copy of a row this round got nothing good of (--old-copies)
    def _old_row(self, it: Item) -> tuple[dict, dict] | None:
        """The copy a row takes (OldCopies' index row) and the url row it is the copy of: its own url row's, else the
        row it lands on's (a redirect, a link through one), when the index holds that row and its result this round
        says it came to nothing good (OLD_NO_FILE with no file of this round for it, OLD_ALWAYS); None otherwise."""
        rids = [int(it.key[1:])] if it.key[:1] == "u" and it.key[1:].isdigit() else []
        if it.landing is not None:
            rids.append(it.landing)
        for rid in rids:
            old, row = self.old.rows.get(rid), self.s.rows.get(rid)
            if old is None or row is None:
                continue
            if row["result"] in OLD_ALWAYS or (row["result"] in OLD_NO_FILE and it.source is None):
                return old, row
            self.old_unneeded[rid] = row["result"]  # counted once every row is written (_old_unneeded_counted)
        return None

    def _old_unneeded_counted(self) -> None:
        """The index rows looked at whose row's result this round is none the export takes a copy for (it came to
        something else since the folder was made: old_copies_1108.TAKE is what the export takes), and that no other
        row took, counted by this round's 结果 (old_not_needed, said in the README and by the command): never left out
        (review 11-08). A row of them with no file says so in 备注; one with a file (got since) says nothing."""
        for rid, result in self.old_unneeded.items():
            if rid in self.old_taken:
                continue
            words = outcome.RESULT_LABEL.get(result, result)
            self.old_not_needed[words] += 1
            it = self.items.get(f"u{rid}")
            if it is not None and not it.path:
                it.notes.append(f"10-24 旧存档有它的副本，没用上：这一轮的结果是「{words}」，不在要补的范围")

    def _old_copy(self, it: Item) -> bool:
        """Item ⑦ (--old-copies): a row that came to nothing good this round, with a good copy in the 10-24 archive
        (OldCopies, _old_row), takes that copy as its file, written as this round's are: a page decoded as the export
        decodes pages (_page: redecode when the crawler's charset does not read it) and saved as UTF-8 with a BOM, a
        file or a picture byte for byte named as file_name() names it (the old answer's Content-Disposition, else the
        old address's last part). Its 来源 says ORIGIN_OLD, its 抓取时间, 最后修改时间, 大小, sha256 and 格式 are the
        copy's, and its 结果, 分类 and 原因 stay this round's, so that the gap stays in sight; 备注 says what this
        round got. A copy whose index row names another address than its url row's (an index made of another
        archive), whose file is not there or has another sha256, or a web page where a file is expected, is not taken:
        counted (old_skipped, said in the README) and said in the row's 备注, and the row is written as this round's
        (False). A copy two rows take (http and https of one address) is written once."""
        if self.old is None or it.type not in (PAGE, FILE, IMAGE):
            return False
        hit = self._old_row(it)
        if hit is None:
            return False
        old, row = hit
        if _plain(old.get("url")) != _plain(row["url"]):  # an index of another archive: its ids name other rows
            self.old_skipped["索引里的网址和存档里这一行的不一样"] += 1
            it.notes.append("10-24 旧存档有它的副本，没用上：索引里的网址和存档里这一行的不一样")
            return False
        words = it.result_words()
        sha = str(old.get("sha256") or "")
        first = self.by_old.get(sha)
        if first is not None and it.type in (FILE, IMAGE) and first.sniff == "html":
            self.old_skipped["该是档案，副本却是网页"] += 1
            it.notes.append("10-24 旧存档有它的副本，没用上：该是档案，副本却是网页")
            return False
        # the copy is of the address the row names (its own url row's), or of the page this round's redirect lands
        # on: 最后落地网址 says where the file is the answer of, which the content stage reads it as (its platform,
        # its links), so a redirect that took its own address's copy lands nowhere (review 11-08)
        copy_url = str(old.get("url") or "") or row["url"]
        landed, own_copy = it.final_url, it.key == f"u{row['id']}"
        if first is not None:
            it.type, it.path, it.shared, it.sniff, it.title = first.type, first.path, True, first.sniff, first.title
            if first.key in self.found:
                self.found[it.key] = self.found[first.key]
            size = first.size
        else:
            body, why = self.old.read(old)
            fmt = sniff.sniff(body, str(old.get("ctype") or "")) if body is not None else ""
            if body is not None and it.type in (FILE, IMAGE) and fmt == "html":
                body, why = None, "该是档案，副本却是网页"
            if body is None:
                self.old_skipped[why] += 1
                it.notes.append(f"10-24 旧存档有它的副本，没用上：{why}")
                return False
            url, ctype = copy_url, str(old.get("ctype") or "")
            if it.type == PAGE and fmt != "html":
                it.type = IMAGE if fmt in _IMAGE_SNIFFS else FILE
                it.notes.append(f"10-24 的副本不是网页（{fmt}），存成{it.type}")
            it.sniff = fmt
            if own_copy:
                it.landing = None  # its file is its own address's answer (a list's rows are read from it: _rows_of)
            if it.type == PAGE:
                data, name = self._page(it, url, body, ctype)
            else:
                cd = old.get("content_disposition")
                name = file_name(replace(it, name=""), url, {"content-disposition": cd} if isinstance(cd, str) and cd
                                 else {}, ctype)
                data, it.title = body, name
            self._put(it, name, data)
            self.by_old[sha] = it
            size = len(body)
        it.origin, it.filled = ORIGIN_OLD, True
        it.fetched_at, it.modified = _taipei(old.get("fetched_at")), changed_at(old.get("last_modified"))
        it.sha256, it.size = sha, size
        it.final_url = "" if copy_url == it.url else copy_url
        if own_copy:
            it.landing = None
        self.old_taken.add(row["id"])
        it.notes.append(f"这一轮：{words}；用 10-24 旧存档的副本补上")
        if landed and landed != it.final_url:
            it.notes.append(f"这一轮跳到 {landed}；副本是{'这个网址自己' if own_copy else '跳到的那一页'}的旧内容")
        if first is not None:
            it.notes.append("和清单里另一行是同一个档")
        return True

    # -- the pages' dates: the platform's field, the notice's list row, the page's words
    def _rows_of(self, it: Item, url: str, text: str) -> None:
        """The rows of a list page or of a page its pager brought (list_rows), read as the crawler read them
        (_read_rows), and the reading kept for 页别 (reads). A list a redirect stands for is not read: its file is the
        page it lands on."""
        lst = self.s.rows.get(it.list_id) if it.list_id is not None else None
        if lst is None or it.landing is not None:
            return
        page_url = url if it.kind == "pager" else lst["url"]
        try:
            pr = _read_rows(lst, page_url, text, it.step)
        except Exception as exc:  # noqa: BLE001 - its rows go unread, said in 备注; never an export stopped by it
            it.notes.append(f"读这一页列表上的行出错（{type(exc).__name__}），列表上写的日期、类别没用上")
            self.reads[it.key] = None
            return
        self.reads[it.key] = pr
        if pr is not None:
            self.list_rows.append((it.list_id, page_url, pr.rows))

    def _foreign_rows(self) -> None:
        """The rows of other sites' lists that link this site's pages (blueprint item 22, part 2, across sites; the
        crawler session's decision 2 on lists.md §2.3): each of their list pages the site's own export of them would
        read (a page that came back with its body, page_role list) and each page their pager brought that it would
        write (best_presses: done, its answer a page in the WARC), read with the same platform reading as this site's
        own lists (_read_rows), their rows added to list_rows. Nothing of them is written or listed as a row of this
        site; a page that cannot be read is counted (foreign_missed, said in the README) and passed over, never an
        export stopped by it: one whose record cannot be read or is not the one recorded, one no list grid is read on,
        and one that came to no page this round although its own site's export lists it (a list page, a pager press
        brought nothing: failed, still queued; the contract review of 11-09 found these passed over uncounted). A
        press a newer one stands for, or one answered with a redirect, is no page of its list's there either
        (Builder._presses) and is not counted. Which of their pages are read (those the other site's own export reads),
        and the counting: 【Claude 的执行办法】"""
        s = self.s
        for lid in sorted(s.foreign_lists):
            lst = s.foreign_lists[lid]
            if lst.get("page_role") != "list" or lst.get("kind") != "page" or _expected_file(lst):
                continue  # no list page in its own site's export either: no rows are read on it there
            if lst["result"] in BODY_RESULTS and _has_body(lst):
                self._foreign_page(lst, lst, None, "列表页")
            else:
                self.foreign_missed[_unread_words(lst["result"])] += 1
        pressed = []
        for a in best_presses(s.foreign_presses.values()):
            if a["kind"] != "pager" or a["state"] == "superseded" or a["result"] == "redirect":
                continue
            if (a["result"] in BODY_RESULTS and a["status"] == 200 and a["warc"] and a["warc_offset"] is not None
                    and a["sniff"] == "html"):
                pressed.append(a)
            else:
                self.foreign_missed[_unread_words(a["result"])] += 1
        for a in sorted(pressed, key=lambda a: (a["list_id"], a["step"], a["id"])):
            lst = s.foreign_lists.get(a["list_id"])
            if lst is not None:
                self._foreign_page(lst, a, a["step"], "翻页")

    def _foreign_page(self, lst: dict, rec: dict, step: int | None, what: str) -> None:
        """One page of another site's list (rec: its url_result row, or its pager press's action_result row), read
        from the WARC as the export reads the pages it writes (_write, _page: the sha256 the archive records, the
        crawler's decoding, else redecode), its rows read by _read_rows: a list page at the list's address, a pager's
        answer at the address it was archived at (as _rows_of reads the site's own)."""
        try:
            url, _status, headers, body = postback.read_response(self.root, rec["warc"], rec["warc_offset"])
        except (OSError, ValueError):
            self.foreign_missed["存档里读不回来"] += 1
            return
        if rec.get("sha256") and hashlib.sha256(body).hexdigest() != rec["sha256"]:
            self.foreign_missed["存档内容和纪录的 sha256 对不上"] += 1
            return
        ctype = headers.get("content-type") or rec.get("ctype")
        try:
            text = decode_body(body, ctype)
        except DecodeError:
            text = redecode(body, ctype)[0]
        page_url = url if step is not None else lst["url"]
        try:
            pr = _read_rows(lst, page_url, text, step)
        except Exception:  # noqa: BLE001 - counted, said in the README; never an export stopped by it
            self.foreign_missed["读列表上的行出错"] += 1
            return
        if pr is None:
            self.foreign_missed["读不出列表格子"] += 1
            return
        self.foreign_got[what] += 1
        self.list_rows.append((lst["id"], page_url, pr.rows))

    def _list_dates(self) -> None:
        """list_dates: the date the list rows state for each page written (list_date), and 列表上的类别 of each page
        (list_category: Item.list_category, a note when the rows disagree), the rows that point to it being: for a
        notice a row's press brought, its row (its list, Row.key = the press's row_key, as the crawler keyed it); for a
        page got by GET (any page, blueprint item 22, part 2: not only one its platform calls a detail, as c.nknu's
        activity and forum pages are not), the row its page_res item entry names (its list and key) and every row whose
        plain link, joined to the address of the page it is on as the crawler joins it (scope.canonical), is the page's
        网址. The item entry is the crawler's own record of which page a row links (Chains._plan: the link as the crawler
        read it), the plain link the export's own reading of it: on round one's archive both name the same rows (the
        rules review of 11-09), and either alone matches. The rows are those of the site's own lists and of other sites'
        lists that link its pages (_foreign_rows, their item entries Snap.foreign_items), the site's own first (the
        page's own site's category comes first in 列表上的类别), each in one order whatever order the archive hands them in
        (the item entries by their lists' url row ids: page_res is read with no order of its own, the contract review of
        11-09). A page that is no row of a list has none; a list's own page and a page its pager brought have none (each
        of their rows is another page's). The category is set on a row with no file too (it is the list row's, whatever
        became of the page), its date only on a page written (_dates) 【Claude 的执行办法】.
        Since 11-16 (the owner: 「请导出给按钮带来的档案行填同样的栏：网页上写的日期、日期原文、日期来源＝列表上的日期，
        以及 列表上的类别，栏名和写法跟网页的完全一样。」): a file a row's download button brought takes its row's date and
        category too, by the press's row key as a pressed notice does (its date only when the file was written). A
        file a page links plainly has none of these: the content stage dates it by the page that links it."""
        self._foreign_rows()
        by_key: dict[tuple[int, str], list[tuple[int, postback.Row]]] = defaultdict(list)
        by_href: dict[str, list[tuple[int, postback.Row]]] = defaultdict(list)
        for lid, base, rows in self.list_rows:
            for row in rows:
                by_key[(lid, row.key)].append((lid, row))
                if row.href and (canon := scope.canonical(row.href, base)) is not None:
                    by_href[canon].append((lid, row))
        if not by_key:
            return
        entries: dict[int, list[tuple[int, str]]] = defaultdict(list)
        own = sorted((r["page_id"], r["res_key"], r["res_id"]) for r in self.s.raw_res
                     if r["role"] == "item" and r["res_id"] is not None and (r["res_key"] or "").startswith("item:"))
        for page_id, res_key, res_id in own:
            entries[res_id].append((page_id, res_key[len("item:"):]))
        for r in self.s.foreign_items:  # item entries every one of them, naming a row of this site, in that order
            entries[r["res_id"]].append((r["page_id"], r["res_key"][len("item:"):]))
        for it in self.items.values():
            if it.type == FILE and it.kind == "download":
                # the file a row's download button brought: its row's date and category, as a notice a row's press
                # brought (the owner 11-16; an SSO unit site's file tables: 966 rows of 36 sites on 11-15, whose
                # server states no Last-Modified, so that they had no date at all)
                rows = (by_key.get((it.list_id, it.row_key))
                        or by_key.get((it.list_id, _PRESS_N.sub("", it.row_key)), []))
            elif it.type != PAGE or it.page_role == "list" or it.kind == "pager":
                continue
            elif it.kind == "detail":
                rows = (by_key.get((it.list_id, it.row_key))
                        or by_key.get((it.list_id, _PRESS_N.sub("", it.row_key)), []))
            elif it.key[:1] == "u" and it.key[1:].isdigit():
                rows = [x for lid, k in entries.get(int(it.key[1:]), ()) for x in by_key.get((lid, k), ())]
                rows += by_href.get(it.url, [])
                rows.sort(key=lambda x: x[0] in self.s.foreign_lists)  # stable: the site's own lists' rows first
            else:
                continue
            got = list_date(rows)
            if got is not None:
                day, raw, note = got
                self.list_dates[it.key] = (day, raw)
                if note:
                    self.list_notes[it.key] = note
            cat = list_category(rows)
            if cat is not None:
                it.list_category, note = cat
                if note:
                    it.notes.append(note)

    def _take_list_date(self, it: Item) -> None:
        (it.date, it.date_raw), it.date_from = self.list_dates[it.key], LIST_ROW
        if it.key in self.list_notes:
            it.notes.append(self.list_notes[it.key])

    def _dates(self) -> None:
        """网页上写的日期 of each page written (日期来源 says which): its platform's date field, else the date its row
        states on its list (P75: SSO WebIdx's notices a row's press brought have their list's address, which no
        detail path names; blueprint item 22: any page a list row links, of this site's lists or another's), else the
        first labelled date in its words (its content area, then all of it)."""
        self._list_dates()
        for key, (own, words) in self.found.items():
            it = self.items[key]
            if own is not None:
                (it.date, it.date_raw), it.date_from = own, OWN_FIELD
            elif key in self.list_dates:
                self._take_list_date(it)
            elif words is not None:
                (it.date, it.date_raw), it.date_from = words, PAGE_WORDS
        for key in self.list_dates:  # a file a row's button brought (no page is read of it: _list_dates), once written
            it = self.items[key]
            if it.type == FILE and it.path:
                self._take_list_date(it)

    def _page(self, it: Item, url: str, body: bytes, ctype: str | None) -> tuple[bytes, str]:
        try:
            text = decode_body(body, ctype)
        except DecodeError:
            # the charset the crawler read it with does not read it (结果 编码有问题; item ⑧ of the 11-08 gaps: 英语系
            # 106 pages, 性别所 10): read again with an encoding the page or its server names (redecode)
            text, it.decoded, said, it.lost = redecode(body, ctype)
        tree = LexborHTMLParser(text)
        if it.decoded:
            # what a reader sees (its words and title; not its comments, nor its links' addresses) says whether it
            # reads whole: 英语系's 106 pages lose 3 characters, all in a Big5 comment in <head> (11-08)
            it.seen_lost = (tree.text() or "").count("\ufffd") if it.lost else 0
            how = encoding_words(it.decoded, said)
            if not it.lost:
                it.notes.append(f"原本的编码读不通，按{how}重读")
            elif not it.seen_lost:
                it.notes.append(f"原本的编码读不通，按{how}重读：看得到的文字都读通了，只有网页的注释、链接网址这类看不到的"
                                f"地方有 {it.lost} 个字读不出来，用 � 代替（原样在存档里）")
            else:
                seen = f"（{it.seen_lost} 个在看得到的文字里）" if it.seen_lost != it.lost else ""
                it.notes.append(f"原本的编码读不通，没有一种编码整页读得通：按{how}读，有 {it.lost} 个字读不出来{seen}，"
                                "用 � 代替（原样在存档里）")
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
        if it.kind != "pager":
            it.unit = own_unit(tree, url, self.site)  # 公告单位: the platform's own field, by the page's own address
        # the school's answer of no data for what was asked (profiles.NoData; the owner 11-09 「照这样做」): by the
        # page's own address, in its HTML as read, whatever row brought it (a GET, a menu's press, a pager's answer);
        # after the empty-area words, so that its words come last (the review of 11-09)
        no_data = profiles.school_no_data(url, text)
        if no_data is not None:
            self._no_data(it, no_data)
        if it.page_role == "list" or it.kind == "pager":
            self._rows_of(it, url, text)  # a list's rows each state their notice's date
        else:
            # what the page states of its date: its platform's own date field (c.nknu's newsdetail: 公告日期), else
            # the first labelled date in its content area, then in all of it; which one is taken, the notice's list
            # row between them, once every list page is read (_dates)
            own = own_date(tree, url, self.site)
            words = None if own is not None else next(
                (got for got in map(find_date, page_texts(tree, self.site, url)) if got), None)
            self.found[it.key] = (own, words)
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


# ---------------------------------------------------------------- what an export is made of (导出依据)


@lru_cache(maxsize=1)
def program_digest() -> str:
    """程式 of 导出依据: the sha256 (its first 16 hex digits) of the export's program, every module of the archive
    package (the views that judge the rows, the platform profiles, the export itself …) and the crawler's decoder
    (nknu.crawl.fetch); '' when one cannot be read."""
    try:
        files = [*sorted(Path(__file__).parent.glob("*.py")), Path(sys.modules[decode_body.__module__].__file__)]
        h = hashlib.sha256()
        for f in files:
            h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    except (OSError, KeyError, TypeError):
        return ""
    return h.hexdigest()[:16]


def _canon(x):
    """A value as plain JSON data, the same each time it is read: dataclasses as dicts, keys as text, sets sorted."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        x = dataclasses.asdict(x)
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_canon(v) for v in x]
    if isinstance(x, set | frozenset):
        return sorted(str(v) for v in x)
    if isinstance(x, bytes):
        return x.hex()
    return x if x is None or isinstance(x, str | int | float | bool) else repr(x)


def snapshot_digest(snap: Snap) -> str:
    """存档内容 of 导出依据: the sha256 (its first 16 hex digits) of everything the export read of the site (Snap): its
    rows as the views judge them, its pages' resources and lists, its presses and walks, its Drive items, where its
    redirects land, its state as the dashboard says it and whether it can be exported, and the other sites' lists that
    link its pages (their item entries, list pages and pager presses: blueprint item 22, the crawler session's
    decision 2: the site is exported again when one of them changes). What changes an export with no fetch changes it
    too: a person's mark (nknu archive review), a rejudge, a hand edit of the archive (11-08's album pictures), another
    judgement by a newer program (review 11-08)."""
    st = snap.status
    parts = {"plan": snap.plan, "rows": snap.rows, "pc": snap.pc, "actions": snap.actions, "walks": snap.walks,
             "drive": snap.drive, "drive_of_content": snap.drive_of_content, "landing": snap.landing,
             "orphans": snap.orphans, "press_to": snap.press_to, "videos": snap.videos,
             "other_site_items": snap.other_site_items, "page_ids": snap.page_ids,
             "status": [st.state, st.why, st.warnings] if st is not None else snap.status_error,
             "ready": snap.ready.as_dict() if snap.ready is not None else snap.ready_error,
             "foreign_lists": snap.foreign_lists, "foreign_presses": snap.foreign_presses}
    h = hashlib.sha256()
    for name in sorted(parts):
        h.update(f"{name}\0{json.dumps(_canon(parts[name]), sort_keys=True, ensure_ascii=False)}\0".encode())
    # in no order of their own
    for name, rows in (("pres", snap.pres), ("raw_res", snap.raw_res), ("foreign_items", snap.foreign_items)):
        h.update(f"{name}\0".encode())
        for line in sorted(json.dumps(_canon(r), sort_keys=True, ensure_ascii=False) for r in rows):
            h.update(line.encode() + b"\n")
    return h.hexdigest()[:16]


def export_basis(snap: Snap, copies: OldCopies | None) -> dict[str, str]:
    """导出依据 (manifest.json): what an export of the site is made of, for the export service to tell whether exporting
    it again would change anything (--skip-same; review 11-08: comparing 资料截至 alone missed a person's mark, a
    rejudge, --old-copies added later and a newer program): 资料截至 (as `nknu archive ready` says it), 存档内容
    (snapshot_digest), 程式 (program_digest) and 旧存档副本 (OldCopies.digest of the folder used, '' with none).
    【Claude 的执行办法】"""
    return {"资料截至": snap.ready.as_of if snap.ready is not None else "", "存档内容": snapshot_digest(snap),
            "程式": program_digest(), "旧存档副本": copies.digest if copies is not None and not copies.why else ""}


def same_basis(folder: Path, site: str, basis: dict[str, str]) -> bool:
    """Whether the export in folder is one of this site made of the same things (导出依据), so that exporting it again
    would change nothing. One whose manifest.json cannot be read or has no 导出依据 (made before 11-08's review), and a
    basis whose 存档内容 or 程式 is unknown, never are."""
    try:
        prev = json.loads((Path(folder).expanduser() / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(prev, dict) and prev.get("网站") == site and bool(basis.get("存档内容"))
            and bool(basis.get("程式")) and prev.get("导出依据") == basis)


# ---------------------------------------------------------------- the export


def export_site(root: Path, site: str, out: Path, *, now: float | None = None, reuse: Path | None = None,
                old_copies: Path | None = None, skip_same: Path | None = None) -> Result:
    """Export one site of the archive at root into the new folder out (see the module's docstring). reuse: a previous
    export of the same site, whose attachments and pictures are hard-linked when they are the same (Reuse).
    old_copies: the folder of the 10-24 archive's good copies of round one's gaps (OldCopies, item ⑦), which the rows
    this round got nothing good of take as their files. skip_same: the site's current export; when it is made of the
    same things (same_basis), nothing is written and Unchanged is raised (the export service: export_sites.sh)."""
    root = Path(root).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    now = time.time() if now is None else now
    check_out(root, out, [(what, Path(folder).expanduser().resolve()) for what, folder in
                          (("10-24 旧存档的副本资料夹", old_copies), ("上一次的导出", reuse), ("现用的那份导出", skip_same))
                          if folder is not None])
    from nknu.archive.crawler import lock_holder

    pid = lock_holder(root)
    conn = connect(root / "frontier.db")
    try:
        snap = read_snapshot(conn, site, now)
    finally:
        conn.close()
    copies = OldCopies(old_copies) if old_copies is not None else None
    basis = export_basis(snap, copies)
    if skip_same is not None and same_basis(skip_same, site, basis):
        raise Unchanged(f"{Path(skip_same).expanduser().resolve()} 是同样的东西导出的（资料截至 "
                        f"{basis['资料截至'] or '（还没抓到东西）'}；存档内容、程式、旧存档副本也都一样），再导一次不会变，没有导出")
    builder = Builder(snap, site)
    items = builder.build()
    old = Reuse(reuse, site) if reuse is not None else None
    out.mkdir(parents=True, exist_ok=True)
    try:
        writer = Writer(root, out, site, items, snap, old, copies)
        writer.run()
        result = _finish(root, out, site, items, snap, builder, pid, now, old, copies, writer.old_skipped, basis,
                         writer.old_not_needed, (writer.foreign_got, writer.foreign_missed))
    except BaseException as exc:
        (out / "导出没有完成.txt").write_text(f"导出到一半出错了，这个资料夹不完整，请删掉重导：{type(exc).__name__}: {exc}\n",
                                           encoding="utf-8")
        raise
    return result


def _finish(root: Path, out: Path, site: str, items: dict[str, Item], snap: Snap, builder: Builder, pid,
            now: float, old: Reuse | None = None, copies: OldCopies | None = None,
            skipped: Counter | None = None, basis: dict[str, str] | None = None,
            not_needed: Counter | None = None, foreign: tuple[Counter, Counter] | None = None) -> Result:
    rank = {t: i for i, t in enumerate(TYPES)}
    ordered = sorted(items.values(), key=lambda i: (rank[i.type], not i.path, i.path or "~", i.order))
    urls = {uid: r["url"] for uid, r in snap.rows.items()}

    def page_url(pid_: int) -> str:
        return urls.get(pid_, f"u{pid_}")

    name = (snap.plan or {}).get("name") or site
    # a row another site owns that the archive does not hold yet waits for that site's turn (frontier: a run given
    # sites takes only the rows they own): said in 备注, never fetched here. The 教务处 export of 10-27 had 55 such
    # bare 还在排队 rows: the 43 files and pictures on news.nknu.edu.tw, SSO and Drive (技术总览 §7), 12 short links
    # (plan 11-02 T1)
    for it in ordered:
        if it.bucket == "queued" and it.owner and it.owner != site and not it.path:
            it.notes.append(f"归属别的网站（{it.owner}），存档里还没有它：只爬{name}时不抓，跟着那个网站的进度抓")
    rows = [it.row(n, page_url, cap=CAP) for n, it in enumerate(ordered, start=1)]
    missing = sorted(((it, r) for it, r in zip(ordered, rows, strict=True) if not it.path),
                     key=lambda p: (_BUCKET_RANK.get(p[0].bucket, -1), rank[p[0].type], p[0].url))
    missing_items = [it for it, _r in missing]
    _csv(out / "manifest.csv", rows)
    _csv(out / "missing.csv", [r for _it, r in missing])
    st = snap.status
    state = st.state if st is not None else "unchecked"
    sign, words, _color = progress.STATE_SHOW.get(state, ("⚠", "没检查", "yellow"))
    state_words = f"{sign} {words}".strip()
    counts = {t: {"got": sum(1 for i in ordered if i.type == t and i.path),
                  "to_do": sum(1 for i in ordered if i.type == t and i.path and i.bucket not in SETTLED),
                  "missing": sum(1 for i in ordered if i.type == t and not i.path)} for t in TYPES}
    missing_by = Counter(progress.BUCKET_ZH.get(it.bucket, it.bucket) for it in missing_items)
    left = Left.of(ordered, site)
    ready = snap.ready
    linked = sum(1 for it in ordered if it.linked)
    meta = {"网站": site, "名称": name, "导出时间": _taipei(now), "存档": str(root),
            "导出时的状态": state_words, "爬虫正在跑": pid is not None, "还在排队的网址": left.urls,
            "还在排队的按钮": left.presses, "其中归属别的网站": left.other,
            # `nknu archive ready`'s, at the same moment: the export service compares 资料截至 with the next one's
            "可以导出": ready.can if ready is not None else False, "资料截至": ready.as_of if ready is not None else "",
            # what it is made of (export_basis): the export service exports the site again only when it changes
            "导出依据": basis if basis is not None else export_basis(snap, copies),
            "栏位": list(COLUMNS), "项目": [it.row(n, page_url) for n, it in enumerate(ordered, start=1)]}
    (out / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    readme = _readme(root, site, name, now, snap, state_words, pid, ordered, counts, missing_items, builder, items,
                     left, old, copies, skipped, not_needed, foreign)
    (out / "README.md").write_text(readme, encoding="utf-8")
    running = f"；爬虫正在跑（pid {pid}），清单里还有 {left.words()}" if pid is not None else ""
    by_bucket = Counter(it.bucket for it in missing_items)
    lines = [f"导出 {name}（{site}）到 {out}",
             f"导出时这个网站的状态：{state_words}{running}",
             "；".join(f"{t}：抓到 {c['got']}" + (f"（其中 {c['to_do']} 个有档但还要处理）" if c["to_do"] else "")
                      + f"、没抓到 {c['missing']}" for t, c in counts.items() if c["got"] or c["missing"]),
             "没抓到的按原因：" + "、".join(f"{progress.BUCKET_ZH.get(b, b)} {n}" for b, n in sorted(
                 by_bucket.items(), key=lambda kv: _BUCKET_RANK.get(kv[0], -1))) if by_bucket else "没有没抓到的"]
    if ready is not None:
        lines.append(f"资料截至 {ready.as_of or '（还没抓到东西）'}；"
                     + ("可以导出（这个网站没有还在排队的网址和按钮）" if ready.can else
                        f"还没爬完：还有 {ready.urls} 个网址、{ready.presses} 个按钮在排队，导出的是这一刻已经抓到的"))
    if old is not None:
        lines.append(f"从上一次导出 {old.folder} 硬链接了 {linked} 个附件和图片" if not old.why else
                     f"上一次导出 {old.folder} 没有用上：{old.why}，附件和图片都从存档读")
    filled = [it for it in ordered if it.filled]
    lines += _old_lines(copies, filled, skipped or Counter(), not_needed or Counter())
    if (said := _foreign_words(foreign)):
        lines.append(said)
    no_data = [it for it in ordered if it.no_data is not None and it.path]
    if no_data:  # the export service's log keeps these lines (the review of 11-09)
        lines.append(f"学校回答没有资料的 {len(no_data)} 行：「缺什么」栏写着{_no_data_words(no_data)}，"
                     "README「学校回答没有资料的网页」一节逐行列出")
    lines.append(f"先看 {out / 'README.md'}；清单 manifest.csv，没抓到的 missing.csv")
    return Result(out, site, name, state, state_words, pid is not None, left.urls, left.presses, counts,
                  missing_by, lines, left.other, ready, linked, len(filled))


def _old_lines(copies: OldCopies | None, filled: list[Item], skipped: Counter, not_needed: Counter) -> list[str]:
    """What the command says of --old-copies (item ⑦): the rows filled from the 10-24 archive by category, the copies
    not used and why, the ones this round needs no more, or why the folder was not used at all."""
    if copies is None:
        return []
    if copies.why:
        return [f"10-24 旧存档的副本资料夹 {copies.folder} 没有用上：{copies.why}；缺口照这一轮的"]
    out = [f"用 10-24 旧存档的副本补上 {len(filled)} 行" + (f"（{_by_category(filled)}）" if filled else "")
           + f"，副本资料夹 {copies.folder}"]
    if skipped:
        out.append(f"10-24 旧存档有副本却没用上的 {sum(skipped.values())} 行（照这一轮的导出）："
                   + "、".join(f"{why} {n}" for why, n in skipped.most_common()))
    if not_needed:
        out.append(f"10-24 旧存档也有副本、这一轮的结果却不在要补的范围的 {sum(not_needed.values())} 行，照这一轮的（这一轮的结果："
                   + "、".join(f"{words} {n}" for words, n in not_needed.most_common()) + "）")
    return out


def _unread_words(result: str | None) -> str:
    """Why a page of another site's list was not read for its rows (Writer._foreign_rows), for the README: what it came
    to this round as the dashboard says it (outcome.RESULT_LABEL; the other site's own export says the same in 结果),
    or, when it came back with a page, that the archive holds no page of it to read (a status other than 200, an answer
    that is no web page) 【Claude 的执行办法】."""
    if result in BODY_RESULTS:
        return "存档里没有它的网页"
    return f"这一轮的结果是「{outcome.RESULT_LABEL.get(result or '', result or '')}」"


def _foreign_words(foreign: tuple[Counter, Counter] | None) -> str:
    """What the README and the command say of the other sites' lists read for the site's pages (Writer._foreign_rows,
    blueprint item 22, part 2): how many of their pages were read, by kind, and how many could not be, by why; '' when
    no other site's list links the site. 【Claude 的执行办法】"""
    got, missed = foreign if foreign is not None else (Counter(), Counter())
    if not got and not missed:
        return ""
    said = (f"这个网站的网页也有别的网站的列表指到（例如单位网站的书表指到 SSO 的活动页）：读了那些列表 {sum(got.values())} 页"
            + (f"（{'、'.join(f'{k} {n}' for k, n in got.items())}）" if got else "")
            + "，它们的行写的日期、类别照「日期怎么来的」第 2 条用上；那些列表是别的网站的，不列进这份清单")
    if missed:
        said += (f"；另有 {sum(missed.values())} 页读不出（{'、'.join(f'{k} {n}' for k, n in missed.most_common())}），"
                 "那几页上的行没用上")
    return said


def _no_data_words(items: list[Item]) -> str:
    """The words these rows marked as the school's answer of no data hold in 缺什么 (Writer._no_data), each once,
    quoted as the cells hold them, for the README and the command (the review of 11-09: never the bare NO_DATA they
    begin with, which no cell holds alone): 「学校回答没有资料（系所未提供相關資料）」."""
    return "、".join(f"「{w}」" for w in dict.fromkeys(i.no_data.words for i in items if i.no_data is not None))


def _by_category(items: list[Item]) -> str:
    """How many of these rows are in each category (分类), worst first: 「连接失败 3、学校那边已经不存在 2」."""
    by = Counter(it.bucket for it in items)
    return "、".join(f"{progress.BUCKET_ZH.get(b, b)} {n}" for b, n in
                    sorted(by.items(), key=lambda kv: (_BUCKET_RANK.get(kv[0], -1), kv[0])))


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
    return f"  - {it.type}「{label[:60]}」{where}：{it.result_words()}{because}"


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


def _judged(items: list[Item], verb: str) -> str:
    """How the views judged these pages, in their manifest rows' own words (分类, 完整度), worst category first, with
    how many of each pair: 「这 27 页都是分类「要人看」、完整度「不完整」」, or one clause per pair. Never a judgement
    written in by hand: the 教务处 export (10-27) called its 27 empty notice pages 「抓到了」「完整」 while their rows
    said 要人看, 不完整 (技术总览 §7, 10-31)."""
    pairs = Counter((i.bucket, i.completeness()) for i in items)
    said = [(f"分类「{progress.BUCKET_ZH.get(b, b)}」、完整度「{level}」", n) for (b, level), n in
            sorted(pairs.items(), key=lambda kv: (_BUCKET_RANK.get(kv[0][0], -1), kv[0][1]))]
    if len(said) == 1:
        return f"这 {len(items)} 页都{verb}{said[0][0]}"
    return "，".join(f"{n} 页{verb}{words}" for words, n in said)


def _readme(root: Path, site: str, name: str, now: float, snap: Snap, state_words: str, pid, ordered: list[Item],
            counts: dict, missing_items: list[Item], builder: Builder, items: dict[str, Item], left: Left,
            old: Reuse | None = None, copies: OldCopies | None = None, skipped: Counter | None = None,
            not_needed: Counter | None = None, foreign: tuple[Counter, Counter] | None = None) -> str:
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
    out += [f"- 导出时：{run}"]
    ready = snap.ready
    if ready is not None:
        out += [f"- **资料截至：{ready.as_of or '（还没抓到东西）'}**（这个网站自己的网址最后一次抓、按钮最后一次按的时间，台北时间）",
                "- 可以导出：" + ("是（这个网站没有还在排队的网址和按钮，进度看板上它那一行打了勾）" if ready.can else
                               f"否（还有 {ready.urls} 个网址、{ready.presses} 个按钮在排队）")
                + "；和 nknu archive ready 同一套判断"]
    else:
        out.append(f"- 资料截至、可以导出：没算出来（{snap.ready_error}；存档比进度看板旧）")
    out += ["", "## 一共有什么", "",
            "| 类型 | 抓到（已导出） | 其中有档但还要处理 | 没抓到 |", "|---|---|---|---|"]
    out += [f"| {t} | {c['got']} | {c['to_do']} | {c['missing']} |" for t, c in counts.items()
            if t != BUTTON or c["got"] or c["missing"]]
    pressed = sum(1 for i in ordered if i.type == PAGE and i.path and i.kind in ("pager", "detail"))
    shared = sum(1 for i in ordered if i.shared)
    empty = [i for i in ordered if i.empty_area and i.path]
    no_data = [i for i in ordered if i.no_data is not None and i.path]  # the owner 11-09 「照这样做」
    to_do = [i for i in ordered if i.path and i.bucket not in SETTLED]
    said = []
    if to_do:
        said.append("「其中有档但还要处理」：档案导出了，分类却不是「抓到了」（要人看、待判定、下一轮补……），"
                    "看过或判定以后才算数，见下面「有档但还要处理的」")
    if empty:
        unsettled = sum(1 for i in empty if i.bucket not in SETTLED)  # in the table's 其中有档但还要处理 too
        where = ("" if not unsettled else "，都算在上表「其中有档但还要处理」里" if unsettled == len(empty)
                 else f"，其中 {unsettled} 页算在上表「其中有档但还要处理」里")
        said.append(f"导出了档的网页里有 {len(empty)} 页是**空的公告页**（公告的内文区找不到或是空的，多半是学校删掉的"
                    f"公告只剩网站模板）：照存档的判断，{_judged(empty, '是')}{where}；「缺什么」栏写着，下面"
                    "「空的公告页」一节逐页列出")
    if no_data:  # said here as the empty notice pages are (the review of 11-09)
        said.append(f"有 {len(no_data)} 行的网页上**学校回答没有资料**（「缺什么」栏写着{_no_data_words(no_data)}），"
                    "结果、分类、完整度照存档的判断，没有改；下面「学校回答没有资料的网页」一节逐行列出")
    if pressed:
        said.append(f"网页里有 {pressed} 页是按按钮才出来的（列表的第 2 页以后、按了才出来的公告内文）")
    if shared:
        said.append(f"有 {shared} 行和另一行是同一个档（跳转的网址、同一个档被几个网址用到），本地档案指向同一个档")
    if counts[BUTTON]["missing"]:
        said.append("「按钮」是网页上要点了才载入的写法，这一轮没办法抓，或还没决定怎么处理")
    whole = [i for i in ordered if i.decoded and i.path and not i.seen_lost and not i.filled]
    if whole:
        by = Counter(i.decoded for i in whole)
        quiet = sum(1 for i in whole if i.lost)
        said.append(f"有 {len(whole)} 页原本的编码读不通（看板上的结果是「编码有问题」），按网页或服务器说的编码重读，看得到的文字"
                    "都读通了：" + "、".join(f"{encoding_words(cs)} {n} 页" for cs, n in sorted(by.items()))
                    + (f"（其中 {quiet} 页只在网页的注释、链接网址这类看不到的地方有读不出来的字，用 � 代替）" if quiet else "")
                    + "。这些页的结果、分类、完整度照读通的网页算（「备注」栏写着按哪一种）")
    lossy = [i for i in ordered if i.decoded and i.path and i.seen_lost and not i.filled]
    if lossy:
        by = Counter(i.decoded for i in lossy)
        said.append(f"有 {len(lossy)} 页原本的编码读不通，没有一种编码读得通看得到的文字：照读不出来的字最少的那一种读（"
                    + "、".join(f"{encoding_words(cs)} {n} 页" for cs, n in sorted(by.items()))
                    + f"），一共 {sum(i.lost for i in lossy)} 个字读不出来，用 � 代替；结果照旧是「编码有问题」、分类「要人看」"
                    "（「备注」栏写着每页按哪一种、有几个）")
    old_read = sum(1 for i in ordered if i.decoded and i.path and i.filled)
    if old_read:
        said.append(f"10-24 旧存档的副本里也有 {old_read} 页按别的编码重读（「备注」栏写着），它们的结果、分类照这一轮的")
    linked = sum(1 for i in ordered if i.linked)
    if old is not None and not old.why:
        said.append(f"附件和图片有 {linked} 个是从上一次导出（`{old.folder}`）硬链接过来的：内容一样（sha256、大小都对得上），"
                    "没有再从存档读。两个资料夹里是同一个档，不要在资料夹里改档")
    elif old is not None:
        said.append(f"上一次导出（`{old.folder}`）没有用上：{old.why}，附件和图片都从存档读")
    filled = [i for i in ordered if i.filled]
    if copies is not None and copies.why:
        said.append(f"10-24 旧存档的副本资料夹（`{copies.folder}`）没有用上：{copies.why}；缺口照这一轮的")
    elif copies is not None:
        unsettled = sum(1 for i in filled if i.bucket not in SETTLED)
        said.append((f"有 {len(filled)} 行这一轮没抓到能用的内容（学校那边没了、连不上、等实验室、不是档案、跳到网站首页或查无资料页、"
                     "内容很少、看起来像不存在），"
                     f"用 10-24 旧存档里的正常副本补上了档（缺口概况第 ⑦ 项）：{_by_category(filled)}。它们的「来源」写"
                     f"「{ORIGIN_OLD}」，「抓取时间」「最后修改时间」「大小」「sha256」「格式」是旧副本的；「结果」「分类」「原因」"
                     "照这一轮的，缺口还看得到（「备注」写这一轮抓到什么）；有档了，所以不在 missing.csv"
                     + (f"，其中 {unsettled} 行分类不是「抓到了」，算在上表「其中有档但还要处理」里" if unsettled else ""))
                    if filled else f"用了 10-24 旧存档的副本资料夹（`{copies.folder}`），这个网站没有可以用它补的行")
        if skipped:
            said.append(f"10-24 旧存档有副本、却没用上的 {sum(skipped.values())} 行，照这一轮的导出（「备注」栏写着）："
                        + "、".join(f"{why} {n}" for why, n in skipped.most_common()))
        if not_needed:
            said.append(f"10-24 旧存档也有副本、这一轮的结果却不在要补的范围的 {sum(not_needed.values())} 行（多半是副本资料夹做好以后"
                        "又抓过、结果变了），照这一轮的，没有档的「备注」栏写着："
                        + "、".join(f"{words} {n}" for words, n in not_needed.most_common()))
    if said:
        out += [""] + [f"- {x}" for x in said]
    out += ["", f"**没抓到的 {len(missing_items)} 个**，按原因（每一个都在 missing.csv，原因写在「结果」「原因」栏）：", ""]
    out += _by_bucket(missing_items) or ["- 没有"]
    if to_do:
        out += ["", f"**有档但还要处理的 {len(to_do)} 个**（本地档案有，分类在「分类」栏）：", ""]
        out += _by_bucket(to_do, then=False)
    bad = [it for it in ordered if it.type == PAGE and it.path and it.level
           and it.level not in ("complete", "complete_school")]
    if bad:
        # not 抓到了但不完整: a page with a file may be 要人看 too (the 教务处 export's 27 empty notices, review 10-31)
        unsettled = sum(1 for it in bad if it.bucket not in SETTLED)
        out += ["", "导出了档但**不完整**的网页（「完整度」「缺什么」栏）：" + "、".join(
            f"{outcome.LEVEL_LABEL.get(k, k)} {n} 页" for k, n in Counter(it.level for it in bad).items())
            + (f"（其中 {unsettled} 页分类不是「抓到了」，算在上表「其中有档但还要处理」里）" if unsettled else "")]
    other = []
    if builder.counts["data_img"]:
        other.append(f"网页里直接内嵌的小图（data: 网址）{builder.counts['data_img']} 张：本来就在网页档里")
    if builder.counts["script"]:
        other.append(f"网站的程式档（.js）{builder.counts['script']} 个：不是内容，没导出")
    if builder.counts["drive_meta"]:
        other.append(f"Google 云端硬盘的档案资讯 {builder.counts['drive_meta']} 笔：程序读档案用的（档名、大小、修改时间），"
                     "没另外导出：档名写在附件那一行，修改时间写在「最后修改时间」栏（备注里有原样）")
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
    kinds = Counter(i.list_kind for i in ordered if i.list_kind)
    if kinds:
        out += ["", (f"「页别」栏标了「{LIST_FIRST}」{kinds[LIST_FIRST]} 行、「{LIST_PAGER}」{kinds[LIST_PAGER]} 行（列表的"
                     "第一页；翻页按钮翻出来的页和用网址翻页的列表的第 2 页以后）。有翻页的内容表格（荣誉榜这类）、平台设定"
                     "读不出列表格子、只靠网址翻页的网页不标：它们的内容可能只在那几页上。")]
    if empty:
        out += ["", "## 空的公告页", "",
                (f"这 {len(empty)} 页是平台的公告页，网页上公告的标题栏和内文栏却找不到或是空的：多半是学校删掉的公告，"
                 "只剩网站的模板（旧存档、Common Crawl 记得网址，学校那边已经删了）。是程序看出来的；按爬虫现在的规则，"
                 f"{_judged(empty, '算成')}，导出照存档的判断，没有改，只在「缺什么」栏写明。做内容的时候可以跳过。"), "",
                "| 网址 | 标题 | 缺什么 | 怎么来的 |", "|---|---|---|---|"]
        out += [f"| {i.url} | {i.title} | {i.empty_area.split('（', 1)[0]} | {i.how} |" for i in empty[:EMPTY_SHOWN]]
        if len(empty) > EMPTY_SHOWN:
            out.append(f"\n…另有 {len(empty) - EMPTY_SHOWN} 页，manifest.csv「缺什么」栏有「内文区」的就是。")
    if no_data:
        whys = "；".join(dict.fromkeys(i.no_data.why for i in no_data))
        out += ["", "## 学校回答没有资料的网页", "",
                (f"这 {len(no_data)} 行的网页上学校回答没有资料（{whys}）：网页照样导出了，「缺什么」栏写着"
                 f"{_no_data_words(no_data)}，结果、分类、完整度照存档的判断，没有改；做内容的时候可以跳过。"), "",
                "| 网址 | 怎么来的 |", "|---|---|"]
        out += [f"| {i.url} | {i.how} |" for i in no_data[:EMPTY_SHOWN]]
        if len(no_data) > EMPTY_SHOWN:
            out.append(f"\n…另有 {len(no_data) - EMPTY_SHOWN} 行，manifest.csv「缺什么」栏有「{NO_DATA}」的就是。")
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
    foreign_said = _foreign_words(foreign)
    out += ["", "## 日期怎么来的", "",
            ("「网页上写的日期」是**程序找的**，依序取第一个找到的："), "",
            ("1. **公告页上平台自己的日期栏**：c.nknu 公告页上「公告日期」那一栏、SSO 公告中心的「公告時間」（写的是开始到"
             "结束，取开始那天，就是公告上架的日子）、"
             "SSO 单位网站消息页上日历图示旁的日期、国际处、电子系、电机系、英语系、华语中心、地理系、工设系公告页的日期栏；"
             "WordPress（视设系、美术系）的日期栏只写日和月，年份从文章网址里的 /年/月/日/ 来，两个对得上才用。日期栏多半"
             "只写日期、没有标签：它上一层（或再上一层）有写同一天的标签（例如「公告日期：」「公告時間：」），「日期原文」就"
             "连标签一起记，否则只有日期栏里的字。"),
            ("2. **列表上指到它的那一行写的日期**：读不到日期栏（学校删掉的公告只剩模板）、网页本来就没有日期栏（讨论区的"
             "文章、活动花絮、相簿这类），或公告是按列表上的按钮才出来的（SSO 单位网站的消息，网址是列表网址加 #按钮=），"
             "只要列表上有一行指到它，就用那一行的日期（列表的日期栏；c.nknu 的列表没有日期栏，取那一行文字里第一个四位数"
             "年份的日期）。别的网站的列表指到它也算（例如单位网站的书表指到 SSO 的活动页）。好几行指到同一则、写的日期又"
             "不一样，取多数的，一样多取最早的，「备注」栏写明。**列表上一行的下载按钮带来的档案也照这一条**（SSO 单位网站的"
             "法规、表单、会议纪录这类档案表格：一行是类别、档名、下载按钮、日期），档案那一行的「网页上写的日期」「日期原文」"
             "「日期来源」「列表上的类别」写的就是它那一行的；网页用普通链接链到的档案不填。"),
            ("3. **网页文字里标签后面紧接的日期**。先找「发布、發佈、公告、更新、張貼、刊登」"
            "（后面可以接「日期」或「時間」，例如「更新時間」「公告日期」；英文 Posted、Published、Updated）这类标签，"
            "找不到才找单独的「日期」「Date」（活动页上「活動日期」写在「公告日期」前面时，取的是公告日期）；「最新公告」"
            "是侧栏标题，不算标签。认得 2025/10/01、2025-10-01、2025.10.01、2025年10月1日、全形数字 ２０２５／１０／０１，"
            "以及民国年 114/10/01、114.10.01、民國114年10月1日、中華民國114年10月1日（民国年加 1911 换成西元）。先找正文区，"
            "找不到再找整页，取第一个。**可能不准**（例如抓到的是页尾的「网站更新日期」），所以「日期原文」栏附上它在网页上"
            "的原样，可以核对；找不到就空白。"), "",
            "列表页上有很多条、各有各的日期，所以列表页不填。附件、图片看「链到它的网页」那一页的日期。", "",
            ("「列表上的类别」是第 2 条那一组行写的类别（平台设定读得到类别的列表才有），不管日期是哪一条找到的都填；"
             "几行写的不一样就都列出，「备注」栏写明。"), "",
            *([f"{foreign_said}。", ""] if foreign_said else []),
            (f"「日期来源」栏写日期是哪一条找到的：「{OWN_FIELD}」（第 1 条）、「{LIST_ROW}」（第 2 条）、「{PAGE_WORDS}」"
             "（第 3 条）；找不到日期就空白。跳转的网址用落地那一页的档：落地那一页的日期栏读得到就用它，否则用这个网址自己"
             "在列表上那一行的日期，再不然用落地那一页其他的日期。学术活动、校园活动、高教深耕活动的页面只写活动的日期，"
             "不写上架的日子，这一版没有当成日期栏读。"), "",
            ("「最后修改时间」是学校服务器回答时说的档案最后修改时间（HTTP 的 Last-Modified），云端硬盘的档案是云端硬盘记的"
             "修改时间，都换成台北时间；服务器没说、写得读不懂、或年份不像真的（例如 1970 年）就空白。网页大多没有，"
             "附件常有，附件没有所属公告时可以拿来当日期参考。**但要先看同一个网站的附件**：有的服务器说的是它自己把档案"
             "复制过去的时间，每个档都差不多（例如图资处在 10-24 旧存档的副本，610 个全在 2026-10-22 的十几分钟里），"
             "很多档的时间挤在一起就是这样，那不是档案自己的日期，不能拿来用。"), "",
            "「抓取时间」是爬虫抓到它的时间（台北时间）。", "",
            "## 编码", "",
            ("网页档是爬虫解码后的结果，存成 UTF-8（档头有 BOM 记号，浏览器会照 UTF-8 显示），内容没有改。学校有些网页的 "
            "`<meta charset>` 写 big5，实际却是 UTF-8（c.nknu 就是这样），所以爬虫不照 meta 解码；网页档里那行 meta "
            "照原样留着，不用理它，档案本身已经是 UTF-8。清单的「大小」「sha256」是存档里原始内容的（网页档改存成 UTF-8，"
            "档案本身的大小可能不同；附件和图片的完全一样）。"), "",
            ("爬虫解不开的网页（「结果」是「编码有问题」），导出时再试一次，只试网页自己在 meta 写的、服务器说的编码（日文 "
             "Shift_JIS、繁体中文 Big5、简体中文 GB 这类多位元组编码），取第一个整页都读得通的；档头有 UTF-8 的 BOM 记号的，"
             "只照 UTF-8。读通了，「备注」写「原本的编码读不通，按 … 重读」。都读不通，就在这些编码和爬虫平常用的 UTF-8、繁体"
             "中文 Big5 里，取读不出来的字最少的那一种读，读不出来的字用 � 代替，「备注」写按哪一种、有几个。看得到的文字"
             "（正文、标题；不算网页的注释、链接网址）都读通的，就是学校原本那一页：「结果」写抓到了，「分类」「完整度」"
             "「缺什么」照读通的网页算（它缺的附件、内嵌页照样算），「备注」写看板上是「编码有问题」；看得到的文字里还有 � "
             "的，结果照旧是「编码有问题」、分类「要人看」。不会改用网页和"
             "服务器都没说的编码去猜：学校自己造的罕用字（造字，常在人名里）换别的编码读，整页会变乱码，或变成另一个字，"
             "看不出来读错了；� 至少看得出那里少了一个字。原样都在存档里。"), ""]
    return "\n".join(out)


COLUMN_HELP = {
    "序号": "清单里的顺序：网页、附件、图片、资料、按钮，各自先列抓到的",
    "类型": "网页 / 附件 / 图片 / 资料 / 按钮（网页上要点了才载入、这一轮没办法抓的写法）",
    "网址": ("网页上链的网址；按钮按出来的页是列表网址加 #第N页 或 #按钮=…（两个按钮的文字一样时，后面再加括号里的存档编号，"
           "分得开）"),
    "最后落地网址": ("跳转以后真正拿到内容的网址（没有跳转就空白）；用 10-24 旧存档的副本补上的行，是副本的网址（和「网址」一样"
               "就空白），这一轮跳到哪里写在「备注」"),
    "本地档案": "导出资料夹里的档案；空白就是没抓到",
    "标题": ("网页：平台的标题栏（公告的标题、活动的名称、讨论区提问的主题、所友的姓名、心得的标题、教师履历页和系所成员页的"
           "姓名、地理系网页的页名），否则第一个 h1、h2，再不然 <title>；附件和图片：档名；按钮按出来的公告：列表上的标题"),
    "网页上写的日期": ("程序在网页上找到的日期（西元，YYYY-MM-DD），找不到空白；见下面「日期怎么来的」。列表上一行的下载按钮"
                "带来的档案：那一行写的日期；别的附件和图片空白"),
    "日期原文": "找到的日期在网页上的原样（含标签），用来核对",
    "完整度": ("有档的网页：完整、部分完整、不完整、待判定、列表没核对、还没抓完、完整（学校那边缺）；其他（附件、图片、"
            "没有档的网页……）：和「分类」一样（抓到了、连接失败、要登录……）"),
    "缺什么": ("网页缺的东西（缺附件、缺图片、连接失败、要人看、列表没翻完、学校那边已不存在或要登录的资源……），括号里是几个；"
            "公告页的内文区找不到或是空的（多半是学校删掉的公告）、学校在网页上回答没有资料（写「学校回答没有资料（…）」，"
            f"括号里是学校那一句或是什么情形，例如「{profiles.COURSE_NO_DATA.words}」是开课课表上学校跳出那句、这个系所这个"
            "学年度没有提供课表；别的还有找不到这一页、404、列表一则都没有、英文版没有内容，README「学校回答没有资料的网页」"
            "一节逐行写明），也写在这里"),
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
    "按钮编号": ("按按钮才出来的页和档：爬虫给这个按钮的编号（翻页是 p 加页数；列表上一行的按钮是用网站、列表、标题、日期、"
             "分类算出来的指纹，列表多了新的一行也不会变）；其他行空白"),
    "最后修改时间": ("学校服务器说的最后修改时间（Last-Modified），云端硬盘的档案是它的修改时间；台北时间，没有就空白。"
               "同一个网站很多档挤在同一段时间的，是服务器复制档案的时间，不是档案的日期（见下面「日期怎么来的」）"),
    "日期来源": (f"「网页上写的日期」是怎么找到的：{OWN_FIELD}（公告页上平台的日期栏）、{LIST_ROW}（指到这一页的列表那一行"
             f"写的日期；别的网站的列表也算；下载按钮带来的档案是它那一行写的日期）、{PAGE_WORDS}（网页文字里标签后面的"
             "日期）；没有日期就空白"),
    "来源": (f"这一行的档从哪里来：空白是这一轮爬虫的存档；「{ORIGIN_OLD}」是这一轮没抓到能用的内容、导出时用 10-24 旧存档"
           "的正常副本补上的（结果、分类、原因还是这一轮的，备注写这一轮抓到什么）"),
    "页别": (f"「{LIST_FIRST}」：平台设定读得到列表格子的网页（列表的第一页；网站首页上的公告列表也算，这种页也有自己的字）；"
           f"「{LIST_PAGER}」：按翻页按钮出来的第 N 页（网址后面是 #第N页），和用网址翻页的列表的第 2 页以后；空白：其他网页，"
           "也包括有翻页的内容表格（荣誉榜这类没有链接的表格，内容只在这几页上），和平台设定读不出列表格子、只靠网址翻页的"
           "网页"),
    "列表上的类别": ("指到这一页的列表那一行写的类别（平台设定读得到类别的列表才有：SSO 公告中心、SSO 单位网站的消息和书表、"
               "cLv2 单位网站的公告；c.nknu 的列表没有设定类别栏，空白）；别的网站的列表指到它也算；几行写的不一样就都列出，"
               "用 | 隔开，「备注」写明；没有就空白。列表上一行的下载按钮带来的档案：那一行写的类别"),
    "公告单位": ("公告页上平台的「公告單位」那一格的原文（平台设定写了这一格的公告页才有：SSO 公告中心和新闻网的公告、校园亮点）；"
             "转到这种公告页的行、和它共用同一个档的行一样有；那一格没有字、别的网页、附件和图片都空白"),
}
