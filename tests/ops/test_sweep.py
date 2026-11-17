"""網頁核查 without remembering anything (design docs/superpowers/specs/2026-11-16-page-check-design.md section 六): the
sweep of every stored page (nknu.ops.sweep), the entry with its sites, a site's page with its misses merged and its
pages, the box that finds by words, the checked page's line back to its site. A made-up ledger (the crawler's own
schema) and WARC hold two sites of c.nknu: one with pages that miss things in the content area and in the menus, one
quiet page, a deleted page and a failed one; a third site of the first round has no page at all. No real data."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from selectolax.lexbor import LexborHTMLParser
from starlette.testclient import TestClient

from nknu.archive import scope
from nknu.archive.frontier import Frontier
from nknu.ops import pagecheck, sweep, web
from nknu.ops.check_sites import SITES_CSS, school_section
from nknu.ops.doc_page import results_html
from tests.ops.library import models
from tests.ops.test_pagecheck import _records, _url
from tests.retrieval.test_search import TODAY

ACA, CH, BIO = "c.nknu.edu.tw/aca", "c.nknu.edu.tw/ch", "c.nknu.edu.tw/bio"
C = scope.canonical
HOME = C("https://c.nknu.edu.tw/aca/default.aspx")
NEWS1, NEWS2 = C("https://c.nknu.edu.tw/aca/News1.aspx"), C("https://c.nknu.edu.tw/aca/News2.aspx")
QUIET, THIN = C("https://c.nknu.edu.tw/aca/Quiet.aspx"), C("https://c.nknu.edu.tw/aca/Thin.aspx")
GONE, FAIL = C("https://c.nknu.edu.tw/aca/Gone.aspx"), C("https://c.nknu.edu.tw/aca/Fail.aspx")
CH_HOME = C("https://c.nknu.edu.tw/ch/default.aspx")
MISSING, LOST = C("https://c.nknu.edu.tw/aca/Missing1.aspx"), C("https://c.nknu.edu.tw/aca/Lost.aspx")
WARC = "warc/2026/11/06/sweep-1.warc.gz"
MENU = '<a href="Ok.aspx">選單抓到</a> <a href="Lost.aspx">選單沒抓到</a>'


def _page(menu: str, content: str, title: str = "頁") -> bytes:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>"
            f"<div id='menu'>{menu}</div><div id='MainContent'>{content}</div></body></html>").encode()


PAGES = {  # address: (stored HTML, <title> the ledger keeps)
    HOME: (_page(MENU, '<a href="News1.aspx">消息一</a> <a href="News2.aspx">第二則消息</a>'), "國立高雄師範大學 教務處｜首頁"),
    NEWS1: (_page(MENU, '<p>內文</p><a href="Missing1.aspx">漏了的辦法</a> <a href="Ok.aspx">抓到</a>'), ""),
    NEWS2: (_page(MENU, '<a href="Missing1.aspx">漏了的辦法</a> <a href="Thin.aspx">很薄的頁</a>'), ""),
    QUIET: (_page('<a href="Ok.aspx">選單抓到</a>', "<p>沒有連結</p>"), "國立高雄師範大學 教務處｜安靜的頁"),
    THIN: (_page(MENU, "<p>幾個字</p>"), "國立高雄師範大學 教務處｜首頁"),
    GONE: (_page(MENU, "<p>找不到</p>"), ""),
    CH_HOME: (_page('<a href="default.aspx">首頁</a>', "<p>國文學系</p>"), "國立高雄師範大學 國文學系"),
}


def build(root: Path) -> SimpleNamespace:
    archive = root / "archive"
    offsets = dict(zip(PAGES, _records(archive / WARC, [
        (url, "404" if url == GONE else "200", [("Content-Type", "text/html; charset=utf-8")], html)
        for url, (html, _t) in PAGES.items()]), strict=True))
    front = Frontier(archive / pagecheck.LEDGER)
    conn = front.conn
    ids = {}
    for url, (_html, title) in PAGES.items():
        ids[url] = _url(conn, url, warc=WARC, offset=offsets[url], status=404 if url == GONE else 200,
                        flags="thin" if url == THIN else None)
        if title:
            conn.execute("UPDATE url SET title = ? WHERE id = ?", (title, ids[url]))
    _url(conn, C("https://c.nknu.edu.tw/aca/Ok.aspx"))
    _url(conn, FAIL, state="failed", status=None, reason="server_down")
    for src, text in ((HOME, "第二則消息"), (NEWS1, "更多"), (QUIET, "更多"), (THIN, "[redirect]")):
        conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, ?, 'queued')", (ids[src], NEWS2, text))
    for key, name, home in ((ACA, "教务处", "https://c.nknu.edu.tw/aca/default.aspx"),
                            (CH, "国文学系", "https://c.nknu.edu.tw/ch"), (BIO, "生物科技系", "https://c.nknu.edu.tw/bio")):
        conn.execute("INSERT INTO site_plan(site, name, round, home) VALUES (?, ?, '1', ?)", (key, name, home))
    conn.execute("INSERT INTO site_plan(site, name, round, home) VALUES ('other.example', '第二輪的', '2', '')")
    conn.close()
    return SimpleNamespace(archive=archive, ids=ids)


@pytest.fixture
def made(tmp_path) -> SimpleNamespace:
    return build(tmp_path)


@pytest.fixture
def lib(db, tmp_path) -> Path:
    """The tests' made-up library with the names a page and a site take from it."""
    db.doc("dnews1", "第一則消息", "內文")
    db.conn.execute("UPDATE docs SET url = ? WHERE doc_id = 'dnews1'", (NEWS1,))
    db.conn.executemany("INSERT INTO sites(site, name, unit, docs) VALUES (?, ?, ?, 1)",
                        [(ACA, "教務處", "教務處"), (CH, "國文學系", "國文學系")])
    db.conn.execute("INSERT INTO unit_aliases(alias, unit_id) VALUES ('註冊組的家', '教務處')")
    db.conn.commit()
    return tmp_path / "t.db"


