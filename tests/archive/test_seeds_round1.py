"""Round 1's seeds (round-1 design §5 ①–⑨; review 10-26 B3, B7, B9): the list's sites, extra pages and login re-checks;
every row of the old archive's url table and the targets of its link table, judged again by today's rules through
round1.admit(); Common Crawl; SiteMap.aspx and the WordPress sitemaps; v1's announcements (honour notices too); the SSO
announcement centre's lists (榮譽典範 and all categories too); round two's starting points, each batch with its reason
and leaf; the old hosts' Crawl-delay and robots.txt; and the check that no site starts with fewer rows than the old
archive had, less what today's rules leave out (exit code 2, with the list). The old archive is a small one made here,
opened read-only and never changed."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nknu.archive import crawler as mod
from nknu.archive import scope, seeds
from tests.archive.plans import plan, site
from tests.archive.test_archive import Clock

OLD_SCHEMA = """
CREATE TABLE url(
  id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL, site TEXT NOT NULL, unit TEXT,
  tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
  found_via TEXT NOT NULL, found_from INTEGER,
  state TEXT NOT NULL DEFAULT 'pending', reason TEXT, next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  status INTEGER, final_url TEXT, ctype TEXT, bytes INTEGER, sha256 TEXT, warc TEXT, title TEXT, flags TEXT,
  fetched_at TEXT, elapsed_ms INTEGER, n_links INTEGER, n_postbacks INTEGER, directory INTEGER NOT NULL DEFAULT 0);
CREATE TABLE link(src INTEGER NOT NULL, dst TEXT NOT NULL, text TEXT, verdict TEXT NOT NULL,
  PRIMARY KEY(src, dst)) WITHOUT ROWID;
CREATE TABLE host(host TEXT PRIMARY KEY, queue TEXT NOT NULL, ip TEXT, cname TEXT,
  robots_state TEXT, robots_at REAL, robots_body TEXT, crawl_delay REAL,
  probe_status INTEGER, probe_sha TEXT, probe_len INTEGER, probe_title TEXT,
  cooldown_until REAL NOT NULL DEFAULT 0, cooldown_s REAL NOT NULL DEFAULT 0, robots_fail INTEGER NOT NULL DEFAULT 0,
  robots_sha TEXT, dns_fail INTEGER NOT NULL DEFAULT 0, dns_at REAL NOT NULL DEFAULT 0,
  not_before REAL NOT NULL DEFAULT 0);
CREATE TABLE request(id INTEGER PRIMARY KEY, queue TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL,
  purpose TEXT NOT NULL, started REAL NOT NULL, ended REAL NOT NULL, status INTEGER, error TEXT,
  scope TEXT, robots_sha TEXT);
