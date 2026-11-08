"""Archive members (plan 2026-11-02 T5; review focus 5, untrusted members): ZIP through zipfile, RAR through bsdtar,
every member written as dest/<index><.ext> and never at a path the archive names; then the archive route of the
to-text run, which converts each member into a cache entry of its own and accounts it. Fakes for Docling, OCR,
LibreOffice and, except in the tests that need it, bsdtar."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from nknu.totext import archives, libreoffice
from nknu.totext.accounting import account, project_totals, write_report
from nknu.totext.archives import ArchiveError, Member, extract, rar_members, zip_members
from nknu.totext.cache import Cache, sha256_file
from nknu.totext.convert import convert_file
from nknu.totext.libreoffice import ConvertError
from nknu.totext.route import Route
from nknu.totext.run import run
from tests.totext.fixtures import docx_with_table, refuse_real_models, tiny_pdf, zip_of
from tests.totext.test_run import PAGE_HTML, PAGE_URL, PDF_TEXT, SITE, FakeDocling, fake_tools, write_export

PDF = b"%PDF-1.4 member"
OLE = bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 56
ZIP_URL = SITE + "uploadfile/m.zip"
NESTED = "压缩档里的压缩档，没有再解开"


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, request):
    refuse_real_models(monkeypatch, request)


class RawName(zipfile.ZipInfo):
    """A member whose name is written as these bytes without the UTF-8 flag (zipfile flags every non-ASCII name)."""

    def __init__(self, raw: bytes):
        super().__init__(raw.decode("cp437"))
        self.raw = raw

    def _encodeFilenameFlags(self):
        return self.raw, self.flag_bits


def zip_raw(path: Path, entries: list[tuple[zipfile.ZipInfo | str, bytes]], *, encrypted: tuple[int, ...] = ()) -> Path:
    """A ZIP of (name or ZipInfo, bytes); the members at the `encrypted` positions carry the encryption flag in the
    central directory (zipfile cannot encrypt; the flag is what the reader goes by)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
        for i in encrypted:
            zf.filelist[i].flag_bits |= 0x1
    return path


def _brief(members: list[Member]) -> list[tuple]:
    return [(m.index, m.path, m.status, m.reason) for m in members]


# --- ZIP ---

def test_zip_cp950_names(tmp_path):
    z = zip_raw(tmp_path / "a.zip", [(RawName("11221教務會議附件.pdf".encode("cp950")), PDF)])
    assert [m.path for m in extract(z, "zip", tmp_path / "d")] == ["11221教務會議附件.pdf"]


def test_zip_utf8_flag_names(tmp_path):
    z = zip_of(tmp_path / "a.zip", {"會議/附件一.pdf": PDF, "a.pdf": PDF})
    ms = extract(z, "zip", tmp_path / "d")
    assert _brief(ms) == [(1, "會議/附件一.pdf", "ok", ""), (2, "a.pdf", "ok", "")]


def test_zip_unicode_path_field_wins(tmp_path):
    raw, name = b"x\xff.pdf", "替代.pdf".encode()
    info = RawName(raw)
    info.extra = struct.pack("<HHBL", 0x7075, 5 + len(name), 1, zlib.crc32(raw)) + name
    [m] = extract(zip_raw(tmp_path / "a.zip", [(info, PDF)]), "zip", tmp_path / "d")
    assert (m.path, m.reason) == ("替代.pdf", "")


def test_zip_unflagged_utf8_names_read_as_utf8(tmp_path):
    # tools that write UTF-8 names without the flag (macOS's own Compress): UTF-8 is tried before CP950
    [m] = extract(zip_raw(tmp_path / "a.zip", [(RawName("附件一.pdf".encode()), PDF)]), "zip", tmp_path / "d")
    assert (m.path, m.reason) == ("附件一.pdf", "")


def test_zip_names_neither_utf8_nor_cp950_read_as_cp437(tmp_path):
    z = zip_raw(tmp_path / "a.zip", [(RawName(b"\xff\xfe.pdf"), PDF),
                                     (RawName("附件.pdf".encode("cp950")), PDF)])
    one, two = extract(z, "zip", tmp_path / "d")
    assert (one.path, one.status, one.reason) == (b"\xff\xfe.pdf".decode("cp437"), "ok", "档名不是 CP950，按 cp437 读")
    assert (two.path, two.reason) == ("附件.pdf", "")  # one odd name leaves the others as they are
    assert one.local.read_bytes() == PDF


