"""Plan 2026-11-14 T2, completed the same day (the build's part): docs.rank_date, the date a merged document ranks
by.

The direction (2026-11-13 第 2 件 1): of exact copies the earliest is shown, with its own date, and for ranking by
recency the most favourable among the copies counts, so that merging never ranks a document lower than before. The
first rebuild kept the earliest and let search rank it by that earliest date: on the 教务处 control a gold document's
keyword rank fell from 4 to 26 once its copies were merged into an earlier one (main session, 11-14). The build now
writes docs.rank_date: the latest identity date among the document kept and the documents merged into it; a document
without copies has its own date there, and one undated with all its copies NULL. docs.ident_date, what the response
shows, stays the kept document's own. nknu.retrieval.search reads the column
(tests/retrieval/test_copy_dates_1114b.py). The sentences are made up; no model loads."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date

import pytest

from nknu.content import build
from nknu.content.corpus import PageMeta, Record
from nknu.content.documents import build_documents
from nknu.content.schema import SCHEMA_VERSION, create_schema
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import RankConfig, search_docs
from tests.content import test_build as tb
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_recurring_1114 as tr

LONG, SHORT, OTHER = tc.LONG, tc.SHORT, tc.OTHER
ACA, ACA_SITE = tc.ACA, tc.ACA_SITE


def ranked(records: list[Record], metas: dict | None = None) -> tuple[dict, dict]:
    """({kept doc_id: (its own identity date, its rank date)}, the report) after the build's own steps."""
    docs, excluded = build_documents(records, metas or {}, [])
    kept, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded)
    report = build._rank_dates(kept, copies)
    return {r.doc.doc_id: (r.ident.date if r.ident else None, r.rank_date) for r in kept}, report


def day(iso: str) -> date:
    return date.fromisoformat(iso)


# ---------------------------------------------------------------- the schema

def test_the_schema_has_the_column_and_is_still_pipeline_8():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    columns = {r[1]: (r[2], r[3], r[4]) for r in conn.execute("PRAGMA table_info(docs)")}
    assert columns["rank_date"] == ("TEXT", 0, None)  # may be NULL: no date anywhere
    assert SCHEMA_VERSION == "pipeline-8"  # the version of 11-14, which no database in use has yet
    conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars)"
                 " VALUES ('d1', 'page', 'page', '甲', 'https://x.invalid/', '教務處', 0, 0)")
    assert conn.execute("SELECT rank_date FROM docs").fetchone() == (None,)
    conn.close()


@pytest.mark.parametrize("order", [("rank_date", "valid_ay", "expires", "date_line"),
                                   ("valid_ay", "rank_date", "date_line", "expires"),
                                   ("date_line", "expires", "valid_ay", "rank_date"),
                                   ("expires", "rank_date", "valid_ay", "date_line")])
def test_the_new_columns_can_be_dropped_in_any_order(order):
    # the tests that read a database as one built before drop them (schema.DDL's note on comments and commas)
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    for column in order:
        conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    assert not {r[1] for r in conn.execute("PRAGMA table_info(docs)")} & set(order)
    conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars)"
                 " VALUES ('d1', 'page', 'page', '甲', 'https://x.invalid/', '教務處', 0, 0)")
    conn.close()


# ---------------------------------------------------------------- which date

def test_a_merged_document_ranks_by_the_latest_date_among_its_copies_and_keeps_its_own():
    pages = [tc.page("p1", 1, "甲"), tc.page("p2", 2, "乙", "2026-05-01"), tc.page("p3", 3, "丙", "2026-02-01"),
             tc.page("p4", 4, "丁"), tc.page("p5", 5, "戊", "2025-11-20", text=OTHER)]
    got, report = ranked(pages)
    # the earliest of the group is kept (plan 2026-11-14 T2) and still shows its own day; it ranks by the latest
    assert got == {"dp3": (day("2026-02-01"), day("2026-05-01")), "dp5": (day("2025-11-20"), day("2025-11-20"))}
    assert report == {"dated": 2, "later": 1, "from_copy": 0, "undated_copy": 1}


