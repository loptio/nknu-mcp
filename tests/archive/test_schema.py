"""The frontier's schema (round-1 design §12): every table and column the dashboard (§14), `archive verify` (§13) and
the result views (§10, outcome.py) read is in TABLES, the views compile against TABLES alone, and a frontier.db written
by the 10-24 version gains the new columns when it is opened (the monitor's read-only view still refuses it)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from nknu.archive import outcome
from nknu.archive.frontier import INDEXES, TABLES, Frontier, OutdatedFrontier

# what the dashboard, the verify checks and the views read (design §10, §12–14), per table
READ = {
    "url": {"id", "url", "path", "host", "site", "unit", "tier", "kind", "depth", "found_via", "found_from", "state",
            "reason", "next_at", "attempts", "status", "final_url", "ctype", "bytes", "sha256", "warc", "title",
            "flags", "fetched_at", "owner_site", "prio", "leaf", "kind0", "page_role", "page_type", "sniff", "text_chars",
            "filename", "size_declared", "warc_offset", "warc_len", "added_at", "old_ref"},
    "host": {"host", "queue", "ip", "crawl_delay", "cooldown_until", "not_before", "dns_fail", "scheme_only", "dns_ms",
             "answered_off"},
    # review 10-26 B4, B7: where its answer is archived and where it was made from
    "request": {"id", "queue", "host", "url", "purpose", "started", "ended", "status", "error", "method", "bytes",
                "new_conn", "connect_ms", "tls_ms", "ip", "attempt", "warc", "warc_offset", "campus"},
    "server": {"queue", "conn_fail", "failed_at", "answered_at", "err_fail"},  # review 10-26 B2; second review
    "postback": {"site", "target", "n", "sample", "argument", "text", "form_id", "decision"},
    "event": {"at", "level", "code", "host", "url", "message"},
    "pause": {"queue", "until", "why"},
    "link": {"src", "dst", "text", "verdict"},
    "site_plan": {"site", "name", "imp", "round", "why", "platform", "home", "paths", "params", "leaf", "base_urls"},
    "page_type": {"key", "site", "host", "path_tpl", "params", "verdict", "why", "method", "direct_chars",
                  "rendered_chars", "data_urls", "sample_id", "sample_url", "n_pages", "n_strong", "n_weak", "tries",
                  "source", "tested_at", "note"},
    "page_res": {"page_id", "res_key", "how", "role", "need", "why", "res_id", "action_id", "zone", "text"},
    "action": {"id", "list_id", "epoch", "step", "kind", "queue", "host", "site", "profile", "form_src_warc",
               "form_src_offset", "form_id", "target", "argument", "row_key", "row_text", "state", "reason", "status",
               "ctype", "bytes", "sha256", "sniff", "filename", "location", "warc", "warc_offset", "warc_len",
               "created", "done"},
    "listwalk": {"list_id", "site", "profile", "epoch", "pages", "rows", "rows_unique", "claimed", "ended_by", "ok",
                 "note", "at"},
    "drive_item": {"url_id", "file_id", "name", "mime", "size", "md5", "modified", "parent_id", "resource_key", "depth",
                   "album", "content_id"},
    "js_page": {"url_id", "page_type", "at", "verdict", "why", "final_url"},
    "reconcile_site": {"site", "batch", "at", "checks", "cover_cc", "cover_old", "gates_ok"},
    "id_probe": {"host", "page_type", "url", "status", "final_url", "sha256", "len", "title", "at"},
    "seed_src": {"url", "source"},
    "meta": {"key", "value"},
    # review 10-26: a person's school_side mark, which url_result compares (B7); the videos a page shows (record 五·15)
    "review_mark": {"url_id", "mark", "result", "reason", "status", "note", "at", "fetched_at"},
    "video": {"page_id", "video_id", "url", "title", "how", "zone"},
}
# what the views add to the rows they are built on
VIEW_ADDS = {
    "url_result": {"result", "type_verdict"},
    "action_result": {"result"},
    "page_resource": {"page_id", "res_key", "role", "need", "result", "res", "via", "marked"},
    "page_completeness": {"id", "site", "tier", "content_id", "page_type", "page_role", "own", "n_must", "n_school",
                          "n_wait", "n_unchecked", "n_round2", "n_bad", "n_failed", "n_click", "n_review", "miss_file",
                          "miss_img", "miss_other", "ended_by", "list_ok", "level", "label", "reasons"},
}
NEW_INDEXES = {"url_next2", "url_owner", "url_ptype", "url_added", "page_res_res", "page_res_action", "action_list",
               "action_click", "request_started", "url_sha", "url_owner_path_state"}

# the frontier.db of the 10-24 run (commit 8c8d23b): no column or table of the round-1 design
OLD_TABLES = """
CREATE TABLE url(
  id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, path TEXT NOT NULL, host TEXT NOT NULL, site TEXT NOT NULL, unit TEXT,
  tier INTEGER NOT NULL, kind TEXT NOT NULL, depth INTEGER NOT NULL,
  found_via TEXT NOT NULL, found_from INTEGER,
  state TEXT NOT NULL DEFAULT 'pending', reason TEXT, next_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  status INTEGER, final_url TEXT, ctype TEXT, bytes INTEGER, sha256 TEXT, warc TEXT, title TEXT, flags TEXT,
  fetched_at TEXT, elapsed_ms INTEGER, n_links INTEGER, n_postbacks INTEGER, directory INTEGER NOT NULL DEFAULT 0);
