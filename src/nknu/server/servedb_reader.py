"""Read-only access to serve.db. `nknu build` replaces the file atomically (new inode), so every query checks the file's
identity and reopens when it changed; a connection already handed out keeps reading its own snapshot until dropped.

FastMCP runs sync tools in worker threads, and a sqlite3 connection must not be used by two threads at once (cursors and
statements interleave: wrong rows, InterfaceError). Each thread therefore gets its own connection (check_same_thread stays
on, so a connection that leaks into another thread fails loudly), together with the meta it read from that snapshot."""

from __future__ import annotations

import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from nknu.build.templates import Templates, load_templates
from nknu.settings import REPO_ROOT

DEFAULT_TEMPLATES = REPO_ROOT / "config" / "templates.yaml"


@lru_cache(maxsize=4)
def _templates(path: Path, mtime_ns: int) -> Templates:
    return load_templates(path)


class ServeDB:
    def __init__(self, path: Path, *, templates_path: Path = DEFAULT_TEMPLATES):
        self.path = Path(path)
        self.templates_path = Path(templates_path)
        self._local = threading.local()  # per thread: key (st_ino, st_mtime_ns), conn, meta

    def _open(self) -> tuple[sqlite3.Connection, dict[str, str]]:
        st = os.stat(self.path)  # FileNotFoundError when serve.db is missing: the caller reports it
        key = (st.st_ino, st.st_mtime_ns)
        local = self._local
        if getattr(local, "key", None) != key:
            conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            local.meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
            local.conn, local.key = conn, key  # the old connection closes when its last user drops it
        return local.conn, local.meta

    def conn(self) -> sqlite3.Connection:
        return self._open()[0]

    def meta(self) -> dict[str, str]:
        return dict(self._open()[1])

    @property
    def kb(self) -> str:
        """Short snapshot id for envelopes: the random tail of the build's uuid7 (the full id is in meta/health)."""
        return self.meta()["build_id"].replace("-", "")[-12:]

    def templates(self) -> Templates:
        return _templates(self.templates_path, os.stat(self.templates_path).st_mtime_ns)