"""
ACA_HOME = "https://c.nknu.edu.tw/aca/default.aspx"
NEWS1 = "https://c.nknu.edu.tw/aca/news.aspx?id=1"
WS9 = "https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx?f=9"
WS8 = "https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx?f=8"
AE = "https://c.nknu.edu.tw/ae/x.aspx"
ADMIN = "https://c.nknu.edu.tw/aca/pintech_admin/index.aspx"
DEEP = "https://c.nknu.edu.tw/aca/deep.aspx"
HONOUR = "https://c.nknu.edu.tw/aca/honor.aspx?id=3"
# url rows: (id, url, site, kind, depth, found_via, found_from, state, reason, attempts, next_at)
OLD_URLS = [
    (1, ACA_HOME, "c.nknu.edu.tw/aca", "page", 0, "seed", None, "done", None, 0, 0),
    (2, NEWS1, "c.nknu.edu.tw/aca", "page", 1, "link", 1, "failed", "http_500", 3, 0),
    (3, WS9, "sso.nknu.edu.tw/webservices", "file", 2, "link", 2, "done", None, 0, 0),
    (4, AE, "c.nknu.edu.tw/ae", "page", 1, "link", 1, "pending", "host_cooldown", 1, 9e9),
    (5, WS8, "sso.nknu.edu.tw/webservices", "file", 2, "link", 4, "done", None, 0, 0),
    (6, ADMIN, "c.nknu.edu.tw/aca", "page", 1, "link", 1, "done", None, 0, 0),
    (7, DEEP, "c.nknu.edu.tw/aca", "page", 20, "link", 2, "skipped", "depth", 0, 0),
]
OLD_LINKS = [
    (2, HONOUR, "賀！本系陳O丙同學榮獲全國競賽第一名", "skipped:honor"),
    (2, "https://c.nknu.edu.tw/aca/uploadfile/pic.jpg", "", "queued"),
    (1, "https://sso.nknu.edu.tw/", "登入", "out:login"),
    (1, NEWS1, "消息", "queued"),
    (1, "https://forms.gle/AbCdEf123", "報名", "out:never_host"),
]
ROBOTS = "User-agent: *\nCrawl-delay: 20\n"


def old_archive(tmp_path: Path, extra_urls=()) -> Path:
    root = tmp_path / "archive-2026-10-24"
    root.mkdir()
    conn = sqlite3.connect(root / "frontier.db")
    conn.executescript(OLD_SCHEMA)
    for i, url, s, kind, depth, via, src, state, reason, attempts, next_at in (*OLD_URLS, *extra_urls):
        conn.execute("INSERT INTO url(id, url, path, host, site, tier, kind, depth, found_via, found_from, state, reason, "
                     "attempts, next_at) VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                     (i, url, url.split("?")[0], scope.urlsplit(url).hostname, s, kind, depth, via, src, state, reason,
                      attempts, next_at))
    conn.executemany("INSERT INTO link VALUES (?,?,?,?)", OLD_LINKS)
    conn.execute("INSERT INTO host(host, queue, robots_state, robots_at, robots_body, crawl_delay, robots_sha) "
                 "VALUES ('fine-arts.nknu.edu.tw', 'ip:140.127.5.5', 'rules', 1.0, ?, 20.0, 'abc')", (ROBOTS,))
    conn.execute("INSERT INTO host(host, queue, robots_state) VALUES ('c.nknu.edu.tw', 'ip:140.127.62.110', 'none')")
    conn.execute("INSERT INTO request(queue, host, url, purpose, started, ended, status) VALUES "
                 "('ip:1', 'c.nknu.edu.tw', 'https://c.nknu.edu.tw/robots.txt', 'robots', 1, 2, 404)")
    conn.execute("INSERT INTO request(queue, host, url, purpose, started, ended, status, error) VALUES "
                 "('ip:2', 'dead.nknu.edu.tw', 'https://dead.nknu.edu.tw/', 'page', 1, 2, NULL, 'timed out')")
    conn.commit()
    conn.close()
    return root


def facts(tmp_path: Path) -> Path:
    db = tmp_path / "facts.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE doc(url TEXT, unit TEXT, title TEXT, category TEXT, withdrawn_at TEXT)")
    conn.executemany("INSERT INTO doc VALUES (?,?,?,?,NULL)", [
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=1", "電子系", "【狂賀】電子系陳O丙同學榮獲獎學金", "活動快報"),
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=2", "秘書室", "校慶活動", "榮譽典範"),
        ("https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=3", "教務處", "115學年度停修申請", "行政資訊"),
    ])
    conn.commit()
    conn.close()
    return db


def cc(tmp_path: Path) -> Path:
    d = tmp_path / "commoncrawl"
    d.mkdir()
    lines = []
    for url, status in ((f"{ACA_HOME}", "200"), ("https://c.nknu.edu.tw/aca/cc_only.aspx", "200"),
                        ("https://c.nknu.edu.tw/aca/gone.aspx", "404"), ("https://c.nknu.edu.tw/ae/cc.aspx", "200"),
                        ("https://sso.nknu.edu.tw/", "302"), ("https://c.nknu.edu.tw/aca/err.aspx", "503"),
                        ("https://c.nknu.edu.tw/aca/denied.aspx", "403")):
        lines.append("tw,edu,nknu,c)/x 20260101000000 " + json.dumps({"url": url, "status": status, "mime": "text/html"}))
    (d / "CC-MAIN-2026-04.cdx").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def probe(tmp_path: Path) -> Path:
    p = tmp_path / "probe.json"
    p.write_text(json.dumps({"sites": [
        {"n": "教務處行事曆", "k": "校务系统", "c": "direct", "u": "https://c.nknu.edu.tw/aca/calendar.aspx"},
        {"n": "成教系系统", "k": "校务系统", "c": "render", "u": "https://c.nknu.edu.tw/ae/sys.aspx"},
        {"n": "要登录的", "k": "校务系统", "c": "login", "u": "https://c.nknu.edu.tw/aca/x.aspx"},
        {"n": "单位", "k": "行政单位", "c": "direct", "u": "https://c.nknu.edu.tw/aca/unit.aspx"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return p


PLAN = plan(
    site("c.nknu.edu.tw/aca", imp="全校重要", name="教务处", home=ACA_HOME, platform="c.nknu"),
    site("tecs.nknu.edu.tw", name="师培处", platform="c.nknu（同一套系统，独立网域）"),
    site("ksped.nknu.edu.tw", name="特教中心", paths=("/resource/",), home="https://ksped.nknu.edu.tw/resource/",
         platform="自建"),
    site("fine-arts.nknu.edu.tw", name="美术系", platform="WordPress"),
    site("sso.nknu.edu.tw/news", imp="全校重要", name="SSO 公告中心",
         home="https://sso.nknu.edu.tw/News/SubjectList.aspx"),
    site("sso.nknu.edu.tw/webservices", rnd="resource"),
    site("c.nknu.edu.tw/ae", rnd="2", why="other_site", home="https://c.nknu.edu.tw/ae/"),
    site("w3.nknu.edu.tw", rnd="2", why="js_site"),
    site("nknulib.nknu.edu.tw", rnd="2", why="other_site"),
    site("github.com", rnd="2", why="offsite_unit", paths=("/gdg-on-campus-nknu",),
         home="https://github.com/GDG-on-Campus-NKNU", seeds=("https://github.com/GDG-on-Campus-NKNU",)),
    site("www.ted.com", rnd="2", why="offsite_unit", leaf=1, paths=("/tedx/events/53158", "/tedx/events/56115"),
         home="https://www.ted.com/tedx/events/53158",
         seeds=("https://www.ted.com/tedx/events/53158", "https://www.ted.com/tedx/events/56115")),
    extra=("https://sso.nknu.edu.tw/studentprofile/stud_mails.aspx",),
    login_checks=("https://c.nknu.edu.tw/aca/admin_login.aspx",),
    round2_pages=(("https://www.openstreetmap.org/way/179222556", "record4"), ("https://www.nknush.kh.edu.tw/", "claude")),
)


def crawler_for(root: Path) -> mod.Crawler:
    clock = Clock()
    return mod.Crawler(root, plan=PLAN, clock=clock, sleep=clock.sleep, campus_url=None,
                       resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True)


def run_seed(tmp_path: Path, **kw):
    old = kw.pop("old", None) or old_archive(tmp_path)
    c = crawler_for(tmp_path / "archive")
    report = seeds.seed_round1(c, old=old, facts_db=kw.pop("facts_db", facts(tmp_path)),
                               cc_dir=kw.pop("cc_dir", cc(tmp_path)), probe=kw.pop("probe", probe(tmp_path)))
    return c, report


def rows(c):
    return {r["url"]: r for r in c.frontier.q("SELECT * FROM url")}


def sources(c, url):
    return {r[0] for r in c.frontier.q("SELECT source FROM seed_src WHERE url=?", (url,))}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- ① the list, ⑤ ⑥ sitemaps, ⑦ v1, ⑧ SSO lists


def test_the_lists_sites_extra_pages_and_login_checks_are_seeds(tmp_path):
    c, _ = run_seed(tmp_path)
    r = rows(c)
    home = r[ACA_HOME]
    assert (home["state"], home["found_via"], home["tier"], home["owner_site"], home["prio"]) == \
        ("pending", "seed", 0, "c.nknu.edu.tw/aca", 0)
    assert {"site_home", "old_url", "commoncrawl"} <= sources(c, ACA_HOME)  # a URL may have several sources
    extra = r["https://sso.nknu.edu.tw/studentprofile/stud_mails.aspx"]
    assert (extra["state"], extra["tier"]) == ("pending", 1)
    check = r["https://c.nknu.edu.tw/aca/admin_login.aspx"]
    assert (check["kind0"], check["state"], check["prio"]) == ("login_check", "pending", 0)


def test_sitemap_pages_for_the_c_nknu_template_and_wordpress_sitemaps(tmp_path):
    c, _ = run_seed(tmp_path)
    r = rows(c)
    for url in ("https://c.nknu.edu.tw/aca/sitemap.aspx", "https://tecs.nknu.edu.tw/sitemap.aspx",
                "https://ksped.nknu.edu.tw/resource/sitemap.aspx"):
        assert r[url]["state"] == "pending" and "sitemap_aspx" in sources(c, url), url
    wp = r["https://fine-arts.nknu.edu.tw/wp-sitemap.xml"]
    assert (wp["kind0"], wp["prio"]) == ("sitemap", 0) and "wp_sitemap" in sources(c, wp["url"])


def test_v1s_announcements_honour_notices_included_and_ssos_lists_with_honours_and_all(tmp_path):
    assert any("cat=GOOD" in u for u in seeds.SSO_LISTS)
    assert "https://sso.nknu.edu.tw/News/SubjectList.aspx" in seeds.SSO_LISTS
    c, _ = run_seed(tmp_path)
    r = rows(c)
    v1 = [u for u in r if "v1_known" in sources(c, u)]
    assert len(v1) == 3 and all(r[u]["found_via"] == "v1_known" for u in v1)  # 榮譽典範 too (record 三·3)
    for u in seeds.SSO_LISTS:
        assert "sso_lists" in sources(c, scope.canonical(u))


# ---------------------------------------------------------------- ② ③ the old archive, judged again (B7)


def test_every_old_row_is_judged_again_and_starts_afresh(tmp_path):
    c, report = run_seed(tmp_path)
    r = rows(c)
    news = r[NEWS1]
    assert (news["state"], news["attempts"], news["reason"], news["next_at"], news["old_ref"], news["found_via"],
            news["depth"]) == ("pending", 0, None, 0, 2, "old_frontier", 1)
    assert news["found_from"] == r[ACA_HOME]["id"]
    ws9 = r[WS9]  # SSO's attachment service, found from 教務處's page: 教務處's
    assert (ws9["state"], ws9["owner_site"], ws9["tier"], ws9["leaf"], ws9["kind0"]) == \
        ("pending", "c.nknu.edu.tw/aca", 0, 1, "file")
    ws8 = r[WS8]  # found from a round-two page: round two's
    assert (ws8["state"], ws8["reason"]) == ("later", "round2:other_site")
    ae = r[AE]
    assert (ae["state"], ae["reason"], ae["next_at"], ae["attempts"]) == ("later", "round2:other_site", 0, 0)
    assert r[DEEP]["depth"] == mod.MAX_DEPTH and r[DEEP]["state"] == "pending"
    assert ADMIN not in r and any(b.url == ADMIN and b.reason == "out:login" for b in report.blocked)


def test_the_old_link_tables_targets_come_in_one_step_back_and_pictures_are_only_counted(tmp_path):
    c, report = run_seed(tmp_path)
    r = rows(c)
    honour = r[HONOUR]  # the 10-24 crawler skipped honour notices; round 1 fetches them (record 三·3)
    assert (honour["state"], honour["prio"], honour["found_from"]) == ("pending", 2, r[NEWS1]["id"])
    assert "old_link" in sources(c, HONOUR)
    assert "https://c.nknu.edu.tw/aca/uploadfile/pic.jpg" not in r and report.images_not_imported == 1
    assert "https://sso.nknu.edu.tw/" not in r
    short = r["https://forms.gle/AbCdEf123"]  # never_host in 10-24, a short link of 教務處's page now (admit)
    assert (short["kind0"], short["owner_site"], short["state"]) == ("shortlink", "c.nknu.edu.tw/aca", "pending")


def test_common_crawl_brings_its_answered_addresses_and_counts_the_404s(tmp_path):
    c, report = run_seed(tmp_path)
    r = rows(c)
    cc_only = r["https://c.nknu.edu.tw/aca/cc_only.aspx"]
    assert (cc_only["found_via"], cc_only["prio"], cc_only["state"]) == ("commoncrawl", 2, "pending")
    assert "https://c.nknu.edu.tw/aca/err.aspx" in r  # a 5xx then: it may be there now
    assert "https://c.nknu.edu.tw/aca/gone.aspx" not in r and report.cc_404 == 1
    assert "https://c.nknu.edu.tw/aca/denied.aspx" not in r
    assert r["https://c.nknu.edu.tw/ae/cc.aspx"]["state"] == "later"


# ---------------------------------------------------------------- ⑨ round two's starting points


def test_round_twos_starting_points_with_their_batch_reason_and_leaf(tmp_path):
    c, report = run_seed(tmp_path)
    r = rows(c)
    w3 = r["https://w3.nknu.edu.tw/"]
    assert (w3["state"], w3["reason"]) == ("later", "round2:js_site") and "round2:js_site" in sources(c, w3["url"])
    gdg = r["https://github.com/GDG-on-Campus-NKNU"]  # not an IIS host: its spelling is kept
    assert (gdg["reason"], gdg["leaf"]) == ("round2:offsite_unit", 0) and "round2:offsite_unit" in sources(c, gdg["url"])
    for u in ("https://www.ted.com/tedx/events/53158", "https://www.ted.com/tedx/events/56115"):
        assert (r[u]["reason"], r[u]["leaf"]) == ("round2:offsite_unit", 1)
    osm = r["https://www.openstreetmap.org/way/179222556"]
    assert (osm["reason"], osm["leaf"]) == ("round2:offsite", 1) and sources(c, osm["url"]) == {"record4"}
    assert sources(c, "https://www.nknush.kh.edu.tw/") == {"claude"}
    assert "https://nknulib.nknu.edu.tw/" not in r  # a system (record 三·19): no row, reported
    assert any(b.url == "https://nknulib.nknu.edu.tw/" and b.reason == "out:system" for b in report.blocked)
    sysr = r["https://c.nknu.edu.tw/aca/calendar.aspx"]  # a 校务系统 seed inside a round-1 site: this round's
    assert sysr["state"] == "pending" and "systems" in sources(c, sysr["url"])
    assert r["https://c.nknu.edu.tw/ae/sys.aspx"]["state"] == "later"
    assert "https://c.nknu.edu.tw/aca/x.aspx" not in r and "https://c.nknu.edu.tw/aca/unit.aspx" not in r


# ---------------------------------------------------------------- B3: the old hosts' Crawl-delay and robots.txt


def test_the_old_hosts_crawl_delay_robots_txt_and_answers_come_along(tmp_path):
    c, _ = run_seed(tmp_path)
    fa = c.frontier.host("fine-arts.nknu.edu.tw")
    assert (fa["crawl_delay"], fa["robots_state"], fa["robots_body"]) == (20.0, "rules", ROBOTS)
    assert fa["queue"] == "?" or fa["queue"].startswith("ip:")  # its address is looked up again, never the old one
    assert c.frontier.host("c.nknu.edu.tw")["answered_off"] == 1  # it answered the 10-24 crawl from off campus
    assert c.frontier.host("dead.nknu.edu.tw") is None or c.frontier.host("dead.nknu.edu.tw")["answered_off"] == 0


def test_a_seeded_host_is_looked_up_again_before_it_is_fetched(tmp_path):
    c, _ = run_seed(tmp_path)
    c.frontier.set_host("fine-arts.nknu.edu.tw", queue="?")
    assert c.queue_of("fine-arts.nknu.edu.tw") == "ip:fine-arts.nknu.edu.tw"


# ---------------------------------------------------------------- the check, the report, the archive's facts


def test_no_site_starts_with_fewer_rows_than_the_old_archive_had(tmp_path):
    _c, report = run_seed(tmp_path)
    assert report.missing == [] and report.exit_code == 0
    by = report.sites["c.nknu.edu.tw/aca"]
    assert (by["old"], by["blocked"], by["seeded"]) == (4, 1, 3)


def test_a_site_that_starts_with_fewer_rows_is_exit_code_2_with_the_list(tmp_path, monkeypatch):
    real = mod.Crawler._enqueue
    lost = "https://c.nknu.edu.tw/aca/news.aspx?id=1"

    def drop(self, href, **kw):  # a seed path that loses a row: the check must notice
        if kw.get("found_via") == "old_frontier" and href == lost:
            return mod.Enqueued("dup", None, None, None)
        return real(self, href, **kw)

    monkeypatch.setattr(mod.Crawler, "_enqueue", drop)
    _c, report = run_seed(tmp_path)
    assert report.exit_code == 2 and [m.url for m in report.missing] == [lost]


def test_the_report_and_the_archives_facts(tmp_path):
    c, report = run_seed(tmp_path)
    text = report.text()
    assert "pintech_admin" in text and "后台" in text  # back offices the new rules keep out, listed one by one
    assert "nknulib.nknu.edu.tw" in text and "c.nknu.edu.tw/aca" in text
    assert c.frontier.meta("plan_sha256") == PLAN.sha256 and c.frontier.meta("round") == "1"
    assert c.frontier.meta("old_archive").endswith("archive-2026-10-24") and c.frontier.meta("seeded_at")
    plan_rows = {r["site"]: r for r in c.frontier.q("SELECT * FROM site_plan")}
    assert plan_rows["c.nknu.edu.tw/aca"]["base_urls"] == 4 and plan_rows["www.ted.com"]["leaf"] == 1
    assert json.loads(plan_rows["ksped.nknu.edu.tw"]["paths"]) == ["/resource/"]


def test_the_old_archive_is_read_only_and_never_changed(tmp_path):
    old = old_archive(tmp_path)
    before = {p.name: sha(p) for p in old.iterdir()}
    run_seed(tmp_path, old=old)
    assert {p.name: sha(p) for p in old.iterdir()} == before


def test_seeding_twice_adds_nothing_twice(tmp_path):
    old = old_archive(tmp_path)
    c, _ = run_seed(tmp_path, old=old)
    n = c.frontier.q("SELECT count(*) FROM url")[0][0]
    report = seeds.seed_round1(c, old=old, facts_db=None, cc_dir=None, probe=None)
    assert c.frontier.q("SELECT count(*) FROM url")[0][0] == n and report.exit_code == 0


# ---------------------------------------------------------------- the command (B9, §6.9)


def cli(*args):
    from nknu.admin.cli import app

    return CliRunner().invoke(app, ["archive", *args])


def test_the_seed_command_writes_a_report_and_says_the_exit_code(tmp_path, monkeypatch):
    old = old_archive(tmp_path)
    monkeypatch.setattr(seeds, "PROBE", probe(tmp_path))
    monkeypatch.setattr(mod, "resolve", lambda h: (f"ip:{h}", None, h))
    monkeypatch.setattr("nknu.archive.round1.load", lambda path=None: PLAN)
    out = cli("seed", "--round", "1", "--old", str(old), "--dir", str(tmp_path / "new"), "--cc", str(cc(tmp_path)))
    assert out.exit_code == 0, out.output
    assert list((tmp_path / "new" / "reports").glob("seed-*.md"))


def test_the_seed_command_refuses_an_old_archive_as_its_own_directory_and_a_round_it_does_not_know(tmp_path):
    old = old_archive(tmp_path)
    out = cli("seed", "--round", "1", "--old", str(old), "--dir", str(old))
    assert out.exit_code == 1 and "10-24" in out.output
    out = cli("seed", "--round", "2", "--old", str(old), "--dir", str(tmp_path / "new"))
    assert out.exit_code == 1


def test_the_seed_and_retry_commands_refuse_while_a_run_holds_the_directory(tmp_path):
    old = old_archive(tmp_path)
    root = tmp_path / "new"
    lock = mod.hold_run_lock(root)
    try:
        out = cli("seed", "--round", "1", "--old", str(old), "--dir", str(root))
        assert out.exit_code == 1 and "stop" in out.output
        from nknu.archive.frontier import Frontier

        Frontier(root / "frontier.db").close()
        out = cli("retry", "http_5", "--dir", str(root))
        assert out.exit_code == 1 and "stop" in out.output
    finally:
        lock.close()


@pytest.mark.parametrize("old_is_file", [True, False])
def test_old_is_the_archive_directory_or_its_frontier_db(tmp_path, old_is_file):
    old = old_archive(tmp_path)
    c = crawler_for(tmp_path / "archive")
    report = seeds.seed_round1(c, old=old / "frontier.db" if old_is_file else old, facts_db=None, cc_dir=None,
                               probe=None)
    assert report.sites["c.nknu.edu.tw/aca"]["old"] == 4
