from dataclasses import replace
from datetime import date

import pytest

from nknu.core.records import HoursRule, SourceRef
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.writer import apply_fact
from tests.factories import PE_TABLE, T0, T1, WR_TERM
from tests.scenario import COURT, GYM, HLIB, NIGHT, POOL, WR, YLIB, YWR, build, fact


@pytest.fixture
def conn(tmp_path):
    c = build(tmp_path)
    yield c
    c.close()


@pytest.fixture
def be(conn):
    return SQLiteBackend(conn)


def r(be, entity, d, **kw):
    return resolve(be, entity, "hours", d, **kw)


def test_today_current_term_table_wins_2022_absent(be):
    res = r(be, WR, date(2026, 10, 22))  # Thursday
    assert res["status"] == "current" and res["decided_by"] == "R1"
    assert res["winner"]["source"]["title"] == "場地開放時間"
    assert res["today"] == {"open": True, "segments": [{"opens": "18:00", "closes": "20:00"}]}
    assert "21:30" not in str(res) and "21:50" not in str(res)
    assert res["trace"][0]["dropped"] >= 1


def test_closure_on_parent_gym_propagates_to_weight_room(be):
    res = r(be, WR, date(2026, 10, 25))
    assert res["decided_by"] == "R2" and res["winner"]["inherited"] and res["winner"]["entity_id"] == GYM
    assert res["today"] == {"open": False, "reason": "臺灣光復紀念日連假"}


def test_closure_not_propagated_to_other_campus(be):
    res = r(be, YWR, date(2026, 10, 26))  # Monday
    assert res["status"] == "current" and res["winner"]["entity_id"] == YWR and not res["winner"]["inherited"]
    assert res["today"]["open"] is True


def test_outdoor_closure_does_not_propagate(be):
    res = r(be, COURT, date(2026, 10, 25))
    assert res["winner"]["entity_id"] == COURT and res["today"]["open"] is True


def test_library_extension_beats_regular_saturday(be):
    res = r(be, HLIB, date(2026, 10, 24))
    assert res["decided_by"] == "R2" and res["today"] == {"open": True, "segments": [{"opens": "08:15", "closes": "21:00"}]}
    assert r(be, YLIB, date(2026, 10, 24))["today"]["segments"] == [{"opens": "09:15", "closes": "21:00"}]


def test_holiday_is_not_closure(be):
    assert r(be, HLIB, date(2026, 10, 24))["today"]["open"] is True
    assert resolve(be, "nknu", "holiday", date(2026, 10, 24))["status"] == "current"


def test_library_two_sources_agree_closed(be):
    res = r(be, HLIB, date(2026, 10, 25))
    assert res["status"] == "current" and res["today"]["open"] is False and res["decided_by"] == "R3"


def test_after_term_is_stale_not_old_value(be):
    for ent in (WR, HLIB):
        res = r(be, ent, date(2027, 2, 1))
        assert res["found"] is False and res["status"] == "stale" and "winner" not in res and "today" not in res
    assert r(be, WR, date(2027, 2, 1))["last_known"]["valid"] == ["2026-09-07", "2027-01-09"]


def test_stale_last_known_carries_its_values(be):
    """U3: a stale answer states the last known hours, so last_known is a full brief (values, source title)."""
    lk = r(be, WR, date(2027, 2, 1))["last_known"]
    assert lk["fact_id"] and lk["value"] == {"closed": False} and lk["specificity"] == 1
    assert lk["rules"] == [{"dows": [1, 2, 3, 5], "opens": "19:00", "closes": "21:00"},
                           {"dows": [4], "opens": "18:00", "closes": "20:00"}]
    assert lk["source"] == {"url": PE_TABLE.url, "title": "場地開放時間", "publisher": "nknu.org.pe"}


def test_stale_last_known_skips_exceptions(conn, be):
    apply_fact(conn, fact("late-closure", WR, PE_TABLE, (date(2027, 1, 20), date(2027, 1, 22)), spec=2, closed=True,
                          reason="維修"), adapter="pe_open_hours", review="verified", now=T0)
    assert r(be, WR, date(2027, 2, 1))["last_known"]["valid"] == ["2026-09-07", "2027-01-09"]


def test_no_data_for_entity_without_facts(be):
    res = r(be, POOL, date(2026, 10, 22))
    assert res["status"] == "no_data" and res["last_known"] is None


def test_inclusive_end_date(be):
    assert r(be, WR, date(2027, 1, 8))["found"] is True
    assert r(be, WR, date(2027, 1, 9))["found"] is False


