"""The export batch, T5: the 「要人看」 write-off (plan docs/superpowers/plans/2026-11-16-export-batch.md; the owner's
11-16 decisions 1–2 of direction 2026-11-16-crawler-updates-round2-direction.md §四, after 2026-11-15 judge.md
§4.4–4.6), in the result views every reader shares (the dashboard, the page check, the export), never by writing the
archive: the school's broken links and dead short links are the school's (学校那边已经不存在); a notice the id probe
calls gone, and the school's not-found pages its deleted notices are sent to, are gone; an album page is not thin for
the photos the rules leave out; Google form pictures and short links whose way cannot be read are round two's; a
page no browser check judged is got, one with a sign of JavaScript round two's. And the export's two rows of
its own wrong category. (The school home page found in a PDF and expected to be a file, the dashboard's
not_file ①, is one row of the old archive: its expected kind is put right once on the lab,
research/crawler_design_2026-11-16/export_batch/fix_home_kind_1116.py; a rule here would break the 11-03
gone_home rule.)"""

from __future__ import annotations

import pytest

from nknu.archive import outcome
from nknu.archive.frontier import Frontier
from tests.archive.test_export_1027 import BASE, PIC, _add, make_archive
from tests.archive.test_export_step3_1108 import _with


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    fr.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', 'c.nknu.edu.tw/aca', 'ok', 5)")
    yield fr
    fr.close()


def _result(f: Frontier, **cols) -> str:
    cols.setdefault("url", BASE + f"p{len(f.q('SELECT id FROM url'))}.aspx")
    uid = _add(f, **cols)
    return f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0]["result"]


@pytest.mark.parametrize("result", ["bad_link", "short_dead"])
def test_the_schools_broken_links_and_dead_short_links_are_the_schools(result):
    assert outcome.user_bucket(result) == "gone"
    assert result in outcome.GROUPS["good"] and result in outcome.SCHOOL and result not in outcome.REVIEW


def test_a_notice_the_id_probe_calls_gone_is_gone_and_a_soft_404_still_looks_gone(f):
    assert _result(f, page_type="t:ok", sniff="html", flags="empty_detail,gone_id") == "gone"
    assert _result(f, page_type="t:ok", sniff="html", flags="soft404") == "looks_gone"


@pytest.mark.parametrize("url", ["https://tecs.nknu.edu.tw/nodata.html", "https://clcts.nknu.edu.tw/404.php"])
def test_the_schools_not_found_pages_are_gone(f, url):
    assert _result(f, url=url, host=url.split("/")[2], page_type="t:ok", sniff="html", flags="thin") == "gone"


def test_an_album_page_is_not_thin_for_the_photos_the_rules_leave_out(f):
    album = _add(f, url=BASE + "album_images.aspx?aid=1", page_type="t:ok", sniff="html", flags="thin")
    f.add_page_res(album, [{"res_key": BASE + "uploadfile/a1.jpg", "how": "get", "role": "img", "need": "no",
                            "why": "album"}])
    other = _add(f, url=BASE + "empty.aspx", page_type="t:ok", sniff="html", flags="thin")
    got = {r["id"]: r["result"] for r in f.q("SELECT id, result FROM url_result WHERE id IN (?,?)", (album, other))}
    assert got == {album: "ok", other: "thin"}


def test_a_short_link_whose_way_cannot_be_read_is_round_twos(f):
    assert _result(f, url="https://goo.gl/abc", host="goo.gl", kind="shortlink", kind0="shortlink",
                   sniff="html") == "later"
    assert outcome.user_bucket("later") == "next_round"


def test_a_google_form_picture_is_round_twos(f):
    page = _add(f, url=BASE + "form.aspx", page_type="t:ok", sniff="html")
    f.add_page_res(page, [{"res_key": "https://docs.google.com/forms-images-rt/x1", "how": "get", "role": "img",
                           "need": "must", "why": "unhandled:google"}])
    got = f.q("SELECT result FROM page_resource WHERE page_id=?", (page,))
    assert [r["result"] for r in got] == ["later"]


def test_a_page_no_browser_check_judged_is_got_and_one_with_a_sign_of_javascript_round_twos(f):
    assert _result(f, sniff="html") == "ok"  # its type never checked (no page type)
    assert _result(f, sniff="html", flags="js_hint") == "unchecked"
    assert outcome.user_bucket("unchecked") == "next_round"


# ---------------------------------------------------------------- the export's own wrong category (judge.md §4.4 ①)

def test_an_address_merged_into_another_is_left_out_by_the_rules_not_for_a_person(tmp_path):
    def build(arch):
        _add(arch.f, url=BASE + "NewsList.aspx", state="skipped", reason="out:alias", status=None, fetched_at=None)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[BASE + "NewsList.aspx"]["分类"] == "规则不抓"


def test_a_redirect_landing_on_a_row_of_its_own_is_counted_there(tmp_path):
    def build(arch):
        _add(arch.f, url=BASE + "uploadfile/pic-old.jpg", kind="image", kind0="image", tier=2, status=302,
             final_url=BASE + "uploadfile/pic.jpg", sniff=None)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[BASE + "uploadfile/pic-old.jpg"]["分类"] == "规则不抓"
    assert PIC  # the landing picture is the fixture's own row
    assert make_archive  # same fixture module
