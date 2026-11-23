"""实验室电脑上只做一次的修正（lab_pc 套件，11-03）：Mac 停线时打断的档放回「还没转」。

Why (facts read from the Mac's to-text cache and lane logs on 11-03): from 11-02 21:39 to 11-03 12:34 the Mac lanes
were stopped and restarted on purpose many times. A stop killed the converter without its cleanup, so the file it was
converting kept its 'started' marker, which the pipeline reads as "this file crashed the process": a run skips it
unless --retry-crashed (run.run), and an archive member with a marker is never tried again at all, even when its
archive is retried (convert._member: a member that really crashes the process must not loop). Left alone:
- lane C: six archives (序号 2162, 2167, 2170, 2174, 2178, 2192) have a result that lists their 教務會議附件 member
  failed, 「上次转到一半程序崩溃，重转压缩档时不再试这个成员」; the seventh, 2193, was cut at 12:34 while converting its
  附件, so with --retry-crashed it is extracted again and that member fails the same way. --retry-crashed reaches the
  export's files, never archive members.
- lane A: 2657 was cut by the stop at about 12:28. 2437 crashed the Mac process at 07:00 (lane A run 3 exit 139; each
  later run skipped one crashed file) and was being retried with RETRY_FIRST when the 12:32 stop cut it. 2437 comes
  before 2657 in lane A's order, so if it crashed again on the lab PC with --retry-crashed (run 1 only), the run would
  end before 2657 and every later run would skip 2657 for good.

What it does, per lane, only while an entry still looks exactly like that:
- lane A: removes the empty 'started' marker of 2437 and of 2657 (neither has a result.json). Both are then plain
  pending: each is tried once on the lab PC whether or not RETRY_FIRST is set, and one that crashes there gets a new
  marker and is skipped afterwards, as the pipeline intends.
- lane C: removes the member's empty 'started' marker (the member has no result.json); renames the archive's
  result.json, when it lists that member (same index and sha256) failed for that reason, to result.json.before-retry,
  so lane C extracts the archive again: the sibling member (教務會議紀錄, converted) is reused from the cache and the
  附件 converts, and the next save of that entry removes the renamed file. 2193 has no result: its own empty marker
  goes too, and it waits as an ordinary pending archive (in lane C's order, right after the six).
Anything that does not match is left alone and printed. Idempotent; --stamp records that it ran. lanes.sh runs it
when a lane starts and its stamp (lab_pc/retry_cut_members.<A|C>.done) is missing, before the lane's first run; each
lane touches only its own entries, so lanes A and C may start together.

--after-stop SINCE (fix stage, 11-03): the same repair for a forced stop on the lab PC itself (a power cut, a reboot,
docker killing the container when the 2-minute stop grace runs out). lanes.sh writes lab_pc/lane<X>.inflight (the
time a run started) before each run and removes it when the run ends however the program ended, so the note is left
only when the lane script itself was killed. Then the file that run was converting, and in lane C the archive and the
member it was converting, kept an empty 'started' marker: a run would skip the file (unless --retry-crashed) and the
member would be failed for good, though neither crashed anything. With SINCE = that note's time, it removes exactly
those markers: empty (the watchdog's carry a note), written at or after SINCE (a real crash in an earlier run is
older), of an entry with no result.json, and of this lane (--jobs, the inputs and their routes: lane A owns its pdf,
office and office_old files; lane C its archives and every entry that is no export file, i.e. the archive members). A
member whose bytes equal a lane-A file is lane A's and is left alone (none of the pending ones on 11-03). Plan
2026-11-08 T5: lane B (pages and images, which now run on the lab PC too) has this repair as well, and only this one
(its files have no members, and the Mac's stop cut none of them); --jobs then lists the inputs of every export of the
list (build_helpers.py jobs, keys <name>:<序号>), so a file of any export is told to its lane.

    /opt/venv/bin/python research/pipeline_2026-11-02/lab_pc/retry_cut_members.py --lane A|C \\
        --cache research/pipeline_2026-11-02/totext/cache [--dry-run] [--stamp FILE]
    /opt/venv/bin/python research/pipeline_2026-11-02/lab_pc/retry_cut_members.py --lane A|C|B \\
        --cache research/pipeline_2026-11-02/totext/cache --jobs JOBS.json --after-stop SINCE [--dry-run]
    (JOBS.json: python research/pipeline_2026-11-02/lab_pc/build_helpers.py jobs --out JOBS.json; lanes.sh does it)

Exit codes: 0 done (also when nothing was left to do), 2 refused (wrong place or bad arguments). Never run it on the
Mac: the Mac's cache is final (it refuses on macOS)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HINT = "重转压缩档时不再试这个成员"  # the end of accounting.MEMBER_CRASH_HINT (after its leading full-width comma)
RENAMED = "result.json.before-retry"
LANE_ROUTES = {"A": frozenset({"pdf", "office", "office_old"}), "C": frozenset({"archive"}),
               "B": frozenset({"page", "image", "data", "unsupported"})}  # as lanes.sh (B: --after-stop only)
SHA = re.compile(r"[0-9a-f]{64}")
# lane A: (序号 in the export, the file's sha256), from the Mac cache and totext_jobs.json
LANE_A = (
    ("2437", "6b06652dadd691e6ac5df514ad839123abc116a8b722d7c737607d37cfe2556f"),
    ("2657", "a0dd13f09de0a5330c9a7b2e11b38fd0fd99c8fbd0f2e9c28b0dd6e2461d5cb3"),
)
# lane C: (序号 of the archive, archive sha256, member index, member path, member sha256), from the Mac cache (2193's
# member: the file the 12:34 run had extracted, totext/work/10346-107243f8cb6dbfb7/members/0001.pdf)
LANE_C = (
    ("2162", "8f9f48d1f117f818de7919bd9eea5da349ab34991e483831d72324c0bbd903c3", 2, "11311教務會議附件.pdf",
     "deafb51337aeb20761f10241c9e8f2bfd542964ced91619f6cc3548d1b8557da"),
    ("2167", "ec5c55d89e3fa6ceca29dac4f6b339c2149dea750f3a0f6acc4d056cbe0b971e", 1, "11211教務會議附件.pdf",
     "2d0cc7c245d113b88a3289723a17c3e4a93960689199bd9076d0010be0796103"),
    ("2170", "4debfa31a6950fbf3624c8d33370adcec236dc9e946c925b992ee26fa6ecb2e9", 2, "11312教務會議附件.pdf",
     "f02a0a22e997f203962195943b643c22c0b5696e3b71c0c69c98b0623b5c5361"),
    ("2174", "25e4e60c65ce1283e021cda9973ad415a3cd0ffc3c463b7f9d3d71ad86ec6534", 1, "11021教務會議附件.pdf",
     "201467242061a3773d48fc4bfba6051b83ddcfec8cbcf073d25f0be5fe783595"),
    ("2178", "b2d23f6399e64bc1ab86fc17e81d773fc0c755004394ee00744cc3a531192063", 1, "10712教務會議附件.pdf",
     "e1b9735c7231f586a0d92985375a17eec3fe33c9a36eeb941f850773a1576205"),
    ("2192", "ba2cfca2a7a1e416ee4d45b57ce83aab92689e0fa6356e258047588c5fe8a6f1", 1, "10622教務會議附件.pdf",
     "a2380219dd5ec10a376667e2133a793ec756c5afb9f97851c652046b31bd5575"),
    ("2193", "107243f8cb6dbfb7c25977c91b9c56b91b5ba7c46c9df510689dc42cb46b7ca7", 1, "10621教務會議附件.pdf",
     "d0396bc8dd70d051d85832ddda572a5934e13a70ef6b52d4210788f839d9dd74"),
)

Action = tuple[str, Path, Path | None]  # ("remove", marker, None) or ("rename", result.json, its new name)


def entry(cache: Path, sha: str) -> Path:
    return cache / sha[:2] / sha


def empty_marker(folder: Path) -> bool | None:
    """True: an empty 'started' marker (a stop or a crash); False: one with a note (the watchdog's); None: none."""
    marker = folder / "started"
    if not marker.is_file():
        return None
    return not marker.read_bytes().strip()


def lists_cut_member(result: dict, index: int, sha: str) -> bool:
    """The archive's result names this member (index and sha256) failed for the marker a stop left."""
    for member in result.get("members") or []:
        if (isinstance(member, dict) and member.get("index") == index and member.get("sha256") == sha
                and member.get("status") == "failed" and HINT in str(member.get("reason") or "")):
            return True
    return False


def plan_a(cache: Path) -> tuple[list[Action], list[str]]:
    actions: list[Action] = []
    notes: list[str] = []
    for key, sha in LANE_A:
        folder = entry(cache, sha)
        state = empty_marker(folder)
        if (folder / "result.json").is_file():
            notes.append(f"第 {key} 行：已经有结果，不用处理")
        elif state is True:
            actions.append(("remove", folder / "started", None))
        elif state is False:
            notes.append(f"第 {key} 行：中断标记里有说明（看门狗结束过），不动")
        else:
            notes.append(f"第 {key} 行：没有中断标记，已经是「还没转」")
    return actions, notes


def plan_c(cache: Path) -> tuple[list[Action], list[str]]:
    actions: list[Action] = []
    notes: list[str] = []
    for key, archive, index, path, member in LANE_C:
        mine = entry(cache, member)
        if (mine / "result.json").is_file():
            notes.append(f"第 {key} 行压缩档的成员 {path}：已经有结果，不用处理")
            continue
        state = empty_marker(mine)
        if state is False:
            notes.append(f"第 {key} 行压缩档的成员 {path}：中断标记里有说明（看门狗结束过），不动")
            continue
        if state is True:
            actions.append(("remove", mine / "started", None))
        folder = entry(cache, archive)
        result = folder / "result.json"
        if not result.is_file():
            own = empty_marker(folder)
            if (folder / RENAMED).is_file():
                notes.append(f"第 {key} 行压缩档的结果已经改名，等 C 线重新解开、转成员 {path}")
            elif own is True:
                # the stop cut the archive itself too (2193, 12:34): pending again, converted in its turn by whatever
                # run reaches it (a crash marker is retried only with RETRY_FIRST: 11-03 in run 1 only, which may end
                # at --max-minutes before it; since the review of plan 2026-11-08 T5 once in a lane start)
                actions.append(("remove", folder / "started", None))
            elif own is None:
                notes.append(f"第 {key} 行压缩档还没转，C 线轮到它时连成员 {path} 一起转")
            else:
                notes.append(f"第 {key} 行压缩档的中断标记里有说明（看门狗结束过），不动（成员 {path} 跟着它）")
            continue
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            notes.append(f"第 {key} 行压缩档的结果读不了（{type(e).__name__}），不动")
            continue
        if isinstance(data, dict) and lists_cut_member(data, index, member):
            actions.append(("rename", result, result.with_name(RENAMED)))
        else:
            notes.append(f"第 {key} 行压缩档的结果没有把成员 {path} 记成「转到一半中断」，不动")
    return actions, notes


def plan_after_stop(cache: Path, lane: str, rows: list, since: float) -> tuple[list[Action], list[str]]:
    """The empty 'started' markers a forced stop left: written at or after since, of this lane's entries, no result."""
    routes = LANE_ROUTES[lane]
    keys = {r["sha"]: str(r.get("key") or "") for r in rows if isinstance(r, dict) and "sha" in r}
    mine = {r["sha"] for r in rows if isinstance(r, dict) and r.get("route") in routes}
    others = set(keys) - mine
    actions: list[Action] = []
    notes: list[str] = []
    for marker in sorted(cache.glob("*/*/started")):
        folder = marker.parent
        sha = folder.name
        if not SHA.fullmatch(sha) or folder.parent.name != sha[:2] or sha in others:
            continue
        if sha not in mine and lane != "C":  # entries that are no export file are archive members: lane C's
            continue
        what = f"第 {keys[sha]} 行" if keys.get(sha) else f"压缩档成员 {sha[:12]}…"
        if (folder / "result.json").is_file():
            continue  # a marker beside a result counts for nothing (Cache.crashed)
        try:
            written = marker.stat().st_mtime
            note = marker.read_bytes().strip()
        except OSError:
            continue
        if written < since:
            continue  # older than the run the stop cut: a real crash or the watchdog, kept as the pipeline intends
        if note:
            notes.append(f"{what}：中断标记里有说明（看门狗结束过），不动")
            continue
        actions.append(("remove", marker, None))
    return actions, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mac 停线时打断的档放回「还没转」（实验室电脑上每条线只做一次）")
    parser.add_argument("--lane", choices=("A", "C", "B"), required=True,
                        help="A：两个 PDF；C：七个压缩档的成员；B：只有 --after-stop（网页、图片）")
    parser.add_argument("--cache", type=Path, required=True, help="转文字缓存（research/pipeline_2026-11-02/totext/cache）")
    parser.add_argument("--dry-run", action="store_true", help="只列出要做什么，不改")
    parser.add_argument("--stamp", type=Path, help="做完写这个档（lanes.sh 看到它就不再跑）")
    parser.add_argument("--after-stop", metavar="SINCE", help="强制结束之后：lab_pc/lane<X>.inflight 里记的那一轮开始时间"
                        "（Unix 秒数），把那之后留下的空中断标记放回「还没转」")
    parser.add_argument("--jobs", type=Path, help="清单里每份导出的档（build_helpers.py jobs 写的；--after-stop 用它分哪些档是这条线的）")
    parser.add_argument("--allow-mac", action="store_true", help=argparse.SUPPRESS)  # tests on a scratch copy only
    args = parser.parse_args(argv)
    if sys.platform == "darwin" and not args.allow_mac:
        print("这是给实验室电脑（容器里）用的；Mac 上的转文字缓存已经定稿，不改", file=sys.stderr)
        return 2
    if not args.cache.is_dir():
        print(f"找不到转文字缓存：{args.cache}", file=sys.stderr)
        return 2
    if args.after_stop is not None:
        try:
            since = float(args.after_stop.strip().replace(",", "."))  # bash's EPOCHREALTIME uses the locale's mark
            rows = json.loads(args.jobs.read_text(encoding="utf-8")) if args.jobs is not None else None
        except (OSError, ValueError) as e:
            print(f"--after-stop 要一个时间（秒数）和 --jobs 读得了的档清单：{type(e).__name__}: {e}",
                  file=sys.stderr)
            return 2
        if not isinstance(rows, list) or args.stamp is not None:
            print("--after-stop 要 --jobs（build_helpers.py jobs 写的档清单），不带 --stamp", file=sys.stderr)
            return 2
        actions, notes = plan_after_stop(args.cache, args.lane, rows, since)
    elif args.lane == "B":
        print("B 线只有强制结束之后的修正（--after-stop）：Mac 停线时没有打断网页和图片", file=sys.stderr)
        return 2
    else:
        actions, notes = (plan_a if args.lane == "A" else plan_c)(args.cache)
    done = []
    for what, path, new in actions:
        line = (f"删掉中断标记 {path.relative_to(args.cache)}" if what == "remove"
                else f"压缩档结果改名 {path.relative_to(args.cache)} -> {RENAMED}（重新解开、转那个成员）")
        if not args.dry_run:
            if what == "remove":
                path.unlink(missing_ok=True)
            else:
                path.replace(new)
        done.append(line)
        print(("（试跑，没改）" if args.dry_run else "") + line, flush=True)
    for note in notes:
        print(note, flush=True)
    which = "强制结束后的中断档修正" if args.after_stop is not None else "中断档修正"
    print(f"线 {args.lane} {which}：{'要做' if args.dry_run else '做了'} {len(done)} 件，没动 {len(notes)} 件", flush=True)
    if args.stamp is not None and not args.dry_run:
        args.stamp.parent.mkdir(parents=True, exist_ok=True)
        args.stamp.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S %z')}\n" + "".join(f"{d}\n" for d in done)
                              + "".join(f"{n}\n" for n in notes), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
