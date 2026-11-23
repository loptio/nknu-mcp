"""`python -m nknu.totext run|report` (plan 2026-11-02 T3b). run converts, one file at a time, every selected input
whose content hash has no result in <out>/cache, and keeps no other state: a rerun resumes, and two runs with
disjoint --routes can share one --out (each works in <out>/work/<its pid>-…). A file that crashed the process, or that
the watchdog ended after HANG_S seconds without progress (review focus 2: a hang in native code that no timeout
reaches), keeps its 'started' marker; the next run accounts it failed with that reason and does not try it again
unless --retry-crashed, or --retry-crashed-before T for a marker written before T (the lab PC's lanes, review of plan
2026-11-08 T5: a crash of an earlier lane start is tried once more, one in this lane start is not, whichever export
holds the file). run prints one summary line and writes no report; report reads the cache back
(accounting.account) and writes accounting.jsonl, summary.json and summary.md into <out>. --out never lies inside the
export, ./data, archive*/ or research/skeleton_2026-11-01/."""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nknu.settings import REPO_ROOT
from nknu.totext.accounting import (
    STATUS_WORDS,
    Input,
    account,
    input_route,
    inputs,
    locate,
    project_totals,
    write_report,
)
from nknu.totext.cache import STARTED, Cache, sha256_file
from nknu.totext.convert import SetupError, Tools, convert_and_save
from nknu.totext.docling_convert import DOC_TIMEOUT_S, KINDS
from nknu.totext.libreoffice import TARGET
from nknu.totext.route import ROUTES, Route

HANG_S = DOC_TIMEOUT_S + 120  # no progress for this long: the watchdog ends the process with exit code 3
EXIT_HANG = 3
EXIT_MORE = 4  # --max-minutes ran out between two files: files are left, the lane script starts a fresh process
CONVERTIBLE = ("page", "pdf", "office", "office_old", "image", "archive")  # what --sample draws from
PROGRESS_EVERY = 100  # a progress line on stderr after this many conversions


def _docling_kind(route: Route) -> str | None:
    """The format Docling reads for this route (an old Office file after LibreOffice), or None."""
    if route.name == "pdf":
        return "pdf"
    if route.name == "office":
        return route.kind
    if route.name == "office_old":
        return TARGET.get(route.kind)
    return None


def _warm_kinds(route: Route) -> tuple[str, ...]:
    """What Tools.warm_up loads before the first file of this route that needs converting: Docling's pipeline for its
    format (for pdf the OCR-on converter's, and the OCR engine), the OCR engine for an image, and for an archive every
    format a member may need (its PDFs and images included)."""
    if route.name == "image":
        return ("ocr",)
    if route.name == "archive":
        return KINDS
    kind = _docling_kind(route)
    return (kind,) if kind else ()


class Watchdog:
    """Calls on_expire once when feed() has not been called for `seconds`: a daemon thread that sleeps until the
    deadline and looks again, so feed() is one assignment. stop() ends it."""

    def __init__(self, seconds: float, on_expire: Callable[[], None]):
        self.seconds = seconds
        self._on_expire = on_expire
        self._deadline = time.monotonic() + seconds
        self._stopped = threading.Event()
        threading.Thread(target=self._watch, name="totext-watchdog", daemon=True).start()

    def feed(self) -> None:
        self._deadline = time.monotonic() + self.seconds

    def stop(self) -> None:
        self._stopped.set()

    def _watch(self) -> None:
        while True:
            left = self._deadline - time.monotonic()
            if left <= 0:
                if not self._stopped.is_set():
                    self._on_expire()
                return
            if self._stopped.wait(left):
                return


@dataclass(frozen=True)
class Job:
    inp: Input
    path: Path
    route: Route
    sha: str


