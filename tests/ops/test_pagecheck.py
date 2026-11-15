"""網頁核查 (design docs/superpowers/specs/2026-11-16-page-check-design.md): one archived page with its regions as the
library reads them and every link, image and button coloured by what the crawler's ledger says of it. A made-up ledger
(the crawler's own schema, nknu.archive.frontier, in a temporary folder) and a made-up WARC hold one list page of c.nknu
with a menu, a content area, an attachment area, links in every bucket, images and buttons."""

from __future__ import annotations

import io
import json
import sqlite3
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from selectolax.lexbor import LexborHTMLParser
from starlette.testclient import TestClient
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from nknu.archive import postback, progress, scope
from nknu.archive.frontier import Frontier
from nknu.ops import pagecheck, web
from nknu.ops.check_page import CHECK_CSS, check_body
from tests.ops.library import models
from tests.retrieval.test_search import TODAY

SITE = "https://c.nknu.edu.tw/aca/"
PAGE = SITE + "News/List.aspx"
WARC = "warc/2026/11/06/ip_140.127.0.1-1.warc.gz"
EVIL = "evil.example"


def _png() -> bytes:
    """A real one-pixel PNG."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


HTML = f"""<!doctype html><html><head><meta charset="utf-8"><title>最新消息</title>
<base href="{SITE}News/">
<meta http-equiv="refresh" content="0;url=https://{EVIL}/">
<link rel="stylesheet" href="https://{EVIL}/site.css">
<style>body{{background:url(https://{EVIL}/bg.png)}} @import url(https://{EVIL}/x.css); .m{{color:red}}</style>
<script>document.write('<img src=https://{EVIL}/s.png>')</script></head>
<body onload="steal()">
<div id="menu"><a href="Ok.aspx" id="m-ok">選單抓到</a> <a href="javascript:void(0)" id="m-js">展開</a></div>
<div id="MainContent">
 <p><a href="Ok.aspx" id="c-ok">抓到</a> <a href="Thin.aspx" id="c-thin">太薄</a>
 <a href="https://www.facebook.com/oooo" id="c-social">社群</a> <a href="Gone.aspx" id="c-gone">已刪除</a>
 <a href="Later.aspx" id="c-later">下一輪</a> <a href="Fail.aspx" id="c-fail">失敗</a>
 <a href="Moved.aspx" id="c-moved">轉址</a> <a href="Missing.aspx" id="c-missing">沒有</a>
 <a href="Login.aspx" id="c-login">登入</a> <a href="mailto:" id="c-mail">信</a> <a href="#top" id="c-top">回上面</a>
 <a href="http://c.nknu.edu.tw/aca/News/Other.aspx" id="c-scheme">另一種協定</a></p>
 <img src="pic.png" alt="照片" id="i-pic"> <img src="logo.gif" alt="標誌" id="i-logo">
 <img src="https://c.nknu.edu.tw/aca/lost.jpg" onerror="x()" id="i-lost" width="40" height="30">
 <a href="javascript:__doPostBack('ctl00$MasterGrid','Page$2')" id="b-next">下一頁</a>
 <a href="javascript:__doPostBack('ctl00$Sort','Sort$Title')" id="b-sort">排序</a>
 <form action="https://{EVIL}/post" method="post"><input type="submit" formaction="https://{EVIL}/f" value="送出"
  onclick="go()"></form>
 <iframe src="https://{EVIL}/frame"></iframe>
 <div onclick="evil()" style="background:url(https://{EVIL}/a.png);color:blue" id="styled">樣式</div>
 <svg><a href="javascript:alert(1)"><text>圖</text></a></svg>
</div>
<div id="lbl_FileLink"><a href="Files/rule.pdf" id="f-pdf">辦法.pdf</a></div>
<footer><a href="Ok.aspx" id="foot">頁尾</a></footer>
</body></html>"""

PDF = b"%PDF-1.4\n%made up\n"


def _records(path: Path, items: list[tuple[str, str, list[tuple[str, str]], bytes]]) -> list[int]:
    """Response records, one gzip member each as the crawler writes them: the offset of each."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    with open(path, "ab") as fh:
        w = WARCWriter(fh, gzip=True)
        for url, status, headers, body in items:
            out.append(fh.tell())
            resp = StatusAndHeaders(status, [*headers, ("Content-Length", str(len(body)))], protocol="HTTP/1.1")
            w.write_record(w.create_warc_record(url, "response", payload=io.BytesIO(body), http_headers=resp))
    return out


def _url(conn: sqlite3.Connection, url: str, *, kind: str = "page", state: str = "done", status: int | None = 200,
         reason: str | None = None, final_url: str | None = None, ctype: str = "text/html; charset=utf-8",
         warc: str | None = None, offset: int | None = None, sniff: str | None = "html", flags: str | None = None,
         nbytes: int | None = 100) -> int:
    parts = scope.urlsplit(url)
    cur = conn.execute(
        "INSERT INTO url(url, path, host, site, tier, kind, kind0, depth, found_via, state, reason, status, final_url,"
        " ctype, warc, warc_offset, sniff, flags, bytes, fetched_at, owner_site)"
        " VALUES (?, ?, ?, ?, 1, ?, ?, 1, 'link', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-11-06T10:00:00+08:00', ?)",
        (url, parts.path, parts.hostname, scope.site_key(url), kind, kind, state, reason, status, final_url, ctype,
         warc, offset, sniff, flags, nbytes, scope.site_key(url)))
    return cur.lastrowid


def build(root: Path) -> SimpleNamespace:
    """The made-up archive: frontier.db (the crawler's own schema) and one WARC holding the page, a picture, a PDF."""
    archive = root / "archive"
    c = scope.canonical
    page_url, pic, pdf = c(PAGE), c(SITE + "News/pic.png"), c(SITE + "News/Files/rule.pdf")
    offsets = _records(archive / WARC, [
        (page_url, "200", [("Content-Type", "text/html; charset=utf-8")], HTML.encode("utf-8")),
        (pic, "200", [("Content-Type", "image/png")], _png()),
        (pdf, "200", [("Content-Type", "application/pdf")], PDF),
        (c(SITE + "News/Gone.aspx"), "404", [("Content-Type", "text/html")], b"<html><body>404</body></html>"),
    ])
    front = Frontier(archive / pagecheck.LEDGER)
    conn = front.conn
    ids = {}
    ids["page"] = _url(conn, page_url, warc=WARC, offset=offsets[0])
    ids["ok"] = _url(conn, c(SITE + "News/Ok.aspx"))
    _url(conn, c(SITE + "News/Thin.aspx"), flags="thin")
    _url(conn, c(SITE + "News/Gone.aspx"), status=404, warc=WARC, offset=offsets[3])
    _url(conn, c(SITE + "News/Later.aspx"), state="later", status=None, reason="round2:other_site")
    _url(conn, c(SITE + "News/Fail.aspx"), state="failed", status=None, reason="server_down")
    _url(conn, c(SITE + "News/Moved.aspx"), status=302, final_url=c(SITE + "News/Ok.aspx"))
    _url(conn, "https://c.nknu.edu.tw/aca/news/other.aspx")  # only https: the http link finds it
    ids["pic"] = _url(conn, pic, kind="image", ctype="image/png", sniff="png", warc=WARC, offset=offsets[1])
    ids["pdf"] = _url(conn, pdf, kind="file", ctype="application/pdf", sniff="pdf", warc=WARC, offset=offsets[2])
    ids["pdf_page"] = _url(conn, c(SITE + "News/Report.aspx"), kind="page", ctype="application/pdf", sniff="pdf",
                           warc=WARC, offset=offsets[2])
    links = [("Ok.aspx", "queued"), ("Thin.aspx", "queued"), ("Gone.aspx", "queued"), ("Later.aspx", "round2:other_site"),
             ("Fail.aspx", "queued"), ("Moved.aspx", "queued"), ("Login.aspx", "out:login")]
    for href, verdict in links:
        conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '', ?)",
                     (ids["page"], c(href, SITE + "News/"), verdict))
    conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '', 'out:social')",
                 (ids["page"], c("https://www.facebook.com/oooo")))
    conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '', 'queued')",
                 (ids["page"], c(SITE + "News/Older.aspx")))  # an earlier fetch's link, not on this stored page
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why, res_id) VALUES (?, ?, 'get', 'img', "
                 "'must', NULL, ?)", (ids["page"], pic, ids["pic"]))
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?, ?, 'get', 'img', 'no', "
                 "'decor_name')", (ids["page"], c(SITE + "News/logo.gif")))
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?, ?, 'unhandled', 'click', "
                 "'no', 'unhandled:never')", (ids["page"], postback.button_key("ctl00$Sort", "Sort$Title")))
    conn.execute(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset,"
        " target, argument, row_key, state, status, ctype, sniff, warc, warc_offset, done)"
        " VALUES (?, 1, 2, 'pager', 'q', 'c.nknu.edu.tw', 'c.nknu.edu.tw/aca', 'cnknu', ?, ?, 'ctl00$MasterGrid',"
        " 'Page$2', 'p2', 'done', 200, 'text/html', 'html', ?, ?, 1793000000)",
        (ids["page"], WARC, offsets[0], WARC, offsets[0]))
    conn.close()
    return SimpleNamespace(archive=archive, page=page_url, ids=ids, pic=pic, pdf=pdf)


