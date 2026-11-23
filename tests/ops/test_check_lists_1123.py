"""A site's lists on 網頁核查 (/check?lists=1&site=…, the check layer) and 品質提醒's lists on its page (the site layer),
in this machine's Chrome (port audit 11-23: check:site-list-counts-match, -chip-switch, -all-pages, site:quality-all-rows,
site:check-links-precise; owner: 「要精准跳转…需要帮我输入好筛选好」).

A link from a number lands on exactly the pages it counts, already filtered, and every one of them can be reached: the
server filters and parts the list (/check/site?site=&only=&q=&offset=, Sweep.site_view), a chip and typing ask again,
「再列 N 頁」 asks for the next part. 品質提醒's lists over SHOW come the same way (/site/quality, site_checks.api).

The pages are stand-ins served on a made-up host (the browser asks nothing outside it): the school's 網頁核查 as
check_sites draws it, the site page's #chk-site as site_section draws it from a real sweep of test_sweep's made-up ledger
(seven pages of c.nknu.edu.tw/aca), and /check/site answered as web.check_site answers once it passes offset= and adds
matched and offset (the wiring this port asks for); a part is LIMIT pages so seven pages take four parts."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl, quote

import pytest

from nknu.ops import design, site_checks, sweep
from nknu.ops.check_sites import CHECK_CSS, SITES_CSS, school_section, site_parts, site_section
from tests.ops.test_site_checks_1123 import BUILT, LONG, _long
from tests.ops.test_site_checks_1123 import SITE as QSITE
from tests.ops.test_sweep import ACA, build

pytestmark = pytest.mark.browser

HOST = "http://panel.test"
LIMIT = 2


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    sw = sweep.Sweep(build(tmp_path_factory.mktemp("lists")).archive, None)
    sw.ensure()
    assert sw.wait(30)
    yield sw
    sw.close()


@pytest.fixture
def chrome():
    pw = pytest.importorskip("playwright.sync_api", reason="没装 Playwright（pyproject 的 extra browser）")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001  no Chrome on this machine
            pytest.skip(f"这台机器打不开 Chrome：{str(exc)[:200]}")
        try:
            yield browser
        finally:
            browser.close()


def check_page(sw) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{CHECK_CSS}{SITES_CSS}</style></head><body><main>{school_section(sw.overview())}</main>"
            + design.layer("check") + "</body></html>")


def site_page(sw, params: dict) -> str:
    """The site page as check.js reads it: its name, and #chk-site (its first part, as /site draws it)."""
    view = sw.site_view(params.get("site", ""), q=params.get("q", ""), only=params.get("only", ""))
    return ("<!doctype html><html><head><meta charset='utf-8'></head><body><header id='now'><h1>教務處</h1></header>"
            + site_section(view, params.get("site", "")) + "</body></html>")


def check_site(sw, params: dict, *, wired: bool) -> dict:
    """/check/site's answer: wired, as web.check_site answers with offset= passed and matched, offset added; else as
    it answers today (offset not passed, so every ask is the first part)."""
    p = {"q": params.get("q", ""), "only": params.get("only", ""), "limit": LIMIT}
    if wired:
        view = sw.site_view(params.get("site", ""), offset=params.get("offset", ""), **p)
        return {**site_parts(view), "matched": view["matched"], "offset": view["offset"]}
    return site_parts(sw.site_view(params.get("site", ""), **p))


def open_page(browser, sw, address: str, *, wired: bool = True, quality=None):
    """address on HOST; quality: (db, html) for the site page with 品質提醒 (its rows from site_checks.api, or a
    refusal when db is None). Returns (page, asked, errors)."""
    page = browser.new_page()
    asked, errors = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def answer(route):
        url = route.request.url
        path, _, query = url[len(HOST):].partition("?")
        params = dict(parse_qsl(query))
        asked.append(url[len(HOST):])
        if path == "/check":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=check_page(sw))
        if path == "/site" and quality is not None:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=quality[1])
        if path == "/site":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=site_page(sw, params))
        if path == "/check/site":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(check_site(sw, params, wired=wired), ensure_ascii=False))
        if path == "/site/quality" and quality is not None and quality[0] is not None:
            status, body = site_checks.api(quality[0], params)
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
        return route.fulfill(status=404, body="")

    page.route(f"{HOST}/**", answer)
    page.goto(HOST + address)
    return page, asked, errors


