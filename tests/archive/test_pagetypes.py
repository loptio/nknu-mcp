"""Page types (round-1 design §8.1–§8.3, pagetypes.py): the key against every sample of the 10-25 test, the import of
its results rule by rule (each condition of §8.2, the https retest, the review table, login only a note, the click
scan's kinds noted and nothing else read from it, the scripted redirect noted and never a seed, several old types on
one key taking the strictest), the import's numbers against design §8.2, the signs measure() reads, and what the crawl
does with them: only a type the import or the crawl gave its verdict is sent back to todo, jscheck's and a person's
stay and are reported. Offline: the 10-25 files are read when research/ is on this machine (it is not in git); every
rule also has a small made-up case with plain addresses and no names."""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest

from nknu.archive import links, pagetypes
from nknu.archive.frontier import Frontier
from nknu.archive.pagetypes import Measure, measure, observe, type_key

REAL = pagetypes.JSTEST_DIR
_n = itertools.count()


def _need_real():
    if not all((REAL / name).exists() for name in (pagetypes.RESULTS, pagetypes.RETEST, pagetypes.TYPES,
                                                    pagetypes.REVIEW, pagetypes.CLICK_SCAN)):
        pytest.skip("research/ is not in git: the 10-25 test results are only on the maintainer's machine")


@pytest.fixture
def fr(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    yield f
    f.close()


# ---------------------------------------------------------------- §8.1 the key

@pytest.mark.parametrize(("url", "key"), [
    ("https://c.nknu.edu.tw/aca/NewsDetail.aspx?nid=12&gi=3",
     "c.nknu.edu.tw/aca|c.nknu.edu.tw|aca/newsdetail.aspx|('gi', 'nid')"),
    ("https://gender.nknu.edu.tw/2014GEEA10th/menu01news.htm",  # every run of digits in a page file's path
     "gender.nknu.edu.tw|gender.nknu.edu.tw|{n}geea{n}th/menu{n}news.htm|()"),
    ("https://lis.nknu.edu.tw/news_display/81-news1/5-5?lang=zh-TW",  # a route: its first segment, then *
     "lis.nknu.edu.tw|lis.nknu.edu.tw|news_display/*/*|('lang',)"),
    ("https://lis.nknu.edu.tw/2", "lis.nknu.edu.tw|lis.nknu.edu.tw|2|()"),  # a route's first segment stays as it is
    ("https://id.nknu.edu.tw/uploads/a-021.pn", "id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()"),  # an upload's file
    ("https://clcts.nknu.edu.tw/uploads/VoiceBook-4/index.html",
     "clcts.nknu.edu.tw|clcts.nknu.edu.tw|uploads/voicebook-{n}/index.html|()"),
    ("https://ee.nknu.edu.tw/", "ee.nknu.edu.tw|ee.nknu.edu.tw||()"),
    ("https://EE.nknu.edu.tw/Course.html?getId=3&page=2",
     "ee.nknu.edu.tw|ee.nknu.edu.tw|course.html|('getid', 'page')"),
    # parameters: the names that have a value, lower-cased, each once, sorted
    ("https://ee.nknu.edu.tw/course.html?B=1&b=2&a=", "ee.nknu.edu.tw|ee.nknu.edu.tw|course.html|('b',)"),
    # SSO's WebIdx: home, list and item are three types by their parameter names (no selpage value in the key)
    ("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=7",
     "sso.nknu.edu.tw/web/7|sso.nknu.edu.tw|services/web/default.aspx|('webidx',)"),
    ("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=7&selPage=3",
     "sso.nknu.edu.tw/web/7|sso.nknu.edu.tw|services/web/default.aspx|('selpage', 'webidx')"),
    ("https://sso.nknu.edu.tw/Services/Web/Default.aspx?WebIdx=7&selPage=3&NewsIdx=55",
     "sso.nknu.edu.tw/web/7|sso.nknu.edu.tw|services/web/default.aspx|('newsidx', 'selpage', 'webidx')"),
    ("https://sites.google.com/mail.nknu.edu.tw/aiml/home",
     "sites.google.com/mail.nknu.edu.tw/aiml|sites.google.com|mail.nknu.edu.tw/*/*|()"),
])
def test_type_key(url, key):
    assert type_key(url) == key
    assert pagetypes.parse_key(key).key == key


def test_type_key_takes_the_row_s_site_when_given():
    assert type_key("https://ee.nknu.edu.tw/a.html", site="x") == "x|ee.nknu.edu.tw|a.html|()"


@pytest.mark.parametrize("bad", ["no bars", "a|b|c|not a tuple", "a|b|c|(1, 2)"])
def test_parse_key_refuses_what_is_not_a_key(bad):
    with pytest.raises(ValueError):
        pagetypes.parse_key(bad)


def test_type_key_gives_every_sample_of_the_10_25_test_its_own_key_back():
    _need_real()
    types = json.loads((REAL / pagetypes.TYPES).read_text(encoding="utf-8"))
    samples = [(key, url) for key, urls in types.items() for url in urls]
    assert len(types) == 1093 and len(samples) == 1906
    assert [(key, url) for key, url in samples if type_key(url) != key] == []


# ---------------------------------------------------------------- §8.2 the import, rule by rule (made-up results)

def rec(url: str, **kw) -> dict:
    out = {"type": type_key(url), "url": url, "server": "ip:192.0.2.1", "final_url": url, "seconds": 1.0,
           "direct_status": 200, "direct_chars": 500, "rendered_chars": 500, "frame_chars": 0, "new_chars": 0,
           "requests": 1, "data_requests": [], "sample_new": [], "login": False, "js": False, "iframe": False,
           "method": "direct"}
    out.update(kw)
    return out


def write_src(d: Path, results, retest=(), review=(), scan=(), types=None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / pagetypes.RESULTS).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                                       encoding="utf-8")
    (d / pagetypes.RETEST).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in retest),
                                      encoding="utf-8")
    if types is None:
        types = {r["type"]: [r["url"]] for r in results}
    (d / pagetypes.TYPES).write_text(json.dumps(types), encoding="utf-8")
    (d / pagetypes.REVIEW).write_text(json.dumps({"kinds": list(review)}), encoding="utf-8")
    (d / pagetypes.CLICK_SCAN).write_text(json.dumps({"kinds": list(scan)}), encoding="utf-8")
    return d


