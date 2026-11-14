"""Plan 2026-11-14 T4 (direction 2026-11-13 第 4 件): where a title says nothing, the document takes the title its own
text gives.

Two kinds of such titles: (a) pages and notices of one site of which 20 or more, copies merged, have exactly the same
title (a site name used as every page's title); (b) attachments titled by a file name without a Han character
(「643-79-8.odt」) 【Claude: narrower than the plan's every file-name title】. The new title is the text's first heading
when it stands on one of its first two lines with text, else its first line; the old one stays findable in the link
texts. A candidate that is too short, too long, no title (a table row, a field label, a numbered part), says nothing
(附件一, 說明, the school's name …), is the old title, or would again be shared by 20 documents of the site leaves the
old title, and so does the text of a picture or a scan. build_sites(own_titles=False) / --no-own-titles builds as
before. The sentences are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from nknu.content import build, documents
from nknu.content.corpus import Record
from nknu.content.documents import build_documents, own_titles, text_title, title_flaw
from nknu.content.schema import BACKGROUND, EXAM, MINUTES, PRIMARY
from nknu.retrieval.search import search_docs
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

TODAY = date(2026, 11, 14)
ACA, ACA_SITE = tc.ACA, tc.ACA_SITE
CHAIR, CHAIR_SITE = tc.CHAIR, tc.CHAIR_SITE
CHEM, CHEM_SITE = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=203012&page=", "sso.nknu.edu.tw/site/203012"
PDF, SCAN, IMAGE = tc.PDF, tc.SCAN, "附件:图片"
SOFT = "軟體工程與管理學系"  # the unit of CHAIR_SITE, and the title of its every page
SCHOOL = "國立高雄師範大學"


def test_the_numbers():
    assert (documents.SAME_TITLE_MIN, documents.TITLE_MIN_CHARS, documents.TITLE_MAX_CHARS, documents.HEAD_LINES) == (
        20, 2, 80, 2)


# ---------------------------------------------------------------- the title a text gives

def test_the_first_heading_at_the_top_else_the_first_line():
    assert text_title("## 學生請假辦法\n第一條 本辦法依學則訂定。") == "學生請假辦法"
    assert text_title("附件四\n\n## 個人資料蒐集同意書\n本人同意。") == "個人資料蒐集同意書"  # a label above the title
    assert text_title("附件四\n本人同意。\n## 個人資料蒐集同意書") == "附件四"  # a heading further down is a part
    assert text_title("修業規定\n每學期至少修習九學分。\n## 附則") == "修業規定"
    assert text_title("\n\n  **學生  手冊**  \n目錄") == "學生 手冊"  # decoration and runs of spaces go
    assert text_title("- 圖書館開放時間\n週一至週五") == "圖書館開放時間"
    assert text_title("") == "" and text_title(" \n\n ") == "" and text_title("##\n**") == ""


@pytest.mark.parametrize(("candidate", "why"), [
    ("", "blank"),
    ("眼", "short"), ("3952", "short"), ("113.4.24", "short"), ("A1", "short"), ("----", "short"),
    ("長" * 81, "long"),
    ("附件", "nothing"), ("附件一", "nothing"), ("附件 1", "nothing"), ("【附件二】", "nothing"), ("附表3", "nothing"),
    ("附錄", "nothing"), ("說明", "nothing"), ("說明：", "nothing"), ("注意事項", "nothing"), ("目錄", "nothing"),
    ("目錄 contents", "nothing"), ("備註", "nothing"), ("抄本", "nothing"),
    (SCHOOL, "nothing"), ("國立高雄師範大學 National Kaohsiung Normal University", "nothing"), (SOFT, "nothing"),
    ("| 項目 | 金額 |", "no_title"), ("檔號：", "no_title"), ("授課科目:", "no_title"), ("一、依據", "no_title"),
    ("第十四條 修業年限", "no_title"), ("1. 申請資格", "no_title"), ("（一）報名方式", "no_title"), ("Q1：如何申請", "no_title"),
    ("舊標題", "same"),
])
def test_a_candidate_that_is_no_title_is_refused(candidate, why):
    assert title_flaw(candidate, "舊標題", (SCHOOL, SOFT)) == why


@pytest.mark.parametrize("candidate", ["學生請假辦法", "長" * 80, "附件一 獎學金申請表", "108 學年度碩士班", "一般文件下載",
                                       "十二年國教課程說明", "Credit Transfer System", "115-1 微學分開課說明",
                                       "國立高雄師範大學學生請假辦法", "主旨：公告轉系名單"])
def test_a_plain_line_of_words_is_a_title(candidate):
    assert title_flaw(candidate, "舊標題", (SCHOOL, SOFT)) == ""


# ---------------------------------------------------------------- which documents, and what they get

def page(n: int, title: str, text: str, site: str = CHAIR_SITE, base: str = CHAIR, kind: str = "page:sso_clv2",
         **more) -> Record:
    return tc.rec(f"p{n}", kind, base + str(n), title, text, site, **more)


def file(rid: str, name: str, text: str, kind: str = PDF, **more) -> Record:
    return tc.rec(rid, kind, f"{ACA}f/{rid}/{name}", name, text, ACA_SITE, **more)


def retitled(records: list[Record], on: bool = True) -> tuple[dict, dict]:
    """({doc_id: (title, link texts)}, the report) of own_titles on the records' documents."""
    docs, _ = build_documents(records, {}, [])
    report = own_titles(docs, SCHOOL, on=on)
    return {d.doc_id: (d.title, d.linktext) for d in docs}, report