def swept(made, lib=None, **kw) -> sweep.Sweep:
    sw = sweep.Sweep(made.archive, lib, **kw)
    sw.ensure()
    assert sw.wait(30)
    return sw


# ---------------------------------------------------------------- what a sweep reads

def test_a_pages_name_is_the_librarys_title_else_its_links_words_else_its_title_else_its_address(made, lib):
    sw = swept(made, lib)
    names = sw.listing.names
    assert names[NEWS1] == "第一則消息"  # the library's title wins over the words linking it
    assert names[NEWS2] == "第二則消息"  # 「更多」 and a crawler's marker never name a page
    assert names[QUIET] == "安靜的頁"  # the <title> without the school's and the site's names
    assert names[HOME] == "首頁"  # a bare 首頁 only for the site's own home
    assert names[THIN] == "/aca/thin.aspx" and names[GONE] == "/aca/gone.aspx"
    sw.close()


def test_a_name_many_pages_of_a_site_share_gives_way_to_the_next_kind():
    shared = {"單一登入 網路資訊平台"}.__contains__
    url = "https://x.example/a.aspx?id=1"
    got = sweep.name_of(url, library={url: "單一登入 網路資訊平台"}, anchors={url: "防毒軟體"}, title="",
                        own_names=(), shared=shared)
    assert got == "防毒軟體"
    alone = sweep.name_of(url, library={url: "單一登入 網路資訊平台"}, anchors={}, title="", own_names=(), shared=shared)
    assert alone == "單一登入 網路資訊平台"  # nothing better: the shared name, its address under it tells it apart


def test_the_sites_are_the_first_rounds_and_those_with_pages_named_in_traditional(made, lib):
    sw = swept(made, lib)
    sites = sw.listing.sites
    assert set(sites) == {ACA, CH, BIO}  # the second round's site has no page and no document: not listed
    assert sites[ACA].name == "教務處" and sites[BIO].name == "生物科技系" and sites[BIO].round1
    assert sites[ACA].home == HOME and sites[CH].home == C("https://c.nknu.edu.tw/ch")
    listed = set(sw.listing.pages)
    assert listed == {HOME, NEWS1, NEWS2, QUIET, THIN, GONE, FAIL, CH_HOME}  # Ok.aspx: no copy, nothing to say
    sw.close()


def test_the_anchor_and_title_rules():
    assert sweep.anchor_words("[redirect]") == "" and sweep.anchor_words("https://x.example/a") == ""
    assert sweep.anchor_words(" 更多 ") == "" and sweep.anchor_words("»") == "" and sweep.anchor_words("12") == ""
    assert sweep.anchor_words("  招生  資訊 ") == "招生 資訊"
    assert sweep.anchor_words("上一頁") == "" and sweep.anchor_words("瀏覽人次：5467600") == ""
    assert sweep.title_words("教務處 策略聯盟 專題報導nknu", ["教務處"]) == "策略聯盟 專題報導"
    named = sweep.name_of("https://x.example/a/b.aspx", library={"https://x.example/a/b.aspx": "國立高雄師範大學 教務處"},
                          anchors={"https://x.example/a/b.aspx": "教務處"}, title="", own_names=["教務處"])
    assert named == "/a/b.aspx"  # only the school's and the site's names: no name
    assert sweep.title_words("國立高雄師範大學-成人教育研究所 ｜師資陣容 - 兼任教師", ["成人教育研究所"]) == "師資陣容 · 兼任教師"
    assert sweep.title_words("國立高雄師範大學 教務處｜首頁｜首頁", ["教務處"]) == "首頁"


# ---------------------------------------------------------------- one site's numbers and its merged misses

def test_a_site_merges_one_target_across_its_pages_the_content_area_first(made, lib):
    sw = swept(made, lib)
    view = sw.site_view(ACA)
    total = view["sum"]
    assert (total["pages"], total["gap"], total["menu_links"], total["check"], total["nofetch"]) == (7, 2, 1, 2, 1)
    fix = [(r["target"], r["zone"], r["pages"]) for r in view["fix"]]
    assert fix == [(MISSING, "in", 2), (LOST, "out", 4)]  # the deleted page's menu adds nothing
    first = view["fix"][0]
    assert first["page"] == NEWS1 and first["n"] > 0 and first["text"] == "漏了的辦法"
    assert [(r["target"], r["bucket"]) for r in view["look"]] == [(THIN, "look")]
    assert view["chips"] == {"gap": 2, "menu": 2, "check": 2, "nofetch": 1}  # the deleted page: listed, no chip
    sw.close()


def test_a_sites_pages_problems_first_and_each_marked(made, lib):
    sw = swept(made, lib)
    rows = sw.site_view(ACA)["rows"]
    states = {r["url"]: r["state"] for r in rows}
    assert [r["url"] for r in rows[:2]] == [NEWS1, NEWS2] and rows[-1]["url"] == QUIET
    assert states == {NEWS1: "gap", NEWS2: "gap", FAIL: "failed", GONE: "gone", THIN: "look", HOME: "menu",
                      QUIET: "ok"}
    assert "沒抓到：正文區 1 個 · 選單等 1 個" in rows[0]["tip"]
    only = sw.site_view(ACA, only="nofetch")["rows"]
    assert {r["url"] for r in only} == {FAIL}
    typed = sw.site_view(ACA, q="第二")
    assert [r["url"] for r in typed["rows"]] == [NEWS2] and typed["matched"] == 1
    assert [r["url"] for r in sw.site_view(ACA, q="quiet")["rows"]] == [QUIET]  # a piece of the address
    assert sw.site_view("nowhere.example") is None
    sw.close()


def test_the_overview_puts_the_content_areas_misses_first_and_a_site_without_pages_last(made, lib):
    sw = swept(made, lib)
    view = sw.overview()
    assert [s["key"] for s in view["sites"]] == [ACA, CH, BIO]
    assert view["totals"]["pages"] == 8 and view["totals"]["gap"] == 2 and view["totals"]["menu_links"] == 1
    assert view["progress"]["phase"] == "done" and view["progress"]["done"] == view["progress"]["total"] == 8
    sw.close()


