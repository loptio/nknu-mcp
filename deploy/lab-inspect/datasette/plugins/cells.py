"""Datasette plugin: cells the owner can read (owner 11-15: 「目前数据库的界面是我完全看不懂」, then 「字太雜了…沒有用一些
視覺化方法點綴，顏色也沒有考慮過」). What a cell shows instead of the raw value, all 【Claude】:

- a link column of the canned queries (metadata.yaml builds them as text, 'http://127.0.0.1:…' || …) shows a short
  word, not the whole address;
- a value the library stores as a code shows its word as a small tag (the code floats over it, for a filter or a
  query of the owner's own); a document's kind takes the hue 資料總覽 gives that kind (deploy/lab-overview/
  page.html's --k0…--k4, the only hues of these pages);
- a document id is followed by a link that traces it in 檢視台; a site's code links its list of documents;
- a no-date reason (a query's 「原因」) shows its short name, its whole sentence floating over it (owner 11-15: 「很多
  名字看着不直觉 然后感觉是想缩短文字 但又想保留细节导致不上不下的」, and 资料总览's floating words as the way to give
  detail); what to do about it (「怎麼辦」) is a tag in the colour of its verdict, who does what floating over it (owner
  11-15: 「目前你做的东西能回答到我去使用这个dashboard时想找到的问题的答案吗？」, then 「没日期那个先补」);
- a count is printed with thousands separators, and a date stays on one line.

Loaded by `datasette --plugins-dir`; tests/ops/test_deploy_files.py loads it the same way."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlsplit

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


from markupsafe import Markup, escape

# a query's column name -> (the word shown, whether it opens in a new tab: the trace page is another service)
LINKS = {"追蹤": ("追蹤 ↗", True, "在檢視台的文件追蹤看這一份：從抓取到搜尋的每一步"),
         "看清單": ("看清單", False, "列出這個原因的每一份文件")}
SITE_COLUMN = "代號"  # sites_overview: the site's own code, linking its list of documents
OURS = ("http://127.0.0.1:18001/", "http://127.0.0.1:18878/")
TRACE = "http://127.0.0.1:18878/trace?ref="
# the same words as plugins/guide.py's WORDS (each file is loaded by Datasette alone and imports nothing of ours;
# tests/ops/test_deploy_files.py keeps the two equal)
WORDS = {
    "kind_class": {"notice": "公告", "page": "一般網頁", "attachment": "附件", "event": "行事曆", "link": "校外連結"},
    "tier": {"primary": "主料", "background": "背景"},
    "ident_kind": {"published": "公告日期", "inherited": "跟所屬公告", "server": "伺服器檔案時間"},
    "kind": {"published": "公告日期", "inherited": "跟所屬公告", "server": "伺服器檔案時間", "period_start": "期間開始",
             "period_end": "期間結束", "event_start": "事件開始", "event_end": "事件結束"},
    "doc_type": {"journal": "學報校刊", "exam": "考題", "minutes": "會議紀錄"},
    "why": {"copy": "別的網址的同一份", "format": "同一份的另一種格式", "duplicate": "重複的紀錄"},
    "relation": {"publisher": "誰發的", "applies_to": "適用於", "mentions": "提到"},
    "zone": {"content": "正文", "attachments": "附件區", "nav": "選單", "unknown": "找不到"},
    "text_source": {"totext": "這次轉的", "none": "沒有"},
}
# a kind's word (a raw table's code, or a canned query's own Chinese) -> the class that gives it its hue
KIND_CLASS = {"notice": "k0", "公告": "k0", "page": "k1", "一般網頁": "k1", "網頁": "k1", "attachment": "k2", "附件": "k2",
              "event": "k3", "行事曆": "k3", "link": "k4", "校外連結": "k4"}
KIND_COLUMNS = ("kind_class", "類型")
TIER_WORDS = {"主料": "", "背景": "bg", "primary": "", "background": "bg"}
TIER_COLUMNS = ("tier", "主料／背景")
ID_COLUMNS = ("doc_id", "parent_id", "ref_id", "alias")  # a document's id, or one merged into a document
# a no-date reason's short name -> its whole sentence (plugins/undated.py's SAY and TIP, paired; each file is loaded
# alone, tests/ops/test_deploy_files.py keeps the two equal): the name is shown, the sentence floats over it
REASON_COLUMN = "原因"
REASONS = {
    "公告沒有日期": "這些多半不是真的公告：打開是網站的首頁，學校那則公告已經不在了。",
    "人物介紹頁": "所友、師資這類人物介紹頁，學校本來就不寫日期。",
    "學報校刊的頁面": "舊校刊、校訊、學報的一頁（背景資料）。它的日期是那一期出刊的日子，頁面上通常沒寫；頁面裡的日期多半是文章講的事。要補的話，得從每一期的目錄拿出刊日期（爬蟲那邊）。",
    "網頁寫了日期沒認": "頁面上寫了日期（例如「活動日期」，或列表裡一行行的日期），但建庫只認平台的日期欄、列表那一行帶的日期，或「發佈／公告／更新」開頭的日期。清單的「頁面上的日期」一欄是它寫的第一個日期。",
    "網頁列了很多日期": "頁面上列了十個以上的日期（像列表、年表、行程或履歷），沒有哪一個是這一頁自己的日期。",
    "網頁沒寫日期": "頁面上找不到任何日期，學校本來就沒寫。",
    "所屬公告沒日期": "附件只跟所屬公告的日期，而那則公告自己也沒有日期。",
    "按鈕下載的附件": "按鈕按出來的檔，而它那一行沒寫日期（像活動頁上的報名表）。有寫日期的，11-16 起都跟那一行的日期了。",
    "網頁日期沒傳給附件": "它掛的網頁有日期，但建庫的規則是一般網頁的日期不傳給它連到的檔（網頁的日期常是網頁自己更新的日子，不一定是檔的）。",
    "掛在沒日期的網頁下": "它掛的網頁自己也沒有日期；伺服器也沒給可信的修改時間（沒給、給的只是下載當下的時間，或是同一個網站同一天有二十個以上的檔共用的時間，那是搬家或整批複製的日子，不是檔自己的）。",
    "只在選單出現的附件": "只有網站選單連到它（多半是表單、規章這類常設的檔），沒有所屬的公告或網頁可以跟；伺服器也沒給可信的修改時間（沒給，或是很多檔共用的同一天）。",
    "沒有所屬文件的附件": "沒有任何公告或網頁掛著它。多半是爬蟲從舊存檔或別處找到、學校現在已經沒有網頁連到的檔；也有一些是列表上的下載按鈕，而那一頁列表沒收進庫。",
    "不看日期的類型": "行事曆事件和校外連結不算在「沒日期」裡。",
}
# what to do about a reason (undated.py's DO_TIP, the same pairs): the word is a tag in the colour of its verdict
# (green: nothing to do; amber: a date that can be had; red: a document to look at), who does what floats over it
ACTION_COLUMN = "怎麼辦"
ACTIONS = {
    "爬蟲可補": ("fix", "日期在學校的網頁上有，只是爬蟲的匯出沒帶過來：爬蟲 session 補上，建庫（內容 session）再認它。"),
    "改建庫規則可補": ("fix", "日期在頁面上有，但建庫的規則不認或不傳。要不要改規則由你定；改的是建庫（內容 session）。"),
    "要查": ("look", "多半不是正常的文件（打開是首頁，或學校已經沒有網頁連到它）。爬蟲 session 查過，再定收不收。"),
    "不用管": ("ok", "學校本來就沒寫，或沒有地方可以拿到日期。"),
}
COUNT_COLUMNS = ("chars", "docs", "missing_pages", "missing_files", "deleted_notices", "under_read", "文件數", "字數",
                 "份數", "沒抓到的網頁", "沒抓到的檔案", "學校刪掉的", "沒日期", "沒讀到字", "讀到的日期數")


def short_link(value, column):
    """The cell's HTML when it is one of our links in a link column, else None (Datasette renders it as usual)."""
    if not isinstance(value, str) or not value.startswith(OURS) or any(c.isspace() for c in value):
        return None
    if column in LINKS:
        word, new_tab, tip = LINKS[column]
    elif column == SITE_COLUMN:
        word, new_tab, tip = (parse_qs(urlsplit(value).query).get("site") or [value])[0], False, "這個網站的文件"
    else:
        return None
    target = ' target="_blank" rel="noopener"' if new_tab else ""
    return Markup(f'<a class="go" href="{escape(value)}"{target} data-tip="{escape(tip)}">{escape(word)}</a>')


