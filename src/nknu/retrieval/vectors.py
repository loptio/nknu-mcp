"""The dense path's index (plan 2026-11-02 T8): every chunk's bge-m3 vector from chunk_vecs (T7b) in one float32 matrix,
searched by brute-force cosine with numpy. One index per database file and process, reloaded when the build changes
(a rebuild replaces the file under a running server and renumbers chunk_id). Scores only: no text is generated. Since
11-08 the index also knows each chunk's document and each document's tier, so top_docs can give the nearest documents
of one tier with a few chunks each (the owner's review of the dense path, plan addendum M7).

A database whose vectors are missing, partial (vec_count is not the table's row count) or in another format gives no
index. hybrid asks for the index only when its dense path is on, and without one it ranks by keywords alone and loads
neither model, as it does for vectors another model or revision wrote (main session 11-03): what the log lines below
say. Its rerank-only mode (dense off) reads no vectors.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass

import numpy as np

from nknu.content.embed import VEC_DTYPE

log = logging.getLogger(__name__)

_META = ("built_at", "vec_built_at", "vec_count", "vec_model", "vec_revision", "vec_dim", "vec_dtype")


@dataclass
class VectorIndex:
    ids: np.ndarray  # int64 chunk ids, row by row
    matrix: np.ndarray  # float32 (n, dim), unit rows
    model: str  # meta vec_model, vec_revision: the model that wrote the vectors
    revision: str
    doc_ids: tuple[str, ...] = ()  # the documents, sorted; load_index fills these three
    doc_of: np.ndarray | None = None  # int64 per row: its document's place in doc_ids
    background: np.ndarray | None = None  # bool per document: search.py's background tier (addendum M2)

    def similarities(self, qvec: np.ndarray) -> np.ndarray:
        """Every row's cosine to qvec (one matrix-vector product); ValueError when qvec is not as wide as the index."""
        q = np.asarray(qvec, dtype=np.float32).reshape(-1)
        if q.shape[0] != self.matrix.shape[1]:
            raise ValueError(f"查询向量 {q.shape[0]} 维，索引是 {self.matrix.shape[1]} 维")
        norm = float(np.linalg.norm(q))
        return self.matrix @ (q / norm if norm > 0 else q)

    def top(self, qvec: np.ndarray, k: int) -> list[tuple[int, float]]:
        """The k chunks nearest qvec: (chunk_id, cosine), best first, ties by chunk_id. ValueError when qvec is not
        as wide as the index."""
        n = len(self.ids)
        if k <= 0 or n == 0:
            return []
        sims = self.similarities(qvec)
        pick = np.argpartition(-sims, k - 1)[:k] if k < n else np.arange(n)
        pick = pick[np.lexsort((self.ids[pick], -sims[pick]))]
        return [(int(self.ids[i]), float(sims[i])) for i in pick]

    def top_docs(self, qvec: np.ndarray, per_doc: int, docs: int, allow: np.ndarray | None = None, *,
                 sims: np.ndarray | None = None) -> list[tuple[int, float]]:
        """The best docs documents by their nearest chunk, each with its per_doc nearest chunks (owner 11-08: the dense
        path capped per document as the keyword path is): (chunk_id, cosine), the documents in that order, each one's
        chunks best first, ties by chunk_id. allow: a bool per document (doc_ids' order) keeping it in the running;
        None keeps all. sims: similarities(qvec) when the caller has them (one product for both tiers). [] for an
        index without its documents (doc_of None). ValueError as top."""
        if self.doc_of is None or per_doc <= 0 or docs <= 0 or len(self.ids) == 0:
            return []
        sims = self.similarities(qvec) if sims is None else sims
        rows = np.arange(len(self.ids)) if allow is None else np.flatnonzero(np.asarray(allow)[self.doc_of])
        if rows.size == 0:
            return []
        rows = rows[np.lexsort((self.ids[rows], -sims[rows]))]  # nearest first
        doc = self.doc_of[rows]
        _, first = np.unique(doc, return_index=True)  # each document's first row: its nearest chunk
        chosen = doc[np.sort(first)][:docs]  # the documents by their nearest chunk
        place = np.full(len(self.doc_ids), docs, dtype=np.int64)
        place[chosen] = np.arange(len(chosen))
        p = place[doc]
        rows, p = rows[p < docs], p[p < docs]
        by = np.argsort(p, kind="stable")  # by document, each one's rows still nearest first
        rows, p = rows[by], p[by]
        keep = np.arange(len(p)) - np.searchsorted(p, p, side="left") < per_doc
        return [(int(self.ids[i]), float(sims[i])) for i in rows[keep]]


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    marks = ",".join("?" * len(_META))
    return {r[0]: r[1] for r in conn.execute(f"SELECT key, value FROM meta WHERE key IN ({marks})", _META)}