def table(fr: Frontier) -> dict[str, dict]:
    return {r["key"]: dict(r) for r in fr.q("SELECT * FROM page_type")}


def U(i: int | str) -> str:
    """A made-up page of its own type: no digits in the name (a page file's digits are {n} in its key)."""
    name = "".join(chr(ord("a") + int(c)) for c in str(i)) if isinstance(i, int) else i
    return f"https://dept.nknu.edu.tw/p{name}.php"


XHR_NEW = {"url": "https://dept.nknu.edu.tw/_get.php", "type": "xhr", "method": "POST", "status": 200,
           "has_new_text": True}


def test_the_rules_in_order(fr, tmp_path):
    cases = {  # url: (record fields, verdict, why)
        U(1): ({"direct_status": 404}, "todo", "inconclusive:status_404"),
        U(2): ({"direct_status": None}, "todo", "inconclusive:status_none"),
        U(3): ({"direct_status": 403, "iframe": True}, "todo", "inconclusive:status_403"),  # status comes first
        U(4): ({"iframe": True, "frame_chars": 250, "rendered_chars": 0}, "frame", None),  # frame before rendered0
        U(5): ({"rendered_chars": 0}, "todo", "inconclusive:rendered0"),
        U(6): ({"direct_chars": 0}, "todo", "inconclusive:direct0"),
        U(7): ({"slow": "TimeoutError"}, "todo", "inconclusive:slow"),
        U(8): ({"not_idle": True}, "todo", "inconclusive:slow"),
        U(9): ({"js": True, "method": "browser"}, "todo", "inconclusive:not_reviewed"),  # never the method column
        U(10): ({"data_requests": [XHR_NEW]}, "todo", "data_text"),  # new text in a data request: at least todo
        U(11): ({"data_requests": [{**XHR_NEW, "type": "fetch", "method": "GET"}]}, "todo", "data_text"),
        # a document is no data request (where a redirect lands, a frame): no data_text
        U(12): ({"data_requests": [{**XHR_NEW, "type": "document", "method": "GET"}]}, "ok", None),
        U(13): ({}, "ok", None),
        U(14): ({"data_requests": [{**XHR_NEW, "has_new_text": False}]}, "ok", None),
    }
    src = write_src(tmp_path / "src", [rec(u, **f) for u, (f, _, _) in cases.items()])
    report = pagetypes.import_jstest(fr, src)
    got = table(fr)
    for u, (_, verdict, why) in cases.items():
        row = got[type_key(u)]
        assert (row["verdict"], row["why"], row["source"]) == (verdict, why, "import"), u
        assert row["n_pages"] == 0 and row["sample_id"] is None and row["sample_url"] == u
        assert row["tested_at"] == pagetypes.TESTED_AT
    assert report.by_old == Counter(v for _, v, _ in cases.values()) == report.by_key
    assert sorted(k for k, v in report.data_text) == sorted([type_key(U(10)), type_key(U(11))])
    # the data requests are kept as evidence, each once
    assert json.loads(got[type_key(U(10))]["data_urls"]) == {
        "requests": [{"url": XHR_NEW["url"], "method": "POST", "new_text": True}]}
    assert got[type_key(U(13))]["data_urls"] is None
    assert fr.q("SELECT count(*) FROM url")[0][0] == 0  # the test's addresses are no seeds


def test_a_data_text_type_a_person_checked_is_ok_with_a_note(fr, tmp_path):
    url = "https://english.nknu.edu.tw/en/index.html"
    assert type_key(url) in pagetypes.DATA_TEXT_CHECKED
    report = pagetypes.import_jstest(fr, write_src(tmp_path / "s", [rec(url, data_requests=[XHR_NEW])]))
    row = table(fr)[type_key(url)]
    assert (row["verdict"], row["note"]) == ("ok", "data_text_checked")
    assert report.data_text and report.data_text[0][0] == type_key(url)


