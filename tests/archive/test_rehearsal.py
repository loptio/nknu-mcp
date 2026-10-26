"""The offline rehearsal (round-1 design §18.1), on a small made-up 10-24 archive (the old frontier.db's schema,
viewer-index.db, WARC files as the old crawler wrote them, one of them only in quarantine/): never the real one. Its
pages are minimal shapes (titles made up, no person's name): a unit's home with a news page, an attachment, a redirect,
attachments the old crawl never got and a Drive file; a c.nknu list whose next page is a postback button nobody pressed,
with a detail and its attachment.

What is checked: the replay answers a GET with the old record (status, headers, body), anything else is not_archived
(a POST included) and trips no breaker; the old archive is not changed by a byte; not_archived fails a URL or a press
at once (no retry, no start again from the list's GET, no server rest, no network pause); the clock only moves by
waits (each worker on its own time), so the politeness audit passes; reconcile and verify ran and wrote their reports;
the check command says PASS or FAIL for each expectation, with why, and exits 0 or 2."""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import socket
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from nknu.archive import crawler as crawl_mod
from nknu.archive import google_api, rehearsal, scope
from nknu.archive.frontier import Frontier
from nknu.archive.monitor import politeness, scope_audit
from nknu.archive.viewer import INDEX_SCHEMA
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import NotArchived
from tests.archive.plans import plan, site
from tests.archive.test_round1_e2e import ACA_DETAILS, ACA_LIST, PDF, aca_detail, aca_list

UNIT = "https://unit.nknu.edu.tw/"
NEWS, RULE, OLD, NEW = UNIT + "news.html", UNIT + "files/rule.pdf", UNIT + "old.html", UNIT + "new.html"
MISSING = [UNIT + f"files/missing{i}.pdf" for i in range(1, 6)]  # five in a row: never a breaker
OLD_PROBE = UNIT + "nknu-archive-probe-0123456789ab.html"  # what a missing page looked like on 10-24
DRIVE_ID = "1ReHeArSaLdRiVeFiLeAbCdEf"
DRIVE = f"https://drive.google.com/file/d/{DRIVE_ID}/view"
ACA_D663 = "https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid=663&GI=1"
ACA_D662 = "https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid=662&GI=1"
ACA_PDF = "https://c.nknu.edu.tw/aca/uploadfile/u663.pdf"
UNIT_IP, CNKNU_IP = "140.127.9.9", "140.127.62.110"
PLAN = plan(site("unit.nknu.edu.tw", name="某单位"),
            site("c.nknu.edu.tw/aca", imp="全校重要", name="教务处", home=ACA_LIST))
HTML = [("Content-Type", "text/html; charset=utf-8")]

# the 10-24 crawler's frontier.db, the tables the rehearsal and the seed read (its own schema, read from it)
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


def _home() -> str:
    links = "".join(f'<li><a href="{u}">附件 {i}</a></li>' for i, u in enumerate(MISSING, 1))
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>某單位</title></head><body><div id="content">'
            "<h1>某單位</h1><p>本單位負責校內某項業務，相關辦法與表單請見下方連結，最新消息另見消息頁。</p>"
            f'<ul><li><a href="news.html">最新消息</a></li><li><a href="files/rule.pdf">辦法</a></li>'
            f'<li><a href="old.html">舊網頁</a></li>{links}<li><a href="{DRIVE}">雲端檔案</a></li></ul>'
            "</div></body></html>")


def _page(title: str, text: str) -> str:
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title></head><body>'
            f'<div id="content"><h1>{title}</h1><p>{text}</p></div></body></html>')