CREATE TABLE link(src INTEGER NOT NULL, dst TEXT NOT NULL, text TEXT, verdict TEXT NOT NULL,
  PRIMARY KEY(src, dst)) WITHOUT ROWID;
CREATE TABLE postback(site TEXT NOT NULL, target TEXT NOT NULL, n INTEGER NOT NULL, sample INTEGER,
  PRIMARY KEY(site, target)) WITHOUT ROWID;
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


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def fresh() -> sqlite3.Connection:
    """A database built from TABLES alone: what the views compile against, nothing migrated in."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(TABLES)
    yield conn
    conn.close()


@pytest.mark.parametrize("table", sorted(READ))
def test_every_column_read_is_in_tables(fresh, table):
    assert READ[table] <= columns(fresh, table), READ[table] - columns(fresh, table)


def test_the_views_compile_against_tables_alone(fresh):
    outcome.create_views(fresh)
    for name, _sql in outcome.VIEWS:
        fresh.execute(f"SELECT * FROM {name}").fetchall()  # preparing it resolves every table and column it reads


def test_the_views_carry_their_rows_and_their_results(fresh):
    outcome.create_views(fresh)
    assert columns(fresh, "url_result") == columns(fresh, "url") | VIEW_ADDS["url_result"]
    assert columns(fresh, "action_result") == columns(fresh, "action") | VIEW_ADDS["action_result"]
    assert VIEW_ADDS["page_completeness"] <= columns(fresh, "page_completeness")
    assert columns(fresh, "page_resource") == VIEW_ADDS["page_resource"]


def test_the_new_indexes_are_built(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    have = {r[0] for r in f.q("SELECT name FROM sqlite_master WHERE type='index'")}
    f.close()
    assert NEW_INDEXES <= have
    assert all(f"EXISTS {name} " in INDEXES for name in NEW_INDEXES)


def test_the_caps_count_only_what_this_round_fetches(tmp_path):
    """§9: later (round two) and held rows take no round-1 site's places; pending, active, done and failed do."""
    f = Frontier(tmp_path / "frontier.db")
    for i, state in enumerate(("pending", "active", "done", "failed", "skipped", "held", "later")):
        f.add(f"https://a.nknu.edu.tw/p{i}", host="a.nknu.edu.tw", site="a.nknu.edu.tw", unit=None, tier=0, kind="page",
              depth=0, found_via="seed", found_from=None, state=state, owner_site="a.nknu.edu.tw")
    try:
        assert f.count_site("a.nknu.edu.tw", "page") == 4
        assert f.count_path("a.nknu.edu.tw", "https://a.nknu.edu.tw/p6") == 0
    finally:
        f.close()


def test_new_columns_have_their_defaults(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    f.add("https://a.nknu.edu.tw/", host="a.nknu.edu.tw", site="a.nknu.edu.tw", unit=None, tier=0, kind="page", depth=0,
          found_via="seed", found_from=None)
    f.request("ip:1", "a.nknu.edu.tw", "https://a.nknu.edu.tw/", "page", 1.0, 2.0, 200, None)
    f.postback("a.nknu.edu.tw", "gvFull", 1)
    f.x("INSERT INTO site_plan(site, round) VALUES ('a.nknu.edu.tw', '1')")
    url, = f.q("SELECT prio, leaf FROM url")
    req, = f.q("SELECT attempt FROM request")
    pb, = f.q("SELECT decision FROM postback")
    plan, = f.q("SELECT leaf, params FROM site_plan")
    f.close()
    assert (url["prio"], url["leaf"], req["attempt"], pb["decision"]) == (1, 0, 1, "unknown")
    assert (plan["leaf"], plan["params"]) == (0, None)


def _old_frontier(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(OLD_TABLES)
    conn.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status, flags) VALUES "
                 "('https://a.nknu.edu.tw/x', 'https://a.nknu.edu.tw/x', 'a.nknu.edu.tw', 'a.nknu.edu.tw', 0, 'page', "
                 "0, 'seed', 'done', 200, 'soft404')")
    conn.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status) VALUES "
                 "('https://a.nknu.edu.tw/y', 'https://a.nknu.edu.tw/y', 'a.nknu.edu.tw', 'a.nknu.edu.tw', 0, 'page', "
                 "0, 'seed', 'done', 200)")
    conn.execute("INSERT INTO postback(site, target, n, sample) VALUES ('a.nknu.edu.tw', 'gvFull', 3, 1)")
    conn.commit()
    conn.close()


