"""Local WARC archive of every fetch: data/warc/YYYY/MM/DD/<adapter>-<run_id>.warc.gz, kept 180 days."""

from __future__ import annotations

import io
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

if TYPE_CHECKING:
    from nknu.crawl.fetch import Snapshot

_DROP = {"content-encoding", "transfer-encoding", "content-length"}  # body is stored decoded


class WarcSink:
    def __init__(self, root: Path, adapter: str, run_id: int, now: datetime):
        d = Path(root) / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{adapter}-{run_id}.warc.gz"
        self._fh = open(self.path, "ab")  # noqa: SIM115 - closed in close()
        self._w = WARCWriter(self._fh, gzip=True)

    def write(self, snap: Snapshot, request_headers: dict[str, str], request_body: bytes = b"") -> None:
        parts = urlsplit(snap.url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        req = StatusAndHeaders(f"{snap.target.method} {target} HTTP/1.1", list(request_headers.items()), is_http_request=True)
        self._w.write_record(self._w.create_warc_record(snap.url, "request", payload=io.BytesIO(request_body),
                                                        http_headers=req))
        headers = [(k, v) for k, v in snap.headers.items() if k.lower() not in _DROP]
        headers.append(("Content-Length", str(len(snap.body))))
        resp = StatusAndHeaders(f"{snap.status}", headers, protocol="HTTP/1.1")
        self._w.write_record(self._w.create_warc_record(snap.url, "response", payload=io.BytesIO(snap.body),
                                                        http_headers=resp))

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
