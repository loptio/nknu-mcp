"""Google Sites' own body pictures (sites.google.com/sitesv-images-rt/…), design §23 B, 【用户同意 Claude 的建议】
(10-27, 「可以，听你的」): round 1 fetches them right after their page, one at a time on the page's own queue after the
usual gap, with the Referer the page's referrer policy gives (its <meta name="referrer" content="origin">: a browser
sends https://sites.google.com/), the crawler's identity otherwise unchanged; their addresses change with every
reading of the page (about 30 s), so a picture is known by where it sits (res_key gsimg:<page>/<n>/<element id>), a
page whose pictures start to answer 403 halfway is read again (twice at most), and a site whose first two pictures
tried after its page was read again both answer 403 sends no more of them: they wait for round two's browser
(round2:google_sites_image), counted by site in the ledger, in the dashboard's 下一轮 and on the site and the page.

The page below is the counselling centre's home (archive-pilot url id 9) cut down to what these tests need: its meta
referrer, the site logo in the nav and the header (twice, one address), body pictures inside the blocks Google Sites
gives an id (h.<hex>_<n>), a section background. Every address and id is made up; no text of the page is kept. All
offline: a fake network that answers by what each request carries, a fake clock."""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import replace
from datetime import UTC, datetime

from nknu.archive import links, monitor, outcome, progress
from nknu.archive.crawler import Crawler
from nknu.archive.warc import WarcSink
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap

SITE = "sites.google.com/mail.nknu.edu.tw/counseling-center"
HOME = f"https://{SITE}"
PAGE2 = f"{HOME}/%E8%81%AF%E7%B5%A1%E6%88%91%E5%80%91"
PIC = "https://sites.google.com/sitesv-images-rt/"
LH = "https://lh3-rt.googleusercontent.com/sitesv/"
ORIGIN = "https://sites.google.com/"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00made-up picture for a test"
GQ = "google"


def page_html(reading: int, n: int = 3, referrer: str | None = "origin", prefix: str = "h", links_to: str = "") -> str:
    """The cut-down page as reading `reading` writes it: n body pictures, each address carrying the reading's number."""
    meta = f'<meta name="referrer" content="{referrer}">' if referrer is not None else ""
    logo = f"{PIC}AMxu72{prefix}{reading}hdr=w16383"
    body = "".join(f'<div id="h.7032808899cb38bc_{i * 6}" class="hJDwNd"><div class="t3iYD">'
                   f'<img src="{PIC}AMxu72{prefix}{reading}p{i}=w1280" class="CENy8b" role="img"></div></div>'
                   for i in range(1, n + 1))
    return (f"<html><head>{meta}<title>學生諮商中心</title></head><body>"
            f'<header><nav id="yuynLe"><a href="/mail.nknu.edu.tw/counseling-center/首頁">'
            f'<img src="{logo}" aria-label="Site home"></a></nav>'
            f'<div class="VLoccc"><a href="/mail.nknu.edu.tw/counseling-center/首頁"><img src="{logo}"></a></div>'
            f"</header>"
            f'<div jsname="ZBtY8b"><section id="h.INITIAL_GRID.6ackxwxhagx8"><div class="IFuOkc" '
            f'style="background-image: url({PIC}AMxu72{prefix}{reading}bg=w16383); background-size: cover;"></div>'
            f"</section>{body}<p>{'諮商服務說明' * 20}</p>{links_to}</div></body></html>")