def test_the_https_retest_replaces_the_first_run(fr, tmp_path):
    src = write_src(tmp_path / "s", [rec(U(1), direct_status=404), rec(U(2))],
                    retest=[rec(U(1), direct_status=200)])
    report = pagetypes.import_jstest(fr, src)
    assert table(fr)[type_key(U(1))]["verdict"] == "ok"
    assert report.retested == [type_key(U(1))]
    assert sum(report.by_old.values()) == 2


def test_the_reviewed_types_take_the_review_not_the_method(fr, tmp_path):
    a, b, c = U("a"), U("b"), "https://clcts.nknu.edu.tw/uploads/VoiceBook-4/index.html"
    d = "https://dept.nknu.edu.tw/uploads/x.pn"
    results = [rec(a, js=True, method="data_url", direct_chars=0, rendered_chars=900),
               rec(b, js=True, method="data_url", data_requests=[{**XHR_NEW, "method": "GET"}]),
               rec(c, js=True, method="browser", direct_chars=0),
               rec(d, direct_status=None)]
    review = [{"type": type_key(a), "verdict": "ok", "note": "js_redirect", "why": "直接抓 0 字"},
              {"type": type_key(b), "verdict": "js", "method": "data_url", "why": "正文来自资料请求"},
              {"type": type_key(c), "verdict": "js", "method": "browser", "why": "翻页电子书"},
              {"type": type_key(d), "verdict": "todo", "why": "inconclusive:broken_sample：样本网址被截断"}]
    report = pagetypes.import_jstest(fr, write_src(tmp_path / "s", results, review=review))
    got = table(fr)
    assert (got[type_key(a)]["verdict"], got[type_key(a)]["why"], got[type_key(a)]["note"]) == ("ok", None,
                                                                                               "js_redirect")
    assert got[type_key(b)]["verdict"] == "js" and json.loads(got[type_key(b)]["data_urls"])["requests"][0]["new_text"]
    assert (got[type_key(c)]["verdict"], got[type_key(c)]["note"]) == ("js", "flip_pdf")  # design §8.2's table
    assert (got[type_key(d)]["verdict"], got[type_key(d)]["why"]) == ("todo", "inconclusive:broken_sample")
    assert sorted(report.reviewed) == sorted(type_key(u) for u in (a, b, c, d))
    assert report.data_text == []  # a reviewed type is the review's: no rule runs on it


def test_a_review_verdict_this_version_does_not_know_is_an_error(fr, tmp_path):
    src = write_src(tmp_path / "s", [rec(U(1))], review=[{"type": type_key(U(1)), "verdict": "maybe"}])
    with pytest.raises(ValueError):
        pagetypes.import_jstest(fr, src)
    assert fr.q("SELECT count(*) FROM page_type")[0][0] == 0


def test_a_password_box_is_a_note_never_a_verdict(fr, tmp_path):
    pagetypes.import_jstest(fr, write_src(tmp_path / "s", [rec(U(1), login=True),
                                                           rec(U(2), login=True, rendered_chars=0)]))
    got = table(fr)
    assert (got[type_key(U(1))]["verdict"], got[type_key(U(1))]["note"]) == ("ok", "login_field")
    assert (got[type_key(U(2))]["verdict"], got[type_key(U(2))]["note"]) == ("todo", "login_field")


def test_the_click_scan_only_notes_ok_types_and_nothing_else_is_read_from_it(fr, tmp_path):
    ok, js, none = U("tabs"), U("tabs-js"), U("plain")
    results = [rec(ok), rec(js, js=True, method="data_url"), rec(none)]
    review = [{"type": type_key(js), "verdict": "js"}]
    # the scan masks digits and names: its tab numbers and addresses are never taken (design §8.2)
    scan = [{"type": type_key(ok), "hidden_pages": 1, "network_targets": ["load_content.php?sub_id=98765"],
             "hit_urls": ["https://dept.nknu.edu.tw/p98765.php"], "bindings": [{"args": ["98765"]}]},
            {"type": type_key(js), "hidden_pages": 2, "network_targets": ["load_content.php?sub_id=98765"]},
            {"type": type_key(none), "hidden_pages": 0}]
    report = pagetypes.import_jstest(fr, write_src(tmp_path / "s", results, review=review, scan=scan))
    got = table(fr)
    assert (got[type_key(ok)]["verdict"], got[type_key(ok)]["note"]) == ("ok", "click_profiled")
    assert (got[type_key(js)]["verdict"], got[type_key(js)]["note"]) == ("js", None)
    assert got[type_key(none)]["note"] is None
    assert sorted(report.click_hidden) == sorted([(type_key(ok), "ok"), (type_key(js), "js")])
    for row in got.values():
        assert "98765" not in json.dumps(row, ensure_ascii=False)
    assert fr.q("SELECT count(*) FROM url")[0][0] == 0


