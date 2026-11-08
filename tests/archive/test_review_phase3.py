"""The third phase's review (10-26, two reviewers of the wiring of postback.py, google_api.py and profiles.py into the
crawler): each finding that held, as a test written before its fix. A page, an attachment or a short link that leads
to a Drive item counted on the item's metadata alone; an empty detail answered by a press counted as got; an SSO unit
site's news list never walked on its first page; a bare ctlNN button decided by another button's text; buttons in a
press's answer nobody wrote down; the id probe asked again before every detail page; drive_item looked up with no
index; an archived record that cannot be read; a file button pressed after its rule changed. All offline: the tests'
fake networks and fake clocks, made-up titles and file ids, no person's name."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from nknu.archive import google_api, outcome, profiles, scope
from nknu.archive.frontier import Frontier
from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import drive
from tests.archive.test_postback import CLV2, MORE, World, fx

KEY = "test-key-not-a-real-one"  # monkeypatched into GOOGLE_API_KEY; the fake network never reads it
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
FID = "1ReDiReCtAbCdEfGhIj"  # made up
CHILD = "1ChIlDfIlEaBcDeFgHi"  # made up
HOME = "https://w.nknu.edu.tw/a/home"
GQ = google_api.QUEUE
WQ = "ip:w.nknu.edu.tw"


# ---------------------------------------------------------------- ① a redirect to a Drive item (reviews 1 and 8)


def _json(u, data, status=200):
    return snap(u, status, body=json.dumps(data).encode(), headers={"content-type": "application/json"})


def _file_meta(fid, name="簡章.pdf"):
    return lambda u: _json(u, {"id": fid, "name": name, "mimeType": "application/pdf", "size": str(len(PDF))})


def _no_download(u):
    return _json(u, {"error": {"code": 403, "errors": [{"reason": "cannotDownloadFile"}]}}, 403)


def _pdf(u):
    return snap(u, body=PDF, headers={"content-type": "application/pdf"})


def _crawl(tmp_path, monkeypatch, routes, rounds=6, url=HOME):
    monkeypatch.setenv(google_api.KEY_ENV, KEY)
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), **routes})
    seed(crawler, url)
    for _ in range(rounds):
        for q in (WQ, GQ, "ip:reurl.cc"):
            drive(crawler, net, q)
    return crawler, net, clock


def _ok_type(f, url):
    """What jscheck would say of the page's type (ok), so that only the page's resources decide its level."""
    f.x("UPDATE page_type SET verdict='ok', source='manual' WHERE key=(SELECT page_type FROM url WHERE url=?)", (url,))


def _level(f, url):
    got = f.q("SELECT level FROM page_completeness WHERE id=(SELECT id FROM url WHERE url=?)", (url,))
    return got[0][0] if got else None


def _drive_entries(f, url):
    return {r["res_key"]: r for r in f.q("SELECT pr.* FROM page_res pr JOIN url u ON u.id = pr.page_id WHERE u.url=? "
                                         "AND pr.res_key LIKE 'drive:%'", (url,))}


@pytest.mark.parametrize("download", ["refused", "got"])
def test_a_page_that_redirects_to_a_drive_file_is_complete_only_when_the_file_is(tmp_path, monkeypatch, download):
    to = f"https://drive.google.com/file/d/{FID}/view"
    crawler, _net, _clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _no_download if download == "refused" else _pdf, FID: _file_meta(FID),
        "/a/home": snap(HOME, 302, headers={"location": to})})
    f = crawler.frontier
    media = f.row(google_api.media_url(FID))
    assert media is not None and media["state"] == "done"
    entry = _drive_entries(f, HOME)[f"drive:{FID}"]  # the page uses what it sends the browser to
    assert (entry["need"], entry["res_id"]) == ("must", media["id"])
    assert (_level(f, HOME) == "complete") == (download == "got")


def test_a_page_that_redirects_to_a_drive_folder_needs_every_file_in_it(tmp_path, monkeypatch):
    folder = "1FoLdErReDiReCtAbCd"
    to = f"https://drive.google.com/drive/folders/{folder}"
    crawler, _net, _clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _no_download,
        "%20in%20parents": lambda u: _json(u, {"files": [{"id": CHILD, "name": "附件.pdf", "mimeType": "application/pdf",
                                                     "size": "10"}]}),
        f"files/{folder}?": lambda u: _json(u, {"id": folder, "name": "公告附件", "mimeType": google_api.FOLDER}),
        CHILD: _file_meta(CHILD),
        "/a/home": snap(HOME, 302, headers={"location": to})})
    f = crawler.frontier
    assert f.row(google_api.media_url(CHILD))["status"] == 403  # the file in the folder was not got
    assert f"drive:{CHILD}" in _drive_entries(f, HOME)
    assert _level(f, HOME) != "complete"


def test_a_page_sent_on_by_a_script_to_a_drive_file_uses_that_file(tmp_path, monkeypatch):
    to = f"https://drive.google.com/file/d/{FID}/view"
    html = f'<html><head><title>轉址</title></head><body><script>location.href="{to}";</script></body></html>'
    crawler, _net, _clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _no_download, FID: _file_meta(FID), "/a/home": lambda u: snap(u, html=html)})
    f = crawler.frontier
    assert f.row(HOME)["flags"] and "js_redirect" in f.row(HOME)["flags"]
    assert f"drive:{FID}" in _drive_entries(f, HOME)
    _ok_type(f, HOME)
    assert _level(f, HOME) != "complete"


@pytest.mark.parametrize("via", ["attachment", "short_link"])
def test_what_a_resource_led_to_on_drive_stays_the_pages_after_the_page_is_fetched_again(tmp_path, monkeypatch, via):
    to = f"https://drive.google.com/file/d/{FID}/view"
    link = "https://w.nknu.edu.tw/files/a.pdf" if via == "attachment" else "https://reurl.cc/AbCdEf"
    crawler, net, clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _no_download, FID: _file_meta(FID),
        link: snap(link, 302, headers={"location": to}),
        "/a/home": lambda u: snap(u, html=f'<p>簡章</p><a href="{link}">簡章.pdf</a>')})
    f = crawler.frontier
    _ok_type(f, HOME)
    first = _level(f, HOME)
    f.x("UPDATE url SET state='pending', next_at=0 WHERE url=?", (HOME,))
    clock.t += 100
    drive(crawler, net, WQ)  # the page again: its page_res written anew, nothing of the redirect's fetched again
    _ok_type(f, HOME)
    assert f"drive:{FID}" in _drive_entries(f, HOME)
    assert first != "complete" and _level(f, HOME) != "complete"
    assert sum(u == link for u in net.seen) == 1


def test_an_attachment_redirected_twice_before_drive_writes_the_drive_entries_on_the_page(tmp_path, monkeypatch):
    to = f"https://drive.google.com/file/d/{FID}/view"
    a, b = "https://w.nknu.edu.tw/files/a.pdf", "https://w.nknu.edu.tw/files/b.pdf"
    crawler, _net, _clock = _crawl(tmp_path, monkeypatch, {
        "alt=media": _no_download, FID: _file_meta(FID),
        a: snap(a, 302, headers={"location": b}), b: snap(b, 302, headers={"location": to}),
        "/a/home": lambda u: snap(u, html=f'<p>簡章</p><a href="{a}">簡章.pdf</a>')})
    f = crawler.frontier
    assert f"drive:{FID}" in _drive_entries(f, HOME)
    assert not f.q("SELECT 1 FROM page_res WHERE page_id IN (SELECT id FROM url WHERE kind <> 'page')")


# the same, read from the rows alone (outcome.py): whatever wrote them, a redirect that ends on a Drive item's metadata
# row is never got on that row's 200
def _row(f, url, **cols):
    row = {"url": url, "host": url.split("/")[2], "site": "w.nknu.edu.tw", "owner_site": "w.nknu.edu.tw", "tier": 1,
           "kind": "file", "kind0": "file", "depth": 1, "found_via": "link", "state": "done", "status": 200,
           "sniff": "pdf", "fetched_at": "2026-10-26T02:00:00+00:00", "path": url.split("?", 1)[0], **cols}
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


@pytest.fixture
def ledger(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    f.x("INSERT INTO page_type(key, site, verdict, source) VALUES ('t:ok', 'w.nknu.edu.tw', 'ok', 'manual')")
    yield f
    f.close()


def _drive_item(f, fid, mime, content=None, **cols):
    meta = _row(f, google_api.item_url(fid), kind="drive", kind0="drive", sniff="json", **cols)
    f.x("INSERT INTO drive_item(url_id, file_id, mime, content_id) VALUES (?,?,?,?)", (meta, fid, mime, content))
    return meta


@pytest.mark.parametrize(("content", "mime", "want"), [
    (None, "application/pdf", "not_discovered"),  # read, and no content row made: a crawler bug
    ("ok", "application/pdf", "ok"),
    ("api_error", "application/pdf", "api_error"),
    ("todo", "application/pdf", "todo"),
    (None, "video/mp4", "rule"),  # a video is listed only (record 一·1)
    (None, "application/vnd.google-apps.site", "unhandled"),  # a type nobody can export (B11)
    (None, google_api.FOLDER, "not_discovered"),
    ("shortcut_ok", google_api.SHORTCUT, "ok"),
    ("shortcut_bad", google_api.SHORTCUT, "api_error"),
    ("shortcut_none", google_api.SHORTCUT, "not_discovered"),
])
def test_a_redirect_to_a_drive_metadata_row_comes_to_what_the_item_came_to(ledger, content, mime, want):
    f = ledger
    content_id = None
    if content in ("ok", "api_error", "todo"):
        content_id = _row(f, google_api.media_url(FID), host=google_api.API_HOST,
                          **({"status": 403, "sniff": "json"} if content == "api_error" else
                             {"state": "pending", "status": None, "sniff": None} if content == "todo" else {}))
    elif content is not None:  # a shortcut: its content is its target's metadata row, whose content is the file
        target_content = None
        if content != "shortcut_none":
            target_content = _row(f, google_api.media_url(CHILD), host=google_api.API_HOST,
                                  **({"status": 403, "sniff": "json"} if content == "shortcut_bad" else {}))
        content_id = _drive_item(f, CHILD, "application/pdf", target_content, host=google_api.API_HOST)
    meta = _drive_item(f, FID, mime, content_id, host=google_api.API_HOST)
    page = _row(f, "https://w.nknu.edu.tw/a/p1", kind="page", kind0="page", sniff="html", page_type="t:ok")
    att = _row(f, "https://w.nknu.edu.tw/files/a.pdf", status=302, sniff=None, final_url=google_api.item_url(FID))
    f.x("INSERT INTO page_res(page_id, res_key, how, role, need, res_id) VALUES (?,?,?,?,?,?)",
        (page, "https://w.nknu.edu.tw/files/a.pdf", "get", "file", "must", att))
    gone = _row(f, "https://w.nknu.edu.tw/a/p2", kind="page", kind0="page", status=302, sniff=None,
                final_url=google_api.item_url(FID))
    res = f.q("SELECT result FROM page_resource WHERE page_id=?", (page,))[0][0]
    assert res == want
    level = {r["id"]: r for r in f.q("SELECT * FROM page_completeness")}
    assert (level[page]["level"] == "complete") == (want in ("ok", "rule"))
    # a page sent to the item: its own result is the item's, and it is judged with its own entries (none here)
    assert level[gone]["own"] == want and level[gone]["content_id"] == gone
    assert (level[gone]["level"] == "complete") == (want == "ok")
    assert meta  # the metadata row itself stays ok: it is evidence only
    assert f.q("SELECT result FROM url_result WHERE id=?", (meta,))[0][0] == "ok"


def test_the_drive_types_read_without_a_download_are_google_apis_own():
    assert set(outcome.DRIVE_READ_TYPES) == {google_api.FOLDER, google_api.SHORTCUT, google_api.FORM,
                                             *google_api.EXPORT}
    assert outcome.GOOGLE_APPS == google_api.GOOGLE_APPS


# ---------------------------------------------------------------- ② a detail a press brings back, empty (review 2)

@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.sink.close()
    w.f.close()


def _webidx_detail(body: bool, files: bool) -> str:
    html = fx("webidx_detail.html")
    if not body:
        html = re.sub(r'(<div class="detail-body">).*?(</div>)', r"\1 \2", html, count=1, flags=re.DOTALL)
    if not files:
        html = re.sub(r'<div class="detail-attachments">.*?</ul></div>', "", html, flags=re.DOTALL)
    return html


@pytest.mark.parametrize(("body", "files"), [(True, True), (False, True), (True, False), (False, False)])
def test_a_detail_a_press_brings_back_is_checked_for_content_as_one_got_by_get(world, body, files):
    html = _webidx_detail(body, files)
    empty = profiles.empty_detail("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=3&newsidx=1", html)
    assert empty == (not body and not files)  # §10.1 nonempty, as a GET of the same detail is judged
    row, _got = world.stored(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    a, target = world.press()
    assert a["kind"] == "detail"
    world.answer(a, target.url, html)
    result = world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0]
    assert result == ("thin" if empty else "ok")
    entry = world.f.q("SELECT result FROM page_resource WHERE page_id=? AND res_key=?",
                      (row["id"], f"pb:detail:{a['row_key']}"))[0][0]
    assert entry == result  # the list page lacks it: 要人看, never 抓到了


@pytest.mark.parametrize(("inner", "want"), [
    ("<h4>出差綜合保險共同供應契約，請查照。</h4><p>主旨：轉知共同供應契約。</p>", "ok"),
    ("<h4>出差綜合保險共同供應契約，請查照。</h4>", "thin"),  # the title alone
    ('<h4>出差綜合保險共同供應契約，請查照。</h4><img src="/upload/a.jpg" alt="">', "ok"),
    ('<h4>出差綜合保險共同供應契約，請查照。</h4><a href="/upload/a.pdf">附件</a>', "ok"),
])
def test_a_clv2_detail_a_press_brings_back_needs_more_than_its_title(world, inner, want):
    world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a, target = world.press()
    html = re.sub(r'<div id="divContent">.*?</div>', f'<div id="divContent">{inner}</div>', fx("clv2_detail_ok.html"),
                  flags=re.DOTALL)
    world.answer(a, target.url, html)
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == want


# ---------------------------------------------------------------- ④ a rule that names a button's text (review 4)

SSO = "sso.nknu.edu.tw/news"


def test_a_rule_that_names_a_text_holds_only_for_a_button_with_that_text():
    assert profiles.button(SSO, "ctl09", "", "查詢").decision == "never"
    assert profiles.button(SSO, "ctl12", "", "附件下載") is None  # another bare ctlNN: nobody decided
    assert profiles.button(SSO, "ctl12", "", None) is None  # a text not known never borrows a rule's text
    assert profiles.decision(SSO, "postback:ctl*", "查詢") == "never"
    assert profiles.decision(SSO, "postback:ctl*", "附件下載") == "unknown"
    assert profiles.decision(SSO, "postback:ctl*") == "unknown"
    assert profiles.decision(SSO, "postback:ctl*$btnLangE") == "never"  # no text in the rule: as before


def _sso(tmp_path, html, url):
    from tests.archive.test_round1_e2e import PLAN

    crawler, net, _clock = make(tmp_path, {"robots.txt": snap("x", 404), "lastnews.aspx": lambda u: snap(u, html=html),
                                           "subjectlist.aspx": lambda u: snap(u, html=html),
                                           "attachedfile.aspx": lambda u: snap(u, body=PDF, headers={
                                               "content-type": "application/pdf"})},
                                queues={"sso.nknu.edu.tw": "ip:sso"}, plan=PLAN)
    crawler.seed_url(url)
    for _ in range(3):
        drive(crawler, net, "ip:sso")
    f = crawler.frontier
    row = f.q("SELECT * FROM url WHERE url = ? COLLATE NOCASE", (url,))[0]
    return f, {r["res_key"]: r for r in f.q("SELECT * FROM page_res WHERE page_id=? AND res_key LIKE 'postback:%'",
                                            (row["id"],))}


_DL = "<a id=\"ctl00_phMain_lbDl\" href=\"javascript:__doPostBack('ctl12','')\">附件下載</a>"
_QUERY = "<button onclick=\"__doPostBack('ctl09','')\" type=\"button\">查詢</button>"


def test_a_bare_ctl_button_that_is_no_search_on_an_sso_detail_page_waits_for_a_person(tmp_path):
    from tests.archive.test_round1_e2e import sso_detail, unid

    url = "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=" + unid(1)
    _f, pr = _sso(tmp_path, sso_detail(1).replace("</body>", _DL + "</body>"), url)
    assert (pr["postback:ctl*"]["need"], pr["postback:ctl*"]["why"]) == ("must", "unhandled:unknown")


@pytest.mark.parametrize("order", ["search_first", "download_first"])
def test_one_key_two_buttons_the_one_nobody_decided_is_written(tmp_path, order):
    from tests.archive.test_round1_e2e import sso_list

    html = sso_list("SCHOOLADMIN", 1)
    html = html.replace(_QUERY, _QUERY + _DL) if order == "search_first" else html.replace(_QUERY, _DL + _QUERY)
    _f, pr = _sso(tmp_path, html, "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=SCHOOLADMIN")
    entry = pr["postback:ctl*"]
    assert (entry["need"], entry["why"], entry["text"]) == ("must", "unhandled:unknown", "附件下載")


# ---------------------------------------------------------------- ⑤ a button in a press's answer (reviews 5 and 9)

_BTN7 = "<a href=\"javascript:__doPostBack(&#39;uDataContentPos2$downloadBtn7&#39;,&#39;&#39;)\">附件二</a>"


def _unpressed(w, list_id):
    return {r["res_key"]: (r["need"], r["why"]) for r in w.f.q(
        "SELECT res_key, need, why FROM page_res WHERE page_id=? AND why='unhandled:unpressed'", (list_id,))}


def test_a_button_to_press_in_a_detail_answer_that_nobody_presses_is_written_on_the_list(world):
    row, _got = world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a, target = world.press()
    html = fx("clv2_detail_ok.html").replace("</p></div>", "</p>" + _BTN7 + "</div>")
    world.answer(a, target.url, html)
    assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "done"
    assert _unpressed(world, row["id"]) == {"postback:uDataContentPos2$downloadBtn7": ("must", "unhandled:unpressed")}
    # the list fetched again (a new epoch): the detail is not pressed again, what its answer held is written again
    row2, snap2 = world.page(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    world.chains.page_stored(row2, fx("clv2_index.html"), world.warc, snap2.warc_offset)
    assert "postback:uDataContentPos2$downloadBtn7" in _unpressed(world, row["id"])
    assert world.f.q("SELECT count(*) FROM action WHERE row_key=? AND kind='detail'", (a["row_key"],))[0][0] == 1


def test_an_answer_that_shows_the_whole_list_again_names_nothing_new(world):
    row, _got = world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a, target = world.press()
    again = fx("clv2_index.html").replace("</form>", '<div id="divContent"><h4>出差綜合保險共同供應契約，請查照。</h4>'
                                                     "<p>主旨：轉知。</p></div></form>")
    world.answer(a, target.url, again)
    assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "done"
    assert _unpressed(world, row["id"]) == {}  # the list's own buttons: pressed by the list's presses already


# ---------------------------------------------------------------- ③ an SSO unit site's lists walked by GET (review 3)

WEB = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31"


def _webidx(rows: list[str], pager: str = "", grid: str = "news") -> str:
    """A WebIdx page in the shape of the school's (made-up titles): a news or items grid, and its GET pager box."""
    if grid == "news":
        body = "".join(f"<div class='news-row '><div class=\"col-date\">2026-10-{20 - i:02d}</div><div class=\"col-cat\">"
                       f"<span class=\"cat-tag\">公告</span></div><div class=\"col-title\"><a id=\"ucNews_rptNews_ctl0{i}"
                       f"_lnkShowDetail\" href=\"javascript:__doPostBack(&#39;ucNews$rptNews$ctl0{i}$lnkShowDetail&#39;,"
                       f"&#39;&#39;)\">{t}</a></div></div>" for i, t in enumerate(rows, start=1))
        grid_html = f'<div class="news-view-container"><div class="news-list-wrapper">{body}</div>{pager}</div>'
    else:
        body = "".join(f'<div class="items-row"><div class="col-cat"><span class="cat-tag">表單</span></div>'
                       f'<div class="col-title">{t}</div><div class="col-dowload"><a id="ucAllTypeList_rptItems_ctl0{i}'
                       f"_lnkDownloadFile\" href=\"javascript:__doPostBack(&#39;ucAllTypeList$rptItems$ctl0{i}"
                       f"$lnkDownloadFile&#39;,&#39;&#39;)\">下載</a></div></div>" for i, t in enumerate(rows, start=1))
        grid_html = f'<div class="items-view-container"><div class="items-list-wrapper">{body}</div>{pager}</div>'
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>單位網站</title></head><body>'
            '<form method="post" action="/services/web/default.aspx?webidx=31" id="form1">'
            '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS-WEB" />'
            f"<main>{grid_html}</main></form></body></html>")


