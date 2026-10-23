import json
import re
from pathlib import Path

import pytest
import yaml

from nknu.server.envelope import BUDGET, NOTICE_LABEL, approx_tokens, count_tokens
from nknu.server.linking import detect_intent, expand, link_entities
from nknu.server.query import CAVEATS, get_hours
from nknu.server.search import CAVEAT_WEAK, T_HIGH, T_LOW, search
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import CONFLICT_DAY, GYM, NOW, WR

YWR = "nknu.yanchao.gym.weight-room"
HLIB, YLIB = "nknu.heping.library.main", "nknu.yanchao.library.main"
SEARCH_DIR = Path(__file__).parents[1] / "search"
ALL_QUERIES = [q["q"] for name in ("queries.yaml", "calibration.yaml")
               for q in yaml.safe_load((SEARCH_DIR / name).read_text())]
_CLOCK = re.compile(r"(?<!\d)\d{1,2}[:：]\d{2}(?!\d)")


@pytest.fixture
def db(world):
    return ServeDB(world[1])


# ---------------------------------------------------------------- the brief's cases

def test_ambiguous_place_lists_both_campuses(db):
    e = search(db, "健身房 幾點開", now=NOW)
    assert e["match"] == "ambiguous" and {f["entity_id"] for f in e["facts"]} == {WR, YWR}
    assert e["caveat"] == "有多個地點符合，請確認校區"


def test_campus_disambiguates(db):
    e = search(db, "燕巢健身房 開放時間", now=NOW)
    assert e["facts"][0]["entity_id"] == YWR and e["match"] == "entity" and "燕巢校區重量訓練室" in e["answer"]


def test_two_char_queries_found(db):
    assert all(search(db, q, now=NOW)["found"] for q in ["重訓", "停修", "閉館"])


def test_english_alias(db):
    assert search(db, "weight room hours", campus="heping", now=NOW)["facts"][0]["entity_id"] == WR


def test_2022_doc_never_returned(db):
    hits = [d for q in ALL_QUERIES for d in search(db, q, limit=10, now=NOW).get("facts", []) if d.get("label")]
    assert hits
    assert not any("Nid=800" in d["url"] or (d.get("published_on") or "").startswith("2022") for d in hits)


def test_noans_found_false(db):
    e = search(db, "學校游泳隊教練的電話", now=NOW)
    assert e["found"] is False and e["caveat"] == "查無相關資料，請勿推測" and "facts" not in e


def test_notice_label(db):
    facts = search(db, "公告", kind="announcement", now=NOW)["facts"]
    assert facts and all(f["label"] == NOTICE_LABEL for f in facts)


# ---------------------------------------------------------------- linking and intent

def test_link_longest_match(db):
    conn = db.conn()
    assert link_entities(conn, "燕巢健身房") == [YWR]  # the longer alias wins over 燕巢 + 健身房
    assert link_entities(conn, "健身房") == [WR, YWR]
    assert link_entities(conn, "和平 健身房") == [WR]  # 和平 in the query is the campus
    assert link_entities(conn, "健身房", campus="yanchao") == [YWR]
    assert link_entities(conn, "和平跟燕巢的圖書館") == [HLIB, YLIB]  # both campuses named: no filter


def test_link_ascii_words_and_non_places(db):
    conn = db.conn()
    assert link_entities(conn, "Weight Room hours") == [WR, YWR]
    assert link_entities(conn, "gymnasium") == [GYM]  # gym inside gymnasium is not a match
    assert link_entities(conn, "燕巢") == [] and link_entities(conn, "體育室") == []  # a campus or a unit is not a place
    assert link_entities(conn, "不存在的館") == []


def test_detect_intent():
    assert detect_intent("健身房幾點開") == "hours" and detect_intent("opening hours") == "hours"
    assert detect_intent("期中考 日期") == "calendar" and detect_intent("下學期什麼時候開學") == "calendar"
    assert detect_intent("期中考 圖書館 開放時間") == "hours"  # 期中 names the period, 開放時間 the question
    assert detect_intent("final exam") == "calendar" and detect_intent("最新消息") == "doc"
    assert detect_intent("校長是誰") is None


