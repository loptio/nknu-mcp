from dataclasses import replace
from datetime import date

from nknu.core.records import DocRecord, ParseIssue
from nknu.crawl.invariants import date_parse_rate, selectors_present
from tests.crawl.adapters.helpers import snap
from tests.factories import DOC

URL = "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"


def test_a_200_without_a_page_fails_the_selector_check():
    # an empty body or a non-HTML answer with status 200 is not a page the adapter can read: void, keep old values
    assert [v.code for v in selectors_present([snap(URL, "")], ["#lbl_Contact"])] == ["selector_missing"]
    assert [v.code for v in selectors_present([snap(URL, None, body=b"%PDF")], ["#lbl_Contact"])] == ["selector_missing"]


def test_selector_check_skips_what_it_cannot_judge():
    assert selectors_present([snap(URL, "<html><div id='lbl_Contact'></div></html>")], ["#lbl_Contact"]) == []
    assert selectors_present([snap(URL, "", status=404)], ["#lbl_Contact"]) == []
    assert selectors_present([snap(URL, None, body=b"BEGIN:VCALENDAR")], ["#x"], pages_are_html=False) == []
    assert selectors_present([snap(URL, "")], []) == []


def _docs(n_dated, n_undated):
    dated = [replace(DOC, key=f"sso_news:D{i}", doc_key=f"D{i}") for i in range(n_dated)]
    undated = [replace(DOC, key=f"sso_news:U{i}", doc_key=f"U{i}", published_on=None) for i in range(n_undated)]
    return dated + undated


def test_rows_without_a_date_fail_the_date_rate():
    # F-sso-row-fields-null: a list whose date cells moved yields rows with no date at all; that is a layout change,
    # not 735 announcements that lost their dates
    assert [v.code for v in date_parse_rate(_docs(0, 30))] == ["date_parse_rate"]
    assert date_parse_rate(_docs(97, 3)) == []
    assert date_parse_rate(_docs(90, 10))[0].message == "dates parsed 90/100"


def test_an_undated_row_with_its_own_parse_issue_counts_once():
    recs = _docs(18, 1) + [ParseIssue("sso_news:U0", "date", "公告日期無法解析")]
    assert date_parse_rate(recs)[0].message == "dates parsed 18/19"


def test_detail_and_gone_records_are_not_rows():
    detail = DocRecord("sso_news:D0", "D0", "", DOC.url, partial=True, excerpt="內文")
    gone = DocRecord("sso_news:X", "X", "", DOC.url, gone=True)
    assert date_parse_rate(_docs(10, 0) + [detail, gone]) == []
    assert date_parse_rate([replace(DOC, published_on=date(2026, 10, 20))]) == []
