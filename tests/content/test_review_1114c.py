"""Plan 2026-11-14, the build's part after the review of that evening (research/pipeline_2026-11-02/v6_1114/
review_1114.md, checked against the first 111-site rebuild):

- T2, which copy of one text is kept: first by whose copy it is (build._owner_level: an office's before a
  department's before the school's shared pages), then the earliest as before. Kept by date alone, 251 texts an office
  has on its own site stood under a department whose copy carried an earlier date.
- T6, docs.expires: a title that names its year expires whether the document has a date or not (758 undated files
  never did); a rule that notes a meeting's year after its fixed word does not.
- T4: a line of the school's and the unit's names alone is no title.
- documents.series_days reads series_text back (respond states a merged recurring event's status from it).
The sentences and names are made up; no model loads."""

from __future__ import annotations

from datetime import date

from nknu.content import build, sites
from nknu.content.documents import SERIES_NOTE, build_documents, series_days, series_text, title_flaw
from nknu.content.terms import first_term_at
from nknu.content.units import Unit
from tests.content import test_copies_1114 as tc
from tests.content import test_validity_1114 as tv

SCHOOL = "國立高雄師範大學"


def unit(name: str, kind: str, parent: str | None) -> Unit:
    return Unit(name, name, kind, parent, None, ())


OFFICE = sites.unit_of_site(tc.WEB_SITE)  # a section's site
DEPT = sites.unit_of_site(tc.CHAIR_SITE)  # a department's
PLATFORM = sites.unit_of_site(tc.NEWS_SITE)  # the announcement platform's placeholder
UNITS = {u.unit_id: u for u in (unit(SCHOOL, "学校", None), unit("學生事務處", "行政单位", SCHOOL),
                                unit(OFFICE, "行政组别", "學生事務處"), unit("科技學院", "学院", SCHOOL),
                                unit(DEPT, "系所", "科技學院"), unit(PLATFORM, "行政单位", SCHOOL))}


def kept(records, units=UNITS):
    docs, excluded = build_documents(records, {}, [])
    rows = build._rows(docs, {}, {})
    left, copies = build._merge_copies(rows, lambda _: None, excluded, build.COPY_TEXT, True, units)
    return [r.doc.doc_id for r in left], {k: [c.doc.doc_id for c in v] for k, v in copies.items()}


def test_the_three_sites_are_what_the_test_takes_them_for():
    assert len({OFFICE, DEPT, PLATFORM}) == 3 and sites.table().rows[tc.NEWS_SITE].shared


def test_an_offices_copy_is_kept_before_a_departments_earlier_one():
    # two notices (a text is one document within one kind): the department's is dated earlier
    dept = tc.rec("p1", "detail:sso_clv2", tc.CHAIR + "1", "轉知", tc.LONG, tc.CHAIR_SITE, "2026-02-01")
    office = tc.rec("p2", "detail:sso_webidx", tc.WEB + "1", "公告", tc.LONG, tc.WEB_SITE, "2026-05-01")
    assert kept([dept, office]) == (["dp2"], {"dp2": ["dp1"]})
    assert kept([dept, office], units=None) == (["dp1"], {"dp1": ["dp2"]})  # without the unit tree: by date, as before


def test_a_departments_copy_is_kept_before_the_platforms_own_export():
    posted = tc.rec("p1", "detail:sso_news", tc.NEWS + "AB12", "轉知", tc.LONG, tc.NEWS_SITE, "2026-02-01")
    dept = tc.rec("p2", "detail:sso_clv2", tc.CHAIR + "2", "系上公告", tc.LONG, tc.CHAIR_SITE, "2026-05-01")
    assert kept([posted, dept]) == (["dp2"], {"dp2": ["dp1"]})
    assert kept([posted, dept], units=None) == (["dp1"], {"dp1": ["dp2"]})


def test_among_copies_of_one_level_the_earliest_as_before():
    older = tc.page("p1", 1, "甲", "2026-02-01")
    newer = tc.page("p2", 2, "乙", "2026-05-01")
    assert kept([newer, older]) == (["dp1"], {"dp1": ["dp2"]})


def test_owner_level():
    docs, _ = build_documents([tc.page("p1", 1, "甲"),
                               tc.rec("p2", "detail:sso_webidx", tc.WEB + "1", "乙", tc.LONG, tc.WEB_SITE),
                               tc.rec("p3", "detail:sso_news", tc.NEWS + "AB12", "丙", tc.LONG, tc.NEWS_SITE)], {}, [])
    by_id = {d.doc_id: d for d in docs}
    assert [build._owner_level(by_id[i], UNITS) for i in ("dp1", "dp2", "dp3")] == [1, 0, 2]
    assert build._owner_level(by_id["dp2"], {SCHOOL: UNITS[SCHOOL]}) == 2  # a unit the tree does not know


def test_a_title_that_names_its_year_expires_without_a_date_too():
    got, report = tv.judged([tv.file("a1", "108學年度行事曆.pdf"), tv.file("a2", "校園地圖.pdf")])
    assert got == {"da1": (True, 108), "da2": (False, None)}
    assert report["why"]["attachment"] == {"title_year": 1, "undated": 1}


