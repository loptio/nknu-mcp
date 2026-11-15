"""Datasette plugin: why a document has no date (owner 11-15: 「我在数据总览看到无日期我就好奇想去看为什么，然后就需要去看
数据库」). Two SQL functions the canned queries of metadata.yaml call, so the reasons live in one place:

  undated_why(kind_class, kind_raw, url, parent_kind, menu_ref, parent_date, text, doc_type) -> a short code
  undated_say(code) -> the short name shown on the page (Traditional Chinese, Taiwan usage: owner 11-15); its
                      whole reason is TIP's, floating over it
  undated_do(code) -> what to do about it (不用管, 爬蟲可補, 改建庫規則可補, 要查; DO), who does what floating over it
  undated_rank(code) -> where it is listed (what can be done first)
  date_seen(text) -> the first date a page writes, for a page the build took no date of

The reasons follow the pipeline's date rules (CLAUDE.md 「现行配置」, the walkthrough's dates section): a notice takes
its platform date field; a page takes the platform's date field, the date its list row carries, or a date written
as 「發佈／公告／更新」; an attachment takes only the date of the notice it belongs to (a page's date is not passed to
the files it links), else the server's file time unless that is the moment of the fetch. The sorting of the cases
and the words are 【Claude】; what the library holds decides which case a document falls in, not a guess at the page.

Loaded by `datasette --plugins-dir`; tests/ops/test_deploy_files.py loads it the same way to run the queries."""

from __future__ import annotations

import itertools
import re

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


# the crawler's page kinds (docs.kind_raw) of pages about people: alumni, teachers, staff; schools write no date on them
PEOPLE = frozenset({"student_detail", "teachercorner", "teacher", "member"})
BUTTON = "#按钮="  # the crawler's mark on the address of a file a button gave (the crawler's words, as they are)
# A date as a page writes it, any form (the build takes only a labelled one, the platform's field or a list row's):
# 2024-01-29, 2024/1/29, 2024.01.29, 2024年1月, 113年1月29日, 113.1.29 (a year 50-139 of the ROC calendar, so a
# product code like 13.8/6.9 is not one). Each pattern is anchored by digits around it; no two \s* side by side.
_AD = (r"(?<!\d)(?:19|20)\d{2}\s*(?:[-/.]\s*(?:1[0-2]|0?[1-9])\s*[-/.]\s*(?:3[01]|[12]\d|0?[1-9])(?!\d)"
       r"|年\s*(?:1[0-2]|0?[1-9])\s*月)")
_ROC = r"(?<!\d)(?:1[0-3]\d|[5-9]\d)\s*(?:年\s*(?:1[0-2]|0?[1-9])\s*月|[./](?:1[0-2]|0?[1-9])[./](?:3[01]|[12]\d|0?[1-9])(?!\d))"
WRITTEN = re.compile(f"{_AD}|{_ROC}")
# a page that writes this many dates or more lists them (a list, a chronicle, a schedule, a CV): none of them is the
# page's own date 【Claude】 (on 11-15, of 2,750 pages that write a date the build did not take, 423 write ten or more)
MANY = 10

