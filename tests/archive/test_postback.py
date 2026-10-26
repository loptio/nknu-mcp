"""Platform profiles and ASP.NET postbacks (round-1 design §7.4, §7.5, §10.1, §16 test_postback). Offline: pages are
the minimal fixtures in tests/fixtures/archive_postback (shapes of real school pages, made-up titles), archived with
the crawler's own WarcSink into a temporary directory; no request is ever sent (the crawler sends, these tests only
hand answers back)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nknu.archive import links, postback, profiles, scope
from nknu.archive.frontier import Frontier
from nknu.archive.postback import Chains, list_end, read_page, title_ok
from nknu.crawl import webforms
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.warc import WarcSink

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "archive_postback"
SSO_LIST = "https://sso.nknu.edu.tw/news/subjectlist.aspx?cat=SCHOOLADMIN"
CNKNU_LIST = "https://c.nknu.edu.tw/aca/newslist.aspx?gi=1"
SPE_LIST = "https://spe.nknu.edu.tw/newslist.aspx?nc=1"
CLV2 = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=105000"
MORE = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=3&selpage=MoreNews&sort=date&pagenum=1"
EE_COURSE = "https://ee.nknu.edu.tw/course.html"
OIA = "https://oia.nknu.edu.tw/sister-schools.php"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def snapshot(url: str, body: bytes | str, status: int = 200, headers: dict | None = None) -> Snapshot:
    if isinstance(body, str):
        body = body.encode("utf-8")
    return Snapshot(target=Target(url), url=url, status=status,
                    headers=headers or {"content-type": "text/html; charset=utf-8"}, body=body,
                    text=body.decode("utf-8", "replace"), fetched_at=datetime.now(UTC),
                    sha256=hashlib.sha256(body).hexdigest())


class World:
    """A frontier, a WARC file and the chains, all in tmp_path."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.f = Frontier(tmp_path / "frontier.db")
        self.sink = WarcSink(tmp_path / "warc", "ip_test", 1, datetime(2026, 10, 26, tzinfo=UTC))
        self.warc = str(self.sink.path.relative_to(tmp_path))
        self.now = 1000.0
        self.chains = Chains(self.f, tmp_path, clock=lambda: self.now)

    def archive(self, url: str, body, status: int = 200, headers: dict | None = None) -> Snapshot:
        snap = snapshot(url, body, status, headers)
        offset, length = self.sink.write(snap, {})
        return replace(snap, warc_offset=offset, warc_len=length)

    def page(self, url: str, site: str, html: str, queue: str = "ip:140.127.40.173"):
        """A round-1 page fetched by GET and archived; its row as the crawler leaves it."""
        url = scope.canonical(url)
        host = url.split("/")[2]
        self.f.set_host(host, queue=queue)
        self.f.add(url, host=host, site=site, unit=None, tier=0, kind="page", depth=0, found_via="seed",
                   found_from=None, owner_site=site, prio=0, kind0="page")
        snap = self.archive(url, html)
        row = self.f.row(url)
        self.f.set_page_res(row["id"], [])  # the crawler writes the page's own page_res anew on each reading
        self.f.finish(row["id"], state="done", status=200, kind="page", sniff="html", warc=self.warc,
                      warc_offset=snap.warc_offset, warc_len=snap.warc_len, final_url=url)
        return self.f.row(url), snap

    def stored(self, url: str, site: str, html: str):
        row, snap = self.page(url, site, html)
        got = self.chains.page_stored(row, html, self.warc, snap.warc_offset)
        return self.f.row_by_id(row["id"]), got

    def actions(self, list_id: int | None = None, **where):
        sql, args = "SELECT * FROM action WHERE 1=1", []
        if list_id is not None:
            sql += " AND list_id=?"
            args.append(list_id)
        for k, v in where.items():
            sql += f" AND {k}=?"
            args.append(v)
        return self.f.q(sql + " ORDER BY id", args)

    def walk(self, list_id: int):
        got = self.f.q("SELECT * FROM listwalk WHERE list_id=?", (list_id,))
        return got[0] if got else None

    def press(self, queue: str = "ip:140.127.40.173"):
        a = self.chains.next_action(queue)
        assert a is not None
        return a, self.chains.target_for(a)

    def answer(self, action, url: str, body, status: int = 200, headers: dict | None = None):
        snap = self.archive(url, body, status, headers)
        return self.chains.answered(action, snap, self.warc)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.sink.close()
    w.f.close()


# ---------------------------------------------------------------- webforms (review 10-26 C, design §7.4)

def _fields(html: str) -> list[tuple[str, str]]:
    return webforms.form_state(f"<form action='/x'>{html}</form>", "https://h.test/")[1]


def test_multiple_select_with_nothing_selected_sends_nothing():
    """HTML Standard, constructing the entry list: a select multiple with no option selected submits nothing (the
    old code sent its first option; scrapy's form2request and lxml's MultipleSelectOptions read it the same way)."""
    assert _fields("<select name='m' multiple><option value='a'>A</option><option value='b'>B</option></select>") == []


def test_multiple_select_sends_every_selected_enabled_option():
    html = ("<select name='m' multiple><option value='a' selected>A</option><option value='b'>B</option>"
            "<option value='c' selected disabled>C</option><option selected>  D  E </option></select>")
    assert _fields(html) == [("m", "a"), ("m", "D E")]


def test_single_select_with_nothing_selected_sends_its_first_enabled_option():
    html = "<select name='s'><option value='x' disabled>X</option><option value='y'>Y</option></select>"
    assert _fields(html) == [("s", "y")]


def test_single_select_last_selected_wins_and_a_list_box_sends_nothing():
    assert _fields("<select name='s'><option value='a' selected>A</option><option value='b' selected>B</option>"
                   "</select>") == [("s", "b")]
    assert _fields("<select name='s' size='3'><option value='a'>A</option></select>") == []
    assert _fields("<select name='s'><optgroup disabled><option value='a' selected>A</option></optgroup></select>"
                   ) == []
    assert _fields("<select name='s'><option value>empty</option></select>") == [("s", "")]


def test_checkbox_value_and_v1_form_state_unchanged():
    assert _fields("<input type='checkbox' name='c' checked><input type='checkbox' name='d' value='' checked>"
                   "<input type='checkbox' name='e'>") == [("c", "on"), ("d", "")]
    _action, fields = webforms.form_state(fx("sso_list_p1.html"), SSO_LIST)
    assert ("ddlCategory", "SCHOOLADMIN") in fields and ("__VIEWSTATE", "VS-SSO-P1") in fields


