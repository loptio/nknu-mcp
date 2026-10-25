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


def test_retry_after_beyond_cap_gives_up_on_the_host():
    # F-retry-after-cap: "come back in an hour" is not shortened to 2 minutes and retried; the host is left alone for
    # the rest of this run (later fetches fail without a request)
    fake, calls = Fake(), []

    def handler(req):
        calls.append(1)
        return html(status=503, headers={"retry-after": "3600"})

    c = client(handler, fake)
    with pytest.raises(FetchError, match="3600"):
        c.fetch(Target(URL), RL)
    assert len(calls) == 1 and not [s for s in fake.sleeps if s > 5]
    with pytest.raises(HostCircuitOpen):
        c.fetch(Target("https://c.nknu.edu.tw/reality/Newslist.aspx"), RL)
    assert len(calls) == 1
    assert client(lambda req: html(), fake).fetch(Target("https://sso.nknu.edu.tw/News/x"), RL).status == 200


def test_no_wait_after_the_last_attempt():
    fake, calls = Fake(), []

    def handler(req):
        calls.append(fake.t)
        return html(status=503, headers={"retry-after": "7"})

    with pytest.raises(FetchError):
        client(handler, fake).fetch(Target(URL), RL)
    assert len(calls) == 4 and fake.sleeps.count(7) == 3  # waits between attempts only
    assert fake.t == calls[-1]  # nothing slept after the final answer


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


API = "https://www.googleapis.com/calendar/v3/calendars/x%40gmail.com/events?singleEvents=true"
SECRET = "test-key-not-real-0123456789"


