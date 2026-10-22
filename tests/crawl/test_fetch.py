import gzip
from datetime import UTC, date, datetime

import httpx
import pytest

from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import USER_AGENT, Client, DecodeError, FetchError, HostCircuitOpen, decode_body
from nknu.crawl.warc import WarcSink, gc_warc

RL = RateLimit()
URL = "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"


class Fake:
    """Fake clock + sleep: sleeping advances the clock."""

    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def client(handler, fake, warc=None):
    return Client(transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock, warc=warc)


def html(body="<html>場地</html>", status=200, headers=None):
    return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8", **(headers or {})})


def test_decode_header_charset():
    assert decode_body("中".encode("cp950"), "text/html; charset=big5") == "中"


def test_decode_ignores_meta_big5():
    assert decode_body('<meta charset="big5">中'.encode(), "text/html") == '<meta charset="big5">中'


def test_decode_cp950_fallback():
    assert decode_body("場地".encode("cp950"), "text/html") == "場地"


def test_decode_garbage_fails():
    with pytest.raises(DecodeError):
        decode_body(b"\xff\xfe\xfa\x80\x81", "text/html")


def test_polite_interval():
    fake = Fake()
    c = client(lambda req: html(), fake)
    c.fetch(Target(URL), RL)
    c.fetch(Target(URL), RL)
    assert 2.0 <= sum(fake.sleeps) <= 5.0


def test_retry_after_honored():
    fake, calls = Fake(), []

    def handler(req):
        calls.append(1)
        return html(status=503, headers={"retry-after": "7"}) if len(calls) == 1 else html()

    snap = client(handler, fake).fetch(Target(URL), RL)
    assert snap.status == 200 and 7 in fake.sleeps and len(calls) == 2


def test_404_returns_snapshot():
    assert client(lambda req: html(status=404), Fake()).fetch(Target(URL), RL).status == 404


def test_other_4xx_raises():
    with pytest.raises(FetchError):
        client(lambda req: html(status=403), Fake()).fetch(Target(URL), RL)


def test_circuit_opens_after_three():
    calls = []

    def handler(req):
        calls.append(1)
        return html(status=500)

    c = client(handler, Fake())
    for _ in range(3):
        with pytest.raises(FetchError):
            c.fetch(Target(URL), RL)
    n = len(calls)
    with pytest.raises(HostCircuitOpen):
        c.fetch(Target(URL), RL)
    assert len(calls) == n


def test_user_agent_sent():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return html()

    client(handler, Fake()).fetch(Target(URL), RL)
    assert seen["user-agent"] == USER_AGENT == "nknu-facts-bot/1.0 (+https://github.com/loptio/nknu-mcp)"


def test_post_form_keeps_order_and_duplicates():
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        return html()

    client(handler, Fake()).fetch(Target(URL, method="POST", data=[("a", "1"), ("b", "$"), ("a", "2")]), RL)
    assert seen["body"] == "a=1&b=%24&a=2"


def test_snapshot_fields():
    snap = client(lambda req: html(), Fake()).fetch(Target(URL), RL)
    assert snap.text == "<html>場地</html>" and len(snap.sha256) == 64 and snap.fetched_at.tzinfo is not None


def test_binary_has_no_text():
    snap = client(lambda req: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}), Fake()).fetch(
        Target(URL), RL)
    assert snap.text is None and snap.body == b"%PDF"


def test_warc_written(tmp_path):
    sink = WarcSink(tmp_path, "pe_open_hours", 1, datetime(2026, 10, 22, 3, 0, tzinfo=UTC))
    client(lambda req: html(), Fake(), warc=sink).fetch(Target(URL), RL)
    sink.close()
    files = list((tmp_path / "2026" / "10" / "22").glob("*.warc.gz"))
    assert files and b"WARC-Target-URI: " + URL.encode() in gzip.decompress(files[0].read_bytes())


def test_gc_warc(tmp_path):
    old = tmp_path / "2026" / "04" / "01"
    new = tmp_path / "2026" / "10" / "01"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "x.warc.gz").write_bytes(b"x")
    assert gc_warc(tmp_path, date(2026, 10, 22), keep_days=180) == 1
    assert not old.exists() and (new / "x.warc.gz").exists()
