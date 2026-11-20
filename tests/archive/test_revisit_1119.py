"""11-19: the off-campus unit sites of round two's batch were crawled once and never read again. The list marks them
(round1.Site.revisit: weekly, 性别所's old Wix site monthly); on the night update.REVISIT_WEEKDAY names, the nightly
put-back also takes each one's home and the pages its home links (one layer: a new post a home links is a new row, and
is fetched as any new row), the same run fetches them, the same review judges them (a site's alarm, a deletion seen
twice), and the night exports those that changed. By hand: `update revisit-back`. A ledger built by hand, made-up
addresses."""

from __future__ import annotations

import json
from datetime import date

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import round1, update, watch
from nknu.archive.frontier import Frontier
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site

R1 = "a.nknu.edu.tw"
W, M, V = "w.example.org", "m.example.org/blog", "v.example.org"
SAT = date(2026, 11, 21)  # the weekly night
FRI = date(2026, 11, 20)
FIRST_SAT = date(2026, 12, 5)  # the month's first: the monthly sites too
PLAN = make_plan(site(R1, home=f"https://{R1}/"),
                 site(W, rnd="2", why="offsite_unit", now="2026-11-17", revisit="weekly", seeds=(f"https://{W}/",)),
                 site(M, rnd="2", why="offsite_unit", now="2026-11-17", revisit="monthly", home=f"https://{M}",
                      seeds=(f"https://{M}",)),
                 site(V, rnd="2", why="offsite_unit", now="2026-11-17", revisit="weekly", seeds=(f"https://{V}/",)))


def _url(f: Frontier, url: str, owner: str, *, state: str = "done", status: int = 200, kind: str = "page",
         depth: int = 1, final_url: str | None = None, reason: str | None = None) -> int:
    host = url.split("/")[2]
    f.set_host(host, queue=f"ip:{host}")
    f.add(url, host=host, site=owner, unit=None, tier=1, kind=kind, depth=depth, found_via="link", found_from=None,
          owner_site=owner)
    uid = f.row(url)["id"]
    if state in ("done", "failed"):
        f.finish(uid, state=state, status=status, kind=kind, final_url=final_url, warc="warc/q.warc.gz",
                 warc_offset=uid * 10)
    else:
        f.x("UPDATE url SET state=?, reason=? WHERE id=?", (state, reason, uid))
    return uid


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_plan", lambda: PLAN)
    f = Frontier(tmp_path / "frontier.db")
    f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (R1, R1))
    f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([R1]))
    ids = {"r1": _url(f, f"https://{R1}/", R1, depth=0)}
    f.seed_src(f"https://{R1}/", "site_home")
    ids["w"] = _url(f, f"https://{W}/", W, depth=0)
    f.seed_src(f"https://{W}/", "round2:offsite_unit")
    for name, path, more in (("news", "/news", {}), ("about", "/about", {}), ("lost", "/lost", {"state": "failed"}),
                             ("pdf", "/files/a.pdf", {"kind": "file"}),
                             ("tags", "/tags", {"state": "skipped", "reason": "out:skip_path"})):
        ids[name] = _url(f, f"https://{W}{path}", W, **more)
        f.link(ids["w"], f"https://{W}{path}", name, "r1")
    ids["deep"] = _url(f, f"https://{W}/news/1", W, depth=2)
    f.link(ids["news"], f"https://{W}/news/1", "一則", "r1")
    ids["other"] = _url(f, "https://elsewhere.example.net/x", "elsewhere.example.net")
    f.link(ids["w"], "https://elsewhere.example.net/x", "友站", "round2")
    ids["m"] = _url(f, f"https://{M}", M, depth=0)
    f.seed_src(f"https://{M}", "round2:offsite_unit")
    ids["m1"] = _url(f, f"https://{M}/post-1", M)
    f.link(ids["m"], f"https://{M}/post-1", "文章", "r1")
    # a way in that is a sitemap a person gave the site: the sitemaps it names, the pages those name
    ids["map"] = _url(f, f"https://{M}/sitemap.xml", M, kind="file", depth=0)
    f.seed_src(f"https://{M}/sitemap.xml", "sitemap 11-17")
    ids["posts"] = _url(f, f"https://{M}/posts-sitemap.xml", M, kind="file", depth=0)
    f.link(ids["map"], f"https://{M}/posts-sitemap.xml", "", "r1")
    ids["m9"] = _url(f, f"https://{M}/post-9", M)
    f.link(ids["posts"], f"https://{M}/post-9", "", "r1")
    ids["mpdf"] = _url(f, f"https://{M}/files/b.pdf", M, kind="file")
    f.link(ids["posts"], f"https://{M}/files/b.pdf", "", "r1")
    ids["v"] = _url(f, f"https://{V}/", V, depth=1, status=302, final_url=f"https://{V}/home")  # a home that moves
    ids["vh"] = _url(f, f"https://{V}/home", V, depth=2)
    ids["v1"] = _url(f, f"https://{V}/p1", V, depth=3)
    f.link(ids["vh"], f"https://{V}/p1", "一頁", "r1")
    yield f, ids, tmp_path
    f.close()