def counts(part: dict) -> dict:
    return {k: part[k] for k in ("documents", "retitled", "kept", "why")}


def test_twenty_pages_of_one_site_with_one_title_take_their_own():
    pages = [page(n, SOFT, f"課程地圖第{n}版\n本頁說明第 {n} 版的修課順序。") for n in range(1, 21)]
    other = [page(n, SOFT, f"實驗室介紹第{n}間\n設備清單。", CHEM_SITE, CHEM) for n in range(101, 120)]  # 19: too few
    got, report = retitled([*pages, *other, page(200, "系所簡介", "本系成立於民國八十年。")])
    assert got["dp1"] == ("課程地圖第1版", SOFT) and got["dp20"] == ("課程地圖第20版", SOFT)  # the old title stays findable
    assert got["dp101"] == (SOFT, "") and got["dp200"] == ("系所簡介", "")
    assert report["on"] is True and (report["same_min"], report["max_chars"]) == (20, 80)
    assert report["shared"] == {"titles": 1, "documents": 20, "retitled": 20, "kept": 0, "why": {}}
    assert counts(report["file"]) == {"documents": 0, "retitled": 0, "kept": 0, "why": {}}
    assert report["by_site"] == {CHAIR_SITE: {"shared": {"retitled": 20, "kept": 0}}}
    assert report["examples"]["shared"][0] == {"id": "dp1", "site": CHAIR_SITE, "old": SOFT, "new": "課程地圖第1版"}
    assert len(report["examples"]["shared"]) == 20 and report["examples"]["file"] == []


def test_pages_and_notices_count_together_and_an_empty_title_is_one_title():
    pages = [page(n, "", f"活動紀錄第{n}場\n照片與心得。") for n in range(1, 13)]
    notices = [page(n, "", f"公告事項第{n}則\n請同學留意。", kind="detail:sso_webidx", base=CHAIR + "n", site=CHAIR_SITE)
               for n in range(13, 21)]
    got, report = retitled([*pages, *notices])
    assert got["dp1"] == ("活動紀錄第1場", "") and got["dp20"] == ("公告事項第20則", "")  # nothing to keep of an empty title
    assert report["shared"]["retitled"] == 20


def test_a_line_twenty_documents_would_share_is_no_title_either():
    # the site's header is the first line of 20 of the 25 pages: those keep their title, the other five take theirs
    header = [page(n, SOFT, f"最新消息\n第 {n} 則消息的內容。") for n in range(1, 21)]
    own = [page(n, SOFT, f"招生說明會第{n}場\n歡迎參加。") for n in range(21, 26)]
    got, report = retitled([*header, *own])
    assert got["dp1"] == (SOFT, "") and got["dp21"] == ("招生說明會第21場", SOFT)
    assert report["shared"] == {"titles": 1, "documents": 25, "retitled": 5, "kept": 20, "why": {"shared": 20}}
    # a document that has the line as its title already counts: 19 candidates and one such page are 20
    nineteen = [page(n, SOFT, "系所公告\n" + f"第 {n} 則。") for n in range(1, 20)]
    filler = page(20, SOFT, "獎學金申請\n說明如下。")
    got, report = retitled([*nineteen, filler, page(30, "系所公告", "歷年公告一覽。")])
    assert got["dp1"] == (SOFT, "") and got["dp20"] == ("獎學金申請", SOFT)
    assert report["shared"]["why"] == {"shared": 19}