def test_member_bytes_and_flat_paths(tmp_path):
    big = bytes(range(256)) * 9000  # 2.3 MB: read in blocks
    z = zip_raw(tmp_path / "a.zip", [("子目錄/", b""), ("子目錄/會議.pdf", PDF), ("B.DOCX", big),
                                     ("readme.config1", b"x"), (".hidden", b"y"), ("win\\dir\\", b"")])
    dest = tmp_path / "d"
    ms = extract(z, "zip", dest)
    assert [(m.index, m.path, m.local, m.size, m.status) for m in ms] == [
        (1, "子目錄/會議.pdf", dest / "0001.pdf", len(PDF), "ok"), (2, "B.DOCX", dest / "0002.docx", len(big), "ok"),
        (3, "readme.config1", dest / "0003", 1, "ok"), (4, ".hidden", dest / "0004", 1, "ok")]  # directories aside
    assert ms[0].local.read_bytes() == PDF and ms[1].local.read_bytes() == big
    assert sorted(p.name for p in dest.iterdir()) == ["0001.pdf", "0002.docx", "0003", "0004"]


def test_unsafe_paths_never_used_on_disk(tmp_path):
    z = tmp_path / "a.zip"
    ms = extract(zip_of(z, {"../evil.pdf": b"%PDF-1.4", "/abs/x.pdf": b"%PDF-1.4"}), "zip", tmp_path / "d")
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["0001.pdf", "0002.pdf"]
    assert not (tmp_path / "evil.pdf").exists() and all("路径不安全" in m.reason for m in ms)
    assert [(m.path, m.status) for m in ms] == [("../evil.pdf", "ok"), ("/abs/x.pdf", "ok")]
    assert ms[0].reason == "路径不安全（../evil.pdf），只取内容、不照路径放"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.zip", "d"]


def test_symlink_member_skipped(tmp_path):
    link = zipfile.ZipInfo("link.pdf")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = zip_raw(tmp_path / "a.zip", [(link, b"../../etc/hosts"), ("a.pdf", PDF)])
    one, two = extract(z, "zip", tmp_path / "d")
    assert (one.index, one.path, one.local, one.status) == (1, "link.pdf", None, "skipped")
    assert one.reason == "符号链接，不取" and (two.index, two.local.name, two.status) == (2, "0002.pdf", "ok")
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["0002.pdf"]


def test_encrypted_member_failed(tmp_path):
    z = zip_raw(tmp_path / "a.zip", [("secret.pdf", PDF), ("a.pdf", PDF)], encrypted=(0,))
    one, two = extract(z, "zip", tmp_path / "d")
    assert (one.path, one.local, one.status, one.reason) == ("secret.pdf", None, "failed", "加密的成员")
    assert two.status == "ok"


def test_size_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 10)
    z = zip_raw(tmp_path / "a.zip", [("big.pdf", b"%PDF" + b"x" * 20), ("small.pdf", b"%PDF-1.4")])
    big, small = extract(z, "zip", tmp_path / "d")
    assert (big.local, big.size, big.status, big.reason) == (None, 24, "failed", "超过大小上限（10 bytes）")
    assert (small.local.name, small.status) == ("0002.pdf", "ok")
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["0002.pdf"]


def test_total_and_count_caps(tmp_path, monkeypatch):
    z = zip_raw(tmp_path / "a.zip", [("a.pdf", b"%PDF" * 5), ("b.pdf", b"%PDF" * 5), ("c.pdf", b"%PDF")])
    monkeypatch.setattr(archives, "MAX_TOTAL_BYTES", 40)
    with pytest.raises(ArchiveError, match=r"解开后共 44 bytes，超过上限（40 bytes）"):
        extract(z, "zip", tmp_path / "d1")
    monkeypatch.setattr(archives, "MAX_TOTAL_BYTES", 2 * 2**30)
    monkeypatch.setattr(archives, "MAX_MEMBERS", 2)
    with pytest.raises(ArchiveError, match=r"成员太多（3 份，上限 2 份）"):
        extract(z, "zip", tmp_path / "d2")


