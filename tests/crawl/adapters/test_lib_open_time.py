from datetime import date

import pytest

from nknu.core.pii import find_pii
from nknu.core.records import FactRecord, HoursRule
from nknu.crawl.adapters.lib_open_time import URL, LibOpenTime
from tests.crawl.adapters.helpers import NO_HISTORY, read, snap

A = LibOpenTime()
SPACES = {"nknu.heping.library.main", "nknu.yanchao.library.main", "nknu.heping.library.iread", "nknu.yanchao.library.iread"}


@pytest.fixture
def recs():
    return A.parse(snap(URL, read("lib_open_time", "page.html")))


def regular(recs, eid):
    return next(r for r in recs if isinstance(r, FactRecord) and r.key == f"lib_open_time:{eid}:regular")


def closures(recs, eid):
    return [r for r in recs if isinstance(r, FactRecord) and r.entity_id == eid and r.value["closed"]]


def test_regular_hours_both_libraries(recs):
    assert regular(recs, "nknu.heping.library.main").rules == (HoursRule((1, 2, 3, 4, 5), "08:15", "22:00"),
                                                               HoursRule((6, 7), "08:15", "17:00"))
    assert regular(recs, "nknu.yanchao.library.main").rules == (HoursRule((1, 2, 3, 4, 5), "09:00", "22:00"),
                                                                HoursRule((6, 7), "09:15", "17:00"))


def test_regular_is_term_based_tier1_table(recs):
    r = regular(recs, "nknu.heping.library.main")
    assert r.valid_basis == "term" and r.valid_from is None and r.specificity == 1
    assert r.source.kind == "table_page" and r.source.publisher == "nknu.org.lis"


def test_iread_spaces_parsed(recs):
    assert regular(recs, "nknu.yanchao.library.iread").rules == (HoursRule((1, 2, 3, 4, 5), "09:15", "21:30"),
                                                                 HoursRule((6, 7), "09:15", "16:30"))


def test_closure_days_for_every_space(recs):
    n = {eid: len(closures(recs, eid)) for eid in SPACES}
    assert len(set(n.values())) == 1 and n["nknu.heping.library.main"] == 12


def test_closure_1025_range(recs):
    c = next(r for r in closures(recs, "nknu.heping.library.main") if "光復" in r.value["reason"])
    assert (c.valid_from, c.valid_to) == (date(2026, 10, 25), date(2026, 10, 27)) and c.specificity == 2


def test_dash_ranges_parsed(recs):
    c = next(r for r in closures(recs, "nknu.heping.library.main") if r.value["reason"].startswith("和平紀念日"))
    assert (c.valid_from, c.valid_to) == (date(2026, 2, 27), date(2026, 3, 1))


def test_notes_kept(recs):
    assert any("closed on national and school holidays" in n for n in regular(recs, "nknu.heping.library.main").value["notes"])


def test_maintenance_page_fails_invariants():
    s = snap(URL, read("lib_open_time", "maintenance.html"))
    recs = A.parse(s)
    assert recs == [] and any(v.code == "selector_missing" for v in A.invariants(recs, NO_HISTORY, [s]))


def test_parse_is_pure():
    s = snap(URL, read("lib_open_time", "page.html"))
    assert A.parse(s) == A.parse(s)


def test_fixture_has_no_pii():
    assert find_pii(read("lib_open_time", "page.html")) == []
