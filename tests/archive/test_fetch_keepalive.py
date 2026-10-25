"""The archive crawl's fetch client (design §6.2–6.5, §12): one connection per server kept open between requests, each
request sent once, an idle connection the server let go resent once (GET only), every answer archived before it is
judged, a total time limit per request, and what each request did on the wire (the request table's new columns).

Most tests talk to a server of their own on 127.0.0.1 and count TCP connections; tests/conftest.py refuses
every other name lookup or connection, so a school host name used here is pointed at this machine first."""

from __future__ import annotations

import gzip
import ipaddress
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from warcio.archiveiterator import ArchiveIterator

from nknu.crawl import fetch as fetch_mod
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import KEEPALIVE_LIMITS, Client, FetchError, RetryLater, TooSlow
from nknu.crawl.warc import WarcSink

RL = RateLimit()  # 2–5 s between requests, on the fake clock


class Fake:
    """Fake clock + sleep: sleeping advances the clock."""

    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Loopback:
    """A local HTTP/1.1 server that keeps a connection open between requests, as the school's IIS and Apache do.

    The last path segment, when it is a number, is the status to answer (200 otherwise). A path under /drop/ on a
    connection that has already served a request is read and then dropped without an answer: an idle connection the
    server let go just as the next request went out. A path under /garbage/ on such a connection gets a broken status
    line: the server did answer, badly."""

    def __init__(self, tls: ssl.SSLContext | None = None):
        self.accepted: list[tuple] = []  # one per TCP connection
        self.seen: list[tuple[str, str]] = []  # (method, path) of every request read, dropped ones included
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.httpd.daemon_threads = True
        if tls is not None:
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path: str, host: str = "127.0.0.1", scheme: str = "http") -> str:
        return f"{scheme}://{host}:{self.port}{path}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # the connection stays open unless the client closes it

            def setup(self):
                super().setup()
                self.served = 0
                srv.accepted.append(self.client_address)

            def do_GET(self):
                self._answer()

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._answer()

            def _answer(self):
                srv.seen.append((self.command, self.path))
                if self.path.startswith("/drop/") and self.served:
                    self.close_connection = True  # read, then closed without a byte of answer
                    return
                if self.path.startswith("/garbage/") and self.served:
                    self.wfile.write(b"HTTP/1.1 2OO broken\r\n\r\n")  # an answer, but not one HTTP can read
                    self.close_connection = True
                    return
                self.served += 1
                last = self.path.rsplit("/", 1)[-1]
                body = b"<p>ok</p>"
                self.send_response(int(last) if last.isdigit() else 200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler


@pytest.fixture
def server():
    srv = Loopback()
    yield srv
    srv.close()


@pytest.fixture
def sockets(monkeypatch, server):
    """The client side of every connection to the test server: how many were open at once, at most. Counted when a
    socket connects and when it is closed, in the client's own thread, so it shows the order the client did them in."""
    open_now: set[int] = set()
    peak = {"n": 0}
    real_connect, real_close = socket.socket.connect, socket.socket.close

    def connect(self, address):
        real_connect(self, address)
        if isinstance(address, tuple) and address[1] == server.port:
            open_now.add(id(self))
            peak["n"] = max(peak["n"], len(open_now))

    def close(self):
        open_now.discard(id(self))
        real_close(self)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "close", close)
    return peak


