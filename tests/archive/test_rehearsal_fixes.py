"""What the offline rehearsal of 10-26 (design §18.1, rehearsal_expect.json) caught in how the crawler records things:

- error pages (design §10.1 「错误页本身不入队」, §18.1 「/errorpage/... 那几行本身不算内容」): only a 3xx to one was kept
  out; the same address brought by a seed list (the 10-24 archive's url table, Common Crawl) or a page's link became a
  round-1 page of its own, counted incomplete or thin in page_completeness (news.nknu's /errorpage/pagenotfound.aspx,
  c.nknu's filenotfound.htm?aspxerrorpath= and default.aspx?aspxerrorpath=, ksped's default.aspx?aspxerrorpath=). Now
  round1.admit(), the one way in, keeps every ERROR_SIGNATURES address out (out:error_page); a script or refresh that
  sends a page to one is judged as a 3xx to it; a part of a page whose address is one is a broken link (bad_link).
- request.scope (B7): a part of a round-1 page on its own site's host but outside the list's paths (ksped's
  /uploadfile/ beside its /resource/) said admit:r1, which the scope audit rightly read as a breach; it is a resource.
"""

from __future__ import annotations

import pytest

from nknu.archive import outcome, round1, scope
from nknu.archive.monitor import _landed_nowhere, scope_audit
from tests.archive.plans import plan, site
from tests.archive.test_archive import snap
from tests.archive.test_crawler_round1 import (
    ACA,
    HOME,
    NEWS,
    QC,
    QS,
    crawl,
    links_of,
    page,
    result,
    rows,
    settle,
)
from tests.archive.test_reconcile import BODY, Arch, _thin, _tpl

KSPED = site("ksped.nknu.edu.tw", name="特教中心资源", paths=("/resource/",),
             home="https://ksped.nknu.edu.tw/resource/default.aspx")
QK = "ip:ksped.nknu.edu.tw"
NEWS_HOST = site("news.nknu.edu.tw", imp="全校重要", name="新闻", home="https://news.nknu.edu.tw/")

ERROR_PAGES = [
    "https://news.nknu.edu.tw/errorpage/pagenotfound.aspx",
    "https://sso.nknu.edu.tw/ErrorPage/PageNotFound.aspx?aspxerrorpath=/News/x.aspx",
    "https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/sitemap.aspx",
    "https://c.nknu.edu.tw/aca/default.aspx?aspxerrorpath=/aca/ForumsView.aspx",
    "https://ksped.nknu.edu.tw/resource/default.aspx?aspxerrorpath=/resource/Page.aspx",
]
ERROR_PLAN = plan(ACA, NEWS, NEWS_HOST, KSPED, site("sso.nknu.edu.tw/errorpage"))


# ---------------------------------------------------------------- error pages: never a row, whoever brings them


@pytest.mark.parametrize("url", ERROR_PAGES)
def test_admit_keeps_every_error_page_out_whatever_the_list_says_of_its_site(url):
    adm = round1.admit(ERROR_PLAN, url)
    assert adm is not None and adm.placement.verdict == "out"
    assert adm.placement.reason == round1.ERROR_PAGE and not adm.placement.row


@pytest.mark.parametrize("url", ERROR_PAGES)
def test_an_error_page_from_a_seed_list_makes_no_row_and_no_page(tmp_path, url):
    crawler, _net, _ = crawl(tmp_path, {}, plan=ERROR_PLAN)
    for found_via in ("old_frontier", "commoncrawl", "seed"):
        assert crawler.seed_url(url, found_via=found_via) == round1.ERROR_PAGE
    assert rows(crawler) == {}
    assert crawler.frontier.q("SELECT count(*) FROM page_completeness")[0][0] == 0


def test_an_error_page_a_page_links_to_is_in_the_link_table_only(tmp_path):
    err = "https://c.nknu.edu.tw/aca/default.aspx?aspxerrorpath=/aca/Page.aspx"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(f'<p>教務處公告</p><a href="{err}">找資源</a>')})
    crawler.seed_url(HOME)
    settle(crawler, net, [QC])
    assert err not in rows(crawler)
    assert links_of(crawler, HOME)[err] == round1.ERROR_PAGE
    assert err not in net.seen


def test_a_picture_whose_address_is_an_error_page_is_the_schools_broken_link(tmp_path):
    err = "https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/uploadfile/a.jpg"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(f'<p>教務處公告</p><img src="{err}">')})
    crawler.seed_url(HOME)
    settle(crawler, net, [QC])
    pid = rows(crawler)[HOME]["id"]
    got = crawler.frontier.q("SELECT result FROM page_resource WHERE page_id=?", (pid,))
    assert [r[0] for r in got] == ["bad_link"]
    level = crawler.frontier.q("SELECT level FROM page_completeness WHERE id=?", (pid,))[0][0]
    assert level == "complete_school"  # the school's broken link (the owner 11-16 「销账照表做」, export batch T5)


