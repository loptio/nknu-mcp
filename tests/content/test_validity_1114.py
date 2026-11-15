"""Plan 2026-11-14 T6, the build's part (direction 2026-11-13 第 6 件, 「还有效」): every document gets docs.expires
(1: it goes out of date) and docs.valid_ay (the ROC academic year it belongs to), which hybrid.validity reads.

It expires: a notice; a page dated by its platform's date field or its list row; a document whose title names a term
or year; a calendar event; an attachment of a document that expires. It does not: an undated document; one whose title
holds a fixed word of rules and forms (辦法, 申請表 …); one that states an entry cohort; one with a merged copy that
does not expire. A title that names a term or year expires whatever cohort it holds and whether it has a date or
not, unless a fixed word stands before that year (a rule that notes the meeting which passed it) 【Claude; the last
two since the review of 11-14: tests/content/test_review_1114c.py】, and an event always does. valid_ay: the year the title names, else the year of the identity date (it turns on 1 August), an
event's last day's; the latest among a document and its merged copies 【Claude】. The schema is pipeline-8. The
sentences are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from nknu.content import build
from nknu.content.corpus import Event, Record
from nknu.content.dates import LIST_ROW, PAGE_WORDS
from nknu.content.documents import build_documents, one_event_per_series
from nknu.content.schema import SCHEMA_VERSION, create_schema
from nknu.retrieval.hybrid import validity
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

TODAY = date(2026, 11, 14)  # the 115 academic year
ACA, ACA_SITE = tc.ACA, tc.ACA_SITE
WEB, WEB_SITE = tc.WEB, tc.WEB_SITE
CHAIR, CHAIR_SITE = tc.CHAIR, tc.CHAIR_SITE
PDF = tc.PDF
OLD = "2023-10-02T01:00:00+08:00"  # a file's server time: the 112 academic year


def notice(rid: str, n: int, title: str, day: str = "", text: str = "") -> Record:
    """A notice of a detail path, dated by its platform's date field (undated without day)."""
    return tc.rec(rid, "detail:sso_webidx", WEB + str(n), title, text or f"{title}的內容，第 {n} 則。", WEB_SITE, day)


def page(rid: str, n: int, title: str, day: str = "", text: str = "", **more) -> Record:
    """A page; with day, dated by the row of a list linking it."""
    return tc.rec(rid, "page:sso_clv2", CHAIR + str(n), title, text or f"{title}的內容，第 {n} 頁。", CHAIR_SITE, day,
                  LIST_ROW, **more)


def file(rid: str, name: str, on: Record | None = None, text: str = "", **more) -> Record:
    linked = {"linked": (on.url,)} if on is not None else {}
    return tc.rec(rid, PDF, f"{ACA}f/{rid}/{name}", name, text or f"{name}的內容。", ACA_SITE, **linked, **more)


def judged(records: list[Record], events: tuple[Event, ...] = (), metas: dict | None = None) -> tuple[dict, dict]:
    """({doc_id: (expires, valid_ay)} of the documents kept, the report) after the build's own steps."""
    docs, excluded = build_documents(records, metas or {}, list(events))
    rows = build._rows(docs, {"e" + ev.id: ev for ev in events}, {})
    kept, copies = build._merge_copies(rows, lambda _: None, excluded)
    report = build._validity(kept, copies)
    return {r.doc.doc_id: (r.expires, r.valid_ay) for r in kept}, report


# ---------------------------------------------------------------- the schema

def test_the_schema_has_the_two_columns_hybrid_reads():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    columns = {r[1]: (r[2], r[3], r[4]) for r in conn.execute("PRAGMA table_info(docs)")}
    assert columns["expires"] == ("INTEGER", 1, "0") and columns["valid_ay"] == ("INTEGER", 0, None)
    assert SCHEMA_VERSION == "pipeline-8"
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("pipeline-8",)
    conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, chars)"
                 " VALUES ('d1', 'page', 'page', '甲', 'https://x.invalid/', '教務處', 0, 0)")
    assert conn.execute("SELECT expires, valid_ay FROM docs").fetchone() == (0, None)  # a row that names neither
    assert validity(conn, ["d1"], TODAY, 0.85) == {}  # hybrid reads these very names
    conn.close()


