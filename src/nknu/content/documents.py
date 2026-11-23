"""Corpus records -> documents: one per notice, page, attachment, outside link and calendar event (skeleton spec:
「一个网页算一份，一个附件也算一份」). List pages and albums are left out and counted, as are images only menus carry
(MENU_IMAGE, decided by assemble: I4), duplicate notices (one site and nid: ids.location_key, plan 2026-11-08 T1),
records with neither title nor text and calendar events that start before CALENDAR_SINCE, so nothing goes missing
silently. Records of one identity have one id (plan 2026-11-08 T1): one_per_id keeps one of them before this.

The school's deleted notices (owner 11-09, blueprint item 15, 「照建议定」): a notice the school deleted leaves only the
site's template, which went in as a notice with neither text nor date (教务处 27, 体育室 27); 「建议不收、在每站报告里记
个数（学校删掉的公告不该被搜到），照导出标的『内文区是空的』做」. A record the export marked so (Record.area_mark,
corpus.AREA_MARKS) is no document (deleted_notice): counted as <kind>(学校删掉), never kept for its address nor counted
as (重复), and the build writes each site's count to sites.deleted_notices. AREA_EMPTY (the platform's own title and
text areas on the page, and empty) always; AREA_MISSING (neither area on the page) only with no text read either,
since a template the export's profile does not know yet looks the same (review 11-09) 【Claude】.

A list's pager pages (blueprint item 22, part 3; owner 11-09 12:27, 「照这个做」, to the item's proposal that the
content side leave them out by the crawler's mark once it marks the lists): a page the export marks 列表翻页
(Record.list_kind LIST_PAGER, the crawler's 53cf31b: a page a list's pager brought, …#第N页, or a GET-paged list's
page 2 on) is no document whatever its kind (_class), counted as <kind>(列表翻页) when its kind alone would keep it, else under its kind as before. The content
side's check of 11-09 evening, of what is only on them (research/pipeline_2026-11-02/reading_gaps_1109/lists_1109g.log
to lists_1109j.log): the 1,580 such pages the build kept hold 15,391 list rows by the crawler's reading, each linking a
page the export holds and the build keeps with its text (but 17 notices the school deleted), with the row's title on
that page and its date that page's day, and 30,129 of their 31,582 table cells and lines are on the linked pages; the
1,453 left are on none of them (tecs's per-row categories, 7 labels of the c.nknu grid's category column, which the
crawler does not read yet; iu's card summaries, case.aspx, 336; tecs's activity-list fields, 86; pager widgets).
Leaving those out with the pages is 【Claude】: the check ran after the owner's 「照这个做」, which did not weigh them.
The other 4,991 marked pages were left out by their kind already. A page marked 列表 (LIST_FIRST: a list's first page,
a home page, a teacher's file grid; 450 kept) stays a page: it holds words of its own
(research/crawler_lab_2026-11-04/item22_1109/lists.md §3.3 item 7), and none of the 450 is dated (the export gives a
list page no row's date, and _stated takes none on a site's root or a c.nknu list path: lists_1109g.log).

The school's answers of no data (the crawler's fa81f41): the course tables (sso.nknu.edu.tw/dataopen2search's
opencoursetablepure.aspx) answer 「系所未提供相關資料」 in a pop-up the to-text step cannot read, so the 124 answers of
115學年度 read as empty forms of 71 to 86 characters (research/pipeline_2026-11-02/reading_gaps_1109/lists_1109g.log
§D); 14 default pages, of 113, say it too, and the 9 of them unit sites list read 72 to 84 (after_1109f.log; the
other 5 were not measured). The export marks such a page 「学校回答没有资料」 in 缺什么 (corpus.NO_DATA) in every export
listing it (147 rows of 10 exports on 11-09: nodata_1109l.log). The owner 11-09 「照这样做」, in the crawler session,
to the content session's proposal as research/project_audit_2026-10-25/owner_own_words.md records it
(2026-11-09T12:12:12.218Z: 「…不按网址拿掉，导出在「缺什么」标「学校回答没有资料（系所未提供相關資料）」，内容 session
照标记不收、记个数」): such a page is no document (_class) and is counted, never by its address, since the real tables
would go too once the school publishes the year, and 16 real notices on 5 SSO unit sites carry the same
「#按钮=115學年度」 (research/crawler_lab_2026-11-04/item22_1109/peer_notes.md 第六、七则). How the content side does it
is 【Claude】: Record.area_mark holds the mark, before an area mark when a row has both (corpus.ROW_MARKS: a page so
marked is never kept; no row had both on 11-09, the course tables' platform naming no notice areas); the count is <kind>(学校回答没有资料) when its kind alone
would keep the page, one marked 列表翻页 too counted so (_left_out_as); and an image only such pages show is no kept
page's (assemble.inline), as on a list's pager page.

Every platform (plan 2026-11-08 T3; kind_class): a notice is a page of c.nknu's notice templates (newsdetail, newsview)
or a detail page of any profiled platform (detail:<profile>, a press-made one too); a list read from its page
(list:<profile>, pagetext.list_grid) and a list's pager row (…#翻页) are left out like c.nknu's lists. A document's unit
is the site table's (nknu.content.sites.unit_for: its own URL's site, else its export's), an event's its export's.
Dates: a c.nknu notice takes its date field (corpus.read_metas: 公告頁日期欄); any other notice, and a page, takes the
date the export read on its page (网页上写的日期, source 網頁上寫的日期: _stated) when it read it after a label of
putting the page up or changing it (公告、發佈、更新 …, never a bare 日期: _OWN_LABEL), a page only as its own date.

Sub-step 4 (spec 2026-11-08 §四), the step-3 export's columns ('' in an export made before 3c33a79, which then dates
and classes everything as above): a date the export read in the platform's own date field (日期来源 详细页的日期栏,
source 公告頁日期欄) or on the row of a list linking the page (列表上的日期, source 列表上的日期: a notice's row on its
list; since the crawler's 53cf31b, blueprint item 22 part 2, the row of any list, this site's or another site's, that
links any page: _stated) needs no label, the crawler's platform rule having chosen the field (spec §二 2); and a page
it read the platform's date field on is a notice (_class: a detail page whose address no detail path names, GEO, ID,
WordPress profiles: 2,253 pages on 11-09) 【Claude】. Every document carries the server's time of its file (最后修改时间,
Document.server_date, spec §二 3), which the build dates only an attachment by.

An attachment hangs on the latest-dated page linking to it (a notice's date; undated notices, then pages, after;
corpus order on ties) and takes that page's date (addendum 2026-11-02 B; 11-01: with several pages, the latest). The
assembled corpus (T7a) keeps apart the pages that link it only from a menu (nav_linked): they are references, never
its parent. One linked only from a left-out list page (album_images) has no parent but keeps that page's title as
parent_title, so the index still knows its context. Its title is what its latest-dated linking page calls it
(Record.contexts; the most frequent name among pages of that date), the file name otherwise; a title taken from a page
keeps the file name in linktext. refs: every page document linking it, with the zone and the name.
組別: a notice's own group field; a page's from the gi URL parameter, mapped by what the notices of its own site with
that gi say (gi 2 is 和平教務組 on 教務處's site, another group elsewhere: plan 2026-11-08 T1); an attachment's from its
parent when the parent is of its own unit; else (no parent, or another unit's page: another site's later re-post of the
file) from the latest-dated page of its own unit linking it that has one, then from the gi of the pages of its own unit
linking to it (menus included) (plan 2026-11-08 T4's pilot check: 體育室's PDF a 教務處 notice links took that
notice's 課務組; its review: 教務處's file a 生輔組 re-post links later lost its 和平教務組) 【Claude】.

Many exports (plan 2026-11-08 T4): the build reads several, so one identity can come from each (a file two sites link,
the SSO news mirror, one calendar two sites show). one_per_id keeps the record of the newest 抓取时间 (an &amp;
spelling still gives way first; on a tie, _rank: the export of the address's own site unless that site is a shared
platform, then any other export, the one that brought it, a shared platform's own export last; then the first in input
order) with what all of them say; one_event_per_id keeps one event per id the same way. A document's site is the
export its record or event came from (docs.site), which the sites table dates.

Recurring calendar events (plan 2026-11-14 T2; direction 2026-11-13 第 2 件 3: 「行事历重复系列合成一笔：原民系 10,019
个事件变 195 个系列，进修学院 598 个变 48 个」): the calendar export holds the Calendar API's items expanded with
singleEvents=true, so every instance of a recurring event is an item of its own that carries recurringEventId
(Event.series) and the id <that id>_<its start> (教务处's 11-02 export: 46 such items of 6 series, each id of that
shape, all of 2017 and 2018, so none of them is a document and 教务处's events stay as they were).
one_event_per_series, run after one_event_per_id, makes one event of a series' instances: its id is the series
id (the document 'e' + it), its title their title, its start the first day and its end the last (what dates an event
and says when it is over: build._facts), and its text lists every instance's days after the title (series_text), so
no day is dropped. Only instances that would be documents (from CALENDAR_SINCE on, with a title) and share the title
most of them have are merged; an instance with a title of its own (an exception the unit renamed) and the only
instance of a series stay the single events they were 【Claude】.

An event's date line (plan 2026-11-14 T3; direction 2026-11-13 第 3 件: 「给向量和重排读的字，加一行学期和日期。现在模型只读到
标题，分不出不同年的同名事件」): event_line writes an event's term and days, 「115學年度第2學期 2027-02-15～2027-02-18」,
which the build stores in docs.date_line and embed puts into the passages the reranker reads of the event. Corrected
the same day, against the direction's 「给向量」: the vectors do not embed it (measured on the first rebuild, the line
dilutes an event's short title in the vectors while the reranker gains from it: nknu.content.embed).

A document's own title (plan 2026-11-14 T4; direction 2026-11-13 第 4 件: 「其余同名页（约四千份）和标题只是档名的附件…：
用正文第一个标题，没有就用正文第一行。旧标题都留着。」 and 「取到『附件一』『說明』这种的那一类，就留旧标题」): own_titles,
which the build runs on the documents it keeps (copies merged), gives a document whose title says nothing the title
its own text gives (text_title), unless that line is no title either (title_flaw; since the first rebuild of 11-14 a
line that holds a file size, a download list's 「… pdf 146 KB」, is none: _SIZE), and keeps the old title in
Document.old_title. Two kinds of titles say nothing: one that SAME_TITLE_MIN or more pages and notices of one site share
(TITLE_SHARED: a site name used as every page's title; the crawler's export gives activity pages, forum posts and
alumni pages their own title cell since d9d8239, so this is for what is left), and an attachment's file name without
a Han character, 「643-79-8.odt」 (TITLE_FILE) 【Claude, the main session's brief of 11-14: narrower than the plan's
「标题只是档名的附件」, since a file name with Han characters usually says what the file is, and the 11-02 decision G4
keeps file names as titles for year tables on purpose】. Since 11-15 a third kind, the other way round (TITLE_LABEL,
own_titles' _labels): the name a page gives a file is a label, not a name, when SAME_TITLE_MIN or more files of the
site carry it (「檔案連結」, 「公文」), and such a file is titled by its file name again when that has a Han character.

A document's stated publishing unit (the owner, 11-15: 「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」; the
crawler's 2989f60): Document.stated_unit carries the words of the announcement platform's 「公告單位：」 cell from the
export's 公告单位 (Record.publisher; one page in several exports: _joined). build_documents only carries them: the
unit stays the site table's here, and the build, which has the unit tree, names the unit and the 組別 by them
(build._stated_units).
"""

