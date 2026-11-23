"""Blueprint item 22, part 1 (the owner 11-09 「照这个做」, relayed by the content session:
research/crawler_lab_2026-11-04/item22_1109/peer_notes.md): three c.nknu page types hold their words outside the
platform's content area (profiles.CNKNU.content_css), so that the content stage read almost nothing of them. The
crawler session's read-only survey of round one's archive (research/crawler_lab_2026-11-04/item22_1109/areas.md) found
where each one's words sit and chose six selectors (【Claude 的执行办法】), each matching on round one's 28,496 c.nknu
pages and button answers only the pages named here:
- 所友名錄's detail page (c.nknu.edu.tw/tg/student_detail.aspx): its record in table.EditArea, not the div.well around
  it (that adds a button link); on tecs the same table holds an event's data on activeorder.aspx (26 pages), read too,
  and the form of activecancel.aspx (2 pages), now read alone in place of its whole page, mostly menus (both
  accepted); on the old template of a teacher's page it sits in #DataFormView, read already, and once;
- a teacher's page (teachercorner.aspx) of the new template: its fields, photo and own HTML in .FocusMain, the
  template's main box on every page, so only the box holding a teacher's field (an id starting lbl_Tea);
- a discussion thread (forumsview.aspx): its question in the four lbl_Po* spans; #lbl_Dept, the site's heading, is
  none of them (2,158 other pages and button answers carry it).
Pages of other kinds read as before: one with #lbl_Dept and a .FocusMain holding no teacher's field, whether its own
area matches or it is read whole. The crawler's empty-area sign (design §8.3, pagetypes.measure) changes on no page
of round one: a thread with no reply now finds its area in the question, which holds words; only a thread the school
blanked would show it.

Made-up pages of the shapes areas.md describes, texts made up, names O-masked, no address or number of a person. The
crawler (links.extract) and the content stage's reader (pagetext.page_text, its public reading function, read only
here) parse them with lexbor; so does the check of what the content area matches."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from nknu.archive import links, pagetypes, profiles

# the six selectors item 22 adds to CNKNU.content_css (areas.md 第一节第 1 点, 第四节第 1 点)
AREAS_1109 = ("table.EditArea", ".FocusMain:has([id^='lbl_Tea'])", "#lbl_PoTitle", "#lbl_PoName", "#lbl_PoTime",
              "#lbl_PoText")
TG = "https://c.nknu.edu.tw/tg/"
ALUMNUS_URL = TG + "student_detail.aspx?aid=1"
TEACHER_URL = "https://c.nknu.edu.tw/pe/teachercorner.aspx?id=1"
OLD_TEACHER_URL = "https://cprc.nknu.edu.tw/teachercorner.aspx?id=1"
FORUM_URL = "https://c.nknu.edu.tw/affair/forumsview.aspx?id=1"
EVENT_URL = "https://tecs.nknu.edu.tw/activeorder.aspx?id=1"
PAGE_URL = "https://c.nknu.edu.tw/affair/page.aspx?pid=5"
WHOLE_URL = "https://c.nknu.edu.tw/affair/movieview.aspx?id=3"

MENU = ('<div class="navbar"><ul><li><a href="default.aspx">首頁</a></li><li><a href="newslist.aspx">最新消息</a></li>'
        '</ul></div>')
COUNTER = '<div class="footer">版權所有<span id="WUC_Footer_lbl_Counter">瀏覽人次：1234</span></div>'
# the doctype every one of round one's 3,092 pages of these types declares (student_detail, teachercorner, forumsview,
# tecs's activeorder and activecancel; the review's re-measure of 11-09): behind it lexbor matches #id and .class in
# their case, as on those pages; a page with none is parsed in quirks mode, where they match in any case (#lbl_aname
# would match span#lbl_AName). The made-up pages of other kinds take it too: on round one's 1,436 items with none, the
# six selectors match the same in either mode
DOCTYPE = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
           '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">')


def _page(body: str) -> str:
    return (f'{DOCTYPE}<html><head><title>測試網站</title></head><body><form method="post" id="form1">{body}</form>'
            "</body></html>")


def _row(name: str, value: str) -> str:
    return f'<tr><td class="EditArea_Title">{name}</td><td class="EditArea_Contact">{value}</td></tr>'


# 所友名錄's detail: the page's title in #MainContent, the record in div.well > table.EditArea (the field names in
# td.EditArea_Title, the values in td.EditArea_Contact's spans), a button link in the well, the breadcrumb's last item
# and the visitor counter outside. The link in a field is made up to show its zone (round one's 1,976 hold none)
ALUMNUS = _page(
    f'{MENU}<ol id="NowPos" class="breadcrumb breadcrumbCus"><li><a href="default.aspx">首頁</a></li>'
    '<li>王O明</li></ol><div class="row"><div class="col-md-12"><div id="MainContent" class="FocusText">所友名錄</div>'
    '<div class="well well-sm"><table class="EditArea">'
    + _row("姓名", '<span id="lbl_aname">王O明</span>')
    + _row("屆別", '<span id="lbl_classnoN">碩士班第十屆</span>')
    + _row("電子郵件", '<span id="lbl_email"></span>')
    + _row("畢業日期", '<span id="lbl_dateN">2020/06/30</span>')
    + _row("論文", '<span id="lbl_articleN">成人學習動機之研究 <a href="thesis.aspx?aid=1">摘要</a></span>')
    + '</table><a class="btn btn-default" href="student.aspx?pclass=1">回上頁</a></div></div></div>' + COUNTER)

# a teacher's page of the new template: the page's title in #MainContent, then .FocusMain (its next sibling) with the
# photo in one column, the fields (span#lbl_Tea*) with their names in div.col-md-8, the teacher's own HTML
# (span#lbl_WebHtml) and a button link (a#link_Bank); the breadcrumb's last item (li#NowPos) and the counter outside
TEACHER = _page(
    f'{MENU}<ol class="breadcrumb"><li><a href="default.aspx">首頁</a></li>'
    '<li id="NowPos" class="active">陳O華</li></ol>'
    '<div id="MainContent" class="FocusText">師資介紹</div><div class="FocusMain"><div class="row">'
    '<div class="col-md-4"><img id="img_Tea" src="UploadFile/TeacherPhoto/t1.jpg" alt="陳O華"></div>'
    '<div class="col-md-8"><h3><span id="lbl_TeaName">陳O華</span> <span id="lbl_TeaTitle">副教授</span></h3>'
    '<p>類別：<span id="lbl_TeaKind">專任</span></p><p>學歷：<span id="lbl_TeaEdu">某大學運動科學博士</span></p>'
    '<p>專長：<span id="lbl_TeaSpecialty">運動心理學</span></p><p>授課：<span id="lbl_TeaCourse">運動與健康</span></p>'
    '<p>網頁：<span id="lbl_TeaWeb"><a href="page.aspx?pid=9">個人網頁</a></span></p></div></div>'
    '<span id="lbl_WebHtml"><p>研究室在體育館二樓。</p><img src="UploadFile/Editor/lab.jpg"></span>'
    '<a id="link_Bank" class="btn btn-default" href="teacher.aspx?kind=1">返回</a></div>' + COUNTER)

# a teacher's page of the old template: the record in table#DataFormView > table.EditArea, read already
OLD_TEACHER = _page(
    f'{MENU}<table id="DataFormView"><tr><td><table class="EditArea">'
    + _row("姓名", "<span>林O珍</span>") + _row("專長", "<span>課程與教學</span>")
    + "</table></td></tr></table>" + COUNTER)

# a discussion thread: an old table layout with no container of its own, the side menu (div.urbangreymenu) in one
# cell, the site's heading (#lbl_Dept) and the question's four spans in a small table in the next, the replies
# (#MasterGrid) below, the footer (td.Footer) in the last row; the field names of the question's table are template
_QUESTION = (
    '<table><tr><td>主題：</td><td><span id="lbl_PoTitle">停車證何時開放申請</span></td></tr>'
    '<tr><td>發問：</td><td><span id="lbl_PoName">林O君</span> <span id="lbl_PoTime">2026/10/1 上午 10:00:00</span>'
    '</td></tr><tr><td colspan="2"><span id="lbl_PoText">請問這學期的機車停車證什麼時候開放申請？<br>'
    '<img src="UploadFile/Forum/q1.jpg"></span></td></tr></table>')
_REPLIES = ('<table id="MasterGrid"><tr><td>回覆：<span id="MasterGrid_lbl_ReText_0">十月中旬開放，請見'
            '<a href="newsdetail.aspx?nid=1">公告</a>。</span></td></tr></table>')
# the question's table with its four spans left empty: a thread the school blanked (none of round one's 655)
_BLANK_QUESTION = ('<table><tr><td>主題：</td><td><span id="lbl_PoTitle"></span></td></tr><tr><td>發問：</td><td>'
                   '<span id="lbl_PoName"></span> <span id="lbl_PoTime"></span></td></tr><tr><td colspan="2">'
                   '<span id="lbl_PoText"></span></td></tr></table>')


def forum(replies: str = _REPLIES, question: str = _QUESTION) -> str:
    return _page(
        '<table width="100%"><tr><td valign="top"><div class="urbangreymenu"><h3 class="headerbar">討論區</h3><ul>'
        '<li><a href="forums.aspx">問題列表</a></li><li><a href="default.aspx">回首頁</a></li></ul></div></td>'
        f'<td valign="top"><span id="lbl_Dept">OO處</span>{question}{replies}</td></tr>'
        '<tr><td colspan="2" class="Footer">OO處 版權所有 <a href="contact.aspx">聯絡我們</a></td></tr></table>')


# tecs's activeorder.aspx: an event's data in table.EditArea (span#lbl_AName, #lbl_ATime, #lbl_APlace …)
EVENT = _page(
    '<div class="menu"><a href="activelist.aspx">活動列表</a></div><table class="EditArea">'
    + _row("活動名稱", '<span id="lbl_AName">教學觀摩研習</span>')
    + _row("活動時間", '<span id="lbl_ATime">2026/12/01 09:00</span>')
    + _row("活動地點", '<span id="lbl_APlace">某大樓會議室</span>') + "</table>")

# negative: a page with an area of its own (#lbl_Title, #lbl_Contents) inside .FocusMain, which holds a search box too
# and no teacher's field, and the site's heading (#lbl_Dept) outside
PAGE = _page(
    f'{MENU}<div class="dept"><span id="lbl_Dept">OO處</span></div><div class="FocusMain">'
    '<div class="search"><a href="search.aspx">站內搜尋</a> 請輸入關鍵字</div>'
    '<h4><span id="lbl_Title">業務簡介</span></h4><div id="lbl_Contents"><p>本處負責校園環境維護。</p>'
    '<a href="files.aspx?id=2">相關表單</a></div></div>' + COUNTER)
# negative: a page no area of the platform matches (read whole), with the site's heading and a .FocusMain holding no
# teacher's field; adding #lbl_Dept or .FocusMain unguarded would have read it as one of them (areas.md 三·2, 三·3)
WHOLE = _page(
    '<span id="lbl_Dept">OO處</span><p class="intro">本頁收錄宣導影片。</p><div class="FocusMain"><ul>'
    '<li><a href="movieview.aspx?id=4">防災宣導影片</a></li></ul></div>')


def selectors(css: str | None) -> list[str]:
    return [s.strip() for s in (css or "").split(",")]


def _label(node) -> str:
    a = node.attributes
    if a.get("id"):
        return f"{node.tag}#{a['id']}"
    return node.tag + "".join(f".{c}" for c in (a.get("class") or "").split())


def matched(html: str, url: str) -> list[str]:
    """What the page's whole content area matches, as lexbor finds it (the crawler and the content stage parse with
    it), in document order."""
    return [_label(n) for n in LexborHTMLParser(html).css(profiles.content_css(url))]


def zones(html: str, url: str) -> dict[str, str]:
    """The zone links.extract gives each link (by href as written) and each picture (by src) of the page."""
    page = links.extract(html, url, profiles.content_css(url))
    return {**page.link_zone, **{img.src: img.zone for img in page.images}}


def read(html: str, url: str) -> str:
    """The content stage's reading of the page (pagetext.page_text, read only here)."""
    pagetext = pytest.importorskip("nknu.totext.pagetext")
    return pagetext.page_text(html.encode(), url).text


