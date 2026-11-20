"""Design §23 A (10-27, 【Claude 的执行办法】; review of 830c1b7, research/review_830c1b7_2026-10-27.md): what an SSO
download button brings back is a file only when it is one. Each test was written before its fix:

A1 an answer that is empty, or names no file and is no known file format, or carries the school's 「檔案不存在」 words,
   is not_file (连接失败, for a person), never ok; a plain attachment row answered with nothing is not_file too;
A2 an export GET that redirects (to the sign-in page, the home page, anywhere) is a failed press: the chain starts
   again from its GET once, the second time it is not_file, never login, and the Location is never queued;
A3 `nknu archive retry download` puts the downloads that came to not_file or failed back, pressing only them on a new
   GET of their list; a press answered too large is too_large (要人看), not failed;
A4 the cLv2 button (uDataContentPos2$downloadBtn0, cut down from the 10-26 pilot's WARC), the wait between the press
   and its export GET, a stop between them, and failures of that GET (refused connection, RetryLater, a timeout).

All offline: fake networks, fake clocks; the documents' titles are the school's own, no person's name."""

from __future__ import annotations

import itertools

import httpx
import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import outcome, postback, sniff
from nknu.archive.crawler import Crawler, hold_run_lock
from nknu.crawl.fetch import FetchError, RetryLater, TooLarge
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_pilot_fixes import PDF, SSO_Q, PressNet, work
from tests.archive.test_postback import fx

CLV2 = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=100061&page=4"
EXPORT = postback.file_followup(fx("clv2_download_export.html"), CLV2)
BTN0, BTN1 = "uDataContentPos2$downloadBtn0", "uDataContentPos2$downloadBtn1"
PDF_NAMED = {"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="計畫.pdf"'}


def make(tmp_path, routes, **kw):
    clock = Clock()
    net = PressNet(clock, routes)
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None, **kw)
    return crawler, net, clock


def _press_answer(target):
    return snap(target.url, html=fx("clv2_download_export.html"))


def clv2(tmp_path, export, *, page=None, press=_press_answer, robots=None, **kw):
    """The cLv2 page with two download buttons, each press answered with the export page, the export GET with
    `export` (a Snapshot, or a callable of the target)."""
    page = page or (lambda t: snap(CLV2, html=fx("clv2_files.html")))
    crawler, net, clock = make(tmp_path, {
        ("GET", "/robots.txt"): robots or snap("x", 404),
        ("GET", EXPORT): export,
        ("GET", "clv2=100061"): page,
        ("POST", "clv2=100061"): press,
    }, **kw)
    seed(crawler, CLV2)
    return crawler, net, clock


def downloads(f):
    return f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id WHERE a.kind='download' "
               "ORDER BY a.id")


def by_target(f, target):
    return [r for r in downloads(f) if r["target"] == target]


def posts(net, target=None):
    return [u for m, u in net.seen if m == "POST" and (target is None or target in u)]


def _event(target):
    return dict(target.data)["__EVENTTARGET"]


def _sent_once(answers):
    """A callable route that answers each call with the next of `answers` (the last one again once they run out)."""
    left = list(answers)

    def route(target):
        out = left.pop(0) if len(left) > 1 else left[0]
        if isinstance(out, Exception):
            raise out
        return out(target) if callable(out) else out

    return route


# ---------------------------------------------------------------- A4 ①: the cLv2 button, end to end


def test_the_clv2_button_from_the_pilot_brings_its_file_on_the_export_get(tmp_path):
    assert EXPORT == "https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx"
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF, headers=PDF_NAMED))
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    rows = downloads(f)
    assert {r["target"] for r in rows} == {BTN0, BTN1}
    for r in rows:
        assert (r["state"], r["reason"], r["sniff"], r["filename"], r["location"], r["result"]) == \
            ("done", None, "pdf", "計畫.pdf", EXPORT, "ok")
    # each press, then its export GET as the next request, nothing pressed twice
    seen = [(m, u) for m, u in net.seen if u in (CLV2, EXPORT) or m == "POST"]
    assert seen[1:] == [("POST", CLV2), ("GET", EXPORT), ("POST", CLV2), ("GET", EXPORT)], seen