def _weekly(ids) -> list[int]:
    return sorted(ids[k] for k in ("w", "news", "about", "lost", "v", "vh", "v1"))


def test_the_weekly_night_is_the_one_that_starts_on_a_saturday():
    assert update.REVISIT_WEEKDAY == 5 == SAT.weekday()


def test_on_the_weekly_night_a_site_s_home_and_the_pages_it_links_are_taken(ledger):
    f, ids, root = ledger
    t = update.select(f, root, today=SAT, plan=PLAN)
    assert sorted(t.revisit) == _weekly(ids)  # no file, no page a rule left, no page two links away, no other site's
    assert t.revisit_sites == [V, W]
    assert t.homes == [ids["r1"]]  # the update site's own batches as before
    assert len(set(t.ids())) == len(t.ids()) and set(t.revisit) <= set(t.ids())


def test_on_another_night_no_such_site_is_taken(ledger):
    f, ids, root = ledger
    t = update.select(f, root, today=FRI, plan=PLAN)
    assert (t.revisit, t.revisit_sites, t.homes) == ([], [], [ids["r1"]])
    assert update.select(f, root, today=SAT).revisit == []  # no list at hand: the night as it was


def test_a_monthly_site_comes_on_the_month_s_first_weekly_night(ledger):
    f, ids, root = ledger
    t = update.select(f, root, today=FIRST_SAT, plan=PLAN)
    assert t.revisit_sites == [M, V, W]
    assert sorted(set(t.revisit) - set(_weekly(ids))) == sorted(ids[k] for k in ("m", "m1", "map", "posts", "m9"))
    assert update.revisit_due(PLAN, date(2026, 12, 12)) == {V, W}


def test_a_site_an_alarm_holds_comes_back_the_next_night(ledger):
    f, ids, root = ledger
    own = _url(f, f"https://{R1}/files/a.pdf", R1, kind="file")
    f.meta(watch.HELD_KEY, json.dumps({"since": 1.0, "ids": [ids["news"], ids["deep"], own]}))
    t = update.select(f, root, today=FRI, plan=PLAN)
    assert t.revisit_sites == [W]
    assert sorted(t.revisit) == sorted(ids[k] for k in ("w", "news", "about", "lost", "deep"))  # its held rows too
    assert t.again == [own]  # an update site's held rows: as before, and no revisit site's among them


def test_a_revisit_site_is_never_read_as_an_update_site(ledger):
    f, _ids, root = ledger
    f.seed_src(f"https://{W}/", "site_home")
    t = update.select(f, root, sites={W}, today=FRI, plan=PLAN)  # named, on a night it is not due
    assert not t.ids()
    t = update.select(f, root, sites={W}, today=SAT, plan=PLAN)
    assert t.revisit_sites == [W] and not t.homes and not t.standing


def test_put_back_takes_them_and_the_run_is_given_their_keys(ledger, capsys):
    f, ids, root = ledger
    f.close()
    assert update.main(["--dir", str(root), "put-back", "--today", SAT.isoformat()]) == 0
    assert "校外" in capsys.readouterr().out
    g = Frontier(root / "frontier.db", readonly=True)
    try:
        got = {r["id"]: (r["state"], r["reason"]) for r in g.q("SELECT id, state, reason FROM url")}
        assert all(got[i] == ("pending", "update_revisit") for i in _weekly(ids))
        assert got[ids["deep"]][0] == "done" and got[ids["m"]][0] == "done" and got[ids["pdf"]][0] == "done"
        assert json.loads(g.meta(update.REVISIT_KEY)) == [V, W]
        assert json.loads(g.meta(update.PUT_KEY))["revisit"] == len(_weekly(ids))
    finally:
        g.close()
    assert update.main(["--dir", str(root), "sites"]) == 0
    assert capsys.readouterr().out.strip() == f"{R1},{V},{W}"
    assert update.main(["--dir", str(root), "put-back", "--today", FRI.isoformat()]) == 0  # the next night: none
    capsys.readouterr()
    assert update.main(["--dir", str(root), "sites"]) == 0
    assert capsys.readouterr().out.strip() == R1


