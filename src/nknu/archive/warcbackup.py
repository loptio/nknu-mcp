"""Nightly copy of the raw crawl files (the WARC folder) to the backup disk.

Run as `python -m nknu.archive.warcbackup --dir <archive> --to <backup folder> [--check] [--first] [--quiet]`. On the
lab it is compose's service warc-backup: the source is mounted read-only there, and the schedule's file runs it last,
after the content's ingest, so nothing it does can hold up the night (deploy/lab-crawler/README.md).

Why (2026-11-23): the working copy moved to another disk and was then the only whole copy; some of the pages it holds
the school has since removed, so they cannot be fetched again.

What it keeps to:
- The backup only grows. A file the source no longer has stays. A backup file whose size is not the source's is kept
  beside the new copy as <name>.old-<time>, never overwritten.
- The source is only read.
- A file is written as <name>.part, read back, and given its name only when its SHA-256 is the source's. A source
  that changed while it was read (the crawl is still writing it) is left for the next time.
- Every time compares the whole tree by name and size, so a night that was missed is made up.
- No copy while a run holds run.lock, and none into an empty folder unless --first: a mount path typed wrong makes
  Docker create an empty folder without a word, and a second whole copy would go there.
- reports/warc-backup.json says "running" from the start and the result at the end (a copy killed halfway leaves
  "running", not the last good result); reports/warc-backup.log gets one line each time. update.log is not written:
  the panels cut the night from its lines.

Exit code: 0 the backup holds every file the source has; 1 it is behind (a file failed, a run was going); 3 nothing
was tried (an empty folder on either side, a folder that is not there).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

CHUNK = 4 * 1024 * 1024
STATUS = "warc-backup.json"
LOG = "warc-backup.log"
_PART = ".part"  # a copy not yet read back
_ASIDE = ".old-"  # a backup file that was not the source's size, kept beside the new copy
_LISTED = 20  # failed files named in the status file (the count of files behind is always whole)


class _NotKept(Exception):
    """This file's copy was not given its name; the message is the reason, for the status file."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _human(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    return f"{n / (1 << 20):.1f} MB"


def _sizes(top: Path) -> dict[str, int]:
    """Every regular file under top: path from top (with /) -> size. Links are passed over."""
    out: dict[str, int] = {}
    for folder, _dirs, names in os.walk(top):
        for name in names:
            p = Path(folder) / name
            if p.is_symlink() or not p.is_file():
                continue
            out[p.relative_to(top).as_posix()] = p.stat().st_size
    return out


def _ours(rel: str) -> bool:
    """A file this program left in the backup beside the copies: a half-written copy, or a file kept aside."""
    name = rel.rsplit("/", 1)[-1]
    return name.endswith(_PART) or _ASIDE in name


def run_held(root: Path) -> bool:
    """A run (archive run, jsfetch, jscheck) holds <root>/run.lock right now: it may still be writing a file."""
    try:
        fh = open(root / "run.lock")  # noqa: SIM115 - closed below
    except FileNotFoundError:
        return False
    with fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def _copy(src: Path, part: Path) -> tuple[str, int]:
    """Write src's bytes to part and force them out; (the SHA-256 of what was read, how many bytes)."""
    h = hashlib.sha256()
    n = 0
    with open(src, "rb") as fin, open(part, "wb") as fout:
        while chunk := fin.read(CHUNK):
            h.update(chunk)
            fout.write(chunk)
            n += len(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    return h.hexdigest(), n


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _keep(src: Path, dst: Path, stamp: str) -> tuple[int, bool]:
    """Copy one file and give it its name only once it reads back the same; (bytes, a file was kept aside)."""
    before = src.stat()
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + _PART)
    try:
        digest, n = _copy(src, part)
        after = src.stat()
        if n != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise _NotKept("原档还在写（拷的时候它变了），下一次再拷")
        if _digest(part) != digest:
            raise _NotKept("拷过去的档读回来和原档不一样（SHA-256 对不上）")
    except BaseException:
        part.unlink(missing_ok=True)  # this run's own half copy; nothing that had a name is removed
        raise
    aside = dst.exists()
    if aside:
        os.rename(dst, dst.with_name(f"{dst.name}{_ASIDE}{stamp}"))
    try:
        os.utime(part, ns=(after.st_atime_ns, after.st_mtime_ns))
    except OSError:  # a mount that does not take times: the copy is whole all the same
        pass
    os.replace(part, dst)
    return n, aside