def test_expand_appends_canonical_terms():
    assert "重量訓練室" in expand("健身房") and "停修" in expand("退課 申請") and expand("校長是誰") == "校長是誰"


def test_get_hours_links_free_text(db):
    e = get_hours(db, "燕巢的健身房", now=NOW)
    assert e["match"] == "entity" and e["status"] == "current" and "燕巢校區重量訓練室" in e["answer"]


# ---------------------------------------------------------------- full-text path

def test_thresholds_are_ordered():
    assert 0 < T_LOW < T_HIGH <= 1


def test_text_match_over_cards_and_notices(db):
    e = search(db, "停修", now=NOW)
    assert e["found"] is True and e["match"] in ("text", "weak")
    assert any(f.get("category") == "course_withdrawal" for f in e["facts"])
    assert any(f.get("label") == NOTICE_LABEL and "停修" in f["title"] for f in e["facts"])


def test_hours_cards_filtered_by_on_date(db):
    e = search(db, "重訓", on_date="2026-10-25", now=NOW)
    wr = [f for f in e["facts"] if f.get("entity_id") == WR]
    assert wr and "暫停開放" in wr[0]["answer"]


def test_expired_calendar_events_filtered(db):
    e = search(db, "期中考試", kind="calendar", on_date="2026-11-05", now=NOW)
    assert all(f["valid"][1] >= "2026-11-05" for f in e.get("facts", []))
    assert not any(f.get("category") == "midterm_exam" for f in e.get("facts", []))


def test_kind_announcement_only_notices(db):
    e = search(db, "停修", kind="announcement", now=NOW)
    assert e["facts"] and all(f.get("label") == NOTICE_LABEL for f in e["facts"])


def test_kind_hours_without_place_searches_cards(db):
    e = search(db, "撞球", kind="hours", now=NOW)
    assert e["facts"][0]["entity_id"] == "nknu.yanchao.gym.table-tennis"


def test_kind_place_lists_entities(db):
    e = search(db, "燕巢", kind="place", now=NOW)
    assert e["found"] is True and e["facts"]
    assert all(set(f) == {"id", "name", "campus"} and f["campus"] == "yanchao" for f in e["facts"])
    e = search(db, "健身房", kind="place", now=NOW)
    assert [f["id"] for f in e["facts"]] == [WR, YWR]


def test_bare_place_name_or_id_answers_with_its_card(db):
    e = search(db, WR, now=NOW)  # an id from kind=place, passed back as the query
    assert e["match"] == "entity" and e["facts"][0]["entity_id"] == WR and "週四 18:00–20:00" in e["answer"]
    e = search(db, "愛閱館", now=NOW)
    assert e["match"] == "ambiguous" and {f["entity_id"] for f in e["facts"]} == {
        "nknu.heping.library.iread", "nknu.yanchao.library.iread"}
    assert search(db, "體育室", now=NOW).get("match") != "entity"  # a unit is not a place


def test_on_date_answer_for_one_place(db):
    e = search(db, "和平重訓室 開放", on_date="2026-10-25", now=NOW)
    assert e["match"] == "entity" and "暫停開放" in e["answer"]


def test_stale_entity_match_has_no_hours(db):
    e = search(db, "和平重訓室 開放時間", on_date="2027-02-01", now=NOW)
    body = {k: v for k, v in e.items() if k != "today"}
    assert e["status"] == "stale" and not _CLOCK.search(json.dumps(body, ensure_ascii=False))


def test_text_match_status_follows_its_hours_facts(db):
    """Every hours card the text path returns is stale: the envelope says so (as the entity path does), never current."""
    e = search(db, "重訓", on_date="2027-01-20", now=NOW)
    assert e["match"] in ("text", "weak") and e["facts"] and all(f["status"] == "stale" for f in e["facts"])
    assert e["status"] == "stale" and e["found"] is False and CAVEATS["stale"] in e["caveat"]


