"""Platform profiles (round-1 design §7.4, §7.8, §10.1): what the crawler knows of each school platform it walks, as
data, and the decisions it takes from it. Nothing here sends anything; postback.py presses what these say to press.

A profile holds, for the sites it covers (by site key, scope.site_key / url.owner_site):
- rows: the list grids and row sets a page may hold (Rows): where the rows are, what a row's title, date and category
  are, how the list is walked (postback presses of a pager, GET pager links, one GET with every row, or none: a
  detail page's attachment buttons), how to tell the page number shown, the 「共 N 筆」 it states, the words of an empty
  list, and the title area of a detail a row's press brings (the title check, §7.5 step 5);
- details: the detail pages (no cap), their body, attachments and title, what makes one empty (nonempty: at least a
  body text, an attachment, an image or a related link; §10.1) and the id probe (§10.1: an id that cannot exist);
- buttons: each kind of ASP.NET postback button: press (pager, detail, download), never (sign-in, captcha, a form
  sent, a search, a language switch), ignore (each with its reason);
- writings: the decision on a writing a crawler of plain GETs cannot follow (links.Unhandled, §7.8): ignore, never,
  no_content, round2, each with its reason; none written is unknown (要人看);
- clicks: the rules for a click that loads something whose writing the crawler can read (§7.4 clicks, record 二·3,
  the owner 10-25 22:19 「可以，照这样改」): oia's tabs (a GET per tab), ee's download buttons (a POST per file number);
- says: what the school's own page says of a missing file (gone_page, seen twice) or a broken link (bad_link) (§10.1,
  §10.2; review 10-26 B6).
- data_apis: a data address the platform's pages ask by script, which the crawler reads itself once per site with the
  request the page's own script sends (DataApi: cLv2's timeline, design §23 C: reading it in round 1 【用户同意 Claude 的建议】
  10-27, how 【Claude 的执行办法】); the
  browser check never counts it as a data address the round cannot read (known_data_api).

decision(site, res_key) is the one answer the crawler, rejudge and verify check 8 share (design §15): for a button's
key (postback:<control, ctlNN as ctl*>[|<argument>], with the button's text, page_res.text, where a rule names one:
SSO's bare ctlNN 「查詢」) press, never, ignore or unknown; for a click's key handle (a clicks rule presses it),
no_content, never, round2 or unknown; for any other writing ignore, never, no_content, round2 or unknown. A site's
own profile is asked first, then the decisions every site shares (GLOBAL: only what can be shown, §7.8: a browser's
own call in a javascript: link, a GET search form).

Which platform has which buttons was read from the 10-24 archive's own pages (read only) and the 10-25 click scan
(click_scan_2026-10-25.json: postback_uncovered, external_functions_summary, nav_computed_summary). Every rule here
that the design does not spell out is Claude's way of doing it (【Claude 的执行办法】); the owner may overturn any.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import scope

BUTTON_DECISIONS = ("press", "never", "ignore", "no_content", "round2")
WRITING_DECISIONS = ("ignore", "never", "no_content", "round2")
CLICK_DECISIONS = ("handle", "no_content", "never", "round2")
UNKNOWN = "unknown"
WALKS = ("postback", "get", "one_get", "none")
PAGER_HOWS = ("page_n", "next")
PRESS_ROLES = ("pager", "detail", "download")
GONE_PAGE, BAD_LINK = "gone_page", "bad_link"
# a file the school's page says is missing is fetched once more this much later; the same answer twice is gone (§10.2)
GONE_RECHECK_S = 3600.0
GONE_RECHECK = "gone_page:recheck"  # url.reason while the second look waits
_CTL = re.compile(r"(?i)ctl\d+")


@dataclass(frozen=True)
class Button:
    """One kind of ASP.NET postback button (design §7.4). target, argument: regular expressions matched in full
    against the control name as the page writes it (ctlNN with its number) and the argument (None: any); text: one
    searched in the control's text (None: any)."""

    target: str
    decision: str  # press, never, ignore (no_content, round2: as for any writing, links.unhandled_need)
    why: str
    role: str = ""  # press: pager, detail, download
    argument: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in BUTTON_DECISIONS:
            raise ValueError(f"button {self.target}: decision {self.decision}")
        if (self.decision == "press") != (self.role in PRESS_ROLES):
            raise ValueError(f"button {self.target}: a press has a role (pager, detail, download), nothing else has")
        if not self.why.strip():
            raise ValueError(f"button {self.target}: every decision says why")


@dataclass(frozen=True)
class Writing:
    """A decision on a writing nobody can follow with a GET (links.Unhandled.key, or a click's key with no rule):
    key is matched in full against page_res.res_key."""

    key: str
    decision: str  # ignore, never, no_content, round2
    why: str

    def __post_init__(self) -> None:
        if self.decision not in WRITING_DECISIONS:
            raise ValueError(f"writing {self.key}: decision {self.decision}")
        if self.key.startswith("click:") and self.decision == "ignore":
            raise ValueError(f"writing {self.key}: a click that loads something is never ignored (design §7.8)")
        if not self.why.strip():
            raise ValueError(f"writing {self.key}: every decision says why")


@dataclass(frozen=True)
class Pager:
    """How one list's next page is reached by a press: page_n, the control target with argument Page$<n+1> (a
    GridView pager); next, a 「下一頁」 control (spe's MasterGrid$ctlNN$lbnNext). target: a regular expression; family:
    one that every control of that pager matches (first, previous, the page numbers, last), by which the pager is
    found and read on the last page too, where the next control may be gone ("": the target)."""

    how: str
    target: str
    family: str = ""


@dataclass(frozen=True)
class Rows:
    """A list grid or a row set of a platform's pages (design §7.5): CSS selectors within the page. A row is an element
    matched by `row` inside `grid` that holds the title element (or, with no title selector, a plain link or a press).
    walk: postback (its pager is pressed), get (GET pager links: listwalk by the walk of the pages, §7.5 generic),
    one_get (one GET answers every row: listwalk of that page alone), none (presses only: a detail's attachments)."""

    name: str
    grid: str
    row: str
    walk: str
    title: str = ""
    title_attr: str = ""  # the title element's attribute that holds the full title (cLv2's title=)
    strip: str = ""  # inside the title, what is no part of it (WebIdx's 「重要」 tag)
    date: str = ""
    category: str = ""
    pagers: tuple[Pager, ...] = ()
    claimed: str = ""  # a regular expression with one group: the 「共 N 筆」 the page states
    empty: tuple[str, ...] = ()  # the words of an empty list
    suspicious: str = ""  # one_get: a pager target that, with a row count a multiple of 10, makes mismatch:clv2
    detail_title: str | None = None  # the title area of a detail a row's press brings; None: the text holds the title
    # the body and the attachments of a detail a row's press brings (§10.1 nonempty, as Detail's text and files say for
    # a detail got by GET; review 10-26 phase 3): "" when the platform's answer is not known
    detail_body: str = ""
    detail_files: str = ""
    paths: str = ""  # a regular expression searched in the lowercase path: only those pages
    query: str = ""  # one searched in the lowercase query: only those pages (WebIdx's selpage=morenews)
    # a get walk: the links of its pager box (CSS), each a page of this same list whatever else its address carries
    # (WebIdx's first MoreNews page links its pages with a sort=date it does not carry itself: links.is_pager alone
    # would not see them; review 10-26 phase 3) 【Claude 的执行办法】
    get_pager: str = ""

    def __post_init__(self) -> None:
        if self.walk not in WALKS:
            raise ValueError(f"rows {self.name}: walk {self.walk}")
        if (self.walk == "postback") != bool(self.pagers):
            raise ValueError(f"rows {self.name}: a postback walk has pagers, no other walk has")
        if self.get_pager and self.walk != "get":
            raise ValueError(f"rows {self.name}: only a get walk names its GET pager box")