@pytest.fixture
def made(tmp_path) -> SimpleNamespace:
    return build(tmp_path)


def _by_id(checked: pagecheck.Check) -> dict[str, dict]:
    """The items of a check by the made-up page's element ids (read back from the safe page)."""
    tree = LexborHTMLParser(checked.html)
    items = {i["n"]: i for i in checked.summary["items"]}
    return {n.attributes["id"]: items[int(n.attributes["data-nk"])] for n in tree.css("[data-nk][id]")}


# ---------------------------------------------------------------- the buckets (design section 二)

def test_every_link_gets_the_bucket_the_ledger_gives(made):
    got = _by_id(pagecheck.check(made.archive, PAGE))
    want = {"c-ok": "got", "c-thin": "look", "c-social": "rule", "c-gone": "gone", "c-later": "next",
            "c-fail": "failed", "c-moved": "got", "c-missing": "missing", "c-login": "login", "c-mail": "notlink",
            "c-top": "notlink", "c-scheme": "got", "m-ok": "got", "m-js": "notlink", "foot": "got"}
    assert {k: got[k]["bucket"] for k in want} == want


def test_a_redirect_takes_its_landings_colour_and_says_where_it_went(made):
    moved = _by_id(pagecheck.check(made.archive, PAGE))["c-moved"]
    assert moved["landing"].endswith("/news/ok.aspx") and "轉到" in moved["tip"]


