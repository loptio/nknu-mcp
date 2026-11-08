"""Design §23 D (10-27, 【Claude 的执行办法】; review of 830c1b7, research/review_830c1b7_2026-10-27.md): Google's
limits and blocks, each test written before its fix.

D1 downloadQuotaExceeded (one file's downloads used up) puts that one row off 24 hours, never the whole Google queue
   and never the block streak; the third time it is api_quota (要人看). Only the rate and quota answers, a 429 and
   the "automated queries" page block the queue, an hour at least.
D2 the block streak and when it began live in frontier.db (meta), so the rest keeps doubling across runs; more than 3
   days or the 6th block in a row is an error event google_block_long, the Google server red on the dashboard, and a
   line of verify's item 17.
D3 the dashboard's words: a block is 「Google 暂时挡下载…」, never 「隔 30 分钟再试，共 3 次」; api: reasons (a pause's
   why with its host in front too) have their own words; a row Google blocked is no problem (错), never 「被拒绝」.
All offline: fake networks and clocks, made-up file ids, no key."""

from __future__ import annotations

import json

import pytest

from nknu.archive import google_api, monitor, outcome, progress
from nknu.archive.crawler import Crawler
from nknu.archive.frontier import Frontier
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, Net, seed, snap
from tests.archive.test_crawler_round1 import drive
from tests.archive.test_review_phase3 import GQ, HOME, KEY, WQ, _file_meta, _json, _pdf

FA, FB = "1QuOtAaBcDeFgHiJkLm", "1FrEeBbCdEfGhIjKlMn"  # made up
SORRY = ("<html><head><title>Sorry...</title></head><body><h1>We're sorry...</h1><p>... but your computer or network "
         "may be sending automated queries.</p></body></html>")
HOUR, DAY = 3600.0, 86400.0


def _quota(u):
    return _json(u, {"error": {"code": 403, "errors": [{"reason": "downloadQuotaExceeded"}]}}, 403)


def _sorry(u):
    return snap(u, 403, html=SORRY, headers={"content-type": "text/html; charset=UTF-8"})


def _page(*ids):
    return lambda u: snap(u, html="".join(f'<a href="https://drive.google.com/file/d/{i}/view">檔{n}</a>'
                                          for n, i in enumerate(ids)))


def world(tmp_path, monkeypatch, routes, clock=None):
    """A crawler on the fake network (test_archive.make's), with a clock that may be another crawler's."""
    monkeypatch.setenv(google_api.KEY_ENV, KEY)
    clock = clock or Clock()
    net = Net(clock, {"robots.txt": snap("x", 404), **routes})
    crawler = Crawler(tmp_path, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True, disk_free=lambda p: 10**12,
                      campus_url=None)
    return crawler, net, clock


def turns(crawler, net, n=6):
    for _ in range(n):
        for q in (WQ, GQ):
            drive(crawler, net, q)


# ---------------------------------------------------------------- D1: one file's downloads used up


def test_a_used_up_file_is_its_own_affair_never_a_block_of_the_queue():
    e = google_api.classify(403, b'{"error":{"errors":[{"reason":"downloadQuotaExceeded"}]}}')
    assert (e.action, e.url_reason, e.block) == ("file_quota", "api:downloadQuotaExceeded", False)
    assert "downloadQuotaExceeded" not in google_api.QUOTA_REASONS


@pytest.mark.parametrize("status, body, headers, least", [
    (403, b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}', {}, HOUR),
    (403, b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}', {"retry-after": "120"}, HOUR),
    (403, b'{"error":{"errors":[{"reason":"dailyLimitExceeded"}]}}', {"retry-after": "7200"}, 7200.0),
    (403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}', {}, HOUR),
    (429, b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}', {}, HOUR),
    (403, SORRY.encode(), {"content-type": "text/html"}, HOUR),
])
def test_every_block_rests_an_hour_at_least_or_the_longer_retry_after(status, body, headers, least):
    e = google_api.classify(status, body, headers)
    assert e.block and e.action == "retry"
    assert e.pause_s == least