def _write(path: Path, items: list[tuple[str, str, list[tuple[str, str]], bytes]]) -> list[tuple[str, int]]:
    """Request and response records as the 10-24 crawler wrote them (its body decoded, a Content-Length added, the
    status line with no phrase): (address, offset of the response record)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    with open(path, "ab") as fh:
        w = WARCWriter(fh, gzip=True)
        for url, status, headers, body in items:
            parts = scope.urlsplit(url)
            req = StatusAndHeaders(f"GET {parts.path or '/'}{'?' + parts.query if parts.query else ''} HTTP/1.1",
                                   [("User-Agent", "nknu-facts-bot/1.0")], is_http_request=True)
            w.write_record(w.create_warc_record(url, "request", payload=io.BytesIO(b""), http_headers=req))
            offset = fh.tell()
            resp = StatusAndHeaders(status, [*headers, ("Content-Length", str(len(body)))], protocol="HTTP/1.1")
            w.write_record(w.create_warc_record(url, "response", payload=io.BytesIO(body), http_headers=resp))
            out.append((url, offset))
    return out


def make_old(root: Path) -> Path:
    """A made-up 10-24 archive: unit's records in one WARC file the index covers (news.html first answered 503, then
    200), c.nknu's in one it never indexed, new.html only in quarantine/; the old url rows point at each."""
    c = scope.canonical
    unit_warc = "warc/2026/10/24/ip_140.127.9.9-1.warc.gz"
    cnknu_warc = "warc/2026/10/24/ip_140.127.62.110-1.warc.gz"
    quarantine = "quarantine/ip_140.127.9.9-1.warc.gz"
    unit = _write(root / unit_warc, [
        (c(UNIT + "robots.txt"), "200", [("Content-Type", "text/plain")], b"User-agent: *\nDisallow:\n"),
        (c(UNIT), "200", HTML, _home().encode()),
        (c(NEWS), "503", HTML, b"<html><body>busy</body></html>"),
        (c(NEWS), "200", HTML, _page("最新消息", "本學期的活動與公告整理如下，詳情請洽承辦人員。").encode()),
        (c(RULE), "200", [("Content-Type", "application/pdf")], PDF),
        (c(OLD), "302", [("Location", "new.html"), *HTML], b""),
        (c(OLD_PROBE), "404", HTML, b"<html><body>404 Not Found</body></html>"),  # the 10-24 crawler's probe
    ])
    _write(root / cnknu_warc, [
        (c(ACA_LIST), "200", HTML, aca_list(1).encode()),
        (c(ACA_D663), "200", HTML, aca_detail(*ACA_DETAILS[663]).encode()),
        (c(ACA_PDF), "200", [("Content-Type", "application/pdf")], PDF),
    ])
    _write(root / quarantine, [(c(NEW), "200", HTML, _page("新網頁", "舊網頁搬到這裡，內容照舊。").encode())])
    idx = sqlite3.connect(root / "viewer-index.db")
    idx.executescript(INDEX_SCHEMA)
    idx.executemany("INSERT INTO rec(url, warc, offset) VALUES (?,?,?)", [(u, unit_warc, o) for u, o in unit])
    idx.execute("INSERT INTO indexed(warc, upto) VALUES (?,?)", (unit_warc, (root / unit_warc).stat().st_size))
    idx.commit()
    idx.close()
    db = sqlite3.connect(root / "frontier.db")
    db.executescript(OLD_SCHEMA)
    rows = [(UNIT, unit_warc, 200), (NEWS, unit_warc, 200), (RULE, unit_warc, 200), (OLD, unit_warc, 302),
            (NEW, quarantine, 200), (MISSING[0], None, None), (ACA_LIST, cnknu_warc, 200), (ACA_D663, cnknu_warc, 200),
            (ACA_PDF, cnknu_warc, 200)]
    for i, (u, warc, status) in enumerate(rows, 1):
        cu = c(u)
        db.execute("INSERT INTO url(id, url, path, host, site, tier, kind, depth, found_via, state, status, warc) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (i, cu, scope.urlsplit(cu).path, scope.urlsplit(cu).hostname, scope.site_key(cu), 1,
                    "file" if cu.endswith(".pdf") else "page", 0, "seed", "done" if warc else "pending", status, warc))
    db.executemany("INSERT INTO host(host, queue, ip, cname) VALUES (?,?,?,?)",
                   [("unit.nknu.edu.tw", f"ip:{UNIT_IP}", UNIT_IP, "unit.nknu.edu.tw"),
                    ("c.nknu.edu.tw", f"ip:{CNKNU_IP}", CNKNU_IP, "c.nknu.edu.tw"),
                    ("gone.nknu.edu.tw", "dns-fail", None, None)])
    db.execute("INSERT INTO request(queue, host, url, purpose, started, ended, status) VALUES (?,?,?,?,?,?,?)",
               (f"ip:{UNIT_IP}", "unit.nknu.edu.tw", c(UNIT), "page", 1.0, 2.0, 200))
    db.commit()
    db.close()
    return root


