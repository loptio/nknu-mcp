"""T7b (plan 2026-11-02): passages, vector keys, the vector cache and embed_chunks on built test databases, with a
fake embedder (tests/conftest.py's guard stops any real model load here); one real bge-m3 test, marked models."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from nknu.content import embed
from nknu.content.embed import VecCache, chunk_passages, embed_chunks, encode_cached, from_blob, passage, vec_key
from nknu.content.schema import create_schema
from nknu.core import models
from tests.content.test_build import NOTICES, attachment_url, make_inputs, run


class FakeEmbedder:
    """model_id fake/embed, revision "0" * 40, dim 16: a unit vector from a hash of each text; records the texts."""

    def __init__(self, model_id: str = "fake/embed", revision: str = "0" * 40, dim: int = 16) -> None:
        self.model_id, self.revision, self.dim = model_id, revision, dim
        self.texts: list[str] = []
        self.calls = 0

    def vector(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        return v / np.linalg.norm(v)

    def encode(self, texts: list[str], *, batch_size: int = 16) -> np.ndarray:
        texts = list(texts)
        self.calls += 1
        self.texts += texts
        return np.stack([self.vector(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class FailsOnCall(FakeEmbedder):
    """Encodes until its nth call, which raises (an MPS out-of-memory half way through a run)."""

    def __init__(self, n: int) -> None:
        super().__init__()
        self.n = n

    def encode(self, texts: list[str], *, batch_size: int = 16) -> np.ndarray:
        if self.calls + 1 >= self.n:
            self.calls += 1
            raise RuntimeError("MPS backend out of memory")
        return super().encode(texts, batch_size=batch_size)


def built_db(tmp_path: Path, **kw) -> Path:
    """The tiny export of test_build through the real build (the current schema, empty chunk_vecs)."""
    out = tmp_path / "aca.db"
    run(make_inputs(tmp_path, **kw), out)
    return out


def tiny_db(path: Path, docs: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """docs (doc_id, title, text), each one section and one chunk over its whole text, in the current schema."""
    conn = sqlite3.connect(path)
    create_schema(conn)
    for i, (doc_id, title, text) in enumerate(docs, start=1):
        conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars)"
                     " VALUES (?, 'notice', 'newsdetail', ?, ?, '教務處', 1, ?, ?)",
                     (doc_id, title, f"https://c.nknu.edu.tw/aca/{doc_id}", text, len(text)))
        conn.execute("INSERT INTO sections(doc_id, sec_id, heading, start, end) VALUES (?, 0, ?, 0, ?)",
                     (doc_id, text.split("\n", 1)[0][:40], len(text)))
        conn.execute("INSERT INTO chunks(chunk_id, doc_id, sec_id, start, end) VALUES (?, ?, 0, 0, ?)",
                     (i, doc_id, len(text)))
    conn.commit()
    return conn


def meta_of(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM meta WHERE key LIKE 'vec\\_%' ESCAPE '\\'").fetchall())


def vectors_of(conn: sqlite3.Connection) -> dict[int, bytes]:
    return dict(conn.execute("SELECT chunk_id, vec FROM chunk_vecs").fetchall())


FIVE = [(f"dp{i}", f"公告{i}", f"第{i}則公告的內容") for i in range(1, 6)]


# ---------------------------------------------------------------- passages and keys

def test_passage():
    assert passage("a.pdf", "停修公告", "停修申請表 | pdf", "第二條", "| 項目 | 金額 |", "學費 1,000") == \
        "a.pdf\n停修公告\n停修申請表 | pdf\n第二條\n| 項目 | 金額 |\n學費 1,000"
    assert passage("停修", "停修", "", "", "", "x") == "停修\nx"


def test_passage_gives_each_part_once():
    # stripped, the empty parts left out; a link text already given as the title or the parent title, or twice, once
    links = "停修申請表 | 停修申請表.pdf | 停修申請表.pdf"
    assert passage("  停修申請表 ", "", links, "", "", "") == "停修申請表\n停修申請表.pdf"
    assert passage("申請表", "停修公告", "停修公告 | 申請表", "", "", "") == "申請表\n停修公告"
    # the heading is the first line of its section: left out where the chunk (or its table header) starts with it
    assert passage("t", "", "", "第一條 總則", "", "第一條 總則\n本辦法依學則") == "t\n第一條 總則\n本辦法依學則"
    assert passage("t", "", "", "第一章 總則", "", "## **第一章 總則**\n本章說明") == "t\n## **第一章 總則**\n本章說明"
    assert passage("t", "", "", "| 項目 | 金額 |", "| 項目 | 金額 |\n| --- | --- |", "| 學費 | 1,000 |") == \
        "t\n| 項目 | 金額 |\n| --- | --- |\n| 學費 | 1,000 |"
    assert passage("t", "", "", "停修辦法 第二條 停修", "", "第二條 停修以一科為限") == \
        "t\n停修辦法 第二條 停修\n第二條 停修以一科為限"  # a merged heading says more than the chunk: kept
    assert passage("申請停修", "", "", "", "", "申請停修") == "申請停修"  # a calendar event: its text is its title
    assert passage("", "", "", "", "", "") == ""


def test_passage_caps_the_link_texts():
    names = [f"表單{i:03d}.pdf" for i in range(60)]  # 9 characters each, 12 with the separator
    links = passage("t", "", " | ".join(names), "", "", "").split("\n")[1]
    assert len(links) <= embed.LINKTEXT_CHARS == 200 and links.split(" | ") == names[:16]  # whole names only
    assert passage("t", "", "長" * 300, "", "", "").split("\n")[1] == "長" * embed.LINKTEXT_CHARS  # cut


def test_vec_key_includes_the_revision():
    assert vec_key("m", "r1", "t") != vec_key("m", "r2", "t")
    assert vec_key("m", "r1", "t") == hashlib.sha256(b"m@r1\nt").hexdigest()
    assert len({vec_key("m", "r1", "t"), vec_key("n", "r1", "t"), vec_key("m", "r1", "u")}) == 3


# ---------------------------------------------------------------- the vector cache

def test_vec_cache_round_trip(tmp_path):
    path = tmp_path / "cache" / "vec_cache.sqlite"
    cache = VecCache(path)
    items = {f"k{i}": bytes([i % 256]) * 4 for i in range(1_200)}  # more keys than one IN (...) lookup takes
    cache.put(items)
    assert cache.get(list(items)) == items and cache.get(["k1", "missing"]) == {"k1": items["k1"]}
    cache.close()
    with VecCache(path) as again:  # committed: a new process finds them
        assert again.get(["k7", "k1100"]) == {"k7": items["k7"], "k1100": items["k1100"]}
    # a 2 KB vector row fits once in a 4 KB page (twice the vectors' size on disk), 7 times in a 16 KB one
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA page_size").fetchone() == (embed.PAGE_SIZE,) == (16_384,)


def test_encode_cached(tmp_path):
    emb = FakeEmbedder()
    with VecCache(tmp_path / "c.sqlite") as cache:
        blobs, cached, encoded = encode_cached(emb, ["甲", "乙", "甲"], cache=cache)
        assert (cached, encoded, emb.texts) == (0, 2, ["甲", "乙"])  # the same text is encoded once
        assert blobs[0] == blobs[2] and np.allclose(from_blob(blobs[1]), emb.vector("乙"), atol=1e-3)
        assert from_blob(blobs[0]).dtype == np.float32 and len(blobs[0]) == 2 * emb.dim
        again, cached, encoded = encode_cached(emb, ["乙", "丙"], cache=cache)
        assert (cached, encoded, emb.texts[2:], again[0]) == (1, 1, ["丙"], blobs[1])
    assert encode_cached(emb, [], cache=None) == ([], 0, 0)


# ---------------------------------------------------------------- embed_chunks

def test_embed_chunks_writes_one_vector_per_chunk(tmp_path):
    conn = sqlite3.connect(built_db(tmp_path))
    emb = FakeEmbedder()
    stats = embed_chunks(conn, emb)
    n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert n >= 27 and conn.execute("SELECT count(*) FROM chunk_vecs").fetchone()[0] == n  # 27 documents
    assert conn.execute("SELECT count(*) FROM chunks JOIN chunk_vecs USING (chunk_id)").fetchone()[0] == n
    assert {length for (length,) in conn.execute("SELECT length(vec) FROM chunk_vecs")} == {32}
    meta = meta_of(conn)
    # 11-08 (main session): the vectors hold the passages without their unit lines unless asked (unit=True), so
    # vec_passage says version 1, the text they hold
    assert meta == {"vec_model": "fake/embed", "vec_revision": "0" * 40, "vec_dim": "16", "vec_dtype": "float16",
                    "vec_count": str(n), "vec_passage": "1", "vec_built_at": meta["vec_built_at"]}
    assert meta["vec_built_at"].startswith("20") and meta["vec_built_at"].endswith("+08:00")
    assert (stats.chunks, stats.cached, stats.model, stats.revision) == (n, 0, "fake/embed", "0" * 40)
    assert stats.encoded == len(emb.texts) == len(set(emb.texts)) and stats.seconds >= 0
    conn.close()


def test_each_vector_is_its_chunks_passage(tmp_path):
    header = "| 項目 | 金額 |\n| --- | --- |"
    text = header + "\n" + "\n".join(f"| 學費第{i}期 | {i},000 元 |" for i in range(1, 80))
    table = ("a61", "附件:pdf:有文字层", attachment_url("a61", "收費表.pdf"), "收費表.pdf", text, ["p32"])
    conn = sqlite3.connect(built_db(tmp_path, attachments=[table]))
    emb = FakeEmbedder()
    embed_chunks(conn, emb, unit=True)  # the vectors as the owner's patch reads (--unit-words-in-vectors)
    vecs = vectors_of(conn)
    every = chunk_passages(conn, list(vecs))
    # plan 2026-11-14 T3, corrected the same day: a calendar event's date line is the reranker's only, so the two
    # events' vectors are their passages without that line; every other chunk's vector is its passage
    dated = dict(conn.execute("SELECT c.chunk_id, d.date_line FROM chunks c JOIN docs d USING (doc_id)"
                              " WHERE d.date_line != ''"))
    embedded = {c: p.replace("\n" + dated[c], "", 1) if c in dated else p for c, p in every.items()}
    assert len(dated) == 2 and all(dated[c] in every[c] for c in dated)
    assert set(every) == set(vecs) and all(np.allclose(from_blob(vecs[c]), emb.vector(p), atol=1e-3)
                                           for c, p in embedded.items())
    assert conn.execute("SELECT value FROM meta WHERE key = 'vec_passage'").fetchone() == (embed.PASSAGE_VERSION,)
    ids = [c for (c,) in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY chunk_id", ("da3",))]
    # plan 2026-11-08 T4 (deliberate, the owner's unit patch): the unit line is every passage's second line; both
    # files hang on 和平教務組's pages (its other name from the made-up unit config)
    unit = "單位：教務處 和平教務組（和平校區教務組）"
    assert len(ids) == 1 and every[ids[0]] == f"115-1註冊須知掃描.pdf\n{unit}\n表單下載"  # no text: its titles alone
    rows = conn.execute("SELECT c.chunk_id, c.start, c.end FROM chunks c WHERE c.doc_id = 'da61' ORDER BY chunk_id")
    (first, *_), (cid, start, end) = rows.fetchmany(2)
    stored = conn.execute("SELECT text FROM docs WHERE doc_id = 'da61'").fetchone()[0]
    # under the table: the file name, its unit, its notice, the table's header (the heading, its first row, given
    # once), rows
    assert every[cid] == "\n".join(("收費表.pdf", unit, NOTICES[0][3], header, stored[start:end]))
    assert every[first].startswith(f"收費表.pdf\n{unit}\n{NOTICES[0][3]}\n{header}\n| 學費第1期")
    conn.close()


def test_cache_makes_a_rerun_free(tmp_path):
    conn = sqlite3.connect(built_db(tmp_path))
    path = tmp_path / "vec_cache.sqlite"
    with VecCache(path) as cache:
        first = embed_chunks(conn, FakeEmbedder(), cache=cache)
        before = vectors_of(conn)
        emb = FakeEmbedder()
        second = embed_chunks(conn, emb, cache=cache)
    assert first.encoded > 0 and first.cached < first.chunks
    assert (second.encoded, second.cached, second.chunks, emb.calls) == (0, first.chunks, first.chunks, 0)
    assert vectors_of(conn) == before and meta_of(conn)["vec_count"] == str(first.chunks)
    with VecCache(path) as reopened:  # another process, another day: still free
        assert embed_chunks(conn, FakeEmbedder(), cache=reopened).encoded == 0
        other = embed_chunks(conn, FakeEmbedder(revision="1" * 40), cache=reopened)
    assert other.encoded == first.encoded and meta_of(conn)["vec_revision"] == "1" * 40  # another revision: new
    conn.close()


def test_identical_passages_are_encoded_once(tmp_path):
    conn = tiny_db(tmp_path / "t.db", [("dp1", "停修公告", "停修申請"), ("dp2", "停修公告", "停修申請")])
    emb = FakeEmbedder()
    stats = embed_chunks(conn, emb)
    vecs = vectors_of(conn)
    assert (stats.chunks, stats.encoded, emb.texts) == (2, 1, ["停修公告\n停修申請"]) and vecs[1] == vecs[2]
    conn.close()


def test_a_failed_round_keeps_the_finished_rounds_in_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "EMBED_CHUNK", 2)  # rounds of two passages
    conn = tiny_db(tmp_path / "t.db", FIVE)
    with VecCache(tmp_path / "c.sqlite") as cache:
        with pytest.raises(RuntimeError, match="out of memory"):
            embed_chunks(conn, FailsOnCall(2), cache=cache)
        assert vectors_of(conn) == {} and meta_of(conn) == {}  # the database is as it was
        emb = FakeEmbedder()
        stats = embed_chunks(conn, emb, cache=cache)
    assert (stats.cached, stats.encoded, len(vectors_of(conn))) == (2, 3, 5)  # the first round was kept
    conn.close()


def test_a_failed_run_keeps_the_vectors_it_replaces(tmp_path):
    conn = tiny_db(tmp_path / "t.db", FIVE)
    embed_chunks(conn, FakeEmbedder())
    before, meta = vectors_of(conn), meta_of(conn)
    with pytest.raises(RuntimeError):
        embed_chunks(conn, FailsOnCall(1))
    assert vectors_of(conn) == before and meta_of(conn) == meta
    conn.close()


def test_wrong_shape_or_values_are_errors(tmp_path):
    conn = tiny_db(tmp_path / "t.db", FIVE)

    class Wide(FakeEmbedder):
        def encode(self, texts, *, batch_size=16):
            return np.ones((len(texts), self.dim + 1), dtype=np.float32)

    class Huge(FakeEmbedder):
        def encode(self, texts, *, batch_size=16):
            return np.full((len(texts), self.dim), 1e6, dtype=np.float32)  # inf in float16

    for bad in (Wide(), Huge()):
        with pytest.raises(RuntimeError):
            embed_chunks(conn, bad)
        assert vectors_of(conn) == {}
    conn.close()


def test_a_cache_entry_of_the_wrong_size_is_encoded_again(tmp_path):
    conn = tiny_db(tmp_path / "t.db", FIVE[:2])
    emb = FakeEmbedder()
    texts = list(chunk_passages(conn, [1, 2]).values())
    with VecCache(tmp_path / "c.sqlite") as cache:
        cache.put({vec_key(emb.model_id, emb.revision, texts[0]): b"\x00" * 4})
        stats = embed_chunks(conn, emb, cache=cache)
        assert (stats.cached, stats.encoded, emb.texts) == (0, 2, texts)
        assert len(cache.get([vec_key(emb.model_id, emb.revision, texts[0])]).popitem()[1]) == 32
    conn.close()


def test_embedding_needs_the_pipeline_schema(tmp_path):
    conn = tiny_db(tmp_path / "t.db", FIVE[:1])
    conn.execute("DROP TABLE chunk_vecs")  # a database built before plan 2026-11-02 (skeleton-2)
    with pytest.raises(ValueError, match="chunk_vecs"):
        embed_chunks(conn, FakeEmbedder())
    conn.close()


@pytest.mark.models
def test_real_bge_m3_vectors(tmp_path):
    pytest.importorskip("sentence_transformers")
    if not models.is_cached(models.EMBED_ID, models.EMBED_REVISION):
        pytest.skip("bge-m3 不在本机快取")
    conn = tiny_db(tmp_path / "t.db", [("dp1", "115學年度第1學期停修公告", "停修申請自11月3日起至11月16日止"),
                                       ("dp2", "學生證遺失補辦", "學生證遺失請至教務處申請補發")])
    stats = embed_chunks(conn, models.load_embedder())
    vecs = np.stack([from_blob(v) for _, v in sorted(vectors_of(conn).items())])
    assert stats.chunks == 2 and vecs.shape == (2, models.EMBED_DIM)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1, atol=1e-2)
    assert float(vecs[0] @ vecs[1]) < 0.95  # two different notices, two different vectors
    assert (meta_of(conn)["vec_model"], meta_of(conn)["vec_revision"]) == (models.EMBED_ID, models.EMBED_REVISION)
    conn.close()