# ---------------------------------------------------------------- the platform's content area


@pytest.mark.parametrize("url", [
    ALUMNUS_URL, TEACHER_URL, "https://hrkm.nknu.edu.tw/teachercorner.aspx?id=1",
    "https://spe.nknu.edu.tw/teachercorner.aspx?id=1", "https://arts.nknu.edu.tw/teachercorner.aspx?id=1",
    OLD_TEACHER_URL, FORUM_URL, EVENT_URL])
def test_the_content_area_of_each_page_types_address_holds_the_new_selectors(url):
    css = profiles.content_css(url)
    assert css == profiles.CNKNU.content_css
    assert set(AREAS_1109) <= set(selectors(css))
    # the site's heading, on 2,158 other pages and button answers (areas.md 三·3)
    assert "#lbl_Dept" not in selectors(css)


@pytest.mark.parametrize(("html", "url", "expected"), [
    (ALUMNUS, ALUMNUS_URL, ["div#MainContent", "table.EditArea"]),  # the table, not the well around it
    (TEACHER, TEACHER_URL, ["div#MainContent", "div.FocusMain"]),
    (OLD_TEACHER, OLD_TEACHER_URL, ["table#DataFormView", "table.EditArea"]),  # the table inside: read once (below)
    (forum(), FORUM_URL, ["span#lbl_PoTitle", "span#lbl_PoName", "span#lbl_PoTime", "span#lbl_PoText",
                          "table#MasterGrid"]),
    (forum(""), FORUM_URL, ["span#lbl_PoTitle", "span#lbl_PoName", "span#lbl_PoTime", "span#lbl_PoText"]),
    (EVENT, EVENT_URL, ["table.EditArea"]),
    # nothing new: neither the site's heading nor a .FocusMain holding no teacher's field
    (PAGE, PAGE_URL, ["span#lbl_Title", "div#lbl_Contents"]),
    (WHOLE, WHOLE_URL, []),
], ids=["student_detail", "teachercorner", "teachercorner_old", "forumsview", "forumsview_no_reply", "activeorder",
        "page_with_its_area", "page_read_whole"])
