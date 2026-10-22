"""Read-only access to serve.db. `nknu build` replaces the file atomically (new inode), so every query checks the file's
identity and reopens when it changed; a connection already handed out keeps reading its own snapshot until dropped."""

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
        self._lock = threading.Lock()
        self._key: tuple[int, int] | None = None
        self._conn: sqlite3.Connection | None = None
        self._meta: dict[str, str] = {}

    def _open(self) -> tuple[sqlite3.Connection, dict[str, str]]:
        st = os.stat(self.path)  # FileNotFoundError when serve.db is missing: the caller reports it
        key = (st.st_ino, st.st_mtime_ns)
        with self._lock:
            if key != self._key or self._conn is None:
                conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
                self._conn, self._key = conn, key  # the old connection closes when its last user drops it
            return self._conn, self._meta

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
