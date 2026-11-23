"""What the 10-26 pilot on four sites found (research/pilot_findings_2026-10-26.md), each as a test written before its
fix: SSO's download buttons answer with an export page whose iframe names the file (F3); Google's "automated queries"
page on Drive downloads is a block to rest from, never a final api_error (F9); every host gets the TWCA-bundled
certificate check (F1); a Drive preview picture is the Drive file (F2); Google Sites' runtime body pictures (F8, since
10-27 fetched right after their page: design §23 B); a directory that keeps refusing is parked, not drained for hours; the browser check's refused
data POSTs are a sign of a data address (cLv2's timeline), not no conclusion. All offline: fake networks, fake clocks,
made-up titles and ids, no person's name."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from nknu.archive import postback
from nknu.archive.crawler import Crawler
from nknu.archive.fetch import FetchError, RetryLater
from nknu.archive.warc import WarcSink
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, Net, seed, snap
from tests.archive.test_postback import MORE, fx

PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SSO_Q = "ip:140.127.40.173"


class PressNet(Net):
    """The fake network of test_archive, routing by method as well (a GET and a POST to one address answer
    differently: a list page and the presses on it), and archiving every answer in the worker's WARC file as the
    real client does, so that a press can be built from the page it was planned on. routes: {(method, substring):
    Snapshot | callable(target)}."""

    sink: WarcSink | None = None

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        self.seen.append((target.method, target.url))
        self.clock.t += 1.0
        got = snap(target.url, 404)
        for (method, key), out in self.routes.items():
            if method == target.method and key in target.url:
                got = out(target) if callable(out) else out
                break
        if self.sink is not None:
            offset, length = self.sink.write(got, {})
            got = replace(got, warc_offset=offset, warc_len=length)
        return got


def make(tmp_path, routes, queues=None):
    clock = Clock()
    net = PressNet(clock, routes)
    queues = queues or {}
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (queues.get(h, f"ip:{h}"), None, h), netcheck=lambda: True,
                      disk_free=lambda path: 10**12, campus_url=None)
    return crawler, net, clock


def work(crawler, net, queue, rounds=40):
    """One queue's worker without its thread: a chain's next press before the queue's rows, as _work does."""
    sink = WarcSink(crawler.root / "warc", "q", 1, datetime(2026, 10, 26, tzinfo=UTC))
    net.sink = sink
    for _ in range(rounds):
        press = crawler.chains.next_action(queue, crawler.clock(), crawler.sites)
        if press is not None:
            crawler._press(queue, net, sink, press)
            continue
        row = crawler._next(queue)
        if row is None:
            return
        crawler._handle(queue, net, sink, row)


# ---------------------------------------------------------------- F3: a download answered with an export page


def _sso_press(target):
    """What SSO's unit site answers a press with: a detail for a row's button, the export page for a file's."""
    event = dict(target.data)["__EVENTTARGET"]
    if event.endswith("lnkDownloadFile"):
        return snap(target.url, html=fx("sso_download_export.html"))
    if event == "ucNews$rptNews$ctl01$lnkShowDetail":
        return snap(target.url, html=fx("webidx_detail.html"))
    return snap(target.url, html=fx("webidx_detail_wrong.html"))


def test_the_file_an_export_page_names_is_fetched_at_once_on_the_same_queue(tmp_path):
    export = postback.file_followup(fx("sso_download_export.html"), MORE)
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): _sso_press,
        ("GET", export): snap(export, body=PDF, headers={"content-type": "application/octet-stream",
                                                        "content-disposition": 'attachment; filename="名單.pdf"'}),
    }, queues={"sso.nknu.edu.tw": SSO_Q})
    seed(crawler, MORE)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    downloads = f.q("SELECT * FROM action WHERE kind='download'")
    assert len(downloads) == 1
    a = downloads[0]
    assert (a["state"], a["sniff"], a["filename"], a["location"]) == ("done", "pdf", "名單.pdf", export)
    assert f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"
    # the GET went out right after the press, on the list's own queue, logged like any request
    sent = [(m, u) for m, u in net.seen if "lnkDownloadFile" in u or u == export or m == "POST"]
    i = next(i for i, (m, u) in enumerate(net.seen) if m == "GET" and u == export)
    assert net.seen[i - 1][0] == "POST" and "webidx=3" in net.seen[i - 1][1], net.seen
    reqs = f.q("SELECT url, purpose, queue FROM request WHERE url=?", (export,))
    assert len(reqs) == 1 and reqs[0]["purpose"] == "postback" and reqs[0]["queue"] == SSO_Q
    assert f.q("SELECT count(*) FROM url WHERE url=?", (export,))[0][0] == 0  # the file is the press's, no row of its own
    assert sent  # the chain pressed something


