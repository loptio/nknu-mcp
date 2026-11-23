"""檔案 (the 11-23 design's file drawer, live: nknu.ops.lineage): each step's input and output files as they stand, and a
look inside one by its key, parsed for the eye. The Google key is never touched, a key not in the map (or a path) is
refused, every string served has secrets masked, and every path follows from the service's own places. A made-up
world (tests/ops/world.py) with the files the steps leave. No real data."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from nknu.archive.frontier import Frontier
from nknu.ops import lineage, pipeline
from nknu.ops.pipeline import Where
from tests.ops import world as made

KEY = "AIza" + "SyD" + "x" * 30  # a made-up Google key, never a real one


@pytest.fixture
def where(tmp_path, monkeypatch) -> Where:
    from nknu.retrieval import vectors

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    w.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('vec_built_at', '2026-11-23T03:10:00+08:00')")
    w.conn.commit()
    archive = tmp_path / "crawl" / "archive"
    (archive / "reports").mkdir(parents=True)
    (archive / "frontier.db").write_bytes(b"")
    (archive / "reports" / "update-status.json").write_text(json.dumps(
        {"night_started": "2026-11-23T01:00:02+08:00", "note": f"url https://x.example/?key={KEY} and {KEY}"}),
        encoding="utf-8")
    (archive / "warc" / "2026" / "11" / "22").mkdir(parents=True)
    (archive / "warc" / "2026" / "11" / "22" / "a.warc.gz").write_bytes(b"\x1f\x8b" + b"0" * 100)
    (archive / "update.log").write_text(f"[2026-11-23 01:00:02] 开跑\n[2026-11-23 01:05:00] 错误 token={KEY}\n",
                                        encoding="utf-8")
    exports = tmp_path / pipeline.EXPORTS
    (exports / "current.tsv").write_text(f"{made.SITE}\t{made.EXPORTS[made.SITE]}\n{made.BOARD}\t"
                                         f"{made.EXPORTS[made.BOARD]}\n", encoding="utf-8")
    pipe = tmp_path / pipeline.PIPE
    pipe.mkdir(parents=True, exist_ok=True)
    (pipe / "exports.txt").write_text(f"# 庫要用的導出資料夾。\n{made.EXPORTS[made.SITE]}\n", encoding="utf-8")
    (pipe / "ingest-hold.txt").write_text("# 先不進庫的網站。\n", encoding="utf-8")
    (pipe / "lab_pc").mkdir()
    (pipe / "lab_pc" / "build.log").write_text("[2026-11-23 02:00:00] build ok\n", encoding="utf-8")
    (pipe / "ingest-history.jsonl").write_text(json.dumps({"night": "2026-11-22", "outcome": "swapped"}) + "\n",
                                              encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "units.yaml").write_text("# 單位表。\n# 第二段說明。\nunits:\n  文學院:\n    campus: 和平\n    evidence: >-\n"
                                       "      頁面 0093\n  教務處:\n    aliases: [教務]\n", encoding="utf-8")
    (config / "site_units.yaml").write_text(f"# 網站對單位。\nschool: 國立高雄師範大學\nsites:\n  {made.SITE}: "
                                            "{unit: \"教務處\", how: \"auto: 第一轮名称\"}\n  sso.example: {unit: \"全校\","
                                            " shared: true, how: \"hand: 不确定\"}\n", encoding="utf-8")
    (config / "aliases.yaml").write_text("# 同義詞。\ngroups:\n  - [停修, 棄選]  # 舊名\n  - [學測, 學科能力測驗]\n",
                                         encoding="utf-8")
    return Where(root=tmp_path, db=w.db, archive=archive)


def file(where: Where, key: str, **params) -> dict:
    status, body = lineage.api(where, {"key": key, **params})
    assert status == 200, body
    return body


def counts(rows: list) -> tuple[dict, list[str]]:
    """A database's table rows as {table: (rows, how)}, and its notes."""
    return {r[0]: (r[1], r[2]) for r in rows if r[2] != "note"}, [r[0] for r in rows if r[2] == "note"]