def test_same_tier_conflict_reports_both_sides(conn, be):
    other = SourceRef("https://c.nknu.edu.tw/reality/Page.aspx?PN=9", "場地開放時間(另一版本)", "nknu.org.pe", "table_page", "x")
    apply_fact(conn, replace(WR_TERM, key="v2", source=other,
                             rules=(HoursRule((1, 2, 3, 5), "19:00", "21:00"), HoursRule((4,), "18:30", "20:30"))),
               adapter="pe_open_hours", review="verified", now=T0)
    res = r(be, WR, date(2026, 10, 22))
    assert res["conflict"] is True and res["decided_by"] == "R5" and len(res["alternatives"]) == 2
    assert sorted(a["today"]["segments"][0]["opens"] for a in res["alternatives"]) == ["18:00", "18:30"]


def _announcement(n, published):
    return SourceRef(f"https://c.nknu.edu.tw/reality/NewsView.aspx?Nid={n}", f"公告{n}", "nknu.org.pe", "announcement", f"s{n}",
                     published_on=published)


def test_recency_breaks_tie_between_dated_announcements(conn, be):
    term = (date(2026, 9, 7), date(2027, 1, 9))
    apply_fact(conn, fact("a1", POOL, _announcement(1, date(2026, 9, 1)), term, [HoursRule((1,), "08:00", "10:00")]),
               adapter="pe_news", review="verified", now=T0)
    apply_fact(conn, fact("a2", POOL, _announcement(2, date(2026, 10, 1)), term, [HoursRule((1,), "09:00", "11:00")]),
               adapter="pe_news", review="verified", now=T0)
    res = r(be, POOL, date(2026, 10, 26))
    assert res["decided_by"] == "R4" and res["today"]["segments"] == [{"opens": "09:00", "closes": "11:00"}]


def test_recency_skipped_when_any_undated(conn, be):
    term = (date(2026, 9, 7), date(2027, 1, 9))
    apply_fact(conn, fact("a1", POOL, _announcement(1, None), term, [HoursRule((1,), "08:00", "10:00")]),
               adapter="pe_news", review="verified", now=T0)
    apply_fact(conn, fact("a2", POOL, _announcement(2, date(2026, 10, 1)), term, [HoursRule((1,), "09:00", "11:00")]),
               adapter="pe_news", review="verified", now=T0)
    assert r(be, POOL, date(2026, 10, 26))["status"] == "conflict"


def test_evidence_group_counts_once(conn, be):
    term = (date(2026, 9, 7), date(2027, 1, 9))
    body = replace(_announcement(7, date(2026, 10, 1)), evidence_key="pe_news:7")
    attachment = replace(body, url="https://c.nknu.edu.tw/files/7.pdf", sha256="att")
    for key, src in (("b", body), ("att", attachment)):
        apply_fact(conn, fact(key, POOL, src, term, [HoursRule((1,), "08:00", "10:00")]), adapter="pe_news", review="verified",
                   now=T0)
    res = r(be, POOL, date(2026, 10, 26))
    assert res["status"] == "current" and res["supporting"] == []


def test_rejected_and_deprecated_excluded(conn, be):
    other = SourceRef("https://c.nknu.edu.tw/reality/Page.aspx?PN=9", "x", "nknu.org.pe", "table_page", "y")
    bad = replace(WR_TERM, key="bad", source=other, rules=(HoursRule((4,), "07:00", "08:00"),))
    fid = apply_fact(conn, bad, adapter="pe_open_hours", review="rejected", now=T0)
    assert r(be, WR, date(2026, 10, 22))["status"] == "current"
    conn.execute("UPDATE fact SET recorded_to=? WHERE id=?", (T1, fid))
    conn.execute(
        """INSERT INTO fact(id, lineage_id, record_key, entity_id, attribute, value_json, valid_from, valid_to, valid_basis,
                            specificity, source_doc, tier, extraction, rank, rank_reason, review, recorded_from)
           SELECT 'dep', 'dep-l', 'dep', entity_id, attribute, value_json, valid_from, valid_to, valid_basis, specificity,
                  source_doc, tier, extraction, 'deprecated', 'test', 'verified', recorded_from FROM fact WHERE id=?""", (fid,))
    conn.execute("INSERT INTO hours_rule VALUES ('dep', '[4]', '07:00', '08:00')")
    assert r(be, WR, date(2026, 10, 22))["status"] == "current"


def test_cross_midnight_rule_returned(be):
    res = r(be, NIGHT, date(2026, 10, 23))  # Friday
    assert res["today"] == {"open": True, "segments": [{"opens": "22:00", "closes": "02:00"}]}


def test_bitemporal_as_of(conn, be):
    apply_fact(conn, replace(WR_TERM, valid_to=date(2027, 1, 16)), adapter="pe_open_hours", review="verified", now=T1)
    t = date(2027, 1, 12)
    assert r(be, WR, t, as_of="2026-10-30T00:00:00Z")["found"] is False
    assert r(be, WR, t, as_of="2026-11-02T00:00:00Z")["found"] is True
    assert r(be, WR, t)["found"] is True
