"""SSO's addresses the crawler mishandled (11-03):

P02: the shared module's activity detail pages (/services/academicactivities/actdetail.aspx?unid=…, and
campusactivities') are a platform's detail pages, free of the variant cap (design §9, §7.4 detail_paths: 免上限); in the
official archive 446 of them were held (cap:variant) and never fetched. Rows held so before the fix are let go at the
next run's start.

P05: one SSO WebIdx page under several spellings (…?webidx=N&menuid=M, …?cwebidx=N&cmenuid=M, either with &pagenum=1)
had its download buttons pressed once per spelling. In the 10-26/27 pilot every group of such spellings that was fetched
more than once (27 groups) answered the same links, the same buttons, and the same files (sha256) behind them: those
spellings are one address now, and a row queued under another spelling is skipped (out:alias, final_url the address it
is the same page as) at the next run's start, its page asked under that one address only.

Everything runs on tests/archive/test_crawler_round1.py's fake network and fake clock; no address leaves this
machine."""

from __future__ import annotations

import pytest

from nknu.archive import crawler as mod
from nknu.archive import links, scope
from tests.archive.plans import OpenPlan
from tests.archive.test_crawler_round1 import crawl, page, rows
from tests.archive.test_run_only import _known, run

ACT = "https://sso.nknu.edu.tw/services/academicactivities/actdetail.aspx?unid="
CAMP = "https://sso.nknu.edu.tw/services/campusactivities/actdetail.aspx?unid="
OTHER = "https://sso.nknu.edu.tw/services/other/list.aspx?d="
WEB = "https://sso.nknu.edu.tw/services/web/default.aspx?"