def _write_status(root: Path, said: dict) -> None:
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    tmp = reports / (STATUS + ".tmp")
    tmp.write_text(json.dumps(said, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, reports / STATUS)


def back_up(root: Path, dst: Path, first: bool = False, check: bool = False) -> tuple[int, dict]:
    """One pass. (exit code, the status as written; with check nothing is written anywhere and nothing copied)."""
    src = root / "warc"
    t0 = time.monotonic()
    said = {"state": "done", "started_at": _now(), "ended_at": None, "ok": False, "why": None, "message": "",
            "in_source": 0, "in_backup": 0, "behind": 0, "copied": 0, "copied_bytes": 0, "set_aside": 0,
            "only_in_backup": 0, "failed": [], "seconds": None}

    def end(code: int, why: str | None, message: str) -> tuple[int, dict]:
        said.update(state="done", ended_at=_now(), ok=code == 0, why=why, message=message,
                    seconds=round(time.monotonic() - t0))
        if not check:
            _write_status(root, said)
            with open(root / "reports" / LOG, "a", encoding="utf-8") as fh:
                fh.write(f"{said['ended_at']} {message}\n")
        return code, said

    for folder, why in ((src, "source_missing"), (dst, "backup_folder_missing")):
        if not folder.is_dir():
            return end(3, why, f"{folder} 不在：什么都没拷")
    have = _sizes(src)
    there = {k: n for k, n in _sizes(dst).items() if not _ours(k)}
    todo = sorted(k for k, n in have.items() if there.get(k) != n)
    only = sum(1 for k in there if k not in have)
    said.update(in_source=len(have), in_backup=len(have) - len(todo), behind=len(todo), only_in_backup=only)
    extra = f"；另有 {only} 个档备份里有、原档那边没有（原档那边少了档？备份里的没动）" if only else ""
    if not have:
        return end(3, "source_empty", f"原档的资料夹 {src} 是空的：多半是挂载的路径写错了，什么都没拷")
    if not there and not first:
        return end(3, "backup_folder_empty",
                   f"备份的资料夹 {dst} 是空的，原档却有 {len(have)} 个档：多半是挂载的路径写错了（Docker 会自己建一个"
                   "空资料夹），什么都没拷。真的是第一次拷就加 --first")
    if not todo:
        return end(0, None, f"原档 {len(have)} 个档，备份里都有、大小都一样，这一次不用拷{extra}")
    if check:
        size = _human(sum(have[k] for k in todo))
        return end(1, None, f"原档 {len(have)} 个档，备份里少了或不一样的 {len(todo)} 个（{size}），要拷{extra}")
    if run_held(root):
        return end(1, "run_in_progress", f"有抓取正在跑（run.lock 被拿着），这一次不拷；备份落后 {len(todo)} 个档，下一次补{extra}")

    _write_status(root, said | {"state": "running", "ok": None, "message": f"正在拷 {len(todo)} 个档"})
    stamp = time.strftime("%Y%m%d-%H%M%S")
    failed: list[dict] = []
    for rel in todo:
        try:
            n, aside = _keep(src / rel, dst / rel, stamp)
        except _NotKept as e:
            failed.append({"file": rel, "why": str(e)})
        except OSError as e:
            failed.append({"file": rel, "why": f"{type(e).__name__}：{e.strerror or e}"})
        else:
            said["copied"] += 1
            said["copied_bytes"] += n
            said["set_aside"] += aside
    now = _sizes(dst)  # what the backup holds is read again: the count is not this run's own word
    held = sum(1 for k, n in have.items() if now.get(k) == n)
    said.update(in_backup=held, behind=len(have) - held, failed=failed[:_LISTED])
    aside_said = f"；其中 {said['set_aside']} 个备份里原来有、大小不一样，旧的留成 {_ASIDE}{stamp}" if said["set_aside"] else ""
    if said["behind"]:
        ex = f"（例：{failed[0]['file']}：{failed[0]['why']}）" if failed else ""
        return end(1, "copy_failed", f"原档备份没做完：该拷 {len(todo)} 个，拷成 {said['copied']} 个；备份落后 {said['behind']} 个档，"
                                     f"下一次会再补{ex}{aside_said}{extra}")
    return end(0, None, f"原档备份好了：新拷 {said['copied']} 个（{_human(said['copied_bytes'])}），每个都读回来对过 SHA-256；"
                        f"原档 {len(have)} 个档备份里都有了{aside_said}{extra}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把抓下来的原档（WARC）拷一份到备份的硬盘：只加不删，拷完读回来对校验码")
    ap.add_argument("--dir", required=True, help="存档的资料夹（原档在它下面的 warc/）")
    ap.add_argument("--to", required=True, help="备份放哪个资料夹")
    ap.add_argument("--check", action="store_true", help="只看要拷几个，什么都不拷、不写")
    ap.add_argument("--first", action="store_true", help="备份的资料夹是空的也拷（第一次，或换了一颗备份硬盘）")
    ap.add_argument("--quiet", action="store_true", help="不印结果（排程用：结果在 reports/warc-backup.json）")
    a = ap.parse_args(argv)
    code, said = back_up(Path(a.dir), Path(a.to), first=a.first, check=a.check)
    if not a.quiet:
        print(said["message"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
