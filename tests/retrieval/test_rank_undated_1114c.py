"""Plan 2026-11-14 T2 (main session, the same day): a dated document one of whose merged copies has no date ranks no
lower than that copy did alone.

The direction's fifth version put it as 「没日期的副本照现在算 0.15。这样合并不会让一份文件比现在排得更后」. The build
keeps the earliest dated copy; a rule kept on a standing page (undated) beside the notice that posted it would
otherwise rank by the notice's old day, under where its standing copy stood. The build writes docs.rank_undated for
such a document (build._rank_dates) and search gives its date part the no-date share at least (search._date_part).
No model loads; nothing here is real data."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from nknu.content.schema import create_schema
from nknu.retrieval.search import RankConfig, _date_part, _Doc, _recency, doc_rows

TODAY = date(2026, 11, 1)


def test_an_undated_copy_keeps_the_no_date_share():
    rank = RankConfig()
    old = _Doc("du1", None, None, "2022-03-01", rank_date="2022-03-01", rank_undated=True)
    assert _date_part(rank, old, None, TODAY) == _recency(rank, None, TODAY)
    assert _recency(rank, None, TODAY) > _recency(rank, "2022-03-01", TODAY)  # what the old day alone would give


def test_a_recent_day_still_counts_more_than_the_no_date_share():
    rank = RankConfig()
    new = _Doc("du2", None, None, "2026-10-20", rank_date="2026-10-20", rank_undated=True)
    assert _date_part(rank, new, None, TODAY) == _recency(rank, "2026-10-20", TODAY) > _recency(rank, None, TODAY)


def test_without_the_flag_the_date_part_is_the_date_s():
    rank = RankConfig()
    plain = _Doc("du3", None, None, "2022-03-01", rank_date="2022-03-01")
    assert _date_part(rank, plain, None, TODAY) == _recency(rank, "2022-03-01", TODAY)


def _db(extra: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars, ident_date,"
                 " rank_date, rank_undated) VALUES ('d1', 'attachment', 'file', '甲要點', 'https://x.invalid/a.pdf',"
                 " '教務處', 0, 0, '2022-03-01', '2022-03-01', 1)")
    if extra:
        conn.execute(extra)
    return conn


def test_doc_rows_reads_the_flag_and_copy_dates_off_does_not():
    conn = _db()
    assert doc_rows(conn, ["d1"])["d1"].rank_undated is True
    off = doc_rows(conn, ["d1"], copy_dates=False)["d1"]
    assert (off.rank_date, off.rank_undated) == (None, False)
    conn.close()


@pytest.mark.parametrize("order", [("rank_undated", "rank_date"), ("rank_date", "rank_undated"),
                                   ("expires", "rank_undated", "valid_ay", "rank_date", "date_line")])
def test_a_database_without_the_columns_reads_as_before(order):
    conn = _db()
    for column in order:  # schema.DDL's note on comments and commas: the columns can be dropped in any order
        conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    doc = doc_rows(conn, ["d1"])["d1"]
    assert (doc.ident, doc.rank_date, doc.rank_undated) == ("2022-03-01", None, False)
    conn.close()