def test_the_list_and_the_opened_page_never_disagree(made, lib):
    """workers=0 checks in a thread of this process, by the very function the page uses."""
    sw = swept(made, lib)
    for url, found in sw.found.items():
        summary = pagecheck.check(made.archive, url).summary
        assert found.state == summary["state"], url
        if summary["state"] == "ok":
            v = summary["verdict"]
            assert found.fix == (v["fix"]["in"], v["fix"]["files"], v["fix"]["out"]) and found.check == v["check"]
            assert [p.n for p in found.problems] == [p["n"] for p in summary["problems"]]
    sw.close()


def test_the_next_page_with_a_miss_in_the_content_area(made, lib):
    sw = swept(made, lib)
    assert sw.crumb(ACA, NEWS1)["next"] == NEWS2
    assert sw.crumb(ACA, NEWS2)["next"] == ""  # the last one: no link
    got = sw.crumb(ACA, HOME)
    assert got["next"] == NEWS1 and got["site_name"] == "教務處" and got["name"] == "首頁"
    assert sw.crumb(CH, CH_HOME)["next"] == ""
    sw.close()


# ---------------------------------------------------------------- finding by words

def test_words_find_sites_by_any_of_their_names_in_either_script_and_pages_by_name_or_address(made, lib):
    sw = swept(made, lib)
    assert [s["key"] for s in sw.find("教务")["sites"]] == [ACA]  # Simplified typed, Traditional name
    assert [s["key"] for s in sw.find("国文")["sites"]] == [CH]  # the crawler's own Simplified name
    assert [s["key"] for s in sw.find("註冊組的家")["sites"]] == [ACA]  # the unit's other name
    assert [s["key"] for s in sw.find("教處")["sites"]] == [ACA]  # a short name: its characters in order
    assert [s["key"] for s in sw.find("c.nknu.edu.tw/ch")["sites"]] == [CH]
    pages = sw.find("第二則")
    assert [p["url"] for p in pages["pages"]] == [NEWS2] and pages["pages"][0]["site_name"] == "教務處"
    assert {p["url"] for p in sw.find("news")["pages"]} == {NEWS1, NEWS2}
    assert sw.find("沒有這種字")["pages"] == [] and sw.find("")["sites"] == []
    sw.close()


# ---------------------------------------------------------------- running

def test_the_site_asked_for_is_checked_first(made, lib):
    seen: list[str] = []

    def checker(archive, url, **kw):
        seen.append(url)
        return pagecheck.check(archive, url, **kw)

    sw = sweep.Sweep(made.archive, lib, checker=checker)
    sw.prioritize(ACA)  # the larger site, which the sweep's own order puts after the smaller one
    sw.ensure()
    assert sw.wait(30)
    assert {u for u in seen[:7]} == set(sw.listing.site_pages[ACA]) and seen[-1] == CH_HOME
    sw.close()


def test_a_page_whose_check_fails_is_listed_under_check_with_the_error_floating(made, lib):
    def checker(archive, url, **kw):
        if url == NEWS1:
            raise ValueError("讀到一半壞了")
        return pagecheck.check(archive, url, **kw)

    sw = sweep.Sweep(made.archive, lib, checker=checker)
    sw.ensure()
    assert sw.wait(30)
    view = sw.site_view(ACA)
    row = next(r for r in view["rows"] if r["url"] == NEWS1)
    assert row["state"] == "error" and row["pill"] == "核查出錯" and "讀到一半壞了" in row["tip"]
    assert "check" in row["chips"] and view["errors"] == 1
    sw.close()


def test_a_changed_ledger_is_swept_again_and_the_older_results_show_until_replaced(made, lib):
    gate, entered = threading.Event(), threading.Event()

    def checker(archive, url, **kw):
        if sw.gen > 1:
            entered.set()
            assert gate.wait(30)
        return pagecheck.check(archive, url, **kw)

    sw = sweep.Sweep(made.archive, lib, checker=checker, resweep_seconds=0)
    sw.ensure()
    assert sw.wait(30)
    sw.ensure()  # nothing changed: no new sweep
    assert sw.gen == 1 and sw.phase == "done"
    new = C("https://c.nknu.edu.tw/ch/New.aspx")
    offset, = _records(made.archive / WARC, [(new, "200", [("Content-Type", "text/html")], _page("", "<p>新的</p>"))])
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, new, warc=WARC, offset=offset)
    conn.close()
    sw.ensure()
    assert entered.wait(30)
    progress = sw.progress()
    assert sw.gen == 2 and progress["running"] and progress["older"] == 8 and progress["total"] == 9
    view = sw.site_view(ACA)
    assert all(r["older"] for r in view["rows"]) and view["sum"]["gap"] == 2  # the old results, said to be older
    assert next(r for r in sw.site_view(CH)["rows"] if r["url"] == new)["state"] == "wait"
    gate.set()
    assert sw.wait(30)
    assert sw.progress()["older"] == 0 and not any(r["older"] for r in sw.site_view(ACA)["rows"])
    assert sw.found[new].state == "ok"
    sw.close()


def test_a_ledger_busy_for_a_moment_never_hides_an_older_real_result(made, lib):
    """The review of 11-16: a resweep runs while the crawler writes; a check answered 帳本正在寫入 or failing keeps the
    page's older result (still said to be older) instead of dropping its misses from the site's numbers."""
    def checker(archive, url, **kw):
        if sw.gen > 1 and url == NEWS1:
            raise sqlite3.OperationalError("database is locked")
        return pagecheck.check(archive, url, **kw)

    sw = sweep.Sweep(made.archive, lib, checker=checker, resweep_seconds=0)
    sw.ensure()
    assert sw.wait(30)
    new = C("https://c.nknu.edu.tw/ch/New.aspx")
    offset, = _records(made.archive / WARC, [(new, "200", [("Content-Type", "text/html")], _page("", "<p>新的</p>"))])
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, new, warc=WARC, offset=offset)
    conn.close()
    sw.ensure()
    assert sw.wait(30) and sw.gen == 2
    kept = sw.found[NEWS1]
    assert kept.state == "ok" and kept.fix[0] == 1 and kept.gen == 1
    assert sw.site_view(ACA)["sum"]["gap"] == 2
    sw.close()


