"""Where jscheck's and the spot check's requests are made from (11-03, P33): the crawl's rule (crawler B4, review
10-26), not a fixed 'unknown'. SSO's campus check page is asked on SSO's own queue before the first request, hourly,
and again when this machine's address changes; every request row says what it found (on, off or unknown). Offline:
the school is httpx.MockTransport (test_jscheck's Server); made-up text, no names."""

from __future__ import annotations

import random

import httpx

from nknu.archive import jscheck
from nknu.archive.crawler import CAMPUS_URL, archive_client
from tests.archive.test_jscheck import (
    EE,
    HTML,
    JSON_T,
    Clock,
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


def _spot(root, server, browser, f, addr, **kw):
    clock = Clock()
    return jscheck.SpotChecker(root, f, browser=browser, clock=clock, sleep=clock.sleep, rng=random.Random(1),
                               client_factory=lambda warc: archive_client(warc, transport=httpx.MockTransport(server),
                                                                          clock=clock, sleep=clock.sleep),
                               resolver=lambda h: (f"ip:{h}", None, h), local_address=lambda: addr["now"], **kw)


def _rows(f):
    return [(r["purpose"], r["url"], r["campus"]) for r in f.q("SELECT * FROM request ORDER BY id")]


def test_the_spot_check_records_off_campus_as_the_crawl_does(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    server = Server({CAMPUS_URL: _OFF, url: (200, HTML, page("載入中")), f"{EE}/api/home.json": (200, JSON_T, "{}")})
    browser = FakeBrowser({url: FakePage(page(*lines("首頁", 12)), [jscheck.Ask(f"{EE}/api/home.json", kind="xhr")])})
    addr = {"now": "10.0.0.5"}
    with opened(root) as f:
        with _spot(root, server, browser, f, addr, campus_url=CAMPUS_URL) as spot:
            spot(url)
            spot(url)  # within the hour, the same address: no second check
        assert _rows(f) == [("campus", CAMPUS_URL, "unknown"), ("spotcheck", url, "off"),
                            ("spotcheck", f"{EE}/api/home.json", "off"), ("spotcheck", url, "off"),
                            ("spotcheck", f"{EE}/api/home.json", "off")]
        assert f.q("SELECT count(*) FROM url WHERE url=?", (CAMPUS_URL,))[0][0] == 0  # the check is no row to fetch


def test_on_campus_and_a_new_address_asks_again(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    server = Server({CAMPUS_URL: _ON, url: (200, HTML, page("首頁"))})
    browser = FakeBrowser({url: FakePage(page(*lines("首頁", 12)))})
    addr = {"now": "140.127.0.9"}
    with opened(root) as f:
        with _spot(root, server, browser, f, addr, campus_url=CAMPUS_URL) as spot:
            spot(url)
            server.routes[CAMPUS_URL] = _OFF
            addr["now"] = "10.0.0.5"  # the machine moved: asked again before the next request
            spot(url)
        assert _rows(f) == [("campus", CAMPUS_URL, "unknown"), ("spotcheck", url, "on"),
                            ("campus", CAMPUS_URL, "unknown"), ("spotcheck", url, "off")]
        assert server.asked().count(("GET", "https://edocap.nknu.edu.tw/")) == 0  # never followed


def test_a_check_page_it_cannot_read_leaves_unknown_and_says_so(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    server = Server({CAMPUS_URL: (200, HTML, page("系統維護中")), url: (200, HTML, page("首頁"))})
    browser = FakeBrowser({url: FakePage(page(*lines("首頁", 12)))})
    with opened(root) as f:
        with _spot(root, server, browser, f, {"now": "10.0.0.5"}, campus_url=CAMPUS_URL) as spot:
            spot(url)
        assert [r[2] for r in _rows(f)] == ["unknown", "unknown"]
        assert f.q("SELECT count(*) FROM event WHERE code='campus' AND level='warn'")[0][0] == 1


def test_without_a_check_page_nothing_more_is_sent(tmp_path):
    root = make_root(tmp_path)
    url = f"{EE}/index.html"
    server = Server({url: (200, HTML, page("首頁"))})
    browser = FakeBrowser({url: FakePage(page(*lines("首頁", 12)))})
    with opened(root) as f:
        with _spot(root, server, browser, f, {"now": "10.0.0.5"}) as spot:
            spot(url)
        assert _rows(f) == [("spotcheck", url, "unknown")]


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