def test_a_list_that_cannot_be_read_leaves_the_night_as_it_was(ledger, monkeypatch, capsys):
    f, ids, root = ledger
    f.close()

    def broken():
        raise ValueError("no list")

    monkeypatch.setattr(update, "_plan", broken)
    assert update.main(["--dir", str(root), "put-back", "--today", SAT.isoformat()]) == 0
    said = capsys.readouterr()
    assert "名单" in said.err
    g = Frontier(root / "frontier.db", readonly=True)
    try:
        assert g.row_by_id(ids["r1"])["state"] == "pending" and g.row_by_id(ids["w"])["state"] == "done"
        assert json.loads(g.meta(update.REVISIT_KEY)) == []
    finally:
        g.close()


def test_the_night_s_export_takes_a_revisit_site_that_changed_and_no_other_outside_site(ledger):
    f, _ids, root = ledger
    f.close()
    assert update._night_sites(root) == {R1, W, M, V}
    status = {"sites_changed": [W, R1, "elsewhere.example.net"], "alarms": {}}
    assert update.export_list(status, [M], update._night_sites(root)) == sorted([R1, W, M])
    update.owe(root, [W, "elsewhere.example.net"])
    assert update._owed(root) == [W]


def test_by_hand_the_rows_go_back_without_a_night_starting(ledger, capsys):
    f, ids, root = ledger
    f.meta(update.NIGHT_KEY, "5.0")
    f.close()
    assert update.main(["--dir", str(root), "revisit-back", "--dry-run"]) == 0
    said = capsys.readouterr().out
    assert W in said and M in said and "12 页" in said  # every revisit site, whatever the day: 4 + 5 + 3 rows
    g = Frontier(root / "frontier.db", readonly=True)
    assert g.row_by_id(ids["w"])["state"] == "done"
    g.close()
    assert update.main(["--dir", str(root), "revisit-back", "--sites", W]) == 0
    assert W in capsys.readouterr().out
    g = Frontier(root / "frontier.db", readonly=True)
    try:
        assert g.row_by_id(ids["w"])["state"] == "pending" and g.row_by_id(ids["v"])["state"] == "done"
        assert g.row_by_id(ids["r1"])["state"] == "done"
        assert g.meta(update.NIGHT_KEY) == "5.0" and g.meta(update.REVISIT_KEY) is None  # no night of its own
    finally:
        g.close()
    assert update.main(["--dir", str(root), "revisit-back", "--sites", R1]) == 1  # not a revisit site: nothing done
    assert "不是" in capsys.readouterr().err


def test_seen_says_a_site_whose_night_it_was_not():
    status = {"night_started": "2026-11-20T01:00:00+08:00", "alarms": {},
              "sites_seen": {R1: "2026-11-19T17:30:00+00:00", W: "2026-11-13T17:40:00+00:00",
                             V: "2026-11-19T17:45:00+00:00"},
              "revisit_idle": [W]}
    rows = {s: (at, what) for s, at, what in update.seen_rows(status, {V: "ok"})}
    assert rows[W] == ("2026-11-14 01:40", update.SEEN_WORDS["idle"])  # its last answer, never 连不上
    assert rows[V][1] == update.SEEN_WORDS["ok"] and rows[R1][1] == update.SEEN_WORDS["same"]


def test_the_list_names_the_off_campus_unit_sites_weekly_and_the_old_wix_site_monthly():
    real = round1.load()
    got = update.revisit_sites(real)
    assert len(got) == 30 and got["nknugender.wixsite.com/nknugender"] == "monthly"
    assert sorted(set(got.values())) == ["monthly", "weekly"] and list(got.values()).count("monthly") == 1
    assert all(real.site(k).round == "2" and real.site(k).why == "offsite_unit" and real.site(k).now for k in got)
    assert not {"w3.nknu.edu.tw", "gauss.nknu.edu.tw"} & set(got)  # the JavaScript sites have their own nightly lap
    with pytest.raises(ValueError):
        round1.parse_site({"site": "x.nknu.edu.tw", "round": 1, "imp": "本系重要", "home": "https://x.nknu.edu.tw/",
                           "revisit": "weekly"})
    with pytest.raises(ValueError):
        round1.parse_site({"site": "x.example.org", "round": 2, "why": "offsite_unit", "home": "https://x.example.org/",
                           "now": "2026-11-17", "revisit": "daily"})