def test_closing_stops_the_thread(made, lib):
    sw = swept(made, lib)
    sw.close()
    assert not sw.thread.is_alive() and sw.phase == "closed"


def test_without_a_ledger_the_sweep_says_it_could_not_read_it(tmp_path):
    sw = sweep.Sweep(tmp_path, None)
    sw.ensure()
    assert sw.wait(10) and sw.phase == "failed" and sw.failure
    sw.ensure()  # tried again only after a while
    assert sw.phase == "failed"
    sw.close()


def test_a_failed_sweep_never_asks_the_doc_page_to_look_again(tmp_path):
    sw = sweep.Sweep(tmp_path, None)
    sw.ensure()
    assert sw.wait(10) and sw.phase == "failed" and not sw.progress()["ready"]
    school = school_section(sw.overview())
    found = results_html("教務", [], sw.find("教務"))
    sw.close()
    assert "data-running" not in school and "讀不了帳本" in school
    assert "data-running" not in found and "讀不了帳本" in found and "還在讀" not in found
    reading = {"phase": "reading", "running": True, "ready": False, "done": 0, "total": 0, "older": 0, "failure": ""}
    assert "data-running" in school_section({"progress": reading, "totals": None, "top": None})
    assert "data-running" in results_html("教務", [], {"progress": reading, "sites": [], "pages": [], "pages_total": 0})


# ---------------------------------------------------------------- the pages

@pytest.fixture
def client(made, lib, tmp_path):
    app = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        c.get("/check")  # the first request starts the sweep
        assert app.state.sweep.wait(30)
        c.app_state = app.state
        yield c


def test_the_entry_is_the_doc_pages_school_numbers_and_its_sites_are_on_their_own_pages(client):
    # structure design 2026-11-17 section 二: 網頁核查's entry is 文件's (its four numbers and the school's lists); its
    # table of sites went to each site's page (/site), so one list of sites is left (網站)
    page = client.get("/check")
    assert page.status_code == 200 and str(page.url).endswith("/doc") and "aria-current='page'>文件</a>" in page.text
    tree = LexborHTMLParser(page.text)
    assert not tree.css("a.srow") and tree.css_first("h2.sec").text() == "全校網頁核查"
    assert "已核查 8 頁" in page.text and "正文區有漏" in page.text and "選單沒抓到" in page.text
    assert "帳本裡沒有這個網址" not in page.text and "<iframe" not in page.text
    assert " title=" not in page.text.split("<main>", 1)[1]
    # the old list's answer (its script's) still answers, its rows now opening a site's page
    rows = LexborHTMLParser(client.get("/check/list").json()["html"]).css("a.srow")
    assert [r.css_first("b").text() for r in rows] == ["教務處", "國文學系", "生物科技系"]
    assert [n.text() for n in rows[0].css(".num")] == ["7", "2", "1", "2", "1"]  # pages, then the four numbers
    assert rows[0].attributes["href"] == "/site?site=c.nknu.edu.tw%2Faca#check"
    assert "沒有抓到網頁" in rows[2].text() and "zero" in rows[2].attributes["class"]


def test_words_in_the_box_list_matches_never_a_missing_address(client):
    page = client.get("/check", params={"url": "第二則"})  # words: 文件 finds them
    assert page.status_code == 200 and "帳本裡沒有這個網址" not in page.text and str(page.url).endswith("/doc?q=%E7%AC%AC%E4%BA%8C%E5%89%87")
    assert "第二則消息" in page.text and "/check?url=" + "https%3A%2F%2Fc.nknu.edu.tw%2Faca%2Fnews2.aspx" in page.text
    got = client.get("/check/list", params={"q": "教务"}).json()
    assert "教務處" in got["html"] and "國文學系" not in got["html"] and got["running"] is False
    none = client.get("/check", params={"url": "沒有這種字"}).text
    assert "網頁名和網址裡都沒有「沒有這種字」" in none and "標題裡沒有「沒有這種字」的文件" in none
    # an address or a document id still checks that page (the frame itself, not the script that names it)
    for ref in (NEWS1, "dnews1"):
        assert LexborHTMLParser(client.get("/check", params={"url": ref}).text).css_first("iframe.chk-frame"), ref


def test_a_sites_page_has_its_merged_misses_and_its_pages(client):
    page = client.get("/check", params={"site": ACA}).text  # since 11-17 its 網頁核查 is a part of its page (/site)
    tree = LexborHTMLParser(page)
    assert tree.css_first(".st-head h1").text() == "教務處"
    start = tree.css_first("h2#check a.btn")
    assert start.text() == "從首頁開始" and start.attributes["data-live-href"] == HOME
    lists = tree.css(".lists section")
    fix = lists[0].css("a.prob")
    assert len(fix) == 2 and "正文區 · 帳本沒有" in fix[0].text() and "2 頁" in fix[0].text() and "選單等" in fix[1].text()
    assert fix[0].attributes["href"].endswith(f"&n={made_n(client)}")
    assert "很薄的頁" in lists[1].text()
    rows = tree.css("#chk-rows a.prow")
    assert [r.css_first(".pn").text() for r in rows[:2]] == ["第一則消息", "第二則消息"]
    assert rows[0].css_first(".pill").text() == "正文區有漏"
    chips = {c.attributes["data-only"]: c for c in tree.css("a.chip")}
    assert chips["gap"].css_first("b").text() == "2" and chips["gap"].attributes["aria-pressed"] == "false"
    assert " title=" not in page.split("<main>", 1)[1]


def made_n(client) -> int:
    """The number the first page carrying the merged content-area miss gives it."""
    return next(p.n for p in client.app_state.sweep.found[NEWS1].problems if p.zone == "in")