def to_loopback(monkeypatch, *names: str) -> None:
    """Let these school host names resolve to this machine (the conftest guard refuses every other name)."""
    guarded = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        return guarded("127.0.0.1" if host in names else host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def archive(fake: Fake, warc=None, **kw) -> Client:
    """The client the archive crawl will use (design §6.2, §6.4): one kept connection, each request sent once."""
    return Client(limits=KEEPALIVE_LIMITS, attempts=1, sleep=fake.sleep, clock=fake.clock, warc=warc,
                  follow_redirects=False, **kw)


def open_connections(client: Client) -> int:
    return sum(len(pool._transport._pool.connections) for pool in {client._nknu, client._other})


def responses(path) -> list[str]:
    with open(path, "rb") as fh:
        return [rec.http_headers.get_statuscode() for rec in ArchiveIterator(fh) if rec.rec_type == "response"]


def record_at(path, offset: int) -> tuple[str, str]:
    """(record type, status) of the one WARC record that starts at this offset."""
    with open(path, "rb") as fh:
        fh.seek(offset)
        rec = next(iter(ArchiveIterator(fh)))
        return rec.rec_type, rec.http_headers.get_statuscode()


# ---------------------------------------------------------------- connection kept (§6.2)


def test_the_archive_limits_keep_one_connection_open_for_a_minute():
    assert (KEEPALIVE_LIMITS.max_connections, KEEPALIVE_LIMITS.max_keepalive_connections,
            KEEPALIVE_LIMITS.keepalive_expiry) == (1, 1, 60)
    client = archive(Fake())
    try:
        for pool in (client._nknu, client._other):
            p = pool._transport._pool
            assert (p._max_connections, p._max_keepalive_connections, p._keepalive_expiry) == (1, 1, 60)
    finally:
        client.close()


def test_two_requests_to_one_host_share_one_connection(server, sockets):
    client = archive(Fake())
    try:
        first = client.fetch(Target(server.url("/a")), RL)
        second = client.fetch(Target(server.url("/b")), RL)
    finally:
        client.close()
    assert (first.status, second.status) == (200, 200)
    assert len(server.accepted) == 1 and sockets["n"] == 1
    assert [s.new_conn for s in first.sent] == [True] and [s.new_conn for s in second.sent] == [False]
    assert second.sent[0].connect_ms is None  # nothing was connected for it


def test_switching_pools_closes_the_other_pools_connection_first(server, sockets, monkeypatch):
    # one server queue can hold a school host name (the school's certificate pool) and the bare IP (the general pool):
    # the two pools together never keep two connections to it
    to_loopback(monkeypatch, "c.nknu.edu.tw")
    client = archive(Fake())
    try:
        for url in (server.url("/a", "c.nknu.edu.tw"), server.url("/b"), server.url("/c", "c.nknu.edu.tw")):
            assert client.fetch(Target(url), RL).status == 200
            assert open_connections(client) == 1  # the one just used, kept for the next request
    finally:
        client.close()
    assert len(server.accepted) == 3 and sockets["n"] == 1


def test_two_names_on_one_pool_never_open_a_second_connection(server, sockets, monkeypatch):
    # c.nknu and aca.nknu are one server: the pool closes the idle connection to one before it opens one to the other
    to_loopback(monkeypatch, "c.nknu.edu.tw", "aca.nknu.edu.tw")
    client = archive(Fake())
    try:
        for host in ("c.nknu.edu.tw", "aca.nknu.edu.tw", "aca.nknu.edu.tw", "c.nknu.edu.tw"):
            assert client.fetch(Target(server.url("/p", host)), RL).status == 200
    finally:
        client.close()
    assert len(server.accepted) == 3 and sockets["n"] == 1  # aca's two requests shared one


def test_a_client_for_many_servers_keeps_both_pools(server, monkeypatch):
    # v1's client (default limits) talks to many servers from one object: switching pools closes nothing
    to_loopback(monkeypatch, "c.nknu.edu.tw")
    client = Client(sleep=Fake().sleep, clock=Fake().clock)
    try:
        client.fetch(Target(server.url("/a", "c.nknu.edu.tw")), RL)
        client.fetch(Target(server.url("/b")), RL)
        assert open_connections(client) == 2
    finally:
        client.close()


def test_a_proxy_in_the_environment_is_not_used(server, sockets, monkeypatch):
    # with a proxy set, httpx would send through a transport of its own that closing the pool never reaches: the two
    # pools would keep a tunnel each to the same server. The archive client asks the server itself (port 9: nothing
    # listens there, so a request that went to the proxy would fail)
    to_loopback(monkeypatch, "c.nknu.edu.tw")
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    client = archive(Fake())
    try:
        for pool, url in ((client._nknu, "https://c.nknu.edu.tw/"), (client._other, "http://127.0.0.1/")):
            assert pool._transport_for_url(httpx.URL(url)) is pool._transport  # the one switching pools closes
        for url in (server.url("/a", "c.nknu.edu.tw"), server.url("/b"), server.url("/c", "c.nknu.edu.tw")):
            assert client.fetch(Target(url), RL).status == 200
    finally:
        client.close()
    assert len(server.accepted) == 3 and sockets["n"] == 1


# ---------------------------------------------------------------- one request per call; the idle-connection resend (§6.4)


@pytest.mark.parametrize("rate", [RL, RateLimit(min_interval_s=0.0, jitter_s=0.0)])  # v1's gap; the crawl's NO_RATE
def test_an_idle_connection_the_server_closed_is_resent_once_after_the_least_gap(server, sockets, rate):
    # the server may have read the request it closed the connection on (this test server does: it sees it twice), so
    # the resend waits the least gap like any request to it (CLAUDE.md: 2–5 s; monitor.politeness: under 2 s is a
    # violation), never at once (review 10-26). The archive crawl waits its gap after the whole fetch, not inside it
    fake = Fake()
    client = archive(fake)
    try:
        client.fetch(Target(server.url("/a")), rate)
        snap = client.fetch(Target(server.url("/drop/x")), rate)
        assert client._failures["127.0.0.1"] == 0
    finally:
        client.close()
    assert snap.status == 200 and server.seen.count(("GET", "/drop/x")) == 2
    lost, again = snap.sent
    assert (lost.attempt, lost.idle_closed, lost.new_conn, lost.status) == (1, True, False, None) and lost.error
    assert (again.attempt, again.idle_closed, again.new_conn, again.status, again.error) == (2, False, True, 200, None)
    assert again.started - lost.ended >= fetch_mod.RESEND_GAP_S == 2.0
    assert len(server.accepted) == 2 and sockets["n"] == 1  # the dropped connection was gone before the new one


def test_the_crawl_sets_the_least_gap_of_a_resend_to_its_own():
    from nknu.archive.crawler import GAP, archive_client

    client = archive_client(None)
    try:
        assert client._resend_gap_s == GAP[0]
    finally:
        client.close()


def test_an_idle_close_is_not_held_against_the_host(server, tmp_path):
    # the resend answers 503: one failure for the breaker, not two, and the 503 is in the archive
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    client = archive(Fake(), warc=sink)
    try:
        client.fetch(Target(server.url("/a")), RL)
        with pytest.raises(FetchError, match="503") as exc:
            client.fetch(Target(server.url("/drop/503")), RL)
        assert client._failures["127.0.0.1"] == 1
    finally:
        client.close()
        sink.close()
    assert [(s.attempt, s.idle_closed, s.status) for s in exc.value.sent] == [(1, True, None), (2, False, 503)]
    assert responses(sink.path) == ["200", "503"]


def test_a_broken_answer_on_a_kept_connection_is_not_resent(server):
    # the server read the request and answered, if badly: not an idle connection it had let go (design §6.4: only
    # when no byte of answer came). Sent once, and held against the host like any failure
    client = archive(Fake())
    try:
        client.fetch(Target(server.url("/a")), RL)
        with pytest.raises(FetchError) as exc:
            client.fetch(Target(server.url("/garbage/x")), RL)
        assert client._failures["127.0.0.1"] == 1
    finally:
        client.close()
    assert server.seen.count(("GET", "/garbage/x")) == 1
    (sent,) = exc.value.sent
    assert (sent.attempt, sent.idle_closed, sent.new_conn, sent.status) == (1, False, False, None)
    assert "RemoteProtocolError" in sent.error


def test_a_post_is_never_resent(server):
    client = archive(Fake())
    try:
        client.fetch(Target(server.url("/a")), RL)
        with pytest.raises(FetchError) as exc:
            client.fetch(Target(server.url("/drop/list"), method="POST", data=[("__EVENTTARGET", "gvFull")]), RL)
    finally:
        client.close()
    assert server.seen.count(("POST", "/drop/list")) == 1
    assert [(s.attempt, s.method, s.status) for s in exc.value.sent] == [(1, "POST", None)]


def test_the_archive_client_sends_each_request_once():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(503, content=b"busy", headers={"content-type": "text/html"})

    client = Client(transport=httpx.MockTransport(handler), attempts=1, sleep=Fake().sleep, clock=Fake().clock)
    with pytest.raises(FetchError, match="503") as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert len(calls) == 1 and [s.status for s in exc.value.sent] == [503]


def test_v1_still_tries_four_times_and_each_try_is_noted():
    statuses = iter([503, 502, 200])

    def handler(req):
        return httpx.Response(next(statuses), content=b"<p>x</p>", headers={"content-type": "text/html"})

    snap = Client(transport=httpx.MockTransport(handler), sleep=Fake().sleep, clock=Fake().clock).fetch(
        Target("https://c.nknu.edu.tw/aca/"), RL)
    assert snap.status == 200 and [(s.attempt, s.status) for s in snap.sent] == [(1, 503), (2, 502), (3, 200)]


# ---------------------------------------------------------------- the server asks us to wait (§6.4, crawler._rest_queue)


@pytest.mark.parametrize(("status", "headers", "seconds"), [(503, {"retry-after": "60"}, 60), (429, {"retry-after": "90"}, 90),
                                                            (429, {}, fetch_mod.RETRY_429_S)])
def test_a_wait_asked_on_the_only_try_goes_to_the_caller(status, headers, seconds):
    # sent once, nothing is waited in place: the caller hears how long the server asked for (the archive crawl rests
    # the whole server queue on a RetryLater), and the host is not skipped for the run (the frontier keeps the wait)
    fake, calls = Fake(), []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, content=b"busy", headers={"content-type": "text/html", **headers})

    client = Client(transport=httpx.MockTransport(handler), attempts=1, sleep=fake.sleep, clock=fake.clock)
    with pytest.raises(RetryLater, match=f"HTTP {status}") as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert exc.value.seconds == seconds and isinstance(exc.value, FetchError)
    assert len(calls) == 1 and fake.sleeps == [] and [s.status for s in exc.value.sent] == [status]
    assert client._skip == {}
    client.reset_host("c.nknu.edu.tw")  # what crawler._rest_queue does
    with pytest.raises(RetryLater):
        client.fetch(Target("https://c.nknu.edu.tw/aca/b"), RL)  # sent: not refused as a skipped host
    assert len(calls) == 2


