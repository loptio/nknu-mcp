"""The first real night on one small site (the owner 11-16: 「挑一个小站试一晚」): go-live --sites names only the sites
given as under update, so the nightly script's put-back, sites and run take that site alone, and a run's start lets go
of no other site's capped rows (crawler._release_caps judges every site's held rows; a site under update has run caps
instead). Made-up addresses."""

from __future__ import annotations

import json

from nknu.archive import crawler as crawler_mod
from nknu.archive import update
from nknu.archive.frontier import Frontier

from .test_update_select_1116 import QUIET, _pager, _site, _url

A, B = "a.nknu.edu.tw", "b.nknu.edu.tw"


def _two_sites(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    for s in (A, B):
        _site(f, s, "1")
        _url(f, "/", site=s, depth=0, found_via="seed")
        f.seed_src(f"https://{s}/", "site_home")
    f.close()


def _named(root) -> list[str] | None:
    g = Frontier(root / "frontier.db")
    try:
        raw = g.meta(crawler_mod.UPDATE_SITES_KEY)
    finally:
        g.close()
    return json.loads(raw) if raw else None


def test_go_live_with_sites_names_only_those(tmp_path, capsys):
    _two_sites(tmp_path)
    assert update.main(["--dir", str(tmp_path), "go-live", "--sites", B]) == 0
    assert _named(tmp_path) == [B]
    assert update.main(["--dir", str(tmp_path), "sites"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == B
    # the night that follows takes B alone, with no --sites of its own (what the nightly script runs)
    assert update.main(["--dir", str(tmp_path), "put-back", "--today", QUIET.isoformat()]) == 0
    g = Frontier(tmp_path / "frontier.db")
    try:
        assert {r["owner_site"] for r in g.q("SELECT owner_site FROM url WHERE state='pending'")} == {B}
    finally:
        g.close()


def test_go_live_with_a_site_not_under_update_writes_nothing(tmp_path, capsys):
    _two_sites(tmp_path)
    assert update.main(["--dir", str(tmp_path), "go-live", "--sites", f"{B},c.nknu.edu.tw/typo"]) == 1
    assert _named(tmp_path) is None
    assert "c.nknu.edu.tw/typo" in capsys.readouterr().err


def test_go_live_again_without_sites_names_every_round_one_site(tmp_path):
    _two_sites(tmp_path)
    assert update.main(["--dir", str(tmp_path), "go-live", "--sites", A]) == 0
    assert update.main(["--dir", str(tmp_path), "go-live"]) == 0
    assert _named(tmp_path) == [A, B]


def test_a_run_start_holds_only_the_named_sites_to_run_caps(tmp_path):
    """crawler.start_run_caps reads what go-live --sites wrote: every other site keeps the round's caps, so a run's
    start (crawler._release_caps) lets none of their capped rows go."""
    _two_sites(tmp_path)
    assert update.main(["--dir", str(tmp_path), "go-live", "--sites", A]) == 0
    f = Frontier(tmp_path / "frontier.db")
    try:
        crawl = crawler_mod.Crawler.__new__(crawler_mod.Crawler)
        crawl.frontier, crawl.clock = f, lambda: 1.0
        crawl.start_run_caps()
        assert crawl._update_sites == frozenset({A})
    finally:
        f.close()


def test_a_list_with_no_first_page_counts_on_its_own_site_only(tmp_path):
    """A list paged by GET whose pages never say 「1」 (update.select: get_no_first_page) is counted for its own site,
    not in every site's plan (11-16: each candidate's plan said 「认不出第 1 页 3」)."""
    _two_sites(tmp_path)
    f = Frontier(tmp_path / "frontier.db")
    try:
        p2, p3 = _url(f, "/news.aspx?page=2", site=A), _url(f, "/news.aspx?page=3", site=A)
        for src, dst, text in ((p2, p3, "3"), (p3, p2, "2")):
            _pager(f, src, dst, text)
        assert update.select(f, tmp_path, sites={B}, today=QUIET).skipped.get("get_no_first_page", 0) == 0
        assert update.select(f, tmp_path, sites={A}, today=QUIET).skipped.get("get_no_first_page", 0) == 1
    finally:
        f.close()


def test_go_live_says_how_many_stored_list_pages_it_could_not_read(tmp_path, capsys):
    """The 11-16 rehearsal gave the ledger copy an empty warc folder: go-live read 0 list rows and said nothing of it
    (postback.backfill_list_rows skips an answer it cannot read). A list unread is all news on the first night."""
    cn = "c.nknu.edu.tw/x"
    f = Frontier(tmp_path / "frontier.db")
    try:
        _site(f, cn, "1")
        lst = _url(f, "/x/NewsList.aspx?gi=1", site=cn)  # a c.nknu list (its profile reads it); no WARC file there
        f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, rows, rows_unique, claimed, ended_by, ok, note, "
            "at) VALUES (?, ?, 'cnknu', 1, 1, 3, 3, NULL, 'last_page', 1, NULL, 1)", (lst, cn))
    finally:
        f.close()
    assert update.main(["--dir", str(tmp_path), "go-live"]) == 0
    assert "读不到的列表页 1 页" in capsys.readouterr().out
