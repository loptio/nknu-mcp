"""Each night's coverage by site, kept for the panels (2026-11-23): how many of a site's addresses were fetched, wait,
failed, need a look, and so on, as the ledger stood when that night's run was over. The panels drew this from the
nightly ledger backups, which their container cannot read; the counting is the panels' own (ops.pipeline.crawl_buckets),
kept here in the crawler's copy of the program."""
import json
import sqlite3

from nknu.archive import coverage

ACA, LIB, IEC = "c.nknu.edu.tw/aca", "lis.nknu.edu.tw", "sites.google.com/iec-nknu.page/iec"


def _ledger(path, rows, promoted=(), open_sites=None, with_batches=True, listed=(ACA, LIB, IEC), extra=()):
    """A ledger with only what the count reads: url_result (id, owner_site, result, added_at), url, round2_promoted,
    meta, site_plan (the sites of the list; None: a ledger without the table) and seed_src (extra: the row numbers,
    from 1, of the pages the owner added as seeds of their own)."""
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE url(id INTEGER PRIMARY KEY, owner_site TEXT, url TEXT);"
                       "CREATE TABLE seed_src(url TEXT NOT NULL, source TEXT NOT NULL);"
                       "CREATE TABLE url_result(id INTEGER PRIMARY KEY, owner_site TEXT, result TEXT, added_at REAL);"
                       "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);")
    for i, (site, result, added) in enumerate(rows, start=1):
        conn.execute("INSERT INTO url VALUES (?, ?, ?)", (i, site, f"https://page/{i}"))
        conn.execute("INSERT INTO url_result VALUES (?, ?, ?, ?)", (i, site, result, added))
    conn.executemany("INSERT INTO seed_src VALUES (?, 'extra')", [(f"https://page/{i}",) for i in extra])
    if listed is not None:
        conn.execute("CREATE TABLE site_plan(site TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO site_plan VALUES (?)", [(s,) for s in listed])
    if with_batches:
        conn.execute("CREATE TABLE round2_promoted(url_id INTEGER PRIMARY KEY, batch TEXT NOT NULL, at REAL NOT NULL)")
        conn.executemany("INSERT INTO round2_promoted VALUES (?, 'b', ?)", promoted)
    if open_sites is not None:
        conn.execute("INSERT INTO meta VALUES ('round2:open', ?)", (json.dumps({"sites": open_sites}),))
    conn.commit()
    return conn


ROWS = [(ACA, "ok", 10.0), (ACA, "ok", 10.0), (ACA, "todo", 10.0), (ACA, "thin", 10.0), (ACA, "held", 10.0),
        (ACA, "thin", 10.0), (LIB, "server_error", 10.0), (LIB, "ok", 10.0), (LIB, "rule", 10.0), (LIB, "later", 10.0),
        (None, "ok", 10.0), (ACA, "a_code_of_a_newer_program", 10.0), ("www.example.org", "rule_offsite", 10.0),
        ("www.example.org", "rule_offsite", 10.0), ("another.example.com", "later", 10.0)]


def test_each_site_s_addresses_are_counted_by_the_crawler_s_own_categories(tmp_path):
    got = coverage.count(_ledger(tmp_path / "f.db", ROWS))

    assert got["round2_open"] == []
    assert got["sites"][ACA] == {"buckets": {"got": 2, "queued": 1, "review": 4},
                                 "review": {"thin": 2, "held": 1, "a_code_of_a_newer_program": 1}}
    assert got["sites"][LIB] == {"buckets": {"failed": 1, "got": 1, "rule": 1, "next_round": 1}, "review": {}}
    # only the sites of the list are written one by one: the ledger names thousands of other owners (every host a
    # page links off campus, the rows no site owns), which are one sum
    assert sorted(got["sites"]) == [ACA, LIB]
    assert got["others"] == {"sites": 3, "buckets": {"got": 1, "rule": 2, "next_round": 1}}


def test_round_two_s_open_batch_is_counted_apart_from_the_site_s_own_rows(tmp_path):
    # the panels' rule: of a site whose batch is open, the rows the batch took from 下一輪 and the rows added since
    # the batch began are the batch's; so the batch is never read as round one falling behind
    rows = ROWS + [(IEC, "ok", 5.0), (IEC, "todo", 5.0), (IEC, "ok", 99.0), (IEC, "failed", 120.0), (ACA, "ok", 130.0)]
    took = len(ROWS) + 2  # the second IEC row was taken from the next round
    got = coverage.count(_ledger(tmp_path / "f.db", rows, promoted=[(took, 100.0)], open_sites=[IEC]))

    assert got["round2_open"] == [IEC]
    assert got["sites"][IEC] == {"buckets": {"got": 2}, "review": {}, "batch": {"queued": 1, "failed": 1}}
    assert got["sites"][ACA]["buckets"]["got"] == 3 and "batch" not in got["sites"][ACA]  # not a site of the batch


