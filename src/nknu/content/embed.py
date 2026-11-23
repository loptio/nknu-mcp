"""Vectors in the content database (plan 2026-11-02 T7b): one bge-m3 vector per chunk, in chunk_vecs, for hybrid
retrieval's dense path (T8). A chunk's passage is its document's title, its unit line (plan 2026-11-08 T4, the owner's
unit patch: docs.unit_line, 單位：<unit>（<short names>） <組別>), a calendar event's date line (plan 2026-11-14 T3:
docs.date_line, 115學年度第2學期 2027-02-15～2027-02-18), parent title and link texts, its section heading, the
header of the table it starts inside and the chunk itself: what the reranker reads at query time (T8 builds its
passages with the same passage()) and, without the date line and without the unit line, what bge-m3 embeds
here. A database without docs.unit_line (built before T4) gets the passages of then, unit line aside, so its vectors
and its reranking stay as they were. Scores only: no text is generated.

The date line is the reranker's only (plan 2026-11-14 T3, direction 2026-11-13 第 3 件; corrected the same day after
the first rebuild). The build writes it once into docs.date_line (documents.event_line); chunk_passages, what the
reranker reads at query time, puts it after the unit line, and embed_chunks never embeds it: an event's embedded text
is to the character the one of before T3, so the vector cache of before serves it and no vector is computed again
for the line (the one document of a merged recurring event, plan 2026-11-14 T2, has a text of its own and is embedded
once, like any new text).
The first rebuild of 11-14 had it in both texts, and the main session measured both databases: in the vectors the line
dilutes an event's short title (a question that finds the event 「大學學科能力測驗」 only through the vectors fell
from dense rank 33 to 104 on the 教务处 control and from 24 to 448 on the 111 sites), while the reranker gains from it
(a question about a deadline rose from rank 3 to 1). A database without the column (pipeline-7 and before) gives
the reranker the passages of before. PASSAGE_VERSION stays "2": no embedded passage changed.

Vectors are float16, little-endian, dim × 2 bytes. A cache file keyed by model, revision and passage text
(vec_key) makes a rebuild encode only the passages that changed; it is committed after every round, so a run that
fails half way (MPS out of memory) keeps the rounds it finished, while the database keeps the vectors it had.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from pathlib import Path
from typing import Self

import numpy as np

from nknu.content.documents import LINK_SEP
from nknu.content.numbering import strip_decor
from nknu.core.models import BASE_SEQ, BATCH
from nknu.core.timeutil import TAIPEI

# What passage() writes for the reranker; changing passage() means a new version. "2" (plan 2026-11-08 T4): the unit
# line, second ("1" before). The embedded text has no unit line: it is version "1"'s, which meta vec_passage says
PASSAGE_VERSION = "2"
LINKTEXT_CHARS = 200  # link texts kept in a passage, whole names only (one longer name is cut)
EMBED_CHUNK = 512  # passages looked up, encoded, cached and written per round
PROGRESS_EVERY = 5_000  # chunks between two progress lines
VEC_DTYPE = np.dtype("<f2")  # what chunk_vecs.vec and the cache hold: meta vec_dtype "float16"
# SQLite page size of a new file that holds vectors (the cache; the build's database): a 2 KB row of a 1024-wide
# float16 vector fits once in the default 4 KB page, twice its size on disk (measured 11-02: 2.00x), 7 times in 16 KB
PAGE_SIZE = 16_384
_IN_MAX = 500  # keys or ids per IN (...) query, well under SQLite's variable limit
_CHUNKS = ("SELECT c.chunk_id, c.doc_id, c.start, c.end, c.head_start, c.head_end, coalesce(s.heading, '')"
           " FROM chunks c LEFT JOIN sections s ON s.doc_id = c.doc_id AND s.sec_id = c.sec_id")
# unit_line, or '' before plan 2026-11-08 T4 and for the vectors; date_line, or '' before plan 2026-11-14 T3 and for
# the vectors (_passages)
_DOC = "SELECT title, parent_title, linktext, text, {unit}, {dates} FROM docs WHERE doc_id = ?"

log = logging.getLogger("nknu.content.embed")


def _opens_with(text: str, heading: str) -> bool:
    """text's first line with text starts with heading, as written or without its Markdown decoration (the heading is
    that line cleaned by strip_decor, cut to HEADING_CHARS)."""
    first = next((line.strip() for line in text.split("\n") if line.strip()), "")
    return first.startswith(heading) or strip_decor(first)[0].startswith(heading)


def _links(linktext: str, given: list[str]) -> str:
    """The link texts not already given (as the title, the parent title or an earlier link), whole names joined by
    LINK_SEP while they fit LINKTEXT_CHARS; a first name longer than that is cut."""
    kept: list[str] = []
    size = 0
    for name in (part.strip() for part in linktext.split(LINK_SEP)):
        if not name or name in given or name in kept:
            continue
        need = len(name) + (len(LINK_SEP) if kept else 0)
        if size + need > LINKTEXT_CHARS:
            if not kept:
                kept.append(name[:LINKTEXT_CHARS].rstrip())
            break
        kept.append(name)
        size += need
    return LINK_SEP.join(kept)


def passage(title: str, parent_title: str, linktext: str, heading: str, header: str, body: str, *,
            unit: str = "", dates: str = "") -> str:
    """The text bge-m3 embeds and the reranker reads for one chunk: the parts stripped, the empty ones left out, one per
    line in this order, each once: the title, the unit line (plan 2026-11-08 T4: docs.unit_line, the second line; ''
    in a database built before), a calendar event's date line (plan 2026-11-14 T3: docs.date_line, after the unit line
    so that line stays the second 【Claude】; given for the reranker's text only, never for the embedded one:
    _passages; '' for any other document and in a database without it), the parent title, the link texts, the
    heading, the table header, the chunk. Once: a part equal to an earlier one is left out; so is a link text already
    given as the title, the parent title or an earlier link (the export joins link texts with LINK_SEP), and the
    heading when the table header or the chunk opens with it (a section's first chunk: its heading is its first
    line)."""
    parts: list[str] = []

    def add(part: str) -> None:
        part = part.strip()
        if part and part not in parts:
            parts.append(part)

    add(title)
    add(unit)
    add(dates)
    add(parent_title)
    add(_links(linktext, parts))
    heading = heading.strip()
    if heading and not any(_opens_with(p, heading) for p in (header, body)):
        add(heading)
    add(header)
    add(body)
    return "\n".join(parts)


# spec 2026-11-16 §三 【Claude】: a text of this many characters or fewer is inside BASE_SEQ tokens whatever the window
# (the tokenizer gives a character one piece at most, and one more at the start of a run), so the vector an embedder of
# a wider window computes for it is the one of before, and its cache key stays; only a longer text's key names the
# window. A rebuild after the window widened so encodes the long passages alone.
SAFE_CHARS = 500


def vec_key(model_id: str, revision: str, text: str, window: int = BASE_SEQ) -> str:
    """The cache key of text's vector under one model revision and the tokens the embedder reads (window: BASE_SEQ,
    512, is the key of every build before spec 2026-11-16 §三; another window is part of the key of a text longer than
    SAFE_CHARS only)."""
    model = f"{model_id}@{revision}" if window == BASE_SEQ or len(text) <= SAFE_CHARS else f"{model_id}@{revision}@{window}"
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=VEC_DTYPE).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """A stored vector as float32 (unit length within float16's precision)."""
    return np.frombuffer(blob, dtype=VEC_DTYPE).astype(np.float32)


class VecCache:
    """Vectors computed before, in their own SQLite file: vecs(key = vec_key(...), vec = float16 bytes). put commits,
    so a run that fails keeps every round it finished."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(f"PRAGMA page_size={PAGE_SIZE}")  # a new file only; an existing one keeps its own
        self._conn.execute("CREATE TABLE IF NOT EXISTS vecs (key TEXT PRIMARY KEY, vec BLOB NOT NULL)")
        self._conn.commit()

    def get(self, keys: list[str]) -> dict[str, bytes]:
        """The cached ones among keys."""
        keys = list(dict.fromkeys(keys))
        out: dict[str, bytes] = {}
        for i in range(0, len(keys), _IN_MAX):
            part = keys[i : i + _IN_MAX]
            out.update(self._conn.execute(f"SELECT key, vec FROM vecs WHERE key IN ({','.join('?' * len(part))})",
                                          part).fetchall())
        return out

    def put(self, items: dict[str, bytes]) -> None:
        self._conn.executemany("INSERT OR REPLACE INTO vecs(key, vec) VALUES (?, ?)", items.items())
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


@dataclass
class EmbedStats:
    chunks: int  # rows written to chunk_vecs: one per chunk
    cached: int  # chunks whose vector came from the cache
    encoded: int  # passages encoded in this run: one per distinct passage of a round
    seconds: float
    model: str
    revision: str


def encode_cached(embedder, texts: list[str], *, cache: VecCache | None = None,
                  batch_size: int = BATCH) -> tuple[list[bytes], int, int]:
    """(float16 bytes of each text's vector, texts found in the cache, texts encoded). embedder: model_id, revision,
    dim and encode(texts, *, batch_size) -> (n, dim) unit vectors (models.Embedder or a fake). One encode call for the
    distinct texts the cache lacks (a cached entry of the wrong size counts as lacking), and their vectors go into the
    cache. RuntimeError when the embedder returns another shape or a value float16 cannot hold."""
    dim = int(embedder.dim)
    window = int(getattr(embedder, "max_seq", BASE_SEQ))  # spec 2026-11-16 §三; an embedder of before: 512
    keys = [vec_key(embedder.model_id, embedder.revision, t, window) for t in texts]
    found = cache.get(keys) if cache is not None and keys else {}
    have = {k: v for k, v in found.items() if len(v) == dim * VEC_DTYPE.itemsize}
    cached = sum(k in have for k in keys)
    todo = {k: t for k, t in zip(keys, texts) if k not in have}
    if todo:
        vecs = np.asarray(embedder.encode(list(todo.values()), batch_size=batch_size), dtype=np.float32)
        if vecs.shape != (len(todo), dim):
            raise RuntimeError(f"{embedder.model_id} 给了形状 {vecs.shape} 的向量，应该是 {(len(todo), dim)}")
        with np.errstate(over="ignore"):  # a value float16 cannot hold becomes inf, and is refused just below
            half = vecs.astype(VEC_DTYPE)
        if not np.isfinite(half).all():
            raise RuntimeError(f"{embedder.model_id} 算出了 NaN、无穷大或 float16 存不下的值（推理出错）")
        new = {k: row.tobytes() for k, row in zip(todo, half)}
        if cache is not None:
            cache.put(new)
        have.update(new)
    return [have[k] for k in keys], cached, len(todo)


def _chunk_rows(conn: sqlite3.Connection, chunk_ids: list[int] | None = None) -> list[tuple]:
    """(chunk_id, doc_id, start, end, head_start, head_end, heading) by doc_id, then chunk_id; every chunk, or those."""
    if chunk_ids is None:
        return conn.execute(_CHUNKS + " ORDER BY c.doc_id, c.chunk_id").fetchall()
    ids = list(dict.fromkeys(int(c) for c in chunk_ids))
    rows: list[tuple] = []
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        rows += conn.execute(f"{_CHUNKS} WHERE c.chunk_id IN ({','.join('?' * len(part))})", part).fetchall()
    return sorted(rows, key=lambda r: (r[1], r[0]))


def _doc_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(docs)")}


def _passages(conn: sqlite3.Connection, rows: list[tuple], *, unit: bool = True,
              dates: bool = True) -> Iterator[tuple[int, str]]:
    """(chunk_id, passage) for rows sorted by document; each document's title, unit line (none in a database built
    before plan 2026-11-08 T4, and none when unit is False), date line (docs.date_line, an event's: plan 2026-11-14
    T3; none in a database built before it, and none when dates is False: the embedded text, which never holds it),
    parent title, link text and text are read once. The defaults give the reranker's text (chunk_passages)."""
    columns = _doc_columns(conn)
    sql = _DOC.format(unit="unit_line" if unit and "unit_line" in columns else "''",
                      dates="date_line" if dates and "date_line" in columns else "''")
    doc_id, doc = None, None
    for chunk_id, did, start, end, head_start, head_end, heading in rows:
        if did != doc_id:
            doc_id, doc = did, conn.execute(sql, (did,)).fetchone()
            if doc is None:
                raise ValueError(f"小段 {chunk_id} 的文件 {did} 不在 docs 表里")
        title, parent_title, linktext, text, unit_line, date_line = doc
        header = text[head_start:head_end] if head_start is not None and head_end is not None else ""
        yield chunk_id, passage(title, parent_title, linktext, heading, header, text[start:end], unit=unit_line,
                                dates=date_line)


def chunk_passages(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, str]:
    """chunk_id -> the passage the reranker reads (pipeline-1 databases: parent_title, head offsets; pipeline-4: the
    unit line, always; pipeline-8: an event's date line, plan 2026-11-14 T3). embed_chunks embedded the same text
    without the date line (the reranker's only: the module docstring) and without the unit line (11-08, main session:
    see embed_chunks)."""
    return dict(_passages(conn, _chunk_rows(conn, chunk_ids)))


def embed_chunks(conn: sqlite3.Connection, embedder, *, cache: VecCache | None = None,
                 batch_size: int = BATCH) -> EmbedStats:
    """One vector per chunk in chunk_vecs (chunks without text too: their passage is their document's titles), and the
    meta keys vec_model, vec_revision, vec_dim, vec_dtype, vec_count, vec_built_at, vec_max_seq and vec_passage ("1":
    the embedded passages are version 1's, without the unit line), replacing what was there in one
    transaction: a failure leaves the database as it was (conn in Python's default transaction mode), while the cache
    keeps every finished round. Passages go to the embedder in rounds of EMBED_CHUNK, batch_size at a time; progress
    is logged every PROGRESS_EVERY chunks.
    The unit line is not embedded (the lab-PC measurement of 11-08, main session 【Claude】, evidence in
    research/pipeline_2026-11-02/from_lab_pc_1108/e2e/): in the vectors the line makes every document of one 組別 near
    a query naming it, so on 教务处 the hybrid's question MRR fell 0.615 -> 0.563 (N11 rank 1 -> 54) while the reranker
    reading the line gained. The keyword index and the reranker have it.
    A calendar event's date line (docs.date_line; plan 2026-11-14 T3) is not embedded either: the measurement of
    11-14 on the first rebuild went the same way (the module docstring), so every passage embedded here is the one of
    before T3 and a cache of before serves it."""
    started = time.monotonic()
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "chunk_vecs" not in tables:
        raise ValueError("这个资料库没有 chunk_vecs 表（pipeline-1 以前建的）：先用 nknu.content.build 重建")
    rows = _chunk_rows(conn)
    done = cached = encoded = 0
    try:
        conn.execute("DELETE FROM chunk_vecs")
        for part in batched(_passages(conn, rows, unit=False, dates=False), EMBED_CHUNK):
            blobs, hit, new = encode_cached(embedder, [text for _, text in part], cache=cache, batch_size=batch_size)
            conn.executemany("INSERT INTO chunk_vecs(chunk_id, vec) VALUES (?, ?)",
                             zip((chunk_id for chunk_id, _ in part), blobs))
            before, done = done, done + len(part)
            cached, encoded = cached + hit, encoded + new
            if done // PROGRESS_EVERY > before // PROGRESS_EVERY or done == len(rows):
                log.info("向量：%d/%d 小段（快取 %d、新算 %d），用时 %.0f 秒", done, len(rows), cached, encoded,
                         time.monotonic() - started)
        meta = {"vec_model": embedder.model_id, "vec_revision": embedder.revision, "vec_dim": str(int(embedder.dim)),
                "vec_dtype": "float16", "vec_count": str(done),
                "vec_built_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                "vec_passage": "1",
                # spec 2026-11-16 §三: the tokens the embedder read of each passage (512 in every build before)
                "vec_max_seq": str(int(getattr(embedder, "max_seq", BASE_SEQ)))}
        conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return EmbedStats(chunks=done, cached=cached, encoded=encoded, seconds=round(time.monotonic() - started, 2),
                      model=embedder.model_id, revision=embedder.revision)