@pytest.mark.parametrize(("to", "own"), [
    ("https://sso.nknu.edu.tw/ErrorPage/PageNotFound.aspx?aspxerrorpath=/News/x.aspx", "gone"),
    ("https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/x.aspx", "server_error"),
])
def test_a_page_a_script_sends_to_an_error_page_is_judged_as_a_3xx_to_it(tmp_path, to, own):
    html = f"<script>location.href='{to}';</script>"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(html)}, plan=ERROR_PLAN)
    crawler.seed_url(HOME)
    settle(crawler, net, [QC, QS])
    r, canon = rows(crawler), scope.canonical(to)
    assert canon not in r and canon not in net.seen
    assert links_of(crawler, HOME)[canon] == outcome.ERROR_LINK
    assert result(crawler, HOME) == own
    level = crawler.frontier.q("SELECT level FROM page_completeness WHERE id=?", (r[HOME]["id"],))[0][0]
    assert level == ("complete_school" if own == "gone" else "incomplete")


def test_a_short_link_to_an_error_page_lands_on_a_broken_link(tmp_path):
    err = "https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/x.aspx"
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page('<p>教務處</p><a href="https://reurl.cc/AbCd">表</a>'),
                                       "reurl.cc": snap("x", 302, headers={"location": err})},
                            queues={"c.nknu.edu.tw": QC, "reurl.cc": "ip:reurl"})
    crawler.seed_url(HOME)
    settle(crawler, net, [QC, "ip:reurl"])
    short = rows(crawler)["https://reurl.cc/AbCd"]
    assert err not in rows(crawler)
    assert _landed_nowhere(crawler.frontier, short["id"]) == "bad_link"


# ---------------------------------------------------------------- request.scope: a part outside the list's paths


@pytest.mark.parametrize(("tag", "path", "kind0"), [
    ('<img src="/uploadfile/cmsfiles/a.jpg">', "/uploadfile/cmsfiles/a.jpg", "image"),
    ('<a href="/uploadfile/cmsfiles/a.pdf">附件</a>', "/uploadfile/cmsfiles/a.pdf", "file"),
])
def test_a_part_on_its_sites_host_outside_the_lists_paths_is_admitted_as_a_resource(tmp_path, tag, path, kind0):
    home = KSPED.home
    body = {"image": (b"\x89PNG\r\n\x1a\n" + b"\0" * 100, "image/png"), "file": (b"%PDF-1.4 x", "application/pdf")}
    data, ctype = body[kind0]
    crawler, net, _ = crawl(tmp_path, {"/resource/default.aspx": page(f"<p>特教資源</p>{tag}"),
                                       path: lambda u: snap(u, body=data, headers={"content-type": ctype})},
                            plan=plan(KSPED), queues={"ksped.nknu.edu.tw": QK})
    crawler.seed_url(home)
    settle(crawler, net, [QK])
    part = f"https://ksped.nknu.edu.tw{path}"
    assert rows(crawler)[part]["state"] == "done"
    scopes = {r["url"]: r["scope"] for r in crawler.frontier.q("SELECT url, scope FROM request WHERE purpose='page'")}
    assert scopes[home] == "admit:r1:ksped.nknu.edu.tw:page"
    assert scopes[part] == f"admit:resource:ksped.nknu.edu.tw:{kind0}"
    assert scope_audit(crawler.frontier, crawler.plan) == []


def test_a_page_of_its_own_site_inside_the_lists_paths_is_still_admitted_as_r1(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/resource/default.aspx": page('<p>特教</p><a href="/resource/Page.aspx?p=1">頁</a>'),
                                       "/resource/page.aspx": page("<p>特教資源頁的正文，足夠長的一段文字內容。</p>")},
                            plan=plan(KSPED), queues={"ksped.nknu.edu.tw": QK})
    crawler.seed_url(KSPED.home)
    settle(crawler, net, [QK])
    scopes = {r["url"]: r["scope"] for r in crawler.frontier.q("SELECT url, scope FROM request WHERE purpose='page'")}
    assert scopes["https://ksped.nknu.edu.tw/resource/page.aspx?p=1"] == "admit:r1:ksped.nknu.edu.tw:page"
    assert scope_audit(crawler.frontier, crawler.plan) == []


# ---------------------------------------------------------------- thin: a page and its copies are one page of the layout


@pytest.fixture
def arch(tmp_path):
    a = Arch(tmp_path)
    yield a
    a.close()


