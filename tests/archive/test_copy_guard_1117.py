"""11-17 (爬虫judge, under the owner's 11-17 delegation: 「上一份用了旧存档副本、这一份没用上就停下来说明」, its terms: only
a copy that silently goes; a page the school mended, now got of this round, and a notice the two nights' rule calls
deleted pass; only that site is held, its reason in the status file; the night's export keeps it owed): an export
whose folder before filled a row from the 10-24 archive's copy and that now gives that row neither the copy, nor a
good answer of this round's own, nor the school's deletion, is held — not made the site's current folder. The 11-16
go-live re-exported 711 such rows without their copies. Made-up rows."""

from __future__ import annotations

import csv
from pathlib import Path

from nknu.archive import export

OLD = export.ORIGIN_OLD


def _row(url: str, **cols) -> dict:
    return {"类型": "附件", "网址": url, "按钮编号": "", "本地档案": "", "来源": "", "分类": "抓到了", "结果": "抓到了",
            "缺什么": "", **cols}


def _manifest(folder: Path, rows: list[dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0])
    with (folder / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return folder


def _check(tmp_path, before: dict, now: dict | None):
    a = _manifest(tmp_path / "a", [before, _row("https://x/keep.pdf", 本地档案="files/k.pdf")])
    b = _manifest(tmp_path / "b", ([now] if now else []) + [_row("https://x/keep.pdf", 本地档案="files/k.pdf")])
    return export.copies_dropped(a, b)


COPY = _row("https://x/form.pdf", 本地档案="files/f.pdf", 来源=OLD, 分类="连接失败", 结果="试了 3 次还是失败")


def test_the_copy_kept_passes(tmp_path):
    assert _check(tmp_path, COPY, dict(COPY, 本地档案="files/g.pdf")) == []


def test_a_page_the_school_mended_passes(tmp_path):
    assert _check(tmp_path, COPY, _row("https://x/form.pdf", 本地档案="files/f.pdf")) == []


def test_a_row_the_school_deleted_passes(tmp_path):
    gone = _row("https://x/form.pdf", 分类="学校那边已经不存在", 缺什么=f"{export.SCHOOL_DELETED}（两晚都说不存在）")
    assert _check(tmp_path, COPY, gone) == []


def test_a_notice_the_school_sent_elsewhere_passes(tmp_path):
    # 爬虫judge 11-17: never covered with its old copy (export, the review's F1) and never kept by the content stage
    moved = _row("https://x/form.pdf", 类型="公告", 分类="转址", 结果="转到首页", 缺什么=export.NOTICE_MOVED)
    assert _check(tmp_path, COPY, moved) == []


def test_a_copy_that_went_with_no_file_is_held(tmp_path):
    now = _row("https://x/form.pdf", 分类="连接失败", 结果="试了 3 次还是失败")
    assert _check(tmp_path, COPY, now) == [("https://x/form.pdf", "连接失败：试了 3 次还是失败")]


def test_a_copy_replaced_by_this_rounds_thin_page_is_held(tmp_path):
    now = _row("https://x/form.pdf", 本地档案="pages/f.html", 分类="要人看", 结果="页面几乎是空的")
    assert _check(tmp_path, COPY, now) == [("https://x/form.pdf", "要人看：页面几乎是空的")]


def test_a_row_that_left_the_export_is_held(tmp_path):
    assert _check(tmp_path, COPY, None) == [("https://x/form.pdf", "这一行不在这一份导出里")]


def test_no_folder_before_holds_nothing(tmp_path):
    b = _manifest(tmp_path / "b", [_row("https://x/a.pdf")])
    assert export.copies_dropped(None, b) == []


# ---------------------------------------------------------------- the export service holds it


def test_the_export_service_holds_an_export_that_drops_a_copy(tmp_path):
    import shutil

    import pytest

    from tests.archive.test_export_1027 import SITE, make_archive
    from tests.archive.test_export_review_1108 import SERVICE, _broken_copy, _current, _service

    if not SERVICE.exists() or shutil.which("bash") is None:
        pytest.skip("the lab tools are on the disk and in code.tar, not in git")
    arch = make_archive(tmp_path / "archive")
    copies = _broken_copy(tmp_path / "old copies", arch)
    code, said = _service(tmp_path, arch, OLD_COPIES=str(copies))
    first = _current(tmp_path)
    results = tmp_path / "results.tsv"
    code, said = _service(tmp_path, arch, OLD_COPIES="", FORCE="1", EXPORT_RESULTS=str(results))
    assert code == 0 and "拦下" in said and _current(tmp_path) == first, said
    held = list((tmp_path / "sites").glob("*.held"))
    assert len(held) == 1 and (held[0] / "held.txt").is_file()
    site, word, why = results.read_text(encoding="utf-8").strip().split("\t")
    assert (site, word) == (SITE, "held") and "副本" in why
    # a person who means it: ALLOW_COPY_DROP=1
    code, said = _service(tmp_path, arch, OLD_COPIES="", FORCE="1", ALLOW_COPY_DROP="1")
    assert code == 0 and _current(tmp_path) != first, said
    assert not list((tmp_path / "sites").glob("*.held"))  # the held one went with the export that replaced it


def test_seen_keeps_a_held_site_owed_and_says_why(tmp_path):
    import json

    from nknu.archive import update
    from nknu.archive.frontier import Frontier
    from tests.archive.test_nightly_export_1116 import _status

    Frontier(tmp_path / "frontier.db").close()
    _status(tmp_path)
    update.owe(tmp_path, ["a.nknu.edu.tw"])  # what the night's review does with a changed site
    results = tmp_path / "results.tsv"
    results.write_text("a.nknu.edu.tw\theld\t1 行用 10-24 旧存档副本补上的这次没补\n", encoding="utf-8")
    assert update.main(["--dir", str(tmp_path), "seen", "--results", str(results), "--out", str(tmp_path / "seen.tsv")]) == 0
    status = json.loads((tmp_path / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["export"]["held"] == {"a.nknu.edu.tw": "1 行用 10-24 旧存档副本补上的这次没补"}
    assert "a.nknu.edu.tw" in status["export"]["owed"] and "a.nknu.edu.tw" in status["export"]["failed"]


def test_seen_tsv_says_held_and_keeps_the_previous_time(tmp_path):
    from nknu.archive import update

    status = {"night_started": "2026-11-17T01:00:00+08:00", "sites_seen": {"a.nknu.edu.tw": "2026-11-17T01:20:00+08:00"}}
    rows = update.seen_rows(status, {"a.nknu.edu.tw": "held"}, {"a.nknu.edu.tw": "2026-11-16 01:20"})
    assert rows == [("a.nknu.edu.tw", "2026-11-16 01:20", "导出拦下")]


def test_the_dashboard_says_which_sites_were_held_and_why():
    from nknu.archive import progress

    export = {"failed": ["a.nknu.edu.tw"], "held": {"a.nknu.edu.tw": "1 行用 10-24 旧存档副本补上的这次没补"}}
    mark = progress.export_mark(export)
    assert mark["color"] == "red" and mark["words"] == "定期更新导出拦下 1 站"
    assert "a.nknu.edu.tw（1 行用 10-24 旧存档副本补上的这次没补）" in mark["tip"]


def test_a_site_a_person_holds_is_not_exported_and_says_why(tmp_path):
    """11-17 (爬虫judge: 9 is away tonight and the CV site's export of 57,943 rows must not become current before the
    owner decides): a site key in the export folder's export-hold.txt (site<TAB>why; # a comment) is not exported, its
    result held with that why (the night keeps it owed, as the copy guard's), its current folder untouched."""
    import shutil

    import pytest

    from tests.archive.test_export_1027 import SITE, make_archive
    from tests.archive.test_export_review_1108 import SERVICE, _current, _service

    if not SERVICE.exists() or shutil.which("bash") is None:
        pytest.skip("the lab tools are on the disk and in code.tar, not in git")
    arch = make_archive(tmp_path / "archive")
    code, said = _service(tmp_path, arch)
    first = _current(tmp_path)
    (tmp_path / "sites" / "export-hold.txt").write_text(f"# 人工暂缓\n{SITE}\t等用户决定\n", encoding="utf-8")
    results = tmp_path / "results.tsv"
    code, said = _service(tmp_path, arch, FORCE="1", EXPORT_RESULTS=str(results))
    assert code == 0 and "暂缓" in said and _current(tmp_path) == first, said
    assert not list((tmp_path / "sites").glob("*.partial")) and len(list((tmp_path / "sites").glob("c.nknu*"))) == 1
    site, word, why = results.read_text(encoding="utf-8").strip().split("\t")
    assert (site, word) == (SITE, "held") and "等用户决定" in why