def _busy(export):
    return snap(export, 503, html="busy")


def _too_many(export):
    raise RetryLater(f"{export}: HTTP 429", 600.0)


def _timed_out(export):
    exc = FetchError(f"{export}: timed out")
    exc.__cause__ = httpx.ReadTimeout("timed out")
    raise exc


@pytest.mark.parametrize("answer, reason", [(_busy, "http_503"), (_too_many, "retry_after"), (_timed_out, "timeout")])
def test_a_file_get_that_fails_starts_the_chain_again_from_its_get_once(tmp_path, answer, reason):
    """Design §23 F6 (with A4 ④): a 5xx, a Retry-After and a timeout of the WebIdx export GET each start the chain
    again from its GET exactly once; the second failure is the button's (failed), never a third walk."""
    export = postback.file_followup(fx("sso_download_export.html"), MORE)
    crawler, net, clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): _sso_press,
        ("GET", export): lambda t: answer(export),
    }, queues={"sso.nknu.edu.tw": SSO_Q})
    seed(crawler, MORE)
    f = crawler.frontier
    for _ in range(6):  # the rests (a busy server, a Retry-After) end, the chain walks again, fails again
        work(crawler, net, SSO_Q, rounds=40)
        clock.t += 3 * 3600
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    live = f.q("SELECT state, reason FROM action WHERE kind='download' AND state <> 'superseded' ORDER BY id")
    assert live and all(r["state"] == "failed" and r["reason"].startswith(reason) for r in live), \
        [tuple(r) for r in live]
    assert len([m for m, u in net.seen if m == "GET" and u == export]) == 2  # once per walk


# ---------------------------------------------------------------- F9: Google's block page on Drive downloads

import pytest

from nknu.archive import google_api, jscheck, links, profiles, reconcile, scope
from nknu.archive.crawler import DRIVE_MEDIA_GAP
from tests.archive.test_archive import make as make_net
from tests.archive.test_crawler_round1 import drive
from tests.archive.test_review_phase3 import FID, GQ, HOME, WQ, _crawl, _file_meta

SORRY = ('<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"/><title>Sorry...</title>'
         "</head><body><h1>We're sorry...</h1><p>... but your computer or network may be sending automated queries. "
         "To protect our users, we can't process your request right now.</p></body></html>")


def _sorry(u):
    return snap(u, 403, html=SORRY, headers={"content-type": "text/html; charset=UTF-8"})


def test_the_automated_queries_page_is_a_block_to_rest_from_never_a_final_answer():
    e = google_api.classify(403, SORRY.encode(), {"content-type": "text/html; charset=UTF-8"})
    assert (e.action, e.url_reason, e.block) == ("retry", "api:automated_queries", True)
    assert e.pause_s == google_api.BLOCK_PAUSE_S >= 3600
    # Google's JSON rate answers are blocks too (no attempt used); a 5xx is a retry that costs one
    assert google_api.classify(403, b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}').block
    assert not google_api.classify(500, b'{"error":{"errors":[{"reason":"backendError"}]}}').block
    assert google_api.classify(403, b"<html>Forbidden</html>").action == "answer"  # a plain 403 is still an answer


