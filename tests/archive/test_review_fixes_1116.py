"""The export batch's one review (11-16): its fixes. A notice the school deletes by sending its address elsewhere (its
host's root page, a not-found page) or by emptying it is the school's deletion: the change list counts such an answer
as gone (twice a day apart: deleted), and the export never covers a sent-away notice with its old copy (F1, F4). A
page that was good and this time answers next to nothing falls back on its last good answer (F3). Made-up pages
only."""

from __future__ import annotations

import time

from nknu.archive import export
from tests.archive import test_update_stage2_1116 as stage2
from tests.archive.test_export_1027 import BASE
from tests.archive.test_export_prev_1116 import _again, _content
from tests.archive.test_export_step3_1108 import _with
from tests.archive.test_update_stage2_1116 import DAY, SITE, World, _answer, _change, _html, _night  # noqa: F401

world = stage2.world  # the fixture, under the name the tests take it by

NOTICE2 = BASE + "newsview.aspx?nid=2"


def _moved(w, url: str, to: str) -> None:
    uid = w.f.row(url)["id"]
    w.f.x("UPDATE url SET status=302, final_url=?, sniff=NULL WHERE id=?", (to, uid))


def _emptied(w, url: str) -> None:
    w.f.x("UPDATE url SET flags='empty_detail' WHERE id=?", (w.f.row(url)["id"],))


def _twice(world, url: str, after) -> list[str]:
    """Good once, then two answers a day apart, `after(world, url)` turning each into what the school answered."""
    uid = _answer(world, url, _html("<p>公告內文</p>"))
    out = []
    for n in (0, 1):
        since = time.time()
        _answer(world, url, _html("<p>不在了</p>"))
        after(world, url)
        out.append(_change(_night(world, since, now=since + 10 + n * (DAY + 3600)), uid))
    return out


def test_an_emptied_notice_twice_a_day_apart_is_deleted(world):
    assert _twice(world, f"https://{SITE}/aca/newsview.aspx?nid=5", _emptied) == ["not_seen", "deleted"]


def test_a_page_sent_to_a_not_found_page_twice_a_day_apart_is_deleted(world):
    to = f"https://{SITE}/nodata.html"
    assert _twice(world, f"https://{SITE}/aca/newsview.aspx?nid=6", lambda w, u: _moved(w, u, to)) == \
        ["not_seen", "deleted"]


def test_a_page_sent_to_its_hosts_root_twice_a_day_apart_is_deleted(world):
    to = f"https://{SITE}/"
    assert _twice(world, f"https://{SITE}/aca/newsview.aspx?nid=7", lambda w, u: _moved(w, u, to)) == \
        ["not_seen", "deleted"]


def test_a_page_moved_to_another_page_is_not_gone(world):
    to = f"https://{SITE}/aca/newsview2.aspx?nid=8"
    uid = _answer(world, f"https://{SITE}/aca/newsview.aspx?nid=8", _html("<p>公告</p>"))
    since = time.time()
    _answer(world, f"https://{SITE}/aca/newsview.aspx?nid=8", _html("<p>公告</p>"))
    _moved(world, f"https://{SITE}/aca/newsview.aspx?nid=8", to)
    assert _change(_night(world, since, now=since + 10), uid) != "not_seen"


def test_a_notice_sent_to_its_hosts_root_is_never_covered_with_its_old_copy(tmp_path):
    def build(arch):
        _again(arch, "d2", body="<html><body>moved</body></html>", status=302, final_url="https://c.nknu.edu.tw/",
               sniff=None)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[NOTICE2]
    assert r["来源"] == "" and r["最后落地网址"] == "https://c.nknu.edu.tw/"
    assert export.NOTICE_MOVED in r["缺什么"]


def test_a_page_that_now_answers_next_to_nothing_falls_back(tmp_path):
    """A standing page answering a one-off maintenance page at night: no thin step runs at night, so its own text
    size tells (F3)."""
    maint = "<html><head><title>系統維護中</title></head><body><p>系統維護中</p></body></html>"

    def build(arch):
        arch.f.x("UPDATE url SET text_chars=? WHERE id=?", (400, arch.ids["about"]))
        _again(arch, "about", body=maint, text_chars=5)

    _arch, _got, out, rows, _readme = _with(tmp_path, build)
    r = rows[BASE + "about.aspx"]
    assert r["来源"] == export.ORIGIN_PREV and "教務處簡介" in _content(out, r).decode("utf-8-sig")


def test_a_list_that_says_it_has_no_notice_is_not_next_to_nothing(tmp_path):
    def build(arch):
        arch.f.x("UPDATE url SET text_chars=? WHERE id=?", (400, arch.ids["about"]))
        _again(arch, "about", body="<html><body>共0筆</body></html>", text_chars=4, flags="no_rows")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert rows[BASE + "about.aspx"]["来源"] == ""