# ---------------------------------------------------------------- A1: an answer that is no file is not_file


@pytest.mark.parametrize("answer, reason", [
    (snap(EXPORT, body=b"", headers=PDF_NAMED), "not_file:empty_body"),
    # a short answer that says only 「檔案不存在」 is the school's gone page since the second review (§23.9 A1 ③; it
    # was no_name:text): not_file either way
    (snap(EXPORT, body="檔案不存在".encode(), headers={"content-type": "text/plain; charset=utf-8"}),
     "not_file:saying:gone_page"),
    (snap(EXPORT, html="<html><body><p>系統訊息：請稍後再試</p></body></html>"), "not_file:no_name:html"),
    (snap(EXPORT, html="<html><body><h3>附件無法下載</h3><p>檔案不存在</p></body></html>",
          headers={"content-disposition": 'attachment; filename="計畫.pdf"'}), "not_file:saying:gone_page"),
    (snap(EXPORT, body="附件無法下載：檔案包含不支援的字元".encode(),
          headers={"content-type": "text/plain; charset=utf-8", "content-disposition": "attachment; filename=a.txt"}),
     "not_file:saying:bad_link"),
    (snap(EXPORT, body=b'{"error":"no file"}', headers={"content-type": "application/json"}),
     "not_file:no_name:json"),
    (snap(EXPORT, body=b"\x00\x01\x02garbage", headers={"content-type": "application/octet-stream"}),
     "not_file:no_name:other"),
])
def test_an_export_answer_that_is_no_file_is_never_ok(tmp_path, answer, reason):
    crawler, net, _clock = clv2(tmp_path, answer)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    for r in by_target(f, BTN0):
        assert (r["state"], r["reason"], r["result"]) == ("done", reason, "not_file"), dict(r)
    assert outcome.user_bucket("not_file") == "failed"
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (CLV2,))
    assert not level or level[0][0] not in ("complete", "complete_school")


@pytest.mark.parametrize("answer, sniffed, name", [
    (snap(EXPORT, body=PDF, headers={"content-type": "application/octet-stream"}), "pdf", None),  # no name, a PDF
    (snap(EXPORT, body=PDF, headers=PDF_NAMED), "pdf", "計畫.pdf"),
    (snap(EXPORT, body="報名表欄位說明\n姓名、系級\n".encode(),
          headers={"content-type": "text/plain", "content-disposition": 'attachment; filename="說明.txt"'}),
     "text", "說明.txt"),
])
def test_a_real_file_is_ok_named_or_not(tmp_path, answer, sniffed, name):
    crawler, net, _clock = clv2(tmp_path, answer)
    work(crawler, net, SSO_Q)
    for r in downloads(crawler.frontier):
        assert (r["state"], r["reason"], r["sniff"], r["filename"], r["result"]) == ("done", None, sniffed, name, "ok")


def test_a_press_that_answers_with_the_file_itself_is_judged_the_same(tmp_path):
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF), press=lambda t: snap(
        t.url, body=b"", headers={"content-type": "application/octet-stream"}))
    work(crawler, net, SSO_Q)
    rows = downloads(crawler.frontier)
    assert rows and all((r["reason"], r["result"]) == ("not_file:empty_body", "not_file") for r in rows)
    assert not [u for m, u in net.seen if u == EXPORT]  # no export page: nothing more to GET


@pytest.mark.parametrize("sniffed, known", [
    ("pdf", True), ("ooxml:docx", True), ("ooxml:xlsx", True), ("ooxml", True), ("odf:odt", True), ("zip", True),
    ("ole", True), ("rtf", True), ("png", True), ("jpeg", True), ("gif", True), ("webp", True), ("tiff", True),
    ("html", False), ("xml", False), ("json", False), ("text", False), ("other", False), (None, False),
])
def test_the_known_file_formats_are_sniffs_own(sniffed, known):
    assert sniff.is_file_format(sniffed) is known


