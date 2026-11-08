"""The to-text cache (plan 2026-11-02 T3b): one folder per content hash, <root>/<sha[:2]>/<sha>/, so a file that sits
in the export under several names converts once and a rerun converts only what has no result yet. In a folder:
text.md (the text), extras such as docling.json.gz and ocr.json, and result.json (status, reason, counts), written
last; a result counts only at the current PIPELINE_VERSION, and a page's only when the current page rule read it
(pagetext.PAGE_VERSION ends its page_kind, plan 2026-11-08 T2: pages, and only pages, are read again when the page
rule changes; every PDF and OCR result stays). A 'started' marker stands while a file converts: left with no result,
the process crashed or the watchdog ended it on that file (the marker's text then says why), and a run does not try
that file again unless asked. Every file is written through a temporary file and os.replace, so a crash never leaves
half a file under a real name."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from nknu.totext.pagetext import read_now

PIPELINE_VERSION = "totext-2"  # bumped on every change to what a conversion produces (OCR settings and rules, the
#                                 routes): totext-2 = T4's OCR, T5's archives and the phase-1 review's fixes
RESULT, TEXT, STARTED = "result.json", "text.md", "started"
_SHA = re.compile(r"[0-9a-f]{64}")
_EXTRA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """path holds data or its old content, never a part: a temporary file beside it, then os.replace."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def dir(self, sha: str) -> Path:
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError(f"not a sha256 hex digest: {sha!r}")
        return self.root / sha[:2] / sha

    def done(self, sha: str) -> dict | None:
        """result.json when it is at PIPELINE_VERSION and, for a page, read by the current page rule; None when
        missing, damaged or older (converted again: the run, the accounting and assemble all ask here)."""
        path = self.dir(sha) / RESULT
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(result, dict) or result.get("version") != PIPELINE_VERSION:
            return None
        if result.get("route") == "page" and not read_now(str(result.get("page_kind") or "")):
            return None  # read by an older page rule (plan 2026-11-08 T2)
        return result

    def crashed(self, sha: str) -> bool:
        """A 'started' marker and no current result: the process ended while converting this file."""
        return (self.dir(sha) / STARTED).exists() and self.done(sha) is None

    def begin(self, sha: str, note: str = "") -> None:
        folder = self.dir(sha)
        folder.mkdir(parents=True, exist_ok=True)
        write_atomic(folder / STARTED, note.encode("utf-8"))

    def crash_note(self, sha: str) -> str:
        """The marker's text (the watchdog's reason); '' after a plain crash or with no marker."""
        try:
            return (self.dir(sha) / STARTED).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""

    def abandon(self, sha: str) -> None:
        """Drop the marker of a conversion that was interrupted, not crashed: the file stays pending."""
        (self.dir(sha) / STARTED).unlink(missing_ok=True)

    def save(self, sha: str, result: dict, text: str, extras: dict[str, bytes]) -> None:
        """text.md and the extras, then result.json (with "version", PIPELINE_VERSION unless result names one), then
        anything an earlier save left that this one does not write (a *.tmp may be another process's write in
        flight: kept), then the marker."""
        reserved = {RESULT, TEXT, STARTED}
        for name in extras:
            if not _EXTRA.fullmatch(name) or name in reserved or name.endswith(".tmp"):
                raise ValueError(f"not a name for an extra: {name!r}")
        folder = self.dir(sha)
        folder.mkdir(parents=True, exist_ok=True)
        write_atomic(folder / TEXT, text.encode("utf-8"))
        for name, data in extras.items():
            write_atomic(folder / name, data)
        record = {"version": PIPELINE_VERSION, **result}
        write_atomic(folder / RESULT, json.dumps(record, ensure_ascii=False, indent=1, default=str).encode("utf-8"))
        keep = reserved | set(extras)
        for old in folder.iterdir():
            if old.name not in keep and not old.name.endswith(".tmp") and old.is_file():
                old.unlink(missing_ok=True)
        (folder / STARTED).unlink(missing_ok=True)

    def text(self, sha: str) -> str:
        """text.md; '' when there is none."""
        try:
            return (self.dir(sha) / TEXT).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