def _pager_box(base: str, n: int, last: int, box: str = "news-pagination") -> str:
    prev = f"<a href='{base}&pageNum={n - 1}' class='page-btn '>上一頁</a>" if n > 1 else \
        "<a href='#' class='page-btn disabled'>上一頁</a>"
    nxt = f"<a href='{base}&pageNum={n + 1}' class='page-btn '>下一頁</a>" if n < last else \
        "<a href='#' class='page-btn disabled'>下一頁</a>"
    nums = "".join(f"<a href='{base}&pageNum={i}' class='page-num {'active' if i == n else ''}'>{i}</a>"
                   for i in range(1, last + 1))
    return f"<div class='{box}'>{prev}<div class='page-numbers'>{nums}</div>{nxt}</div>"


def _web_crawl(tmp_path, pages: dict[str, str], seed_url: str, rounds: int = 8):
    from tests.archive.plans import plan, site

    p = plan(site("sso.nknu.edu.tw/web/31", name="某單位", home=seed_url))
    routes = {"robots.txt": snap("x", 404)}

    def answer(html):
        # a WARC position as the real sink gives one (the fake network writes no WARC; nothing here is pressed)
        return lambda u: replace(snap(u, html=html), warc_offset=0, warc_len=1)

    for key in sorted(pages, key=len, reverse=True):  # the longest address first: the fake network matches by part
        routes[key] = answer(pages[key])
    crawler, net, clock = make(tmp_path, routes, queues={"sso.nknu.edu.tw": "ip:sso"}, plan=p)
    crawler.seed_url(seed_url)
    for _ in range(rounds):
        drive(crawler, net, "ip:sso")
        clock.t += 10
    return crawler.frontier