def jstest_dir(root: Path) -> Path:
    """The 10-25 page-type test's five files, made up for this world's pages: each type tested ok."""
    from nknu.archive import pagetypes

    d = root / "jstest"
    d.mkdir(parents=True)
    pages = [UNIT, NEWS, NEW, ACA_LIST, ACA_D663]
    lines = [{"type": pagetypes.type_key(c, scope.site_key(c)), "url": c, "direct_status": 200, "direct_chars": 500,
              "rendered_chars": 500, "method": "direct"} for c in map(scope.canonical, pages)]
    (d / pagetypes.RESULTS).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                       encoding="utf-8")
    (d / pagetypes.RETEST).write_text("", encoding="utf-8")
    (d / pagetypes.TYPES).write_text(json.dumps({x["type"]: [x["url"]] for x in lines}, ensure_ascii=False),
                                     encoding="utf-8")
    (d / pagetypes.REVIEW).write_text(json.dumps({"kinds": []}), encoding="utf-8")
    (d / pagetypes.CLICK_SCAN).write_text(json.dumps({"kinds": []}), encoding="utf-8")
    return d


def fingerprint(root: Path) -> dict[str, tuple[int, str, float]]:
    return {str(p.relative_to(root)): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- the replay, alone


@pytest.fixture
def old(tmp_path) -> Path:
    return make_old(tmp_path / "old")


def test_a_get_is_answered_as_the_old_record_holds_it_and_anything_else_is_not_archived(old):
    archive = rehearsal.OldArchive(old)
    assert sorted(archive.scanned) == ["quarantine/ip_140.127.9.9-1.warc.gz", "warc/2026/10/24/ip_140.127.62.110-1.warc.gz"]
    clock = rehearsal.ReplayClock(1000.0)
    transport = rehearsal.ReplayTransport(archive, rehearsal.OldDNS(archive.hosts), clock)
    assert transport.offline is True
    client = crawl_mod.archive_client(None, transport=transport, clock=clock, sleep=clock.sleep)
    got = client.fetch(Target(NEWS), crawl_mod.NO_RATE)  # the old row says 200: not the 503 before it
    assert got.status == 200 and "本學期的活動" in got.text
    assert got.headers["content-type"] == "text/html; charset=utf-8"
    pdf = client.fetch(Target(RULE), crawl_mod.NO_RATE)
    assert pdf.body == PDF and pdf.headers["content-length"] == str(len(PDF))
    moved = client.fetch(Target(OLD), crawl_mod.NO_RATE)  # a redirect is handed back as it was, never followed
    assert moved.status == 302 and moved.headers["location"] == "new.html"
    assert client.fetch(Target(NEW), crawl_mod.NO_RATE).status == 200  # only in quarantine/
    assert client.fetch(Target(ACA_D663), crawl_mod.NO_RATE).status == 200  # a file the index never covered
    # the trace: one connection kept for the server, its address the old host table's
    assert [s.new_conn for s in (got.sent[0], pdf.sent[0], moved.sent[0])] == [True, False, False]
    assert got.sent[0].ip == UNIT_IP
    for url in MISSING:  # five in a row on one host: each its own not_archived, no breaker
        with pytest.raises(NotArchived) as exc:
            client.fetch(Target(url), crawl_mod.NO_RATE)
        assert exc.value.sent and "NotArchived" in exc.value.sent[0].error
        assert crawl_mod.error_kind(exc.value) == crawl_mod.NOT_ARCHIVED
    with pytest.raises(NotArchived):  # a POST: the old crawl pressed nothing, never answered with the GET
        client.fetch(Target(ACA_LIST, method="POST", data=[("__EVENTTARGET", "MasterGrid")]), crawl_mod.NO_RATE)
    assert client.fetch(Target(UNIT), crawl_mod.NO_RATE).status == 200  # the host was never cut off
    stats = transport.stats.as_dict()
    assert stats["not_archived"] == 6 and stats["not_archived_by_method"] == {"GET": 5, "POST": 1}


def test_a_probe_is_answered_with_the_old_probe_of_the_same_host_and_scheme(old):
    """Review 10-26 (清单①): every probe is a new random address, so none was ever in the old archive and soft404 could
    not be rehearsed (all 24 probes not_archived). A probe now gets the 10-24 crawler's probe of the same host on the
    same scheme (one a host, its name random too): what a missing page looks like there."""
    archive = rehearsal.OldArchive(old)
    rec = archive.find("GET", UNIT + "nknu-archive-probe-fedcba987654.html")
    assert rec is not None and archive.read(rec).status == 404
    assert archive.find("GET", "http://unit.nknu.edu.tw/nknu-archive-probe-fedcba987654.html") is None  # other scheme
    assert archive.find("GET", "https://c.nknu.edu.tw/nknu-archive-probe-fedcba987654.html") is None  # no old probe
    assert archive.find("POST", UNIT + "nknu-archive-probe-fedcba987654.html") is None
    assert archive.find("GET", UNIT + "nknu-archive-probe-x.html") is None  # not the crawler's probe name
    assert archive.find("GET", UNIT + "files/nknu-archive-probe-fedcba987654.html") is None


def test_without_an_old_row_the_newest_record_answers(tmp_path):
    root = tmp_path / "old"
    c = scope.canonical(UNIT + "page.html")
    _write(root / "warc/2026/10/24/a.warc.gz", [(c, "503", HTML, b"busy"), (c, "200", HTML, b"<html>ok</html>")])
    db = sqlite3.connect(root / "frontier.db")
    db.executescript(OLD_SCHEMA)
    db.close()
    archive = rehearsal.OldArchive(root)
    assert archive.read(archive.find("GET", UNIT + "page.html")).status == 200
    assert archive.find("GET", UNIT + "other.html") is None and archive.find("POST", UNIT + "page.html") is None


def test_names_come_from_the_old_host_table_and_nothing_is_looked_up(old):
    dns = rehearsal.OldDNS(rehearsal.OldArchive(old).hosts)
    assert dns.resolve("unit.nknu.edu.tw") == (f"ip:{UNIT_IP}", UNIT_IP, "unit.nknu.edu.tw")
    assert dns.resolve("gone.nknu.edu.tw")[:2] == (f"ip:{rehearsal.UNKNOWN_IP}", rehearsal.UNKNOWN_IP)
    assert dns.resolve("never.seen.example")[:2] == (f"ip:{rehearsal.UNKNOWN_IP}", rehearsal.UNKNOWN_IP)
    assert dns.resolve(scope.GOOGLE_SITES)[0] == "google"
    assert rehearsal.OldDNS({"x.nknu.edu.tw": rehearsal.OldHost("ip:1.2.3.4", "1.2.3.4", "ghs.google.com")}
                            ).resolve("x.nknu.edu.tw")[0] == "google"
    assert dns.ip_of(google_api.API_HOST) is None  # a named queue: its own server key in the audit


def test_the_clock_moves_only_by_waits_each_worker_on_its_own_time():
    clock = rehearsal.ReplayClock(1000.0)
    clock.sleep(5)  # the main loop's wait moves the latest time
    assert clock() == 1005.0
    seen: dict[str, list[float]] = {}

    def worker(name: str, gaps: list[float]) -> None:
        times = [clock()]
        for g in gaps:
            clock.sleep(g)
            times.append(clock())
        seen[name] = times

    a = threading.Thread(target=worker, args=("a", [3.0, 3.0, 3.0]))
    a.start()
    a.join()
    b = threading.Thread(target=worker, args=("b", [2.0]))
    b.start()
    b.join()
    assert seen["a"] == [1005.0, 1008.0, 1011.0, 1014.0]  # its own gaps, nobody else's
    assert seen["b"] == [1014.0, 1016.0]  # a new worker starts where the latest got to
    assert clock() == clock.latest == 1016.0
    event = rehearsal.ReplayEvent(clock)
    t0 = time.monotonic()
    assert event.wait(3600) is False  # an hour on the clock, a moment in real time
    assert time.monotonic() - t0 < 1.0 and clock() == 4616.0
    event.set()
    assert event.wait(10) is True


def test_the_guard_keeps_the_rehearsal_off_the_network():
    with rehearsal.offline_guard():
        with pytest.raises(rehearsal.OffNetwork, match="rehearsal guard"):
            socket.getaddrinfo("www.nknu.edu.tw", 443)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(OSError, match="rehearsal guard"):
            s.connect(("192.0.2.1", 9))
        assert socket.getaddrinfo("127.0.0.1", 80)
    with pytest.raises(OSError, match="test guard"):  # what was there before is back
        socket.getaddrinfo("www.nknu.edu.tw", 443)


def test_the_rehearsal_never_writes_into_or_over_the_old_archive(old, tmp_path):
    with pytest.raises(rehearsal.RehearsalRefused):
        rehearsal.refuse(old, old / "inside")
    with pytest.raises(rehearsal.RehearsalRefused):
        rehearsal.refuse(old, old)
    other = make_old(tmp_path / "another-old")  # a directory that holds the 10-24 crawler's frontier.db
    with pytest.raises(rehearsal.RehearsalRefused, match="10-24"):
        rehearsal.refuse(old, other)


def test_the_rehearsal_never_runs_on_a_round_one_archive_that_is_no_rehearsals(old, tmp_path):
    """Review 10-26: a real round-1 archive (its round mark, no rehearsal mark: a pilot's, or archive/ after the rename)
    given as --dir by mistake was let through: seeded, replayed into (not_archived for every page, the old answers
    written into its WARC as new ones, its hosts' addresses replaced) and marked a rehearsal's. Only a rehearsal's own
    directory goes on; nothing in the real one is touched."""
    from nknu.archive.frontier import rehearsal_of, round_of

    real = tmp_path / "archive-pilot"
    real.mkdir()
    Frontier(real / "frontier.db").close()  # what seed and run make for a real round-1 archive
    before = fingerprint(real)
    with pytest.raises(rehearsal.RehearsalRefused, match="第一轮的真存档"):
        rehearsal.refuse(old, real)
    with pytest.raises(rehearsal.RehearsalRefused, match="第一轮的真存档"):
        rehearsal.rehearse(old, real, plan=PLAN, cc_dir=None, probe=None, jstest=None)
    assert fingerprint(real) == before
    assert round_of(real / "frontier.db") == "1" and rehearsal_of(real / "frontier.db") is None


def test_a_rehearsal_stopped_before_its_seed_ended_is_still_a_rehearsals(old, tmp_path, monkeypatch):
    """The rehearsal's mark is written as its frontier.db is made, before the seed: a rehearsal stopped in the seed
    goes on (seeded again), and no command that sends requests runs on it meanwhile."""
    from nknu.archive.frontier import rehearsal_of

    def stopped(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(rehearsal.seeds, "seed_round1", stopped)
    root = tmp_path / "half"
    with pytest.raises(KeyboardInterrupt):
        rehearsal.rehearse(old, root, plan=PLAN, cc_dir=None, probe=None, jstest=None)
    assert rehearsal_of(root / "frontier.db") is not None
    rehearsal.refuse(old, root)  # its own directory: goes on
    f = Frontier(root / "frontier.db", readonly=True)
    try:
        assert f.meta(rehearsal.REHEARSAL_KEY) is None  # not seeded yet: the next rehearse seeds it
    finally:
        f.close()


# ---------------------------------------------------------------- the rehearsal end to end


@pytest.fixture(scope="module")
def rehearsed(tmp_path_factory):
    base = tmp_path_factory.mktemp("rehearsal")
    old = make_old(base / "old")
    before = fingerprint(old)
    mp = pytest.MonkeyPatch()
    mp.delenv(google_api.KEY_ENV, raising=False)
    try:
        done = rehearsal.rehearse(old, base / "new", plan=PLAN, cc_dir=None, probe=None, jstest=jstest_dir(base))
        key_after = google_api.key_set()
    finally:
        mp.undo()
    return done, old, before, key_after


def _f(done) -> Frontier:
    return Frontier(done.root / "frontier.db", readonly=True)


def test_the_old_archive_is_not_changed_by_a_byte(rehearsed):
    _done, old, before, _key = rehearsed
    assert fingerprint(old) == before  # no file written, none added (an -shm or -wal included)


def test_the_run_finishes_and_every_answer_came_from_the_old_archive(rehearsed):
    done, _old, _before, key_after = rehearsed
    assert done.run_end["reason"] == "finished" and done.run_end["pending"] == 0
    assert not key_after  # the placeholder key is gone again
    f = _f(done)
    try:
        rows = {r["url"]: r for r in f.q("SELECT * FROM url_result")}
        for url in (UNIT, NEWS, NEW, ACA_LIST, ACA_D663):  # answered as archived (200), the 503 before news not
            r = rows[scope.canonical(url)]
            assert (r["state"], r["status"], r["sniff"], r["attempts"]) == ("done", 200, "html", 0), url
        assert rows[scope.canonical(RULE)]["sniff"] == "pdf" and rows[scope.canonical(RULE)]["result"] == "ok"
        assert rows[scope.canonical(OLD)]["final_url"] == scope.canonical(NEW)  # the redirect, followed by its row
        for url in MISSING:  # failed at once: one attempt, never retried
            r = rows[scope.canonical(url)]
            assert (r["state"], r["reason"], r["attempts"], r["result"]) == ("failed", "not_archived", 1, "failed")
        d662 = rows[scope.canonical(ACA_D662)]
        assert (d662["state"], d662["reason"]) == ("failed", "not_archived")
        api = [r for r in rows.values() if r["host"] == google_api.API_HOST]
        assert api and all((r["state"], r["reason"]) == ("failed", "not_archived") for r in api)
        # no server rested, no network paused, no host cooled for it
        assert f.q("SELECT * FROM pause") == []
        codes = {r[0] for r in f.q("SELECT code FROM event")}
        assert not codes & {"conn_pause", "server_rest", "host_cooldown", "offline", "robots_unreachable",
                            "host_unreachable"}
        assert f.q("SELECT count(*) FROM request WHERE error LIKE 'NotArchived%'")[0][0] >= len(MISSING)
    finally:
        f.close()


def test_a_press_the_old_archive_never_made_fails_once_and_the_list_is_fetched_once(rehearsed):
    done = rehearsed[0]
    f = _f(done)
    try:
        acts = [dict(r) for r in f.q("SELECT kind, state, reason, epoch FROM action")]
        assert acts == [{"kind": "pager", "state": "failed", "reason": "not_archived", "epoch": 1}]
        walk = f.q("SELECT ended_by FROM listwalk")[0][0]
        assert walk == "post_failed"
        lst = scope.canonical(ACA_LIST)
        assert f.q("SELECT count(*) FROM request WHERE url=? AND purpose='page'", (lst,))[0][0] == 1  # no restart
        assert f.q("SELECT count(*) FROM request WHERE purpose='id_probe'")[0][0] == 1  # asked once, then not again
        page = f.q("SELECT level, reasons FROM page_completeness pc JOIN url u ON u.id = pc.id WHERE u.url=?",
                   (lst,))[0]
        assert page["level"] != "complete" and "failed" in page["reasons"].split(",")
    finally:
        f.close()


def test_the_politeness_and_scope_audits_pass_on_the_rehearsals_clock(rehearsed):
    done = rehearsed[0]
    f = _f(done)
    try:
        assert politeness(f) == [] and scope_audit(f, PLAN) == []
        reqs = f.q("SELECT ip, new_conn, started, ended FROM request WHERE host='unit.nknu.edu.tw' ORDER BY started")
        assert {r["ip"] for r in reqs} - {None} == {UNIT_IP}  # none: a request on a kept connection that got no answer
        assert reqs[0]["new_conn"] == 1 and any(r["new_conn"] == 0 for r in reqs)
        gaps = [b["started"] - a["ended"] for a, b in itertools.pairwise(reqs)]
        assert gaps and min(gaps) >= crawl_mod.GAP[0]  # the run's own gap, on the clock
    finally:
        f.close()


def test_reconcile_and_verify_ran_and_the_summary_says_what_was_replayed(rehearsed):
    done = rehearsed[0]
    assert done.reconcile_report is not None and done.reconcile_report.is_file()
    assert done.verify_report is not None and done.verify_report.is_file() and done.verify_code in (0, 1, 2)
    assert set(done.reconcile_gates) == {"unit.nknu.edu.tw", "c.nknu.edu.tw/aca"}
    assert not any(done.reconcile_gates.values())  # R6 was not done: no site passes its gates
    text = done.summary.read_text(encoding="utf-8")
    assert "not_archived" in text and "run 结束：finished" in text
    data = json.loads(done.summary.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["stats"]["not_archived_by_method"]["POST"] == 1
    assert data["stats"]["answered"] >= 7


def test_soft404_is_rehearsed_where_the_old_archive_has_a_probe_and_the_blind_spots_are_said(rehearsed):
    done = rehearsed[0]
    f = _f(done)
    try:
        status = {r["host"]: r["probe_status"] for r in f.q("SELECT host, probe_status FROM host")}
        assert status["unit.nknu.edu.tw"] == 404  # answered with the 10-24 probe of that host
        assert status["c.nknu.edu.tw"] == 0  # no old probe there: not_archived, nothing known
    finally:
        f.close()
    data = json.loads(done.summary.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["stats"]["probes"] == {"answered": 1, "not_archived": 1}
    text = done.summary.read_text(encoding="utf-8")
    assert "彩排测不到" in text and "gone_id" in text and "soft404" in text


def test_the_page_lacking_what_the_old_archive_never_got_is_never_complete(rehearsed):
    done = rehearsed[0]
    f = _f(done)
    try:
        home = f.q("SELECT level, reasons FROM page_completeness pc JOIN url u ON u.id = pc.id WHERE u.url=?",
                   (scope.canonical(UNIT),))[0]
        assert home["level"] != "complete"
        assert {"failed", "miss_file"} <= set(home["reasons"].split(","))
    finally:
        f.close()


# ---------------------------------------------------------------- the check


def _expect(path: Path, items: list[dict]) -> Path:
    path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return path


GOOD = [
    {"id": "home", "说明": "首页缺附件", "select": {"pages": {"url": UNIT}},
     "expect": {"n": 1, "level_not": ["complete"], "reasons_has": ["failed", "miss_file"]}},
    {"id": "missing", "说明": "旧存档没有的附件", "select": {"urls": {"urls": MISSING}},
     "expect": {"n": len(MISSING), "state": "failed", "reason": "not_archived", "result": "failed"}},
    {"id": "press", "说明": "没按过的按钮", "select": {"sql": "SELECT count(*) FROM action WHERE reason=?",
                                                    "params": ["not_archived"]}, "expect": {"value": 1}},
    {"id": "site", "说明": "这个网站的网页都不是完整", "select": {"pages": {"site": "c.nknu.edu.tw/aca",
                                                                    "url_like": "%newslist%"}},
     "expect": {"n_min": 1, "level_not": ["complete"]}},
    {"id": "none", "说明": "反例：没有这种网址", "select": {"urls": {"url_like": "%nothing-like-this%"}},
     "expect": {"n": 0}},
    {"id": "polite", "说明": "礼貌稽核在彩排的时钟上也要过", "select": {"audit": "politeness"}, "expect": {"n": 0}},
]


def test_the_check_says_pass_for_what_holds_and_fail_with_why_for_what_does_not(rehearsed, tmp_path):
    done = rehearsed[0]
    got = rehearsal.check(_expect(tmp_path / "good.json", GOOD), done.root)
    assert [(r.id, r.ok) for r in got] == [(x["id"], True) for x in GOOD], [r.why for r in got]
    bad = [
        {"id": "wrong_level", "select": {"pages": {"url": UNIT}}, "expect": {"level": "complete"}},
        {"id": "wrong_count", "select": {"urls": {"urls": MISSING}}, "expect": {"n": 2}},
        {"id": "typo", "select": {"pages": {"url": UNIT}}, "expect": {"levle": "partial"}},
        {"id": "bad_sql", "select": {"sql": "SELECT nope FROM nowhere"}, "expect": {"value": 1}},
        {"id": "reason_missing", "select": {"pages": {"url": UNIT}}, "expect": {"reasons_has": ["js"]}},
    ]
    got = {r.id: r for r in rehearsal.check(_expect(tmp_path / "bad.json", bad), done.root)}
    assert not any(r.ok for r in got.values())
    assert "level=" in got["wrong_level"].why and "预期 ['complete']" in got["wrong_level"].why
    assert "选到 5 行，预期 2 行" in got["wrong_count"].why
    assert "levle" in got["typo"].why and "读不出来" in got["bad_sql"].why
    assert "缺 ['js']" in got["reason_missing"].why
    many = rehearsal.check(_expect(tmp_path / "many.json", [{"id": "m", "select": {"urls": {"urls": MISSING}},
                                                             "expect": {"n": 0}}]), done.root)[0]
    assert not many.ok and "例如" in many.why and scope.canonical(MISSING[0]) in many.why  # which rows, not only how many
    odd = rehearsal.check(_expect(tmp_path / "odd.json", [{"id": "a", "select": {"audit": "speed"}, "expect": {"n": 0}}]),
                          done.root)[0]
    assert not odd.ok and "unknown audit" in odd.why


def test_the_check_command_exits_0_when_all_pass_and_2_when_one_fails(rehearsed, tmp_path):
    from typer.testing import CliRunner

    from nknu.admin.cli import app

    done = rehearsed[0]
    good = _expect(tmp_path / "good.json", GOOD)
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(good), "--dir", str(done.root)])
    assert out.exit_code == 0, out.output
    lines = out.output.splitlines()
    assert sum(x.startswith("PASS  ") for x in lines) == len(GOOD) and not any(x.startswith("FAIL") for x in lines)
    bad = _expect(tmp_path / "bad.json", [*GOOD, {"id": "x", "select": {"pages": {"url": UNIT}},
                                                  "expect": {"level": "complete"}}])
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(bad), "--dir", str(done.root)])
    assert out.exit_code == 2 and "FAIL  x：" in out.output


