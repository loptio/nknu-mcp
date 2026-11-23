"""Round one's gap ⑪ (docs/round1-gaps-2026-11-08.md; the owner 11-08 19:58 had ⑩⑪⑫ filled in, how each is done
【Claude 的执行办法】), each fix with tests that fail without it: the licensed software list (sso.nknu.edu.tw/software)
shows each software as an ASP.NET ImageButton (<input type="image">), which calls no __doPostBack, so the crawler never
saw it.

- its platform file (profiles.SSO_SOFTWARE): the list page presses each software's icon (one GET holds all of them),
  a software's page presses its 安裝說明 folder only, never an installer folder; the view switches and 「返回」 need
  nothing;
- an icon's press is its form with the click's coordinates (name.x, name.y), as a browser sends it; a page that no
  longer holds the icon, or a form the platform file does not name, sends nothing;
- a detail press answered 302 to another page of its own site is that page (detail_redirect), queued as the list
  page's resource; a 302 to a site's home or a sign-in page stays a lapsed session (§23.9 A2);
- a detail's answer that shows the button pressed again (an opened folder) is never pressed from that answer;
- an element two of the postback selectors match (a named input with an onclick) is one button, pressed once;
- a Drive upload whose name says a program or a disk image is listed only, its name and size (media:drive_installer);
- `retry unpressed` after a rejudge puts back the list page and a software page, the folder button's text kept.
All offline: fake networks and clocks, made-up pages (tests/archive/fixtures_1108), no person's name."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nknu.archive import export, google_api, links, outcome, postback, profiles, reconcile, scope
from nknu.archive.crawler import Crawler
from nknu.archive.frontier import Frontier
from nknu.archive.postback import IMAGE_ARG, UNPRESSED, Chains, read_page
from tests.archive.plans import OpenPlan
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site as make_site
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_export_1027 import _res
from tests.archive.test_export_step3_1108 import _with
from tests.archive.test_google_api import DRIVE_LINK, FILE_ID, FOLDER_ID, add_page, answer, entries, item, use
from tests.archive.test_pilot_fixes import PDF, SSO_Q, PressNet, work
from tests.archive.test_postback import World

FIX = Path(__file__).resolve().parent / "fixtures_1108"
SITE = "sso.nknu.edu.tw/software"
LIST = "https://sso.nknu.edu.tw/software/campussoftware.aspx"
PAGE7 = "https://sso.nknu.edu.tw/software/campussoftware.aspx?csoftid=7"
ICON0, ICON1 = "ctl00$phMain$dataList1$ctrl0$uImage", "ctl00$phMain$dataList1$ctrl1$uImage"
NOTES_BUTTON = "ctl00$phMain$ascxDownload_ListMode$btFolder_43"
GUIDE = "https://sso.nknu.edu.tw/software/files/guide.pdf"
PDF_HEADERS = {"content-type": "application/pdf", "content-disposition": 'attachment; filename="guide.pdf"'}


def fx8(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


LIST_HTML = fx8("software_list.html")
PAGE_HTML = fx8("software_page.html")
# what pressing 「安裝說明」 brings: the same page, that folder open (its header, a guide on the school's host), the
# folder list as before (安裝說明 among its buttons again)
NOTES_HTML = PAGE_HTML.replace(">Windows 作業系統</label>", ">安裝說明</label>").replace(
    '<a href="/software/files/setup_x64.exe" target="_blank" onclick="addClickCount(&#39;501&#39;)">安裝程式（測試）</a>',
    '<a href="/software/files/guide.pdf" target="_blank">安裝說明文件（測試）</a>')
# the second software's page (軟體乙, csoftid=8)
PAGE8_HTML = PAGE_HTML.replace("軟體甲", "軟體乙").replace("csoftid=7", "csoftid=8")


def test_the_fixtures_differ_where_the_tests_say():
    assert NOTES_HTML != PAGE_HTML and "guide.pdf" in NOTES_HTML and "setup_x64.exe" not in NOTES_HTML
    assert ">安裝說明</label>" in NOTES_HTML and "軟體乙" in PAGE8_HTML


# ---------------------------------------------------------------- the platform file


def test_the_list_page_presses_each_softwares_icon(tmp_path):
    w = World(tmp_path)
    row, got = w.stored(LIST, SITE, LIST_HTML)
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["argument"], a["profile"], a["form_id"], a["row_text"]) for a in acts] == [
        ("detail", ICON0, IMAGE_ARG, "sso_software/software_items", "uForm", "軟體甲"),
        ("detail", ICON1, IMAGE_ARG, "sso_software/software_items", "uForm", "軟體乙")]
    assert (got.page_role, got.ended_by, got.unplanned) == ("list", "last_page", ())  # one GET holds the list
    pr = read_page(profiles.SSO_SOFTWARE, LIST, LIST_HTML, site=SITE, list_url=LIST)
    assert (pr.rowset, pr.walk, pr.unknown, [r.title for r in pr.rows]) == ("software_items", "one_get", (),
                                                                            ["軟體甲", "軟體乙"])
    res = {r["res_key"]: (r["role"], r["need"], r["action_id"]) for r in w.f.q(
        "SELECT * FROM page_res WHERE page_id=? AND res_key LIKE 'pb:%'", (row["id"],))}
    assert sorted(v[:2] for v in res.values()) == [("detail", "must")] * 2
    for key, want in (("postback:ctl*$phMain$linkList", "ignore"), ("postback:ctl*$btnLangE", "never"),
                      (f"postback:ctl*$phMain$dataList1$ctrl0$uImage|{IMAGE_ARG}", "press")):
        assert profiles.decision(SITE, key, "") == want, key
    w.sink.close()
    w.f.close()


def test_an_image_button_no_rule_names_stays_unseen():
    """english's report viewer pages hold ImageButtons too (the toolbar): no platform file names them, so nothing
    changes there; a software page's own list is no list on a software's page (Rows.query)."""
    toolbar = ('<html><body><form id="form1" action="./x.aspx" method="post"><input type="hidden" name="__VIEWSTATE" '
               'value="V"><input type="image" name="ReportViewer1$ctl05$ctl00$Next$ctl00$ctl00" src="n.gif">'
               "</form></body></html>")
    pr = read_page(profiles.ENGLISH, "https://english.nknu.edu.tw/x.aspx", toolbar, site="english.nknu.edu.tw")
    assert (pr.presses, pr.unknown, pr.unplanned) == ((), (), ())
    inside = read_page(profiles.SSO_SOFTWARE, PAGE7, LIST_HTML, site=SITE, list_url=PAGE7)
    assert inside.rowset is None and inside.walk == "none"
    disabled = LIST_HTML.replace('class="img-responsive"', 'class="img-responsive" disabled', 1)
    pr = read_page(profiles.SSO_SOFTWARE, LIST, disabled, site=SITE, list_url=LIST)
    assert [p.target for p in pr.presses] == [ICON1]  # a disabled button sends nothing


def test_a_button_an_element_writes_twice_is_pressed_once():
    """An element that two of the postback selectors match (a named submit input with an onclick, now that inputs are
    read for their ImageButtons; an <a href="#" onclick="__doPostBack(…)">) is one button of its row, never two."""
    url = "https://sso.nknu.edu.tw/regulations/default.aspx?cpara=TESTPARA2"
    html = ('<html><body><form id="uForm" action="./default.aspx?cPara=TESTPARA2" method="post"><input type="hidden" '
            'name="__VIEWSTATE" value="VS-REG-2"><table id="ctl00_phMain_reggv">'
            '<tr><td><a id="ctl00_phMain_reggv_ctl02_fileview">某某辦法（測試）</a></td><td><input type="submit" '
            'name="ctl00$phMain$reggv$ctl02$downloadBtn" value="檔案下載" '
            "onclick=\"__doPostBack('ctl00$phMain$reggv$ctl02$downloadBtn','')\"></td></tr>"
            '<tr><td><a id="ctl00_phMain_reggv_ctl03_fileview">某某要點（測試）</a></td><td><a href="#" '
            "onclick=\"__doPostBack('ctl00$phMain$reggv$ctl03$downloadBtn','')\">檔案下載</a></td></tr>"
            "</table></form></body></html>")
    pr = read_page(profiles.SSO_REGULATIONS, url, html, site="sso.nknu.edu.tw/regulations", list_url=url)
    assert [(p.kind, p.target, "/" in p.row_key) for p in pr.presses] == [
        ("download", "ctl00$phMain$reggv$ctl02$downloadBtn", False),
        ("download", "ctl00$phMain$reggv$ctl03$downloadBtn", False)]


def test_a_software_page_presses_only_its_install_notes_folder(tmp_path):
    w = World(tmp_path)
    row, got = w.stored(PAGE7, SITE, PAGE_HTML)
    acts = w.actions(row["id"])
    assert [(a["kind"], a["target"], a["argument"], a["profile"], a["row_text"]) for a in acts] == [
        ("detail", NOTES_BUTTON, "", "sso_software/software_folders", "安裝說明")]
    assert (got.page_role, got.unplanned) == (None, ())
    folder = "postback:ctl*$phMain$ascxDownload_ListMode$btFolder_41"
    assert profiles.decision(SITE, folder, "Windows 作業系統") == "ignore"
    assert profiles.decision(SITE, folder, "Mac作業系統") == "ignore"
    assert profiles.decision(SITE, folder, "安裝說明") == "press"
    assert profiles.decision(SITE, folder) == "ignore"  # a folder whose text is not known is never pressed
    for key in ("postback:ctl*$phMain$btBack1", "postback:ctl*$phMain$download_linkGrid"):
        assert profiles.decision(SITE, key, "") == "ignore"
    w.sink.close()
    w.f.close()


def test_the_platform_file_claims_its_key_and_says_why():
    assert profiles.check_profiles() == []
    assert profiles.profile_for(LIST) is profiles.SSO_SOFTWARE
    assert profiles.page_kind(PAGE7) == "detail" and profiles.page_kind(LIST) is None  # csoftid=: free of the caps
    assert all(b.why.strip() for b in profiles.SSO_SOFTWARE.buttons)


# ---------------------------------------------------------------- an icon's press


def test_an_icons_press_is_its_form_with_the_clicks_coordinates(tmp_path):
    w = World(tmp_path)
    w.stored(LIST, SITE, LIST_HTML)
    _a, target = w.press()
    assert (target.method, target.url) == ("POST", LIST)
    # the form's own fields as written, then the coordinates (HTML Standard's entry list of an image button): no
    # __EVENTTARGET, which this form does not hold and no __doPostBack sets
    assert list(target.data) == [("__VIEWSTATE", "VS-SOFTWARE-LIST"), ("ctl00$phMain$SelTab", "1"),
                                 ("ctl00$phMain$SelTab_Software", "2"), ("__VIEWSTATEGENERATOR", "0000TEST"),
                                 ("__VIEWSTATEENCRYPTED", ""), (f"{ICON0}.x", "1"), (f"{ICON0}.y", "1")]
    w.sink.close()
    w.f.close()


@pytest.mark.parametrize("change, reason", [
    (("target", "ctl00$phMain$dataList1$ctrl9$uImage"), "control_missing"),
    (None, "form_not_in_profile:form9"),
])
def test_an_icon_the_page_no_longer_holds_or_a_form_not_named_sends_nothing(tmp_path, change, reason):
    w = World(tmp_path)
    html = LIST_HTML if change else LIST_HTML.replace('id="uForm"', 'id="form9"')
    row, _got = w.stored(LIST, SITE, html)
    if change:
        w.f.x(f"UPDATE action SET {change[0]}=? WHERE list_id=?", (change[1], row["id"]))
    a = w.chains.next_action(SSO_Q)
    assert w.chains.target_for(a) is None
    got = w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("mismatch", reason)
    assert postback.image_form(LIST_HTML, ICON0) == ('form[id="uForm"]', "")
    assert postback.image_form("<html><body><input type='image' name='x'></body></html>", "x") == (None,
                                                                                                    "control_missing")
    w.sink.close()
    w.f.close()


def test_the_install_notes_answer_is_never_pressed_again(tmp_path):
    w = World(tmp_path)
    row, _got = w.stored(PAGE7, SITE, PAGE_HTML)
    a, target = w.press()
    assert dict(target.data)["__EVENTTARGET"] == NOTES_BUTTON and target.url == PAGE7
    out = w.answer(a, target.url, NOTES_HTML)
    assert not out.restart
    done = w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert tuple(done) == ("done", None)
    assert len(w.actions(row["id"])) == 1  # the folder shown again in its own answer: not pressed again
    assert not w.f.q("SELECT 1 FROM page_res WHERE page_id=? AND why=?", (row["id"], UNPRESSED))
    w.sink.close()
    w.f.close()


def test_an_install_notes_answer_with_another_folder_open_is_a_mismatch(tmp_path):
    w = World(tmp_path)
    w.stored(PAGE7, SITE, PAGE_HTML)
    a, target = w.press()
    out = w.answer(a, target.url, PAGE_HTML)  # the page again, the Windows folder open: not what was pressed
    got = w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("mismatch", "title") and out.restart
    w.sink.close()
    w.f.close()


def test_an_icons_answer_in_place_plans_its_install_notes_with_its_own_row_set(tmp_path):
    w = World(tmp_path)
    row, _got = w.stored(LIST, SITE, LIST_HTML)
    a, target = w.press()
    w.answer(a, target.url, PAGE_HTML)  # the software's page in place of the list
    acts = w.actions(row["id"])
    assert [(x["kind"], x["target"], x["state"], x["profile"]) for x in acts] == [
        ("detail", ICON0, "done", "sso_software/software_items"),
        ("detail", ICON1, "pending", "sso_software/software_items"),
        ("detail", NOTES_BUTTON, "pending", "sso_software/software_folders")]
    w.sink.close()
    w.f.close()


# ---------------------------------------------------------------- a detail press answered with a redirect


@pytest.mark.parametrize("location, want", [
    ("campussoftware.aspx?cSoftID=7", PAGE7),
    ("/software/campussoftware.aspx?csoftid=8", "https://sso.nknu.edu.tw/software/campussoftware.aspx?csoftid=8"),
    ("/software/campussoftware.aspx", None),  # the list itself, the address pressed
    ("/software/", None),  # the site's home
    ("/software/default.aspx", None),
    ("/default.aspx", None),  # SSO's home: another site key
    ("/software/login.aspx?ReturnUrl=x", None),
    ("/software/x.aspx?aspxerrorpath=/y", None),  # the school's error page
    ("https://www.nknu.edu.tw/software/campussoftware.aspx?csoftid=7", None),  # another host
    ("", None),
])
def test_detail_redirect(location, want):
    assert postback.detail_redirect(location, LIST, LIST) == want
    assert postback.press_redirect("detail", location, LIST, LIST) == want
    assert postback.press_redirect("pager", location, LIST, LIST) is None


def test_an_icons_press_sent_home_is_still_a_lapsed_session(tmp_path):
    w = World(tmp_path)
    w.stored(LIST, SITE, LIST_HTML)
    a, target = w.press()
    out = w.answer(a, target.url, b"", status=302, headers={"location": "/default.aspx", "content-type": "text/html"})
    got = w.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0]
    assert tuple(got) == ("failed", "press_redirect") and out.restart and out.location is None
    w.sink.close()
    w.f.close()


# ---------------------------------------------------------------- the whole walk, fake network


def make(root: Path, routes):
    clock = Clock()
    net = PressNet(clock, routes)
    crawler = Crawler(root, plan=OpenPlan(), clock=clock, sleep=clock.sleep, client_factory=net,
                      resolver=lambda h: (SSO_Q if h == "sso.nknu.edu.tw" else f"ip:{h}", None, h),
                      netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None)
    return crawler, net


def presses(in_place: bool):
    """SSO's answers to the software pages' presses: an icon's, the software's page (in place, or a 302 to its own
    address); 安裝說明's, the notes folder."""

    def press(target):
        data = dict(target.data)
        if f"{ICON0}.x" in data:
            return snap(target.url, html=PAGE_HTML) if in_place else snap(
                target.url, 302, headers={"location": "campussoftware.aspx?cSoftID=7"})
        if f"{ICON1}.x" in data:
            return snap(target.url, html=PAGE8_HTML) if in_place else snap(
                target.url, 302, headers={"location": "campussoftware.aspx?cSoftID=8"})
        if data.get("__EVENTTARGET") == NOTES_BUTTON:
            return snap(target.url, html=NOTES_HTML)
        return snap(target.url, html=LIST_HTML)

    return press