def _walk_of(f, url):
    got = f.q("SELECT lw.ended_by FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE u.url = ? COLLATE NOCASE",
              (url,))
    return got[0][0] if got else None


def _list_level(f, url):
    got = f.q("SELECT level, list_unchecked FROM page_completeness WHERE id=(SELECT id FROM url WHERE url = ? "
              "COLLATE NOCASE)", (url,))
    return tuple(got[0]) if got else None


def test_a_one_page_news_list_of_a_unit_site_ends_on_its_only_page(tmp_path):
    entry = WEB + "&selpage=MoreNews"
    f = _web_crawl(tmp_path, {"selpage=MoreNews": _webidx(["獎學金申請公告", "研習活動報名"])}, entry)
    assert _walk_of(f, entry) == "last_page"
    assert _list_level(f, entry)[1] == 0  # never 列表没核对


def test_a_one_page_list_with_no_rows_and_no_words_for_none_claims_no_end(tmp_path):
    entry = WEB + "&selpage=MoreNews"
    f = _web_crawl(tmp_path, {"selpage=MoreNews": _webidx([])}, entry)
    assert _walk_of(f, entry) == "mismatch:empty"


def test_the_first_page_of_a_paged_news_list_is_walked_with_the_pages_its_pager_box_names(tmp_path):
    entry = WEB + "&selpage=MoreNews"
    base = "/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
    pages = {f"sort=date&pagenum={n}": _webidx([f"第{n}頁公告甲", f"第{n}頁公告乙"], _pager_box(base, n, 3))
             for n in (1, 2, 3)}
    pages["selpage=MoreNews"] = _webidx(["第1頁公告甲", "第1頁公告乙"], _pager_box(base, 1, 3))
    f = _web_crawl(tmp_path, pages, entry)
    pagers = f.q("SELECT count(*) FROM page_res WHERE role='pager' AND page_id=(SELECT id FROM url WHERE url=?)",
                 (scope.canonical(entry),))[0][0]
    assert pagers >= 3  # 1, 2, 3 (and 下一頁) are the list's pager, whatever sort=date adds to their address
    assert _walk_of(f, entry) == "last_page"
    assert _walk_of(f, "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
                       "&pagenum=3") == "last_page"


