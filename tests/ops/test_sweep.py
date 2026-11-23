"""網頁核查 without remembering anything (design docs/superpowers/specs/2026-11-16-page-check-design.md section 六): the
sweep of every stored page (nknu.ops.sweep), the entry with its sites, a site's page with its misses merged and its
pages, the box that finds by words, the checked page's line back to its site. A made-up ledger (the crawler's own
schema) and WARC hold two sites of c.nknu: one with pages that miss things in the content area and in the menus, one
quiet page, a deleted page and a failed one; a third site of the first round has no page at all. No real data."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
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
    # owner 11-22: each address once, in its class, with on how many pages; the menus' misses apart, counted once
    assert total["cls"] == {"fix": 1, "check": 1, "school": 0} and total["cls_pages"] == {"fix": 2, "check": 1, "school": 0}
    misses = [(r["target"], r["zone"], r["pages"], r["cls"], r["kind"]) for r in view["misses"]]
    assert misses == [(MISSING, "in", 2, "fix", "page"), (THIN, "in", 1, "check", "page")]
    assert [(r["target"], r["zone"], r["pages"]) for r in view["menu"]] == [(LOST, "out", 4)]  # not the deleted page's
    first = view["misses"][0]
    assert first["page"] == NEWS1 and first["n"] > 0 and first["text"] == "漏了的辦法"
    assert view["misses"][1]["bucket"] == "look" and view["misses"][1]["reason"] == "頁面幾乎是空的"
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


def test_a_sites_pages_come_a_part_at_a_time_from_where_the_list_has_got_to(made, lib):
    """11-23 port audit: a kind over 300 pages could not be reached past its first 300. offset: where this part
    starts in the filtered list (matched counts the whole of it); anything that is not a number starts at 0."""
    sw = swept(made, lib)
    whole = [r["url"] for r in sw.site_view(ACA)["rows"]]
    parts = [sw.site_view(ACA, offset=at, limit=3) for at in (0, 3, 6)]
    assert [r["url"] for p in parts for r in p["rows"]] == whole and len(whole) == 7
    assert [p["offset"] for p in parts] == [0, 3, 6] and {p["matched"] for p in parts} == {7}
    assert sw.site_view(ACA, offset=99)["rows"] == [] and sw.site_view(ACA, offset=99)["matched"] == 7
    for odd in ("", "x", "-4", None):
        assert sw.site_view(ACA, offset=odd)["offset"] == 0
    assert sw.site_view(ACA, offset="3", limit=3)["offset"] == 3  # as a query string gives it
    menu = sw.site_view(ACA, only="menu", offset=1, limit=1)  # the filter first, then the part
    assert menu["matched"] == 2 and len(menu["rows"]) == 1 and "menu" in menu["rows"][0]["chips"]
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
        return pagecheck.check_verdict(archive, url, **kw)

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
        return pagecheck.check_verdict(archive, url, **kw)

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
        return pagecheck.check_verdict(archive, url, **kw)

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
        return pagecheck.check_verdict(archive, url, **kw)

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


def test_the_schools_page_check_is_its_own_page_and_the_doc_search_opens_on_what_it_takes(client):
    # 2026-11-18: 全校網頁核查 is /check with nothing asked, in the bar's 工具 menu (the four numbers and the school's
    # lists; the table of sites is on each site's page since 2026-11-17); 文件搜尋 with nothing typed says what its box
    # takes and does not start the sweep
    page = client.get("/check")
    assert page.status_code == 200 and str(page.url).endswith("/check") and "<title>全校網頁核查" in page.text
    assert "aria-current='page'>網頁核查</a>" in page.text and "<details class='nk-more nk-on'>" in page.text
    tree = LexborHTMLParser(page.text)
    assert not tree.css("a.srow") and tree.css_first("form#chk-ask input[name=url]") is not None
    empty = LexborHTMLParser(client.get("/doc").text)
    # 11-23 design: what the box takes is the ? beside it, not a card under it
    assert not empty.css("#doc-list .dc-empty") and "可以打" in empty.css_first("form.dc-ask .pd-hp").attributes["data-tip"]
    assert not empty.css("#doc-list .lists") and "已核查" not in empty.text()
    assert "dc-empty" in client.get("/doc/find").json()["html"]
    assert "已核查 8 頁" in page.text and "爬蟲能補" in page.text and "選單裡沒抓到" in page.text
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
    panes = tree.css(".ck-tabs .ck-pane")
    fix = panes[0].css("a.prob")
    assert len(fix) == 1 and "網頁 · 帳本沒有" in fix[0].text() and "出現在 2 頁" in fix[0].text()
    assert fix[0].attributes["href"].endswith(f"&n={made_n(client)}")
    assert "很薄的頁" in panes[1].text() and "hidden" in panes[1].attributes
    menu = tree.css_first("details.ck-menu")  # the menus' miss: a small line under the tiles that opens its list
    assert "選單裡沒抓到 1 個連結" in menu.css_first("summary").text() and "選單等" in menu.css_first("a.prob").text()
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

def _row(key, pages, *, zone="in", bucket="missing", text="", page="p", what="link", target=None, cls="fix"):
    """A site's merged row as sweep.merge_problems makes it."""
    target = key if target is None else target
    return {"key": key, "target": target, "text": text, "zone": zone, "bucket": bucket, "reason": "", "what": what,
            "tip": "", "page": page, "n": 1, "pages": pages, "last": "", "cls": cls,
            "kind": pagecheck.miss_kind(what, target)}


