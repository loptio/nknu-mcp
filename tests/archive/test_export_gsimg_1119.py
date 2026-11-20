"""11-19 (the content stage's guard, after the first revisit lap): a Google Sites runtime picture changes its address
with every reading of its page. A page read again keeps the row of the picture it already got (the crawler asks for
no new address), so the page's file names the picture by an address the manifest had no row for, and the content
stage, which pairs a page's <img> with the manifest's row by address, lost the words of 33 pages' pictures. The
export writes such a picture's row under the address its page's file shows now: the same file, the row it was
fetched as, and a note of the address it was fetched by. A picture several pages share (one row, each page's file
showing it under an address of its own) has a row under each of those addresses, all of them the one file. Made-up
pages and tokens."""

from __future__ import annotations

from nknu.archive import links, profiles
from tests.archive.test_export_1027 import BASE, PIC, _add, _res
from tests.archive.test_export_step3_1108 import _with

RT = "https://sites.google.com/sitesv-images-rt/"
OLD, OLD2, NEW, NEW2 = RT + "AAAold111=w1280", RT + "AAAold222=w1280", RT + "BBBnew111=w1280", RT + "BBBnew222=w1280"
LANDED = "https://lh3.googleusercontent.com/sitesv/AAAold222=w1280"
PAGE = BASE + "poster.aspx"
PAGE2 = BASE + "poster2.aspx"


def _html(*srcs: str) -> str:
    imgs = "".join(f'<div id="h.p{i}"><img src="{s}" alt="海報{i}"></div>' for i, s in enumerate(srcs, 1))
    return f"<html><head><title>增能班海報</title></head><body><h2>增能班海報</h2>{imgs}</body></html>"


def _keys(html: str, page_id: int, url: str = PAGE) -> dict[str, str]:
    """The picture keys of a page as the crawler works them out (by where each sits), by its address in this html."""
    return links.google_sites_keys(links.extract(html, url, profiles.content_css(url)), page_id)


def _build(arch, shown: tuple[str, str]) -> None:
    """A page whose two pictures were got under OLD (a plain 200) and OLD2 (a 302 to its picture); its file shows
    `shown`."""
    page = arch.page("poster", PAGE, _html(*shown))
    one = arch.file("one", OLD, PIC, "image/jpeg", kind="image", sniff="jpeg", host="sites.google.com")
    off, sha = arch.put("two", OLD2, "text/html", b"", headers={"location": LANDED}, status=302)
    two = _add(arch.f, url=OLD2, final_url=LANDED, status=302, bytes=0, sha256=sha, warc=arch.rel, warc_offset=off,
               kind="image", kind0="image", tier=2, host="sites.google.com")
    arch.file("landed", LANDED, PIC + b"2", "image/jpeg", kind="image", sniff="jpeg", host="lh3.googleusercontent.com")
    keys = _keys(_html(*shown), page)
    for src, rid, alt in zip(shown, (one, two), ("海報1", "海報2")):
        _res(arch.f, page, keys[src], "img", how="gsimg", res_id=rid, text=alt)


def test_a_picture_whose_page_was_read_again_is_written_under_the_address_the_page_shows_now(tmp_path):
    _arch, _got, out, rows, _readme = _with(tmp_path, lambda arch: _build(arch, (NEW, NEW2)))
    page = rows[PAGE]
    text = (out / page["本地档案"]).read_text(encoding="utf-8-sig")
    assert NEW in text and NEW2 in text and OLD not in text
    for now, was in ((NEW, OLD), (NEW2, OLD2)):
        r = rows[now]
        assert r["本地档案"].startswith("images/") and r["分类"] == "抓到了", now
        assert PAGE in r["链到它的网页"] and was in r["备注"] and "换网址" in r["备注"]
        assert was not in rows  # one row a picture: no row left under an address no page shows
    assert (out / rows[NEW]["本地档案"]).read_bytes() == PIC
    assert (out / rows[NEW2]["本地档案"]).read_bytes() == PIC + b"2"  # the picture its 302 landed on