from __future__ import annotations

import dataclasses
import html
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from nknu.archive import profiles, scope
from nknu.content import ids, sites
from nknu.content.corpus import (
    AREA_EMPTY,
    AREA_MARKS,
    CATEGORY_SEP,
    GONE,
    IN_BUTTONS,
    LIST_PAGER,
    MOVED,
    NO_DATA,
    NOTICE_KINDS,
    Event,
    PageMeta,
    Record,
    plausible,
    taipei_day,
)
from nknu.content.dates import FIELD_SOURCE, LIST_ROW, LIST_SOURCE, OWN_FIELD, PAGE_SOURCE, kept_range
from nknu.content.numbering import marks, strip_decor
from nknu.content.terms import Term
from nknu.core.textnorm import CJK, norm_text
from nknu.core.timeutil import TAIPEI

EVENT_KIND = "行事历"  # kind_raw of calendar events (the export's data file name)
# c.nknu's list pages by their 10-29 names (its pages keep them: pagetext.page_kind)
EXCLUDED_KINDS = frozenset({"newslist", "newslist#翻页/按钮", "album", "album#翻页/按钮", "album_images", "sitemap",
                            "linkslist"})
# page_kind's names on the other profiled platforms (plan 2026-11-08 T2): a detail page (a notice), a list read from
# its page and a list's pager row (left out)
DETAIL, LIST, PAGER = "detail:", "list:", "#翻页"
# the kind assemble gives an image every page referencing it carries only as a picture outside its content area (a
# unit banner, whose OCR text is the unit's name): no document, counted under this name (main session 11-02, I4); a
# file linked only from menus stays a document (a standing file of its unit)
MENU_IMAGE = "只在选单出现的图片"
# prefixes: scanned PDFs, mostly scanned PDFs with a few words (the words are not the document; review 11-01), zip,
# xlsx, ole ...: never shown as 全文; whatever text they have is still indexed and shown as a few words
NO_TEXT_KINDS = ("附件:pdf:无文字层", "附件:pdf:文字很少", "附件:无文字")
BLANK = "(空白)"  # excluded-count suffix: neither title nor text
# excluded-count suffix: a second record of one notice (one site and nid), or of one identity (one id: one_per_id)
DUPLICATE = "(重复)"
# The calendar starts here: the owner's decision of 11-01, a fixed date that never rolls forward with today
# (「不需要滚动算法，就直接从23年开始就好了」). An event whose first day (Asia/Taipei) is earlier is no document; it stays
# in the export and is counted as 行事历(2023年以前).
CALENDAR_SINCE = date(2023, 1, 1)
BEFORE = f"({CALENDAR_SINCE.year}年以前)"  # excluded-count suffix: an event before CALENDAR_SINCE (a 1 January)
FORMAT = "(其他格式)"  # excluded-count suffix: a format sibling assemble folded into another record (addendum B)
DELETED = "(学校删掉)"  # excluded-count suffix: a notice the school deleted, only the template left (deleted_notice)
# excluded-count suffix (plan 2026-11-14 T2): an instance of a recurring calendar event folded into the one event of
# its series (one_event_per_series); the series' own document is not counted
SERIES = "(重复系列)"
# the line a series' text carries after its title (series_text): ours, not the school's, so it opens with 【系統】;
# as few words as say it, since they are indexed with the text 【Claude】
SERIES_NOTE = "【系統】共 {n} 次，日期（年-月：日）："
PAGER_SUFFIX = "(列表翻页)"  # excluded-count suffix: a page marked 列表翻页 its kind alone would keep (_class; item 22)
# excluded-count suffix: a page the school answered with no data (corpus.NO_DATA, the crawler's fa81f41) its kind alone
# would keep (_class); the key, and that it goes before PAGER_SUFFIX (_left_out_as), are 【Claude】
NO_DATA_SUFFIX = "(学校回答没有资料)"
# excluded-count suffix: a row the school deleted (corpus.GONE; spec 2026-11-16 nightly ingest §五), before the others
GONE_SUFFIX = "(学校已删除)"
# excluded-count suffix: a file that only pages the school deleted link (build_documents; 11-16 【Claude】)
GONE_FILE_SUFFIX = "(学校已删除的网页带的附件)"
MOVED_SUFFIX = "(学校把公告转到别的页)"  # excluded-count suffix: corpus.MOVED
IN_BUTTONS_SUFFIX = "(内容在按钮行)"  # excluded-count suffix: corpus.IN_BUTTONS (11-19), a page of a teachers' menu only
UNKNOWN_ZONE = "unknown"  # a linking page whose link to the file was not found (a button), or the 10-29 corpus
ZONE_ORDER = {"attachments": 0, "content": 1, UNKNOWN_ZONE: 2, "nav": 3}  # the zone a page's reference is shown with

_HSPACE = re.compile(r"[ \t\v\xa0\u1680\u2000-\u200a\u202f\u205f\u3000]+")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_BLANK_LINES = re.compile(r"\n{3,}")
# the size the site writes after each attachment link, 「115-1停修公告.pdf (619775 Kb)」: no word of the document
_FILE_SIZE = re.compile(r"\s*[(（]\s*\d+(?:\.\d+)?\s*(?:[KkMmGg]?[Bb]|bytes?)\s*[)）]$")
LINK_SEP = " | "  # how the export joins the texts of all the links to one file (archive.export._joined)
# The export's labels of putting a page up or changing it (archive.export._STRONG: 發佈、发布、公告、更新、張貼、刊登, with
# 日期 or 時間 after them or not; Posted, Published, Updated). Its 日期原文 starts at the label it read the date after,
# so one starting with these is a date the page gives itself. One after a bare 日期 (the export's weaker label) is no
# date of the page: the label is the tail of a word that names another day, 校慶日期, 申請日期 (a deadline), 修正日期
# (a rule's); 教务处's three such dates were all of that kind (plan 2026-11-08 T3 review) 【Claude】
_OWN_LABEL = re.compile(r"(?i)^\s*(?:發\s*[佈布]|发\s*布|公\s*告|更\s*新|張\s*貼|张\s*贴|刊\s*登|posted|published|updated?)")
# the export's 日期来源 that need no label (spec 2026-11-08 §二 2: the crawler's platform rule chose the field), with the
# source each date gets
_TRUSTED = {OWN_FIELD: FIELD_SOURCE, LIST_ROW: LIST_SOURCE}
# A 最后修改时间 this close to the row's 抓取时间 is the server's clock answering a page it made on the fly, not the
# time of a file: 825 pages of geo.nknu.edu.tw, 2 附件 and 2 图片 elsewhere in the 111 exports of 11-09 (sub-step 4,
# spec 2026-11-08 §四) 【Claude】; so is one after it (a server clock running fast: a file is never changed after the
# answer that carried it, and HTTP forbids a Last-Modified later than the answer's Date; none in the local crawls)
CLOCK_SECONDS = 120
# plan 2026-11-14 T4 (own_titles; the module docstring). The kinds of titles that say nothing, as the report names them
TITLE_SHARED, TITLE_FILE = "shared", "file"
# 11-15 (the owner: 「请看一下附件标题取自链接文字还是档名的规则对不对，能修的话在这轮重建前一起修掉」) 【Claude】: a name
# SAME_TITLE_MIN or more attachments of one site take from their pages is a label, not a name (own_titles)
TITLE_LABEL = "label"
# the plan's 「同一网站 20 页以上标题一模一样的」: so many pages and notices of one site with one title make it no title,
# and a new title so many documents of the site would carry is none either
SAME_TITLE_MIN = 20
TITLE_MIN_CHARS = 2  # the plan's 「太短（少于 2 个字）」, counted in letters and Han characters (title_flaw)
# 【Claude】 a longer line is a sentence or a paragraph, not a title: the longest of 教务处's 498 notice titles has 76
# characters (99 in 100 have at most 62), and a line cut short would be no title either
TITLE_MAX_CHARS = 80
# 【Claude】 the text's first heading is its title only on one of its first lines with text: a label or a date stands
# above a title (附件四, 114年9月修訂), a heading further down heads a part (說明：, 切結書, 五、報考資格). Counted on
# 教务处's converted files of 11-14 (the Mac's partial cache, 395 with a Markdown heading): the first heading is the
# first line with text in 301 and the second in 42, where it is the better title in about 36 (「附件四」 above
# 「個人資料蒐集、處理及利用同意書」); in the 22 where it is the third to sixth it is as often worse than the first line
# (目錄 contents, 電子公布欄公文, 第十四條), and in the 30 where it is later still it is nearly always a part's
HEAD_LINES = 2
TITLE_EXAMPLES = 40  # the retitled documents of each kind the report lists, spread over them, to look at after a build
TITLE_SHOWN = 80  # how much of an old title an example shows
IMAGE_KIND = "附件:图片"  # assemble's kind of a picture with OCR text (title_unread)
_HAN = re.compile(rf"[{CJK}]")
_LETTER = re.compile(r"[^\W\d_]")  # a letter or a Han character, as numbering._TEXT
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")  # a file name's extension
_LINE = re.compile(r"[^\n]+")
_NOT_HAN_OR_DIGIT = re.compile(rf"[^{CJK}0-9]+")
# 【Claude】 the lines that say nothing as a title (the direction's 「附件一」「說明」, the plan's 「注意事項」; kept short),
# each alone or with a number after it, compared as the Han characters and digits of norm_text (_bare_key: 附件 1,
# 【附件二】, 說明：, 目錄 contents). 附表, 附錄, 目錄, 備註 and 抄本 (the copy mark of an official letter) are of the
# same kind: labels a file carries above its title. The school's own name and the document's unit say nothing either
# (title_flaw's names): of 教务处's 628 files with text that are named without a Han character (pictures aside), 28
# open with the school's name on a line of its own
_NOTHING = re.compile(r"(?:附件|附表|附录|说明|注意事项|目录|备注|抄本)[一二三四五六七八九十0-9]*")
_LABEL_END = ("：", ":")  # a line ending so is a field's label (檔號：, 授課科目：), no title
# 【Claude】 plan 2026-11-14 T4, after the first rebuild: a file size in a line, on norm_text (NFKC, lowercase,
# Simplified): a number, then a unit of bytes (146 KB, 23kb, 1.2 MB, 3 MiB, 3072 bytes, 512 位元組, 千位元組, 字节; the
# one-character ㎅ and ㎆ come out of NFKC as kb and mb). Not a longer word that only starts like a unit (100 kbps,
# 3MBA), a bare letter (113B 教室, 5G) or a unit with no number before it (KB 國民銀行). The rebuilt 111-site database
# had 9 attachments whose new title was such a line, 「… 146 KB」, 「pdf 23kb」: a download list's link text as the
# first line of their text, which says how big a file is and not what the document is (title_flaw's 'size')
_SIZE = re.compile(r"\d\s*(?:(?:[kmgt]i?b|[kmgt]?bytes?)(?![a-z])|[千兆百万]{0,2}(?:位元组|字节))")