class Site:
    """A fake Google Site and its picture service. reading: how many times each page was read; good[r]: how many
    pictures of reading r are served before its addresses expire (403), None for all of them; referer: the Referer the
    service wants (None: any). Every request is kept with its method, address and the headers the crawler set."""

    def __init__(self, clock, *, n=3, referrer="origin", good=None, referer=ORIGIN, deny_all=False, twin=False,
                 status=None):
        self.clock, self.n, self.referrer, self.referer, self.deny_all = clock, n, referrer, referer, deny_all
        self.twin, self.status = twin, status  # twin: the next page shows the home's addresses; status: every picture's
        self.good = good if good is not None else {}
        self.reading: dict[str, int] = {}
        self.served: dict[tuple[str, int], int] = {}
        self.seen: list[tuple[str, str, dict]] = []
        self.sink: WarcSink | None = None

    def __call__(self, _warc):
        return self

    def reset_host(self, host):
        pass

    def close(self):
        pass

    def _page(self, url: str, prefix: str, links_to: str = ""):
        r = self.reading[url] = self.reading.get(url, 0) + 1
        return snap(url, html=page_html(r, self.n, self.referrer, prefix, links_to),
                    headers={"referrer-policy": "origin"} if self.referrer == "origin" else {})

    def _picture(self, url: str, headers: dict):
        if self.status is not None:
            return snap(url, self.status, body=b"<html>busy</html>")
        if self.deny_all or (self.referer is not None and headers.get("Referer") != self.referer):
            return snap(url, 403, body=b"<html>Your client does not have permission</html>")
        token = url[len(PIC):]
        m = re.match(r"AMxu72([a-z])(\d+)", token)
        key = (m[1], int(m[2]))
        limit = self.good.get(key)
        if limit is not None and self.served.get(key, 0) >= limit:
            return snap(url, 403, body=b"<html>Your client does not have permission</html>")
        self.served[key] = self.served.get(key, 0) + 1
        return snap(url, 302, headers={"location": LH + token})

    def fetch(self, target, rate, max_bytes=None, pass_statuses=None):
        self.seen.append((target.method, target.url, dict(target.headers)))
        self.clock.t += 1.0
        url = target.url
        if url.endswith("/robots.txt") or "/nknu-archive-probe-" in url:
            got = snap(url, 404)
        elif url == HOME:
            got = self._page(url, "h", f'<a href="{PAGE2}">聯絡我們</a>')
        elif url == PAGE2:
            got = self._page(url, "c") if not self.twin else snap(
                url, html=page_html(self.reading.get(HOME, 1), self.n, self.referrer))  # the home's last reading
        elif url.startswith(PIC):
            got = self._picture(url, dict(target.headers))
        elif url.startswith(LH):
            ok = self.referer is None or target.headers.get("Referer") == self.referer
            got = snap(url, body=JPEG, headers={"content-type": "image/jpeg"}) if ok and not self.deny_all \
                else snap(url, 403, body=b"<html>forbidden</html>")
        else:
            got = snap(url, 404)
        if self.sink is not None:
            offset, length = self.sink.write(got, dict(target.headers))
            got = replace(got, warc_offset=offset, warc_len=length)
        return got

    def asked(self, start: str) -> list[str]:
        return [u for _m, u, _h in self.seen if u.startswith(start)]


def make(tmp_path, net: Site, queues=None):
    queues = {"sites.google.com": GQ, "lh3-rt.googleusercontent.com": GQ, **(queues or {})}
    return Crawler(tmp_path, plan=OpenPlan(), clock=net.clock, sleep=net.clock.sleep, client_factory=net,
                   resolver=lambda h: (queues.get(h, f"ip:{h}"), None, h), netcheck=lambda: True,
                   disk_free=lambda path: 10**12, campus_url=None)


def work(crawler, net, queue=GQ, rounds=80):
    sink = WarcSink(crawler.root / "warc", "q", 1, datetime(2026, 10, 27, tzinfo=UTC))
    net.sink = sink
    for _ in range(rounds):
        row = crawler._next(queue)
        if row is None:
            return
        crawler._handle(queue, net, sink, row)


def entries(f, url):
    return {r["res_key"]: r for r in f.q("SELECT pr.* FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE u.url=? "
                                         "AND pr.res_key LIKE 'gsimg:%' ORDER BY pr.res_key", (url,))}


def ok_type(f, *urls):
    for url in urls:
        f.x("UPDATE page_type SET verdict='ok', source='manual' WHERE key=(SELECT page_type FROM url WHERE url=?)",
            (url,))


# ---------------------------------------------------------------- what a page says (links)


def test_a_runtime_picture_is_a_body_picture_by_the_ordinary_rules():
    body = links.Image(f"{PIC}AMxu72h1p1=w1280", "src", "unknown", "", node_id="h.7032808899cb38bc_6")
    assert links.image_need(body, HOME) == ("must", "")
    logo = links.Image(f"{PIC}AMxu72h1hdr=w16383", "src", "chrome", "", node_id="yuynLe")
    assert links.image_need(logo, HOME) == ("no", "chrome")  # a layout area on a site without a profile, as any


