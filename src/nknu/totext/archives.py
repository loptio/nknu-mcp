"""The members of an exported archive (plan 2026-11-02 T5; review focus 5: everything in an archive is untrusted).
extract() writes each member of a ZIP or RAR into a folder of its own as <index:04d><.ext>, never at a path the archive
names, and returns one Member per member, directories aside: ok (written), skipped (a symbolic link or another file
that is not a plain file) or failed (encrypted, over a size cap, unreadable, not extracted), with the reason in
Simplified Chinese. An archive with more than MAX_MEMBERS members, or more than MAX_TOTAL_BYTES declared in all, is
not opened at all (ArchiveError, as for an archive that cannot be read: str() is the container's failure reason).

ZIP goes through zipfile, each member read in 1 MiB blocks and stopped at its cap: declared sizes are not trusted.
Names: UTF-8 when the member is flagged so, the Info-ZIP Unicode Path field when it has one, else its raw bytes as
UTF-8 (zips made on macOS do not set the flag), then as CP950 (Windows in Taiwan), else as cp437 with a note. RAR goes
through bsdtar (libarchive): its listing gives the count and the sizes, then it extracts into a fresh folder (bsdtar
refuses a name with '..' and drops a leading '/'), which is walked without following links; each listed member that
did not come out is failed with bsdtar's own message, and a file that came out unlisted is still a member."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

MAX_MEMBERS = 1_000
MAX_MEMBER_BYTES = 512 * 2**20
MAX_TOTAL_BYTES = 2 * 2**30
BSDTAR_TIMEOUT_S = 300
BLOCK = 2**20
MESSAGE_CHARS = 200  # an error message kept in a reason
UNSAFE = "路径不安全（{path}），只取内容、不照路径放"
CP437_NOTE = "档名不是 CP950，按 cp437 读"
SYMLINK = "符号链接，不取"
NOT_A_FILE = "不是一般档案，不取"
ENCRYPTED = "加密的成员"
TOO_BIG = "超过大小上限（{cap} bytes）"
TOTAL_TOO_BIG = "压缩档解开的总量超过上限（{cap} bytes）"
DUPLICATE = "压缩档里有同名的档，只解出前一份"
_EXT = re.compile(r"[a-z0-9]{1,5}")
_SEP = re.compile(r"[\\/]")
_DRIVE = re.compile(r"[A-Za-z]:")
# one line of `bsdtar -tv`: mode, links, owner, group, size (major,minor for a device), date in three words, name
_LISTED = re.compile(r"(?P<mode>\S+)\s+\d+\s+\S+\s+\S+\s+(?P<size>[\d,]+)\s+\S+\s+\S+\s+\S+ (?P<name>.*)")
_ESCAPE = re.compile(r"\\([abfnrtv\\]|[0-3][0-7]{2})")
_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92}


@dataclass(frozen=True)
class Member:
    index: int  # 1-based, in archive order, directories aside
    path: str  # the member's name in the archive
    size: int  # bytes written; for a member not written, its declared size
    local: Path | None  # dest/<index:04d><.ext>, or None when it was not written
    status: str  # ok (written: its conversion says the rest), skipped or failed
    reason: str  # why it was not written; for a written member a note on its name ('' when none)


class ArchiveError(Exception):
    """The archive as a whole cannot be read, or is over a cap; str() is the reason (Simplified Chinese)."""


class TooBig(Exception):
    """copy_capped read more than its cap."""


@dataclass(frozen=True)
class _Listed:
    shown: str  # the name as bsdtar printed it (with its escapes)
    name: str  # the name, escapes undone
    type: str  # the mode's first character: - d l h c b p s
    size: int


def extension(path: str) -> str:
    """The extension of the member's file name, lower-cased, with its dot; '' when it has none."""
    return PurePosixPath(_SEP.split(path)[-1]).suffix.lower()


def local_name(index: int, path: str) -> str:
    """<index:04d>, plus the member's extension when it is [a-z0-9]{1,5}."""
    ext = extension(path)[1:]
    return f"{index:04d}" + (f".{ext}" if _EXT.fullmatch(ext) else "")


def unsafe(path: str) -> bool:
    """A name that would leave the folder it is unpacked in: a '..' part, a leading '/' or '\\', a drive letter."""
    return path.startswith(("/", "\\")) or ".." in _SEP.split(path) or bool(_DRIVE.match(path))