@dataclass(frozen=True)
class Detail:
    """A detail page (design §7.4 detail_paths, §10.1 nonempty and the id probe). paths: searched in the lowercase
    path and query; text, files, title: CSS selectors; id_param: the parameter that numbers the item; probe: the
    number no item has; date: the CSS of the platform's own date field, its label in the field's parent (the export
    reads it before the page's text: export.own_date), '' when the platform has none."""

    paths: str
    text: str
    files: str = ""
    title: str = ""
    id_param: str = ""
    probe: str = ""
    date: str = ""


@dataclass(frozen=True)
class ClickRule:
    """A click whose writing the crawler reads and presses this round (design §7.4 clicks, §7.8). how: get_tab (a GET
    per tab, the function's one argument an integer: oia's loadSubcategoryContent(N) → load_content.php?sub_id=N) or
    post_file (a POST per file number, read from the bound element: ee's .down → fileAjax.html, id from its
    input.download, k as the page writes it). fn: the function a tab's onclick calls, or the binding's selector; call:
    the one network call it makes, as jsread summarizes it (the method, then the address it builds)."""

    name: str
    how: str
    fn: str
    call: str
    why: str
    id_expr: str = ""  # post_file: the data expression the file number comes from, as written
    id_css: str = ""  # post_file: where that value is in the bound element
    fields: tuple[str, ...] = ()  # post_file: the fields it posts (id first, then the literal ones)


@dataclass(frozen=True)
class Saying:
    """What the school's own page says of what was asked for (design §10.1, §10.2): paths searched in the lowercase
    path, words searched in the page's text."""

    paths: str
    words: str
    flag: str  # gone_page, bad_link
    why: str


@dataclass(frozen=True)
class DataApi:
    """A data address a platform's pages ask by script (design §23 C1): read by the crawler itself, once per site, a
    POST written exactly as the page's script writes it (url, headers, body: JSON.stringify of the script's object
    literal, its members in the script's order, no spaces). calls: what a page's HTML holds when its script asks it;
    value: group 1 is the value the request carries (a page that asks it with no value found sends nothing:
    unhandled:data_api_value); body: the object's members, (name, literal) or (name, None) for the value."""

    name: str
    url: str
    calls: str
    value: str
    body: tuple[tuple[str, str | None], ...]
    headers: tuple[tuple[str, str], ...]
    why: str
    row_text: str = ""


@dataclass(frozen=True)
class DataRead:
    """What a data address answered (read_data_api): state done or mismatch; reason read:empty, read:events:<n>,
    school_error:<its message> (the answer says the school's server erred), or why it is no answer of that address
    (not_json, shape); html: what its entries link, written out for links.extract (the site's home page's, §4.4)."""

    state: str
    reason: str
    html: str = ""


@dataclass(frozen=True)
class Profile:
    name: str
    platform: str
    sites: str  # a regular expression matched in full against the site key
    content_css: str | None = None
    rows: tuple[Rows, ...] = ()
    details: tuple[Detail, ...] = ()
    buttons: tuple[Button, ...] = ()
    writings: tuple[Writing, ...] = ()
    clicks: tuple[ClickRule, ...] = ()
    says: tuple[Saying, ...] = ()
    forms: tuple[str, ...] = ()  # the ids of the forms a press may post (§7.5 step 10: no other form is ever sent)
    list_paths: str = ""  # searched in the lowercase path: list pages (prio 0, free of the variant cap)
    detail_paths: str = ""  # detail pages (free of the caps)
    download_paths: str = ""
    pending_r7: tuple[str, ...] = ()  # function names R7 reads in the school's .js before deciding
    data_apis: tuple[DataApi, ...] = ()  # read by the crawler once per site (design §23 C1)

    def covers(self, site: str) -> bool:
        return _re(self.sites).fullmatch(site or "") is not None


@lru_cache(maxsize=512)
def _re(pattern: str) -> re.Pattern:
    return re.compile(pattern)


# ---------------------------------------------------------------- the decisions every site shares (design §7.8)

# a language switch on any ASP.NET site: never (design §7.4 「语言切换」), whatever unit control holds it
LANGUAGE = Button(r"(?i)(?:.*\$)?(?:btn|lbtn|lnk|lb)?(?:mobile)?lang(?:e|en|tc|ch|zh|english|chinese)?", "never",
                  "语言切换：换成另一种语言的同一个网站，不是内容（§7.4 绝不按）")
GLOBAL_BUTTONS = (LANGUAGE,)
SEARCH_FIELDS = frozenset({"q", "keyword", "keywords", "kw", "search", "query", "txtsearch", "txt_title",
                           "tbkeyword", "searchword", "s"})
GLOBAL_WRITINGS = (
    Writing(r"js_link:(?:window\.)?(?:print|close|scrollTo)", "ignore",
            "浏览器内建的呼叫（列印、关窗、卷动），不载入内容（§7.8 全校通用）"),
    Writing(r"js_link:(?:window\.)?history\.(?:back|go|forward)", "ignore",
            "浏览器内建的上一页、下一页，不载入内容（§7.8 全校通用）"),
    Writing(r"js_link:(?:isFinite|isNaN|parseInt|parseFloat|Number|String|Boolean|encodeURIComponent|"
            r"decodeURIComponent|encodeURI|decodeURI|alert|confirm|prompt)", "ignore",
            "JavaScript 内建函数（算数、转码、弹出对话框），不发请求、不载入内容（§7.8 全校通用；试跑 10-26：校安中心的 "
            "isFinite）"),
)
_GET_FORM = re.compile(r"get_form:(?P<action>\S*) (?P<fields>.*)")


def _search_form(key: str) -> bool:
    """A GET form whose fields name a search (§7.8: never, record 三·17 a search's result pages are not fetched)."""
    m = _GET_FORM.fullmatch(key)
    if not m:
        return False
    fields = {f.strip().lower() for f in m.group("fields").split(",") if f.strip()}
    return bool(fields & SEARCH_FIELDS)


SEARCH_WHY = "站内搜索的 GET 表单：搜索结果页不抓（§7.8 全校通用，记录三·17）"

# ---------------------------------------------------------------- the profiles