def test_a_503_without_retry_after_is_an_ordinary_failure():
    # nothing asked of us: the frontier tries the URL again later (30 min × attempts), the server queue does not rest
    client = Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)), attempts=1, sleep=Fake().sleep,
                    clock=Fake().clock)
    with pytest.raises(FetchError) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert not isinstance(exc.value, RetryLater) and client._failures["c.nknu.edu.tw"] == 1


def test_v1_hands_the_last_retry_after_to_the_caller():
    # v1 waits a short Retry-After in place between its tries; after the last one it says how long was asked
    fake = Fake()
    client = Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, headers={"retry-after": "7"})),
                    sleep=fake.sleep, clock=fake.clock)
    with pytest.raises(RetryLater) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert exc.value.seconds == 7 and fake.sleeps.count(7) == 3 and len(exc.value.sent) == 4
    assert client._failures["c.nknu.edu.tw"] == 1 and client._skip == {}  # one failure for the breaker, as before


# ---------------------------------------------------------------- answers archived before they are judged (§6.4)


@pytest.mark.parametrize(("status", "headers", "error"), [(503, {}, FetchError), (500, {}, FetchError),
                                                          (408, {}, FetchError),
                                                          (429, {"retry-after": "3600"}, RetryLater)])
def test_a_408_429_or_5xx_is_archived_before_the_error(tmp_path, status, headers, error):
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))

    def handler(req):
        return httpx.Response(status, content=b"<h1>Service Unavailable</h1>",
                              headers={"content-type": "text/html", **headers})

    client = Client(transport=httpx.MockTransport(handler), attempts=1, sleep=Fake().sleep, clock=Fake().clock,
                    warc=sink)
    with pytest.raises(error):
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    sink.close()
    assert responses(sink.path) == [str(status)]
    assert b"Service Unavailable" in gzip.decompress(sink.path.read_bytes())