def test_declared_sizes_are_not_trusted():
    # a member that reads past its cap stops there, whatever its header said
    from io import BytesIO

    sink = BytesIO()
    assert archives.copy_capped(BytesIO(b"x" * 25), sink, 25) == 25 and sink.getvalue() == b"x" * 25
    with pytest.raises(archives.TooBig):
        archives.copy_capped(BytesIO(b"x" * 26), BytesIO(), 25)


def test_unreadable_member_is_failed_and_the_rest_go_on(tmp_path):
    z = zip_raw(tmp_path / "a.zip", [("a.pdf", b"%PDF" * 100), ("b.pdf", PDF)])
    data = bytearray(z.read_bytes())
    at = data.index(zlib.compress(b"%PDF" * 100)[2:6])  # inside a.pdf's deflate stream
    data[at:at + 4] = b"\xff\xff\xff\xff"
    z.write_bytes(bytes(data))
    one, two = extract(z, "zip", tmp_path / "d")
    assert (one.local, one.status) == (None, "failed") and one.reason.startswith("解不开（")
    assert (two.status, two.local.read_bytes()) == ("ok", PDF)
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["0002.pdf"]


def test_bad_zip_is_an_archive_error(tmp_path):
    path = tmp_path / "a.zip"
    path.write_bytes(b"PK\x03\x04 broken")
    with pytest.raises(ArchiveError, match=r"^压缩档打不开（BadZipFile"):
        zip_members(path, tmp_path / "d")


def test_dest_must_be_new(tmp_path):
    z = zip_of(tmp_path / "a.zip", {"a.pdf": PDF})
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "x").write_bytes(b"x")
    with pytest.raises(ArchiveError):
        extract(z, "zip", dest)
    (tmp_path / "f").write_bytes(b"x")
    with pytest.raises(ArchiveError):
        extract(z, "zip", tmp_path / "f")
    (tmp_path / "empty").mkdir()
    assert [m.path for m in extract(z, "zip", tmp_path / "empty")] == ["a.pdf"]  # an empty folder will do


def test_unknown_kind_is_an_archive_error(tmp_path):
    with pytest.raises(ArchiveError, match="7z"):
        extract(zip_of(tmp_path / "a.zip", {"a.pdf": PDF}), "7z", tmp_path / "d")


# --- RAR through bsdtar ---

def ls_line(name: str, size: int, mode: str = "-rw-r--r-- ") -> str:
    """One line of `bsdtar -tvf`, as libarchive 3.7 prints it."""
    return f"{mode} 0 0      0      {size:>8} Nov  3  2021 {name}"


class FakeBsdtar:
    """subprocess.run for bsdtar: -tvf prints `listing`; -x writes `files` (and `links`) under its -C folder. Records
    each call as (argv, keyword arguments)."""

    def __init__(self, listing: list[str], files: dict[str, bytes] | None = None, *,
                 links: dict[str, str] | None = None, list_code: int = 0, list_err: str = "", x_code: int = 0,
                 x_err: str = "", raises: BaseException | None = None):
        self.listing, self.files, self.links = listing, files or {}, links or {}
        self.list_code, self.list_err, self.x_code, self.x_err, self.raises = list_code, list_err, x_code, x_err, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        if argv[1] == "-tvf":
            out = "".join(line + "\n" for line in self.listing).encode()
            return subprocess.CompletedProcess(argv, self.list_code, out, self.list_err.encode())
        root = Path(argv[argv.index("-C") + 1])
        for name, data in self.files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        for name, target in self.links.items():
            (root / name).symlink_to(target)
        return subprocess.CompletedProcess(argv, self.x_code, b"", self.x_err.encode())