def test_a_scripted_redirect_is_a_note_and_where_the_browser_stopped_is_never_a_seed(fr, tmp_path):
    moved = rec(U("notes"), final_url=U("notes") + "?getId=4",
                data_requests=[{"url": U("notes") + "?getId=4", "type": "document", "method": "GET", "status": 200,
                                "has_new_text": True}])
    by_3xx = rec(U("old"), final_url=U("new"),
                 data_requests=[{"url": U("old"), "type": "document", "status": 301, "has_new_text": False}])
    hash_only = rec(U("book"), final_url=U("book") + "#p=1")
    broken = rec(U("gone"), final_url="chrome-error://chromewebdata/")
    report = pagetypes.import_jstest(fr, write_src(tmp_path / "s", [moved, by_3xx, hash_only, broken]))
    got = table(fr)
    assert (got[type_key(U("notes"))]["verdict"], got[type_key(U("notes"))]["note"]) == ("ok", "js_redirect")
    assert got[type_key(U("old"))]["note"] is None and got[type_key(U("book"))]["note"] is None
    assert got[type_key(U("gone"))]["note"] is None
    assert report.js_redirect == [(type_key(U("notes")), U("notes") + "?getId=4")]
    assert fr.q("SELECT count(*) FROM url")[0][0] == 0


def test_old_types_on_one_new_key_take_the_strictest_and_the_report_says_so(fr, tmp_path):
    a, b, c = (f"https://dept.nknu.edu.tw/p{i}.php" for i in (1, 2, 3))
    new = type_key(a)  # p1.php and p2.php are both p{n}.php now
    assert type_key(b) == new
    results = [{**rec(a), "type": "dept.nknu.edu.tw|dept.nknu.edu.tw|p1.php|()"},
               {**rec(b, rendered_chars=0, login=True), "type": "dept.nknu.edu.tw|dept.nknu.edu.tw|p2.php|()"},
               {**rec(c, iframe=True, frame_chars=300), "type": "dept.nknu.edu.tw|dept.nknu.edu.tw|p3.php|()"}]
    report = pagetypes.import_jstest(fr, write_src(tmp_path / "s", results))
    got = table(fr)
    assert list(got) == [new]
    assert (got[new]["verdict"], got[new]["why"], got[new]["note"]) == ("todo", "inconclusive:rendered0",
                                                                         "login_field")
    assert report.by_old == Counter({"ok": 1, "todo": 1, "frame": 1}) and report.by_key == Counter({"todo": 1})
    assert len(report.key_changed) == 3
    assert report.merged == [(new, ("dept.nknu.edu.tw|dept.nknu.edu.tw|p2.php|()",
                                    "dept.nknu.edu.tw|dept.nknu.edu.tw|p3.php|()",
                                    "dept.nknu.edu.tw|dept.nknu.edu.tw|p1.php|()"), "todo")]
    assert any("合并" in line for line in report.lines())


def test_a_type_no_result_covers_is_todo(fr, tmp_path):
    src = write_src(tmp_path / "s", [rec(U(1))], types={type_key(U(1)): [U(1)], type_key(U("x")): [U("x")]})
    report = pagetypes.import_jstest(fr, src)
    row = table(fr)[type_key(U("x"))]
    assert (row["verdict"], row["why"], row["sample_url"]) == ("todo", "inconclusive:untested", U("x"))
    assert report.untested == [type_key(U("x"))]


def test_a_missing_file_is_an_error(fr, tmp_path):
    src = write_src(tmp_path / "s", [rec(U(1))])
    (src / pagetypes.CLICK_SCAN).unlink()
    with pytest.raises(FileNotFoundError):
        pagetypes.import_jstest(fr, src)


def test_the_import_is_once_before_the_round_records_a_page(fr, tmp_path):
    src = write_src(tmp_path / "s", [rec(U(1)), rec(U(2), rendered_chars=0)])
    pagetypes.import_jstest(fr, src)
    pagetypes.import_jstest(fr, write_src(tmp_path / "t", [rec(U(1))]))  # its own untouched rows: replaced
    assert {k: r["verdict"] for k, r in table(fr).items()} == {type_key(U(1)): "ok"}
    observe(fr, url_id=add_page(fr, U(1)), url=U(1), m=plain())
    with pytest.raises(pagetypes.ImportRefused):
        pagetypes.import_jstest(fr, src)
    assert table(fr)[type_key(U(1))]["n_pages"] == 1  # nothing undone


def test_the_import_refuses_a_table_holding_jscheck_s_or_a_person_s_verdict(fr, tmp_path):
    fr.x("INSERT INTO page_type(key, site, verdict, source) VALUES ('k', 's', 'ok', 'manual')")
    with pytest.raises(pagetypes.ImportRefused):
        pagetypes.import_jstest(fr, write_src(tmp_path / "s", [rec(U(1))]))
    assert [r["key"] for r in fr.q("SELECT key FROM page_type")] == ["k"]


# ---------------------------------------------------------------- §8.2 the import of the real 10-25 results