def test_a_used_up_file_waits_a_day_while_the_next_file_downloads(tmp_path, monkeypatch):
    crawler, net, clock = world(tmp_path, monkeypatch, {
        google_api.media_url(FA): _quota, google_api.media_url(FB): _pdf, FA: _file_meta(FA), FB: _file_meta(FB),
        "/a/home": _page(FA, FB)})
    seed(crawler, HOME)
    turns(crawler, net)
    f = crawler.frontier
    a, b = f.row(google_api.media_url(FA)), f.row(google_api.media_url(FB))
    assert (b["state"], b["sniff"]) == ("done", "pdf")  # B got its file: the queue never stopped for A
    assert (a["state"], a["reason"], a["attempts"]) == ("pending", "api:downloadQuotaExceeded", 1)
    assert a["next_at"] >= clock() + DAY - 120
    assert f.paused_until(GQ) <= clock()
    assert f.q("SELECT count(*) FROM event WHERE code='google_block'")[0][0] == 0
    assert google_api.block_state(f) == (0, None)  # no block streak for it
    assert f.q("SELECT count(*) FROM event WHERE code='google_file_quota'")[0][0] == 1


def test_a_file_used_up_three_times_is_api_quota_for_a_person(tmp_path, monkeypatch):
    crawler, net, clock = world(tmp_path, monkeypatch, {
        google_api.media_url(FA): _quota, FA: _file_meta(FA), "/a/home": _page(FA)})
    seed(crawler, HOME)
    turns(crawler, net)
    f = crawler.frontier
    for _ in range(2):
        clock.t = f.row(google_api.media_url(FA))["next_at"] + 1
        turns(crawler, net, 2)
    a = f.row(google_api.media_url(FA))
    assert (a["state"], a["reason"], a["attempts"]) == ("failed", "api:downloadQuotaExceeded", 3)
    assert len([u for u in net.seen if u == google_api.media_url(FA)]) == 3
    assert f.q("SELECT result FROM url_result WHERE id=?", (a["id"],))[0][0] == "api_quota"
    assert outcome.RESULT_LABEL["api_quota"] == "Google：这个档案的下载次数被用完"
    assert outcome.user_bucket("api_quota") == "review" and outcome.RESULT_COLOR["api_quota"] == "red"
    assert "api_quota" in outcome.REVIEW and "api_quota" in outcome.GROUPS["bad"]
    assert not outcome.markable("api_quota", "api:downloadQuotaExceeded")  # never 学校那边的问题


# ---------------------------------------------------------------- D2: the streak lives in frontier.db


def test_the_block_streak_outlives_the_run_and_keeps_doubling(tmp_path, monkeypatch):
    routes = {"alt=media": _sorry, FA: _file_meta(FA), "/a/home": _page(FA)}
    crawler, net, clock = world(tmp_path, monkeypatch, routes)
    seed(crawler, HOME)
    turns(crawler, net)
    f = crawler.frontier
    first = f.paused_until(GQ)
    assert first - clock() >= HOUR - 60
    assert google_api.block_state(f) == (1, clock())  # the streak and when it began, in meta
    crawler.frontier.close()
    # a new run, after the rest: the second block rests twice as long
    clock.t = first + 1
    again, net2, _ = world(tmp_path, monkeypatch, routes, clock=clock)
    turns(again, net2, 2)
    g = again.frontier
    assert g.paused_until(GQ) - clock() >= 2 * HOUR - 60
    streak, began = google_api.block_state(g)
    assert streak == 2 and began < clock() - HOUR
    assert again._google_blocks == 2
    # a download that gets through ends the streak, in the file too
    again._answered(google_api.API_HOST, google_api.media_url(FA), 200)
    assert google_api.block_state(g) == (0, None)