@pytest.mark.parametrize("in_place", [True, False])
def test_each_software_and_its_install_notes_are_got_and_no_installer(tmp_path, in_place):
    crawler, net = make(tmp_path, {
        ("GET", "/robots.txt"): snap("x", 404),
        ("GET", "guide.pdf"): snap(GUIDE, body=PDF, headers=PDF_HEADERS),
        ("GET", "csoftid=7"): snap(PAGE7, html=PAGE_HTML),
        ("GET", "csoftid=8"): snap(PAGE7.replace("=7", "=8"), html=PAGE8_HTML),
        ("GET", "campussoftware.aspx"): snap(LIST, html=LIST_HTML),
        ("POST", "campussoftware.aspx"): presses(in_place),
    })
    seed(crawler, LIST)
    work(crawler, net, SSO_Q, rounds=80)
    f = crawler.frontier
    lst = f.row(LIST)
    posts = [u for m, u in net.seen if m == "POST"]
    assert len(posts) == 4  # two icons, each software's 安裝說明 once
    acts = f.q("SELECT a.*, r.result FROM action a JOIN action_result r ON r.id = a.id ORDER BY a.id")
    assert [(a["target"], a["state"]) for a in acts] == [
        (ICON0, "done"), (ICON1, "done"), (NOTES_BUTTON, "done"), (NOTES_BUTTON, "done")]
    if in_place:
        assert {a["list_id"] for a in acts} == {lst["id"]} and {a["result"] for a in acts} == {"ok"}
    else:  # each icon's 302 is the software's page, fetched by GET and pressed from there
        icons = acts[:2]
        assert [(a["result"], a["location"]) for a in icons] == [("redirect", PAGE7),
                                                                 ("redirect", PAGE7.replace("=7", "=8"))]
        page7 = f.row(PAGE7)
        assert (page7["state"], page7["found_via"], page7["owner_site"]) == ("done", "redirect", SITE)
        assert f.q("SELECT text FROM link WHERE src=? AND dst=?", (lst["id"], PAGE7))[0][0] == "[postback]"
        assert acts[2]["list_id"] == page7["id"]
    gets = [u for m, u in net.seen if m == "GET"]
    assert gets.count(GUIDE) == 1 and not [u for u in gets if u.endswith(".exe")]  # never an installer
    assert f.row(GUIDE)["state"] == "done"
    walk = f.q("SELECT ended_by FROM listwalk WHERE list_id=?", (lst["id"],))[0][0]
    assert walk == "last_page"
    assert not f.q("SELECT 1 FROM page_res WHERE why=?", (UNPRESSED,))
    # a software's page got by GET rests on its page type, which the browser check judges (as any new type): judged
    f.x("UPDATE page_type SET verdict='ok', source='manual'")
    bad = f.q("SELECT res_key, result FROM page_resource WHERE page_id=? AND result NOT IN ('ok', 'rule')",
              (lst["id"],))
    assert not bad, [tuple(x) for x in bad]
    f.close()