def test_images_buttons_and_the_unchecked_page(made):
    checked = pagecheck.check(made.archive, PAGE)
    got = _by_id(checked)
    assert got["i-pic"]["bucket"] == "got" and got["i-logo"]["bucket"] == "rule" and got["i-lost"]["bucket"] == "missing"
    assert got["b-next"]["what"] == "button" and got["b-next"]["bucket"] == "got" and got["b-next"]["kind"] == "pager"
    assert got["b-sort"]["bucket"] == "rule"
    # the page itself has no page type verdict (no browser check in round one): 抓到了, not 待判定
    assert checked.summary["page"]["result"] == "unchecked" and checked.summary["page"]["bucket"] == "got"
    counts = checked.summary["counts"]
    # the styled div's background picture is a picture the crawler reads too (links._images): not in this ledger
    assert got["styled"]["what"] == "image" and got["styled"]["bucket"] == "missing"
    assert counts["button"] == {"got": 1, "rule": 1} and counts["image"] == {"got": 1, "rule": 1, "missing": 2}


def test_the_problems_list_the_content_area_first_and_counts_the_next_round(made):
    summary = pagecheck.check(made.archive, PAGE).summary
    problems = summary["problems"]
    assert {p["bucket"] for p in problems} == {"missing", "failed", "look"}  # the next round's are counted, not listed
    keys = [(pagecheck.ZONE_ORDER.index(p["zone"]), pagecheck.PROBLEMS.index(p["bucket"])) for p in problems]
    assert keys == sorted(keys) and problems[0]["zone"] == "in" and problems[0]["bucket"] == "missing"
    assert {p["who"] for p in problems} == {"fix", "check"}
    verdict = summary["verdict"]
    assert verdict["next"] == 1 and verdict["check"] == 1 and verdict["fix"]["in"] >= 2


def test_the_bucket_rules_start_from_outcome_with_the_three_changes():
    assert pagecheck.bucket_of("unchecked") == "got"
    assert pagecheck.bucket_of("thin") == "look" and pagecheck.bucket_of("decode") == "look"
    assert pagecheck.bucket_of("rule", "album:drive") == "album" and pagecheck.bucket_of("rule", "decor:repeat") == "rule"
    assert pagecheck.bucket_of("held") == "rule" and pagecheck.bucket_of("later") == "next"
    assert pagecheck.bucket_of("never-seen-code") == "review"
    assert pagecheck.verdict_bucket("error_page") == "gone" and pagecheck.verdict_bucket("dup") == "missing"
    assert pagecheck.res_bucket("no", "album") == "album" and pagecheck.res_bucket("no", "captcha") == "login"
    assert pagecheck.res_bucket("must", "unhandled:unknown") == "review" and pagecheck.res_bucket("must", None) is None


# ---------------------------------------------------------------- the regions

def test_the_content_area_is_framed_and_the_menu_washed(made):
    checked = pagecheck.check(made.archive, PAGE)
    tree = LexborHTMLParser(checked.html)
    main = tree.css_first("#MainContent")
    assert "nk-in" in main.attributes["class"] and main.attributes["data-nk-label"] == "正文區（進庫）"
    assert "nk-out" in tree.css_first("#menu").attributes["class"]
    assert "nk-out" in tree.css_first("footer").attributes["class"]
    got = _by_id(checked)
    assert got["c-ok"]["zone"] == "in" and got["m-ok"]["zone"] == "out" and got["f-pdf"]["zone"] == "files"
    assert checked.summary["regions"]["how"] == "area"


# ---------------------------------------------------------------- the page made safe (design section 四)

def test_nothing_of_the_stored_page_can_run_or_reach_a_server(made):
    page = pagecheck.check(made.archive, PAGE).html.lower()
    for bad in ("<script", "onload", "onclick", "onerror", "<iframe", "<object", "<embed", "<base", "<link",
                "http-equiv", "javascript:", "@import", "formaction", " action=", " href=", " srcset", "<svg"):
        assert bad not in page, bad
    tree = LexborHTMLParser(page)
    for node in tree.root.traverse():
        for name, value in node.attributes.items():
            assert not name.startswith("on"), (node.tag, name)
            if not name.startswith("data-"):  # a target may be named as data and in words, never as a load
                assert EVIL not in (value or "") and "://" not in (value or ""), (node.tag, name, value)
    assert EVIL not in "".join(n.text() or "" for n in tree.css("style"))
    srcs = [n.attributes.get("src") for n in tree.css("[src]")]
    assert srcs and all(s.startswith("/check/res?u=") for s in srcs)
    assert tree.css_first("#i-lost").tag == "span" and "nk-noimg" in tree.css_first("#i-lost").attributes["class"]
    styled = tree.css_first("#styled").attributes
    assert "url(" not in styled.get("style", "") and "blue" in styled.get("style", "")