# one short name per case, shown in a query's 「原因」 column (owner 11-15: 「頁面廢話太多了」, then 「很多名字看着不直觉
# 然后感觉是想缩短文字 但又想保留细节导致不上不下的」): a plain name on the page, the whole reason in a sentence that
# floats over it (TIP; plugins/cells.py keeps the same pairs, tests/ops/test_deploy_files.py keeps them equal)
SAY = {
    "notice": "公告沒有日期",
    "people": "人物介紹頁",
    "journal": "學報校刊的頁面",
    "page-written": "網頁寫了日期沒認",
    "page-many": "網頁列了很多日期",
    "page": "網頁沒寫日期",
    "notice-parent": "所屬公告沒日期",
    "button": "按鈕下載的附件",
    "page-parent-dated": "網頁日期沒傳給附件",
    "page-parent": "掛在沒日期的網頁下",
    "menu": "只在選單出現的附件",
    "orphan": "沒有所屬文件的附件",
    "other": "不看日期的類型",
}
TIP = {
    "notice": "這些多半不是真的公告：打開是網站的首頁，學校那則公告已經不在了。",
    "people": "所友、師資這類人物介紹頁，學校本來就不寫日期。",
    "page-written": "頁面上寫了日期（例如「活動日期」，或列表裡一行行的日期），但建庫只認平台的日期欄、列表那一行帶的"
                    "日期，或「發佈／公告／更新」開頭的日期。清單的「頁面上的日期」一欄是它寫的第一個日期。",
    "journal": "舊校刊、校訊、學報的一頁（背景資料）。它的日期是那一期出刊的日子，頁面上通常沒寫；頁面裡的"
               "日期多半是文章講的事。要補的話，得從每一期的目錄拿出刊日期（爬蟲那邊）。",
    "page-many": "頁面上列了十個以上的日期（像列表、年表、行程或履歷），沒有哪一個是這一頁自己的日期。",
    "page": "頁面上找不到任何日期，學校本來就沒寫。",
    "notice-parent": "附件只跟所屬公告的日期，而那則公告自己也沒有日期。",
    "button": "按鈕按出來的檔，而它那一行沒寫日期（像活動頁上的報名表）。有寫日期的，11-16 起都跟那一行的日期了。",
    "page-parent-dated": "它掛的網頁有日期，但建庫的規則是一般網頁的日期不傳給它連到的檔（網頁的日期常是網頁自己更新的"
                         "日子，不一定是檔的）。",
    "page-parent": "它掛的網頁自己也沒有日期；伺服器也沒給可信的修改時間（沒給、給的只是下載當下的時間，或是同一個網站"
                   "同一天有二十個以上的檔共用的時間，那是搬家或整批複製的日子，不是檔自己的）。",
    "menu": "只有網站選單連到它（多半是表單、規章這類常設的檔），沒有所屬的公告或網頁可以跟；伺服器也沒給可信的修改時間"
            "（沒給，或是很多檔共用的同一天）。",
    "orphan": "沒有任何公告或網頁掛著它。多半是爬蟲從舊存檔或別處找到、學校現在已經沒有網頁連到的檔；也有一些是列表上的"
              "下載按鈕，而那一頁列表沒收進庫。",
    "other": "行事曆事件和校外連結不算在「沒日期」裡。",
}
# what to do about a case: its short word, shown beside the reason, and who does what, floating over it. The owner,
# 11-15, asked whether the panels answer what they come to them for (「目前你做的东西能回答到我去使用这个dashboard时想找
# 到的问题的答案吗？」); Claude's answer was that a reason said what kind, not whether it is normal, whether to act and who
# fixes it, and the owner: 「没日期那个先补」. The verdicts are 【Claude】's, from the library as it was on 11-15 (the
# evidence: the design doc 2026-11-15-inspect-tools section 十 item 16). 11-16: since the official library of 11-16
# (spec 2026-11-16-data-fixes §二, crawler f3e4278) a button's file takes its row's date; 4 of the 844 were left, on
# rows that write none, so 「button」 needs nothing more; a server time many files of a site share on one recent day is
# no file's time (date_spikes), said in the tips of the cases it falls in
DO = {
    "notice": "要查", "people": "不用管", "journal": "不用管", "page-written": "改建庫規則可補", "page-many": "不用管",
    "page": "不用管",
    "notice-parent": "要查",
    "button": "不用管", "page-parent-dated": "改建庫規則可補", "page-parent": "不用管", "menu": "不用管",
    "orphan": "要查", "other": "不用管",
}
DO_TIP = {
    "爬蟲可補": "日期在學校的網頁上有，只是爬蟲的匯出沒帶過來：爬蟲 session 補上，建庫（內容 session）再認它。",
    "改建庫規則可補": "日期在頁面上有，但建庫的規則不認或不傳。要不要改規則由你定；改的是建庫（內容 session）。",
    "要查": "多半不是正常的文件（打開是首頁，或學校已經沒有網頁連到它）。爬蟲 session 查過，再定收不收。",
    "不用管": "學校本來就沒寫，或沒有地方可以拿到日期。",
}
# the order the cases are listed in on a site's page: what can be done first, what needs nothing last
DO_ORDER = {"爬蟲可補": 0, "改建庫規則可補": 1, "要查": 2, "不用管": 3}


def undated_why(kind_class, kind_raw, url, parent_kind, menu_ref, parent_date=None, text=None, doc_type=None) -> str:
    """The case an undated document falls in (a key of SAY). parent_kind and parent_date are the kind_class and
    ident_date of its docs.parent_id (None when it has none); menu_ref is whether a menu (doc_refs.zone 'nav') links
    it; text is its docs.text (a page that writes a date somewhere is told from one that writes none); doc_type is its
    docs.doc_type (a journal's page: the dates in it are its articles', not the issue's)."""
    if kind_class == "notice":
        return "notice"
    if kind_class == "page":
        if kind_raw in PEOPLE:
            return "people"
        if doc_type == "journal":
            return "journal"
        written = sum(1 for _ in itertools.islice(WRITTEN.finditer(text), MANY)) if text else 0
        return "page-many" if written >= MANY else "page-written" if written else "page"
    if kind_class != "attachment":
        return "other"
    if parent_kind == "notice":
        return "notice-parent"
    if url and BUTTON in url:
        return "button"
    if parent_kind:
        return "page-parent-dated" if parent_date else "page-parent"
    return "menu" if menu_ref else "orphan"


def undated_say(code) -> str:
    return SAY.get(code, code or "")


def undated_do(code) -> str:
    """What to do about a case (DO), '' for a code there is none of."""
    return DO.get(code, "")


def undated_rank(code) -> int:
    """Where a case is listed: by what to do about it (DO_ORDER), then the caller's own order."""
    return DO_ORDER.get(DO.get(code, ""), 9)


def date_seen(text) -> str:
    """The first date a text writes, with a few characters before it, and how many it writes when more than one
    (「（14 個）…活動日期* | 2017-12-21」): what a page shows that the build did not take. '' when it writes none."""
    if not text:
        return ""
    found = list(WRITTEN.finditer(text))
    if not found:
        return ""
    first = found[0]
    before = " ".join(text[max(0, first.start() - 12):first.start()].split())
    seen = f"{before} {first.group(0)}".strip()
    return (f"（{len(found)} 個）" if len(found) > 1 else "") + seen


def register(conn) -> None:
    conn.create_function("undated_why", 8, undated_why, deterministic=True)
    conn.create_function("undated_say", 1, undated_say, deterministic=True)
    conn.create_function("undated_do", 1, undated_do, deterministic=True)
    conn.create_function("undated_rank", 1, undated_rank, deterministic=True)
    conn.create_function("date_seen", 1, date_seen, deterministic=True)


@hookimpl
def prepare_connection(conn):
    register(conn)