def test_form_for_control_picks_the_form_that_holds_the_button_not_spe_search():
    html = fx("spe_list_p1.html")
    css = webforms.form_for_control(html, "MasterGrid$ctl23$lbnNext")
    assert css == 'form[id="form1"]'
    action, fields = webforms.postback(html, SPE_LIST, "MasterGrid$ctl23$lbnNext", form_css=css)
    names = [k for k, _v in fields]
    assert action.endswith("/NewsList.aspx?NC=1") and "q" not in names and "cx" not in names
    assert ("__VIEWSTATE", "VS-SPE-P1") in fields and ("__EVENTTARGET", "MasterGrid$ctl23$lbnNext") in fields


def test_form_for_control_outside_any_form_is_the_server_form_the_page_names():
    html = ("<a href=\"javascript:__doPostBack('btnLangTC','')\">TC</a><form id='search'><input name='q'></form>"
            "<form id='form1' method='post'><input type='hidden' name='__VIEWSTATE' value='v'></form>"
            "<script>var theForm = document.forms['form1'];</script>")
    assert webforms.form_for_control(html, "btnLangTC") == 'form[id="form1"]'
    assert webforms.form_for_control(html, "nothing") is None


def test_find_postbacks_reads_onclick_and_onchange():
    got = {(t, a) for t, a, _x in webforms.find_postbacks(fx("affair_contact.html"))}
    assert {("Btn_Send", ""), ("Btn_Clear", ""), ("btnReGenCode", "")} <= got
    html = "<select name='d' onchange=\"javascript:setTimeout('__doPostBack(\\'ddl\\',\\'\\')', 0)\"></select>"
    assert ("ddl", "") in {(t, a) for t, a, _x in webforms.find_postbacks(html)}


# ---------------------------------------------------------------- profiles: the decisions (§7.4, §7.8)

@pytest.mark.parametrize(("site", "key", "want"), [
    # the 10 kinds the 10-25 click scan found uncovered (postback_uncovered), each with a decision
    ("c.nknu.edu.tw/affair", "postback:Btn_Send", "never"),
    ("c.nknu.edu.tw/affair", "postback:Btn_Clear", "never"),
    ("c.nknu.edu.tw/affair", "postback:btnReGenCode", "never"),
    ("c.nknu.edu.tw/reality", "postback:Btn_Send", "never"),
    ("c.nknu.edu.tw/reality", "postback:Btn_Clear", "never"),
    ("c.nknu.edu.tw/reality", "postback:btnReGenCode", "never"),
    ("c.nknu.edu.tw/reality", "postback:MasterGrid|Sort$FileClass", "ignore"),
    ("c.nknu.edu.tw/reality", "postback:MasterGrid|Sort$FileSize", "ignore"),
    ("c.nknu.edu.tw/reality", "postback:MasterGrid|Sort$FileName", "ignore"),
    # SubjectList.aspx's 「查詢」: its rule names the text, so the key alone decides nothing (review 10-26 phase 3;
    # with its text: test_review_phase3.test_a_rule_that_names_a_text_holds_only_for_a_button_with_that_text)
    ("sso.nknu.edu.tw/news", "postback:ctl*", "unknown"),
    # pressed
    ("c.nknu.edu.tw/aca", "postback:MasterGrid|Page$2", "press"),
    ("c.nknu.edu.tw/aca", "postback:MasterGrid", "press"),  # the crawler's key has no argument
    ("spe.nknu.edu.tw", "postback:MasterGrid$ctl*$lbnNext", "press"),
    ("sso.nknu.edu.tw/news", "postback:gvFull", "press"),
    ("sso.nknu.edu.tw/web/3", "postback:ucNews$rptNews$ctl*$lnkShowDetail", "press"),
    ("sso.nknu.edu.tw/web/3", "postback:ucAllTypeList$rptItems$ctl*$lnkDownloadFile", "press"),
    ("sso.nknu.edu.tw/site/105000", "postback:uSSONews$gvList0$ctl*$gv_lbSel", "press"),
    ("sso.nknu.edu.tw/site/105000", "postback:uDataContentPos2$downloadBtn12", "press"),
    # never pressed: last page, languages, cLv2's menu
    ("spe.nknu.edu.tw", "postback:MasterGrid$ctl*$lbnLast", "ignore"),
    ("spe.nknu.edu.tw", "postback:MasterGrid$ctl*$ctl*", "ignore"),
    ("sso.nknu.edu.tw/news", "postback:ctl*$btnLangE", "never"),
    ("sso.nknu.edu.tw/web/3", "postback:btnMobileLangTC", "never"),
    ("sso.nknu.edu.tw/site/105000", "postback:btnLangTC", "never"),
    ("sso.nknu.edu.tw/site/105000", "postback:uSSONews$Menu1", "never"),
    ("sso.nknu.edu.tw/web/3", "postback:ucNews$rptCategories$ctl*$btnCat", "ignore"),
    # nobody decided
    ("c.nknu.edu.tw/aca", "postback:WUC_News$lbtnMore", "unknown"),
    ("lis.nknu.edu.tw", "postback:anything", "unknown"),
])
def test_button_decisions(site, key, want):
    assert profiles.decision(site, key) == want


@pytest.mark.parametrize(("site", "key", "want"), [
    ("elec.nknu.edu.tw", "js_link:userLogInForm", "never"),
    ("english.nknu.edu.tw", "js_link:_checkLogin", "never"),
    ("english.nknu.edu.tw", "js_link:_getRandom", "never"),
    ("ee.nknu.edu.tw", "js_link:checkForm", "never"),
    ("lis.nknu.edu.tw", "js_link:Joomla.tableOrdering", "ignore"),
    ("ee.nknu.edu.tw", "select:.option", "round2"),  # the year menu
    ("sso.nknu.edu.tw/news", "select:ddlCategory", "never"),
    ("c.nknu.edu.tw/affair", "select:ddl_Sort", "ignore"),
    ("ksped.nknu.edu.tw", "select:ddl_NewsClass", "never"),
    ("anything.nknu.edu.tw", "js_link:window.print", "ignore"),
    ("anything.nknu.edu.tw", "js_link:history.back", "ignore"),
    ("spe.nknu.edu.tw", "get_form:search.aspx cx,cof,ie,q", "never"),
    # the 11 R7 reads first, English's tabs, js_link:void: unknown (要人看) until R7 or a person decides
    ("sso.nknu.edu.tw/web/3", "js_link:toggleMobileSub", "unknown"),
    ("lis.nknu.edu.tw", "js_link:initSort", "unknown"),
    ("clcts.nknu.edu.tw", "js_link:papago", "unknown"),
    ("english.nknu.edu.tw", "js_link:", "unknown"),
    ("oia.nknu.edu.tw", "js_link:void", "unknown"),
    # the clicks rules
    ("oia.nknu.edu.tw", "click:loadSubcategoryContent(16)", "handle"),
    ("ee.nknu.edu.tw", "click:.down[3]", "handle"),
    ("ee.nknu.edu.tw", "click:fileAjax.html(k=2,id=61)", "handle"),
    ("ee.nknu.edu.tw", "click:somethingElse(1)", "unknown"),
])
def test_writing_and_click_decisions(site, key, want):
    assert profiles.decision(site, key) == want


