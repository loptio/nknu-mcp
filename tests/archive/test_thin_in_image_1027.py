"""A page whose body is mainly a picture is no empty page (design §23 E3, 【Claude 的执行办法】, review of 830c1b7: the
health centre's clinic timetable, one of the 25 questions' answer pages, is a data: picture with a line of text, and a
fresh rehearsal called it 「页面几乎是空的」).

thin is not set on a page that holds, outside its layout areas, a data: picture of 1 KB or more (a small icon or a 1×1
placeholder is not one) or a picture left for round two (need round2: Google Sites' runtime pictures, §23 B3); such a
page, whose words alone would be thin, carries the mark in_image instead: never a gap (not one of PAGE_FLAGS), said on
the page's row of the dashboard, in verify's item 6 and in item 15's sample as 「内容在图片里（整理阶段做文字识别）」. A
page with a picture it needs (need must) was never thin, being one with a needed resource, and gets no mark (the 10-27
rehearsal: 277 such pages, most of them with a site's icons and banners, would all have said 内容在图片里).

Offline: the frontier and WARC are written straight in (test_reconcile's Arch), the page_res rows as the crawler
writes them (links.extract's pictures: a data: picture by its first characters, its zone). Made-up words and pictures;
no person's name."""

from __future__ import annotations

import base64

import pytest

from nknu.archive import links, monitor, outcome, progress, reconcile, viewer
from tests.archive.test_reconcile import BODY, SITE, Arch, W, _thin, _tpl

IN_IMAGE_WORDS = "内容在图片里（整理阶段做文字识别）"


@pytest.fixture
def arch(tmp_path):
    a = Arch(tmp_path)
    yield a
    a.close()


def data_picture(n_bytes: int, kind: str = "jpeg") -> str:
    """A data: address carrying n_bytes of (made-up) picture."""
    head = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" if kind == "jpeg" else b"GIF89a"
    return f"data:image/{kind};base64," + base64.b64encode((head + bytes(range(256)) * 64)[:n_bytes]).decode()


TIMETABLE = data_picture(40_000)  # the clinic timetable: a big picture
SPACER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"  # a 1×1 placeholder


def stored(arch, path: str, html: str, ptype: str = "t1", extra=()) -> int:
    """A page written into the WARC with the page_res rows the crawler writes for its pictures (Crawler._page: a data:
    picture is need no, why data, keyed by its first characters, with its zone)."""
    uid = arch.page(path, html, ptype)
    page = links.extract(html, f"{W}{path}")
    entries = [{"res_key": img.src, "how": "get", "role": "img", "need": "no", "why": "data", "zone": img.zone}
               for img in page.images if img.src.startswith("data:")]
    arch.f.set_page_res(uid, [*entries, *extra])
    return uid


def layout_pages(arch, n: int = 5) -> list[int]:
    return [stored(arch, f"/p{i}.html", _tpl(f"<p>第{i}篇：{BODY}</p>")) for i in range(n)]


def flags(arch, uid: int) -> set[str]:
    return set((arch.f.row_by_id(uid)["flags"] or "").split(",")) - {""}


def test_a_page_whose_body_is_a_big_data_picture_is_not_thin_and_says_its_content_is_in_the_picture(arch):
    layout_pages(arch)
    clinic = stored(arch, "/clinic.html", _tpl(f'<p>門診表</p><img src="{TIMETABLE}">'))
    got = _thin(arch)
    assert clinic not in got.thin
    assert flags(arch, clinic) == {"in_image"}
    assert arch.f.q("SELECT result FROM url_result WHERE id=?", (clinic,))[0][0] == "ok"  # no gap, nothing missing
    level = arch.f.q("SELECT level, label FROM page_completeness WHERE id=?", (clinic,))[0]
    assert tuple(level) == ("complete", "complete")


def test_a_small_icon_or_a_placeholder_is_no_picture_body_and_the_page_is_still_thin(arch):
    layout_pages(arch)
    icon = stored(arch, "/icon.html", _tpl(f'<p>短</p><img src="{data_picture(600, "gif")}">'))
    spacer = stored(arch, "/spacer.html", _tpl(f'<p>短</p><img src="{SPACER}">'))
    got = _thin(arch)
    assert sorted(got.thin) == sorted([icon, spacer])
    assert flags(arch, icon) == {"thin"} and flags(arch, spacer) == {"thin"}