def test_an_old_frontier_gains_the_new_columns_and_tables_when_opened(tmp_path):
    path = tmp_path / "frontier.db"
    _old_frontier(path)
    f = Frontier(path)
    try:
        for table, cols in READ.items():
            assert cols <= columns(f.conn, table), (table, cols - columns(f.conn, table))
        rows = {r["url"]: r for r in f.q("SELECT url, prio, leaf, owner_site, kind0, result FROM url_result")}
        pb, = f.q("SELECT n, decision FROM postback")
    finally:
        f.close()
    old = rows["https://a.nknu.edu.tw/x"]
    assert (old["prio"], old["leaf"], old["owner_site"], old["kind0"]) == (1, 0, None, None)  # the defaults
    assert old["result"] == "looks_gone"  # a soft 404 is the program's comparison: a person looks (§10.1)
    assert rows["https://a.nknu.edu.tw/y"]["result"] == "unchecked"  # no page type yet: never ok
    assert (pb["n"], pb["decision"]) == (3, "unknown")


def test_the_monitor_still_refuses_an_old_frontier(tmp_path):
    path = tmp_path / "frontier.db"
    _old_frontier(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(OutdatedFrontier):
        Frontier(path, readonly=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # never altered by the read-only view


def test_views_are_rebuilt_at_every_open(tmp_path):
    path = tmp_path / "frontier.db"
    Frontier(path).close()
    conn = sqlite3.connect(path)
    conn.executescript("DROP VIEW url_result; CREATE VIEW url_result AS SELECT u.*, 'stale' AS result FROM url u;")
    conn.close()
    f = Frontier(path)
    sql, = f.q("SELECT sql FROM sqlite_master WHERE type='view' AND name='url_result'")
    f.close()
    assert "'stale'" not in sql[0] and "'unknown'" in sql[0]


def test_a_read_only_open_applies_this_versions_rules_without_writing(tmp_path):
    path = tmp_path / "frontier.db"
    f = Frontier(path)
    f.add("https://a.nknu.edu.tw/", host="a.nknu.edu.tw", site="a.nknu.edu.tw", unit=None, tier=0, kind="page", depth=0,
          found_via="seed", found_from=None)
    f.close()
    conn = sqlite3.connect(path)
    conn.executescript("DROP VIEW url_result; CREATE VIEW url_result AS SELECT u.*, NULL AS type_verdict, "
                       "'stale' AS result FROM url u;")
    conn.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ro = Frontier(path, readonly=True)
    try:
        assert [r["result"] for r in ro.q("SELECT result FROM url_result")] == ["todo"]
        assert ro.q("SELECT count(*) FROM page_completeness")[0][0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.x("UPDATE url SET state='done'")
    finally:
        ro.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------- the 10-24 archive is never written to (review 10-26)


def test_a_new_frontier_is_marked_round_one_and_opening_an_old_one_never_marks_it(tmp_path):
    from nknu.archive.frontier import round_of

    new = tmp_path / "new" / "frontier.db"
    Frontier(new).close()
    assert round_of(new) == "1"
    Frontier(new).close()
    assert round_of(new) == "1"  # opened again: still the same archive
    old = tmp_path / "old.db"
    _old_frontier(old)
    assert round_of(old) is None
    Frontier(old).close()  # a test's migration of an old file: it is still no round-1 archive
    assert round_of(old) is None
    assert round_of(tmp_path / "nowhere" / "frontier.db") is None


@pytest.mark.parametrize("cmd", [["retry", "timeout"], ["seed", "--round", "1", "--old", "/no/old-archive"],
                                 ["run", "--limit", "1"]])
def test_the_commands_that_write_refuse_the_10_24_archive(tmp_path, monkeypatch, cmd):
    # without --dir the commands use the repository's archive/, the 10-24 crawler's until it is renamed
    # archive-2026-10-24 (design §3, which asks the owner first): seed, run and retry would migrate it, and run would
    # resume the old crawl (CLAUDE.md: 全新重爬，不恢复旧爬虫). Here archive/ is a copy's stand-in under tmp_path
    from typer.testing import CliRunner

    import nknu.settings
    from nknu.admin.cli import app

    monkeypatch.setattr(nknu.settings, "REPO_ROOT", tmp_path)
    old = tmp_path / "archive" / "frontier.db"
    old.parent.mkdir()
    _old_frontier(old)
    (old.parent / "STOP").write_text("stop\n")
    before = hashlib.sha256(old.read_bytes()).hexdigest()
    for extra in ([], ["--dir", str(old.parent)]):
        out = CliRunner().invoke(app, ["archive", *cmd, *extra])
        assert out.exit_code == 1 and "archive-2026-10-24" in out.output, out.output
    assert hashlib.sha256(old.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in old.parent.iterdir()) == ["STOP", "frontier.db"]  # no lock, the old run not resumed
