"""原始紀錄 (the 11-23 design's raw drawer, live: nknu.ops.raw): every record behind each step of the night, served a page
at a time from the step's own source with the label and facet counts under the filters. The counts are the steps' own
(pipeline.crawl_step, export_step, totext_step, build_step), the site and miss presets the night view passes land on
exactly those rows, a preset may name a key or a word, and nothing of a school's text is converted. A made-up world
(tests/ops/world.py: two sites' exports and a library) and a made-up ledger (the crawler's own schema). No real data."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from nknu.archive import outcome
from nknu.archive.batch import TABLE as PROMOTED
from nknu.archive.frontier import Frontier
from nknu.core.timeutil import TAIPEI
from nknu.ops import design, night_view, pipeline, raw
from nknu.ops.names import rename
from nknu.ops.pipeline import Where
from tests.ops import world as made

SITE, BOARD = made.SITE, made.BOARD
OUTSIDE = "elsewhere.example"
# (url, owner_site, state, reason, status, flags, kind): one of each bucket on the first site, two on the second, and
# one of a site the library does not have
LEDGER = [
    (made.NOTICE_URL, SITE, "done", None, 200, None, "page"),
    (made.FORM_URL, SITE, "done", None, 200, None, "file"),
    ("https://school.example/thin.aspx", SITE, "done", None, 200, "thin", "page"),
    ("https://school.example/fail.aspx", SITE, "failed", "server_down", None, None, "page"),
    ("https://school.example/later.aspx", SITE, "later", "round2:other_site", None, None, "page"),
    ("https://school.example/rule.aspx", SITE, "skipped", "out:social", None, None, "page"),
    ("https://school.example/todo.aspx", SITE, "pending", None, None, None, "page"),
    ("https://school.example/gone.aspx", SITE, "done", None, 404, None, "page"),
    ("https://school.example/login.aspx", SITE, "done", None, 200, "login", "page"),
    (made.COPY_URL, BOARD, "done", None, 200, None, "page"),
    ("https://school.example/board/fail.aspx", BOARD, "failed", "server_down", None, None, "page"),
    ("https://elsewhere.example/a", OUTSIDE, "done", None, 200, None, "page"),
]
STARTED = datetime(2026, 11, 23, 1, 5, tzinfo=TAIPEI).timestamp()  # the notice's last request


def _ledger(archive: Path) -> dict[str, int]:
    front = Frontier(archive / "frontier.db")
    conn = front.conn
    ids = {}
    for url, owner, state, reason, status, flags, kind in LEDGER:
        cur = conn.execute(
            "INSERT INTO url(url, path, host, site, tier, kind, kind0, depth, found_via, found_from, state, reason,"
            " status, attempts, flags, sniff, ctype, title, fetched_at, owner_site)"
            " VALUES (?, '/', ?, ?, 1, ?, ?, 2, 'link', ?, ?, ?, ?, 1, ?, ?, ?, ?, '2026-11-22T17:05:00+00:00', ?)",
            (url, url.split("/")[2], owner, kind, kind, ids.get(made.NOTICE_URL), state, reason, status, flags,
             "pdf" if kind == "file" else "html", "application/pdf" if kind == "file" else "text/html",
             "停修公告" if url == made.NOTICE_URL else None, owner))
        ids[url] = cur.lastrowid
    conn.execute("INSERT INTO host(host, queue) VALUES ('school.example', 'ip:10.0.0.1')")
    conn.executemany(
        "INSERT INTO request(queue, host, url, purpose, started, ended, status, method, bytes, campus)"
        " VALUES ('ip:10.0.0.1', 'school.example', ?, ?, ?, ?, ?, 'GET', 2048, 'off')",
        [(made.NOTICE_URL, "page", STARTED - 60, STARTED - 59.6, 500), (made.NOTICE_URL, "page", STARTED, STARTED + 0.25,
                                                                      200)])
    conn.close()
    return ids


def _accounting(where: Where) -> None:
    folder = where.pipe / "totext"
    folder.mkdir(parents=True, exist_ok=True)
    lines = ([{"key": f"school.example:{i}", "status": "ok", "file": f"pages/{i:04}.html", "route": "page",
               "type": "网页", "chars": 100} for i in range(1, 6)]
             + [{"key": "school.example:8", "status": "empty", "reason": "OCR 没认出字", "file": "files/scan.pdf",
                 "route": "pdf", "type": "附件", "pages": 2, "ocr_pages": [{"page": 1}, {"page": 2}]},
                {"key": "school.example_board:1", "status": "ok", "file": "pages/0001-board.html", "route": "page",
                 "type": "网页", "chars": 80},
                {"key": "school.example_board:2", "status": "failed", "reason": "LibreOffice 超时（120 秒）",
                 "file": "files/a.doc", "route": "office", "type": "附件"}])
    (folder / "accounting.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                            encoding="utf-8")
    by_status = {"ok": 6, "empty": 1, "failed": 1}
    (folder / "summary.json").write_text(json.dumps({"files": 8, "by_status": by_status}), encoding="utf-8")


@pytest.fixture
def where(tmp_path, monkeypatch) -> Where:
    from nknu.retrieval import vectors

    monkeypatch.setattr(vectors, "_CACHE", {})
    w = made.build(tmp_path)
    # a row whose title a converter would wrongly change (the design copy's 張岳印 -> 張嶽印), and two lacks in one row
    w.row("https://school.example/vita.aspx?id=1", "网页", "", None, "張岳印", 结果="没抓到", 完整度="缺",
          缺什么="连接失败（1 个）、缺图片（1 个）")
    w.write()
    w.conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", [
        ("counts_json", json.dumps({"docs": {"notice": 1, "attachment": 4, "total": 5}, "chunks": 6})),
        ("excluded_json", json.dumps({"网页(列表翻页)": 7, "公告(学校删掉)": 2, "附件(副本)": 1})),
        ("vec_built_at", "2026-11-23T03:10:00+08:00")])
    w.conn.commit()
    archive = tmp_path / "archive"
    _ledger(archive)
    out = Where(root=tmp_path, db=w.db, archive=archive)
    _accounting(out)
    raw.forget()
    return out


def page(where: Where, step: str, **params) -> dict:
    got = raw.page(where, step, {k: str(v) for k, v in params.items()}, wait=20)
    assert got["state"] == "ready", got
    return got


def by_key(body: dict) -> dict[str, int]:
    return {k: n for k, _w, n, _o in body["labels"]}


# ---------------------------------------------------------------- 爬取


def test_the_crawl_rows_count_what_the_crawl_step_counts(where):
    body = page(where, "crawl")
    conn = pipeline.open_ledger(where.archive)
    try:
        buckets, _batch = pipeline.crawl_buckets(conn, {SITE, BOARD}, [])
    finally:
        conn.close()
    want: dict[str, int] = {}
    for per in buckets.values():
        for b, n in per.items():
            want[b] = want.get(b, 0) + n
    got: dict[str, int] = {}
    for k, n in by_key(body).items():
        b = "review" if k.startswith("review:") else k
        got[b] = got.get(b, 0) + n
    assert got == want and body["scope"] == sum(want.values()) == 11  # the outside site's address is not in scope
    step = pipeline.crawl_step(where)
    assert step.out == [("個抓到了", by_key(body)["got"])]
    assert (body["run_day"], body["night"]) == ("", "")  # no nightly status file: no night to name
    assert "庫外網站 1" in body["head"] and body["out"] == {"key": "got", "word": "已取得"}
    # 待確認 split by its result, in the renamed words; buckets as names.py words them
    words = {k: w for k, w, _n, _o in body["labels"]}
    assert words["review:thin"] == "待確認：內容過少" and words["gone"] == "校方已移除" and words["failed"] == "連線失敗"


def test_round_twos_open_batch_is_kept_apart_as_the_crawl_step_keeps_it(where):
    conn = sqlite3.connect(where.archive / "frontier.db")
    conn.execute(PROMOTED)
    board_fail = conn.execute("SELECT id FROM url WHERE url = 'https://school.example/board/fail.aspx'").fetchone()[0]
    conn.execute("INSERT INTO round2_promoted(url_id, batch, at) VALUES (?, 'b1', 1)", (board_fail,))
    conn.execute("UPDATE url SET added_at = 0")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('round2:open', ?)", (json.dumps({"sites": [BOARD]}),))
    conn.commit()
    conn.close()
    body = page(where, "crawl")
    assert body["scope"] == 10 and "第二輪這一批 1" in body["head"]
    assert page(where, "crawl", site=BOARD)["matched"] == 1  # the batch's row is out; the copy (older) stays


def test_a_page_of_addresses_has_the_ledgers_fields_its_requests_and_where_it_came_from(where):
    body = page(where, "crawl", label="got", q="example/news.aspx")
    assert body["matched"] == 1 and body["label_on"] == ["got"]
    r = body["rows"][0]
    assert r["url"] == made.NOTICE_URL and r["site"] == SITE and r["bucket"] == "got" and r["label"] == "已取得"
    assert r["how"] == "直接請求" and r["at"] == "11-23 01:05"  # the ledger's UTC time said in Taipei time
    assert [q["status"] for q in r["req"]] == [200, 500] and r["req"][0]["at"].startswith("2026-11-23 01:05")
    assert r["req"][0]["ms"] == 250 and r["req"][0]["campus"] == "off"
    form = page(where, "crawl", q="form.pdf")["rows"][0]
    assert form["from_url"] == made.NOTICE_URL and form["how"] == "沒發請求" and form["kind"] == "file"


def test_the_night_views_presets_land_on_exactly_their_rows(where):
    body = page(where, "crawl", site=SITE, miss=1)
    assert body["matched"] == 5 and body["site_name"] == made.NAMES[SITE]
    assert {r["bucket"] for r in body["rows"]} == {"review", "failed", "queued", "gone", "login"}
    assert all(r["site"] == SITE for r in body["rows"])
    # a loss segment: its bucket; a bucket's word does too (a site's 未取得 lists name buckets)
    assert page(where, "crawl", site=SITE, bucket="failed")["matched"] == 1
    worded = page(where, "crawl", label="待確認")
    assert worded["filters"].get("bucket") == "review" and worded["matched"] == 1 and worded["bucket_word"] == "待確認"
    # a label by an older word (the design's 學校那邊已經不存在) finds the renamed one
    assert page(where, "crawl", label="學校那邊已經不存在")["label_on"] == ["gone"]
    # the facets count under the other filters, and the state facet in the ledger's words
    facets = {k: {v: (w, n) for v, w, n in vals} for k, _t, vals in body["facets"]}
    assert facets["state"]["failed"] == ("失敗", 1) and set(facets) == {"state", "reason", "how", "via", "kind"}


def test_only_what_did_not_go_on_is_every_bucket_the_crawl_step_counts_as_lost(where, monkeypatch):
    """RD-04: 只看沒通過的 is the 通過率 bar's lost, 待判定 (pending_check) and 校方 (school_side) included."""
    assert set(raw.MISS_BUCKETS) == {"review", "failed", "queued", "gone", "login", "school_side", "pending_check"}
    monkeypatch.setitem(outcome._BUCKET_OF, "retry", "pending_check")  # the queued address: 待判定 now
    step = pipeline.crawl_step(where)
    body = page(where, "crawl", miss=1)
    assert "pending_check" in {r["bucket"] for r in body["rows"]}
    assert body["matched"] == sum(n for _k, _w, n in step.lost_keys) == step.into[0][1] - step.out[0][1]