# ---------------------------------------------------------------- what expires

def test_a_notice_expires_and_an_undated_one_does_not():
    got, _ = judged([notice("p1", 1, "圖書館暑假開館公告", "2025-03-01"), notice("p2", 2, "社團招新")])
    assert got == {"dp1": (True, 113), "dp2": (False, None)}  # March 2025 is in the 113 academic year


def test_a_page_expires_only_when_its_platform_or_its_list_row_dates_it():
    words = {"page_date": "2026-03-01", "page_date_raw": "更新日期：2026-03-01", "date_source": PAGE_WORDS}
    got, report = judged([page("p1", 1, "迎新活動", "2024-09-10"),  # a post: its list row's date
                          page("p2", 2, "聯絡資訊", **words),  # a standing page that says when it was changed
                          page("p3", 3, "系所簡介")])
    assert got == {"dp1": (True, 113), "dp2": (False, 114), "dp3": (False, None)}
    assert report["why"]["page"] == {"dated_page": 1, "standing": 1, "undated": 1}
    assert report["expires"] == {"page": 1, "total": 1}


def test_a_title_naming_a_term_or_year_expires_with_that_year():
    got, report = judged([page("p1", 1, "系所簡介"),
                          file("a1", "112學年度行事曆.pdf", modified=OLD),  # on no notice: the server's time dates it
                          file("a2", "交通資訊.pdf", modified=OLD),
                          file("a3", "116學年度招生簡章.pdf", modified=OLD),  # the year it is about, not its file's
                          file("a4", "113學年度課程地圖.pdf")])  # undated: its title still says its year
    assert got == {"dp1": (False, None), "da1": (True, 112), "da2": (False, 112), "da3": (True, 116),
                   "da4": (True, 113)}
    assert report["why"]["attachment"] == {"title_year": 3, "standing": 1}
    assert report["valid_ay"] == {"title": 3, "date": 1, "none": 1}


def test_an_attachment_follows_the_document_it_hangs_on():
    posted, rule = notice("p1", 1, "圖書館暑假開館公告", "2025-03-01"), notice("p2", 2, "學生請假辦法", "2024-05-01")
    standing = page("p3", 3, "表單下載")
    got, _ = judged([posted, rule, standing, file("a1", "開館時間表.pdf", posted), file("a2", "修正對照表.pdf", rule),
                     file("a3", "選課流程圖.pdf", standing, modified=OLD)])
    assert got["dp1"] == (True, 113) and got["da1"] == (True, 113)  # its notice's date and fate
    assert got["dp2"] == (False, 112) and got["da2"] == (False, 112)  # a rule's notice does not expire, nor its file
    assert got["dp3"] == (False, None) and got["da3"] == (False, 112)  # a standing page's file


def test_fixed_words_a_cohort_and_no_date_keep_a_document_from_expiring():
    posted = notice("p1", 1, "圖書館暑假開館公告", "2025-03-01")
    cohort = file("a3", "必修科目表.pdf", posted, text="113學年度起入學學生適用\n必修科目共三十學分。")
    listed = page("p4", 4, "活動花絮", "2024-09-10")  # a page its list row dates: it expires, its files inherit no date
    got, report = judged([posted, listed, file("a1", "開館時間表.pdf", posted), file("a2", "圖書館借閱規則.pdf", posted),
                          cohort, file("a5", "場地借用申請表.pdf", listed), file("a6", "平面圖.pdf", listed, modified=OLD)])
    assert got == {"dp1": (True, 113), "dp4": (True, 113), "da1": (True, 113), "da2": (False, 113),
                   "da3": (False, 113), "da5": (False, None), "da6": (True, 112)}
    assert report["why"]["attachment"] == {"parent": 2, "fixed_words": 1, "cohort": 1, "undated": 1}
    # the count that stands in for an audit by hand: of the files on documents that expire, what each clue kept
    assert report["attachments"] == {"parent_expires": 5, "expire": 2, "kept": 3, "undated": 1,
                                     "clues": {"fixed_words": 2, "standing_copy": 0, "cohort": 1}}


