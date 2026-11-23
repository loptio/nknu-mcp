"""spec 2026-11-16 §二 (plan 2026-11-16 T3), the query's side of 「只写某一届的文件届满四年后开始打折」: a document of one
entry cohort alone falls by whole academic years from the year the build gave it (docs.valid_ay, the last year that
cohort studies), like a document whose title names its year, and not by the age of its file; a question that names
the cohort takes nothing off the documents of that cohort. Only in a library whose meta says cohort_expiry on: in
any library built before, every value is the one of before."""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import date
from types import SimpleNamespace

import pytest

from nknu.retrieval import hybrid
from nknu.retrieval.hybrid import HybridConfig, validity
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content.test_validity_1114 import ACA, ACA_SITE, OLD, PDF, TODAY

OLD_TABLE, NEW_TABLE, PLAIN = ACA + "f/x/98.pdf", ACA + "f/y/111.pdf", ACA + "f/z/map.pdf"
ROWS = [{"kind": PDF, "url": OLD_TABLE, "title": "校定共同必修科目表(98學年度入學新生適用).pdf", "text": "必修共二十八學分。",
         "modified": "2025-10-08T09:00:00+08:00"},
        {"kind": PDF, "url": NEW_TABLE, "title": "校定共同必修科目表(111學年度起入學新生適用).pdf", "text": "必修共二十八學分，體育四學分。",
         "modified": OLD},
        {"kind": "newsdetail", "url": ACA + "news/1", "title": "停修申請公告", "text": "受理停修申請。", **tc.dated("2024-03-01")},
        # a notice of its year that names an entry cohort: it goes out of date by its title's year, not for the cohort;
        # the file on it states the one cohort alone and no year: that one does expire for its cohort
        {"kind": "newsdetail", "url": ACA + "news/2", "title": "111學年度英文檢測通過名單公告", "text": "111學年度入學新生適用。",
         **tc.dated("2023-06-01")},
        {"kind": PDF, "url": ACA + "f/w/list.pdf", "title": "通過名單.pdf", "text": "111學年度入學新生適用\n名單如下。",
         "linked": ACA + "news/2"}]
IDS = {"old": tc.doc_id(OLD_TABLE), "new": tc.doc_id(NEW_TABLE), "notice": tc.doc_id(ACA + "news/1"),
       "year": tc.doc_id(ACA + "news/2"), "hung": tc.doc_id(ACA + "f/w/list.pdf")}


@pytest.fixture
def on(tmp_path):
    aca = tc.site_input(tmp_path / "on", ACA_SITE, "教务处", ROWS)
    out = tmp_path / "db" / "on.db"
    tm.run(tm.tree_files(tmp_path / "tree-on"), out, inputs=[aca])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield conn
    conn.close()


@pytest.fixture
def before(tmp_path, on):
    """The same library without the word of the single-cohort rule in its meta, as one built before the rule."""
    path = tmp_path / "db" / "before.db"
    shutil.copy(tmp_path / "db" / "on.db", path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'cohort_expiry'")
        conn.commit()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    yield conn
    conn.close()


def _pool(cohorts=()):
    return SimpleNamespace(recent=True, cohorts=list(cohorts))


def test_one_cohort_falls_by_the_years_since_that_cohort_finished(on):
    ids = list(IDS.values())
    got = validity(on, ids, TODAY, 0.85, cohort_step=4)
    # 98 + 3 = 101, today the 115 year: fourteen whole years, whatever day its file was put up (2025-10-08)
    assert got[IDS["old"]] == pytest.approx(0.85 ** 14)
    assert IDS["new"] not in got  # 「起入學」: it does not go out of date
    days = (TODAY - date(2024, 3, 1)).days
    assert got[IDS["notice"]] == pytest.approx(0.85 ** (days / 365))  # any other document: by its own age, as before


def test_without_the_step_it_falls_by_its_files_age_as_any_document(on):
    got = validity(on, list(IDS.values()), TODAY, 0.85)
    days = (TODAY - date(2025, 10, 8)).days
    assert got[IDS["old"]] == pytest.approx(0.85 ** (days / 365))


def test_the_search_reads_the_rule_from_the_library(on, before):
    head = list(IDS.values())
    assert hybrid._valid(on, head, _pool(), TODAY, HybridConfig())[IDS["old"]] == pytest.approx(0.85 ** 14)
    # a library that does not say so (any built before 11-16): no whole steps for a cohort, the file's own age
    aged = 0.85 ** ((TODAY - date(2025, 10, 8)).days / 365)
    assert hybrid._valid(before, head, _pool(), TODAY, HybridConfig())[IDS["old"]] == pytest.approx(aged)


def test_a_question_naming_the_cohort_takes_nothing_off_its_documents(on):
    head = list(IDS.values())
    got = hybrid._valid(on, head, _pool([98]), TODAY, HybridConfig())
    assert IDS["old"] not in got and IDS["notice"] in got
    assert IDS["old"] in hybrid._valid(on, head, _pool([110]), TODAY, HybridConfig())  # another cohort's: still off


def test_a_notice_of_its_year_keeps_its_discount_whatever_cohort_the_question_names(on):
    """Found on the trial library of 11-16 (question Q15): the first version took the discount off every document
    that applies to the cohort named, and two notices of 2023 about the 111 entrants rose from 0.52 to 1."""
    head = list(IDS.values())
    plain = hybrid._valid(on, head, _pool(), TODAY, HybridConfig())
    named = hybrid._valid(on, head, _pool([111]), TODAY, HybridConfig())
    assert plain[IDS["year"]] == pytest.approx(0.85 ** 4) and named[IDS["year"]] == plain[IDS["year"]]
    # the file of one cohort alone: one year past that cohort's four (111 + 3 = 114), and nothing off for its cohort
    assert plain[IDS["hung"]] == pytest.approx(0.85) and IDS["hung"] not in named
    assert on.execute("SELECT value FROM meta WHERE key = 'cohort_years'").fetchone() == ("4",)


def test_a_library_of_before_scores_to_the_bit_as_before(before):
    ids = list(IDS.values())
    plain = validity(before, ids, TODAY, 0.85)
    assert IDS["old"] in plain and hybrid._valid(before, ids, _pool([98]), TODAY, HybridConfig()) == plain