def test_every_lost_row_a_step_keys_finds_its_rows_or_its_count(where):
    """RD-13: each row of the night view's lost lists (pipeline's lost_keys; 換庫's bare reasons from
    night_view.swap_flow) opens the drawer on exactly its rows, or on the note of a kind only counted; a key the drawer
    does not know falls back to the row's words (alt)."""
    for name, step in (("crawl", pipeline.crawl_step(where)), ("export", pipeline.export_step(where)),
                       ("totext", pipeline.totext_step(where)), ("build", pipeline.build_step(where))):
        assert step.lost_keys, name
        for key, word, n in step.lost_keys:
            body = page(where, name, label=key, per=0)
            if body["only"]:
                assert body["matched"] == 0 and f"（{n:,} 筆）" in body["note"], (name, key)
            else:
                assert body["matched"] == n and body["label_on"], (name, key, word)
            assert page(where, name, label="nowhere:" + key, alt=rename(word), per=0)["matched"] == body["matched"]
    flow = night_view.swap_flow(10, 10, _changes())
    assert flow["lost"]
    for word, n, key in flow["lost"]:
        body = raw.page(where, "swap", {"label": key, "per": "0"}, changes=_changes())
        assert body["matched"] == n and body["label_on"] == [f"left:{key}"] and word
        assert body["aliases"][key] == [f"left:{key}"]  # what the drawer's hook looks a row's key up in
    # a split bucket's key (an older night's coverage line keys 待確認's causes by the bucket) goes by the words
    review = page(where, "crawl", label="review", alt="待確認：內容過少", per=0)
    assert review["label_on"] == ["review:thin"] and review["matched"] == 1


