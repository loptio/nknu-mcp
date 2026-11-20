"""Thin pages, what a page's own scripts say (R7), rejudge, a person's list of addresses and a person's mark (round-1
design §18.2a, §10.1 thin, §10.2.1; §16 test_reconcile). The reconciliation batch this file was named for (R1–R6,
V13, V14, its report) went with the judgment-layer cut of 11-19, and its tests with it. Offline: synthetic sites
crawled on the tests' fake network into a real WARC, and a made-up old archive. Titles, text and file names are made
up; no person's name."""

from __future__ import annotations

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


def _r7(fr, root: Path, url: str) -> reconcile.R7Page:
    """R7's reading of one stored page (what a rejudge writes of it comes from this; nothing is written here)."""
    arc = reconcile.Archive(root)
    row = fr.row(url)
    html = arc.text(row["warc"], row["warc_offset"])
    page = reconcile._page_read(row, html, url)
    return reconcile.r7_page(fr, row, html, url, page, reconcile.ScriptCache(fr, arc))


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


# ---------------------------------------------------------------- rejudge and R7 on a crawled site


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


def test_rejudge_reads_a_page_with_every_kind_of_part_back_to_the_same_page_res(tmp_path):
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
    # rejudge reads it back to the same page_res (R7 has nothing to add: no school script)
    fr.close()
    net.sink.close()
    got = reconcile.rejudge(tmp_path, [SITE], plan=PLAN)
    assert got.pages >= 2 and not got.unread and got.r7_rows == 0  # home and the frame page
    fr = Frontier(tmp_path / "frontier.db")
    again = _keys(fr, HOME)
    assert {k: (r["need"], r["why"], r["res_id"]) for k, r in again.items()} == \
        {k: (r["need"], r["why"], r["res_id"]) for k, r in keys.items()}
    fr.close()


def test_r7_reads_the_schools_js_and_writes_what_it_says(tmp_path):
    c, net = crawl(tmp_path)
    fr = c.frontier
    before = _keys(fr, HOME)
    assert before["js_link:toggleMenu"]["why"] == "unhandled:unknown" and before["js_link:"]["need"] == "must"
    assert not [k for k, r in before.items() if (r["zone"] or "").startswith(reconcile.R7_ZONE)]  # no run writes R7
    # what the school's script says of the page's two writings (both answered): toggleMenu is read and loads
    # nothing; the bare javascript: link is bound to something that loads (a click the page alone does not show)
    read = _r7(fr, tmp_path, HOME)
    assert read.read == [f"{W}/js/site.js"] and not read.missing
    assert read.gone == {"js_link:toggleMenu", "js_link:"} and read.still == set()
    assert "toggleMenu" not in read.loads and read.bound_loads
    fr.close()
    net.sink.close()
    got = reconcile.rejudge(tmp_path, [SITE], plan=PLAN)
    assert got.r7_rows == 1
    fr = Frontier(tmp_path / "frontier.db")
    after = _keys(fr, HOME)
    bound = [k for k, r in after.items() if (r["zone"] or "").startswith(reconcile.R7_ZONE)]
    assert bound == ["click:.cat[0]"]
    assert (after["click:.cat[0]"]["need"], after["click:.cat[0]"]["why"]) == ("round2", "round2:click")
    _ok_types(fr)
    level = fr.q("SELECT level, reasons FROM page_completeness WHERE id=?", (_id(fr, HOME),))[0]
    assert level[0] == "partial" and "click" in level[1]  # what only the school's script loads is no longer unseen
    fr.close()
    # a second rejudge writes the same rows again (never twice)
    reconcile.rejudge(tmp_path, [SITE], plan=PLAN)
    fr = Frontier(tmp_path / "frontier.db")
    assert [k for k, r in _keys(fr, HOME).items() if (r["zone"] or "").startswith("r7:")] == ["click:.cat[0]"]
    fr.close()


# ---------------------------------------------------------------- the commands' guards


def test_the_old_archive_is_never_written(tmp_path):
    old = _old(tmp_path / "old", [(HOME, SITE, "done", 200)])
    before = hashlib.sha256((old / "frontier.db").read_bytes()).hexdigest()
    for call in (lambda: reconcile.rejudge(old, [SITE], plan=PLAN),
                 lambda: reconcile.enqueue(old, [HOME], plan=PLAN),
                 lambda: reconcile.review_mark(old, 1, note="x")):
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


def test_r7_names_a_school_script_not_got_and_reads_no_library(tmp_path):
    routes = {"/js/site.js": lambda u: snap(u, 404), **{k: v for k, v in ROUTES.items() if k != "/js/site.js"}}
    c, net = crawl(tmp_path, routes)
    fr = c.frontier
    got = _r7(fr, tmp_path, HOME)  # the school's script is not got (yet): named, nothing read, nothing answered
    assert got.missing == [f"{W}/js/site.js"] and got.read == [] and got.entries == []
    assert got.gone == set() and got.still == {"js_link:toggleMenu", "js_link:"}
    net.sink.close()
    fr.close()
    lib = tmp_path / "lib"
    html = HOME_HTML.replace("/js/site.js", "/js/jquery.min.js")
    routes = {"/js/jquery.min.js": lambda u: snap(u, body=b"/*! jQuery */", headers={"content-type": "text/javascript"}),
              f"{SITE}/": lambda u: snap(u, html=html)}
    c, net = crawl(lib, routes)
    got = _r7(c.frontier, lib, HOME)  # a library is not the page's own code: not read, and no script is left unread
    assert got.libraries == [f"{W}/js/jquery.min.js"] and got.read == [] and got.missing == []
    assert not got.other_js and got.entries == [] and got.still == {"js_link:toggleMenu", "js_link:"}
    net.sink.close()
    c.frontier.close()