def test_a_link_keeps_its_target_only_as_data_for_the_panels_script(made):
    tree = LexborHTMLParser(pagecheck.check(made.archive, PAGE).html)
    a = tree.css_first("#c-ok")
    assert "href" not in a.attributes and a.attributes["data-nk-href"].endswith("/news/ok.aspx")
    assert a.attributes["data-tip-h"] == "抓到了" and "在正文區" in a.attributes["data-tip"]


def test_a_style_sheet_keeps_its_rules_and_loses_what_it_loads():
    assert pagecheck.clean_css("a{background:url(x.png);color:red}@import 'y.css';") == "a{background:none;color:red}"
    assert pagecheck.clean_css("a{width:expression(alert(1))}") == ""
    assert pagecheck.clean_css(r"a{background:\75rl(x)}") == ""


# ---------------------------------------------------------------- pages that are not there

def test_a_page_the_ledger_does_not_hold(made):
    got = pagecheck.check(made.archive, SITE + "Nowhere.aspx")
    assert got.summary["state"] == "not_in_ledger" and got.html == ""


def test_a_page_never_fetched(made):
    got = pagecheck.check(made.archive, SITE + "News/Later.aspx")
    assert got.summary["state"] == "not_fetched" and got.summary["page"]["result"] == "later"


def test_a_page_address_that_came_back_a_pdf(made):
    got = pagecheck.check(made.archive, SITE + "News/Report.aspx")
    assert got.summary["state"] == "not_html" and "pdf" in got.summary["page"]["stored_type"]


def test_no_archive_mounted(tmp_path):
    assert pagecheck.check(None, PAGE).summary["state"] == "no_archive"
    assert pagecheck.check(tmp_path, PAGE).summary["state"] == "no_archive"


def test_the_caveats_that_hold_for_the_page(made):
    summary = pagecheck.check(made.archive, PAGE).summary
    assert "pressed" in summary["caveats"] and "older_links" in summary["caveats"] and summary["older_links"] == 1


# ---------------------------------------------------------------- the ledger read-only

def test_a_read_only_folder_without_wal_files_is_opened_immutable(made, monkeypatch):
    def refuse(db):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(progress, "connect", refuse)
    conn = pagecheck.open_ledger(made.archive)
    try:
        assert conn.execute("SELECT result FROM url_result WHERE id = ?", (made.ids["page"],)).fetchone()[0]
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM url")
    finally:
        conn.close()
    assert pagecheck.check(made.archive, PAGE).summary["state"] == "ok"


def test_the_ledger_is_never_written(made):
    before = (made.archive / pagecheck.LEDGER).read_bytes()
    pagecheck.check(made.archive, PAGE)
    assert (made.archive / pagecheck.LEDGER).read_bytes() == before


def test_an_archived_image_is_served_by_its_bytes_only(made):
    body, kind = pagecheck.archived_image(made.archive, made.pic)
    assert kind == "image/png" and body.startswith(b"\x89PNG")
    assert pagecheck.archived_image(made.archive, made.pdf) is None  # a PDF is no image
    assert pagecheck.archived_image(made.archive, SITE + "nothing.png") is None


# ---------------------------------------------------------------- the routes

@pytest.fixture
def client(db, tmp_path, made):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, archive=made.archive)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        yield c


def test_the_panel_frames_the_safe_page_and_lists_the_problems(client):
    got = client.get("/check", params={"url": PAGE})
    assert got.status_code == 200
    text = got.text
    assert "sandbox=\"allow-same-origin\"" in text and "/check/raw?url=" in text and "開真實網頁" in text
    assert "nknu_live" in text and "帳本沒有" in text and "網頁核查" in text


def test_the_raw_page_is_served_under_a_policy_that_loads_nothing_but_our_own_images(client):
    got = client.get("/check/raw", params={"url": PAGE})
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/html")
    csp = got.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "img-src 'self' data:" in csp and "script-src" not in csp
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in got.text.lower()


def test_res_serves_an_image_and_nothing_else(client, made):
    got = client.get("/check/res", params={"u": made.pic})
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert client.get("/check/res", params={"u": made.pdf}).status_code == 404
    assert client.get("/check/res", params={"u": SITE + "none.png"}).status_code == 404


def test_the_api_gives_the_summary(client):
    got = client.get("/api/check", params={"url": PAGE}).json()
    assert got["state"] == "ok" and got["counts"]["link"]["missing"] == 2  # Missing.aspx and the frame
    assert json.loads(json.dumps(got)) == got


def test_a_page_not_there_says_so_plainly(client):
    for url in (SITE + "Nowhere.aspx", SITE + "News/Report.aspx", SITE + "News/Later.aspx"):
        got = client.get("/check", params={"url": url})
        assert got.status_code == 200 and "<iframe" not in got.text, url


