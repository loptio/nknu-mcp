"""Helpers for build.sh and lanes.sh, phase 2 on the lab PC (lab_pc kit, 11-03; plan 2026-11-02 T11 steps 4-8; plan
2026-11-08 T5: any number of exports). Subcommands:

  exports                            the exports of the list (below), checked: one line each on stdout, folder, name
                                     and site separated by a tab (lanes.sh and build.sh read it); a line on stderr says
                                     which list was read. Exit 0, else 2 and why.
  jobs --out FILE                    every export's to-text inputs in one JSON list (key <name>:<序号>, route, sha,
                                     deferred, export), what retry_cut_members.py --after-stop reads. Exit 0, else 2.
  account --out DIR                  the to-text accounting of every export as one report in DIR (accounting.jsonl,
                                     summary.json, summary.md; keys <name>:<序号>), as `nknu.totext report` writes it
                                     for one export. Exit 0, 1 when files are still to convert, 2 a bad list or DIR.
  ready [--first] [--skip-marker-check]  is the to-text run finished, for every export? With --first: everything but
                                     the journals and past exam papers (the deferred files; CLAUDE.md 「学报考题以外都转完」).
                                     Exit 0 ready, 10 files still to convert, 11 a lane may still be converting, 2 a
                                     bad list.
  vectors BUILD_REPORT               build_report.json: every chunk has its bge-m3 vector. Exit 0, else 1.
  runs RUN.json ...                  offline_eval run reports: no gold id missing and, for a hybrid run, no query whose
                                     ranking fell back to keywords (a model or the index failed). Exit 0, else 1.
  check-realdata ARGS...             research/skeleton_2026-11-01/check_realdata.py, unchanged, with two fixes for this
                                     container (see check_realdata below).

What they write: check-realdata the --out it is given, account its DIR, jobs its FILE; jobs and ready (and
totext_progress.py) the job lists below; exports only reads the manifests. Nothing else.

The export list (plan 2026-11-08 T5): a text file, one export folder per line, relative to the repository (/work on the
lab PC) or absolute; blank lines and lines starting with # are skipped, and so are a Windows editor's BOM and CR (a \\
in a folder reads as /). The file is $EXPORTS when that is set and not empty (relative to the repository), else
research/pipeline_2026-11-02/exports.txt when it exists, else the list is 教务处's 11-02 export alone (ACA_EXPORT), so
the kit does what it did before. 【Claude】: the default file, so the lanes, progress and the build read one list
without an environment variable each. Each folder must hold a manifest.json naming its site (网站). An export's name is
its site key with / -> _ (corpus/<name>.json, totext_jobs/<name>.json, the accounting's keys); two exports of one site,
or two sites with one name, stop the reader: one database takes one export per site.

Job lists (route and sha256 of each input; ready, jobs and totext_progress.py read them): one per export in
research/pipeline_2026-11-02/totext_jobs/<name>.json, made from the export (every input hashed, as the to-text run
selects them) when missing, or made from another manifest.json (by sha256) or at another PIPELINE_VERSION, and written
whole. 【Claude】: an export folder is never rewritten (the crawler's export refuses a folder that is not empty), so its
manifest's sha256 stands for its files, and ready and progress hash each export once instead of at every look (build
steps 3 and 4, the accounting and assemble, still hash every file of every export at each build:
nknu.totext.accounting.account). The Mac's totext_jobs.json (教务处's inputs, 11-03) is no longer read here.

The plan's step-6 comparison (gold-diff) is offline_eval.py gold-diff since plan 2026-11-08 T1: it compared the gold
documents by id, and every id changed then, so it compares them by address now, each gold with its own database.

Messages are Simplified Chinese like the rest of the pipeline's command lines.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

# 11-19: the programs are in the repository (deploy/lab-pipeline, on the lab PC /work/deploy/lab-pipeline); what they
# write (locks, logs, status, kept reports) stays in the pipeline's own folder, which no commit holds.
KIT = Path(__file__).resolve().parent  # the programs: deploy/lab-pipeline


def _root(start: Path) -> Path:
    """The project root the programs work in: $NKNU_LANE_WORK when it is set (lanes.sh's work folder; a test's own
    tree), else the nearest folder above the programs that holds src/nknu (/work on the lab PC)."""
    given = os.environ.get("NKNU_LANE_WORK")
    if given and Path(given).is_dir():
        return Path(given).resolve()
    return next((d for d in start.parents if (d / "src" / "nknu").is_dir()), start.parents[1])


REPO = _root(KIT)
PIPE = REPO / "research" / "pipeline_2026-11-02"  # the pipeline's data: lists, to-text cache, corpora, libraries
LAB = PIPE / "lab_pc"  # the state folder: locks, logs, build.status, previous/
ACA_EXPORT = "archive-export/aca-20261102"  # the list when there is none: 教务处's 11-02 export alone
ACA_SITE = "c.nknu.edu.tw/aca"  # the site the offline checks' questions are about (build.sh: steps 6-9 need it)
EXPORTS_FILE = PIPE / "exports.txt"  # the lab PC's export list (plan 2026-11-08 T5); $EXPORTS names another
JOBS_DIR = PIPE / "totext_jobs"  # one cached job list per export
CACHE = PIPE / "totext" / "cache"  # the to-text cache every export shares (content-addressed)
DEFER = PIPE / "totext_defer_urls.txt"  # 教务处's journals and past exam papers, converted last
# lanes.sh: lane A converts pdf, office and office_old, lane C archive, both with --defer (the journals and past exam
# papers go last); lane B page, image, data and unsupported (on the Mac until 11-03; on the lab PC since plan
# 2026-11-08 T5)
LANE_ROUTES = {"A": frozenset({"pdf", "office", "office_old"}), "C": frozenset({"archive"}),
               "B": frozenset({"page", "image", "data", "unsupported"})}
DEFERRED_ROUTES = LANE_ROUTES["A"] | LANE_ROUTES["C"]
LANE_LOCKS = {lane: LAB / f"lane{lane}.lock" for lane in LANE_ROUTES}  # flock-held by lanes.sh while a lane runs
# A conversion writes its 'started' marker when it begins; the watchdog ends one that shows no progress for
# HANG_S = DOC_TIMEOUT_S + 120 = 3720 s (nknu.totext.run). A marker without a result younger than this may belong to
# a file a lane is converting now.
QUIET_MIN = 65
SHOWN = 30  # files or exports a message lists by name before 「……另外 N 个」
_NAME = re.compile(r"[A-Za-z0-9_~-][A-Za-z0-9._~-]*")  # an export's name: a file name of its own, never hidden
_JOB_FIELDS = ("key", "route", "sha", "url")


def _say(*parts: object) -> None:
    print(*parts, flush=True)


def _err(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


# ---- the export list (plan 2026-11-08 T5) ---------------------------------------------------------------------------


class ExportsError(ValueError):
    """The export list cannot be used as it is; the message says why, in Simplified Chinese."""


@dataclass(frozen=True)
class Export:
    folder: str  # as the list writes it (build.sh and lanes.sh pass it on as it is)
    path: Path  # where it is
    site: str  # manifest.json 网站
    name: str  # the site key with / -> _


def site_name(site: str) -> str:
    """corpus/<name>.json's name of a site key: / -> _ (plan 2026-11-08 T5); ExportsError for a key that would not
    make a plain file name."""
    name = site.replace("/", "_")
    if not _NAME.fullmatch(name):
        raise ExportsError(f"网站「{site}」做不成档名（只能有英文字母、数字和 . _ ~ -，不能用 . 开头）")
    return name


def exports_file(env: dict | None = None, *, default: Path | None = None, repo: Path | None = None) -> Path | None:
    """The list to read: $EXPORTS when set and not empty (relative to the repository), else the default file when it
    exists, else None (教务处's 11-02 export alone)."""
    env = os.environ if env is None else env
    default = EXPORTS_FILE if default is None else default
    repo = REPO if repo is None else repo
    value = str(env.get("EXPORTS") or "").strip()
    if value:
        path = Path(value)
        return path if path.is_absolute() else repo / path
    return default if default.is_file() else None


def _folders(list_file: Path) -> list[str]:
    try:
        text = list_file.read_text(encoding="utf-8-sig")  # -sig: a Windows editor's BOM
    except OSError as e:
        raise ExportsError(f"读不了导出清单 {list_file}：{type(e).__name__}（{e.strerror or e}）") from e
    folders = []
    for line in text.splitlines():  # \r\n and \r too
        line = line.strip().replace("\\", "/")
        if line and not line.startswith("#"):
            folders.append(line.rstrip("/") or "/")
    if not folders:
        raise ExportsError(f"导出清单 {list_file} 是空的：一行导出资料夹也没有")
    return folders


def read_exports(list_file: Path | None, repo: Path | None = None) -> list[Export]:
    """The exports of list_file (None: 教务处's 11-02 export alone), in its order, each checked: a manifest.json naming
    its site, a usable name, no folder twice, no site twice, no two sites with one name. ExportsError says what is
    wrong."""
    repo = REPO if repo is None else repo
    out: list[Export] = []
    by_path: dict[Path, str] = {}
    by_name: dict[str, Export] = {}
    for folder in _folders(list_file) if list_file is not None else [ACA_EXPORT]:
        if "\t" in folder:
            raise ExportsError(f"导出资料夹的名字里有 Tab：{folder!r}")
        path = Path(folder) if Path(folder).is_absolute() else repo / folder
        try:
            data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise ExportsError(f"{folder}：没有 manifest.json（不是导出资料夹，或还没导出完）") from e
        except (OSError, ValueError) as e:
            raise ExportsError(f"{folder}：manifest.json 读不了（{type(e).__name__}）") from e
        site = str(data.get("网站") or "").strip() if isinstance(data, dict) else ""
        if not site:
            raise ExportsError(f"{folder}：manifest.json 没写是哪个网站（「网站」）")
        name = site_name(site)
        real = path.resolve()
        if real in by_path:
            raise ExportsError(f"{folder}：清单里写了两次（和「{by_path[real]}」是同一个资料夹）")
        if (other := by_name.get(name)) is not None:
            if other.site == site:
                raise ExportsError(f"{folder} 和 {other.folder} 是同一个网站（{site}）的导出：一个网站只放一份")
            raise ExportsError(f"网站 {site}（{folder}）和 {other.site}（{other.folder}）的档名一样（{name}）")
        export = Export(folder, path, site, name)
        by_path[real], by_name[name] = folder, export
        out.append(export)
    return out


def list_words(list_file: Path | None) -> str:
    return f"导出清单 {list_file}" if list_file is not None else f"没有导出清单（{EXPORTS_FILE.name}），只有教务处的 {ACA_EXPORT}"


# ---- each export's to-text inputs -----------------------------------------------------------------------------------


def _manifest_sha(export: Export) -> str:
    with (export.path / "manifest.json").open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _cached_rows(path: Path, manifest_sha: str, version: str) -> list[dict] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("manifest_sha256") != manifest_sha or data.get("pipeline") != version:
        return None
    rows = data.get("jobs")
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and all(isinstance(r.get(k), str) for k in _JOB_FIELDS) for r in rows):
        return None
    return rows