def test_a_candidate_that_is_no_title_leaves_the_old_one():
    texts = ["", "附件一\n申請表如下。", f"{SOFT}\n系所簡介。", "| 項目 | 金額 |\n| - | - |\n| 學費 | 一萬元 |", "一、依據\n本校學則。",
             SOFT + "課程介紹，" + "本學系課程分為基礎、核心與專業選修三類。" * 5, "眼\n活動照片", "檔號：\n保存年限："]
    pages = [page(n, SOFT, text) for n, text in enumerate(texts, start=1)]
    pages += [page(n, SOFT, f"課程地圖第{n}版\n修課順序。") for n in range(len(texts) + 1, 21)]
    got, report = retitled(pages)
    assert [got[f"dp{n}"][0] for n in range(1, 9)] == [SOFT] * 8 and got["dp9"] == ("課程地圖第9版", SOFT)
    assert report["shared"] == {"titles": 1, "documents": 20, "retitled": 12, "kept": 8,
                                "why": {"blank": 1, "nothing": 2, "no_title": 3, "long": 1, "short": 1}}


def test_a_file_named_without_a_han_character_takes_its_texts_title():
    notice = tc.rec("p1", "detail:sso_webidx", tc.WEB + "1", "請假規定公告", "請見附件。", tc.WEB_SITE, "2026-03-01")
    named = file("a3", "a.pdf", "## 獎學金申請須知\n申請期限。", linked=(notice.url,), contexts={notice.url: "獎學金須知"})
    files = [file("a1", "643-79-8.odt", "## 學生請假辦法\n第一條 本辦法依學則訂定。"),
             file("a2", "停修申請表.pdf", "## 停修申請表\n學號 姓名"),  # a name with Han characters says what it is
             named,  # titled by what its page calls it: not a file name
             file("a4", "0423.jpg", "迎新茶會\n歡迎新生", IMAGE),  # what OCR read on a picture
             file("a5", "scan01.pdf", "學生手冊\n第一章", SCAN),  # a scan's few words are not the document
             file("a6", "x1.pdf", "", "附件:pdf:无文字层"),
             file("a7", "form_v2.docx", "附件三\n申請人簽名", "附件:office")]
    got, report = retitled([notice, *files])
    assert got["da1"] == ("學生請假辦法", "643-79-8.odt")
    assert got["da2"] == ("停修申請表.pdf", "") and got["da3"] == ("獎學金須知", "a.pdf")
    assert [got[d][0] for d in ("da4", "da5", "da6", "da7")] == ["0423.jpg", "scan01.pdf", "x1.pdf", "form_v2.docx"]
    assert counts(report["file"]) == {"documents": 5, "retitled": 1, "kept": 4,
                                      "why": {"image": 1, "scan": 1, "blank": 1, "nothing": 1}}
    assert report["by_site"] == {ACA_SITE: {"file": {"retitled": 1, "kept": 4}}}
    assert report["examples"]["file"] == [{"id": "da1", "site": ACA_SITE, "old": "643-79-8.odt", "new": "學生請假辦法"}]
    assert report["shared"] == {"titles": 0, "documents": 0, "retitled": 0, "kept": 0, "why": {}}


def test_files_share_a_line_like_pages_do():
    exams = [file(f"a{n}", f"10{n:02d}.pdf", f"轉學生招生考試試題\n科目第 {n} 科") for n in range(1, 21)]
    got, report = retitled([*exams, file("a30", "map.pdf", "校區平面圖\n和平校區")])
    assert got["da1"] == ("1001.pdf", "") and got["da30"] == ("校區平面圖", "map.pdf")
    assert counts(report["file"]) == {"documents": 21, "retitled": 1, "kept": 20, "why": {"shared": 20}}


def test_switched_off_nothing_changes_and_the_report_still_counts():
    pages = [page(n, SOFT, f"課程地圖第{n}版\n修課順序。") for n in range(1, 21)]
    got, report = retitled([*pages, file("a1", "643-79-8.odt", "## 學生請假辦法\n第一條")], on=False)
    assert got["dp1"] == (SOFT, "") and got["da1"] == ("643-79-8.odt", "")
    assert report["on"] is False and report["examples"] == {"shared": [], "file": []} and report["by_site"] == {}
    assert report["shared"] == {"titles": 1, "documents": 20, "retitled": 0, "kept": 20, "why": {}}
    assert counts(report["file"]) == {"documents": 1, "retitled": 0, "kept": 1, "why": {}}


def test_the_examples_are_spread_over_the_documents():
    pages = [page(n, SOFT, f"課程地圖第{n}版\n修課順序。") for n in range(1, 101)]
    _, report = retitled(pages)
    shown = [e["id"] for e in report["examples"]["shared"]]
    assert len(shown) == documents.TITLE_EXAMPLES == 40 and shown[0] == "dp1" and shown[-1] in ("dp98", "dp99", "dp100")