def test_an_items_list_paged_by_get_is_walked_to_its_last_page(tmp_path):
    entry = WEB + "&menuid=214"
    base = "/services/web/default.aspx?webidx=31&menuid=214"
    pages = {f"menuid=214&pagenum={n}": _webidx([f"表單{n}-甲", f"表單{n}-乙"], _pager_box(base, n, 2, "items-pagination"),
                                                grid="items") for n in (1, 2)}
    pages["menuid=214"] = pages["menuid=214&pagenum=1"]
    f = _web_crawl(tmp_path, pages, entry)
    assert {_walk_of(f, u) for u in (entry, entry + "&pagenum=2")} == {"last_page"}
    assert _walk_of(f, entry + "&pagenum=1") is None  # page 1 is the entry's own row now (11-03, P05)


def test_the_news_on_a_unit_sites_home_page_is_read_as_the_list_it_is(tmp_path):
    base = "/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
    home = _webidx(["第1頁公告甲", "第1頁公告乙"], _pager_box(base, 1, 2))
    pages = {f"sort=date&pagenum={n}": _webidx([f"第{n}頁公告甲"], _pager_box(base, n, 2)) for n in (1, 2)}
    pages["webidx=31"] = home
    f = _web_crawl(tmp_path, pages, WEB)
    home_id = f.row(WEB)["id"]
    assert not f.q("SELECT 1 FROM page_res WHERE page_id=? AND why='unhandled:unpressed'", (home_id,))
    assert {r["target"] for r in f.q("SELECT target FROM action WHERE list_id=? AND kind='detail'", (home_id,))} == {
        "ucNews$rptNews$ctl01$lnkShowDetail", "ucNews$rptNews$ctl02$lnkShowDetail"}
    assert _walk_of(f, WEB) == "last_page"