def test_a_pressed_chip_shows_only_its_pages_and_a_second_press_returns_to_all(client):
    page = client.get("/check", params={"site": ACA, "only": "gap"}).text
    tree = LexborHTMLParser(page)
    assert len(tree.css("#chk-rows a.prow")) == 2
    gap = next(c for c in tree.css("a.chip") if c.attributes["data-only"] == "gap")
    assert gap.attributes["aria-pressed"] == "true" and gap.attributes["href"] == "/site?site=c.nknu.edu.tw%2Faca#check"
    parts = client.get("/check/site", params={"site": ACA, "q": "沒有這種字"}).json()
    assert "名字和網址裡都沒有「沒有這種字」的網頁" in parts["rows"] and parts["running"] is False
    empty = client.get("/check", params={"site": BIO}).text
    assert "帳本裡這個網站沒有抓下來的網頁" in empty


def test_a_checked_page_names_its_site_and_leads_to_the_next_page_with_a_miss(client):
    page = client.get("/check", params={"url": NEWS1, "n": "3"}).text
    tree = LexborHTMLParser(page)
    crumb = tree.css_first("nav.crumb")
    links = crumb.css("a")
    assert links[0].text() == "教務處" and links[0].attributes["href"] == "/site?site=c.nknu.edu.tw%2Faca#check"
    assert crumb.css_first(".here").text() == "第一則消息"
    assert links[1].text() == "下一個有漏的頁 →" and links[1].attributes["data-live-href"] == NEWS2
    assert tree.css_first("iframe.chk-frame").attributes["data-flash"] == "3"
    last = LexborHTMLParser(client.get("/check", params={"url": NEWS2}).text).css_first("nav.crumb")
    assert "下一個有漏的頁" not in last.text()


def test_the_sweeps_progress_is_an_api(client):
    got = client.get("/api/check/sweep", params={"site": ACA}).json()
    assert got["phase"] == "done" and got["site_total"] == 7 and got["totals"]["gap"] == 2


def test_without_an_archive_the_entry_says_it_is_not_mounted(lib, tmp_path):
    app = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        assert "沒有掛上爬蟲存檔" in c.get("/check").text
        assert "沒有掛上爬蟲存檔" in c.get("/check", params={"url": "教務"}).text
        assert c.get("/check/list", params={"q": "x"}).json() == {"html": "", "running": False}
        assert c.get("/api/check/sweep").json()["phase"] == "no_archive"
    assert app.state.sweep is None


def test_the_new_routes_are_local_only_and_get_only(client):
    for path in ("/check/list", "/check/site", "/check/item", "/api/check/sweep"):
        assert client.get(path, headers={"host": "evil.example:8878"}).status_code == 403
        assert client.post(path).status_code == 405


def test_an_app_starts_no_sweep_until_its_page_is_asked_for(made, lib, tmp_path):
    app = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        assert c.get("/").status_code == 200
        assert app.state.sweep.phase == "idle" and app.state.sweep.thread is None


def test_the_site_list_draws_numbers_only_and_never_blue():
    """The owner 11-15 of 資料總覽's site list: 「那个网站列表的可视化条形可以拿掉」; blue names 公告 and 一般網頁. Since the
    redesign of 2026-11-17 a number of what is missing is violet (--amt), never a state colour."""
    assert ".count" not in SITES_CSS and ".srow .num.amt{color:var(--amt)" in SITES_CSS
    assert "--acc" not in SITES_CSS and "#3578b8" not in SITES_CSS
    assert "num.bad" not in SITES_CSS and "num.warn" not in SITES_CSS and "sv-n" not in SITES_CSS


def test_the_box_checks_an_address_or_a_document_id_and_finds_by_anything_else(lib):
    conn = sqlite3.connect(lib)
    try:
        for ref in ("https://c.nknu.edu.tw/aca/x.aspx", "c.nknu.edu.tw/aca", "sso.nknu.edu.tw/web/592", "dnews1"):
            assert web.asks_a_page(ref, conn), ref
        for ref in ("教務", "List.aspx", "aca/news", "goo.gl", "", "第二則 消息"):
            assert not web.asks_a_page(ref, conn), ref
    finally:
        conn.close()


# ---------------------------------------------------------------- the review of 11-16

def test_words_list_the_matching_sites_before_the_pages(client):
    html = client.get("/check/list", params={"q": "aca"}).json()["html"]
    assert html.index("a class='srow'") < html.index("a class='prow'")


def test_words_never_steer_the_school_window(client):
    for words in ("goo.gl", "aca/news", "List.aspx", "教務"):
        assert client.get("/check/go", params={"ref": words}, follow_redirects=False).status_code == 204, words
    got = client.get("/check/go", params={"ref": NEWS1}, follow_redirects=False)
    assert got.status_code == 302 and got.headers["location"] == NEWS1


def test_a_sites_key_pasted_from_its_row_opens_the_site(client):
    page = client.get("/check", params={"url": ACA}).text
    assert LexborHTMLParser(page).css_first(".st-head h1").text() == "教務處" and "帳本裡沒有這個網址" not in page
    assert LexborHTMLParser(client.get("/check", params={"url": "https://" + ACA + "/"}).text).css_first(".st-head h1")


def test_a_page_of_a_site_not_listed_draws_no_line_to_a_site_page(made, lib):
    sw = swept(made, lib)
    assert sw.crumb("w3.example.org", "https://w3.example.org/admin.html") is None
    sw.close()


def test_only_the_planned_home_is_called_home(made, lib):
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    conn.execute("UPDATE site_plan SET home = '' WHERE site = ?", (CH,))
    conn.close()
    sw = swept(made, lib)
    assert sw.listing.sites[CH].home == CH_HOME  # 「從首頁開始」 still opens something
    assert sw.listing.names[CH_HOME] != "首頁"
    sw.close()


def test_an_attachment_only_miss_is_said_as_the_attachment_area():
    page = sweep.Page("https://x.example/a", "x", "got", True, "/a")
    found = sweep.Found("https://x.example/a", "ok", fix=(0, 2, 0))
    assert sweep.state_of(page, found) == "files" and sweep.PILLS["files"][0] == "附件區有漏"
    assert "gap" in sweep.chips_of(page, found, "files")
    doubtful = sweep.Page("https://x.example/a", "x", "look", True, "/a")
    assert "check" in sweep.chips_of(doubtful, sweep.Found("https://x.example/a", "ok", fix=(1, 0, 0)), "gap")


