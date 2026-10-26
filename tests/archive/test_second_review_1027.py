"""The second review of the 830c1b7 fixes (10-27, workflow review of `git diff 830c1b7`; design §23.9), each as a test
written before its fix, all 【Claude 的执行办法】 unless marked:

A1 ④ a download answer that names a file of a format sniff knows (計畫.pdf, x.docx …) but is no such file (a line of
     text, an error page) is not_file, never ok; a short text or page with the school's 「檔案不存在」 is the school's
     gone page (③) whatever its name. A real file named so, or a named text file, stays ok.
A2   a download's or a detail's own POST answered with a redirect (the session sent back to SSO's home, a sign-in
     page) is a failed press as the export GET's is: the chain walks again from its GET once, the second time the
     download is not_file:redirect and the detail mismatch (redirect); never login, never 学校那边缺, no Location queued.
B2   (【用户同意 Claude 的建议】's B, how: 【Claude 的执行办法】) a Google Sites page read again only for its pictures'
     new addresses keeps its stored answer when that reading fails (403, a timeout): the page stays ok, one request
     more, its pictures not got wait for round two; a page stored with no 200 is never read again for that.
C1   (B's sibling C) `nknu archive retry data` puts a data address's read that came to nothing back (failed, mismatch,
     the school's error), for a new request; the timeline's other link fields (media.link, media.thumbnail, the HTML
     of media.caption and media.credit) are read too.
D3   a Google 5xx is Google's server's error in the dashboard's words, never 学校服务器出错.
verify's check 6 says what a download's not_file is in words when its reason is empty (a web page), never None.

All offline: fake networks, fake clocks; titles are the school's own or made up, no person's name."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import google_api, links, monitor, profiles, progress, sniff
from nknu.archive.crawler import hold_run_lock
from nknu.archive.frontier import Frontier
from nknu.crawl.fetch import FetchError
from tests.archive import test_clv2_timeline_1027 as tl
from tests.archive import test_gsites_images_1027 as gs
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_pilot_fixes import PDF, SSO_Q, work
from tests.archive.test_pilot_fixes import make as make_press
from tests.archive.test_postback import MORE, fx
from tests.archive.test_sso_download_1027 import BTN0, CLV2, EXPORT, by_target, clv2, downloads

# ---------------------------------------------------------------- A1 ④: a name that says a file the answer is not


@pytest.mark.parametrize("answer, reason", [
    (snap(EXPORT, body="檔案不存在".encode(), headers={
        "content-type": "text/plain; charset=utf-8", "content-disposition": 'attachment; filename="計畫.pdf"'}),
     "not_file:saying:gone_page"),
    (snap(EXPORT, body=b"Object reference not set to an instance of an object.", headers={
        "content-type": "text/plain", "content-disposition": "attachment; filename=x.docx"}),
     "not_file:name_mismatch:docx/text"),
    (snap(EXPORT, html="<html><body><p>系統發生錯誤，請洽系統管理員</p></body></html>",
          headers={"content-disposition": 'attachment; filename="報名表.XLS"'}), "not_file:name_mismatch:xls/html"),
    (snap(EXPORT, body=b'{"error":"no file"}', headers={
        "content-type": "application/json", "content-disposition": "attachment; filename=a.odt"}),
     "not_file:name_mismatch:odt/json"),
    (snap(EXPORT, body=b"\x00\x01\x02garbage", headers={
        "content-type": "application/octet-stream", "content-disposition": "attachment; filename=a.jpg"}),
     "not_file:name_mismatch:jpg/other"),
])
def test_a_named_answer_that_is_not_the_file_its_name_says_is_never_ok(tmp_path, answer, reason):
    crawler, net, _clock = clv2(tmp_path, answer)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    rows = by_target(f, BTN0)
    assert rows
    for r in rows:
        assert (r["state"], r["reason"], r["result"]) == ("done", reason, "not_file"), dict(r)
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (CLV2,))
    assert not level or level[0][0] not in ("complete", "complete_school")


@pytest.mark.parametrize("answer, sniffed, name", [
    (snap(EXPORT, body=PDF, headers={"content-type": "application/pdf",
                                     "content-disposition": 'attachment; filename="計畫.pdf"'}), "pdf", "計畫.pdf"),
    # a PDF a server sends with a line break first: its signature within the first 1,024 bytes, as readers allow
    (snap(EXPORT, body=b"\r\n" + PDF, headers={"content-type": "application/pdf",
                                               "content-disposition": 'attachment; filename="計畫.pdf"'}),
     "text", "計畫.pdf"),
    (snap(EXPORT, body="報名表欄位說明\n".encode(), headers={
        "content-type": "text/plain", "content-disposition": 'attachment; filename="說明.txt"'}), "text", "說明.txt"),
    (snap(EXPORT, body=b"PK\x03\x04not really a zip but a 7z is no format sniff knows", headers={
        "content-type": "application/octet-stream", "content-disposition": "attachment; filename=a.7z"}),
     "zip", "a.7z"),
])
def test_a_named_file_that_is_what_its_name_says_or_a_name_sniff_cannot_judge_stays_ok(tmp_path, answer, sniffed,
                                                                                         name):
    crawler, net, _clock = clv2(tmp_path, answer)
    work(crawler, net, SSO_Q)
    for r in downloads(crawler.frontier):
        assert (r["state"], r["reason"], r["sniff"], r["filename"], r["result"]) == ("done", None, sniffed, name,
                                                                                       "ok"), dict(r)


@pytest.mark.parametrize("body, sniffed, name, why", [
    (b"abc", "text", "a.pdf", "name_mismatch:pdf/text"),
    (b"<html>x</html>", "html", "A.PDF", "name_mismatch:pdf/html"),
    (b"abc", "other", "b.pptx", "name_mismatch:pptx/other"),
    (b"abc", "xml", "c.tif", "name_mismatch:tif/xml"),
    (b"x", "ooxml:docx", "a.doc", None),  # a .doc that is a docx, a .doc that is RTF: files all the same
    (b"x", "rtf", "a.doc", None),
    (b"x", "ole", "a.xlsx", None),
    (b"x", "other", "a.rar", None),  # sniff knows no RAR, 7z: it cannot say they are not
    (b"x", "text", "a.csv", None),
    (b"x", "text", "a", None),
    (b"x", "html", "a.htm", None),  # a page named a page: no name says otherwise (its sniff says not_file elsewhere)
])
def test_why_a_named_answer_is_no_file(body, sniffed, name, why):
    assert sniff.not_file(body, sniffed, name) == why


def test_a_short_answer_with_the_schools_words_is_its_gone_page_whatever_its_name():
    assert profiles.file_says("檔案不存在") is not None
    assert profiles.file_says("檔案不存在").flag == profiles.GONE_PAGE
    long_page = "<html><body>" + "<p>課程說明</p>" * 200 + "<p>檔案不存在的話請洽承辦人</p></body></html>"
    assert profiles.file_says(long_page) is None  # a long page that mentions the words in passing is no such answer


# ---------------------------------------------------------------- A2: the press's own POST redirects


def test_a_download_press_sent_back_to_sso_home_walks_again_once_then_not_file_never_login(tmp_path):
    login = "https://sso.nknu.edu.tw/"
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF), press=lambda t: snap(t.url, 302, headers={
        "location": login}))
    work(crawler, net, SSO_Q, rounds=60)
    f = crawler.frontier
    first = by_target(f, BTN0)[0]
    assert (first["state"], first["reason"]) == ("failed", "press_redirect")
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    live = [r for r in downloads(f)[1:] if r["state"] != "superseded"]
    assert live and all((r["state"], r["reason"], r["location"], r["result"]) ==
                        ("done", "not_file:redirect", login, "not_file") for r in live), [dict(r) for r in live]
    assert f.q("SELECT count(*) FROM url WHERE url=?", (login,))[0][0] == 0  # the Location is never queued
    assert not [u for m, u in net.seen if u == EXPORT]
    results = {r["result"] for r in f.q("SELECT result FROM page_resource WHERE page_id=(SELECT id FROM url WHERE "
                                        "url=?)", (CLV2,))}
    assert "login" not in results
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (CLV2,))[0][0]
    assert level not in ("complete", "complete_school")
    # no form state was posted twice: every POST came from a page of its own
    srcs = f.q("SELECT form_src_warc, form_src_offset, target FROM action WHERE kind='download' AND status IS NOT NULL")
    assert len({tuple(r) for r in srcs}) == len(srcs)


def test_a_detail_press_sent_back_to_sso_home_walks_again_once_then_mismatch_never_login(tmp_path):
    login = "https://sso.nknu.edu.tw/"
    crawler, net, _clock = make_press(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): lambda t: snap(t.url, 302, headers={"location": login}),
    }, queues={"sso.nknu.edu.tw": SSO_Q})
    seed(crawler, MORE)
    work(crawler, net, SSO_Q, rounds=60)
    f = crawler.frontier
    details = f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id WHERE a.kind='detail' "
                  "ORDER BY a.id")
    assert (details[0]["state"], details[0]["reason"]) == ("failed", "press_redirect")
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    live = [r for r in details[1:] if r["state"] != "superseded"]
    assert len(live) == 2 and all((r["state"], r["reason"], r["location"], r["result"]) ==
                                  ("mismatch", "redirect", login, "mismatch") for r in live), [dict(r) for r in live]
    assert f.q("SELECT count(*) FROM url WHERE url=?", (login,))[0][0] == 0
    results = {r["result"] for r in f.q("SELECT result FROM page_resource WHERE page_id=(SELECT id FROM url WHERE "
                                        "url=?)", (MORE,))}
    assert "login" not in results and "mismatch" in results
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (MORE,))[0][0]
    assert level not in ("complete", "complete_school")
    srcs = f.q("SELECT form_src_warc, form_src_offset, target FROM action WHERE kind='detail' AND status IS NOT NULL")
    assert len({tuple(r) for r in srcs}) == len(srcs)


# ---------------------------------------------------------------- B2: a reading again for new addresses that fails


class _RereadFails(gs.Site):
    """The counselling centre's fake site whose page, read again for its pictures' new addresses, answers `mode`:
    403, a timeout, or a sign-in page. The page's stored row is kept as it stood right before that reading."""

    def __init__(self, clock, mode, **kw):
        super().__init__(clock, **kw)
        self.mode, self.crawler, self.before = mode, None, None

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        if target.url == gs.HOME and self.reading.get(gs.HOME, 0) >= 1:
            self.before = dict(self.crawler.frontier.row(gs.HOME))
            self.seen.append((target.method, target.url, dict(target.headers)))
            self.clock.t += 1.0
            self.reading[gs.HOME] += 1
            if self.mode == "403":
                return snap(gs.HOME, 403, body=b"<html>denied</html>")
            if self.mode == "login":  # a sign-in page (200): the page's own reading would empty its page_res
                return snap(gs.HOME, html='<html><head><title>登入</title></head><body><form action="/login" '
                                          'method="post"><input name="u"><input type="password" name="p"></form>'
                                          '</body></html>')
            exc = FetchError(f"{gs.HOME}: timed out")
            exc.__cause__ = httpx.ReadTimeout("timed out")
            raise exc
        return super().fetch(target, rate, max_bytes, pass_statuses)