def test_each_runtime_picture_is_known_by_where_it_sits_the_same_on_every_reading():
    first = links.extract(page_html(1), HOME)
    again = links.extract(page_html(2), HOME)
    k1, k2 = links.google_sites_keys(first, 9), links.google_sites_keys(again, 9)
    assert len(k1) == 5  # the logo written twice is one picture; three body pictures; the background
    assert list(k1.values()) == list(k2.values())  # new addresses, the same keys
    assert set(k1) != set(k2)
    assert k1[f"{PIC}AMxu72h1hdr=w16383"] == "gsimg:9/1/yuynLe"
    assert k1[f"{PIC}AMxu72h1p2=w1280"] == "gsimg:9/3/h.7032808899cb38bc_12"
    assert k1[f"{PIC}AMxu72h1bg=w16383"] == "gsimg:9/5/h.INITIAL_GRID.6ackxwxhagx8"
    assert links.google_sites_keys(links.extract("<p>no picture</p>", HOME), 9) == {}


def test_the_pages_referrer_policy_is_read_from_its_meta_and_header():
    assert links.extract(page_html(1), HOME).policy == "origin"
    assert links.extract(page_html(1, referrer=None), HOME).policy == "strict-origin-when-cross-origin"
    assert links.extract(page_html(1, referrer="never"), HOME).policy == "no-referrer"  # the HTML's legacy word
    assert links.extract(page_html(1, referrer="bogus"), HOME).policy == "strict-origin-when-cross-origin"
    assert links.referrer_policy("no-referrer, origin", None) == "origin"  # the header's last valid token
    assert links.referrer_policy("unsafe-url", "origin") == "origin"  # the meta comes later and wins
    assert links.referrer_policy("same-origin", "nonsense") == "same-origin"  # an invalid meta is ignored


def test_the_referer_is_what_the_w3c_algorithm_gives():
    page = f"{HOME}/x?a=1#top"
    pic, hop, plain = f"{PIC}AMxu72h1p1=w1280", f"{LH}AMxu72h1p1=w1280", "http://example.org/a.png"
    r = links.referrer_for
    assert r("origin", page, pic) == ORIGIN and r("origin", page, hop) == ORIGIN
    assert r("no-referrer", page, pic) is None and r("no-referrer", page, hop) is None
    assert r("strict-origin-when-cross-origin", page, pic) == f"{HOME}/x?a=1"  # same origin: the page, no fragment
    assert r("strict-origin-when-cross-origin", page, hop) == ORIGIN
    assert r("strict-origin-when-cross-origin", page, plain) is None  # https to http: nothing
    assert r("same-origin", page, pic) == f"{HOME}/x?a=1" and r("same-origin", page, hop) is None
    assert r("strict-origin", page, hop) == ORIGIN and r("strict-origin", page, plain) is None
    assert r("origin-when-cross-origin", page, hop) == ORIGIN
    assert r("no-referrer-when-downgrade", page, hop) == f"{HOME}/x?a=1"
    assert r("unsafe-url", page, plain) == f"{HOME}/x?a=1"
    assert r("weird", page, hop) == ORIGIN  # an unknown policy is the default


# ---------------------------------------------------------------- B1: right after the page, with its Referer


def test_runtime_pictures_go_right_after_their_page_with_the_pages_referer_and_are_got(tmp_path):
    clock = Clock()
    net = Site(clock)
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    order = [u for _m, u, _h in net.seen if "robots.txt" not in u and "nknu-archive-probe" not in u]
    home_at, page2_at = order.index(HOME), order.index(PAGE2)
    pictures = [i for i, u in enumerate(order) if u.startswith((PIC, LH)) and "AMxu72h" in u]
    assert len(pictures) == 8  # 4 pictures the page needs (3 body, 1 background), each a 302 and its hop
    assert home_at < min(pictures) and max(pictures) < page2_at  # the home's pictures before the next page
    for _m, u, h in net.seen:
        if u.startswith((PIC, LH)):
            assert h == {"Referer": ORIGIN}, (u, h)  # the Referer only: no Accept, no Sec-Fetch-*, no other UA
        else:
            assert "Referer" not in h, u
    assert not any("hdr=" in u for u in net.asked(PIC))  # the logo in the nav and header: a layout area, as any
    got = entries(f, HOME)
    assert len(got) == 5 and {r["how"] for r in got.values()} == {links.GSIMG.rstrip(":")}
    assert {k: r["need"] for k, r in got.items()} == {
        k: ("no" if k.endswith("/yuynLe") else "must") for k in got}
    res = {r["res_key"]: r["result"] for r in f.q("SELECT res_key, result FROM page_resource WHERE page_id=(SELECT id "
                                                     "FROM url WHERE url=?) AND res_key LIKE 'gsimg:%'", (HOME,))}
    assert set(res.values()) == {"ok"} and len(res) == 4
    ok_type(f, HOME)
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (HOME,))[0][0]
    assert level == "complete"
    reqs = f.q("SELECT url, started, ended FROM request ORDER BY started")
    for a, b in itertools.pairwise(reqs):
        assert b["started"] - a["ended"] >= 2.0  # the usual gap after every answer, on one connection


