"""Round two, batch two, 11-17 (爬虫judge under the owner's delegation, choice B: 「照网站地图把 567 篇文章、24 页、17 个分类排进
待抓；这一站新发现的标签页不再往下翻；网页上限 900、图片 2,600。规则只写在这一站的名单档里」): a site of the list may name
paths of its own not followed (skip): an address under one is left out by the rule out:skip_path (no url row, the page
that links it says why), every other address of the site as before. The gender institute's old Wix blog skips its
tag lists (/blog/hashtags: every post they list is in the site's own sitemap). Made-up addresses."""

from __future__ import annotations

import pytest

from nknu.archive import export, progress, round1

GENDER = "nknugender.wixsite.com/nknugender"
BASE = "https://nknugender.wixsite.com/nknugender"


@pytest.fixture(scope="module")
def plan():
    return round1.load()


def test_a_site_entry_may_name_paths_it_does_not_follow():
    s = round1.parse_site({"site": "x.example.org/blog", "round": "2", "why": "offsite_unit",
                           "home": "https://x.example.org/blog", "paths": ["/blog"], "now": "2026-11-17",
                           "skip": ["/blog/Hashtags"]})
    assert s.skip == ("/blog/hashtags",)
    assert s.skips("https://x.example.org/blog/hashtags/a")
    assert s.skips("https://x.example.org/blog/hashtags")
    assert not s.skips("https://x.example.org/blog/hashtags2")  # whole segments
    assert not s.skips("https://x.example.org/blog/post/a")


def test_the_gender_blog_skips_its_tag_lists_and_keeps_its_posts(plan):
    site = plan.site(GENDER)
    assert site.skip == ("/nknugender/blog/hashtags",) and site.caps == (900, 300, 2600)
    tag = round1.verdict(plan, f"{BASE}/blog/hashtags/%E6%BC%94%E8%AC%9B")
    assert (tag.verdict, tag.reason, tag.state) == ("out", "out:skip_path", None)
    post = round1.verdict(plan, f"{BASE}/single-post/a-post")
    assert (post.verdict, post.owner_site) == ("r1", GENDER)
    cat = round1.verdict(plan, f"{BASE}/blog/categories/anno")
    assert cat.verdict == "r1"


def test_the_rule_has_words():
    assert export.RULE_WHY["skip_path"] and "skip_path" in progress.PLAIN


# ---------------------------------------------------------------- GitHub: the org's home and each repo's home only


GH = "https://github.com/GDG-on-Campus-NKNU"


def test_a_skip_may_hold_any_segment_and_a_site_may_skip_every_query():
    s = round1.parse_site({"site": "github.com", "round": "2", "why": "offsite_unit", "home": GH,
                           "paths": ["/gdg-on-campus-nknu"], "now": "2026-11-17",
                           "skip": ["/gdg-on-campus-nknu/*/*"], "skip_query": True})
    assert not s.skips(GH) and not s.skips(f"{GH}/NKNU-Core")  # the org's home, a repo's home
    assert s.skips(f"{GH}/NKNU-Core/tree/main") and s.skips(f"{GH}/NKNU-Core/commits")
    assert s.skips(f"{GH}?tab=repositories") and s.skips(f"{GH}/NKNU-Core?search=1")


def test_github_keeps_its_org_and_repo_homes_only(plan):
    site = plan.site("github.com")
    assert site.skip == ("/gdg-on-campus-nknu/*/*",) and site.skip_query
    assert round1.verdict(plan, f"{GH}/NKNU-Core").verdict == "r1"
    for url in (f"{GH}/NKNU-Core/blob/main/README.md", f"{GH}/NKNU-Core/forks", f"{GH}?tab=repositories"):
        assert round1.verdict(plan, url).reason == "out:skip_path", url


def test_a_page_got_before_its_rule_is_exported_as_the_rule_with_no_file(tmp_path, monkeypatch):
    from tests.archive.test_export_1027 import BASE
    from tests.archive.test_export_step3_1108 import _with

    rule = round1.parse_site({"site": "c.nknu.edu.tw/aca", "round": "1", "imp": "全校重要", "home": BASE,
                              "paths": ["/aca"], "skip": ["/aca/about.aspx"]})
    monkeypatch.setattr(export, "_site_rules", lambda site: rule)
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda a: None)
    r = rows[BASE + "about.aspx"]
    assert (r["分类"], r["本地档案"]) == ("规则不抓", "") and export.RULE_WHY["skip_path"] in r["原因"]


def test_the_five_javascript_sites_are_crawled_now_with_a_round_one_sites_caps(plan):
    js = {"w3.nknu.edu.tw", "ord.nknu.edu.tw", "ite.nknu.edu.tw", "gauss.nknu.edu.tw", "see.nknu.edu.tw"}
    for key in js:
        s = plan.site(key)
        assert s.why == "js_site" and s.now and s.caps is None and s.this_round, key
    assert round1.verdict(plan, "https://ord.nknu.edu.tw/p/news.php?id=1").verdict == "r1"
