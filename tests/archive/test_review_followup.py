"""The second look at the 10-26 review fixes (F1–F7): one or more tests per point the reviewer raised that held, each
written to fail before its fix. Offline: nothing here sends a request.

- c.nknu's filenotfound.htm?aspxerrorpath= is a program error, retried (design §10.1, record 二·4 and 二·6), not 学校那边
  已经不存在: the address alone does not show it is the 404 page (affair's sitemap.aspx went there while aca's, ccee's
  and reality's answered 200, old archive, read-only).
- an attachment counts as not got when it lands on a web page, or where the rules fetch nothing, however it was
  reached: a short link, a frame, or a redirect along the way that was expected to be a file.
- a page a person marked as the school's is complete_school only when what it needs is there too.
- an attachment the school's link sends to its home page can be marked, by the address that redirects.
- what may be marked is narrower (no lab, refused, http_4xx; failed only when the server refused the connection or
  kept answering 5xx), a mark counts only for the fetch it was made on, and a mark of a result that may not be marked
  counts for nothing.
- short links: a rule that leaves the address out comes first; without the Google step a Google address is for a
  person (unhandled:google), never 规则不抓; Google's sign-in page is a login.
- YouTube: playlists and live streams are not videos (each keeps its own title), a video in a script or a thumbnail is
  noted too, and YouTube's image host is YouTube's (never fetched).
"""

from __future__ import annotations

import pytest

from nknu.archive import links, outcome, round1, scope
from nknu.archive.frontier import Frontier
from tests.archive.test_outcome import (
    FILE,
    ROW_FOR,
    SHORT,
    SITE,
    TYPES,
    add_action,
    add_res,
    add_url,
    completeness,
    lv,
    moved,
    result_of,
    url_of,
    walk,
)

PLAN = round1.load()


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    for key, verdict in TYPES.items():
        fr.x("INSERT INTO page_type(key, site, verdict) VALUES (?,?,?)", (key, SITE, verdict))
    yield fr
    fr.close()


ACA = {"owner_site": "c.nknu.edu.tw/aca", "tier": 0}
PAGE = "https://c.nknu.edu.tw/aca/news.aspx"


# ---------------------------------------------------------------- c.nknu's filenotfound.htm: an error, retried


@pytest.mark.parametrize("url", [
    "https://c.nknu.edu.tw/affair/filenotfound.htm?aspxerrorpath=/affair/sitemap.aspx",
    "https://cacd.nknu.edu.tw/cacd/FileNotFound.htm?aspxerrorpath=/cacd/activeviewlist.aspx",
])
def test_c_nknus_filenotfound_page_is_a_program_error_retried_as_the_design_says(url):
    sig = outcome.error_signature(url)
    assert (sig.result, sig.flag, sig.reason) == ("error", None, "error_page")
    assert all(s.result != "gone" or s.hosts for s in outcome.ERROR_SIGNATURES)  # gone: SSO's pageNotFound only


def test_a_filenotfound_redirect_retries_then_is_a_server_error_a_person_may_mark(f):
    assert result_of(f, add_url(f, state="pending", attempts=1, reason="error_page", status=None)) == "retry"
    uid = add_url(f, state="failed", attempts=3, reason="error_page", status=None)
    assert result_of(f, uid) == "server_error" and outcome.user_bucket("server_error") == "failed"
    f.mark_school_side(uid, "人看过：这一页在学校那边确实没有")
    assert result_of(f, uid) == "school_side"


# ---------------------------------------------------------------- a file expected, a web page or a rule at the end


def test_an_attachment_behind_a_short_link_that_lands_on_a_web_page_is_not_got(f):
    home = add_url(f)
    doc = moved(f, home, **{**FILE, "url": "https://c.nknu.edu.tw/aca/a.doc"})
    p = add_url(f)
    add_res(f, p, "shortlink", res=add_url(f, **{**SHORT, "status": 302, "final_url": url_of(f, doc)}))
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_failed"], r["miss_file"], r["miss_other"]) == ("partial", "failed", 1, 1, 0)
    assert "miss_file" in r["reasons"].split(",")  # 缺附件, not 缺内嵌页或按钮