KEPT = ("status", "attempts", "final_url", "ctype", "bytes", "sha256", "warc", "warc_offset", "warc_len",
        "title", "flags", "kind", "page_type", "sniff", "text_chars", "n_links")


@pytest.mark.parametrize("mode", ["403", "timeout", "login"])
def test_a_page_read_again_for_new_picture_addresses_keeps_its_answer_when_that_reading_fails(tmp_path, mode):
    clock = Clock()
    net = _RereadFails(clock, mode, good={("h", 1): 1})  # the first picture comes, the second answers 403
    crawler = gs.make(tmp_path, net)
    net.crawler = crawler
    seed(crawler, gs.HOME)
    f = crawler.frontier
    keys_before = None
    for _ in range(6):
        gs.work(crawler, net, rounds=80)
        if keys_before is None:
            keys_before = set(gs.entries(f, gs.HOME))
        clock.t += 3 * 3600
    assert net.reading[gs.HOME] == 2  # the first reading and the one that failed: nothing more
    row = f.row(gs.HOME)
    assert net.before is not None and row["state"] == "done"
    assert {k: row[k] for k in KEPT} == {k: net.before[k] for k in KEPT}  # its stored answer, attempts unspent
    assert net.before["reason"] == "gsimg_reread" and row["reason"] is None  # the reason it was stored with
    gs.ok_type(f, gs.HOME, gs.PAGE2)  # the browser check's verdict (not this test's)
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == "ok"
    got = gs.entries(f, gs.HOME)
    assert set(got) == keys_before  # its pictures' entries stand, none twice
    body = {k: r for k, r in got.items() if not k.endswith("/yuynLe")}
    needs = sorted(r["need"] for r in body.values())
    assert needs.count("must") == 1 and needs.count("round2") == len(body) - 1  # the one got; the rest round two
    assert all(r["why"] == links.GOOGLE_SITES_ROUND2 for r in body.values() if r["need"] == "round2")
    level = f.q("SELECT level, label FROM page_completeness WHERE id=?", (row["id"],))[0]
    assert level["level"] not in ("incomplete", "failed") and level["label"] == "round2_img", tuple(level)
    assert f.q("SELECT count(*) FROM event WHERE code='gsimg_reread_failed'")[0][0] == 1
    said = progress.plain_error("gsimg_reread_failed")  # the dashboard's words for it, never 「抓取失败：隔 30 分钟再试」
    assert said == progress.PLAIN["gsimg_reread_failed"] and "保留原来存好的答案" in said


