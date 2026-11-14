"""11-15, for the owner's 「请看一下附件标题取自链接文字还是档名的规则对不对，能修的话在这轮重建前一起修掉」 (the
inspection tools found a file named 普通教室管理及使用要點 titled 「7 , 普通教室借用申請單」).

A file is titled by what the page linking it from its text calls it, its file name otherwise. Two places where that
went wrong 【Claude】: a site map writes the site's menu out as a page, so the menu's names for its files read as a
page's (assemble.MENU_PAGES: its links are a menu's); and a name many files of one site take from their pages is a
label, not a name (documents.own_titles' TITLE_LABEL: such a file is titled by its file name again when that has a
Han character; off unless the build is asked, --label-titles, since one of the 33 questions fell for it on the 111
sites). And a merged copy's link names stay findable in the document kept for it (build._take_over; --no-copy-names
builds as before): without them the first change lost a file's best name. The sentences are made up; no model
loads."""

from __future__ import annotations

import sqlite3

from nknu.content import assemble, build, documents
from nknu.content.corpus import Record, _pages
from nknu.content.documents import build_documents, own_titles
from tests.content import test_assemble as ta
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_own_titles_1114 as to

SITEMAP = ta.ACA + "sitemap.aspx"
RULE = ta.ACA + "uploadfile/rule.pdf"


def sitemap_html(href: str = "uploadfile/rule.pdf") -> bytes:
    """c.nknu's site map: the menu as a nested list inside the page's content area, each entry numbered."""
    return ta.notice_html(body=f'<ul><li>教室借用<ul><li><a href="{href}">7 , 普通教室借用申請單</a></li></ul></li></ul>',
                          title="網站導覽")


# ---------------------------------------------------------------- a site map is the menu written out

def test_a_site_maps_links_are_a_menus(tmp_path):
    page = ta.notice_html(body='<p>請參閱<a href="uploadfile/rule.pdf">普通教室管理及使用要點</a></p>')
    r, report = ta.links_export(tmp_path, [(SITEMAP, sitemap_html()), (ta.NOTICE, page)],
                                [(RULE, [SITEMAP, ta.NOTICE], ta.BODY)])
    rule = r["a3"]
    assert (rule["linked"], rule["nav_linked"]) == (ta.NOTICE, SITEMAP)
    assert rule["zones"] == {SITEMAP: "nav", ta.NOTICE: "content"}
    assert rule["contexts"][ta.NOTICE] == "普通教室管理及使用要點"
    assert report.nav_only == 0


def test_a_file_only_the_site_map_and_the_menus_name_keeps_its_file_name(tmp_path):
    menu = ta.notice_html(menu='<li><a href="uploadfile/rule.pdf">普通教室借用申請單</a></li>')
    r, report = ta.links_export(tmp_path, [(SITEMAP, sitemap_html()), (ta.NOTICE, menu)],
                                [(RULE, [SITEMAP, ta.NOTICE], ta.BODY)])
    rule = r["a3"]
    assert (rule["linked"], rule["nav_linked"]) == ("", f"{SITEMAP} | {ta.NOTICE}")
    assert set(rule["zones"].values()) == {"nav"} and report.nav_only == 1
    # the build names a file by the pages linking it from their text only: none here, so the file name stays
    docs, _ = build_documents(ta_records(rule), {}, [])
    assert [(d.title, d.title_from_context) for d in docs] == [("rule.pdf", False)]


def ta_records(*records: dict):
    """Assembled records as the build reads them (corpus.load_corpus' fields; _pages: its splitting of the joined
    page lists)."""
    return [Record(id=r["id"], kind=r["kind"], url=r["url"], file=r["file"], title=r["title"], text=r["text"],
                   linktext=r["linktext"], linked=_pages(r["linked"]), nav_linked=_pages(r["nav_linked"]),
                   zones=r["zones"], contexts=r["contexts"], rows=r["rows"], key=r["key"], site=r["site"])
            for r in records]


def test_the_page_kinds_whose_links_are_a_menus():
    assert assemble.MENU_PAGES == {"sitemap"} and assemble.page_kind(SITEMAP) == "sitemap"
    assert assemble.MENU_PAGES <= documents.EXCLUDED_KINDS  # such a page is no document either


# ---------------------------------------------------------------- a name many files carry is a label

PAGE = to.CHAIR + "9"


def labelled_file(n: int, name: str, label: str = "檔案連結", site: str = to.CHAIR_SITE, text: str = ""):
    """A file its page calls label."""
    return tc.rec(f"a{n}", tc.PDF, f"{to.CHAIR}f/{n}/{name}", name, text or f"第 {n} 份文件的內容。", site,
                  linked=(PAGE,), contexts={PAGE: label})


def titles(records, on: bool = True, labels: bool = True):
    docs, _ = build_documents(records, {}, [])
    report = own_titles(docs, to.SCHOOL, on=on, labels=labels)
    return {d.doc_id: d for d in docs}, report


