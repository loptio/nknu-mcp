"""The last fixes before the first full run (2026-10-24): escaped dot segments, a probe that times out, the directory
marker on redirects to known off-campus rows, and honour notices (title, link and date only, never the body)."""

import sqlite3

import httpx
import pytest

from nknu.archive import scope
from nknu.archive.seeds import SSO_LISTS, v1_known
from nknu.crawl.fetch import FetchError
from tests.archive.test_archive import drain, make, seed, snap


@pytest.mark.parametrize("href", [
    "https://sso.nknu.edu.tw/News/%2e%2e/",                      # → the SSO login portal
    "https://sso.nknu.edu.tw/Recruit/Statistics/%2e%2e/front.aspx",  # → /Recruit/front.aspx, a login system
    "https://sso.nknu.edu.tw/x/%2E%2E/Default.aspx",
])
def test_escaped_dot_segments_resolve_before_judging(href):
    url = scope.canonical(href)
    assert url is None or not scope.judge(url).ok
    assert url is None or "/../" not in url and "%2e" not in url.lower()


def test_escaped_dots_resolve_to_the_path_that_is_sent():
    assert scope.canonical("https://lis.nknu.edu.tw/a/%2e%2e/b.html") == "https://lis.nknu.edu.tw/b.html"
    assert scope.canonical("https://lis.nknu.edu.tw/a/%2e/b.html") == "https://lis.nknu.edu.tw/a/b.html"


def _timeout(msg):
    exc = FetchError(msg)
    exc.__cause__ = httpx.ReadTimeout("read timed out")
    return exc


def test_a_probe_that_times_out_does_not_stop_the_pages(tmp_path):
    routes = {"robots.txt": snap("x", 404), "nknu-archive-probe": _timeout("probe timed out"),
              "/page": lambda u: snap(u, html="<p>ok</p>")}
    crawler, net, _ = make(tmp_path, routes)
    seed(crawler, "https://slow.nknu.edu.tw/page")
    drain(crawler, net, "ip:slow.nknu.edu.tw")
    row = crawler.frontier.q("SELECT state, status, attempts FROM url WHERE url='https://slow.nknu.edu.tw/page'")[0]
    assert (row["state"], row["status"], row["attempts"]) == ("done", 200, 0)
    assert crawler.frontier.host("slow.nknu.edu.tw")["probe_status"] == 0


def test_honour_links_are_recorded_masked_and_never_fetched(tmp_path):
    html = ('<a href="/News/lastNews.aspx?UNID=AAA">賀！本系陳乙丙同學榮獲全國競賽第一名</a>'
            '<a href="/News/lastNews.aspx?UNID=BBB">115學年度停修申請</a>')
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/list": lambda u: snap(u, html=html)})
    seed(crawler, "https://sso.nknu.edu.tw/News/list")
    drain(crawler, net, "ip:sso.nknu.edu.tw")
    f = crawler.frontier
    links = {r["verdict"]: r["text"] for r in f.q("SELECT verdict, text FROM link")}
    assert links["skipped:honor"] == "賀！本系陳O丙同學榮獲全國競賽第一名"
    assert not any("AAA" in u for u in net.seen) and any("BBB" in u for u in net.seen)
    assert not f.q("SELECT 1 FROM url WHERE url LIKE '%UNID=AAA%'")


def test_no_honour_list_or_honour_notice_is_seeded(tmp_path):
    assert not any("cat=GOOD" in u for u in SSO_LISTS)
    db = tmp_path / "facts.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE doc(url TEXT, unit TEXT, title TEXT, category TEXT, withdrawn_at TEXT)")
    conn.executemany("INSERT INTO doc VALUES (?,?,?,?,NULL)", [
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1", "電子系", "【狂賀】電子系陳O丙同學榮獲獎學金", "活動快報"),
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=2", "秘書室", "校慶活動", "榮譽典範"),
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=3", "教務處", "115學年度停修申請", "行政資訊"),
    ])
    conn.commit()
    conn.close()
    assert [s.url for s in v1_known(db)] == [scope.canonical("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=3")]