def test_a_page_stored_with_no_200_is_never_read_again_for_new_picture_addresses(tmp_path):
    clock = Clock()
    net = gs.Site(clock)
    crawler = gs.make(tmp_path, net)
    seed(crawler, gs.HOME)
    f = crawler.frontier
    row = f.row(gs.HOME)
    f.x("UPDATE url SET state='done', status=403, reason='http_403', kind='page' WHERE id=?", (row["id"],))
    assert crawler._gsimg_reread(row["id"]) is False
    assert (f.row(gs.HOME)["state"], f.row(gs.HOME)["status"]) == ("done", 403)


# ---------------------------------------------------------------- C1: retry data; the timeline's other link fields


def _failed_read(tmp_path, data, status=200, ctype="application/json"):
    clock = Clock()
    net = tl.Net(clock, tl.answer(data, status=status, ctype=ctype), pages={tl.HOME: tl.page()})
    crawler = tl.make(tmp_path, net)
    seed(crawler, tl.HOME)
    tl.work(crawler, net, rounds=3)
    f = crawler.frontier
    for _ in range(3):  # a 5xx is read again twice more before it is failed
        due = f.q("SELECT next_at FROM action WHERE kind='data' AND state='pending'")
        if not due:
            break
        clock.t = due[0][0] + 1
        tl.work(crawler, net, rounds=3)
    return crawler, net, clock


