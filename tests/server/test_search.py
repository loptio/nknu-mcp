import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from nknu.server.envelope import NOTICE_LABEL
from nknu.server.linking import detect_intent, expand, link_entities, link_query
from nknu.server.query import CAVEATS, CURRENT_DOC_SQL, current_doc_params, get_hours
from nknu.server.search import CAVEAT_OLD_NOTICES, CAVEAT_WEAK, T_HIGH, T_LOW, search
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import CONFLICT_DAY, GYM, MIDTERM_UID, NOW, OLD_WITHDRAWAL, OLD_XUECE, WITHDRAWAL_UID, WR

YWR = "nknu.yanchao.gym.weight-room"
HLIB, YLIB = "nknu.heping.library.main", "nknu.yanchao.library.main"
SEARCH_DIR = Path(__file__).parents[1] / "search"
ALL_QUERIES = [q["q"] for name in ("queries.yaml", "calibration.yaml")
               for q in yaml.safe_load((SEARCH_DIR / name).read_text())]


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


def test_2022_doc_never_returned_as_current(db):
    """C5: a notice over a year old is never current; search returns one only when nothing current matches, labelled
    with its date, and the envelope is then stale and found=false."""
    envs = [search(db, q, limit=10, now=NOW) for q in ALL_QUERIES]
    assert any(d.get("label") == NOTICE_LABEL for e in envs for d in e.get("facts", []))
    for e in envs:
        for d in e.get("facts", []):
            if d.get("label") and (d.get("published_on") or "") < "2025-10-22":
                assert d["label"] != NOTICE_LABEL and d["published_on"] in d["label"] and "可能已變動" in d["label"], d
                assert e["status"] == "stale" and e["found"] is False and CAVEAT_OLD_NOTICES in e["caveat"], e