def test_why_an_answer_is_no_file():
    assert sniff.not_file(b"", "other", "a.pdf") == "empty_body"
    assert sniff.not_file(b"abc", "text", None) == "no_name:text"
    assert sniff.not_file(b"abc", "text", "a.txt") is None
    assert sniff.not_file(PDF, "pdf", None) is None
    assert outcome.RESULT_LABEL["not_file"] == "应该是档案，回来不是档案（网页、空白或错误说明）"


def test_a_plain_attachment_answered_with_nothing_is_not_file(tmp_path):
    from tests.archive.test_archive import drain
    from tests.archive.test_archive import make as make_net

    home = "https://w.nknu.edu.tw/a/home"
    crawler, net, _clock = make_net(tmp_path, {
        "robots.txt": snap("x", 404),
        "/a/home": snap(home, html='<p>表單下載</p><a href="/a/form.pdf">表單</a><img src="/a/pic.png">'),
        "/a/form.pdf": snap("https://w.nknu.edu.tw/a/form.pdf", body=b"", headers={"content-type": "application/pdf"}),
        "/a/pic.png": snap("https://w.nknu.edu.tw/a/pic.png", body=b"", headers={"content-type": "image/png"}),
    })
    seed(crawler, home)
    for _ in range(3):
        drain(crawler, net, "ip:w.nknu.edu.tw")
    f = crawler.frontier
    got = {r["url"].rsplit("/", 1)[1]: (r["bytes"], r["result"]) for r in
           f.q("SELECT url, bytes, result FROM url_result WHERE url LIKE '%/a/form.pdf' OR url LIKE '%/a/pic.png'")}
    assert got == {"form.pdf": (0, "not_file"), "pic.png": (0, "not_file")}


# ---------------------------------------------------------------- A2: an export GET that redirects is no file


def test_an_export_get_sent_to_the_sign_in_page_is_tried_once_more_then_not_file_never_login(tmp_path):
    login = "https://sso.nknu.edu.tw/"
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, 302, headers={"location": login}))
    work(crawler, net, SSO_Q, rounds=60)
    f = crawler.frontier
    first = by_target(f, BTN0)[0]
    assert (first["state"], first["reason"]) == ("failed", "file_get_redirect")
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    for r in downloads(f)[1:]:
        if r["state"] == "superseded":
            continue
        assert (r["state"], r["reason"], r["location"], r["result"]) == ("done", "not_file:redirect", login,
                                                                          "not_file"), dict(r)
    assert by_target(f, BTN0)[-1]["reason"] == "not_file:redirect"
    assert f.q("SELECT count(*) FROM url WHERE url=?", (login,))[0][0] == 0  # the Location is never queued
    assert "login" not in {r["result"] for r in downloads(f)}
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (CLV2,))
    assert level and level[0][0] not in ("complete", "complete_school")


def test_an_export_get_sent_to_the_home_page_is_the_same(tmp_path):
    home = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=100061"
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, 301, headers={"location": home}))
    work(crawler, net, SSO_Q, rounds=60)
    last = by_target(crawler.frontier, BTN0)[-1]
    assert (last["reason"], last["result"]) == ("not_file:redirect", "not_file")


# ---------------------------------------------------------------- A3: retry download, too_large


def _two_downloads(tmp_path, first, then, page=None):
    """BTN0's export answers `first` until the retry, then `then`; BTN1's is a PDF. Returns the world after the
    first walk."""
    state = {"retried": False}

    def export(target):
        return then if state["retried"] else first

    def press(target):
        state.setdefault("posts", []).append((_event(target), dict(target.data).get("__VIEWSTATE")))
        state["last"] = _event(target)
        return _press_answer(target)

    def answer(target):
        if state["last"] == BTN1:
            return snap(EXPORT, body=PDF, headers=PDF_NAMED)
        return export(target)(target) if callable(export(target)) else export(target)

    crawler, net, clock = clv2(tmp_path, answer, press=press, page=page)
    work(crawler, net, SSO_Q, rounds=60)
    return crawler, net, clock, state