def test_twenty_files_under_one_label_take_their_file_names_back():
    files = [labelled_file(n, f"第{n}次系務會議紀錄.pdf") for n in range(1, 20)]
    files.append(labelled_file(20, "scan_0420.pdf"))  # a file name that says nothing: the label stays
    files.append(labelled_file(21, "課程地圖.pdf", label="113學年度課程地圖"))  # a name of its own
    docs, report = titles(files)
    first = docs["da1"]
    assert (first.title, first.old_title, first.file_title) == ("第1次系務會議紀錄.pdf", "檔案連結", "")
    assert (first.title_from_context, first.title_was_label, first.title_from_text) == (False, True, False)
    assert first.linktext.split(documents.LINK_SEP) == ["第1次系務會議紀錄.pdf", "檔案連結"]  # both stay findable
    assert (docs["da20"].title, docs["da20"].title_was_label) == ("檔案連結", False)
    assert (docs["da21"].title, docs["da21"].title_from_context) == ("113學年度課程地圖", True)
    assert report["labels"] is True and report["label"] == {
        "titles": 1, "documents": 20, "retitled": 19, "kept": 1, "why": {"no_name": 1},
        "by_site": {to.CHAIR_SITE: {"retitled": 19, "kept": 1}},
        "examples": [{"id": f"da{n}", "site": to.CHAIR_SITE, "old": "檔案連結", "new": f"第{n}次系務會議紀錄.pdf"}
                     for n in range(1, 20)]}
    # the file-name kind of own_titles is told apart: none of these was titled by a file name without a Han character
    assert to.counts(report["file"]) == {"documents": 0, "retitled": 0, "kept": 0, "why": {}}


def test_nineteen_files_or_twenty_over_two_sites_keep_the_name_their_page_gives():
    docs, report = titles([labelled_file(n, f"第{n}次系務會議紀錄.pdf") for n in range(1, 20)])
    assert docs["da1"].title == "檔案連結" and report["label"]["documents"] == 0
    split = [labelled_file(n, f"第{n}次會議紀錄.pdf", site=to.CHAIR_SITE if n % 2 else to.CHEM_SITE)
             for n in range(1, 21)]
    docs, report = titles(split)
    assert {d.title for d in docs.values()} == {"檔案連結"} and report["label"]["titles"] == 0


def test_switched_off_the_labels_stay_and_are_still_counted():
    files = [labelled_file(n, f"第{n}次系務會議紀錄.pdf") for n in range(1, 21)]
    for on, labels in ((True, False), (False, True)):
        docs, report = titles(files, on=on, labels=labels)
        assert docs["da1"].title == "檔案連結" and report["labels"] is False
        assert {k: report["label"][k] for k in ("titles", "documents", "retitled", "kept")} == {
            "titles": 1, "documents": 20, "retitled": 0, "kept": 20}


def test_what_the_file_name_says_is_read_again():
    # the label named no term; the file name does, and the old label still counts for what only it said
    files = [labelled_file(n, f"115學年度第1學期第{n}次會議紀錄.pdf", label="113學年度") for n in range(1, 21)]
    docs, _ = build_documents(files, {}, [])
    rows = build._rows(docs, {}, {})
    assert {(r.ay, r.sem) for r in rows} == {(113, None)}
    report = build._retitle(rows, True, True)
    assert report["label"]["retitled"] == 20 and {(r.ay, r.sem) for r in rows} == {(115, 1)}
    assert report["changed"]["period"] == 20


# ---------------------------------------------------------------- the build

def label_inputs(root):
    rows = [{"kind": "page:sso_clv2", "url": PAGE, "title": "會議紀錄", "text": "歷次系務會議紀錄如下。"}]
    rows += [{"kind": tc.PDF, "url": f"{to.CHAIR}f/{n}/m.pdf", "title": f"第{n}次系務會議紀錄.pdf",
              "text": f"第 {n} 次系務會議紀錄\n" + tc.SHORT * n, "linked": PAGE, "contexts": {PAGE: "檔案連結"},
              "zones": {PAGE: "content"}} for n in range(1, 21)]
    return [tc.site_input(root / "chair", to.CHAIR_SITE, "软体系", rows)]


