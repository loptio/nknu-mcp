"""Round one's gaps ① to ④ (docs/round1-gaps-2026-11-08.md; the owner 11-08 asked for the fill-in of ①～⑥ and
⑩⑪⑫, how each is done 【Claude 的执行办法】), each test written before its fix:

① a download press answered 302 to a file on the press's own host (WebIdx's notice files: /WebServices/
   ajaxDownloadFileCourse.aspx) is that file's address, queued as the list page's attachment, never a lapsed session;
   the presses round one took for one are adopted by a rejudge, nothing sent; every other redirect stays a failed
   press (§23.9 A2);
② ③ a page is read by the platform its own address is written on (a /site/ key's WebIdx 书表 pages, 图资处's WebIdx
   MoreNews pages), the site it counts for keeping every key and count (row keys, action.site, listwalk.site, the
   postback table); rejudge decides the same way;
④ the 学务处 osa event attachments and the 法规汇编 「檔案下載」 buttons are pressed (two platform files), and a hidden
   frame the press filled names the file;
and `nknu archive retry unpressed` puts back the pages whose buttons today's platform file presses and nobody pressed.
All offline: fake networks and clocks, made-up pages (tests/archive/fixtures_1108, tests/fixtures/archive_postback),
no person's name."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import export, links, postback, profiles, reconcile, scope
from nknu.archive.crawler import Crawler, hold_run_lock
from nknu.archive.frontier import Frontier
from nknu.archive.postback import REDO_KEY, UNPRESSED, Chains, read_page
from tests.archive.plans import OpenPlan
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_pilot_fixes import PDF, SSO_Q, PressNet, work
from tests.archive.test_postback import MORE, World, fx

FIX = Path(__file__).resolve().parent / "fixtures_1108"
PDF_NAMED = {"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="a.pdf"'}
# where WebIdx's notice file buttons send the browser (round one: 302, every one; made-up path and name)
FILE_SVC = "/WebServices/ajaxDownloadFileCourse.aspx?action=download&path=%5CWebPage%5Cnews%5C3%5C1%23a.pdf%7Ca.pdf"
FILE_URL = scope.canonical(FILE_SVC, MORE)
WEB3 = "sso.nknu.edu.tw/web/3"
# a /site/ key whose seed redirects to a WebIdx 书表 page (gap ②)
OWNER = "sso.nknu.edu.tw/site/999001"
CLV2_HOME = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=999001"
ITEMS = scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=37&menuid=1")
WEB37 = "sso.nknu.edu.tw/web/37"
DL1, DL2 = "ucAllTypeList$rptItems$ctl01$lnkDownloadFile", "ucAllTypeList$rptItems$ctl02$lnkDownloadFile"
OSA = "https://sso.nknu.edu.tw/osa/hesp/eventlist.aspx?event=7"
REG = "https://sso.nknu.edu.tw/regulations/default.aspx?cpara=TESTPARA1"


def fx8(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# the second row's notice: its own title, no attachment
SECOND = re.sub(r'<div class="detail-attachments">.*?</div>', "",
                fx("webidx_detail.html").replace("模範人員獲獎名單（115年）", "勞資會議代表選舉公告"), flags=re.DOTALL)


def make(root: Path, routes, plan=None):
    clock = Clock()
    net = PressNet(clock, routes)
    crawler = Crawler(root, plan=plan or OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    return crawler, net, clock


def press_to(location: str, detail: str | None = None):
    """WebIdx's answers to a MoreNews page's presses: the first row's notice (with one file button, or `detail`), the
    second's (no file), and every file button sent elsewhere by a 302 to `location`."""

    def press(target):
        event = dict(target.data)["__EVENTTARGET"]
        if event.endswith("lnkDownloadFile"):
            return snap(target.url, 302, headers={"location": location})
        if event == "ucNews$rptNews$ctl01$lnkShowDetail":
            return snap(target.url, html=detail or fx("webidx_detail.html"))
        return snap(target.url, html=SECOND)

    return press


def downloads(f: Frontier):
    return f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id WHERE a.kind='download' "
               "ORDER BY a.id")


def file_gets(net) -> list[str]:
    return [u for m, u in net.seen if m == "GET" and "ajaxdownloadfilecourse" in u.lower()]


# ---------------------------------------------------------------- ①: a download's redirect to its file