def test_v1_archives_every_try(tmp_path):
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    client = Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)), sleep=Fake().sleep,
                    clock=Fake().clock, warc=sink)
    with pytest.raises(FetchError):
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    sink.close()
    assert responses(sink.path) == ["503"] * 4


def test_a_failed_request_points_at_its_archived_answer(tmp_path):
    # the 503 kept as evidence is found by its place, as every new record is (design §12), not by its URL
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    client = Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, content=b"busy")), attempts=1,
                    sleep=Fake().sleep, clock=Fake().clock, warc=sink)
    with pytest.raises(FetchError) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    sink.close()
    (sent,) = exc.value.sent
    assert record_at(sink.path, sent.warc_offset) == ("response", "503") and sent.warc_len > 0


def test_each_try_points_at_its_own_answer(server, tmp_path):
    statuses = iter([503, 502, 200])
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    v1 = Client(transport=httpx.MockTransport(lambda req: httpx.Response(next(statuses))), sleep=Fake().sleep,
                clock=Fake().clock, warc=sink)
    snap = v1.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert [record_at(sink.path, s.warc_offset) for s in snap.sent] == [("response", c) for c in ("503", "502", "200")]
    assert (snap.sent[-1].warc_offset, snap.sent[-1].warc_len) == (snap.warc_offset, snap.warc_len)
    # the request lost on an idle connection has no answer, so no place; its resend has one
    client = archive(Fake(), warc=sink)
    try:
        client.fetch(Target(server.url("/a")), RL)
        lost, again = client.fetch(Target(server.url("/drop/x")), RL).sent
    finally:
        client.close()
        sink.close()
    assert (lost.warc_offset, lost.warc_len) == (None, None)
    assert record_at(sink.path, again.warc_offset) == ("response", "200")
    unarchived = Client(transport=httpx.MockTransport(lambda req: httpx.Response(200)), sleep=Fake().sleep,
                        clock=Fake().clock)
    assert unarchived.fetch(Target("https://c.nknu.edu.tw/aca/"), RL).sent[0].warc_offset is None  # no archive