def test_the_hop_to_the_picture_server_is_first_on_its_own_queue_with_the_same_referer(tmp_path):
    clock = Clock()
    net = Site(clock)
    crawler = make(tmp_path, net, queues={"lh3-rt.googleusercontent.com": "ip:lh"})
    seed(crawler, HOME)
    f = crawler.frontier
    # a row of that server's queue that would go first by its tier and kind (a list of the most important sites)
    f.add(f"{LH}other.jpg", host="lh3-rt.googleusercontent.com", site="x", unit=None, tier=0, kind="image", depth=0,
          found_via="seed", found_from=None, owner_site=SITE, prio=0, kind0="image")
    work(crawler, net, GQ, rounds=4)  # robots, probe, the home, the first picture's 302
    hop = f.q("SELECT * FROM url WHERE url LIKE ? AND found_via='redirect' ORDER BY id LIMIT 1", (f"{LH}AMxu72h%",))
    assert hop and hop[0]["prio"] < 0 and json.loads(hop[0]["referrer"])["policy"] == "origin"
    first = crawler._next("ip:lh")
    assert first["id"] == hop[0]["id"]
    crawler.frontier.x("UPDATE url SET state='pending' WHERE id=?", (first["id"],))
    work(crawler, net, "ip:lh", rounds=3)
    sent = [(u, h) for _m, u, h in net.seen if u == hop[0]["url"]]
    assert sent == [(hop[0]["url"], {"Referer": ORIGIN})]


def test_a_page_that_writes_no_referrer_sends_none_and_one_that_writes_nothing_gets_the_default(tmp_path):
    clock = Clock()
    net = Site(clock, referrer="no-referrer", referer=None)
    crawler = make(tmp_path / "a", net)
    seed(crawler, HOME)
    work(crawler, net)
    asked = [h for _m, u, h in net.seen if u.startswith((PIC, LH))]
    assert asked and all(h == {} for h in asked)
    clock = Clock()
    net = Site(clock, referrer=None, referer=None)
    crawler = make(tmp_path / "b", net)
    seed(crawler, HOME)
    work(crawler, net)
    pic = {u: h for _m, u, h in net.seen if u.startswith(PIC)}
    hop = [h for _m, u, h in net.seen if u.startswith(LH)]
    # strict-origin-when-cross-origin: same origin, the page's own address (each page's for its pictures)
    assert pic and all(h == {"Referer": HOME if "AMxu72h" in u else PAGE2} for u, h in pic.items())
    assert hop and all(h == {"Referer": ORIGIN} for h in hop)  # cross origin: the origin


# ---------------------------------------------------------------- B2: addresses that expire halfway


def test_pictures_that_expire_halfway_read_the_page_again_once_and_keep_one_row_each(tmp_path):
    clock = Clock()
    net = Site(clock, good={("h", 1): 1})  # the first reading's addresses: one picture, then 403
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    assert net.reading[HOME] == 2  # read again once
    first = [u for u in net.asked(PIC) if "AMxu72h1" in u]
    second = [u for u in net.asked(PIC) if "AMxu72h2" in u]
    assert first == [f"{PIC}AMxu72h1p1=w1280", f"{PIC}AMxu72h1p2=w1280"]  # p3 and bg of reading 1: never sent
    assert f"{PIC}AMxu72h2p1=w1280" not in second  # got already: its row stands
    assert sorted(second) == sorted([f"{PIC}AMxu72h2p2=w1280", f"{PIC}AMxu72h2p3=w1280", f"{PIC}AMxu72h2bg=w16383"])
    got = entries(f, HOME)
    assert len(got) == 5  # no picture twice
    res = {r["res_key"]: r["result"] for r in f.q("SELECT res_key, result FROM page_resource WHERE page_id=(SELECT id "
                                                     "FROM url WHERE url=?) AND res_key LIKE 'gsimg:%'", (HOME,))}
    assert set(res.values()) == {"ok"}
    stale = {r["url"]: r for r in f.q("SELECT * FROM url_result WHERE reason='gsimg_stale'")}
    assert set(stale) == {f"{PIC}AMxu72h1p2=w1280", f"{PIC}AMxu72h1p3=w1280", f"{PIC}AMxu72h1bg=w16383"}
    assert stale[f"{PIC}AMxu72h1p2=w1280"]["status"] == 403  # its answer kept, as answered
    assert stale[f"{PIC}AMxu72h1p2=w1280"]["final_url"] == f"{PIC}AMxu72h2p2=w1280"
    assert stale[f"{PIC}AMxu72h1p3=w1280"]["state"] == "skipped"
    assert {r["result"] for r in stale.values()} == {"redirect"}  # replaced by the same picture's new address
    ok_type(f, HOME, PAGE2)
    level = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (HOME,))[0][0]
    assert level == "complete"
    assert monitor.orphans(f).get(SITE, []) == []  # nothing left over that no page uses: never 连接失败, never red
    assert f.q("SELECT count(*) FROM url WHERE prio < 0 AND kind='page'")[0][0] == 0  # the page's own place back


