"""Periodic updates, stage 1, Task 6 (plan docs/superpowers/plans/2026-11-16-update-stage-1.md; design 2026-11-16
§7.3): tonight's selection (nknu.archive.update): the lists walked by button, the first page of each list paged by
GET, the sites' home and seed pages; the plan read only, a put-back under run.lock, the go-live. A ledger built by
hand (made-up addresses), so that the rules are pinned apart from any crawl."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import update
from nknu.archive.frontier import Frontier

R1, R2 = "a.nknu.edu.tw", "b.nknu.edu.tw"
# a day on which no share of these fixtures falls (stage 2's monthly walks and weekly standing pages): these tests pin
# the nightly batches apart from the rotations (test_update_rotations_1116)
QUIET = date(2026, 11, 20)


def _site(f: Frontier, site: str, rnd: str) -> None:
    f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, ?)", (site, site, rnd))


def _url(f: Frontier, path: str, *, site: str = R1, state: str = "done", status: int = 200, kind: str = "page",
         tier: int = 1, depth: int = 1, found_via: str = "link", final_url: str | None = None) -> int:
    url = f"https://{site}{path}"
    f.set_host(site, queue=f"ip:{site}")
    f.add(url, host=site, site=site, unit=None, tier=tier, kind=kind, depth=depth, found_via=found_via,
          found_from=None, owner_site=site)
    uid = f.row(url)["id"]
    f.finish(uid, state=state, status=status, kind=kind, final_url=final_url, warc="warc/q.warc.gz",
             warc_offset=uid * 10)
    return uid


def _walk(f: Frontier, list_id: int, ended: str | None, rows: int | None) -> None:
    f.x("INSERT OR REPLACE INTO listwalk(list_id, site, profile, epoch, pages, rows, rows_unique, claimed, ended_by, ok, "
        "note, at) VALUES (?,?,?,1,2,?,?,NULL,?,?,NULL,1)", (list_id, R1, "cnknu", rows, rows, ended,
                                                             int(ended == "last_page")))


def _pager(f: Frontier, src: int, dst: int, text: str) -> None:
    f.add_page_res(src, [{"res_key": f"https://x/{dst}", "how": "get", "role": "pager", "need": "must",
                          "res_id": dst, "text": text}])


@pytest.fixture
def ledger(tmp_path):
    """A round-one site with: three lists walked by button (one ended, one walking, one with a press waiting), a list
    paged by GET in three pages, a home page, a sitemap page, a seed list (SSO's), an old v1 seed; a round-two site's
    home."""
    f = Frontier(tmp_path / "frontier.db")
    _site(f, R1, "1")
    _site(f, R2, "2")
    ids = {}
    ids["list"] = _url(f, "/newslist.aspx?gi=1")
    _walk(f, ids["list"], "last_page", 30)
    ids["walking"] = _url(f, "/newslist.aspx?gi=2")
    _walk(f, ids["walking"], "walking", 10)
    ids["waiting"] = _url(f, "/newslist.aspx?gi=3")
    _walk(f, ids["waiting"], "last_page", 12)
    f.x("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
        "target, row_key, state) VALUES (?, 1, 2, 'pager', 'q', ?, ?, 'cnknu/x', 'w', 1, 't', 'p2', 'pending')",
        (ids["waiting"], R1, R1))
    g1, g2, g3 = (_url(f, p) for p in ("/news/list.aspx", "/news/list.aspx?page=2", "/news/list.aspx?page=3"))
    for i in (g1, g2, g3):
        _walk(f, i, "last_page", None)
    for src, dst, text in ((g1, g2, "2"), (g1, g3, "3"), (g2, g1, "1"), (g2, g3, "3"), (g3, g1, "1"), (g3, g2, "2")):
        _pager(f, src, dst, text)
    ids.update(g1=g1, g2=g2, g3=g3)
    ids["home"] = _url(f, "/", depth=0, found_via="seed")
    f.seed_src(f"https://{R1}/", "site_home")
    ids["moved"] = _url(f, "/index.aspx", depth=0, found_via="seed", status=302, final_url=f"https://{R1}/zh/")
    f.seed_src(f"https://{R1}/index.aspx", "extra")
    ids["moved_to"] = _url(f, "/zh/", depth=0, found_via="redirect")
    ids["sitemap"] = _url(f, "/SiteMap.aspx", depth=0, found_via="seed")
    f.seed_src(f"https://{R1}/SiteMap.aspx", "sitemap_aspx")
    f.seed_src(f"https://{R1}/newslist.aspx?gi=1", "sso_lists")  # a seed that is a list: a look, never a home
    ids["v1"] = _url(f, "/old/notice.aspx?id=1", depth=0, found_via="v1_known")
    f.seed_src(f"https://{R1}/old/notice.aspx?id=1", "v1_known")
    ids["r2"] = _url(f, "/", site=R2, depth=0, found_via="seed", tier=2)
    f.seed_src(f"https://{R2}/", "site_home")
    yield f, ids, tmp_path
    f.close()


def test_tonight_takes_each_list_once_and_the_homes_apart(ledger):
    f, ids, root = ledger
    t = update.select(f, root, today=QUIET)
    assert t.looks == [ids["list"]]
    assert t.looks_get == [ids["g1"]]  # the list paged by GET: its first page only
    assert sorted(t.homes) == sorted([ids["home"], ids["moved"], ids["moved_to"], ids["sitemap"]])
    assert dict(t.skipped) == {"walk_unfinished": 1, "presses_waiting": 1}
    assert len(set(t.ids())) == len(t.ids())  # disjoint


def test_the_plan_reads_only_even_an_archive_from_before_stage_1(ledger, capsys):
    f, _ids, root = ledger
    f.x("DROP TABLE list_look")
    f.x("DROP TABLE list_row")
    f.close()
    db, wal = root / "frontier.db", root / "frontier.db-wal"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert not wal.exists() or wal.stat().st_size == 0
    assert update.main(["--dir", str(root), "plan"]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not wal.exists() or wal.stat().st_size == 0  # SQLite's read-only open may leave an empty -wal: no frame
    out = capsys.readouterr().out
    assert "按按钮翻页的列表 1 个" in out and "首页和起点 4 页" in out


def test_put_back_opens_the_looks_and_puts_the_pages_back(ledger, capsys):
    f, ids, root = ledger
    f.close()
    assert update.main(["--dir", str(root), "go-live"]) == 0
    assert update.main(["--dir", str(root), "put-back", "--today", QUIET.isoformat()]) == 0
    g = Frontier(root / "frontier.db")
    try:
        looks = {r["list_id"]: r["mode"] for r in g.q("SELECT * FROM list_look WHERE ended_at IS NULL")}
        assert looks == {ids["list"]: "look", ids["g1"]: "look_get"}
        pending = {r["id"] for r in g.q("SELECT id FROM url WHERE state='pending'")}
        assert pending == {ids["list"], ids["g1"], ids["home"], ids["moved"], ids["moved_to"], ids["sitemap"]}
        assert g.row_by_id(ids["v1"])["state"] == "done" and g.row_by_id(ids["r2"])["state"] == "done"
    finally:
        g.close()
    assert "放回去了" in capsys.readouterr().out


def test_put_back_is_refused_while_a_run_holds_the_archive(ledger):
    f, _ids, root = ledger
    f.close()
    assert update.main(["--dir", str(root), "go-live"]) == 0
    lock = crawler_mod.hold_run_lock(root)
    try:
        assert update.main(["--dir", str(root), "put-back", "--today", QUIET.isoformat()]) == 1
    finally:
        lock.close()
    g = Frontier(root / "frontier.db")
    try:
        assert not g.q("SELECT 1 FROM url WHERE state='pending'")
    finally:
        g.close()


def test_go_live_names_the_round_one_sites_under_update(ledger):
    f, _ids, root = ledger
    f.close()
    assert update.main(["--dir", str(root), "go-live"]) == 0
    g = Frontier(root / "frontier.db")
    try:
        assert json.loads(g.meta(crawler_mod.UPDATE_SITES_KEY)) == [R1]
    finally:
        g.close()


# ---------------------------------------------------------------- the review of 7f12e29..8474164 (fix pass)


def _tiny(tmp_path, *sites):
    f = Frontier(tmp_path / "frontier.db")
    for s in sites:
        _site(f, s, "1")
    return f


def test_a_home_inside_a_list_paged_by_get_stays_a_home(tmp_path):
    """An SSO unit site's home holds MoreNews page 1's grid and pager: it is in the pager group, a home all the same
    (only the group's first page is a GET look)."""
    f = _tiny(tmp_path, R1)
    home = _url(f, "/web/default.aspx", depth=0, found_via="seed")
    f.seed_src(f"https://{R1}/web/default.aspx", "site_home")
    p1, p2 = _url(f, "/web/more.aspx?pagenum=1"), _url(f, "/web/more.aspx?pagenum=2")
    for i in (home, p1, p2):
        _walk(f, i, "last_page", None)
    for src, dst, text in ((home, p1, "1"), (home, p2, "2"), (p1, p2, "2"), (p2, p1, "1")):
        _pager(f, src, dst, text)
    t = update.select(f, tmp_path, today=QUIET)
    assert t.looks_get == [p1] and t.homes == [home]
    f.close()


def test_the_owners_extra_seed_on_a_key_of_no_round_one_is_under_update(tmp_path):
    """The 7 extra seeds (archive_round1.json extra) sit on run keys site_plan lists as round 2 or not at all."""
    f = _tiny(tmp_path, R1)
    _site(f, "sso.nknu.edu.tw/recruit", "2")
    extra = _url(f, "/recruit/index.aspx", site="sso.nknu.edu.tw/recruit", depth=0, found_via="seed", tier=0)
    f.seed_src("https://sso.nknu.edu.tw/recruit/recruit/index.aspx", "extra")
    t = update.select(f, tmp_path, today=QUIET)
    assert t.homes == [extra]
    f.close()
    assert update.main(["--dir", str(tmp_path), "go-live"]) == 0
    g = Frontier(tmp_path / "frontier.db")
    try:
        assert json.loads(g.meta(crawler_mod.UPDATE_SITES_KEY)) == [R1, "sso.nknu.edu.tw/recruit"]
    finally:
        g.close()


def test_sites_narrow_the_night_to_the_run_that_follows(ledger, capsys):
    f, _ids, root = ledger
    third = "c.nknu.edu.tw/x"
    _site(f, third, "1")
    home3 = _url(f, "/x/", site=third, depth=0, found_via="seed")
    f.seed_src(f"https://{third}/x/", "site_home")
    assert home3 in update.select(f, root, today=QUIET).homes
    assert update.select(f, root, sites={third}, today=QUIET).ids() == [home3]
    f.close()
    assert update.main(["--dir", str(root), "plan", "--sites", third]) == 0
    assert "共 1 个网址" in capsys.readouterr().out


def test_a_home_that_moves_twice_is_read_where_it_lands(tmp_path):
    f = _tiny(tmp_path, R1)
    home = _url(f, "/", depth=0, found_via="seed", status=302, final_url=f"https://{R1}/a/")
    f.seed_src(f"https://{R1}/", "site_home")
    a = _url(f, "/a/", found_via="redirect", final_url=f"https://{R1}/b/")
    f.x("UPDATE url SET flags='js_redirect' WHERE id=?", (a,))
    b = _url(f, "/b/", found_via="js_redirect")
    assert update.select(f, tmp_path, today=QUIET).homes == [home, a, b]
    f.close()


def test_a_mistyped_dir_does_nothing(tmp_path, capsys):
    gone = tmp_path / "no-such-archive"
    for action in ("plan", "put-back", "go-live"):
        assert update.main(["--dir", str(gone), action]) == 1
    assert not gone.exists()
    assert "frontier.db" in capsys.readouterr().err


def test_put_back_waits_for_go_live(ledger, capsys):
    """Without list_row filled every row is news and every look walks its list whole: put-back refuses."""
    f, _ids, root = ledger
    f.close()
    assert update.main(["--dir", str(root), "put-back", "--today", QUIET.isoformat()]) == 1
    g = Frontier(root / "frontier.db")
    try:
        assert not g.q("SELECT 1 FROM url WHERE state='pending'")
    finally:
        g.close()
    assert "go-live" in capsys.readouterr().err


def test_the_plan_counts_what_put_back_will_do(ledger, capsys):
    """A look no answer can end (its page not pending) is closed by put-back first: the plan counts its list as
    taken, not as a look still open; a list whose page waits is counted with that reason."""
    f, ids, root = ledger
    f.x("INSERT INTO list_look(list_id, mode, started, saved_walk, saved_res) VALUES (?, 'look', 1, '[]', '[]')",
        (ids["list"],))
    f.x("UPDATE url SET state='pending' WHERE id=?", (ids["g1"],))
    f.close()
    assert update.main(["--dir", str(root), "plan"]) == 0
    out = capsys.readouterr().out
    assert "按按钮翻页的列表 1 个" in out and "第 1 页还在等着抓 1" in out
