"""The WARC archive keeps each answer as it came (review 10-26 A1; WARC 1.1 §6.3.2: a response record holds the HTTP
response as received): the status line with the server's own reason phrase, every header line in its own case and
order, a name sent twice kept twice, and the body still compressed. The program reads the decoded copy; a reader of the
archive (warcio, pywb) decodes it. What cannot be kept is the chunk framing (httpcore takes it off before any caller
sees the body), so Transfer-Encoding is the one header left out. The request record still hides the API key.

A server of these tests' own on 127.0.0.1 writes the exact bytes of each answer, so the wire is known byte for byte."""

from __future__ import annotations

import gzip
import io
import socket
import threading
from datetime import UTC, datetime

import pytest
from warcio.archiveiterator import ArchiveIterator

from nknu.archive.fetch import KEEPALIVE_LIMITS, Client, FetchError, TooLarge
from nknu.archive.target import RateLimit, Target
from nknu.archive.warc import WarcSink

RL = RateLimit()
PAGE = "<html><body>體育室 開放時間</body></html>".encode()


class Fake:
    def __init__(self):
        self.t = 1000.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


class RawServer:
    """Answers each request with the bytes it was given, as written, then closes the connection."""

    def __init__(self, answer: bytes):
        self.answer = answer
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                conn.sendall(self.answer)

    def url(self, path: str = "/page.aspx") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def serve():
    servers = []

    def start(answer: bytes) -> RawServer:
        servers.append(RawServer(answer))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


def answer(head: list[bytes], body: bytes) -> bytes:
    return b"\r\n".join(head) + b"\r\n\r\n" + body


def fetch(tmp_path, url: str, **kw):
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 26, 3, 0, tzinfo=UTC))
    client = Client(warc=sink, sleep=Fake().sleep, clock=Fake().clock, follow_redirects=False, attempts=1,
                    strict_decode=False, limits=KEEPALIVE_LIMITS)  # as the archive crawl: no proxy from the environment
    try:
        snap = client.fetch(Target(url), RL, **kw)
    finally:
        client.close()
        sink.close()
    return snap, sink


class Stored:
    """One record as a WARC reader sees it, read while the reader is on it."""

    def __init__(self, rec):
        self.rec_type, self.rec_headers, self.http_headers = rec.rec_type, rec.rec_headers, rec.http_headers
        self.content = rec.content_stream().read()  # decoded from its Content-Encoding, as warcio and pywb do

    def content_stream(self):
        return io.BytesIO(self.content)


def response_at(sink: WarcSink, offset: int, length: int):
    """The one record at (offset, length) of the WARC file, read on its own as url.warc_offset/warc_len point to it,
    and the bytes of that gzip member."""
    data = sink.path.read_bytes()[offset:offset + length]
    records = [Stored(rec) for rec in ArchiveIterator(io.BytesIO(data))]
    assert len(records) == 1
    return records[0], gzip.decompress(data)


def http_block(record_bytes: bytes) -> bytes:
    """What follows the WARC headers of a record: the HTTP response as stored."""
    return record_bytes.split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n\r\n")


def test_a_gzip_answer_is_archived_byte_for_byte_and_read_back_decoded(tmp_path, serve):
    gz = gzip.compress(PAGE)
    wire = answer([b"HTTP/1.1 200 Fine Thanks", b"Content-Type: text/html; charset=utf-8", b"Content-Encoding: gzip",
                   b"Content-Length: " + str(len(gz)).encode(), b"X-Powered-By: ASP.NET"], gz)
    srv = serve(wire)
    snap, sink = fetch(tmp_path, srv.url())
    assert snap.body == PAGE and snap.text == PAGE.decode()  # the program reads the decoded page
    assert snap.headers["content-encoding"] == "gzip"
    rec, stored = response_at(sink, snap.warc_offset, snap.warc_len)
    assert rec.rec_type == "response" and rec.rec_headers.get_header("WARC-Target-URI") == srv.url()
    assert http_block(stored) == wire  # the answer exactly as the server wrote it
    assert rec.http_headers.protocol == "HTTP/1.1" and rec.http_headers.statusline == "200 Fine Thanks"
    assert rec.http_headers.get_header("Content-Encoding") == "gzip"
    assert rec.content_stream().read() == PAGE  # a WARC reader decodes it


def test_a_header_sent_twice_is_archived_twice_in_its_own_case(tmp_path, serve):
    wire = answer([b"HTTP/1.1 302 Found", b"Location: /next.aspx", b"Set-Cookie: ASP.NET_SessionId=abc; path=/",
                   b"set-cookie: lang=zh-TW; path=/", b"X-AspNet-Version: 4.0.30319", b"Content-Length: 0"], b"")
    srv = serve(wire)
    snap, sink = fetch(tmp_path, srv.url())
    assert snap.status == 302 and snap.headers["location"] == "/next.aspx"  # the program's view: one name, lower case
    rec, stored = response_at(sink, snap.warc_offset, snap.warc_len)
    assert http_block(stored) == wire
    cookies = [(k, v) for k, v in rec.http_headers.headers if k.lower() == "set-cookie"]
    assert cookies == [("Set-Cookie", "ASP.NET_SessionId=abc; path=/"), ("set-cookie", "lang=zh-TW; path=/")]
    assert ("X-AspNet-Version", "4.0.30319") in rec.http_headers.headers


