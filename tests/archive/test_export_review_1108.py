"""The 11-08 review of the export's step-3 additions (test_export_step3_1108, test_export_dates_1108; each fix is
Claude's): what an export is made of (manifest.json's 导出依据) and --skip-same, by which the export service skips a
site only when exporting it again would change nothing (a person's mark, a hand edit, --old-copies added later, a
newer program all change it with no fetch); the service itself (research/crawler_lab_2026-11-04/export_sites.sh, run
with bash on the fixture archive); a file of the previous export hard-linked only when its bytes hash right; an out
folder inside a folder the export only reads; names percent-encoded twice; the old copies of rows the id probe calls
gone or that wait for the lab, and the copies a row no longer needs; where a filled redirect's file is the answer of;
tecs's notice areas in the c.nknu content area; the caveat on 最后修改时间. Built on the small made-up archive of
test_export_1027 (its one name is O-masked)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import export, links, outcome, profiles, reconcile
from nknu.archive.frontier import Frontier
from tests.archive.test_export_1027 import BASE, SITE, _add, _export, _res, by_url, make_archive, rows_of
from tests.archive.test_export_dates_1108 import TECS
from tests.archive.test_export_step3_1108 import AS_OF, OLD_NOTICE, OLD_PDF, _copies, _manifest

SERVICE = Path(__file__).resolve().parents[2] / "research" / "crawler_lab_2026-11-04" / "export_sites.sh"
BROKEN = BASE + "uploadfile/broken.pdf"  # the fixture's attachment that never connected


def _broken_copy(folder: Path, arch) -> Path:
    return _copies(folder, {arch.ids["broken"]: (OLD_PDF, {"url": BROKEN, "ctype": "application/pdf"})})


# ---------------------------------------------------------------- 导出依据 and --skip-same


def test_the_manifest_says_what_the_export_is_made_of(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, out, _rows, _readme = _export(arch, tmp_path)
    basis = _manifest(out)["导出依据"]
    assert set(basis) == {"资料截至", "存档内容", "程式", "旧存档副本"}
    assert basis["资料截至"] == AS_OF == _manifest(out)["资料截至"]
    assert len(basis["存档内容"]) == len(basis["程式"]) == 16 and basis["旧存档副本"] == ""
    copies = _broken_copy(tmp_path / "old", arch)
    export.export_site(arch.root, SITE, tmp_path / "filled", old_copies=copies)
    filled = _manifest(tmp_path / "filled")["导出依据"]
    assert filled["旧存档副本"] == export.OldCopies(copies).digest != ""
    assert {k: v for k, v in filled.items() if k != "旧存档副本"} == {k: v for k, v in basis.items() if k != "旧存档副本"}


def test_skip_same_writes_nothing_when_nothing_would_change(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    with pytest.raises(export.Unchanged, match="没有导出"):
        export.export_site(arch.root, SITE, tmp_path / "second", reuse=first, skip_same=first)
    assert not (tmp_path / "second").exists()
    assert export.same_basis(first, SITE, _manifest(first)["导出依据"])
    assert not export.same_basis(first, "c.nknu.edu.tw/affair", _manifest(first)["导出依据"])  # another site's


def _mark(arch, tmp_path) -> None:
    reconcile.review_mark(arch.root, arch.ids["broken"], note="学校那边的档案服务器停了（测试）")


def _hand_edit(arch, tmp_path) -> None:
    f = Frontier(arch.root / "frontier.db")  # what 11-08's album edit did: a row changed by hand, nothing fetched
    try:
        f.x("UPDATE url SET state='skipped', reason='album:drive' WHERE id=?", (arch.ids["pic"],))
    finally:
        f.close()


@pytest.mark.parametrize("change", ["mark", "hand_edit", "old_copies", "program"])
def test_what_changes_an_export_with_no_fetch_exports_it_again(tmp_path, monkeypatch, change):
    # review 11-08: comparing 资料截至 alone skipped all four, the stale folder staying the current one
    arch = make_archive(tmp_path / "archive")
    _got, first, rows1, _readme = _export(arch, tmp_path, "first")
    kw = {}
    if change == "mark":
        _mark(arch, tmp_path)
    elif change == "hand_edit":
        _hand_edit(arch, tmp_path)
    elif change == "old_copies":
        kw["old_copies"] = _broken_copy(tmp_path / "old", arch)
    else:
        monkeypatch.setattr(export, "program_digest", lambda: "0123456789abcdef")
    export.export_site(arch.root, SITE, tmp_path / "second", reuse=first, skip_same=first, **kw)
    second = _manifest(tmp_path / "second")
    assert second["资料截至"] == _manifest(first)["资料截至"] == AS_OF  # nothing was fetched
    assert second["导出依据"] != _manifest(first)["导出依据"]
    rows2 = by_url(rows_of(tmp_path / "second" / "manifest.csv"))
    if change == "mark":
        assert (rows1[BROKEN]["分类"], rows2[BROKEN]["分类"]) == ("连接失败", "学校那边的问题（人看过）")
    elif change == "hand_edit":
        assert rows2[BASE + "uploadfile/pic.jpg"]["本地档案"] == "" != rows1[BASE + "uploadfile/pic.jpg"]["本地档案"]
    elif change == "old_copies":
        assert rows2[BROKEN]["来源"] == export.ORIGIN_OLD and rows2[BROKEN]["本地档案"]
    # and again with the same: nothing changes now
    with pytest.raises(export.Unchanged):
        export.export_site(arch.root, SITE, tmp_path / "third", skip_same=tmp_path / "second", **kw)


def test_an_export_with_no_basis_or_an_unknown_part_is_never_the_same(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    data = _manifest(first)
    basis = data.pop("导出依据")
    (first / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert not export.same_basis(first, SITE, basis)  # made before the review: exported again once
    data["导出依据"] = {**basis, "程式": ""}
    (first / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert not export.same_basis(first, SITE, {**basis, "程式": ""})  # a program that could not be read
    assert not export.same_basis(tmp_path / "nothing", SITE, basis)


def test_the_export_command_says_unchanged_with_its_own_exit_code(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    r = CliRunner().invoke(app, ["archive", "export", "--dir", str(arch.root), "--site", SITE, "--out",
                                 str(tmp_path / "second"), "--reuse", str(first), "--skip-same", str(first)])
    assert r.exit_code == export.UNCHANGED == 3, r.output
    assert "没有变动：" in r.output and not (tmp_path / "second").exists()


# ---------------------------------------------------------------- the export service (export_sites.sh)


def _service(tmp_path: Path, arch, **env) -> tuple[int, str]:
    """export_sites.sh run with bash on the fixture archive: python on PATH is this test's (a wrapper: a virtual
    environment's own executable keeps its packages), the folders scratch ones."""
    bin_dir = tmp_path / "bin"
    if not bin_dir.exists():
        bin_dir.mkdir()
        (bin_dir / "python").write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n", encoding="utf-8")
        (bin_dir / "python").chmod(0o755)
        (tmp_path / "list.txt").write_text(f"{SITE}  # 教务处\n", encoding="utf-8")
    run_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               "EXPORT_BASE": str(tmp_path / "sites"), "ARCHIVE_DIR": str(arch.root),
               "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"), **env}
    got = subprocess.run(["bash", str(SERVICE), str(tmp_path / "list.txt")], env=run_env, capture_output=True,
                         text=True, errors="replace", timeout=300, check=False)
    return got.returncode, got.stdout + got.stderr