def _data(*rows, menu=(), pages=None) -> sweep.SiteData:
    misses = sorted(rows, key=sweep.list_order)
    return sweep.SiteData(misses=misses, menu=list(menu), cls_pages=pages or {},
                          cls={k: sum(r["cls"] == k for r in misses) for k in sweep.CLASS_KEYS})


def test_the_entry_lists_the_schools_misses_merged_across_sites_each_address_once():
    one, two = sweep.Site("a", "甲站", "", True, ()), sweep.Site("b", "乙站", "", True, ())
    m = _row("https://x.example/m", 50, zone="out")
    first = _data(_row("https://x.example/1.pdf", 3, page="a1"), _row("https://x.example/thin", 2, bucket="look",
                                                                       cls="check"), menu=[m], pages={"fix": 3, "check": 2})
    second = _data(_row("https://x.example/1.pdf", 5, page="b1", text="辦法"), _row("https://x.example/2", 4, zone="files"),
                   menu=[m], pages={"fix": 7})
    top = sweep.school_problems([(one, first), (two, second)], limit=1)
    assert top["n"] == {"fix": 2, "check": 1, "school": 0}  # a menu's miss is no tile's: counted apart, once
    assert top["menu"] == 1 and top["pages"] == {"fix": 10, "check": 2, "school": 0}
    got = top["rows"]["fix"][0]
    assert (got["target"], got["pages"], got["sites"], got["kind"]) == ("https://x.example/1.pdf", 8, 2, "file")
    assert (got["site_name"], got["page"], got["text"]) == ("乙站", "b1", "辦法")  # opens the site that has it most
    assert len(top["rows"]["fix"]) == 1