def test_simplified_typing_finds_the_schools_tai_written_with_the_simple_form():
    """OpenCC s2t turns 台湾 into 臺灣, which the school's titles seldom write: both sides compare in Simplified."""
    assert sweep.norm("台灣 發布") == sweep.norm("台湾发布") == sweep.norm("臺灣發佈")[:2] + "发布"
    assert sweep.query_forms("台湾")[0] in sweep.norm("台灣歷史文化及語言研究所")


def test_school_text_is_escaped_everywhere(made, lib, tmp_path):
    """A title, a link's words and a site's name that carry markup reach the pages and their JSON as text only."""
    bad = "<script>alert(1)</script>\"'<img src=x onerror=alert(2)>\u2028"
    conn = sqlite3.connect(lib)
    conn.execute("UPDATE docs SET title = ? WHERE doc_id = 'dnews1'", (bad,))
    conn.execute("UPDATE sites SET name = ? WHERE site = ?", (bad, ACA))
    conn.commit()
    conn.close()
    ledger = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    ledger.execute("UPDATE link SET text = ? WHERE text = '第二則消息'", (bad,))
    ledger.close()
    app = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        c.get("/check")
        assert app.state.sweep.wait(30)
        pages = [c.get("/check").text, c.get("/check", params={"site": ACA, "q": bad}).text,
                 c.get("/check", params={"url": "script"}).text, c.get("/check", params={"url": NEWS1}).text,
                 c.get("/check/list", params={"q": "alert"}).json()["html"],
                 c.get("/check/site", params={"site": ACA, "only": bad}).json()["rows"]]
    for html in pages:
        tree = LexborHTMLParser(html)
        assert not any("alert" in (n.text() or "") for n in tree.css("script")), "a script carries school text"
        assert not tree.css("img[onerror]") and "<script>alert" not in html


# ---------------------------------------------------------------- 11-16 evening: the second round of the page check

def test_the_entry_lists_the_schools_misses_merged_across_sites_the_most_pages_first():
    def row(key, pages, zone="in", bucket="missing", text="", page="p"):
        return {"key": key, "target": key, "text": text, "zone": zone, "bucket": bucket, "reason": "", "what": "link",
                "tip": "", "page": page, "n": 1, "pages": pages, "last": ""}
    one, two = sweep.Site("a", "甲站", "", True, ()), sweep.Site("b", "乙站", "", True, ())
    first = sweep.SiteData(fix=[row("https://x.example/1", 3, page="a1"), row("https://x.example/m", 50, zone="out")],
                           look=[row("https://x.example/thin", 2, bucket="look")])
    second = sweep.SiteData(fix=[row("https://x.example/1", 5, page="b1", text="辦法"),
                                 row("https://x.example/2", 4, zone="files")])
    top = sweep.school_problems([(one, first), (two, second)], limit=1)
    assert top["n_fix"] == 2 and top["n_look"] == 1  # a menu's miss is the site page's, not the entry's
    got = top["fix"][0]
    assert (got["target"], got["pages"], got["sites"]) == ("https://x.example/1", 8, 2)
    assert (got["site_name"], got["page"], got["text"]) == ("乙站", "b1", "辦法")  # opens the site that has it most
    assert len(top["fix"]) == 1


def test_the_entry_shows_the_schools_lists_and_its_old_list_still_sorts(client):
    tree = LexborHTMLParser(client.get("/doc").text)
    lists = tree.css("#doc-list .lists section")
    assert len(lists) == 2 and "漏了的辦法" in lists[0].text() and "教務處 · 正文區 · 帳本沒有" in lists[0].text()
    assert "很薄的頁" in lists[1].text() and "選單沒抓到" not in lists[0].text()
    assert client.get("/doc/find").json()["html"].count("class='lists") == 1  # the part its script asks for again
    listed = LexborHTMLParser(client.get("/check/list", params={"sort": "check"}).json()["html"])
    check = next(a for a in listed.css(".sl-head a") if a.text() == "要查")
    assert check.attributes["aria-pressed"] == "true"
    assert str(client.get("/check", params={"sort": "check"}).url).endswith("/doc")
    assert client.app_state.sweep.overview("drop table")["sort"] == "gap"
    found = client.get("/check/list", params={"q": "教務", "sort": "check"}).json()["html"]
    assert "class='lists" not in found and "<a href" not in found.split("sl-head", 1)[1].split("</div>", 1)[0]


def test_a_ledger_read_again_says_so_never_a_full_count():
    from nknu.ops.check_sites import summary_line
    line = summary_line({"phase": "reading", "running": True, "ready": True, "done": 8, "total": 8},
                        {"pages": 8, "checked": 8})
    assert "帳本改過，正在重讀" in line and "核查中" not in line and "class='prog ind'" in line


def test_every_reason_the_first_sweep_showed_as_code_is_said_in_words():
    import re
    for code in ("api:notFound", "empty_detail", "not_file:no_name:html", "unhandled:google", "net_RemoteProtocolError",
                 "api:cannotDownloadFile", "http_401", "http_403", "http_500", "http_400", "http_4xx", "http_418", "thin",
                 "round2:unhandled", "restart: download 0123 failed: press_redirect", "not_file:saying:other"):
        words = pagecheck.reason_words(code)
        assert re.search(r"[一-鿿]", words), (code, words)
    assert pagecheck.reason_words("http_401") == "學校要求登入（401）"
    assert pagecheck.reason_words("not_file:no_name:html") == "應該是檔案，回來是網頁"