def refuse_out(out: Path, export_dir: Path) -> None:
    """ValueError when out lies inside the export, ./data, archive*/ (existing or not) or research/skeleton_2026-11-01/."""
    target, repo = Path(out).resolve(), REPO_ROOT.resolve()
    roots = [Path(export_dir).resolve(), (repo / "data").resolve(),
             (repo / "research" / "skeleton_2026-11-01").resolve(), *(p.resolve() for p in repo.glob("archive*"))]
    inside = any(target == r or r in target.parents for r in roots)
    if not inside and repo in target.parents:
        inside = target.relative_to(repo).parts[0].startswith("archive")
    if inside:
        raise ValueError(f"--out 不能放在导出资料夹、./data、archive*/ 或 research/skeleton_2026-11-01/ 里面：{out}")


def _check_routes(routes: set[str] | None) -> set[str] | None:
    if routes is None:
        return None
    unknown = sorted(set(routes) - set(ROUTES))
    if unknown:
        raise ValueError(f"不认识的路线：{', '.join(unknown)}（可用：{', '.join(ROUTES)}）")
    return set(routes)


def _sample(jobs: list[Job], n: int, seed: int) -> list[Job]:
    """n inputs of the convertible routes, one route after another in ROUTES order, each route's inputs (one per
    content hash) shuffled once by random.Random(seed); returned in manifest order."""
    rng = random.Random(seed)
    pools = []
    for name in ROUTES:
        if name not in CONVERTIBLE:
            continue
        seen: set[str] = set()
        pool = []
        for job in jobs:
            if job.route.name == name and job.sha not in seen:
                seen.add(job.sha)
                pool.append(job)
        rng.shuffle(pool)
        pools.append(pool)
    picked: list[Job] = []
    depth = 0
    while len(picked) < n and any(len(pool) > depth for pool in pools):
        picked += [pool[depth] for pool in pools if len(pool) > depth][:n - len(picked)]
        depth += 1
    order = {id(j): i for i, j in enumerate(jobs)}
    return sorted(picked, key=lambda j: order[id(j)])


def read_defer(path: Path | None) -> frozenset[str]:
    """The URLs listed in path (UTF-8, one per line; blank lines and lines starting with # skipped); empty for None."""
    if path is None:
        return frozenset()
    lines = (line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines())
    return frozenset(line for line in lines if line and not line.startswith("#"))


def select(export_dir: Path, *, routes: set[str] | None = None, sample: int | None = None, seed: int = 1102,
           limit: int | None = None, defer: frozenset[str] = frozenset()) -> tuple[list[Job], int]:
    """(the jobs this run goes through, how many inputs the export has). Inputs whose file is missing are left out
    (the accounting says why). Inputs whose URL is in defer go last, each group in export order (11-02: journals and
    past exam papers after the documents students ask about, on a machine where the full run takes a day)."""
    all_inputs = inputs(export_dir)
    jobs = []
    for inp in all_inputs:
        path, _why = locate(export_dir, inp)
        if path is None:
            continue
        route = input_route(inp, path)
        if routes is None or route.name in routes:
            jobs.append(Job(inp, path, route, sha256_file(path)))
    if defer:
        jobs = [j for j in jobs if j.inp.url not in defer] + [j for j in jobs if j.inp.url in defer]
    if sample is not None:
        jobs = _sample(jobs, sample, seed)
    if limit is not None:
        jobs = jobs[:max(limit, 0)]
    return jobs, len(all_inputs)


def _counts(by_status: Counter) -> str:
    return "、".join(f"{s} {by_status[s]}" for s in STATUS_WORDS if by_status[s]) or "没有"


def _crashed_before(cache: Cache, sha: str, before: float | None) -> bool:
    """The file's 'started' marker was written before `before` (seconds since the epoch; None: never). Cache.begin
    writes it when a conversion starts, and the watchdog writes it again when it ends one, so a file that crashes or
    hangs after `before` has a newer marker. A marker gone meanwhile is no crash any more: the file is converted."""
    if before is None:
        return False
    try:
        return (cache.dir(sha) / STARTED).stat().st_mtime < before
    except FileNotFoundError:
        return True


def _epoch(value: str) -> float:
    """--retry-crashed-before: seconds since the epoch, a decimal comma too (bash's EPOCHREALTIME in such a locale)."""
    try:
        seconds = float(value.strip().replace(",", "."))
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds):
        raise argparse.ArgumentTypeError(f"要一个时间（Unix 秒数，可以带小数），不是「{value}」")
    return seconds