def test_rar_command(monkeypatch, tmp_path):
    rar = tmp_path / "a.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00 fake")
    fake = FakeBsdtar([ls_line("附件/會議.pdf", len(PDF)), ls_line("附件", 0, "drwxr-xr-x "), ls_line("b c.doc", 3)],
                      {"附件/會議.pdf": PDF, "b c.doc": b"doc"})
    monkeypatch.setattr(archives.subprocess, "run", fake)
    dest = tmp_path / "d"
    ms = rar_members(rar, dest)
    (listed, list_kw), (extracted, x_kw) = fake.calls
    assert listed == ["bsdtar", "-tvf", str(rar)]
    assert extracted[:4] == ["bsdtar", "-x", "-k", "-C"] and extracted[5:] == ["-f", str(rar)]
    for kw in (list_kw, x_kw):
        assert kw["env"]["LC_ALL"] == ("C.UTF-8" if sys.platform.startswith("linux") else "en_US.UTF-8")
        assert kw["timeout"] == 300 and kw["capture_output"] is True
    assert dest in Path(extracted[4]).parents  # a fresh folder of its own, gone afterwards
    assert [(m.index, m.path, m.local, m.size, m.status, m.reason) for m in ms] == [
        (1, "附件/會議.pdf", dest / "0001.pdf", len(PDF), "ok", ""), (2, "b c.doc", dest / "0002.doc", 3, "ok", "")]
    assert ms[0].local.read_bytes() == PDF and sorted(p.name for p in dest.iterdir()) == ["0001.pdf", "0002.doc"]


def test_rar_members_that_did_not_come_out_are_failed(monkeypatch, tmp_path):
    rar = tmp_path / "a.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00 fake")
    listing = [ls_line("a.pdf", len(PDF)), ls_line("secret.pdf", 9), ls_line("../evil.pdf", 9),
               ls_line("/abs/x.pdf", 4), ls_line("dup.pdf", 3), ls_line("dup.pdf", 3), ls_line("tab\\tname.pdf", 2),
               ls_line("link.pdf -> ../../etc/hosts", 0, "lrwxrwxrwx "), ls_line("gone.pdf", 5)]
    err = ("secret.pdf: Encrypted file is unsupported\n../evil.pdf: Path contains '..': Unknown error: -1\n"
           "bsdtar: Removing leading '/' from member names\nbsdtar: Error exit delayed from previous errors.\n")
    outside = tmp_path / "hosts"
    outside.write_bytes(b"not a member")
    fake = FakeBsdtar(listing, {"a.pdf": PDF, "abs/x.pdf": b"%PDF", "dup.pdf": b"one", "tab\tname.pdf": b"ta",
                                "extra.pdf": b"%PDF extra"}, links={"link.pdf": str(outside)}, x_code=1, x_err=err)
    monkeypatch.setattr(archives.subprocess, "run", fake)
    dest = tmp_path / "d"
    ms = rar_members(rar, dest)
    assert _brief(ms) == [
        (1, "a.pdf", "ok", ""),
        (2, "secret.pdf", "failed", "加密的成员"),
        (3, "../evil.pdf", "failed", "没解出来（Path contains '..': Unknown error: -1）"),
        (4, "/abs/x.pdf", "ok", "路径不安全（/abs/x.pdf），只取内容、不照路径放"),
        (5, "dup.pdf", "ok", ""),
        (6, "dup.pdf", "failed", "压缩档里有同名的档，只解出前一份"),
        (7, "tab\tname.pdf", "ok", ""),  # bsdtar's escapes undone
        (8, "link.pdf", "skipped", "符号链接，不取"),
        (9, "gone.pdf", "failed", "没解出来（bsdtar 退出码 1）"),
        (10, "extra.pdf", "ok", "")]  # on disk but not in the listing: still a member
    assert outside.read_bytes() == b"not a member" and ms[7].local is None
    assert sorted(p.name for p in dest.iterdir()) == ["0001.pdf", "0004.pdf", "0005.pdf", "0007.pdf", "0010.pdf"]


def test_rar_hard_link_and_a_name_that_only_looks_like_one(monkeypatch, tmp_path):
    rar = tmp_path / "a.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00 fake")
    fake = FakeBsdtar([ls_line("a.pdf", 4), ls_line("b.pdf link to a.pdf", 0), ls_line("c link to d.pdf", 4)],
                      {"a.pdf": b"%PDF", "b.pdf": b"%PDF", "c link to d.pdf": b"%PDF"})
    monkeypatch.setattr(archives.subprocess, "run", fake)
    ms = rar_members(rar, tmp_path / "d")
    assert _brief(ms) == [(1, "a.pdf", "ok", ""), (2, "b.pdf", "ok", ""), (3, "c link to d.pdf", "ok", "")]


