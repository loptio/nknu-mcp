"""The JS sites' handoff, item ⑤ (research/jssites_2026-11-17/handoff_crawler.md; plan
docs/superpowers/plans/2026-11-17-jsfetch-integration.md task 4): a page the browser opened is exported with the look
jsfetch.for_export gives (its rendered record, 来源 「浏览器显示版」, 抓取时间 that look's); a page no look of which was
complete is exported as the school answered it, 缺什么 saying why; a page with no look, or an archive with no js_render,
as before. Made-up pages."""

from __future__ import annotations

from nknu.archive import export, jsfetch
from tests.archive.test_export_1027 import BASE
from tests.archive.test_export_prev_1116 import _content
from tests.archive.test_export_step3_1108 import _with

ABOUT = BASE + "about.aspx"
SHOWN = "<html><head><title>教務處簡介</title></head><body><div id=\"app\">瀏覽器畫出來的簡介全文</div></body></html>"
LOOK = {"warc": "warc/rendered.warc.gz", "warc_offset": 3, "at": "2026-11-17T10:00:00+08:00"}


def _export(tmp_path, monkeypatch, gives):
    monkeypatch.setattr(jsfetch, "for_export", lambda fr, uid: gives(fr, uid))
    monkeypatch.setattr(jsfetch, "read_rendered", lambda root, w, o: (ABOUT, SHOWN))

    def build(arch):
        jsfetch.ensure_table(arch.f)
        arch.f.x("INSERT INTO js_render(url_id, run, at) VALUES (?, 1, ?)", (arch.ids["about"], LOOK["at"]))

    arch, _got, out, rows, _readme = _with(tmp_path, build)
    return arch, out, rows


def test_a_page_with_a_look_is_exported_as_the_browser_showed_it(tmp_path, monkeypatch):
    holder = {}

    def gives(fr, uid):
        return (LOOK, None) if uid == holder.get("id") else (None, None)

    import tests.archive.test_export_1027 as fx

    real = fx.make_archive

    def make(path):
        a = real(path)
        holder["id"] = a.ids["about"]
        return a

    monkeypatch.setattr(fx, "make_archive", make)
    import tests.archive.test_export_step3_1108 as st

    monkeypatch.setattr(st, "make_archive", make)
    _arch, out, rows = _export(tmp_path, monkeypatch, gives)
    r = rows[ABOUT]
    assert r["来源"] == export.ORIGIN_RENDERED and "瀏覽器畫出來的簡介全文" in _content(out, r).decode("utf-8")
    assert r["抓取时间"].startswith("2026-11-17T10:00")


def test_a_page_no_look_of_which_was_complete_says_why(tmp_path, monkeypatch):
    holder = {}
    import tests.archive.test_export_1027 as fx
    import tests.archive.test_export_step3_1108 as st

    real = fx.make_archive

    def make(path):
        a = real(path)
        holder["id"] = a.ids["about"]
        return a

    monkeypatch.setattr(fx, "make_archive", make)
    monkeypatch.setattr(st, "make_archive", make)
    _arch, out, rows = _export(tmp_path, monkeypatch,
                               lambda fr, uid: (None, "post_body https://x/api") if uid == holder["id"] else (None, None))
    r = rows[ABOUT]
    assert r["来源"] == "" and "内文区是空的（浏览器没开完整：post_body https://x/api）" in r["缺什么"]
    assert "瀏覽器畫出來的簡介全文" not in _content(out, r).decode("utf-8")


def test_the_basis_counts_the_looks_only_where_there_are_some():
    """11-17 evening (the nightly JS step, plan task 6): a JS list's own answer never changes (its rows come by script),
    so the export's 导出依据 counts the look each page is exported with: a new look exports the site again. A site with
    no look has the 存档内容 it had before (no site exported again for this change)."""
    import dataclasses
    import tempfile
    import time
    from pathlib import Path

    from tests.archive.test_export_1027 import SITE, make_archive

    with tempfile.TemporaryDirectory() as d:
        arch = make_archive(Path(d) / "archive")
        conn = export.connect(arch.root / "frontier.db")
        try:
            snap = export.read_snapshot(conn, SITE, time.time())
        finally:
            conn.close()
        base = export.snapshot_digest(snap)
        assert export.snapshot_digest(dataclasses.replace(snap, looks={})) == base  # none: as before
        one = export.snapshot_digest(dataclasses.replace(snap, looks={7: ("warc/r.warc.gz", 3)}))
        other = export.snapshot_digest(dataclasses.replace(snap, looks={7: ("warc/r.warc.gz", 99)}))
        assert len({base, one, other}) == 3
