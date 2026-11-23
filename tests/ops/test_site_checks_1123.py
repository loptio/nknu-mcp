"""品質提醒 on a site's page (nknu.ops.site_checks, the 11-23 design): the build's own lists, only while they are this
library's; each row opens 文件頁 with the reminder that listed it; a document under several reminders says so."""
import json
import sqlite3

from selectolax.lexbor import LexborHTMLParser

from nknu.ops import design, site_checks

BUILT = "2026-11-23T02:53:09+08:00"
SITE = "c.nknu.edu.tw/aca"


def _lists(tmp_path, built=BUILT):
    data = {"built_at": built, "sites": {SITE: {
        "flags": ["same_title"],
        "self": {"sampled": 20, "found": 19, "top": 10, "deep": 50, "misses": [{"doc_id": "d1", "title": "第二頁", "place": None}]},
        "same_title": {"total": 2, "groups": [{"title": "第二頁", "total": 2, "truncated": False, "docs": ["d1", "d2"]}]},
        "no_date": {"total": 1, "truncated": False, "docs": ["d1"]},
        "empty": {"total": 0, "truncated": False, "docs": []}}}}
    (tmp_path / "site_check_docs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE docs (doc_id, kind_class, title, url, ident_date, date_line, ident_kind, text)")
    conn.executemany("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [("d1", "attachment", "第二頁", "https://c.nknu.edu.tw/aca/a.pdf", None, "", "", "一\n二"),
                      ("d2", "page", "第二頁", "https://c.nknu.edu.tw/aca/b.aspx", "2026-03-18", "", "", "")])
    return conn


def test_the_lists_are_read_only_while_they_are_this_librarys(tmp_path):
    _lists(tmp_path)
    assert site_checks.load(tmp_path, BUILT)["built_at"] == BUILT
    assert site_checks.load(tmp_path, "2026-11-22T02:53:09+08:00") is None  # an older list names other documents
    assert site_checks.load(tmp_path / "nowhere", BUILT) is None
    assert "還沒寫出來" in site_checks.card(SITE, None, sqlite3.connect(":memory:"))


def test_a_reminders_rows_open_the_document_page_with_why_and_say_every_reminder_on_hover(tmp_path):
    conn = _lists(tmp_path)
    html = site_checks.card(SITE, site_checks.load(tmp_path, BUILT), conn)
    tree = LexborHTMLParser(html)
    tabs = {b.attributes["data-k"]: b.text() for b in tree.css(".sq-tabs button")}
    assert list(tabs) == ["self", "same_title", "no_date"]  # in the build's order; a kind with no number has no tab
    assert tree.css_first(".sq-tabs button[data-k='same_title']").attributes["class"] == "flag"
    first = tree.css_first(".sq-p[data-k='same_title'] a.sq-t").attributes["href"]
    assert first.startswith("/trace?ref=d1&ck=same_title&site=c.nknu.edu.tw%2Faca&why=")
    tip = tree.css_first(".sq-p[data-k='same_title'] a.sq-t").attributes["data-tip"]
    assert tip.splitlines()[0].startswith("標題重複") and "無日期" in tip and "標題搜不到" in tip  # the one looked at first
    assert tree.css_first(".sq-p[data-k='same_title'] .sq-plus").text() == "+2"
    assert tree.css_first(".sq-p[data-k='same_title'] .ki").attributes["data-tip"] == "附件"  # a kind is its icon


def test_the_site_page_layout_and_its_layer(tmp_path):
    body = ("<header class='v3-q st-head'>x</header><div class='hm st' data-home>"
            "<section class='v3-q st-q' id='problems' data-q='3'><p>沒有問題</p></section>"
            "<section class='v3-q st-q' id='nights' data-q='5'>n</section></div>")
    out = site_checks.lay_out(body, "<section id='quality'></section>")
    assert out.index("id='sqBack'") < out.index("st-head") < out.index("id='quality'") < out.index("class='hm st'")
    assert "id='nights'" not in out and "id='problems'" not in out
    layer = design.layer("site")
    assert "design-site" in layer and "window.kic" in layer and "/check?lists=1&site=" in layer
    assert design.layer("nothing") == "" and all(design.layer(x) for x in design.LAYERS)
    assert ".html" not in "".join(design.layer(x).replace("text/html", "") for x in design.LAYERS)  # no copy page names


def test_the_site_layer_never_writes_the_address_into_the_page(tmp_path):
    """Review 11-23: the design copy's sample note put ?want= and ?site= into innerHTML, and an unknown ?ck= went into
    the back link the same way (a crafted link ran script in the panel's origin). Only a known reminder key is used,
    set as text; the back link keeps the filter."""
    js = design.layer("site")
    assert "q.get('want')" not in js and "N[ck]||ck" not in js
    assert "hasOwnProperty.call(N,ck)" in js and "b.textContent=N[ck]" in js and "back.href='/sites?ck='" in js
    (tmp_path / "site_check_docs.json").write_text("[1, 2]", encoding="utf-8")  # not the build's shape
    assert site_checks.load(tmp_path, BUILT) is None
    assert "讀不了" in site_checks.card(SITE, None, sqlite3.connect(":memory:"), broken=True)