def test_the_build_titles_them_by_their_file_names_when_asked_and_leaves_them_else(tmp_path):
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=label_inputs(tmp_path), label_titles=True)
    assert (report.titles["label"]["documents"], report.titles["label"]["retitled"]) == (20, 20)
    assert any("网页给的叫法是很多档共用的附件 20 份（1 种叫法）里改回档名 20、留 0" in part for part in build._v6_words(report))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'label_titles'").fetchone() == ("on",)
        got = conn.execute("SELECT title, linktext FROM docs WHERE kind_class = 'attachment' ORDER BY rowid").fetchall()
        assert got[0] == ("第1次系務會議紀錄.pdf", "第1次系務會議紀錄.pdf | 檔案連結") and len({t for t, _ in got}) == 20
    finally:
        conn.close()
    # the default: the labels stay the titles, and the report still says how many files stand under one
    left = tmp_path / "db" / "y.db"
    report = tm.run(tm.tree_files(tmp_path / "tree2"), left, inputs=label_inputs(tmp_path / "again"))
    assert report.titles["labels"] is False and report.titles["label"]["retitled"] == 0
    assert any("网页给的叫法是很多档共用的附件 20 份（1 种叫法）照旧用那个叫法当标题（--label-titles 才改回档名）" in part
               for part in build._v6_words(report))
    conn = sqlite3.connect(f"file:{left}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'label_titles'").fetchone() == ("off",)
        assert {t for (t,) in conn.execute("SELECT title FROM docs WHERE kind_class = 'attachment'")} == {"檔案連結"}
    finally:
        conn.close()


# ---------------------------------------------------------------- a merged copy's link names stay findable

def fee_copies():
    """One file's text on two sites: 國際處-like page calls its copy by a full name; 教务处's copy is 「大學部」 on its
    page and has its full name only as a menu's link text (the export's 链接文字 holds every link's text)."""
    kept = tc.rec("a1", tc.PDF, tc.WEB + "f/1/fee.pdf", "fee.pdf", tc.LONG, tc.WEB_SITE, linked=(tc.WEB + "9",),
                  contexts={tc.WEB + "9": "大學部收費表"})
    copy = tc.rec("a2", tc.PDF, tc.ACA + "f/2/大學部收費.pdf", "大學部收費.pdf", tc.LONG, tc.ACA_SITE,
                  linked=(tc.ACA + "page.aspx?pn=1",), contexts={tc.ACA + "page.aspx?pn=1": "大學部"},
                  linktext="日間大學部學雜費各學系學生收費標準 | 大學部 | pdf | 下載")
    return [kept, copy]


def test_the_kept_document_takes_its_copies_link_names():
    docs, excluded = build_documents(fee_copies(), {}, [])
    seen: dict = {}
    kept, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, seen=seen)
    assert [r.doc.doc_id for r in kept] == ["da1"] and [c.doc.doc_id for c in copies["da1"]] == ["da2"]
    # its own file name, then the copy's title and file name as before, then the copy's link names that name something
    assert kept[0].doc.linktext.split(documents.LINK_SEP) == [
        "fee.pdf", "大學部", "大學部收費.pdf", "日間大學部學雜費各學系學生收費標準"]
    assert seen["names_carried"] == {"on": True, "documents": 1, "names": 1}
    # switched off: the title and the file name alone, as before
    docs, excluded = build_documents(fee_copies(), {}, [])
    seen = {}
    kept, _ = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded, seen=seen, names=False)
    assert kept[0].doc.linktext.split(documents.LINK_SEP) == ["fee.pdf", "大學部", "大學部收費.pdf"]
    assert seen["names_carried"] == {"on": False, "documents": 0, "names": 0}


def test_the_build_finds_the_kept_document_by_a_copys_menu_name(tmp_path):
    def inputs(root):
        # two offices' sites hold the file; the first input's copy is kept, the other one's menu gives the full name
        aca = tc.site_input(root / "aca", tc.ACA_SITE, "教务处", [
            {"kind": tc.PDF, "url": tc.ACA + "f/2/fee.pdf", "title": "fee.pdf", "text": tc.LONG}])
        web = tc.site_input(root / "web", tc.WEB_SITE, "生辅组", [
            {"kind": tc.PDF, "url": tc.WEB + "f/1/fee.pdf", "title": "fee.pdf", "text": tc.LONG,
             "linktext": "日間大學部學雜費各學系學生收費標準 | 下載"}])
        return [aca, web]

    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=inputs(tmp_path))
    assert report.dedupe["copies"]["names_carried"] == {"on": True, "documents": 1, "names": 1}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'copy_names'").fetchone() == ("on",)
        url, linktext = conn.execute("SELECT url, linktext FROM docs WHERE kind_class = 'attachment'").fetchone()
        assert url.startswith(tc.ACA) and "日間大學部學雜費各學系學生收費標準" in linktext.split(documents.LINK_SEP)
        assert "下載" not in linktext.split(documents.LINK_SEP)  # a link text that names nothing is not carried
    finally:
        conn.close()
    left = tmp_path / "db" / "y.db"
    report = tm.run(tm.tree_files(tmp_path / "tree2"), left, inputs=inputs(tmp_path / "again"), copy_names=False)
    assert report.dedupe["copies"]["names_carried"] == {"on": False, "documents": 0, "names": 0}
    conn = sqlite3.connect(f"file:{left}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'copy_names'").fetchone() == ("off",)
    finally:
        conn.close()
