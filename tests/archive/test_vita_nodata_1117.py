"""11-17 (爬虫judge under the owner's delegation, to the crawler session's proposal: 「查無資料」的改法同意，照三做): a teacher's
CV page the school answers 「查無資料」 with no card (the daytime run of round two: 13 pages, all old links of unit staff
pages, the teachers gone or no longer public) is the school's answer of no data, as the course tables' 「系所未提供相關資
料」: the export says 「学校回答没有资料（查無資料）」 in 缺什么 and the content stage leaves the row out. Judge's term:
「以前好、现在说没有」 needs two such answers at least 24 hours apart (watch.GONE_AGAIN_S, the rule of 学校已删除); till
then the row is written with its last good answer and says nothing. A page never good is marked at once. Made-up pages,
O-masked names."""

from __future__ import annotations

from nknu.archive import export, profiles, watch
from tests.archive.test_export_prev_1116 import _again
from tests.archive.test_export_step3_1108 import _with
from tests.archive.test_vita_tabs_1117 import _page

CV = "https://sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=7"
EMPTY = ("<html><head><title>教師資料</title></head><body><form id=\"uForm\" method=\"post\" action=\"vita.aspx\">"
         "<div class=\"container\">系統訊息： 是 確 定</div><span>查無資料</span></form></body></html>")
WORDS = "学校回答没有资料（查無資料）"
T0 = "2026-11-16T09:07:15+00:00"


def test_the_rule_reads_an_answer_with_no_card_that_says_no_data():
    assert profiles.school_no_data(CV, EMPTY).words == WORDS
    # a tab with no rows says 查無資料 inside the card: the teacher is there
    assert profiles.school_no_data(CV, _page().replace("教學內容一則", "查無資料")) is None
    assert profiles.school_no_data(CV, _page()) is None
    assert profiles.school_no_data("https://sso.nknu.edu.tw/dataopen2search/lostandfound.aspx", EMPTY) is None


def _cv(arch, body: str, **cols) -> None:
    arch.page("cv", CV, body, host="sso.nknu.edu.tw", **cols)


def _later(t: str, hours: float) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(t) + timedelta(hours=hours)).isoformat()


def test_a_cv_page_never_good_that_says_no_data_is_marked_at_once(tmp_path):
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda a: _cv(a, EMPTY, flags="no_area", text_chars=48))
    assert WORDS in rows[CV]["缺什么"]


def test_a_good_cv_page_that_says_no_data_once_keeps_its_last_good_answer_and_says_nothing(tmp_path):
    def build(arch):
        _cv(arch, _page(), text_chars=600)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=T0)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[CV]
    assert r["来源"] == export.ORIGIN_PREV and WORDS not in r["缺什么"]


def test_a_good_cv_page_that_says_no_data_twice_within_a_day_still_keeps_it(tmp_path):
    def build(arch):
        _cv(arch, _page(), text_chars=600)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=T0)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=_later(T0, 17))

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert WORDS not in rows[CV]["缺什么"]


def test_a_good_cv_page_that_says_no_data_a_day_apart_is_marked(tmp_path):
    assert watch.GONE_AGAIN_S <= 24 * 3600

    def build(arch):
        _cv(arch, _page(), text_chars=600)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=T0)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=_later(T0, 25))

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[CV]
    assert WORDS in r["缺什么"]
    assert r["来源"] == export.ORIGIN_PREV  # its file stays the last good one, as a deleted row's does


def test_a_good_answer_between_resets_the_day(tmp_path):
    def build(arch):
        _cv(arch, _page(), text_chars=600)
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=T0)
        _again(arch, "cv", body=_page(), text_chars=600, fetched_at=_later(T0, 10))
        _again(arch, "cv", body=EMPTY, flags="no_area", text_chars=48, fetched_at=_later(T0, 30))

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert WORDS not in rows[CV]["缺什么"]