def test_every_string_a_row_holds_is_masked(where):
    """FD-05: the to-text, export, build and swap rows (the whole record under 全部欄位 too) are masked as the ledger's."""
    key = "AIza" + "SyD" + "x" * 30  # made up
    with (where.pipe / "totext" / "accounting.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": "school.example:9", "status": "failed", "file": "files/k.pdf", "route": "pdf",
                            "reason": f"GET https://x.example/?key={key} 403", "type": "附件"}) + "\n")
    raw.forget()
    body = page(where, "totext", q="k.pdf")
    assert body["matched"] == 1 and key not in json.dumps(body, ensure_ascii=False)
    assert "（遮掉）" in body["rows"][0]["raw"]["reason"]
    changes = _changes()
    changes["sites"][0]["lists"]["new"][0]["url"] = f"https://school.example/n1?token={key}"
    swap = raw.page(where, "swap", {"label": "new"}, changes=changes)
    assert key not in json.dumps(swap, ensure_ascii=False)


def test_paging_reads_one_page_and_counts_all(where):
    first = page(where, "crawl", per=2)
    second = page(where, "crawl", per=2, page=2)
    assert first["matched"] == second["matched"] == 11 and len(first["rows"]) == len(second["rows"]) == 2
    assert {r["id"] for r in first["rows"]}.isdisjoint({r["id"] for r in second["rows"]})
    counts = page(where, "crawl", per=0)
    assert counts["rows"] == [] and counts["labels"]


def test_the_ledgers_reading_is_named_by_the_crawls_run_day(where):
    (where.archive / "reports").mkdir()
    (where.archive / "reports" / "update-status.json").write_text(
        json.dumps({"night_started": "2026-11-22T17:00:02+00:00"}), encoding="utf-8")
    body = page(where, "crawl", per=0)
    assert (body["run_day"], body["night"]) == ("2026-11-23", "2026-11-22")
    assert "帳本" in body["src"]["tip"] and body["as_of"] in body["src"]["tip"]


def test_no_ledger_mounted_says_so(where):
    where.archive = None
    got = raw.page(where, "crawl", {})
    assert got["state"] == "none" and "帳本" in got["words"]


def _spy_ledger(monkeypatch) -> list[str]:
    """Every statement the drawer sends the ledger."""
    from nknu.ops import pagecheck

    said, real = [], pagecheck.open_ledger

    def spied(archive):
        conn = real(archive)
        conn.set_trace_callback(said.append)
        return conn

    monkeypatch.setattr(pagecheck, "open_ledger", spied)
    return said


def test_the_ledger_is_read_in_short_statements_of_the_librarys_sites_only(where, monkeypatch):
    """Review 11-23 #14: no statement reads the whole ledger: url_result a rowid range at a time with the library's
    sites in the statement, the request table read on by id (no table-wide join or GROUP BY), and the sites outside
    only counted. However small the ranges, the drawer counts what the crawl step counts."""
    said = _spy_ledger(monkeypatch)
    whole = page(where, "crawl", per=0)
    reads = [s for s in said if "FROM url_result" in s]
    assert reads and all("id > " in s and "owner_site IN" in s for s in reads)
    assert not [s for s in said if "group_concat" in s or "GROUP BY" in s]
    assert [s for s in said if "FROM request WHERE id > " in s]
    raw.forget()
    monkeypatch.setattr(raw, "CHUNK", 2)  # many ranges, many request readings
    said.clear()
    small = page(where, "crawl", per=0)
    assert len([s for s in said if "FROM url_result" in s]) > len(reads)
    assert by_key(small) == by_key(whole) and small["head"] == whole["head"] and "庫外網站 1" in small["head"]
    form = page(where, "crawl", q="news.aspx")["rows"][0]
    assert [q["status"] for q in form["req"]] == [200, 500] and form["how"] == "直接請求"