def _current(rows: list[tuple], today: date) -> list[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE doc(title, published_on, announce_until, withdrawn_at)")
    conn.executemany("INSERT INTO doc VALUES (?, ?, ?, ?)", rows)
    return [r[0] for r in conn.execute(f"SELECT title FROM doc WHERE {CURRENT_DOC_SQL} ORDER BY title",
                                       current_doc_params(today))]


def test_current_notice_rule():
    """C5: inside its posting period AND published within 365 days; with no period, published within 180 days."""
    rows = [("a far end date, 2017", "2017-01-19", "2030-12-31", None),
            ("b far end date, 366 days", "2025-10-21", "2030-12-31", None),
            ("c far end date, 365 days", "2025-10-22", "2030-12-31", None),
            ("d period over", "2026-10-01", "2026-10-21", None),
            ("e no period, 180 days", "2026-04-25", None, None),
            ("f no period, 181 days", "2026-04-24", None, None),
            ("g withdrawn", "2026-10-20", "2026-11-30", "2026-10-21T00:00:00Z"),
            ("h undated, far end date", None, "2030-12-31", None)]
    assert _current(rows, date(2026, 10, 22)) == ["c far end date, 365 days", "e no period, 180 days"]


def test_old_notice_answers_only_as_an_old_notice(world_old_notices):
    """F-stale-sso-notices: 學測 matches only a 2017 notice whose SSO end date is 2030-12-31."""
    e = search(world_old_notices, "學測", now=NOW)
    assert e["status"] == "stale" and e["found"] is False and CAVEAT_OLD_NOTICES in e["caveat"]
    top = e["facts"][0]
    assert top["title"] == OLD_XUECE and top["published_on"] == "2017-01-19"
    assert top["label"] == "舊公告（2017-01-19 發布），內容可能已變動" and NOTICE_LABEL not in top["label"]


def test_old_notice_never_beside_current_results(world_old_notices):
    e = search(world_old_notices, "停修", limit=10, now=NOW)
    assert e["found"] is True and e["status"] == "current" and "舊公告" not in (e.get("caveat") or "")
    assert not any(f.get("title") == OLD_WITHDRAWAL for f in e["facts"])
    assert any(f.get("label") == NOTICE_LABEL for f in e["facts"])


def test_old_notices_only_for_notice_questions(world_old_notices):
    """A years-old notice states past hours and dates: it never answers an hours or calendar question."""
    for q, kind in (("學測", "hours"), ("學測", "calendar"), ("學測 開放時間", "any")):
        e = search(world_old_notices, q, kind=kind, now=NOW)
        assert e["found"] is False and e["status"] == "no_data" and "facts" not in e, (q, kind)
    e = search(world_old_notices, "學測 考試 日期", now=NOW)  # a calendar question: current events or nothing
    assert not any("舊公告" in f.get("label", "") for f in e.get("facts", [])) and e["status"] != "stale"
    assert search(world_old_notices, "學測", kind="announcement", now=NOW)["facts"][0]["title"] == OLD_XUECE


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


def test_detect_intent_close_words():
    """F-close-words-intent: asking whether a place is closed asks for its hours."""
    for q in ("體育館關了嗎", "圖書館今天休息嗎", "What time does the library close today?", "is the gym closed",
              "体育馆关门了吗", "游泳池休館嗎", "圖書館今天可以去嗎", "library closing time"):
        assert detect_intent(q) == "hours", q
    assert detect_intent("圖書館相關公告") == "doc"  # 相關 is not 關門
    assert detect_intent("停修可以去哪裡申請") == "calendar"  # where to go, not whether it is open


def test_link_school_and_unit_names(db):
    """F-school-name-topic: the university and its units are named, not asked about: link_query blanks them out of
    rest (as it does places) and lists them in others; a unit is any office a notice comes from (教務處和平教務組 gives
    教務處 and 教務組)."""
    linked = link_query(db.conn(), "高雄師大教務組停修")
    assert linked.ids == [] and linked.schools == ["高雄师大"] and linked.units == ["教务组"]
    assert linked.rest.split() == ["停修"]
    linked = link_query(db.conn(), "NKNU 體育室 體育館")
    assert linked.ids == [GYM] and linked.schools == ["nknu"] and linked.units == ["体育室"] and not linked.rest.strip()


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


def test_tentative_event_found_by_search_is_labelled(world_calendar_status):
    e = search(world_calendar_status, "期中考試", kind="calendar", now=NOW)
    hit = [f for f in e["facts"] if f.get("id") == MIDTERM_UID]
    assert hit and hit[0]["status"] == "tentative" and "暫定" in e["caveat"]


def test_cancelled_event_not_found_by_search(world_calendar_status):
    e = search(world_calendar_status, "申辦停修", kind="calendar", now=NOW)
    assert not any(f.get("id") == WITHDRAWAL_UID for f in e.get("facts", []))


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


def test_stale_entity_match_gives_last_known_hours(db):
    e = search(db, "和平重訓室 開放時間", on_date="2027-02-01", now=NOW)
    assert e["status"] == "stale" and e["found"] is False and e["last_known"]["valid"] == ["2026-09-07", "2027-01-08"]
    assert "19:00–21:00" in e["answer"] and "可能已變動" in e["answer"] and e["caveat"] == CAVEATS["stale"]


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


# ---------------------------------------------------------------- what coverage counts

OFF_TOPIC = [
    "學餐開放時間", "影印店開放時間", "停車場 開放時間", "宿舍 開放時間", "籃球場 開放時間",  # unknown place + an hours question
    "圖書館 借書", "健身房 收費", "燕巢圖書館 地址", "圖書館 館長是誰", "體育館 租借", "重量訓練室 教練",  # place + unsupported word
]


@pytest.mark.parametrize("q", OFF_TOPIC)
def test_off_topic_query_is_not_found(db, q):
    """開放/時間 are in every hours card and a place name is in its own card: neither says the question is answered."""
    e = search(db, q, now=NOW)
    assert e["found"] is False and e["status"] == "no_data" and "facts" not in e, e.get("facts")


@pytest.mark.parametrize("q, kwargs", [("燕巢 游泳池 開放時間", {}), ("游泳池 開放時間", {"campus": "yanchao"}),
                                       ("燕巢 游泳池", {}), ("游泳池", {"campus": "yanchao", "kind": "place"})])
def test_place_not_on_that_campus(db, q, kwargs):
    e = search(db, q, now=NOW, **kwargs)
    assert e["found"] is False and e["status"] == "no_data" and "facts" not in e
    assert e["caveat"] == "燕巢校區查無此地點，請勿推測" and e["similar"] == ["和平校區游泳池"]


def test_place_questions_the_card_answers_still_found(db):
    e = search(db, "游泳池 整修", now=NOW)  # 整修 is in the pool's card
    assert e["found"] is True and e["match"] == "text" and e["facts"][0]["entity_id"] == "nknu.heping.gym.pool"
    e = search(db, "I-Read Space 週末", now=NOW)  # a place plus when: an hours question
    assert e["match"] == "ambiguous" and {f["entity_id"] for f in e["facts"]} == {
        "nknu.heping.library.iread", "nknu.yanchao.library.iread"}
    e = search(db, "燕巢 桌球室", now=NOW)  # nothing but a campus and a place
    assert e["match"] == "entity" and e["facts"][0]["entity_id"] == "nknu.yanchao.gym.table-tennis"


def test_place_with_a_notice_word_lists_its_notices(db):
    """公告 says what kind of answer is wanted, like 開放時間: the place is the topic, and the answer is not its hours."""
    e = search(db, "圖書館 公告", kind="announcement", now=NOW)
    assert e["facts"][0]["unit"].startswith("圖書資訊處")
    assert search(db, "體育館 公告", now=NOW).get("match") != "entity"  # not the gym's hours as the answer


def test_named_place_outranks_a_fact_that_does_not_name_it(db):
    e = search(db, "臺灣光復節 圖書館", now=NOW)  # holiday != closure: the library's own closure card comes first
    assert e["facts"][0]["entity_id"] in (HLIB, YLIB) and "暫停開放" in e["facts"][0]["answer"]
    assert not any("PHP" in f.get("title", "") for f in e["facts"])  # a 圖資處 notice that only shares the alias


def test_close_words_answer_with_the_place_hours(db):
    """F-close-words-intent: 關了嗎 / 休息 / close ask for the hours, so the place's card answers with the coming
    closure in next_change (the gym closes 10-24..10-26), never 查無."""
    e = search(db, "體育館關了嗎", now=NOW)
    assert e["match"] == "entity" and e["facts"][0]["entity_id"] == GYM
    assert "暫停開放（臺灣光復紀念日連假）" in e["next_change"]["answer"]
    for q in ("圖書館今天休息嗎", "What time does the library close today?"):
        e = search(db, q, now=NOW)
        assert e["found"] is True and e["match"] == "ambiguous", q
        assert {f["entity_id"] for f in e["facts"]} == {HLIB, YLIB}, q


@pytest.mark.parametrize("joined, spaced", [("停修截止", "停修 截止"), ("颱風停課", "颱風 停課"), ("退課截止", "退課 截止")])
def test_joined_words_read_like_spaced_ones(db, joined, spaced):
    """F-short-calendar-queries: 修截 in 停修截止 only joins two words. It is in no hit while both its neighbours are,
    so the coverage leaves it out and the question is answered as 停修 截止 is. An alias is found when its canonical
    term is (退課 by 停修)."""
    a, b = search(db, joined, now=NOW), search(db, spaced, now=NOW)
    assert a["found"] is True and (a["match"], a["facts"]) == (b["match"], b["facts"])


def test_a_word_absent_from_every_hit_still_counts(db):
    """Only a bigram between two found ones is a joint: 學餐 (no hit has it) still counts against 學餐菜單."""
    assert search(db, "學餐菜單", now=NOW)["found"] is False


def test_this_or_next_term_only_says_when(db):
    e = search(db, "下學期什麼時候開學", now=NOW)
    assert e["found"] is True and e["facts"][0]["category"] == "term_begin"
    e = search(db, "下學期什麼時候開學？加退選到哪一天？", now=NOW)
    assert e["found"] is True and e["facts"][0]["category"] == "term_begin"


def test_equally_covered_events_soonest_first(db):
    """F-calendar-order: the midterm week (10-27..10-30) comes before the one-day 11-02 entry and the 11-09 deadline."""
    e = search(db, "期中考是什麼時候", now=NOW)
    events = [f for f in e["facts"] if "summary" in f]
    assert events[0]["valid"] == ["2026-10-27", "2026-10-30"]
    assert [f["valid"][0] for f in events] == sorted(f["valid"][0] for f in events)


def test_school_and_unit_names_are_not_the_topic(db):
    """F-school-name-topic: every notice is the school's, so 高雄師範大學 says nothing about what is asked, not even when
    nothing else is left."""
    assert search(db, "高雄師範大學 停修", now=NOW)["facts"] == search(db, "停修", now=NOW)["facts"]
    for q in ("高師大有哪些餐廳，幾點開", "致理大樓 教務組", "NKNU 學餐", "高師大", "NKNU"):
        e = search(db, q, now=NOW)
        assert e["found"] is False and e["status"] == "no_data", (q, e.get("facts"))


def test_a_unit_named_ranks_its_own_facts_first(db):
    """A unit is not the topic, but like a place it says whose facts are meant: 圖資處 期中考 is the libraries' midterm
    extension before the midterm itself; a unit alone is searched for (its notices)."""
    e = search(db, "圖資處 期中考", now=NOW)
    assert e["facts"][0]["entity_id"] in (HLIB, YLIB) and "延長開館" in e["facts"][0]["answer"]
    e = search(db, "體育室", kind="announcement", now=NOW)
    assert e["found"] is True and all(f["unit"] == "體育室" for f in e["facts"])


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
    assert g["facts"] == e["facts"]


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


def test_search_keeps_titles_and_sentences_whole(db):
    """U1: notice titles and card sentences are served as stored (no 40-character titles, no stripped 附註 or 來源)."""
    conn = db.conn()
    titles = {r[0]: r[1] for r in conn.execute("SELECT url, title FROM doc")}
    cards = {r[0] for r in conn.execute("SELECT answer_zh FROM answer_card")}
    for q in ALL_QUERIES:
        e = search(db, q, limit=10, now=NOW)
        for f in e.get("facts", []):
            if f.get("label"):
                assert f["title"] == titles[f["url"]], q
            if f.get("answer"):
                assert f["answer"] in cards, q
            if isinstance(f.get("next_change"), dict):
                assert f["next_change"]["answer"] in cards, q