# ---------------------------------------------------------------- the build

def site(tmp_path, rows: list[dict], name: str = "chair", key: str = CHAIR_SITE):
    return tc.site_input(tmp_path / name, key, "軟體工程與管理學系品保網站", rows)


def chair_rows() -> list[dict]:
    """21 pages titled by the site's name (one of them a copy of another), a page with a title of its own, and four
    files: two named without a Han character."""
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + str(n), "title": SOFT,
             "text": f"114學年度第{1 + n % 2}學期課程地圖第{n}版\n" + f"本頁說明第 {n} 版的修課順序與先修科目。" * 12}
            for n in range(1, 21)]
    rows.append({**rows[0], "url": CHAIR + "21"})  # the same page at another address: one document
    rows.append({"kind": "page:sso_clv2", "url": CHAIR + "30", "title": "系所簡介", "text": "本系成立於民國八十年。"})
    notice = CHAIR + "40"
    rows.append({"kind": "detail:sso_clv2", "url": notice, "title": "會議資料公告", "text": "會議資料如附件。",
                 **tc.dated("2026-03-01")})
    for name, text in (("643-79-8.odt", "## 113學年度第1次系務會議紀錄\n主席：O主任\n決議：照案通過。"),
                       ("115-1a.pdf", "選課時程表\n九月一日起開放選課。"), ("停修申請表.pdf", "停修申請表\n學號 姓名"),
                       ("0423.jpg", "迎新茶會\n歡迎新生")):
        rows.append({"kind": IMAGE if name.endswith(".jpg") else PDF, "url": f"{CHAIR}f/{name}", "title": name, "text": text,
                     "linked": notice})
    return rows


def open_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


@pytest.fixture
def built(tmp_path):
    inputs = [site(tmp_path, chair_rows())]
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "db" / "on.db"
    report = tm.run(paths, out, inputs=inputs)
    conn = open_ro(out)
    yield report, conn, inputs, paths
    conn.close()


def by_url(conn: sqlite3.Connection, columns: str) -> dict:
    return {r[0]: r[1:] for r in conn.execute(f"SELECT url, {columns} FROM docs")}


def test_the_build_gives_the_new_titles_after_the_copies_are_merged(built):
    report, conn, _, _ = built
    got = by_url(conn, "title, linktext, period_ay, period_sem, doc_type, tier")
    # 20 documents share the title once the two addresses of one page are one: all of them take their own
    assert conn.execute("SELECT count(*) FROM docs WHERE kind_class = 'page'").fetchone() == (21,)
    assert got[CHAIR + "1"] == ("114學年度第2學期課程地圖第1版", SOFT, 114, 2, "", PRIMARY)  # and the term it names
    assert got[CHAIR + "20"][:4] == ("114學年度第1學期課程地圖第20版", SOFT, 114, 1)
    assert got[CHAIR + "30"][:2] == ("系所簡介", "")
    # the files: a name without a Han character gives way to the text's own title, and what that title says counts
    assert got[CHAIR + "f/643-79-8.odt"] == ("113學年度第1次系務會議紀錄", "643-79-8.odt", 113, None, MINUTES, BACKGROUND)
    assert got[CHAIR + "f/115-1a.pdf"][:4] == ("選課時程表", "115-1a.pdf", 115, 1)  # the term only its old name said
    assert got[CHAIR + "f/停修申請表.pdf"][:2] == ("停修申請表.pdf", "")
    assert got[CHAIR + "f/0423.jpg"][:2] == ("0423.jpg", "")
    # plan 2026-11-14 T6 reads the new title too: the minutes belong to the year their own title names
    assert by_url(conn, "expires, valid_ay")[CHAIR + "f/643-79-8.odt"] == (1, 113)
    titles = dict(report.titles)
    examples = titles.pop("examples")
    # labels and label (11-15, tests/content/test_file_titles_1115.py): off unless asked, and no file here is titled
    # by a page's label
    assert titles == {"on": True, "labels": False, "same_min": 20, "max_chars": 80,
                      "shared": {"titles": 1, "documents": 20, "retitled": 20, "kept": 0, "why": {}},
                      "file": {"documents": 3, "retitled": 2, "kept": 1, "why": {"image": 1}},
                      "label": {"titles": 0, "documents": 0, "retitled": 0, "kept": 0, "why": {}, "by_site": {},
                                "examples": []},
                      "by_site": {CHAIR_SITE: {"shared": {"retitled": 20, "kept": 0}, "file": {"retitled": 2, "kept": 1}}},
                      "changed": {"period": 21, "typed": {MINUTES: 1}, "untyped": {}}}
    assert len(examples["shared"]) == 20 and [e["old"] for e in examples["file"]] == ["643-79-8.odt", "115-1a.pdf"]
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('own_titles', 'titles_json')"))
    assert meta["own_titles"] == "on" and json.loads(meta["titles_json"]) == report.titles
    # an attachment still titled by its file name is counted as one; the two with a title of their own are not
    assert report.counts["attachments"]["title_file_name"] == 2