def test_a_check_on_a_column_the_selected_rows_lack_fails_never_passes():
    """Review 10-26 (清单①): result_not, each_lacks, each_not on a column the rows do not have (pages have own and
    level, no result or flags; a column name mistyped) passed for every row, the value being None."""
    page_row = {"id": 1, "url": UNIT, "own": "ok", "level": "partial", "reasons": "miss_file", "label": "x"}
    for exp in ({"n": 1, "result_not": ["thin"]}, {"each_lacks": {"flags": ["thin"]}}, {"each_not": {"levle": ["ok"]}},
                {"each": {"flags": [None]}}, {"each_has": {"flagz": ["thin"]}}, {"state": "done"}):
        bad = rehearsal._judge([page_row], exp)
        assert bad and any("不在选到的行里" in b for b in bad), exp
    assert rehearsal._judge([page_row], {"n": 1, "own_not": ["thin"], "reasons_not": ["failed"]}) == []
    assert rehearsal._judge([], {"n": 0, "result_not": ["thin"]}) == []  # no rows: nothing to check the column on
    sql_row = {"flags": None, "result": "ok"}  # a column that is there and NULL is a value, not a missing column
    assert rehearsal._judge([sql_row], {"each_lacks": {"flags": ["thin"]}, "result_not": ["thin"]}) == []