def test_a_document_without_copies_has_its_own_date_and_an_undated_one_none():
    got, report = ranked([tc.page("p1", 1, "甲", "2026-05-01"), tc.page("p2", 2, "乙", text=OTHER),
                          tc.page("p3", 3, "丙", text=SHORT)])
    assert got == {"dp1": (day("2026-05-01"), day("2026-05-01")), "dp2": (None, None), "dp3": (None, None)}
    assert report == {"dated": 1, "later": 0, "from_copy": 0, "undated_copy": 0}


def test_copies_that_are_all_undated_give_none():
    got, report = ranked([tc.page("p1", 1, "甲"), tc.page("p2", 2, "乙")])
    assert got == {"dp1": (None, None)} and report == {"dated": 0, "later": 0, "from_copy": 0, "undated_copy": 0}


def test_a_group_of_the_title_rule_keeps_the_latest_and_so_its_own_date():
    # one title and a short text: the latest dated one is kept (the title rule's choice), which is the latest already
    pages = [tc.page("p1", 1, "社團博覽會", "2026-02-01", SHORT), tc.page("p2", 2, "社團博覽會", "2026-05-01", SHORT)]
    got, report = ranked(pages)
    assert got == {"dp2": (day("2026-05-01"), day("2026-05-01"))}
    assert report == {"dated": 1, "later": 0, "from_copy": 0, "undated_copy": 0}


def test_a_file_on_a_notice_ranks_by_a_later_copy_dated_by_the_server_alone():
    # the title rule keeps the file on the latest notice; a copy the server dates later still counts for ranking
    notice = tc.rec("p1", "newsdetail", ACA + "newsdetail.aspx?nid=1&gi=2", "開館公告", "請見附件", ACA_SITE)
    metas = {"p1": PageMeta("和平教務組", "開館公告", "2026/3/1 上午 09:00:00", day("2026-03-01"), ())}
    hung = tc.rec("a1", tc.PDF, ACA + "f/1/表.pdf", "表.pdf", SHORT, ACA_SITE, linked=(notice.url,))
    loose = tc.rec("a2", tc.PDF, ACA + "f/2/表.pdf", "表.pdf", SHORT, ACA_SITE, modified="2026-06-10T01:00:00+08:00")
    got, report = ranked([notice, hung, loose], metas)
    assert got["da1"] == (day("2026-03-01"), day("2026-06-10")) and "da2" not in got
    assert report == {"dated": 2, "later": 1, "from_copy": 0, "undated_copy": 0}


def test_an_undated_document_takes_a_dated_copys_day():
    # the primary file is kept before the archive member (build._merge_copies), undated as it is; the member's day
    # is the only one the group has. Search never ranks such a document below what no date gives
    notice = tc.rec("p1", "detail:sso_webidx", tc.WEB + "1", "申請公告", "請見附件", tc.WEB_SITE)  # an undated notice
    member = tc.rec("m1", tc.PDF, ACA + "f/forms.zip#表/申請表.pdf", "申請表.pdf", LONG, ACA_SITE,
                    modified="2026-01-05T01:00:00+08:00")
    loose = tc.rec("a2", tc.PDF, ACA + "f/2/申請表單.pdf", "申請表單.pdf", LONG, ACA_SITE, linked=(notice.url,))
    got, report = ranked([notice, member, loose])
    assert got["da2"] == (None, day("2026-01-05")) and "dm1" not in got
    assert report == {"dated": 1, "later": 0, "from_copy": 1, "undated_copy": 0}


# ---------------------------------------------------------------- the build

@pytest.fixture
def copied(tmp_path):
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=tc.copy_inputs(tmp_path))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn
    conn.close()


