"""Short links and link wrappers (round-1 design §7.7, review 10-26 A2): one admission entry, round1.admit(), for every
address a page (or a seed list, a redirect, rejudge, the scope audit) brings: the wrappers taken off without a request
(Google's /url, Facebook's l.php, SSO's ExternalLink), a short link held for one GET to learn where it leads, a Google
address handed to the Google step (google_api.parse_link, a later phase), everything else placed by scope.judge() and
round1.verdict(). A short link's answer is read in three steps (round1.short_answer): a 3xx's Location; a 200 page's
meta refresh, then the service's own writing (scope.SHORTLINK_RULES: reurl.cc keeps it in input#target); else
unhandled:shortlink, for a person. A short link that answers 404 is dead (short_dead), not a page the school removed.
Its target is judged as if the page had linked it directly: a form is the page's form, an attachment its attachment.

Before 10-26: forms.gle was never_host (125 of the round-1 pages' 161 forms behind it, shown as 规则不抓), lurl.cc no
short link (35), reurl.cc's 200 page unreadable (83), l.facebook.com/l.php a social link (6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nknu.archive import links, round1, scope

PLAN = round1.load()
ACA = {"owner_site": "c.nknu.edu.tw/aca", "tier": 0}  # 教務處, 全校重要
CCEE = {"owner_site": "c.nknu.edu.tw/ccee", "tier": 1}
LATER = {"owner_site": "c.nknu.edu.tw/ae", "tier": 2}  # a round-2 page
# reurl.cc/3Lp06M as the old archive holds it (2026-10-23), cut to what matters: its analytics and pixel scripts out
REURL = (Path(__file__).parent.parent / "fixtures" / "archive_shortlink" / "reurl_redirecting.html").read_text("utf-8")
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSdAbCdEfGhIjKlMnOp/viewform?usp=send_form"


def admit(href: str, src=ACA, how: str = "link", **kw) -> round1.Admission | None:
    return round1.admit(PLAN, href, "https://c.nknu.edu.tw/aca/news.aspx", src, how, **kw)


# ---------------------------------------------------------------- which addresses are short links


@pytest.mark.parametrize("href", [
    "https://forms.gle/AbCdEfGh123", "https://reurl.cc/3Lp06M", "https://lurl.cc/AbCdE", "https://bit.ly/3xYz",
    "https://tinyurl.com/nknu-x", "https://lihi1.cc/AbCd", "https://ppt.cc/fAbC", "https://pse.is/4abc",
    "http://goo.gl/AbCd",
])
def test_a_short_link_on_a_round_1_page_is_its_resource_asked_once_where_it_leads(href):
    got = admit(href)
    p = got.placement
    assert (got.step, p.verdict, p.kind0, p.leaf, p.owner_site, p.tier, p.state) == (
        "shortlink", "resource", "shortlink", 1, "c.nknu.edu.tw/aca", 0, "pending")
    assert got.url == got.link == scope.canonical(href)
    assert scope.site_key(got.url) != scope.site_key(got.url).split("/")[0]  # each short link its own "site"


def test_forms_gle_and_lurl_are_short_links_now():
    assert "forms.gle" in scope.SHORTENERS and "lurl.cc" in scope.SHORTENERS
    assert "forms.gle" not in scope.NEVER_HOSTS
    assert scope.judge("https://forms.gle/AbCdEfGh123").reason != "never_host"


def test_a_short_link_only_a_round_2_page_or_nobody_links_waits_for_round_2():
    for src in (LATER, None):
        p = admit("https://reurl.cc/3Lp06M", src).placement
        assert (p.verdict, p.reason, p.kind0, p.leaf, p.state) == ("round2", "round2:offsite", "shortlink", 1, "later")


@pytest.mark.parametrize("href", ["https://goo.gl/maps/7Gr3NDiEYHVMkSUp6", "https://maps.app.goo.gl/TsEYN6G7DzL565jG7"])
def test_a_map_short_link_is_a_link_only_never_asked(href):
    got = admit(href)
    assert (got.step, got.placement.verdict, got.placement.reason) == ("place", "out", "out:not_content")


def test_a_short_link_services_own_pages_are_not_short_links():
    for href in ("https://reurl.cc/", "https://bit.ly"):
        got = admit(href)
        assert got.step == "place" and got.placement.reason == "round2:offsite", href


def test_what_is_no_address_is_not_admitted():
    for href in ("mailto:x@nknu.edu.tw", "javascript:void(0)", "#top", ""):
        assert admit(href) is None


# ---------------------------------------------------------------- wrappers come off first, without a request


def test_facebooks_link_wrapper_is_taken_off_like_googles():
    wrapped = ("https://l.facebook.com/l.php?u=https%3A%2F%2Freurl.cc%2FAbCdE%3Ffbclid%3DIwAR0x&h=AT0abc&s=1")
    assert scope.canonical(wrapped) == "https://reurl.cc/AbCdE"  # fbclid is a tracking parameter
    got = admit(wrapped)
    assert (got.step, got.link) == ("shortlink", "https://reurl.cc/AbCdE")
    mobile = admit("https://lm.facebook.com/l.php?u=https%3A%2F%2Fc.nknu.edu.tw%2Faca%2Fdefault.aspx&h=x")
    assert (mobile.step, mobile.placement.verdict, mobile.link) == ("place", "r1", "https://c.nknu.edu.tw/aca/default.aspx")
    assert admit("https://l.facebook.com/l.php?h=AT0abc").placement.reason == "out:social"  # no target: Facebook's


def test_a_google_redirect_to_a_short_link_is_that_short_link():
    got = admit("https://www.google.com/url?q=https%3A%2F%2Fforms.gle%2FAbCdEfGh123&sa=D&sntz=1")
    assert (got.step, got.link) == ("shortlink", "https://forms.gle/AbCdEfGh123")


# ---------------------------------------------------------------- what the short link's answer says


def test_a_3xx_says_where_with_its_location():
    a = round1.short_answer("https://forms.gle/AbCdEfGh123", 302, FORM, None)
    assert (a.target, a.how, a.reason) == (FORM, "location", None)
    rel = round1.short_answer("https://bit.ly/x", 301, "/y", None)
    assert rel.target == "https://bit.ly/y"
    lost = round1.short_answer("https://bit.ly/x", 302, None, None)
    assert (lost.target, lost.reason) == (None, "unhandled:shortlink")


def test_reurls_200_page_says_where_in_input_target():
    a = round1.short_answer("https://reurl.cc/3Lp06M", 200, None, REURL)
    assert a.how == "rule:reurl.cc" and a.reason is None
    assert a.target == ("https://www.facebook.com/pages/category/Art/"
                        "%E9%AB%98%E5%B8%AB%E5%A4%A7%E8%8C%B6%E9%81%93%E7%A4%BE-104517067594185/")
    assert admit(a.target).placement.reason == "out:social"  # a club's Facebook page: a link only
    assert scope.SHORTLINK_RULES["reurl.cc"] == ("input#target", "value")


def test_a_200_page_with_a_meta_refresh_says_where_before_any_rule():
    page = ('<html><head><meta http-equiv="refresh" content="0; url=https://c.nknu.edu.tw/aca/x.pdf"></head>'
            '<body><input id="target" value="https://example.com/other"></body></html>')
    a = round1.short_answer("https://reurl.cc/AbCdE", 200, None, page)
    assert (a.target, a.how) == ("https://c.nknu.edu.tw/aca/x.pdf", "refresh")
    assert links.shortlink_target(page, "https://reurl.cc/AbCdE") == ("https://c.nknu.edu.tw/aca/x.pdf", "refresh")


@pytest.mark.parametrize("url, page", [
    ("https://reurl.cc/AbCdE", "<html><body><p>此短網址已停用</p></body></html>"),  # nothing to read
    ("https://reurl.cc/AbCdE", '<html><body><input id="target" value="javascript:alert(1)"></body></html>'),
    ("https://lihi1.cc/AbCd", '<html><body><input id="target" value="https://c.nknu.edu.tw/aca/"></body></html>'),
    ("https://reurl.cc/AbCdE", None),  # a 200 that is no web page
])
def test_a_200_page_nobody_can_read_a_target_from_is_for_a_person(url, page):
    """Only the service's own writing counts (lihi's page is not read with reurl's rule): no guessing."""
    a = round1.short_answer(url, 200, None, page)
    assert (a.target, a.reason) == (None, "unhandled:shortlink")