@pytest.mark.parametrize("data, status, ctype, result", [
    ({"x": 1}, 500, "text/html", "failed"),
    (b"<html><body>Runtime Error</body></html>", 200, "text/html", "mismatch"),
    ({"bErrOn": True, "cMessage": "系統忙碌", "oData": None}, 200, "application/json", "server_error"),
])
def test_retry_data_reads_a_data_address_that_came_to_nothing_again_with_a_new_request(tmp_path, data, status,
                                                                                         ctype, result):
    crawler, net, _clock = _failed_read(tmp_path, data, status, ctype)
    f = crawler.frontier
    assert f.q("SELECT result FROM action_result WHERE kind='data'")[0][0] == result
    sent = len(net.posts())
    assert crawler.chains.retry_data() == 1
    a = f.q("SELECT * FROM action WHERE kind='data'")[0]
    assert (a["state"], a["attempts"], a["next_at"]) == ("pending", 0, 0)
    assert a["reason"].startswith("retried: ")
    net.timeline = tl.answer(tl.EMPTY_NEW)
    tl.work(crawler, net, rounds=3)
    assert len(net.posts()) == sent + 1  # one new request
    got = f.q("SELECT result, reason FROM action_result WHERE kind='data'")[0]
    assert (got["result"], got["reason"]) == ("ok", "read:empty")
    assert f.q("SELECT count(*) FROM action WHERE kind='data'")[0][0] == 1
    assert crawler.chains.retry_data() == 0  # nothing left to put back: an ok read is never read again


def test_the_retry_data_command_refuses_while_a_run_holds_the_directory_and_says_what_it_put_back(tmp_path):
    crawler, net, _clock = _failed_read(tmp_path, {"x": 1}, 500, "text/html")
    crawler.frontier.close()
    net.sink.close()
    lock = hold_run_lock(tmp_path)
    try:
        got = CliRunner().invoke(app, ["archive", "retry", "data", "--dir", str(tmp_path)])
    finally:
        lock.close()
    assert got.exit_code == 1 and "archive run 正在用这个目录" in got.output
    got = CliRunner().invoke(app, ["archive", "retry", "data", "--dir", str(tmp_path)])
    assert got.exit_code == 0, got.output
    assert "1 个网站" in got.output