def test_the_10_25_results_come_in_as_design_8_2_counts_them(fr):
    _need_real()
    report = pagetypes.import_jstest(fr, REAL)
    expect = Counter({"ok": 992, "js": 4, "frame": 10, "todo": 87})
    assert report.by_old == expect and sum(report.by_old.values()) == 1093
    assert report.by_key == expect and report.merged == [] and report.key_changed == []  # no merge: same numbers
    got = table(fr)
    assert Counter(r["verdict"] for r in got.values()) == expect
    assert len(report.retested) == 13 and len(report.reviewed) == 10 and report.untested == []
    why = report.why
    assert sum(n for w, n in why.items() if w.startswith("inconclusive:status_")) == 29
    assert (why["inconclusive:status_404"], why["inconclusive:status_none"]) == (25, 2)
    assert (why["inconclusive:status_400"], why["inconclusive:status_403"]) == (1, 1)
    assert (why["inconclusive:rendered0"], why["inconclusive:direct0"], why["inconclusive:slow"]) == (42, 2, 13)
    assert why["inconclusive:broken_sample"] == 1 and "data_text" not in why
    # the scan's 14 kinds: the 12 ok ones noted click_profiled, the 2 js ones as they were
    noted = sorted(k for k, r in got.items() if "click_profiled" in (r["note"] or "").split(","))
    assert len(noted) == 12 and all(got[k]["verdict"] == "ok" for k in noted)
    assert Counter(v for _, v in report.click_hidden) == Counter({"ok": 12, "js": 2})
    assert report.notes["login_field"] == 71
    js = {k: r for k, r in got.items() if r["verdict"] == "js"}
    assert {parse(k).host for k in js} == {"lis.nknu.edu.tw", "oia.nknu.edu.tw", "clcts.nknu.edu.tw"}
    lis = json.loads(js["lis.nknu.edu.tw|lis.nknu.edu.tw||()"]["data_urls"])["requests"]
    assert len({r["url"] for r in lis if "/gviz/" in r["url"]}) == 3  # the three spreadsheets the home page reads
    notes = "ee.nknu.edu.tw|ee.nknu.edu.tw|notes.html|()"  # ok, noted; where the browser stopped is no seed
    assert (got[notes]["verdict"], got[notes]["note"]) == ("ok", "js_redirect")
    assert [k for k, _ in report.js_redirect] == [notes] and report.notes["js_redirect"] == 4
    english = "english.nknu.edu.tw|english.nknu.edu.tw|en/index.html|()"
    assert (got[english]["verdict"], got[english]["note"]) == ("ok", "data_text_checked")
    assert got["id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()"]["why"] == "inconclusive:broken_sample"
    assert fr.q("SELECT count(*) FROM url")[0][0] == 0


def parse(key: str) -> pagetypes.TypeKey:
    return pagetypes.parse_key(key)


# ---------------------------------------------------------------- §8.3 the signs a page reads

def page(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head><title>t</title>{head}</head><body>{body}</body></html>"


LONG = "<p>" + "本學期課程異動公告請同學留意辦理期限" * 20 + "</p>"  # 360 characters
# the shape of a 國際處 tab page while its tab loads (research/segment_demo_2026-10-24/raw/_probes, rewritten)
LOADER = ('<div class="loaderdiv" id="loaderdiv" style="display:block;">\n\t載入中....\n\t<div class="loader">'
          '\n\t\t&nbsp;</div>\n</div>\n<div class="row" id="content"></div>')


@pytest.mark.parametrize(("html", "signal"), [
    (page(LOADER + LONG), "loading"),
    (page(LONG, "<script>box.innerHTML = '讀取中';</script>"), "loading"),  # a script's string counts too
    (page(LONG + "<p>Loading...</p>"), "loading"),
    (page('<div id="app"></div>' + LONG), "spa"),
    (page('<div id="root" class="x">  </div>' + LONG), "spa"),
    (page("<div>{{ item.title }}</div>" + LONG), "spa"),
    (page('<div v-cloak id="list"></div>' + LONG), "spa"),
])
def test_strong_signs(html, signal):
    m = measure(html)
    assert m.strong and signal in m.signals


def test_no_sign_on_a_plain_page():
    m = measure(page(LONG, "<script src='a.js'></script><script src='b.js'></script><script>x()</script>"))
    assert not m.strong and not m.weak and m.signals == ()
    assert m.scripts == 3 and m.text_chars == 360


@pytest.mark.parametrize(("inside", "empty"), [
    ("  <script>render()</script> <style>.a{}</style> <!-- --> ", True),
    ("<div class='wrap'><span>  </span></div>", True),
    ("<a href='list.php'><i class='icon'></i></a>", True),  # a link to a page with no words is no content
    ("<p>公告</p>", False),
    ("<img src='/upload/a.jpg'>", False),
    ("<a href='/upload/rule.pdf'><i></i></a>", False),
    ("<a href='/webservices/ajaxdownloadfile.aspx?x=1'></a>", False),
    ("<a href='https://drive.google.com/file/d/AbC/view'></a>", False),
    ("<div style='background:url(/upload/a.jpg)'></div>", False),
    ("<iframe src='/frame.html'></iframe>", False),
    ("<div data-code='&lt;p&gt;x&lt;/p&gt;'></div>", False),  # a Google Sites embed-code block holds HTML
])
def test_an_empty_content_area_is_a_strong_sign(inside, empty):
    html = page(f"<header>網站選單 首頁 最新消息</header><div id='content'>{inside}</div><footer>地址 電話</footer>"
                + LONG)
    m = measure(html, content_css="#content")
    assert m.content_found and m.content_empty is empty and m.strong is empty


def test_no_content_area_sign_without_a_profile_or_without_the_area():
    html = page("<div id='content'></div>" + LONG)
    assert not measure(html).content_found and not measure(html).strong
    m = measure(html, content_css="#main")
    assert not m.content_found and not m.content_empty


@pytest.mark.parametrize(("html", "weak"), [
    (page("<p>短</p>" + "<script>a()</script>" * 3), True),  # under 150 characters, 3 scripts
    (page("<p>短</p>" + "<script>a()</script>" * 2), False),
    (page("<p>" + "字" * 300 + "</p><script>$.ajax({url: 'x.php'})</script>"), True),  # ajax, under 400
    (page("<p>" + "字" * 450 + "</p><script>$.ajax({url: 'x.php'})</script>"), False),
    (page("<p>" + "字" * 300 + "</p><script>fetch('/load_content.php?sub_id=1')</script>"), True),
    (page("<p>" + "字" * 300 + "</p><script>fetch(url)</script>"), False),  # fetch with no address written
])
def test_weak_signs(html, weak):
    m = measure(html)
    assert m.weak is weak and not m.strong


def test_text_chars_is_the_same_count_as_links_extract():
    html = page("<nav>選單 首頁</nav><main><h1>標題</h1>" + LONG + "</main><aside>側欄</aside>",
                "<script>var a = '不算';</script>")
    assert measure(html).text_chars == links.extract(html, "https://dept.nknu.edu.tw/a.php").text_chars
    assert measure(html, text_chars=7).text_chars == 7  # extract()'s count with embed-code blocks, when given


# ---------------------------------------------------------------- §8.3 what the crawl does with a page

def add_page(fr: Frontier, url: str, **cols) -> int:
    row = {"url": url, "path": url.split("?", 1)[0], "host": url.split("/")[2], "site": url.split("/")[2],
           "owner_site": url.split("/")[2], "tier": 0, "kind": "page", "kind0": "page", "depth": 0,
           "found_via": "seed", "state": "active", "status": 200, "sniff": "html"}
    row.update(cols)
    with fr.lock:
        return fr.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                               tuple(row.values())).lastrowid