# ---------------------------------------------------------------- retry unpressed, after a rejudge


def test_a_rejudge_then_retry_unpressed_puts_back_the_list_and_the_software_page(tmp_path):
    """Round one read both pages with no platform file: the icons were never seen, the folders were unknown. Today's
    file read offline (rejudge) writes them unpressed, the folder's text kept (its rule names a text); `retry
    unpressed` puts both pages back, and each entry written is a button today's platform file says to press."""
    w = World(tmp_path)
    lst, _snap = w.page(LIST, SITE, LIST_HTML)
    page7, _snap = w.page(PAGE7, SITE, PAGE_HTML)
    w.f.add_page_res(page7["id"], [{"res_key": "postback:ctl*$phMain$ascxDownload_ListMode$btFolder_43",
                                    "how": "unhandled", "role": "click", "need": "must", "why": "unhandled:unknown",
                                    "text": "安裝說明"}])
    w.sink.close()
    w.f.close()
    reconcile.rejudge(tmp_path, [SITE], plan=make_plan(make_site(SITE, home=LIST)))
    f = Frontier(tmp_path / "frontier.db")
    got = {(r["page_id"], r["res_key"]): (r["need"], r["why"], r["text"]) for r in f.q(
        "SELECT * FROM page_res WHERE why=?", (UNPRESSED,))}
    assert got == {
        (lst["id"], f"postback:ctl*$phMain$dataList1$ctrl0$uImage|{IMAGE_ARG}"): ("must", UNPRESSED, None),
        (lst["id"], f"postback:ctl*$phMain$dataList1$ctrl1$uImage|{IMAGE_ARG}"): ("must", UNPRESSED, None),
        (page7["id"], "postback:ctl*$phMain$ascxDownload_ListMode$btFolder_43"): ("must", UNPRESSED, "安裝說明")}
    assert not f.q("SELECT 1 FROM action")  # a rejudge presses nothing
    for (_pid, key), (_need, _why, text) in got.items():  # with its text: the folder's rule names 「安裝說明」
        assert profiles.decision(SITE, key, text) == "press", key
    assert Chains(f, tmp_path).retry_unpressed([SITE]) == (2, 3)
    for pid in (lst["id"], page7["id"]):
        back = f.row_by_id(pid)
        assert (back["state"], back["reason"], back["prio"]) == ("pending", "chain_restart", 0)
    f.close()