def _stored(value, word) -> str:
    """The floating word of a tag: the code the library stores, when it differs from the word shown (a filter of the
    owner's own is typed with the code)."""
    return f' data-tip="庫裡存成 {escape(value)}"' if value != word else ""


def tag(value, column):
    """The small tag of a coded value, else None."""
    if not isinstance(value, str) or not value:
        return None
    if column in KIND_COLUMNS and value in KIND_CLASS:
        word = WORDS["kind_class"].get(value, value)
        return Markup(f'<span class="tag kind {KIND_CLASS[value]}"{_stored(value, word)}><i></i>{escape(word)}</span>')
    if column in TIER_COLUMNS and value in TIER_WORDS:
        word = WORDS["tier"].get(value, value)
        extra = f" {TIER_WORDS[value]}" if TIER_WORDS[value] else ""
        return Markup(f'<span class="tag{extra}"{_stored(value, word)}>{escape(word)}</span>')
    word = WORDS.get(column, {}).get(value)
    if word is not None:
        return Markup(f'<span class="tag" data-tip="庫裡存成 {escape(value)}">{escape(word)}</span>')
    if column == REASON_COLUMN and value in REASONS:
        return Markup(f'<span data-tip="{escape(REASONS[value])}">{escape(value)}</span>')
    if column == ACTION_COLUMN and value in ACTIONS:
        tone, tip = ACTIONS[value]
        return Markup(f'<span class="tag do-{tone}" data-tip="{escape(tip)}">{escape(value)}</span>')
    return None