def test_expectations_need_an_id_once_a_select_and_an_expect(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        rehearsal.load_expectations(_expect(tmp_path / "a.json", [GOOD[0], GOOD[0]]))
    with pytest.raises(ValueError, match="needs id"):
        rehearsal.load_expectations(_expect(tmp_path / "b.json", [{"id": "x", "select": {}}]))


def test_the_crawler_takes_not_archived_as_the_urls_own_failure_never_the_servers(tmp_path):
    """Unit: error_kind, _failed and the press (no restart) on not_archived, apart from the replay."""
    exc = NotArchived("x: none")
    assert crawl_mod.error_kind(exc) == "not_archived"
    assert not crawl_mod._erred("not_archived") and "not_archived" not in crawl_mod.NET_KINDS
    assert isinstance(exc, crawl_mod.FetchError) and not isinstance(exc, httpx.TransportError)


def test_no_command_that_sends_requests_runs_on_a_rehearsal_folder(rehearsed, tmp_path):
    """Its hosts' addresses are the old archive's or made up: run, seed, jscheck and reconcile with R6 refuse it; what
    only reads it (verify, the check) does not."""
    from typer.testing import CliRunner

    from nknu.admin.cli import app
    from nknu.archive.frontier import rehearsal_of

    done, old = rehearsed[0], rehearsed[1]
    assert rehearsal_of(done.root / "frontier.db") is not None and rehearsal_of(old / "frontier.db") is None
    for args in (["run"], ["seed", "--old", str(old)], ["jscheck"], ["reconcile", "--batch", "x"]):
        out = CliRunner().invoke(app, ["archive", *args, "--dir", str(done.root)])
        assert out.exit_code == 1 and "离线彩排的资料夹" in out.output, (args, out.output)
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(_expect(tmp_path / "g.json", GOOD[:1])),
                                   "--dir", str(done.root)])
    assert out.exit_code == 0
