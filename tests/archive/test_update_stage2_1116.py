"""Periodic updates, stage 2 (plan docs/superpowers/plans/2026-11-16-update-stage-2.md; design 2026-11-16 §7.3-7.7):
the fingerprint of a page's own text, the night's review of what changed, the rotations, the nightly script. Made-up
pages; the list fixture is the SSO list of test_postback (its read counters)."""

from __future__ import annotations

import time

import pytest

from nknu.archive import watch
from tests.archive.test_postback import SSO_LIST, World, fx

PAGE = "https://a.nknu.edu.tw/aca/page.aspx?id=1"


def _html(body: str, extra: str = "") -> str:
    return (f"<html><head><title>頁</title><style>.x{{color:red}}</style></head><body><header>學校</header>"
            f"<nav>選單</nav><main>{body}</main><footer>頁尾 {extra}</footer><script>var n=1;</script></body></html>")


def test_a_counter_and_white_space_are_no_change():
    a = _html("<p>報名期限：11/20</p><table><tr><td>瀏覽</td><td>120</td></tr></table>")
    b = _html("<p>報名期限：  11/20 </p><table><tr><td>瀏覽</td><td>1,234</td></tr></table>")
    assert watch.fingerprint(a, PAGE) == watch.fingerprint(b, PAGE)


def test_a_deadline_moved_is_a_change():
    assert watch.fingerprint(_html("<p>報名期限：11/20</p>"), PAGE) != watch.fingerprint(_html("<p>報名期限：11/30</p>"),
                                                                                       PAGE)


def test_the_template_and_scripts_are_not_the_pages_text():
    a = _html("<p>內文</p>", extra="2026")
    b = _html("<p>內文</p>", extra="2027").replace("var n=1", "var n=2").replace("color:red", "color:blue")
    assert watch.fingerprint(a, PAGE) == watch.fingerprint(b, PAGE)


def test_a_lists_read_counters_are_no_change():
    p1 = fx("sso_list_p1.html")
    again = p1.replace(">10</", ">11</").replace(">20</", ">25</").replace(">30</", ">31</")
    assert again != p1 and watch.fingerprint(p1, SSO_LIST) == watch.fingerprint(again, SSO_LIST)
    assert watch.fingerprint(p1, SSO_LIST) != watch.fingerprint(p1.replace("停修申請時間公告", "停修申請延長"), SSO_LIST)


# ---------------------------------------------------------------- Task 2: the night's review

SITE = "a.nknu.edu.tw"
DAY = 86400.0


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.f.close()


def _answer(w, url: str, body, *, status: int = 200, ctype: str = "text/html; charset=utf-8", kind: str = "page"):
    """An answer stored as the crawler stores one (a real WARC record), the row taken first as a fetch takes it."""
    if w.f.row(url) is None:
        w.f.set_host(SITE, queue="ip:test")
        w.f.add(url, host=SITE, site=SITE, unit=None, tier=1, kind=kind, depth=1, found_via="link", found_from=None,
                owner_site=SITE, kind0=kind, added_at=time.time())
    uid = w.f.row(url)["id"]
    w.f.activate(uid)  # its answer kept first (stage 0a)
    snap = w.archive(url, body, status, {"content-type": ctype})
    sniffed = "html" if "html" in ctype else "pdf"
    w.f.finish(uid, state="done", reason=None if status == 200 else f"http_{status}", status=status, kind=kind,
               sniff=sniffed if status == 200 else None, sha256=snap.sha256, warc=w.warc, warc_offset=snap.warc_offset,
               warc_len=snap.warc_len, final_url=url, attempts=0)
    return uid


def _night(w, since: float, now: float | None = None):
    return watch.night(w.f, w.root, since, now=time.time() if now is None else now)


def _change(night, uid):
    return next(c.change for c in night.rows if c.url_id == uid)


def test_a_page_got_tonight_for_the_first_time_is_new(world):
    since = time.time() - 1
    uid = _answer(world, f"https://{SITE}/n1.aspx", _html("<p>新公告</p>"))
    assert _change(_night(world, since), uid) == "new"


def test_a_page_read_again_is_changed_or_the_same_by_its_text(world):
    a = _answer(world, f"https://{SITE}/a.aspx", _html("<p>期限 11/20</p><td>10</td>"))
    b = _answer(world, f"https://{SITE}/b.aspx", _html("<p>期限 11/20</p>"))
    since = time.time()
    _answer(world, f"https://{SITE}/a.aspx", _html("<p>期限 11/20</p><td>12</td>"))  # a counter only
    _answer(world, f"https://{SITE}/b.aspx", _html("<p>期限 11/30</p>"))
    night = _night(world, since)
    assert (_change(night, a), _change(night, b)) == ("same", "changed")
    assert night.sites_changed == [SITE]


def test_a_page_gone_twice_a_day_apart_is_deleted_and_back_when_it_comes_back(world):
    url = f"https://{SITE}/gone.aspx"
    uid = _answer(world, url, _html("<p>公告</p>"))
    t0 = time.time()
    _answer(world, url, "<html>找不到</html>", status=404)
    assert _change(_night(world, t0, now=t0 + 10), uid) == "not_seen"
    t1 = time.time()
    _answer(world, url, "<html>找不到</html>", status=404)
    assert _change(_night(world, t1, now=t0 + DAY + 3600), uid) == "deleted"
    t2 = time.time()
    _answer(world, url, _html("<p>公告</p>"))
    assert _change(_night(world, t2, now=t0 + 2 * DAY), uid) == "back"


def test_a_file_is_changed_by_its_bytes(world):
    url = f"https://{SITE}/f.pdf"
    uid = _answer(world, url, b"%PDF-1.4 one", ctype="application/pdf", kind="file")
    since = time.time()
    _answer(world, url, b"%PDF-1.4 two", ctype="application/pdf", kind="file")
    assert _change(_night(world, since), uid) == "changed"


def test_a_site_answering_one_body_everywhere_is_an_alarm(world):
    uids = [_answer(world, f"https://{SITE}/p{i}.aspx", _html(f"<p>公告 {i}</p>")) for i in range(6)]
    since = time.time()
    for i in range(6):
        _answer(world, f"https://{SITE}/p{i}.aspx", "<html><body>系統維護中</body></html>")
    night = _night(world, since)
    assert SITE in night.alarms and SITE not in night.sites_changed
    assert {_change(night, u) for u in uids} == {"changed"}