def test_a_short_link_that_answers_404_is_dead_not_gone():
    for status in (404, 410):
        a = round1.short_answer("https://lurl.cc/dead1", status, None, "<p>404</p>")
        assert (a.target, a.reason) == (None, "short_dead")


def test_other_answers_are_judged_by_the_general_rules():
    for status in (403, 500, 503):
        assert round1.short_answer("https://bit.ly/x", status, None, None) == round1.ShortAnswer(None, None, None)


# ---------------------------------------------------------------- the target: as if the page linked it directly


def test_a_short_links_target_is_judged_as_if_the_page_linked_it():
    form = admit(round1.short_answer("https://forms.gle/AbCdEfGh123", 302, FORM, None).target)
    assert (form.placement.verdict, form.placement.kind0, form.placement.owner_site) == ("resource", "form",
                                                                                       "c.nknu.edu.tw/aca")
    school_file = admit("https://c.nknu.edu.tw/ae/uploadfile/x.pdf", CCEE).placement
    assert (school_file.verdict, school_file.kind0, school_file.owner_site, school_file.tier) == (
        "resource", "file", "c.nknu.edu.tw/ccee", 1)
    off_file = admit("https://www.edu.tw/files/x.pdf").placement  # off campus too (record 五·15)
    assert (off_file.verdict, off_file.kind0, off_file.leaf) == ("resource", "file", 1)
    picture = admit("https://i.imgur.com/AbCd.png", how="image").placement
    assert (picture.verdict, picture.kind0) == ("resource", "image")
    page = admit("https://c.nknu.edu.tw/aca/default.aspx").placement
    assert (page.verdict, page.owner_site) == ("r1", "c.nknu.edu.tw/aca")