def finish(fr: Frontier, uid: int, seen: pagetypes.Observed) -> None:
    """What the crawler does after observe(): the row is done, with the flags observe() gives."""
    fr.finish(uid, state="done", flags=",".join(seen.flags) or None)


def plain() -> Measure:
    return Measure(text_chars=500, scripts=1, loading=False, spa=False, ajax=False)


def strong() -> Measure:
    return Measure(text_chars=500, scripts=1, loading=True, spa=False, ajax=False)


def weak() -> Measure:
    return Measure(text_chars=40, scripts=4, loading=False, spa=False, ajax=False)


def typed(fr: Frontier, key: str, verdict: str, source: str, why: str | None = None) -> None:
    fr.x("INSERT INTO page_type(key, site, verdict, why, source) VALUES (?,?,?,?,?)",
         (key, key.split("|")[0], verdict, why, source))


def ptype(fr: Frontier, key: str) -> dict:
    return dict(fr.q("SELECT * FROM page_type WHERE key=?", (key,))[0])


def P(i: int | str) -> str:
    return f"https://dept.nknu.edu.tw/news.php?id={i}"


KEY = type_key(P(1))


def result(fr: Frontier, uid: int) -> str:
    return fr.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0]


def test_a_new_type_waits_for_jscheck_whatever_it_looks_like(fr):
    uid = add_page(fr, P(1))
    seen = observe(fr, url_id=uid, url=P(1), m=plain())
    assert (seen.key, seen.new, seen.flags, seen.retyped, seen.kept) == (KEY, True, (), None, ())
    row = ptype(fr, KEY)
    assert (row["verdict"], row["why"], row["source"], row["sample_id"], row["sample_url"]) == (
        "todo", "new", "crawl", uid, P(1))
    assert (row["site"], row["host"], row["path_tpl"], json.loads(row["params"])) == (
        "dept.nknu.edu.tw", "dept.nknu.edu.tw", "news.php", ["id"])
    assert (row["n_pages"], row["n_strong"], row["n_weak"]) == (1, 0, 0)
    assert fr.q("SELECT page_type FROM url WHERE id=?", (uid,))[0][0] == KEY
    finish(fr, uid, seen)
    assert result(fr, uid) == "ok"  # no browser check this round: got unless the page shows JavaScript (the owner 11-16)


def test_a_page_seen_again_is_counted_once(fr):
    a, b = add_page(fr, P(1)), add_page(fr, P(2))
    finish(fr, a, observe(fr, url_id=a, url=P(1), m=strong()))
    finish(fr, a, observe(fr, url_id=a, url=P(1), m=strong()))
    assert (ptype(fr, KEY)["n_pages"], ptype(fr, KEY)["n_strong"]) == (1, 1)
    finish(fr, a, observe(fr, url_id=a, url=P(1), m=plain()))  # its sign is gone on a fetch again
    assert (ptype(fr, KEY)["n_pages"], ptype(fr, KEY)["n_strong"]) == (1, 0)
    observe(fr, url_id=b, url=P(2), m=weak())
    row = ptype(fr, KEY)
    assert (row["n_pages"], row["n_strong"], row["n_weak"], row["sample_id"]) == (2, 0, 1, a)


