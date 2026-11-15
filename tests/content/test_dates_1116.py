"""spec 2026-11-16 §二 (plan 2026-11-16 T3; the owner 11-16: 「可以，先写程序」 on the design), three rules on a
document's date, each with a switch that builds as before:
- a file the export dates by the row of a file table it was downloaded from (日期来源 列表上的日期 on a file's own
  row: the crawler session's finding of 11-16, 966 rows of 36 SSO unit and department sites) takes that date, ahead
  of its notice's and of the server's time;
- a day on which more than SPIKE_MIN files of one site have the same server time is the day a server was moved or a
  folder copied, not when the files were written: those files are undated (4,817 of the 12,552 files dated by the
  server's time on the official library of 11-15);
- a document that states one entry cohort alone (「X學年度入學適用」, no 起) goes out of date once that cohort has
  studied COHORT_YEARS years; one with 起 stays, as every cohort document did before."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta

from nknu.content import build
from nknu.content.dates import LIST_ROW, LIST_SOURCE
from nknu.content.documents import build_documents
from nknu.retrieval.search import RankConfig
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content.test_validity_1114 import ACA, ACA_SITE, OLD, PDF, TODAY, file, notice, page

DAY = "2026-10-22T03:00:00+08:00"  # the day a server's files were copied
LONG_AGO = "2021-08-24T03:00:00+08:00"  # the day a site moved platforms, five years before


def spikes(days: int = 0, documents: int = 0, by_site: dict | None = None, on: bool = True, old: tuple = (0, 0)) -> dict:
    return {"on": on, "min": 20, "recent_days": 253, "days": days, "documents": documents, "by_site": by_site or {},
            "old_days": old[0], "old_documents": old[1]}


def rows_of(records, **switches):
    docs, _ = build_documents(records, {}, [])
    return build._rows(docs, {}, {}, **switches)


def ident(rows) -> dict:
    return {r.doc.doc_id: (r.ident.kind, r.ident.date.isoformat(), r.ident.source) if r.ident else None for r in rows}


# ---------------------------------------------------------------- a file's own row on a file table

def test_a_file_takes_the_date_of_its_row_on_a_file_table():
    posted = notice("p1", 1, "表單更新公告", "2025-03-01")
    listed = file("a1", "場地借用申請表.pdf", posted, page_date="2024-05-31", page_date_raw="2024-05-31",
                  date_source=LIST_ROW, modified=OLD)
    plain = file("a2", "平面圖.pdf", posted, modified=OLD)
    got = ident(rows_of([posted, listed, plain]))
    assert got["da1"] == ("published", "2024-05-31", LIST_SOURCE)  # ahead of its notice's date and the server's time
    assert got["da2"] == ("inherited", "2025-03-01", "所屬公告")
    assert ident(rows_of([posted, listed, plain], row_dates=False))["da1"] == ("inherited", "2025-03-01", "所屬公告")


def test_only_a_list_row_dates_a_file_never_the_words_of_a_page():
    words = file("a1", "場地借用申請表.pdf", page_date="2024-05-31", page_date_raw="更新日期：2024-05-31",
                 date_source="网页上的字")
    later = file("a2", "活動報名表.pdf", page_date="2031-01-01", page_date_raw="2031-01-01", date_source=LIST_ROW)
    assert ident(rows_of([words, later])) == {"da1": None, "da2": None}  # a day after the fetch is no date either


# ---------------------------------------------------------------- days many files share

def many(n: int, day: str = DAY, site: str = ACA_SITE, base: str = ACA, start: int = 0):
    return [tc.rec(f"s{start + i}", PDF, f"{base}f/{start + i}/舊檔{start + i}.pdf", f"舊檔{start + i}.pdf",
                   f"第 {start + i} 份舊檔的內容。", site, modified=day) for i in range(n)]


def test_a_day_more_than_twenty_files_of_a_site_share_dates_none_of_them():
    assert build.SPIKE_MIN == 20
    rows = rows_of(many(21) + [file("a1", "平面圖.pdf", modified=OLD)])
    report = build._date_spikes(rows)
    got = ident(rows)
    assert all(got[f"ds{i}"] is None for i in range(21)) and got["da1"] == ("server", "2023-10-02", "伺服器檔案時間")
    assert report == spikes(1, 21, {ACA_SITE: 21})


def test_twenty_files_keep_their_day_and_so_do_two_sites_with_fewer_each():
    rows = rows_of(many(20) + many(15, site=tc.TG_SITE, base=tc.TG, start=100))
    assert build._date_spikes(rows) == spikes()
    assert all(v == ("server", "2026-10-22", "伺服器檔案時間") for v in ident(rows).values())


def test_the_members_of_one_archive_are_one_file():
    """An archive's members carry the archive's time: thirty of them on a day are one upload, not a moved server."""
    members = [tc.rec(f"m{i}", PDF, f"{ACA}f/zip/表單.zip#表{i}.pdf", f"表{i}.pdf", f"第 {i} 張表。", ACA_SITE, modified=DAY)
               for i in range(30)]
    rows = rows_of(members + many(5))
    assert build._date_spikes(rows)["documents"] == 0  # 1 archive + 5 files: 6 addresses
    assert all(v is not None for v in ident(rows).values())


def test_a_file_dated_otherwise_is_neither_counted_nor_touched():
    posted = notice("p1", 1, "舊檔整理公告", "2025-03-01")
    hung = [tc.rec(f"h{i}", PDF, f"{ACA}h/{i}/附件{i}.pdf", f"附件{i}.pdf", f"附件 {i}。", ACA_SITE, modified=DAY,
                   linked=(posted.url,)) for i in range(25)]
    rows = rows_of([posted, *hung, *many(10)])
    assert build._date_spikes(rows)["documents"] == 0  # the 25 take their notice's date: 10 are dated by the server
    assert ident(rows)["dh0"] == ("inherited", "2025-03-01", "所屬公告")


def test_switched_off_nothing_moves():
    rows = rows_of(many(21))
    assert build._date_spikes(rows, on=False) == spikes(on=False)
    assert all(v is not None for v in ident(rows).values())


def test_a_shared_day_long_ago_keeps_its_files_dated():
    """Left undated they would gain the undated share over what an old date gives: 教务处's 1,247 files of the day
    its site moved platforms in 2021 did, on the first trial build, and two forms passed the rule that answers N10."""
    rows = rows_of(many(21, LONG_AGO) + many(21, DAY, start=100))
    report = build._date_spikes(rows, today=TODAY)
    got = ident(rows)
    assert got["ds0"] == ("server", "2021-08-24", "伺服器檔案時間") and got["ds100"] is None
    assert report == spikes(1, 21, {ACA_SITE: 21}, old=(1, 21))


def test_recent_is_what_would_outrank_an_undated_file():
    rank = RankConfig()
    # w_recent x exp(-days / tau) > w_recent x no_date_factor  <=>  days < tau x ln(1 / no_date_factor)
    assert build.SPIKE_RECENT_DAYS == round(rank.tau_days * math.log(1 / rank.no_date_factor)) == 253
    edge = TODAY - timedelta(days=253)
    for day, kept in ((edge, True), (edge + timedelta(days=1), False)):
        rows = rows_of(many(21, f"{day.isoformat()}T03:00:00+08:00"))
        build._date_spikes(rows, today=TODAY)
        assert (ident(rows)["ds0"] is not None) is kept


# ---------------------------------------------------------------- one entry cohort alone

def judged(records, **switches):
    docs, excluded = build_documents(records, {}, [])
    rows = build._rows(docs, {}, {})
    kept, copies = build._merge_copies(rows, lambda _: None, excluded)
    report = build._validity(kept, copies, **switches)
    return {r.doc.doc_id: (r.expires, r.valid_ay) for r in kept}, report


def test_a_document_of_one_cohort_expires_four_years_on():
    assert build.COHORT_YEARS == 4
    one = file("a1", "校定共同必修科目表(98學年度入學新生適用).pdf", modified=OLD)
    onwards = file("a2", "校定共同必修科目表(111學年度起入學新生適用).pdf", modified=OLD)
    undated = file("a3", "課程架構表.pdf", text="112學年度入學學生適用\n必修共三十學分。")
    got, report = judged([one, onwards, undated])
    # 98 + 3: the last year that cohort normally studies; the one with 起 has no end; an undated one expires too
    assert got == {"da1": (True, 101), "da2": (False, 112), "da3": (True, 115)}
    assert report["why"]["attachment"] == {"cohort_single": 2, "cohort": 1}
    assert "cohort_single" in build.EXPIRES_WHY


def test_a_fixed_word_does_not_keep_a_rule_of_one_cohort():
    got, _ = judged([file("a1", "選課辦法(105學年度入學適用).pdf", modified=OLD)])
    assert got == {"da1": (True, 108)}


def test_a_title_naming_its_year_still_goes_by_that_year():
    posted = page("p1", 1, "114學年度第1學期課程表", "2025-09-01", text="113學年度入學學生適用的課程。")
    got, report = judged([posted])
    assert got == {"dp1": (True, 114)} and report["why"]["page"] == {"title_year": 1}


def test_switched_off_a_cohort_keeps_every_document_as_before():
    one = file("a1", "校定共同必修科目表(98學年度入學新生適用).pdf", modified=OLD)
    got, report = judged([one], cohort_expiry=False)
    assert got == {"da1": (False, 112)} and report["why"]["attachment"] == {"cohort": 1}


def test_today_is_in_the_115_year():
    assert TODAY == date(2026, 11, 14)


def _docs(conn) -> dict:
    return {r[0]: r[1:] for r in conn.execute("SELECT title, ident_date, ident_kind, expires, valid_ay FROM docs")}


def test_the_build_applies_the_three_rules_and_says_so(tmp_path):
    rows = [{"kind": PDF, "url": f"{ACA}f/{i}/舊檔{i}.pdf", "title": f"舊檔{i}.pdf", "text": f"第 {i} 份舊檔的內容。",
             "modified": DAY} for i in range(21)]
    rows += [{"kind": PDF, "url": ACA + "f/x/必修.pdf", "title": "校定共同必修科目表(98學年度入學新生適用).pdf",
              "text": "必修共二十八學分。", "modified": OLD},
             {"kind": PDF, "url": ACA + "f/y/表.pdf", "title": "場地借用申請表.pdf", "text": "申請人請簽名。",
              "page_date": "2024-05-31", "page_date_raw": "2024-05-31", "date_source": LIST_ROW}]
    aca = tc.site_input(tmp_path / "aca", ACA_SITE, "教务处", rows)
    paths = tm.tree_files(tmp_path / "tree")
    report = tm.run(paths, tmp_path / "db" / "on.db", inputs=[aca])
    found = spikes(1, 21, {ACA_SITE: 21})
    assert report.date_spikes == found
    with sqlite3.connect(tmp_path / "db" / "on.db") as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        got = _docs(conn)
    assert json.loads(meta["date_spikes_json"]) == found
    assert (meta["cohort_expiry"], meta["file_row_dates"]) == ("on", "on")
    assert got["舊檔0.pdf"] == (None, None, 0, None)
    assert got["校定共同必修科目表(98學年度入學新生適用).pdf"] == ("2023-10-02", "server", 1, 101)
    assert got["場地借用申請表.pdf"][:2] == ("2024-05-31", "published")

    off = tm.run(paths, tmp_path / "db" / "off.db", inputs=[aca], date_spikes=False, cohort_expiry=False,
                 file_row_dates=False)
    assert off.date_spikes == spikes(on=False)
    with sqlite3.connect(tmp_path / "db" / "off.db") as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        got = _docs(conn)
    assert (meta["cohort_expiry"], meta["file_row_dates"]) == ("off", "off")
    assert got["舊檔0.pdf"][:2] == ("2026-10-22", "server")
    assert got["校定共同必修科目表(98學年度入學新生適用).pdf"] == ("2023-10-02", "server", 0, 112)
    assert got["場地借用申請表.pdf"][:2] == (None, None)