@dataclass
class Document:
    doc_id: str
    kind_class: str  # notice | page | attachment | event | link
    kind_raw: str
    title: str
    url: str
    unit: str
    grp: str
    parent_id: str | None
    parent_title: str
    linktext: str
    has_text: bool
    text: str
    linked_from: tuple[str, ...] = ()  # doc_ids of linking docs, corpus order
    added: date | None = None  # notices only: the date the notice states, read where added_source says
    added_raw: str = ""
    # dates.FIELD_SOURCE (a notice's date field), dates.LIST_SOURCE (its list row's: sub-step 4), dates.PAGE_SOURCE
    # (plan 2026-11-08 T3)
    added_source: str = ""
    # pages only (plan 2026-11-08 T3): the date the export read on the page (dates.PAGE_SOURCE), the page's own date;
    # never a parent's date for the files it links, nor what orders the pages naming a file (only a notice's date does,
    # as before: 教务处's files keep their parents, titles and dates 【Claude】)
    page_date: date | None = None
    page_date_raw: str = ""
    page_date_source: str = ""  # the source of page_date (_stated): dates.PAGE_SOURCE, or LIST_SOURCE (sub-step 4)
    # attachments only (spec 2026-11-16 §二, the crawler session's finding of 11-16): the date on the file's own row of
    # a file table (the export's 日期来源 列表上的日期 on the row of a file a button downloaded: SSO unit sites and
    # department sites list files with a name, a category and a date, and no notice stands between the list and the
    # file to inherit from); None for any other file, and in an export that gives files no such date
    row_date: date | None = None
    row_date_raw: str = ""
    # 11-16 (join_single_lines): the one-character lines joined away in text and the leader marks dropped from it
    lines_joined: int = 0
    marks_dropped: int = 0
    # every document (sub-step 4, spec 2026-11-08 §二 3): the server's time of its file (_server_day: the export's
    # 最后修改时间), which dates an attachment only (owner 11-01: a file inherits its page's date, else the server's
    # time; a page is never dated by it); None without one
    server_date: date | None = None
    source: str = ""  # where the text came from (assembled corpus): totext | fallback-1029 | none
    refs: tuple[tuple[str, str, str], ...] = ()  # attachments: (doc_id, zone, context) of each page document linking it
    title_from_context: bool = False  # the title is what a page calls it (addendum B), not its file name
    file_title: str = ""  # the file name, when the title came from a page
    formats: tuple[dict, ...] = ()  # format siblings assemble folded into it (shown as its copies)
    duplicates: tuple[str, ...] = ()  # doc_ids of the notices left out as (重复) of it (the build's doc_alias)
    site: str = ""  # the site key of the export its record or event came from (plan 2026-11-08 T4: docs.site)
    # blueprint item 22 (owner 11-09, 「照这个做」), the crawler's 53cf31b: the categories the list rows linking its page
    # state (_categories of Record.list_category, with those of the other records of its identity: _joined; and of its
    # copies: build._take_over), which build.UnitWords puts on its unit line; () for a record without them (an
    # attachment, a link) and for an event
    categories: tuple[str, ...] = ()
    # plan 2026-11-14 T4 (own_titles): the title is the one its own text gives, and old_title the title it had until
    # then (a site's name, a file name; '' when that was empty), which the link names do not take; False and '' for
    # any other document
    title_from_text: bool = False
    old_title: str = ""
    # plan 2026-11-14 T2 (main session, after the review): the units of the copies the build merged into this
    # document, each once, never its own (build._take_over); build.unit_scope reads them
    also_units: tuple[str, ...] = ()
    # the owner, 11-15 (「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」): the publishing unit its page states
    # (Record.publisher: the announcement platform's 「公告單位：」 cell, as written), '' without one; and whether its
    # unit and 組別 are the ones that statement names, which the build sets (build._stated_units: a notice so stated,
    # and a file of the platform hanging on it), never build_documents
    stated_unit: str = ""
    unit_stated: bool = False
    # 11-15 (own_titles' TITLE_LABEL): the title is its file name again, the name its page gave it being a label many
    # files of its site carry (old_title holds that label)
    title_was_label: bool = False


