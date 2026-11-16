"""The export batch, T1b (plan docs/superpowers/plans/2026-11-16-export-batch.md; the content session's question 4,
11-16): a page that was good before and this time answers 200 as an empty notice template or a sign-in page keeps its
page resources as they were, so that the attachments it linked still name it (链到它的网页) while the export falls back
on its last good answer. A page never good before is read as always. The real crawler on a fake network; made-up
pages only."""

from __future__ import annotations

import pytest

from tests.archive.test_archive import seed, snap
from tests.archive.test_pilot_fixes import make, work

URL = "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=5"
PDF = "https://c.nknu.edu.tw/aca/uploadfile/form5.pdf"
GOOD = """<html><head><title>教務處</title></head><body><div id="DataFormView">
<span id="lbl_Title">選課說明會</span><span id="lbl_Contents">本週五舉行選課說明會，請同學準時出席。</span>
<span id="lbl_FileLink"><a href="/aca/uploadfile/form5.pdf">報名表</a></span></div></body></html>"""
EMPTY = """<html><head><title>教務處</title></head><body><div id="DataFormView">
<span id="lbl_Title"></span><span id="lbl_Contents"></span><span id="lbl_FileLink"></span></div>
<a href="/aca/sitemap.aspx">網站導覽</a></body></html>"""
LOGIN = """<html><head><title>教務處</title>
<meta http-equiv="refresh" content="0;url=https://accounts.google.com/ServiceLogin"></head><body></body></html>"""


def _res(f, uid: int) -> list[str]:
    return sorted(r["res_key"] for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (uid,)))


def _answer(answers: list):
    """nid=5 answers in turn; any other number (the crawler's probe for a notice that does not exist) an empty
    template, as c.nknu answers one."""
    def answer(target):
        url = str(getattr(target, "url", target))
        return answers.pop(0) if "nid=5" in url else snap(url, 200, html=EMPTY)
    return answer


def _crawl_twice(tmp_path, second: str):
    answers = [snap(URL, 200, html=GOOD), snap(URL, 200, html=second)]
    crawler, net, clock = make(tmp_path, {("GET", "/aca/newsdetail.aspx"): _answer(answers),
                                          ("GET", "/aca/uploadfile/form5.pdf"): snap(PDF, 200, body=b"%PDF-1.4 x\n")})
    seed(crawler, URL)
    queue = "ip:c.nknu.edu.tw"
    work(crawler, net, queue)
    f = crawler.frontier
    uid = f.row(URL)["id"]
    before = _res(f, uid)
    f.x("UPDATE url SET state='pending', reason='chain_restart', prio=0, next_at=0 WHERE id=?", (uid,))
    clock.t += 3600
    work(crawler, net, queue)
    return f, uid, before


@pytest.mark.parametrize("second", [EMPTY, LOGIN], ids=["empty_template", "sign_in"])
def test_a_page_good_before_keeps_its_resources_when_this_time_is_no_good(tmp_path, second):
    f, uid, before = _crawl_twice(tmp_path, second)
    assert any("form5.pdf" in k for k in before)
    assert _res(f, uid) == before


def test_a_page_good_again_takes_its_new_resources(tmp_path):
    newer = GOOD.replace("form5.pdf", "form6.pdf")
    f, uid, before = _crawl_twice(tmp_path, newer)
    assert any("form6.pdf" in k for k in _res(f, uid)) and _res(f, uid) != before


@pytest.mark.parametrize(("kept", "want"), [([], False), (["empty_detail"], False), (["login", "thin"], False),
                                           ([None], True), (["empty_detail", None], True), (["js_hint"], True)])
def test_only_a_good_kept_answer_counts_as_good_before(tmp_path, kept, want):
    """A page never good before (no kept answer, or only a sign-in, a soft 404, an empty template, a thin page) is read
    as always: its resources are what this time's answer links."""
    crawler, _net, _clock = make(tmp_path, {})
    f = crawler.frontier
    seed(crawler, URL)
    uid = f.row(URL)["id"]
    for n, flags in enumerate(kept):
        f.x("INSERT INTO url_version(url_id, kept_at, status, kind, sniff, flags, warc, warc_offset) "
            "VALUES (?,?,?,?,?,?,?,?)", (uid, n, 200, "page", "html", flags, "warc/x.warc.gz", n))
    assert crawler._good_before(uid) is want