def test_the_check_routes_are_local_only_and_get_only(client):
    for path in ("/check", "/check/raw", "/check/res", "/api/check"):
        assert client.get(path, params={"url": PAGE}, headers={"host": "evil.example:8878"}).status_code == 403
        assert client.post(path).status_code == 405


def test_without_an_archive_the_panel_says_it_is_not_mounted(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        assert "沒有掛上爬蟲存檔" in c.get("/check", params={"url": PAGE}).text


def test_the_panel_uses_only_the_good_and_bad_colours_and_greys():
    """Blue names 公告 and 一般網頁, violet amounts (the owner 11-15): neither is a page check's colour."""
    for source in (CHECK_CSS, pagecheck.MARK_CSS):
        assert "--acc" not in source and "#3578b8" not in source and "#3d8fd6" not in source
    assert callable(check_body)


# ---------------------------------------------------------------- the fixes of the review of 11-16

MORE = SITE + "More.aspx"
MORE_HTML = f"""<!doctype html><html><head><meta charset="utf-8"><title>更多</title>
<style>.h{{background-image:image-set("https://{EVIL}/i.png" 1x)}} .k{{background:-webkit-image-set("https://{EVIL}/j.png" 1x)}}
</style></head><body><div id="MainContent">
 <a href="Att1.pdf" id="att-page">附件一</a> <a href="Att2.pdf" id="att-home">附件二</a>
 <a href="https://goo.gl/oooo" id="short-out">短網址</a> <a href="Loop1.aspx" id="loop">繞圈</a>
 <a href="uploadfile/album/1/001.jpg" id="album-photo">相片</a>
 <a href="http://140.127.40.135/WebCallInfo.aspx?ext=1" id="internal">分機</a>
 <a href="https://drive.google.com/file/d/1OooOooOooOooOooOooOooOooOooOooOo/view" id="drive-video">影片</a>
 <img src="https://drive.google.com/thumbnail?id=1OooOooOooOooOooOooOooOooOooOooOo" id="drive-thumb">
 <a href="javascript:window.open('Pop.aspx')" id="js-open">開視窗</a>
 <div onclick="location.href='Click.aspx'" id="on-click">點這裡</div>
 <span data-href="Data.aspx" id="data-href">資料</span>
 <a href="Dup.aspx" id="dup">漏記</a>
 <a href="Big.aspx" id="lang">一頁</a>
 <a href="Big.aspx"><img src="banner.png" id="linked-pic" alt="橫幅"></a>
 <embed src="Emb.pdf" id="emb">
 <form name="querySelectorAll"><img name="addEventListener" src="named.png" id="named"></form>
</div></body></html>"""
PAGE2_HTML = """<!doctype html><html><body><div id="MainContent"><p id="second">第二頁的內容</p>
<a href="Ok.aspx" id="p2-ok">抓到</a></div></body></html>"""
FRAMESET = """<!doctype html><html><head><title>框架</title></head><frameset cols="20%,80%">
<frame src="Left.aspx"><frame src="Ok.aspx"></frameset></html>"""
BIG5 = "<html><body><div id='MainContent'><p>繁體中文的頁</p></div></body></html>"


@pytest.fixture
def more(made) -> SimpleNamespace:
    """The made-up archive with a second page of every case the review of 11-16 found drawn wrong."""
    c = scope.canonical
    warc2 = "warc/2026/11/06/ip_140.127.0.1-2.warc.gz"
    offsets = _records(made.archive / warc2, [
        (c(MORE), "200", [("Content-Type", "text/html; charset=utf-8")], MORE_HTML.encode("utf-8")),
        (c(MORE), "200", [("Content-Type", "text/html; charset=utf-8")], PAGE2_HTML.encode("utf-8")),
        (c(SITE + "Frames.aspx"), "200", [("Content-Type", "text/html; charset=utf-8")], FRAMESET.encode("utf-8")),
        (c(SITE + "Big5.aspx"), "200", [("Content-Type", "text/html; charset=utf-8")], BIG5.encode("big5")),
    ])
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    conn.row_factory = sqlite3.Row
    ids = {}
    ids["more"] = _url(conn, c(MORE), warc=warc2, offset=offsets[0])
    _url(conn, c(SITE + "Frames.aspx"), warc=warc2, offset=offsets[2])
    _url(conn, c(SITE + "Big5.aspx"), warc=warc2, offset=offsets[3])
    _url(conn, c(SITE + "Broken.aspx"), warc=warc2, offset=offsets[3] + 7)  # an offset inside a record
    _url(conn, c(SITE + "Index.aspx"))
    _url(conn, "https://w3.nknu.edu.tw/")
    for name, final in (("Att1.pdf", SITE + "Index.aspx"), ("Att2.pdf", "https://w3.nknu.edu.tw/")):
        rid = _url(conn, c(SITE + name), kind="file", status=302, final_url=c(final))
        conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, res_id) VALUES (?, ?, 'get', 'file', "
                     "'must', ?)", (ids["more"], c(SITE + name), rid))
    short = _url(conn, "https://goo.gl/oooo", kind="shortlink", status=301, final_url="https://www.facebook.com/oooo")
    conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '[shortlink]', 'out:social')",
                 (short, c("https://www.facebook.com/oooo")))
    _url(conn, c(SITE + "Loop1.aspx"), status=302, final_url=c(SITE + "Loop2.aspx"))
    _url(conn, c(SITE + "Loop2.aspx"), status=302, final_url=c(SITE + "Loop1.aspx"))
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?, ?, 'link', 'img', 'no', "
                 "'album')", (ids["more"], c(SITE + "uploadfile/album/1/001.jpg")))
    meta = _url(conn, "https://www.googleapis.com/drive/v3/files/1OooOooOooOooOooOooOooOooOooOooOo", kind="drive", ctype="application/json")
    conn.execute("INSERT INTO drive_item(url_id, file_id, name, mime) VALUES (?, '1OooOooOooOooOooOooOooOooOooOooOo', 'a.mp4', 'video/mp4')",
                 (meta,))
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why, res_id) VALUES (?, 'drive:1OooOooOooOooOooOooOooOooOooOooOo', "
                 "'get', 'drive', 'no', 'media:drive_video', ?)", (ids["more"], meta))
    _url(conn, c(SITE + "Pop.aspx"), state="failed", status=None, reason="server_down")
    _url(conn, c(SITE + "Click.aspx"))
    conn.execute("INSERT INTO link(src, dst, text, verdict) VALUES (?, ?, '', 'queued')", (ids["more"], c(SITE + "Dup.aspx")))
    for dst, text in ((c(SITE + "Click.aspx"), "[onclick]"), (c(SITE + "Data.aspx"), "[data-href]"),
                      (c(SITE + "Pop.aspx"), "開視窗"), (c(SITE + "Old.aspx"), "舊的"), (c(SITE + "Re.aspx"), "[refresh]")):
        conn.execute("INSERT OR IGNORE INTO link(src, dst, text, verdict) VALUES (?, ?, ?, 'queued')",
                     (ids["more"], dst, text))
    act = conn.execute(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset,"
        " target, argument, row_key, row_text, state, status, ctype, sniff, warc, warc_offset, done)"
        " VALUES (?, 1, 2, 'pager', 'q', 'c.nknu.edu.tw', 'c.nknu.edu.tw/aca', 'cnknu', ?, ?, 'ctl00$Grid',"
        " 'Page$2', 'p2', '第 2 頁', 'done', 200, 'text/html', 'html', ?, ?, 1793000000)",
        (ids["more"], warc2, offsets[0], warc2, offsets[1]))
    ids["pager"] = act.lastrowid
    conn.close()
    return SimpleNamespace(archive=made.archive, ids=ids, made=made)