def test_text_match_conflict_carries_its_caveat(conflict_world):
    e = search(conflict_world, "重訓", on_date=CONFLICT_DAY, now=NOW)
    assert e["facts"][0]["status"] == "conflict" and e["status"] == "conflict" and e["found"] is True
    assert CAVEATS["conflict"] in e["caveat"]


def test_text_match_mixing_current_and_stale_takes_the_worst(db):
    e = search(db, "重訓", on_date="2027-02-08", now=NOW)  # 和平: 春節 closure card (current); 燕巢: stale
    statuses = {f.get("status", "current") for f in e["facts"] if f.get("entity_id")}
    assert statuses == {"current", "stale"} and e["status"] == "stale" and e["found"] is True


def test_weak_caveat_is_kept_with_a_status_caveat():
    from nknu.server.search import _caveat

    assert _caveat("weak", "stale") == CAVEAT_WEAK + "；" + CAVEATS["stale"]
    assert _caveat("text", "current") is None and _caveat("weak", "current") == CAVEAT_WEAK


# ---------------------------------------------------------------- dated cards (closures, extensions)

def test_entity_match_is_the_get_hours_envelope(db):
    """An entity match answers as get_hours does for that place and day, so the coming closure is in next_change."""
    e = search(db, "体育馆 暂停开放 连假", now=NOW)
    g = get_hours(db, GYM, now=NOW)
    assert e["match"] == "entity" and e["facts"] == [{"entity_id": GYM, "name": "和平校區體育館"}]
    assert e["next_change"]["from"] == "2026-10-24" and "暫停開放（臺灣光復紀念日連假）" in e["next_change"]["answer"]
    keys = ("found", "status", "answer", "next_change", "open_now")
    assert {k: e.get(k) for k in keys} == {k: g.get(k) for k in keys} and e["open_now"] is False


def test_ambiguous_candidates_carry_their_next_change(db):
    e = search(db, "期中考那個週末圖書館有延長開館嗎？和平跟燕巢分別開到幾點", now=NOW)
    assert e["match"] == "ambiguous" and [f["entity_id"] for f in e["facts"]] == [HLIB, YLIB]
    assert all(f["next_change"]["from"] == "2026-10-24" and "延長開館" in f["next_change"]["answer"] for f in e["facts"])
    g = get_hours(db, "圖書館", now=NOW)  # get_hours lists the same candidates the same way
    assert g["facts"] == e["facts"] and count_tokens(e) <= BUDGET


def test_text_match_lists_the_dated_card_the_query_points_at(db):
    e = search(db, "圖資處 期中考 延長開館", now=NOW)
    top = e["facts"][0]
    assert top["entity_id"] in (HLIB, YLIB) and "2026-10-24" in top["answer"] and "延長開館" in top["answer"]
    assert "暫停開放" in search(db, "閉館", now=NOW)["facts"][0]["answer"]
    e = search(db, "重訓 行憲紀念日", on_date="2026-12-20", now=NOW)  # the 12-25 closure is 5 days after on_date
    assert "行憲紀念日" in e["facts"][0]["answer"] and e["facts"][0]["entity_id"] == WR


def test_upcoming_card_only_when_the_query_fits_it_better(db):
    e = search(db, "重訓", limit=10, now=NOW)  # the 10-24 closure fits 重訓 no better than today's card does
    assert [f["entity_id"] for f in e["facts"] if f.get("entity_id")] == [WR, YWR]
    e = search(db, "重訓 行憲紀念日", limit=10, now=NOW)  # 64 days ahead: beyond the horizon
    assert not any("行憲" in f.get("answer", "") for f in e.get("facts", []))


def test_bad_arguments(db):
    for kwargs in ({"kind": "everything"}, {"campus": "taipei"}, {"limit": 0}, {"limit": 11}, {"on_date": "10/24"}):
        with pytest.raises(ValueError):
            search(db, "重訓", now=NOW, **kwargs)
    with pytest.raises(ValueError):
        search(db, "   ", now=NOW)


def test_every_search_envelope_within_budget(db):
    for q in ALL_QUERIES:
        for limit in (5, 10):
            e = search(db, q, limit=limit, now=NOW)
            assert approx_tokens(e) <= BUDGET and count_tokens(e) <= BUDGET, q