def test_a_download_press_redirected_to_the_file_service_queues_its_file(tmp_path):
    root = tmp_path / "archive"
    crawler, net, _clock = make(root, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "ajaxdownloadfilecourse"): snap(FILE_URL, body=PDF, headers=PDF_NAMED),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): press_to(FILE_SVC),
    })
    seed(crawler, MORE)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    more = f.row(scope.canonical(MORE))
    got = downloads(f)
    assert len(got) == 1, [dict(r) for r in got]
    a = got[0]
    assert (a["state"], a["reason"], a["status"], a["location"], a["result"]) == ("done", None, 302, FILE_URL,
                                                                                  "redirect")
    assert not f.q("SELECT 1 FROM event WHERE code='chain_restart'")  # no lapsed session: nothing walked again
    row = f.row(FILE_URL)
    assert (row["kind0"], row["owner_site"], row["found_via"], row["found_from"]) == ("file", WEB3, "redirect",
                                                                                      more["id"])
    assert (row["state"], row["status"], row["sniff"], row["filename"]) == ("done", 200, "pdf", "a.pdf")
    assert f.q("SELECT text FROM link WHERE src=? AND dst=?", (more["id"], FILE_URL))[0][0] == "[postback]"
    assert file_gets(net) == [FILE_URL]  # one GET of the file, after its press
    res = {r["res_key"]: (r["result"], r["res"]) for r in f.q("SELECT res_key, result, res FROM page_resource "
                                                              "WHERE page_id=?", (more["id"],))}
    assert res[f"pb:download:{a['row_key']}"] == ("ok", f"u{row['id']}")
    assert not f.q("SELECT 1 FROM page_resource WHERE result='not_file'")
    crawler.frontier.close()
    net.sink.close()
    # the export lists the file once, as its own row (the press that led to it is no second copy)
    out = tmp_path / "out"
    export.export_site(root, WEB3, out)
    items = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["项目"]
    assert [x["存档编号"] for x in items if x["网址"] == FILE_URL] == [f"u{row['id']}"]


def test_one_file_pressed_from_two_lists_is_fetched_once(tmp_path):
    home = scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=3")
    lists = {home: fx("webidx_morenews.html"), scope.canonical(MORE): fx("webidx_morenews.html")}  # page 2: 404
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "ajaxdownloadfilecourse"): snap(FILE_URL, body=PDF, headers=PDF_NAMED),
        ("GET", "webidx=3"): lambda t: snap(t.url, html=lists[t.url]) if t.url in lists else snap(t.url, 404),
        ("POST", "webidx=3"): press_to(FILE_SVC),
    })
    seed(crawler, home)
    seed(crawler, MORE)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    got = downloads(f)
    assert len({r["list_id"] for r in got}) == 2  # the home's news grid and MoreNews each pressed its notice's file
    assert {(r["state"], r["reason"], r["location"], r["result"]) for r in got} == {("done", None, FILE_URL,
                                                                                    "redirect")}
    assert f.q("SELECT count(*) FROM url WHERE url=?", (FILE_URL,))[0][0] == 1
    assert file_gets(net) == [FILE_URL]


@pytest.mark.parametrize("location", ["/Login.aspx?ReturnUrl=%2fWebServices%2fajaxDownloadFileCourse.aspx",
                                      "https://files.example.org/a.pdf"])
def test_a_download_press_sent_to_a_sign_in_page_or_another_host_is_still_a_failed_press(tmp_path, location):
    """§23.9 A2 as it was: the session lapsed (a sign-in page), or the file is elsewhere (another host: no address
    this press may vouch for). The press fails, the chain walks again from its GET once, the second time it is
    not_file:redirect; the Location is never queued, the page never 要登录."""
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): press_to(location),
    })
    seed(crawler, MORE)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    loc = scope.canonical(location, MORE)
    got = [(r["state"], r["reason"], r["location"]) for r in downloads(f) if r["state"] != "superseded"]
    assert got == [("failed", "press_redirect", loc), ("done", "not_file:redirect", loc)]
    assert f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    assert f.row(loc) is None and not [u for _m, u in net.seen if u == loc]
    assert "login" not in (f.row(scope.canonical(MORE))["flags"] or "")


BASE = scope.canonical(MORE)


@pytest.mark.parametrize("location, want", [
    (FILE_SVC, FILE_URL),
    ("/aca/uploadfile/a.pdf", "https://sso.nknu.edu.tw/aca/uploadfile/a.pdf"),
    ("/x.jpg", "https://sso.nknu.edu.tw/x.jpg"),
    ("/", None),  # SSO's home: the session lapsed
    ("/Default.aspx", None),
    ("/Login/a.pdf", None),  # a file's name on a sign-in path: never fetched (judge: login)
    ("https://files.example.org/a.pdf", None),  # another host
    ("", None),
    ("javascript:void(0)", None),
    (None, None),
])
def test_file_redirect(location, want):
    assert postback.file_redirect(location, BASE) == want


FIRST_FILE = "ucNews$rptFiles$ctl00$lnkDownloadFile"  # the notice's first file button (its second: ctl01)


