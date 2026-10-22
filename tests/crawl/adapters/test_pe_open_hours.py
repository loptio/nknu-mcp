from datetime import date

import pandas as pd
import pytest

from nknu.core.pii import find_pii
from nknu.core.records import EntityHint, FactRecord, HoursRule
from nknu.crawl.adapter import ParseError
from nknu.crawl.adapters import pe_open_hours as mod
from nknu.crawl.adapters.pe_open_hours import URL, PeOpenHours
from tests.crawl.adapters.helpers import NO_HISTORY, read, snap

A = PeOpenHours()


@pytest.fixture
def page():
    return read("pe_open_hours", "page.html")


def facts(records):
    return [r for r in records if isinstance(r, FactRecord)]


def by_entity(records, eid):
    return next(r for r in facts(records) if r.entity_id == eid)


def test_weight_room_hours(page):
    r = by_entity(A.parse(snap(URL, page)), "nknu.heping.gym.weight-room")
    assert (r.valid_from, r.valid_to) == (date(2026, 9, 7), date(2027, 1, 9))
    assert r.rules == (HoursRule((1, 2, 3, 5), "19:00", "21:00"), HoursRule((4,), "18:00", "20:00"))
    assert r.specificity == 1 and r.valid_basis == "explicit" and not r.propagates
    assert r.source.kind == "table_page" and r.source.publisher == "nknu.org.pe" and r.source.title == "場地開放時間"


def test_numbered_segments_parsed(page):
    r = by_entity(A.parse(snap(URL, page)), "nknu.heping.gym.main")
    assert r.rules == (HoursRule((1, 2, 3, 4, 5), "17:30", "21:50"), HoursRule((6,), "13:00", "16:50"))


def test_all_known_venues_parsed(page):
    assert {r.entity_id for r in facts(A.parse(snap(URL, page)))} == {
        "nknu.heping.gym.main", "nknu.heping.gym.outdoor", "nknu.heping.gym.weight-room", "nknu.heping.gym.pool",
        "nknu.yanchao.gym.outdoor", "nknu.yanchao.gym.table-tennis", "nknu.yanchao.gym.weight-room"}


def test_pool_under_renovation_is_closed_for_term(page):
    r = by_entity(A.parse(snap(URL, page)), "nknu.heping.gym.pool")
    assert r.value["closed"] is True and r.valid_basis == "term" and r.valid_from is None and "整修" in r.value["reason"]


def test_notes_kept_verbatim(page):
    r = by_entity(A.parse(snap(URL, page)), "nknu.heping.gym.weight-room")
    assert any("國定及連續假日" in n and "不予開放" in n for n in r.value["notes"])


def test_quote_le_200(page):
    assert all(len(r.quote) <= 200 for r in facts(A.parse(snap(URL, page))))


def test_cross_check_mismatch_fails(page, monkeypatch):
    real = pd.read_html

    def tampered(*a, **k):
        dfs = real(*a, **k)
        for df in dfs:
            if df.shape == (8, 5):
                df.iat[3, 3] = "週一 06:00~07:00"
        return dfs

    monkeypatch.setattr(mod.pd, "read_html", tampered)
    with pytest.raises(ParseError, match="cross_check"):
        A.parse(snap(URL, page))


def test_unknown_venue_hint(page):
    html = page.replace("游泳池", "攀岩場", 1)
    recs = A.parse(snap(URL, html))
    assert any(isinstance(r, EntityHint) and "攀岩場" in r.name for r in recs)
    assert "nknu.heping.gym.pool" not in {r.entity_id for r in facts(recs)}


def test_unparseable_date_is_issue(page):
    recs = A.parse(snap(URL, page.replace("115.09.07至116.01.08", "另行公告", 1)))
    assert any(getattr(r, "code", None) == "date" for r in recs)


def test_maintenance_page_fails_invariants():
    s = snap(URL, read("pe_open_hours", "maintenance.html"))
    recs = A.parse(s)
    assert recs == [] and any(v.code == "selector_missing" for v in A.invariants(recs, NO_HISTORY, [s]))


def test_missing_weight_room_fails_invariants(page):
    s = snap(URL, page)
    recs = [r for r in A.parse(s) if getattr(r, "entity_id", "") != "nknu.heping.gym.weight-room"]
    assert any(v.code == "missing_core_venue" for v in A.invariants(recs, NO_HISTORY, [s]))


def test_parse_is_pure(page):
    assert A.parse(snap(URL, page)) == A.parse(snap(URL, page))


def test_fixture_has_no_pii(page):
    assert find_pii(page) == []


def test_layout_tables_do_not_break_cross_check(page):
    nested = A.parse(snap(URL, read("pe_open_hours", "nested_layout.html")))
    assert [(r.key, r.rules) for r in facts(nested)] == [(r.key, r.rules) for r in facts(A.parse(snap(URL, page)))]