def export_jobs(export: Export, defer: frozenset[str], jobs_dir: Path | None = None) -> list[dict]:
    """[{key (序号), route, sha, url, deferred}] of export's inputs whose file is there, in the to-text run's order
    before --defer (nknu.totext.run.select): the cached list when it is of this manifest and PIPELINE_VERSION, else
    made now and kept (module docstring)."""
    from nknu.totext.cache import PIPELINE_VERSION, write_atomic

    jobs_dir = JOBS_DIR if jobs_dir is None else jobs_dir
    path, manifest_sha = jobs_dir / f"{export.name}.json", _manifest_sha(export)
    rows = _cached_rows(path, manifest_sha, PIPELINE_VERSION)
    if rows is None:
        from nknu.totext.run import select

        picked, _total = select(export.path)
        rows = [{"key": j.inp.key, "route": j.route.name, "sha": j.sha, "url": j.inp.url} for j in picked]
        record = {"export": export.folder, "site": export.site, "manifest_sha256": manifest_sha,
                  "pipeline": PIPELINE_VERSION, "jobs": rows}
        jobs_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(record, ensure_ascii=False).encode("utf-8"))
    return [{**r, "deferred": r["url"] in defer} for r in rows]


def deferred_urls(path: Path) -> frozenset[str]:
    """The deferred URLs (journals and past exam papers); none when the list is missing (then nothing is held back for
    a --first build: the safe way round)."""
    from nknu.totext.run import read_defer

    return read_defer(path) if path.is_file() else frozenset()