def test_a_chunked_answer_is_archived_whole_without_its_transfer_encoding(tmp_path, serve):
    gz = gzip.compress(PAGE)
    chunked = b"".join(b"%x\r\n" % len(part) + part + b"\r\n" for part in (gz[:10], gz[10:])) + b"0\r\n\r\n"
    head = [b"HTTP/1.1 200 OK", b"Content-Type: text/html; charset=utf-8", b"Transfer-Encoding: chunked",
            b"Content-Encoding: gzip", b"Vary: Accept-Encoding"]
    srv = serve(answer(head, chunked))
    snap, sink = fetch(tmp_path, srv.url())
    assert snap.body == PAGE
    rec, stored = response_at(sink, snap.warc_offset, snap.warc_len)
    # the chunk framing never reaches us (httpcore removes it): the body is the gzip the chunks carried, and the one
    # header that would make a reader look for chunks is left out; every other line is as it came
    assert http_block(stored) == answer([h for h in head if not h.startswith(b"Transfer-Encoding")], gz)
    assert rec.content_stream().read() == PAGE


def test_a_header_value_in_big5_or_utf8_is_archived_as_its_bytes(tmp_path, serve):
    # warcio would percent-encode a byte above 127 in a header; the archive keeps the bytes the server sent
    name = "附件 一.pdf"
    head = [b"HTTP/1.1 200 OK", b"Content-Type: application/pdf",
            b'Content-Disposition: attachment; filename="' + name.encode("cp950") + b'"',
            b"X-Name: " + name.encode(), b"Content-Length: 4"]
    srv = serve(answer(head, b"%PDF"))
    snap, sink = fetch(tmp_path, srv.url("/download.aspx"))
    _rec, stored = response_at(sink, snap.warc_offset, snap.warc_len)
    assert http_block(stored) == answer(head, b"%PDF")


def test_an_answer_whose_compression_is_broken_is_archived_before_it_fails(tmp_path, serve):
    wire = answer([b"HTTP/1.1 200 OK", b"Content-Type: text/html", b"Content-Encoding: gzip", b"Content-Length: 9"],
                  b"not gzip!")
    srv = serve(wire)
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 26, 3, 0, tzinfo=UTC))
    client = Client(warc=sink, sleep=Fake().sleep, clock=Fake().clock, follow_redirects=False, attempts=1,
                    limits=KEEPALIVE_LIMITS)
    with pytest.raises(FetchError) as exc:
        client.fetch(Target(srv.url()), RL)
    client.close()
    sink.close()
    sent = exc.value.sent[-1]
    assert sent.status == 200 and sent.warc_offset is not None  # the request table finds the answer in the WARC
    _rec, stored = response_at(sink, sent.warc_offset, sent.warc_len)
    assert http_block(stored) == wire


def test_max_bytes_holds_on_the_wire_and_once_decoded(tmp_path, serve):
    bomb = gzip.compress(b"\0" * 200_000)  # small on the wire, large once decoded
    srv = serve(answer([b"HTTP/1.1 200 OK", b"Content-Type: application/pdf", b"Content-Encoding: gzip",
                        b"Content-Length: " + str(len(bomb)).encode()], bomb))
    with pytest.raises(TooLarge, match="once decoded"):
        fetch(tmp_path, srv.url("/a.pdf"), max_bytes=50_000)
    big = b"%PDF" + b"0" * 60_000
    srv2 = serve(answer([b"HTTP/1.1 200 OK", b"Content-Type: application/pdf", b"Transfer-Encoding: chunked"],
                        b"%x\r\n" % len(big) + big + b"\r\n0\r\n\r\n"))
    with pytest.raises(TooLarge, match="more than"):
        fetch(tmp_path / "b", srv2.url("/b.pdf"), max_bytes=50_000)


def test_the_request_record_keeps_the_headers_sent_and_hides_the_key(tmp_path, monkeypatch):
    import httpx

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real-0123456789")
    sink = WarcSink(tmp_path, "q", 1, datetime(2026, 10, 26, 3, 0, tzinfo=UTC))
    client = Client(warc=sink, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
                    sleep=Fake().sleep, clock=Fake().clock)
    client.fetch(Target("https://www.googleapis.com/drive/v3/files/x", credential="google_api_key",
                        headers={"X-Goog-Drive-Resource-Keys": "x/rk"}), RL)
    client.close()
    sink.close()
    with open(sink.path, "rb") as fh:
        req = next(r for r in ArchiveIterator(fh) if r.rec_type == "request")
        names = [k for k, _v in req.http_headers.headers]
        assert "X-Goog-Drive-Resource-Keys" in names  # the case it was sent in
        assert req.http_headers.get_header("X-Goog-Api-Key") == "[redacted]"
    assert b"test-key-not-real" not in gzip.decompress(sink.path.read_bytes())
