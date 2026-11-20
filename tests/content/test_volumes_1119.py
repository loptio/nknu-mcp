"""11-19: a whole volume is background material (the owner through session judge: several hundred pages of
proceedings, a journal, a large report, by default, so that the like is not asked about one by one; the large files
students look for stay: handbooks, compiled regulations, admission brochures, course handbooks). An attachment of
build.VOLUME_CHARS characters or more is background unless its title, the title of the page that links it or its link
text holds one of build.VOLUME_KEEP; never by its text (a paper's own title says 課程 often enough); never a page or
a notice. --no-volumes: as before. The sentences are made up; no model loads."""
from __future__ import annotations

import sqlite3

from nknu.content import build
from nknu.content.schema import BACKGROUND, PRIMARY
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

ACA, ACA_SITE, PDF = tc.ACA, tc.ACA_SITE, tc.PDF
LONG = "第一章　緒論：本研究探討課程與教學的關係，並說明研究方法。" * 3600  # over VOLUME_CHARS characters


def row(name: str, title: str, text: str, kind: str = PDF, linktext: str = "") -> dict:
    return {"kind": kind, "url": f"{ACA}f/{name}", "title": title, "text": text, **({"linktext": linktext} if linktext else {})}


def tiers(path) -> dict[str, str]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return dict(conn.execute("SELECT title, tier FROM docs"))


def test_the_threshold_and_the_words_that_keep_a_long_file_primary():
    assert build.VOLUME_CHARS == 100_000 and len(LONG) >= build.VOLUME_CHARS
    for kept in ("學生手冊", "115學年度招生簡章", "師資培育法規選輯", "課程架構對照表", "學分抵免申請表"):
        assert build.VOLUME_KEEP.search(kept), kept
    for volume in ("2021學術研討會論文集", "學報第三十期", "成果報告", "檔案連結"):
        assert not build.VOLUME_KEEP.search(volume), volume


def test_whole_volumes_by_size_and_by_where_the_school_named_the_file():
    class Doc:
        def __init__(self, doc_id, text, title="檔案連結", parent_title="", linktext="", kind_class="attachment"):
            self.doc_id, self.text, self.title, self.parent_title = doc_id, text, title, parent_title
            self.linktext, self.kind_class = linktext, kind_class

    docs = [Doc("a", LONG, "2021學術研討會論文集"), Doc("b", LONG, "學生手冊"), Doc("c", LONG, parent_title="課程規劃"),
            Doc("d", LONG, linktext="招生簡章下載"), Doc("e", LONG, parent_title="學報專區"),
            Doc("f", LONG[:5000], "學術研討會論文集"),  # short: no volume
            Doc("g", LONG, "系所簡介", kind_class="page"),  # a page is never a volume
            Doc("h", LONG)]  # its text says 課程, the school's names for it do not
    assert build.whole_volumes(docs) == {"a", "e", "h"}
    assert build.tier_of("a", "", "", True, frozenset({"a"})) == BACKGROUND and build.tier_of("b", "", "") == PRIMARY


def test_a_build_makes_the_volume_background_and_the_switch_does_not(tmp_path):
    rows = [row("proceedings.pdf", "2021學術研討會論文集", LONG), row("handbook.pdf", "學生手冊", LONG + "手冊"),
            row("form.pdf", "請假單", "請假單的說明。")]
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    report = tm.run(paths, out, inputs=[tc.site_input(tmp_path / "s", ACA_SITE, "教务处", rows)])
    got = tiers(out)
    assert (got["2021學術研討會論文集"], got["學生手冊"], got["請假單"]) == (BACKGROUND, PRIMARY, PRIMARY)
    assert report.dedupe["volumes"] == {"on": True, "chars": build.VOLUME_CHARS, "background": 1}
    with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as conn:
        assert '"background": 1' in conn.execute("SELECT value FROM meta WHERE key='volumes_json'").fetchone()[0]
    off = tmp_path / "db" / "off.db"
    report = tm.run(paths, off, inputs=[tc.site_input(tmp_path / "s2", ACA_SITE, "教务处", rows)], volumes=False)
    assert set(tiers(off).values()) == {PRIMARY} and report.dedupe["volumes"]["on"] is False
