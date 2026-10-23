"""Detail pages of the two announcement adapters, run through the real pipeline with a fake network: which detail pages
are asked for (F-excerpt-backlog), and what a failing one costs (F-detail-void-freeze)."""

import re
from datetime import date

import pytest

from nknu.crawl.adapters import pe_news, sso_news
from nknu.crawl.adapters.pe_news import PeNews
from nknu.crawl.adapters.sso_news import BASE, SsoNews
from nknu.crawl.fetch import FetchError
from nknu.crawl.pipeline import run_adapter
from tests.crawl.adapters.helpers import FakeClient, read, snap
from tests.factories import make_db

TODAY = date(2026, 10, 22)
EMPTY_GRID = "<html><body><table id='gvFull'></table></body></html>"
SSO_P1 = read("sso_news", "list_p1.html")
SSO_P2 = read("sso_news", "list_p2.html")
UNIDS = re.findall(r"UNID=([0-9A-F]{40})", SSO_P1 + SSO_P2)
FIRST, SECOND = UNIDS[0], UNIDS[1]


@pytest.fixture
def conn(tmp_path):
    c = make_db(tmp_path)
    yield c
    c.close()


def sso_route(details=None):
    """SCHOOLADMIN has two pages, GOOD one (names, never detailed), every other category is empty."""
    details = details or {}

    def route(t):
        if "lastNews" in t.url:
            unid = t.url.rsplit("=", 1)[1]
            return details.get(unid, read("sso_news", "detail.html"))
        if t.method == "POST":
            return SSO_P2 if t.meta["cat"] == "SCHOOLADMIN" and t.meta["page"] == 2 else EMPTY_GRID
        if t.url.endswith("cat=SCHOOLADMIN"):
            return SSO_P1
        if t.url.endswith("cat=GOOD"):
            return read("sso_news", "list_good.html")
        return EMPTY_GRID

    return route


def crawl(conn, adapter, client, full_scan=False):
    return run_adapter(conn, adapter, client, today=TODAY, full_scan=full_scan, readonly=False)


def detail_unids(client):
    return [t.url.rsplit("=", 1)[1] for t in client.requests if "lastNews" in t.url]


def excerpt(conn, doc_key):
    return conn.execute("SELECT excerpt FROM doc WHERE doc_key=?", (doc_key,)).fetchone()[0]


def test_detail_backlog_is_read_over_later_runs(conn, monkeypatch):
    # F-excerpt-backlog: rows over the per-run cap get their detail page on a later run, newest first; rows already
    # detailed (or with names, GOOD) are not asked for again
    monkeypatch.setattr(sso_news, "DETAIL_CAP", 8)
    first = FakeClient(sso_route())
    assert crawl(conn, SsoNews(), first).passed
    got = detail_unids(first)
    assert len(got) == 8 and all(excerpt(conn, u) for u in got)
    second = FakeClient(sso_route())
    res = crawl(conn, SsoNews(), second)
    later = detail_unids(second)
    assert len(later) == 8 and not set(later) & set(got) and res.n_written == 8  # excerpts of unchanged rows count
    third = FakeClient(sso_route())
    crawl(conn, SsoNews(), third)
    assert len(detail_unids(third)) == 4  # 20 rows, all detailed after three runs
    assert conn.execute("SELECT count(*) FROM doc WHERE excerpt IS NULL AND pii_level <> 'personal'").fetchone()[0] == 0
    fourth = FakeClient(sso_route())
    assert crawl(conn, SsoNews(), fourth).n_written == 0 and detail_unids(fourth) == []


def test_new_rows_are_detailed_before_the_backlog(conn, monkeypatch):
    monkeypatch.setattr(sso_news, "DETAIL_CAP", 3)
    crawl(conn, SsoNews(), FakeClient(sso_route()))
    # a row this run sees for the first time jumps the queue
    conn.execute("DELETE FROM record_state WHERE record_key=?", (f"sso_news:{UNIDS[-1]}",))
    conn.execute("DELETE FROM doc WHERE doc_key=?", (UNIDS[-1],))
    conn.commit()
    client = FakeClient(sso_route())
    crawl(conn, SsoNews(), client)
    assert detail_unids(client)[0] == UNIDS[-1]


def test_a_detail_page_without_text_is_not_asked_for_again(conn):
    blank = read("sso_news", "detail.html")
    blank = re.sub(r'(<span id="ctl00_phMain_lbContent"[^>]*>).*?(</span>)', r"\1\2", blank, flags=re.DOTALL)
    crawl(conn, SsoNews(), FakeClient(sso_route({FIRST: blank})))
    assert excerpt(conn, FIRST) == ""  # read, nothing to quote
    again = FakeClient(sso_route())
    crawl(conn, SsoNews(), again)
    assert FIRST not in detail_unids(again)