def test_the_build_writes_both_dates(copied):
    report, conn = copied
    d = tc.IDS
    dates = {r[0]: r[1:] for r in conn.execute("SELECT doc_id, ident_date, rank_date FROM docs")}
    # the notice on three sites: 教务处's of 03-01 is kept and shown; the latest copy is 成教所's of 03-05
    assert dates[d["a_notice"]] == ("2026-03-01", "2026-03-05")
    assert dates[d["a_file"]] == ("2026-03-01", "2026-03-05")  # each file inherits its own notice's day
    assert dates[d["a_page"]] == (None, None)  # two addresses of one undated page
    assert dates[tc.doc_id(tc.TG + "page.aspx?pn=3")] == (None, None)  # no copies, no date
    assert report.dedupe["rank_dates"] == {"dated": 2, "later": 2, "from_copy": 0, "undated_copy": 0}
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('dedupe_json', 'schema_version')"))
    assert json.loads(meta["dedupe_json"]) == report.dedupe and meta["schema_version"] == "pipeline-8"
    # what the copies' list shows is each copy's own day, as before
    listed = json.loads(conn.execute("SELECT value FROM meta WHERE key = ?", ("copies:" + d["a_notice"],)).fetchone()[0])
    assert [c["date"] for c in listed] == ["2026-03-05", "2026-03-02"]


def test_search_ranks_by_the_latest_copy_and_the_response_shows_the_kept_ones_own_day(copied):
    _, conn = copied
    d = tc.IDS
    today = date(2026, 11, 14)

    def bonus(day: str) -> float:  # ranking v2's date part at its defaults (search._recency)
        return 0.3 * math.exp(-(today - date.fromisoformat(day)).days / 365)

    on = search_docs(conn, "宿舍熱水", today=today, aliases=[])
    plain = search_docs(conn, "宿舍熱水", today=today, aliases=[], rank=RankConfig(recency=False))  # no date part
    scores = [next(h.score for h in r.hits if h.doc_id == d["a_notice"]) for r in (on, plain)]
    # the date part is the latest copy's (03-05), more than the kept document's own day would give
    assert scores[0] - scores[1] == pytest.approx(bonus("2026-03-05")) and bonus("2026-03-05") > bonus("2026-03-01")
    # the answer shows 教务处's notice with its own day, and each copy with its
    shown = next(r for r in build_response(conn, on, today=today)["results"] if r["id"] == d["a_notice"])
    assert "2026-03-01" in shown["identity"] and "2026-03-05" not in shown["identity"]
    assert [(c["id"], c["date"]) for c in shown["copies"]] == [(d["t_notice"], "2026-03-05"),
                                                                (d["n_notice"], "2026-03-02")]


def test_an_event_has_no_rank_date(tmp_path):
    # an event has no identity date: search ranks it by its last day, as before
    out = tmp_path / "db" / "cal.db"
    tm.run(tm.tree_files(tmp_path / "tree"), out,
           inputs=[tr.calendar_input(tmp_path / "aca", tr.ACA_SITE, "教务处", tr.ITEMS)])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT DISTINCT ident_date, rank_date FROM docs WHERE kind_class = 'event'").fetchall() == [
            (None, None)]
    finally:
        conn.close()


def test_the_command_line_says_how_many_rank_by_a_copys_date(tmp_path, capsys):
    paths = tm.tree_files(tmp_path / "tree")
    pairs = [arg for corpus, export in tc.copy_inputs(tmp_path) for arg in ("--input", f"{corpus}::{export}")]
    rest = ["--aliases", str(paths["aliases"]), "--server-dates", str(paths["server"]), "--site-list",
            str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*pairs, "--out", str(tmp_path / "cli" / "new.db"), *rest]) == 0
    assert "按副本里较新的日期排名的 2 份" in capsys.readouterr().out
    saved = json.loads((tmp_path / "cli" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["dedupe"]["rank_dates"] == {"dated": 2, "later": 2, "from_copy": 0, "undated_copy": 0}


def test_inputs_without_copies_of_another_date_rank_by_their_own(tmp_path):
    # test_build's export: every kept document's rank date is its own identity date
    paths = tb.make_inputs(tmp_path)
    report = tb.run(paths, tmp_path / "out" / "aca.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'out' / 'aca.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT ident_date, rank_date FROM docs").fetchall()
    finally:
        conn.close()
    assert rows and all(own == rank for own, rank in rows)
    assert report.dedupe["rank_dates"]["later"] == 0 and report.dedupe["rank_dates"]["from_copy"] == 0