def _blocked(f, streak, began):
    f.meta(google_api.BLOCK_STREAK_KEY, str(streak))
    f.meta(google_api.BLOCK_FIRST_KEY, repr(began))


@pytest.mark.parametrize("streak, days, long_", [(5, 1.0, False), (6, 1.0, True), (2, 3.1, True), (3, 2.9, False)])
def test_a_long_block_is_an_error_for_a_person(tmp_path, monkeypatch, streak, days, long_):
    crawler, net, clock = world(tmp_path, monkeypatch, {"alt=media": _sorry, FA: _file_meta(FA),
                                                        "/a/home": _page(FA)})
    f = crawler.frontier
    _blocked(f, streak - 1, clock() - days * DAY)
    seed(crawler, HOME)
    turns(crawler, net)
    assert google_api.block_state(f)[0] == streak
    longs = f.q("SELECT level, message FROM event WHERE code='google_block_long'")
    assert bool(longs) is long_
    if long_:
        assert longs[0]["level"] == "error" and f"连续挡下载 {streak} 次" in longs[0]["message"]
        assert "要人看" in longs[0]["message"]
    assert google_api.block_long(*google_api.block_state(f), clock()) is long_


SITE_A = "w.nknu.edu.tw/a"


def _site_with_google_rows(f: Frontier, *rows: tuple[str, dict]) -> None:
    """A round-1 site whose home page was fetched, with these rows of its own (url, columns)."""
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?, '测试', '全校重要', '1', 'c.nknu', ?)",
        (SITE_A, HOME))
    f.set_host("w.nknu.edu.tw", queue=WQ)
    f.set_host(google_api.API_HOST, queue=GQ)
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', ?, 'ok', 1)", (SITE_A,))
    for url, cols in ((HOME, {"kind": "page", "state": "done", "status": 200, "sniff": "html", "page_type": "t:ok",
                              "found_via": "seed"}), *rows):
        row = {"url": url, "path": url.split("?")[0], "host": url.split("/")[2], "site": SITE_A, "owner_site": SITE_A,
               "tier": 0, "kind": "file", "kind0": cols.get("kind", "file"), "depth": 1, "found_via": "link", **cols}
        f.x(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))