def test_failing_detail_pages_cost_only_their_own_excerpt(conn):
    # F-detail-void-freeze: a detail page that errors (5xx after retries) or no longer has its body (a deleted notice,
    # a changed detail layout) loses that excerpt only; the list is written, the failure is an alert, and the page is
    # asked for again next run
    client = FakeClient(sso_route({FIRST: FetchError("HTTP 503"), SECOND: read("sso_news", "maintenance.html")}))
    res = crawl(conn, SsoNews(), client)
    assert res.passed and conn.execute("SELECT count(*) FROM doc").fetchone()[0] == 30
    assert excerpt(conn, FIRST) is None and excerpt(conn, SECOND) is None and excerpt(conn, UNIDS[2])
    issues = [r[0] for r in conn.execute("SELECT message FROM alert WHERE code='detail_failed'")]
    assert len(issues) == 2 and any("HTTP 503" in m for m in issues)
    again = FakeClient(sso_route())
    crawl(conn, SsoNews(), again)
    assert {FIRST, SECOND} <= set(detail_unids(again)) and excerpt(conn, FIRST)


def test_a_gone_announcement_is_not_asked_for_again(conn):
    from tests.crawl.adapters.helpers import snap

    gone = snap(sso_news.detail_url(FIRST), "", status=404, meta=sso_news.DETAIL_META)
    crawl(conn, SsoNews(), FakeClient(sso_route()))
    conn.execute("UPDATE doc SET excerpt=NULL WHERE doc_key=?", (FIRST,))
    conn.commit()
    crawl(conn, SsoNews(), FakeClient(sso_route({FIRST: gone})))
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (FIRST,)).fetchone()[0]
    again = FakeClient(sso_route())
    assert crawl(conn, SsoNews(), again).n_written == 0 and FIRST not in detail_unids(again)


def test_closed_host_stops_the_detail_pages_not_the_run(conn):
    # three failures in a row open the client's breaker: the remaining detail pages wait for the next run
    from nknu.crawl.fetch import HostCircuitOpen

    calls = []

    def route(t):
        if "lastNews" in t.url:
            calls.append(t.url)
            return FetchError("HTTP 500") if len(calls) <= 3 else HostCircuitOpen("closed")
        return sso_route()(t)

    res = crawl(conn, SsoNews(), FakeClient(route))
    assert res.passed and len(calls) == 4
    assert conn.execute("SELECT count(*) FROM alert WHERE code='detail_failed'").fetchone()[0] == 3


def test_detail_pages_that_keep_failing_go_to_the_back_of_the_queue(conn):
    # three detail pages that always fail (5xx) would open the client's breaker before anything else is read, run after
    # run; a detail page that failed is asked for after the rest of the backlog, and its alert closes once it is read
    from nknu.crawl.fetch import HostCircuitOpen

    broken: set[str] = set()
    state = {"fails": 0, "fixed": False}

    def route(t):  # the first three detail pages ever asked for are broken until fixed; 3 failures in a row close the host
        if "lastNews" in t.url:
            unid = t.url.rsplit("=", 1)[1]
            if state["fails"] >= 3:
                return HostCircuitOpen("3 consecutive failures")
            if not state["fixed"] and (unid in broken or len(broken) < 3):
                broken.add(unid)
                state["fails"] += 1
                return FetchError("HTTP 500")
            state["fails"] = 0
        return sso_route()(t)

    crawl(conn, SsoNews(), FakeClient(route))
    assert len(broken) == 3 and conn.execute("SELECT count(*) FROM doc WHERE excerpt IS NOT NULL").fetchone()[0] == 0
    state["fails"] = 0  # a new run, a new client
    second = FakeClient(route)
    crawl(conn, SsoNews(), second)
    assert set(detail_unids(second)[-3:]) == broken
    assert conn.execute("SELECT count(*) FROM doc WHERE excerpt IS NOT NULL").fetchone()[0] == 17
    assert conn.execute("SELECT count(*) FROM alert WHERE code='detail_failed' AND resolved_at IS NULL").fetchone()[0] == 3
    state.update(fails=0, fixed=True)
    crawl(conn, SsoNews(), FakeClient(route))
    assert conn.execute("SELECT count(*) FROM doc WHERE excerpt IS NULL AND pii_level <> 'personal'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM alert WHERE code='detail_failed' AND resolved_at IS NULL").fetchone()[0] == 0


def test_a_gone_announcement_that_answers_again_is_back(conn):
    # the 'gone' memory of a 404 page is lifted when the page answers again; full scans check it once a day, partial
    # scans never (F-404-churn)
    gone = snap(sso_news.detail_url(FIRST), "", status=404, meta=sso_news.DETAIL_META)
    crawl(conn, SsoNews(), FakeClient(sso_route()))
    conn.execute("UPDATE doc SET excerpt=NULL WHERE doc_key=?", (FIRST,))
    conn.commit()
    crawl(conn, SsoNews(), FakeClient(sso_route({FIRST: gone})))
    partial = FakeClient(sso_route())
    crawl(conn, SsoNews(), partial)
    assert FIRST not in detail_unids(partial)
    full = FakeClient(sso_route())
    assert crawl(conn, SsoNews(), full, full_scan=True).passed and FIRST in detail_unids(full)
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (FIRST,)).fetchone()[0] is None and excerpt(conn, FIRST)
    assert not conn.execute("SELECT 1 FROM suppressed WHERE record_key=?", (f"sso_news:{FIRST}",)).fetchone()


