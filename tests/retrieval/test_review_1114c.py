"""Plan 2026-11-14, the query's part after the review of that evening (research/pipeline_2026-11-02/v6_1114/
review_1114.md):

- respond: a merged recurring event's status is said from its instances' days (documents.series_days), not as one
  period from the first day to the last, which read 進行中 between two instances.
- search, RankConfig.topic_required: the topic is asked of a chunk's own words, not of its unit words, whose bigrams
  (工程 in 軟體工程與管理學系) would admit every page of that unit.
No model loads; the sentences are made up."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from nknu.content.documents import series_text
from nknu.retrieval import vectors
from nknu.retrieval.hybrid import off_audience
from nknu.retrieval.respond import _Date, _status
from tests.retrieval.test_hybrid import Tiny
from tests.retrieval.test_search import BASE, _ids
from tests.retrieval.test_topic_1114 import SOFTWARE, unit_doc

ON = dataclasses.replace(BASE, topic_required=True)
YEARLY = [(date(2024, 11, 9), date(2024, 11, 9)), (date(2025, 11, 8), date(2025, 11, 8)),
          (date(2026, 11, 7), date(2026, 11, 8))]
TEXT = series_text("校慶運動會", YEARLY)
SPAN = [_Date("event_start", "2024-11-09", "", "行事曆"), _Date("event_end", "2026-11-08", "", "行事曆")]
HEAD = "行事曆寫的日期 2024-11-09～2026-11-08（重複事件，共 3 次）："


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    t = Tiny(tmp_path / "t.db")
    t.units()
    yield t
    t.conn.close()


def test_between_two_instances_a_series_is_not_under_way():
    assert _status(SPAN, date(2025, 6, 1), TEXT) == HEAD + "上一次 2024-11-09，下一次 2025-11-08"
    assert "進行中" not in _status(SPAN, date(2025, 6, 1), TEXT)
    # as one period it had read 進行中, which a single event of those days still does
    assert _status(SPAN, date(2025, 6, 1)) == "行事曆寫的日期 2024-11-09～2026-11-08：進行中"
    assert _status(SPAN, date(2025, 6, 1), "校慶運動會") == "行事曆寫的日期 2024-11-09～2026-11-08：進行中"


def test_on_a_day_before_and_after():
    assert _status(SPAN, date(2025, 11, 8), TEXT) == HEAD + "今天有一次（2025-11-08），下一次 2026-11-07～2026-11-08"
    assert _status(SPAN, date(2026, 11, 8), TEXT) == HEAD + "今天有一次（2026-11-07～2026-11-08）"
    assert _status(SPAN, date(2024, 1, 1), TEXT) == HEAD + "尚未開始，第一次 2024-11-09"
    assert _status(SPAN, date(2027, 1, 1), TEXT) == HEAD + "已結束，最後一次 2026-11-07～2026-11-08"


def test_a_written_period_is_said_as_before_beside_it():
    dates = [_Date("period_start", "2026-11-01", "11/1～11/5", "正文期間"),
             _Date("period_end", "2026-11-05", "11/1～11/5", "正文期間"), *SPAN]
    assert _status(dates, date(2026, 11, 3), TEXT) == ("正文寫的期間 2026-11-01～2026-11-05：進行中；" + HEAD
                                                       + "上一次 2025-11-08，下一次 2026-11-07～2026-11-08")


def test_the_topic_is_not_asked_of_the_unit_words(db):
    unit_doc(db, "bus", "交通資訊", "搭乘公車路線", unit=SOFTWARE)  # the unit's page on something else
    unit_doc(db, "map", "系館平面圖", "各樓層教室位置", unit=SOFTWARE)
    unit_doc(db, "ethics", "工程倫理課程", "工程倫理為必修", unit=SOFTWARE)
    db.doc("school", "工程倫理講座", "全校工程倫理講座")
    for q in ("軟體系 工程倫理", "115-1 工程倫理"):
        on = db.search(q, rank=ON)
        assert set(_ids(on)) == {"ethics", "school"}, q  # 工程 in the unit's name brings no page of the unit
    assert {"bus", "map"} <= set(_ids(db.search("軟體系 工程倫理")))  # off: the unit's name brings them, as before


def test_a_text_several_departments_hold_is_none_of_theirs_to_lose(db):
    # the build gives a document kept under one department the other departments whose sites hold a copy
    # (build.unit_scope, Document.also_units): naming one of those is naming its own
    db.doc("shared", "畢業門檻", "學生須修畢 128 學分")
    db.scope("shared", ["軟體工程與管理學系", "化學系"], publisher="軟體工程與管理學系")
    assert off_audience(db.conn, "化學系 畢業門檻", ["shared"]) == set()
    assert off_audience(db.conn, "軟體系 畢業門檻", ["shared"]) == set()
    assert off_audience(db.conn, "國文學系 畢業門檻", ["shared"]) == {"shared"}  # a department that has no copy
    assert off_audience(db.conn, "畢業門檻", ["shared"]) == {"shared"}  # no department named: a department's own