def test_a_page_reads_again_at_most_twice_and_what_still_expires_waits_for_round_two(tmp_path):
    clock = Clock()
    net = Site(clock, n=6, good={("h", 1): 1, ("h", 2): 1, ("h", 3): 1})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=120)
    f = crawler.frontier
    assert net.reading[HOME] == 3  # the first reading and two more
    got = entries(f, HOME)
    needs = sorted(r["need"] for k, r in got.items() if not k.endswith("/yuynLe"))
    assert needs.count("must") == 3 and needs.count("round2") == 4  # one picture per reading, the rest round two
    later = [r for r in got.values() if r["need"] == "round2"]
    assert all(r["why"] == links.GOOGLE_SITES_ROUND2 and r["res_id"] is None for r in later)
    assert f.q("SELECT count(*) FROM url_result WHERE url LIKE ? AND result IN ('refused', 'failed')",
               (f"{PIC}%",))[0][0] == 0
    # the third reading: its first new picture got, the next refused, and nothing more sent on its expired addresses
    assert [u for u in net.asked(PIC) if "AMxu72h3" in u] == [f"{PIC}AMxu72h3p3=w1280", f"{PIC}AMxu72h3p4=w1280"]


def test_two_pages_that_show_one_address_share_its_row_and_its_outcome(tmp_path):
    """Two readings seconds apart write the same addresses (archive-pilot: the counselling centre's home as
    /counseling-center and /counseling-center/, 25 of 25 the same): the second page's pictures are the first's rows,
    fetched once, with whatever they came to; a failure stays a failure (never moved to round two by the other page)."""
    clock = Clock()
    net = Site(clock, twin=True)
    crawler = make(tmp_path / "ok", net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    asked = net.asked(PIC)
    assert len(asked) == len(set(asked)) == 4
    home, twin = entries(f, HOME), entries(f, PAGE2)
    assert {r["res_id"] for r in home.values()} == {r["res_id"] for r in twin.values()}
    res = {r[0] for r in f.q("SELECT DISTINCT result FROM page_resource WHERE res_key LIKE 'gsimg:%'")}
    assert res == {"ok"}
    clock = Clock()
    net = Site(clock, twin=True, status=500)
    crawler = make(tmp_path / "bad", net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    twin = entries(f, PAGE2)
    assert {r["need"] for k, r in twin.items() if not k.endswith("/yuynLe")} == {"must"}
    res = {r[0] for r in f.q("SELECT DISTINCT result FROM page_resource WHERE res_key LIKE 'gsimg:%'")}
    assert res == {"server_error"}  # the school's server's answer, 连接失败 as any: never 下一轮
    assert f.q("SELECT count(*) FROM url WHERE state='later'")[0][0] == 0
    # the home read again twice, its last addresses left for round two: the twin page shows those very addresses and
    # asks none of them again (an address is sent once, whichever page shows it)
    clock = Clock()
    net = Site(clock, twin=True, good={("h", 1): 1, ("h", 2): 0, ("h", 3): 0})
    crawler = make(tmp_path / "later", net)
    seed(crawler, HOME)
    work(crawler, net, rounds=120)
    f = crawler.frontier
    asked = net.asked(PIC)
    assert len(asked) == len(set(asked)), [u for u in asked if asked.count(u) > 1]
    twin = entries(f, PAGE2)
    left = [k for k, r in entries(f, HOME).items() if r["need"] == "round2"]
    assert left and all(twin[k.replace(f"gsimg:{f.row(HOME)['id']}/", f"gsimg:{f.row(PAGE2)['id']}/")]["need"]
                        == "round2" for k in left)


# ---------------------------------------------------------------- B3: a site whose pictures never come


def test_two_refusals_after_the_page_was_read_again_leave_the_sites_pictures_for_round_two(tmp_path):
    clock = Clock()
    net = Site(clock, deny_all=True)
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net)
    f = crawler.frontier
    asked = net.asked(PIC)
    assert asked == [f"{PIC}AMxu72h1p1=w1280", f"{PIC}AMxu72h2p1=w1280", f"{PIC}AMxu72h2p2=w1280"]
    assert net.reading[HOME] == 2 and net.reading[PAGE2] == 1
    assert f.meta(f"gsimg_giveup:{SITE}") is not None
    events = f.q("SELECT * FROM event WHERE code='gsimg_giveup'")
    assert len(events) == 1 and "AMxu72h2p1" in events[0]["message"] and "403" in events[0]["message"]
    home, page2 = entries(f, HOME), entries(f, PAGE2)
    for got in (home, page2):
        body = [r for k, r in got.items() if not k.endswith("/yuynLe")]
        assert body and all((r["need"], r["why"]) == ("round2", links.GOOGLE_SITES_ROUND2) for r in body)
    assert not [u for u in asked if "AMxu72c" in u]  # the next page's pictures: no request at all
    # never 连接失败, never a refusal streak (the directory is never parked)
    assert f.q("SELECT count(*) FROM url_result WHERE (url LIKE ? OR url LIKE ?) AND result IN ('refused', 'failed', "
               "'unknown')", (f"{PIC}%", f"{LH}%"))[0][0] == 0
    assert f.q("SELECT count(*) FROM deny")[0][0] == 0
    assert f.q("SELECT count(*) FROM event WHERE code LIKE 'host_denies%'")[0][0] == 0
    # seen: counted by site in the ledger (what verify item 16's known gap read), the dashboard's 下一轮, the site,
    # the page
    n = len([k for k in home if not k.endswith("/yuynLe")]) + len([k for k in page2 if not k.endswith("/yuynLe")])
    by_site = f.q("""SELECT p.owner_site, count(DISTINCT pr.res_key) FROM page_res pr JOIN url p ON p.id = pr.page_id
                     WHERE pr.need = 'round2' AND pr.why = ? GROUP BY p.owner_site""", (links.GOOGLE_SITES_ROUND2,))
    assert [tuple(r) for r in by_site] == [(SITE, n)]
    assert progress.ROUND2_ZH[links.GOOGLE_SITES_ROUND2] == "协作平台正文图片"
    ok_type(f, HOME, PAGE2)
    label = f.q("SELECT label, level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (PAGE2,))[0]
    assert (label["label"], label["level"]) == ("round2_img", "partial")
    assert dict(outcome.LABEL_ORDER)["round2_img"] == "缺图片（下一轮补）"
    assert progress.LABEL_COLOR["round2_img"] == "yellow"
    status = monitor.site_status(f)[SITE]
    assert status.state != monitor.ERROR, status.why
    assert any(f"协作平台正文图片 {n} 张下一轮补" in w for w in status.why), status.why
    # the decision stands across runs: a page read in a later run asks for none of its pictures either
    later = make(tmp_path, net)
    later.frontier.x("UPDATE url SET state='pending', next_at=0 WHERE url=?", (PAGE2,))
    work(later, net)
    assert net.reading[PAGE2] == 2 and not [u for u in net.asked(PIC) if "AMxu72c" in u]


def test_a_site_that_got_a_picture_is_never_given_up_for_a_later_refusal(tmp_path):
    clock = Clock()
    net = Site(clock, good={("h", 1): 1, ("h", 2): 0, ("h", 3): 0})
    crawler = make(tmp_path, net)
    seed(crawler, HOME)
    work(crawler, net, rounds=120)
    f = crawler.frontier
    assert f.meta(f"gsimg_giveup:{SITE}") is None
    assert [u for u in net.asked(PIC) if "AMxu72c" in u]  # the next page's pictures are asked for