def run(export_dir: Path, out: Path, *, routes: set[str] | None = None, sample: int | None = None, seed: int = 1102,
        limit: int | None = None, retry_crashed: bool = False, retry_crashed_before: float | None = None,
        tools: Tools | None = None, defer: frozenset[str] = frozenset(), max_minutes: float | None = None) -> dict:
    """Converts the selected inputs into <out>/cache; returns {"inputs", "selected" (keys), "converted", "cached",
    "crashed" (left as they are), "statuses" (of this run's conversions), "warm_up_seconds" (Docling's pipelines and
    the OCR engine loading, before the first file of each route that needs them: in no file's time), "seconds",
    "stopped_early"}. With max_minutes the run stops between two files once that much time has passed and files are
    left (stopped_early; 11-03: one process grew to 6.3 GB over a few hours, and a fresh one gives it back).
    A file with a crash marker is skipped unless retry_crashed, or unless its marker was written before
    retry_crashed_before (review of plan 2026-11-08 T5: lanes.sh passes its own start time to every run, so each file
    that crashed before the lane started is tried once in it, and one that crashes again then is skipped by every
    later run, of this export or another holding the same bytes). Raises SetupError when Docling or the OCR engine
    cannot load (nothing of that route is marked)."""
    export_dir, out = Path(export_dir), Path(out).resolve()  # tools given a work folder may need it absolute
    refuse_out(out, export_dir)
    jobs, total = select(export_dir, routes=_check_routes(routes), sample=sample, seed=seed, limit=limit, defer=defer)
    cache = Cache(out / "cache")
    tools = tools or Tools()
    info = tools.describe()  # read before the watchdog starts: the first file gets the whole HANG_S
    seconds = HANG_S
    current: list[Job | str | None] = [None]  # the file converting now, or what else runs (a model loading)

    def expire() -> None:
        job = current[0]
        doing = job.inp.file if isinstance(job, Job) else job or "（两个档之间）"
        print(f"看门狗：{doing} 超过 {seconds:g} 秒没有进展，结束程序", file=sys.stderr, flush=True)
        if isinstance(job, Job):
            try:
                cache.begin(job.sha, note=f"转换超过 {seconds:g} 秒没有进展，看门狗结束了程序")
            except OSError:
                pass
        os._exit(EXIT_HANG)

    stats: Counter = Counter()
    by_status: Counter = Counter()
    warm = 0.0
    started = time.monotonic()
    own_beat = tools.beat
    dog = Watchdog(seconds, expire)

    def beat() -> None:
        dog.feed()
        own_beat()

    tools.beat = beat
    stopped_early = False
    try:
        for n, job in enumerate(jobs, start=1):
            dog.feed()
            if cache.done(job.sha) is not None:
                stats["cached"] += 1
                continue
            if cache.crashed(job.sha) and not (retry_crashed
                                               or _crashed_before(cache, job.sha, retry_crashed_before)):
                stats["crashed"] += 1
                continue
            for kind in _warm_kinds(job.route):  # a SetupError stops the run here, before any file of that route
                current[0] = f"载入转换工具（{kind}）"
                warm += tools.warm_up(kind)
                dog.feed()
            current[0] = job
            result = convert_and_save(cache, job.sha, job.path, job.route, tools,
                                      out / "work" / f"{os.getpid()}-{job.sha[:16]}", key=job.inp.key,
                                      file=job.inp.file, fmt=job.inp.fmt)
            current[0] = None
            stats["converted"] += 1
            by_status[result["status"]] += 1
            if stats["converted"] % PROGRESS_EVERY == 0:
                print(f"进度：转了 {stats['converted']} 个（{_counts(by_status)}），已选 {len(jobs)} 个，"
                      f"用时 {(time.monotonic() - started) / 60:.1f} 分钟", file=sys.stderr, flush=True)
            if max_minutes is not None and n < len(jobs) and time.monotonic() - started >= max_minutes * 60:
                stopped_early = True
                print(f"跑满 {max_minutes:g} 分钟，在两个档之间停下，由外层重开（把内存还回去）", file=sys.stderr, flush=True)
                break
    finally:
        dog.stop()
        tools.beat = own_beat
    elapsed = time.monotonic() - started
    print(f"转文字：导出里 {total} 个档，选了 {len(jobs)} 个；这次转了 {stats['converted']} 个（{_counts(by_status)}），"
          f"已有结果 {stats['cached']} 个，上次转到一半中断、跳过 {stats['crashed']} 个；用时 {elapsed:.0f} 秒，"
          f"其中 Docling {info.get('docling') or '?'} 和 OCR 载入 {warm:.0f} 秒（不算进各档的时间）。"
          f"记账：python -m nknu.totext report --export {export_dir} --out {out}")
    return {"inputs": total, "selected": [j.inp.key for j in jobs], "converted": stats["converted"],
            "cached": stats["cached"], "crashed": stats["crashed"], "statuses": dict(by_status),
            "warm_up_seconds": round(warm, 3), "seconds": round(elapsed, 3), "stopped_early": stopped_early}


