"""Round one's gap ⑫ (docs/round1-gaps-2026-11-08.md; the owner 11-08 19:58 had ⑩⑪⑫ filled in, how each is done
【Claude 的执行办法】): 性别所's pages frame the Wix pages of the unit's own site (the list's offsite_unit entry), where
their content is, and round one left those frames for round two. A round-1 page's frame of a page of a school unit's
own site off campus is that page's part now, fetched with it, one layer (round1._resource): the crawler queues it,
what it uses (its pictures) comes with it, what it links stays round two's; a rejudge brings round one's frames
forward, sending nothing. Made-up hosts and pages, fake networks."""

from __future__ import annotations

from datetime import UTC, datetime

from nknu.archive import reconcile, round1, scope
from nknu.archive.crawler import Crawler
from nknu.archive.frontier import Frontier
from nknu.crawl.warc import WarcSink
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_pilot_fixes import PressNet
from tests.archive.test_postback import World

UNIT = "unit.nknu.edu.tw"  # a round-1 department (made up)
WIX = "unitsite.wixsite.com/unit"  # its own site off campus, as the list names it (offsite_unit, round two)
PAGE = f"https://{UNIT}/recruit.html"
FRAME = "https://unitsite.wixsite.com/unit/recruit"
PICTURE = "https://static.wixstatic.com/media/0a1b2c_0123456789abcdef~mv2.jpg"
LINKED = "https://unitsite.wixsite.com/unit/single-post/a"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
# the department's page: a shell whose content is the Wix page, loaded into its frame by a script (data-src)
SHELL = (f'<html><head><title>招生資訊（測試）</title></head><body><h1>招生資訊</h1><iframe id="embed-wix" '
         f'data-src="{FRAME}" width="100%" height="900"></iframe><script src="script.js"></script></body></html>')
WIX_HTML = (f'<html><head><title>招生資訊 | 測試所</title></head><body><div id="SITE_CONTAINER"><h2>115 學年度招生'
            f'</h2><p>報名日期與簡章（測試）。</p><img src="{PICTURE}" width="400" height="300" alt="海報">'
            f'<a href="{LINKED}">招生公告</a></div></body></html>')


def plan():
    return make_plan(make_site(UNIT, home=f"https://{UNIT}/"),
                     make_site(WIX, rnd="2", why="offsite_unit", home="https://unitsite.wixsite.com/unit"))


def test_the_list_holds_the_entry_this_file_stands_for():
    real = round1.load().site("nknugender.wixsite.com/nknugender")
    assert (real.round, real.why) == ("2", "offsite_unit")


def test_a_frame_of_the_units_own_site_is_fetched_with_its_page(tmp_path):
    clock = Clock()
    net = PressNet(clock, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "/script.js"): snap(f"https://{UNIT}/script.js", body=b"/* loads the frame */",
                                    headers={"content-type": "application/javascript"}),
        ("GET", FRAME): snap(FRAME, html=WIX_HTML),
        ("GET", "static.wixstatic.com"): snap(PICTURE, body=JPEG, headers={"content-type": "image/jpeg"}),
    })
    crawler = Crawler(tmp_path, plan=plan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True,
                      disk_free=lambda path: 10**12, campus_url=None)
    net.routes[("GET", PAGE)] = snap(PAGE, html=SHELL)
    seed(crawler, PAGE)
    sink = WarcSink(tmp_path / "warc", "q", 1, datetime(2026, 11, 9, tzinfo=UTC))
    net.sink = sink
    f = crawler.frontier

    def drain(queue: str) -> None:
        for _ in range(20):
            row = crawler._next(queue)
            if row is None:
                return
            crawler._handle(queue, net, sink, row)

    drain(f"ip:{UNIT}")
    page, frame = f.row(PAGE), f.row(FRAME)
    assert (frame["state"], frame["owner_site"], frame["tier"], frame["leaf"], frame["kind0"]) == (
        "pending", UNIT, page["tier"], 1, "page")
    use = f.q("SELECT need, role FROM page_res WHERE page_id=? AND res_key=?", (page["id"], FRAME))[0]
    assert tuple(use) == ("must", "iframe")
    drain("ip:unitsite.wixsite.com")
    assert f.row(FRAME)["state"] == "done"
    picture, linked = f.row(PICTURE), f.row(LINKED)
    assert (picture["state"], picture["owner_site"], picture["kind0"]) == ("pending", UNIT, "image")
    assert (linked["state"], linked["reason"], linked["owner_site"]) == ("later", "round2:offsite_unit", WIX)
    drain("ip:static.wixstatic.com")
    assert f.row(PICTURE)["state"] == "done"
    # the frame is a page of its own: its picture is its resource, which page_completeness brings to the page
    framed = f.row(FRAME)["id"]
    assert f.q("SELECT result FROM page_resource WHERE page_id=? AND res_key=?", (framed, PICTURE))[0][0] == "ok"
    assert f.q("SELECT verdict FROM link WHERE src=? AND dst=?", (framed, LINKED))[0][0] == "round2:offsite_unit"
    sink.close()
    f.close()


def test_a_rejudge_brings_round_ones_frames_forward_and_sends_nothing(tmp_path):
    w = World(tmp_path)
    row, _snap = w.page(PAGE, UNIT, SHELL, queue=f"ip:{UNIT}")
    # as round one's rules placed the frame: round two's, off campus, one layer; the page's entry waiting for it
    w.f.add(FRAME, host="unitsite.wixsite.com", site=scope.site_key(FRAME), unit=None, tier=2, kind="page",
            depth=1, found_via="link", found_from=row["id"], state="later", reason="round2:offsite",
            owner_site=scope.site_key(FRAME), leaf=1, kind0="page")
    w.f.add_page_res(row["id"], [{"res_key": FRAME, "how": "get", "role": "iframe", "need": "round2",
                                  "res_id": w.f.row(FRAME)["id"]}])
    w.sink.close()
    w.f.close()
    got = reconcile.rejudge(tmp_path, [UNIT], plan=plan())  # no client and no name lookup: nothing can be sent
    f = Frontier(tmp_path / "frontier.db")
    assert got.pages == 1 and [r[0] for r in f.q("SELECT url FROM url WHERE found_via='rejudge'")] == [
        f"https://{UNIT}/script.js"]  # the shell's script, new to this made-up archive; the frame is no new row
    frame = f.row(FRAME)
    assert (frame["state"], frame["reason"], frame["owner_site"], frame["tier"], frame["leaf"]) == (
        "pending", None, UNIT, row["tier"], 1)
    use = f.q("SELECT need FROM page_res WHERE page_id=? AND res_key=?", (row["id"], FRAME))[0][0]
    assert use == "must"
    assert not f.q("SELECT 1 FROM request")
    f.close()
