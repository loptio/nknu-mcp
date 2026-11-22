"""The small problems the review of 830c1b7 found (design §23 F, 【Claude 的执行办法】), each as a test written before its
fix:

- F1 a row parked with its directory (denied_dir) sent nothing: it is `parked` (要人看), said so with how to put it
  back, never 「试了 3 次还是失败」 nor a mark of the school's;
- F2 select:ddl_Class is ignored only because the list without a class parameter shows 「全部」, and the platform
  file says so (the check that a site ignoring it has such a list walked to its last page, reconcile R2 and verify
  item 4, went with the judgment-layer cut of 11-19);
- F3 the dashboard during a --sites run: only those sites, a site's row on another server counted for it (since the
  11-04 dashboard: the last run_start's sites, whether its run is alive or not);
- F4 the browser check: play.google.com/log is never sent, reCAPTCHA stays a login (inconclusive:not_sent, for a
  person), and only Google Sites' exact logging path is held back, never all of /_/;
- F5 a certificate that fails its check: robots.txt and the URL both say tls_cert, the host never waits an hour for its
  robots.txt.

All offline: fake networks, fake clocks, made-up addresses and words; no person's name."""

from __future__ import annotations

import json

import httpx

from nknu.archive import jscheck, outcome, profiles, progress
from nknu.archive.fetch import FetchError
from tests.archive.test_archive import make as make_net
from tests.archive.test_archive import seed, snap
from tests.archive.test_crawler_round1 import conn_err, rows, settle
from tests.archive.test_pilot_fixes import HOME, WQ, make, work
from tests.archive.test_progress import NOW, add_url, event, two_servers

# ---------------------------------------------------------------- F1: a parked directory's rows


def _parked(tmp_path):
    files = "".join(f'<a href="/protected/{n}.pdf">f{n}</a>' for n in range(25))
    crawler, net, clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "/a/home"): snap(HOME, html=files),
        ("GET", "/protected/"): lambda t: snap(t.url, 403, html="<html>forbidden</html>"),
    })
    seed(crawler, HOME)
    work(crawler, net, WQ, rounds=60)
    f = crawler.frontier
    clock.t = f.q("SELECT max(next_at) FROM url WHERE url LIKE '%/protected/%'")[0][0] + 1
    work(crawler, net, WQ, rounds=60)
    return crawler, net


def test_a_parked_row_sent_nothing_and_is_for_a_person_with_how_to_put_it_back(tmp_path):
    crawler, net = _parked(tmp_path)
    f = crawler.frontier
    parked = f.q("SELECT id, url, attempts, result FROM url_result WHERE reason='denied_dir' AND state='failed'")
    assert len(parked) == 5 and {r["result"] for r in parked} == {"parked"}
    sent = {u for _m, u in net.seen}
    assert not any(r["url"] in sent for r in parked)  # not one request for them
    assert outcome.user_bucket("parked") == "review"
    assert "parked" in outcome.REVIEW and "parked" in outcome.GROUPS["bad"] and "parked" in outcome.URL_RESULTS
    label = outcome.RESULT_LABEL["parked"]
    assert "连续被拒两轮" in label and "没发请求" in label and "nknu archive retry denied_dir" in label
    assert "试了 3 次" not in label
    assert not outcome.markable("parked", "denied_dir")  # never 学校那边的问题 by a person's mark
    # the refused ones themselves are what the server said: refused (连接失败), as before
    refused = f.q("SELECT DISTINCT result FROM url_result WHERE url LIKE '%/protected/%' AND status = 403")
    assert [r[0] for r in refused] == ["refused"]
    # put back, they are rows to fetch again
    assert f.retry("denied_dir")[0] == 5
    assert {r[0] for r in f.q("SELECT result FROM url_result WHERE reason='denied_dir'")} == {"todo"}


def test_a_row_still_waiting_out_its_directorys_rest_is_waiting_as_before(tmp_path):
    crawler, net = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "/a/home"): snap(HOME, html="".join(f'<a href="/protected/{n}.pdf">f{n}</a>' for n in range(25))),
        ("GET", "/protected/"): lambda t: snap(t.url, 403, html="<html>forbidden</html>"),
    })[:2]
    seed(crawler, HOME)
    work(crawler, net, WQ, rounds=60)
    waiting = crawler.frontier.q("SELECT result FROM url_result WHERE reason='denied_dir' AND state='pending'")
    assert waiting and {r[0] for r in waiting} <= {"todo", "retry"}


# ---------------------------------------------------------------- F2: why ddl_Class is ignored


def test_the_reason_ddl_class_is_ignored_is_the_list_without_a_class():
    why = next(w.why for w in profiles.CNKNU.writings if w.key == r"select:ddl_Class")
    assert "只有不带 class 参数的 newslist.aspx" in why and "?class=" in why


# ---------------------------------------------------------------- F3: one scope for the cards (dashboard 11-04)