def all_jobs(exports: list[Export], defer: frozenset[str], jobs_dir: Path | None = None) -> list[dict]:
    """export_jobs of every export, keys made <name>:<序号> and each row naming its export."""
    return [{**job, "key": f"{e.name}:{job['key']}", "export": e.name}
            for e in exports for job in export_jobs(e, defer, jobs_dir)]


# ---- ready --------------------------------------------------------------------------------------------------------


def _lock_held(path: Path) -> bool | None:
    """True when another process holds an flock on path, False when not, None when there is no such file (never
    created here)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _minutes(path: Path, now: float) -> float:
    """How old path is; 0 when it went away meanwhile (a lane just finished that file: it counts as recent)."""
    try:
        return (now - path.stat().st_mtime) / 60
    except OSError:
        return 0.0


def ready(first: bool, skip_markers: bool, *, exports: list[Export] | None = None, cache_root: Path | None = None,
          defer_file: Path | None = None, locks: dict[str, Path] | None = None,
          jobs_dir: Path | None = None) -> int:
    from nknu.totext.cache import STARTED, Cache

    cache_root = CACHE if cache_root is None else cache_root
    defer_file = DEFER if defer_file is None else defer_file
    locks = LANE_LOCKS if locks is None else locks
    if not cache_root.is_dir():
        _say(f"找不到转文字的缓存：{cache_root}")
        return 10
    if exports is None:
        list_file = exports_file()
        exports = read_exports(list_file)
        _say(f"{list_words(list_file)}：{len(exports)} 份导出")
    cache, now = Cache(cache_root), time.time()
    counts: Counter = Counter()
    left: Counter = Counter()  # (export name, part) -> files without a result yet (crashed ones aside)
    crashed: list[tuple[str, str, str, float, str]] = []  # part, key, route, minutes, note
    deferred_left: set[str] = set()  # routes that still have journals or past exam papers without a result
    for job in all_jobs(exports, deferred_urls(defer_file), jobs_dir):
        part = "学报考题" if job["deferred"] and job["route"] in DEFERRED_ROUTES else "其余"
        sha = job["sha"]
        if cache.done(sha) is not None:
            counts[(part, "done")] += 1
            continue
        if part == "学报考题":
            deferred_left.add(job["route"])
        if cache.crashed(sha):
            counts[(part, "crashed")] += 1
            crashed.append((part, job["key"], job["route"], _minutes(cache.dir(sha) / STARTED, now),
                            cache.crash_note(sha)))
        else:
            counts[(part, "pending")] += 1
            left[(job["export"], part)] += 1
    young_any = []  # every marker without a current result, archive members included
    for marker in cache_root.glob(f"*/*/{STARTED}"):
        try:
            if cache.done(marker.parent.name) is None and (age := _minutes(marker, now)) < QUIET_MIN:
                young_any.append((age, marker.parent.name))
        except ValueError:  # a folder name that is not a sha256: not a cache entry
            continue
    young_main = [c for c in crashed if c[0] == "其余" and c[3] < QUIET_MIN]
    held = {lane: _lock_held(path) for lane, path in locks.items()}

    for part in ("其余", "学报考题"):
        _say(f"转文字 {part}：有结果 {counts[(part, 'done')]}，中断或看门狗结束（算失败）{counts[(part, 'crashed')]}，"
             f"还没转 {counts[(part, 'pending')]}")
    waiting = [e for e in exports if left[(e.name, "其余")] or left[(e.name, "学报考题")]]
    if waiting:
        _say(f"还有档没转的导出 {len(waiting)} 份：")
        for e in waiting[:SHOWN]:
            _say(f"  {e.name}：还没转 {left[(e.name, '其余')]}，学报考题 {left[(e.name, '学报考题')]}（{e.folder}）")
        if len(waiting) > SHOWN:
            _say(f"  ……另外 {len(waiting) - SHOWN} 份")
    if crashed:
        _say(f"中断或看门狗结束的档 {len(crashed)} 个（组装时算失败；同一个档才补用 10-29 的文字）：")
        for part, key, route, age, note in sorted(crashed, key=lambda c: c[3])[:SHOWN]:
            _say(f"  {key}（{route}，{part}）：标记是 {age:.0f} 分钟前写的；{note or '没有说明（程序中断）'}")
        if len(crashed) > SHOWN:
            _say(f"  ……另外 {len(crashed) - SHOWN} 个")
        _say("  没有说明的多半是停线时被打断的：要重转，先停掉转文字线，再用 RETRY_FIRST=1 开一次线（那次开线会把它们各重转一次）。")
    _say("转文字线的锁：" + "、".join(f"线 {lane} {'有人拿着（在跑）' if h else '没人拿' if h is False else '没有锁档'}"
                                  for lane, h in held.items()))

    if counts[("其余", "pending")]:
        _say(f"还不能建库：学报考题以外还有 {counts[('其余', 'pending')]} 个档没转。等转文字线跑完再建。")
        return 10
    if not first and counts[("学报考题", "pending")]:
        _say(f"还不能建完整的库：学报考题还有 {counts[('学报考题', 'pending')]} 个档没转。等转完；"
             "要先建第一版库就加 --first。")
        return 10
    running = [lane for lane, h in held.items() if h]
    if running and not first:
        _say(f"线 {'、'.join(running)} 还在跑（锁有人拿着）：先等它跑完或停掉，再建库。")
        return 11
    # --first lets a lane keep converting the journals and exams beside the build; a lane with none of those left can
    # only be on a file the first database needs (lane C has no deferred file at all)
    busy = [lane for lane in running if not LANE_ROUTES[lane] & deferred_left]
    if busy:
        _say(f"线 {'、'.join(busy)} 还在跑，而它没有学报考题要转，转的只会是建库要用的档：等它跑完（progress 看进度）再建库。")
        return 11
    if young_main and not skip_markers:
        _say(f"学报考题以外有 {len(young_main)} 个档的「开始转」标记不到 {QUIET_MIN} 分钟、还没有结果，可能还在转"
             "（最新的：" + "、".join(c[1] for c in sorted(young_main, key=lambda c: c[3])[:5]) + "）。"
             "先确认转文字线都停了（docker ps 看不到 nknu-lane-A、nknu-lane-B、nknu-lane-C），再加 --skip-marker-check 重跑"
             "（这些标记确定是中断留下的，也可以加）。")
        return 11
    if young_any and not first and not skip_markers:
        age, sha = min(young_any)
        _say(f"缓存里有 {len(young_any)} 个「开始转」标记不到 {QUIET_MIN} 分钟、还没有结果（最新的 {age:.0f} 分钟前，"
             f"{sha[:12]}…），可能有转文字线还在转。先确认转文字线都停了（docker ps 看不到 nknu-lane-A、"
             "nknu-lane-B、nknu-lane-C），再加 --skip-marker-check 重跑（这些标记确定是中断留下的，也可以加）。")
        return 11
    if first and (running or young_any):
        _say("注意：学报考题还在转，和建库共用这块 GPU；这一版库里它们算还没转（没有结果的档照失败处理）。")
    _say("可以建库" + ("（第一版：学报考题还没转的照失败处理）" if first else "（完整）"))
    return 0


# ---- account (build.sh step 3) --------------------------------------------------------------------------------------


def account(exports: list[Export], out: Path, *, cache_root: Path | None = None, tools: dict | None = None) -> int:
    """The to-text accounting of every export as one report in out (nknu.totext.accounting.write_report: what
    `nknu.totext report` writes for one export, which would write fixed names into one folder per export), each key
    <name>:<序号>; 0, or 1 when anything is still pending. out never lies inside an export, ./data, archive*/ or the
    skeleton's folder (nknu.totext.run.refuse_out: ValueError)."""
    from nknu.totext.accounting import STATUSES, write_report
    from nknu.totext.accounting import account as account_export
    from nknu.totext.cache import Cache
    from nknu.totext.run import refuse_out

    for e in exports:
        refuse_out(out, e.path)
    cache = Cache(CACHE if cache_root is None else cache_root)
    accounts, pending = [], Counter()
    for e in exports:
        for a in account_export(e.path, cache):
            accounts.append(replace(a, key=f"{e.name}:{a.key}"))
            pending[e.name] += a.status == "pending"
    if tools is None:
        from nknu.totext.convert import Tools

        tools = Tools().describe()
    summary = write_report(out, accounts, tools=tools)
    counts = "、".join(f"{s} {n}" for s in STATUSES if s != "pending" and (n := summary["by_status"].get(s)))
    _say(f"记账：{len(exports)} 份导出、{summary['files']} 个档（不同内容 {summary['unique_files']} 个）："
         f"{counts or '没有'}；还没转 {summary['pending']} 个。写在 {out / 'summary.md'}")
    waiting = [e for e in exports if pending[e.name]]
    for e in waiting[:SHOWN]:
        _say(f"  {e.name}：还没转 {pending[e.name]}")
    if len(waiting) > SHOWN:
        _say(f"  ……另外 {len(waiting) - SHOWN} 份导出")
    return 1 if summary["pending"] else 0