def test_a_list_whose_pager_box_nobody_reads_is_never_taken_for_a_one_page_list(tmp_path):
    entry = WEB + "&selpage=MoreNews"
    base = "/services/web/default.aspx?webidx=31&selpage=MoreNews&sort=date"
    box = _pager_box(base, 1, 3, "page-list")  # a class the platform file does not name (a site that changed)
    f = _web_crawl(tmp_path, {"selpage=MoreNews": _webidx(["第1頁公告甲", "第1頁公告乙"], box)}, entry, rounds=1)
    assert _walk_of(f, entry) == "mismatch:unknown_button"


# ---------------------------------------------------------------- ⑥ the id probe (review 6)

def _probe_run(tmp_path, probe_answer):
    from tests.archive.test_round1_e2e import PLAN, sso_detail, unid

    urls = [f"https://sso.nknu.edu.tw/News/lastNews.aspx?UNID={unid(n)}" for n in (1, 2, 3, 4)]
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "0" * 40: probe_answer,
                                          "lastnews.aspx": lambda u: snap(u, html=sso_detail(1))},
                               queues={"sso.nknu.edu.tw": "ip:sso"}, plan=PLAN)
    for u in urls:
        crawler.seed_url(u)
    for _ in range(6):
        drive(crawler, net, "ip:sso")
        clock.t += 60
    f = crawler.frontier
    done = f.q("SELECT count(*) FROM url WHERE url LIKE '%lastnews.aspx%' AND state='done' AND status=200")[0][0]
    return sum("0" * 40 in u for u in net.seen), done, f