def test_a_blocked_download_waits_out_a_growing_rest_and_loses_no_attempt(tmp_path, monkeypatch):
    crawler, net, clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _sorry, FID: _file_meta(FID),
        "/a/home": lambda u: snap(u, html=f'<a href="https://drive.google.com/file/d/{FID}/view">簡章</a>')})
    f = crawler.frontier
    media = f.row(google_api.media_url(FID))
    assert (media["state"], media["reason"], media["attempts"]) == ("pending", "api:automated_queries", 0)
    first = f.paused_until(GQ)
    assert first - clock() >= google_api.BLOCK_PAUSE_S - 60 and media["next_at"] >= first - 1
    assert f.q("SELECT count(*) FROM event WHERE code='google_block'")[0][0] == 1
    clock.t = first + 1  # the rest is over: asked again, blocked again, the rest doubles
    drive(crawler, net, GQ)
    second = f.paused_until(GQ)
    assert second - clock() >= 2 * google_api.BLOCK_PAUSE_S - 60
    assert f.row(google_api.media_url(FID))["attempts"] == 0
    assert len([u for u in net.seen if "alt=media" in u]) == 2


def test_drive_downloads_wait_longer_between_them_than_pages(tmp_path):
    crawler, _net, clock = make_net(tmp_path, {})
    t0 = clock()
    crawler._gap(google_api.API_HOST, 0.1, url=google_api.media_url(FID))
    assert clock() - t0 >= DRIVE_MEDIA_GAP[0]
    t1 = clock()
    crawler._gap(google_api.API_HOST, 0.1, url=google_api.item_url(FID))  # metadata: the run's own gap
    assert clock() - t1 < DRIVE_MEDIA_GAP[0]
    t2 = clock()
    crawler._gap("w.nknu.edu.tw", 0.1, url="https://w.nknu.edu.tw/a.pdf")
    assert clock() - t2 < DRIVE_MEDIA_GAP[0]


# ---------------------------------------------------------------- F1: every host gets the TWCA-bundled check


def test_every_host_is_checked_with_the_bundled_twca_intermediate(monkeypatch):
    from nknu.archive import fetch

    assert not hasattr(fetch, "default_ssl_context")  # the context without the intermediate is not used for any host
    seen = []
    real = fetch.nknu_ssl_context
    monkeypatch.setattr(fetch, "nknu_ssl_context", lambda: seen.append(1) or real())
    client = fetch.Client()
    try:
        assert seen == [1]  # one context, built once
        assert client._pool("www.kh.edu.tw") is client._pool("c.nknu.edu.tw")
    finally:
        client.close()


# ---------------------------------------------------------------- F2: a Drive preview picture is the Drive file


@pytest.mark.parametrize("url", [
    f"https://lh3.google.com/u/0/d/{FID}=s2048",
    f"https://lh3.googleusercontent.com/d/{FID}=w1280",
    f"https://lh3.googleusercontent.com/u/1/d/{FID}",
])
def test_a_drive_preview_address_is_the_drive_file(url):
    assert scope.is_google_link(url)
    g = google_api.parse_link(url)
    assert (g.kind, g.file_id) == ("file", FID)


def test_other_googleusercontent_pictures_are_plain_images():
    url = "https://lh3.googleusercontent.com/AbCdEfGhIjKlMnOpQrStUvWx=w1280"
    assert not scope.is_google_link(url)
    assert google_api.parse_link(url).kind == "unhandled"  # never asked: scope leaves it to the image rules


def test_a_page_whose_picture_is_a_drive_preview_counts_the_drive_file(tmp_path, monkeypatch):
    crawler, _net, _clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": lambda u: snap(u, body=PDF, headers={"content-type": "application/pdf"}), FID: _file_meta(FID),
        "/a/home": lambda u: snap(u, html=f'<p>法規</p><img src="https://lh3.google.com/u/0/d/{FID}=s2048">')})
    f = crawler.frontier
    assert f.q("SELECT count(*) FROM url WHERE host LIKE 'lh3.%'")[0][0] == 0  # no row of its own, nothing 要登录
    entries = {r["res_key"]: r for r in f.q("SELECT pr.* FROM page_res pr JOIN url u ON u.id=pr.page_id WHERE u.url=?",
                                            (HOME,))}
    assert f"drive:{FID}" in entries and entries[f"drive:{FID}"]["need"] == "must"