def report(export_dir: Path, out: Path, *, project: bool = False) -> int:
    """Writes the accounting and the summary; 0, or 1 when anything is still pending."""
    export_dir, out = Path(export_dir), Path(out)
    refuse_out(out, export_dir)
    accounts = account(export_dir, Cache(out / "cache"))
    totals = project_totals(export_dir, accounts) if project else None
    summary = write_report(out, accounts, tools=Tools().describe(), totals=totals)
    counts = Counter({k: v for k, v in summary["by_status"].items() if k != "pending"})
    print(f"记账：{summary['files']} 个档（不同内容 {summary['unique_files']} 个）：{_counts(counts)}；"
          f"还没转 {summary['pending']} 个。写在 {out / 'summary.md'}")
    return 1 if summary["pending"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nknu.totext",
                                     description="转文字：导出资料夹里的每个档转成文字，逐个记账（可以续跑）")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="转文字；已经有结果的档不重转")
    go.add_argument("--export", type=Path, required=True, help="导出资料夹（archive-export/<网站>-<日期>）")
    go.add_argument("--out", type=Path, required=True, help="输出资料夹（缓存在 <out>/cache）")
    go.add_argument("--routes", help=f"只转这些路线，逗号隔开：{','.join(ROUTES)}")
    go.add_argument("--sample", type=int, help="每种路线轮流抽，共抽 N 个（测速度用）")
    go.add_argument("--seed", type=int, default=1102, help="抽样的种子（默认 1102）")
    go.add_argument("--limit", type=int, help="只走前 N 个")
    go.add_argument("--retry-crashed", action="store_true", help="上次转到一半中断的档也重转")
    go.add_argument("--retry-crashed-before", type=_epoch, metavar="SECONDS",
                    help="中断标记早于这个时间（Unix 秒数）的档也重转；之后才中断的照样跳过（转文字线传开线时间：一次开线每个档只再试一次）")
    go.add_argument("--defer", type=Path, help="这个档列的网址（一行一个，# 开头是注释）放到最后转")
    go.add_argument("--max-minutes", type=float, help="跑满这么多分钟就在两个档之间停下（退出码 4），由外层重开")
    rep = sub.add_parser("report", help="写 accounting.jsonl、summary.json、summary.md；还有没转的档就退出码 1")
    rep.add_argument("--export", type=Path, required=True)
    rep.add_argument("--out", type=Path, required=True)
    rep.add_argument("--project", action="store_true", help="加上全部转完要多久的推算（数 PDF 页数）")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            routes = {r.strip() for r in args.routes.split(",") if r.strip()} if args.routes else None
            summary = run(args.export, args.out, routes=routes, sample=args.sample, seed=args.seed, limit=args.limit,
                          retry_crashed=args.retry_crashed, retry_crashed_before=args.retry_crashed_before,
                          defer=read_defer(args.defer), max_minutes=args.max_minutes)
            return EXIT_MORE if summary.get("stopped_early") else 0
        return report(args.export, args.out, project=args.project)
    except (ValueError, FileNotFoundError, SetupError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