def test_retry_download_presses_only_the_downloads_that_came_to_nothing_on_a_new_get(tmp_path):
    pages = iter([fx("clv2_files.html"), fx("clv2_files.html").replace("VS-CLV2-FILES", "VS-CLV2-FILES-2")])
    crawler, net, _clock, state = _two_downloads(
        tmp_path, snap(EXPORT, body=b"", headers=PDF_NAMED), snap(EXPORT, body=PDF, headers=PDF_NAMED),
        page=lambda t: snap(CLV2, html=next(pages)))
    f = crawler.frontier
    assert [r["result"] for r in by_target(f, BTN0)] == ["not_file"]
    assert [r["result"] for r in by_target(f, BTN1)] == ["ok"]
    walk = f.q("SELECT ended_by, ok FROM listwalk WHERE list_id=(SELECT id FROM url WHERE url=?)", (CLV2,))[0][:]
    lists, buttons = crawler.chains.retry_downloads()
    assert (lists, buttons) == (1, 1)
    old = by_target(f, BTN0)[0]
    assert (old["state"], old["reason"]) == ("superseded", "retried: not_file:empty_body")
    row = f.row(CLV2)
    assert (row["state"], row["reason"], row["prio"]) == ("pending", "chain_restart", 0)
    # the walk stays what it was: only the buttons are pressed again, no page is walked
    assert f.q("SELECT ended_by, ok FROM listwalk WHERE list_id=?", (row["id"],))[0][:] == walk
    state["retried"] = True
    n_posts = len(state["posts"])
    work(crawler, net, SSO_Q, rounds=60)
    again = state["posts"][n_posts:]
    assert again == [(BTN0, "VS-CLV2-FILES-2")]  # BTN0 only, its form state from the new GET, never the old POST
    assert [r["result"] for r in by_target(f, BTN0)] == ["redirect", "ok"]  # superseded, then the new press
    assert [r["result"] for r in by_target(f, BTN1)] == ["ok"]
    assert f.q("SELECT ended_by, ok FROM listwalk WHERE list_id=?", (row["id"],))[0][:] == walk
    entries = {r["res_key"]: r["action_id"] for r in f.q("SELECT res_key, action_id FROM page_res WHERE page_id=? "
                                                         "AND res_key LIKE 'pb:download:%'", (row["id"],))}
    assert sorted(entries.values()) == sorted([by_target(f, BTN0)[-1]["id"], by_target(f, BTN1)[0]["id"]])
    level = f.q("SELECT level FROM page_completeness WHERE id=?", (row["id"],))[0][0]
    assert level not in ("waiting",)
    assert crawler.chains.retry_downloads() == (0, 0)  # nothing left to put back


def test_retry_download_puts_back_failed_downloads_too(tmp_path):
    crawler, net, _clock, state = _two_downloads(
        tmp_path, snap(EXPORT, 500, html="<html>錯誤</html>"), snap(EXPORT, body=PDF, headers=PDF_NAMED))
    f = crawler.frontier
    assert by_target(f, BTN0)[-1]["result"] == "failed"
    assert crawler.chains.retry_downloads()[1] == 1
    state["retried"] = True
    work(crawler, net, SSO_Q, rounds=60)
    assert by_target(f, BTN0)[-1]["result"] == "ok"