def test_the_request_table_is_read_on_from_where_the_last_reading_left_off(where):
    """The ledger's request table only grows: the next index reads the requests after the last id it saw, so a
    reading every pipeline.EVERY during the crawl costs what the crawl added; a table shorter than what was read (another
    ledger put in place) is read anew."""
    first = raw._crawl_build(where)
    asked = first.info["asked"]
    assert asked.last_id == 2 and asked.of[made.NOTICE_URL][1] == STARTED
    conn = sqlite3.connect(where.archive / "frontier.db")
    conn.execute("INSERT INTO request(queue, host, url, purpose, started, ended, status, method)"
                 " VALUES ('ip:10.0.0.1', 'school.example', ?, 'jsfetch', ?, ?, 200, 'GET')",
                 (made.FORM_URL, STARTED + 5, STARTED + 6))
    conn.commit()
    conn.close()
    second = raw._crawl_build(where, first)
    assert second.info["asked"] is asked and asked.last_id == 3
    how = dict(zip(second.cols["how"][1], range(len(second.cols["how"][1])), strict=True))
    form = next(i for i, ref in enumerate(second.ref) if int(ref) == _ids(where)[made.FORM_URL])
    assert second.cols["how"][0][form] == how["瀏覽器開"] and second.info["last"][form] == STARTED + 5
    asked.last_id = 10 ** 6  # more than the table holds: another ledger
    third = raw._crawl_build(where, second)
    assert third.info["asked"] is not asked and third.info["asked"].last_id == 3


def _ids(where: Where) -> dict[str, int]:
    conn = sqlite3.connect(where.archive / "frontier.db")
    try:
        return dict(conn.execute("SELECT url, id FROM url").fetchall())
    finally:
        conn.close()


def test_a_changed_ledger_is_read_again_no_sooner_than_every_and_the_last_reading_is_served_meanwhile(where,
                                                                                                    monkeypatch):
    first = page(where, "crawl", per=0)
    builds, gate, real = [], threading.Event(), raw._crawl_build

    def slow(w, prev=None):
        builds.append(prev)
        gate.wait(10)
        return real(w, prev)

    monkeypatch.setattr(raw, "_crawl_build", slow)
    conn = sqlite3.connect(where.archive / "frontier.db")
    conn.execute("UPDATE url SET title = '改了' WHERE url = ?", (made.FORM_URL,))  # the crawl writes on
    conn.commit()
    conn.close()
    assert raw.page(where, "crawl", {"per": "0"})["state"] == "ready"
    time.sleep(0.05)
    assert builds == []  # within pipeline.EVERY of the last reading: not read again
    monkeypatch.setattr(pipeline, "EVERY", 0.0)
    started = time.monotonic()
    served = raw.page(where, "crawl", {"per": "0"})
    assert served["state"] == "ready" and served["labels"] == first["labels"] and time.monotonic() - started < 2
    deadline = time.monotonic() + 5
    while not builds and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(builds) == 1 and builds[0] is not None  # handed the last reading, to read the requests on from it
    gate.set()
    assert raw.page(where, "crawl", {"per": "0"}, wait=10)["state"] == "ready"  # the new reading is in


# ---------------------------------------------------------------- 導出


def test_the_export_rows_count_what_the_manifests_say(where):
    body = page(where, "export")
    missing: dict[str, int] = {}
    file_rows = rows = 0
    for s in pipeline.library_facts(where.library)["sites"]:
        summary = pipeline.manifest_summary(pipeline.folder_of(where.root, s["export"]))
        rows += summary["rows"]
        file_rows += summary["file_rows"]
        for k, v in summary["missing"].items():
            missing[k] = missing.get(k, 0) + v
    by_word = {w: n for _k, w, n, _o in body["labels"]}
    assert body["scope"] == rows and body["head"].startswith(f"{rows:,} 筆")
    assert (body["run_day"], body["night"]) == ("2026-11-23", "2026-11-22")  # swapped 11-23 03:10: both names of it
    assert by_key(body)["file"] == file_rows and {rename(k): by_word[rename(k)] for k in missing} == {
        rename(k): v for k, v in missing.items()}
    assert pipeline.export_step(where).out == [("筆存下了檔案", file_rows)]
    words = {k: w for k, w, _n, _o in body["labels"]}
    # a kind of lack is keyed by its mark in 缺什么 (stable), shown in the 11-23 words
    assert words["file"] == "已存檔" and words["连接失败"] == "連線失敗" and words["缺图片"] == "圖片未取得"
    # a row missing two things is found under each, by key or by word (the old word too)
    assert page(where, "export", label="缺图片")["rows"][0]["main"] == "張岳印"
    assert page(where, "export", label="圖片沒抓到")["label_on"] == ["缺图片"]


def test_an_export_row_reads_as_the_design_draws_it_and_its_title_is_never_converted(where):
    vita = page(where, "export", q="vita.aspx")["rows"][0]
    assert vita["main"] == "張岳印" and vita["dot"] == "bad" and vita["also"] == ["圖片未取得"]
    form = page(where, "export", q="form.pdf")["rows"][0]
    dl = {k: (v, o) for k, v, o in form["dl"]}
    assert dl["來路"][1]["from"] == made.NOTICE_URL and dl["網站"][0] == made.NAMES[SITE]
    go = {t: h for _i, t, h, _tip in form["go"]}
    assert go["原樣"].startswith("/check?url=https%3A%2F%2Fschool.example%2Ffiles%2Fform.pdf")
    assert go["檔案"].startswith("/file?folder=archive-export%2Fsites%2Fschool.example-20261115-000000&path=files")
    assert form["raw"]["标题"] == "停修申請表"