# ---------------------------------------------------------------- F8: Google Sites' runtime body pictures
# F8 left them all for round two (830c1b7); design §23 B replaced it (10-27, 【用户同意 Claude 的建议】): they are
# fetched right after their page with the page's Referer (tests/archive/test_gsites_images_1027.py holds the rest)


def test_google_sites_runtime_pictures_are_body_pictures_since_10_27():
    img = links.Image("https://sites.google.com/sitesv-images-rt/AMxu72uc32nFHxEN3BPfa=w1280", "src", "body", "首頁")
    assert links.image_need(img, "https://sites.google.com/mail.nknu.edu.tw/counseling-center") == ("must", "")
    plain = links.Image("https://sites.google.com/mail.nknu.edu.tw/counseling-center/a.png", "src", "body")
    assert links.image_need(plain, "https://sites.google.com/mail.nknu.edu.tw/counseling-center") == ("must", "")


RUNTIME_PIC = "https://sites.google.com/sitesv-images-rt/AMxu72uc32nFHxEN3BPfa=w1280"


def _runtime_picture_page(tmp_path, picture):
    """A page whose body is one line and a runtime picture, crawled; then the thin judgement, as a rejudge makes it
    after a real round (design §23 F6: the test asserts on what thin_pages leaves, never on a state made up by
    hand)."""
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "/a/home"): lambda t: snap(HOME, html=f'<p>辅导中心的页</p><img src="{RUNTIME_PIC}" alt="首頁">'),
        ("GET", "sitesv-images-rt"): picture,
    }, queues={"w.nknu.edu.tw": WQ, "sites.google.com": WQ})
    seed(crawler, HOME)
    work(crawler, net, WQ, rounds=60)
    f = crawler.frontier
    page = f.row(HOME)
    thin = reconcile.thin_pages(f, tmp_path, [page["owner_site"]])[page["owner_site"]]
    # what a person's (or jscheck's) verdict on the page's type says: the page itself is fine to read
    f.x("UPDATE page_type SET verdict='ok', source='manual' WHERE key=?", (page["page_type"],))
    return crawler, net, f, page, thin


def test_a_page_with_a_runtime_picture_asks_for_it_right_after_the_page(tmp_path):
    jpeg = snap(RUNTIME_PIC, body=b"\xff\xd8\xff\xe0\x00\x10JFIF\x00made up", headers={"content-type": "image/jpeg"})
    _crawler, net, f, page, thin = _runtime_picture_page(tmp_path, jpeg)
    asked = [u for _m, u in net.seen if "/robots.txt" not in u and "nknu-archive-probe" not in u]
    assert asked[:2] == [HOME, RUNTIME_PIC]
    e = f.q("SELECT res_key, how, need, res_id FROM page_res pr JOIN url u ON u.id=pr.page_id WHERE u.url=? AND "
            "pr.how='gsimg'", (HOME,))
    assert len(e) == 1 and e[0]["need"] == "must" and e[0]["res_id"] == f.row(RUNTIME_PIC)["id"]
    # one line of words and the picture got: a needed resource, never thin, and the page is complete
    assert page["id"] not in thin.thin and thin.in_image == []
    assert f.q("SELECT result FROM url_result WHERE id=?", (page["id"],))[0][0] == "ok"
    row = f.q("SELECT level, label FROM page_completeness WHERE id=?", (page["id"],))[0]
    assert tuple(row) == ("complete", "complete")


def test_a_page_whose_runtime_picture_never_comes_lacks_a_picture_for_round_two_and_is_not_thin(tmp_path):
    refused = lambda t: snap(t.url, 403, body=b"<html>Your client does not have permission</html>")
    _crawler, _net, f, page, thin = _runtime_picture_page(tmp_path, refused)
    e = f.q("SELECT need, why FROM page_res pr JOIN url u ON u.id=pr.page_id WHERE u.url=? AND pr.how='gsimg'",
            (HOME,))
    assert [tuple(x) for x in e] == [("round2", links.GOOGLE_SITES_ROUND2)]
    assert page["id"] not in thin.thin and thin.in_image == [page["id"]]  # never 「页面几乎是空的」
    row = f.q("SELECT level, label FROM page_completeness WHERE id=?", (page["id"],))[0]
    assert tuple(row) == ("partial", "round2_img")  # 缺图片（下一轮补）


