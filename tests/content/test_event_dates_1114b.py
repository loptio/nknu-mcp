"""Plan 2026-11-14 T3, corrected the same day after the first rebuild: a calendar event's date line (docs.date_line,
「115學年度第1學期 2026-12-21～2026-12-25」) is for the reranker only.

The first rebuild put the line into both texts, the one embedded and the one the reranker reads. Measured on the two
rebuilt databases (main session, 11-14): in the vectors the line dilutes an event's short title (a question that finds
the event 「大學學科能力測驗」 only through the vectors fell from dense rank 33 to 104 on the 教务处 control and from 24
to 448 on the 111 sites), while the reranker gains from it (a question about a deadline rose from rank 3 to 1). So the
embedded text of an event is the one of before T3, to the character, and the vector cache of before serves it; only
chunk_passages / hybrid.rerank_passages carry the line. docs.date_line stays; meta event_dates says rerank. The
titles are made up; no model loads."""

from __future__ import annotations

import sqlite3

import pytest

from nknu.content import embed
from nknu.content.embed import VecCache, chunk_passages, embed_chunks
from nknu.retrieval.hybrid import rerank_passages
from tests.content import test_event_dates_1114 as te
from tests.content import test_multisite_1108 as tm
from tests.content import test_recurring_1114 as tr
from tests.content.test_embed import FakeEmbedder

UNIT, LINES = te.UNIT, te.LINES
# what each event's vector holds: its passage as before T3, the vectors holding no unit line either (11-08)
EMBEDDED = {"es1": "期末考試", "ewk": f"族語課\n{tr.WK_TEXT}", "ewk_20260928": "族語課（停課一次）",
            "emt_20260910": "系務會議", "etu": f"導師會議\n{tr.TU_TEXT}"}


def open_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def vectors_of(path) -> dict[str, bytes]:
    """doc_id -> its vector (every document here has one chunk)."""
    conn = open_ro(path)
    try:
        return dict(conn.execute("SELECT c.doc_id, v.vec FROM chunk_vecs v JOIN chunks c USING (chunk_id)"))
    finally:
        conn.close()


@pytest.fixture
def inputs(tmp_path):
    return [tr.calendar_input(tmp_path / "aca", tr.ACA_SITE, "教务处", tr.ITEMS)], tm.tree_files(tmp_path / "tree")


def test_an_events_vector_is_the_one_of_before_the_line(inputs, tmp_path):
    pairs, paths = inputs
    emb = FakeEmbedder()
    out = tmp_path / "db" / "on.db"
    report = tm.run(paths, out, inputs=pairs, embed=True, embedder=emb)
    assert report.event_dates == {"on": True, "events": 5}
    conn = open_ro(out)
    try:
        read = te.event_passages(conn)
        lines = dict(conn.execute("SELECT doc_id, date_line FROM docs WHERE kind_class = 'event'"))
    finally:
        conn.close()
    assert lines == LINES  # the column is written as before
    # the embedder was given every event without its line, and no text that holds one
    assert set(EMBEDDED.values()) <= set(emb.texts)
    assert not [text for text in emb.texts for line in LINES.values() if line in text]
    # the reranker's passage is the embedded one with the unit line and the date line after the title
    for doc_id, embedded in EMBEDDED.items():
        title, _, rest = embedded.partition("\n")
        assert read[doc_id] == "\n".join(part for part in (title, UNIT, LINES[doc_id], rest) if part), doc_id


def test_the_line_changes_no_vector_and_the_cache_of_before_serves_every_chunk(inputs, tmp_path):
    pairs, paths = inputs
    cache = tmp_path / "cache" / "vec_cache.sqlite"
    old, on = tmp_path / "db" / "old.db", tmp_path / "db" / "on.db"
    tm.run(paths, old, inputs=pairs)
    conn = sqlite3.connect(old)
    try:
        conn.execute("ALTER TABLE docs DROP COLUMN date_line")  # a library of before the line: pipeline-7
        with VecCache(cache) as vc:
            before = embed_chunks(conn, FakeEmbedder(), cache=vc)
    finally:
        conn.close()
    again = FakeEmbedder()
    after = tm.run(paths, on, inputs=pairs, embed=True, embedder=again, vec_cache=cache)
    assert (before.cached, before.encoded) == (0, 6)
    assert (after.vectors["cached"], after.vectors["encoded"]) == (6, 0) and again.texts == []  # nothing new to encode
    assert vectors_of(on) == vectors_of(old) and len(vectors_of(on)) == 6
    conn = open_ro(on)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'vec_passage'").fetchone() == ("1",)  # as before
    finally:
        conn.close()


def test_the_reranker_reads_the_line_and_the_database_says_whose_it_is(inputs, tmp_path):
    pairs, paths = inputs
    out = tmp_path / "db" / "on.db"
    tm.run(paths, out, inputs=pairs, embed=True, embedder=FakeEmbedder())
    conn = open_ro(out)
    try:
        ids = [c for (c,) in conn.execute("SELECT chunk_id FROM chunks")]
        read = rerank_passages(conn, ids)
        assert read == chunk_passages(conn, ids)
        assert sorted(p for p in read.values() if LINES["es1"] in p) == [f"期末考試\n{UNIT}\n{LINES['es1']}"]
        assert sum(any(line in p for line in LINES.values()) for p in read.values()) == 5  # the five events
        assert conn.execute("SELECT value FROM meta WHERE key = 'event_dates'").fetchone() == ("rerank",)
    finally:
        conn.close()


def test_embed_chunks_reads_no_date_line_and_chunk_passages_does(inputs, tmp_path):
    # the two readers of one database, asked directly: the same rows, two texts
    pairs, paths = inputs
    out = tmp_path / "db" / "plain.db"
    tm.run(paths, out, inputs=pairs)
    conn = sqlite3.connect(out)
    try:
        rows = embed._chunk_rows(conn)
        embedded = dict(embed._passages(conn, rows, unit=False, dates=False))
        read = dict(embed._passages(conn, rows))
        emb = FakeEmbedder()
        embed_chunks(conn, emb)
        events = {c for c, in conn.execute("SELECT c.chunk_id FROM chunks c JOIN docs d USING (doc_id)"
                                           " WHERE d.kind_class = 'event'")}
    finally:
        conn.close()
    assert emb.texts == list(dict.fromkeys(embedded.values()))  # what embed_chunks gave the embedder, in order
    assert len(events) == 5 and all(embedded[c] != read[c] for c in events)
    for c, text in read.items():  # the date line is the only difference, the unit line aside
        lines = text.split("\n")
        plain = "\n".join(line for i, line in enumerate(lines)
                          if not (i == 1 and line == UNIT) and not (c in events and i == 2 and line in LINES.values()))
        assert plain == embedded[c], c