def test_a_retried_button_the_new_get_no_longer_shows_is_mismatch_row_gone(tmp_path):
    pages = iter([fx("clv2_files.html")])

    def page(target):
        html = next(pages, None)
        if html is None:  # the second GET: BTN0's row is gone from the school's page
            html = fx("clv2_files.html").replace("交通安全實施計畫", "交通安全宣導海報")
        return snap(CLV2, html=html)

    crawler, net, _clock, state = _two_downloads(
        tmp_path, snap(EXPORT, body=b"", headers=PDF_NAMED), snap(EXPORT, body=PDF, headers=PDF_NAMED), page=page)
    f = crawler.frontier
    key = by_target(f, BTN0)[0]["row_key"]
    assert crawler.chains.retry_downloads() == (1, 1)
    state["retried"] = True
    n_posts = len(state["posts"])
    work(crawler, net, SSO_Q, rounds=60)
    assert state["posts"][n_posts:] == []  # only the retried button may be pressed, and it is gone
    gone = f.q("SELECT a.state, a.reason, r.result FROM action a JOIN action_result r ON r.id=a.id WHERE a.row_key=? "
               "ORDER BY a.id DESC LIMIT 1", (key,))[0]
    assert gone[:] == ("mismatch", "row_gone", "mismatch")
    entry = f.q("SELECT action_id FROM page_res WHERE res_key=?", (f"pb:download:{key}",))[0][0]
    assert f.q("SELECT state FROM action WHERE id=?", (entry,))[0][0] == "mismatch"
    # the new row the school added is not pressed by the retry either: written on the page for a person (要人看)
    assert f.q("SELECT count(*) FROM page_res WHERE page_id=(SELECT id FROM url WHERE url=?) AND why=?",
               (CLV2, postback.UNPRESSED))[0][0] == 1


def test_a_download_answered_too_large_is_too_large_for_a_person_not_failed(tmp_path):
    crawler, net, _clock = clv2(tmp_path, _sent_once([TooLarge(f"{EXPORT}: 99999999 bytes declared (> 1)")]))
    work(crawler, net, SSO_Q, rounds=60)
    f = crawler.frontier
    rows = downloads(f)
    assert rows and all((r["state"], r["reason"], r["result"]) == ("failed", "too_large", "too_large") for r in rows)
    assert outcome.user_bucket("too_large") == "review"
    assert "too_large" in outcome.ACTION_RESULTS
    assert crawler.chains.retry_downloads() == (0, 0)  # too large again: a person decides, the retry leaves it


def test_a_press_answered_too_large_is_too_large_too(tmp_path):
    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF),
                                press=_sent_once([TooLarge(f"{CLV2}: 99999999 bytes declared (> 1)")]))
    work(crawler, net, SSO_Q, rounds=60)
    rows = downloads(crawler.frontier)
    assert rows and all(r["result"] == "too_large" for r in rows)


def test_the_retry_download_command_refuses_while_a_run_holds_the_directory_and_says_what_it_put_back(tmp_path):
    crawler, net, _clock, _state = _two_downloads(
        tmp_path, snap(EXPORT, body=b"", headers=PDF_NAMED), snap(EXPORT, body=PDF, headers=PDF_NAMED))
    crawler.frontier.close()
    net.sink.close()
    lock = hold_run_lock(tmp_path)
    try:
        got = CliRunner().invoke(app, ["archive", "retry", "download", "--dir", str(tmp_path)])
    finally:
        lock.close()
    assert got.exit_code == 1 and "archive run 正在用这个目录" in got.output
    got = CliRunner().invoke(app, ["archive", "retry", "download", "--dir", str(tmp_path)])
    assert got.exit_code == 0, got.output
    assert "1 个列表" in got.output and "1 个下载按钮" in got.output


# ---------------------------------------------------------------- A4 ②: the wait between the press and its GET


