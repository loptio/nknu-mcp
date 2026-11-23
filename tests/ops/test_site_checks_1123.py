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


# ---------------------------------------------------------------- a list longer than the page draws (port audit 11-23)
# site:quality-all-rows: the card drew the first SHOW rows of a reminder and said how many there were, with no way to
# the rest. The rest now come from the server a part at a time (api: GET /site/quality), each part as the card draws it.

LONG = 650  # 無日期 documents: more than two parts


def _long(tmp_path, built=BUILT):
    """A library file (meta + docs) and the build's lists beside it: 無日期 with LONG documents, 標題重複 in two titles
    (the second over one part), 選單混入正文 with two."""
    nd = [f"n{i:03d}" for i in range(LONG)]
    second = [f"t{i:03d}" for i in range(320)]
    data = {"built_at": built, "sites": {SITE: {
        "flags": ["no_date"],
        "same_title": {"total": 322, "groups": [{"title": "公告", "total": 2, "truncated": False, "docs": ["n000", "n001"]},
                                                {"title": "活動<花絮>", "total": 320, "truncated": False, "docs": second}]},
        "same_opening": {"total": 2, "of": 9, "opening": "首頁 關於我們", "truncated": False, "docs": ["n002", "n003"]},
        "no_date": {"total": LONG, "truncated": False, "docs": nd}}}}
    (tmp_path / "site_check_docs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "aca.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (BUILT,))
    conn.execute("CREATE TABLE docs (doc_id, kind_class, title, url, ident_date, date_line, ident_kind, text)")
    conn.executemany("INSERT INTO docs VALUES (?, 'page', ?, ?, NULL, ?, '', '')",
                     [(i, f"第 {i} 頁", f"https://c.nknu.edu.tw/aca/{i}.aspx", f"日期原文{i}") for i in nd + second])
    conn.commit()
    return db, conn


def test_a_long_list_draws_its_first_part_and_a_button_for_the_rest(tmp_path):
    _db, conn = _long(tmp_path)
    tree = LexborHTMLParser(site_checks.card(SITE, site_checks.load(tmp_path, BUILT), conn))
    assert tree.css_first("#quality").attributes["data-site"] == SITE  # the button asks for this site's rows
    nd = tree.css_first(".sq-p[data-k='no_date']")
    assert len(nd.css("tbody tr")) == site_checks.SHOW == 300
    more = nd.css(".sq-more-b")
    assert len(more) == 1 and more[0].attributes["data-k"] == "no_date" and more[0].attributes["data-g"] == "0"
    assert (more[0].attributes["data-at"], more[0].attributes["data-of"]) == ("300", str(LONG))
    assert "再列 300 份" in more[0].text() and "共 650 份" in nd.css_first(".sq-more").text()
    groups = tree.css(".sq-p[data-k='same_title'] .sq-list")
    assert [len(g.css(".sq-more-b")) for g in groups] == [0, 1]  # each title its own list, its own button
    assert groups[1].css_first(".sq-more-b").attributes["data-g"] == "1"
    assert "再列 20 份" in groups[1].css_first(".sq-more-b").text()
    assert not tree.css(".sq-p[data-k='same_opening'] .sq-more-b")  # all drawn: no button


def test_the_rest_of_a_list_comes_a_part_at_a_time_as_the_card_draws_it(tmp_path):
    db, _conn = _long(tmp_path)
    status, got = site_checks.api(db, {"site": SITE, "k": "no_date", "g": "0", "at": "300"})
    assert status == 200 and (got["at"], got["of"]) == (600, LONG)
    rows = LexborHTMLParser(f"<table><tbody>{got['rows']}</tbody></table>").css("tbody tr")
    assert len(rows) == 300
    first = rows[0].css_first("a.sq-t").attributes["href"]
    assert first.startswith("/trace?ref=n300&ck=no_date&site=c.nknu.edu.tw%2Faca&why=")
    assert rows[0].css_first("td.sq-x").text() == "日期原文n300"  # the reminder's own column, as on the card
    status, last = site_checks.api(db, {"site": SITE, "k": "no_date", "g": "0", "at": "600"})
    assert status == 200 and (last["at"], last["of"]) == (LONG, LONG) and last["rows"].count("<tr>") == 50
    status, title = site_checks.api(db, {"site": SITE, "k": "same_title", "g": "1", "at": "300"})
    assert status == 200 and title["rows"].count("<tr>") == 20 and (title["at"], title["of"]) == (320, 320)
    href = LexborHTMLParser(title["rows"]).css_first("a.sq-t").attributes["href"]
    assert "ck=same_title" in href and "%E6%B4%BB%E5%8B%95%3C%E8%8A%B1%E7%B5%AE%3E" in href  # the title's why, quoted
    assert "<花絮>" not in title["rows"]  # every word from the library or the build is escaped


def test_a_part_that_is_not_there_is_refused(tmp_path):
    db, _conn = _long(tmp_path)
    for asked in ({"site": SITE, "k": "bogus", "g": "0", "at": "0"}, {"site": SITE, "k": "self", "g": "0", "at": "0"},
                  {"site": SITE, "k": "same_title", "g": "2", "at": "0"}, {"site": "other.example", "k": "no_date"},
                  {"site": "", "k": "no_date"}):
        status, got = site_checks.api(db, asked)
        assert status == 404 and "rows" not in got, asked
    status, odd = site_checks.api(db, {"site": SITE, "k": "no_date", "g": "x", "at": "-3"})
    assert status == 200 and odd["at"] == 300  # not a number: the first part
    (tmp_path / "old").mkdir()
    old, _ = _long(tmp_path / "old", built="2026-11-22T02:53:09+08:00")  # the library's own built_at stays BUILT
    assert site_checks.api(old, {"site": SITE, "k": "no_date", "at": "0"})[0] == 404  # an older list: not this library's