def test_rar_caps(monkeypatch, tmp_path):
    rar = tmp_path / "a.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00 fake")
    fake = FakeBsdtar([ls_line("a.pdf", 30), ls_line("b.pdf", 4), ls_line("c.pdf", 4)],
                      {"a.pdf": b"%PDF" + b"x" * 26, "b.pdf": b"%PDF", "c.pdf": b"%PDF"})
    monkeypatch.setattr(archives.subprocess, "run", fake)
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 10)
    ms = rar_members(rar, tmp_path / "d1")
    assert _brief(ms)[0] == (1, "a.pdf", "failed", "超过大小上限（10 bytes）") and ms[0].local is None
    assert [m.status for m in ms] == ["failed", "ok", "ok"]
    monkeypatch.setattr(archives, "MAX_MEMBERS", 2)
    with pytest.raises(ArchiveError, match=r"成员太多（3 份，上限 2 份）"):
        rar_members(rar, tmp_path / "d2")
    monkeypatch.setattr(archives, "MAX_MEMBERS", 1_000)
    monkeypatch.setattr(archives, "MAX_TOTAL_BYTES", 37)
    calls = len(fake.calls)
    with pytest.raises(ArchiveError, match=r"解开后共 38 bytes，超过上限（37 bytes）"):
        rar_members(rar, tmp_path / "d3")
    assert len(fake.calls) == calls + 1  # refused after the listing, before anything is written


@pytest.mark.parametrize("fake,reason", [
    (FakeBsdtar([], list_code=1, list_err="bsdtar: Error opening archive: Unrecognized archive format\n"),
     "压缩档打不开（bsdtar: Error opening archive: Unrecognized archive format）"),
    (FakeBsdtar([], raises=subprocess.TimeoutExpired(["bsdtar"], 300)), "bsdtar 超时（300 秒）"),
    (FakeBsdtar([], raises=FileNotFoundError("bsdtar")), "没有 bsdtar（要装 libarchive 的 bsdtar）")])
def test_rar_that_cannot_be_read_is_an_archive_error(monkeypatch, tmp_path, fake, reason):
    rar = tmp_path / "a.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00 fake")
    monkeypatch.setattr(archives.subprocess, "run", fake)
    with pytest.raises(ArchiveError) as e:
        extract(rar, "rar", tmp_path / "d")
    assert str(e.value) == reason


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="no bsdtar")
def test_bsdtar_walks_a_real_archive(tmp_path):
    z = zip_of(tmp_path / "a.zip", {"a.pdf": PDF, "子目錄/b c.txt": b"bb"})  # bsdtar reads ZIP as well as RAR
    dest = tmp_path / "d"
    ms = rar_members(z, dest)
    assert [(m.index, m.path, m.local, m.size, m.status) for m in ms] == [
        (1, "a.pdf", dest / "0001.pdf", len(PDF), "ok"), (2, "子目錄/b c.txt", dest / "0002.txt", 2, "ok")]
    assert ms[1].local.read_bytes() == b"bb" and sorted(p.name for p in dest.iterdir()) == ["0001.pdf", "0002.txt"]


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="no bsdtar")
def test_bsdtar_never_writes_outside(tmp_path):
    link = zipfile.ZipInfo("link.pdf")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = zip_raw(tmp_path / "a.zip", [("../evil.pdf", PDF), ("/abs/x.pdf", PDF), (link, b"../../hosts"),
                                     ("a.pdf", PDF)])
    work = tmp_path / "work"
    work.mkdir()
    ms = rar_members(z, work / "d")
    assert [(m.path, m.status) for m in ms] == [("../evil.pdf", "failed"), ("/abs/x.pdf", "ok"),
                                                ("link.pdf", "skipped"), ("a.pdf", "ok")]
    assert "Path contains '..'" in ms[0].reason and "路径不安全" in ms[1].reason
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.zip", "work"]
    assert [p.name for p in work.iterdir()] == ["d"] and sorted(p.name for p in (work / "d").iterdir()) == [
        "0002.pdf", "0004.pdf"]


# --- the archive route of the to-text run ---