def test_a_sites_run_counts_only_its_own_sites_and_a_sites_row_on_another_server_counts_for_it(tmp_path):
    """F3 on the 11-04 dashboard: a --sites run shows its own sites only (the last run_start's, alive or not), and a
    row of the site on another server counts for that site (and for the small card of that server), never for the
    sites that live there."""
    f = two_servers(tmp_path, 10, 1000)  # a: 10 rows left, b: 1,000; the run covers a only
    event(f, NOW - 60, "info", "run_start", json.dumps({"pid": 1, "gap": [2, 5], "sites": ["a.nknu.edu.tw"]}))
    add_url(f, url="https://b.nknu.edu.tw/of-a", host="b.nknu.edu.tw", site="a.nknu.edu.tw",
            owner_site="a.nknu.edu.tw", state="pending", status=None)
    conn = progress.connect(f.path)
    try:
        for running in ("1", "2"):  # pid 2: a run whose run_start is not logged yet shows the last one's sites
            snap_ = progress.snapshot(conn, NOW, running=running)
            assert snap_["scope"].sites == ("a.nknu.edu.tw",)
            rows = [r for c in snap_["cards"] for r in c["rows"]]
            assert [(r["site"], r["known"], r["left"]) for r in rows] == [("a.nknu.edu.tw", 11, 11)]
            assert [(c["name"], c["known"]) for c in snap_["small"]] == [("学校首页等", 1)]  # b's server, a's row
            assert snap_["total"]["known"] == 11
    finally:
        conn.close()
        f.close()


# ---------------------------------------------------------------- F4: what the browser check never sends


def _blocked(url, method="GET", kind="xhr"):
    return jscheck.PageBridge._blocked(None, jscheck.Ask(url, method, kind))


def test_googles_play_log_is_never_sent_and_only_sites_exact_logging_path_is_held_back():
    assert _blocked("https://play.google.com/log?format=json&hasfast=true", "POST") == "tracking"
    assert _blocked("https://sites.google.com/_/view/naLogImpressions", "POST") == "tracking"
    assert _blocked("https://sites.google.com/_/view/naLogImpressions?x=1", "POST") == "tracking"
    assert _blocked("https://drive.google.com/auth_warmup") == "tracking"
    assert _blocked("https://www.google.com/log?format=json", "POST") == "tracking"
    assert _blocked("https://www.google.com/gen_204?x=1") == "tracking"
    for other in ("https://sites.google.com/_/view/other", "https://sites.google.com/_/SitesFrontend/data"):
        assert not jscheck.BLOCK_PATHS.match(other), other
    assert not jscheck.BLOCK_PATHS.match("https://www.google.com/logos/doodle.png")


def test_recaptcha_is_still_a_login_the_check_cannot_see_past_for_a_person():
    for url, kind in (("https://www.google.com/recaptcha/api.js", "script"),
                      ("https://www.google.com/recaptcha/api2/anchor?k=x", "document")):
        assert not jscheck.BLOCK_PATHS.match(url)
        assert _blocked(url, kind=kind) == "login", url
    page = "https://w.nknu.edu.tw/contact.html"
    own = jscheck.Exchange(page, "document", "GET", True, 200, "<html><body>" + "聯絡" * 200 + "</body></html>")
    cap = jscheck.Exchange("https://www.google.com/recaptcha/api.js", "script", "GET", False, blocked="login")
    obs = jscheck.Observation(page, page, "<html><body>" + "聯絡" * 200 + "</body></html>", log=[own, cap])
    v = jscheck.judge(obs)
    assert (v.verdict, v.why) == (None, "inconclusive:not_sent")


# ---------------------------------------------------------------- F5: a certificate that fails its check


def tls_cert_err():
    exc = FetchError("https://x/: [SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired")
    exc.__cause__ = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has "
                                       "expired (_ssl.c:1000)")
    return exc


def test_a_certificate_that_fails_its_check_is_the_urls_tls_cert_and_robots_txt_never_holds_the_host(tmp_path):
    news = "https://dept.nknu.edu.tw/news.html"  # a school host (a government one meets the same check)

    def route(u):
        if u.startswith("https://"):
            raise tls_cert_err()
        raise conn_err()  # its http refuses: robots.txt cannot be read on either scheme

    crawler, net, _clock = make_net(tmp_path, {"dept.nknu.edu.tw": route})
    seed(crawler, news)
    settle(crawler, net, ["ip:dept.nknu.edu.tw"])
    f = crawler.frontier
    codes = [r[0] for r in f.q("SELECT code FROM event")]
    assert "robots_unreachable" not in codes and "host_unreachable" not in codes
    assert "robots_tls_cert" in codes
    r = rows(crawler)[news]
    assert (r["state"], r["reason"], r["attempts"]) == ("failed", "tls_cert", 3)
    assert f.q("SELECT count(*) FROM url WHERE reason = 'robots_unreachable'")[0][0] == 0
    robots = [u for u in net.seen if u.endswith("/robots.txt")]
    pages = [u for u in net.seen if u.endswith("/news.html")]
    assert pages and len(robots) <= 2 * len(pages)  # robots.txt read again in an hour, not before every try
    assert "过期、设定错或缺中继证书" in progress.PLAIN["tls_cert"] and "学校" not in progress.PLAIN["tls_cert"]