def tips(rows: list) -> dict:
    return {r[0]: r[3] for r in rows if r[2] != "note"}


def ledger(where: Where) -> Path:
    """A ledger in the crawler's own schema, empty."""
    path = where.archive / "frontier.db"
    path.unlink()
    Frontier(path).conn.close()
    lineage._tables.clear()
    return path


# ---------------------------------------------------------------- the map


def test_the_map_is_up_to_date_with_what_the_night_writes():
    steps = lineage.STEPS
    assert {"coverage", "warc_backup"} <= set(steps["crawl"]["out"])
    assert {"site_checks", "site_check_docs"} <= set(steps["build"]["out"])
    assert "nights_day" in steps["swap"]["out"] and "checks_md" not in steps["swap"]["out"]  # by hand since 11-23
    for step in steps.values():
        assert all(k in lineage.FILES for k in step["in"] + step["out"])


def test_every_path_follows_from_the_services_own_places():
    where = Where(root=Path("/work"), db=Path("research/pipeline_2026-11-02/aca.db"), archive=Path("/crawl/archive"))
    for key, (_name, _what, base, _rel) in lineage.FILES.items():
        path = lineage.path_of(where, key, site="school.example")
        if base == "host" or path is None:
            continue
        assert str(path).startswith(("/work/", "/crawl/")), (key, path)
    assert str(lineage.path_of(where, "aca_prev")) == "/work/research/pipeline_2026-11-02/aca.prev.db"
    assert str(lineage.path_of(where, "round1")) == "/crawl/code-update/config/archive_round1.json"


def test_a_steps_files_as_they_stand_with_the_files_a_role_reads_instead(where):
    status, body = lineage.api(where, {"step": "swap"})
    assert status == 200 and body["steps"]["swap"]["name"] == "換庫" and body["run_day"] == "2026-11-23"
    info = body["info"]
    assert info["aca"]["exists"] and info["aca"]["size"] > 0 and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", info["aca"]["mtime"])
    assert "aca_prev" in info and info["aca_prev"]["exists"] is False  # 換前 reads it
    assert info["aca_tmp"]["absent"] == "建好、換上以後就改名成 aca.db 了"
    crawl = lineage.files(where, "crawl")["info"]
    assert crawl["warc_day"]["name"] == "warc/2026/11/22/" and crawl["warc_day"]["count"] == 1  # the night's UTC day
    # the D: backup is not missing (FD-08): no exists:false, its chip says where it is
    backup = crawl["backup_nightly"]
    assert backup["host"] and "exists" not in backup and backup["chip"] == "檢視台沒掛 D 槽" and "D 槽" in backup["absent"]
    assert file(where, "backup_nightly")["look"] == {"kind": "host", "note": backup["absent"]}
    folder = lineage.files(where, "export", site=made.SITE)["info"]["export_folder"]
    assert folder["dir"] and folder["count"] >= 3 and folder["name"].startswith("school.example-")
    assert lineage.info(where, "export_folder", made.SITE, "export", wait=True)["size"] > 0  # walked in the background


def test_unknown_keys_and_paths_are_refused(where):
    assert lineage.api(where, {"key": "../../etc/passwd"})[0] == 404
    assert lineage.api(where, {"key": "nowhere"})[0] == 404
    assert lineage.api(where, {"step": "nowhere"})[0] == 404


# ---------------------------------------------------------------- secrets