@pytest.mark.parametrize("word", ["辦法", "要點", "規則", "須知", "準則", "細則", "章程", "規程", "規定", "注意事項", "申請書",
                                  "申請表", "表單"])
def test_each_fixed_word_keeps_a_dated_notice(word):
    got, report = judged([notice("p1", 1, f"宿舍{word}", "2024-05-01"), notice("p2", 2, "宿舍公告", "2024-05-01")])
    assert got == {"dp1": (False, 112), "dp2": (True, 112)}
    assert report["why"]["notice"] == {"fixed_words": 1, "notice": 1}


def test_a_title_that_names_a_year_expires_whatever_else_it_holds():
    # 【Claude】 「114學年度第二學期 網路選課注意事項」 is that term's notice: the next term's replaces it
    yearly = notice("p1", 1, "114學年度第二學期網路選課注意事項", "2026-01-09")
    cohort = notice("p2", 2, "115學年度第1學期選課公告", "2026-06-20", "113學年度起入學學生適用新制，請依規定選課。")
    form = file("a1", "113學年度暑修選課申請表.pdf", yearly)
    got, report = judged([yearly, cohort, form])
    assert got == {"dp1": (True, 114), "dp2": (True, 115), "da1": (True, 113)}
    # counted, so the choice can be weighed: dp1 and da1 hold a fixed word, dp2 states a cohort
    assert report["year_named"] == {"fixed_words": 2, "cohort": 1}


def test_a_calendar_event_always_expires_by_its_last_day():
    events = (Event("ev1", "期末考試，另訂日期者從其規定", date(2025, 12, 21), date(2025, 12, 25), ""),
              Event("ev2", "114學年度大學美術術科考試", date(2025, 2, 1), date(2025, 2, 2), ""),  # held in 113
              Event("ev3", "暑期輔導", date(2026, 7, 30), date(2026, 8, 2), ""))  # ends in the 115 academic year
    got, report = judged([], events)
    assert got == {"eev1": (True, 114), "eev2": (True, 113), "eev3": (True, 115)}
    assert report["why"]["event"] == {"event": 3} and report["valid_ay"] == {"event": 3}


def test_a_recurring_event_belongs_to_the_year_of_its_last_instance():
    instances = [Event(f"tu_{d.isoformat()}", "導師會議", d, d, "", series="tu")
                 for d in (date(2025, 12, 15), date(2026, 3, 16), date(2026, 9, 14))]
    merged, _ = one_event_per_series(instances)
    got, _ = judged([], tuple(merged))
    assert got == {"etu": (True, 115)}


def test_an_outside_link_has_no_date_and_expires_only_by_a_year_in_its_title():
    link = tc.rec("p1", "外站:reurl.cc", "https://reurl.cc/abc", "113學年跨域成果展報名表單", "報名表單", ACA_SITE)
    plain = tc.rec("p2", "外站:reurl.cc", "https://reurl.cc/def", "跨域成果展報名網頁", "報名網頁", ACA_SITE)
    got, report = judged([link, plain])
    assert got == {"dp1": (True, 113), "dp2": (False, None)}
    assert report["why"]["link"] == {"title_year": 1, "undated": 1}


# ---------------------------------------------------------------- merged copies

def test_a_copy_that_does_not_expire_keeps_the_document():
    # one file under a dated notice and, undated, on a standing page: the direction's 「有一份在常设页上，就算不会过期」
    posted, standing = notice("p1", 1, "圖書館暑假開館公告", "2025-03-01"), page("p2", 2, "表單下載")
    got, report = judged([posted, standing, file("a1", "開館時間表.pdf", posted, tc.LONG),
                          file("a2", "library.pdf", standing, tc.SPACED),
                          file("a3", "借書證說明.pdf", posted)])
    assert got == {"dp1": (True, 113), "dp2": (False, None), "da1": (False, 113), "da3": (True, 113)}
    assert report["why"]["attachment"] == {"standing_copy": 1, "parent": 1}
    assert report["attachments"] == {"parent_expires": 2, "expire": 1, "kept": 1, "undated": 0,
                                     "clues": {"fixed_words": 0, "standing_copy": 1, "cohort": 0}}