_SSO_ATTACHMENT = r"/news/attachedfile\.aspx$"
SSO_NEWS = Profile(
    name="sso_news", platform="SSO 公告中心", sites=r"sso\.nknu\.edu\.tw/news|news\.nknu\.edu\.tw", forms=("uForm",),
    content_css=("#gvFull, #lbResult, #ctl00_phMain_lbSubject, #ctl00_phMain_lbContent, #ctl00_phMain_lbFileLink"),
    list_paths=r"/(?:subjectlist|lastnewslist(?:_dept)?)\.aspx$", detail_paths=r"/lastnews\.aspx$",
    download_paths=_SSO_ATTACHMENT,
    rows=(Rows("gvFull", grid="#gvFull", row="tr", walk="postback", title="a[id$='_linkSubject_short']",
               date="span[id$='_StartDate']", category="span[id$='_Category']",
               pagers=(Pager("page_n", r"gvFull"),), claimed=r"共\s*(\d+)\s*筆",
               empty=("查無資料", "沒有資料", "目前沒有"), paths=r"/(?:subjectlist|lastnewslist(?:_dept)?)\.aspx$"),),
    details=(Detail(r"/lastnews\.aspx", text="#ctl00_phMain_lbContent", files="#ctl00_phMain_lbFileLink a",
                    title="#ctl00_phMain_lbSubject", id_param="UNID", probe="0" * 40),),
    buttons=(
        Button(r"gvFull", "press", "GridView 翻页：回传 gvFull / Page$N（§7.4）", role="pager", argument=r"Page\$\d+"),
        Button(r"ctl\d+", "never", "SubjectList.aspx、lastNewsList.aspx 上的「查詢」：关键字搜索（§7.4、§7.5 第 10 点，"
               "记录三·17）。控件名是裸的 ctlNN（10-24 旧存档 subjectlist.aspx 是 ctl09），旧存档这几页没有别的裸 ctlNN 按钮",
               text=r"查詢"),
        Button(r"CateogryTab", "ignore", "LastNewsList_Dept.aspx 的分类签（控件名照原页拼法）：各分类本来就有自己的 "
               "SubjectList.aspx?cat= 列表，单位列表是它们的子集；显示中那一签的行照样翻页抓"),
    ),
    writings=(Writing(r"select:ddlCategory", "never", "「查詢」旁边的分类条件（搜索的一部分）；各分类另有自己的 "
                      "SubjectList.aspx?cat= 网址，起点都有"),),
    says=(Saying(_SSO_ATTACHMENT, r"附件無法下載[\s\S]*檔案不存在", GONE_PAGE,
                 "学校的附件页写「附件無法下載…檔案不存在」：隔一段时间再抓还是一样才算不存在（§10.2，核对意见 B6）"),
          Saying(_SSO_ATTACHMENT, r"附件無法下載[\s\S]*檔案包含不支援的字元", BAD_LINK,
                 "学校的附件页写「檔案包含不支援的字元」：学校的链接本身坏了（§10.2）")),
)

SSO_WEBIDX = Profile(
    name="sso_webidx", platform="SSO 单位网站（WebIdx）", sites=r"sso\.nknu\.edu\.tw/web/\d+", forms=("form1",),
    content_css=".main-content, .news-view-container, .news-detail-view, .items-view-container, #divContent",
    list_paths=r"/services/web/default\.aspx$", detail_paths=r"newsidx=",
    rows=(
        # MoreNews, and the home page (default.aspx?webidx=N), whose news grid is MoreNews's first page with the same
        # pager box (the 10-24 archive, read only: webidx=3 and 35, the home's 10 rows are MoreNews's first 10):
        # its row buttons are pressed like MoreNews's, never left unpressed (review 10-26 phase 3)
        Rows("webidx_news", grid=".news-list-wrapper", row=".news-row", walk="get", title=".col-title a",
             strip=".tag", date=".col-date", category=".col-cat", query=r"selpage=morenews|^webidx=\d+$",
             detail_title=".news-detail-view .detail-header h3", detail_body=".news-detail-view .detail-body",
             detail_files=".news-detail-view .detail-attachments a", empty=("查無資料", "目前沒有"),
             get_pager=".news-pagination a[href], .pagination a[href]"),
        # 书表 (menuid=N): paged by GET too, menuid=N&pageNum=K (.items-pagination; the 10-24 archive, read only:
        # webidx=3&menuid=214 has 4 pages), so a get walk, never one GET
        Rows("webidx_items", grid=".items-list-wrapper", row=".items-row", walk="get", title=".col-title",
             date=".col-date", category=".col-cat", empty=("查無資料", "目前沒有"),
             get_pager=".items-pagination a[href]"),
        Rows("webidx_files", grid=".detail-attachments", row="li.file-item", walk="none", title=".file-text"),
    ),
    details=(Detail(r"newsidx=", text=".detail-body", files=".detail-attachments a", title=".detail-header h3",
                    id_param="newsIdx"),),
    buttons=(
        Button(r"ucNews\$rptNews\$ctl\d+\$lnkShowDetail", "press", "单位网站消息的单则内文：同一行有 newsidx= 的普通"
               "链接时用 GET、不按，没有才按（§7.4）", role="detail"),
        Button(r"(?:ucNews\$rptFiles|ucAllTypeList\$rptItems)\$ctl\d+\$lnkDownloadFile", "press",
               "单位网站的附件下载（§7.4）", role="download"),
        Button(r"(?:ucNews|ucAllTypeList)\$(?:btnAll|rptCategories\$ctl\d+\$btnCat)", "ignore",
               "分类签：载入时显示的就是「全部」（btnAll 带 active），各分类是同一批的子集。§7.4 写「btnCat、btnAll 先不按，"
               "试跑时核对 MoreNews 的总数」（第 18.2 节）"),
    ),
    writings=(Writing(r"select:ucNews\$ddlSort", "ignore", "排序选单：只换排序，行还是同一批"),
              Writing(r"js_link:scrollToTop", "ignore", "「回到頂端」：R7 读了学校的 script.js 本体（试跑 10-26），不发请求、"
                      "不载入内容"),
              Writing(r"js_link:toggleMenu", "ignore", "主选单的开关：R7 读了学校的 script.js 本体（试跑 10-26），不发请求、"
                      "不载入内容"),
              # the pilot 10-26/27 (生活辅导组 webidx=595): R7 read __doPostBack('<%= btnPageNav.UniqueID %>') and
              # '<%= btnClearFileSearch.UniqueID %>' in the school's .js, 66 pages each, must (P53 11-03)
              Writing(r"js_postback:<%=[^%]*%>", "ignore",
                      "学校 .js 里没展开的 ASP.NET 模板字串（<%= … %>）：服务器只展开 .aspx 里的，放在 .js 里原样送到浏览器，"
                      "不是控件名也不是网址，按不出任何东西")),
    pending_r7=("toggleMobileSub", "currentSlide", "moveSlide", "toggleClearBtn", "setSearch", "clearSearchInput"),
)