# ---------------------------------------------------------------- the total time limit (§6.5)


def trickle(fake: Fake, ctype: str, chunks: int = 3, seconds: float = 50.0):
    """A response whose body comes in slowly: each chunk arrives `seconds` after the last (on the fake clock)."""

    def body():
        for _ in range(chunks):
            fake.t += seconds
            yield b"x" * 100

    return lambda req: httpx.Response(200, content=body(), headers={"content-type": ctype})


def test_a_page_that_trickles_past_two_minutes_is_cut_off():
    from nknu.archive.crawler import error_kind

    fake = Fake()
    client = Client(transport=httpx.MockTransport(trickle(fake, "text/html; charset=utf-8")), attempts=1,
                    sleep=fake.sleep, clock=fake.clock)
    with pytest.raises(FetchError) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL, max_bytes=10_000)
    assert isinstance(exc.value.__cause__, TooSlow) and isinstance(exc.value.__cause__, httpx.TimeoutException)
    assert error_kind(exc.value) == "timeout"  # retried as GETs are, like any timeout
    assert exc.value.sent[0].status == 200 and exc.value.sent[0].error


def test_an_attachment_may_take_ten_minutes():
    fake = Fake()
    client = Client(transport=httpx.MockTransport(trickle(fake, "application/pdf")), attempts=1, sleep=fake.sleep,
                    clock=fake.clock)
    assert client.fetch(Target("https://c.nknu.edu.tw/aca/a.pdf"), RL, max_bytes=10_000).body == b"x" * 300
    slow = Client(transport=httpx.MockTransport(trickle(fake, "application/pdf", chunks=13)), attempts=1,
                  sleep=fake.sleep, clock=fake.clock)
    with pytest.raises(FetchError) as exc:
        slow.fetch(Target("https://c.nknu.edu.tw/aca/a.pdf"), RL)  # without max_bytes too
    assert isinstance(exc.value.__cause__, TooSlow)