def test_an_export_page_never_parses_a_manifest_again(where, monkeypatch):
    """Review 11-23 #16: the rows are kept compressed beside the index; a page (open, 更多, a facet, a keystroke) reads
    them back whole without opening a manifest."""
    page(where, "export", per=0)
    manifests = {}
    for s in pipeline.library_facts(where.library)["sites"]:
        path = pipeline.folder_of(where.root, s["export"]) / "manifest.json"
        manifests[s["site"]] = json.loads(path.read_text(encoding="utf-8"))["项目"]
    real = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "manifest.json":
            raise AssertionError("a manifest was read for a page")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    every = page(where, "export", per=200)
    assert every["matched"] == len(every["rows"]) == sum(map(len, manifests.values()))
    for row in every["rows"]:
        site, n = row["id"].rsplit("#", 1)
        assert row["raw"] == manifests[site][int(n) - 1]
    assert page(where, "export", q="vita.aspx")["rows"][0]["main"] == "張岳印"


def test_the_programs_words_are_converted_once_each(where, monkeypatch):
    """Review 11-23 #15: 类型, 分类, 怎么来的, 缺什么, a reason are a few dozen words over every row; each is converted
    once (the converter is pure Python), never a school's text."""
    calls, real = [], pipeline.tw
    monkeypatch.setattr(pipeline, "tw", lambda text: (calls.append(text), real(text))[1])
    raw.tw.cache_clear()
    raw.lacks.cache_clear()
    try:
        page(where, "export", per=0)
        page(where, "totext", per=0)
        twice = sorted({c for c in calls if calls.count(c) > 1})
        assert calls and not twice and "張岳印" not in calls, twice
        assert raw.tw("网页") == "網頁" and calls.count("网页") == 1
    finally:
        raw.tw.cache_clear()
        raw.lacks.cache_clear()


def test_the_export_site_and_miss_presets(where):
    board = page(where, "export", site=BOARD)
    assert board["matched"] == 1 and board["rows"][0]["main"].startswith("115-1 停修公告")
    missed = page(where, "export", miss=1)
    assert missed["matched"] == sum(n for k, n in by_key(page(where, "export")).items() if k != "file") - 1
    assert all(r["lk"] != "file" for r in missed["rows"])


# ---------------------------------------------------------------- 轉文字


def test_the_to_text_rows_count_what_the_step_counts_and_a_site_is_its_key(where):
    body = page(where, "totext")
    step = pipeline.totext_step(where)
    got = by_key(body)
    assert step.out == [("個轉成文字", got["ok"])] and ("轉換失敗", got["failed"]) in [(rename(w), n) for w, n in step.lost]
    words = {k: w for k, w, _n, _o in body["labels"]}
    assert words["ok"] == "已轉文字" and words["empty"] == "無文字可讀" and words["failed"] == "轉換失敗"
    # the accounting's prefix (the site with / as _) is filtered by the site's key (the design matched nothing)
    board = page(where, "totext", site=BOARD)
    assert board["matched"] == 2 and board["site_name"] == made.NAMES[BOARD]
    failed = page(where, "totext", site=BOARD, miss=1)["rows"][0]
    assert failed["dot"] == "bad" and failed["label"] == "轉換失敗"
    dl = {k: v for k, v, _o in failed["dl"]}
    assert dl["原因"] == "LibreOffice 超時（120 秒）"
    scan = page(where, "totext", label="empty")["rows"][0]
    chips = next(o["chips"] for k, _v, o in scan["dl"] if k == "頁")
    assert ["OCR 2", "用 OCR 認字的頁"] in chips


# ---------------------------------------------------------------- 建庫


def test_the_build_rows_are_its_documents_and_merges_and_the_counted_only_say_so(where):
    body = page(where, "build")
    labels = {k: (w, n, only) for k, w, n, only in body["labels"]}
    conn = sqlite3.connect(where.library)
    kinds = dict(conn.execute("SELECT kind_class, count(*) FROM docs GROUP BY 1").fetchall())
    conn.close()
    assert {k[4:]: n for k, (_w, n, _o) in labels.items() if k.startswith("doc:")} == kinds
    assert labels["doc:notice"][0] == "文件：公告" and labels["alias:copy"][:2] == ("重複內容，已合併", 1)
    # what the build only counted: a count, flagged, and no rows behind it
    pager = labels["left:网页(列表翻页)"]  # keyed by the build's own key, shown in the 11-23 words
    assert pager == ("公告列表分頁", 7, True) and "附件(副本)" not in json.dumps(body["labels"], ensure_ascii=False)
    only = page(where, "build", label="公告列表分頁")
    assert only["rows"] == [] and only["matched"] == 0 and "只記了這一類的數量（7 筆）" in only["note"]
    # with a site picked the counts without rows are left out (they are the whole school's)
    assert not any(o for *_x, o in page(where, "build", site=SITE)["labels"])
    merged = page(where, "build", miss=1)
    assert merged["matched"] == 1 and merged["rows"][0]["lk"] == "alias:copy"
    out = page(where, "build", label="doc:*")
    assert out["matched"] == sum(kinds.values()) and body["out"] == {"key": "doc:*", "word": "文件"}


def _spy_library(monkeypatch) -> tuple[list[tuple[str, str]], list[str]]:
    """The library's columns the drawer reads ((table, column)) and its statements."""
    read, said, real = [], [], pipeline.open_ro

    def spied(path):
        conn = real(path)

        def seen(action, table, column, _db, _src):
            if action == sqlite3.SQLITE_READ:
                read.append((table, column))
            return sqlite3.SQLITE_OK

        conn.set_authorizer(seen)
        conn.set_trace_callback(said.append)
        return conn

    monkeypatch.setattr(raw, "open_ro", spied)
    return read, said