def test_lexbor_finds_exactly_the_intended_elements(html, url, expected):
    assert matched(html, url) == expected


# ---------------------------------------------------------------- the crawler's zones


def test_links_and_pictures_in_the_new_areas_are_body_and_the_rest_stays_chrome():
    alumnus = zones(ALUMNUS, ALUMNUS_URL)
    assert alumnus["thesis.aspx?aid=1"] == "body"
    assert alumnus["student.aspx?pclass=1"] == alumnus["newslist.aspx"] == "chrome"  # the well's button, the menu
    teacher = zones(TEACHER, TEACHER_URL)
    # a field's link, the button link, the photo, a picture in the teacher's own HTML
    for key in ("page.aspx?pid=9", "teacher.aspx?kind=1", "UploadFile/TeacherPhoto/t1.jpg",
                "UploadFile/Editor/lab.jpg"):
        assert teacher[key] == "body", key
    assert teacher["newslist.aspx"] == "chrome"
    for html in (forum(), forum("")):
        thread = zones(html, FORUM_URL)
        assert thread["UploadFile/Forum/q1.jpg"] == "body"  # the question's picture
        assert thread["forums.aspx"] == thread["contact.aspx"] == "chrome"  # the side menu, the footer
    assert zones(forum(), FORUM_URL)["newsdetail.aspx?nid=1"] == "body"  # a reply's link, as before