def test_the_time_limits_can_be_set():
    fake = Fake()
    client = Client(transport=httpx.MockTransport(trickle(fake, "application/pdf", seconds=5)), attempts=1,
                    sleep=fake.sleep, clock=fake.clock, time_limits=(2.0, 12.0))
    with pytest.raises(FetchError) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/a.pdf"), RL)
    assert isinstance(exc.value.__cause__, TooSlow)


# ---------------------------------------------------------------- what each request did on the wire (§6.2, §12)


def test_the_trace_notes_the_connection(server):
    client = archive(Fake())
    try:
        snap = client.fetch(Target(server.url("/a")), RL)
    finally:
        client.close()
    (sent,) = snap.sent
    assert (sent.attempt, sent.method, sent.status, sent.bytes, sent.new_conn, sent.ip) == (
        1, "GET", 200, len(b"<p>ok</p>"), True, "127.0.0.1")
    assert sent.connect_ms is not None and sent.connect_ms >= 0 and sent.tls_ms is None  # plain http: no handshake
    assert sent.started <= sent.ended and sent.error is None


def test_a_request_refused_before_sending_notes_nothing():
    client = Client(transport=httpx.MockTransport(lambda req: httpx.Response(500)), sleep=Fake().sleep,
                    clock=Fake().clock, attempts=1)
    for _ in range(3):
        with pytest.raises(FetchError):
            client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    with pytest.raises(fetch_mod.HostCircuitOpen) as exc:
        client.fetch(Target("https://c.nknu.edu.tw/aca/"), RL)
    assert exc.value.sent == ()


def _tls(tmp_path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """A test CA and a certificate for 127.0.0.1 it signed: (the server's context, a client context trusting only that
    CA). Verification stays on and strict, as in production."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def cert(subject, key, issuer, issuer_key, *extensions):
        b = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
        for ext, critical in extensions:
            b = b.add_extension(ext, critical=critical)
        return b.sign(issuer_key, hashes.SHA256())

    usage = {k: False for k in ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment",
                                "key_agreement", "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")}
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = cert("nknu test CA", ca_key, "nknu test CA", ca_key, (x509.BasicConstraints(ca=True, path_length=None), True),
              (x509.KeyUsage(**{**usage, "digital_signature": True, "key_cert_sign": True, "crl_sign": True}), True))
    leaf = cert("127.0.0.1", key, "nknu test CA", ca_key, (x509.BasicConstraints(ca=False, path_length=None), True),
                (x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False),
                (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False),
                (x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False))
    pem = serialization.Encoding.PEM
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(pem))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
    served = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    served.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "key.pem")
    trusting = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
    assert trusting.verify_flags & ssl.VERIFY_X509_STRICT and trusting.verify_mode == ssl.CERT_REQUIRED
    return served, trusting


def test_the_trace_times_the_tls_handshake(tmp_path, monkeypatch):
    served, trusting = _tls(tmp_path)
    monkeypatch.setattr(fetch_mod, "default_ssl_context", lambda: trusting)
    srv = Loopback(tls=served)
    client = archive(Fake())
    try:
        first = client.fetch(Target(srv.url("/a", scheme="https")), RL)
        second = client.fetch(Target(srv.url("/b", scheme="https")), RL)
    finally:
        client.close()
        srv.close()
    (a,), (b,) = first.sent, second.sent
    assert a.new_conn and a.tls_ms is not None and a.tls_ms > 0 and a.connect_ms is not None and a.ip == "127.0.0.1"
    assert b.new_conn is False and b.tls_ms is None and b.ip == "127.0.0.1"  # the kept connection: no new handshake
    assert len(srv.accepted) == 1