# design §23 C1. Who decided (record 五·17): Claude's proposal 「第一轮直接读这个接口一次……网页照常算抓好」, the owner
# 10-27 「可以，听你的」 【用户同意 Claude 的建议】; how it is read (this request, once per site, the answer's shapes)
# 【Claude 的执行办法】. The request as written here is what the 10-26 pilot's archive holds, read only: site/100061's index.aspx, $(document).ready → getTimeLine()
# → json('/Unit/Info/ajaxServices/ajaxTimeline.aspx', {cFunction: "getTimelineJson", pLv2: cLv2}), and json() in
# /include/js/extend_NKNU/ajaxAnalysis.js: jQuery $.ajax, type POST, data JSON.stringify(oData), contentType
# "application/json", dataType 'json' (jQuery's Accept for it); cLv2 from the page's `var cLv2 = '100061';`. No
# X-Requested-With: the design names only these; the User-Agent stays the crawler's)
CLV2_TIMELINE = DataApi(
    "sso_clv2_timeline", "https://sso.nknu.edu.tw/Unit/Info/ajaxServices/ajaxTimeline.aspx",
    calls=r"ajaxServices/ajaxTimeline\.aspx", value=r"var\s+cLv2\s*=\s*'(\d+)'",
    body=(("cFunction", "getTimelineJson"), ("pLv2", None)),
    headers=(("Content-Type", "application/json"), ("Accept", "application/json, text/javascript, */*; q=0.01")),
    why="cLv2 单位网站的时间轴：网页载入时用 POST 向这个接口要资料；第一轮直接读一次（每个网站一次），空的记「时间轴是空的」，"
        "有事件就把事件里的链接收进来（§23 C，用户 10-27 同意 Claude 的建议）",
    row_text="时间轴（ajaxTimeline）")

SSO_CLV2 = Profile(
    name="sso_clv2", platform="SSO 单位网站（cLv2）", sites=r"sso\.nknu\.edu\.tw/site/\d+", forms=("form1",),
    data_apis=(CLV2_TIMELINE,),
    content_css="#uSSONews_PanelList, #divContent, #uDataContentPos2_UpdatePanel2",
    list_paths=r"/services/site/index\.aspx$",
    rows=(
        Rows("clv2_news", grid="#uSSONews_gvList0", row="tr", walk="one_get", title="a[id$='_gv_lbSel']",
             title_attr="title", category="span[id$='_lbcTypeName']", suspicious=r"uSSONews\$gvList0",
             detail_body="#divContent", empty=("查無資料", "無資料")),
        Rows("clv2_files", grid="#uDataContentPos2_uList", row="tr", walk="one_get",
             title="span[id^='uDataContentPos2_conLB']", empty=("查無資料", "無資料")),
    ),
    buttons=(
        Button(r"uSSONews\$gvList0\$ctl\d+\$gv_lbSel", "press", "cLv2 公告的单则内文（§7.4）", role="detail"),
        Button(r"uDataContentPos2\$downloadBtn\d+", "press", "cLv2 书表下载（§7.4）", role="download"),
        Button(r"uSSONews\$Menu1", "never", "§7.4 绝不按：公告的分类选单；列表一次 GET 全部送到"),
        Button(r"uSSONews\$gvList0\$ctl\d+\$btnTopStatus", "ignore",
               "置顶星号：原页这个链接写了两个 href，浏览器只用第一个 javascript:void(0)，使用者点它不会回传"),
    ),
    writings=(Writing(r"select:uDataContentPos2\$ddlType", "ignore",
                      "书表的分类筛选：载入时是「請選擇...」，列出全部分类的书表"),),
)

_CNKNU_DETAILS = (
    # newsdetail states its date apart from its text: <h5>發佈人員：…｜公告日期：<span id="lbl_AddTime">…</span></h5>
    Detail(r"/newsdetail\.aspx", text="#lbl_Contents", files="#lbl_FileLink a", title="#lbl_Title", id_param="Nid",
           probe="999999999", date="#lbl_AddTime"),
    Detail(r"/newsview\.aspx", text="#DataFormView_ContentsLabel", files="#DataFormView_FileLinkLabel a",
           title="#DataFormView_TitleLabel", id_param="Nid", probe="999999999"),
)
CNKNU = Profile(
    name="cnknu", platform="c.nknu 模板", forms=("form1",),
    sites=r"c\.nknu\.edu\.tw(?:/[^/]+)?|(?:tecs|spe|sea|cprc|hrkm|arts|giast|ksped)\.nknu\.edu\.tw",
    content_css=("#lbl_Title, #lbl_Contact, #lbl_PageSub, #lbl_Contents, #lbl_FileLink, #DataFormView, #MasterGrid, "
                 "#MainContent, .news_content, #lbl_ADJHtml, #lbl_CaseShow, #lbl_YoutubeHtml, .Page_Contact"),
    list_paths=r"/(?:newslist|album|download|laws)\.aspx$|/default\.aspx$",
    detail_paths=r"/(?:newsview|newsdetail|album_images)\.aspx$", download_paths=r"/uploadfile/|/files/",
    rows=(Rows("MasterGrid", grid="#MasterGrid", row="tr", walk="postback",
               pagers=(Pager("page_n", r"MasterGrid"),
                       Pager("next", r"MasterGrid\$ctl\d+\$lbnNext", family=r"MasterGrid\$ctl\d+\$\w+")),
               # member.aspx (編制與業務, P51 11-03), forumsview.aspx, activeorder.aspx, honor.aspx lay out text in a
               # MasterGrid too, with no link and no press in a row: tables of content, not lists, by one judgement
               # (postback._content_table, FIX 3 11-06, 【用户同意 Claude 的建议】) in place of a path left out page by
               # page
               empty=("查無資料", "目前沒有相關消息", "沒有資料")),),
    details=_CNKNU_DETAILS,
    buttons=(
        Button(r"MasterGrid", "press", "GridView 翻页：回传 MasterGrid / Page$N，form #form1（§7.4）", role="pager",
               argument=r"Page\$\d+"),
        Button(r"MasterGrid", "ignore", "表头排序（体育室 download.aspx、laws.aspx）：只换排序，行还是同一批，翻页照 "
               "Page$N（§7.4，点击扫描 10-25）", argument=r"Sort\$\w+"),
        Button(r"MasterGrid\$ctl\d+\$lbnNext", "press", "spe 的「下一頁」：要选含有这个按钮的 form（第一个 form 是站内"
               "搜索，§7.4）", role="pager"),
        Button(r"MasterGrid\$ctl\d+\$lbn(?:First|Prev)", "ignore", "spe 的「首頁」「上一頁」：往回翻，前面的页已经存过"),
        Button(r"MasterGrid\$ctl\d+\$lbnLast", "ignore", "spe 的「尾頁」：直接跳到最后会跳过中间的页，一律按「下一頁」"
               "（§7.4：尾页按钮 lbnLast 不按）"),
        Button(r"MasterGrid\$ctl\d+\$ctl\d+", "ignore", "spe 的页码按钮：翻页一律按「下一頁」，每一页都会存到"),
        Button(r"Btn_Send", "never", "联络表单的「確定送出」（总务处 contact.aspx、体育室 contactus.aspx）：绝不送出表单"
               "（§7.4）"),
        Button(r"Btn_Clear", "never", "联络表单的「清除重填」（同上，§7.4）"),
        Button(r"btnReGenCode", "never", "联络表单的换验证码：验证码不碰（§7.4，CLAUDE.md）"),
    ),
    writings=(
        Writing(r"select:(?:ddl_GroupId|ddl_NewsClass)", "never",
                "站内搜索的条件（组别、类别，配合「搜尋」按钮）：搜索结果页不抓；各类别另有自己的列表网址"),
        Writing(r"select:ddl_Sort", "ignore", "排序方式（日期、标题、单位）：只换排序，行还是同一批"),
        Writing(r"select:ddl_FileKind", "ignore", "体育室表格下载的「類別索引」：载入时是「全部」，列表已列出全部类别"),
        Writing(r"select:ddl_Class", "ignore", "newslist.aspx 的分类筛选：只有不带 class 参数的 newslist.aspx 载入时是「全部」，"
                "翻到底就是全部（试跑 10-26：体育室 99 条，和 v1 已知的一样多）；带 ?class= 的列表载入时就是那一类，是同一批的"
                "子集。所以记 ignore 的网站一定要有一页不带 class 的 newslist.aspx 翻到最后一页（对账 R2、验收第 4 项查，"
                "monitor.class_all_missing；设计 §23 F2）"),
    ),
    pending_r7=("WdatePicker",),
)