def test_the_timelines_other_link_fields_are_read_too():
    events = {"bErrOn": False, "cMessage": "", "oData": [{
        "title": {"media": {"url": "", "link": "https://sso.nknu.edu.tw/Upload/title-link.pdf"}},
        "events": [{"start_date": {"year": "2026"},
                    "media": {"url": "", "link": "https://c.nknu.edu.tw/affair/link.pdf",
                              "thumbnail": "https://sso.nknu.edu.tw/Upload/thumb.jpg",
                              "caption": '說明見<a href="https://c.nknu.edu.tw/affair/caption.pdf">此</a>',
                              "credit": '<img src="https://sso.nknu.edu.tw/Upload/credit.png">'}}]}]}
    got = profiles.read_data_api(profiles.CLV2_TIMELINE, json.dumps(events, ensure_ascii=False))
    assert (got.state, got.reason) == ("done", "read:events:1")
    page = links.extract(got.html, tl.HOME)
    hrefs = {href for href, _text in page.links}
    srcs = {img.src for img in page.images}
    assert {"https://c.nknu.edu.tw/affair/link.pdf", "https://c.nknu.edu.tw/affair/caption.pdf",
            "https://sso.nknu.edu.tw/Upload/title-link.pdf"} <= hrefs
    assert {"https://sso.nknu.edu.tw/Upload/thumb.jpg", "https://sso.nknu.edu.tw/Upload/credit.png"} <= srcs


def test_a_timeline_events_link_only_in_media_link_reaches_the_home_page(tmp_path):
    events = {"bErrOn": False, "cMessage": "", "oData": [{"events": [
        {"start_date": {"year": "2026"}, "media": {"url": "", "link": "https://c.nknu.edu.tw/affair/only-link.pdf"}}]}]}
    clock = Clock()
    net = tl.Net(clock, tl.answer(events), pages={tl.HOME: tl.page()})
    crawler = tl.make(tmp_path, net)
    seed(crawler, tl.HOME)
    tl.work(crawler, net, rounds=3)
    f = crawler.frontier
    home = f.row(tl.HOME)
    targets = {r["dst"].lower() for r in f.q("SELECT dst FROM link WHERE src=?", (home["id"],))}
    assert "https://c.nknu.edu.tw/affair/only-link.pdf" in targets
    keys = {r["res_key"].lower() for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (home["id"],))}
    assert "https://c.nknu.edu.tw/affair/only-link.pdf" in keys


# ---------------------------------------------------------------- D3: a Google 5xx is Google's


@pytest.mark.parametrize("reason", ["api:backendError", "api:internalError", "api:http_503",
                                    "www.googleapis.com: api:backendError"])
def test_a_google_5xx_is_googles_server_never_the_schools(reason):
    assert progress.error_code(reason) == "google_5xx"
    assert "学校" not in progress.plain_error(reason) and "Google" in progress.plain_error(reason)


def test_a_google_5xx_request_in_the_recent_errors_is_googles(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    now = time.time()
    f.x("INSERT INTO request(queue, host, url, method, purpose, started, ended, status) VALUES (?,?,?,?,?,?,?,?)",
        ("google", google_api.API_HOST, "https://www.googleapis.com/drive/v3/files/x?alt=media", "GET", "page",
         now - 60, now - 59, 503))
    got = progress._errors(f.conn, now)
    assert [g["code"] for g in got if g["level"] == "request"] == ["google_5xx"]
    assert all("学校" not in g["plain"] for g in got)
    f.close()


# ---------------------------------------------------------------- verify check 6: no None in its words


def test_check_six_says_a_downloads_web_page_in_words_when_its_reason_is_empty(tmp_path):
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF), press=lambda t: snap(
        t.url, html="<html><body><p>請稍候</p></body></html>",
        headers={"content-disposition": "attachment; filename=wait.htm"}))
    work(crawler, net, SSO_Q)
    rows = downloads(crawler.frontier)
    assert rows and all((r["reason"], r["result"]) == (None, "not_file") for r in rows)
    six = next(c for c in monitor.checks(crawler.frontier) if c.no == "6")
    lines = [x for x in six.lines if BTN0 in x]
    assert lines and "None" not in lines[0] and "回来是网页" in lines[0]