def _current(tmp_path: Path) -> Path:
    line = (tmp_path / "sites" / "current.tsv").read_text(encoding="utf-8").strip()
    return Path(line.split("\t")[1])


@pytest.mark.skipif(not SERVICE.exists() or shutil.which("bash") is None,
                    reason="the lab tools are on the disk and in code.tar, not in git")
def test_the_export_service_exports_a_site_again_only_when_something_changed(tmp_path):
    arch = make_archive(tmp_path / "archive")
    code, said = _service(tmp_path, arch)
    assert code == 0 and f"好了 {SITE}" in said, said
    first = _current(tmp_path)
    code, said = _service(tmp_path, arch)
    assert code == 0 and f"{SITE} 没有变动，跳过" in said and _current(tmp_path) == first, said
    assert not list((tmp_path / "sites").glob("*.partial"))
    # OLD_COPIES set later: the same 资料截至, exported again with the gap filled (review 11-08)
    copies = _broken_copy(tmp_path / "old copies", arch)  # a path with a space
    code, said = _service(tmp_path, arch, OLD_COPIES=str(copies))
    second = _current(tmp_path)
    assert code == 0 and f"好了 {SITE}" in said and second != first, said
    assert by_url(rows_of(second / "manifest.csv"))[BROKEN]["来源"] == export.ORIGIN_OLD
    code, said = _service(tmp_path, arch, OLD_COPIES=str(copies))
    assert code == 0 and "没有变动，跳过" in said and _current(tmp_path) == second, said
    # an OLD_COPIES that cannot be read stops the batch before any export, the current folder kept
    code, said = _service(tmp_path, arch, OLD_COPIES=str(tmp_path / "not there"))
    assert code == 1 and "读不了" in said and "开始" not in said and _current(tmp_path) == second, said
    # a person's mark changes the export with no fetch
    _mark(arch, tmp_path)
    code, said = _service(tmp_path, arch, OLD_COPIES=str(copies))
    third = _current(tmp_path)
    assert code == 0 and f"好了 {SITE}" in said and third != second, said
    assert by_url(rows_of(third / "manifest.csv"))[BASE + "uploadfile/broken.pdf"]["来源"] == export.ORIGIN_OLD
    # FORCE=1 exports whatever; only the current folder and the one before stay
    code, said = _service(tmp_path, arch, OLD_COPIES=str(copies), FORCE="1")
    assert code == 0 and f"好了 {SITE}" in said and _current(tmp_path) != third, said
    kept = sorted(p.name for p in (tmp_path / "sites").iterdir() if p.is_dir())
    assert kept == sorted([third.name, _current(tmp_path).name]), kept