def test_pages_of_other_kinds_keep_their_zones():
    page = zones(PAGE, PAGE_URL)
    assert page["files.aspx?id=2"] == "body"  # in its own area, as before
    assert page["search.aspx"] == "chrome"  # in .FocusMain but outside the area: no teacher's field
    assert zones(WHOLE, WHOLE_URL)["movieview.aspx?id=4"] == "chrome"  # no area: all chrome on a profiled site


# ---------------------------------------------------------------- the crawler's sign of an empty area (design §8.3)


def test_a_thread_shows_the_empty_area_sign_only_when_its_question_is_blank():
    """pagetypes.measure, as the crawler calls it (crawler._page): the question is now a content area of a thread. Round
    one's 15 threads with no reply matched no area before (read whole); now they find one, and it holds words: no sign,
    as areas.md 第六节 counted (js_hint 不变). A thread whose question the school blanked, with no reply (none of round
    one's 655), finds its area empty: the strong sign design §8.3 gives every found area holding nothing (a blank
    notice gets it too), so a fetch or rejudge would write js_hint on it. Not a rule of item 22: pinned so that such a
    thread is understood if it comes (the review of 11-09)."""
    css = profiles.content_css(FORUM_URL)
    for html in (forum(), forum("")):
        m = pagetypes.measure(html, content_css=css)
        assert (m.content_found, m.content_empty, m.strong) == (True, False, False)
    m = pagetypes.measure(forum("", _BLANK_QUESTION), content_css=css)
    assert (m.content_found, m.content_empty, m.strong) == (True, True, True)