def test_a_big_data_picture_in_a_layout_area_is_no_body(arch):
    layout_pages(arch)
    html = (f"<html><body><header><img src='{TIMETABLE}'><p>學校首頁</p><p>國立高雄師範大學 測試單位</p></header>"
            "<p>網站導覽 回到上方</p><div><p>短</p></div><footer>地址 高雄市測試路 1 號 電話 07-0000000</footer>"
            "</body></html>")
    banner = stored(arch, "/banner.html", html)
    assert _thin(arch).thin == [banner] and flags(arch, banner) == {"thin"}


def test_a_page_whose_pictures_wait_for_round_two_is_not_thin_and_lacks_pictures_for_the_next_round(arch):
    layout_pages(arch)
    later = {"res_key": "gsimg:0/1/h.x_6", "how": "gsimg", "role": "img", "need": "round2",
             "why": links.GOOGLE_SITES_ROUND2, "zone": "body"}
    page = stored(arch, "/gsites.html", _tpl("<p>短</p>"), extra=[later])
    assert _thin(arch).thin == [] and flags(arch, page) == {"in_image"}
    row = arch.f.q("SELECT level, label FROM page_completeness WHERE id=?", (page,))[0]
    assert row["label"] == "round2_img" and dict(outcome.LABEL_ORDER)["round2_img"] == "缺图片（下一轮补）"


def test_a_page_with_a_needed_picture_was_never_thin_and_gets_no_mark(arch):
    layout_pages(arch)
    pic = {"res_key": f"{W}/up/a.jpg", "how": "get", "role": "img", "need": "must", "zone": "body"}
    page = stored(arch, "/pic.html", _tpl(f'<p>圖</p><img src="{TIMETABLE}">'), extra=[pic])
    got = _thin(arch)
    assert got.thin == [] and got.in_image == [] and flags(arch, page) == set()  # a needed resource, as before
    attachment = {"res_key": f"{W}/up/a.pdf", "how": "get", "role": "file", "need": "must"}
    filed = stored(arch, "/filed.html", _tpl("<p>表</p>"), extra=[attachment])
    _thin(arch)
    assert flags(arch, filed) == set()


def test_a_page_with_words_enough_carries_no_mark_and_the_mark_comes_off_when_it_no_longer_holds(arch):
    layout_pages(arch)
    worded = stored(arch, "/worded.html", _tpl(f'<p>{BODY}</p><img src="{TIMETABLE}">'))
    clinic = stored(arch, "/clinic.html", _tpl(f'<p>門診表</p><img src="{TIMETABLE}">'))
    _thin(arch)
    assert flags(arch, worded) == set() and flags(arch, clinic) == {"in_image"}
    arch.f.set_page_res(clinic, [])  # read again (say) with no picture: its words alone are thin
    again = _thin(arch)
    assert again.thin == [clinic] and flags(arch, clinic) == {"thin"}


def test_reconcile_judges_the_same_as_thin_pages(arch):
    layout_pages(arch)
    clinic = stored(arch, "/clinic.html", _tpl(f'<p>門診表</p><img src="{TIMETABLE}">'))
    empty = stored(arch, "/empty.html", _tpl("<p>短</p>"))
    from tests.archive.test_reconcile import PLAN

    res = reconcile.reconcile_sites(arch.f, arch.root, [SITE], batch="t", plan=PLAN,
                                    sources=reconcile.Sources(cc_dir=None), spot=None).sites[0]
    assert res.thin_set == 1
    assert flags(arch, clinic) == {"in_image"} and flags(arch, empty) == {"thin"}


def test_in_image_is_a_known_flag_and_never_a_gap():
    assert "in_image" in outcome.FLAGS and "in_image" not in outcome.PAGE_FLAGS


def test_verify_and_the_dashboard_say_the_content_is_in_the_picture(arch):
    layout_pages(arch)
    clinic = stored(arch, "/clinic.html", _tpl(f'<p>門診表</p><img src="{TIMETABLE}">'))
    _thin(arch)
    checks = {c.no: c for c in monitor.checks(arch.f)}
    assert "内容在图片里 1 页" in checks["6"].detail and checks["6"].status == monitor.PASS
    assert any(IN_IMAGE_WORDS in x and f"{W}/clinic.html" in x for x in checks["15"].lines), checks["15"].lines
    arch.f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
             (SITE, "测试单位", "全校重要", "1", None, f"{W}/"))
    conn = progress.connect(arch.f.path)
    try:
        slow = progress.slow_snapshot(conn, 0.0)
        got = progress.site_detail(conn, SITE, 0.0, slow)
    finally:
        conn.close()
    row = next(p for p in got["pages"] if p["id"] == clinic)
    assert row["in_image"] is True
    assert all(not p["in_image"] for p in got["pages"] if p["id"] != clinic)
    assert IN_IMAGE_WORDS in viewer.progress_site_html(got)