def old_redirects(root: Path):
    """A MoreNews page as round one left it: its notice's two file buttons pressed, every press sent elsewhere by a
    302 (the first failed press_redirect, the chain walked again, the second press done not_file:redirect), the first
    button's presses to the file service, the second's to SSO's home. Returns the closed crawler's network, the
    presses left (id: state, reason) and the page's id."""
    two = fx("webidx_detail.html").replace("</ul>", '<li class="file-item"><a id="ucNews_rptFiles_ctl01_lnkDownload'
                                           'File" href="javascript:__doPostBack(&#39;ucNews$rptFiles$ctl01$lnkDownload'
                                           'File&#39;,&#39;&#39;)"><span class="file-text">附表.pdf</span></a></li></ul>')
    crawler, net, _clock = make(root, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
        ("POST", "webidx=3"): press_to("https://sso.nknu.edu.tw/", detail=two),
    })
    seed(crawler, MORE)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    # what round one left: the first file button's presses were sent to the file service
    f.x("UPDATE action SET location=? WHERE kind='download' AND target=? AND state<>'superseded'",
        (FILE_URL, FIRST_FILE))
    before = {r["id"]: (r["state"], r["reason"]) for r in downloads(f) if r["state"] != "superseded"}
    assert sorted(before.values()) == [("done", "not_file:redirect")] * 2 + [("failed", "press_redirect")]
    more_id = f.row(scope.canonical(MORE))["id"]
    crawler.frontier.close()
    net.sink.close()
    return net, before, more_id


def test_rejudge_takes_old_redirect_presses_as_their_files(tmp_path):
    """Round one wrote every such redirect as a lapsed session: the first press failed (press_redirect), the chain
    walked again, the second press done not_file:redirect, the Location never queued. A rejudge adopts the presses
    whose Location is a file on their own host (done, reason cleared: result redirect) and queues the file
    (found_via rejudge), sending nothing; a press sent to SSO's home stays as it was, for `retry download`."""
    root = tmp_path / "archive"
    net, before, more_id = old_redirects(root)
    first = FIRST_FILE
    seen = len(net.seen)
    reconcile.rejudge(root, [WEB3], plan=make_plan(make_site(WEB3, home=MORE)))
    assert len(net.seen) == seen  # nothing sent
    f = Frontier(root / "frontier.db")
    after = {r["id"]: r for r in downloads(f) if r["state"] != "superseded"}
    for aid, (state, reason) in before.items():
        r = after[aid]
        if r["target"] == first:
            assert (r["state"], r["reason"], r["result"]) == ("done", None, "redirect")
        else:
            assert (r["state"], r["reason"]) == (state, reason)
    row = f.row(FILE_URL)
    assert (row["state"], row["found_via"], row["kind0"], row["owner_site"]) == ("pending", "rejudge", "file", WEB3)
    key = next(r["row_key"] for r in after.values() if r["target"] == first)
    got = f.q("SELECT result, res FROM page_resource WHERE page_id=? AND res_key=?", (more_id, f"pb:download:{key}"))
    assert tuple(got[0]) == ("todo", f"u{row['id']}")  # rests on the file's row, which the next run fetches
    lists, buttons = Chains(f, root).retry_downloads()
    assert (lists, buttons) == (1, 1)
    assert key not in json.loads(f.meta(f"{REDO_KEY}{more_id}"))  # only the press sent to SSO's home goes back
    f.close()


def test_retry_download_before_the_rejudge_leaves_the_presses_sent_to_their_file(tmp_path):
    """`retry download` run before the rejudge (review of 11-09: it superseded the presses round one took for a lapsed
    session, which no rejudge or GET adopted then, and their files were never queued): a press sent by its redirect to
    a file on its own host is no press to put back, its file is the Location. Only the press sent to SSO's home goes
    back; the page's next reading (the GET retry download asks for) adopts the others and fetches their file."""
    root = tmp_path / "archive"
    _net, before, more_id = old_redirects(root)
    f = Frontier(root / "frontier.db")
    assert Chains(f, root).retry_downloads() == (1, 1)
    keys = json.loads(f.meta(f"{REDO_KEY}{more_id}"))
    now = {r["id"]: (r["state"], r["reason"], r["target"], r["row_key"]) for r in downloads(f)}
    sent_home = {k for _s, _r, t, k in now.values() if t != FIRST_FILE}
    assert keys == sorted(sent_home)  # the second button only
    for aid, (state, reason) in before.items():  # the first button's presses: as round one left them
        if now[aid][2] == FIRST_FILE:
            assert now[aid][:2] == (state, reason)
    f.close()
    crawler, net, _clock = make(root, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "ajaxdownloadfilecourse"): snap(FILE_URL, body=PDF, headers=PDF_NAMED),
        ("GET", MORE): snap(MORE, html=fx("webidx_morenews.html")),
    })
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    assert not [m for m, _u in net.seen if m == "POST"]  # the second button sits in a notice: row_gone, no press
    first = [r for r in downloads(f) if r["target"] == FIRST_FILE and r["state"] != "superseded"]
    assert {(r["state"], r["reason"], r["result"]) for r in first} == {("done", None, "redirect")}
    row = f.row(FILE_URL)
    assert (row["state"], row["kind0"], row["owner_site"], row["filename"]) == ("done", "file", WEB3, "a.pdf")
    assert file_gets(net) == [FILE_URL]


# ---------------------------------------------------------------- ② ③: a page is read by its own platform