def test_every_decision_says_why_and_no_profile_ignores_a_click():
    assert profiles.check_profiles() == []
    for p in profiles.PROFILES:
        for b in p.buttons:
            assert b.why.strip()
        for w in p.writings:
            assert w.why.strip()
    with pytest.raises(ValueError):
        profiles.Writing("click:x", "ignore", "no")
    with pytest.raises(ValueError):
        profiles.Button("x", "never", "")


def test_crawler_keys_and_unhandled_need_fit_together():
    """The crawler keys a button postback:<target, ctlNN as ctl*> and hands decision() to links.unhandled_need."""
    assert profiles.norm_key("ucNews$rptNews$ctl07$lnkShowDetail") == "postback:ucNews$rptNews$ctl*$lnkShowDetail"
    assert links.unhandled_need(profiles.decision("c.nknu.edu.tw/affair", "postback:Btn_Send")) == \
        ("no", "unhandled:never")
    assert links.unhandled_need(profiles.decision("c.nknu.edu.tw/aca", "postback:WUC_News$lbtnMore")) == \
        ("must", "unhandled:unknown")


def test_profile_paths_and_probe():
    assert profiles.page_kind("https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=663&gi=4") == "detail"
    assert profiles.page_kind(SSO_LIST) == "list"
    assert profiles.page_kind("https://sso.nknu.edu.tw/news/attachedfile.aspx?cencrptedfilepath=x") == "download"
    assert profiles.page_kind(MORE + "&newsidx=1326") == "detail"
    assert profiles.id_probe_url("https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=663&gi=4") == \
        "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=999999999&gi=4"
    probe = profiles.id_probe_url("https://sso.nknu.edu.tw/news/lastnews.aspx?unid=3E3F")
    assert probe is not None and probe.endswith("=" + "0" * 40)
    assert profiles.content_css(CNKNU_LIST) is not None and profiles.content_css("https://x.example/") is None


def test_empty_detail_and_gone_id_need_all_three():
    """§10.1: an item with only an attachment or only one picture is no empty detail, so never gone_id."""
    view = "https://c.nknu.edu.tw/affair/newsview.aspx?nid=3033"
    assert profiles.empty_detail(view, fx("cnknu_newsview_files_only.html")) is False
    assert profiles.empty_detail(view, fx("cnknu_newsview_one_image.html")) is False
    assert profiles.empty_detail(view, fx("cnknu_newsview_empty.html")) is True
    assert profiles.empty_detail(CNKNU_LIST, fx("cnknu_list_p1.html")) is None  # no detail page
    assert profiles.gone_id(200, True, True, True)
    assert not profiles.gone_id(404, True, True, True)
    assert not profiles.gone_id(200, False, True, True)
    assert not profiles.gone_id(200, True, True, False)


def test_sso_attachment_says_gone_twice_and_bad_link_at_once():
    att = "https://sso.nknu.edu.tw/news/attachedfile.aspx?cencrptedfilepath=abc&inline=1"
    gone = profiles.school_says(att, fx("sso_attachment_gone.html"))
    bad = profiles.school_says(att, fx("sso_attachment_badchar.html"))
    assert gone.flag == "gone_page" and bad.flag == "bad_link"
    assert profiles.school_says("https://c.nknu.edu.tw/aca/x.aspx", fx("sso_attachment_gone.html")) is None
    first = profiles.says_step(gone, None, 0, 100.0)
    assert first == {"state": "pending", "reason": profiles.GONE_RECHECK, "attempts": 1,
                     "next_at": 100.0 + profiles.GONE_RECHECK_S}
    assert profiles.says_step(gone, profiles.GONE_RECHECK, 1, 5000.0)["flags"] == "gone_page"
    assert profiles.says_step(bad, None, 0, 100.0) == {"state": "done", "reason": None, "flags": "bad_link"}


def test_gone_page_and_bad_link_reach_the_result_codes(world):
    """What says_step writes is what url_result reads (§10.2): gone (学校那边已经不存在), bad_link (要人看)."""
    att = scope.canonical("https://sso.nknu.edu.tw/news/attachedfile.aspx?cencrptedfilepath=abc")
    world.f.add(att, host="sso.nknu.edu.tw", site="sso.nknu.edu.tw/news", unit=None, tier=0, kind="file", depth=1,
                found_via="link", found_from=None, owner_site="sso.nknu.edu.tw/news", kind0="file", prio=2, leaf=1)
    uid = world.f.row(att)["id"]
    gone = profiles.school_says(att, fx("sso_attachment_gone.html"))
    world.f.finish(uid, status=200, sniff="html", kind="file", **profiles.says_step(gone, None, 0, 10.0))
    assert world.f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0] == "retry"
    row = world.f.row(att)
    world.f.finish(uid, status=200, sniff="html", kind="file",
                   **profiles.says_step(gone, row["reason"], row["attempts"], 20.0))
    assert world.f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0] == "gone"
    bad = profiles.school_says(att, fx("sso_attachment_badchar.html"))
    world.f.finish(uid, status=200, sniff="html", kind="file", **profiles.says_step(bad, None, 0, 30.0))
    assert world.f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0] == "bad_link"


# ---------------------------------------------------------------- clicks rules (§7.4)

def test_oia_tabs_one_get_per_tab_shown_one_too():
    html = fx("oia_tabs.html")
    page = links.extract(html, OIA)
    plans = [profiles.click_plan("oia.nknu.edu.tw", c, html, OIA) for c in page.clicks]
    assert [p.url for p in plans] == [f"https://oia.nknu.edu.tw/load_content.php?sub_id={n}" for n in (20, 21, 46)]
    assert all(p.method == "GET" and p.res_key == p.click_key for p in plans)
    assert [profiles.click_decision("oia.nknu.edu.tw", c, html, OIA) for c in page.clicks] == ["handle"] * 3
    shown = next(c for c in page.clicks if c.shown_on_load)
    assert links.click_need(shown, profiles.click_decision("oia.nknu.edu.tw", shown, html, OIA)) == ("must", "")