# ---- the build's own checks -----------------------------------------------------------------------------------------


def vectors(report: Path) -> int:
    r = json.loads(report.read_text(encoding="utf-8"))
    chunks, v = r["counts"]["chunks"], r.get("vectors") or {}
    got = v.get("chunks")
    _say(f"向量：{got} / {chunks} 小段（快取 {v.get('cached')}、新算 {v.get('encoded')}，{v.get('seconds')} 秒；"
         f"模型 {v.get('model')} {str(v.get('revision'))[:12]}）")
    if not chunks or got != chunks:
        _say("向量不完整：混合检索会退回只用关键词。看上面建库的 log。")
        return 1
    return 0


def runs(paths: list[Path]) -> int:
    bad = 0
    for path in paths:
        r = json.loads(path.read_text(encoding="utf-8"))
        fallbacks, missing = r.get("ranking_fallbacks") or [], r.get("missing_gold") or []
        hybrid = (r.get("search") or {}).get("function", "").endswith("hybrid_search")
        _say(f"{path.name}：{'混合检索' if hybrid else '关键词'}；排序退回关键词 {len(fallbacks)} 个查询；"
             f"资料库里没有的标准出处 {len(missing)} 个")
        if (hybrid and fallbacks) or missing:
            bad += 1
            for q in fallbacks[:5]:
                _say(f"  退回关键词：{q}")
            if missing:
                _say(f"  没有的标准出处：{'、'.join(missing[:10])}")
    if bad:
        _say(f"{bad} 份结果不能用：模型或向量出错（混合检索每个查询都应该用上模型），或 gold 不是这个资料库的。")
    return 1 if bad else 0


