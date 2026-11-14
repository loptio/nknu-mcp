"""Plan 2026-11-14 T3 (direction 2026-11-13 第 3 件): a calendar event's passages carry one line with its term and its
days, 「115學年度第2學期 2027-02-15～2027-02-18」, so the reranker can tell two years' events of one name apart; until
now it read the title alone.

The build writes the line to docs.date_line (documents.event_line: the term as the school writes it, one day or the
first and the last, a merged recurring series with how many times it occurs); embed.passage puts it after the unit
line in the text the reranker reads at query time. Corrected the same day (test_event_dates_1114b.py has the
measurement and the rest of the tests): the text embedded at build time never holds the line, so an event's vector is
the one of before. The keyword index does not change. A database without the column (built before) and one built with
event_dates=False / --no-event-dates are read as before. The titles are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from nknu.content import build
from nknu.content.documents import event_line
from nknu.content.embed import chunk_passages, passage
from nknu.content.terms import Term
from nknu.retrieval.hybrid import rerank_passages
from tests.content import test_multisite_1108 as tm
from tests.content import test_recurring_1114 as tr
from tests.content.test_embed import FakeEmbedder

UNIT = "單位：教務處"
LINES = {"es1": "115學年度第1學期 2026-12-21～2026-12-25",
         "ewk": "115學年度第1學期 2026-09-07～2026-09-22（共 3 次）",
         "ewk_20260928": "115學年度第1學期 2026-09-28",
         "emt_20260910": "115學年度第1學期 2026-09-10",
         "etu": "115學年度第1學期～115學年度第2學期 2026-12-15～2027-03-16（共 2 次）"}


# ---------------------------------------------------------------- the line

def test_the_line_says_the_term_and_the_days():
    assert event_line([Term(115, 2)], date(2027, 2, 15), date(2027, 2, 18)) == "115學年度第2學期 2027-02-15～2027-02-18"
    assert event_line([Term(115, 1)], date(2026, 11, 3), date(2026, 11, 3)) == "115學年度第1學期 2026-11-03"  # one day
    assert event_line([Term(115, 1)], date(2026, 12, 30), date(2027, 1, 2)) == "115學年度第1學期 2026-12-30～2027-01-02"


def test_a_series_line_says_its_first_and_last_day_and_how_many_times():
    first, last = date(2026, 9, 7), date(2026, 9, 22)
    assert event_line([Term(115, 1)], first, last, 3) == "115學年度第1學期 2026-09-07～2026-09-22（共 3 次）"
    assert event_line([Term(115, 1)], first, first, 2) == "115學年度第1學期 2026-09-07（共 2 次）"  # two on one day
    assert event_line([Term(115, 1)], first, last, 1) == "115學年度第1學期 2026-09-07～2026-09-22"
    # instances in several terms: the first term and the last, whatever order they come in
    terms = [Term(114, 2), Term(115, 2), Term(113, 1), Term(115, 1), Term(114, 2)]
    assert event_line(terms, date(2024, 9, 2), date(2027, 3, 1), 90) == \
        "113學年度第1學期～115學年度第2學期 2024-09-02～2027-03-01（共 90 次）"
    assert event_line([], first, last) == "2026-09-07～2026-09-22"  # no term known: the days alone


def test_passage_puts_the_line_after_the_unit_line():
    line = LINES["es1"]
    assert passage("期末考試", "", "", "", "", "期末考試", unit=UNIT, dates=line) == f"期末考試\n{UNIT}\n{line}"
    assert passage("期末考試", "", "", "", "", "期末考試", dates=line) == f"期末考試\n{line}"  # a document with no unit line
    assert passage("期末考試", "", "", "", "", "期末考試", unit=UNIT, dates="") == f"期末考試\n{UNIT}"  # as before
    assert passage("a.pdf", "停修公告", "申請表", "第二條", "", "學費", unit=UNIT) == \
        passage("a.pdf", "停修公告", "申請表", "第二條", "", "學費", unit=UNIT, dates="  ")


# ---------------------------------------------------------------- the build

def open_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def event_passages(conn: sqlite3.Connection) -> dict[str, str]:
    """doc_id -> the passage of its first chunk, for the events."""
    first = dict(conn.execute("SELECT c.doc_id, min(c.chunk_id) FROM chunks c JOIN docs d USING (doc_id)"
                              " WHERE d.kind_class = 'event' GROUP BY c.doc_id"))
    texts = chunk_passages(conn, list(first.values()))
    return {doc_id: texts[chunk] for doc_id, chunk in first.items()}


@pytest.fixture
def built(tmp_path):
    inputs = [tr.calendar_input(tmp_path / "aca", tr.ACA_SITE, "教务处", tr.ITEMS)]
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "db" / "on.db"
    report = tm.run(paths, out, inputs=inputs)
    conn = open_ro(out)
    yield report, conn, inputs, paths
    conn.close()


def test_the_build_writes_each_events_line(built):
    report, conn, _, _ = built
    lines = dict(conn.execute("SELECT doc_id, date_line FROM docs WHERE kind_class = 'event'"))
    assert lines == LINES
    assert conn.execute("SELECT DISTINCT date_line FROM docs WHERE kind_class != 'event'").fetchall() == [("",)]
    # changed 11-14 on purpose (was "on", which the first rebuild's two databases say: their vectors hold the line too)
    assert conn.execute("SELECT value FROM meta WHERE key = 'event_dates'").fetchone() == ("rerank",)
    assert report.event_dates == {"on": True, "events": 5}


def test_the_reranker_reads_the_line_after_the_unit_line(built):
    _, conn, _, _ = built
    got = event_passages(conn)
    assert got["es1"] == f"期末考試\n{UNIT}\n{LINES['es1']}"
    assert got["ewk_20260928"] == f"族語課（停課一次）\n{UNIT}\n{LINES['ewk_20260928']}"
    # a series: its text lists every day; the line gives the term, the first and the last day and the count
    assert got["ewk"] == f"族語課\n{UNIT}\n{LINES['ewk']}\n{tr.WK_TEXT}"
    assert got["etu"] == f"導師會議\n{UNIT}\n{LINES['etu']}\n{tr.TU_TEXT}"
    ids = [c for (c,) in conn.execute("SELECT chunk_id FROM chunks")]
    assert rerank_passages(conn, ids) == chunk_passages(conn, ids)  # what hybrid hands the reranker
    page = conn.execute("SELECT c.chunk_id FROM chunks c JOIN docs d USING (doc_id) WHERE d.kind_class = 'page'").fetchone()
    assert chunk_passages(conn, [page[0]])[page[0]] == f"教务处辦公時間\n{UNIT}\n上午八時至下午五時"  # no line of its own


def test_the_vectors_do_not_embed_the_line(built, tmp_path):
    # was test_the_vectors_embed_the_same_line; changed 11-14 on purpose (the correction: the line is the reranker's)
    _, _, inputs, paths = built
    emb = FakeEmbedder()
    out = tmp_path / "db" / "vec.db"
    tm.run(paths, out, inputs=inputs, embed=True, embedder=emb)
    conn = open_ro(out)
    try:
        read = event_passages(conn)
    finally:
        conn.close()
    # the vectors hold each passage without its unit line (11-08) and without its date line
    plain = {doc_id: p.replace(f"\n{UNIT}", "", 1).replace(f"\n{LINES[doc_id]}", "", 1) for doc_id, p in read.items()}
    assert set(plain.values()) <= set(emb.texts)
    assert "期末考試" in emb.texts and f"期末考試\n{LINES['es1']}" not in emb.texts


def test_the_keyword_index_and_the_texts_do_not_change(built, tmp_path):
    _, conn, inputs, paths = built
    out = tmp_path / "db" / "off.db"
    off = tm.run(paths, out, inputs=inputs, event_dates=False)
    plain = open_ro(out)
    docs = "SELECT doc_id, title, text, linktext, unit_line, period_ay, period_sem, expires, valid_ay FROM docs ORDER BY doc_id"
    try:
        for sql in ("SELECT rowid, title_tokens, body_tokens, unit_tokens FROM chunk_fts ORDER BY rowid", docs,
                    "SELECT * FROM chunks ORDER BY chunk_id", "SELECT * FROM dates ORDER BY rowid"):
            assert conn.execute(sql).fetchall() == plain.execute(sql).fetchall(), sql
        # without the line: every passage as before, and the database says so
        assert plain.execute("SELECT DISTINCT date_line FROM docs").fetchall() == [("",)]
        assert plain.execute("SELECT value FROM meta WHERE key = 'event_dates'").fetchone() == ("off",)
        assert event_passages(plain) == {"es1": f"期末考試\n{UNIT}", "ewk": f"族語課\n{UNIT}\n{tr.WK_TEXT}",
                                         "ewk_20260928": f"族語課（停課一次）\n{UNIT}", "emt_20260910": f"系務會議\n{UNIT}",
                                         "etu": f"導師會議\n{UNIT}\n{tr.TU_TEXT}"}
    finally:
        plain.close()
    assert off.event_dates == {"on": False, "events": 0}


def test_a_database_built_before_is_read_as_before(built, tmp_path):
    _, _, inputs, paths = built
    out = tmp_path / "db" / "old.db"
    tm.run(paths, out, inputs=inputs)
    conn = sqlite3.connect(out)
    try:
        conn.execute("ALTER TABLE docs DROP COLUMN date_line")  # pipeline-7 and before: no such column
        conn.execute("DELETE FROM meta WHERE key = 'event_dates'")
        got = event_passages(conn)
    finally:
        conn.close()
    assert got["es1"] == f"期末考試\n{UNIT}" and got["ewk"] == f"族語課\n{UNIT}\n{tr.WK_TEXT}"


def test_no_vector_is_computed_again_for_the_line(built, tmp_path):
    # was test_only_the_events_vectors_are_computed_again (the five events encoded again); changed 11-14 on purpose:
    # the line is in no embedded text, so the cache of a build without it serves every chunk
    _, _, inputs, paths = built
    cache = tmp_path / "cache" / "vec_cache.sqlite"
    before = tm.run(paths, tmp_path / "db" / "v0.db", inputs=inputs, embed=True, embedder=FakeEmbedder(),
                    vec_cache=cache, event_dates=False)
    after = tm.run(paths, tmp_path / "db" / "v1.db", inputs=inputs, embed=True, embedder=FakeEmbedder(), vec_cache=cache)
    assert before.vectors["cached"] == 0 and before.vectors["chunks"] == after.vectors["chunks"] == 6
    assert (after.vectors["encoded"], after.vectors["cached"]) == (0, 6)


def test_the_command_line_switch(built, tmp_path, capsys):
    _, _, inputs, paths = built
    (corpus, export), = inputs
    rest = ["--input", f"{corpus}::{export}", "--aliases", str(paths["aliases"]), "--server-dates",
            str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*rest, "--out", str(tmp_path / "cli" / "on.db")]) == 0
    assert "行事历事件加日期行 5 份" in capsys.readouterr().out
    assert build.main([*rest, "--out", str(tmp_path / "cli2" / "off.db"), "--no-event-dates"]) == 0
    assert "行事历事件没加日期行（--no-event-dates）" in capsys.readouterr().out
    saved = json.loads((tmp_path / "cli2" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["event_dates"] == {"on": False, "events": 0}
    conn = open_ro(tmp_path / "cli2" / "off.db")
    try:
        assert conn.execute("SELECT count(*) FROM docs WHERE date_line != ''").fetchone() == (0,)
    finally:
        conn.close()