@pytest.mark.skipif(not SERVICE.exists() or shutil.which("bash") is None,
                    reason="the lab tools are on the disk and in code.tar, not in git")
def test_the_export_service_never_deletes_a_folder_the_content_stages_export_lists_name(tmp_path):
    """11-08, asked by the content session: a folder its build reads (exports.txt, or another list EXPORTS names, such
    as exports-pilot.txt, in EXPORT_LISTS) stays, whatever the keep-two rule says; read as build_helpers.py reads
    them (a Windows editor's BOM, CRLF, backslashes, a trailing slash, # comments)."""
    arch = make_archive(tmp_path / "archive")
    lists = tmp_path / "pipeline"
    lists.mkdir()

    def run() -> tuple[Path, str]:
        code, said = _service(tmp_path, arch, FORCE="1", EXPORT_LISTS=str(lists))
        assert code == 0 and f"好了 {SITE}" in said, said
        return _current(tmp_path), said

    def kept() -> list[str]:
        return sorted(p.name for p in (tmp_path / "sites").iterdir() if p.is_dir())

    first, _said = run()
    (lists / "exports.txt").write_text(f"# 教务处\r\narchive-export\\sites\\{first.name}\\\r\n", encoding="utf-8-sig")
    (lists / "notes.txt").write_text(f"{tmp_path / 'sites'}/{first.name}\n", encoding="utf-8")  # not a list
    second, _said = run()
    third, said = run()
    assert f"没删这个网站更旧的一份 {first}：内容 session 的导出清单 {lists / 'exports.txt'} 写着它" in said, said
    assert kept() == sorted([first.name, second.name, third.name])
    (lists / "exports-pilot.txt").write_text(f"{second}/\n", encoding="utf-8")  # another list, an absolute path
    fourth, _said = run()
    assert kept() == sorted([first.name, second.name, third.name, fourth.name])
    (lists / "exports.txt").unlink()
    (lists / "exports-pilot.txt").write_text(f"# {second}\n", encoding="utf-8")  # a comment names nothing
    fifth, said = run()
    assert kept() == sorted([fourth.name, fifth.name]) and "没删" not in said, said