def test_a_twin_on_no_kept_page_says_nothing_either_way():
    # 【Claude】 a file linked only from a page the build leaves out (an album, a list) is no copy 「在常设页上」
    posted = notice("p1", 1, "轉系錄取名單公告", "2025-03-01")
    listed = file("a1", "轉系同意名單.pdf", posted, tc.LONG)
    none = {"fixed_words": 0, "standing_copy": 0, "cohort": 0}
    got, report = judged([posted, listed, file("a2", "file1_417.pdf", None, tc.SPACED, modified="2025-03-05T01:00:00+08:00")])
    assert got == {"dp1": (True, 113), "da1": (True, 113)}  # the twin keeps nothing
    assert report["why"]["attachment"] == {"parent": 1}
    assert report["attachments"] == {"parent_expires": 1, "expire": 1, "kept": 0, "undated": 0, "clues": none}
    # the twin is the earlier one and so the one kept: it takes the fate of the copy on the notice
    got, report = judged([posted, listed, file("a2", "file1_417.pdf", None, tc.SPACED, modified=OLD)])
    assert got == {"dp1": (True, 113), "da2": (True, 113)}
    assert report["why"]["attachment"] == {"parent": 1} and report["valid_ay"] == {"date": 1, "copy": 1}
    assert report["attachments"] == {"parent_expires": 1, "expire": 1, "kept": 0, "undated": 0, "clues": none}
    # alone, a file on no kept page stays, as any file without a parent that expires
    got, _ = judged([file("a2", "file1_417.pdf", None, tc.SPACED, modified=OLD)])
    assert got == {"da2": (False, 112)}
    # and what its own title says still counts: the twin is named as a rule
    got, report = judged([posted, file("a1", "附件1.pdf", posted, tc.LONG),
                          file("a2", "轉系辦法.pdf", None, tc.SPACED, modified="2025-03-05T01:00:00+08:00")])
    assert got["da1"] == (False, 113) and report["why"]["attachment"] == {"standing_copy": 1}


def test_copies_that_all_expire_leave_the_document_expiring_with_the_latest_of_their_years():
    # 【Claude】 the same text posted again in a later year is still valid then; the earliest one is the one kept
    first, again = notice("p1", 1, "宿舍熱水公告", "2022-10-01", tc.OTHER), notice("p2", 2, "轉知：宿舍熱水", "2025-10-01", tc.OTHER)
    got, report = judged([first, again])
    assert got == {"dp1": (True, 114)}
    assert report["valid_ay"] == {"copy": 1}
    # a copy without a date of its own does not expire: the most favourable one counts
    got, _ = judged([first, notice("p3", 3, "宿舍熱水", "", tc.OTHER)])
    assert got == {"dp1": (False, 111)}


# ---------------------------------------------------------------- the build

A_NOTICE, A_RULE, A_PAGE = ACA + "newsdetail.aspx?nid=1&gi=2", ACA + "newsdetail.aspx?nid=2&gi=2", ACA + "page.aspx?pn=7"
A_TABLE, A_FORM, A_MAP = (ACA + "uploadfile/news/1/" + name for name in ("開館時間表.pdf", "借書申請表.pdf", "map.pdf"))
CAL = [tm.event("ev1", "申請停修", "2026-11-03", "2026-11-17"), tm.event("ev2", "畢業典禮", "2024-06-08", "2024-06-09")]


@pytest.fixture
def built(tmp_path):
    aca = tc.site_input(tmp_path / "aca", ACA_SITE, "教务处", [
        {"kind": "newsdetail", "url": A_NOTICE, "title": "圖書館暑假開館公告", "text": "暑假期間調整開館時間。",
         **tc.dated("2025-03-01")},
        {"kind": "newsdetail", "url": A_RULE, "title": "圖書館借閱規則", "text": "借閱以三十日為限。", **tc.dated("2023-09-01")},
        {"kind": "page", "url": A_PAGE, "title": "表單下載", "text": "各式表單請自行下載。"},
        {"kind": PDF, "url": A_TABLE, "title": "開館時間表.pdf", "text": "週一至週五開館。", "linked": A_NOTICE},
        {"kind": PDF, "url": A_FORM, "title": "借書申請表.pdf", "text": "申請人請簽名。", "linked": A_NOTICE},
        {"kind": PDF, "url": A_MAP, "title": "map.pdf", "text": "校區平面圖", "linked": A_PAGE}])
    (aca[1] / "data").mkdir()
    (aca[1] / tm.CAL_FILE).write_text(json.dumps({"kind": "calendar#events", "items": CAL}, ensure_ascii=False),
                                      encoding="utf-8")
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[aca])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn
    conn.close()


