"""page_links on the platforms the page rule of plan 2026-11-08 T2 reads: a link's zone follows pagetext.page_roots, so
the zones assemble trusts (parents, context titles, the menu-picture rule I4) agree with the page's text on every
platform, not only on c.nknu. The pages are the made-up templates of the to-text tests; the last test runs assemble on
a Google Sites page read by the page rule (T2 review: Review Focus 2's body pictures)."""

from __future__ import annotations

from nknu.content import links
from nknu.content.documents import MENU_IMAGE
from nknu.content.links import page_links
from nknu.totext.pagetext import page_roots, page_text
from tests.content.test_assemble import JPEG, by_id, run_assemble
from tests.totext.test_pages_1108 import (
    ACA,
    CLV2,
    CLV2_HOME,
    DELETED,
    GSITE,
    GSITE_PAGE,
    GSITE_TEXT,
    GSITE_UI,
    NOTICE,
    NOTICE_HTML,
    STAFF,
    WEB,
    clv2_page,
    gsite_page,
    webidx_page,
)


def zones(html: bytes, url: str) -> dict[str, str]:
    return {link.anchor: link.zone for link in page_links(html, url)}


def test_content_roots_are_the_page_rules_roots():
    assert links.content_roots is page_roots


def test_sso_web_page_links_in_its_content_area_are_content_and_its_menus_nav():
    body = STAFF + '<p>詳見<a href="/uploads/595/plan.pdf">交通安全計畫</a></p>'
    # T2 review (deliberate): the breadcrumb nav inside the profile's area is chrome there too, so its 首頁 is nav
    assert zones(webidx_page(body), WEB + "&menuid=518") == {
        "生活輔導組": "nav", "關於生輔組": "nav", "膳食服務": "nav", "生輔組成員": "nav",  # nav bar and side card
        "首頁": "nav", "交通安全計畫": "content"}  # the breadcrumb; the text, inside the profile's area


def test_sso_web_detail_attachments_are_attachments():
    detail = ('<div class="news-detail-view"><div class="detail-header"><h3>停修公告</h3></div>'
              '<div class="detail-body"><p>申請期間</p></div><ul class="detail-attachments"><li class="file-item">'
              '<a href="/uploads/595/apply.pdf">申請表</a></li></ul></div>')
    got = zones(webidx_page(detail), WEB + "&selpage=MoreNews&newsIdx=1326")
    assert got["申請表"] == "attachments" and got["首頁"] == "nav" and got["膳食服務"] == "nav"  # 首頁: T2 review


def test_a_home_without_a_content_area_has_content_in_its_body():
    # cLv2's home matches none of the profile's areas: its body is read, the header's menu is not
    got = [(link.anchor, link.zone) for link in page_links(clv2_page(CLV2_HOME), CLV2)]
    assert got == [("校安中心(原軍訓室)", "nav"), ("同仁介紹", "nav"), ("折抵役期", "nav"), ("折抵役期", "content")]


def test_google_sites_menus_are_nav_and_the_page_is_content():
    html = gsite_page(GSITE_TEXT + f'<p><a href="{GSITE}/%E8%B3%87%E6%BA%90">中心資源</a></p>')
    assert zones(html, GSITE_PAGE) == {"首頁": "nav", "中心沿革": "nav", "聯絡我們": "nav", "中心資源": "content"}


def test_links_in_chrome_roles_are_nav():
    # T2 review: a link in an element of a chrome role is a menu's, as one in a nav is; under aria-hidden it is read
    # (a Wix gallery's photos sit there)
    html = gsite_page(GSITE_UI + f'<div role="navigation"><a href="{GSITE}/a">麵包屑</a></div>'
                      f'<form role="search"><a href="{GSITE}/b">進階搜尋</a></form>'
                      f'<div aria-hidden="true"><a href="{GSITE}/c">活動相簿</a></div>'
                      f'<p><a href="{GSITE}/d">中心資源</a></p>')
    got = zones(html, GSITE_PAGE)
    assert (got["麵包屑"], got["進階搜尋"], got["活動相簿"], got["中心資源"]) == ("nav", "nav", "content", "content")


def test_google_sites_pictures_in_the_body_are_inlined_and_menu_pictures_are_menu_images(tmp_path):
    # T2 review (Review Focus 2): read by the page rule, a Google Sites page shows its body picture, whose OCR text
    # assemble puts in its place; the logo in its header and the picture in its side card are chrome, carried only
    # outside what is read, so I4 makes them menu images (the build leaves them out and counts them)
    body, logo, side = GSITE + "/img/body.jpg", GSITE + "/img/logo.png", GSITE + "/img/side.jpg"
    html = (f'<html><body><header><img src="{logo}"><nav><a href="{GSITE}">首頁</a></nav></header>'
            f'<aside><img src="{side}"></aside><div class="page"><p>中心沿革說明</p><img src="{body}"></div>'
            '</body></html>').encode()
    page = page_text(html, GSITE_PAGE)
    assert page.images == (body,)
    rows = [{"type": "网页", "url": GSITE_PAGE, "file": "pages/0001.html", "data": html, "title": "中心沿革"},
            {"type": "图片", "url": body, "file": "images/0001-b.jpg", "data": JPEG + b"b", "linked": [GSITE_PAGE]},
            {"type": "图片", "url": logo, "file": "images/0002-l.png", "data": JPEG + b"l", "linked": [GSITE_PAGE]},
            {"type": "图片", "url": side, "file": "images/0003-s.jpg", "data": JPEG + b"s", "linked": [GSITE_PAGE]}]
    results = {"1": ({"page": {"title": page.title, "album": page.album, "images": list(page.images)}}, page.text),
               "2": ({}, "歷任主管合照與中心簡介"), "3": ({}, "學生輔導中心的標誌文字"), "4": ({}, "側欄的活動海報說明文字")}
    records, report = run_assemble(tmp_path, rows, results, old=None)
    r = by_id(records)
    assert r["p1"]["text"].splitlines() == ["中心沿革說明", "歷任主管合照與中心簡介"]
    assert (report.images_inlined, report.images_menu_only, report.image_docs) == (1, 2, 0)
    assert "i2" not in r and (r["i3"]["kind"], r["i4"]["kind"]) == (MENU_IMAGE, MENU_IMAGE)
    assert r["i3"]["nav_linked"] == r["i4"]["nav_linked"] == GSITE_PAGE


def test_a_deleted_notice_is_all_nav():
    assert set(zones(DELETED, NOTICE).values()) == {"nav"}


def test_a_cnknu_notice_keeps_its_zones():
    assert zones(NOTICE_HTML, NOTICE) == {"首頁": "nav", "借用要點": "nav", "停修申請表.pdf (12 Kb)": "attachments",
                                          "會議資料.zip": "attachments"}
    assert zones(NOTICE_HTML, ACA + "page.aspx?pn=1") == zones(NOTICE_HTML, NOTICE)  # its area holds the same links