def test_oia_tab_rule_that_no_longer_fits_is_round2():
    html = fx("oia_tabs_changed.html")
    page = links.extract(html, OIA)
    assert page.clicks
    for c in page.clicks:
        assert profiles.click_plan("oia.nknu.edu.tw", c, html, OIA) is None
        assert profiles.click_decision("oia.nknu.edu.tw", c, html, OIA) == "round2"


def test_tab_content():
    assert profiles.tab_content('{"success": true, "content": "<p>亞洲</p>", "message": ""}') == "<p>亞洲</p>"
    assert profiles.tab_content('{"success": false, "content": "", "message": "x"}') is None
    assert profiles.tab_content('{"success": true, "content": "  "}') is None
    assert profiles.tab_content("<html>") is None


def test_ee_file_buttons_post_id_and_k():
    html = fx("ee_course.html")
    page = links.extract(html, EE_COURSE)
    plans = [profiles.click_plan("ee.nknu.edu.tw", c, html, EE_COURSE) for c in page.clicks]
    assert [p.data for p in plans] == [(("id", "61"), ("k", "2")), (("id", "62"), ("k", "2"))]
    assert [p.res_key for p in plans] == ["click:fileAjax.html(k=2,id=61)", "click:fileAjax.html(k=2,id=62)"]
    assert [p.row_key for p in plans] == ["click:k=2,id=61", "click:k=2,id=62"]
    assert all(p.url == "https://ee.nknu.edu.tw/fileAjax.html" and p.method == "POST" for p in plans)


def test_click_file_answer():
    assert profiles.click_file(" upload/files/a.pdf \n", EE_COURSE) == "https://ee.nknu.edu.tw/upload/files/a.pdf"
    assert profiles.click_file("<html>error</html>", EE_COURSE) is None
    assert profiles.click_file("", EE_COURSE) is None


def test_ee_same_pair_on_two_pages_is_one_action(world):
    rows = []
    for url, name in ((EE_COURSE, "ee_course.html"), (EE_COURSE + "?getId=2", "ee_course_p2.html")):
        html = fx(name)
        row, snap = world.page(url, "ee.nknu.edu.tw", html, queue="ip:61.218.135.171")
        page = links.extract(html, row["url"])
        entries = []
        for c in page.clicks:
            plan = profiles.click_plan("ee.nknu.edu.tw", c, html, row["url"])
            aid = world.chains.click_action(row, plan, world.warc, snap.warc_offset)
            entries.append(postback.click_entry(plan, action_id=aid))
        world.f.set_page_res(row["id"], entries)
        rows.append(row)
    acts = world.actions(kind="click")
    assert len(acts) == 2  # (2, 61) once, (2, 62) once
    shared = next(a for a in acts if a["row_key"] == "click:k=2,id=61")
    assert shared["list_id"] == rows[0]["id"]
    pointing = world.f.q("SELECT page_id FROM page_res WHERE action_id=?", (shared["id"],))
    assert sorted(r[0] for r in pointing) == sorted(r["id"] for r in rows)
    # the POST: only id and k, never resent; its answer is a file address
    a = world.chains.next_action("ip:61.218.135.171")
    target = world.chains.target_for(a)
    assert target.method == "POST" and dict(target.data) == {"id": "61", "k": "2"}
    out = world.answer(a, target.url, "upload/files/course.pdf", headers={"content-type": "text/html"})
    assert out.files == ("https://ee.nknu.edu.tw/upload/files/course.pdf",)
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"
    b = world.chains.next_action("ip:61.218.135.171")
    out = world.answer(b, world.chains.target_for(b).url, "<html>伺服器錯誤</html>")
    assert out.files == () and world.f.q("SELECT state, reason FROM action WHERE id=?", (b["id"],))[0][:] == \
        ("mismatch", "not_an_address")


def test_ee_click_post_failure_is_never_resent(world):
    html = fx("ee_course.html")
    row, snap = world.page(EE_COURSE, "ee.nknu.edu.tw", html, queue="ip:61.218.135.171")
    for c in links.extract(html, row["url"]).clicks:
        world.chains.click_action(row, profiles.click_plan("ee.nknu.edu.tw", c, html, row["url"]), world.warc,
                                  snap.warc_offset)
    a = world.chains.next_action("ip:61.218.135.171")
    out = world.chains.failed(a, "timeout")
    assert not out.restart and world.f.row_by_id(row["id"])["state"] == "done"
    assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "failed"
    nxt = world.chains.next_action("ip:61.218.135.171")
    assert nxt["id"] != a["id"]  # the failed one is not handed out again
    assert world.chains.retry_clicks() == 1
    assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "pending"


# ---------------------------------------------------------------- reading list pages

def test_read_sso_list_page():
    pr = read_page(profiles.SSO_NEWS, SSO_LIST, fx("sso_list_p1.html"), site="sso.nknu.edu.tw/news",
                   list_url=SSO_LIST)
    assert (pr.rowset, pr.walk, pr.current, pr.max_seen, pr.claimed) == ("gvFull", "postback", 1, 2, 4)
    assert len(pr.rows) == 3 and pr.rows[1].title == "停修申請時間公告" and pr.rows[1].date == "2026-10-21"
    assert pr.next.target == "gvFull" and pr.next.argument == "Page$2" and pr.next.step == 2
    assert pr.presses == () and pr.unknown == ()  # the 「查詢」 button is never, not unknown


def test_read_spe_list_next_button_and_last_page():
    pr = read_page(profiles.CNKNU, SPE_LIST, fx("spe_list_p1.html"), site="spe.nknu.edu.tw", list_url=SPE_LIST)
    assert pr.pager_how == "next" and pr.current == 1 and pr.next.target == "MasterGrid$ctl23$lbnNext"
    assert pr.next.form_id == "form1" and len(pr.rows) == 2
    last = read_page(profiles.CNKNU, SPE_LIST, fx("spe_list_last.html"), site="spe.nknu.edu.tw", list_url=SPE_LIST)
    assert last.current == 2 and last.next is None and list_end(last) == "last_page"


def test_row_keys_follow_the_row_not_its_number():
    a = read_page(profiles.SSO_CLV2, CLV2, fx("clv2_index.html"), site="sso.nknu.edu.tw/site/105000", list_url=CLV2)
    moved = fx("clv2_index.html").replace("ctl02$gv_lbSel", "ctl09$gv_lbSel").replace("ctl02_gv_lbSel",
                                                                                      "ctl09_gv_lbSel")
    b = read_page(profiles.SSO_CLV2, CLV2, moved, site="sso.nknu.edu.tw/site/105000", list_url=CLV2)
    assert [p.row_key for p in a.presses] == [p.row_key for p in b.presses]
    assert a.presses[0].target != b.presses[0].target