OIA_TAB = ClickRule("oia_tab", "get_tab", fn="loadSubcategoryContent",
                    call="GET load_content.php?sub_id={subcategoryId}",
                    why="国际处的分页签：每个签一个 GET load_content.php?sub_id=N，载入时显示的那一签也按（§7.4）")
OIA = Profile(name="oia", platform="国际处（厂商写的 PHP 系统）", sites=r"oia\.nknu\.edu\.tw", clicks=(OIA_TAB,))

EE_FILE = ClickRule("ee_file", "post_file", fn=".down", call="POST fileAjax.html",
                    why="电子系的下载按钮：每一对 (k, 档案编号) 一个 POST fileAjax.html，回来的档案网址再抓（§7.4）",
                    id_expr="$(this).find('.download').val()", id_css="input.download", fields=("id", "k"))
EE = Profile(
    name="ee", platform="电子系（自建）", sites=r"ee\.nknu\.edu\.tw", clicks=(EE_FILE,),
    writings=(
        Writing(r"select:\.option", "round2", "学年选单（notes.html、student4.html：location.href = html + "
                "\".html?getId=\" + 选项的值）：这一轮不自动按下拉选单，各学年的页别处没有普通链接；下一轮照拼法组网址补"
                "（§7.8、§8.2、第 19 节）"),
        Writing(r"js_link:checkForm", "never", "送出表单前的检查（外部 .js）：送出表单绝不做（§7.8）"),
    ),
)
LIS = Profile(
    name="lis", platform="图书馆（Joomla 3.10）", sites=r"lis\.nknu\.edu\.tw",
    writings=(Writing(r"js_link:Joomla\.tableOrdering", "ignore",
                      "Joomla 列表的表头排序（换排序栏位后送出 adminForm）：只换排序，行还是同一批（§7.8；Joomla 核心函数）"),),
    pending_r7=("initSort",),
)
ELEC = Profile(name="elec", platform="电机系（自建）", sites=r"elec\.nknu\.edu\.tw",
               writings=(Writing(r"js_link:userLogInForm", "never", "侧栏的登录框：登录不碰（§7.8，CLAUDE.md）"),))
ENGLISH = Profile(
    name="english", platform="英语系（自建）", sites=r"english\.nknu\.edu\.tw",
    writings=(Writing(r"js_link:_checkLogin", "never", "登录框：登录不碰（§7.8，CLAUDE.md）"),
              Writing(r"js_link:_getRandom", "never", "登录框的换验证码：验证码不碰（§7.8，CLAUDE.md）")),
    # 首页最新消息的分类签 <a href="javascript:" data-id="N">（key js_link:）：R7 读了外部 .js 才定（§7.8）
    pending_r7=("js_link:",),
)
CLCTS = Profile(name="clcts", platform="华语中心（自建）", sites=r"clcts\.nknu\.edu\.tw", pending_r7=("papago",))
WORDPRESS = Profile(name="wordpress", platform="WordPress", sites=r"vd\.nknu\.edu\.tw|fine-arts\.nknu\.edu\.tw")

PROFILES = (SSO_NEWS, SSO_WEBIDX, SSO_CLV2, CNKNU, OIA, EE, LIS, ELEC, ENGLISH, CLCTS, WORDPRESS)


def profile_for_site(site: str | None) -> Profile | None:
    """The profile of a site key (url.owner_site), or None for a site no platform file describes."""
    return next((p for p in PROFILES if p.covers(site or "")), None)


def profile_for(url: str) -> Profile | None:
    return profile_for_site(scope.site_key(url))


def _path(url: str) -> str:
    try:
        return urlsplit(url).path.lower()
    except ValueError:
        return ""


def content_css(url: str) -> str | None:
    """The content area links.extract reads zones by (design §7.2), or None for a site with no profile."""
    p = profile_for(url)
    return p.content_css if p is not None else None


def page_kind(url: str) -> str | None:
    """download, detail or list by the profile's paths (lists: prio 0 and free of the variant cap; details and
    downloads: free of the caps), or None."""
    p = profile_for(url)
    if p is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    path = parts.path.lower()
    full = path + ("?" + parts.query.lower() if parts.query else "")
    if p.download_paths and re.search(p.download_paths, path):
        return "download"
    if p.detail_paths and (re.search(p.detail_paths, path) or re.search(p.detail_paths, full)):
        return "detail"
    if p.list_paths and re.search(p.list_paths, path):
        return "list"
    return None


# ---------------------------------------------------------------- data addresses read once per site (design §23 C)

DATA_KEY = "data:"  # page_res.res_key of a data address: data:<its name>:<value>
DATA_READ, DATA_SCHOOL_ERROR = "read:", "school_error:"  # action.reason of an answer read, of the school's error


def data_api_reads(site: str | None, html: str | None) -> list[tuple[DataApi, str | None]]:
    """The data addresses of a site's platform a page's HTML asks (calls), each with the value its request carries,
    None when the page names none (nothing is sent: unhandled, 要人看)."""
    p = profile_for_site(site)
    if p is None or not html:
        return []
    out = []
    for api in p.data_apis:
        if re.search(api.calls, html):
            m = re.search(api.value, html)
            out.append((api, m.group(1) if m else None))
    return out


def data_api(name: str) -> DataApi | None:
    return next((a for p in PROFILES for a in p.data_apis if a.name == name), None)