def one_row_export(root: Path, row: int, rel: str, data: bytes, url: str = ZIP_URL) -> Path:
    """An export whose manifest has one 附件 row, numbered `row`."""
    write_export(root, [{"type": "附件", "url": url, "file": rel, "bytes": data, "fmt": "zip"}])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["项目"][0]["序号"] = str(row)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def _fake_to_modern(src, kind, workdir, **_):
    (workdir / "out").mkdir(parents=True)
    return docx_with_table(workdir / "out" / "input.docx")


def _member_zip(made: Path) -> bytes:
    made.mkdir(parents=True, exist_ok=True)
    inner = zip_of(made / "c.zip", {"x.pdf": PDF}).read_bytes()
    pdf = tiny_pdf(made / "a.pdf", [PDF_TEXT]).read_bytes()
    return zip_of(made / "m.zip", {"a.pdf": pdf, "b.doc": OLE, "c.zip": inner}).read_bytes()


def test_archive_route_accounts_each_member(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "to_modern", _fake_to_modern)
    made = tmp_path / "made"
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", _member_zip(made))
    out, fake, beats = tmp_path / "out", FakeDocling(), []
    run(export, out, tools=fake_tools(fake, beat=lambda: beats.append(1)))
    cache = Cache(out / "cache")
    accounts = account(export, cache)
    assert [a.key for a in accounts] == ["9", "9!a.pdf", "9!b.doc", "9!c.zip"]
    by = {a.key: a for a in accounts}
    assert (by["9"].route, by["9"].status, by["9"].reason, by["9"].member) == ("archive", "ok", "解开 3 份成员", "")
    assert [(a.route, a.status, a.reason, a.member) for a in accounts[1:]] == [
        ("pdf", "ok", "", "a.pdf"), ("office_old", "ok", "", "b.doc"), ("archive", "skipped", NESTED, "c.zip")]
    assert all((a.row, a.type, a.file) == (9, "附件", "files/0005-m.zip") for a in accounts)
    assert by["9!a.pdf"].pages == 2 and by["9!a.pdf"].chars > 0 and by["9!a.pdf"].seconds > 0
    assert fake.kinds() == ["pdf", "docx"] and len(beats) == 5  # 2 Docling calls, extraction, 2 converted members
    result = cache.done(by["9"].sha256)
    assert [(m["index"], m["path"], m["route"], m["status"], m["size"]) for m in result["members"]] == [
        (1, "a.pdf", "pdf", "ok", (made / "a.pdf").stat().st_size), (2, "b.doc", "office_old", "ok", len(OLE)),
        (3, "c.zip", "archive", "skipped", (made / "c.zip").stat().st_size)]
    assert {m["sha256"] for m in result["members"]} == {by["9!a.pdf"].sha256, by["9!b.doc"].sha256,
                                                         by["9!c.zip"].sha256}
    assert cache.text(by["9!a.pdf"].sha256).startswith("page one") and cache.text(by["9"].sha256) == ""
    member = cache.done(by["9!a.pdf"].sha256)
    assert (member["first_key"], member["first_file"]) == ("9!a.pdf", "files/0005-m.zip!a.pdf")
    assert list((out / "work").iterdir()) == []  # the extracted files are gone after the run


def test_report_counts_members(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "to_modern", _fake_to_modern)
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", _member_zip(tmp_path / "made"))
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    summary = write_report(out, account(export, Cache(out / "cache")), tools={})
    assert (summary["files"], summary["members"], summary["by_status"]) == (4, 3, {"ok": 3, "skipped": 1})
    assert summary["by_route_status"]["archive"] == {"ok": 1, "skipped": 1}
    assert "其中压缩档成员 3 个" in (out / "summary.md").read_text(encoding="utf-8")