def test_a_nameless_image_or_frame_is_named_by_its_file_or_its_host():
    from nknu.ops.check_page import item_name
    img = {"text": "", "what": "image",
           "target": "https://sso.nknu.edu.tw/news/attachedfile.aspx?cimagepath=%5CNknuNews%5Cimage%5CNews1%5Cab12.jpg"}
    assert item_name(img) == "圖片 · ab12.jpg"
    frame = {"text": "[iframe]", "what": "link", "target": "https://www.youtube.com/embed/xyz"}
    assert item_name(frame) == "內嵌框 · www.youtube.com"
    assert item_name({"text": "", "what": "button", "target": ""}) == "按鈕"
    assert item_name({"text": "辦法", "what": "link", "target": "https://x.example/a.pdf"}) == "辦法"
    assert item_name({"text": "D", "what": "button", "target": ""}) == "按鈕「D」"
    assert item_name({"text": "8", "what": "link", "target": "https://x.example/p8"}) == "連結「8」"
    assert item_name({"text": "", "what": "button", "target": "", "tip": "按鈕（按出檔案）\n帳本：失敗"}) == "按鈕（按出檔案）"


def test_buttons_merge_by_their_words_on_one_site_only_and_a_wordless_one_is_its_own():
    def row(key, target="", text="", page="p"):
        return {"key": key, "target": target, "text": text, "zone": "in", "bucket": "failed", "reason": "",
                "what": "button", "tip": "", "page": page, "n": 1, "pages": 1, "last": ""}
    one, two = sweep.Site("a", "甲站", "", True, ()), sweep.Site("b", "乙站", "", True, ())
    first = sweep.SiteData(fix=[row("button|查詢", text="查詢"), row("button|https://a/1#3")])
    second = sweep.SiteData(fix=[row("button|查詢", text="查詢"), row("button|https://b/1#3")])
    top = sweep.school_problems([(one, first), (two, second)])
    assert top["n_fix"] == 4 and all(r["sites"] == 1 for r in top["fix"])


def test_an_older_result_is_not_dimmed_and_a_checked_page_names_its_tab_by_the_page(client):
    from nknu.ops.check_sites import page_rows
    drawn = page_rows([{"url": NEWS1, "name": "第一則消息", "path": "/aca/news1.aspx", "pill": "正文區有漏", "tone": "bad",
                        "tip": "帳本改過，這是改之前的結果", "older": True, "state": "gap"}])
    assert "class='prow'" in drawn and "old" not in drawn.split("class='prow", 1)[1].split("'", 1)[0]
    title = LexborHTMLParser(client.get("/check", params={"url": NEWS1}).text).css_first("title").text()
    assert title.startswith("第一則消息 · 網頁核查")


# ---------------------------------------------------------------- 11-16 evening: a miss opened in place

def test_a_merged_row_opens_in_place_with_its_pages_and_the_archived_page_flashed(made, lib, client):
    sw = client.app_state.sweep
    view = sw.item_view(ACA, MISSING)
    assert [p["url"] for p in view["pages"]] == [NEWS1, NEWS2] and view["total"] == 2 and view["sites"] == 1
    assert view["item"]["bucket"] == "missing" and view["item"]["zone"] == "in"
    assert sw.item_view("", MISSING)["total"] == 2  # an address from the entry: every site's pages
    assert sw.item_view(ACA, "https://nowhere.example/") is None and sw.item_view(ACA, "") is None
    got = client.get("/check/item", params={"site": ACA, "key": MISSING})
    tree = LexborHTMLParser(got.json()["html"])
    assert tree.css_first("h2").text() == "漏了的辦法" and "出現在" in tree.text() and "爬蟲可補" in tree.text()
    frame = tree.css_first("iframe.dr-frame")
    assert frame.attributes["src"] == "/check/raw?" + urlencode({"url": NEWS1})
    assert frame.attributes["data-flash"] == str(made_n(client)) and frame.attributes["sandbox"] == "allow-same-origin"
    picks = tree.css("button.dr-pg")
    assert [b.attributes["data-page"] for b in picks] == [NEWS1, NEWS2] and picks[0].attributes["aria-pressed"] == "true"
    school = tree.css_first("a.dr-school")
    assert school.attributes["target"] == "_blank" and school.attributes["href"] == NEWS1  # only when asked
    assert client.get("/check/item", params={"site": ACA, "key": "nothing"}).status_code == 404


def test_the_rows_open_the_drawer_and_never_the_school_window(client):
    for page in (client.get("/check", params={"site": ACA}).text, client.get("/check").text):
        tree = LexborHTMLParser(page)
        assert "hidden" in tree.css_first("aside#chk-drawer").attributes
        rows = tree.css("a.prob.two")
        assert rows and all(r.attributes.get("data-key") for r in rows)
        assert not any("data-live-href" in r.attributes for r in rows)
    site_row = LexborHTMLParser(client.get("/check", params={"site": ACA}).text).css_first("a.prob.two")
    assert site_row.attributes["data-site"] == ACA and site_row.attributes["data-key"] == MISSING
    entry_row = LexborHTMLParser(client.get("/check").text).css_first("a.prob.two")
    assert entry_row.attributes["data-site"] == ""  # an address: the drawer lists every site's pages


def test_the_school_window_follows_only_while_its_switch_is_on():
    from nknu.ops.check_page import CHECK_JS, check_form
    form = LexborHTMLParser(check_form("", NEWS1))
    switch = form.css_first("button[data-follow]")
    assert switch.text() == "跟著跳" and switch.attributes["aria-pressed"] == "false"
    assert "nk-follow" in CHECK_JS and "nk-live" not in CHECK_JS  # 開真實網頁 alone no longer turns following on


# ---------------------------------------------------------------- the redesign of 2026-11-17