def stored_moved(w: World, url: str, owner: str, html: str, *, plan: bool = True):
    """A page of `owner` written on another platform (a seed's redirect adopted its site: url.site is its own
    address's key, url.owner_site the site it counts for), stored as the crawler leaves it; plan: its presses
    planned by page_stored (False: as round one left a page whose platform file pressed nothing)."""
    url = scope.canonical(url)
    host = urlsplit(url).hostname
    w.f.set_host(host, queue=SSO_Q)
    w.f.add(url, host=host, site=scope.site_key(url), unit=None, tier=1, kind="page", depth=1, found_via="link",
            found_from=None, owner_site=owner, prio=0, kind0="page")
    shot = w.archive(url, html)
    row = w.f.row(url)
    w.f.set_page_res(row["id"], [])
    w.f.finish(row["id"], state="done", status=200, kind="page", sniff="html", warc=w.warc,
               warc_offset=shot.warc_offset, warc_len=shot.warc_len, final_url=url)
    row = w.f.row(url)
    if plan:
        w.chains.page_stored(row, html, w.warc, shot.warc_offset)
    return row, shot


def test_a_moved_clv2_site_presses_its_webidx_download_buttons(tmp_path):
    """Every cLv2 site of round one redirects to a WebIdx site, which its key adopts: the 书表 page is read as
    WebIdx's (its two download buttons pressed: profile sso_webidx), the /site/ key keeping every count (action.site,
    the postback table, listwalk.site)."""
    page_html = fx("sso_download_export.html")
    export_url = postback.file_followup(page_html, ITEMS)
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "clv2=999001"): snap(CLV2_HOME, 302,
                                     headers={"location": "/services/web/default.aspx?webIdx=37&menuid=1"}),
        ("GET", "ajaxdownloadfile.aspx"): snap(export_url, body=PDF, headers=PDF_NAMED),
        ("GET", "webidx=37"): snap(ITEMS, html=fx8("webidx_items.html")),
        ("POST", "webidx=37"): lambda t: snap(t.url, html=page_html),
    }, plan=OpenPlan(make_site(OWNER, home=CLV2_HOME)))
    seed(crawler, CLV2_HOME)
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    page = f.row(ITEMS)
    assert (page["owner_site"], page["site"]) == (OWNER, WEB37)
    got = [(r["target"], r["profile"], r["site"], r["form_id"], r["state"], r["result"], r["location"])
           for r in downloads(f)]
    assert got == [(t, "sso_webidx/webidx_items", OWNER, "form1", "done", "ok", export_url) for t in (DL1, DL2)]
    left = {r["res_key"]: (r["need"], r["why"]) for r in f.q("SELECT * FROM page_res WHERE page_id=? AND "
                                                             "how='unhandled'", (page["id"],))}
    assert left == {"postback:ucAllTypeList$btnAll": ("no", "unhandled:ignore"),
                    "postback:ucAllTypeList$rptCategories$ctl*$btnCat": ("no", "unhandled:ignore")}
    assert f.q("SELECT decision FROM postback WHERE site=? AND target=?", (OWNER, DL1))[0][0] == "press"
    assert f.q("SELECT decision FROM postback WHERE site=? AND target=?", (OWNER, "ucAllTypeList$btnAll"))[0][0] == \
        "ignore"
    lw = f.q("SELECT site, profile, ended_by FROM listwalk WHERE list_id=?", (page["id"],))
    assert [tuple(x) for x in lw] == [(OWNER, "sso_webidx", "last_page")]


def test_a_run_given_the_owner_key_sends_the_moved_pages_presses(tmp_path):
    w = World(tmp_path)
    stored_moved(w, ITEMS, OWNER, fx8("webidx_items.html"))
    assert w.chains.due_queues(sites=[WEB37]) == [] and w.chains.waiting(sites=[WEB37])[0] == 0
    assert w.chains.next_action(SSO_Q, sites=[WEB37]) is None  # the platform's key is no site of the list
    assert w.chains.due_queues(sites=[OWNER]) == [SSO_Q] and w.chains.waiting(sites=[OWNER])[0] == 2
    a = w.chains.next_action(SSO_Q, sites=[OWNER])
    assert a is not None and a["target"] == DL1
    w.sink.close()
    w.f.close()


def test_row_keys_stay_the_owners(tmp_path):
    w = World(tmp_path)
    row, _shot = stored_moved(w, ITEMS, OWNER, fx8("webidx_items.html"))
    want = [postback.row_key(OWNER, ITEMS, "實驗室安全講習報名表", "2026-09-01", "表單下載"),
            postback.row_key(OWNER, ITEMS, "學系儀器借用要點", "2026-08-15", "法令規章")]
    assert [a["row_key"] for a in w.actions(row["id"])] == want
    w.sink.close()
    w.f.close()