def test_list_failure_still_voids(conn):
    def route(t):
        return FetchError("HTTP 503") if t.url.endswith("cat=CAMPUS") else sso_route()(t)

    res = crawl(conn, SsoNews(), FakeClient(route))
    assert not res.passed and conn.execute("SELECT count(*) FROM doc").fetchone()[0] == 0


PE_LIST = read("pe_news", "list.html")


def pe_route(details=None):
    details = details or {}

    def route(t):
        if "NewsView" in t.url:
            nid = t.url.rsplit("=", 1)[1]
            return details.get(nid, read("pe_news", "old_2022.html"))
        return PE_LIST if t.method == "GET" else "<html><body><table id='MasterGrid'></table></body></html>"

    return route


def pe_details(client):
    return [t.url.rsplit("=", 1)[1] for t in client.requests if "NewsView" in t.url]


def test_pe_backlog_and_failures(conn, monkeypatch):
    monkeypatch.setattr(pe_news, "DETAIL_CAP", 10)
    first = FakeClient(pe_route())
    crawl(conn, PeNews(), first)
    got = pe_details(first)
    assert len(got) == 10
    broken = got[0]
    conn.execute("UPDATE doc SET excerpt=NULL WHERE doc_key=?", (broken,))
    conn.commit()
    second = FakeClient(pe_route({broken: read("pe_news", "maintenance.html")}))
    res = crawl(conn, PeNews(), second)
    later = pe_details(second)
    assert res.passed and later[0] == broken and not set(later[1:]) & set(got)
    assert conn.execute("SELECT count(*) FROM alert WHERE code='detail_failed'").fetchone()[0] == 1
    assert conn.execute("SELECT excerpt FROM doc WHERE doc_key=?", (broken,)).fetchone()[0] is None


def test_pe_full_scan_rereads_closures_before_the_backlog(conn, monkeypatch):
    monkeypatch.setattr(pe_news, "DETAIL_CAP", 2)
    crawl(conn, PeNews(), FakeClient(pe_route()))
    conn.execute("UPDATE doc SET excerpt='x'")
    conn.execute("UPDATE doc SET excerpt=NULL WHERE doc_key='882'")
    conn.commit()
    client = FakeClient(pe_route())
    crawl(conn, PeNews(), client, full_scan=True)
    closures = [n for n in pe_details(client) if n != "882"]
    titles = dict(conn.execute("SELECT doc_key, title FROM doc").fetchall())
    assert closures and all(pe_news.CLOSURE_WORDS.search(titles[n]) for n in closures)
    assert "882" not in pe_details(client)[:len(closures)]


def test_detail_meta_has_no_run_voiding_selector():
    # the layout guard checks list pages only; a detail page's own check is in the adapter (ParseIssue)
    assert "selectors" not in sso_news.DETAIL_META and "selectors" not in pe_news.DETAIL_META
    assert BASE


def pe_closures(conn) -> int:
    return conn.execute("SELECT count(*) FROM fact WHERE record_key LIKE 'pe_news:460:%' AND recorded_to IS NULL").fetchone()[0]


def test_a_closure_announcement_back_after_a_404_gets_its_closures_back(conn, monkeypatch):
    # C2 withdraws a closure announcement whose page answers 404, with its closures. A transient 404 must not take them
    # down for good: the page is checked again on the next full scan, and when it answers they are all back
    monkeypatch.setattr(pe_news, "DETAIL_CAP", 100)
    closure = read("pe_news", "closure.html")
    gone = snap(pe_news.detail_url("460"), "", status=404, meta=pe_news.DETAIL_META)
    crawl(conn, PeNews(), FakeClient(pe_route({"460": closure})), full_scan=True)
    assert pe_closures(conn) == 28
    crawl(conn, PeNews(), FakeClient(pe_route({"460": gone})), full_scan=True)
    assert pe_closures(conn) == 0
    partial = FakeClient(pe_route({"460": closure}))
    crawl(conn, PeNews(), partial)
    assert "460" not in pe_details(partial)
    full = FakeClient(pe_route({"460": closure}))
    res = crawl(conn, PeNews(), full, full_scan=True)
    assert "460" in pe_details(full) and pe_closures(conn) == 28 and res.n_written == 1 + 28
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key='460'").fetchone()[0] is None
    assert not conn.execute("SELECT 1 FROM suppressed WHERE record_key='pe_news:460'").fetchone()
    # a page that keeps answering 404 is asked once per full scan and changes nothing after the first time
    crawl(conn, PeNews(), FakeClient(pe_route({"460": gone})), full_scan=True)
    again = FakeClient(pe_route({"460": gone}))
    res = crawl(conn, PeNews(), again, full_scan=True)
    assert "460" in pe_details(again) and (res.n_written, res.n_retired) == (0, 0) and pe_closures(conn) == 0