# ---------------------------------------------------------------- --reuse, --out


def test_reuse_links_only_a_file_whose_bytes_hash_to_the_archives_sha256(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, first, rows1, _readme = _export(arch, tmp_path, "first")
    rule = first / rows1[BASE + "uploadfile/rule.pdf"]["本地档案"]
    data = bytearray(rule.read_bytes())
    data[-3] ^= 0x01  # one byte changed in place, the size kept (review 11-08: it was linked, under the archive's sha256)
    rule.write_bytes(bytes(data))
    got = export.export_site(arch.root, SITE, tmp_path / "second", reuse=first)
    rows2 = by_url(rows_of(tmp_path / "second" / "manifest.csv"))
    new = tmp_path / "second" / rows2[BASE + "uploadfile/rule.pdf"]["本地档案"]
    assert os.stat(new).st_ino != os.stat(rule).st_ino and os.stat(new).st_nlink == 1
    assert hashlib.sha256(new.read_bytes()).hexdigest() == rows2[BASE + "uploadfile/rule.pdf"]["sha256"] == arch.sha["rule"]
    assert got.linked == 3  # the other three are the same


@pytest.mark.parametrize("folder", ["old_copies", "reuse", "skip_same"])
def test_an_out_folder_inside_a_folder_the_export_only_reads_is_refused(tmp_path, folder):
    arch = make_archive(tmp_path / "archive")
    _got, first, _rows, _readme = _export(arch, tmp_path, "first")
    read = _broken_copy(tmp_path / "old", arch) if folder == "old_copies" else first
    before = sorted(p.relative_to(read) for p in read.rglob("*"))
    with pytest.raises(export.ExportRefused, match="只读，导出要放在它外面"):
        export.export_site(arch.root, SITE, read / "export", **{folder: read})
    with pytest.raises(export.ExportRefused, match="只读"):
        export.export_site(arch.root, SITE, read, **{folder: read})
    assert sorted(p.relative_to(read) for p in read.rglob("*")) == before


# ---------------------------------------------------------------- names


@pytest.mark.parametrize(("part", "want"), [
    ("%C3%A5%C2%85%C2%AC%C3%A5%C2%91%C2%8A.pdf", "公告.pdf"),  # percent-encoded twice (10-24's 圖資處 addresses)
    ("%E5%85%AC%E5%91%8A.pdf", "公告.pdf"),  # once
    ("caf%C3%A9.pdf", "café.pdf"),  # a real Latin-1 name stays
    ("plain.pdf", "plain.pdf"),
    ("%B2%C4.pdf", "��.pdf"),  # Big5 bytes in an address: unquote's own reading, as before
])
def test_unquoted(part, want):
    assert export.unquoted(part) == want


def test_a_file_name_keeps_no_c1_control_characters():
    assert export.safe_name("a\x85b\x9fc.pdf") == "a_b_c.pdf"
    assert export.safe_name("å\x85¬å\x91\x8a.pdf") == "å_¬å_.pdf"  # what a twice-encoded name gave before


def test_names_percent_encoded_twice_are_read_as_utf8(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    twice = BASE + "uploadfile/%C3%A5%C2%85%C2%AC%C3%A5%C2%91%C2%8A.pdf"  # 公告.pdf, encoded twice
    gone = BASE + "uploadfile/%C3%A8%C2%A1%C2%A8.pdf"  # 表.pdf, encoded twice, never got
    for name, url in (("twice", twice), ("gone", gone)):
        uid = _add(arch.f, url=url, kind="file", kind0="file", tier=2, state="failed", reason="connect", attempts=3,
                   status=None, fetched_at=None)
        arch.ids[name] = uid
        _res(arch.f, arch.ids["about"], url, "file", res_id=uid, text=name)
    arch.close()
    old = _copies(tmp_path / "old", {arch.ids["twice"]: (OLD_PDF, {"url": twice, "ctype": "application/pdf"})})
    export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    rows = by_url(rows_of(tmp_path / "out" / "manifest.csv"))
    assert rows[twice]["本地档案"].endswith("-公告.pdf") and rows[twice]["标题"] == "公告.pdf"
    assert rows[gone]["本地档案"] == "" and rows[gone]["标题"] == "表.pdf"


# ---------------------------------------------------------------- the old copies


def test_a_row_the_id_probe_calls_gone_or_waiting_for_the_lab_takes_its_old_copy(tmp_path):
    # review 11-08: old_copies_1108.TAKE held both, the export neither, so their copies were neither used nor counted
    arch = make_archive(tmp_path / "archive").open()
    probe = arch.page("probe", BASE + "newsview.aspx?nid=7", "<html><body><div id='DataFormView'></div></body></html>",
                      flags="gone_id")
    lab = _add(arch.f, url=BASE + "uploadfile/lab.pdf", kind="file", kind0="file", tier=2, state="failed", reason="dns",
               attempts=3, status=None, fetched_at=None)
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/lab.pdf", "file", res_id=lab, text="校內表格")
    arch.close()
    old = _copies(tmp_path / "old", {
        probe: (OLD_NOTICE.encode(), {"url": BASE + "newsview.aspx?nid=7", "ctype": "text/html; charset=utf-8"}),
        lab: (OLD_PDF, {"url": BASE + "uploadfile/lab.pdf", "ctype": "application/pdf"})})
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    rows = by_url(rows_of(tmp_path / "out" / "manifest.csv"))
    page, pdf = rows[BASE + "newsview.aspx?nid=7"], rows[BASE + "uploadfile/lab.pdf"]
    assert (page["结果"], page["来源"], page["标题"]) == (outcome.RESULT_LABEL["looks_gone"], export.ORIGIN_OLD, "宿舍搬遷公告")
    assert (pdf["结果"], pdf["来源"]) == (outcome.RESULT_LABEL["lab"], export.ORIGIN_OLD)
    assert (tmp_path / "out" / pdf["本地档案"]).read_bytes() == OLD_PDF
    assert got.old_copies == 2
    from importlib.util import module_from_spec, spec_from_file_location

    builder = SERVICE.parent / "old_copies_1108.py"
    if builder.exists():  # the folder's builder takes exactly what the export takes
        spec = spec_from_file_location("old_copies_1108", builder)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert set(mod.TAKE) | {mod.REDIRECT} == export.OLD_NO_FILE | export.OLD_ALWAYS


def test_a_row_a_person_marked_as_the_schools_takes_its_old_copy(tmp_path):
    # found while testing the review's fixes: a mark (school_side) took the row out of the results the export fills
    arch = make_archive(tmp_path / "archive")
    _mark(arch, tmp_path)
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=_broken_copy(tmp_path / "old", arch))
    row = by_url(rows_of(tmp_path / "out" / "manifest.csv"))[BROKEN]
    assert (row["分类"], row["来源"]) == ("学校那边的问题（人看过）", export.ORIGIN_OLD)
    assert (tmp_path / "out" / row["本地档案"]).read_bytes() == OLD_PDF and got.old_copies == 1


def test_an_index_row_this_round_no_longer_needs_is_counted_and_its_row_left_alone(tmp_path):
    arch = make_archive(tmp_path / "archive")
    old = _copies(tmp_path / "old", {
        arch.ids["d2"]: (OLD_NOTICE.encode(), {"url": BASE + "newsview.aspx?nid=2"}),  # got this round since
        arch.ids["broken"]: (OLD_PDF, {"url": BROKEN, "ctype": "application/pdf"})})
    got = export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    rows = by_url(rows_of(tmp_path / "out" / "manifest.csv"))
    d2 = rows[BASE + "newsview.aspx?nid=2"]
    assert d2["来源"] == "" and d2["备注"] == "" and rows[BROKEN]["来源"] == export.ORIGIN_OLD
    readme = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert "10-24 旧存档也有副本、这一轮的结果却不在要补的范围的 1 行" in readme and "「备注」栏写着：抓到了 1" in readme
    assert any(line.startswith("10-24 旧存档也有副本、这一轮的结果却不在要补的范围的 1 行，照这一轮的（这一轮的结果：抓到了 1）")
               for line in got.lines)
    assert got.old_copies == 1


def test_an_index_row_whose_result_is_none_the_export_fills_and_has_no_file_says_so(tmp_path):
    arch = make_archive(tmp_path / "archive")
    later = "https://www.edu.tw/files/plan.pdf"  # round two's this round (state later): none the export fills
    old = _copies(tmp_path / "old", {arch.ids["later"]: (OLD_PDF, {"url": later, "ctype": "application/pdf"})})
    export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    row = by_url(rows_of(tmp_path / "out" / "manifest.csv"))[later]
    assert row["本地档案"] == "" and row["来源"] == ""
    assert f"10-24 旧存档有它的副本，没用上：这一轮的结果是「{outcome.RESULT_LABEL['later']}」，不在要补的范围" in row["备注"]
    readme = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    assert f"「备注」栏写着：{outcome.RESULT_LABEL['later']} 1" in readme


def test_a_redirect_that_takes_the_copy_of_the_page_it_lands_on_keeps_that_address(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    thin = arch.page("thin", BASE + "intro.aspx", "<html><body><h2>系所簡介</h2></body></html>", flags="thin")
    moved = BASE + "intro_old.aspx"
    off, sha = arch.put("moved", moved, "text/html", b"moved", headers={"location": BASE + "intro.aspx"}, status=302)
    _add(arch.f, url=moved, final_url=BASE + "intro.aspx", status=302, bytes=5, sha256=sha, warc=arch.rel,
         warc_offset=off, ctype="text/html", sniff="html", page_type="t:ok")
    arch.close()
    old = _copies(tmp_path / "old", {thin: (OLD_NOTICE.encode(), {"url": BASE + "intro.aspx"})})
    export.export_site(arch.root, SITE, tmp_path / "out", old_copies=old)
    rows = by_url(rows_of(tmp_path / "out" / "manifest.csv"))
    page, redirect = rows[BASE + "intro.aspx"], rows[moved]
    assert redirect["本地档案"] == page["本地档案"] and redirect["来源"] == page["来源"] == export.ORIGIN_OLD
    # the file is the answer of the page it lands on: 最后落地网址 stays that page's, with no 这一轮跳到
    assert redirect["最后落地网址"] == BASE + "intro.aspx" and "这一轮跳到" not in redirect["备注"]
    assert page["最后落地网址"] == ""


# ---------------------------------------------------------------- tecs's notices in the content area


def test_tecs_notice_areas_are_in_the_cnknu_content_area():
    # review 11-08: tecs's newsview writes no DataFormView; with none of its areas in CNKNU.content_css, the content
    # stage read its 155 notices of round one as empty, and the crawler called every link on them chrome
    url = "https://tecs.nknu.edu.tw/newsview.aspx?kind=id&nid=7555"
    css = profiles.content_css(url)
    assert css == profiles.CNKNU.content_css
    found = {n.attributes.get("id") for n in LexborHTMLParser(TECS).css(css)}
    assert found == {"TitleLabel", "ContentsLabel", "FileLinkLabel"}
    page = links.extract(TECS, url, css)
    assert page.link_zone == {"/upload/a.pdf": "body"}  # its attachment, which was chrome
    pagetext = pytest.importorskip("nknu.totext.pagetext")  # the content stage's reader, read only here
    text = pagetext.page_text(TECS.encode(), url).text
    assert "榜單如附件。" in text and "碩士班榜單公告115年12月30日" in text


# ---------------------------------------------------------------- 最后修改时间


def test_the_readme_says_a_servers_copy_time_is_no_file_date(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, _out, _rows, readme = _export(arch, tmp_path)
    assert "有的服务器说的是它自己把档案复制过去的时间" in readme and "不能拿来用" in readme
    assert "不是档案的日期" in export.COLUMN_HELP["最后修改时间"]