def test_a_frame_whose_file_lands_on_a_web_page_is_not_got(f):
    home = add_url(f)
    p = add_url(f)
    add_res(f, p, "iframe", res=moved(f, home, **FILE))  # an iframe of a PDF that now goes to the home page
    assert lv(f, p) == ("partial", "failed")
    assert completeness(f, p)["miss_file"] == 1


def test_a_resource_sent_to_a_google_address_with_no_google_step_is_for_a_person(f):
    p = add_url(f)
    r = moved(f, "https://drive.google.com/file/d/1AbC/view", **FILE)
    f.link(r, "https://drive.google.com/file/d/1AbC/view", "[redirect]", "unhandled:google")
    add_res(f, p, "file", res=r)
    assert f.q("SELECT result FROM page_resource WHERE page_id=?", (p,))[0][0] == "unhandled"
    assert lv(f, p) == ("partial", "review")


def test_a_short_link_or_a_frame_that_leads_to_a_web_page_is_got(f):
    page = add_url(f, tier=2)
    p = add_url(f)
    add_res(f, p, "shortlink", res=add_url(f, **{**SHORT, "url": "https://reurl.cc/page1", "status": 302,
                                                 "final_url": url_of(f, page)}))
    add_res(f, p, "iframe", res=moved(f, page))  # a frame page that moved
    assert lv(f, p) == ("complete", "complete")


@pytest.mark.parametrize("to, verdict, want", [
    ("https://drive.google.com/file/d/1AbC/view", "out:never_host", ("partial", "failed")),
    ("https://www.facebook.com/nknu/posts/1", "out:social", ("partial", "failed")),
    ("https://c.nknu.edu.tw/aca/a/a/a/a.pdf", "out:trap", ("partial", "failed")),
    ("https://c.nknu.edu.tw/aca/clip.mp4", "out:media", ("complete", "complete")),  # a video: the rule's own case
    ("https://webmail.nknu.edu.tw/login", "out:login", ("complete_school", "complete_school")),
    ("https://drive.google.com/x", "unhandled:google", ("partial", "review")),  # no Google step: for a person
])
def test_an_attachment_redirected_where_no_row_was_made_counts_by_what_it_should_have_been(f, to, verdict, want):
    p = add_url(f)
    r = moved(f, to, **FILE)
    f.link(r, to, "[redirect]", verdict)
    add_res(f, p, "file", res=r)
    assert lv(f, p) == want
    q = add_url(f)  # the same behind a short link
    hop = moved(f, to, **{**FILE, "url": f"https://c.nknu.edu.tw/aca/s{r}.pdf"})
    f.link(hop, to, "[redirect]", verdict)
    add_res(f, q, "shortlink", res=add_url(f, **{**SHORT, "url": f"https://reurl.cc/x{r}", "status": 302,
                                                 "final_url": url_of(f, hop)}))
    assert lv(f, q) == want


@pytest.mark.parametrize("verdict, level", [("out:social", "partial"), ("out:media", "complete"),
                                            ("out:login", "complete_school")])
def test_a_download_press_redirected_where_no_row_was_made_is_not_got_unless_a_rule_says_what_it_is(f, verdict, level):
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    to = "https://www.facebook.com/x"
    f.link(lst, to, "[redirect]", verdict)
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, kind="download", status=302, location=to))
    assert lv(f, lst)[0] == level


# ---------------------------------------------------------------- a page a person marked: its resources still count


def test_a_marked_page_is_complete_school_only_when_what_it_needs_is_there(f):
    thin = add_url(f, flags="thin")
    att = add_url(f, **{**FILE, "url": "https://c.nknu.edu.tw/aca/d.pdf", "state": "failed", "attempts": 3,
                        "reason": "timeout", "status": None})
    add_res(f, thin, "file", res=att)
    f.mark_school_side(thin, "人看过：这页学校本来就几乎是空的")
    r = completeness(f, thin)
    assert (r["own"], r["level"], r["label"]) == ("school_side", "partial", "failed")
    f.x("UPDATE url SET state='done', status=200, reason=NULL, sniff='pdf' WHERE id=?", (att,))
    assert lv(f, thin) == ("complete_school", "complete_school")


# ---------------------------------------------------------------- B5 marked by the address that redirects