def test_list_ends():
    def end(name, url, prof, site):
        return list_end(read_page(prof, url, fx(name), site=site, list_url=url))
    assert end("cnknu_empty.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca") == "no_rows_ok"
    no_words = fx("cnknu_empty.html").replace("查無資料", "")
    assert list_end(read_page(profiles.CNKNU, CNKNU_LIST, no_words, site="c.nknu.edu.tw/aca")) == "mismatch:empty"
    assert end("cnknu_unknown_button.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca") == \
        "mismatch:unknown_button"
    assert end("cnknu_no_next.html", CNKNU_LIST, profiles.CNKNU, "c.nknu.edu.tw/aca") == "mismatch:no_next"
    assert end("clv2_ten.html", CLV2, profiles.SSO_CLV2, "sso.nknu.edu.tw/site/205011") == "mismatch:clv2"
    assert end("clv2_index.html", CLV2, profiles.SSO_CLV2, "sso.nknu.edu.tw/site/105000") == "last_page"
    assert end("reality_download.html", "https://c.nknu.edu.tw/reality/download.aspx?pn=7", profiles.CNKNU,
               "c.nknu.edu.tw/reality") == "last_page"  # Sort$ buttons are known (ignore)
    p2 = read_page(profiles.SSO_NEWS, SSO_LIST, fx("sso_list_p2.html"), site="sso.nknu.edu.tw/news",
                   list_url=SSO_LIST)
    assert list_end(p2, rows_unique=4) == "last_page" and list_end(p2, rows_unique=3) == "mismatch:total"
    lost = replace(p2, has_pager=True, current=None)
    assert list_end(lost) == "mismatch:page"


def test_title_check():
    assert title_ok("模範人員獲獎名單", fx("webidx_detail.html"), ".news-detail-view .detail-header h3")
    assert not title_ok("模範人員獲獎名單", fx("webidx_detail_wrong.html"), ".news-detail-view .detail-header h3")
    assert title_ok("模範人員獲獎…", fx("webidx_detail.html"), ".news-detail-view .detail-header h3")
    assert title_ok("出差綜合保險共同供應契約，請查照。", fx("clv2_detail_ok.html"), None)
    assert not title_ok("出差綜合保險共同供應契約，請查照。", fx("clv2_detail_wrong.html"), None)
    # an answer that shows the list again names every title there: only the rest of the page counts
    with_list = fx("clv2_detail_wrong.html").replace("</form>", fx("clv2_index.html").split("<div id=\"uSSONews_PanelList\">")[1])
    assert title_ok("出差綜合保險共同供應契約，請查照。", with_list, None)
    assert not title_ok("出差綜合保險共同供應契約，請查照。", with_list, None, exclude="#uSSONews_gvList0")


# ---------------------------------------------------------------- the chain (§7.5)