# ---------------------------------------------------------------- a directory that keeps refusing is parked


def test_a_directory_refusing_twice_over_is_parked_for_a_person_not_drained_for_hours(tmp_path):
    files = "".join(f'<a href="/protected/{n}.pdf">f{n}</a>' for n in range(25))
    crawler, net, clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "/a/home"): snap(HOME, html=files),
        ("GET", "/protected/"): lambda t: snap(t.url, 403, html="<html>forbidden</html>"),
    })
    seed(crawler, HOME)
    work(crawler, net, WQ, rounds=60)
    f = crawler.frontier
    refused = lambda: len([u for _m, u in net.seen if "/protected/" in u])
    assert refused() == 10  # the first streak: the directory waits 15 min
    clock.t = f.q("SELECT max(next_at) FROM url WHERE url LIKE '%/protected/%'")[0][0] + 1
    work(crawler, net, WQ, rounds=60)
    assert refused() == 20  # the second streak: the rest is parked, nothing more is sent
    parked = f.q("SELECT state, reason, count(*) FROM url WHERE url LIKE '%/protected/%' AND status IS NULL "
                 "GROUP BY 1, 2")
    assert [tuple(r) for r in parked] == [("failed", "denied_dir", 5)]
    clock.t += 7200
    work(crawler, net, WQ, rounds=60)
    assert refused() == 20
    assert f.q("SELECT count(*) FROM event WHERE code='host_denies_parked'")[0][0] == 1


# ---------------------------------------------------------------- the browser check and refused data POSTs


def _obs(url, log):
    return jscheck.Observation(url, url, "<html><body>" + "消息" * 200 + "</body></html>", log=log)


def test_a_data_post_the_round_cannot_send_makes_the_page_a_data_address_type_not_no_conclusion():
    # a data address of the page's own server that no platform file reads (cLv2's timeline is read since 10-27,
    # design §23 C: tests/archive/test_clv2_timeline_1027.py)
    page = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=100061"
    own = jscheck.Exchange(page, "document", "GET", True, 200, "<html><body>" + "消息" * 200 + "</body></html>")
    xhr = jscheck.Exchange("https://sso.nknu.edu.tw/Unit/Info/ajaxServices/ajaxOther.aspx", "xhr", "POST", False,
                           unsent="post_body")
    v = jscheck.judge(_obs(page, [own, xhr]))
    assert (v.verdict, v.method) == ("js", "data_post")
    assert jscheck.incomplete(_obs(page, [own, xhr])) is None  # what loaded can be compared; the POST is the type's
    failed = jscheck.Exchange("https://sso.nknu.edu.tw/x.js", "script", "GET", False, error="timeout")
    assert jscheck.judge(_obs(page, [own, failed])).why == "inconclusive:request_failed"
    assert jscheck.incomplete(_obs(page, [own, failed])) is not None


def test_googles_own_logging_and_warmup_calls_are_never_sent():
    for url, method in [("https://sites.google.com/_/view/naLogImpressions", "POST"),
                        ("https://drive.google.com/auth_warmup", "GET")]:
        assert jscheck.PageBridge._blocked(None, jscheck.Ask(url, method, "xhr")) == "tracking", url
    assert jscheck.PageBridge._blocked(None, jscheck.Ask("https://sites.google.com/mail.nknu.edu.tw/x", "GET")) is None


# ---------------------------------------------------------------- the platform files' decisions the pilot settled


def test_the_decisions_the_pilot_settled():
    assert profiles.decision("sso.nknu.edu.tw/web/595", "js_link:scrollToTop") == "ignore"  # R7 read script.js
    assert profiles.decision("sso.nknu.edu.tw/web/595", "js_link:toggleMenu") == "ignore"
    assert profiles.decision("c.nknu.edu.tw/reality", "select:ddl_Class") == "ignore"  # 「全部」 shown, walked whole
    assert profiles.decision("sso.nknu.edu.tw/site/100061", "js_link:isFinite") == "ignore"  # a JavaScript builtin
    assert profiles.decision("w.nknu.edu.tw", "js_link:parseInt") == "ignore"


