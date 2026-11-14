"""Plan 2026-11-14 T2 item 4 (direction 2026-11-13 第 2 件 4; the owner agreed on 11-11): the alumni directory of the
c.nknu template is background material.

Its pages are told by their page kind, never by their site: on a c.nknu-template host pagetext.page_kind names a page
by its leaf, so the detail page of one alumnus is student_detail and the directory's list is student (a row a press
brought: …#翻页/按钮). They stay documents, found by a name as before; search places them after the primary documents.
build_sites(alumni_background=False) / --no-alumni-background builds as before. The people are O masks; no model
loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nknu.content import build, ids
from nknu.content.corpus import LIST_FIRST, LIST_PAGER
from nknu.content.schema import BACKGROUND, JOURNAL, PRIMARY
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import search_docs
from nknu.totext import pagetext
from tests.content import test_multisite_1108 as tm

TODAY = date(2026, 11, 14)
FETCHED = "2026-11-01T04:41:00+08:00"
TG, TG_SITE = "https://c.nknu.edu.tw/tg/", "c.nknu.edu.tw/tg"
ALUMNUS, ROLL = TG + "student_detail.aspx?aid=12", TG + "student.aspx?pclass=1"
TEACHER, NOTICE = TG + "teachercorner.aspx?tid=3", TG + "newsdetail.aspx?nid=9"


def test_the_page_kinds_are_the_ones_the_page_reader_gives():
    # what the corpus record carries of the platform page (Record.kind, docs.kind_raw)
    assert pagetext.page_kind(ALUMNUS) == "student_detail" and pagetext.page_kind(ROLL) == "student"
    assert pagetext.page_kind(ROLL + "#第2页") == "student#翻页/按钮"
    assert pagetext.page_kind("https://tecs.nknu.edu.tw/student_detail.aspx?aid=3") == "student_detail"  # any such host
    assert pagetext.page_kind("https://www.nknu.edu.tw/student_detail.aspx?aid=3") == "page"  # no c.nknu template
    assert build.ALUMNI_KINDS == ("student_detail", "student")


def test_tier_of_puts_the_alumni_directory_in_the_background_by_its_page_kind():
    for kind in ("student_detail", "student", "student#翻页/按钮", "student_detail#翻页/按钮"):
        assert build.tier_of("du1", "", kind) == BACKGROUND, kind
        assert build.tier_of("du1", "", kind, alumni=False) == PRIMARY, kind
    for kind in ("teachercorner", "teacher", "forumsview", "students", "student_list", "page", "page:sso_clv2",
                 "newsdetail", "detail:sso_news", "附件:pdf:有文字层", "外站:student", ""):
        assert build.tier_of("du1", "", kind) == PRIMARY, kind
    # as before: an archive member, a journal, and a call without the page kind
    assert build.tier_of("dm1", "", "page") == BACKGROUND and build.tier_of("du1", JOURNAL, "page") == BACKGROUND
    assert build.tier_of("dm1", "") == BACKGROUND and build.tier_of("du1", "") == PRIMARY


def tg_input(root: Path) -> tuple[Path, Path]:
    """成教所's export, written by hand: one alumnus's page, the directory's first page and a pager page of it, a
    teacher's page and a notice."""
    rows = [
        {"kind": "student_detail", "url": ALUMNUS, "title": "王O明",
         "text": "王O明\n畢業級別：第 12 屆\n論文題目：社區大學學員學習動機之研究\n服務單位：O O 國民小學"},
        {"kind": "student", "url": ROLL, "title": "所友名錄", "text": "所友名錄\n第 12 屆 王O明\n第 12 屆 李O華",
         "list_kind": LIST_FIRST},
        {"kind": "student#翻页/按钮", "url": ROLL + "#第2页", "title": "所友名錄", "text": "所友名錄\n第 11 屆 陳O宏",
         "list_kind": LIST_PAGER},
        {"kind": "teachercorner", "url": TEACHER, "title": "專任教師", "text": "專任教師\n研究專長：社區大學與學習動機"},
        {"kind": "newsdetail", "url": NOTICE, "title": "論文口試公告", "text": "本所論文口試時程公告，社區大學專題組請留意。"},
    ]
    export = root / "export"
    export.mkdir(parents=True)
    items, records = [], []
    for n, row in enumerate(rows, start=1):
        key = ids.location_key(row["url"])
        file = f"pages/{n:04d}.html"
        items.append({"序号": str(n), "类型": "网页", "网址": row["url"], "最后落地网址": "", "本地档案": file,
                      "抓取时间": FETCHED})
        records.append({"id": ids.record_id(key), "key": key, "file": file, "site": TG_SITE, "fetched": FETCHED, **row})
    (export / "manifest.json").write_text(
        json.dumps({"网站": TG_SITE, "名称": "成人教育研究所", "项目": items}, ensure_ascii=False), encoding="utf-8")
    (root / "corpus.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return root / "corpus.json", export


def doc_id(url: str) -> str:
    return "d" + ids.record_id(ids.location_key(url))


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "db" / "x.db"
    inputs = [tg_input(tmp_path / "tg")]
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=inputs)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn, inputs
    conn.close()