IDS = {name: tc.doc_id(url) for name, url in (("notice", A_NOTICE), ("rule", A_RULE), ("page", A_PAGE),
                                              ("table", A_TABLE), ("form", A_FORM), ("map", A_MAP))}


def test_the_build_writes_the_two_columns(built):
    _, conn = built
    got = {r[0]: r[1:] for r in conn.execute("SELECT doc_id, expires, valid_ay FROM docs")}
    assert got == {IDS["notice"]: (1, 113), IDS["rule"]: (0, 112), IDS["page"]: (0, None), IDS["table"]: (1, 113),
                   IDS["form"]: (0, 113), IDS["map"]: (0, None), "eev1": (1, 115), "eev2": (1, 112)}
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("pipeline-8",)


def test_the_report_counts_what_expires_and_what_each_clue_kept(built):
    report, conn = built
    assert report.validity == {
        "expires": {"notice": 1, "attachment": 1, "event": 2, "total": 4},
        "why": {"notice": {"notice": 1, "fixed_words": 1}, "page": {"undated": 1},
                "attachment": {"parent": 1, "fixed_words": 1, "undated": 1}, "event": {"event": 2}},
        "attachments": {"parent_expires": 2, "expire": 1, "kept": 1, "undated": 0,
                        "clues": {"fixed_words": 1, "standing_copy": 0, "cohort": 0}},
        "valid_ay": {"date": 4, "event": 2, "none": 2}, "year_named": {"fixed_words": 0, "cohort": 0}}
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key = 'validity_json'").fetchone()[0]) == report.validity
    said = {reason for reasons in report.validity["why"].values() for reason in reasons}
    assert said <= {*build.EXPIRES_WHY, *build.STAYS_WHY}
    # cohort_single: spec 2026-11-16 §二 (a document of one entry cohort alone; tests/content/test_dates_1116.py)
    assert (build.EXPIRES_WHY, build.STAYS_WHY) == (("event", "title_year", "cohort_single", "notice", "dated_page",
                                                     "parent"),
                                                    ("undated", "fixed_words", "cohort", "standing_copy", "standing"))


def test_hybrid_reads_what_the_build_wrote(built):
    _, conn = built
    ids = [r[0] for r in conn.execute("SELECT doc_id FROM docs")]
    # in the 115 academic year: the notice and its file are two years old, the 2024 event three, this year's event none
    assert validity(conn, ids, TODAY, 0.85) == pytest.approx(
        {IDS["notice"]: 0.85 ** 2, IDS["table"]: 0.85 ** 2, "eev2": 0.85 ** 3})


def test_main_prints_how_many_expire(tmp_path, capsys):
    aca = tc.site_input(tmp_path / "aca", ACA_SITE, "教务处", [
        {"kind": "newsdetail", "url": A_NOTICE, "title": "圖書館暑假開館公告", "text": "暑假期間調整開館時間。",
         **tc.dated("2025-03-01")},
        {"kind": PDF, "url": A_FORM, "title": "借書申請表.pdf", "text": "申請人請簽名。", "linked": A_NOTICE}])
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "o" / "x.db"
    assert build.main(["--input", f"{aca[0]}::{aca[1]}", "--out", str(out), "--aliases", str(paths["aliases"]),
                       "--server-dates", str(paths["server"]), "--site-list", str(paths["sites"]), "--units",
                       str(paths["units"])]) == 0
    printed = capsys.readouterr().out
    assert "会过期的文件 1 份（公告 1）" in printed
    assert ("挂在会过期的文件上的附件 1 份：照样会过期 0、不会过期 1（各线索认到的：固定字眼 1、常设页上有副本 0、"
            "入学届别 0，一份可以算进两项）") in printed
    saved = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["validity"]["expires"] == {"notice": 1, "total": 1}