def test_a_page_counted_under_a_key_the_rules_no_longer_give_moves(fr):
    uid = add_page(fr, P(1), page_type="old|key|x|()")
    typed(fr, "old|key|x|()", "ok", "crawl")
    fr.x("UPDATE page_type SET n_pages=1 WHERE key='old|key|x|()'")
    observe(fr, url_id=uid, url=P(1), m=plain())
    assert ptype(fr, "old|key|x|()")["n_pages"] == 0 and ptype(fr, KEY)["n_pages"] == 1


@pytest.mark.parametrize("source", pagetypes.CHANGEABLE_SOURCES)
@pytest.mark.parametrize("verdict", ["ok", "frame"])
def test_a_strong_sign_sends_an_imported_or_crawled_ok_or_frame_type_back(fr, source, verdict):
    typed(fr, KEY, verdict, source)
    plain_page, hinted = add_page(fr, P(1)), add_page(fr, P(2))
    finish(fr, plain_page, observe(fr, url_id=plain_page, url=P(1), m=plain()))
    assert result(fr, plain_page) == "ok"
    seen = observe(fr, url_id=hinted, url=P(2), m=strong())
    finish(fr, hinted, seen)
    assert (seen.flags, seen.retyped, seen.kept) == (("js_hint",), "hint", ())
    row = ptype(fr, KEY)
    assert (row["verdict"], row["why"], row["source"]) == ("todo", "hint", source)
    # the type waits for a browser check, which does not run this round (the owner 11-16, export batch T5): the page
    # with the sign is round two's (unchecked), the plain one got
    assert (result(fr, plain_page), result(fr, hinted)) == ("ok", "unchecked")


@pytest.mark.parametrize("source", pagetypes.JUDGED_SOURCES)
def test_a_type_jscheck_or_a_person_judged_keeps_its_verdict_and_the_sign_is_reported(fr, source):
    typed(fr, KEY, "ok", source)
    plain_page, hinted = add_page(fr, P(1)), add_page(fr, P(2))
    finish(fr, plain_page, observe(fr, url_id=plain_page, url=P(1), m=plain()))
    seen = observe(fr, url_id=hinted, url=P(2), m=strong())
    finish(fr, hinted, seen)
    assert (seen.flags, seen.retyped, seen.kept) == (("js_hint",), None, ("hint",))
    assert (ptype(fr, KEY)["verdict"], ptype(fr, KEY)["why"]) == ("ok", None)
    assert result(fr, plain_page) == "ok"
    assert result(fr, hinted) == "unchecked"  # the page itself waits until jscheck opens it (§8.3, §10.2)
    report = pagetypes.judged_with_hints(fr)
    assert [(r["key"], r["source"], r["signs"]) for r in report] == [(KEY, source, ("hint",))]


@pytest.mark.parametrize(("verdict", "why"), [("todo", "inconclusive:slow"), ("todo", "new"), ("js", None),
                                              ("click", None), ("unresolved", "inconclusive:status_404")])
def test_a_sign_leaves_a_type_that_is_not_ok_as_it_is(fr, verdict, why):
    typed(fr, KEY, verdict, "import", why)
    uid = add_page(fr, P(1))
    seen = observe(fr, url_id=uid, url=P(1), m=strong(), click=True)
    assert (seen.retyped, seen.kept) == (None, ())
    assert (ptype(fr, KEY)["verdict"], ptype(fr, KEY)["why"]) == (verdict, why)


def test_weak_signs_need_5_pages_and_30_percent(fr):
    typed(fr, KEY, "ok", "import")
    ids = [add_page(fr, P(i)) for i in range(5)]
    for i, uid in enumerate(ids[:4]):  # 4 of 4 pages: too few pages
        assert observe(fr, url_id=uid, url=P(i), m=weak()).retyped is None
    assert ptype(fr, KEY)["verdict"] == "ok"
    seen = observe(fr, url_id=ids[4], url=P(4), m=plain())  # 4 of 5
    assert seen.retyped == "weak" and seen.flags == ()
    assert (ptype(fr, KEY)["verdict"], ptype(fr, KEY)["why"], ptype(fr, KEY)["n_weak"]) == ("todo", "weak", 4)


def test_weak_signs_under_30_percent_change_nothing(fr):
    typed(fr, KEY, "ok", "crawl")
    for i in range(10):
        uid = add_page(fr, P(i))
        seen = observe(fr, url_id=uid, url=P(i), m=weak() if i >= 8 else plain())
        assert seen.retyped is None
    assert ptype(fr, KEY)["verdict"] == "ok"  # 2 of 10
    uid = add_page(fr, P(10))
    assert observe(fr, url_id=uid, url=P(10), m=weak()).retyped is None  # 3 of 11: 27%
    uid = add_page(fr, P(11))
    assert observe(fr, url_id=uid, url=P(11), m=weak()).retyped == "weak"  # 4 of 12: 33%