SHOWN = "[...document.querySelectorAll('#chk-rows li')].filter(li=>!li.hidden&&li.offsetParent!==null)"


def names(page) -> list[str]:
    """The pages a reader sees (a row hidden on the page is not reached)."""
    return page.evaluate(f"{SHOWN}.map(li=>li.querySelector('.pn').textContent)")


def wait_rows(page, n: int) -> None:
    page.wait_for_function(f"{SHOWN}.length==={n}", timeout=5000)


def state(page) -> dict:
    return page.evaluate("""()=>{const n=document.querySelector('.pd-cut'),m=document.querySelector('.pd-more');
      return {note:n&&!n.hidden?n.textContent:'',more:m&&!m.hidden?m.textContent:'',
        pressed:[...document.querySelectorAll('#chk-chips .chip[aria-pressed=true]')].map(c=>c.dataset.only),
        chips:Object.fromEntries([...document.querySelectorAll('#chk-chips .chip')].map(c=>[c.dataset.only,
          c.querySelector('b').textContent])),search:location.search}}""")


def test_a_kinds_link_lands_on_exactly_its_pages_a_menu_page_to_look_at_too(made, chrome):
    """只漏選單 counts 2 here: the home page, and a thin page whose own state is 要查 (the old filter hid it)."""
    page, asked, errors = open_page(chrome, made, f"/check?lists=1&site={quote(ACA, safe='')}&only=menu")
    wait_rows(page, 2)
    page.wait_for_function("[...document.querySelectorAll('#chk-rows li .pill')].some(p=>p.textContent!=='只漏選單')")
    assert sorted(names(page)) == sorted(["/aca/thin.aspx", "首頁"])
    got = state(page)
    assert got["chips"]["menu"] == "2" and got["pressed"] == ["menu"] and got["note"] == "" and got["more"] == ""
    assert any(a.startswith("/check/site?") and "only=menu" in a for a in asked) and not errors


def test_every_page_is_reached_a_part_at_a_time_and_a_chip_or_words_ask_again(made, chrome):
    page, asked, errors = open_page(chrome, made, f"/check?lists=1&site={quote(ACA, safe='')}")
    whole = [r["name"] for r in made.site_view(ACA)["rows"]]
    page.wait_for_function("document.querySelector('.pd-more')&&!document.querySelector('.pd-more').hidden")
    assert names(page) == whole[:2]
    assert state(page)["note"] == "列出 2 頁，共 7 頁" and state(page)["more"] == "再列 2 頁"
    for n in (4, 6, 7):
        page.click(".pd-more")
        wait_rows(page, n)
    assert names(page) == whole  # in the server's order, none twice, none missing
    assert state(page)["note"] == "" and state(page)["more"] == ""
    assert [a for a in asked if "offset=" in a] == [f"/check/site?site={quote(ACA, safe='')}&offset={n}" for n in (2, 4, 6)]
    # a chip asks again: the server's pages of that kind, its number and the pages from one answer
    page.click("#chk-chips .chip[data-only='nofetch']")
    wait_rows(page, 1)
    assert names(page) == ["/aca/fail.aspx"] and state(page)["pressed"] == ["nofetch"]
    assert "only=nofetch" in state(page)["search"] and "lists=1" in state(page)["search"]
    page.click("#chk-chips .chip[data-only='check']")
    wait_rows(page, 2)
    assert state(page)["chips"]["check"] == "2" and state(page)["note"] == ""
    page.click("#chk-chips .chip[data-only='check']")  # pressed again: every page
    page.wait_for_function("document.querySelector('.pd-cut').textContent==='列出 2 頁，共 7 頁'")
    assert state(page)["pressed"] == [] and "only=" not in state(page)["search"]
    # words ask the server too (they reach pages the first part does not hold)
    page.fill("#chk-filter", "quiet")
    wait_rows(page, 1)
    assert names(page) == ["安靜的頁"] and "q=quiet" in state(page)["search"]
    assert not errors