def test_the_build_marks_the_directorys_pages_background(built):
    report, conn, _ = built
    tiers = dict(conn.execute("SELECT doc_id, tier FROM docs"))
    assert tiers == {doc_id(ALUMNUS): BACKGROUND, doc_id(ROLL): BACKGROUND, doc_id(TEACHER): PRIMARY,
                     doc_id(NOTICE): PRIMARY}  # the pager page is left out by its mark, as before
    assert report.counts["tiers"] == {PRIMARY: 2, BACKGROUND: 2}
    assert report.dedupe["alumni"] == {"on": True, "background": 2}
    assert conn.execute("SELECT value FROM meta WHERE key = 'alumni_background'").fetchone() == ("on",)
    # still a document, found by the name; after the primary documents and marked background
    hits = [(h.doc_id, h.background) for h in search_docs(conn, "社區大學 學習動機", today=TODAY, aliases=[]).hits]
    assert set(hits[:-1]) == {(doc_id(TEACHER), False), (doc_id(NOTICE), False)}
    assert hits[-1] == (doc_id(ALUMNUS), True)
    shown = build_response(conn, search_docs(conn, "王O明", today=TODAY, aliases=[]), today=TODAY)
    assert {r["id"]: r.get("background") for r in shown["results"]} == {doc_id(ALUMNUS): True, doc_id(ROLL): True}


def test_without_the_switch_the_directory_is_primary_as_before(built, tmp_path, capsys):
    _, _, inputs = built
    paths = tm.tree_files(tmp_path / "tree2")
    out = tmp_path / "db" / "off.db"
    off = tm.run(paths, out, inputs=inputs, alumni_background=False)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert {t for (t,) in conn.execute("SELECT tier FROM docs")} == {PRIMARY}
        assert conn.execute("SELECT value FROM meta WHERE key = 'alumni_background'").fetchone() == ("off",)
    finally:
        conn.close()
    assert off.counts["tiers"] == {PRIMARY: 4} and off.dedupe["alumni"] == {"on": False, "background": 0}
    # the command line
    (corpus, export), = inputs
    rest = ["--input", f"{corpus}::{export}", "--aliases", str(paths["aliases"]), "--server-dates",
            str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*rest, "--out", str(tmp_path / "cli" / "on.db")]) == 0
    printed = capsys.readouterr().out
    assert "所友名录 2 份算背景" in printed and "背景资料共 2" in printed
    assert build.main([*rest, "--out", str(tmp_path / "cli2" / "off.db"), "--no-alumni-background"]) == 0
    printed = capsys.readouterr().out
    assert "所友名录没算背景（--no-alumni-background）" in printed and "背景资料共 0" in printed