def test_one_address_on_thousands_of_pages_counts_once_and_never_heads_the_list():
    """The lab, 11-22: 「高師大官網 7,859 頁 · 課程查詢系統 等 62 個網站 · 正文區 · 失敗」 was one address heading the
    school's list (the school's homepage taken for a file: not_file), 「Hide Document Map」 a report viewer's button."""
    home = "https://w3.nknu.edu.tw/"
    sites = []
    for i in range(62):
        site = sweep.Site(f"s{i}", f"站{i}", "", True, ())
        rows = [_row(home, 7859 // 62 + (i < 7859 % 62), bucket="failed", text="高師大官網")]
        if i == 0:
            rows += [_row("https://x.example/rule.pdf", 2, text="辦法"), _row("https://x.example/a.aspx", 3, text="一頁"),
                     _row("https://x.example/p.png", 9, what="image"),
                     _row("button|Hide Document Map", 423, what="button", target="", text="Hide Document Map",
                          bucket="missing", cls="check")]
        sites.append((site, _data(*rows, pages={"fix": 7859 if i == 0 else 0})))
    top = sweep.school_problems(sites)
    assert top["n"]["fix"] == 4  # the homepage once, not 7,859 times; the button is no 爬蟲能補
    fix = [(r["target"], r["kind"], r["sites"], r["pages"]) for r in top["rows"]["fix"]]
    assert fix == [("https://x.example/rule.pdf", "file", 1, 2), (home, "page", 62, 7859),
                   ("https://x.example/a.aspx", "page", 1, 3), ("https://x.example/p.png", "image", 1, 9)]
    assert [(r["text"], r["kind"]) for r in top["rows"]["check"]] == [("Hide Document Map", "button")]
    rows = [_row("button|查詢", 900, what="button", target="", text="查詢", cls="check"),
            _row("https://x.example/thin", 1, bucket="look", cls="check")]
    assert [r["kind"] for r in sorted(rows, key=sweep.list_order)] == ["page", "button"]  # buttons last


def test_the_class_of_a_miss_follows_the_owners_three_groups():
    c = pagecheck.miss_class
    # the crawler's: no record in the ledger, a failure of ours
    assert c("missing") == c("missing", "image", "not_discovered") == "fix"
    assert c("failed", "link", "not_file") == "fix"  # the school's homepage taken for a file
    assert c("failed", "link", "failed", "timeout") == c("failed", "link", "failed", "tls") == "fix"
    assert c("failed", "link", "failed", "net_ReadError") == c("failed", "link", "lab", "connect") == "fix"
    # 11-22 evening: a host no name server knows is a dead link on the school's page; a wrong certificate the site's
    assert c("failed", "link", "lab", "dns") == c("failed", "link", "failed", "tls_cert") == "school"
    assert c("failed", "link", "http_4xx", "http_400") == c("failed", "link", "parked", "denied_dir") == "fix"
    assert c("failed", "link", "api_quota") == "fix"
    # the school's: sign-in and refusals, deletions and broken links, its error pages and servers, Google's
    assert c("failed", "link", "refused", "http_403") == c("failed", "link", "server_error") == "school"
    assert c("failed", "link", "failed", "connect") == c("failed", "link", "failed", "server_down") == "school"
    assert c("failed", "link", "failed", "http_503") == "school"
    assert c("failed", "link", "not_file", "not_file:saying:gone_page") == "school"
    assert c("failed", "link", "not_file", "out:error_page") == "school"  # a file link sent to the school's error page
    assert c("review", "link", "api_error", "api:notFound") == c("review", "link", "api_error", "api:cannotDownloadFile")
    assert c("review", "link", "api_error", "api:notFound") == "school"
    # to look at: doubtful content, a writing nobody decided, Google's other answers, and every button
    assert c("look", "link", "thin") == c("review", "link", "unhandled:google") == "check"
    assert c("review", "link", "api_error", "api:automated_queries") == "check"
    for bucket in pagecheck.PROBLEMS:
        assert c(bucket, "button", "refused") == c(bucket, "button") == "check"
    assert c("got") == c("rule", "link", "gone") == ""  # what no list shows has no class
    assert [k for k, _n, _t in pagecheck.CLASSES] == ["fix", "check", "school"]  # the tabs' order


def test_what_a_miss_is_is_read_from_its_address():
    k = pagecheck.miss_kind
    assert k("link", "https://x.example/a/rule.PDF") == k("link", "https://drive.google.com/file/d/1a/view") == "file"
    assert k("link", "https://sso.nknu.edu.tw/news/attachedfile.aspx?x=1") == "file"
    assert k("link", "https://docs.google.com/document/d/1a/edit") == "file"
    assert k("link", "https://docs.google.com/forms/d/1a/viewform") == k("link", "https://w3.nknu.edu.tw/") == "page"
    assert k("link", "https://x.example/p.jpg") == k("image", "https://x.example/a.pdf") == "image"
    assert k("button", "") == "button" and k("link", "") == "page"
    assert [w for _k, w in pagecheck.KINDS] == ["檔案", "網頁", "圖片", "按鈕"]


def test_a_check_without_a_class_is_classed_when_it_is_kept():
    summary = {"state": "ok", "verdict": {"fix": {"in": 1}}, "problems": [
        {"target": "https://x.example/a", "zone": "in", "bucket": "failed", "result": "refused", "what": "link"},
        {"target": "https://x.example/b", "zone": "in", "bucket": "failed", "result": "not_file", "what": "link"}]}
    kept = sweep.compact("https://x.example/", summary).problems
    assert [p.cls for p in kept] == ["school", "fix"]
    assert kept[1].reason == "應該是檔案，回來不是"  # no reason of its own: the result's words, not the bucket's 失敗


def test_the_entry_shows_the_schools_lists_and_its_old_list_still_sorts(client):
    tree = LexborHTMLParser(client.get("/check").text)
    tabs = tree.css("#doc-list .ck-tabs [role=tab]")
    assert [t.text() for t in tabs] == ["爬蟲能補1", "要確認1", "學校那邊0"]
    panes = tree.css("#doc-list .ck-tabs .ck-pane")
    assert len(panes) == 3 and "漏了的辦法" in panes[0].text() and "網頁 · 教務處 · 帳本沒有" in panes[0].text()
    assert "很薄的頁" in panes[1].text() and "選單" not in panes[0].text()  # a menu's miss: the site page's
    assert "選單裡沒抓到 1 個連結" in tree.css_first("#doc-list .ck-notes").text()
    # the part its script asks for again
    assert client.get("/doc/find", params={"school": "1"}).json()["html"].count("class='card pad ck-tabs'") == 1
    listed = LexborHTMLParser(client.get("/check/list", params={"sort": "check"}).json()["html"])
    check = next(a for a in listed.css(".sl-head a") if a.text() == "要查")
    assert check.attributes["aria-pressed"] == "true"
    assert "<title>全校網頁核查" in client.get("/check", params={"sort": "check"}).text  # the school's page itself
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
    def row(key, text=""):
        return _row(key, 1, bucket="failed", what="button", target="", text=text, cls="check")
    one, two = sweep.Site("a", "甲站", "", True, ()), sweep.Site("b", "乙站", "", True, ())
    first = _data(row("button|查詢", text="查詢"), row("button|https://a/1#3"))
    second = _data(row("button|查詢", text="查詢"), row("button|https://b/1#3"))
    top = sweep.school_problems([(one, first), (two, second)])
    assert top["n"]["check"] == 4 and all(r["sites"] == 1 for r in top["rows"]["check"])


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
    assert tree.css_first("h2").text() == "漏了的辦法" and "出現在" in tree.text() and "爬蟲能補" in tree.text()
    assert "網頁" in tree.css_first(".dr-tags").text()  # what it is, as its row says
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
    site_row = LexborHTMLParser(client.get("/check", params={"site": ACA}).text).css_first(".ck-tabs a.prob.two")
    assert site_row.attributes["data-site"] == ACA and site_row.attributes["data-key"] == MISSING
    entry_row = LexborHTMLParser(client.get("/check").text).css_first(".ck-tabs a.prob.two")
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
    tree = LexborHTMLParser(client.get("/check").text)
    tiles = tree.css("#doc-list .sv .ck-fig")
    # owner 11-22: the misses counted once each in their three classes, on how many pages they are under each
    assert [t.css_first(".ck-w").text() for t in tiles] == ["爬蟲能補", "要確認", "學校那邊", "沒抓下來"]
    assert [t.css_first("b").text() for t in tiles] == ["1", "1", "0", "1"]
    assert [t.css_first("small").text() for t in tiles] == ["個網址", "個", "個", "頁"]
    assert [(s.text() if (s := t.css_first(".ck-s")) else "") for t in tiles] == ["出現在 2 頁", "出現在 1 頁", "", ""]
    assert all(t.attributes.get("data-tip") for t in tiles) and "class='sep'" not in tree.html
    assert tree.css_first("#doc-list .sv .sv-line .hd").text() == "已核查 8 頁"
    zero = summary_line({"phase": "done", "ready": True}, {"pages": 3, "checked": 3, "cls": {"fix": 2}, "menu": 4})
    assert "class='ck-fig z'" in zero and "class='ck-fig'" in zero  # a 0 is faint, a count violet
    running = summary_line({"phase": "checking", "running": True, "ready": True, "done": 1, "total": 4},
                           {"pages": 4, "checked": 1, "gap": 1})
    assert "正在逐頁核查 1 / 4 頁" in running and "class='prog'" in running and "class='ck-figs'" in running
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
    assert page.status_code == 200 and "aria-current='page'>內容</a>" in page.text  # a site's page is 內容's
    tree = LexborHTMLParser(page.text)
    head = tree.css_first(".st-head h1")
    assert head.text() == "教務處" and ACA in head.attributes["data-tip"] and HOME in head.attributes["data-tip"]
    assert tree.css_first(".st-head .v3-state b").text() and len(tree.css(".hm-nodes .hm-node")) == 4
    # the site page of 2026-11-18 (spec 十): the state, the steps with a picked one's detail beside them, what is not
    # normal, the documents, the nights, the last swap; then 網頁核查 across the page
    assert tree.css_first("header.st-head").attributes["data-q"] == "1"
    secs = [(x.attributes["id"], x.attributes["data-q"], x.css_first("h2").text().strip()) for x in tree.css("section.st-q")]
    # 11-23 design: 每晚紀錄 went (the nights are on 內容); the layer then folds 文件 and 上次換庫 into the head
    assert secs == [("flow", "2", "流程"), ("problems", "3", "問題"), ("docs", "4", "文件"), ("changes", "6", "上次換庫的變動")]
    assert tree.css_first("#quality") is not None and tree.css_first("a#sqBack").attributes["href"] == "/sites"
    text = page.text
    assert text.index("id='changes'") < text.index("<h2 class='sec st-sec st-check' id='check'>網頁核查")
    assert "各步驟細節" not in text and "echarts" not in text  # a step's facts are in its panel; no chart library
    assert "沒有前一份庫可以比" in page.text  # the last swap's comparison says why it has nothing to show
    assert tree.css_first("#chk-site").attributes["data-path"] == "/site"  # its 網頁核查, kept on this page
    kinds = [li.text() for li in tree.css("#docs .st-legend li")]
    assert any(k.startswith("公告") for k in kinds) and tree.css("#docs .st-kbar i")
    links = {a.text(): a.attributes["href"] for a in tree.css(".st-links a")}
    assert links["沒日期的原因"] == "http://127.0.0.1:18001/aca/site_undated_why?site=c.nknu.edu.tw%2Faca"
    assert links["全部文件"].endswith("/aca/site_docs?site=c.nknu.edu.tw%2Faca")
    assert client.get("/site", params={"site": "nowhere.example"}).status_code == 404
    old = client.get("/pipeline/site", params={"site": ACA})
    assert str(old.url).endswith("/site?site=c.nknu.edu.tw%2Faca") and "id='flow'" in old.text


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


def test_a_sweep_is_not_started_while_the_nightly_update_fetches_and_the_pages_say_so(made, lib):
    # 2026-11-18: opening 找文件 at 03:32 began a sweep (about 17 minutes of reads of the archive) beside the nightly
    # update; with hold nothing starts, the last results stay, and a page says why
    from nknu.ops.check_sites import summary_line

    sw = sweep.Sweep(made.archive, lib, resweep_seconds=0)
    sw.ensure(hold=True)
    assert sw.phase == "idle" and sw.thread is None and sw.progress()["held"] and not sw.progress()["ready"]
    waiting = summary_line(sw.progress(), None)
    assert "每晚更新正在抓網頁，核查等它跑完再開始" in waiting and "prog" not in waiting  # no bar: nothing runs
    sw.ensure()  # the night's fetching over: the next look starts it
    assert sw.wait(30) and sw.phase == "done" and not sw.progress()["held"]
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    conn.execute("UPDATE meta SET value = value WHERE 0")  # nothing changed, then a change the crawler would make
    new = C("https://c.nknu.edu.tw/ch/Held.aspx")
    offset, = _records(made.archive / WARC, [(new, "200", [("Content-Type", "text/html")], _page("", "<p>新的</p>"))])
    _url(conn, new, warc=WARC, offset=offset)
    conn.close()
    sw.ensure(hold=True)  # the ledger changed, but the nightly update is fetching: the last results stay
    progress = sw.progress()
    assert sw.gen == 1 and sw.phase == "done" and progress["held"] and progress["ready"]
    line = summary_line(progress, sw.overview()["totals"])
    assert "每晚更新在跑，先看上次的結果" in line and "已核查 8 頁" in line
    sw.ensure()
    assert sw.wait(30) and sw.gen == 2 and not sw.progress()["held"]


# ---------------------------------------------------------------- owner 11-22: three classes, each address once

def test_the_list_is_three_tabs_that_switch_in_place():
    from nknu.ops.check_sites import MERGED_SHOWN, SITES_JS, miss_tabs
    rows = {"fix": [{**_row(f"https://x.example/{i}.pdf", 1), "site_name": "甲站", "sites": 1, "site": "a"}
                    for i in range(MERGED_SHOWN + 5)],
            "check": [{**_row("button|匯出PDF", 421, what="button", target="", text="匯出PDF", cls="check"),
                       "site_name": "甲站", "sites": 1, "site": "a"}]}
    html = miss_tabs(rows, {"fix": MERGED_SHOWN + 7, "check": 1, "school": 0}, {"fix": 9, "check": 421, "school": 0},
                     group="school", empty={"school": "沒有學校那邊的"}, school=True)
    tree = LexborHTMLParser(html)
    box = tree.css_first("section.ck-tabs")
    assert box.attributes["data-tabs"] == "school" and box.css_first(".ck-tabbar").attributes["role"] == "tablist"
    tabs = box.css("[role=tab]")
    assert [(t.attributes["data-tab"], t.attributes["aria-selected"]) for t in tabs] == [
        ("fix", "true"), ("check", "false"), ("school", "false")]  # 爬蟲能補 first, shown
    assert [t.css_first("b").text() for t in tabs] == [f"{MERGED_SHOWN + 7:,}", "1", "0"]
    panes = box.css("[role=tabpanel]")
    assert [p.attributes["id"] for p in panes] == [t.attributes["aria-controls"] for t in tabs]
    assert ["hidden" in p.attributes for p in panes] == [False, True, True]
    assert len(panes[0].css("a.prob")) == MERGED_SHOWN and "還有 7 條沒列出" in panes[0].text()
    button = panes[1].css_first("a.prob")
    assert "按鈕 · 甲站 · 帳本沒有" in button.text() and "出現在 421 頁" in button.text()
    assert "沒有學校那邊的" in panes[2].text()
    # the page's script switches them without a reload and keeps the one picked when the sweep draws the list again
    assert "[data-tabs] [role=tab]" in SITES_JS and "MutationObserver" in SITES_JS and "ArrowRight" in SITES_JS



# ---------------------------------------------------------------- kept in ops.db (owner 2026-11-22: 「改」)

FIVE_AM = datetime(2026, 11, 22, 5, 0, tzinfo=pagecheck.TAIPEI).timestamp()  # night 11-21 (nightly.night_of)


def kept_sweep(made, lib, ops: Path, **kw) -> sweep.Sweep:
    """A sweep kept in ops (no watcher thread: the tests call tick), its first sweep run to its end."""
    kw.setdefault("watch_seconds", 0)
    sw = sweep.Sweep(made.archive, lib, ops_db=ops, **kw)
    if sw.listing is None:
        sw.ensure()
        assert sw.wait(30)
    return sw


def _same(f: sweep.Found) -> tuple:
    return f.state, f.fix, f.check, f.next, f.problems, f.more


def _whole(made, url: str) -> tuple:
    """What a whole check of the page (the opened page's) leaves for the lists."""
    return _same(sweep.compact(url, pagecheck.check(made.archive, url).summary))


def test_the_results_are_on_the_pages_at_once_after_a_restart_and_nothing_is_checked_again(made, lib, tmp_path):
    ops = tmp_path / "ops" / "ops.db"
    first = kept_sweep(made, lib, ops)
    before, totals = {u: _same(f) for u, f in first.found.items()}, first.overview()["totals"]
    first.close()
    seen: list[str] = []

    def checker(archive, url, **kw):
        seen.append(url)
        return pagecheck.check_verdict(archive, url, **kw)

    again = sweep.Sweep(made.archive, lib, ops_db=ops, checker=checker, watch_seconds=0)
    progress = again.progress()
    assert progress["ready"] and progress["phase"] == "done" and progress["at"]  # no 正在讀帳本: drawn at once
    assert not progress["stale"] and not progress["running"]
    assert {u: _same(f) for u, f in again.found.items()} == before and again.overview()["totals"] == totals
    assert again.listing.names[NEWS1] == "第一則消息" and again.site_view(ACA)["sum"]["gap"] == 2
    again.ensure()  # the ledger is the one they were checked over: nothing to do
    assert again.thread is None and again.gen == first.gen and seen == []
    html = school_section(again.overview())
    assert "正在讀帳本" not in html and f"{progress['at']} 核查完" in html
    again.close()


def test_a_page_whose_stored_copy_changed_is_read_again_and_the_others_only_looked_up(made, lib, tmp_path,
                                                                                      monkeypatch):
    ops = tmp_path / "ops.db"
    kept_sweep(made, lib, ops).close()
    offset, = _records(made.archive / WARC, [(NEWS2, "200", [("Content-Type", "text/html; charset=utf-8")],
                                              _page(MENU, '<a href="Thin.aspx">很薄的頁</a>'))])
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    conn.execute("UPDATE url SET warc_offset = ? WHERE url = ?", (offset, NEWS2))  # the crawl fetched it again
    conn.close()
    read: list[str] = []
    real = pagecheck.read_record
    monkeypatch.setattr(pagecheck, "read_record", lambda archive, row: (read.append(row["url"]), real(archive, row))[1])
    sw = kept_sweep(made, lib, ops, resweep_seconds=0)
    assert sw.found[NEWS2].fix[0] == 1  # the kept result shows until the page is checked again
    sw.ensure()
    assert sw.wait(30) and sw.phase == "done"
    assert read == [NEWS2]  # one page read again; the six others looked up from what was kept
    assert sw.found[NEWS2].fix[0] == 0 and not any(p.target == MISSING for p in sw.found[NEWS2].problems)
    assert all(_same(f) == _whole(made, u) for u, f in sw.found.items())  # what a whole check says, page by page
    sw.close()


def test_an_address_the_ledger_now_has_changes_the_verdict_of_the_pages_linking_it(made, lib, tmp_path):
    ops = tmp_path / "ops.db"
    first = kept_sweep(made, lib, ops)
    assert first.found[NEWS1].fix[0] == 1 and first.site_view(ACA)["sum"]["gap"] == 2  # 漏了的辦法 is missing
    first.close()
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, MISSING)  # the crawl found it: neither page's own copy changed
    conn.close()
    sw = kept_sweep(made, lib, ops, resweep_seconds=0)
    sw.ensure()
    assert sw.wait(30)
    assert sw.found[NEWS1].fix[0] == 0 and sw.found[NEWS2].fix[0] == 0 and sw.site_view(ACA)["sum"]["gap"] == 0
    assert all(_same(f) == _whole(made, u) for u, f in sw.found.items())
    sw.close()
    third = sweep.Sweep(made.archive, lib, ops_db=ops, watch_seconds=0)  # and so it stays after a restart
    assert third.found[NEWS1].fix[0] == 0 and third.phase == "done"
    third.close()


def test_a_changed_ledger_waits_until_it_is_quiet_and_never_while_the_nightly_update_fetches(made, lib, tmp_path):
    ops = tmp_path / "ops.db"
    kept_sweep(made, lib, ops).close()
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, MISSING)
    conn.close()
    later = {"s": 0.0}
    fetching = {"now": True}
    sw = sweep.Sweep(made.archive, lib, ops_db=ops, watch_seconds=0, hold=lambda: fetching["now"],
                     wall=lambda: time.time() + later["s"])
    sw.tick()  # the crawler wrote a moment ago, and the nightly update is fetching
    assert sw.thread is None and sw.progress()["held"]
    fetching["now"] = False
    sw.tick()  # not fetching, but the ledger was written a moment ago: not quiet yet
    assert sw.thread is None and not sw.progress()["held"] and sw.progress()["stale"]
    assert "稍後重新核查" in school_section(sw.overview())  # the kept results, said to be of an older ledger
    later["s"] = sweep.QUIET_SECONDS + 1
    sw.tick()
    assert sw.thread is not None and sw.wait(30) and sw.found[NEWS1].fix[0] == 0
    sw.close()