def test_a_picture_whose_page_still_shows_the_address_it_was_fetched_by_is_as_before(tmp_path):
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: _build(arch, (OLD, OLD2)))
    for url in (OLD, OLD2):
        assert rows[url]["本地档案"].startswith("images/") and "换网址" not in rows[url]["备注"]
    assert NEW not in rows and NEW2 not in rows


def test_a_page_with_no_such_picture_and_a_picture_with_no_file_are_untouched(tmp_path):
    def build(arch):
        page = arch.page("poster", PAGE, _html(NEW))
        left = _add(arch.f, url=OLD, kind="image", kind0="image", tier=2, host="sites.google.com", state="skipped",
                    reason="out:cap_image", status=None, fetched_at=None)
        _res(arch.f, page, _keys(_html(NEW), page)[NEW], "img", how="gsimg", res_id=left, text="海報1")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[OLD]["本地档案"] == "" and rows[OLD]["分类"] == "规则不抓" and NEW not in rows  # nothing to pair: as it was
    assert rows[BASE + "newsview.aspx?nid=1"]["本地档案"]  # the fixture's own pages and pictures are as they were
    assert rows[BASE + "uploadfile/pic.jpg"]["本地档案"].startswith("images/")


def _shared(arch, first: str, second: str) -> None:
    """One picture got under OLD, the part of two pages (as a banner two pages of a site show): PAGE's file shows it
    under `first`, PAGE2's under `second`. PAGE's part is the earlier row."""
    pic = arch.file("one", OLD, PIC, "image/jpeg", kind="image", sniff="jpeg", host="sites.google.com")
    for name, url, src in (("poster", PAGE, first), ("poster2", PAGE2, second)):
        page = arch.page(name, url, _html(src))
        _res(arch.f, page, _keys(_html(src), page, url)[src], "img", how="gsimg", res_id=pic, text="海報1")


def test_a_picture_two_pages_share_has_a_row_under_each_address_the_pages_show(tmp_path):
    _arch, _got, out, rows, _readme = _with(tmp_path, lambda arch: _shared(arch, NEW, NEW2))
    a, b = rows[NEW], rows[NEW2]
    assert a["本地档案"].startswith("images/") and b["本地档案"] == a["本地档案"]  # one file, two rows
    assert (out / a["本地档案"]).read_bytes() == PIC
    assert a["分类"] == b["分类"] == "抓到了" and a["存档编号"] == b["存档编号"] != ""
    assert PAGE in a["链到它的网页"] and PAGE2 in b["链到它的网页"] and PAGE not in b["链到它的网页"]
    assert OLD in a["备注"] and OLD in b["备注"] and "同一个档" in b["备注"] and "同一个档" not in a["备注"]
    assert OLD not in rows


def test_a_shared_picture_one_page_still_shows_as_fetched_keeps_its_row_and_gains_one(tmp_path):
    # the page read again is the earlier part: the row must not move away from the address the other page still shows
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: _shared(arch, NEW, OLD))
    assert rows[OLD]["本地档案"].startswith("images/") and "换网址" not in rows[OLD]["备注"]
    assert rows[NEW]["本地档案"] == rows[OLD]["本地档案"] and "同一个档" in rows[NEW]["备注"]
    # the content stage's guard finds by 链到它的网页 the page a picture's words went into: the kept row names both
    assert PAGE2 in rows[OLD]["链到它的网页"]
    assert PAGE in rows[NEW]["链到它的网页"] and rows[NEW]["分类"] == "抓到了"


def test_two_pages_that_show_a_shared_picture_under_one_address_get_no_second_row(tmp_path):
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: _shared(arch, NEW, NEW))
    assert rows[NEW]["本地档案"].startswith("images/") and OLD not in rows
    assert sum(1 for r in rows.values() if r["本地档案"] == rows[NEW]["本地档案"]) == 1
