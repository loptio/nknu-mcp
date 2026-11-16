"""The export batch, T1 (plan docs/superpowers/plans/2026-11-16-export-batch.md; design 2026-11-16-periodic-update-
design.md §六 1–2, §7.1; 2026-11-15 update.md §5, §6.1): a row fetched again whose answer this time is no good, and
that had a good one before (url_version), is exported with the latest good one, said in 来源 and 备注; its 结果, 分类
and 原因 stay this time's. A row good this time, or never good, is exported as before. Made-up pages only."""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from nknu.archive import export, outcome
from tests.archive.test_export_1027 import BASE, DETAIL2, RULE_PDF, make_archive
from tests.archive.test_export_step3_1108 import _with

PREV = export.ORIGIN_PREV
LATER = "2026-11-16T17:00:00+00:00"  # this time's fetch (Taipei 11-17 01:00)
GONE_PAGE = "<html><head><title>404</title></head><body><h1>找不到網頁</h1></body></html>"
EMPTY_TEMPLATE = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>最新消息</h2>
<div id="DataFormView"></div><div id="footer">本網站著作權屬於教務處</div></body></html>"""
NOTICE2 = BASE + "newsview.aspx?nid=2"
RULE = BASE + "uploadfile/rule.pdf"


def _again(arch, name: str, *, body: str | bytes | None = None, ctype: str = "text/html; charset=utf-8",
           status: int = 200, **cols) -> None:
    """Row `name` fetched again: this time's answer written over it as Frontier.finish writes one, which keeps the
    answer it held in url_version first (stage 0a). body None: no answer stored (a failed fetch)."""
    uid = arch.ids[name]
    row = {"state": "done", "reason": None, "status": status, "fetched_at": LATER, "flags": None}
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        url = arch.f.conn.execute("SELECT url FROM url WHERE id=?", (uid,)).fetchone()[0]
        offset, sha = arch.put(f"{name}-again", url, ctype, data)
        row.update(ctype=ctype, bytes=len(data), sha256=sha, warc=arch.rel, warc_offset=offset,
                   sniff="html" if b"<html" in data[:200] else "pdf")
    else:
        row.update(status=None, warc=None, warc_offset=None, sha256=None, bytes=None)
    row.update(cols)
    arch.f.activate(uid)  # the worker takes it (its answer kept), then this time's answer lands on it
    arch.f.finish(uid, **row)


def _row(rows: dict, url: str) -> dict:
    return rows[url]


def _content(out, row: dict) -> bytes:
    return (out / row["本地档案"]).read_bytes()


@pytest.mark.parametrize("case", ["404", "failed", "todo", "thin", "login"])
def test_a_page_whose_answer_this_time_is_no_good_is_exported_with_its_last_good_one(tmp_path, case):
    def build(arch):
        if case == "404":
            _again(arch, "d2", body=GONE_PAGE, status=404)
        elif case == "failed":
            _again(arch, "d2", body=None, state="failed", reason="connect", attempts=3)
        elif case == "todo":
            arch.f.activate(arch.ids["d2"])
            arch.f.x("UPDATE url SET state='pending', reason='chain_restart' WHERE id=?", (arch.ids["d2"],))
        elif case == "thin":
            _again(arch, "d2", body=EMPTY_TEMPLATE, flags="thin")
        else:
            _again(arch, "d2", body=GONE_PAGE, flags="login")

    _arch, _got, out, rows, readme = _with(tmp_path, build)
    r = _row(rows, NOTICE2)
    assert r["本地档案"], r
    assert "新生註冊須知" in _content(out, r).decode("utf-8-sig")
    assert r["来源"] == PREV
    assert r["抓取时间"].startswith("2026-10-27T09:02:03")  # the first fetch's, in Taipei
    assert "上一版补上" in r["备注"]
    assert r["结果"] != outcome.RESULT_LABEL["ok"]  # this time's result stays in sight
    assert "上一次抓到的" in readme


def test_a_file_that_came_back_as_a_web_page_keeps_its_last_good_bytes(tmp_path):
    _arch, _got, out, rows, _readme = _with(tmp_path, lambda arch: _again(arch, "rule", body=GONE_PAGE))
    r = _row(rows, RULE)
    assert _content(out, r) == RULE_PDF
    assert r["来源"] == PREV and r["sha256"] != "" and r["格式"] == "pdf"


def test_a_notice_page_answered_as_an_empty_template_this_time_takes_its_last_good_one(tmp_path):
    """Its result this time may be ok (the views do not read a notice's areas): the export reads them (core design §5
    item 3) and takes the last good answer instead."""
    _arch, _got, out, rows, _readme = _with(tmp_path, lambda arch: _again(arch, "d2", body=EMPTY_TEMPLATE))
    r = _row(rows, NOTICE2)
    assert "新生註冊須知" in _content(out, r).decode("utf-8-sig")
    assert r["来源"] == PREV


def test_a_row_good_this_time_is_exported_with_this_times_answer(tmp_path):
    newer = DETAIL2.replace("新生註冊須知", "新生註冊須知（更新）")
    _arch, _got, out, rows, _readme = _with(tmp_path, lambda arch: _again(arch, "d2", body=newer))
    r = _row(rows, NOTICE2)
    assert "（更新）" in _content(out, r).decode("utf-8-sig")
    assert r["来源"] == "" and "上一版" not in r["备注"]


def test_a_rows_last_good_answer_is_the_latest_of_several(tmp_path):
    second = DETAIL2.replace("新生註冊須知", "新生註冊須知（第二版）")

    def build(arch):
        _again(arch, "d2", body=second)
        _again(arch, "d2", body=GONE_PAGE, status=404, fetched_at="2026-11-17T17:00:00+00:00")

    _arch, _got, out, rows, _readme = _with(tmp_path, build)
    assert "（第二版）" in _content(out, _row(rows, NOTICE2)).decode("utf-8-sig")


def test_a_row_never_good_is_exported_as_before(tmp_path):
    """The broken attachment never connected: nothing kept, no file, its result as it was."""
    _arch, _got, _out, rows, _readme = _with(tmp_path, lambda arch: None)
    r = _row(rows, BASE + "uploadfile/broken.pdf")
    assert r["本地档案"] == "" and r["来源"] == ""


def test_an_archive_without_url_version_exports_as_before(tmp_path):
    arch = make_archive(tmp_path / "a")
    first = export.export_site(arch.root, "c.nknu.edu.tw/aca", tmp_path / "with")
    db = sqlite3.connect(arch.root / "frontier.db")
    db.execute("DROP TABLE url_version")
    db.commit()
    db.close()
    second = export.export_site(arch.root, "c.nknu.edu.tw/aca", tmp_path / "without")
    assert (tmp_path / "with" / "manifest.csv").read_bytes() == (tmp_path / "without" / "manifest.csv").read_bytes()
    assert first.counts == second.counts


ANSWERS = ("good", "404", "failed", "thin", "template")


@pytest.mark.parametrize("seq", list(itertools.product(ANSWERS, repeat=2)))
def test_a_row_once_good_always_has_its_latest_good_file(tmp_path, seq):
    """Invariant (design §六 2): whatever the answers after a good one, the export has a file, the latest good one."""
    versions = {}

    def build(arch):
        for n, what in enumerate(seq, start=1):
            at = f"2026-11-1{n}T17:00:00+00:00"
            if what == "good":
                body = DETAIL2.replace("新生註冊須知", f"新生註冊須知（第{n}版）")
                versions["last"] = f"（第{n}版）"
                _again(arch, "d2", body=body, fetched_at=at)
            elif what == "404":
                _again(arch, "d2", body=GONE_PAGE, status=404, fetched_at=at)
            elif what == "failed":
                _again(arch, "d2", body=None, state="failed", reason="connect", attempts=3, fetched_at=at)
            elif what == "thin":
                _again(arch, "d2", body=EMPTY_TEMPLATE, flags="thin", fetched_at=at)
            else:
                _again(arch, "d2", body=EMPTY_TEMPLATE, fetched_at=at)

    _arch, _got, out, rows, _readme = _with(tmp_path, build)
    text = _content(out, _row(rows, NOTICE2)).decode("utf-8-sig")
    assert "新生註冊須知" in text
    if "last" in versions:
        assert versions["last"] in text
    else:
        assert "（第" not in text  # the first fetch's own page, the only good one


def test_a_list_that_now_says_it_holds_nothing_is_taken_as_it_is(tmp_path):
    """Only a notice detail page falls back when its answer is good but empty (update.md §5 item 3): a list the school
    emptied (「搜尋結果：共0筆」, profiles.NEWS_NO_ROWS) is its answer now, never the old list's rows (the lab's T8
    comparison of 11-16: news.nknu.edu.tw's unit lists)."""
    from tests.archive.test_export_1027 import _add

    url = "https://news.nknu.edu.tw/nknu_news/lastnewslist_dept.aspx?place=WB"
    with_rows = "<html><body><form id='uForm'>搜尋結果：共2筆<table><tr><td>招生公告</td></tr></table></form></body></html>"
    empty = "<html><body><form id='uForm'>搜尋結果：共0筆</form></body></html>"

    def build(arch):
        off, sha = arch.put("lst", url, "text/html; charset=utf-8", with_rows.encode())
        arch.ids["lst"] = _add(arch.f, url=url, host="news.nknu.edu.tw", final_url=url, ctype="text/html",
                               bytes=len(with_rows.encode()), sha256=sha, warc=arch.rel, warc_offset=off,
                               sniff="html", page_type="t:ok")
        _again(arch, "lst", body=empty)

    _arch, _got, out, rows, _readme = _with(tmp_path, build)
    r = rows[url]
    assert r["来源"] == "" and "共0筆" in _content(out, r).decode("utf-8-sig")