def test_a_file_with_a_members_bytes_converts_once_and_counts_in_the_projection(tmp_path):
    made = tmp_path / "made"
    made.mkdir()
    pdf = tiny_pdf(made / "a.pdf", [PDF_TEXT, PDF_TEXT]).read_bytes()
    export = write_export(tmp_path / "export", [
        {"type": "附件", "url": ZIP_URL, "file": "files/0001-m.zip", "fmt": "zip",
         "bytes": zip_of(made / "m.zip", {"a.pdf": pdf}).read_bytes()},
        {"type": "附件", "url": SITE + "uploadfile/a.pdf", "file": "files/0002-a.pdf", "bytes": pdf, "fmt": "pdf"}])
    out, fake = tmp_path / "out", FakeDocling()
    run(export, out, tools=fake_tools(fake))
    accounts = account(export, Cache(out / "cache"))
    assert [(a.key, a.status) for a in accounts] == [("1", "ok"), ("1!a.pdf", "ok"), ("2", "ok")]
    assert fake.kinds() == ["pdf"] and accounts[1].sha256 == accounts[2].sha256
    totals = project_totals(export, accounts)  # the member came first; the exported PDF still counts
    assert (totals["pdf_files"], totals["pdf_pages"], totals["files_by_route"]) == (1, 2, {"pdf": 1, "archive": 1})


def test_rerun_reuses_member_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "to_modern", _fake_to_modern)
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", _member_zip(tmp_path / "made"))
    out, fake = tmp_path / "out", FakeDocling()
    run(export, out, tools=fake_tools(fake))
    cache = Cache(out / "cache")
    first = {a.key: a for a in account(export, cache)}
    (cache.dir(first["9"].sha256) / "result.json").unlink()  # as after a crash mid-archive
    assert account(export, cache)[0].status == "pending" and len(account(export, cache)) == 1  # members unknown
    calls = len(fake.calls)
    summary = run(export, out, tools=fake_tools(fake))
    assert summary["converted"] == 1 and len(fake.calls) == calls  # the archive opened again, no member converted
    again = {a.key: a for a in account(export, cache)}
    assert {k: (a.status, a.sha256) for k, a in again.items()} == {k: (a.status, a.sha256) for k, a in first.items()}
    # each member's status is read from its own cache entry
    pdf = again["9!a.pdf"]
    cache.save(pdf.sha256, {**cache.done(pdf.sha256), "status": "partial", "reason": "第 1 页 OCR 失败"},
               cache.text(pdf.sha256), {})
    assert {a.key: (a.status, a.reason) for a in account(export, cache)}["9!a.pdf"] == ("partial", "第 1 页 OCR 失败")


def test_member_that_crashed_is_not_retried(tmp_path, monkeypatch):
    # a member that crashed the process: retrying its archive (--retry-crashed) must not loop on it
    monkeypatch.setattr(libreoffice, "to_modern", _fake_to_modern)
    made = tmp_path / "made"
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", _member_zip(made))
    out, fake = tmp_path / "out", FakeDocling()
    cache = Cache(out / "cache")
    cache.begin(sha256_file(made / "a.pdf"))
    run(export, out, tools=fake_tools(fake))
    by = {a.key: a for a in account(export, cache)}
    assert fake.kinds() == ["docx"] and by["9"].status == "ok" and by["9!a.pdf"].status == "failed"
    assert by["9!a.pdf"].reason == "上次转到一半程序崩溃，重转压缩档时不再试这个成员"
    result = cache.done(by["9"].sha256)
    assert (result["members"][0]["status"], result["members"][0]["reason"]) == ("failed", by["9!a.pdf"].reason)


def test_member_result_gone_says_how_to_redo(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "to_modern", _fake_to_modern)
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", _member_zip(tmp_path / "made"))
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    cache = Cache(out / "cache")
    pdf = {a.key: a for a in account(export, cache)}["9!a.pdf"]
    shutil.rmtree(cache.dir(pdf.sha256))
    gone = {a.key: a for a in account(export, cache)}["9!a.pdf"]
    assert gone.status == "failed"
    assert gone.reason == "缓存里没有这个成员的结果（删掉压缩档的 result.json 再跑一次可重转）"


def test_archive_that_cannot_be_opened_is_the_containers_failure(tmp_path):
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", b"PK\x03\x04 broken")
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    [only] = account(export, Cache(out / "cache"))
    assert (only.key, only.route, only.status) == ("9", "archive", "failed")
    assert only.reason.startswith("压缩档打不开（BadZipFile")


def test_empty_archive_is_empty(tmp_path):
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip",
                            zip_raw(tmp_path / "e.zip", [("子目錄/", b"")]).read_bytes())
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    [only] = account(export, Cache(out / "cache"))
    assert (only.status, only.reason) == ("empty", "压缩档里没有档案")


