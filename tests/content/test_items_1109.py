"""Owner decisions 15 and 17 of 11-09 (blueprint 第五节, 「照建议定」):

- item 15: a page the export marked 内文区是空的 (its platform's own title and text areas there and empty: the school
  deleted the notice and only the site's template is left), or 找不到内文区 with no text read either, is left out,
  counted as <kind>(学校删掉), and each site's count is written to sites.deleted_notices;
- item 17: 教务处's old 校刊 (spaper) and 校讯 (sinfo) folders are journals, so background, like the 學報 folder.

An export without 缺什么 marks gives the documents of before. The titles in the fixtures are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from nknu.archive.export import EMPTY_AREA, NO_AREA
from nknu.content import build
from nknu.content.assemble import assemble
from nknu.content.corpus import AREA_EMPTY, AREA_MARKS, AREA_MISSING, Record, load_corpus
from nknu.content.dates import OWN_FIELD
from nknu.content.documents import DELETED, build_documents, deleted_notice
from nknu.content.schema import BACKGROUND, JOURNAL, PRIMARY, SCHEMA_VERSION
from nknu.retrieval import respond
from tests.content import test_assemble as ta
from tests.content import test_multisite_1108 as tm
from tests.totext import test_pages_1108 as tp

ACA = "https://c.nknu.edu.tw/aca/"
WEB_SITE = "sso.nknu.edu.tw/web/595"
WEB_DETAIL = "https://sso.nknu.edu.tw/web/595/index.aspx?selpage=MoreNews&newsidx=1326"
GEO = "https://geo.nknu.edu.tw/news/detail/1326"
TODAY = date(2026, 11, 9)


def nid(n: int, scheme: str = "https") -> str:
    return f"{scheme}://c.nknu.edu.tw/aca/newsdetail.aspx?nid={n}&gi=2"


def rec(rid: str, kind: str, url: str, title: str = "公告", text: str = "", area_mark: str = "", **more) -> Record:
    more.setdefault("fetched", "2026-11-07T07:00:00+08:00")
    return Record(rid, kind, url, f"pages/{rid}.html", title, text, "", (), area_mark=area_mark, **more)


# ---------------------------------------------------------------- the export's words (item 15)

def test_the_marks_are_the_leading_words_of_the_exports_reasons():
    assert AREA_MARKS == (AREA_EMPTY, AREA_MISSING) == ("内文区是空的", "找不到内文区")
    assert EMPTY_AREA.startswith(AREA_EMPTY) and NO_AREA.startswith(AREA_MISSING)
    assert DELETED == "(学校删掉)"


def assemble_with(tmp_path: Path, rows: list[dict], results: dict, extra: dict[int, dict], *,
                  site: str = "c.nknu.edu.tw/aca", name: str = "教务处") -> tuple[list[dict], Path, Path]:
    """ta.write_export's export of the site with the columns of extra (序号 -> {column: value}) added to its rows,
    assembled without the 10-29 fallback: (corpus.json's records, the corpus' path, the export)."""
    export = ta.write_export(tmp_path / "export", rows, site, name)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["项目"]:
        item.update(extra.get(int(item["序号"]), {}))
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    totext = ta.fake_totext(tmp_path, export, results)
    out = tmp_path / "out" / "corpus.json"
    assemble(export, totext, out, fallback=None)
    return json.loads(out.read_text(encoding="utf-8")), out, export


def test_assemble_flags_rows_whose_areas_are_missing_or_empty(tmp_path):
    extra = {1: {"缺什么": EMPTY_AREA},  # the notice: alone
             3: {"缺什么": "连接失败、" + NO_AREA},  # the album page: joined after another reason
             6: {"缺什么": "连接失败"},  # another reason only
             7: {"缺什么": NO_AREA + "、连接失败"},  # the archive: its members carry its row's
             8: {"缺什么": ""}}
    records, out, _ = assemble_with(tmp_path, ta.base_rows(), ta.base_results(), extra)
    got = {r["url"]: r["area_mark"] for r in records}
    assert (got[ta.NOTICE], got[ta.ALBUM]) == (AREA_EMPTY, AREA_MISSING)
    assert got[ACA + "uploadfile/news/1/a6.pdf"] == got[ACA + "uploadfile/news/1/a8.pdf"] == ""
    members = [v for u, v in got.items() if u.startswith(ta.ZIP_URL + "#")]
    assert members == [AREA_MISSING, AREA_MISSING]
    loaded = {r.url: r.area_mark for r in load_corpus(out)}
    assert loaded == got


def test_an_export_without_the_column_flags_nothing(tmp_path):
    records, _, _ = assemble_with(tmp_path, ta.base_rows(), ta.base_results(), {})
    assert {r["area_mark"] for r in records} == {""}
    older = tmp_path / "older.json"  # a corpus assembled before: no such field
    older.write_text(json.dumps([{"id": "p1", "kind": "newsdetail", "url": nid(1)}]), encoding="utf-8")
    assert load_corpus(older)[0].area_mark == ""


# ---------------------------------------------------------------- build_documents (item 15)

def test_a_notice_shell_is_left_out_and_counted():
    for kind, url in (("newsdetail", nid(9)), ("detail:sso_webidx", WEB_DETAIL)):
        for mark in AREA_MARKS:  # no text read: either mark
            shell = rec("n1", kind, url, area_mark=mark)
            assert deleted_notice(shell)
            docs, excluded = build_documents([shell], {}, [])
            assert docs == [] and excluded == {kind + "(学校删掉)": 1}


def test_a_page_the_platforms_date_field_made_a_notice_is_left_out_too():
    shell = rec("p1", "page:geo", GEO, area_mark=AREA_EMPTY, page_date="2026-09-11", page_date_raw="2026-09-11",
                date_source=OWN_FIELD)
    assert build_documents([shell], {}, []) == ([], {"page:geo(学校删掉)": 1})


def test_areas_missing_but_text_read_stays():
    # a template the export's profile does not know yet: the 11-08 export marked 156 real tecs notices 找不到内文区
    notice = rec("n1", "newsview", "https://tecs.nknu.edu.tw/newsview.aspx?nid=9", text="教檢報名公告",
                 area_mark=AREA_MISSING)
    assert not deleted_notice(notice)
    docs, excluded = build_documents([notice], {}, [])
    assert [(d.doc_id, d.kind_class, d.text) for d in docs] == [("dn1", "notice", "教檢報名公告")]
    assert excluded == {}
    page = rec("p1", "page", ACA + "page.aspx?pn=71&gi=5", title="招生資訊", text="招生資訊", area_mark=AREA_MISSING)
    assert [d.doc_id for d in build_documents([page], {}, [])[0]] == ["dp1"]
    # whitespace only is no text
    assert deleted_notice(rec("n2", "newsdetail", nid(9), text=" \n　", area_mark=AREA_MISSING))


def test_areas_empty_is_a_shell_whatever_was_read():
    # 电机系's 3 of 11-09: page:elec (the platform is read as a whole body), its menus read, the export's areas empty
    elec = rec("p1", "page:elec", "https://elec.nknu.edu.tw/news_ii.html?type=1&id=9", title="電機系",
               text="系所簡介 師資陣容 最新消息", area_mark=AREA_EMPTY)
    assert deleted_notice(elec)
    assert build_documents([elec], {}, []) == ([], {"page:elec(学校删掉)": 1})
    # a record the build leaves out by its kind is never counted as one
    listed = rec("l1", "list:sso_webidx", WEB_DETAIL + "&selpage=MoreNews", area_mark=AREA_EMPTY)
    assert not deleted_notice(listed)


def test_an_unflagged_notice_without_text_stays_as_before():
    notice = rec("n1", "newsdetail", nid(9))
    docs, excluded = build_documents([notice], {}, [])
    assert [(d.doc_id, d.kind_class, d.has_text) for d in docs] == [("dn1", "notice", False)] and excluded == {}


def test_at_one_address_the_real_notice_is_kept_and_the_shell_counted_as_deleted():
    shell = rec("n1", "newsdetail", nid(9, "http"), area_mark=AREA_EMPTY)  # first, and written without &amp;
    real = rec("n2", "newsdetail", nid(9), text="停修申請期限延長")
    docs, excluded = build_documents([shell, real], {}, [])
    assert [d.doc_id for d in docs] == ["dn2"] and docs[0].duplicates == ()
    assert excluded == {"newsdetail(学校删掉)": 1}  # never (重复)


# ---------------------------------------------------------------- build_sites: each site's count (item 15)

def shells_exports(tmp_path: Path) -> list[tuple[Path, Path]]:
    """教务处's export with one notice and two shells (one of each mark), 生活輔導組's with a notice and a page marked
    找不到内文区 whose text was read (kept): both assembled."""
    aca_rows = [{"type": "网页", "url": nid(n), "file": f"pages/000{n}.html", "title": title,
                 "data": ta.notice_html(body=body, title=title)}
                for n, title, body in ((1, "停修公告", "<p>停修</p>"), (2, "公告", ""), (3, "公告", ""))]
    aca_results = {"1": (tm.PAGE, "停修公告\n停修申請期限延長"), "2": (tm.PAGE, ""), "3": (tm.PAGE, "")}
    aca = assemble_with(tmp_path / "aca", aca_rows, aca_results,
                        {2: {"缺什么": EMPTY_AREA}, 3: {"缺什么": "连接失败、" + NO_AREA}})
    web_rows = [{"type": "网页", "url": tm.DETAIL, "file": "pages/0001.html", "title": "學分學程申請表下載",
                 "data": tp.webidx_page(tm.DETAIL_HTML)},
                {"type": "网页", "url": tp.WEB, "file": "pages/0002.html", "title": "生輔組首頁",
                 "data": tp.webidx_page("", area=False)}]
    web_results = {"1": (tm.PAGE, "學分學程申請表下載\n請下載申請表。"), "2": (tm.PAGE, "生輔組首頁\n服務項目")}
    web = assemble_with(tmp_path / "web", web_rows, web_results, {2: {"缺什么": NO_AREA}}, site=WEB_SITE,
                        name="学务处生活辅导组")
    return [(aca[1], aca[2]), (web[1], web[2])]


def test_each_sites_deleted_notices_are_in_the_sites_table(tmp_path):
    inputs = shells_exports(tmp_path)
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "db" / "c.db"
    report = build.build_sites(inputs, out, server_dates=None, aliases=paths["aliases"], today=TODAY)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        rows = dict(conn.execute("SELECT site, deleted_notices FROM sites"))
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        kinds = dict(conn.execute("SELECT kind_class, count(*) FROM docs WHERE site = ? GROUP BY kind_class",
                                  (WEB_SITE,)))
    finally:
        conn.close()
    assert rows == {"c.nknu.edu.tw/aca": 2, WEB_SITE: 0}
    assert report.excluded.get("newsdetail" + DELETED) == 2 == sum(rows.values())
    assert sum(n for k, n in report.excluded.items() if k.endswith(DELETED)) == 2
    assert [s["deleted_notices"] for s in report.sites] == [2, 0]
    assert "page" in kinds  # the page whose areas are missing but whose text was read stays
    assert meta["schema_version"] == SCHEMA_VERSION == "pipeline-7"  # pipeline-7: sites.under_read (item 22)
    assert "deleted_notices" in build.SITE_COLUMNS


def test_main_says_how_many_it_left_out(tmp_path, capsys):
    inputs = shells_exports(tmp_path)
    paths = tm.tree_files(tmp_path / "tree")
    pairs = [arg for corpus, export in inputs for arg in ("--input", f"{corpus}::{export}")]
    out = tmp_path / "o" / "c.db"
    assert build.main([*pairs, "--out", str(out), "--aliases", str(paths["aliases"]), "--server-dates",
                       str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]) == 0
    printed = capsys.readouterr().out
    assert "学校删掉的公告 2（不收，每站数见 sites）" in printed and "学报（含旧校刊、校讯）" in printed
    report = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert [s["deleted_notices"] for s in report["sites"]] == [2, 0]


def test_without_shells_main_says_nothing_of_them(tmp_path, capsys):
    paths = tm.two_exports(tmp_path)
    out = tmp_path / "o" / "c.db"
    pairs = [arg for corpus, export in paths["inputs"] for arg in ("--input", f"{corpus}::{export}")]
    assert build.main([*pairs, "--out", str(out), "--aliases", str(paths["aliases"]), "--server-dates",
                       str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]) == 0
    assert "学校删掉的公告" not in capsys.readouterr().out
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert {n for (n,) in conn.execute("SELECT deleted_notices FROM sites")} == {0}
    finally:
        conn.close()


# ---------------------------------------------------------------- the old 校刊 and 校讯 (item 17)

def test_the_old_school_paper_and_news_folders_are_journals_so_background():
    for url in (ACA + "uploadfile/spaper/88/p1.htm", ACA + "uploadfile/spaper/88/p1.pdf",
                "https://c.nknu.edu.tw/~publish2/sinfo/12/a.jpg", ACA + "uploadfile/sinfo/12/index.htm",
                "https://c.nknu.edu.tw/~publish2/spaper/3/b.jpg"):
        for kind in ("page", "attachment"):
            dtype = build.doc_type("第 3 頁", "", url, kind)
            assert dtype == JOURNAL, (url, kind)
            assert build.tier_of("du1", dtype) == BACKGROUND
    for url in (ACA + "uploadfile/news/1/a6.pdf", ACA + "newsdetail.aspx?nid=1&gi=2"):
        assert build.doc_type("第 3 頁", "", url, "attachment") == ""
        assert build.tier_of("du1", "") == PRIMARY


def test_agents_are_told_the_old_paper_and_news_are_background():
    assert "學報、舊校刊和校訊" in respond.NOTE_BACKGROUND


def test_a_corpus_assembled_before_the_marks_is_warned_of(tmp_path):
    inputs = shells_exports(tmp_path)
    corpus = inputs[0][0]
    stale = [{k: v for k, v in r.items() if k != "area_mark"} for r in json.loads(corpus.read_text(encoding="utf-8"))]
    corpus.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    paths = tm.tree_files(tmp_path / "tree")
    report = build.build_sites(inputs, tmp_path / "db" / "c.db", server_dates=None, aliases=paths["aliases"],
                               today=TODAY)
    assert [s["deleted_notices"] for s in report.sites] == [0, 0]
    assert any("第 1 份输入" in w and "没有 area_mark" in w and "2 行" in w for w in report.warnings)
    assert not any("第 2 份输入" in w and "area_mark" in w for w in report.warnings)