def test_credential_travels_only_in_its_header(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", SECRET)
    seen = {}

    def handler(req):
        seen.update(req.headers, url=str(req.url))
        return httpx.Response(200, json={"kind": "calendar#events", "items": []})

    snap = client(handler, Fake()).fetch(Target(API, credential="google_api_key"), RL)
    assert seen["x-goog-api-key"] == SECRET and SECRET not in seen["url"] and SECRET not in snap.url
    assert SECRET not in repr(snap.target)  # the target names the credential, never holds it
    seen.clear()
    client(handler, Fake()).fetch(Target(API), RL)
    assert "x-goog-api-key" not in seen  # only a target that names the credential sends it


def test_missing_credential_fails_without_a_request(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    calls = []
    with pytest.raises(FetchError, match="GOOGLE_API_KEY") as exc:
        client(lambda req: calls.append(req) or html(), Fake()).fetch(Target(API, credential="google_api_key"), RL)
    assert not calls and "test-key" not in str(exc.value)


@pytest.mark.parametrize("broken", [f"{SECRET[:10]}\n{SECRET[10:]}", f"{SECRET[:10]}\r{SECRET[10:]}", f"{SECRET} x",
                                    f"{SECRET[:10]}\x01{SECRET[10:]}", f"{SECRET}全"])
def test_malformed_credential_fails_without_a_request_or_its_value(monkeypatch, broken):
    # an env file written wrong (a value split over two lines) must not put the key into an error message, an alert
    # or a Discord notification: the header library quotes an illegal header value in full
    monkeypatch.setenv("GOOGLE_API_KEY", broken)
    calls = []
    with pytest.raises(FetchError, match="GOOGLE_API_KEY") as exc:
        client(lambda req: calls.append(req) or html(), Fake()).fetch(Target(API, credential="google_api_key"), RL)
    assert not calls and SECRET[:10] not in str(exc.value) and SECRET[10:] not in str(exc.value)


def test_credentialed_request_never_follows_a_redirect(monkeypatch):
    # httpx drops Authorization on a cross-site redirect, but not X-Goog-Api-Key: the key would reach the other host
    monkeypatch.setenv("GOOGLE_API_KEY", SECRET)
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/steal"})

    with pytest.raises(FetchError, match="302") as exc:
        client(handler, Fake()).fetch(Target(API, credential="google_api_key"), RL)
    assert hosts == ["www.googleapis.com"] and SECRET not in str(exc.value)


def test_plain_request_still_follows_redirects():
    def handler(req):
        if req.url.host == "short.nknu.edu.tw":
            return httpx.Response(302, headers={"location": URL})
        return html()

    snap = client(handler, Fake()).fetch(Target("https://short.nknu.edu.tw/x"), RL)
    assert snap.status == 200 and snap.url == URL


def test_credential_never_archived(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", SECRET)
    sink = WarcSink(tmp_path, "aca_calendar_ics", 1, datetime(2026, 10, 24, 3, 0, tzinfo=UTC))
    client(lambda req: httpx.Response(200, json={"items": []}), Fake(), warc=sink).fetch(
        Target(API, credential="google_api_key"), RL)
    sink.close()
    raw = gzip.decompress(next((tmp_path / "2026" / "10" / "24").glob("*.warc.gz")).read_bytes())
    assert SECRET.encode() not in raw and b"x-goog-api-key: [redacted]" in raw.lower()


def test_gc_warc(tmp_path):
    old = tmp_path / "2026" / "04" / "01"
    new = tmp_path / "2026" / "10" / "01"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "x.warc.gz").write_bytes(b"x")
    assert gc_warc(tmp_path, date(2026, 10, 22), keep_days=180) == 1
    assert not old.exists() and (new / "x.warc.gz").exists()


def test_archive_mode_hands_back_redirects_and_gone_pages():
    def handler(req):
        if req.url.path == "/moved":
            return httpx.Response(302, headers={"location": "https://sso.nknu.edu.tw/x"})
        return httpx.Response(410)

    c = Client(transport=httpx.MockTransport(handler), sleep=Fake().sleep, clock=Fake().clock, follow_redirects=False,
               pass_statuses=frozenset({404, 410}))
    moved = c.fetch(Target("https://pbws.nknu.edu.tw/moved"), RL)
    assert moved.status == 302 and moved.headers["location"] == "https://sso.nknu.edu.tw/x"
    assert c.fetch(Target("https://pbws.nknu.edu.tw/gone"), RL).status == 410


def test_max_bytes_stops_a_large_download():
    from nknu.crawl.fetch import TooLarge

    big = b"%PDF" + b"0" * 5000
    declared = client(lambda req: httpx.Response(200, content=big, headers={"content-type": "application/pdf"}), Fake())
    with pytest.raises(TooLarge, match="declared"):
        declared.fetch(Target(URL), RL, max_bytes=1000)
    streamed = client(lambda req: httpx.Response(200, content=iter([big[:3000], big[3000:]]),
                                                 headers={"content-type": "application/pdf"}), Fake())
    with pytest.raises(TooLarge, match="more than"):
        streamed.fetch(Target(URL), RL, max_bytes=1000)
    ok = client(lambda req: httpx.Response(200, content=big, headers={"content-type": "application/pdf"}), Fake())
    assert ok.fetch(Target(URL), RL, max_bytes=10_000).body == big


# ---------------------------------------------------------------- archive crawl review (2026-10-24)


def test_retry_after_beyond_cap_hands_the_seconds_to_the_caller():
    # P5/R8: the archive crawl rests the whole server that long; v1 still sees a FetchError and the host is skipped
    from nknu.crawl.fetch import RetryLater

    c = client(lambda req: html(status=429, headers={"retry-after": "3600"}), Fake())
    with pytest.raises(RetryLater) as exc:
        c.fetch(Target(URL), RL)
    assert exc.value.seconds == 3600 and isinstance(exc.value, FetchError)
    with pytest.raises(HostCircuitOpen):
        c.fetch(Target(URL), RL)


def test_a_warc_that_cannot_be_written_is_not_a_fetch_error():
    # P2/R3: a full disk is not the server's failure, and the caller must be able to tell
    from nknu.crawl.fetch import ArchiveWriteError

    class Full:
        def write(self, *args):
            raise OSError(28, "No space left on device")

    with pytest.raises(ArchiveWriteError) as exc:
        client(lambda req: html(), Fake(), warc=Full()).fetch(Target(URL), RL)
    assert not isinstance(exc.value, FetchError) and exc.value.status == 200


def test_a_failed_warc_write_leaves_the_file_readable(tmp_path):
    # R3: half a gzip member would hide every record written after it
    from warcio.archiveiterator import ArchiveIterator

    from nknu.crawl.fetch import ArchiveWriteError

    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 24, 3, 0, tzinfo=UTC))
    c = client(lambda req: html(), Fake(), warc=sink)
    c.fetch(Target(URL), RL)

    class Half:
        def __init__(self, fh):
            self.fh = fh

        def fileno(self):
            return self.fh.fileno()

        def write(self, data):
            self.fh.write(bytes(data)[: len(data) // 2])
            raise OSError(28, "No space left on device")

        def close(self):
            self.fh.close()

    good = sink._fh
    sink._fh = Half(good)
    with pytest.raises(ArchiveWriteError):
        c.fetch(Target(URL), RL)
    sink._fh = good
    c.fetch(Target(URL), RL)
    sink.close()
    with open(sink.path, "rb") as fh:
        assert sum(rec.rec_type == "response" for rec in ArchiveIterator(fh)) == 2


def test_archive_mode_keeps_a_page_it_cannot_decode(tmp_path):
    # M4/R12: the raw bytes go to WARC first; the archive crawl gets the page without text instead of an error
    from warcio.archiveiterator import ArchiveIterator

    bad = "中".encode("cp950") + b"\xfa\x40"  # a user-defined Big5 character cp950 has no mapping for

    def handler(req):
        return httpx.Response(200, content=bad, headers={"content-type": "text/html; charset=big5"})

    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 24, 3, 0, tzinfo=UTC))
    lenient = Client(transport=httpx.MockTransport(handler), sleep=Fake().sleep, clock=Fake().clock, warc=sink,
                     strict_decode=False)
    snap = lenient.fetch(Target(URL), RL)
    assert snap.text is None and snap.target.meta.get("decode_error") and snap.body == bad
    with pytest.raises(DecodeError):
        client(handler, Fake(), warc=sink).fetch(Target(URL), RL)  # v1 still refuses it, but it is archived
    sink.close()
    with open(sink.path, "rb") as fh:
        assert sum(rec.rec_type == "response" for rec in ArchiveIterator(fh)) == 2


def test_decode_an_unusable_charset_is_a_decode_error():
    with pytest.raises(DecodeError):
        decode_body(b"\xff\xfe", "text/html; charset=undefined")