def test_both_titles_find_the_document(built):
    _, conn, _, _ = built
    file_id = conn.execute("SELECT doc_id FROM docs WHERE url = ?", (CHAIR + "f/643-79-8.odt",)).fetchone()[0]
    for query in ("系務會議紀錄", "643-79-8.odt"):
        assert file_id in [h.doc_id for h in search_docs(conn, query, today=TODAY, aliases=[]).hits], query
    page_id = conn.execute("SELECT doc_id FROM docs WHERE url = ?", (CHAIR + "7",)).fetchone()[0]
    tokens = conn.execute("SELECT f.title_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
                          " WHERE c.doc_id = ? LIMIT 1", (page_id,)).fetchone()[0].split()
    assert {"课程", "地图", "软体", "term114s2"} <= set(tokens)  # the new title, the old one and the term


def test_without_the_switch_the_build_is_the_one_of_before(built, tmp_path, capsys):
    report, conn, inputs, paths = built
    out = tmp_path / "db" / "off.db"
    off = tm.run(paths, out, inputs=inputs, own_titles=False)
    plain = open_ro(out)
    try:
        got = by_url(plain, "title, linktext, period_ay, period_sem, doc_type, tier")
        assert plain.execute("SELECT value FROM meta WHERE key = 'own_titles'").fetchone() == ("off",)
        # under its file name the file has no year of its own: its notice's (March 2026 is in the 114 academic year)
        assert by_url(plain, "expires, valid_ay")[CHAIR + "f/643-79-8.odt"] == (1, 114)
        ids = "SELECT doc_id, url, kind_class, text, ident_date, parent_id, parent_title, unit_line FROM docs ORDER BY doc_id"
        assert plain.execute(ids).fetchall() == conn.execute(ids).fetchall()  # the titles aside, the same documents
    finally:
        plain.close()
    assert got[CHAIR + "1"] == (SOFT, "", None, None, "", PRIMARY)
    assert got[CHAIR + "f/643-79-8.odt"] == ("643-79-8.odt", "", None, None, "", PRIMARY)
    assert got[CHAIR + "f/115-1a.pdf"][:4] == ("115-1a.pdf", "", 115, 1)
    assert off.titles["on"] is False and off.titles["shared"]["kept"] == 20 and off.titles["file"]["kept"] == 3
    assert off.titles["changed"] == {"period": 0, "typed": {}, "untyped": {}}
    assert off.counts["attachments"]["title_file_name"] == 4 == report.counts["attachments"]["title_file_name"] + 2
    # the command line
    (corpus, export), = inputs
    rest = ["--input", f"{corpus}::{export}", "--aliases", str(paths["aliases"]), "--server-dates",
            str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*rest, "--out", str(tmp_path / "cli" / "on.db")]) == 0
    assert "标题换成文件自己的 22 份（同名网页 20 份里换 20、留 0；档名没有汉字的附件 3 份里换 2、留 1）" in capsys.readouterr().out
    assert build.main([*rest, "--out", str(tmp_path / "cli2" / "off.db"), "--no-own-titles"]) == 0
    assert "没换标题（--no-own-titles）" in capsys.readouterr().out
    saved = json.loads((tmp_path / "cli2" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["titles"]["on"] is False


def test_what_only_the_old_title_said_plainly_still_counts(tmp_path):
    # 20 pages of a list of past exam papers, each titled 歷屆試題: an exam by its title before, and still one
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + str(n), "title": "歷屆試題", "text": f"{100 + n}學年度碩士班計算機概論\n試題內容"}
            for n in range(1, 21)]
    out = tmp_path / "db" / "x.db"
    tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[site(tmp_path, rows)])
    conn = open_ro(out)
    try:
        got = by_url(conn, "title, doc_type, tier, period_ay")
    finally:
        conn.close()
    assert got[CHAIR + "3"] == ("103學年度碩士班計算機概論", EXAM, BACKGROUND, 103)