def test_member_notes_stay_in_the_account(tmp_path):
    z = zip_raw(tmp_path / "m.zip", [(RawName(b"\xff\xfe.htm"), b"x"), ("../up.txt", b"y")])
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", z.read_bytes())
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    odd, up = account(export, Cache(out / "cache"))[1:]
    assert (odd.status, odd.reason) == ("skipped", "认不出的档案格式（.htm）；档名不是 CP950，按 cp437 读")
    assert up.status == "skipped"
    assert up.reason == "认不出的档案格式（.txt）；路径不安全（../up.txt），只取内容、不照路径放"


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")  # zipfile, writing the fixture
def test_a_path_that_repeats_gets_a_key_of_its_own(tmp_path):
    z = zip_raw(tmp_path / "m.zip", [("dup.htm", b"one"), ("dup.htm", b"two")])
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", z.read_bytes())
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    accounts = account(export, Cache(out / "cache"))
    assert [(a.key, a.member, a.status) for a in accounts] == [
        ("9", "", "ok"), ("9!dup.htm", "dup.htm", "skipped"), ("9!dup.htm#2", "dup.htm", "skipped")]
    assert accounts[1].sha256 != accounts[2].sha256


def test_member_the_router_cannot_read_is_failed_alone(tmp_path, monkeypatch):
    # route_for reads a ZIP-like member's mimetype; a damaged deflate stream there must cost that member only. Changed
    # deliberately after the phase-1 review: route_for no longer lets the zlib.error out (an exported file hit it in
    # select() and account() too) but routes such a zip by its extension, so the member's own converter says why it
    # fails (LibreOffice is faked here, failing as it would on a damaged file)
    def broken_to_modern(src, kind, workdir, **_):
        raise ConvertError("LibreOffice 没产出可用的档（退出码 0）")

    monkeypatch.setattr(libreoffice, "to_modern", broken_to_modern)
    made = tmp_path / "made"
    made.mkdir()
    mimetype = b"application/vnd.oasis.opendocument.text" * 20
    odt = bytearray(zip_of(made / "bad.odt", {"mimetype": mimetype}).read_bytes())
    deflate = zlib.compressobj(-1, zlib.DEFLATED, -15)
    at = odt.index((deflate.compress(mimetype) + deflate.flush())[:6])
    odt[at:at + 6] = b"\xff" * 6
    z = zip_of(made / "m.zip", {"bad.odt": bytes(odt), "a.pdf": tiny_pdf(made / "a.pdf", [PDF_TEXT]).read_bytes()})
    export = one_row_export(tmp_path / "export", 9, "files/0005-m.zip", z.read_bytes())
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    container, bad, pdf = account(export, Cache(out / "cache"))
    assert (container.status, pdf.status, bad.status, bad.route) == ("ok", "ok", "failed", "office_old")
    assert bad.reason == "LibreOffice 没产出可用的档（退出码 0）" and bad.sha256 == hashlib.sha256(odt).hexdigest()


def test_member_with_the_bytes_of_a_page_is_said(tmp_path):
    # one cache entry per content: a member whose bytes an exported page already gave, read another way, is accounted
    z = zip_of(tmp_path / "m.zip", {"p.htm": PAGE_HTML})
    export = write_export(tmp_path / "export", [
        {"type": "网页", "url": PAGE_URL, "file": "pages/0001-a.html", "bytes": PAGE_HTML},
        {"type": "附件", "url": ZIP_URL, "file": "files/0001-m.zip", "bytes": z.read_bytes(), "fmt": "zip"}])
    out = tmp_path / "out"
    run(export, out, tools=fake_tools())
    accounts = {a.key: a for a in account(export, Cache(out / "cache"))}
    assert accounts["1"].status == "ok" and accounts["2"].status == "ok"
    member = accounts["2!p.htm"]
    assert (member.status, member.route) == ("skipped", "unsupported") and "和第 1 行是同一个档" in member.reason


def test_archive_route_needs_the_cache(tmp_path):
    z = zip_of(tmp_path / "m.zip", {"a.pdf": PDF})
    with pytest.raises(ValueError):
        convert_file(z, Route("archive", "zip"), fake_tools(), tmp_path / "w")