def test_read_page_uses_the_profile_it_is_given():
    html = fx8("webidx_items.html")
    pr = read_page(profiles.SSO_WEBIDX, ITEMS, html, site="sso.nknu.edu.tw/site/1", list_url=ITEMS)
    assert [(p.kind, p.target, p.form_id) for p in pr.presses] == [("download", DL1, "form1"), ("download", DL2,
                                                                                               "form1")]
    assert (pr.rowset, pr.unknown, pr.unplanned) == ("webidx_items", (), ())
    # read with the platform file of the site it counts for (cLv2), as round one did: nothing to press, every one of
    # WebIdx's buttons unknown
    old = read_page(profiles.SSO_CLV2, ITEMS, html, site="sso.nknu.edu.tw/site/1", list_url=ITEMS)
    assert not old.presses and "postback:ucAllTypeList$rptItems$ctl*$lnkDownloadFile|" in old.unknown


def test_rejudge_then_retry_unpressed_puts_back_the_pages_whose_buttons_nobody_pressed(tmp_path):
    w = World(tmp_path)
    old = [{"res_key": "postback:ucAllTypeList$rptItems$ctl*$lnkDownloadFile", "how": "unhandled", "role": "click",
            "need": "must", "why": "unhandled:unknown", "text": ""}]
    row, _shot = stored_moved(w, ITEMS, OWNER, fx8("webidx_items.html"), plan=False)
    w.f.add_page_res(row["id"], old)  # as round one wrote it: the owner's platform file knew no such button
    # a page whose only button nobody decided about: nothing to press, never put back
    switch = ("<html><body><form method='post' action='/services/web/default.aspx?webidx=37&amp;menuid=2' id='form1'>"
              "<input type='hidden' name='__VIEWSTATE' value='VS-SWITCH'><a href=\"javascript:__doPostBack("
              "'ctl00$uTopMainMenu$btnSwitch','')\">切換</a><p>本組簡介。</p></form></body></html>")
    other, _ = stored_moved(w, "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=37&menuid=2", OWNER, switch,
                            plan=False)
    w.f.add_page_res(other["id"], [{"res_key": "postback:ctl*$uTopMainMenu$btnSwitch", "how": "unhandled",
                                    "role": "click", "need": "must", "why": "unhandled:unknown", "text": "切換"}])
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [OWNER], plan=make_plan(make_site(OWNER, home=CLV2_HOME)))
    f = Frontier(tmp_path / "frontier.db")
    entry = f.q("SELECT need, why FROM page_res WHERE page_id=? AND res_key LIKE 'postback:ucAllTypeList$rptItems%'",
                (row["id"],))
    assert [tuple(x) for x in entry] == [("must", UNPRESSED)]
    assert not f.q("SELECT 1 FROM action WHERE list_id=?", (row["id"],))  # a rejudge presses nothing
    assert f.q("SELECT why FROM page_res WHERE page_id=? AND res_key LIKE 'postback:%btnSwitch'",
               (other["id"],))[0][0] == "unhandled:unknown"
    assert Chains(f, tmp_path).retry_unpressed(["another.nknu.edu.tw"]) == (0, 0)
    assert Chains(f, tmp_path).retry_unpressed() == (1, 1)
    back = f.row_by_id(row["id"])
    assert (back["state"], back["reason"], back["prio"], back["next_at"]) == ("pending", "chain_restart", 0, 0)
    assert f.row_by_id(other["id"])["state"] == "done"
    assert f.q("SELECT 1 FROM event WHERE code='chain_restart' AND message LIKE 'retry unpressed%'")
    f.close()


def test_retry_unpressed_drops_a_retry_download_waiting_for_the_page(tmp_path):
    """A page `retry download` put back presses only its downloads (REDO_KEY); one put back for its unpressed
    buttons too must press every button not pressed well before: the redo list goes, the whole page is planned."""
    w = World(tmp_path)
    row, _shot = stored_moved(w, ITEMS, OWNER, fx8("webidx_items.html"), plan=False)
    w.f.add_page_res(row["id"], [{"res_key": "postback:ucAllTypeList$rptItems$ctl*$lnkDownloadFile",
                                  "how": "unhandled", "role": "click", "need": "must", "why": UNPRESSED, "text": ""}])
    w.f.meta(f"{REDO_KEY}{row['id']}", json.dumps(["k1"]))
    assert w.chains.retry_unpressed() == (1, 1)
    assert w.f.meta(f"{REDO_KEY}{row['id']}") is None
    w.sink.close()
    w.f.close()