def test_the_build_index_never_reads_the_text_for_the_night_views_counts(where, monkeypatch):
    """Review 11-23 #19: docs.site lies after docs.text, so reading it walks every document's text. The index reads
    the columns before the text; with no index on site in the library, the sites are read only when a page needs them
    (a page of rows and its 網站 facet, a site picked), never for the night view's counts (per=0)."""
    want = _two_sites(where)
    read, said = _spy_library(monkeypatch)
    counts = page(where, "build", per=0)
    assert counts["labels"] and ("docs", "site") not in read and ("docs", "text") not in read
    assert ("docs", "title") in read
    assert [c for c, _t, _v in page(where, "build", label="doc:notice", per=0)["facets"]] == ["kind"]
    assert not [s for s in said if s.startswith("SELECT doc_id, site FROM docs")]
    rows = page(where, "build")  # a page of rows: the sites read once, attached to this library's index
    assert {v: n for col, _t, vals in rows["facets"] if col == "site" for v, _w, n in vals} == want
    assert page(where, "build", site=BOARD)["matched"] == want[BOARD] == 1
    assert page(where, "build", site=SITE, label="doc:*")["matched"] == want[SITE] - 1  # its merged record apart
    assert len([s for s in said if s.startswith("SELECT doc_id, site FROM docs")]) == 1  # once a library version


def _two_sites(where: Where) -> dict[str, int]:
    """One document of the made-up library moved to the second site; the rows the drawer lists per site (a merged
    record counts for the site of the document it was merged into)."""
    conn = sqlite3.connect(where.library)
    try:
        conn.execute("UPDATE docs SET site = ? WHERE doc_id = (SELECT min(doc_id) FROM docs WHERE doc_id NOT IN"
                     " (SELECT doc_id FROM doc_alias))", (BOARD,))
        conn.commit()
        want = dict(conn.execute("SELECT site, count(*) FROM docs GROUP BY site").fetchall())
        for (s,) in conn.execute("SELECT d.site FROM doc_alias a JOIN docs d ON d.doc_id = a.doc_id"):
            want[s] = want.get(s, 0) + 1
    finally:
        conn.close()
    return want


def test_with_an_index_on_site_the_build_index_reads_the_sites_from_it(where, monkeypatch):
    want = _two_sites(where)
    conn = sqlite3.connect(where.library)
    conn.execute("CREATE INDEX docs_site ON docs(site, kind_class)")  # what the build may write (a content change)
    conn.commit()
    plan = " ".join(r[3] for r in conn.execute('EXPLAIN QUERY PLAN SELECT rowid, site FROM docs INDEXED BY "docs_site"'))
    conn.close()
    assert "COVERING INDEX docs_site" in plan  # the index alone, never a document's text
    raw.forget()
    _read, said = _spy_library(monkeypatch)
    counts = page(where, "build", per=0)
    assert [s for s in said if 'INDEXED BY "docs_site"' in s]
    assert {v: n for col, _t, vals in counts["facets"] if col == "site" for v, _w, n in vals} == want
    listed = [r["raw"]["site"] for r in page(where, "build", per=200)["rows"]]
    assert listed == sorted(listed) and set(listed) == {SITE, BOARD}  # in site order, as the design lists them
    assert page(where, "build", site=BOARD)["matched"] == 1
    assert not [s for s in said if s.startswith("SELECT doc_id, site FROM docs")]  # no reading of every text


def test_a_document_row_links_to_its_trace_and_shows_its_opening(where):
    row = page(where, "build", q="news.aspx?id=7", label="doc:notice")["rows"][0]
    go = {t: h for _i, t, h, _tip in row["go"]}
    assert go["文件頁"] == "/trace?ref=" + made.doc_id(made.NOTICE_URL)
    dl = {k: v for k, v, _o in row["dl"]}
    assert dl["開頭"].startswith("115學年度第1學期停修申請") and dl["單位"] == "教務處"
    assert ["", "2 小段", "檢索時比對的單位"] in row["meta"]


# ---------------------------------------------------------------- 換庫


def _changes(cap: bool = False) -> dict:
    new = [{"id": "dn1", "title": "新公告", "url": "https://school.example/n1", "kind": "notice", "date": "2026-11-22",
            "chars": 120}]
    left = [{"id": "dl1", "title": "舊副本", "url": "https://school.example/board/c1", "kind": "page", "chars": 40}]
    return {"state": "ready", "keys": ["a", "b"], "night": "2026-11-22",
            "new": {"swapped_at": "2026-11-23T03:10:00+08:00"},
            "totals": {"new": 3 if cap else 1, "changed": 1, "left": 1},
            "by_reason": {"copy": 1},
            "reasons": {"copy": {"label": "併成副本", "tip": "別處有一模一樣的一份", "kind": "ok", "fix": ""}},
            "sites": [{"site": SITE, "name": made.NAMES[SITE], "lists": {
                "new": new, "changed": [{"id": "dc1", "title": "改過", "url": "https://school.example/c", "kind":
                                         "attachment", "what": ["標題"], "chars": 10}], "left": {}}},
                      {"site": BOARD, "name": made.NAMES[BOARD], "lists": {"new": [], "changed": [],
                                                                            "left": {"copy": left}}}]}