@pytest.fixture(autouse=True)
def _workers_leave_when_idle(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_EXIT_S", -1.0)


def sso_crawler(tmp_path, routes=None, **kw):
    crawler, net, clock = crawl(tmp_path, {"/services/": page("<p>活動</p>"), **(routes or {})}, plan=OpenPlan(),
                                **kw)
    _known(crawler.frontier, "sso.nknu.edu.tw")
    return crawler, net, clock


# ---------------------------------------------------------------- P02: activity detail pages and the variant cap


@pytest.mark.parametrize("url", [ACT + "MjAwOTAwMDg0", CAMP + "MjAyMzAwMTIz",
                                 "https://sso.nknu.edu.tw/Services/AcademicActivities/ActDetail.aspx?unid=x"])
def test_ssos_activity_detail_pages_are_platform_pages(url):
    assert scope.is_platform_path(url)


def test_activity_detail_pages_pass_the_variant_cap_and_other_paths_still_do_not(tmp_path):
    crawler, _, _ = sso_crawler(tmp_path, variant_cap=2)
    got = [crawler.enqueue(f"{ACT}a{i}", depth=1, found_via="link") for i in range(4)]
    got += [crawler.enqueue(f"{CAMP}c{i}", depth=1, found_via="link") for i in range(3)]
    assert set(got) == {"queued"}
    other = [crawler.enqueue(f"{OTHER}{i}", depth=1, found_via="link") for i in range(3)]
    assert other == ["queued", "queued", "cap:variant"]


def test_activity_details_held_before_the_fix_are_fetched_at_the_next_run_and_real_caps_stay(tmp_path):
    seeder, _, _ = sso_crawler(tmp_path, variant_cap=2)
    for i in range(3):
        seeder.enqueue(f"{OTHER}{i}", depth=1, found_via="link")  # the third: held, cap:variant (the path is full)
    f = seeder.frontier
    for url in (ACT + "h1", ACT + "h2", CAMP + "h3"):  # held by the crawler before 11-03
        f.add(url, host="sso.nknu.edu.tw", site=scope.site_key(url), unit=None, tier=1, kind="page", depth=1,
              found_via="commoncrawl", found_from=None, state="held", reason="cap:variant",
              owner_site="sso.nknu.edu.tw/services", prio=2, kind0="page")
    f.close()
    crawler, net, _ = sso_crawler(tmp_path, variant_cap=2)
    run(crawler)
    got = rows(crawler)
    for url in (ACT + "h1", ACT + "h2", CAMP + "h3"):
        assert got[url]["state"] == "done" and url in net.seen
    assert (got[f"{OTHER}2"]["state"], got[f"{OTHER}2"]["reason"]) == ("held", "cap:variant")
    assert f"{OTHER}2" not in net.seen


def test_a_run_given_only_lets_no_held_row_go(tmp_path):
    """--only fetches the listed rows pending at its start (crawler._narrow_only): a held row, listed or not, stays."""
    seeder, _, _ = sso_crawler(tmp_path)
    f = seeder.frontier
    for url in (ACT + "h1", ACT + "h2"):
        f.add(url, host="sso.nknu.edu.tw", site=scope.site_key(url), unit=None, tier=1, kind="page", depth=1,
              found_via="commoncrawl", found_from=None, state="held", reason="cap:variant",
              owner_site="sso.nknu.edu.tw/services", prio=2, kind0="page")
    listed = f.row(ACT + "h1")["id"]
    f.close()
    crawler, net, _ = sso_crawler(tmp_path, only=frozenset({listed}))
    run(crawler)
    got = rows(crawler)
    for url in (ACT + "h1", ACT + "h2"):
        assert (got[url]["state"], got[url]["reason"]) == ("held", "cap:variant") and url not in net.seen


# ---------------------------------------------------------------- P05: one WebIdx page, one address


@pytest.mark.parametrize(("url", "want"), [
    (WEB + "cwebidx=595&cmenuid=506", WEB + "webidx=595&menuid=506"),
    (WEB + "CWebIdx=595&CMenuID=506&PageNum=1", WEB + "webidx=595&menuid=506"),
    (WEB + "webidx=595&menuid=506&pagenum=1", WEB + "webidx=595&menuid=506"),
    (WEB + "cwebidx=595&cmenuid=506&pagenum=2", WEB + "webidx=595&menuid=506&pagenum=2"),
    (WEB + "cwebidx=599", WEB + "webidx=599"),
    # spellings the pilot did not show to be the same page keep theirs
    (WEB + "cwebidx=610&ctheme=Spacelab", WEB + "cwebidx=610&ctheme=Spacelab"),
    (WEB + "webidx=595&selpage=morenews&pagenum=1&sort=date", WEB + "webidx=595&selpage=morenews&pagenum=1&sort=date"),
    (WEB + "webidx=595&cwebidx=596", WEB + "webidx=595&cwebidx=596"),
    (WEB + "webidx=595&pagenum=1", WEB + "webidx=595&pagenum=1"),
])
def test_one_webidx_page_has_one_address(url, want):
    assert scope.canonical(url) == want


def test_a_pager_link_from_the_first_page_still_reads_as_the_next_page(tmp_path):
    crawler, _, _ = sso_crawler(tmp_path)
    first = scope.canonical(WEB + "cwebidx=595&cmenuid=506&pagenum=1")
    assert crawler.enqueue(WEB + "webidx=595&menuid=506", depth=1, found_via="link") == "queued"
    assert crawler.enqueue(WEB + "cwebidx=595&cmenuid=506", depth=1, found_via="link") == "dup"
    assert crawler.enqueue(WEB + "webidx=595&menuid=506&pagenum=1", depth=1, found_via="link") == "dup"
    assert links.is_pager(first, scope.canonical(WEB + "webidx=595&menuid=506&pagenum=2"), "2")


def _add_web(f, url, **kw):
    cols = {"host": "sso.nknu.edu.tw", "site": scope.site_key(url), "unit": None, "tier": 1, "kind": "page",
            "depth": 1, "found_via": "link", "found_from": None, "owner_site": "sso.nknu.edu.tw/web/595", "prio": 1,
            "kind0": "page", **kw}
    f.add(url, **cols)
    return f.row(url)["id"]


def test_rows_queued_under_another_spelling_are_skipped_and_their_page_asked_once(tmp_path):
    seeder, _, _ = sso_crawler(tmp_path)
    f = seeder.frontier
    canon = WEB + "webidx=595&menuid=506"
    _add_web(f, canon)
    alias_ids = [_add_web(f, WEB + q) for q in ("cwebidx=595&cmenuid=506", "cwebidx=595&cmenuid=506&pagenum=1",
                                                 "webidx=595&menuid=506&pagenum=1")]
    # an alias whose page has no row under its one address: that row is made, and the alias skipped
    lone = _add_web(f, WEB + "cwebidx=595&cmenuid=511", state="held", reason="cap:site")
    # a list page fetched under an alias whose chain was left pressing: its presses are not sent again
    done_alias = _add_web(f, WEB + "cwebidx=595&cmenuid=509&pagenum=1")
    f.x("UPDATE url SET state='done', status=200 WHERE id=?", (done_alias,))
    _add_web(f, WEB + "webidx=595&menuid=509", state="done")
    f.conn.execute(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
        "target, argument, row_key, state, created) VALUES (?,1,1,'download','q','sso.nknu.edu.tw',"
        "'sso.nknu.edu.tw/web/595','sso_webidx/-','none.warc.gz',0,'btn','','k1','pending',0)", (done_alias,))
    f.close()
    crawler, net, _ = sso_crawler(tmp_path, site_cap=1)  # the site is full: the run's start keeps the made row held
    run(crawler)
    got = rows(crawler)
    assert net.seen.count(canon) == 1
    for uid in (*alias_ids, done_alias):
        r = crawler.frontier.row_by_id(uid)
        assert (r["state"], r["reason"]) == ("skipped", "out:alias")
        assert r["final_url"] == scope.canonical(r["url"]) and r["url"] not in net.seen
    lone_row = crawler.frontier.row_by_id(lone)
    assert (lone_row["state"], lone_row["reason"], lone_row["final_url"]) == (
        "skipped", "out:alias", WEB + "webidx=595&menuid=511")
    made = got[WEB + "webidx=595&menuid=511"]
    assert (made["state"], made["reason"]) == ("held", "cap:site")  # it keeps what the alias row had
    presses = crawler.frontier.q("SELECT state FROM action WHERE list_id=?", (done_alias,))
    assert [p["state"] for p in presses] == ["superseded"]
    assert WEB + "webidx=595&menuid=509" not in net.seen