# ---------------------------------------------------------------- the review of these fixes (10-26, one round)


def test_a_file_get_interrupted_by_a_pause_never_sends_the_press_again_in_that_run(tmp_path):
    """The press was sent and answered; its export GET meets a pause (another queue paused every queue, or the
    network went down). The press must not go back to pending, or the same POST goes out again (§6.4): it fails, and
    the chain starts again from its GET once, as after any failure of that GET (review of the pilot fixes, high)."""
    export = postback.file_followup(fx("sso_download_export.html"), MORE)
    state = {"crawler": None, "paused": False}

    def press(target):
        if dict(target.data)["__EVENTTARGET"].endswith("lnkDownloadFile") and not state["paused"]:
            state["paused"] = True  # once: the first file press's export GET meets the pause
            c = state["crawler"]
            c.frontier.pause("*", c.clock() + 300, "every queue: the network is down")  # before the export GET
        return _sso_press(target)

    crawler, net, clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): press,
        ("GET", export): snap(export, body=PDF, headers={"content-type": "application/pdf"}),
    }, queues={"sso.nknu.edu.tw": SSO_Q})
    state["crawler"] = crawler
    seed(crawler, MORE)
    work(crawler, net, SSO_Q, rounds=12)
    posts = [u for m, u in net.seen if m == "POST"]
    assert sum("webidx=3" in u for u in posts) == 3  # two details and one file press, nothing twice
    f = crawler.frontier
    a = f.q("SELECT state, reason FROM action WHERE kind='download' ORDER BY id")
    assert a[0][:] == ("failed", "file_get_interrupted")
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    f.unpause("*")
    clock.t += 400
    work(crawler, net, SSO_Q, rounds=20)  # the chain again from its GET: the file press once more, then its file
    assert len([u for m, u in net.seen if m == "GET" and u == export]) == 1
    assert f.q("SELECT state, sniff FROM action WHERE kind='download' ORDER BY id")[-1][:] == ("done", "pdf")


def test_a_metadata_answer_between_two_blocks_does_not_shorten_the_second_rest(tmp_path, monkeypatch):
    crawler, net, clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _sorry, FID: _file_meta(FID),
        "/a/home": lambda u: snap(u, html=f'<a href="https://drive.google.com/file/d/{FID}/view">簡章</a>')})
    f = crawler.frontier
    first = f.paused_until(GQ)
    crawler._answered(google_api.API_HOST, google_api.item_url(FID), 200)  # a files.get answered meanwhile
    clock.t = first + 1
    drive(crawler, net, GQ)
    assert f.paused_until(GQ) - clock() >= 2 * google_api.BLOCK_PAUSE_S - 60  # still doubled
    crawler._answered(google_api.API_HOST, google_api.media_url(FID), 200)  # a download got through: from the start
    assert crawler._google_blocks == 0


def test_a_directory_that_answered_again_starts_its_streaks_from_the_first(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    f = crawler.frontier
    f.set_host("w.nknu.edu.tw", queue=WQ)
    for n in range(10):
        crawler._answered("w.nknu.edu.tw", f"https://w.nknu.edu.tw/protected/{n}.pdf", 403)
    assert crawler._deny_rests.get("w.nknu.edu.tw/protected") == 1
    crawler._answered("w.nknu.edu.tw", "https://w.nknu.edu.tw/protected/open.pdf", 200)
    assert "w.nknu.edu.tw/protected" not in crawler._deny_rests  # the next streak waits again before any parking


def test_a_sign_in_page_is_still_a_login_for_the_browser_check_not_tracking():
    ask = jscheck.Ask("https://accounts.google.com/ServiceLogin", "GET", "document")
    assert jscheck.PageBridge._blocked(None, ask) == "login"  # inconclusive:not_sent, for a person, as before