def test_an_attachment_the_schools_link_sends_home_can_be_marked_by_that_link(f):
    home = add_url(f)
    doc = moved(f, home, **{**FILE, "url": "https://c.nknu.edu.tw/elec/a.doc"})
    p1, p2 = add_url(f), add_url(f)
    add_res(f, p1, "file", res=doc)
    add_res(f, p1, "file", res=home)  # the crawler's copy of the row for the redirect's target (design §7.1)
    add_res(f, p2, "file", res=doc)
    assert lv(f, p1) == ("partial", "failed") and lv(f, p2) == ("partial", "failed")
    assert f.mark_school_side(doc, "人看过：学校的附件链接指到首页") == "not_file"
    assert result_of(f, doc) == "redirect"  # the row itself is still a redirect; what it led to is the school's
    assert lv(f, p1) == ("complete_school", "complete_school") and lv(f, p2) == ("complete_school", "complete_school")
    f.finish(doc, status=302, final_url=url_of(f, home), fetched_at="2099-01-01T00:00:00+00:00")  # fetched again
    assert lv(f, p1) == ("partial", "failed")  # the mark was for the fetch a person looked at


def test_a_redirect_that_leads_to_nothing_markable_cannot_be_marked(f):
    fine = moved(f, add_url(f, **FILE), **FILE)  # a file that moved to a file: nothing wrong
    add_res(f, add_url(f), "file", res=fine)
    unused = moved(f, add_url(f))  # a redirect no page uses as a resource
    for uid in (fine, unused):
        with pytest.raises(ValueError, match="school"):
            f.mark_school_side(uid, "看过")


# ---------------------------------------------------------------- what may be marked, and for how long


@pytest.mark.parametrize("cols", [
    ROW_FOR["lab"],  # the lab machine fetches it again (record 三·19)
    ROW_FOR["refused"], ROW_FOR["http_4xx"],
    {"state": "failed", "attempts": 3, "reason": "timeout", "status": None},  # may be ours
    {"state": "failed", "reason": "crash:KeyError", "status": None},  # ours
    {"state": "failed", "attempts": 3, "reason": "tls", "status": None},  # a certificate we may not trust yet
])
def test_what_may_be_ours_cannot_be_marked(f, cols):
    uid = add_url(f, **cols)
    with pytest.raises(ValueError, match="school"):
        f.mark_school_side(uid, "看过")


@pytest.mark.parametrize("cols", [
    {"state": "failed", "attempts": 3, "reason": "connect", "status": None},  # the host refuses every connection
    {"state": "failed", "attempts": 3, "reason": "http_503", "status": 503},  # the server keeps failing
])
def test_a_server_that_refuses_or_keeps_failing_can_be_marked(f, cols):
    uid = add_url(f, **cols)
    f.mark_school_side(uid, "主机已经下线")
    assert result_of(f, uid) == "school_side"


def test_a_mark_counts_for_the_fetch_it_was_made_on_and_never_comes_back(f):
    uid = add_url(f, **{**FILE, "flags": "bad_link", "fetched_at": "2026-10-26T01:00:00+00:00"})
    f.mark_school_side(uid, "人看过：学校的链接本身坏了")
    assert result_of(f, uid) == "school_side"
    f.finish(uid, status=200, reason=None, flags="bad_link")  # fetched again, the same answer
    assert result_of(f, uid) == "bad_link"


@pytest.mark.parametrize("cols, result", [({**FILE}, "ok"), ({**FILE, "flags": "thin"}, "unknown"),
                                          (ROW_FOR["refused"], "refused")])