def test_copies_of_one_listing_under_many_addresses_never_make_its_words_the_layout(arch):
    """The 10-26 rehearsal: id.nknu's WordPress shows one category listing under several addresses (category/x/page/2,
    category/category/x …): six pages of one kind, four of them the same listing. Counted page by page, the listing's
    lines were on half of the kind, taken out as layout, and the listings (150-200 characters of post titles) were
    thin. Each distinct page now counts once, its copies included (design §10.1's 5 pages and 50% are of distinct
    pages; 【Claude 的执行办法】); a copy of an empty page is thin all the same."""
    listing = f"<p>{BODY}</p><p>2025年05月15日 系友回娘家</p>"
    copies = [arch.page(f"/category/x/page/{i}", _tpl(listing), "list") for i in range(4)]
    others = [arch.page(f"/category/y{i}", _tpl(f"<p>第{i}類：{BODY}</p>"), "list") for i in range(2)]
    news = [arch.page(f"/news/{i}", _tpl(f"<p>第{i}則：{BODY}</p>"), "news") for i in range(5)]
    empty = [arch.page(f"/news/empty{i}", _tpl("<p>短</p>"), "news") for i in range(3)]
    got = _thin(arch)
    assert sorted(got.thin) == sorted(empty)
    assert all(arch.f.row_by_id(i)["flags"] is None for i in (*copies, *others, *news))


# ---------------------------------------------------------------- review of the rehearsal fixes (10-26)


def test_a_page_of_its_sites_host_outside_the_lists_paths_let_in_by_a_bug_is_a_breach_the_audit_sees(tmp_path):
    """Review 10-26 (清单①): request.scope said resource for every row on its site's host outside the list's paths,
    so a page a bug let in there as r1 read admit:resource and the scope audit passed it. Only a part (not a page) or
    a page a page uses (a frame: page_res, or through a short link or a redirect) is that page's resource; any other
    page there says r1, which the audit reads against the list."""
    bad = "https://ksped.nknu.edu.tw/news/list.aspx"  # not under /resource/
    crawler, net, _ = crawl(tmp_path, {"/resource/default.aspx": page("<p>特教資源首頁的正文內容。</p>"),
                                       "/news/list.aspx": page("<p>不在名單範圍裡的網頁正文。</p>")},
                            plan=plan(KSPED), queues={"ksped.nknu.edu.tw": QK})
    crawler.seed_url(KSPED.home)
    placed = round1.Placement("r1", None, "ksped.nknu.edu.tw", 0, 0, "page")  # what a bug would write
    crawler._enqueue(bad, placement=placed, url=bad, found_via="link")
    settle(crawler, net, [QK])
    scopes = {r["url"]: r["scope"] for r in crawler.frontier.q("SELECT url, scope FROM request WHERE purpose='page'")}
    assert scopes[bad] == "admit:r1:ksped.nknu.edu.tw:page"
    breaches = scope_audit(crawler.frontier, crawler.plan)
    assert len(breaches) == 1 and bad in breaches[0]


def test_a_frame_of_its_sites_host_outside_the_lists_paths_is_still_the_pages_resource(tmp_path):
    frame = "https://ksped.nknu.edu.tw/news/frame.aspx"
    crawler, net, _ = crawl(tmp_path, {"/resource/default.aspx": page('<p>特教資源</p><iframe src="/news/frame.aspx">'
                                                                      "</iframe>"),
                                       "/news/frame.aspx": page("<p>框架裡的正文，足夠長的一段文字內容。</p>")},
                            plan=plan(KSPED), queues={"ksped.nknu.edu.tw": QK})
    crawler.seed_url(KSPED.home)
    settle(crawler, net, [QK])
    assert rows(crawler)[frame]["state"] == "done"
    scopes = {r["url"]: r["scope"] for r in crawler.frontier.q("SELECT url, scope FROM request WHERE purpose='page'")}
    assert scopes[frame] == "admit:resource:ksped.nknu.edu.tw:page"
    assert scope_audit(crawler.frontier, crawler.plan) == []


# ---------------------------------------------------------------- an error page's address: never 规则不抓 (清单②)


def test_a_menu_item_whose_address_is_an_error_page_is_the_schools_broken_link_never_a_rule(tmp_path):
    """Review 10-26: R1 read every out: verdict as 规则不抓, out:error_page too, so a menu item that is the school's
    broken link passed unseen. It is bad_link: listed for a person (⚠ 要看), never a rule, never a missed address."""
    from nknu.archive import reconcile
    from nknu.archive.frontier import Frontier

    err = "https://c.nknu.edu.tw/aca/filenotfound.htm?aspxerrorpath=/aca/x.aspx"
    fr = Frontier(tmp_path / "frontier.db")
    try:
        fr.link(1, err, "選單", outcome.ERROR_OUT)
        assert reconcile._menu_status(fr, err) == "bad_link"
        check, found = reconcile._r1(fr, {err: HOME})
        assert check.status == reconcile.WARN and check.counts["bad_link"] == 1 and check.counts["rule"] == 0
        assert [(i["target"], i["status"]) for i in check.items] == [(err, "bad_link")]
        assert found == []  # the crawler missed nothing: the school's link is broken
    finally:
        fr.close()


def test_a_resource_with_no_row_whose_address_is_an_error_page_is_a_broken_link_not_a_rule():
    from nknu.archive import progress

    assert progress._no_row_result("must", outcome.ERROR_OUT) == "bad_link"
    assert progress._no_row_result("no", outcome.ERROR_OUT) == "bad_link"
    assert progress._no_row_result("must", "out:media") == "rule"
    assert progress._no_row_result("must", "out:login") == "login"
