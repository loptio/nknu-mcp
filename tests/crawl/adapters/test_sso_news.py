import re
from datetime import date

import pytest

from nknu.core.pii import find_pii
from nknu.core.records import DocRecord
from nknu.crawl.adapter import ParseError
from nknu.crawl.adapters.sso_news import BASE, CATEGORIES, DETAIL_META, SsoNews, detail_url, list_meta, next_page_target
from nknu.crawl.webforms import postback
from tests.crawl.adapters.helpers import NO_HISTORY, read, snap

A = SsoNews()
URL = f"{BASE}?cat=SCHOOLADMIN"


def docs(recs):
    return [r for r in recs if isinstance(r, DocRecord)]


@pytest.fixture
def p1():
    return docs(A.parse(snap(URL, read("sso_news", "list_p1.html"), meta=list_meta("SCHOOLADMIN"))))


@pytest.fixture
def p2():
    return docs(A.parse(snap(URL, read("sso_news", "list_p2.html"), meta=list_meta("SCHOOLADMIN"))))


@pytest.fixture
def good():
    return docs(A.parse(snap(f"{BASE}?cat=GOOD", read("sso_news", "list_good.html"), meta=list_meta("GOOD"))))


def test_seven_categories():
    assert CATEGORIES == ["SCHOOLADMIN", "ENTRANCEEXAM", "CAMPUS", "Lights", "LEARNING", "GOOD", "JOB"]


def test_list_rows_parsed(p1):
    assert len(p1) == 10 and all(re.fullmatch(r"[0-9A-F]{40}", d.doc_key) and d.key == f"sso_news:{d.doc_key}" for d in p1)
    d = next(x for x in p1 if x.doc_key.startswith("3E3FDDE2"))
    assert d.unit == "教務處和平教務組" and d.category == "行政資訊" and "停修" in d.title
    assert (d.published_on, d.announce_until) == (date(2026, 10, 21), date(2026, 11, 16))
    assert d.url == detail_url(d.doc_key)


def test_pages_do_not_overlap(p1, p2):
    assert not {d.key for d in p1} & {d.key for d in p2}


def test_dates_parsed(p1, p2):
    assert all(d.published_on and d.announce_until for d in p1 + p2)


def test_good_titles_masked_no_excerpt(good):
    assert len(good) == 10 and all(d.excerpt is None and d.pii_level == "personal" for d in good)
    t = read("sso_news", "list_good.html")
    assert all(d.title in t for d in good)  # fixture is already masked; masking again changes nothing


def test_honor_title_masked_in_any_category():
    # F-sso-crosspost-honor: the 恭賀 post cross-posted under 徵才就業 is masked and gets no excerpt, like GOOD
    old = "【圖資處重要通知】校園資安提醒 PHP緊急安全更新"
    html = read("sso_news", "list_p1.html").replace(old, "【恭賀】本所研究生謝乙丙考取苗栗縣115學年度國民中學正式教師")
    recs = docs(A.parse(snap(f"{BASE}?cat=JOB", html, meta=list_meta("JOB"))))
    d = next(x for x in recs if x.doc_key.startswith("63BBE83D"))
    assert d.title == "【恭賀】本所研究生謝O丙考取苗栗縣115學年度國民中學正式教師" and d.pii_level == "personal"
    assert all(x.pii_level == "none" for x in recs if x is not d)


def test_honor_rows_get_no_detail_page():
    old = "【圖資處重要通知】校園資安提醒 PHP緊急安全更新"
    html = read("sso_news", "list_p1.html").replace(old, "賀 本系學生參加競賽 榮獲金牌")
    recs = docs(A.parse(snap(f"{BASE}?cat=CAMPUS", html, meta=list_meta("CAMPUS"))))
    assert next(x for x in recs if x.doc_key.startswith("63BBE83D")).pii_level == "personal"


def test_detail_partial_excerpt():
    d = docs(A.parse(snap(detail_url("3E3FDDE24A008A28322CF9E129D050890FBE26AC"), read("sso_news", "detail.html"),
                          meta=DETAIL_META)))[0]
    assert d.partial and 0 < len(d.excerpt) <= 200 and d.excerpt.startswith("主旨")
    assert d.key == "sso_news:3E3FDDE24A008A28322CF9E129D050890FBE26AC"


def test_detail_404_gone():
    d = docs(A.parse(snap(detail_url("A" * 40), "", status=404, meta=DETAIL_META)))[0]
    assert d.gone


def test_postback_fields():
    _, fields = postback(read("sso_news", "list_p1.html"), URL, "gvFull", "Page$2")
    assert ("__EVENTARGUMENT", "Page$2") in fields and any(k == "__VIEWSTATE" for k, _ in fields)


def test_next_page_is_sequential():
    t = next_page_target(read("sso_news", "list_p1.html"), URL, "SCHOOLADMIN", 1)
    assert t.method == "POST" and ("__EVENTARGUMENT", "Page$2") in t.data and t.meta["page"] == 2
    assert next_page_target(read("sso_news", "list_p2.html"), URL, "SCHOOLADMIN", 2).meta["page"] == 3


def test_last_page_has_no_next():
    html = re.sub(r"__doPostBack\(&#39;gvFull&#39;,&#39;Page\$\d+&#39;\)", "", read("sso_news", "list_p1.html"))
    html = re.sub(r"__doPostBack\('gvFull','Page\$\d+'\)", "", html)
    assert next_page_target(html, URL, "SCHOOLADMIN", 1) is None


def test_row_without_unid_is_layout_change():
    html = re.sub(r"UNID=[0-9A-F]{40}", "UNID=", read("sso_news", "list_p1.html"))
    with pytest.raises(ParseError):
        A.parse(snap(URL, html, meta=list_meta("SCHOOLADMIN")))


def test_renamed_row_fields_void_the_run():
    # F-sso-row-fields-null: the grid and the title links stay, only the date/unit/category cells are renamed; every
    # row would lose its date and drop out of search, so the run is void and the stored rows keep their values
    html = read("sso_news", "list_p1.html")
    for old, new in (("_StartDate", "_PostDate"), ("_EndDate", "_StopDate"), ("_DeptName", "_Unit"), ("_Category", "_Kind")):
        html = html.replace(old, new)
    s = snap(URL, html, meta=list_meta("SCHOOLADMIN"))
    recs = A.parse(s)
    assert len(docs(recs)) == 10 and all(d.published_on is None and d.unit is None for d in docs(recs))
    assert [v.code for v in A.invariants(recs, NO_HISTORY, [s])] == ["date_parse_rate"]


def test_maintenance_page_fails_invariants():
    s = snap(URL, read("sso_news", "maintenance.html"), meta=list_meta("SCHOOLADMIN"))
    recs = A.parse(s)
    assert recs == [] and any(v.code == "selector_missing" for v in A.invariants(recs, NO_HISTORY, [s]))


@pytest.mark.parametrize("name", ["list_p1.html", "list_p2.html", "list_good.html", "detail.html"])
def test_fixture_has_no_pii(name):
    assert find_pii(read("sso_news", name)) == []