def test_without_the_wiring_the_list_says_how_many_and_never_draws_a_page_twice(made, chrome):
    """/check/site as it answers today (no offset, no matched): the count still comes right, no 「再列」."""
    page, _asked, errors = open_page(chrome, made, f"/check?lists=1&site={quote(ACA, safe='')}", wired=False)
    page.wait_for_function("document.querySelector('.pd-cut')&&!document.querySelector('.pd-cut').hidden")
    assert state(page)["note"] == "列出 2 頁，共 7 頁：打字找其他的" and state(page)["more"] == ""
    assert len(names(page)) == 2 and not errors


def test_an_address_made_up_never_reaches_the_page(made, chrome):
    bad = quote("<img src=x onerror=alert(1)>", safe="")
    page, asked, errors = open_page(chrome, made, f"/check?lists=1&site={quote(ACA, safe='')}&only={bad}&tab=%27%5D")
    page.wait_for_function("document.querySelector('.pd-cut')!==null")
    wait_rows(page, 2)
    assert "<img" not in page.content() and state(page)["pressed"] == [] and not errors
    assert not any("only=" in a for a in asked if a.startswith("/check/site"))  # an unknown kind is no filter


# ---------------------------------------------------------------- 品質提醒's lists on a site's page

def quality_page(tmp_path) -> tuple[Path, str]:
    db, conn = _long(tmp_path)
    card = site_checks.card(QSITE, site_checks.load(tmp_path, BUILT), conn)
    return db, ("<!doctype html><html><head><meta charset='utf-8'></head><body><main>" + card + "</main>"
                + design.layer("site") + "</body></html>")


def test_a_reminders_list_is_read_to_its_end_a_part_at_a_time(made, chrome, tmp_path):
    db, html = quality_page(tmp_path)
    page, asked, errors = open_page(chrome, made, f"/site?site={quote(QSITE, safe='')}", quality=(db, html))
    pane = ".sq-p[data-k='no_date']"
    rows = f"{pane} tbody tr"
    assert page.eval_on_selector_all(rows, "xs=>xs.length") == 300
    page.click(f"{pane} .sq-more-b")
    page.wait_for_function(f"document.querySelectorAll(\"{rows}\").length===600")
    assert page.text_content(f"{pane} .sq-more") == "共 650 份，這裡列前 600 份"
    assert page.text_content(f"{pane} .sq-more-b") == "再列 50 份"
    page.click(f"{pane} .sq-more-b")
    page.wait_for_function(f"document.querySelectorAll(\"{rows}\").length==={LONG}")
    assert page.query_selector(f"{pane} .sq-more-b") is None and page.text_content(f"{pane} .sq-more") == "共 650 份"
    refs = page.eval_on_selector_all(f"{rows} a.sq-t", "xs=>xs.map(x=>new URL(x.href).searchParams.get('ref'))")
    assert refs == [f"n{i:03d}" for i in range(LONG)]  # every document once, in the build's order
    cells = page.eval_on_selector_all(f"{rows}:last-child td", "xs=>xs.length")
    assert cells == page.eval_on_selector_all(f"{rows}:first-child td", "xs=>xs.length")  # the same columns
    assert [a for a in asked if a.startswith("/site/quality")] == [
        f"/site/quality?site={quote(QSITE, safe='')}&k=no_date&g=0&at={n}" for n in (300, 600)]
    assert not errors


def test_a_part_that_cannot_be_read_says_so_and_keeps_the_rows(made, chrome, tmp_path):
    _db, html = quality_page(tmp_path)
    page, _asked, errors = open_page(chrome, made, f"/site?site={quote(QSITE, safe='')}", quality=(None, html))
    page.click(".sq-p[data-k='no_date'] .sq-more-b")
    page.wait_for_function("document.querySelector(\".sq-p[data-k='no_date'] .sq-more-b\").textContent==='讀不到，再試一次'")
    assert page.eval_on_selector_all(".sq-p[data-k='no_date'] tbody tr", "xs=>xs.length") == 300 and not errors