def copy_capped(src: BinaryIO, dst: BinaryIO, cap: int) -> int:
    """src into dst in BLOCK reads; the bytes copied. Raises TooBig once more than cap bytes came."""
    written = 0
    while block := src.read(BLOCK):
        written += len(block)
        if written > cap:
            raise TooBig(written)
        dst.write(block)
    return written


def _brief(e: BaseException) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:MESSAGE_CHARS]


def _clean(name: str) -> str:
    """A name fit for JSON: bytes that were not UTF-8 (surrogate escapes) become U+FFFD."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _notes(path: str, *more: str) -> str:
    return "；".join([UNSAFE.format(path=path)] * unsafe(path) + [note for note in more if note])


def _fresh_dest(dest: Path) -> Path:
    dest = Path(dest)
    if (dest.exists() or dest.is_symlink()) and (not dest.is_dir() or any(dest.iterdir())):
        raise ArchiveError(f"解开压缩档的资料夹不是新的（{dest}）")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _check_caps(count: int, total: int) -> None:
    if count > MAX_MEMBERS:
        raise ArchiveError(f"成员太多（{count} 份，上限 {MAX_MEMBERS} 份）")
    if total > MAX_TOTAL_BYTES:
        raise ArchiveError(f"解开后共 {total} bytes，超过上限（{MAX_TOTAL_BYTES} bytes）")


def extract(path: Path, kind: str, dest: Path) -> list[Member]:
    """The members of the archive at path (kind: zip or rar, as route_for names it) written into dest, which must not
    exist or be empty."""
    if kind == "zip":
        return zip_members(path, dest)
    if kind == "rar":
        return rar_members(path, dest)
    raise ArchiveError(f"不认识的压缩档格式（{kind}）")


# --- ZIP ---

def _zip_name(info: zipfile.ZipInfo) -> tuple[str, str]:
    """(the member's name, a note). zipfile, opened as cp437 (every byte maps to one character), already read a
    UTF-8-flagged name and an Info-ZIP Unicode Path field; any other name is decoded from its raw bytes."""
    orig = info.orig_filename.split("\x00", 1)[0]  # zipfile cuts a name at a NUL as well
    if info.flag_bits & 0x800 or info.filename != (orig.replace("\\", "/") if os.sep == "\\" else orig):
        return info.filename, ""
    raw = orig.encode("cp437")
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return orig, CP437_NOTE


def _zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, index: int, name: str, note: str, dest: Path,
                room: int) -> Member:
    """One member; room: what MAX_TOTAL_BYTES leaves after the members before it."""
    def failed(reason: str, size: int = info.file_size) -> Member:
        return Member(index, name, size, None, "failed", reason)

    if stat.S_ISLNK(info.external_attr >> 16):
        return Member(index, name, info.file_size, None, "skipped", SYMLINK)
    if info.flag_bits & 0x41:  # 0x1 encrypted, 0x40 strong encryption
        return failed(ENCRYPTED)
    if info.file_size > MAX_MEMBER_BYTES:
        return failed(TOO_BIG.format(cap=MAX_MEMBER_BYTES))
    if info.file_size > room:
        return failed(TOTAL_TOO_BIG.format(cap=MAX_TOTAL_BYTES))
    cap = min(MAX_MEMBER_BYTES, room)
    local = dest / local_name(index, name)
    try:
        with zf.open(info) as src, local.open("wb") as dst:
            size = copy_capped(src, dst, cap)
    except TooBig as e:
        local.unlink(missing_ok=True)
        return failed(TOO_BIG.format(cap=MAX_MEMBER_BYTES) if cap == MAX_MEMBER_BYTES
                      else TOTAL_TOO_BIG.format(cap=MAX_TOTAL_BYTES), e.args[0])
    except NotImplementedError as e:  # a compression method or encryption zipfile does not read
        local.unlink(missing_ok=True)
        return failed(f"压缩方式不支持（{_brief(e)}）")
    except Exception as e:  # noqa: BLE001 - a damaged member (bad data, CRC, header) costs that member only
        local.unlink(missing_ok=True)
        return failed(f"解不开（{_brief(e)}）")
    return Member(index, name, size, local, "ok", _notes(name, note))


def zip_members(path: Path, dest: Path) -> list[Member]:
    dest = _fresh_dest(dest)
    try:
        zf = zipfile.ZipFile(path, metadata_encoding="cp437")
    except Exception as e:  # whatever zipfile raises on a broken archive is the container's failure
        raise ArchiveError(f"压缩档打不开（{_brief(e)}）") from e
    with zf:
        files = []
        for info in zf.infolist():
            name, note = _zip_name(info)
            if info.is_dir() or (name.endswith("\\") and info.file_size == 0):  # a folder (Windows writes '\')
                continue
            files.append((info, name, note))
        _check_caps(len(files), sum(info.file_size for info, _name, _note in files))
        members, total = [], 0
        for index, (info, name, note) in enumerate(files, start=1):
            member = _zip_member(zf, info, index, name, note, dest, MAX_TOTAL_BYTES - total)
            total += member.size if member.local else 0
            members.append(member)
    return members


# --- RAR (bsdtar) ---

def _bsdtar(argv: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C.UTF-8" if sys.platform.startswith("linux") else "en_US.UTF-8"}
    try:
        return subprocess.run(argv, capture_output=True, env=env, timeout=BSDTAR_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        raise ArchiveError(f"bsdtar 超时（{BSDTAR_TIMEOUT_S} 秒）") from e
    except FileNotFoundError as e:
        raise ArchiveError("没有 bsdtar（要装 libarchive 的 bsdtar）") from e
    except OSError as e:
        raise ArchiveError(f"bsdtar 跑不起来（{_brief(e)}）") from e


def _unescape(shown: str) -> str:
    """A name as bsdtar printed it, its escapes undone (\\t, \\n, …, \\\\, and \\ooo for each byte it cannot print)."""
    raw, at = bytearray(), 0
    for m in _ESCAPE.finditer(shown):
        raw += shown[at:m.start()].encode("utf-8", "surrogateescape")
        code = m.group(1)
        raw.append(_ESCAPES[code] if code in _ESCAPES else int(code, 8))
        at = m.end()
    raw += shown[at:].encode("utf-8", "surrogateescape")
    return raw.decode("utf-8", "surrogateescape")


def _listing(text: str) -> list[_Listed]:
    """`bsdtar -tv` lines (split on '\\n' only: bsdtar escapes the other line breaks). bsdtar prints a symbolic link
    as "name -> target" and a hard link as "name link to <a name listed before>". A line it cannot parse still
    counts, as a member of unknown kind."""
    out: list[_Listed] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        if not line:
            continue
        m = _LISTED.fullmatch(line)
        if m is None:
            out.append(_Listed(line, _unescape(line), "?", 0))
            continue
        kind, shown = m["mode"][0], m["name"]
        if kind == "l":
            shown = shown.split(" -> ", 1)[0]
        elif " link to " in shown:
            name, _sep, target = shown.rpartition(" link to ")
            shown = name if target in seen else shown
        seen.add(shown)
        size = int(m["size"]) if m["size"].isdigit() else 0
        out.append(_Listed(shown, _unescape(shown), kind, size))
    return out


def _key(name: str) -> str | None:
    """Where bsdtar writes a member under its -C folder, as a lookup key ('', '.' and so a leading '/' dropped);
    None for a name with '..', which bsdtar refuses."""
    parts = [p for p in name.split("/") if p not in ("", ".")]
    return None if ".." in parts else unicodedata.normalize("NFC", "/".join(parts))


def _walk(root: Path) -> list[tuple[str, os.stat_result]]:
    """Everything under root but directories, as (path relative to root, lstat), sorted. Links are never followed;
    each real directory is made 0o700 before it is read (bsdtar gives it the archive's mode)."""
    out, pending = [], [""]
    while pending:
        rel = pending.pop()
        with os.scandir(root / rel if rel else root) as entries:
            for entry in entries:
                sub = f"{rel}/{entry.name}" if rel else entry.name
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    os.chmod(entry.path, 0o700)  # lstat said directory: no link is followed
                    pending.append(sub)
                else:
                    out.append((sub, st))
    return sorted(out, key=lambda item: item[0])


def _missing(entry: _Listed, errors: list[str], code: int) -> str:
    """Why a listed member did not come out: bsdtar's message on it ("<name>: <message>"), else its exit code."""
    for line in errors:
        text = line.removeprefix("bsdtar: ")
        if text.startswith(entry.shown + ": "):
            message = text[len(entry.shown) + 2:].strip()
            return ENCRYPTED if "ncrypt" in message else f"没解出来（{message[:MESSAGE_CHARS]}）"
    return f"没解出来（bsdtar 退出码 {code}）"


def _moved(index: int, name: str, work: Path, rel: str, st: os.stat_result, dest: Path, total: int) -> Member:
    """A file bsdtar wrote at work/rel, moved to dest/<index><.ext> when it is a plain file within the caps."""
    if stat.S_ISLNK(st.st_mode):
        return Member(index, name, 0, None, "skipped", SYMLINK)
    if not stat.S_ISREG(st.st_mode):
        return Member(index, name, 0, None, "skipped", NOT_A_FILE)
    if st.st_size > MAX_MEMBER_BYTES:
        return Member(index, name, st.st_size, None, "failed", TOO_BIG.format(cap=MAX_MEMBER_BYTES))
    if total + st.st_size > MAX_TOTAL_BYTES:
        return Member(index, name, st.st_size, None, "failed", TOTAL_TOO_BIG.format(cap=MAX_TOTAL_BYTES))
    local = dest / local_name(index, name)
    os.replace(work / rel, local)
    os.chmod(local, 0o600)
    return Member(index, name, st.st_size, local, "ok", _notes(name))


def _match(listed: list[_Listed], disk: list[tuple[str, os.stat_result]], work: Path, dest: Path, errors: list[str],
           code: int) -> list[Member]:
    """Listed members in listing order, each with the file bsdtar wrote for it; then files nobody listed."""
    by_key: dict[str, tuple[str, os.stat_result]] = {}
    extra = []
    for rel, st in disk:
        key = unicodedata.normalize("NFC", rel)
        if key in by_key:
            extra.append((rel, st))
        else:
            by_key[key] = (rel, st)
    members: list[Member] = []
    claimed: set[str] = set()
    total = 0
    for entry in listed:
        index, name, key = len(members) + 1, _clean(entry.name), _key(entry.name)
        if entry.type == "l":
            claimed.add(key or "")
            member = Member(index, name, 0, None, "skipped", SYMLINK)
        elif key is not None and key in claimed:
            member = Member(index, name, entry.size, None, "failed", DUPLICATE)
        elif key is None or key not in by_key:
            member = Member(index, name, entry.size, None, "failed", _missing(entry, errors, code))
        else:
            claimed.add(key)
            member = _moved(index, name, work, *by_key[key], dest, total)
        total += member.size if member.local else 0
        members.append(member)
    extra += [item for key, item in by_key.items() if key not in claimed]
    for rel, st in sorted(extra, key=lambda item: item[0]):
        member = _moved(len(members) + 1, _clean(rel), work, rel, st, dest, total)
        total += member.size if member.local else 0
        members.append(member)
    return members


def rar_members(path: Path, dest: Path) -> list[Member]:
    """Through bsdtar, which reads ZIP, 7z and tar as well (the to-text run sends it RAR only)."""
    dest = _fresh_dest(dest)
    shown = _bsdtar(["bsdtar", "-tvf", str(path)])
    listed = _listing(shown.stdout.decode("utf-8", "surrogateescape"))
    if shown.returncode != 0 and not listed:
        first = next((line.strip() for line in shown.stderr.decode("utf-8", "replace").splitlines() if line.strip()),
                     f"bsdtar 退出码 {shown.returncode}")
        raise ArchiveError(f"压缩档打不开（{first[:MESSAGE_CHARS]}）")
    files = [entry for entry in listed if entry.type != "d"]
    _check_caps(len(files), sum(entry.size for entry in files))
    work = Path(tempfile.mkdtemp(prefix=".bsdtar-", dir=dest))
    try:
        done = _bsdtar(["bsdtar", "-x", "-k", "-C", str(work), "-f", str(path)])
        errors = done.stderr.decode("utf-8", "surrogateescape").split("\n")
        return _match(files, _walk(work), work, dest, errors, done.returncode)
    finally:
        shutil.rmtree(work, ignore_errors=True)