def check_realdata(argv: list[str]) -> int:
    """check_realdata.py as it is, with two fixes for this container, both in this process only:
    - its check 17 starts the MCP server with REPO/.venv/bin/python, which exists on the Mac; here the venv is
      /opt/venv, so PY becomes this interpreter;
    - that server is started through fastmcp's StdioTransport without an env, so mcp.client.stdio.stdio_client gives
      it only HOME, LOGNAME, PATH, SHELL, TERM and USER (get_default_environment, mcp 2.3.0 with fastmcp 4.1.3), and
      here the code is found through PYTHONPATH (the image does not install the project) and the models through
      HF_HOME and HF_HUB_CACHE, so the server gets the whole environment instead.
    No __pycache__ is written beside the script: the skeleton's folder is never written."""
    import importlib.util

    from mcp.client import stdio

    if not callable(getattr(stdio, "get_default_environment", None)):
        _say("mcp.client.stdio 没有 get_default_environment 了（mcp 换了版本）：先看 check_realdata 的 MCP 检查怎么启动服务")
        return 2
    sys.dont_write_bytecode = True
    script = KIT / "eval" / "check_realdata.py"  # beside the programs (in the repository since 11-19)
    spec = importlib.util.spec_from_file_location("check_realdata", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is made
    spec.loader.exec_module(module)
    module.PY = Path(sys.executable)
    stdio.get_default_environment = lambda: dict(os.environ)
    return module.main(argv)


# ---- command line -------------------------------------------------------------------------------------------------


def _out_arg(rest: list[str]) -> Path | None:
    """--out PATH (or --out=PATH) when it is all rest holds."""
    if len(rest) == 2 and rest[0] == "--out":
        return Path(rest[1])
    if len(rest) == 1 and rest[0].startswith("--out="):
        return Path(rest[0].removeprefix("--out="))
    return None


def _listed() -> list[Export]:
    """The exports of the list, its name on stderr (ExportsError passes up)."""
    list_file = exports_file()
    exports = read_exports(list_file)
    _err(f"{list_words(list_file)}：{len(exports)} 份导出")
    return exports


def main(argv: list[str]) -> int:
    if not argv:
        _say(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "exports" and not rest:
            for e in _listed():
                print(f"{e.folder}\t{e.name}\t{e.site}", flush=True)
            return 0
        if cmd == "jobs" and (out := _out_arg(rest)) is not None:
            from nknu.totext.cache import write_atomic

            rows = all_jobs(_listed(), deferred_urls(DEFER))
            write_atomic(out, json.dumps(rows, ensure_ascii=False).encode("utf-8"))
            _err(f"写了 {out}：{len(rows)} 个档")
            return 0
        if cmd == "account" and (out := _out_arg(rest)) is not None:
            return account(_listed(), out)
        if cmd == "ready":
            unknown = [a for a in rest if a not in ("--first", "--skip-marker-check")]
            if unknown:
                _say(f"不认得的选项：{' '.join(unknown)}")
                return 2
            return ready("--first" in rest, "--skip-marker-check" in rest)
    except ExportsError as e:
        _err(f"导出清单不能用：{e}")
        return 2
    except ValueError as e:  # account: an --out inside an export, ./data, archive*/ or the skeleton's folder
        _err(f"错误：{e}")
        return 2
    if cmd == "vectors" and len(rest) == 1:
        return vectors(Path(rest[0]))
    if cmd == "runs" and rest:
        return runs([Path(p) for p in rest])
    if cmd == "gold-diff":  # moved (plan 2026-11-08 T1): ids cannot be compared across the id schemes
        _say("gold-diff 搬到 offline_eval.py 了（按网址比，每份 gold 配它自己的资料库）：python research/eval_2026-11-01/"
             "offline_eval.py gold-diff --base-gold OLD --base-db OLD_DB --gold NEW --db NEW_DB --out OUT")
        return 2
    if cmd == "check-realdata":
        return check_realdata(rest)
    _say(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
