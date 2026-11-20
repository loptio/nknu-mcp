"""Where jscheck's requests are made from (11-03, P33): the crawl's rule (crawler B4, review 10-26), not a fixed
'unknown'. SSO's campus check page is asked on SSO's own queue before the first request, hourly, and again when this
machine's address changes; every request row says what it found (on, off or unknown). The reconciliation's spot
check, which sent through the same queues and for which four of these were first written, went with the judgment-layer
cut of 11-19: they go through jscheck itself now. Offline: the school is httpx.MockTransport (test_jscheck's Server);
made-up text, no names."""

from __future__ import annotations

from nknu.archive import jscheck
from nknu.archive.crawler import CAMPUS_URL
from tests.archive.test_jscheck import (
    EE,
    HTML,
    JSON_T,
    FakeBrowser,
    FakePage,
    Server,
    add_page,
    lines,
    make_check,
    make_root,
    opened,
    page,
)

_OFF = (200, HTML, page("您的IP 不在校內網路，請由校內網路連線"))
_ON = (302, {"location": "https://edocap.nknu.edu.tw/"}, "")
_BODY = page(*lines("首頁", 12))  # what the school answers and the browser shows: the page is ok at the first look


def _check(root, server, browser, addr, **kw):
    """jscheck on the pages a test added, one page at a time, this machine's address read from addr["now"]."""
    return make_check(root, server, browser, local_address=lambda: addr["now"], pages_at_once=1, **kw).run()


def _rows(f):
    return [(r["purpose"], r["url"], r["campus"]) for r in f.q("SELECT * FROM request ORDER BY id")]


def test_within_the_hour_the_same_address_is_checked_once_and_the_check_is_no_row(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{EE}/index.html", f"{EE}/news.html"]  # two types: two pages opened
    api = f"{EE}/api/home.json"
    with opened(root) as f:
        for u in urls:
            add_page(f, u)
    server = Server({CAMPUS_URL: _OFF, api: (200, JSON_T, "{}"), **{u: (200, HTML, _BODY) for u in urls}})
    browser = FakeBrowser({urls[0]: FakePage(_BODY, [jscheck.Ask(api, kind="xhr")]), urls[1]: FakePage(_BODY)})
    _check(root, server, browser, {"now": "10.0.0.5"}, campus_url=CAMPUS_URL)
    with opened(root) as f:
        # within the hour, the same address: no second check; a page's own request says off as its page does
        assert _rows(f) == [("campus", CAMPUS_URL, "unknown"), ("jscheck", urls[0], "off"), ("jscheck", api, "off"),
                            ("jscheck", urls[1], "off")]
        assert f.q("SELECT count(*) FROM url WHERE url=?", (CAMPUS_URL,))[0][0] == 0  # the check is no row to fetch


def test_on_campus_and_a_new_address_asks_again(tmp_path):
    root = make_root(tmp_path)
    urls = [f"{EE}/index.html", f"{EE}/news.html"]
    with opened(root) as f:
        for u in urls:
            add_page(f, u)
    server = Server({CAMPUS_URL: _ON, **{u: (200, HTML, _BODY) for u in urls}})
    addr = {"now": "140.127.0.9"}

    def moved(url):
        if url == urls[1]:
            server.routes[CAMPUS_URL] = _OFF
            addr["now"] = "10.0.0.5"  # the machine moved: asked again before the next request

    browser = FakeBrowser({u: FakePage(_BODY) for u in urls}, on_open=moved)
    _check(root, server, browser, addr, campus_url=CAMPUS_URL)
    with opened(root) as f:
        assert _rows(f) == [("campus", CAMPUS_URL, "unknown"), ("jscheck", urls[0], "on"),
                            ("campus", CAMPUS_URL, "unknown"), ("jscheck", urls[1], "off")]
    assert server.asked().count(("GET", "https://edocap.nknu.edu.tw/")) == 0  # never followed


def test_a_check_page_it_cannot_read_leaves_unknown_and_says_so(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    with opened(root) as f:
        add_page(f, url)
    server = Server({CAMPUS_URL: (200, HTML, page("系統維護中")), url: (200, HTML, _BODY)})
    _check(root, server, FakeBrowser({url: FakePage(_BODY)}), {"now": "10.0.0.5"}, campus_url=CAMPUS_URL)
    with opened(root) as f:
        assert [r[2] for r in _rows(f)] == ["unknown", "unknown"]
        assert f.q("SELECT count(*) FROM event WHERE code='campus' AND level='warn'")[0][0] == 1


def test_without_a_check_page_nothing_more_is_sent(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    with opened(root) as f:
        add_page(f, url)
    server = Server({url: (200, HTML, _BODY)})
    _check(root, server, FakeBrowser({url: FakePage(_BODY)}), {"now": "10.0.0.5"})
    with opened(root) as f:
        assert _rows(f) == [("jscheck", url, "unknown")]


def test_jscheck_records_where_its_requests_were_made_from(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/news.html"
    with opened(root) as f:
        add_page(f, url)
    server = Server({CAMPUS_URL: _OFF, url: (200, HTML, page(*lines("消息", 12)))})
    browser = FakeBrowser({url: FakePage(page(*lines("消息", 12)))})
    make_check(root, server, browser, campus_url=CAMPUS_URL, local_address=lambda: "10.0.0.5").run()
    with opened(root) as f:
        rows = _rows(f)
        assert rows[0] == ("campus", CAMPUS_URL, "unknown")
        assert rows[1:] and {(p, c) for p, _u, c in rows[1:]} == {("jscheck", "off")}
