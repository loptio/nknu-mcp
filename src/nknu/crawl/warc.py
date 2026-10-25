"""Local WARC archive of every fetch: data/warc/YYYY/MM/DD/<adapter>-<run_id>.warc.gz, kept 180 days.

A response record holds the HTTP response as it was received (WARC 1.1 §6.3.2; review 10-26 A1, the owner's 「照原样
一笔一笔写进去，不改一个字」): the status line with the server's reason phrase, every header line in its own case and
order (a name sent twice, twice; a byte above 127 as that byte, where warcio would percent-encode it), and the body as
the wire carried it, still compressed (a reader such as warcio's content_stream() or pywb decodes it). The one thing
that cannot be kept is the chunk framing: httpcore takes it off before the body reaches us, so the body is stored whole
and the Transfer-Encoding line is left out (it would send a reader looking for chunks). The request record holds the
headers as they were sent, the API key and other credentials as [redacted]."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

if TYPE_CHECKING:
    from nknu.crawl.fetch import Snapshot

_DROP = {"content-encoding", "transfer-encoding", "content-length"}  # a snapshot made by hand holds the decoded body
_CHUNKED = b"transfer-encoding"  # the framing httpcore removed: never written for a body stored whole
_SECRET = {"x-goog-api-key", "authorization", "proxy-authorization"}  # request headers archived as [redacted]


class _AsReceived(StatusAndHeaders):
    """An HTTP head written exactly as given: warcio rebuilds the header block it writes (compute_headers_buffer, as
    ASCII with any other byte percent-encoded); this one keeps the bytes the server sent."""

    def __init__(self, head: bytes, statusline: str, headers: list[tuple[str, str]], protocol: str):
        super().__init__(statusline, headers, protocol=protocol)
        self._head = head

    def compute_headers_buffer(self, header_filter=None):
        self.headers_buff = self._head


def _as_received(snap: Snapshot) -> tuple[_AsReceived, bytes]:
    """The response's head as the server wrote it (Transfer-Encoding left out), and its body still compressed."""
    status = f"{snap.status} ".encode("ascii") + (snap.reason or b"")
    lines = [(k, v) for k, v in snap.raw_headers if k.strip().lower() != _CHUNKED]
    head = snap.http_version.encode("ascii") + b" " + status + b"\r\n"
    head += b"".join(k + b": " + v + b"\r\n" for k, v in lines) + b"\r\n"
    parsed = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in lines]
    body = snap.raw_body if snap.raw_body is not None else snap.body
    return _AsReceived(head, status.decode("latin-1"), parsed, snap.http_version), body


def _made_by_hand(snap: Snapshot) -> tuple[StatusAndHeaders, bytes]:
    """A snapshot with no raw answer (tests; nothing the client fetched): its decoded body with the headers that fit it."""
    headers = [(k, v) for k, v in snap.headers.items() if k.lower() not in _DROP]
    headers.append(("Content-Length", str(len(snap.body))))
    try:
        phrase = HTTPStatus(snap.status).phrase
    except ValueError:
        phrase = ""
    return StatusAndHeaders(f"{snap.status} {phrase}".rstrip(), headers, protocol="HTTP/1.1"), snap.body


class WarcSink:
    def __init__(self, root: Path, adapter: str, run_id: int, now: datetime):
        d = Path(root) / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{adapter}-{run_id}.warc.gz"
        # unbuffered: a write that fails leaves nothing behind in a buffer to be flushed on close
        self._fh = open(self.path, "ab", buffering=0)  # noqa: SIM115 - closed in close()

    def write(self, snap: Snapshot, request_headers: Mapping[str, str] | Iterable[tuple[str, str]],
              request_body: bytes = b"") -> tuple[int, int]:
        """The request and response records, appended whole or not at all: on any error the file is cut back to where
        it was (no new space needed), since half a gzip member would hide every record written after it.
        request_headers: as sent, in order (a mapping or (name, value) pairs); any credential is written [redacted].

        Returns (offset, length) of the response record in this file: each record is a gzip member of its own, so a
        reader seeks there and reads that one record (url.warc_offset, url.warc_len; design §12)."""
        buf = io.BytesIO()
        w = WARCWriter(buf, gzip=True)
        parts = urlsplit(snap.url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        pairs = request_headers.items() if isinstance(request_headers, Mapping) else request_headers
        kept = [(k, "[redacted]" if k.lower() in _SECRET else v) for k, v in pairs]
        req = StatusAndHeaders(f"{snap.target.method} {target} HTTP/1.1", kept, is_http_request=True)
        w.write_record(w.create_warc_record(snap.url, "request", payload=io.BytesIO(request_body), http_headers=req))
        request_len = buf.tell()
        resp, payload = _as_received(snap) if snap.raw_headers else _made_by_hand(snap)
        w.write_record(w.create_warc_record(snap.url, "response", payload=io.BytesIO(payload), http_headers=resp))
        records = buf.getvalue()
        data = memoryview(records)
        start = os.fstat(self._fh.fileno()).st_size
        try:
            while data:
                n = self._fh.write(data)
                if not n:
                    raise OSError("the WARC file took no bytes")
                data = data[n:]
        except BaseException:
            with contextlib.suppress(OSError):
                os.ftruncate(self._fh.fileno(), start)
            raise
        return start + request_len, len(records) - request_len

    def close(self) -> None:
        self._fh.close()


def gc_warc(root: Path, today: date, keep_days: int = 180) -> int:
    """Delete day directories older than keep_days. Returns the number of files removed."""
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for day_dir in sorted(Path(root).glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]")):
        y, m, d = (int(p) for p in day_dir.parts[-3:])
        if date(y, m, d) < cutoff:
            removed += sum(1 for f in day_dir.iterdir() if f.is_file())
            shutil.rmtree(day_dir)
    for month in Path(root).glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]"):
        if not any(month.iterdir()):
            month.rmdir()
    return removed