def test_a_rule_that_notes_a_meetings_year_does_not_expire():
    rule = tv.notice("p1", 1, "學生請假規則（113學年度第1學期第2次校務會議修正通過）", "2024-12-20")
    scoped = tv.file("a1", "獎助學金申請要點(111學年度起適用).pdf", modified=tv.OLD)
    yearly = tv.notice("p2", 2, "114學年度第二學期網路選課注意事項", "2026-01-05")
    got, report = tv.judged([rule, scoped, yearly])
    assert got == {"dp1": (False, 113), "da1": (False, 111), "dp2": (True, 114)}
    assert report["why"]["notice"] == {"fixed_words": 1, "title_year": 1}
    assert build._year_notes_a_rule("學生請假規則（113學年度修正）") and not build._year_notes_a_rule("113學年度請假須知")
    assert not build._year_notes_a_rule("學生請假規則") and not build._year_notes_a_rule("113學年度行事曆")


def test_first_term_at():
    assert first_term_at("學生請假規則（113學年度修正）") == 7 and first_term_at("113學年度行事曆") == 0
    assert first_term_at("校園地圖") is None


def test_a_letterhead_is_no_title():
    names = (SCHOOL, "語文教學中心")
    assert title_flaw("國立高雄師範大學語文教學中心", "舊標題", names) == "nothing"
    assert title_flaw("國立高雄師範大學 國立高雄師範大學", "舊標題", names) == "nothing"
    assert title_flaw("語文教學中心", "舊標題", names) == "nothing"  # one name alone, as before
    assert title_flaw("國立高雄師範大學學則", "舊標題", names) == ""  # the names and something more
    assert title_flaw("語文教學中心華語課程簡章", "舊標題", names) == ""
    assert title_flaw("華語課程簡章", "舊標題", ()) == ""


def test_series_days_reads_series_text_back():
    days = [(date(2026, 9, 7), date(2026, 9, 7)), (date(2026, 9, 14), date(2026, 9, 14)),
            (date(2026, 9, 21), date(2026, 9, 22)), (date(2026, 12, 30), date(2027, 1, 2)),
            (date(2026, 10, 30), date(2026, 11, 1)), (date(2026, 9, 7), date(2026, 9, 7))]
    text = series_text("族語課", days)
    assert text.split("\n")[1] == SERIES_NOTE.format(n=6)
    assert series_days(text) == sorted(days)
    assert series_days("族語課") == [] and series_days("族語課\n別的字\n2026-09：07") == []
    assert series_days("") == []


# ---------------------------------------------------------------- a text on several departments' sites

BIO_SITE = "c.nknu.edu.tw/bio"
BIO = sites.unit_of_site(BIO_SITE)
TREE = UNITS | {u.unit_id: u for u in (unit("理學院", "学院", SCHOOL), unit(BIO, "系所", "理學院"))}


def _two_departments(office: bool = False):
    """The documents kept of one text on two departments' sites (and on an office's with office)."""
    records = [tc.rec("p1", "detail:sso_clv2", tc.CHAIR + "1", "轉知", tc.LONG, tc.CHAIR_SITE, "2026-02-01"),
               tc.rec("p2", "detail:cnknu", f"https://{BIO_SITE}/newsdetail.aspx?nid=5&gi=2", "公告", tc.LONG, BIO_SITE,
                      "2026-05-01")]
    if office:
        records.append(tc.rec("p3", "detail:sso_webidx", tc.WEB + "1", "公告", tc.LONG, tc.WEB_SITE, "2026-06-01"))
    docs, excluded = build_documents(records, {}, [])
    left, _ = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, build.COPY_TEXT, True, TREE)
    return [r.doc for r in left]


def test_the_kept_copy_knows_the_other_units_and_applies_to_them():
    assert BIO != DEPT and TREE[BIO].kind == "系所"
    (doc,) = _two_departments()
    assert doc.doc_id == "dp1" and doc.unit == DEPT and doc.also_units == (BIO,)
    scope = build.unit_scope([doc], TREE)
    rows = [(rel, unit_id) for _, rel, unit_id, *_ in scope.rows]
    assert rows == [("applies_to", DEPT), ("applies_to", BIO), ("publisher", DEPT)] or rows == [
        ("applies_to", BIO), ("applies_to", DEPT), ("publisher", DEPT)]
    assert scope.report["copies_units"] == 1 and scope.report["narrowed"]["total"] == 0  # no narrowing of its own


def test_an_offices_document_applies_to_the_school_whoever_copied_it():
    (doc,) = _two_departments(office=True)
    assert doc.doc_id == "dp3" and doc.unit == OFFICE and set(doc.also_units) == {DEPT, BIO}
    scope = build.unit_scope([doc], TREE)
    assert [(rel, unit_id) for _, rel, unit_id, *_ in scope.rows] == [("applies_to", SCHOOL), ("publisher", OFFICE)]
    assert scope.report["copies_units"] == 0