def test_the_retry_unpressed_command_says_how_many_and_waits_for_a_run(tmp_path):
    w = World(tmp_path)
    row, _shot = stored_moved(w, ITEMS, OWNER, fx8("webidx_items.html"), plan=False)
    w.f.add_page_res(row["id"], [{"res_key": "postback:ucAllTypeList$rptItems$ctl*$lnkDownloadFile",
                                  "how": "unhandled", "role": "click", "need": "must", "why": UNPRESSED, "text": ""}])
    w.sink.close()
    w.f.close()
    run = CliRunner()
    lock = hold_run_lock(tmp_path)
    try:
        got = run.invoke(app, ["archive", "retry", "unpressed", "--dir", str(tmp_path)])
    finally:
        lock.close()
    assert got.exit_code == 1 and "archive run 正在用这个目录" in got.output
    got = run.invoke(app, ["archive", "retry", "download", "--sites", OWNER, "--dir", str(tmp_path)])
    assert got.exit_code == 1 and "--sites 只给 unpressed 用" in got.output
    # an empty list (a script's unset variable) is no site, never every site (as archive run, 11-06; review of 11-09)
    for empty in ("", " , "):
        got = run.invoke(app, ["archive", "retry", "unpressed", "--sites", empty, "--dir", str(tmp_path)])
        assert got.exit_code == 1 and "--sites 是空的" in got.output, got.output
    f = Frontier(tmp_path / "frontier.db")
    assert f.row_by_id(row["id"])["state"] == "done"
    f.close()
    got = run.invoke(app, ["archive", "retry", "unpressed", "--sites", "c.nknu.edu.tw/aca", "--dir", str(tmp_path)])
    assert got.exit_code == 0 and "0 个网页" in got.output, got.output
    got = run.invoke(app, ["archive", "retry", "unpressed", "--sites", OWNER, "--dir", str(tmp_path)])
    assert got.exit_code == 0 and "1 个网页、1 种" in got.output, got.output
    f = Frontier(tmp_path / "frontier.db")
    assert f.row_by_id(row["id"])["state"] == "pending"
    f.close()


def test_a_page_written_on_a_platform_with_no_profile_plans_nothing(tmp_path):
    """english's teachers' pages live on SSO's vita system (no platform file): read by their own platform they plan
    no press and are decided by the rules every site shares, never by english's own file."""
    vita = "https://sso.nknu.edu.tw/vita/index.aspx?id=7"
    html = ("<html><body><form method='post' action='./index.aspx?id=7' id='form1'><input type='hidden' "
            "name='__VIEWSTATE' value='VS-VITA'><a href=\"javascript:__doPostBack('ctl00$btnLangE','')\">English"
            "</a><a href=\"javascript:_checkLogin()\">登入</a><p>研究專長：某某領域。</p></form></body></html>")
    w = World(tmp_path)
    row, _shot = stored_moved(w, vita, "english.nknu.edu.tw", html)
    assert profiles.profile_for(vita) is None and not w.actions(row["id"]) and w.walk(row["id"]) is None
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, ["english.nknu.edu.tw"], plan=make_plan(make_site("english.nknu.edu.tw")))
    f = Frontier(tmp_path / "frontier.db")
    got = {r["res_key"]: (r["need"], r["why"]) for r in f.q("SELECT * FROM page_res WHERE page_id=? AND "
                                                            "how='unhandled'", (row["id"],))}
    assert got["postback:ctl*$btnLangE"] == ("no", "unhandled:never")  # every site's language rule
    # english's own file says never for its log-in box; this page is SSO's: nobody decided (要人看)
    assert got["js_link:_checkLogin"] == ("must", "unhandled:unknown")
    f.close()


# ---------------------------------------------------------------- ③: 图资处's MoreNews pages on SSO WebIdx


def test_a_library_moved_to_webidx_presses_its_news_details(tmp_path):
    lis_home = "https://lis.nknu.edu.tw/"
    lis_more = scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=53&selpage=MoreNews&sort=date"
                               "&pagenum=1")
    one_page = re.sub(r"<div class='pagination'>.*?</div>", "", fx("webidx_morenews.html"), flags=re.DOTALL)
    crawler, net, _clock = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "ajaxdownloadfilecourse"): snap(FILE_URL, body=PDF, headers=PDF_NAMED),
        ("GET", "lis.nknu.edu.tw"): snap(lis_home, 302, headers={"location": lis_more}),
        ("GET", "webidx=53"): snap(lis_more, html=one_page.replace("webidx=3&amp;", "webidx=53&amp;")),
        ("POST", "/services/web/default.aspx"): press_to(FILE_SVC),  # a notice's form posts where its page says
    }, plan=OpenPlan(make_site("lis.nknu.edu.tw", home=lis_home)))
    seed(crawler, lis_home)
    work(crawler, net, "ip:lis.nknu.edu.tw")
    work(crawler, net, SSO_Q)
    f = crawler.frontier
    page = f.row(lis_more)
    assert (page["owner_site"], page["site"]) == ("lis.nknu.edu.tw", "sso.nknu.edu.tw/web/53")
    details = f.q("SELECT * FROM action WHERE kind='detail' ORDER BY id")
    assert [(d["profile"], d["site"], d["state"]) for d in details] == [("sso_webidx/webidx_news", "lis.nknu.edu.tw",
                                                                        "done")] * 2
    got = downloads(f)
    assert [(r["site"], r["state"], r["reason"], r["location"]) for r in got] == [("lis.nknu.edu.tw", "done", None,
                                                                                   FILE_URL)]
    assert f.row(FILE_URL)["owner_site"] == "lis.nknu.edu.tw" and file_gets(net) == [FILE_URL]
    lw = f.q("SELECT site, profile, ended_by FROM listwalk WHERE list_id=?", (page["id"],))
    assert [tuple(x) for x in lw] == [("lis.nknu.edu.tw", "sso_webidx", "last_page")]