@pytest.mark.parametrize("kw, robots, least", [
    ({}, None, 2.0),
    ({"gap": (6.0, 8.0)}, None, 6.0),
    ({}, snap("https://sso.nknu.edu.tw/robots.txt", body=b"User-agent: *\nCrawl-delay: 9\n",
              headers={"content-type": "text/plain"}), 9.0),
])
def test_the_export_get_waits_the_gap_after_the_press_like_any_request(tmp_path, kw, robots, least):
    crawler, net, clock = clv2(tmp_path, snap(EXPORT, body=PDF, headers=PDF_NAMED), robots=robots, **kw)
    for _ in range(12):  # a Crawl-delay hands the rows back until the host is due: the clock moves on meanwhile
        work(crawler, net, SSO_Q)
        clock.t += 10
    reqs = crawler.frontier.q("SELECT url, purpose, started, ended FROM request ORDER BY id")
    pairs = [(a, b) for a, b in itertools.pairwise(reqs) if b["url"] == EXPORT]
    assert len(pairs) == 2
    for post, get in pairs:
        assert post["url"] == CLV2 and post["purpose"] == "postback" and get["purpose"] == "postback"
        assert get["started"] - post["ended"] >= least, (dict(post), dict(get))


# ---------------------------------------------------------------- A4 ③: a stop between the press and its GET


def test_a_stop_between_the_press_and_its_export_get_sends_nothing_more_and_the_next_run_walks_from_the_get(tmp_path):
    holder = {}

    def press(target):
        holder["crawler"].stop.set()  # STOP arrives while the press is on the wire
        return _press_answer(target)

    crawler, net, _clock = clv2(tmp_path, snap(EXPORT, body=PDF, headers=PDF_NAMED), press=press)
    holder["crawler"] = crawler
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    assert [m for m, u in net.seen if u == EXPORT] == []  # the export GET is not sent after the stop
    assert len(posts(net)) == 1
    first = downloads(f)[0]
    assert first["state"] == "pending"  # sent and answered: resume, never next_action, takes it from here
    # the next run: resume supersedes the chain and walks it again from its GET
    crawler.stop.clear()
    assert crawler.chains.resume() == 1
    assert f.q("SELECT state, reason FROM action WHERE id=?", (first["id"],))[0][:] == ("superseded",
                                                                                        "restart: resume")
    holder["crawler"] = None
    net.routes[("POST", "clv2=100061")] = _press_answer
    work(crawler, net, SSO_Q)
    assert [r["result"] for r in downloads(f) if r["state"] != "superseded"] == ["ok", "ok"]
    # no form state was posted twice: every POST came from a page of its own (form_src)
    srcs = f.q("SELECT form_src_warc, form_src_offset, target FROM action WHERE kind='download' AND status IS NOT NULL")
    assert len({(r[0], r[1], r[2]) for r in srcs}) == len(srcs)


# ---------------------------------------------------------------- A4 ④: the export GET fails


def _conn_refused():
    exc = FetchError(f"{EXPORT}: [Errno 61] Connection refused")
    exc.__cause__ = httpx.ConnectError("[Errno 61] Connection refused")
    return exc


def _timeout():
    exc = FetchError(f"{EXPORT}: timed out")
    exc.__cause__ = httpx.ReadTimeout("timed out")
    return exc


@pytest.mark.parametrize("failure, reason", [
    (_conn_refused, "connect"),
    (lambda: RetryLater(f"{EXPORT}: HTTP 429", 600.0), "retry_after"),
    (_timeout, "timeout"),
])
def test_an_export_get_that_fails_starts_the_chain_again_exactly_once(tmp_path, failure, reason):
    def export(target):
        raise failure()

    crawler, net, clock = clv2(tmp_path, export)
    f = crawler.frontier
    for _ in range(6):  # the rests (a refused connection, a Retry-After) end, the chain walks again, fails again
        work(crawler, net, SSO_Q, rounds=60)
        clock.t += 3 * 3600
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    rows = [r for r in downloads(f) if r["state"] != "superseded"]
    assert rows and all((r["state"], r["result"]) == ("failed", "failed") for r in rows)
    assert all(r["reason"] == reason for r in rows)
    assert f.row(CLV2)["state"] == "done"  # no third walk
    # each press went out once per walk: the button of the first walk and the two of the second
    assert len(posts(net)) == 3