def test_a_chain_of_short_links_is_followed_one_link_at_a_time():
    first = admit("https://bit.ly/3xYz")
    hop = round1.short_answer(first.url, 301, "https://reurl.cc/3Lp06M", None)
    second = admit(hop.target)
    assert (first.step, second.step, second.placement.owner_site) == ("shortlink", "shortlink", "c.nknu.edu.tw/aca")
    end = round1.short_answer(second.url, 200, None, REURL)
    assert admit(end.target).step == "place"


def test_google_addresses_go_to_the_google_step_before_any_rule():
    """The Google step (google_api.parse_link, design §7.6) says what a Drive, Docs or Calendar address becomes, before
    scope.judge() and its length limit; a short link to Drive comes to it the same way. Until that step is plugged in
    the address is unhandled:google, for a person, never never_host (规则不抓: second look F2)."""
    seen = []

    def google(url, src, how):
        seen.append(url)
        if "drive.google.com" not in url:
            return None
        api = "https://www.googleapis.com/drive/v3/files/1AbC?fields=id,name,mimeType"
        return round1.Admission(url, api, round1.Placement("resource", None, src["owner_site"], src["tier"], 1, "drive"),
                                "google")

    drive = "https://drive.google.com/file/d/1AbC/view?usp=sharing"
    a = round1.short_answer("https://reurl.cc/AbCdE", 302, drive, None)
    got = admit(a.target, google=google)
    assert (got.step, got.url, got.placement.kind0, got.placement.owner_site) == (
        "google", "https://www.googleapis.com/drive/v3/files/1AbC?fields=id,name,mimeType", "drive",
        "c.nknu.edu.tw/aca")
    long_cal = "https://calendar.google.com/calendar/embed?" + "&".join(f"src=c{i}%40group.calendar.google.com"
                                                                        for i in range(40))
    assert len(long_cal) > scope.MAX_URL
    assert admit(long_cal, google=google).placement.reason == "out:too_long"  # the step declined: judged as before
    assert seen[-1] == long_cal  # but it was asked first, before the length limit could say too_long
    assert admit(drive).placement.reason == "unhandled:google"  # no Google step yet: for a person, never a rule
    assert admit("https://c.nknu.edu.tw/aca/", google=google).step == "place" and seen[-1] == long_cal


def test_scripts_and_tabs_never_go_through_the_short_link_step():
    assert admit("https://reurl.cc/AbCdE", how="script").placement.reason == "out:media"
    tab = admit("https://c.nknu.edu.tw/aca/load_content.php?sub_id=16", how="click").placement
    assert (tab.verdict, tab.kind0) == ("resource", "click")