def test_one_record_a_night_of_the_schools_totals_and_the_entry_draws_them(made, lib, tmp_path):
    ops = tmp_path / "ops.db"
    clock = {"t": FIVE_AM}
    sw = kept_sweep(made, lib, ops, wall=lambda: clock["t"])
    totals = sw.overview()["totals"]
    rec = sw.store.night("2026-11-21")
    assert rec is not None and rec["run"] == sw.gen and rec["at"].startswith("2026-11-22T05:00")
    assert (rec["cls_fix"], rec["cls_check"], rec["cls_school"]) == tuple(totals["cls"][k] for k in sweep.CLASS_KEYS)
    assert (rec["pages_fix"], rec["menu"], rec["nofetch"], rec["pages"]) == (
        totals["cls_pages"]["fix"], totals["menu"], totals["nofetch"], totals["pages"])
    assert rec["cls_fix"] == 1 and rec["pages_fix"] == 2  # 漏了的辦法, once, on two pages
    clock["t"] += 86400  # the next night, the ledger untouched: the watcher keeps its record too
    sw.tick()
    assert sw.thread is not None and sw.store.night("2026-11-22")["cls_fix"] == 1
    hist = sw.overview()["history"]
    assert hist["enabled"] and len(hist["days"]) == sweep.NIGHTS_SHOWN and hist["days"][-1] == "2026-11-22"
    assert [n is not None for n in hist["nights"][-3:]] == [False, True, True]
    tree = LexborHTMLParser(school_section(sw.overview()))
    trend = tree.css_first(".ck-trend")
    assert "最近 14 晚" in trend.text() and [p.css_first(".ck-tr-h").text() for p in trend.css(".ck-tr")] == [
        "爬蟲能補1", "要確認1", "學校那邊0"]
    bars = trend.css(".ck-tr")[0].css(".ck-bars i")
    assert len(bars) == 14 and [b.attributes.get("class") for b in bars[-3:]] == ["none", None, "last"]
    assert "11-22 夜" == bars[-1].attributes["data-tip-h"] and "爬蟲能補 1 個，出現在 2 頁" in bars[-1].attributes["data-tip"]
    assert [r.css_first("td").text() for r in trend.css("tbody tr")] == ["11-22 夜", "11-21 夜"]
    sw.close()