def test_the_deciding_numbers_are_four_tiles_never_a_line_joined_by_dots(client):
    """research/dashboard_redesign_2026-11-17/diagnosis.md: the line 「…正文區有漏 67 頁 · 選單沒抓到…」 became tiles."""
    from nknu.ops.check_sites import summary_line
    tree = LexborHTMLParser(client.get("/doc").text)
    tiles = tree.css("#doc-list .sv .ck-fig")
    assert [t.css_first(".ck-w").text() for t in tiles] == ["正文區有漏", "選單沒抓到", "要查", "沒抓下來"]
    assert [t.css_first("b").text() for t in tiles] == ["2", "1", "2", "1"]
    assert all(t.attributes.get("data-tip") for t in tiles) and "class='sep'" not in tree.html
    assert tree.css_first("#doc-list .sv .sv-line .hd").text() == "已核查 8 頁"
    zero = summary_line({"phase": "done", "ready": True}, {"pages": 3, "checked": 3, "gap": 0, "menu_links": 4})
    assert "class='ck-fig z'" in zero and "class='ck-fig'" in zero  # a 0 is faint, a count violet
    running = summary_line({"phase": "checking", "running": True, "ready": True, "done": 1, "total": 4},
                           {"pages": 4, "checked": 1, "gap": 1})
    assert "核查中 1 / 4 頁" in running and "class='prog'" in running and "class='ck-figs'" in running
    nothing = summary_line({"phase": "checking", "running": True, "ready": True, "done": 0, "total": 4},
                           {"pages": 4, "checked": 0})
    assert "ck-fig" not in nothing  # nothing checked yet: no 0 that reads as 「nothing missed」


def test_a_sites_key_floats_in_its_names_card_not_after_every_row(client):
    tree = LexborHTMLParser(client.get("/check/list").json()["html"])
    first = tree.css_first("a.srow")
    assert not first.css("code") and first.css_first(".nm").attributes["data-tip"] == ACA
    assert "amt" in first.css(".num")[1].attributes["class"]  # 正文區有漏 2: violet, not a state colour


def test_a_merged_row_has_no_check_link_of_its_own_and_its_drawer_has_it(client):
    page = client.get("/check", params={"site": ACA}).text
    tree = LexborHTMLParser(page)
    assert not tree.css(".lists a.go") and ">核查</a>" not in page
    got = client.get("/check/item", params={"site": ACA, "key": MISSING}).json()["html"]
    drawer = LexborHTMLParser(got)
    check = next(a for a in drawer.css("a.dr-go") if a.text() == "核查它指的網址")
    assert check.attributes["href"] == "/check?" + urlencode({"url": MISSING})


def test_only_a_checked_page_is_wider_than_the_shared_page_width(client):
    assert "max-width:1680px" not in client.get("/check").text
    assert "max-width:1680px" not in client.get("/check", params={"site": ACA}).text
    assert "max-width:1680px" in client.get("/check", params={"url": NEWS1}).text


# ---------------------------------------------------------------- the four entries (structure design 2026-11-17 二)

def test_one_sites_page_holds_everything_about_it_in_order(client):
    lib = client.app_state.kinds.db
    conn = sqlite3.connect(lib)
    conn.execute("UPDATE docs SET site = ? WHERE doc_id = 'dnews1'", (ACA,))
    conn.commit()
    conn.close()
    client.get("/site", params={"site": ACA})
    client.app_state.kinds.wait(10)
    page = client.get("/site", params={"site": ACA})
    assert page.status_code == 200 and "aria-current='page'>網站</a>" in page.text  # a site's page is 網站's
    tree = LexborHTMLParser(page.text)
    head = tree.css_first(".st-head h1")
    assert head.text() == "教務處" and ACA in head.attributes["data-tip"] and HOME in head.attributes["data-tip"]
    assert tree.css_first(".st-now").attributes["aria-label"] == "這個網站現在" and tree.css_first(".st-now .pill")
    heads = [(h.attributes.get("id"), h.text().strip()) for h in tree.css("h2.st-sec")]
    assert [i for i, _ in heads] == ["steps", "changes", "check", "docs"]
    assert heads[0][1] == "處理流程" and heads[2][1].startswith("網頁核查") and heads[3][1] == "文件"
    assert "沒有前一份庫可以比" in page.text  # the last swap's comparison says why it has nothing to show
    assert tree.css_first("#chk-site").attributes["data-path"] == "/site"  # its 網頁核查, kept on this page
    kinds = [li.text() for li in tree.css(".st-kinds li")]
    assert any(k.startswith("公告") for k in kinds) and kinds[-1].startswith("合計")
    links = {a.text(): a.attributes["href"] for a in tree.css(".st-links a")}
    assert links["沒日期的原因"] == "http://127.0.0.1:18001/aca/site_undated_why?site=c.nknu.edu.tw%2Faca"
    assert links["全部文件"].endswith("/aca/site_docs?site=c.nknu.edu.tw%2Faca")
    assert client.get("/site", params={"site": "nowhere.example"}).status_code == 404
    old = client.get("/pipeline/site", params={"site": ACA})
    assert str(old.url).endswith("/site?site=c.nknu.edu.tw%2Faca") and "處理流程" in old.text


def test_the_doc_box_finds_documents_pages_and_sites_and_says_which_is_which(client):
    page = LexborHTMLParser(client.get("/doc", params={"q": "第一則"}).text)
    lib, web_ = page.css("#doc-list .dc-two .plist")
    assert lib.css_first("h2").text().startswith("庫裡的文件") and web_.css_first("h2").text().startswith("抓到的網頁")
    assert lib.css_first("a.prow").attributes["href"] == "/trace?ref=dnews1" and "第一則消息" in lib.text()
    assert web_.css_first("a.prow").attributes["href"].startswith("/check?url=") and "第一則消息" in web_.text()
    sites = LexborHTMLParser(client.get("/doc", params={"q": "教务"}).text).css(".dc-sites a")  # Simplified finds it
    assert sites and sites[0].text().strip() == "教務處" and sites[0].attributes["href"] == "/site?site=c.nknu.edu.tw%2Faca"
    named = LexborHTMLParser(client.get("/doc", params={"q": "dnews1"}).text)
    assert named.css_first("a.prow").attributes["href"] == "/trace?ref=dnews1"  # an id: that document first
    found = client.get("/doc/find", params={"q": "第一則"}).json()["html"]
    assert "庫裡的文件" in found and "<form" not in found  # the list alone, for the page's script
    assert "全校網頁核查" in client.get("/doc/find").json()["html"]  # nothing typed: the school's lists again