def test_the_google_key_is_never_touched(where, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the key file was touched")

    monkeypatch.setattr(lineage, "path_of", boom)
    body = file(where, "google_key")
    assert body["secret"] and body["look"] == {"kind": "secret"} and "size" not in body and "exists" not in body
    assert body["path"] == r"C:\nknu-crawl\secrets\google.env"


def test_a_path_holding_secrets_is_treated_as_the_key(where, monkeypatch):
    secret = where.pipe / "secrets" / "google.env"
    secret.parent.mkdir(parents=True)
    secret.write_text(f"GOOGLE_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setitem(lineage.FILES, "oops", ("google.env", "", "pipe", "secrets/google.env"))
    body = file(where, "oops")
    assert body["secret"] and KEY not in json.dumps(body)


def test_every_string_served_has_secrets_masked(where):
    status = file(where, "update_status")
    assert status["look"]["kind"] == "json" and KEY not in json.dumps(status, ensure_ascii=False)
    assert "（遮掉的金鑰）" in status["look"]["json"]["note"]
    tail = file(where, "update_log")
    assert tail["look"]["kind"] == "tail" and KEY not in json.dumps(tail) and len(tail["look"]["tail"]) == 2


def test_the_views_and_folder_lists_are_masked_too(where):
    """FD-05: a YAML or list file read as a table, a folder's names and the step's chips are masked as the rest."""
    units = where.root / "config" / "units.yaml"
    units.write_text(units.read_text(encoding="utf-8") + f"  總務處:\n    evidence: https://x.example/?key={KEY}\n",
                     encoding="utf-8")
    (where.pipe / "ingest-hold.txt").write_text(f"school.example  # 見 https://x.example/?token={KEY}\n",
                                                encoding="utf-8")
    (where.archive / "warc" / "2026" / "11" / "22" / f"{KEY}.warc.gz").write_bytes(b"0")
    for key in ("units", "ingest_hold", "warc_day"):
        assert KEY not in json.dumps(file(where, key), ensure_ascii=False), key
    assert KEY not in json.dumps(lineage.files(where, "crawl"), ensure_ascii=False)


# ---------------------------------------------------------------- a look inside


def test_yaml_files_read_as_tables_with_their_head_as_a_note(where):
    units = file(where, "units")["look"]["view"]
    assert units["intro"] == ["單位表。", "第二段說明。"] and units["cols"][0] == "單位"
    assert units["rows"][0]["單位"] == "文學院" and units["rows"][0]["校區"] == "和平"
    site_units = file(where, "site_units")["look"]["view"]
    assert site_units["top"] == {"全校": "國立高雄師範大學"}
    assert site_units["rows"][0]["怎麼定"] == "程式對上" and site_units["rows"][1]["共用平台"] == "是"
    aliases = file(where, "aliases")["look"]["view"]
    assert aliases["rows"][0] == {"同一個意思的詞": ["停修", "棄選"], "說明": "舊名"}


def test_list_files_name_the_site_of_an_export_folder_and_an_empty_list_says_so(where):
    rows = file(where, "exports_txt")["look"]["view"]["rows"]
    assert rows[0]["網站"] == made.SITE and rows[0]["名稱"] == made.NAMES[made.SITE] and rows[0]["導出於"] == "11-15 00:00"
    hold = file(where, "ingest_hold")["look"]["view"]
    assert hold["rows"] == [] and hold["empty"] == "空的：現在沒有網站在這裡" and hold["intro"] == ["先不進庫的網站。"]
    history = file(where, "ingest_history")["look"]["view"]
    assert history["rows"][0]["outcome"] == "swapped" and history["cols"] == ["night", "outcome"]


def _run_line(day: int) -> str:
    """One line of ingest-history.jsonl as long as the lab's (8-10 KB): the sites that came in with their folders, a
    departure's long address, a key in a log path."""
    night = f"2026-11-{day:02d}"
    sites = [{"site": f"site{n}.example", "folder": f"archive-export/sites/site{n}.example-202611{day:02d}-013000"}
             for n in range(60)]
    url = "https://x.example/" + "a" * 262 + f"?key={KEY}&next=" + "b" * 300  # the key across where a value is cut
    return json.dumps({"night": night, "started_at": f"{night}T02:39:12+08:00", "finished_at": f"{night}T03:14:34+08:00",
                       "outcome": "swapped", "step": "done", "error": None, "sites_new": sites, "sites_held": [],
                       "departures": {"left": 2, "items": [{"url": url}]}, "log": f"lab_pc/ingest.log?token={KEY}"},
                      ensure_ascii=False)


def test_the_ingest_history_is_a_table_of_the_last_runs_read_from_whole_lines(where):
    """FD-04 / NV18: each line (~9 KB on the lab) is parsed whole, then its strings masked and cut; the last
    HISTORY_RUNS runs, newest first; a line being written is said, not dropped in silence."""
    lines = [_run_line(day) for day in range(1, 26)]
    assert min(map(len, lines)) > 1500 and sum(map(len, lines)) > lineage.TAIL_BYTES
    (where.pipe / "ingest-history.jsonl").write_text("\n".join(lines) + '\n{"night": "2026-11-26", "outc',
                                                    encoding="utf-8")
    view = file(where, "ingest_history")["look"]["view"]
    rows = view["rows"]
    assert len(rows) == lineage.HISTORY_RUNS
    assert rows[0]["night"] == "2026-11-25" and rows[-1]["night"] == f"2026-11-{26 - lineage.HISTORY_RUNS:02d}"
    assert view["cols"] == ["night", "outcome", "started_at", "finished_at", "step", "sites_new"]  # none empty
    assert len(rows[0]["sites_new"]) == 60 and rows[0]["sites_new"][0]["site"] == "site0.example"
    url = rows[0]["departures"]["items"][0]["url"]
    assert len(url) == lineage.VALUE_CHARS + 1 and url.endswith("…")
    said = json.dumps(view, ensure_ascii=False)
    assert KEY[:12] not in said and "key=（遮掉" in url  # masked before it was cut
    assert any("1 行" in p for p in view["intro"])  # the line being written


def test_a_sites_manifest_shows_its_rows_and_without_a_site_the_folders_to_pick(where):
    body = file(where, "manifest", site=made.SITE, step="totext")
    look = body["look"]
    assert look["kind"] == "json" and look["json"]["网站"] == made.SITE
    assert look["cut"]["项目"] == len(look["json"]["项目"]) and body["readers"] == ["轉文字"] and body["writers"] == ["導出"]
    pick = file(where, "manifest")["look"]
    assert pick["kind"] == "view" and pick["view"]["open"] == "site"
    assert {r["網站"] for r in pick["view"]["rows"]} == {made.SITE, made.BOARD}
    current = file(where, "current")["look"]
    assert current["kind"] == "lines" and current["head"] == ["網站", "導出資料夾"] and len(current["lines"]) == 2


def test_a_library_shows_its_tables_counted_in_the_background(where):
    body = lineage.file(where, "aca", {}, wait=True)
    tables, _notes = counts(body["look"]["tables"])
    assert tables["docs"] == (5, "exact") and tables["sites"] == (2, "exact")
    assert body["writers"] == ["換庫"] and body["readers"] == ["換庫"]


def test_a_librarys_index_tables_are_said_by_what_they_hold(where):
    """FD-06: the keyword index's own tables are not listed (chunk_fts_data's last id is a segment number, never a row
    count) and a line says why; chunk_fts is counted by its one row a chunk; the vectors are what the build wrote down
    with them (meta vec_count), never read (kilobytes a row)."""
    conn = sqlite3.connect(where.db)
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('vec_count', ?)", (str(chunks),))
    conn.commit()
    conn.close()
    lineage._tables.clear()
    rows = lineage.file(where, "aca", {}, wait=True)["look"]["tables"]
    tables, notes = counts(rows)
    assert not [n for n in tables if n.startswith("chunk_fts_")]
    assert tables["chunk_fts"] == (chunks, "exact") and "chunk_fts_docsize" in tips(rows)["chunk_fts"]
    assert tables["chunk_vecs"] == (chunks, "exact") and "vec_count" in tips(rows)["chunk_vecs"]
    assert any("chunk_fts_data" in n and "chunk_fts_idx" in n for n in notes)
    assert all(n is not None for n, _how in tables.values())


def test_a_vector_cache_is_said_by_its_ids_never_read_whole(where, monkeypatch):
    """FD-06: vec_cache's vecs (a few KB a row, 1.4 million rows on the lab) is said 約 by its id range and a sample."""
    monkeypatch.setattr(lineage, "ROW_CAP", 10)
    monkeypatch.setattr(lineage, "SAMPLES", 2)
    monkeypatch.setattr(lineage, "SAMPLE_IDS", 3)
    path = where.pipe / "vec_cache.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vecs (key TEXT PRIMARY KEY, vec BLOB NOT NULL)")
    conn.executemany("INSERT INTO vecs VALUES (?, ?)", [(f"k{i}", b"\0" * 64) for i in range(20)])
    conn.commit()
    conn.close()
    lineage._tables.clear()
    rows = lineage.file(where, "vec_cache", {}, wait=True)["look"]["tables"]
    tables, notes = counts(rows)
    assert tables == {"vecs": (20, "about")} and "1–20" in tips(rows)["vecs"] and any("約" in n for n in notes)


def test_the_ledger_says_every_table_and_never_an_upper_bound_as_a_count(where, monkeypatch):
    """FD-06: the ledger's small tables are counted (a TEXT key's, as host's, too); a table is read up to ROW_CAP rows
    only; past it, a table whose ids run on is said 約 by its id range and a sample, any other 超過 the cap. A table
    whose last id runs far past its rows (action: 69,870 against 19,365 rows) is counted, never said by that id."""
    monkeypatch.setattr(lineage, "ROW_CAP", 50)
    monkeypatch.setattr(lineage, "SAMPLES", 4)
    monkeypatch.setattr(lineage, "SAMPLE_IDS", 5)
    path = ledger(where)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO host(host, queue) VALUES (?, 'q')", [(f"h{i}.example",) for i in range(3)])
    conn.execute("CREATE TABLE sparse(id INTEGER PRIMARY KEY, x)")  # as action: rows gone, its last id far on
    conn.executemany("INSERT INTO sparse(id) VALUES (?)", [(i,) for i in [*range(1, 31), *range(1000, 1010)]])
    conn.execute("CREATE TABLE dense(id INTEGER PRIMARY KEY, x)")  # as url: ids run on
    conn.executemany("INSERT INTO dense(id) VALUES (?)", [(i,) for i in range(1, 121)])
    conn.execute("CREATE TABLE keyed(url_id INTEGER PRIMARY KEY)")  # as page_watch: another table's ids
    conn.executemany("INSERT INTO keyed VALUES (?)", [(i * 7,) for i in range(1, 61)])
    conn.executemany("INSERT INTO link(src, dst, verdict) VALUES (?, ?, 'ok')",
                     [(i, f"https://a.example/{i}") for i in range(80)])  # without rowids
    conn.commit()
    conn.close()
    rows = lineage.tables(where, "frontier", path, wait=True)[0]
    got, notes = counts(rows)
    assert got["host"] == (3, "exact") and got["sparse"] == (40, "exact") and got["page_res"] == (0, "exact")
    assert got["dense"] == (120, "about") and "1–120" in tips(rows)["dense"]
    assert got["link"] == (50, "over") and got["keyed"] == (50, "over")
    assert all(n is not None for n, _how in got.values())  # every table said
    assert any(n.startswith("超過") for n in notes) and any(n.startswith("約") for n in notes)


def test_a_count_that_takes_too_long_stops_and_says_at_least(where, monkeypatch):
    monkeypatch.setattr(lineage, "TABLE_SECONDS", 0.0)
    monkeypatch.setattr(lineage, "FETCH", 10)
    path = ledger(where)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO link(src, dst, verdict) VALUES (?, ?, 'ok')",
                     [(i, f"https://a.example/{i}") for i in range(80)])
    conn.commit()
    conn.close()
    got, notes = counts(lineage.tables(where, "frontier", path, wait=True)[0])
    assert got["link"] == (10, "least") and got["host"] == (0, "exact")
    assert any("至少" in n for n in notes)