def test_a_mark_of_a_result_that_may_not_be_marked_counts_for_nothing(f, cols, result):
    uid = add_url(f, **cols)
    row = f.q("SELECT reason, status, fetched_at FROM url WHERE id=?", (uid,))[0]
    f.x("INSERT INTO review_mark(url_id, mark, result, reason, status, fetched_at, note, at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, "school_side", result, row["reason"], row["status"], row["fetched_at"], "x", "2026-10-26T00:00:00Z"))
    assert result_of(f, uid) == result


# ---------------------------------------------------------------- short links and Google's addresses


@pytest.mark.parametrize("href, reason", [
    ("https://bit.ly/login", "out:login"),
    ("https://bit.ly/captcha1", "out:login"),
    ("https://bit.ly/" + "a" * 1200, "out:too_long"),
])
def test_a_short_link_a_rule_leaves_out_is_never_asked(href, reason):
    got = round1.admit(PLAN, href, PAGE, ACA)
    assert (got.step, got.placement.verdict, got.placement.reason, got.placement.row) == ("place", "out", reason, False)


@pytest.mark.parametrize("href", [
    "https://drive.google.com/file/d/1AbC/view?usp=sharing",
    "https://docs.google.com/document/d/1AbC/edit",
    "https://drive.google.com/drive/folders/1AbC",
    "https://calendar.google.com/calendar/embed?src=nknubf%40gmail.com",
    "https://www.google.com/calendar/embed?src=x%40group.calendar.google.com",
])
def test_without_the_google_step_a_google_address_is_for_a_person_never_a_rule(href):
    for how in ("link", "frame", "image"):
        got = round1.admit(PLAN, href, PAGE, ACA, how)
        p = got.placement
        assert (p.verdict, p.reason, p.row, got.step) == ("unhandled", "unhandled:google", False, "google"), how


def test_a_google_form_needs_no_google_step():
    got = round1.admit(PLAN, "https://docs.google.com/forms/d/e/1FAIpQLSf_abc/viewform", PAGE, ACA)
    assert (got.placement.verdict, got.placement.kind0) == ("resource", "form")


def test_googles_sign_in_page_is_a_login():
    for href in ("https://accounts.google.com/ServiceLogin?continue=https://docs.google.com/forms/d/e/x/viewform",
                 "https://accounts.google.com/v3/signin/identifier?continue=x"):
        assert scope.judge(scope.canonical(href)).reason == "login"
        assert round1.admit(PLAN, href, PAGE, ACA).placement.reason == "out:login"


# ---------------------------------------------------------------- YouTube


YT = """<html><body><main>
<iframe src="https://www.youtube.com/embed/videoseries?list=PL111" title="播放清单一"></iframe>
<iframe src="https://www.youtube.com/embed/videoseries?list=PL222" title="播放清单二"></iframe>
<iframe src="https://www.youtube.com/embed/live_stream?channel=UC1abc" title="直播"></iframe>
<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ" title="校庆影片"></iframe>
<a href="javascript:window.open('https://www.youtube.com/watch?v=JJJJJJJJJJJ')">开幕影片</a>
<div onclick="location.href='https://youtu.be/OOOOOOOOOOO'">社团介绍</div>
<img src="https://i.ytimg.com/vi/TTTTTTTTTTT/hqdefault.jpg" alt="演讲缩图">
</main></body></html>"""


def test_playlists_and_live_streams_are_not_videos_and_each_keeps_its_own_title():
    got = {v.video_id: (v.title, v.how) for v in links.extract(YT, PAGE).videos}
    assert "videoseries" not in got and "live_stream" not in got
    assert got["list:PL111"] == ("播放清单一", "embed") and got["list:PL222"] == ("播放清单二", "embed")
    assert got["live:UC1abc"] == ("直播", "embed")
    assert got["dQw4w9WgXcQ"] == ("校庆影片", "embed")


def test_a_video_in_a_script_or_a_thumbnail_is_noted_too():
    got = {v.video_id: (v.title, v.how) for v in links.extract(YT, PAGE).videos}
    assert got["JJJJJJJJJJJ"] == ("开幕影片", "link")
    assert got["OOOOOOOOOOO"] == ("社团介绍", "link")
    assert got["TTTTTTTTTTT"] == ("演讲缩图", "image")


@pytest.mark.parametrize("url", ["https://i.ytimg.com/vi/TTTTTTTTTTT/hqdefault.jpg",
                                 "https://img.youtube.com/vi/TTTTTTTTTTT/0.jpg"])
def test_youtubes_thumbnails_are_youtubes_never_fetched(url):
    assert scope.judge(url).reason == "social"
    got = round1.admit(PLAN, url, PAGE, ACA, "image").placement
    assert (got.verdict, got.reason, got.row) == ("out", "out:social", False)


def test_a_page_with_only_youtube_thumbnails_is_complete(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    try:
        fr.x("INSERT INTO page_type(key, site, verdict) VALUES ('t:ok', 'c.nknu.edu.tw/aca', 'ok')")
        pid = add_url(fr)
        fr.x("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?,?,?,?,?,?)",
             (pid, "https://i.ytimg.com/vi/TTTTTTTTTTT/hqdefault.jpg", "get", "img", "must", "out:social"))
        assert lv(fr, pid) == ("complete", "complete")
    finally:
        fr.close()