def test_attachments_that_land_on_a_page_or_the_school_home_are_not_got(more):
    got = _by_id(pagecheck.check(more.archive, MORE))
    assert got["att-page"]["bucket"] == "failed" and got["att-page"]["result"] == "not_file"
    assert got["att-home"]["bucket"] == "gone" and got["att-home"]["result"] == "gone_home"


def test_a_redirect_to_an_address_left_out_takes_its_link_verdict_and_a_loop_fails(more):
    got = _by_id(pagecheck.check(more.archive, MORE))
    assert got["short-out"]["bucket"] == "rule" and got["short-out"]["result"] == "out:social"
    assert got["loop"]["bucket"] == "failed" and got["loop"]["result"] == "server_error"


def test_links_kept_only_as_resources_or_never_written_are_not_missing(more):
    got = _by_id(pagecheck.check(more.archive, MORE))
    assert got["album-photo"]["bucket"] == "album"  # an <a> straight to a picture: page_res only
    assert got["internal"]["bucket"] == "rule" and got["internal"]["result"] == "out:internal_service"
    assert got["drive-video"]["bucket"] == "rule" and "只記檔名" in got["drive-video"]["tip"]
    assert got["drive-thumb"]["bucket"] == "rule"  # a Drive file's thumbnail stands on the file's own resource


def test_links_a_script_opens_are_links(more):
    got = _by_id(pagecheck.check(more.archive, MORE))
    assert got["js-open"]["bucket"] == "failed" and got["js-open"]["target"].endswith("/pop.aspx")
    assert got["on-click"]["what"] == "link" and got["on-click"]["bucket"] == "got"
    assert got["data-href"]["what"] == "link" and got["data-href"]["bucket"] == "missing"
    assert got["emb"]["what"] == "link"


def test_a_link_found_and_never_kept_says_so_in_words(more):
    dup = _by_id(pagecheck.check(more.archive, MORE))["dup"]
    assert dup["bucket"] == "missing" and dup["result"] == "not_discovered"
    assert "queued" not in dup["tip"] and "沒有留下紀錄" in dup["tip"]
    nowhere = _by_id(pagecheck.check(more.archive, MORE))["lang"]
    assert nowhere["bucket"] == "missing" and "帳本：沒有紀錄" in nowhere["tip"]