def test_a_probe_that_timed_out_is_asked_at_most_twice_in_a_run(tmp_path):
    from tests.archive.test_crawler_round1 import timeout_err

    probes, done, f = _probe_run(tmp_path, timeout_err())
    assert done == 4  # every detail page is fetched
    assert probes == 2  # a timeout may have reached the server: not before every detail page
    assert f.q("SELECT count(*) FROM id_probe")[0][0] == 0  # no answer: nothing to judge gone_id by


def test_a_probe_that_never_reached_the_server_is_asked_again_with_a_later_page(tmp_path):
    from tests.archive.test_crawler_round1 import conn_err

    probes, done, _f = _probe_run(tmp_path, conn_err())
    assert done == 4 and probes == 4  # a refused connection sent nothing: asked again, at no cost to the server


def test_a_probe_that_answered_is_asked_once(tmp_path):
    probes, done, f = _probe_run(tmp_path, lambda u: snap(u, 404))
    assert done == 4 and probes == 1 and f.q("SELECT status FROM id_probe")[0][0] == 404


# ---------------------------------------------------------------- ⑪ drive_item looked up by its index (review 11)

def _plan_of(f, sql, args):
    return " ".join(r[3] for r in f.q("EXPLAIN QUERY PLAN " + sql, args))


def test_drive_items_are_looked_up_by_file_and_by_folder_through_an_index(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        by_file = _plan_of(f, "SELECT d.*, u.state FROM drive_item d JOIN url u ON u.id = d.url_id WHERE d.file_id=? "
                              "ORDER BY d.url_id LIMIT 1", ("x",))
        by_folder = _plan_of(f, "SELECT file_id, name FROM drive_item WHERE parent_id=? ORDER BY url_id", ("x",))
        assert "SCAN d" not in by_file and "drive_item_file" in by_file, by_file
        assert "SCAN drive_item" not in by_folder and "drive_item_parent" in by_folder, by_folder
        # a frontier.db made before the indexes gets them when it is opened
        f.x("DROP INDEX drive_item_file")
        f.x("DROP INDEX drive_item_parent")
    finally:
        f.close()
    f = Frontier(tmp_path / "frontier.db")
    try:
        names = {r[0] for r in f.q("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='drive_item'")}
        assert {"drive_item_file", "drive_item_parent"} <= names
    finally:
        f.close()


# ---------------------------------------------------------------- ⑫ an archived record that cannot be read (review 12)

def test_a_record_that_cannot_be_read_back_is_a_value_error_for_every_reader(world):
    from nknu.archive import postback

    snap1 = world.archive(CLV2, fx("clv2_index.html"))
    world.sink.close()  # written out, as a finished file is
    with pytest.raises(ValueError):
        postback.read_response(world.root, world.warc, snap1.warc_offset + 7)  # warcio's ArchiveLoadFailed inside


def test_a_press_whose_page_cannot_be_read_back_is_mismatch_and_nothing_is_sent(world):
    world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a = world.chains.next_action("ip:140.127.40.173")
    world.f.x("UPDATE action SET form_src_offset = form_src_offset + 7 WHERE id=?", (a["id"],))
    world.sink.close()
    a = world.f.q("SELECT * FROM action WHERE id=?", (a["id"],))[0]
    assert world.chains.target_for(a) is None
    got = world.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert got["state"] == "mismatch" and got["reason"].startswith("form_src")


def test_what_a_page_brought_is_skipped_when_its_record_cannot_be_read_and_the_page_stays_got(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    w = World(tmp_path / "w")  # a real WARC (the crawler tests' fake sink writes none)
    good = w.archive("https://w.nknu.edu.tw/x", "<html><body>甲</body></html>")
    w.sink.close()
    crawler.root = w.root
    assert crawler._archived_text(w.warc, good.warc_offset) is not None
    assert crawler._archived_text(w.warc, good.warc_offset + 7) is None  # never a crash after the page is done
    w.f.close()


# ---------------------------------------------------------------- ⑬ a file button's rule asked again (review 13)

@pytest.mark.parametrize("rule", ["kept", "removed", "never"])
def test_a_file_button_left_from_an_earlier_run_is_sent_only_while_its_rule_still_says_so(world, monkeypatch, rule):
    from dataclasses import replace as _replace

    from nknu.archive import links

    html = fx("ee_course.html")
    row, snap1 = world.page("https://ee.nknu.edu.tw/course.html", "ee.nknu.edu.tw", html, queue="ip:61.218.135.171")
    for c in links.extract(html, row["url"]).clicks:
        world.chains.click_action(row, profiles.click_plan("ee.nknu.edu.tw", c, html, row["url"]), world.warc,
                                  snap1.warc_offset)
    if rule != "kept":  # between two runs the owner takes the rule out, or says never
        writings = (profiles.Writing(r"click:fileAjax\.html\(.*\)", "never", "不再按（测试）"),) if rule == "never" else ()
        monkeypatch.setattr(profiles, "PROFILES", tuple(
            _replace(p, clicks=(), writings=p.writings + writings) if p.name == "ee" else p for p in profiles.PROFILES))
    a = world.chains.next_action("ip:61.218.135.171")
    target = world.chains.target_for(a)
    got = world.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    if rule == "kept":
        assert target is not None and target.method == "POST"
    else:
        assert target is None and (got["state"], got["reason"]) == ("mismatch", "rule_changed")


def test_a_misplaced_offset_never_reads_as_some_other_record(world):
    """warcio takes a run of bytes with five spaces in a line for an old ARC record (its rec_type is response, no
    HTTP headers, no body): read at a wrong offset, compressed bytes sometimes look like one. Every record of this
    archive is WARC/1.1, so anything else is no record (the reviewer's run: 1 in 7 gave control_missing)."""
    from nknu.archive import postback

    snaps = [world.archive(url, fx(name)) for url, name in (
        (CLV2, "clv2_index.html"), (MORE, "webidx_morenews.html"), (MORE, "webidx_detail.html"),
        (CLV2, "clv2_detail_ok.html"), ("https://c.nknu.edu.tw/aca/newslist.aspx?gi=1", "cnknu_list_p1.html"),
        ("https://c.nknu.edu.tw/aca/newslist.aspx?gi=1", "cnknu_list_p2.html"))]
    world.sink.close()
    read_back = []
    for s in snaps:
        assert postback.read_response(world.root, world.warc, s.warc_offset)[1] == 200
        for delta in range(1, 400):
            try:
                read_back.append((s.warc_offset, delta, postback.read_response(world.root, world.warc,
                                                                               s.warc_offset + delta)[:2]))
            except ValueError:
                pass
    assert read_back == []