def test_without_a_file_nothing_is_kept_and_no_trend_is_drawn(made, lib, tmp_path):
    sw = swept(made, lib)
    view = sw.overview()
    assert sw.kept is None and not view["history"]["enabled"] and sw.watcher is None
    assert "ck-trend" not in school_section(view)
    sw.close()
    assert not any(tmp_path.rglob("ops.db"))


def test_the_panel_keeps_the_sweep_in_its_own_file_and_a_restarted_panel_draws_it_at_once(made, lib, tmp_path,
                                                                                          tmp_path_factory):
    ops = tmp_path_factory.mktemp("ops") / "ops.db"  # its own volume: never under the library's root (history.Store)
    app = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive, ops_db=ops,
                         record=False)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        c.get("/check")
        assert app.state.sweep.wait(30) and app.state.sweep.kept is not None
    again = web.create_app(lib, tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive, ops_db=ops,
                           record=False)
    with TestClient(again, base_url="http://127.0.0.1:8878") as c:
        got = c.get("/api/check/sweep").json()
        assert got["ready"] and got["phase"] == "done" and not got["running"] and got["totals"]["cls"]["fix"] == 1
        assert again.state.sweep.thread is None
        assert "正在讀帳本" not in c.get("/check").text


def test_a_kept_result_and_listing_read_back_as_they_were(made, lib):
    sw = swept(made, lib)
    back = sweep.unpack_listing(sweep.pack_listing(sw.listing))
    assert back.pages == sw.listing.pages and back.sites == sw.listing.sites and back.names == sw.listing.names
    assert back.site_pages == sw.listing.site_pages and back.keys == sw.listing.keys and back.order == sw.listing.order
    for url, f in sw.found.items():
        assert sweep.unpack_found(url, sweep.pack_found(f), f.gen, f.at) == f
    sw.close()