# ---------------------------------------------------------------- the content stage's reading (cross-check)


ALUMNUS_WORDS = ["所友名錄", "王O明", "碩士班第十屆", "2020/06/30", "成人學習動機之研究"]
TEACHER_WORDS = ["陳O華", "副教授", "某大學運動科學博士", "運動心理學", "運動與健康", "個人網頁",
                 "研究室在體育館二樓。", "[[img:UploadFile/TeacherPhoto/t1.jpg]]"]
QUESTION_WORDS = ["停車證何時開放申請", "林O君", "2026/10/1 上午 10:00:00",
                  "請問這學期的機車停車證什麼時候開放申請？"]
# the side menu, the footer, the site's heading, a field name of the question's table
FORUM_TEMPLATE = ["問題列表", "版權所有", "OO處", "主題："]


@pytest.mark.parametrize(("html", "url", "words", "not_words"), [
    (ALUMNUS, ALUMNUS_URL, ALUMNUS_WORDS, ["回上頁", "瀏覽人次", "最新消息"]),
    (TEACHER, TEACHER_URL, TEACHER_WORDS, ["瀏覽人次", "最新消息"]),
    (forum(), FORUM_URL, [*QUESTION_WORDS, "十月中旬開放"], FORUM_TEMPLATE),
    (forum(""), FORUM_URL, QUESTION_WORDS, FORUM_TEMPLATE),
    (EVENT, EVENT_URL, ["教學觀摩研習", "2026/12/01 09:00", "某大樓會議室"], ["活動列表"]),
], ids=["student_detail", "teachercorner", "forumsview", "forumsview_no_reply", "activeorder"])
def test_the_content_stage_reads_the_fields(html, url, words, not_words):
    text = read(html, url)
    assert [w for w in words if w not in text] == []
    assert [w for w in not_words if w in text] == []


def test_the_content_stage_reads_other_pages_as_before():
    old = read(OLD_TEACHER, OLD_TEACHER_URL)
    assert old.count("林O珍") == old.count("課程與教學") == 1  # the table inside #DataFormView is read once
    page = read(PAGE, PAGE_URL)
    assert "本處負責校園環境維護。" in page and "業務簡介" in page
    assert "請輸入關鍵字" not in page and "OO處" not in page  # .FocusMain and #lbl_Dept are no area of it
    whole = read(WHOLE, WHOLE_URL)
    assert all(w in whole for w in ("OO處", "本頁收錄宣導影片。", "防災宣導影片"))  # still read whole