def test_older_links_count_only_what_this_stored_page_does_not_have(more):
    summary = pagecheck.check(more.archive, MORE).summary
    assert summary["older_links"] == 1  # Old.aspx; the onclick, data-href and [refresh] rows are this page's


def test_the_safe_page_keeps_no_name_no_image_set_and_draws_the_embed(more):
    page = pagecheck.check(more.archive, MORE).html
    tree = LexborHTMLParser(page)
    assert all("name" not in n.attributes for n in tree.root.traverse())
    assert "image-set" not in page.lower() and "nk-blocked(" in page  # its pictures' strings stay as void words
    emb = tree.css_first("#emb")
    assert emb.tag == "div" and "nk-box" in emb.attributes["class"]
    assert pagecheck.clean_css('a{background:image-set("https://x/y.png" 1x)}') == 'a{background:nk-blocked("https://x/y.png" 1x)}'


def test_a_press_answer_is_shown_from_its_own_record(more):
    got = pagecheck.check(more.archive, MORE + "#第2页")
    assert got.summary["state"] == "ok" and got.summary["press"]["found"] and "press_answer" in got.summary["caveats"]
    assert "第二頁的內容" in got.html and got.summary["raw_ref"].endswith("#第2页")
    missing = pagecheck.check(more.archive, MORE + "#按钮=沒有這一則")
    assert "press_unfound" in missing.summary["caveats"] and "附件一" in missing.html


def test_a_press_documents_id_checks_that_press(more, db):
    db.doc("dpress", "第 2 頁", "x")
    db.conn.execute("UPDATE docs SET url = ? WHERE doc_id = 'dpress'", (MORE + "#第2页",))
    got = pagecheck.check(more.archive, "dpress", library=db.conn)
    assert got.summary["from_doc"] == "dpress" and got.summary["doc"]["doc_id"] == "dpress"
    assert "第二頁的內容" in got.html


def test_a_button_falls_back_to_its_rows_press_never_to_another_rows(more):
    lid = more.ids["more"]
    writer = sqlite3.connect(more.archive / pagecheck.LEDGER, isolation_level=None)
    for key, epoch in (("kA", 1), ("kB", 2)):  # two rows whose ctlNN, target and argument are the same
        writer.execute(
            "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc,"
            " form_src_offset, target, argument, row_key, state, status, done) VALUES (?, ?, 1, 'detail', 'q',"
            " 'h', 's', 'p', 'old.warc.gz', 1, 'ctl00$ctl02$lnk', '', ?, 'done', 200, 1793000000)",
            (lid, epoch, key))
    writer.close()
    conn = pagecheck.open_ledger(more.archive)
    try:
        ledger = pagecheck.Ledger(conn)
        press = postback.Press("detail", "ctl00$ctl02$lnk", "", "kA", "甲", 1)
        assert ledger.action(lid, "now.warc.gz", 5, "ctl00$ctl02$lnk", "", press)["row_key"] == "kA"
        assert ledger.action(lid, "now.warc.gz", 5, "ctl00$ctl02$lnk", "", None) is None  # no row: no guess
    finally:
        conn.close()


def test_a_superseded_press_gives_way_to_its_rows_later_one(made):
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    conn.execute("UPDATE action SET state = 'superseded'")
    conn.execute(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset,"
        " target, argument, row_key, state, status, done) VALUES (?, 2, 2, 'pager', 'q', 'h', 's', 'p', 'x', 1,"
        " 'ctl00$MasterGrid', 'Page$2', 'p2', 'done', 200, 1793000000)", (made.ids["page"],))
    conn.close()
    got = _by_id(pagecheck.check(made.archive, PAGE))["b-next"]
    assert got["bucket"] == "got" and got["result"] == "ok"


def test_pages_that_cannot_be_read_strictly_or_at_all_or_are_framesets(more):
    assert pagecheck.check(more.archive, SITE + "Broken.aspx").summary["state"] == "unreadable"
    big5 = pagecheck.check(more.archive, SITE + "Big5.aspx")  # the header lies: export.redecode reads it
    assert big5.summary["state"] == "ok" and "繁體中文的頁" in big5.html
    frames = pagecheck.check(more.archive, SITE + "Frames.aspx")
    assert "frameset" in frames.summary["caveats"] and len(LexborHTMLParser(frames.html).css("div.nk-box")) == 2


def test_a_google_sites_runtime_picture_is_found_by_where_it_sits(made):
    from nknu.archive import links
    url = "https://sites.google.com/mail.nknu.edu.tw/oooo"
    html_text = ("<html><body><div id='h.abc_1'><img src='https://sites.google.com/sitesv-images-rt/zzz'></div>"
                 "</body></html>")
    warc3 = "warc/2026/11/06/gs.warc.gz"
    off = _records(made.archive / warc3, [(url, "200", [("Content-Type", "text/html; charset=utf-8")],
                                           html_text.encode())])[0]
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    pid = _url(conn, url, warc=warc3, offset=off)
    key = links.google_sites_keys(links.extract(html_text, url), pid)["https://sites.google.com/sitesv-images-rt/zzz"]
    conn.execute("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?, ?, 'gsimg', 'img', 'no', "
                 "'chrome')", (pid, key))
    conn.close()
    items = pagecheck.check(made.archive, url).summary["items"]
    assert [i["bucket"] for i in items if i["what"] == "image"] == ["rule"]