def doc_link(value, column):
    """A document id with the link that traces it, else None (an id has no blank and is not an address)."""
    if column not in ID_COLUMNS or not isinstance(value, str) or not value or len(value) > 64:
        return None
    if any(c.isspace() for c in value) or "/" in value:
        return None
    return Markup(f'<code class="id">{escape(value)}</code> <a class="go" href="{TRACE}{quote(value, safe="")}" '
                  f'target="_blank" rel="noopener" data-tip="{LINKS["追蹤"][2]}">追蹤 ↗</a>')


def site_link(value, column, table, database, datasette):
    """A site's code linking that site's list of documents (in the tables that store one), else None."""
    if column != "site" or table not in ("docs", "sites") or not isinstance(value, str) or not value or not database:
        return None
    try:
        base = datasette.urls.database(database)
    except Exception:  # noqa: BLE001 - a cell never fails the page: Datasette then renders the value as usual
        return None
    return Markup(f'<a href="{escape(base)}/site_docs?site={quote(value, safe="")}" data-tip="這個網站的文件">'
                  f"{escape(value)}</a>")


DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def day(value):
    """A date (2026-11-06) kept on one line: a narrow column would break it at a hyphen. Else None."""
    if isinstance(value, str) and DAY.fullmatch(value):
        return Markup(f'<span class="nw">{value}</span>')
    return None


def count(value, column):
    """A count with thousands separators, else None."""
    if column in COUNT_COLUMNS and isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 1000:
        return Markup(f"{value:,}")
    return None


@hookimpl
def render_cell(value, column, table, database, datasette):  # no defaults: pluggy fills only plain names
    for made in (short_link(value, column), tag(value, column), doc_link(value, column),
                 site_link(value, column, table, database, datasette), count(value, column), day(value)):
        if made is not None:
            return made
    return None