DRAWERS_JS = Path(lineage.__file__).parent / "theme" / "design" / "drawers.js"
# a stand-in document for drawers.js in node: every element keeps one element per selector; fetch answers /api/file
# from RESP by its key (the map without one)
STUB = r"""
class El { constructor(t) { this.tag = t; this.dataset = {}; this.style = {}; this.kept = {}; this.innerHTML = '';
    this.classList = { add() {}, remove() {}, toggle() {} }; }
  setAttribute() {} appendChild(c) { return c; } before() {} after() {} remove() {} addEventListener() {}
  querySelector(s) { return (this.kept[s] = this.kept[s] || new El(s)); } querySelectorAll() { return []; } }
const made = [];
global.window = globalThis;
global.addEventListener = () => {};
global.document = { body: new El('body'), createElement: t => { const e = new El(t); made.push(e); return e; } };
const RESP = { map: { files: {}, steps: {}, order: [] } };
global.fetch = async url => { const r = RESP[new URL(url, 'http://panel').searchParams.get('key') || 'map'];
  return { ok: true, json: async () => r }; };
"""


def test_the_drawer_draws_how_each_count_is_known_and_the_notes_under_the_table():
    """FD-06 in the browser: exact, 約, 超過 and 至少 as the server says, what a number rests on as a hover, 「—」 with
    why, and the notes as lines under the table, not as rows of it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    tables = [["action", 19365, "exact", ""], ["link", 100000, "over", "數到 100,000 筆就停了"],
              ["url", 191899, "about", "編號 1–191,899"], ["page_res", 42000, "least", "數了 1 秒"],
              ["odd", None, "", "這一次讀不到"], ["超過、至少：帳本邊抓邊改。", None, "note", ""]]
    script = ("RESP.frontier = { key: 'frontier', name: 'frontier.db', exists: true, size: 10, look: { kind: 'tables', "
              f"counted: '11-23 13:40', tables: {json.dumps(tables, ensure_ascii=False)} }} }};\n"
              "window.FILEOPEN('frontier', 'crawl'); await new Promise(r => setTimeout(r, 50));\n"
              "console.log(JSON.stringify(made.find(e => e.className === 'rvd rvd-wide').innerHTML));")
    src = STUB + DRAWERS_JS.read_text(encoding="utf-8") + "\n(async () => {\n" + script + "\n})()"
    html = json.loads(subprocess.run([node, "-e", src], check=True, capture_output=True, text=True, timeout=60).stdout)
    cells = dict(re.findall(r"<tr><td>([^<]+)</td><td class='num'>(.*?)</td></tr>", html))
    assert cells["action"] == "19,365 筆"
    assert cells["link"] == "<span data-tip='數到 100,000 筆就停了'>超過 100,000 筆</span>"
    assert cells["url"] == "<span data-tip='編號 1–191,899'>約 191,899 筆</span>"
    assert cells["page_res"] == "<span data-tip='數了 1 秒'>至少 42,000 筆</span>"
    assert cells["odd"] == "<span data-tip='這一次讀不到'>—</span>"
    assert "超過、至少" not in cells and "<p class='fv-note'>超過、至少：帳本邊抓邊改。</p>" in html


def test_the_ledgers_table_counts_are_kept_a_while_and_counted_by_one_thread_at_a_time(where, monkeypatch):
    """FD-06: the ledger changes all night; its last count is served (with when it was made) for pipeline.EVERY
    seconds, then counted again by one thread however often the drawer asks, the newer count replacing the older."""
    path = ledger(where)  # the crawler's own schema
    first, at = lineage.tables(where, "frontier", path, wait=True)
    names = [r[0] for r in first]
    assert "url" in names and "zz_new" not in names and re.fullmatch(r"\d\d-\d\d \d\d:\d\d", at)
    look = file(where, "frontier")["look"]
    assert look["kind"] == "tables" and look["counted"] == at and not look["pending"]
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE zz_new(x)")  # the crawl writes on
    conn.execute("INSERT INTO zz_new VALUES (1)")
    conn.commit()
    conn.close()
    calls, gate, real = [], threading.Event(), lineage._count

    def slow(*args):
        calls.append(args)
        gate.wait(10)
        real(*args)

    monkeypatch.setattr(lineage, "_count", slow)
    assert lineage.tables(where, "frontier", path) == (first, at)
    time.sleep(0.05)
    assert calls == []  # within pipeline.EVERY: no count started
    monkeypatch.setattr(pipeline, "EVERY", 0.0)
    for _ in range(5):
        assert lineage.tables(where, "frontier", path) == (first, at)  # the last count meanwhile
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1  # one thread a file
    gate.set()
    while str(path) in lineage._counting and time.monotonic() < deadline:
        time.sleep(0.01)
    assert counts(lineage.tables(where, "frontier", path)[0])[0]["zz_new"] == (1, "exact")
    assert list(lineage._tables) == [str(path)]  # the older count dropped


def test_the_live_ledger_is_never_read_whole(where, monkeypatch):
    """Review 11-23 #18: a count(*) of the ledger's link table (WITHOUT ROWID) is seconds of one read beside the
    crawl, every pipeline.EVERY. Every read of the ledger is bounded: a table read up to ROW_CAP rows (LIMIT), its
    first and last ids, a count of a range of ids; never a count of a whole table."""
    from nknu.ops import pagecheck

    path = where.archive / "frontier.db"
    path.unlink()
    front = Frontier(path)
    front.conn.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via) VALUES"
                       " ('https://a.example/1', '/1', 'a.example', 'a.example', 1, 'page', 1, 'seed'),"
                       " ('https://a.example/2', '/2', 'a.example', 'a.example', 1, 'page', 1, 'seed')")
    front.conn.execute("INSERT INTO link(src, dst, verdict) VALUES (1, 'https://a.example/2', 'ok')")
    front.conn.commit()
    front.conn.close()
    said, real = [], pagecheck.open_ledger

    def spied(archive):
        conn = real(archive)
        conn.set_trace_callback(said.append)
        return conn

    monkeypatch.setattr(pagecheck, "open_ledger", spied)
    lineage._tables.clear()
    got, notes = counts(lineage.tables(where, "frontier", path, wait=True)[0])
    reads = [s for s in said if re.search(r'\bFROM\s+"', s)]
    assert reads and not [s for s in reads if "count(" in s.lower() and "between" not in s.lower()]
    assert all("LIMIT" in s for s in reads if s.startswith("SELECT 1 FROM"))
    assert got["url"] == (2, "exact") and got["link"] == (1, "exact") and notes == []
    # a library is still counted exactly (it changes once a night)
    exact, _notes = counts(lineage.file(where, "aca", {}, wait=True)["look"]["tables"])
    assert exact["sites"] == (2, "exact")


def test_a_table_without_rowids_is_told_by_its_options_not_a_comment():
    assert lineage.without_rowid("CREATE TABLE link(src INTEGER, dst TEXT, PRIMARY KEY(src, dst)) WITHOUT ROWID")
    assert lineage.without_rowid("CREATE TABLE t(a PRIMARY KEY) STRICT, WITHOUT ROWID")
    assert not lineage.without_rowid("CREATE TABLE url(id INTEGER PRIMARY KEY, -- not WITHOUT ROWID here\n x TEXT)")
    assert not lineage.without_rowid(None)


def test_a_count_that_fails_is_said_and_tried_again(where, monkeypatch):
    """An older ledger (or any error) never leaves the drawer at 正在數 for ever: it says 這一次讀不到 and the count is
    made again after a while."""
    path = where.archive / "frontier.db"  # empty: the crawler's check calls it an older version
    lineage._tables.clear()
    got, _at = lineage.tables(where, "frontier", path, wait=True)
    assert got == [lineage.UNREAD] and str(path) not in lineage._counting
    path.unlink()
    Frontier(path).conn.close()
    assert lineage.tables(where, "frontier", path)[0] == [lineage.UNREAD]  # within pipeline.EVERY
    monkeypatch.setattr(pipeline, "EVERY", 0.0)
    assert "url" in counts(lineage.tables(where, "frontier", path, wait=True)[0])[0]


def test_a_log_reads_through_the_logs_own_masking(where):
    body = file(where, "build_log")
    assert body["look"] == {"kind": "tail", "tail": ["[2026-11-23 02:00:00] build ok"]}


def test_a_file_that_is_not_there_says_why(where):
    body = file(where, "aca_held")
    assert body["exists"] is False and body["look"] == {"kind": "absent", "absent": "沒有被擋下的新庫"}
    where.archive = None
    assert "沒有掛上爬蟲" in file(where, "frontier")["look"]["absent"]