def test_profile_pagers_read_the_pager_box_of_the_pages_platform():
    """The default-sorted first MoreNews page links its pages with a sort=date it does not carry itself: WebIdx's
    pager box names them, whatever site the page counts for (图资处's own platform file is its old Joomla site's)."""
    first = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=53&selpage=MoreNews"
    html = fx("webidx_morenews.html").replace("webidx=3&", "webidx=53&")
    got = postback.profile_pagers(first, html)
    assert got == [scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=53&selpage=MoreNews"
                                   "&sort=date&pageNum=1"),
                   scope.canonical("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=53&selpage=MoreNews"
                                   "&sort=date&pageNum=2")]


def test_the_librarys_own_joomla_pages_keep_their_platform_file():
    page = "https://lis.nknu.edu.tw/index.php/zh/news?start=10"
    assert profiles.profile_for(page) is profiles.LIS
    assert profiles.decision(scope.site_key(page), "js_link:Joomla.tableOrdering") == "ignore"


# ---------------------------------------------------------------- ④: osa's attachments, the regulations' downloads


def test_osa_event_attachments_are_download_presses(tmp_path):
    w = World(tmp_path)
    row, got = w.stored(OSA, "sso.nknu.edu.tw/osa", fx8("osa_event.html"))
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["profile"], a["form_id"]) for a in acts] == [
        ("download", f"EventDetails1$EventAttachmentList$ctl0{n}$EventAttachmentD", "sso_osa/-", "form1")
        for n in (2, 3)]
    assert (got.page_role, got.ended_by) == (None, None) and w.walk(row["id"]) is None
    _a, target = w.press()
    data = dict(target.data)
    assert (target.method, target.url) == ("POST", OSA)
    assert data["__EVENTTARGET"] == acts[0]["target"] and data["__VIEWSTATE"] == "VS-OSA-EVENT"
    # 「關閉」 needs nothing; the year and theme queries wait for round two
    assert links.unhandled_need(profiles.decision("sso.nknu.edu.tw/osa", "postback:BackList", "關閉"))[0] == "no"
    for key in ("postback:QueryEventSCYear", "postback:QueryStaSub", "select:StaYearList"):
        assert profiles.decision("sso.nknu.edu.tw/osa", key, "查詢") == "round2"
    w.sink.close()
    w.f.close()


FILLED = "/osa/hesp/x.ashx?id=1"
FILLED_URL = "https://sso.nknu.edu.tw/osa/hesp/x.ashx?id=1"


def _osa_page(kind: str) -> str:
    page = fx8("osa_event.html")
    if kind == "frame":
        return page.replace('<iframe id="DownloadEventAtt"', f'<iframe id="DownloadEventAtt" src="{FILLED}"')
    if kind == "script":
        return page.replace("</form>", f"<script>document.getElementById('DownloadEventAtt').src = '{FILLED}';"
                                       "</script></form>")
    return page


@pytest.mark.parametrize("answer, follow, result, reason", [
    ("file", None, "ok", None),
    ("frame", FILLED_URL, "ok", None),
    ("script", FILLED_URL, "ok", None),
    ("page", None, "not_file", "not_file:no_name:html"),
])
def test_an_osa_attachment_press_comes_to_its_file_or_to_not_file(tmp_path, answer, follow, result, reason):
    w = World(tmp_path)
    w.stored(OSA, "sso.nknu.edu.tw/osa", fx8("osa_event.html"))
    a, target = w.press()
    if answer == "file":
        out = w.answer(a, target.url, PDF, headers=PDF_NAMED)
    else:
        out = w.answer(a, target.url, _osa_page(answer))
    assert out.file_get == follow and not out.restart
    if follow is not None:
        w.chains.file_answered(a, w.archive(follow, PDF, headers=PDF_NAMED), w.warc)
    got = w.f.q("SELECT state, reason, result FROM action_result WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("done", reason, result)
    w.sink.close()
    w.f.close()


def test_an_osa_attachment_press_sent_to_the_file_service_is_that_file(tmp_path):
    w = World(tmp_path)
    w.stored(OSA, "sso.nknu.edu.tw/osa", fx8("osa_event.html"))
    a, target = w.press()
    out = w.answer(a, target.url, b"", status=302, headers={"location": FILE_SVC, "content-type": "text/html"})
    assert (out.location, out.restart, out.file_get) == (FILE_URL, False, None)
    got = w.f.q("SELECT state, reason, location, result FROM action_result WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("done", None, FILE_URL, "redirect")
    w.sink.close()
    w.f.close()


def test_regulations_press_only_where_the_row_has_no_preview(tmp_path):
    w = World(tmp_path)
    row, got = w.stored(REG, "sso.nknu.edu.tw/regulations", fx8("regulations.html"))
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["profile"], a["form_id"], a["row_text"]) for a in acts] == [
        ("download", "ctl00$phMain$reggv$ctl03$downloadBtn", "sso_regulations/-", "uForm", "某某獎學金甄選要點（測試）")]
    assert w.walk(row["id"]) is None and got.page_role is None  # one GET holds the whole table
    _a, target = w.press()
    data = dict(target.data)
    assert data["__EVENTTARGET"] == "ctl00$phMain$reggv$ctl03$downloadBtn" and data["__VIEWSTATE"] == "VS-REGULATIONS"
    site = "sso.nknu.edu.tw/regulations"
    assert profiles.decision(site, "postback:ctl*$phMain$searchBtn", "") == "never"
    assert profiles.decision(site, "select:ctl00$phMain$unitLv2DDL") == "never"
    w.sink.close()
    w.f.close()