def test_a_live_wal_and_an_error_never_opens_the_ledger_immutable(made, monkeypatch):
    (made.archive / (pagecheck.LEDGER + "-wal")).write_bytes(b"x" * 64)

    def refuse(db):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(progress, "connect", refuse)
    with pytest.raises(sqlite3.OperationalError):
        pagecheck.open_ledger(made.archive)
    assert pagecheck.check(made.archive, PAGE).summary["state"] == "busy"


def test_a_snapshot_read_is_said_on_the_page(made, monkeypatch):
    def refuse(db):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(progress, "connect", refuse)
    summary = pagecheck.check(made.archive, PAGE).summary
    assert summary["snapshot"] and "snapshot" in summary["caveats"]


def test_fetch_times_are_shown_in_taipei_time():
    assert pagecheck.taipei("2026-10-26T22:20:27+00:00") == "2026-10-27T06:20:27+08:00"
    assert pagecheck.taipei("2026-11-06T10:00:00") == "2026-11-06T10:00:00" and pagecheck.taipei(None) == ""


def test_the_panel_states_a_verdict_and_who_acts(client):
    text = client.get("/check", params={"url": PAGE}).text
    assert "結論" in text and "沒抓到：正文區" in text and "爬蟲可補" in text and "第二輪 1" in text
    assert "要人看" not in text and "要看的" not in text and "真實網頁是現在的" not in text


def test_the_ledgers_reason_is_escaped_in_the_facts_line():
    from nknu.ops.check_page import _facts
    page = {"bucket": "failed", "result_words": "失敗", "reason": "x' onmouseover='alert(1)", "url": PAGE,
            "hops": [], "platform": "c.nknu 模板"}
    out = _facts({"page": page, "doc": None, "state": "ok", "regions": {"how": "area"}})
    assert "onmouseover='" not in out and "c.nknu 模板" in out and "cnknu:" not in out


def test_go_sends_the_live_window_on_for_a_bare_address(client):
    got = client.get("/check/go", params={"ref": "c.nknu.edu.tw/aca/default.aspx"}, follow_redirects=False)
    assert got.status_code == 302 and got.headers["location"] == "https://c.nknu.edu.tw/aca/default.aspx"
    assert client.get("/check/go", params={"ref": "no-such-doc"}, follow_redirects=False).status_code == 204
    assert client.get("/check/go", params={"ref": PAGE + "#第2页"}, follow_redirects=False).headers["location"] == PAGE


# ---------------------------------------------------------------- trial 2 of 11-16

HIDDEN_HTML = """<!doctype html><html><body><form><input type="hidden" name="__VIEWSTATE" value="{state}">
<div id="MainContent"><p>有一個很大的隱藏欄位</p><input type="text" value="看得到" id="shown"></div></form></body></html>"""


def test_a_hidden_fields_value_never_reaches_the_safe_page(made):
    """A __VIEWSTATE of 16 MB went whole into the frame (c.nknu affair pages): a hidden field shows nothing."""
    c = scope.canonical
    big = "QUJD" * 5000
    warc3 = "warc/2026/11/06/ip_140.127.0.1-3.warc.gz"
    offset, = _records(made.archive / warc3, [(c(SITE + "State.aspx"), "200", [("Content-Type", "text/html")],
                                               HIDDEN_HTML.format(state=big).encode("utf-8"))])
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, c(SITE + "State.aspx"), warc=warc3, offset=offset)
    conn.close()
    checked = pagecheck.check(made.archive, SITE + "State.aspx")
    assert checked.summary["state"] == "ok"
    assert big not in checked.html and "__VIEWSTATE" not in checked.html and "看得到" in checked.html


def test_a_sites_home_typed_without_its_file_name_finds_the_page_the_crawler_reached(made):
    """c.nknu.edu.tw/aca is in the ledger only as …/aca/default.aspx: found, and said so."""
    conn = sqlite3.connect(made.archive / pagecheck.LEDGER, isolation_level=None)
    _url(conn, scope.canonical(SITE + "default.aspx"))
    conn.close()
    for typed in ("https://c.nknu.edu.tw/aca", "c.nknu.edu.tw/aca/"):
        summary = pagecheck.check(made.archive, typed).summary
        assert summary["state"] == "not_fetched" and summary["spelled"].endswith("/aca/default.aspx")
    # a file name or a query is never guessed at
    assert pagecheck.check(made.archive, SITE + "Nothing.aspx").summary["state"] == "not_in_ledger"
    assert pagecheck.check(made.archive, "https://c.nknu.edu.tw/aca?x=1").summary["state"] == "not_in_ledger"


def test_the_spelling_caveat_names_the_ledgers_address():
    from nknu.ops.check_page import caveats_html
    out = caveats_html({"caveats": ["spelled"], "spelled": "https://c.nknu.edu.tw/aca/default.aspx"})
    assert "帳本寫法不同" in out and "aca/default.aspx" in out