# ---------------------------------------------------------------- installers on Drive: listed only


@pytest.mark.parametrize("name, mime", [
    ("setup_x64.msi", "application/x-msi"), ("Tool.EXE", "application/x-msdownload"),
    ("win11.iso", "application/x-iso9660-image"), ("app.dmg", "application/x-apple-diskimage"),
    ("pkg_for_mac.pkg", "application/octet-stream"), ("app.apk", "application/vnd.android.package-archive"),
])
def test_a_drive_installer_is_listed_by_its_name_only(f, name, mime):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, google_api.item_url(FILE_ID), item(FILE_ID, mime, name=name, size="74797056"))
    assert f.row(google_api.media_url(FILE_ID)) is None  # no download
    e = entries(f, page)[f"drive:{FILE_ID}"]
    assert (e["need"], e["why"]) == ("no", "media:drive_installer")
    d = f.q("SELECT name, size FROM drive_item")[0]
    assert (d["name"], d["size"]) == (name, 74797056)  # 只记档名和大小


def test_a_guide_and_a_folder_named_like_a_disk_image_are_read_as_before(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, google_api.item_url(FILE_ID), item(FILE_ID, "application/pdf", name="安裝說明.pdf", size="2000"))
    assert f.row(google_api.media_url(FILE_ID)) is not None
    use(f, page, f"https://drive.google.com/drive/folders/{FOLDER_ID}")
    answer(f, google_api.item_url(FOLDER_ID), item(FOLDER_ID, google_api.FOLDER, name="Windows.iso"))
    assert f.row(google_api.list_url(FOLDER_ID)) is not None  # a folder is listed, whatever its name


