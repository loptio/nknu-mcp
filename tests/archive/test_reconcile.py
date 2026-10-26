"""Reconciliation, rejudge and a person's mark (round-1 design §18.2a, §18.2b, §10.1 thin, §10.2.1, verify checks 13
and 14; §16 test_reconcile). Offline: synthetic sites crawled on the tests' fake network into a real WARC, a
made-up Common Crawl index, a made-up old archive and made-up questions; a fake spot checker stands for jscheck's
browser. Titles, text and file names are made up; no person's name."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nknu.archive import crawler as crawl_mod
from nknu.archive import outcome, profiles, reconcile
from nknu.archive.crawler import AlreadyRunning, Crawler, hold_run_lock
from nknu.archive.frontier import Frontier
from nknu.archive.reconcile import FAIL, PASS, SKIP, WARN, Sources, SpotView
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.warc import WarcSink
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_archive import Clock, Net, snap
from tests.archive.test_crawler_round1 import settle
from tests.archive.test_postback import SSO_LIST, World, fx

SITE = "w.nknu.edu.tw"
W = f"https://{SITE}"
HOME = f"{W}/"
Q = f"ip:{SITE}"
PLAN = make_plan(make_site(SITE, name="测试单位", home=HOME))
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
BODY = "這是一段測試用的內文，介紹本單位的業務與服務項目，內容足夠長，不會被當成空白的頁面。" * 2
SITE_JS = ("function toggleMenu(){ document.getElementById('m').classList.toggle('open'); }\n"
           "$('.cat').click(function(){ $.ajax({url: '/api/news.aspx', data: {id: $(this).data('id')}}); });\n")


def _page(title: str, main: str, *, nav: bool = True, script: bool = False) -> str:
    menu = ('<nav><a href="/about.html">關於我們</a><a href="/news.html">最新消息</a>'
            '<a href="https://sso.nknu.edu.tw/login.aspx">登入</a></nav>') if nav else ""
    js = '<script src="/js/site.js"></script>' if script else ""
    return (f"<html><head><title>{title}</title>{js}</head><body>{menu}<main>{main}</main>"
            '<footer><a href="/contact.html">聯絡我們</a></footer></body></html>')


HOME_HTML = _page("首頁", f"<p>{BODY}</p><a href='/files/a.pdf'>簡章.pdf</a>"
                         '<a href="javascript:void(0)" onclick="toggleMenu()">選單</a>'
                         '<ul><li><a href="javascript:" class="cat" data-id="1">分類一</a></li></ul>'
                         '<div class="slide" data-link="/news/2.html">輪播</div>', script=True)
ROUTES = {
    "/js/site.js": lambda u: snap(u, body=SITE_JS.encode(), headers={"content-type": "application/javascript"}),
    "/files/a.pdf": lambda u: snap(u, body=PDF, headers={"content-type": "application/pdf"}),
    "/about.html": lambda u: snap(u, html=_page("關於", f"<p>關於我們的內文：{BODY}</p>")),
    "/news/2.html": lambda u: snap(u, html=_page("消息二", f"<p>第二則消息。{BODY}</p>")),
    "/news.html": lambda u: snap(u, html=_page("消息", f"<p>最新消息列表。{BODY}</p>")),
    "/contact.html": lambda u: snap(u, html=_page("聯絡", f"<p>聯絡方式。{BODY}</p>")),
    f"{SITE}/": lambda u: snap(u, html=HOME_HTML),
}


class WarcNet(Net):
    """The tests' fake network, each answer archived in a real WARC at the path the tests' Sink names (as the client
    does with its sink): warc/q.warc.gz."""

    def __init__(self, clock, routes, root: Path):
        super().__init__(clock, routes)
        path = root / "warc" / "q.warc.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sink = WarcSink.__new__(WarcSink)
        self.sink.path = path
        self.sink._fh = open(path, "ab", buffering=0)  # noqa: SIM115 - closed by the fixture

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        got = super().fetch(target, rate, max_bytes, pass_statuses)
        offset, length = self.sink.write(got, {})
        return replace(got, warc_offset=offset, warc_len=length)


def crawl(root: Path, routes=None, plan=PLAN, **kw) -> tuple[Crawler, WarcNet]:
    clock = Clock()
    net = WarcNet(clock, {"robots.txt": snap("x", 404), **(ROUTES if routes is None else routes)}, root)
    c = Crawler(root, plan=plan, clock=clock, sleep=clock.sleep, client_factory=net,
                resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True, disk_free=lambda p: 10**12,
                campus_url=None, **kw)
    c.seed_url(HOME)
    settle(c, net, [Q])
    return c, net


@pytest.fixture
def site_db(tmp_path):
    c, net = crawl(tmp_path)
    yield c, net
    net.sink.close()
    c.frontier.close()


def _id(fr, url):
    return fr.row(url)["id"]


def _keys(fr, url):
    return {r["res_key"]: r for r in fr.q("SELECT * FROM page_res WHERE page_id=?", (_id(fr, url),))}


def _ok_types(fr):
    fr.x("UPDATE page_type SET verdict='ok', source='manual'")


def _cdx(path: Path, urls, status="200"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "CC-TEST.cdx").write_text("".join(
        f'tw,edu,nknu,w)/ 20260101000000 {json.dumps({"url": u, "status": status})}\n' for u in urls),
        encoding="utf-8")
    return path


def _old(path: Path, rows):
    """A made-up 10-24 archive: its url table (no meta round: never written)."""
    path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path / "frontier.db")
    conn.execute("CREATE TABLE url(id INTEGER PRIMARY KEY, url TEXT, site TEXT, state TEXT, status INTEGER, "
                 "depth INTEGER, found_from INTEGER)")
    conn.executemany("INSERT INTO url(url, site, state, status, depth) VALUES (?,?,?,?,0)", rows)
    conn.commit()
    conn.close()
    return path


def _golden(path: Path, sources):
    path.write_text(json.dumps({"webAnswers": [{"id": f"Q{i}", "sources": [{"url": u, "quote": q}]}
                                               for i, (u, q) in enumerate(sources, 1)], "grades": []},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _same_spot(root):
    arc = reconcile.Archive(root)
    fr = Frontier(root / "frontier.db", readonly=True)

    def spot(url):
        r = fr.row(url)
        return SpotView(url, url, arc.text(r["warc"], r["warc_offset"]), f"shots/{r['id']}.png")
    return spot


# ---------------------------------------------------------------- thin (design §10.1)


class Arch:
    """A frontier and a WARC with pages written straight in (no crawler): what thin reads."""

    def __init__(self, root: Path):
        self.root = root
        self.f = Frontier(root / "frontier.db")
        self.sink = WarcSink.__new__(WarcSink)
        self.sink.path = root / "warc" / "t.warc.gz"
        self.sink.path.parent.mkdir(parents=True, exist_ok=True)
        self.sink._fh = open(self.sink.path, "ab", buffering=0)  # noqa: SIM115 - closed below
        self.f.set_host(SITE, queue=Q)

    def page(self, path: str, html: str, ptype: str = "t1", *, status: int = 200, site: str = SITE) -> int:
        url = f"{W}{path}"
        body = html.encode()
        sha = hashlib.sha256(body).hexdigest()
        snap_ = Snapshot(Target(url), url, status, {"content-type": "text/html; charset=utf-8"}, body, html,
                         datetime.now(UTC), sha)
        offset, length = self.sink.write(snap_, {})
        self.f.add(url, host=SITE, site=site, unit=None, tier=1, kind="page", depth=1, found_via="link",
                   found_from=None, owner_site=site, kind0="page")
        uid = self.f.row(url)["id"]
        self.f.finish(uid, state="done", status=status, kind="page", sniff="html" if status == 200 else None,
                      warc="warc/t.warc.gz", warc_offset=offset, warc_len=length, sha256=sha, page_type=ptype,
                      final_url=url)
        self.f.x("INSERT OR IGNORE INTO page_type(key, site, verdict, source) VALUES (?,?,'ok','manual')",
                 (ptype, site))
        return uid

    def close(self):
        self.sink.close()
        self.f.close()


@pytest.fixture
def arch(tmp_path):
    a = Arch(tmp_path)
    yield a
    a.close()


LAYOUT = "<header><p>學校首頁</p><p>國立高雄師範大學 測試單位</p></header><p>網站導覽 回到上方</p>"


def _tpl(body: str) -> str:
    return f"<html><body>{LAYOUT}<div>{body}</div><footer>地址 高雄市測試路 1 號 電話 07-0000000</footer></body></html>"


def _thin(arch, site=SITE):
    return reconcile.thin_pages(arch.f, arch.root, [site])[site]


def test_thin_takes_out_the_lines_most_pages_of_a_kind_repeat(arch):
    ids = [arch.page(f"/p{i}.html", _tpl(f"<p>第{i}篇：{BODY}</p>")) for i in range(5)]
    short = arch.page("/short.html", _tpl("<p>短</p>"))
    pic = arch.page("/pic.html", _tpl("<p>圖</p>"))
    arch.f.set_page_res(pic, [{"res_key": f"{W}/up/a.jpg", "how": "get", "role": "img", "need": "must"}])
    got = _thin(arch)
    assert got.thin == [short] and got.set_ == 1
    assert "thin" in arch.f.row_by_id(short)["flags"].split(",")
    assert arch.f.q("SELECT result FROM url_result WHERE id=?", (short,))[0][0] == "thin"
    assert all(arch.f.row_by_id(i)["flags"] is None for i in [*ids, pic])  # only one picture is no thin page
    # it needs an attachment now: no longer thin, the flag comes off
    arch.f.set_page_res(short, [{"res_key": f"{W}/up/a.pdf", "how": "get", "role": "file", "need": "must"}])
    again = _thin(arch)
    assert again.thin == [] and again.cleared == 1 and arch.f.row_by_id(short)["flags"] is None


def test_thin_with_fewer_than_five_pages_of_a_kind_uses_the_site_and_with_fewer_takes_nothing_out(arch):
    for i in range(4):
        arch.page(f"/a{i}.html", _tpl(f"<p>{BODY}{i}</p>"), "ta")
    lone = arch.page("/b.html", _tpl("<p>短</p>"), "tb")  # its kind has 1 page: the site's 5 pages say the layout
    assert _thin(arch).thin == [lone]


def test_thin_takes_nothing_out_of_a_site_of_fewer_than_five_pages(arch):
    arch.page("/a.html", _tpl(f"<p>{BODY}</p>"))
    small = arch.page("/b.html", _tpl("<p>短</p>"))  # the layout's own words are more than 30 characters
    assert _thin(arch).thin == [] and arch.f.row_by_id(small)["flags"] is None


def test_embed_code_words_count_and_a_js_type_or_a_walked_list_is_never_thin(arch):
    for i in range(5):
        arch.page(f"/p{i}.html", _tpl(f"<p>{BODY}{i}</p>"))
    code = f"<p>{BODY}</p>".replace("<", "&lt;").replace(">", "&gt;")
    sites = arch.page("/code.html", _tpl(f'<div data-code="{code}"></div>'))
    js = arch.page("/js.html", _tpl(""), "tjs")
    arch.f.x("UPDATE page_type SET verdict='js' WHERE key='tjs'")
    lst = arch.page("/list.html", _tpl(""))
    arch.f.x("INSERT INTO listwalk(list_id, site, pages, rows, rows_unique, ended_by, ok) "
             "VALUES (?,?,1,3,3,'last_page',1)", (lst, SITE))
    empty = arch.page("/empty.html", _tpl(""))
    assert _thin(arch).thin == [empty]
    assert all(arch.f.row_by_id(i)["flags"] is None for i in (sites, js, lst))


def test_thin_reads_the_platform_content_area(arch):
    site = "c.nknu.edu.tw/aca"
    for i in range(5):
        arch.page(f"/aca/p{i}.aspx", "<html><body><div id='menu'>" + "選單項目很多很多" * 10 +
                  f"</div><span id='lbl_Contents'>{BODY}{i}</span></body></html>", site=site)
    hollow = arch.page("/aca/x.aspx", "<html><body><div id='menu'>" + "選單項目很多很多" * 10 +
                       f"{BODY}</div><span id='lbl_Contents'>無</span></body></html>", site=site)
    arch.f.x("UPDATE url SET url=replace(url, 'w.nknu.edu.tw', 'c.nknu.edu.tw')")
    assert _thin(arch, site).thin == [hollow]  # the words outside the content area are no body


# ---------------------------------------------------------------- R1, R7, V13, V14 on a crawled site


def test_the_crawlers_own_pages_read_again_match_what_it_wrote(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    got = reconcile.verify_extract(fr, tmp_path, [SITE], PLAN)[SITE]
    assert got.status == PASS and got.counts["pages"] == 5, got.items
    v14 = reconcile.verify_archive(fr, tmp_path, [SITE])[SITE]
    assert v14.status == PASS and v14.counts["records"] >= 7 and v14.counts["files"] == 1


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00made up for a test\xff\xd9"
RICH_HTML = _page("豐富", (
    f"<p>{BODY}</p><img src='/up/photo.jpg' width='300' height='200' alt='活動照片'>"
    "<img src='/images/logo.png' alt='標誌'><img src='data:image/png;base64,AAAA' width='300' height='300'>"
    "<iframe src='https://www.youtube.com/embed/AbCdEfGhIjK' title='介紹影片'></iframe>"
    "<iframe src='/frame.html' width='600' height='400'></iframe>"
    "<a href='https://reurl.cc/AbCdEf'>短網址</a>"
    "<a href='https://drive.google.com/file/d/1DrIvEfIlEaBcDeFgHiJ/view'>雲端檔案</a>"
    "<form method='get' action='/search'><input name='q'></form>"
    "<select onchange='location.href=this.value'><option value='/a.html'>A</option></select>"
    "<div data-code=\"&lt;a href='/files/c.pdf'&gt;附件 C&lt;/a&gt;&lt;p&gt;嵌入的字&lt;/p&gt;\"></div>"
    "<a href=\"javascript:__doPostBack('btnMore','')\">更多</a>"
    "<a href='/page.html?page=2'>2</a><a href='/page.html?page=2'>下一頁</a>"))


def test_v13_is_quiet_on_a_page_with_every_kind_of_part(tmp_path):
    routes = {"/up/photo.jpg": lambda u: snap(u, body=JPEG, headers={"content-type": "image/jpeg"}),
              "/images/logo.png": lambda u: snap(u, body=JPEG, headers={"content-type": "image/png"}),
              "/files/": lambda u: snap(u, body=PDF, headers={"content-type": "application/pdf"}),
              "reurl.cc/AbCdEf": lambda u: snap(u, 302, headers={"location": f"{W}/files/b.pdf"}),
              "/frame.html": lambda u: snap(u, html=_page("框架", f"<p>{BODY}</p><a href='/files/d.pdf'>D</a>")),
              f"{SITE}/": lambda u: snap(u, html=RICH_HTML)}
    clock = Clock()
    net = WarcNet(clock, {"robots.txt": snap("x", 404), **routes}, tmp_path)
    c = Crawler(tmp_path, plan=PLAN, clock=clock, sleep=clock.sleep, client_factory=net,
                resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True, disk_free=lambda p: 10**12,
                campus_url=None)
    c.seed_url(HOME)
    settle(c, net, [Q, "ip:reurl.cc"])
    fr = c.frontier
    keys = _keys(fr, HOME)
    assert {"postback:btnMore", "get_form:/search q", f"{W}/up/photo.jpg", f"{W}/images/logo.png",
            "drive:1DrIvEfIlEaBcDeFgHiJ", "https://reurl.cc/AbCdEf", f"{W}/files/c.pdf"} <= set(keys), sorted(keys)
    got = reconcile.verify_extract(fr, tmp_path, [SITE], PLAN)[SITE]
    assert got.status == PASS and got.counts["pages"] >= 2, got.items  # home and the frame page
    assert reconcile.verify_archive(fr, tmp_path, [SITE])[SITE].status == PASS
    # rejudge reads it back to the same page_res (R7 has nothing to add: no school script)
    fr.close()
    net.sink.close()
    reconcile.rejudge(tmp_path, [SITE], plan=PLAN)
    fr = Frontier(tmp_path / "frontier.db")
    again = _keys(fr, HOME)
    assert {k: (r["need"], r["why"], r["res_id"]) for k, r in again.items()} == \
        {k: (r["need"], r["why"], r["res_id"]) for k, r in keys.items()}
    assert reconcile.verify_extract(fr, tmp_path, [SITE], PLAN)[SITE].status == PASS
    fr.close()


def test_v13_names_what_the_crawler_did_not_write(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    home = _id(fr, HOME)
    fr.x("DELETE FROM link WHERE src=? AND dst=?", (home, f"{W}/about.html"))
    fr.x("DELETE FROM page_res WHERE page_id=? AND res_key=?", (home, f"{W}/files/a.pdf"))
    got = reconcile.verify_extract(fr, tmp_path, [SITE], PLAN)[SITE]
    assert got.status == FAIL
    missing = {(i["missing"], i["target"]) for i in got.items}
    assert ("link", f"{W}/about.html") in missing and ("page_res", f"{W}/files/a.pdf") in missing


def test_v14_finds_a_changed_hash_a_lost_position_and_a_broken_file(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    fr.x("UPDATE url SET sha256='0' WHERE url=?", (f"{W}/about.html",))
    fr.x("UPDATE url SET warc_offset=NULL WHERE url=?", (f"{W}/contact.html",))
    got = reconcile.verify_archive(fr, tmp_path, [SITE])[SITE]
    assert got.status == FAIL
    assert {(i["url"], i["problem"]) for i in got.items} == {(f"{W}/about.html", "sha256"),
                                                              (f"{W}/contact.html", "no_position")}
    path = tmp_path / "warc" / "q.warc.gz"
    data = bytearray(path.read_bytes())
    at = fr.row(f"{W}/news.html")["warc_offset"] + 40
    data[at] ^= 0xFF
    path.write_bytes(bytes(data))
    broken = reconcile.verify_archive(fr, tmp_path, [SITE])[SITE]
    problems = {i["problem"] for i in broken.items}
    assert "unreadable" in problems and any(p.startswith(("BadGzipFile", "error", "EOFError", "OSError"))
                                            for p in problems)


def test_v14_cache_reads_only_what_came_after(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    assert reconcile.verify_archive(fr, tmp_path, [SITE], cache=True)[SITE].status == PASS
    end = int(fr.meta("v14:warc/q.warc.gz"))
    assert end == fr.q("SELECT max(warc_offset + warc_len) FROM url WHERE warc='warc/q.warc.gz'")[0][0]


def test_r1_menu_items_each_have_a_result(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    r1 = res.checks["R1"]
    assert r1.status == PASS and r1.counts["rule"] == 1 and r1.counts["missing"] == 0  # the sign-in: a rule
    # a menu item that has no row and that the crawler never noted: missing, found for a person
    fr.x("DELETE FROM url WHERE url=?", (f"{W}/contact.html",))
    fr.x("DELETE FROM link WHERE dst=?", (f"{W}/contact.html",))
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    assert res.checks["R1"].status == FAIL
    assert [(f.check, f.url) for f in res.found] == [("R1", f"{W}/contact.html")]


def test_r7_reads_the_schools_js_and_writes_what_it_says(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    before = _keys(fr, HOME)
    assert before["js_link:toggleMenu"]["why"] == "unhandled:unknown" and before["js_link:"]["need"] == "must"
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    after = _keys(fr, HOME)
    bound = [k for k, r in after.items() if (r["zone"] or "").startswith(reconcile.R7_ZONE)]
    assert bound == ["click:.cat[0]"]
    assert (after["click:.cat[0]"]["need"], after["click:.cat[0]"]["why"]) == ("round2", "round2:click")
    suggest = {s.key: s.decision for s in res.suggestions}
    assert suggest == {"js_link:toggleMenu": "ignore", "js_link:": "round2"}
    assert res.checks["R7"].status == WARN and res.checks["R7"].counts["undecided"] == 2
    _ok_types(fr)
    level = fr.q("SELECT level, reasons FROM page_completeness WHERE id=?", (_id(fr, HOME),))[0]
    assert level[0] == "partial" and "click" in level[1]  # what only the school's script loads is no longer unseen
    # a second reconcile writes the same rows again (never twice), and rejudge keeps them
    reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN, sources=Sources(cc_dir=None, golden=None))
    assert [k for k, r in _keys(fr, HOME).items() if (r["zone"] or "").startswith("r7:")] == ["click:.cat[0]"]


def test_r7_lists_a_data_link_target_and_a_drive_embed_with_no_row(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    fr.x("DELETE FROM link WHERE dst=?", (f"{W}/news/2.html",))
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    r7 = res.checks["R7"]
    assert r7.status == FAIL
    assert {"page": HOME, "problem": "attr_target", "target": f"{W}/news/2.html"} in r7.items


def test_r7_drive_embed_with_no_drive_row(arch):
    fid = "1AbCdEfGhIjKlMnOpQr"  # made up
    html = (f"<html><body><div data-embed-open-url='https://drive.google.com/file/d/{fid}/view'>"
            f"<iframe data-src='https://drive.google.com/file/d/{fid}/preview'></iframe></div><p>{BODY}</p>"
            "</body></html>")
    uid = arch.page("/embed.html", html)
    res = reconcile.reconcile_sites(arch.f, arch.root, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    assert {"page": f"{W}/embed.html", "problem": "drive_embed", "target": f"drive:{fid}"} in res.checks["R7"].items
    arch.f.add_page_res(uid, [{"res_key": f"drive:{fid}", "how": "drive_api", "role": "drive", "need": "must"}])
    res = reconcile.reconcile_sites(arch.f, arch.root, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    assert not [i for i in res.checks["R7"].items if i.get("problem") == "drive_embed"]


# ---------------------------------------------------------------- R2


def test_r2_a_list_with_fewer_rows_than_it_states(arch):
    a = arch.page("/list.html", _tpl(f"<p>{BODY}</p>"))
    arch.f.x("INSERT INTO listwalk(list_id, site, profile, pages, rows, rows_unique, claimed, ended_by, ok) "
             "VALUES (?,?,'generic',1,8,8,10,'last_page',1)", (a, SITE))
    r2 = reconcile._r2(arch.f, arch.root, SITE)
    assert r2.status == FAIL and r2.items[0]["problem"] == "fewer" and r2.items[0]["got"] == 8
    arch.f.x("UPDATE listwalk SET rows_unique=10, rows=10")
    assert reconcile._r2(arch.f, arch.root, SITE).status == PASS


# ---------------------------------------------------------------- R3, R4: discovery coverage


def test_common_crawl_import_and_coverage(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    known = [HOME, f"{W}/about.html", f"{W}/news.html", f"{W}/contact.html", f"{W}/only-cc.html",
             "https://sso.nknu.edu.tw/login.aspx"]
    cc = _cdx(tmp_path / "cc", known)
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=cc, golden=None)).sites[0]
    r3a, r3b = res.checks["R3a"], res.checks["R3b"]
    assert r3a.status == FAIL and r3a.items == [{"url": f"{W}/only-cc.html"}]
    assert ("R3a", f"{W}/only-cc.html") in [(f.check, f.url) for f in res.found]
    # this round's ok pages and files (the script aside): home, about, news, contact, a.pdf, news/2; linked: all
    # but home. Common Crawl's ok ones: home, about, news, contact; linked 3 of 4
    assert (r3b.counts["known_ok"], r3b.counts["linked"], r3b.counts["site_ok"], r3b.counts["site_linked_ok"]) == \
        (4, 3, 6, 5)
    assert r3b.status == WARN and res.cover_cc == pytest.approx(0.75)
    assert r3b.counts["estimate"] == 7 and r3b.counts["missed_at_least"] == 1  # 5 × 4 / 3, a lower bound
    assert r3b.items == [{"url": HOME}]  # only Common Crawl names it: no page of this round links home
    got = fr.q("SELECT cover_cc FROM reconcile_site WHERE site=?", (SITE,))[0][0]
    assert got == pytest.approx(0.75)


def test_coverage_cannot_be_estimated_when_none_is_ok(site_db, tmp_path):
    c, _net = site_db
    cc = _cdx(tmp_path / "cc", [f"{W}/only-cc.html"])
    res = reconcile.reconcile_sites(c.frontier, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=cc, golden=None)).sites[0]
    assert res.checks["R3b"].status == SKIP and res.cover_cc is None
    assert c.frontier.q("SELECT cover_cc FROM reconcile_site")[0][0] is None


def test_old_archive_import_and_coverage_read_only(site_db, tmp_path):
    c, _net = site_db
    old = _old(tmp_path / "old", [(f"{W}/about.html", SITE, "done", 200), (f"{W}/contact.html", SITE, "done", 200),
                                  (f"{W}/gone-now.html", SITE, "done", 200), (f"{W}/x.html", SITE, "failed", None)])
    before = hashlib.sha256((old / "frontier.db").read_bytes()).hexdigest()
    res = reconcile.reconcile_sites(c.frontier, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, old=old, golden=None)).sites[0]
    assert res.checks["R4a"].status == FAIL and res.checks["R4a"].items == [{"url": f"{W}/gone-now.html"}]
    assert res.checks["R4b"].status == PASS and res.cover_old == 1.0
    assert hashlib.sha256((old / "frontier.db").read_bytes()).hexdigest() == before
    assert not (old / "frontier.db-wal").exists() and not (old / "frontier.db-shm").exists()


def test_sources_not_there_are_not_checked_never_passed(site_db, tmp_path):
    c, _net = site_db
    res = reconcile.reconcile_sites(c.frontier, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=tmp_path / "none", golden=tmp_path / "none.json")).sites[0]
    assert {k: res.checks[k].status for k in ("R3a", "R3b", "R4a", "R4b", "R5", "R6")} == dict.fromkeys(
        ("R3a", "R3b", "R4a", "R4b", "R5", "R6"), SKIP)
    assert not res.gates_ok


# ---------------------------------------------------------------- R5


def test_r5_answer_pages_must_be_complete_and_hold_their_quotes(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    _ok_types(fr)
    gold = _golden(tmp_path / "q.json", [(f"{W}/about.html", "關於我們的內文；本單位的業務"),
                                         (f"{W}/news.html", "不在頁面上的一句話，學校可能改了"),
                                         (f"{W}/files/a.pdf", "簡章"),
                                         ("https://elsewhere.example.org/x", "別的網站")])
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=gold)).sites[0]
    r5 = res.checks["R5"]
    assert r5.status == FAIL and r5.counts["answers"] == 3 and r5.counts["quotes_unchecked"] == 1
    assert [(i["url"], i["problem"]) for i in r5.items if "problem" in i] == [(f"{W}/news.html", "quote_not_found")]


# ---------------------------------------------------------------- R6 and the gates


def _clean_site(root: Path):
    routes = {k: v for k, v in ROUTES.items() if k in ("/about.html", "/news.html", "/contact.html")}
    routes[f"{SITE}/"] = lambda u: snap(u, html=_page("首頁", f"<p>{BODY}</p>"))  # last: every address holds it
    return crawl(root, routes)


def test_every_gate_passes_and_a_run_after_it_makes_it_stale(tmp_path):
    c, net = _clean_site(tmp_path)
    fr = c.frontier
    cc = _cdx(tmp_path / "cc", [f"{W}/about.html", f"{W}/news.html"])
    old = _old(tmp_path / "old", [(f"{W}/about.html", SITE, "done", 200)])
    gold = _golden(tmp_path / "q.json", [(f"{W}/about.html", "關於我們的內文")])
    src = Sources(cc_dir=cc, old=old, golden=gold)
    _ok_types(fr)
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="pilot", plan=PLAN, sources=src,
                                    spot=_same_spot(tmp_path)).sites[0]
    assert {k: v.status for k, v in res.checks.items() if v.status != PASS} == {}
    assert res.gates_ok and reconcile.fresh(fr, SITE)
    row = fr.q("SELECT * FROM reconcile_site WHERE site=?", (SITE,))[0]
    assert (row["batch"], row["gates_ok"]) == ("pilot", 1) and set(json.loads(row["checks"])) == set(
        reconcile.CHECKS)
    fr.event("info", "run_end", json.dumps({"reason": "finished", "sites": [SITE]}))
    assert not reconcile.fresh(fr, SITE)  # a run covered the site after it: reconcile again
    fr.event("info", "run_end", json.dumps({"reason": "finished", "sites": ["other.nknu.edu.tw"]}))
    reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="pilot", plan=PLAN, sources=src, spot=_same_spot(tmp_path))
    assert reconcile.fresh(fr, SITE)
    fr.event("info", "rejudge", json.dumps({"sites": None}))
    assert not reconcile.fresh(fr, SITE)
    net.sink.close()
    fr.close()


def test_site_done_in_the_crawlers_process_skips_r6_so_never_a_pass(tmp_path):
    c, net = _clean_site(tmp_path)
    fr = c.frontier
    assert reconcile.site_finished(fr, SITE)
    res = reconcile.site_done(fr, tmp_path, SITE, plan=PLAN, sources=Sources(cc_dir=None, golden=None))
    assert res.checks["R6"].status == SKIP and not res.gates_ok
    assert fr.q("SELECT gates_ok FROM reconcile_site WHERE site=?", (SITE,))[0][0] == 0
    assert json.loads(fr.q("SELECT message FROM event WHERE code='reconcile'")[0][0])["r6"] is False
    fr.x("UPDATE url SET state='pending', attempts=0, reason=NULL WHERE url=?", (f"{W}/about.html",))
    assert not reconcile.site_finished(fr, SITE)
    fr.x("UPDATE url SET attempts=1 WHERE url=?", (f"{W}/about.html",))  # waiting for a retry only: done for now
    assert reconcile.site_finished(fr, SITE)
    net.sink.close()
    fr.close()


def test_r6_text_the_browser_shows_more_of_and_a_menu_item_only_the_browser_sees(tmp_path):
    c, net = _clean_site(tmp_path)
    fr = c.frontier
    same = _same_spot(tmp_path)
    more = "<p>" + "瀏覽器才看得到的文字。" * 40 + "</p>"

    def spot(url):
        view = same(url)
        if url.endswith("/about.html"):
            return replace(view, html=view.html.replace("</main>", more + "</main>").replace(
                "</nav>", '<a href="/hidden-menu.html">隱藏選單</a></nav>'))
        return view

    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="b", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None), spot=spot,
                                    out_dir=tmp_path / "spot").sites[0]
    r6 = res.checks["R6"]
    assert r6.status == FAIL and r6.counts["pages"] == 4
    item = next(i for i in r6.items if i["url"].endswith("/about.html"))
    assert "text" in item and item["missing"] == [{"what": "menu", "target": f"{W}/hidden-menu.html"}]
    assert item["screenshot"].startswith("shots/")
    assert (tmp_path / "spot" / SITE / f"{_id(fr, f'{W}/about.html')}.txt").is_file()
    errors = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="b", plan=PLAN,
                                       sources=Sources(cc_dir=None, golden=None),
                                       spot=lambda u: SpotView(u, error="timeout")).sites[0]
    assert errors.checks["R6"].status == FAIL
    net.sink.close()
    fr.close()


def test_a_read_only_reconcile_writes_nothing(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    before = fr.q("SELECT count(*) FROM page_res")[0][0]
    got = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None), write=False).sites[0]
    assert fr.q("SELECT count(*) FROM page_res")[0][0] == before
    assert not fr.q("SELECT 1 FROM reconcile_site") and not fr.q("SELECT 1 FROM event WHERE code='reconcile'")
    assert got.checks["R7"].status == FAIL  # what R7 would write and did not


# ---------------------------------------------------------------- the command


def test_reconcile_command_refuses_while_a_run_holds_the_lock_and_writes_its_report(tmp_path):
    c, net = _clean_site(tmp_path)
    c.frontier.close()
    net.sink.close()
    lock = hold_run_lock(tmp_path)
    with pytest.raises(AlreadyRunning):
        reconcile.reconcile(tmp_path, batch="b", plan=PLAN, sources=Sources(cc_dir=None, golden=None))
    lock.close()
    with pytest.raises(Exception, match="machine-wide lock"):
        reconcile.reconcile(tmp_path, batch="b", plan=PLAN, sources=Sources(cc_dir=None, golden=None),
                            spot=_same_spot(tmp_path))
    got, path = reconcile.reconcile(tmp_path, batch="b 1", plan=PLAN, sources=Sources(cc_dir=None, golden=None))
    assert path.parent == tmp_path / "reports" and path.name.startswith("reconcile-b_1-")
    text = path.read_text(encoding="utf-8")
    assert "浏览器抽查" in text and SITE in text and got.sites[0].site == SITE
    assert got.sites[0].checks["R6"].status == SKIP  # --no-r6


def test_reconcile_command_makes_the_spot_checker_inside_its_locks(tmp_path):
    c, net = _clean_site(tmp_path)
    c.frontier.close()
    net.sink.close()
    opened = []

    @contextlib.contextmanager
    def factory(fr):
        assert crawl_mod.lock_holder(tmp_path) is not None  # run.lock is held by now
        same = _same_spot(tmp_path)
        opened.append(fr)
        yield lambda url: same(url)
        opened.append("closed")

    with schoollock.hold():
        got, _path = reconcile.reconcile(tmp_path, batch="b", plan=PLAN, spot_factory=factory,
                                         sources=Sources(cc_dir=None, golden=None))
    assert opened[-1] == "closed" and got.sites[0].checks["R6"].status == PASS and got.r6


def test_the_old_archive_is_never_written(tmp_path):
    old = _old(tmp_path / "old", [(HOME, SITE, "done", 200)])
    before = hashlib.sha256((old / "frontier.db").read_bytes()).hexdigest()
    for call in (lambda: reconcile.rejudge(old, [SITE], plan=PLAN),
                 lambda: reconcile.enqueue(old, [HOME], plan=PLAN),
                 lambda: reconcile.review_mark(old, 1, note="x"),
                 lambda: reconcile.reconcile(old, batch="b", plan=PLAN)):
        with pytest.raises(reconcile.NotRound1):
            call()
    assert hashlib.sha256((old / "frontier.db").read_bytes()).hexdigest() == before
    with pytest.raises(reconcile.NotRound1):
        reconcile.rejudge(tmp_path / "nothing", [SITE], plan=PLAN)
    assert not (tmp_path / "nothing" / "frontier.db").exists()


# ---------------------------------------------------------------- rejudge


def test_rejudge_reads_the_pages_again_with_todays_decisions_and_sends_nothing(tmp_path):
    c, net = crawl(tmp_path)
    fr = c.frontier
    home = _id(fr, HOME)
    assert _keys(fr, HOME)["js_link:toggleMenu"]["need"] == "must"
    fetched = {r["url"]: r["fetched_at"] for r in fr.q("SELECT url, fetched_at FROM url")}
    fr.x("DELETE FROM link WHERE dst=?", (f"{W}/contact.html",))
    fr.x("DELETE FROM url WHERE url=?", (f"{W}/contact.html",))  # as if the old rules had not found it
    seen = list(net.seen)
    fr.close()
    net.sink.close()

    def decide(_site, key):
        return "ignore" if key == "js_link:toggleMenu" else None

    got = reconcile.rejudge(tmp_path, [SITE], plan=PLAN, decide=decide)
    assert got.pages == 4 and got.new_rows == 1 and not got.unread  # home, about, news, news/2
    fr = Frontier(tmp_path / "frontier.db")
    keys = _keys(fr, HOME)
    assert (keys["js_link:toggleMenu"]["need"], keys["js_link:toggleMenu"]["why"]) == ("no", "unhandled:ignore")
    assert keys["click:.cat[0]"]["zone"].startswith("r7:")  # R7's rows come back with the page's
    new = fr.row(f"{W}/contact.html")
    assert (new["state"], new["found_via"], new["owner_site"]) == ("pending", "rejudge", SITE)
    assert net.seen == seen  # nothing was sent
    kept = {u: t for u, t in fetched.items() if u != f"{W}/contact.html"}
    assert {r["url"]: r["fetched_at"] for r in fr.q("SELECT url, fetched_at FROM url") if r["url"] in kept} == kept
    ev = json.loads(fr.q("SELECT message FROM event WHERE code='rejudge'")[0][0])
    assert ev["sites"] == [SITE] and ev["pages"] == 4
    assert fr.row_by_id(home)["page_type"] is not None
    fr.close()


def test_rejudge_refuses_while_a_run_holds_the_lock(tmp_path):
    c, net = crawl(tmp_path)
    c.frontier.close()
    net.sink.close()
    lock = hold_run_lock(tmp_path)
    try:
        with pytest.raises(AlreadyRunning):
            reconcile.rejudge(tmp_path, [SITE], plan=PLAN)
        with pytest.raises(AlreadyRunning):
            reconcile.enqueue(tmp_path, [f"{W}/x.html"], plan=PLAN)
    finally:
        lock.close()


def test_rejudge_a_new_host_waits_for_the_next_runs_lookup(tmp_path):
    routes = {f"{SITE}/": lambda u: snap(u, html=_page("首頁", f"<p>{BODY}</p><a href='https://v.nknu.edu.tw/a'>V</a>",
                                                         nav=False))}
    two = make_plan(make_site(SITE, home=HOME), make_site("v.nknu.edu.tw"))
    c, net = crawl(tmp_path, routes, plan=two)
    fr = c.frontier
    fr.x("DELETE FROM url WHERE host='v.nknu.edu.tw'")
    fr.x("DELETE FROM host WHERE host='v.nknu.edu.tw'")
    fr.close()
    net.sink.close()
    reconcile.rejudge(tmp_path, [SITE], plan=two)
    fr = Frontier(tmp_path / "frontier.db")
    assert fr.host("v.nknu.edu.tw")["queue"] == "?"  # Crawler._place_hosts looks it up before anything is sent
    assert fr.row("https://v.nknu.edu.tw/a")["found_via"] == "rejudge"
    fr.close()


def test_rejudge_keeps_a_walked_lists_presses_and_opens_no_epoch(tmp_path):
    world = World(tmp_path)
    news = "sso.nknu.edu.tw/news"
    row, _got = world.stored(SSO_LIST, news, fx("sso_list_p1.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("sso_list_p2.html"))
    walk = dict(world.walk(row["id"]))
    actions = [dict(x) for x in world.actions(row["id"])]
    keys = {r["res_key"] for r in world.f.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    world.sink.close()
    world.f.close()
    got = reconcile.rejudge(tmp_path, [news], plan=make_plan(make_site(news, imp="全校重要", home=SSO_LIST)))
    assert got.pages == 1
    f = Frontier(tmp_path / "frontier.db")
    assert [dict(x) for x in f.q("SELECT * FROM action WHERE list_id=? ORDER BY id", (row["id"],))] == actions
    assert dict(f.q("SELECT * FROM listwalk WHERE list_id=?", (row["id"],))[0]) == walk
    again = {r["res_key"] for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    assert {k for k in keys if k.startswith(("pb:", "item:"))} <= again
    assert f.q("SELECT result FROM page_resource WHERE page_id=? AND res_key='pb:pager:p2'", (row["id"],))[0][0] == "ok"
    f.close()


# ---------------------------------------------------------------- enqueue and a person's mark


def test_enqueue_queues_only_what_a_person_listed(site_db, tmp_path):
    c, net = site_db
    c.frontier.close()
    net.sink.close()
    lst = tmp_path / "found.txt"
    lst.write_text(f"# 人看过\n{W}/only-cc.html\tR3a\t{SITE}\nhttps://sso.nknu.edu.tw/login.aspx\n", encoding="utf-8")
    got = reconcile.enqueue(tmp_path, reconcile.read_list(lst), plan=PLAN)
    assert got == [(f"{W}/only-cc.html", "queued"), ("https://sso.nknu.edu.tw/login.aspx", "out:login")]
    fr = Frontier(tmp_path / "frontier.db")
    new = fr.row(f"{W}/only-cc.html")
    assert (new["state"], new["found_via"], new["owner_site"]) == ("pending", "reconcile", SITE)
    assert fr.row("https://sso.nknu.edu.tw/login.aspx") is None
    fr.close()
    c.frontier = Frontier(tmp_path / "frontier.db")  # the fixture closes what it opened


def test_review_mark_takes_only_a_markable_result_with_a_note_and_never_during_a_run(arch):
    gone = arch.page("/gone.html", "", status=404)
    arch.f.add(f"{W}/wait.html", host=SITE, site=SITE, unit=None, tier=1, kind="page", depth=1, found_via="link",
               found_from=None, owner_site=SITE, kind0="page")
    wait = arch.f.row(f"{W}/wait.html")["id"]
    root = arch.root
    with pytest.raises(ValueError, match="note"):
        reconcile.review_mark(root, gone, note=" ")
    with pytest.raises(ValueError, match="only mark"):
        reconcile.review_mark(root, gone, note="看过", mark="ok")
    with pytest.raises(ValueError, match="not the school's"):
        reconcile.review_mark(root, wait, note="看过")
    lock = hold_run_lock(root)
    with pytest.raises(AlreadyRunning):
        reconcile.review_mark(root, gone, note="看过：学校删了")
    lock.close()
    assert [r["id"] for r in reconcile.markable_rows(arch.f, SITE)] == [gone]
    assert reconcile.review_mark(root, gone, note="看过：学校删了") == "gone"
    assert arch.f.q("SELECT result FROM url_result WHERE id=?", (gone,))[0][0] == outcome.SCHOOL_SIDE
    assert json.loads(arch.f.q("SELECT message FROM event WHERE code='review_mark'")[0][0])["result"] == "gone"


# ---------------------------------------------------------------- small pieces


def test_quote_pieces_and_the_golden_file(tmp_path):
    assert reconcile.quote_pieces("和平愛閱館：08:15-23:29　燕巢：09:15；短") == ["和平愛閱館:08:15-23:29", "燕巢:09:15"]
    gold = _golden(tmp_path / "q.json", [(HOME, "首頁")])
    assert reconcile.load_golden(gold) == [reconcile.Answer("Q1", HOME, "首頁")]
    assert reconcile.load_golden(reconcile.GOLDEN)  # the real 25 questions' file reads


def test_library_scripts_are_not_read():
    for path in ("/js/jquery-3.6.0.min.js", "/assets/bootstrap.bundle.min.js", "/lib/swiper.min.js"):
        assert reconcile.LIBRARY_JS.search(path), path
    for path in ("/js/site.js", "/js/main.js", "/scripts/news.js"):
        assert not reconcile.LIBRARY_JS.search(path), path


def test_profiles_decide_nothing_for_the_test_site():
    assert profiles.profile_for_site(SITE) is None  # what the tests above rely on: every writing is unknown


def test_crawler_module_does_not_need_reconcile_at_import():
    assert crawl_mod.Crawler is Crawler


def test_rejudge_sets_the_thin_marks_again(arch):
    for i in range(5):
        arch.page(f"/p{i}.html", _tpl(f"<p>第{i}篇：{BODY}</p>"))
    short = arch.page("/short.html", _tpl("<p>短</p>"))
    arch.close()
    got = reconcile.rejudge(arch.root, [SITE], plan=PLAN)
    assert got.pages == 6 and got.thin_set == 1
    fr = Frontier(arch.root / "frontier.db")
    assert fr.q("SELECT result FROM url_result WHERE id=?", (short,))[0][0] == "thin"
    fr.close()
    arch.f = Frontier(arch.root / "frontier.db")  # the fixture closes what it opened
    arch.sink._fh = open(arch.sink.path, "ab", buffering=0)  # noqa: SIM115


def test_r7_waits_for_a_school_script_not_got_and_a_library_only_page_ignores(tmp_path):
    routes = {"/js/site.js": lambda u: snap(u, 404), **{k: v for k, v in ROUTES.items() if k != "/js/site.js"}}
    c, net = crawl(tmp_path, routes)
    fr = c.frontier
    res = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    assert {s.key: s.decision for s in res.suggestions}["js_link:toggleMenu"] == "unknown"
    assert res.checks["R7"].status == WARN
    net.sink.close()
    fr.close()
    lib = tmp_path / "lib"
    html = HOME_HTML.replace("/js/site.js", "/js/jquery.min.js")
    routes = {"/js/jquery.min.js": lambda u: snap(u, body=b"/*! jQuery */", headers={"content-type": "text/javascript"}),
              f"{SITE}/": lambda u: snap(u, html=html)}
    c, net = crawl(lib, routes)
    res = reconcile.reconcile_sites(c.frontier, lib, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None)).sites[0]
    got = {s.key: (s.decision, s.why) for s in res.suggestions}
    assert got["js_link:toggleMenu"][0] == "ignore" and "没有载入任何外面的 .js" in got["js_link:toggleMenu"][1]
    net.sink.close()
    c.frontier.close()


def test_the_report_names_no_link_text(site_db, tmp_path):
    c, _net = site_db
    fr = c.frontier
    fr.x("DELETE FROM url WHERE url=?", (f"{W}/contact.html",))
    fr.x("DELETE FROM link WHERE dst=?", (f"{W}/contact.html",))
    got = reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="t", plan=PLAN,
                                    sources=Sources(cc_dir=None, golden=None))
    text = got.text()
    assert f"{W}/contact.html" in text and "聯絡我們" not in text  # addresses and reasons, never a page's words
    assert "R7 给平台档案的决定建议" in text and "js_link:toggleMenu" in text
