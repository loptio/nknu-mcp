from datetime import date

import pytest

from nknu.core.pii import find_pii
from nknu.core.records import DocRecord, FactRecord, ParseIssue
from nknu.crawl.adapters.pe_news import (
    DETAIL_META,
    LIST_META,
    LIST_URL,
    PeNews,
    closure_ranges,
    detail_url,
    needs_detail,
    page_targets,
)
from tests.crawl.adapters.helpers import NO_HISTORY, read, snap

A = PeNews()


def detail(nid, name=None, html=None, status=200):
    return snap(detail_url(nid), html if html is not None else read("pe_news", name), status=status, meta=DETAIL_META)


def facts(recs):
    return [r for r in recs if isinstance(r, FactRecord)]


def docs(recs):
    return [r for r in recs if isinstance(r, DocRecord)]


def test_list_rows_parsed():
    d = docs(A.parse(snap(LIST_URL, read("pe_news", "list.html"), meta=LIST_META)))
    assert len(d) == 30 and all(not x.partial and x.key == f"pe_news:{x.doc_key}" and x.unit == "體育室" for x in d)
    first = next(x for x in d if x.doc_key == "882")
    assert first.published_on == date(2026, 10, 2) and first.category == "一般公告" and first.url == detail_url("882")


def test_honor_list_rows_masked_and_personal():
    d = docs(A.parse(snap(LIST_URL, read("pe_news", "list.html"), meta=LIST_META)))
    honor = [x for x in d if "名單" in x.title]
    assert honor and all(x.pii_level == "personal" and x.excerpt is None for x in honor)
    assert not needs_detail(honor[0].title, honor[0].key, set(), True)


def test_needs_detail_policy():
    # third argument: announcements whose detail page is already stored (F-excerpt-backlog: not "keys seen before")
    assert needs_detail("115學年度第1學期 共同體育課表", "pe_news:854", set(), False)
    assert not needs_detail("115學年度第1學期 共同體育課表", "pe_news:854", {"pe_news:854"}, True)
    assert needs_detail("本校體育館於10月24至26日暫停開放", "pe_news:460", {"pe_news:460"}, True)
    assert not needs_detail("本校體育館於10月24至26日暫停開放", "pe_news:460", {"pe_news:460"}, False)
    # a page that answered 404 (or was taken down with nknu undo) is not asked for again
    assert not needs_detail("本校體育館於10月24至26日暫停開放", "pe_news:460", set(), True, down={"pe_news:460"})


def test_failed_detail_page_is_an_issue_not_silence():
    # F-detail-void-freeze: the run is not voided for one detail page, so the adapter has to say what it lost
    from nknu.crawl.adapter import Target
    from nknu.crawl.fetch import failed_snapshot

    assert [r.code for r in A.parse(detail("999", "maintenance.html"))] == ["detail"]
    assert [r.code for r in A.parse(detail("999", html=""))] == ["detail"]
    lost = A.parse(failed_snapshot(Target(detail_url("999"), meta=DETAIL_META), "HTTP 503"))
    assert [(r.key, r.code) for r in lost] == [("pe_news:999", "detail")] and "HTTP 503" in lost[0].message


def test_closure_facts():
    f = facts(A.parse(detail("460", "closure.html")))
    assert {x.entity_id for x in f} == {"nknu.heping.gym.main", "nknu.heping.gym.outdoor"}
    gym = sorted((x for x in f if x.entity_id == "nknu.heping.gym.main"), key=lambda x: x.valid_from)
    assert all(x.propagates for x in gym) and not any(x.propagates for x in f if x.entity_id.endswith("outdoor"))
    assert (gym[0].valid_from, gym[0].valid_to) == (date(2026, 10, 24), date(2026, 10, 27))
    assert gym[0].value == {"closed": True, "reason": "臺灣光復紀念日連假"} and len(gym) == 14
    assert gym[0].specificity == 2 and gym[0].source.kind == "announcement" and gym[0].source.published_on == date(2025, 12, 26)


def test_old_2022_news_is_doc_only():
    recs = A.parse(detail("800", "old_2022.html"))
    assert not facts(recs) and docs(recs)[0].partial and "韻律A室" in docs(recs)[0].excerpt


def test_suspended_booking_is_not_closure():
    assert not facts(A.parse(detail("868", "suspend_booking.html")))


def test_year_inferred_from_published_on():
    assert closure_ranges("體育館於11/3~11/16暫停開放", date(2026, 10, 20))[0][:2] == (date(2026, 11, 3), date(2026, 11, 17))
    assert closure_ranges("1/5(二)暫停開放", date(2026, 12, 20))[0][:2] == (date(2027, 1, 5), date(2027, 1, 6))


def test_dash_range_without_weekdays():
    # F-rocdate-dash: 「10/24-10/26暫停開放」 is a closure, not 民國10年 (which used to become a parse issue)
    assert closure_ranges("體育館10/24-10/26暫停開放", date(2026, 10, 20)) == [(date(2026, 10, 24), date(2026, 10, 27),
                                                                              "公告暫停開放")]
    assert closure_ranges("重量訓練室10/2-10/6暫停開放", date(2026, 9, 25))[0][:2] == (date(2026, 10, 2), date(2026, 10, 7))


def test_weekday_mismatch_is_issue():
    html = read("pe_news", "closure.html").replace("115年10月24日（週六）", "115年10月24日（週五）", 1)
    recs = A.parse(detail("460", html=html))
    assert any(isinstance(r, ParseIssue) and r.code == "date" for r in recs)
    assert date(2026, 10, 24) not in {x.valid_from for x in facts(recs)}


def test_excerpt_is_verbatim_le_200():
    d = docs(A.parse(detail("460", "closure.html")))[0]
    assert d.partial and 0 < len(d.excerpt) <= 200 and d.excerpt.startswith("本校體育館(含室外運動場地)")
    assert any(a.endswith(".pdf") for a in d.attachments)


def test_404_marks_gone():
    d = docs(A.parse(detail("999", html="", status=404)))[0]
    assert d.gone and d.key == "pe_news:999"


def test_page_targets_postback():
    t = page_targets(read("pe_news", "list.html"), LIST_URL)
    assert [x.data[[k for k, _ in x.data].index("__EVENTARGUMENT")][1] for x in t] == ["Page$2", "Page$3", "Page$4"]
    assert all(x.method == "POST" and ("__EVENTTARGET", "MasterGrid") in x.data for x in t)


def test_maintenance_page_fails_invariants():
    s = snap(LIST_URL, read("pe_news", "maintenance.html"), meta=LIST_META)
    recs = A.parse(s)
    assert recs == [] and any(v.code == "selector_missing" for v in A.invariants(recs, NO_HISTORY, [s]))


@pytest.mark.parametrize("name", ["list.html", "closure.html", "old_2022.html", "suspend_booking.html"])
def test_fixture_has_no_pii(name):
    assert find_pii(read("pe_news", name)) == []
