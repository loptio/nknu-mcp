"""Page types (round-1 design §8.1, §8.3, pagetypes.py): the key against every sample of the 10-25 test, the signs
measure() reads, and what the crawl does with them: a type the crawl meets is new (todo, why new) unless its row is
there already; only a type the import or the crawl gave its verdict is sent back to todo, jscheck's and a person's
stay. (The import of the 10-25 results, design §8.2, went with the judgment-layer cut of 11-19: a test that needs a
type from it writes the page_type row itself.) Offline: the 10-25 samples are read when research/ is on this machine
(it is not in git); every rule also has a small made-up case with plain addresses and no names."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from nknu.archive import links, pagetypes
from nknu.archive.frontier import Frontier
from nknu.archive.pagetypes import Measure, measure, observe, type_key

# the 10-25 browser test's types and their sample addresses (page_types.json): what type_key() is checked against
REAL_TYPES = (Path(__file__).resolve().parents[2] / "research" / "project_audit_2026-10-25" / "js_test_2026-10-25"
              / "page_types.json")
_n = itertools.count()


def _need_real():
    if not REAL_TYPES.exists():
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


def test_type_key_takes_the_row_s_site_when_given():
    assert type_key("https://ee.nknu.edu.tw/a.html", site="x") == "x|ee.nknu.edu.tw|a.html|()"


def test_type_key_gives_every_sample_of_the_10_25_test_its_own_key_back():
    _need_real()
    types = json.loads(REAL_TYPES.read_text(encoding="utf-8"))
    samples = [(key, url) for key, urls in types.items() for url in urls]
    assert len(types) == 1093 and len(samples) == 1906
    assert [(key, url) for key, url in samples if type_key(url) != key] == []


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


@pytest.mark.parametrize("source", ["jscheck", "manual"])
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


def test_the_strongest_sign_names_the_reason(fr):
    typed(fr, KEY, "ok", "import")
    uid = add_page(fr, P(1))
    assert observe(fr, url_id=uid, url=P(1), m=strong(), click=True).retyped == "hint"


def test_the_imported_10_25_type_is_counted_and_sampled_by_the_crawl(fr):
    # the row as the 10-25 import left it (no import any more): ok, its sample an address, no page counted yet
    fr.x("INSERT INTO page_type(key, site, verdict, sample_url, source, tested_at) VALUES (?,?,'ok',?,'import',?)",
         (KEY, KEY.split("|")[0], P(1), "2026-10-25"))
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