def test_the_swap_rows_are_the_last_comparison_by_site_key(where):
    body = raw.page(where, "swap", {}, changes=_changes())
    assert by_key(body) == {"new": 1, "changed": 1, "left:copy": 1} and body["run_day"] == "2026-11-23"
    assert body["night"] == "2026-11-22"
    words = {k: w for k, w, _n, _o in body["labels"]}
    assert words["left:copy"] == "併為副本"
    board = raw.page(where, "swap", {"site": BOARD}, changes=_changes())
    assert board["matched"] == 1 and board["rows"][0].get("dot", "") == "" and board["site_name"] == made.NAMES[BOARD]
    # 只看沒通過的: the documents that left, not the changed ones
    assert [r["lk"] for r in raw.page(where, "swap", {"miss": "1"}, changes=_changes())["rows"]] == ["left:copy"]
    new = raw.page(where, "swap", {"label": "new"}, changes=_changes())["rows"][0]
    assert new["dot"] == "in" and {t: h for _i, t, h, _x in new["go"]}["文件頁"] == "/trace?ref=dn1"


def test_a_capped_comparison_says_how_many_it_lists(where):
    body = raw.page(where, "swap", {"label": "new"}, changes=_changes(cap=True))
    assert by_key(raw.page(where, "swap", {}, changes=_changes(cap=True)))["new"] == 3
    assert body["matched"] == 1 and "共 3 份，這裡列 1 份" in body["note"]


def test_a_comparison_not_ready_is_said(where):
    got = raw.page(where, "swap", {}, changes={"state": "computing", "words": "計算中"})
    assert got["state"] == "computing" and got["rows"] == []


# ---------------------------------------------------------------- the route and the layer


def test_the_api_answers_a_known_step_and_refuses_another(where):
    status, body = raw.api(where, {"step": "nowhere"})
    assert status == 404 and "error" in body
    status, body = raw.api(where, {"step": "totext", "per": "1"})
    assert status == 200 and body["step"] == "totext"
    assert raw.ROUTES_TO_WIRE[0]["path"] == "/api/raw" and raw.ROUTES_TO_WIRE[0]["handler"] == "api"


DRAWERS_JS = Path(design.__file__).parent / "theme" / "design" / "drawers.js"
# a stand-in document for drawers.js in node: every element answers any query with one element kept per selector, a box
# of the night view answers with its own parts; fetch answers from RESP by step (per=0: the counts only)
STUB = r"""
const made = [], urls = [];
class El { constructor(tag) { this.tag = tag; this.dataset = {}; this.style = {}; this.kept = {}; this.innerHTML = '';
    this.classList = { list: [], add(c) { this.list.push(c); }, remove() {}, toggle() {} }; }
  setAttribute() {} appendChild(c) { return c; } before() {} after() {} remove() {} focus() {} setSelectionRange() {}
  addEventListener() {} querySelector(s) { return this.q ? this.q(s) : (this.kept[s] = this.kept[s] || new El(s)); }
  querySelectorAll(s) { return this.qa ? this.qa(s) : []; } }
global.window = globalThis;
global.addEventListener = () => {};
global.document = { body: new El('body'), createElement: t => { const e = new El(t); made.push(e); return e; } };
const RESP = {};
global.fetch = async url => { urls.push(url); const p = new URL(url, 'http://panel').searchParams;
  const r = RESP[p.get('per') === '0' ? p.get('step') + ':0' : p.get('key') || p.get('step')];
  return { ok: true, json: async () => typeof r === 'function' ? r(p) : r }; };
const tick = () => new Promise(r => setTimeout(r, 20));
const li = (key, word) => { const e = new El('li'); if (key) e.dataset.key = key;
  e.q = s => (s === 'span' ? { textContent: word } : null); return e; };
const box = items => { const b = new El('div'), x = new El('.sd-x');
  b.q = s => (s === '.sd-h b' ? { textContent: '這一步' } : s === '.sd-h .sd-x' ? x : s === '.sd-h' ? new El('h') : null);
  b.qa = s => (s === '.sd-lost li' ? items : []); return b; };
const lastLabel = () => { const p = new URL(urls[urls.length - 1], 'http://panel').searchParams;
  return [p.get('label'), p.get('alt'), p.get('page')]; };
"""