def test_the_dashboard_turns_the_google_card_red_on_a_long_block_and_verify_lists_it(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    now = 2_000_000.0
    _site_with_google_rows(f, (google_api.media_url(FA), {"state": "pending", "reason": "api:automated_queries"}))
    f.pause(GQ, now + 8 * HOUR, f"{google_api.API_HOST}: api:automated_queries")
    f.event("info", "run_start", json.dumps({"pid": 1, "gap": [2, 5], "sites": None}))

    def status():
        conn = progress.connect(f.path)
        try:
            snap = progress.snapshot(conn, now, running="1")  # a run holds the lock
        finally:
            conn.close()
        site_card = next(c for c in snap["cards"] if SITE_A in {r["site"] for r in c["rows"]})
        assert site_card["state"]["words"] == "等 Google"  # its own server has nothing left: never 完成
        return next(c for c in snap["small"] if c["key"] == "google")["state"]

    s = status()
    assert (s["words"], s["color"]) == ("被 Google 挡", "yellow") and progress.PLAIN["google_block"] in s["tip"]
    _blocked(f, 6, now - DAY)
    s = status()
    assert s["color"] == "red" and "要人看" in s["tip"] and "6 次" in s["tip"]
    c = monitor.Check("17", "逐站的问题清单")
    ctx = monitor._Ctx(f, None, None, True, None, {}, monitor.Rounds(f), False, False, None)
    monitor._c17(ctx, c)
    assert c.status == monitor.FAIL and any("Google 连续挡下载 6 次" in line for line in c.lines), c.lines
    f.close()


# ---------------------------------------------------------------- D3: the dashboard's words


def test_a_block_has_its_own_plain_words_never_thirty_minutes_three_times():
    block = progress.PLAIN["google_block"]
    assert block == "Google 暂时挡下载：整条 Google 队至少休息 1 小时，连续被挡就加倍，不扣重试次数"
    for code in ("api:automated_queries", "api:rateLimitExceeded", "api:userRateLimitExceeded",
                 "api:dailyLimitExceeded", "api:quotaExceeded", "api:RATE_LIMIT_EXCEEDED"):
        assert progress.error_code(code) == "google_block", code
        assert progress.plain_error(code) == block
    # a pause's why, with the host in front (crawler._api_error writes it so)
    assert progress.plain_error(None, "www.googleapis.com: api:automated_queries") == block
    assert progress.error_code("api:http_429", status=429) == "google_block"
    e = google_api.classify(429, b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}')
    assert progress.error_code(e.url_reason) == "google_block"  # a 429's reason, whatever Google calls it
    assert progress.error_code(google_api.classify(429, b"").url_reason) == "google_block"
    assert progress.error_code("api:downloadQuotaExceeded") == "google_file_quota"
    assert "24 小时" in progress.PLAIN["google_file_quota"]
    assert progress.error_code("api:notFound") == "api_error"
    assert progress.plain_error("api:notFound").startswith(outcome.RESULT_LABEL["api_error"])
    assert progress.error_code("google_block_long") == "google_block_long"
    assert "30 分钟" not in block and "共 3 次" not in block


def test_rows_blocked_by_google_are_no_problem_and_the_schools_403_is_a_refusal(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    now = 2_000_000.0
    url = google_api.media_url(FA)
    refused = "https://w.nknu.edu.tw/a/staff.aspx"
    _site_with_google_rows(f, (url, {"state": "pending", "reason": "api:automated_queries"}),
                           (refused, {"kind": "page", "state": "done", "status": 403, "sniff": "html",
                                      "page_type": "t:ok"}))
    for n in range(3):
        f.request(GQ, google_api.API_HOST, url, "page", now - 600 + n, now - 599 + n, 403, None)
    conn = progress.connect(f.path)
    try:
        snap = progress.snapshot(conn, now)
        slow = progress.slow_snapshot(conn, now, site_status=monitor.site_status, facts=monitor.facts)
    finally:
        conn.close()
    lines = progress.log_view(snap, slow, site=SITE_A)["rows"]
    assert [(x["url"], x["kind_zh"], x["label"]) for x in lines] == [(refused, "失败", "被拒绝（401、403、451）")]
    f.close()


def test_the_events_of_a_block_say_it_plainly(tmp_path, monkeypatch):
    crawler, net, _clock = world(tmp_path, monkeypatch, {"alt=media": _sorry, FA: _file_meta(FA),
                                                         "/a/home": _page(FA)})
    seed(crawler, HOME)
    turns(crawler, net)
    ev = crawler.frontier.q("SELECT code, message FROM event WHERE code='google_block'")[0]
    assert progress.plain_error(ev["code"], ev["message"]) == progress.PLAIN["google_block"]
    assert "连续第 1 次" in ev["message"] and "60 分钟" in ev["message"]
    assert json.dumps(ev["message"])  # plain text, nothing of the key
    assert KEY not in ev["message"]


def test_verify_lists_a_used_up_file_for_a_person(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    url = google_api.media_url(FA)
    f.x("INSERT INTO url(url, path, host, site, owner_site, tier, kind, kind0, depth, found_via, state, reason, "
        "attempts) VALUES (?,?,?,?,?,2,'file','file',1,'drive','failed','api:downloadQuotaExceeded',3)",
        (url, url.split("?")[0], google_api.API_HOST, "w.nknu.edu.tw/a", "w.nknu.edu.tw/a"))
    ten = next(c for c in monitor.checks(f, cheap=True) if c.no == "10")
    assert ten.status == monitor.FAIL
    assert any(outcome.RESULT_LABEL["api_quota"] in x and url in x for x in ten.lines), ten.lines
    f.close()