def test_an_installer_downloaded_before_the_rule_is_no_longer_needed(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, google_api.item_url(FILE_ID), item(FILE_ID, "application/x-msi", name="old.msi", size="2000"))
    # as round one left it (lis's two .msi): the download made and got, the page's entry pointing at it
    meta = f.row(google_api.item_url(FILE_ID))
    assert f.add(google_api.media_url(FILE_ID), host=google_api.API_HOST, site=google_api.API_HOST, unit=None,
                 tier=0, kind="file", depth=1, found_via="drive", found_from=meta["id"], owner_site=page["owner_site"],
                 kind0="file")
    cid = f.row(google_api.media_url(FILE_ID))["id"]
    f.finish(cid, state="done", status=200, sniff="ole", kind="file")
    f.x("UPDATE drive_item SET content_id=? WHERE url_id=?", (cid, meta["id"]))
    f.x("UPDATE page_res SET res_id=?, need='must', why=NULL WHERE page_id=?", (cid, page["id"]))
    google_api.resync(f, page["id"])  # the page read again (a rejudge, the next run's GET)
    e = entries(f, page)[f"drive:{FILE_ID}"]
    assert (e["need"], e["why"], e["res_id"]) == ("no", "media:drive_installer", meta["id"])


def test_an_attachment_sent_to_a_drive_installer_by_a_redirect_comes_to_the_rule(f):
    """The views follow a redirect onto a Drive item's metadata row to what the item came to (_drive_settled): a
    program listed by its name only is the rule's (规则不抓), as a video is, never an attachment lost."""
    page = add_page(f)
    hop, meta_url = "https://c.nknu.edu.tw/aca/dl.aspx?id=3", google_api.item_url(FILE_ID)
    f.add(hop, host="c.nknu.edu.tw", site=page["owner_site"], unit=None, tier=0, kind="file", depth=1,
          found_via="link", found_from=page["id"], owner_site=page["owner_site"], kind0="file")
    f.finish(f.row(hop)["id"], state="done", status=302, final_url=meta_url)
    f.add(meta_url, host=google_api.API_HOST, site=google_api.API_HOST, unit=None, tier=0, kind="drive", depth=2,
          found_via="redirect", found_from=f.row(hop)["id"], owner_site=page["owner_site"], kind0="drive")
    meta = f.row(meta_url)["id"]
    f.finish(meta, state="done", status=200, sniff="json", kind="drive")
    f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size) VALUES (?,?,?,?,?)",
        (meta, FILE_ID, "setup_x64.msi", "application/x-msi", 74797056))
    f.add_page_res(page["id"], [{"res_key": hop, "how": "get", "role": "file", "need": "must",
                                 "res_id": f.row(hop)["id"]}])
    got = f.q("SELECT result FROM page_resource WHERE page_id=? AND res_key=?", (page["id"], hop))[0][0]
    assert got == "rule"