def clean_text(s: str) -> str:
    """Whitespace only, every other character kept: CR and form feed become newlines, runs of spaces and tabs one space,
    lines trimmed, at most one blank line in a row. The 10-29 text is often more whitespace than words."""
    s = _ZERO_WIDTH.sub("", s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n"))
    s = "\n".join(_HSPACE.sub(" ", line).strip() for line in s.split("\n"))
    return _BLANK_LINES.sub("\n\n", s).strip()


# 11-16 (the owner: 「碎字pdf你现在去查原因和调查 来决定要用什么工具」; research/pipeline_2026-11-02/pdf_fragments_1116/):
# join_single_lines' marks, the characters a dotted or bulleted line is drawn with, and how many of them in one run make
# it a leader line 【Claude】
_LEADERS = frozenset(".…‧·．-_─—~*●•‥")
LEADER_RUN = 5
_HAN = re.compile(r"[\u3400-\u9fff]")


def join_single_lines(text: str) -> tuple[str, int, int]:
    """(text with its runs of one-character lines put right, marks dropped, lines joined away). A run is consecutive
    lines of one character each, blank lines between them aside. Its leader marks (_LEADERS) go when there are
    LEADER_RUN or more of them, or when it is three or more lines of nothing else: the dotted binding line down the
    left margin of an official letter (「…裝…訂…線…」), a poster's bullets. What is left becomes one line when it
    holds two or more Han characters: a label set vertically in a chart or a form, which the converter gives one
    character a line and in the right order (「進 / 階 / 英 / 文 / 2 / 學 / 分」). Any other run stays as it was (page
    numbers, the options A to D), and so does every other line: table rows, headings and list items are never one
    character. Text without such a run comes back as it was.
    Why here and not in the conversion: the characters are all there and in order, so nothing needs reading again
    (the conversion's cache holds weeks of work). On the 111 sites of 11-16, 1,031 attachments were more than a third
    one-character lines; with this, 24 (posters and exam sheets, whose pictures the OCR read); 154,112 marks dropped
    and 66,557 lines joined away in 3,984 documents."""
    lines = text.split("\n")
    out: list[str] = []
    dropped = merged = 0
    i = 0
    while i < len(lines):
        if len(lines[i].strip()) != 1:
            out.append(lines[i])
            i += 1
            continue
        chars, last, j = [], i, i
        while j < len(lines) and len(one := lines[j].strip()) <= 1:
            if one:
                chars.append(one)
                last = j
            j += 1
        marks = sum(ch in _LEADERS for ch in chars)
        keep = [ch for ch in chars if ch not in _LEADERS] if marks >= LEADER_RUN or marks == len(chars) >= 3 else chars
        join = sum(1 for ch in keep if _HAN.match(ch)) >= 2
        if join:
            out.append("".join(keep))
            merged += len(keep) - 1
        elif len(keep) != len(chars):
            out.extend(part for ch in keep for part in (ch, ""))
            if keep:
                out.pop()
        else:
            out.extend(lines[i : last + 1])
        dropped += len(chars) - len(keep)
        i = last + 1
    if not dropped and not merged:
        return text, 0, 0
    return _BLANK_LINES.sub("\n\n", "\n".join(out)).strip(), dropped, merged


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _link_text(s: str) -> str:
    """One line, without the file size the site writes at the end of each link's text (fix round 2, 11-01); nothing
    else changes."""
    parts = (_FILE_SIZE.sub("", p) for p in _one_line(s).split(LINK_SEP))
    return LINK_SEP.join(p for p in parts if p)


def _categories(value: str) -> tuple[str, ...]:
    """Document.categories of a record's 列表上的类别 (Record.list_category): split at CATEGORY_SEP, each on one line,
    blank ones dropped, each once in its first place."""
    return tuple(dict.fromkeys(c for c in map(_one_line, value.split(CATEGORY_SEP)) if c))


def kind_class(kind: str) -> str | None:
    """notice, page, attachment or link for a record kind (pagetext.page_kind's for a page), None for a left-out one
    (plan 2026-11-08 T3): notice for c.nknu's notice templates (newsview too, the template of 总务处, 体育室 and 师培处)
    and every profiled platform's detail page (detail:<profile>, detail:<profile>#按钮); left out a list read from its
    page and a list's pager row (list:<profile>, …#翻页), c.nknu's list names (EXCLUDED_KINDS) and an image only menus
    carry (MENU_IMAGE); attachment for 附件…; link for 外站:<host>; page for the rest (page:<profile>, page, c.nknu's
    other names)."""
    if kind in EXCLUDED_KINDS or kind == MENU_IMAGE or kind.startswith(LIST) or kind.endswith(PAGER):
        return None
    if kind in NOTICE_KINDS or kind.startswith(DETAIL):
        return "notice"
    if kind.startswith("附件"):
        return "attachment"
    if kind.startswith("外站"):
        return "link"
    return "page"


def _stated(r: Record) -> tuple[date, str, str] | None:
    """(the date the export read on the record's page, 网页上写的日期, with the words it read it from, 日期原文, and the
    source it is shown with) when that is the page's own date (plan 2026-11-08 T3), else None, rather none than a
    wrong one:
    - read in the platform's own date field (日期来源 OWN_FIELD: source FIELD_SOURCE) or on the row of a list linking
      the page (LIST_ROW: LIST_SOURCE), whatever its words (the bare 「2024-05-31」 of a date field): the crawler's
      platform rule chose it (sub-step 4, spec 2026-11-08 §二 2). Since the crawler's 53cf31b (blueprint item 22, part
      2) LIST_ROW is the row of any list, this site's or another site's, that links any page, not only a notice's row
      on its list (sso.nknu.edu.tw/services's 11,186 activity pages take the dates of the 25 unit sites' 書表 lists
      linking them; a c.nknu row, with no date column, gives its first western date); it stays trusted for every page
      and notice as before 【Claude】: between the exports before and after 53cf31b, 12,287 pages and 13 notices are
      newly dated and none loses or changes its date (research/pipeline_2026-11-02/reading_gaps_1109/lists_1109g.log);
      where a page had a date of its words too, 1,367 of 1,408 agree to the day and the 31 more than 7 days off are
      nearly all album pages the build leaves out (research/crawler_lab_2026-11-04/item22_1109/lists.md §2.2); two
      pages that are no notice take another site's notice date (sso.nknu.edu.tw/web/596's default.aspx u18326,
      sso.nknu.edu.tw/stu's 18weekssyllabusforenro.aspx), accepted. Any other date (PAGE_WORDS, or '' from an export
      made before 3c33a79) only after a label of putting the page up or changing it (_OWN_LABEL: 「更新時間：
      2025.9.17」, never 「日期113.4.24」 cut from 「校慶日期113.4.24」) 【Claude】, source PAGE_SOURCE;
    and, for all of them: a plausible date (corpus.plausible) no later than the day the page was fetched (a later one
    is a day the page names ahead, a deadline: 「榜單公告115年12月30日」 【Claude】); and not on a c.nknu list page
    (default.aspx, download.aspx, laws.aspx: the profile's list paths), whose date is one of its rows' (教务处's home
    pages show their first notice's, 「公告 2026.10.21」) 【Claude】, nor on a site's root address (site_root: the home
    page there, on any platform, shows its first notice's too; blueprint item 22, owner 11-09 「照这个做」: 13 sites'
    roots took it on 11-09; the rule's form 【Claude】)."""
    raw = " ".join(r.page_date_raw.split())
    source = _TRUSTED.get(r.date_source)
    if source is None:
        if not _OWN_LABEL.match(raw):
            return None
        source = PAGE_SOURCE
    try:
        day = date.fromisoformat(r.page_date) if r.page_date else None
    except ValueError:
        return None
    if day is None or not plausible(day):
        return None
    if (fetched := taipei_day(r.fetched)) is not None and day > fetched:
        return None
    if profiles.CNKNU.covers(scope.site_key(r.url)) and profiles.page_kind(r.url) == "list":
        return None
    if site_root(r.url, r.site):
        return None
    return day, raw, source


def site_root(url: str, site: str) -> bool:
    """Whether url is the root address of site (a site key): https://c.nknu.edu.tw/tcsl, …/tcsl/,
    https://clcts.nknu.edu.tw/ (the site's home page; its fragment aside, a pager row #第2页 too), its host compared
    as scope.site_key reads it (no port). Never with a query: /?p=news&id=1 is a page a site serves at its root path
    (the round-2 sites gauss, ite, ord; 11-09 review). A short link or another site's page in the export is none (its
    address is not the site key) 【Claude】."""
    try:
        parts = urlsplit(url.partition("#")[0])
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return bool(site) and not parts.query and (host + parts.path).rstrip("/").lower() == site.lower()


def _class(r: Record) -> str | None:
    """A record's document class: None for a page the crawler marks 列表翻页 (Record.list_kind LIST_PAGER, whatever its
    kind: blueprint item 22, part 3, the module docstring) and for one the school answered with no data
    (Record.area_mark NO_DATA, the crawler's fa81f41: the module docstring); else kind_class of its kind, except that a
    page the export read the platform's own date field on (日期来源 OWN_FIELD) is a notice: a detail page whose address
    no detail path names (GEO, ID, WordPress profiles: 2,253 pages on 11-09), dated by that field (sub-step 4, spec
    2026-11-08 §四) 【Claude】. Its kind_raw stays. One helper for both places that ask (the notices kept at one
    address, and the documents), so they never disagree."""
    # GONE: any row, a page or a file (11-16); IN_BUTTONS: a page whose content is its button rows' (11-19)
    if r.list_kind == LIST_PAGER or r.area_mark in (NO_DATA, GONE, MOVED, IN_BUTTONS):
        return None
    cls = kind_class(r.kind)
    return "notice" if cls == "page" and r.date_source == OWN_FIELD else cls


def _left_out_as(r: Record) -> str:
    """The key build_documents counts a record _class leaves out under: <kind>(学校回答没有资料) for a page the school
    answered with no data (NO_DATA_SUFFIX), a list's pager page with that mark too 【Claude】, and <kind>(列表翻页) for
    any other list's pager page (PAGER_SUFFIX), when its kind alone would keep it; else its kind, as before."""
    if kind_class(r.kind) is None:
        return r.kind
    if r.area_mark == GONE:
        return r.kind + GONE_SUFFIX
    if r.area_mark == MOVED:
        return r.kind + MOVED_SUFFIX
    if r.area_mark == IN_BUTTONS:
        return r.kind + IN_BUTTONS_SUFFIX
    if r.area_mark == NO_DATA:
        return r.kind + NO_DATA_SUFFIX
    return r.kind + PAGER_SUFFIX if r.list_kind == LIST_PAGER else r.kind


def deleted_notice(r: Record) -> bool:
    """A notice the school deleted (owner 11-09, blueprint item 15, 「照建议定」), by the export's mark on its page
    (Record.area_mark), whatever its class (a record the build leaves out by its kind, by the 列表翻页 mark or as the
    school's answer of no data aside: _class; blueprint item 22, so sites.deleted_notices never counts a list's pager
    page):
    - AREA_EMPTY: the platform's own title and text areas are on the page and hold nothing (no words, picture, link,
      frame or file): one, whatever the content side read there (a platform read as a whole body gives its menus:
      电机系's 3 of 11-09, page:elec, whose bodies hold 887 to 891 characters of menu);
    - AREA_MISSING: neither area is on the page: one only when the to-text step read no text either, since a template
      the export's profile does not know yet looks the same (the 11-08 export marked 156 real tecs notices so, before
      3c33a79 gave CNKNU tecs's newsview areas; on 11-09 it marks tecs's &amp;-spelled address, which shows a list).
    review 11-09 【Claude】."""
    if _class(r) is None or r.area_mark not in AREA_MARKS:
        return False
    return r.area_mark == AREA_EMPTY or not clean_text(r.text)


def _server_day(r: Record) -> date | None:
    """The Asia/Taipei day of the record's 最后修改时间 (Record.modified; spec 2026-11-08 §二 3); None when it is blank or
    unreadable, outside dates.kept_range(), or at most CLOCK_SECONDS before its 抓取时间 or after it (the server's
    clock answering a page made on the fly, or running fast; not a file's time) 【Claude】. Without an 抓取时间 (a
    corpus assembled before plan 2026-11-08 T3) there is nothing to compare it with."""
    day = taipei_day(r.modified) if r.modified else None
    if day is None:
        return None
    earliest, latest = kept_range()
    if not earliest <= day <= latest:
        return None
    fetched = fetched_at(r.fetched)
    if fetched != float("-inf") and fetched_at(r.modified) >= fetched - CLOCK_SECONDS:
        return None
    return day


def _param(url: str, name: str) -> str | None:
    values = parse_qs(urlsplit(html.unescape(url)).query).get(name)
    return values[0] if values else None


def _gi(url: str) -> tuple[str, str] | None:
    """(the URL's site, its gi), the key a gi's group is voted by (plan 2026-11-08 T1); None without a gi."""
    gi = _param(url, "gi")
    return (scope.site_key(html.unescape(url)), gi) if gi else None


def _link_key(url: str) -> str:
    """The identity of an address (ids.location_key, plan 2026-11-08 T1): a c.nknu notice is one page per site and
    nid whatever its gi or &amp; spelling (aca's nid 578 is not ccee's), http and https are one; links.page_links
    spells its targets with it, so they compare with a manifest 网址 key for key."""
    return ids.location_key(url)


def _joined(keep: Record, others: list[Record]) -> Record:
    """keep with what the other records of its identity say of it: their pages (a page some record links from its text
    or attachment area is a text reference, else a menu one), the best zone of each page (ZONE_ORDER), the first
    context and row each page gives, their link texts and their formats; and the categories of their list rows
    (list_category: keep's own first, then each other one once; blueprint item 22, review 11-09: without this, 66 ids
    of the 11-09 exports lost every category, 57 SSO news notices whose news.nknu.edu.tw mirror, fetched later, is
    kept while the list at sso.nknu.edu.tw/news gave the category, and 9 records whose &amp; spelling, the one a list
    links, gives way to the plain one: research/pipeline_2026-11-02/reading_gaps_1109/review_1109k/lab_cat_loss.out);
    and the publishing unit its page states (Record.publisher, 11-15), keep's own, else the first other's."""
    linked, nav = list(keep.linked), list(keep.nav_linked)
    zones, contexts, rows = dict(keep.zones), dict(keep.contexts), dict(keep.rows)
    texts, formats = [t for t in keep.linktext.split(LINK_SEP) if t], list(keep.formats)
    categories = list(_categories(keep.list_category))
    for other in others:
        for u in other.linked:
            if u not in linked:
                linked.append(u)
        for u in other.nav_linked:
            if u not in nav:
                nav.append(u)
        for u, zone in other.zones.items():
            if ZONE_ORDER.get(zone, 9) < ZONE_ORDER.get(zones.get(u, ""), 9):
                zones[u] = zone
        for mine, theirs in ((contexts, other.contexts), (rows, other.rows)):
            for u, value in theirs.items():
                mine.setdefault(u, value)
        texts += [t for t in other.linktext.split(LINK_SEP) if t and t not in texts]
        formats += [f for f in other.formats if f.get("id") not in {g.get("id") for g in formats}]
        categories += [c for c in _categories(other.list_category) if c not in categories]
    # the publishing unit the page states (11-15): one page whatever export brought it, so the first that read it
    publisher = keep.publisher or next((o.publisher for o in others if o.publisher), "")
    return dataclasses.replace(keep, linked=tuple(linked), nav_linked=tuple(u for u in nav if u not in linked),
                               zones=zones, contexts=contexts, rows=rows, linktext=LINK_SEP.join(texts),
                               formats=tuple(formats), list_category=CATEGORY_SEP.join(categories),
                               publisher=publisher)


def fetched_at(stamp: str) -> float:
    """An 抓取时间 (ISO date-time) as a POSIX timestamp, a naive one read as Asia/Taipei time 【Claude】; -inf when it is
    missing or none (a corpus assembled before plan 2026-11-08 T3, a hand-made event): the oldest."""
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return float("-inf")
    return (when if when.tzinfo is not None else when.replace(tzinfo=TAIPEI)).timestamp()


def home_rank(url: str, site: str) -> int:
    """How far the export of site is from being the home of what is at url, the lower the nearer (plan 2026-11-08 T4
    review) 【Claude】:
    - 0: the export of the site its own address belongs to (scope.site_key, as sites.unit_for reads it), whose 资料截至
      then dates it, when that site is no shared platform;
    - 1: any other export, the one that brought it (教务处's notice files on the announcement platform);
    - 2: a shared platform's own export (sites: shared, the announcement system and SSO's common parts). Its record
      would take the platform's placeholder unit (秘書室, or the school, which gives no unit words) and lose the 組別
      and the 资料截至 of the unit that brought it, against T3's rule (sites.unit_for: what another site's export
      brought from a shared platform is that site's unit's).
    one_per_id asks it of the records of one identity (_rank); since plan 2026-11-14 T2 the build asks it of the
    documents of one text dated the same day (build._merge_copies: 「同一天照现有的 _rank」), so a unit's own notice
    stays before the announcement platform's copy of it."""
    if not site or scope.site_key(url) != site:
        return 1
    row = sites.table().rows.get(site)
    return 2 if row is not None and row.shared else 0


def _rank(r: Record) -> int:
    """Which export's record of one identity stays on a tie of 抓取时间 (one_per_id; plan 2026-11-08 T4 review)
    【Claude】, the lower first: home_rank of its address and its export's site."""
    return home_rank(r.url, r.site)


def one_per_id(records: list[Record]) -> tuple[list[Record], dict[str, int]]:
    """One record per id (plan 2026-11-08 T1): an id is its record's identity, so rows of one identity (an &amp;
    spelling, http and https, the SSO news mirror, a file two sites' exports share) are one document. One stays, in its
    own place, joined with what the others say (_joined); the others are counted as <kind>(重复). A corpus with ids
    of its own (the 10-29 one) has none of them. Which one stays (plan 2026-11-08 T4: across the build's inputs, the
    newest 抓取时间), in this order 【Claude】:
    - one written without &amp; (T1's rule: the address shown; 教务处's export has 4 such pairs, the &amp; one fetched
      later in each, and keeps its plain spellings as before);
    - the newest 抓取时间 (fetched_at: a record without one is the oldest);
    - on a tie (one archive's row exported by two sites: the lab PC's normal case), _rank: its own site's export, then
      the export that brought it, a shared platform's own export last (T4 review: 教务处's 35 files on the
      announcement platform stay 教務處's, with their 組別, once the platform's export is in the build too);
    - the first in the records' order (the inputs' order, then corpus order: T1's rule).
    A newer row of a shared platform's own export still wins, with the platform's unit (exports of one archive at one
    time never differ in 抓取时间; exports made on different days only come with the daily updates, deferred)."""
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(r.id, []).append(r)
    if len(groups) == len(records):
        return records, {}
    place = {id(r): i for i, r in enumerate(records)}
    keep = {key: min(group, key=lambda g: ("&amp;" in g.url, -fetched_at(g.fetched), _rank(g), place[id(g)]))
            for key, group in groups.items() if len(group) > 1}
    excluded: Counter[str] = Counter()
    out = []
    for r in records:
        kept = keep.get(r.id, r)
        if r is kept:
            out.append(_joined(r, [g for g in groups[r.id] if g is not r]) if r.id in keep else r)
        else:
            excluded[r.kind + DUPLICATE] += 1
    return out, dict(excluded)


def one_event_per_id(events: list[Event]) -> tuple[list[Event], dict[str, int]]:
    """One event per id (plan 2026-11-08 T4; spec 2026-11-08 附「行事历按事件去重」): every export holding one calendar
    (a site that shows 教务处's) brings each of its events. The one of the newest fetch of its calendar file stays
    (Event.fetched; fetched_at), the first in the events' order on a tie (one archive's row in two exports) 【Claude】;
    the others are counted as 行事历(重复). The kept ones keep their order."""
    best: dict[str, int] = {}
    for i, ev in enumerate(events):
        j = best.get(ev.id)
        if j is None or fetched_at(ev.fetched) > fetched_at(events[j].fetched):
            best[ev.id] = i
    kept = [ev for i, ev in enumerate(events) if best[ev.id] == i]
    left = len(events) - len(kept)
    return kept, ({EVENT_KIND + DUPLICATE: left} if left else {})


def one_event_per_series(events: list[Event]) -> tuple[list[Event], dict[str, int]]:
    """One event per recurring calendar event (plan 2026-11-14 T2; the module docstring). events: one per id
    (one_event_per_id). The instances of one series (Event.series) that would be documents (from CALENDAR_SINCE on,
    with a title: build_documents leaves the others out and counts them as before) are grouped by their title on one
    line; the title most of them have (on a tie the one whose first day is earliest, then by the title) names the
    series, and its instances, two or more, become one event in the place of the first of them:
    - id: the series id, so its document is 'e' + it; title: theirs; series: the same id;
    - start: the first day of the earliest instance; end_inclusive: the last day of any;
    - days: every instance's (first day, last day), by day (one per instance: two on one day are two);
    - url: the earliest instance's (each instance has its own htmlLink);
    - site and fetched: those of the instance of the newest fetch, the first in the events' order on a tie, as
      one_event_per_id keeps the newest (one calendar in two exports fetched at different times) 【Claude】.
    Left as they are: a single event; an instance whose title is not its series' (stats other_titles: an exception the
    unit renamed stays a document of its own, so no title is lost); the only instance of a series (nothing to merge);
    and every instance of a series whose id another event already has (stats id_taken: never seen, the API giving
    instances only; the build warns) 【Claude】.
    Returns the events and {'series': the series merged, 'instances': the instances folded into them, 'other_titles',
    'id_taken'}; the build counts instances - series as 行事历(重复系列)."""
    stats = {"series": 0, "instances": 0, "other_titles": 0, "id_taken": 0}
    groups: dict[str, dict[str, list[int]]] = {}  # series id -> title on one line -> the places of its instances
    for i, ev in enumerate(events):
        if ev.series and not ev.days and ev.start >= CALENDAR_SINCE and (title := _one_line(ev.title)):
            groups.setdefault(ev.series, {}).setdefault(title, []).append(i)
    taken = {ev.id for ev in events}
    merged: dict[int, Event] = {}  # the place of a series' first instance -> the series' one event
    gone: set[int] = set()
    for series, by_title in groups.items():
        title, places = min(by_title.items(),
                            key=lambda kv: (-len(kv[1]), min(events[i].start for i in kv[1]), kv[0]))
        if len(places) < 2:
            continue
        if series in taken:
            stats["id_taken"] += 1
            continue
        instances = sorted((events[i] for i in places), key=lambda e: (e.start, e.end_inclusive))
        newest = events[max(places, key=lambda i: (fetched_at(events[i].fetched), -i))]
        merged[places[0]] = dataclasses.replace(
            newest, id=series, title=title, start=instances[0].start,
            end_inclusive=max(e.end_inclusive for e in instances), url=instances[0].url,
            days=tuple((e.start, e.end_inclusive) for e in instances))
        gone.update(places[1:])
        stats["series"] += 1
        stats["instances"] += len(places)
        stats["other_titles"] += sum(len(p) for t, p in by_title.items() if t != title)
    if not merged:
        return events, stats
    return [merged.get(i, ev) for i, ev in enumerate(events) if i not in gone], stats


def series_text(title: str, days: Sequence[tuple[date, date]]) -> str:
    """The text of a recurring event's one document (plan 2026-11-14 T2: 「同一个 Google 重复事件展开出来的单次事件合成
    一份，日期全部列出来」; no day is dropped): the title, SERIES_NOTE with the number of instances, then one line per
    month an instance starts in, 「2026-09：07、14、21～22」: each instance by its first day, with 「～」 and its last day
    when it runs longer (the day alone in the same month, month-day in another month of the year, the whole date in
    another year), and 「（N 次）」 after days N instances share. The format 【Claude】: a weekly event of fourteen years
    is about 4,000 characters this way, each line far under chunks.MAX_CHARS, and no line starts like a numbered
    heading (numbering.marks)."""
    months: dict[tuple[int, int], list[str]] = {}
    for (start, end), n in sorted(Counter(days).items()):
        said = f"{start.day:02d}"
        if end != start:
            last = (f"{end.day:02d}" if (end.year, end.month) == (start.year, start.month)
                    else f"{end.month:02d}-{end.day:02d}" if end.year == start.year else end.isoformat())
            said += "～" + last
        months.setdefault((start.year, start.month), []).append(said + (f"（{n} 次）" if n > 1 else ""))
    lines = [f"{year}-{month:02d}：{'、'.join(said)}" for (year, month), said in months.items()]
    return "\n".join([title, SERIES_NOTE.format(n=len(days)), *lines])


_SERIES_HEAD = re.compile(r"【系統】共 \d+ 次，日期（年-月：日）：")  # SERIES_NOTE with any number
_SERIES_MONTH = re.compile(r"(\d{4})-(\d{2})：(.+)")
_SERIES_ITEM = re.compile(r"(\d{2})(?:～(?:(\d{4})-)?(?:(\d{2})-)?(\d{2}))?(?:（(\d+) 次）)?")


def series_days(text: str) -> list[tuple[date, date]]:
    """series_text read back (main session 11-14, after the review: respond stated one period from a series' first
    day to its last and called it 進行中 between two instances): each instance's (first day, last day), earliest
    first, an instance the text says N times N times. [] for any other text (a single event's is its title alone)
    and for a line that is no month of days."""
    lines = text.split("\n")
    if len(lines) < 3 or not _SERIES_HEAD.fullmatch(lines[1].strip()):
        return []
    days: list[tuple[date, date]] = []
    for line in lines[2:]:
        month = _SERIES_MONTH.fullmatch(line.strip())
        if month is None:
            continue
        year, mon = int(month.group(1)), int(month.group(2))
        for item in month.group(3).split("、"):
            m = _SERIES_ITEM.fullmatch(item)
            if m is None:
                continue
            try:
                start = date(year, mon, int(m.group(1)))
                end = start if m.group(4) is None else date(int(m.group(2) or year), int(m.group(3) or mon),
                                                            int(m.group(4)))
            except ValueError:
                continue
            days += [(start, end)] * int(m.group(5) or 1)
    return sorted(days)


def event_line(terms: Sequence[Term], start: date, end: date, times: int = 1) -> str:
    """A calendar event's date line (plan 2026-11-14 T3: 「事件给向量和重排读的字多一行：学期和日期，例如『115學年度第2學期
    2027-02-15～02-18』」), which the build stores in docs.date_line and embed.passage gives the reranker, and since
    the correction of 11-14 the reranker only (the embedder reads an event as before T3: nknu.content.embed has the
    measurement): the term as the school writes it and a query is rewritten (terms.school_spelling: 115學年度第2學期), then
    the event's first day, with 「～」 and its last day when it runs longer, both whole so that each reads alone
    【Claude: the plan's example cuts the second to 02-18】. terms: the terms the event falls in; of several (a
    recurring event's instances: build._Row.terms) the first and the last are written with 「～」 between them, none
    gives the days alone. times: how many times a merged recurring event occurs (Event.days), written 「（共 N 次）」
    after its first and last day as documents.SERIES_NOTE words it; not written for a single event. The format is
    【Claude】; the line is read by the reranker only, never shown to an agent, so like the unit line it has no
    【系統】 mark."""
    known = sorted({(t.ay, t.sem or 0) for t in terms})
    ends = list(dict.fromkeys((known[0], known[-1]))) if known else []
    term = "～".join(f"{ay}學年度第{sem}學期" if sem else f"{ay}學年度" for ay, sem in ends)
    days = start.isoformat() if end == start else f"{start.isoformat()}～{end.isoformat()}"
    return " ".join(part for part in (term, days + (f"（共 {times} 次）" if times > 1 else "")) if part)


def _context_title(named: list[tuple[date | None, str]]) -> str:
    """The name the latest-dated pages give (undated pages last); among pages of that date the most frequent, the
    first named on ties. named: (page date, name) of the pages that name the file."""
    if not named:
        return ""
    top = max(day or date.min for day, _ in named)
    return Counter(name for day, name in named if (day or date.min) == top).most_common(1)[0][0]


def _note(left: dict[str, str] | None, r: Record, key: str) -> None:
    """build_documents' left: the document id a left-out record would have had -> the key it is counted under."""
    if left is not None:
        left["d" + r.id] = key


def build_documents(records: list[Record], metas: dict[str, PageMeta], events: list[Event],
                    site: str = "", left: dict[str, str] | None = None) -> tuple[list[Document], dict[str, int]]:
    """Documents in corpus order, then events; and the count of left-out records by kind. left, when given, is filled
    with each left-out record's document id and the key it is counted under (spec 2026-11-17-ingest-guard-design §二:
    the build says of every document that left the library why it left). records: one per id
    (one_per_id); metas: the notice pages' header fields keyed by Record.id (corpus.read_metas). A page the crawler
    marks 列表翻页 (blueprint item 22) and one the school answered with no data (Record.area_mark NO_DATA, the
    crawler's fa81f41) are left out (_class) and counted as <kind>(列表翻页) and <kind>(学校回答没有资料) when the kind
    alone would keep them, one marked both as the latter (_left_out_as), else under the kind. A notice the school
    deleted (deleted_notice) is left out and counted as <kind>(学校删掉).
    Of the other notices at one address (_link_key: one site and nid), the first written without &amp; is kept; the
    others are (重复) and their doc_ids go into its duplicates. Each document kept from a record carries its categories
    (Document.categories). site: the export's site key, for a record or an event that names none of its own (a corpus
    assembled before plan 2026-11-08 T3, an event made by hand); with neither, the unit is the school's."""
    excluded: Counter[str] = Counter()
    keep: dict[str, str] = {}  # link key -> the notice kept: the first one whose URL is written without &amp;
    # a notice the school deleted is never the one kept for its address (owner 11-09, blueprint item 15)
    notices = (r for r in records if _class(r) == "notice" and not deleted_notice(r))
    for r in sorted(notices, key=lambda r: "&amp;" in r.url):
        keep.setdefault(_link_key(r.url), r.id)

    docs: list[Document] = []
    doc_of: dict[str, Document] = {}  # record id -> its document
    duplicate_of: dict[str, str] = {}  # record id of a (重复) notice -> the record id kept for it
    order = {r.id: i for i, r in enumerate(records)}
    linkable: dict[str, list[Record]] = {}  # link key -> every record at that address, kept or not
    for r in records:
        key = _link_key(r.url)
        linkable.setdefault(key, []).append(r)
        cls = _class(r)
        if cls is None:  # one a mark alone leaves out (its kind would keep it) is counted apart (_left_out_as)
            excluded[_left_out_as(r)] += 1
            _note(left, r, _left_out_as(r))
            continue
        if deleted_notice(r):  # before the duplicate check: a shell is counted as deleted, never as (重复)
            excluded[r.kind + DELETED] += 1
            _note(left, r, r.kind + DELETED)
            continue
        if cls == "notice" and keep.get(key, r.id) != r.id:
            excluded[r.kind + DUPLICATE] += 1
            _note(left, r, r.kind + DUPLICATE)
            duplicate_of[r.id] = keep[key]
            continue
        m = metas.get(r.id) if cls == "notice" else None
        title = _one_line(m.title if m and m.title else r.title)
        text, marks, joined = join_single_lines(clean_text(r.text))  # 11-16: runs of one-character lines put right
        if not title and not text:
            excluded[r.kind + BLANK] += 1
            _note(left, r, r.kind + BLANK)
            continue
        stated = _stated(r) if cls in ("notice", "page") else None
        added, added_raw, added_source = None, m.added_raw if m else "", ""
        if m and m.added:  # c.nknu's date field first: the export's dates misdated 16% of 教务处's notices (11-02)
            added, added_source = m.added, FIELD_SOURCE
        elif cls == "notice" and stated:
            added, added_raw, added_source = stated
        page_date, page_raw, page_source = stated if cls == "page" and stated else (None, "", "")
        # spec 2026-11-16 §二: a file's own list row, the one export rule trusted for it (never a page's words)
        row = _stated(r) if cls == "attachment" and r.date_source == LIST_ROW else None
        d = Document(doc_id="d" + r.id, kind_class=cls, kind_raw=r.kind, title=title, url=r.url,
                     # the 組別 the page's own reading gave, else the export's 组别 (the crawler's ef05cf1)
                     unit=sites.unit_for(r.url, r.site or site), grp=(m.group if m else "") or r.group, parent_id=None,
                     parent_title="", linktext=_link_text(r.linktext),
                     has_text=bool(text) and not r.kind.startswith(NO_TEXT_KINDS), text=text, added=added,
                     added_raw=added_raw, added_source=added_source, page_date=page_date, page_date_raw=page_raw,
                     page_date_source=page_source, row_date=row[0] if row else None,
                     row_date_raw=row[1] if row else "", lines_joined=joined, marks_dropped=marks,
                     server_date=_server_day(r), source=r.source, formats=r.formats,
                     site=r.site or site, categories=_categories(r.list_category),
                     # whatever kind its own address makes it: a notice, a page, or a short link another site
                     # writes for the platform's notice (50 of the 2,040 stated rows of 11-15: 外站:reurl.cc, a link)
                     stated_unit=r.publisher)
        docs.append(d)
        doc_of[r.id] = d
        for f in r.formats:
            excluded[(f.get("kind") or r.kind) + FORMAT] += 1
    for dup, kept in duplicate_of.items():
        if (d := doc_of.get(kept)) is not None:
            d.duplicates += ("d" + dup,)

    votes: dict[tuple[str, str], Counter[str]] = {}
    for d in docs:
        if d.kind_class == "notice" and d.grp and (gi := _gi(d.url)):
            votes.setdefault(gi, Counter())[d.grp] += 1
    gi_group = {gi: c.most_common(1)[0][0] for gi, c in votes.items()}
    for d in docs:
        if d.kind_class in ("page", "link"):
            d.grp = gi_group.get(_gi(d.url), "")

    def unit_of(lr: Record) -> str:
        """The unit of a linking page: its document's, else (a page left out: a list) the site table's."""
        return doc_of[lr.id].unit if lr.id in doc_of else sites.unit_for(lr.url, lr.site or site)

    def at(urls) -> list[Record]:
        """Every record at these addresses, kept or not, in corpus order."""
        return sorted({lr.id: lr for u in urls for lr in linkable.get(_link_key(u), [])}.values(),
                      key=lambda lr: order[lr.id])

    def latest(k: Document) -> tuple:
        """The order a file's linking pages are taken in (addendum 2026-11-02 B; 11-01: the latest): a notice's date,
        an undated notice before a page, the first in corpus order on ties."""
        return k.added or date.min, k.kind_class == "notice", -order[k.doc_id[1:]]

    # spec 2026-11-16 (nightly ingest) §五 and §十 item 4 【Claude, after the crawler session's answers of 11-16】: a
    # file goes with the pages the school deleted when every page that links it, from its text, its file area or its
    # menus, is such a page (GONE: left out above), so a deleted notice leaves no file behind without a notice. A file
    # another page still links stays, and so does one no page links (it was never a deleted page's alone). The
    # crawler marks a file only when the file itself is found gone, and keeps a deleted page among the pages linking
    # its files (its export batch of 11-17), which is what this reads.
    orphaned = set()
    for r in records:
        d = doc_of.get(r.id)
        if d is None or d.kind_class != "attachment":
            continue
        linking = at([*r.linked, *r.nav_linked])
        if linking and all(lr.area_mark == GONE for lr in linking):
            orphaned.add(r.id)
            excluded[r.kind + GONE_FILE_SUFFIX] += 1
            _note(left, r, r.kind + GONE_FILE_SUFFIX)
    if orphaned:
        docs = [d for d in docs if d.doc_id[1:] not in orphaned]
        for rid in orphaned:
            del doc_of[rid]

    for r in records:
        d = doc_of.get(r.id)
        if d is None or d.kind_class != "attachment":
            continue
        linking = at(r.linked)
        kept = [doc_of[lr.id] for lr in linking if lr.id in doc_of]
        d.linked_from = tuple(k.doc_id for k in kept)
        pages = [k for k in kept if k.kind_class in ("notice", "page")]
        parent = max(pages, key=latest, default=None)
        if parent is not None:
            d.parent_id, d.parent_title = parent.doc_id, parent.title
        else:
            d.parent_title = next((_one_line(lr.title) for lr in linking if lr.title.strip()), "")
        if own_gi := gi_group.get(_gi(r.url), ""):
            d.grp = own_gi
        elif parent is not None and parent.unit == d.unit:
            d.grp = parent.grp
        else:  # no parent, or another unit's (another site's notice linking this file): its own unit's pages only
            mine = max((k for k in pages if k.unit == d.unit and k.grp), key=latest, default=None)
            d.grp = mine.grp if mine is not None else next(
                (gi_group[g] for lr in [*linking, *at(r.nav_linked)]
                 if (g := _gi(lr.url)) in gi_group and unit_of(lr) == d.unit), "")
        named = []  # (the page's date, what it calls the file): pages linking it from their text, never menus
        for u in r.linked:
            if name := _one_line(r.contexts.get(u, "")):
                days = [doc_of[lr.id].added for lr in linkable.get(_link_key(u), []) if lr.id in doc_of]
                named.append((max((x for x in days if x), default=None), name))
        if (title := _context_title(named)) and title != d.title:
            d.file_title, d.title, d.title_from_context = d.title, title, True
            if d.file_title not in d.linktext.split(LINK_SEP):
                d.linktext = LINK_SEP.join(p for p in (d.linktext, d.file_title) if p)
        refs: dict[str, tuple[str, str]] = {}  # doc_id -> (zone, context): the best zone a page shows it in
        for u, zone in [(u, r.zones.get(u) or UNKNOWN_ZONE) for u in r.linked] + [(u, "nav") for u in r.nav_linked]:
            for lr in linkable.get(_link_key(u), []):
                k = doc_of.get(lr.id)
                if k is None or k is d:
                    continue
                if k.doc_id not in refs or ZONE_ORDER.get(zone, 9) < ZONE_ORDER.get(refs[k.doc_id][0], 9):
                    refs[k.doc_id] = (zone, _one_line(r.contexts.get(u, "")))
        d.refs = tuple((doc_id, zone, context) for doc_id, (zone, context) in refs.items())

    for ev in events:
        if ev.start < CALENDAR_SINCE:
            excluded[EVENT_KIND + BEFORE] += 1
            continue
        title = _one_line(ev.title)
        if not title:
            excluded[EVENT_KIND + BLANK] += 1
            continue
        # an event's whole text is its title: has_text, so it is shown as text and never as a scanned file; its unit is
        # its export's site's (plan 2026-11-08 T3). The one event of a recurring series (one_event_per_series, plan
        # 2026-11-14 T2: its id is the series id) lists every instance's days after the title
        docs.append(Document(doc_id="e" + ev.id, kind_class="event", kind_raw=EVENT_KIND, title=title, url=ev.url,
                             unit=sites.unit_of_site(ev.site or site), grp="", parent_id=None, parent_title="",
                             linktext="", has_text=True, text=series_text(title, ev.days) if ev.days else title,
                             site=ev.site or site))
    return docs, dict(excluded)


# ---------------------------------------------------------------- a document's own title (plan 2026-11-14 T4)

def text_title(text: str) -> str:
    """The title a text gives itself (plan 2026-11-14 T4: 「正文第一个标题，没有就用正文第一行」): its first Markdown
    heading (a line the conversion wrote with #s, told as the sections tell one: numbering.strip_decor) when that is
    one of its first HEAD_LINES lines with text 【Claude: HEAD_LINES has the count】, else its first line with text;
    without its Markdown decoration, on one line. '' for a text with no line of text. A page's text has no Markdown
    heading (pagetext writes h1 to h6 as plain lines), so a page gives its first line."""
    first, seen = "", 0
    for line in _LINE.finditer(text):
        clean, hashes = strip_decor(line.group())
        if not clean:
            continue
        if hashes:
            return _one_line(clean)
        first, seen = first or clean, seen + 1
        if seen >= HEAD_LINES:
            break
    return _one_line(first)


def _bare_key(s: str) -> str:
    """s as its Han characters and digits alone, of norm_text (NFKC, lowercase, Simplified): what a line that says
    nothing is told by, whatever brackets, spaces, colons or English it carries."""
    return _NOT_HAN_OR_DIGIT.sub("", norm_text(s))


def title_flaw(candidate: str, old: str = "", names: Sequence[str] = ()) -> str:
    """Why a line is no title for a document (plan 2026-11-14 T4: 「太短（少于 2 个字）、只是『附件一』『說明』『注意事項』这类
    字眼、或和旧标题一样，就不换」), '' when it is one. candidate: text_title's line; old: the title the document has;
    names: names that say nothing of this document (the school's, its unit's). The first that holds:
    - 'blank': no line;
    - 'short': fewer than TITLE_MIN_CHARS letters or Han characters (the plan's two characters, so counted 【Claude】:
      a date, a number or a rule of dashes is no title either);
    - 'long': more than TITLE_MAX_CHARS characters 【Claude】;
    - 'nothing': one of _NOTHING's words alone or numbered, or names and nothing more (_bare_key of each; fewer than
      TITLE_MIN_CHARS characters are left without them) 【Claude: the list; since the review of 11-14 any run of the
      names, not one name alone: 「國立高雄師範大學語文教學中心」 and the school's name twice were taken as titles】;
    - 'size' 【Claude, after the first rebuild of 11-14: 9 attachments of the 111 sites took such a line】: it holds a
      file size, a number followed by a unit of bytes (_SIZE: 「學生轉系辦法 pdf 146 KB」, 「pdf 23kb」), the link text
      of a download list, whatever else it says: a line cut at the size would be a guess at where the name ends;
    - 'no_title' 【Claude, from the classes that came out wrong on 教务处's files】: a Markdown table's row, a field's
      label (it ends with a colon: 檔號：, 授課科目：) or a numbered part (numbering.marks: 一、依據, 第十四條, 1., Q1：),
      which heads a part of a document, never the document;
    - 'same': the old title.
    A line of words that passes may still be a poor title (a slogan, an English logo read by OCR): the plan leaves
    such documents to the count the build reports (「规则取不好的那一小部分，这一轮就留旧标题」 is for the classes above)."""
    if not candidate:
        return "blank"
    if len(_LETTER.findall(candidate)) < TITLE_MIN_CHARS:
        return "short"
    if len(candidate) > TITLE_MAX_CHARS:
        return "long"
    key = rest = _bare_key(candidate)
    said = sorted({_bare_key(name) for name in names if name} - {""}, key=len, reverse=True)
    for name in said:
        rest = rest.replace(name, "")
    if key and (_NOTHING.fullmatch(key) or (rest != key and len(rest) < TITLE_MIN_CHARS)):
        return "nothing"
    if _SIZE.search(norm_text(candidate)):
        return "size"
    if candidate.startswith("|") or candidate.endswith(_LABEL_END) or marks(candidate):
        return "no_title"
    return "same" if candidate == old else ""


def title_unread(d: Document) -> str:
    """Why a document's text gives it no title whatever its first line says, '' when it may (plan 2026-11-14 T4)
    【Claude】: 'blank', it has no text; 'image', its text is what OCR read on a picture (IMAGE_KIND), top line first:
    a masthead, a logo or a slogan more often than a title (the first lines of 教务处's 848 file-name pictures on
    11-14: 專題報導 55, 第1版 國內郵資已付 22, 人物特寫 20, nknu 14 …, and misread characters); 'scan', its words are
    not the document (has_text false: a scan with a few words)."""
    if not d.text.strip():
        return "blank"
    if d.kind_raw.startswith(IMAGE_KIND):
        return "image"
    return "" if d.has_text else "scan"


def _spread(items: list, n: int) -> list:
    """items, or n of them at even steps from the first when there are more."""
    return items if len(items) <= n else [items[i * len(items) // n] for i in range(n)]


def _labels(docs: list[Document], on: bool) -> dict:
    """own_titles' TITLE_LABEL part (11-15; the owner: 「请看一下附件标题取自链接文字还是档名的规则对不对，能修的话在这轮
    重建前一起修掉」) 【Claude】. An attachment is titled by what its page calls it, its file name otherwise (addendum
    2026-11-02 B), which is right where the page names the file and the file name does not (5,188 of the 10,377
    attachments the 111 sites title by a page are named 「file1_7149.doc」 and the like) and wrong where the page's
    word is a label many files stand under: 「檔案連結」 (395 files of one site), 「公文」 (209), 「第一頁」,
    「114年」, while the file names say what each one is (「中國青年救國團函.pdf」;
    research/pipeline_2026-11-02/fixes_1115/title_labels_1115.json). So: a title SAME_TITLE_MIN or more attachments
    of one site take from their pages, to the character, is no name (the count TITLE_SHARED tells a page's title that
    says nothing by), and each of them whose file name, its extension aside, has a Han character is titled by that
    file name again; the label goes to old_title and stays in linktext, title_was_label is set, and title_from_context
    is cleared. One whose file name has no Han character keeps the label (why 'no_name': 「相關檔案」 says more than
    「a09000000e_1150096406_senddoc1_att.pdf」). Returns {'titles': the labels (site and title), 'documents',
    'retitled', 'kept', 'why', 'by_site': {site: {'retitled', 'kept'}}, 'examples'}; on False changes nothing and
    still counts."""
    carried = Counter((d.site, d.title) for d in docs if d.kind_class == "attachment" and d.title_from_context)
    targets = [d for d in docs if d.kind_class == "attachment" and d.title_from_context
               and carried[(d.site, d.title)] >= SAME_TITLE_MIN]
    why: Counter[str] = Counter()
    by_site: dict[str, dict[str, int]] = {}
    retitled: list[dict] = []
    for d in targets if on else ():
        count = by_site.setdefault(d.site, {"retitled": 0, "kept": 0})
        if not _HAN.search(_EXTENSION.sub("", d.file_title)):
            why["no_name"] += 1
            count["kept"] += 1
            continue
        count["retitled"] += 1
        d.old_title, d.title, d.file_title = d.title, d.file_title, ""
        d.title_from_context, d.title_was_label = False, True
        if d.old_title not in d.linktext.split(LINK_SEP):
            d.linktext = LINK_SEP.join(part for part in (d.linktext, d.old_title) if part)
        retitled.append({"id": d.doc_id, "site": d.site, "old": d.old_title[:TITLE_SHOWN], "new": d.title})
    return {"titles": sum(n >= SAME_TITLE_MIN for n in carried.values()), "documents": len(targets),
            "retitled": len(retitled), "kept": len(targets) - len(retitled), "why": dict(why), "by_site": by_site,
            "examples": _spread(retitled, TITLE_EXAMPLES)}


def own_titles(docs: list[Document], school: str = "", labels: bool = False) -> dict:
    """Give each document whose title says nothing the title its own text gives (plan 2026-11-14 T4; the module
    docstring), in place, and report it. docs: the documents the build keeps, copies merged, so that 20 addresses of
    one page are one document 【Claude: the plan does not say when to count】. school: the school's name
    (sites.table().school), no title for any document. labels (11-15, the build's label_titles; off unless asked):
    first, an attachment whose page-given title is a label many files of its site carry takes its file name back
    (_labels: TITLE_LABEL in the report, with 'by_site' and 'examples' of its own). Off by default
    【Claude, after measuring it on the 111 sites of 11-15】: 894 files got a title that says what they are, and of the
    33 questions the answer to 「學號」 went from the 4th place to the 15th, eight of 師培處's certificate forms taking
    the places before it once their file names (「…教育學程生-學號-學系-姓名-證明書.docx」) were their titles
    (research/pipeline_2026-11-02/fixes_1115/explain_N02.txt); the owner has not seen it yet.

    Whose title says nothing:
    - TITLE_SHARED: a page or a notice whose title SAME_TITLE_MIN or more pages and notices of its site (Document.site)
      have, counted together, to the character; an empty title is a title;
    - TITLE_FILE: an attachment titled by its file name (title_from_context false) when that name, its extension
      aside, has no Han character.
    Each takes text_title of its text, unless its text gives none (title_unread), the line is no title (title_flaw,
    with the school's name and the document's unit as names), or SAME_TITLE_MIN or more documents of its site would
    then carry that title, those that have it already counted in ('shared': a site's header on every page's first
    line). The old title goes to old_title, for the term and the cohort it may name, and title_from_text is set. The
    link names do not take it: a title judged to say nothing, a site's name on every page or a file name of digits,
    would stay in what the index weighs as a title, three times a body word (1,831 documents of the 111 sites on
    11-16). Nothing else of the document changes here: the build reads again what a title gives (the term, the
    cohort, the doc type: build._retitle).

    Returns {'on': True, 'labels', 'same_min', 'max_chars', TITLE_SHARED: {'titles': the shared titles (site and
    title), 'documents', 'retitled', 'kept', 'why': {reason: documents kept for it}}, TITLE_FILE: the same without
    'titles', 'by_site': {site: {kind: {'retitled', 'kept'}}} (the sites and kinds with such documents), 'examples':
    {kind: up to TITLE_EXAMPLES of the retitled, spread over them: id, site, old, new}}, the plan's 「建库报告记换了几
    份、没换几份」 and what the direction's 「抽看几十份」 looks at."""
    labelled = _labels(docs, labels)
    same = Counter((d.site, d.title) for d in docs if d.kind_class in ("page", "notice"))
    targets: list[tuple[Document, str]] = []
    for d in docs:
        if d.kind_class in ("page", "notice"):
            if same[(d.site, d.title)] >= SAME_TITLE_MIN:
                targets.append((d, TITLE_SHARED))
        elif (d.kind_class == "attachment" and not d.title_from_context
              and not _HAN.search(_EXTENSION.sub("", d.title))):
            targets.append((d, TITLE_FILE))
    kinds = (TITLE_SHARED, TITLE_FILE)
    why: dict[str, Counter[str]] = {kind: Counter() for kind in kinds}
    found: list[tuple[Document, str, str]] = []  # (document, kind, the line that may be its title)
    for d, kind in targets:
        candidate = text_title(d.text)
        if flaw := title_unread(d) or title_flaw(candidate, d.title, (school, d.unit)):
            why[kind][flaw] += 1
        else:
            found.append((d, kind, candidate))
    taking = Counter((d.site, candidate) for d, _, candidate in found)
    chosen = {id(d) for d, _, _ in found}
    held = Counter((d.site, d.title) for d in docs if id(d) not in chosen)
    new: dict[int, str] = {}
    for d, kind, candidate in found:
        if taking[(d.site, candidate)] + held[(d.site, candidate)] >= SAME_TITLE_MIN:
            why[kind]["shared"] += 1
        else:
            new[id(d)] = candidate
    retitled: dict[str, list[dict]] = {kind: [] for kind in kinds}
    by_site: dict[str, dict[str, dict[str, int]]] = {}
    for d, kind in targets:
        count = by_site.setdefault(d.site, {}).setdefault(kind, {"retitled": 0, "kept": 0})
        if id(d) not in new:
            count["kept"] += 1
            continue
        count["retitled"] += 1
        d.old_title, d.title, d.title_from_text = d.title, new[id(d)], True
        retitled[kind].append({"id": d.doc_id, "site": d.site, "old": d.old_title[:TITLE_SHOWN], "new": d.title})

    def part(kind: str) -> dict:
        n = sum(k == kind for _, k in targets)
        return {"documents": n, "retitled": len(retitled[kind]), "kept": n - len(retitled[kind]),
                "why": dict(why[kind])}

    return {"on": True, "labels": labels, "same_min": SAME_TITLE_MIN, "max_chars": TITLE_MAX_CHARS,
            TITLE_SHARED: {"titles": sum(n >= SAME_TITLE_MIN for n in same.values()), **part(TITLE_SHARED)},
            TITLE_FILE: part(TITLE_FILE), TITLE_LABEL: labelled, "by_site": by_site,
            "examples": {kind: _spread(retitled[kind], TITLE_EXAMPLES) for kind in kinds}}