def _drawers(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = DRAWERS_JS.read_text(encoding="utf-8")
    src = STUB + js + "\n(async () => {\n" + script + "\n})().catch(e => { console.error(e); process.exit(1); });"
    out = subprocess.run([node, "-e", src], check=True, capture_output=True, text=True, timeout=60).stdout
    return json.loads(out)


def test_a_lost_rows_key_is_looked_up_first_then_its_words():
    """RD-13 in the browser: RAWVIEW wires each .sd-lost row by its data-key, then by its words, then another name
    the server knows (aliases), then a bucket; the click asks for that label."""
    out = _drawers("""
RESP['build:0'] = { state: 'ready', labels: [['doc:notice', '文件：公告', 1, false], ['alias:copy', '重複內容，已合併', 1, false],
  ['left:网页(列表翻页)', '公告列表分頁', 7, true]], aliases: { 'left:copy': ['alias:copy'] } };
RESP['crawl:0'] = { state: 'ready', labels: [['review:thin', '待確認：內容過少', 1, false], ['failed', '連線失敗', 2, false]],
  buckets: [['review', '待確認', 1], ['failed', '連線失敗', 2]] };
RESP['swap:0'] = { state: 'ready', labels: [['left:copy', '併為副本', 1, false]], aliases: { copy: ['left:copy'] } };
RESP.build = RESP.crawl = RESP.swap = { state: 'ready', labels: [], rows: [], matched: 0 };
const got = {};
for (const [step, items] of [
  ['build', [li('left:网页(列表翻页)', '公告列表分頁'), li('left:copy', '重複內容，已合併'), li('', '文件：公告'),
             li('left:nowhere', '公告列表分頁'), li('left:none', '沒有這種')]],
  ['crawl', [li('review', '待確認：內容過少'), li('failed', '連線失敗'), li('gone', '校方已移除')]],
  ['swap', [li('copy', '併為副本')]]]) {
  window.RAWVIEW(box(items), step, { run: '2026-11-23' }); await tick();
  got[step] = [];
  for (const it of items) { if (!it.onclick) { got[step].push(null); continue; } it.onclick(); await tick(); got[step].push(lastLabel()[0]); }
}
console.log(JSON.stringify(got));
""")
    assert out["build"] == ["left:网页(列表翻页)", "alias:copy", "doc:notice", "left:网页(列表翻页)", None]
    assert out["crawl"] == ["review:thin", "failed", None]  # 校方已移除: no rows behind it, not wired
    assert out["swap"] == ["left:copy"]


def test_a_label_naming_several_kinds_is_asked_for_as_sent_and_a_key_with_its_words():
    """RD-02: 更多 asks for the same set the first page counted (doc:*), no single kind's chip pressed; a label the
    server resolves to one key becomes that key. The night view's preset {label: words, key} asks key first, words as
    alt."""
    out = _drawers("""
RESP.build = p => ({ state: 'ready', per: 2, page: +p.get('page') || 1, matched: 5, rows: [{ id: 'a' }, { id: 'b' }],
  labels: [['doc:attachment', '文件：附件', 4, false], ['doc:notice', '文件：公告', 1, false]],
  filters: { label: p.get('label') === 'doc:*' ? ['doc:attachment', 'doc:notice'] : ['doc:notice'] } });
RESP.crawl = { state: 'ready', labels: [], rows: [], matched: 0, filters: { label: [] } };
const dr = made.find(e => e.tag === 'aside');
window.RAWSHOW('build', { label: 'doc:*' }); await tick();
const pressed = /aria-pressed='true'/.test(dr.innerHTML);
dr.querySelector('.rv-next').onclick(); await tick();
const more = lastLabel();
window.RAWSHOW('build', { label: '文件：公告' }); await tick();
dr.querySelector('.rv-next').onclick(); await tick();
const one = lastLabel();
window.RAWSHOW('crawl', { label: '待確認：內容過少', key: 'review', site: 'school.example' }); await tick();
console.log(JSON.stringify({ pressed, more, one, keyed: lastLabel() }));
""")
    assert out["pressed"] is False and out["more"] == ["doc:*", None, "2"]
    assert out["one"] == ["doc:notice", None, "2"]
    assert out["keyed"] == ["review", "待確認：內容過少", "1"]


def test_an_older_night_is_told_by_its_run_day_whichever_way_it_is_named():
    """RD-07: the run day (n.run) is compared with the reading's run day; the old key (n.night, the day before) is
    turned into its run day first. The night before by its run day is older and says so."""
    out = _drawers("""
RESP.crawl = { state: 'ready', run_day: '2026-11-23', night: '2026-11-22', as_of: '11-23 08:00', labels: [], rows: [],
  matched: 0 };
const dr = made.find(e => e.tag === 'aside'), said = {};
for (const [name, n] of [['run', { run: '2026-11-23' }], ['old_key', { night: '2026-11-22' }], ['day_before', { run: '2026-11-22' }],
                         ['string', '2026-11-22'], ['none', null]]) {
  window.RAWSHOW('crawl', {}, n); await tick(); said[name] = dr.innerHTML.includes('這一晚的逐筆紀錄沒留下');
}
console.log(JSON.stringify(said));
""")
    assert out == {"run": False, "old_key": False, "day_before": True, "string": True, "none": False}


def test_a_file_on_the_lab_pc_only_is_not_drawn_as_missing():
    """FD-08: the D: backup's chip says the panel does not mount D:, never 「不在」 struck through."""
    out = _drawers("""
RESP.crawl = { order: ['crawl'], run_day: '2026-11-23', files: { backup_nightly: { name: 'crawler-nightly/', what: '帳本備份' },
  update_status: { name: 'update-status.json', what: '' } }, steps: { crawl: { name: '爬取', prog: '', in: [],
  out: ['backup_nightly', 'update_status'] } }, info: { backup_nightly: { name: 'crawler-nightly/', host: true,
  chip: '檢視台沒掛 D 槽', absent: '檢視台沒掛 D 槽：備份在實驗室電腦的 D 槽' }, update_status: { name: 'update-status.json',
  exists: false, absent: '還沒跑' } } };
const b = box([]);
window.FILEVIEW(b, 'crawl', { run: '2026-11-23' }); await tick();
const el = made.filter(e => e.className === 'fv').pop();
console.log(JSON.stringify({ html: el.innerHTML }));
""")
    chips = {k: c for c, k in re.findall(r"<button class='fv-c([^']*)' type='button' data-k='([a-z_]+)'", out["html"])}
    assert chips == {"backup_nightly": " host", "update_status": " gone"}
    assert "<span>檢視台沒掛 D 槽</span>" in out["html"] and "<span>不在</span>" in out["html"]


def test_the_drawers_layer_keeps_the_designs_entry_points_and_fetches_instead_of_baking():
    html = design.layer("drawers")
    for name in ("window.RAWSHOW=", "window.RAWVIEW=", "window.FILEOPEN=", "window.FILEVIEW=", "/api/raw?", "/api/file"):
        assert name in html
    for css in (".rvd{", ".rv-pills", ".fv-c{", ".jv-cols", ".rv-range", ".rv-next"):
        assert css in html
    # the design's leftovers are gone: no sample badge, no baked rows, no absolute tunnel address
    assert "window.RAW=" not in html and "window.RAW[" not in html and "127.0.0.1" not in html and "樣本" not in html
    for words in ("全部欄位", "篩選", "只看沒通過的", "看帳本裡每個網址", "金鑰：只在請求標頭裡用", "已改名成 aca.db", "接著讀"):
        assert words in html
