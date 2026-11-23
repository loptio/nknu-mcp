"""HTTP Last-Modified of every archived response, read from the crawl's WARC files (read-only): the last-resort date of
an attachment that writes none (review 11-01 §3.5; an upload or replacement day, never a publication day).

    python -m nknu.content.serverdates --warc-dir archive/warc --out research/skeleton_2026-11-01/server_dates.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator

from nknu.content.dates import kept_range
from nknu.core.timeutil import TAIPEI

log = logging.getLogger(__name__)


def _day(value: str | None, lo: date, hi: date) -> date | None:
    """An HTTP-date as its Asia/Taipei day; None when unreadable or outside lo … hi."""
    try:
        when = parsedate_to_datetime(value or "")
    except (TypeError, ValueError, IndexError):
        return None
    day = (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(TAIPEI).date()
    return day if lo <= day <= hi else None


def collect_last_modified(warc_dir: Path, urls: set[str] | None = None) -> dict[str, str]:
    """url -> ISO day (Asia/Taipei) of the Last-Modified of its 2xx response; a URL fetched more than once keeps its
    latest fetch (WARC-Date). Only `urls` when given. A broken record ends its file, not the scan."""
    lo, hi = kept_range()
    best: dict[str, tuple[str, date]] = {}  # url -> (WARC-Date, day)
    for path in sorted(warc_dir.rglob("*.warc.gz")):
        try:
            with open(path, "rb") as fh:
                for rec in ArchiveIterator(fh):
                    if rec.rec_type != "response" or rec.http_headers is None:
                        continue
                    url = rec.rec_headers.get_header("WARC-Target-URI") or ""
                    if (urls is not None and url not in urls) or not (rec.http_headers.get_statuscode() or "").startswith("2"):
                        continue
                    day = _day(rec.http_headers.get_header("Last-Modified"), lo, hi)
                    fetched = rec.rec_headers.get_header("WARC-Date") or ""
                    if day and (url not in best or fetched >= best[url][0]):
                        best[url] = (fetched, day)
        except Exception as exc:  # noqa: BLE001 - a file still being written or cut short: what came before counts
            log.warning("%s: read up to a broken record (%s)", path, type(exc).__name__)
    return {url: day.isoformat() for url, (_, day) in sorted(best.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.content.serverdates",
                                 description="Write url -> Last-Modified day (JSON) from WARC files, read-only.")
    ap.add_argument("--warc-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if not args.warc_dir.is_dir():
        print(f"not a directory: {args.warc_dir}", file=sys.stderr)
        return 2
    got = collect_last_modified(args.warc_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, args.out)
    print(f"{len(got)} URLs with a Last-Modified day -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