def test_sso_walk_to_the_last_page_with_the_total(world):
    row, got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    assert got.page_role == "list" and got.presses == 1
    assert world.walk(row["id"])["ended_by"] == "walking"
    a, target = world.press()
    assert (a["kind"], a["step"], a["row_key"]) == ("pager", 2, "p2")
    data = dict(target.data)
    assert target.method == "POST" and target.url == "https://sso.nknu.edu.tw/news/subjectlist.aspx?cat=SCHOOLADMIN"
    assert data["__EVENTTARGET"] == "gvFull" and data["__EVENTARGUMENT"] == "Page$2"
    assert data["__VIEWSTATE"] == "VS-SSO-P1" and data["__EVENTVALIDATION"] == "EV-SSO-P1"
    assert data["ddlCategory"] == "SCHOOLADMIN" and "ctl09" not in data
    world.answer(a, target.url, fx("sso_list_p2.html"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"], lw["pages"], lw["rows"], lw["rows_unique"], lw["claimed"]) == \
        ("last_page", 1, 2, 5, 4, 4)
    assert world.chains.next_action("ip:140.127.40.173") is None
    assert world.f.q("SELECT result FROM page_resource WHERE page_id=? AND res_key='pb:pager:p2'",
                     (row["id"],))[0][0] == "ok"


def test_sso_walk_total_that_does_not_add_up(world):
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html").replace("共4筆", "共5筆"))
    a, target = world.press()
    world.answer(a, target.url, fx("sso_list_p2.html").replace("共4筆", "共5筆"))
    assert (world.walk(row["id"])["ended_by"], world.walk(row["id"])["ok"]) == ("mismatch:total", 0)


def test_one_page_list_with_an_unknown_button_is_not_last_page(world):
    row, _got = world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_unknown_button.html"))
    assert world.walk(row["id"])["ended_by"] == "mismatch:unknown_button"


TEST_PROFILE = profiles.Profile(
    name="test", platform="一个列表行要按、翻页也要按的平台（测试用）", sites=r"test\.nknu\.edu\.tw",
    rows=(profiles.Rows("g", grid="#g", row="tr", walk="postback", title="a.t",
                        pagers=(profiles.Pager("page_n", r"g"),)),),
    buttons=(profiles.Button(r"g", "press", "翻页", role="pager", argument=r"Page\$\d+"),
             profiles.Button(r"g\$ctl\d+\$lnk", "press", "单则内文", role="detail")))
TEST_LIST = ("<form id='form1' method='post' action='./list.aspx'><input type='hidden' name='__VIEWSTATE' value='v'>"
             "<table id='g'><tr><td><a class='t' href=\"javascript:__doPostBack('g$ctl02$lnk','')\">甲</a></td></tr>"
             "<tr><td><a class='t' href=\"javascript:__doPostBack('g$ctl03$lnk','')\">乙</a></td></tr>"
             "<tr><td><table><tr><td><span>1</span></td>"
             "<td><a href=\"javascript:__doPostBack('g','Page$2')\">2</a></td></tr></table></td></tr></table></form>")


def test_row_presses_go_before_the_next_page(world, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", (*profiles.PROFILES, TEST_PROFILE))
    _row, got = world.stored("https://test.nknu.edu.tw/list.aspx", "test.nknu.edu.tw", TEST_LIST)
    assert got.presses == 3
    order = []
    for _ in range(3):
        a, target = world.press()
        order.append((a["kind"], dict(target.data)["__EVENTTARGET"]))
        if a["kind"] == "detail":
            world.chains.answered(a, world.archive(target.url, "<html><body>甲乙</body></html>"), world.warc)
    assert order == [("detail", "g$ctl02$lnk"), ("detail", "g$ctl03$lnk"), ("pager", "g")]


def test_detail_title_mismatch_restarts_once_from_get(world):
    row, _got = world.stored(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    a, target = world.press()
    assert dict(target.data)["__EVENTTARGET"] == "ucNews$rptNews$ctl01$lnkShowDetail"
    out = world.answer(a, target.url, fx("webidx_detail_wrong.html"))
    assert out.restart and world.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0][:] == \
        ("mismatch", "title")
    lst = world.f.row_by_id(row["id"])
    assert (lst["state"], lst["reason"], lst["prio"]) == ("pending", "chain_restart", 0)
    assert {r["state"] for r in world.actions(row["id"], epoch=1)} == {"mismatch", "superseded"}
    # the GET again: epoch 2, both rows pressed again
    row2, snap = world.page(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    world.chains.page_stored(row2, fx("webidx_morenews.html"), world.warc, snap.warc_offset)
    assert len(world.actions(row["id"], epoch=2)) == 2
    b, target = world.press()
    assert b["epoch"] == 2 and b["row_key"] == a["row_key"]
    out = world.answer(b, target.url, fx("webidx_detail_wrong.html"))
    assert not out.restart  # still wrong: left for a person
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (b["id"],))[0][0] == "mismatch"


def test_detail_ok_plans_its_files_and_is_not_pressed_again_after_a_restart(world):
    row, _got = world.stored(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("webidx_detail.html"))
    assert world.f.q("SELECT state FROM action WHERE id=?", (a["id"],))[0][0] == "done"
    files = world.actions(row["id"], kind="download")
    assert len(files) == 1 and files[0]["target"] == "ucNews$rptFiles$ctl00$lnkDownloadFile"
    assert files[0]["form_src_offset"] != a["form_src_offset"]  # its form is the detail's answer
    b, target = world.press()
    assert b["kind"] == "detail"
    world.answer(b, target.url, fx("webidx_detail_wrong.html"))  # the second row's own title: right for it
    assert world.f.q("SELECT state FROM action WHERE id=?", (b["id"],))[0][0] == "done"
    c, target = world.press()
    assert c["kind"] == "download" and dict(target.data)["__VIEWSTATE"] == "VS-WEBIDX-DETAIL"
    assert world.chains.failed(c, "timeout").restart
    row2, snap = world.page(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    world.chains.page_stored(row2, fx("webidx_morenews.html"), world.warc, snap.warc_offset)
    epoch2 = world.actions(row["id"], epoch=2)
    assert [x["kind"] for x in epoch2] == ["download"]  # the done details are not pressed again, the file is
    keys = {r[0]: r[1] for r in world.f.q("SELECT res_key, action_id FROM page_res WHERE page_id=?", (row["id"],))}
    assert keys[f"pb:detail:{a['row_key']}"] == a["id"] and keys[f"pb:detail:{b['row_key']}"] == b["id"]
    assert keys[f"pb:download:{c['row_key']}"] == epoch2[0]["id"]
    # rejudge: the page's own page_res written anew, the presses' entries back from the action table, nothing sent
    world.f.set_page_res(row["id"], [])
    assert world.chains.rebuild(row["id"]) == 3
    keys = {r[0]: r[1] for r in world.f.q("SELECT res_key, action_id FROM page_res WHERE page_id=?", (row["id"],))}
    assert keys[f"pb:download:{c['row_key']}"] == epoch2[0]["id"] and keys[f"pb:detail:{a['row_key']}"] == a["id"]


def test_post_failure_restarts_from_get_then_post_failed(world):
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    a, _target = world.press()
    out = world.chains.failed(a, "timeout")
    assert out.restart and world.f.row_by_id(row["id"])["reason"] == "chain_restart"
    assert world.chains.next_action("ip:140.127.40.173") is None  # nothing is resent before the GET
    row2, snap = world.page(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    world.chains.page_stored(row2, fx("sso_list_p1.html"), world.warc, snap.warc_offset)
    b, target = world.press()
    assert b["epoch"] == 2 and b["id"] != a["id"]
    out = world.answer(b, target.url, "busy", status=503)
    assert not out.restart
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"]) == ("post_failed", 0)
    assert world.chains.retry_post_failed() == 1
    assert world.f.row_by_id(row["id"])["state"] == "pending"
    assert {r["state"] for r in world.actions(row["id"])} == {"superseded"}


def test_stop_and_resume_supersede_and_start_from_get(world):
    row, _got = world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a, _t = world.press()  # active when the run stops
    assert world.chains.resume() == 1
    assert {r["state"] for r in world.actions(row["id"])} == {"superseded"}
    lst = world.f.row_by_id(row["id"])
    assert (lst["state"], lst["reason"]) == ("pending", "chain_restart")
    assert world.walk(row["id"])["ended_by"] == "walking"
    assert world.f.q("SELECT count(*) FROM event WHERE code='chain_restart'")[0][0] == 1
    assert a["state"] == "pending"  # what next_action handed out, as it was read


def test_form_state_and_row_number_come_from_one_page(world):
    """The list changes between two GETs (a new post moves every row down): a press planned on the first page is
    built from the first page's state, never the newer one's."""
    world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    world.archive(CLV2, fx("clv2_index.html").replace("VS-CLV2-A", "VS-CLV2-B"))  # a newer copy in the WARC
    a, target = world.press()
    data = dict(target.data)
    assert data["__VIEWSTATE"] == "VS-CLV2-A" and data["__EVENTTARGET"] == "uSSONews$gvList0$ctl02$gv_lbSel"
    assert a["row_text"] == "出差綜合保險共同供應契約，請查照。"  # the school's characters kept (keys use NFKC)
    world.answer(a, target.url, fx("clv2_detail_ok.html"))
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "ok"


def test_never_buttons_and_language_are_not_planned(world):
    row, got = world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    assert {r["target"] for r in world.actions(row["id"])} == {
        f"uSSONews$gvList0$ctl0{n}$gv_lbSel" for n in (2, 3, 4)}
    assert got.presses == 3
    row, got = world.stored("https://c.nknu.edu.tw/affair/contact.aspx?pn=22&kind=affair", "c.nknu.edu.tw/affair",
                            fx("affair_contact.html"))
    assert got.presses == 0 and world.actions(row["id"]) == []


def test_target_for_refuses_a_press_whose_button_is_gone(world):
    world.stored(CLV2, "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a = world.chains.next_action("ip:140.127.40.173")
    world.f.x("UPDATE action SET target='uSSONews$gvList0$ctl99$gv_lbSel' WHERE id=?", (a["id"],))
    a = world.f.q("SELECT * FROM action WHERE id=?", (a["id"],))[0]
    assert world.chains.target_for(a) is None
    assert world.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0][:] == ("mismatch",
                                                                                          "control_missing")


def test_spe_walk_next_button_and_stays(world):
    row, _got = world.stored(SPE_LIST, "spe.nknu.edu.tw", fx("spe_list_p1.html"))
    a, target = world.press(queue="ip:140.127.40.173")
    assert dict(target.data)["__EVENTTARGET"] == "MasterGrid$ctl23$lbnNext" and "q" not in dict(target.data)
    world.answer(a, target.url, fx("spe_list_last.html"))
    assert world.walk(row["id"])["ended_by"] == "last_page"
    # a 「下一頁」 still written on the last page answers with the page it was on: the end, by the server's word
    row2, _ = world.stored("https://spe.nknu.edu.tw/newslist.aspx?nc=2", "spe.nknu.edu.tw",
                           fx("spe_list_p1.html").replace("NC=1", "NC=2"))
    b, target = world.press()
    world.answer(b, target.url, fx("spe_list_p1.html").replace("NC=1", "NC=2"))
    lw = world.walk(row2["id"])
    assert lw["ended_by"] == "mismatch:no_next" and "stayed" in lw["note"]


def test_page_mismatch_when_the_answer_shows_another_page(world):
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("sso_list_p1.html"))  # page 1 again, asked for 2
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["ok"]) == ("mismatch:page", 0)


def test_max_pages(world, monkeypatch):
    monkeypatch.setattr(postback, "MAX_PAGES", 1)
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    assert world.walk(row["id"])["ended_by"] == "max_pages" and world.actions(row["id"]) == []


def test_redirect_and_file_answers(world):
    world.stored(CLV2 + "&page=5", "sso.nknu.edu.tw/site/105000", fx("clv2_index.html"))
    a, target = world.press()
    out = world.answer(a, target.url, b"", status=302,
                       headers={"location": "/webservices/download.aspx?f=1", "content-type": "text/html"})
    assert out.location == "https://sso.nknu.edu.tw/webservices/download.aspx?f=1"
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (a["id"],))[0][0] == "redirect"
    world.f.x("UPDATE action SET kind='download' WHERE state='pending'")
    b, target = world.press()
    world.answer(b, target.url, b"%PDF-1.4 test", headers={"content-type": "application/octet-stream",
                                                            "content-disposition": 'attachment; filename="a.pdf"'})
    got = world.f.q("SELECT sniff, filename FROM action WHERE id=?", (b["id"],))[0]
    assert got["sniff"] == "pdf" and got["filename"] == "a.pdf"
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (b["id"],))[0][0] == "ok"


# ---------------------------------------------------------------- lists walked by GET (generic)

def _get_list(world, n: int, site: str = "example.nknu.edu.tw"):
    """A list of n pages by GET: page i links pages 1..n by number (and 下一頁 to i+1 when there is one)."""
    ids = []
    for i in range(1, n + 1):
        url = f"https://{site}/news?page={i}"
        world.f.set_host(site, queue="ip:140.127.1.1")
        world.f.add(url, host=site, site=site, unit=None, tier=1, kind="page", depth=0, found_via="seed",
                    found_from=None, owner_site=site)
        ids.append(world.f.row(url)["id"])
    for i, pid in enumerate(ids, 1):
        entries = [{"res_key": f"https://{site}/news?page={j}", "how": "get", "role": "pager", "need": "must",
                    "res_id": ids[j - 1], "text": str(j)} for j in range(1, n + 1) if j != i]
        if i < n:
            entries.append({"res_key": f"next:{i}", "how": "get", "role": "pager", "need": "must",
                            "res_id": ids[i], "text": "下一頁"})
        world.f.set_page_res(pid, entries)
    return ids


def _done(world, uid: int) -> None:
    world.f.finish(uid, state="done", status=200, kind="page", sniff="html", page_role="list")


def test_generic_get_walk(world):
    ids = _get_list(world, 3)
    _done(world, ids[0])
    assert world.chains.get_walk(ids[0]) == "walking"
    for uid in ids[1:]:
        _done(world, uid)
    assert world.chains.get_walk(ids[0]) == "last_page"
    for uid in ids:
        lw = world.walk(uid)
        assert (lw["ended_by"], lw["ok"], lw["profile"], lw["pages"]) == ("last_page", 1, "generic", 3)


def test_generic_get_walk_next_on_the_last_page(world):
    ids = _get_list(world, 2)
    world.f.add_page_res(ids[1], [{"res_key": "next:back", "how": "get", "role": "pager", "need": "must",
                                   "res_id": ids[0], "text": "下一頁"}])
    for uid in ids:
        _done(world, uid)
    assert world.chains.get_walk(ids[1]) == "mismatch:no_next"


def test_generic_get_walk_last_page_not_got(world):
    ids = _get_list(world, 2)
    _done(world, ids[0])
    world.f.finish(ids[1], state="failed", reason="timeout", attempts=3)
    assert world.chains.get_walk_for(ids[1]) is None
    lw = world.walk(ids[0])
    assert lw["ended_by"] is None and lw["ok"] == 0 and "page 2 not got" in lw["note"]


def test_generic_get_walk_with_an_unknown_button(world):
    ids = _get_list(world, 2)
    for uid in ids:
        _done(world, uid)
    world.f.add_page_res(ids[0], [{"res_key": "postback:WUC$x", "how": "unhandled", "role": "click",
                                   "need": "must", "why": "unhandled:unknown"}])
    assert world.chains.get_walk(ids[0]) == "mismatch:unknown_button"


def test_page_completeness_sees_the_walk(world):
    """The views read what the chain wrote (§10.4): walking is 还没抓完, the walk ended at its last page is complete
    when every press got its answer."""
    row, _got = world.stored(SSO_LIST, "sso.nknu.edu.tw/news", fx("sso_list_p1.html"))
    world.f.x("INSERT INTO page_type(key, site, verdict, source) VALUES ('k', 'sso.nknu.edu.tw/news', 'ok', 'import')")
    world.f.x("UPDATE url SET page_type='k', page_role='list' WHERE id=?", (row["id"],))

    def level():
        return world.f.q("SELECT level FROM page_completeness WHERE id=?", (row["id"],))[0][0]

    assert level() == "waiting"
    a, target = world.press()
    world.answer(a, target.url, fx("sso_list_p2.html"))
    assert level() == "complete"


def test_a_form_the_profile_does_not_name_is_never_sent(world):
    html = fx("cnknu_list_p1.html").replace('id="form1"', 'id="formX"').replace("forms['form1']", "forms['formX']")
    world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", html)
    a = world.chains.next_action("ip:140.127.40.173")
    assert world.chains.target_for(a) is None
    assert world.f.q("SELECT state, reason FROM action WHERE id=?", (a["id"],))[0][:] == \
        ("mismatch", "form_not_in_profile:formX")


def test_a_pager_answered_with_a_redirect_ends_the_walk(world):
    row, _got = world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_list_p1.html"))
    a, target = world.press()
    assert dict(target.data)["__VIEWSTATE"] == "VS-CNKNU-P1"
    out = world.answer(a, target.url, b"", status=302, headers={"location": "/aca/filenotfound.htm"})
    assert out.location == "https://c.nknu.edu.tw/aca/filenotfound.htm"
    lw = world.walk(row["id"])
    assert lw["ended_by"] == "mismatch:page" and "redirect" in lw["note"]


def test_cnknu_walk_two_pages(world):
    row, _got = world.stored(CNKNU_LIST, "c.nknu.edu.tw/aca", fx("cnknu_list_p1.html"))
    a, target = world.press()
    assert target.url == "https://c.nknu.edu.tw/aca/newslist.aspx?gi=1"
    data = dict(target.data)
    assert (data["__EVENTTARGET"], data["__EVENTARGUMENT"]) == ("MasterGrid", "Page$2")
    assert "btn_Search" not in data and data["ddl_GroupId"] == "全部"  # the search's submit is never sent
    world.answer(a, target.url, fx("cnknu_list_p2.html"))
    lw = world.walk(row["id"])
    assert (lw["ended_by"], lw["pages"], lw["rows_unique"]) == ("last_page", 2, 3)


def test_sso_category_totals(world):
    def walk(cat: str, claimed: int):
        url = f"https://sso.nknu.edu.tw/news/subjectlist.aspx?cat={cat}"
        html = fx("sso_list_p2.html").replace("共4筆", f"共{claimed}筆").replace(
            "javascript:__doPostBack(&#39;gvFull&#39;,&#39;Page$1&#39;)", "#")
        row, _got = world.stored(url, "sso.nknu.edu.tw/news", html)
        return row["id"]
    walk("SCHOOLADMIN", 2)
    walk("JOB", 2)
    whole = walk("all", 5)
    for list_id in [r[0] for r in world.f.q("SELECT list_id FROM listwalk")]:
        world.f.x("UPDATE listwalk SET ended_by='last_page', ok=1 WHERE list_id=?", (list_id,))
    assert world.chains.sso_totals() == (4, 5)
    assert world.walk(whole)["ended_by"] == "mismatch:total"


# ---------------------------------------------------------------- a download answered by an export page (pilot 10-26, F3)

PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _download_press(world):
    """MoreNews stored, its first detail answered (one file planned), its second too, then the file's press."""
    row, _got = world.stored(MORE, "sso.nknu.edu.tw/web/3", fx("webidx_morenews.html"))
    a, target = world.press()
    world.answer(a, target.url, fx("webidx_detail.html"))
    b, target = world.press()
    world.answer(b, target.url, fx("webidx_detail_wrong.html"))
    c, target = world.press()
    assert c["kind"] == "download"
    return row, c, target


def test_file_followup_reads_the_export_iframe_the_answer_creates():
    html = fx("sso_download_export.html")
    assert postback.file_followup(html, MORE) == scope.canonical("/WebServices/ajaxDownloadFile.aspx", MORE)
    assert postback.file_followup(fx("webidx_detail.html"), MORE) is None  # no export script: the page is the answer
    off = html.replace("'/WebServices/ajaxDownloadFile.aspx'", "'https://files.example.org/x.pdf'")
    assert postback.file_followup(off, MORE) is None  # another host's address is no session-bound export


def test_a_download_answered_with_an_export_page_is_fetched_from_that_address_as_the_press(world):
    """SSO's download buttons (WebIdx lnkDownloadFile, cLv2 downloadBtnN) answer with the page again and a startup
    script that opens /WebServices/ajaxDownloadFile.aspx in an iframe: the file comes from that GET, in the same
    session, right after the press (pilot 10-26, F3: 61 presses on web/595 ended not_file)."""
    row, c, target = _download_press(world)
    out = world.answer(c, target.url, fx("sso_download_export.html"))
    assert out.file_get == postback.file_followup(fx("sso_download_export.html"), target.url)
    a = world.f.q("SELECT * FROM action WHERE id=?", (c["id"],))[0]
    assert a["state"] == "active" and a["status"] == 200 and a["sniff"] == "html"  # the press's own answer, kept
    snap = world.archive(out.file_get, PDF, headers={"content-type": "application/octet-stream",
                                                     "content-disposition": 'attachment; filename="名單.pdf"'})
    world.chains.file_answered(c, snap, world.warc)
    a = world.f.q("SELECT * FROM action WHERE id=?", (c["id"],))[0]
    assert (a["state"], a["sniff"], a["filename"], a["location"]) == ("done", "pdf", "名單.pdf", out.file_get)
    assert (a["warc_offset"], a["sha256"], a["bytes"]) == (snap.warc_offset, snap.sha256, len(PDF))
    assert world.f.q("SELECT result FROM action_result WHERE id=?", (c["id"],))[0][0] == "ok"
    res = {r["res_key"]: r["result"] for r in world.f.q("SELECT res_key, result FROM page_resource WHERE page_id=?",
                                                        (row["id"],))}
    assert res[f"pb:download:{c['row_key']}"] == "ok"


def test_a_file_address_that_answers_a_web_page_again_is_not_file(world):
    _row, c, target = _download_press(world)
    out = world.answer(c, target.url, fx("sso_download_export.html"))
    world.chains.file_answered(c, world.archive(out.file_get, "<html><body>附件無法下載</body></html>"), world.warc)
    assert world.f.q("SELECT state, result FROM action_result WHERE id=?", (c["id"],))[0][:] == ("done", "not_file")


def test_an_export_to_another_host_is_not_followed(world):
    _row, c, target = _download_press(world)
    off = fx("sso_download_export.html").replace("'/WebServices/ajaxDownloadFile.aspx'", "'https://files.example.org/x'")
    out = world.answer(c, target.url, off)
    assert out.file_get is None
    assert world.f.q("SELECT state, result FROM action_result WHERE id=?", (c["id"],))[0][:] == ("done", "not_file")
