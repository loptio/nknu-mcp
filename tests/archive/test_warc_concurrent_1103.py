"""The request and response records of one fetch name each other (WARC-Concurrent-To; 11-03, P72): pywb's indexer
joins a POST's request to its response only through this header, so without it every postback page (a list's next
page, a download button) replays as 404. The WARCs written before 11-03 carry none (audit 11-03: 0 of 130 POSTs)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from warcio.archiveiterator import ArchiveIterator

from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import Client
from nknu.crawl.warc import WarcSink
from tests.crawl.test_warc_raw import Fake, response_at

URL = "https://c.nknu.edu.tw/aca/newslist.aspx?gi=4"


def _fetch(tmp_path, target: Target):
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 11, 3, 4, 0, tzinfo=UTC))
    client = Client(warc=sink, transport=httpx.MockTransport(lambda req: httpx.Response(200, html="<p>第2頁</p>")),
                    sleep=Fake().sleep, clock=Fake().clock)
    try:
        snap = client.fetch(target, RateLimit())
    finally:
        client.close()
        sink.close()
    with open(sink.path, "rb") as fh:
        recs = [(r.rec_type, r.rec_headers.get_header("WARC-Record-ID"), r.rec_headers.get_header("WARC-Concurrent-To"),
                 r.rec_headers.get_header("WARC-Target-URI")) for r in ArchiveIterator(fh)]
    return snap, sink, recs


def test_a_postbacks_request_and_response_name_each_other(tmp_path):
    _snap, _sink, recs = _fetch(tmp_path, Target(URL, method="POST", data=[("__EVENTTARGET", "gvFull"),
                                                                           ("__EVENTARGUMENT", "Page$2")]))
    (req_type, req_id, req_to, req_uri), (resp_type, resp_id, resp_to, resp_uri) = recs
    assert (req_type, resp_type) == ("request", "response")
    assert resp_to == req_id and req_to == resp_id  # each names the other, whichever a reader starts from
    assert req_id != resp_id and req_uri == resp_uri == URL


def test_a_get_is_paired_too_and_the_stored_place_is_still_the_response(tmp_path):
    snap, sink, recs = _fetch(tmp_path, Target(URL))
    assert [r[0] for r in recs] == ["request", "response"]
    assert recs[1][2] == recs[0][1] and recs[0][2] == recs[1][1]
    rec, _raw = response_at(sink, snap.warc_offset, snap.warc_len)  # url.warc_offset / warc_len still point here
    assert rec.rec_type == "response" and rec.rec_headers.get_header("WARC-Concurrent-To") == recs[0][1]
