"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.1; the owner 11-17,
relayed by 爬虫judge: 「现在开始准备」, the design approved by 爬虫judge under the owner's 11-17 delegation): the list
says what this batch crawls. The special-education centre (ksped) is a round-one site whole; the teachers' CV pages
(dataopen2search/vita.aspx) are in round one's part of their key; 30 of the 35 off-campus unit sites carry the batch
(`now`) and a small cap of their own and are placed as this round's (本系重要); the 5 the list calls 基本没用 stay out.
The real list file; made-up addresses under its keys."""

from __future__ import annotations

import pytest

from nknu.archive import round1

LOW = {"mandy900316.wixsite.com/website", "nknubwy2.wixsite.com/nknu-bwy", "ocsnknu.wixsite.com/index",
       "sites.google.com/view/nknu-edu-teach1", "www.nknu-vd.com"}
BATCH = "2026-11-17"


@pytest.fixture(scope="module")
def plan():
    return round1.load()


def test_ksped_is_this_round_whole(plan):
    for url in ("https://ksped.nknu.edu.tw/newsdetail.aspx?nid=1", "https://ksped.nknu.edu.tw/case_detail.aspx?hid=2",
                "https://ksped.nknu.edu.tw/resource/x.aspx"):
        p = round1.verdict(plan, url)
        assert (p.verdict, p.owner_site, p.tier) == ("r1", "ksped.nknu.edu.tw", 0), url


def test_the_cv_pages_are_this_round_and_the_rest_of_their_key_is_not(plan):
    p = round1.verdict(plan, "https://sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=1")
    assert (p.verdict, p.owner_site, p.tier) == ("r1", "sso.nknu.edu.tw/dataopen2search", 0)
    q = round1.verdict(plan, "https://sso.nknu.edu.tw/dataopen2search/opendatasearch.aspx?dataidx=1")
    assert (q.verdict, q.reason) == ("round2", "round2:other_site")


def test_thirty_units_carry_the_batch_and_a_cap_and_the_five_low_ones_do_not(plan):
    units = {k: s for k, s in plan.sites.items() if s.why == "offsite_unit"}
    assert len(units) == 35
    now = {k for k, s in units.items() if s.now}
    assert now == set(units) - LOW and len(now) == 30
    wider = {"nknugender.wixsite.com/nknugender": (900, 300, 2600)}  # 11-17, widened by the design's §四 way, counted
    assert all(units[k].now == BATCH and units[k].caps == wider.get(k, (300, 300, 600)) for k in now)
    assert all(not units[k].now and units[k].caps is None for k in LOW)


def test_a_unit_of_the_batch_is_placed_this_round_inside_its_reach_only(plan):
    p = round1.verdict(plan, "https://github.com/gdg-on-campus-nknu/site-x")
    assert (p.verdict, p.owner_site, p.tier, p.leaf) == ("r1", "github.com", 1, 0)
    q = round1.verdict(plan, "https://github.com/someone-else/repo")
    assert (q.verdict, q.reason, q.leaf) == ("round2", "round2:offsite", 1)
    w = round1.verdict(plan, "https://nknufablab.github.io/docs/intro")
    assert (w.verdict, w.owner_site, w.tier) == ("r1", "nknufablab.github.io", 1)


def test_a_low_unit_stays_round_two(plan):
    p = round1.verdict(plan, "https://www.nknu-vd.com/about/")
    assert (p.verdict, p.reason) == ("round2", "round2:offsite_unit")


def test_a_unit_page_of_the_batch_takes_its_images_and_files_along(plan):
    page = {"owner_site": "nknufablab.github.io", "tier": 1, "url": "https://nknufablab.github.io/"}
    img = round1.verdict(plan, "https://cdn.example.org/a.png", page, "image")
    assert (img.verdict, img.owner_site, img.tier) == ("resource", "nknufablab.github.io", 1)


def test_this_round_and_tier_of_a_batch_site():
    s = round1.parse_site({"site": "u.example.org", "round": 2, "why": "offsite_unit", "home": "https://u.example.org/",
                           "now": BATCH, "caps": {"page": 10, "file": 20, "image": 30}})
    assert s.this_round and s.tier == 1 and s.caps == (10, 20, 30)
    t = round1.parse_site({"site": "v.example.org", "round": 2, "why": "offsite_unit", "home": "https://v.example.org/"})
    assert not t.this_round and t.tier == round1.ROUND2_TIER and t.caps is None


@pytest.mark.parametrize("entry", [
    {"site": "a.nknu.edu.tw", "round": 1, "imp": "全校重要", "home": "https://a.nknu.edu.tw/", "now": BATCH},
    {"site": "b.example.org", "round": 2, "why": "offsite_unit", "home": "https://b.example.org/", "now": BATCH,
     "caps": {"page": 10}},
    {"site": "c.example.org", "round": 2, "why": "offsite_unit", "home": "https://c.example.org/", "now": BATCH,
     "caps": {"page": 0, "file": 1, "image": 1}},
])
def test_a_bad_batch_entry_is_refused(entry):
    with pytest.raises(ValueError):
        round1.parse_site(entry)


def test_the_scope_audit_reads_a_batch_sites_page_and_parts_as_in_bounds(plan):
    from nknu.archive import monitor

    page = "https://github.com/gdg-on-campus-nknu/site-x"
    assert monitor._placement_breach(plan, page, "admit:r1:github.com:page", set()) is None
    assert monitor._placement_breach(plan, "https://cdn.example.org/a.png", "admit:resource:nknufablab.github.io:image",
                                     set()) is None
    other = "https://github.com/someone-else/repo"
    assert monitor._placement_breach(plan, other, "admit:r1:github.com:page", set()) is not None
    assert monitor._placement_breach(plan, "https://www.nknu-vd.com/a", "admit:r1:www.nknu-vd.com:page",
                                     set()) is not None  # a 基本没用 unit is not this batch's