def data_api_key(api: DataApi, value: str | None) -> str:
    return f"{DATA_KEY}{api.name}:{value if value is not None else '?'}"


def data_api_body(api: DataApi, value: str) -> bytes:
    """The request's body as the page's script writes it: JSON.stringify of its object (members in order, no
    spaces, characters as they are)."""
    return json.dumps({k: (value if v is None else v) for k, v in api.body}, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def known_data_api(page_url: str, request_url: str) -> bool:
    """Whether a request a page made is a data address its platform file reads this round (design §23 C2): on the
    page's own host, the address's host and path (any case). The browser check counts it neither as data_post nor
    as a request the page needed and did not get."""
    try:
        page, req = urlsplit(page_url), urlsplit(request_url)
    except ValueError:
        return False
    host = (req.hostname or "").lower()
    if host != (page.hostname or "").lower():
        return False
    p = profile_for(page_url)
    return p is not None and any((urlsplit(a.url).hostname or "").lower() == host
                                 and urlsplit(a.url).path.lower() == req.path.lower() for a in p.data_apis)


def read_data_api(api: DataApi, text: str | None) -> DataRead:
    """What a data address answered (design §23 C1), as the page's json() reads it: the newer shape {bErrOn, cMessage,
    oData}, the older {result: {bErrOn: "true"/"false", cMessage}, data}. An error the answer says is the school's
    server's (school_error:<message>); the timeline is (oData or data)[0].events: empty ({} or []) is read:empty,
    else read:events:<n>, its events' links written out for links.extract (TimelineJS: media.url, media.link,
    media.thumbnail, background.url, the <a href> and <img src> of text.headline, text.text, media.caption and
    media.credit; the title's too). Not JSON, or no such shape: mismatch, never ok."""
    try:
        got = json.loads(text or "")
    except ValueError:
        return DataRead("mismatch", "not_json")
    if not isinstance(got, dict):
        return DataRead("mismatch", "shape")
    new = "result" not in got
    if new:
        error, message, data = bool(got.get("bErrOn")), got.get("cMessage"), got.get("oData")
    else:
        res = got.get("result") if isinstance(got.get("result"), dict) else {}
        error, message, data = str(res.get("bErrOn", "")).lower() == "true", res.get("cMessage"), got.get("data")
    if error:
        return DataRead("done", f"{DATA_SCHOOL_ERROR}{str(message or '')[:150]}")
    first = data[0] if isinstance(data, list) and data else None
    if not isinstance(first, dict) or not isinstance(first.get("events"), dict | list):
        return DataRead("mismatch", "shape")
    events = first["events"]
    items = list(events.values()) if isinstance(events, dict) else list(events)
    if not items:
        return DataRead("done", f"{DATA_READ}empty")
    parts = []
    for ev in [first.get("title"), *items]:
        if not isinstance(ev, dict):
            continue
        text_ = ev.get("text") if isinstance(ev.get("text"), dict) else {}
        media = ev.get("media") if isinstance(ev.get("media"), dict) else {}
        # HTML TimelineJS shows as written: the text's headline and body, the media's caption and credit (second
        # review 10-27: every field an event can link from, §23.9 C1)
        for box, key in ((text_, "headline"), (text_, "text"), (media, "caption"), (media, "credit")):
            if isinstance(box.get(key), str):
                parts.append(f"<div>{box[key]}</div>")
        # addresses: the media (and the link its picture opens), its thumbnail, the slide's background
        for box, key, tag in ((media, "url", "a"), (media, "link", "a"), (media, "thumbnail", "img"),
                              (ev.get("background"), "url", "img")):
            url = box.get(key) if isinstance(box, dict) else None
            if isinstance(url, str) and url.strip():
                attr = "href" if tag == "a" else "src"
                parts.append(f'<{tag} {attr}="{_attr(url.strip())}">' + ("[timeline]</a>" if tag == "a" else ""))
    return DataRead("done", f"{DATA_READ}events:{len(items)}", "<html><body>" + "".join(parts) + "</body></html>")


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- buttons and writings

def _text_holds(b: Button, text: str | None) -> bool:
    """A rule that names a button's text holds only for a button whose text is known and says so: a bare control name
    (SSO's ctlNN) is no proof of what the button does, and a text not known never borrows the rule's (review 10-26
    phase 3: a 「附件下載」 ctl12 decided never by the 「查詢」 rule) 【Claude 的执行办法】."""
    return b.text is None or (text is not None and _re(b.text).search(text) is not None)


def button(site: str | None, target: str, argument: str = "", text: str | None = None) -> Button | None:
    """The rule for one postback button of a site (its control name as written, its argument and its text; a rule
    that names a text holds only for that text: _text_holds), or None: the platform file says nothing (unknown,
    要人看)."""
    p = profile_for_site(site)
    rules = (*(p.buttons if p is not None else ()), *GLOBAL_BUTTONS)
    for b in rules:
        if not _re(b.target).fullmatch(target):
            continue
        if b.argument is not None and not _re(b.argument).fullmatch(argument or ""):
            continue
        if not _text_holds(b, text):
            continue
        return b
    return None


def _button_key(site: str | None, rest: str, text: str | None = None) -> tuple[str, str]:
    """A postback key's decision: postback:<target, ctlNN as ctl*>[|<argument>] (the crawler's key; ctl* stands for any
    number), with the button's text (page_res.text) for a rule that names one (_text_holds). With no argument, a
    target whose rules differ by argument (MasterGrid: Page$N pressed, Sort$x ignored) is press when one of them is:
    postback.py presses only what its rule says (the pager), the others need nothing."""
    target, sep, argument = rest.partition("|")
    p = profile_for_site(site)
    rules = (*(p.buttons if p is not None else ()), *GLOBAL_BUTTONS)
    probe = target.replace("ctl*", "ctl0")
    hits = [b for b in rules if _re(b.target).fullmatch(probe) and _text_holds(b, text)]
    if sep:
        hits = [b for b in hits if b.argument is None or _re(b.argument).fullmatch(argument)]
    if not hits:
        return UNKNOWN, ""
    if not sep and len({b.decision for b in hits}) > 1:
        pressed = next((b for b in hits if b.decision == "press"), None)
        if pressed is not None:
            return pressed.decision, pressed.why
    return hits[0].decision, hits[0].why


def _click_rule(p: Profile | None, key: str) -> ClickRule | None:
    """A clicks rule a click's key names: click:<fn>(<argument>) for a tab, click:<selector>[<i>] for a bound element,
    or the key the rule writes itself (click:fileAjax.html(k=..,id=..))."""
    if p is None:
        return None
    for rule in p.clicks:
        if rule.how == "get_tab" and re.fullmatch(rf"click:{re.escape(rule.fn)}\(\s*\d+\s*\)", key):
            return rule
        if rule.how == "post_file":
            target = rule.call.split(" ", 1)[1]
            if re.fullmatch(rf"click:{re.escape(rule.fn)}\[\d+\]", key) or \
                    re.fullmatch(rf"click:{re.escape(target)}\(k=\d+,id=[^)]*\)", key):
                return rule
    return None


def decision(site: str | None, res_key: str, text: str | None = None) -> str:
    """The platform file's decision for one key of a page (design §15), as the crawler, rejudge and verify check 8 ask
    it: press/never/ignore/unknown for a button, handle/no_content/never/round2/unknown for a click, and
    ignore/never/no_content/round2/unknown for any other writing. unknown: nobody has decided (要人看). text: a
    button's text (page_res.text), which a rule that names one needs (_text_holds)."""
    return decision_why(site, res_key, text)[0]


def decision_why(site: str | None, res_key: str, text: str | None = None) -> tuple[str, str]:
    """decision() and its reason ("" for unknown)."""
    p = profile_for_site(site)
    if res_key.startswith("postback:"):
        return _button_key(site, res_key[len("postback:"):], text)
    if res_key.startswith("click:") and (rule := _click_rule(p, res_key)) is not None:
        return "handle", rule.why
    for w in (*(p.writings if p is not None else ()), *GLOBAL_WRITINGS):
        if _re(w.key).fullmatch(res_key):
            if res_key.startswith("click:") and w.decision == "ignore":
                raise ValueError(f"{res_key}: a click that loads something is never ignored (design §7.8)")
            return w.decision, w.why
    if _search_form(res_key):
        return "never", SEARCH_WHY
    return UNKNOWN, ""


def check_profiles() -> list[str]:
    """What is wrong in the profiles themselves (a test keeps this empty): a writing that ignores a click, a key
    pattern that does not compile, two profiles that claim one site of the round-1 list."""
    out = []
    for p in PROFILES:
        for w in p.writings:
            if w.key.startswith("click:") and w.decision == "ignore":
                out.append(f"{p.name}: {w.key} ignores a click")
        for pattern in (p.sites, *(b.target for b in p.buttons), *(w.key for w in p.writings)):
            try:
                re.compile(pattern)
            except re.error as exc:
                out.append(f"{p.name}: {pattern}: {exc}")
    return out


# ---------------------------------------------------------------- clicks (design §7.4)

@dataclass(frozen=True)
class ClickPlan:
    """One press a clicks rule makes of a page's click: a GET of a tab's address (a url row, kind0 click) or a POST
    for a file's address (an action row, kind click: one per site and row_key). res_key: the page_res key the crawler
    writes for it in place of the click's own key."""

    rule: str
    method: str  # GET, POST
    url: str
    res_key: str
    click_key: str
    text: str
    zone: str
    data: tuple[tuple[str, str], ...] = ()
    row_key: str = ""  # POST: click:k=<K>,id=<number> (deduplicated across the site's pages)


def click_decision(site: str | None, click, html: str | None = None, page_url: str = "") -> str | None:
    """The decision for one links.Click of a page: handle when a clicks rule of the site reads it (its function or
    binding, its one network call, its argument or its element's value, as the rule says: click_plan gives the
    press), round2 when a rule names it and its writing no longer fits the rule (the site changed: never guessed,
    §7.8), else the profile's writing decision for its key, or None (no rule: links.click_need decides by a plain
    link or the tab shown on load)."""
    p = profile_for_site(site)
    rule = _click_rule(p, click.key) if p is not None else None
    if rule is None and p is not None:
        rule = next((r for r in p.clicks if r.fn in (click.fn, click.group)), None)
    if rule is not None:
        return "handle" if click_plan(site, click, html, page_url) is not None else "round2"
    got, _why = decision_why(site, click.key)
    return None if got == UNKNOWN else got


def click_plan(site: str | None, click, html: str | None, page_url: str) -> ClickPlan | None:
    """The press a clicks rule makes of a click, or None when no rule of the site fits it exactly."""
    p = profile_for_site(site)
    if p is None:
        return None
    for rule in p.clicks:
        if rule.fn not in (click.fn, click.group):
            continue
        if rule.how == "get_tab":
            plan = _tab_plan(rule, click, page_url)
        else:
            plan = _file_plan(rule, click, html, page_url)
        if plan is not None:
            return plan
    return None


def _one_call(rule: ClickRule, click):
    """The click's one network call when it is of the rule's method (several calls: no rule fits)."""
    calls = list(click.calls)
    method = rule.call.split(" ", 1)[0]
    return calls[0] if len(calls) == 1 and calls[0].method.upper() == method else None


def _tab_plan(rule: ClickRule, click, page_url: str) -> ClickPlan | None:
    """oia: onclick="loadSubcategoryContent(N)", the page's own function fetches 'load_content.php?sub_id=' +
    subcategoryId: one GET load_content.php?sub_id=N, joined to the page's address. The function's parameter may be
    named otherwise; the address it builds may not."""
    call = _one_call(rule, click)
    if call is None or click.fn != rule.fn or len(click.params) != 1 or not re.fullmatch(r"\s*\d+\s*", click.args):
        return None
    method, template = rule.call.split(" ", 1)
    hole = "{" + click.params[0] + "}"
    if call.target != template.replace("{subcategoryId}", hole):
        return None
    url = scope.canonical(urljoin(page_url, call.target.replace(hole, click.args.strip())))
    if url is None:
        return None
    return ClickPlan(rule.name, method, url, click.key, click.key, click.text, click.zone)


_LITERAL = re.compile(r"(?P<name>\w+)=(?P<value>\d+)")


def _file_plan(rule: ClickRule, click, html: str | None, page_url: str) -> ClickPlan | None:
    """ee: $(".down").each(… $.ajax({type:"POST", url:"fileAjax.html", data:{id: $(this).find('.download').val(),
    k: K}})): the file number from the bound element's input.download (read again from the page by the element's
    place among the selector's matches), K the literal the page writes."""
    call = _one_call(rule, click)
    if call is None or html is None or not click.trigger.startswith("bind:") or call.target != rule.call.split(" ")[1]:
        return None
    data = dict(d.split("=", 1) for d in call.data if "=" in d)
    id_field, *literal = rule.fields
    if data.get(id_field) != "{" + rule.id_expr + "}" or set(data) != set(rule.fields):
        return None
    lits = {}
    for name in literal:
        m = _LITERAL.fullmatch(f"{name}={data[name]}")
        if m is None:
            return None
        lits[name] = m.group("value")
    m = re.fullmatch(r"click:(?P<sel>.+)\[(?P<i>\d+)\]", click.key)
    if m is None or m.group("sel") != rule.fn:
        return None
    values = _bound_values(html, rule.fn, rule.id_css)
    i = int(m.group("i"))
    number = values[i] if i < len(values) else ""
    if not number or not re.fullmatch(r"[\w.-]{1,40}", number):
        return None
    url = scope.canonical(urljoin(page_url, call.target))
    if url is None:
        return None
    fields = ((id_field, number), *((name, lits[name]) for name in literal))
    shown = ",".join(f"{k}={v}" for k, v in (*((name, lits[name]) for name in literal), (id_field, number)))
    return ClickPlan(rule.name, "POST", url, f"click:{call.target}({shown})", click.key, click.text, click.zone,
                     data=fields, row_key=f"click:{shown}")


@lru_cache(maxsize=4)
def _bound_values(html: str, selector: str, value_css: str) -> tuple[str, ...]:
    """The value of value_css inside each element the selector matches, in the page's order (links._bound numbers a
    binding's elements the same way: click:<selector>[<i>]); "" where there is none. One parse per page."""
    out = []
    for node in LexborHTMLParser(html).css(selector):
        holder = node.css_first(value_css)
        out.append((holder.attributes.get("value") or "").strip() if holder is not None else "")
    return tuple(out)


def tab_content(text: str | None) -> str | None:
    """What a tab's GET brought (oia's load_content.php: JSON {success, content, message}): the content's HTML, read
    as part of the page; None when success is not true or the content is empty (the row's flags get click_empty)."""
    try:
        got = json.loads((text or "").lstrip("﻿"))
    except ValueError:
        return None
    if not isinstance(got, dict) or got.get("success") is not True:
        return None
    content = got.get("content")
    return content if isinstance(content, str) and content.strip() else None


def click_file(text: str | None, base: str) -> str | None:
    """The file address a file button's POST brought (ee's fileAjax.html answers the address window.open opens): the
    body, stripped, when it is one address (joined to the page), else None (the press is mismatch)."""
    body = (text or "").strip().strip('"').strip()
    if not body or any(c.isspace() for c in body) or "<" in body or len(body) > 2000:
        return None
    joined = urljoin(base, body)
    try:
        ok = urlsplit(joined).scheme in ("http", "https") and bool(urlsplit(joined).hostname)
    except ValueError:
        return None
    return scope.canonical(joined) if ok else None


# ---------------------------------------------------------------- what the school's own page says (§10.1, §10.2)

def _text(html: str) -> str:
    tree = LexborHTMLParser(html or "")
    for n in tree.css("script, style"):
        n.decompose()
    body = tree.body or tree.root
    return " ".join((body.text(separator=" ") if body is not None else "").split())


def school_says(url: str, html: str | None) -> Saying | None:
    """What a school page answering for a file says (gone_page, bad_link), or None."""
    p = profile_for(url)
    if p is None or not html:
        return None
    path = _path(url)
    text = None
    for s in p.says:
        if re.search(s.paths, path):
            text = _text(html) if text is None else text
            if re.search(s.words, text):
                return s
    return None


# design §23.9 A1 ③ (second review 10-27, 【Claude 的执行办法】): a short answer (FILE_SHORT_BYTES at most, as text)
# to a download button whose words say only that the file is not there is the school's gone page too, whatever the
# name its header gives (計畫.pdf holding the one line 「檔案不存在」). A long page that mentions the words in passing
# is no such answer (it is not_file all the same when it is no file: no name, or a name it is not)
FILE_SHORT_BYTES = 1024
FILE_SHORT_SAYS = (Saying("", r"檔案不存在|档案不存在", GONE_PAGE,
                          "下载按钮取回来的是很短的一句「檔案不存在」：学校说档案不在（§23.9 A1 ③）"),)


def file_says(text: str | None) -> Saying | None:
    """What the school's words in a download button's answer say (design §23 A1 ③, 【Claude 的执行办法】): SSO's
    sayings (SSO_NEWS.says: 「附件無法下載…檔案不存在」, 「檔案包含不支援的字元」) matched by their words alone, for
    every SSO button (WebIdx's, cLv2's): the export GET's address is no attachment page to match their paths against;
    and an answer of FILE_SHORT_BYTES or less that says 「檔案不存在」 alone (FILE_SHORT_SAYS, §23.9). text: the answer as
    text (a web page or plain text); None when it says neither."""
    if not text:
        return None
    plain = _text(text)
    said = next((s for s in SSO_NEWS.says if re.search(s.words, plain)), None)
    if said is None and len(text.encode("utf-8", "replace")) <= FILE_SHORT_BYTES:
        said = next((s for s in FILE_SHORT_SAYS if re.search(s.words, plain)), None)
    return said


def says_step(said: Saying, reason: str | None, attempts: int, now: float) -> dict:
    """The url row's columns after the school's page said `said` (frontier.finish takes them, with the answer's own
    status, sha256, warc …): bad_link at once; gone_page only the second time running (the row waits GONE_RECHECK_S
    under reason GONE_RECHECK first: an attempt spent, so it reads as 出错待重试 meanwhile)."""
    if said.flag == BAD_LINK or reason == GONE_RECHECK:
        return {"state": "done", "reason": None, "flags": said.flag}
    return {"state": "pending", "reason": GONE_RECHECK, "attempts": attempts + 1, "next_at": now + GONE_RECHECK_S}


# ---------------------------------------------------------------- detail pages: empty, and the id probe (§10.1)

def detail_of(url: str) -> Detail | None:
    p = profile_for(url)
    if p is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    full = parts.path.lower() + ("?" + parts.query.lower() if parts.query else "")
    return next((d for d in p.details if re.search(d.paths, full)), None)


def empty_detail(url: str, html: str) -> bool | None:
    """Whether a detail page holds nothing (design §10.1 nonempty: no body text, no attachment, no image, no related
    link): True, False, or None when the URL is no profiled detail page or its body area is not on the page (that is
    no empty page: the layout is another, and nothing is claimed)."""
    d = detail_of(url)
    if d is None:
        return None
    tree = LexborHTMLParser(html or "")
    body = tree.css(d.text)
    if not body:
        return None
    for node in body:
        if (node.text() or "").strip() or node.css_first("img[src], a[href], iframe[src], embed, object"):
            return False
    if d.files:
        for node in tree.css(d.files):
            href = (node.attributes.get("href") or "").strip()
            if href and href != "#":
                return False
    return True


def id_probe_url(url: str) -> str | None:
    """The address of an item that cannot exist on this detail page's platform (design §10.1): the same address with
    the item's number replaced by the probe (SSO's UNID of 40 zeros, c.nknu's nid=999999999), or None."""
    d = detail_of(url)
    if d is None or not d.id_param or not d.probe:
        return None
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k.lower() == d.id_param.lower() for k, _v in query):
        return None
    query = [(k, d.probe if k.lower() == d.id_param.lower() else v) for k, v in query]
    return scope.canonical(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), "")))


def gone_id(probe_status: int | None, probe_empty: bool | None, page_detail: bool, page_empty: bool) -> bool:
    """§10.1: a page looks gone by its number (gone_id) only when all three hold: the host's probe answered 200, the
    probe's page is an empty detail by the profile's nonempty, and this page is a detail page that is itself empty
    (empty_detail). A real item with one picture or one attachment is never empty, so never this."""
    return probe_status == 200 and bool(probe_empty) and page_detail and page_empty


def norm_key(target: str) -> str:
    """postback:<target> as the crawler keys a button (ctlNN as ctl*)."""
    return "postback:" + _CTL.sub("ctl*", target)