def test_weak_signs_leave_a_frame_type(fr):
    typed(fr, KEY, "frame", "import")
    for i in range(6):
        observe(fr, url_id=add_page(fr, P(i)), url=P(i), m=weak(), click=True)
    assert ptype(fr, KEY)["verdict"] == "frame"


@pytest.mark.parametrize(("need", "why", "retyped"), [
    ("round2", "round2:click", "click"),  # a writing the rule does not fit
    ("no", "click:shown_on_load", "click"),  # no rule: only the tab shown on load is in the page
    ("must", None, None),  # pressed by its platform rule this round
    ("no", "click:plain_href", None),  # the tab has a plain link
    ("no", "click:never", None),
])
def test_a_click_this_round_cannot_press_sends_an_ok_type_back(fr, need, why, retyped):
    typed(fr, KEY, "ok", "import")
    uid = add_page(fr, P(1))
    fr.set_page_res(uid, [{"res_key": "click:loadTab:1", "how": "click", "role": "click", "need": need, "why": why}])
    seen = observe(fr, url_id=uid, url=P(1), m=plain())  # click=None: read from page_res
    assert seen.retyped == retyped
    assert ptype(fr, KEY)["verdict"] == ("todo" if retyped else "ok")
    assert ptype(fr, KEY)["why"] == retyped


def test_an_unhandled_writing_is_no_click_sign(fr):
    typed(fr, KEY, "ok", "import")
    uid = add_page(fr, P(1))
    fr.set_page_res(uid, [{"res_key": "js:show", "how": "unhandled", "role": "click", "need": "round2",
                           "why": "round2:unhandled"}])
    assert observe(fr, url_id=uid, url=P(1), m=plain()).retyped is None


def test_a_click_on_a_type_a_person_judged_is_reported(fr):
    typed(fr, KEY, "ok", "manual")
    uid = add_page(fr, P(1))
    fr.set_page_res(uid, [{"res_key": "click:t:2", "how": "click", "role": "click", "need": "round2",
                           "why": "round2:click"}])
    seen = observe(fr, url_id=uid, url=P(1), m=plain())
    assert (seen.retyped, seen.kept) == (None, ("click",))
    assert ptype(fr, KEY)["verdict"] == "ok"
    assert [r["signs"] for r in pagetypes.judged_with_hints(fr)] == [("click",)]


def test_the_strongest_sign_names_the_reason(fr):
    typed(fr, KEY, "ok", "import")
    uid = add_page(fr, P(1))
    assert observe(fr, url_id=uid, url=P(1), m=strong(), click=True).retyped == "hint"


def test_the_imported_10_25_type_is_counted_and_sampled_by_the_crawl(fr, tmp_path):
    pagetypes.import_jstest(fr, write_src(tmp_path / "s", [rec(P(1))]))
    uid = add_page(fr, P(7))
    seen = observe(fr, url_id=uid, url=P(7), m=plain())
    row = ptype(fr, KEY)
    assert not seen.new and (row["verdict"], row["source"], row["n_pages"]) == ("ok", "import", 1)
    assert (row["sample_id"], row["sample_url"]) == (uid, P(1))  # the 10-25 sample stays, for reference only
    finish(fr, uid, seen)
    assert result(fr, uid) == "ok"


def test_an_unknown_row_changes_nothing(fr):
    with pytest.raises(KeyError):
        observe(fr, url_id=999, url=P(1), m=plain())
    assert fr.q("SELECT count(*) FROM page_type")[0][0] == 0


def test_judged_with_hints_lists_only_judged_types_with_signs(fr):
    typed(fr, "a|h|x|()", "ok", "import")
    typed(fr, "b|h|x|()", "ok", "jscheck")
    typed(fr, "c|h|x|()", "frame", "manual")
    typed(fr, "d|h|x|()", "js", "jscheck")
    fr.x("UPDATE page_type SET n_strong=1, n_pages=1")
    fr.x("UPDATE page_type SET n_strong=0, n_pages=6, n_weak=2 WHERE key='b|h|x|()'")
    assert [(r["key"], r["signs"]) for r in pagetypes.judged_with_hints(fr)] == [
        ("b|h|x|()", ("weak",)), ("c|h|x|()", ("hint",))]


def test_the_broken_sample_type_is_withdrawn_without_a_page_this_round(fr):
    typed(fr, "id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()", "todo", "import", "inconclusive:broken_sample")
    typed(fr, "other|h|x|()", "todo", "import", "inconclusive:broken_sample")
    add_page(fr, "https://other.nknu.edu.tw/x", page_type="other|h|x|()", state="done")
    assert pagetypes.withdraw_files_only(fr) == ["id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()"]
    assert ptype(fr, "id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()")["note"] == "withdrawn:files_only"
    assert ptype(fr, "other|h|x|()")["note"] is None
    pagetypes.withdraw_files_only(fr)  # once only
    assert ptype(fr, "id.nknu.edu.tw|id.nknu.edu.tw|uploads/*|()")["note"] == "withdrawn:files_only"


def test_add_note():
    assert pagetypes.add_note(None, "a", "b", "a") == "a,b"
    assert pagetypes.add_note("a", "b") == "a,b"
    assert pagetypes.add_note(None) is None