def test_the_pages_the_owner_added_as_seeds_are_written_under_their_own_key(tmp_path):
    # 招生報名, 課務查詢 and the like: single pages on keys the list does not name (sso.nknu.edu.tw/unit …), which the
    # library holds as sites of their own; left in the one sum, the panels' total over the library's sites came short
    rows = ROWS + [("sso.nknu.edu.tw/unit", "ok", 10.0), ("sso.nknu.edu.tw/unit", "gone", 10.0)]
    got = coverage.count(_ledger(tmp_path / "f.db", rows, extra=[len(ROWS) + 1]))

    assert got["sites"]["sso.nknu.edu.tw/unit"]["buckets"] == {"got": 1, "gone": 1}
    assert got["others"]["sites"] == 3  # as before: the page's key is not among them


def test_a_ledger_without_the_list_s_table_has_every_owner_written(tmp_path):
    got = coverage.count(_ledger(tmp_path / "f.db", ROWS, listed=None))

    assert sorted(got["sites"]) == sorted(["", ACA, "another.example.com", LIB, "www.example.org"])
    assert got["others"] == {"sites": 0, "buckets": {}}


def test_a_ledger_of_before_the_batches_is_counted_whole(tmp_path):
    got = coverage.count(_ledger(tmp_path / "f.db", ROWS, open_sites=[ACA], with_batches=False))

    assert got["sites"][ACA]["buckets"] == {"got": 2, "queued": 1, "review": 4} and "batch" not in got["sites"][ACA]


def test_a_night_is_one_line_and_counting_it_again_replaces_the_line(tmp_path, capsys):
    root = tmp_path / "archive"
    root.mkdir()
    _ledger(root / "frontier.db", ROWS).close()
    out = root / "reports" / "coverage.jsonl"

    assert coverage.main(["--dir", str(root), "--night", "2026-11-23"]) == 0
    assert "2026-11-23" in capsys.readouterr().out
    assert coverage.main(["--dir", str(root), "--night", "2026-11-21", "--quiet"]) == 0
    assert capsys.readouterr().out == ""  # the schedule's log is not this program's to write in
    _ledger(tmp_path / "later.db", ROWS + [(ACA, "ok", 10.0)]).close()
    (tmp_path / "later.db").replace(root / "frontier.db")
    assert coverage.main(["--dir", str(root), "--night", "2026-11-23"]) == 0

    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [x["night"] for x in lines] == ["2026-11-21", "2026-11-23"]  # by night, each once
    assert lines[1]["sites"][ACA]["buckets"]["got"] == 3 and lines[0]["sites"][ACA]["buckets"]["got"] == 2
    assert lines[1]["ledger"] == "live" and len(lines[1]["written_at"]) == 19 and lines[1]["round2_open"] == []
    assert not list(out.parent.glob("*.tmp"))


def test_the_night_is_the_day_the_run_started(tmp_path):
    # the status file of the night's run says when it began; a run that began before midnight and is counted after it
    # is still that day's
    root = tmp_path / "archive"
    (root / "reports").mkdir(parents=True)
    _ledger(root / "frontier.db", ROWS).close()
    (root / "reports" / "update-status.json").write_text(json.dumps({"night_started": "2026-11-24 01:00:03"}),
                                                         encoding="utf-8")

    assert coverage.main(["--dir", str(root)]) == 0

    assert json.loads((root / "reports" / "coverage.jsonl").read_text(encoding="utf-8"))["night"] == "2026-11-24"


def test_a_nightly_backup_is_the_ledger_as_the_run_before_left_it(tmp_path):
    # the backup is taken before that day's run: counted as the day before's line, and named in the line
    root = tmp_path / "archive"
    root.mkdir()
    backup = tmp_path / "nightly-20261101-010002.db"
    _ledger(backup, ROWS).close()

    assert coverage.main(["--dir", str(root), "--db", str(backup)]) == 0

    line = json.loads((root / "reports" / "coverage.jsonl").read_text(encoding="utf-8"))
    assert line["night"] == "2026-10-31" and line["ledger"] == backup.name and line["sites"][LIB]["buckets"]["got"] == 1