def test_file_followup_reads_a_hidden_frame_the_press_filled():
    page = fx8("osa_event.html")
    filled = _osa_page("frame")
    assert postback.file_followup(filled, OSA, page) == FILLED_URL
    assert postback.file_followup(filled, OSA) == FILLED_URL  # the page pressed not known: a hidden frame counts
    assert postback.file_followup(_osa_page("script"), OSA, page) == FILLED_URL
    jq = page.replace("</form>", f"<script>$('#DownloadEventAtt').attr('src', '{FILLED}');</script></form>")
    assert postback.file_followup(jq, OSA, page) == FILLED_URL
    # no follow-up: the page again with its frame empty, a frame the page pressed had already, a frame that shows,
    # another host's address, a picture's address a script sets
    assert postback.file_followup(page, OSA, page) is None
    assert postback.file_followup(filled, OSA, filled) is None
    shown = page.replace("</form>", '<iframe src="/osa/hesp/map.aspx" width="600" height="400"></iframe>'
                                    '<iframe src="https://www.youtube.com/embed/abc" width="0" height="0"></iframe>'
                                    "</form>")
    assert postback.file_followup(shown, OSA, page) is None
    off = filled.replace(FILLED, "https://files.example.org/x.pdf")
    assert postback.file_followup(off, OSA, page) is None
    pic = page.replace("</form>", f"<script>var pic = new Image(); pic.src = '{FILLED}';</script></form>")
    assert postback.file_followup(pic, OSA, page) is None


# addresses on the press's own host that are no file to fetch: a sign-in, SSO's own sign-in, a log-out, a captcha's
# picture, a back office (scope.judge: login). A press's answer that names one never sends the crawler there (review
# of 11-09: the follow-up GET is sent as it is, so the address rules are asked here, as file_redirect asks them)
SIGN_IN = ("/osa/Login.aspx?ReturnUrl=%2fosa%2fhesp%2feventlist.aspx", "/userlogin.aspx", "/osa/Logout.aspx",
           "/osa/CheckCode.aspx", "/osa/admin/default.aspx")


@pytest.mark.parametrize("src", SIGN_IN)
def test_file_followup_never_names_a_sign_in_page(src):
    page = fx8("osa_event.html")
    assert not scope.judge(scope.canonical(src, OSA)).ok
    frame = page.replace('<iframe id="DownloadEventAtt"', f'<iframe id="DownloadEventAtt" src="{src}"')
    script = page.replace("</form>", f"<script>document.getElementById('DownloadEventAtt').src = '{src}';</script>"
                                     "</form>")
    jq = page.replace("</form>", f"<script>$('#DownloadEventAtt').attr('src', '{src}');</script></form>")
    for answer in (frame, script, jq):
        assert postback.file_followup(answer, OSA, page) is None
        assert postback.file_followup(answer, OSA) is None
    # a sign-in address first, the file's after it: the file's
    both = script.replace("</form>", f"<script>$('#DownloadEventAtt').attr('src', '{FILLED}');</script></form>")
    assert postback.file_followup(both, OSA, page) == FILLED_URL


@pytest.mark.parametrize("src", SIGN_IN)
def test_an_osa_press_whose_answer_names_a_sign_in_page_is_no_file(tmp_path, src):
    w = World(tmp_path)
    w.stored(OSA, "sso.nknu.edu.tw/osa", fx8("osa_event.html"))
    a, target = w.press()
    answer = fx8("osa_event.html").replace('<iframe id="DownloadEventAtt"', f'<iframe id="DownloadEventAtt" src="{src}"')
    out = w.answer(a, target.url, answer)
    assert (out.file_get, out.location, out.restart) == (None, None, False)
    got = w.f.q("SELECT state, reason, result FROM action_result WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("done", "not_file:no_name:html", "not_file")  # 要人看, never a GET of that page
    w.sink.close()
    w.f.close()


def test_the_two_platform_files_claim_their_keys_and_say_why():
    assert profiles.check_profiles() == []
    assert profiles.profile_for(OSA) is profiles.SSO_OSA
    assert profiles.profile_for(REG) is profiles.SSO_REGULATIONS
    assert profiles.profile_for("https://sso.nknu.edu.tw/news/subjectlist.aspx") is profiles.SSO_NEWS
    for p in (profiles.SSO_OSA, profiles.SSO_REGULATIONS):
        assert all(b.why.strip() for b in p.buttons) and all(x.why.strip() for x in p.writings)