def test_installer_names_and_the_views_sql_agree():
    names = ["a.exe", "B.MSI", " c.iso ", "d.dmg", "e.pkg", "f.apk", "a.exe.pdf", "exe", ".exe", "setup", "x.pdf",
             None, "x.ps1"]
    want = {n for n in names if outcome.installer_name(n)}
    assert want == {"a.exe", "B.MSI", " c.iso ", "d.dmg", "e.pkg", "f.apk"}
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE d(name TEXT, mime TEXT)")
    db.executemany("INSERT INTO d VALUES (?, ?)", [(n, "application/octet-stream") for n in names]
                   + [("folder.iso", google_api.FOLDER)])
    got = {r[0] for r in db.execute(f"SELECT name FROM d WHERE {outcome._installer_sql('name', 'mime')}")}
    assert got == want  # a Google type (a folder named so) never


def test_the_export_lists_a_drive_installer_as_a_rule_with_its_name(tmp_path):
    msi_meta = "https://www.googleapis.com/drive/v3/files/1MsIiNsTaLlEr000?fields=id"
    old_meta = "https://www.googleapis.com/drive/v3/files/1OlDmSiInStAlLeR?fields=id"
    old_content = "https://www.googleapis.com/drive/v3/files/1OlDmSiInStAlLeR?alt=media"

    def build(arch):
        meta = arch.file("msi", msi_meta, b"{}", "application/json", kind="drive", sniff="json",
                         host="www.googleapis.com", site="www.googleapis.com")
        arch.f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size) VALUES (?,?,?,?,?)",
                 (meta, "1MsIiNsTaLlEr000", "setup_x64.msi", "application/x-msi", 74797056))
        _res(arch.f, arch.ids["about"], "drive:1MsIiNsTaLlEr000", "drive", need="no", how="drive_api", res_id=meta,
             why="media:drive_installer", text="安裝程式")
        old = arch.file("old", old_meta, b"{}", "application/json", kind="drive", sniff="json",
                        host="www.googleapis.com", site="www.googleapis.com")
        content = arch.file("old_content", old_content, b"\xd0\xcf\x11\xe0 made up", "application/x-msi",
                            sniff="ole", host="www.googleapis.com", site="www.googleapis.com", found_via="drive")
        arch.f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size, content_id) VALUES (?,?,?,?,?,?)",
                 (old, "1OlDmSiInStAlLeR", "old.msi", "application/x-msi", 12, content))
        _res(arch.f, arch.ids["about"], "drive:1OlDmSiInStAlLeR", "drive", need="no", how="drive_api", res_id=old,
             why="media:drive_installer", text="舊安裝程式")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    words = export.RULE_WHY[google_api.INSTALLER_WHY]
    for url in (msi_meta, old_content):
        assert (rows[url]["原因"], rows[url]["本地档案"]) == (words, ""), url


# ---------------------------------------------------------------- what the crawler writes of the folders


def test_the_crawler_writes_the_other_folders_as_needing_nothing(tmp_path):
    w = World(tmp_path)
    w.stored(PAGE7, SITE, PAGE_HTML)
    page = links.extract(PAGE_HTML, PAGE7, profiles.content_css(PAGE7))
    keys = {postback.button_key(b.target, b.argument): b.text for b in page.buttons}
    for key, text in keys.items():
        want = "press" if text == "安裝說明" else ("never" if "Lang" in key else "ignore")
        assert profiles.decision(scope.site_key(PAGE7), key, text) == want, key
    w.sink.close()
    w.f.close()


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    yield fr
    fr.close()