def load_index(conn: sqlite3.Connection) -> VectorIndex | None:
    """chunk_vecs as an index; None when the table is absent or empty, vec_count is not its row count, or the meta
    does not say which model wrote the vectors, or they are not float16 rows of vec_dim values."""
    if not has_table(conn, "chunk_vecs"):
        return None
    meta = _meta(conn)
    count = conn.execute("SELECT count(*) FROM chunk_vecs").fetchone()[0]
    if not count:
        return None
    if meta.get("vec_count") != str(count) or not meta.get("vec_model") or not meta.get("vec_revision"):
        log.warning("向量不完整或来历不明（vec_count %s，表里 %d 笔）：只用关键词排序", meta.get("vec_count"), count)
        return None
    if meta.get("vec_dtype", "float16") != "float16":
        log.warning("向量格式 %s 认不得：只用关键词排序", meta.get("vec_dtype"))
        return None
    dim = int(meta.get("vec_dim") or 0)
    width = dim * VEC_DTYPE.itemsize
    ids = np.empty(count, dtype=np.int64)
    matrix = np.empty((count, dim), dtype=np.float32)
    rows = 0
    for chunk_id, blob in conn.execute("SELECT chunk_id, vec FROM chunk_vecs ORDER BY chunk_id"):
        if rows >= count or dim <= 0 or len(blob) != width:
            log.warning("向量长度不对（应该是 %d 维）：只用关键词排序", dim)
            return None
        ids[rows] = chunk_id
        matrix[rows] = np.frombuffer(blob, dtype=VEC_DTYPE)
        rows += 1
    if rows != count:
        return None
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.where(norms > 0, norms, 1.0)  # float16 rows are unit length only to about 4e-5
    doc_ids, doc_of, background = _documents(conn, ids)
    return VectorIndex(ids, matrix, meta["vec_model"], meta["vec_revision"], doc_ids, doc_of, background)


def _documents(conn: sqlite3.Connection, ids: np.ndarray) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    """Each row's document (chunks.doc_id; '' for a chunk the table lacks) and each document's tier, for top_docs."""
    from nknu.retrieval.search import background_ids  # the tier's one definition (search.py step 3)

    owner = dict(conn.execute("SELECT chunk_id, doc_id FROM chunks"))
    row_doc = [owner.get(int(c), "") for c in ids]
    doc_ids = tuple(sorted(set(row_doc)))
    at = {d: i for i, d in enumerate(doc_ids)}
    doc_of = np.fromiter((at[d] for d in row_doc), dtype=np.int64, count=len(row_doc))
    found = background_ids(conn, [d for d in doc_ids if d])
    return doc_ids, doc_of, np.array([d in found for d in doc_ids], dtype=bool)


_CACHE: dict[str, tuple[tuple, VectorIndex | None]] = {}  # database file -> (its build key, its index)
_LOCK = threading.Lock()


def _db_file(conn: sqlite3.Connection) -> str:
    return next((r[2] or "" for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")


def index_for(conn: sqlite3.Connection) -> VectorIndex | None:
    """The process's index for conn's database file, loaded on first use, kept per file, and loaded again when the
    file's build changed (meta built_at, vec_built_at, vec_count, vec_model or vec_revision). An in-memory or
    temporary database is loaded every time (no file to key on)."""
    file = _db_file(conn)
    if not file:
        return load_index(conn)
    meta = _meta(conn) if has_table(conn, "meta") else {}
    key = tuple(meta.get(k) for k in ("built_at", "vec_built_at", "vec_count", "vec_model", "vec_revision"))
    with _LOCK:
        got = _CACHE.get(file)
        if got is not None and got[0] == key:
            return got[1]
        index = load_index(conn)
        _CACHE[file] = (key, index)  # replaces the old build's index, so a rebuild frees its memory
        return index
