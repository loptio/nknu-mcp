"""Nightly ingest on the lab PC (spec docs/superpowers/specs/2026-11-16-nightly-ingest-design.md; the owner 11-16,
「照这样做」 to its five points): after the crawler's nightly update has exported the sites that changed, take the new
export folders into the library. The steps are the ones run by hand until now, chained:

  plan     the export list (exports.txt) against the crawler's current.tsv: which listed sites have a newer folder.
           Nothing newer, and the library already built from the list: the night ends (nothing_new), no rebuild.
  repoint  exports.txt names the newer folders (the sites of the list only, in their places; a site the crawler
           exports that the list lacks is noted, never added: a new site needs its unit first).
  convert  the three to-text lanes (lanes.sh A, C, B) walk the list; only new and changed files are converted.
           Not through within LANE_LIMIT: the sites whose exports still hold unconverted files go back to the
           folder the library in use was built from (they wait a night; tomorrow moves them on and converts
           the rest), and the night builds from the others (11-19: one batch of slow scans kept the updates of
           fifty sites out for a day). When there is nothing to go back to, or nothing else is new: deferred.
  keep     the library in use stays beside itself as aca.prev.db (a hard link: no space, no time).
  build    build.sh: assemble, build, the swap guard, the swap, the per-site report, the 33 questions and the real-data
           checks. The checks are recorded and never undo a swap (the owner's third point).
  done     the lists that keep the export service from deleting the folders of the library in use
           (exports-official.txt) and of the one before (exports-prev.txt) are written from the libraries themselves
           (sites.export), and a flag file asks the host script to restart Datasette.

Any step that fails ends the night there: the library in use is untouched and the status file says where and why.
A pause file (ingest.pause) lets the lanes convert and skips the build. One night at a time (ingest.lock).
When the build's guards hold the new library for what one site or a few did (documents leaving without a reason over
the site's limit, a site fallen below half, a wave of school deletions), those sites go back to the folder the library
in use was built from and the rest is built again and swapped (11-19: one site's trouble no longer keeps every site's
night out). The folder that was refused is kept in ingest-refused.txt and not tried again; a newer export of the site
is. Too many sites at once (SET_BACK_MAX), a trouble spread thin over many sites, or no folder to go back to: held as
before.
A hold file (ingest-hold.txt: one site key a line, # for a note) keeps the sites it names at the folder exports.txt
holds for them, whatever the crawler exported since: for a site whose newest export is known to be wrong (11-16 night:
the library site's export lost the files an old-archive copy had covered). The status file names them (sites_held).

Status, for the dashboards (fields agreed with the dashboard session 11-16; codes, never sentences): ingest-status.json,
written whole and renamed into place at the start (outcome running) and at each step; one line per night appended to
ingest-history.jsonl. Outcomes: running, swapped, held (the swap guard kept the library in use; the new one is
aca.held.db), nothing_new, paused, deferred, failed; the history also holds busy (another night still ran) and
rolled_back. A night is named by the day it belongs to (night_of: a run at 02:00 is the night before's), and of
several lines of one night the last is that night's result (both asked by the dashboard session 11-16).

Usage, in the container (compose service ingest; the host's nightly_ingest.cmd runs it and restarts Datasette):
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py run        the night
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py rollback   aca.prev.db back in place, and paused
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py status     last night in words
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py plan       what a night would do now; writes nothing
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py promote NAME   the checked trial library NAME.db takes
                                                                              the library's place, without a rebuild
  python research/pipeline_2026-11-02/lab_pc/nightly_ingest.py reports NAME   the promoted trial's later reports (its
                                                                              own checks) beside the library too
Exit: 0 swapped, nothing new, paused, deferred or another night still running; 3 held; 1 failed.
The numbers and the file names are 【Claude】."""

from __future__ import annotations

import contextlib
import fcntl
import filecmp
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent  # deploy/lab-pipeline: lanes.sh, build.sh, build_helpers.py (11-19)
sys.path.insert(0, str(PROGRAMS))
import build_helpers as bh

TAIPEI = timezone(timedelta(hours=8))
PIPE = "research/pipeline_2026-11-02"
LANES = ("A", "C", "B")
LANE_LIMIT = 2 * 60 * 60  # seconds the lanes may take in one night (spec §四: 「转文字超过两小时还没走完」)
LANE_STOP = 150  # seconds a lane gets after SIGTERM: lanes.sh needs up to 2 minutes to put its file back
POLL = 5
CONTAINER_REPO = "/work"  # the repository as the crawler's export service writes it into current.tsv
FLAG = Path(os.environ.get("NKNU_INGEST_FLAG", "/out/ingest-swapped.flag"))  # for the host script: restart Datasette
EXIT_HELD = 3
# 11-19 (the owner through session judge: 「出了问题，要让它只影响出问题的那一站，并且能自己消化」): the sites a held
# build blames are set back and the rest is built again, when they are at most this many 【Claude】; more at once
# reads as something wrong with the night as a whole, and is held as before for a person to look at
SET_BACK_MAX = 5
WHY_WORDS = {"site_collapsed": "文件数掉到一半以下", "unexplained_departures": "说不出原因离开的文件太多",
             "school_wave": "一次有一成以上的文件被标成学校删掉"}
# spec 2026-11-17-ingest-guard-design §四, §五 (the owner's intent through session 爬虫judge, 11-17: a night must not fail
# or fill the disk without anyone knowing; the numbers 【Claude】)
DISK_MIN_GB = 15  # free space on the host drive that holds Docker's disk image, below which a night builds nothing
BUILD_LIMIT = 3 * 60 * 60  # seconds build.sh may take (an hour or two is usual): past it the night ends as failed
EXIT_TIMEOUT = 124


@dataclass(frozen=True)
class Paths:
    repo: Path

    @property
    def pipe(self) -> Path:
        return self.repo / PIPE

    @property
    def kit(self) -> Path:
        return self.pipe / "lab_pc"

    @property
    def exports(self) -> Path:
        return self.pipe / "exports.txt"

    @property
    def official(self) -> Path:  # ends with .txt: the export service keeps the folders it names
        return self.pipe / "exports-official.txt"

    @property
    def prev_list(self) -> Path:
        return self.pipe / "exports-prev.txt"

    @property
    def current(self) -> Path:
        return self.repo / "archive-export" / "sites" / "current.tsv"

    @property
    def db(self) -> Path:
        return self.pipe / "aca.db"

    @property
    def prev_db(self) -> Path:
        return self.pipe / "aca.prev.db"

    @property
    def held_db(self) -> Path:  # nknu.content.build's name for a library the swap guard held
        return self.pipe / "aca.held.db"

    @property
    def swap_json(self) -> Path:  # nknu.content.build.swap_file: what the last swap it decided took out, and why
        return self.pipe / "aca.swap.json"

    @property
    def checks(self) -> Path:
        return self.pipe / "site_checks.json"

    @property
    def status(self) -> Path:
        return self.pipe / "ingest-status.json"

    @property
    def history(self) -> Path:
        return self.pipe / "ingest-history.jsonl"

    @property
    def pause(self) -> Path:
        return self.pipe / "ingest.pause"

    @property
    def hold(self) -> Path:
        return self.pipe / "ingest-hold.txt"

    @property
    def refused(self) -> Path:
        return self.pipe / "ingest-refused.txt"

    @property
    def lock(self) -> Path:
        return self.kit / "ingest.lock"

    def rel(self, path: Path) -> str:
        """path as the lists write it: relative to the repository when it is inside."""
        with contextlib.suppress(ValueError):
            return path.resolve().relative_to(self.repo.resolve()).as_posix()
        return str(path)

    def folder(self, written: str) -> Path:
        """An export folder as a list, current.tsv or a library's sites.export writes it."""
        if written.startswith(CONTAINER_REPO + "/"):
            return self.repo / written[len(CONTAINER_REPO) + 1:]
        p = Path(written)
        return p if p.is_absolute() else self.repo / p


def now() -> datetime:
    return datetime.now(TAIPEI)


def night_of(at: datetime) -> str:
    """The night a run belongs to: the Asia/Taipei day twelve hours before it started, so the scheduled run after
    midnight (02:00) is the night before's and one started in the evening (18:55) is that day's (the dashboard
    session's rule, 11-16: its 「昨晚」 is the line whose night is yesterday)."""
    return (at.astimezone(TAIPEI) - timedelta(hours=12)).date().isoformat()


# ---------------------------------------------------------------------------------------------- plan and repoint


@dataclass(frozen=True)
class Move:
    site: str
    old: str  # the folder as exports.txt writes it now
    new: str  # the folder to write instead


@dataclass(frozen=True)
class Plan:
    moved: list[Move]
    unlisted: list[str]  # sites the crawler exports that exports.txt does not list: noted, never added
    not_ready: list[str]  # listed sites whose newer folder has no manifest.json yet: left as they are tonight
    held: list[str] = field(default_factory=list)  # listed sites with a newer folder that ingest-hold.txt keeps back
    # listed sites whose newest folder is the one the guards refused before (ingest-refused.txt): not tried again
    refused: list[str] = field(default_factory=list)


def read_current(path: Path) -> dict[str, str]:
    """{site key: export folder} of the export service's current.tsv (tab-separated; a missing file gives none)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "\t" in line:
            site, folder = line.split("\t", 1)
            if site.strip() and folder.strip():
                out[site.strip()] = folder.split("\t", 1)[0].strip()
    return out


def read_hold(path: Path) -> set[str]:
    """The site keys ingest-hold.txt names (the first cell of each line; a line starting with # is a note)."""
    if not path.is_file():
        return set()
    lines = (line.split("#", 1)[0].split("\t", 1)[0].strip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    return {line for line in lines if line}


def read_refused(path: Path) -> dict[str, dict]:
    """{site: {'folder', 'why', 'night'}} of ingest-refused.txt (tab-separated; # for a note): the export folders the
    guards refused, each tried once. A person who wants one tried again deletes its line."""
    out: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) >= 2 and cells[0] and not cells[0].startswith("#") and cells[1]:
            out[cells[0]] = {"folder": cells[1], "why": cells[2] if len(cells) > 2 else "",
                             "night": cells[3] if len(cells) > 3 else ""}
    return out


def write_refused(paths: Paths, entries: dict[str, dict]) -> None:
    head = ("# 换库把关退回的导出：网站、被退回的资料夹、原因、哪一晚。每晚进库不会再试同一份，等爬虫给这一站新的导出；"
            "要重试就删掉那一行。由每晚进库写，别的行不要手改。")
    lines = [head]
    lines += ["\t".join((site, e["folder"], e.get("why", ""), e.get("night", ""))) for site, e in sorted(entries.items())]
    tmp = paths.refused.with_name(paths.refused.name + ".new")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, paths.refused)


def plan(paths: Paths) -> Plan:
    exports = bh.read_exports(paths.exports, paths.repo)
    current = read_current(paths.current)
    hold = read_hold(paths.hold)
    refused_before = read_refused(paths.refused)
    moved, not_ready, held, refused = [], [], [], []
    for e in exports:
        if e.site not in current:
            continue
        new = paths.folder(current[e.site])
        if new.resolve() == e.path.resolve():
            continue
        if e.site in hold:
            held.append(e.site)
            continue
        if e.site in refused_before and paths.folder(refused_before[e.site]["folder"]).resolve() == new.resolve():
            refused.append(e.site)
            continue
        if not (new / "manifest.json").is_file():
            not_ready.append(e.site)
            continue
        moved.append(Move(e.site, e.folder, paths.rel(new)))
    return Plan(moved, sorted(set(current) - {e.site for e in exports}), not_ready, held, refused)


def repoint(paths: Paths, moves: list[Move]) -> None:
    """exports.txt with each moved site's folder replaced in its line; checked (the same sites in the same order, each
    at its new folder) before it takes the list's place. The list as it was stays as exports.txt.bak-night."""
    new_of = {m.old: m.new for m in moves}
    lines = [new_of.get(line.strip(), line) for line in paths.exports.read_text(encoding="utf-8").splitlines()]
    tmp = paths.exports.with_name("exports.txt.new-night")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        before = bh.read_exports(paths.exports, paths.repo)
        after = bh.read_exports(tmp, paths.repo)
        if [e.site for e in after] != [e.site for e in before]:
            raise bh.ExportsError("改指以后清单里的网站或顺序变了")
        got = {e.site: e.path.resolve() for e in after}
        if any(got.get(m.site) != paths.folder(m.new).resolve() for m in moves):
            raise bh.ExportsError("改指以后有网站没指到新资料夹")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    shutil.copy2(paths.exports, paths.exports.with_name("exports.txt.bak-night"))
    os.replace(tmp, paths.exports)


# ---------------------------------------------------------------------------------------------- the libraries


def library(db: Path) -> dict | None:
    """What a library says of itself: documents, passages, when it was built, each site's documents and the export
    folder it was built from. None when there is none or it cannot be read."""
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            built = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
            sites = conn.execute("SELECT site, docs, export FROM sites").fetchall()
            return {"docs": conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
                    "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                    "built_at": built[0] if built else "",
                    "sites": {s: n or 0 for s, n, _ in sites}, "exports": {s: e or "" for s, _, e in sites}}
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def behind(paths: Paths, lib: dict | None) -> bool:
    """The library in use was not built from the list as it is now: a listed site it lacks, or built from another
    folder (a night that repointed and then did not swap; a list changed by hand). No library: behind."""
    if lib is None:
        return True
    for e in bh.read_exports(paths.exports, paths.repo):
        built = lib["exports"].get(e.site)
        if not built or paths.folder(built).resolve() != e.path.resolve():
            return True
    return False


def keep_lists(paths: Paths) -> None:
    """exports-official.txt and exports-prev.txt: the folders the library in use and the one kept beside it were built
    from, read from the libraries (sites.export), so a build run by hand is covered too. The export service deletes
    no folder a list named exports*.txt holds (agreed with the crawler session 11-16)."""
    for db, target, words in ((paths.db, paths.official, "正式库"), (paths.prev_db, paths.prev_list, "前一份库")):
        lib = library(db)
        if lib is None or not lib["exports"]:
            continue
        folders = [paths.rel(paths.folder(f)) for f in lib["exports"].values() if f]
        text = (f"# 每晚进库写的（nightly_ingest.py）：{words}（{db.name}，{lib['built_at']} 建）用的导出资料夹。"
                "导出服务不会删这里写着的资料夹；不要手改。\n" + "\n".join(folders) + "\n")
        tmp = target.with_name(target.name + ".new")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)


def link_prev(paths: Paths) -> None:
    """aca.prev.db is the library in use, by a hard link: the build replaces aca.db with a new file and this name
    keeps the old one."""
    if not paths.db.is_file():
        return
    tmp = paths.prev_db.with_name(paths.prev_db.name + ".new")
    tmp.unlink(missing_ok=True)
    os.link(paths.db, tmp)
    os.replace(tmp, paths.prev_db)


def waiting(paths: Paths) -> dict[str, int]:
    """{site: files of its listed export without a to-text result yet}, as build_helpers.ready counts what a build
    still waits for (a file that crashed has its failure and is not waited for)."""
    from nknu.totext.cache import Cache

    exports = bh.read_exports(paths.exports, paths.repo)
    site_of = {e.name: e.site for e in exports}
    cache, left = Cache(bh.CACHE), Counter()
    for job in bh.all_jobs(exports, bh.deferred_urls(bh.DEFER)):
        if cache.done(job["sha"]) is None and not cache.crashed(job["sha"]):
            left[site_of[job["export"]]] += 1
    return dict(left)


def go_back(paths: Paths, lib: dict | None, left: dict[str, int]) -> bool:
    """The sites of left (unconverted files) set back, in exports.txt, to the folder the library in use was built from:
    nothing of theirs leaves the library for a version that is not converted yet, and the others are built tonight
    (session judge, 11-19: 「正式库里已经有的文件，不能因为这一版还没转完就离开」). False, and the list untouched, when a
    site has no such folder to go back to (the library does not hold it, the folder is gone, or it is at that folder
    already and still waits): the night is then deferred as before."""
    built = (lib or {}).get("exports") or {}
    listed = {e.site: e for e in bh.read_exports(paths.exports, paths.repo)}
    moves = []
    for site in left:
        was = built.get(site)
        if not was or site not in listed or not (paths.folder(was) / "manifest.json").is_file():
            return False
        if paths.folder(was).resolve() == listed[site].path.resolve():
            return False
        moves.append(Move(site, listed[site].folder, paths.rel(paths.folder(was))))
    repoint(paths, moves)
    return True


# ---------------------------------------------------------------------------------------------- lanes and build


def run_lanes(paths: Paths, limit: float = LANE_LIMIT, *, start=subprocess.Popen, clock=time.monotonic,
              sleep=time.sleep) -> str:
    """The three lanes side by side: 'ok' when each ended with 0, 'timeout' when one still ran after limit seconds
    (each is asked to stop, as docker compose stop does, and gets LANE_STOP seconds), else 'lane_<X>_exit_<code>'."""
    script = str(PROGRAMS / "lanes.sh")
    procs = {x: start(["bash", script, x], cwd=str(paths.repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for x in LANES}
    def stop() -> None:
        for p in procs.values():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        stop_by = clock() + LANE_STOP
        while any(p.poll() is None for p in procs.values()) and clock() < stop_by:
            sleep(POLL)
        for p in procs.values():
            if p.poll() is None:
                p.kill()

    deadline = clock() + limit
    try:
        while any(p.poll() is None for p in procs.values()):
            if clock() >= deadline:
                stop()
                return "timeout"
            sleep(POLL)
    except BaseException:  # this night was stopped (Stopped) or broke: no lane is left running behind it
        stop()
        raise
    for x, p in procs.items():
        if p.returncode != 0:
            return f"lane_{x}_exit_{p.returncode}"
    return "ok"


def run_build(paths: Paths, limit: float = BUILD_LIMIT, options: tuple[str, ...] = ()) -> int:
    """build.sh, its output on ours (docker logs) beside its own build.log. Not done within limit seconds (the card's
    memory shared with another program slows a build without failing it): stopped, and EXIT_TIMEOUT. options: more
    words for build.sh (--skip-marker-check after the lanes were stopped at their limit: what they held is put back)."""
    proc = subprocess.Popen(["bash", str(PROGRAMS / "build.sh"), *options], cwd=str(paths.repo))
    try:
        return proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
        return EXIT_TIMEOUT
    except BaseException:  # this night was stopped: the build goes with it
        proc.kill()
        raise


def disk_free_gb() -> float | None:
    """Free space, in GB, of the host drive behind /out (a bind of C:\\nknu-transfer\\out: the drive Docker's disk image
    grows on; measured 11-17: the container reports the host drive's own numbers there). None when /out is not
    mounted (a test, a run by hand): nothing to check."""
    try:
        return shutil.disk_usage(FLAG.parent).free / 2 ** 30 if FLAG.parent.is_dir() else None
    except OSError:
        return None


def gpu_mb() -> list[int] | None:
    """[used, total] MB of the graphics card, for the status file (how full it was when the build began); None when
    nvidia-smi is not there or says nothing readable."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30, check=False).stdout
        used, total = (int(x) for x in out.strip().splitlines()[0].split(","))
        return [used, total]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------------------------- status


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Status:
    paths: Paths
    data: dict = field(default_factory=dict)

    def begin(self, at: datetime) -> None:
        self.data = {"night": night_of(at), "started_at": at.isoformat(timespec="seconds"),
                     "finished_at": None, "outcome": "running", "step": "plan", "error": None, "sites_new": [],
                     "sites_unlisted": [], "sites_not_ready": [], "sites_held": [],
                     "library": {"before": None, "after": None},
                     "held": None, "departures": None, "unexplained_left": None, "new_reminders": [],
                     "student_eval": None, "disk_free_gb": None, "gpu_mb": None,
                     "log": f"{PIPE}/lab_pc/ingest.log", "build_log": f"{PIPE}/lab_pc/build.log"}
        _write(self.paths.status, self.data)

    def step(self, name: str) -> None:
        self.data["step"] = name
        _write(self.paths.status, self.data)
        say(f"步骤 {name}")

    def end(self, outcome: str, error: str | None = None) -> str:
        self.data.update(outcome=outcome, error=error, finished_at=now().isoformat(timespec="seconds"))
        _write(self.paths.status, self.data)
        with self.paths.history.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.data, ensure_ascii=False) + "\n")
        say(f"结束：{outcome}" + (f"（{error}）" if error else ""))
        return outcome


_LOG: Path | None = None


def say(words: str) -> None:
    line = f"[ingest {now():%F %T}] {words}"
    print(line, flush=True)
    if _LOG is not None:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _facts(lib: dict | None, changed: set[str]) -> dict | None:
    """A library for the status file: its totals and the documents of the sites that changed."""
    if lib is None:
        return None
    return {"docs": lib["docs"], "chunks": lib["chunks"], "built_at": lib["built_at"],
            "sites": {s: lib["sites"].get(s, 0) for s in sorted(changed)}}


def _missed(site: dict) -> int | None:
    """Titles a site's own search did not find: site_checks.json's self is [found, sampled]."""
    pair = site.get("self")
    return pair[1] - pair[0] if pair and None not in pair[:2] else None


# the number behind each reminder kind of site_report.py, as site_checks.json holds it
_COUNT = {"same_title": lambda s: s.get("same_title"), "fragments": lambda s: s.get("fragments"),
          "same_opening": lambda s: (s.get("same_opening") or [None])[0],
          "no_date": lambda s: (s.get("no_date") or [None])[0],
          "empty": lambda s: (s.get("empty_pages") or [None])[0], "self": _missed, "none": lambda s: 0}


def new_reminders(checks: Path, built_at: str) -> list[dict]:
    """site_report.py's 「这次新出现的提醒」 as data: [{site, kind, count}], when the checks file is of this library."""
    try:
        data = json.loads(checks.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("built_at") != built_at:
        return []
    sites = data.get("sites") or {}
    out = []
    for pair in data.get("new") or []:
        if isinstance(pair, list) and len(pair) == 2:
            site, kind = pair
            count = _COUNT.get(kind, lambda s: None)(sites.get(site) or {})
            out.append({"site": site, "kind": kind, "count": count})
    return out


def read_swap(paths: Paths, since: float) -> dict | None:
    """What the build that began at since said of its swap (nknu.content.build writes aca.swap.json whenever it had a
    library to compare with; spec 2026-11-17-ingest-guard-design §二); None when it wrote none, or the file is an
    earlier build's."""
    try:
        if paths.swap_json.stat().st_mtime < since - 1:
            return None
        told = json.loads(paths.swap_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return told if isinstance(told, dict) else None


STUDENT_EVAL = "student_eval.json"          # build.sh step 5b's measurement (eval/student_eval.py), the panels read it
STUDENT_EVAL_RUN = "student_eval.run.json"  # build.sh step 5b: {exit, seconds, at}, written whatever happened


def student_eval_ran(paths: Paths, since: float, folder: Path | None = None) -> dict | None:
    """build.sh step 5b in the build that began at since, as codes: outcome ok / failed / timeout / unreadable, exit,
    seconds; on ok also the measurement's time, the library it measured, n and totals (totals only: no question of
    the sealed half is ever in the status). None: it did not run in that build. folder: where that build wrote (a
    trial's own folder for promote; the pipeline folder by default)."""
    folder = paths.pipe if folder is None else folder
    path = folder / STUDENT_EVAL_RUN
    try:
        if path.stat().st_mtime < since - 1:
            return None
        said = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"outcome": "unreadable"}
    rc = said.get("exit") if isinstance(said, dict) else None
    out = {"outcome": "ok" if rc == 0 else "timeout" if rc == 124 else "failed", "exit": rc,
           "seconds": said.get("seconds") if isinstance(said, dict) else None}
    if rc == 0:
        try:
            got = json.loads((folder / STUDENT_EVAL).read_text(encoding="utf-8"))
            out.update(measured=got.get("measured"), library_built=(got.get("library") or {}).get("built"),
                       n=got.get("n"), totals=got.get("totals"))
        except (OSError, ValueError, AttributeError):
            out["outcome"] = "unreadable"
    return out


def held_detail(paths: Paths, told: dict | None = None) -> dict:
    """The swap guards' reason as data. site_collapsed: the sites the held library holds less than half of;
    unexplained_departures: too many documents left, or were emptied, for no reason the build can give (the documents
    themselves are the status file's departures); school_wave: a site lost more than a tenth of its documents to the
    school's side at once (departures.waves), which a person looks at before it is believed. told: read_swap's; without it (a build before 11-17) the first
    rule is read again from the two libraries."""
    if told is not None:
        detail = [{"site": s, "before": a, "after": b} for s, a, b in told.get("collapsed") or []]
        reasons = [r for r, on in (("site_collapsed", detail), ("unexplained_departures", told.get("too_many_left")),
                                   ("school_wave", told.get("school_wave"))) if on]
        return {"reason": reasons[0] if reasons else "site_collapsed", "reasons": reasons, "detail": detail,
                "db": paths.rel(paths.held_db)}
    detail = []
    with contextlib.suppress(Exception):
        from nknu.content.build import _collapsed  # the container has the package; a test without it gets no detail

        detail = [{"site": s, "before": a, "after": b} for s, a, b in _collapsed(paths.db, paths.held_db)]
    return {"reason": "site_collapsed", "reasons": ["site_collapsed"], "detail": detail,
            "db": paths.rel(paths.held_db)}


def culprits(told: dict | None) -> dict[str, dict]:
    """{site: {'why', 'count'}} of the sites a held build's guards blame (its swap file, read_swap): a site fallen below
    half (collapsed), a site whose documents left without a reason over the site's limit while what the other sites
    lost stays within the whole library's limit, a site in a wave of school deletions. {} when the trouble is no
    site's alone: spread thin over many sites, more than SET_BACK_MAX sites, a site without a name, or nothing held."""
    if not told or told.get("swapped") or not told.get("held"):
        return {}
    out: dict[str, dict] = {}
    for site, was, got in told.get("collapsed") or []:
        out[site] = {"why": "site_collapsed", "count": int(was) - int(got)}
    left = told.get("departures") or {}
    if told.get("too_many_left"):
        limits, by = left.get("limits") or {}, left.get("by_site") or {}
        over = {s: n for s, n in by.items() if n > limits.get("site", 0)}
        rest = sum(n for s, n in by.items() if s not in over)
        if not over or rest > limits.get("total", 0):
            return {}
        for site, n in over.items():
            out.setdefault(site, {"why": "unexplained_departures", "count": n})
    if told.get("school_wave"):
        for site, pair in (left.get("waves") or {}).items():
            out.setdefault(site, {"why": "school_wave", "count": pair[0] if pair else 0})
    return out if out and len(out) <= SET_BACK_MAX and all(out) else {}


def refuse(paths: Paths, blamed: dict[str, dict], night: str) -> list[dict]:
    """ingest-refused.txt with the folder each blamed site is listed at now (the one the guards refused), so that no
    later night tries it again; the entries of sites the crawler has since exported anew are dropped. The rows
    written, as the status file keeps them (sites_set_back)."""
    listed = {e.site: e.folder for e in bh.read_exports(paths.exports, paths.repo)}
    current = read_current(paths.current)
    kept = {s: e for s, e in read_refused(paths.refused).items()
            if s in current and paths.folder(current[s]).resolve() == paths.folder(e["folder"]).resolve()}
    rows = []
    for site, said in sorted(blamed.items()):
        kept[site] = {"folder": listed[site], "why": said["why"], "night": night}
        rows.append({"site": site, "why": said["why"], "count": said["count"], "folder": listed[site]})
    write_refused(paths, kept)
    return rows


# ---------------------------------------------------------------------------------------------- the night


class Stopped(Exception):
    """The container was asked to stop (docker stop sends SIGTERM): the night ends as failed, the lanes are stopped."""


def _on_term(signum, frame):
    raise Stopped("stopped")


@contextlib.contextmanager
def one_at_a_time(paths: Paths):
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def flag_swap(at: str) -> None:
    """Ask the host script to restart Datasette (it holds the replaced file open). No /out folder: nothing to ask."""
    with contextlib.suppress(OSError):
        if FLAG.parent.is_dir():
            FLAG.write_text(at + "\n", encoding="utf-8")


def night(paths: Paths, *, lanes=run_lanes, build=run_build, limit: float = LANE_LIMIT, disk=disk_free_gb,
          gpu=gpu_mb, left_of=waiting) -> int:
    global _LOG
    _LOG = paths.kit / "ingest.log"
    with one_at_a_time(paths) as mine:
        if not mine:
            at = now()
            with paths.history.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"night": night_of(at), "outcome": "busy",
                                    "started_at": at.isoformat(timespec="seconds"),
                                    "finished_at": at.isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            say("上一晚的进库还在跑，这一晚不开")
            return 0
        st = Status(paths)
        st.begin(now())
        with contextlib.suppress(ValueError):  # not the main thread (a test): no handler, nothing else changes
            signal.signal(signal.SIGTERM, _on_term)
        try:
            return _night(paths, st, lanes, build, limit, disk, gpu, left_of)
        except Exception as exc:  # noqa: BLE001  whatever broke, the night ends as failed with its reason
            st.end("failed", f"{type(exc).__name__}: {exc}")
            return 1


def _low_disk(st: Status, disk) -> bool:
    """Record the host drive's free space; True (and the night ended) when it is under DISK_MIN_GB."""
    free = disk()
    st.data["disk_free_gb"] = None if free is None else round(free, 1)
    if free is not None and free < DISK_MIN_GB:
        st.end("failed", f"disk_low_{int(free)}GB")
        return True
    return False


def _night(paths: Paths, st: Status, lanes, build, limit: float, disk, gpu, left_of=waiting) -> int:
    keep_lists(paths)  # before anything: the folders of the library in use are named in a list of their own
    todo = plan(paths)
    before = library(paths.db)
    st.data.update(sites_new=[{"site": m.site, "folder": m.new} for m in todo.moved],
                   sites_unlisted=todo.unlisted, sites_not_ready=todo.not_ready, sites_held=todo.held,
                   sites_refused=todo.refused)
    changed = {m.site for m in todo.moved}
    st.data["library"]["before"] = _facts(before, changed)
    if not todo.moved and not behind(paths, before):
        st.end("nothing_new")
        return 0
    if _low_disk(st, disk):  # before anything is changed: the list, the cache and the library stay as they are
        return 1
    if todo.moved:
        st.step("repoint")
        repoint(paths, todo.moved)
        say(f"清单改指 {len(todo.moved)} 站的新资料夹")
    st.step("convert")
    got = lanes(paths, limit)
    late: dict[str, int] = {}
    if got == "timeout":
        # 11-19: the sites still converting wait a night at the folder the library was built from; the rest is built
        late = left_of(paths)
        if not late or not go_back(paths, before, late):
            st.end("deferred", "lanes_timeout")
            return 0
        st.data["sites_waiting"] = [{"site": s, "files": n} for s, n in sorted(late.items())]
        st.data["sites_new"] = [x for x in st.data["sites_new"] if x["site"] not in late]
        changed -= set(late)
        say(f"转文字到点还没走完：{len(late)} 站还有档没转（{'、'.join(sorted(late))}），这一晚先退回正式库用的资料夹，其余照常建库")
        if not behind(paths, before):  # nothing else was new tonight
            st.end("deferred", "lanes_timeout")
            return 0
    elif got != "ok":
        st.end("failed", got)
        return 1
    if paths.pause.exists():
        st.end("paused")
        return 0
    if _low_disk(st, disk):
        return 1
    st.step("keep")
    link_prev(paths)
    st.step("build")
    st.data["gpu_mb"] = gpu()
    began = time.time()
    def note(told: dict | None) -> None:
        # every document that left, by reason; held or swapped. unexplained_left: how many left (or were emptied) for
        # no reason the build can give, said on the first line of `ingest status` even when the swap went ahead
        # (session 爬虫judge, 11-17: 「只要有说不通的离开，即使没到门槛，也请在人会看的地方亮出来」); None when nothing
        # was compared
        left = (told or {}).get("departures")
        st.data["departures"] = left
        st.data["unexplained_left"] = (left.get("unexplained_total")
                                       if isinstance(left, dict) and left.get("checked") else None)

    rc = build(paths, options=("--skip-marker-check",)) if late else build(paths)
    told = read_swap(paths, began)
    note(told)
    # 11-19: what one site or a few did is theirs alone: they go back, the folder is not tried again, the rest is
    # built once more. The first try's reasons stay in the status file (set_back_from).
    blamed = culprits(told) if rc == EXIT_HELD else {}
    if blamed and all(s in {e.site for e in bh.read_exports(paths.exports, paths.repo)} for s in blamed):
        rows = refuse(paths, blamed, st.data["night"])
        if go_back(paths, before, dict.fromkeys(blamed, 0)):
            st.data["sites_set_back"] = rows
            st.data["set_back_from"] = held_detail(paths, told)
            st.data["sites_new"] = [x for x in st.data["sites_new"] if x["site"] not in blamed]
            changed -= set(blamed)
            say("把关拦下的是这几站：" + "、".join(f"{r['site']}（{WHY_WORDS.get(r['why'], r['why'])}）" for r in rows)
                + "；它们退回正式库用的资料夹")
            if not behind(paths, before):  # they were all that was new: nothing left to build, the held library stays
                st.data["held"] = held_detail(paths, told)
                st.end("held")
                return EXIT_HELD
            say("其余的网站重建一次")
            paths.held_db.unlink(missing_ok=True)  # the first try's library: the status file keeps why it was held
            began = time.time()
            rc = build(paths, options=("--skip-marker-check",)) if late else build(paths)
            told = read_swap(paths, began)
            note(told)
        else:  # nothing to go back to: the folder is not marked refused, the night is held as before
            write_refused(paths, {s: e for s, e in read_refused(paths.refused).items() if s not in blamed})
    after = library(paths.db)
    swapped = after is not None and (before is None or after["built_at"] != before["built_at"])
    if swapped:
        st.step("done")
        keep_lists(paths)
        flag_swap(after["built_at"])
        if before is not None:  # every site whose count moved, not only the ones repointed tonight
            changed |= {s for s in set(before["sites"]) | set(after["sites"])
                        if before["sites"].get(s) != after["sites"].get(s)}
        st.data["library"] = {"before": _facts(before, changed), "after": _facts(after, changed)}
        st.data["new_reminders"] = new_reminders(paths.checks, after["built_at"])
        st.data["student_eval"] = student_eval_ran(paths, began)  # build.sh step 5b, the question sample (11-22)
        # the swap stands whatever the checks after it did (the owner's third point): their failure is only said
        st.end("swapped", None if rc == 0 else "checks_timeout" if rc == EXIT_TIMEOUT else f"checks_exit_{rc}")
        return 0
    if rc == EXIT_HELD and paths.held_db.is_file() and paths.held_db.stat().st_mtime >= began - 1:
        st.data["held"] = held_detail(paths, told)
        st.end("held")
        return EXIT_HELD
    st.end("failed", "build_timeout" if rc == EXIT_TIMEOUT else f"build_exit_{rc}")
    return 1


# ---------------------------------------------------------------------------------------------- rollback, status


def rollback(paths: Paths) -> int:
    """aca.prev.db back in place of the library in use, which stays as aca.rolled-back.db; and paused, so the next
    nights convert and build nothing until the pause file is removed (spec §四)."""
    with one_at_a_time(paths) as mine:
        if not mine:
            print("进库正在跑，等它结束再退回", file=sys.stderr)
            return 1
        if not paths.prev_db.is_file() or not paths.db.is_file():
            print(f"没有可以退回的库（{paths.prev_db}）", file=sys.stderr)
            return 1
        if paths.prev_db.samefile(paths.db):
            print("aca.prev.db 和现在的库是同一份（上一晚没有换库），没有可以退回的", file=sys.stderr)
            return 1
        began = now()
        left = library(paths.db)
        bad = paths.db.with_name("aca.rolled-back.db")
        bad.unlink(missing_ok=True)
        os.link(paths.db, bad)
        tmp = paths.db.with_name("aca.db.rollback")
        tmp.unlink(missing_ok=True)
        os.link(paths.prev_db, tmp)
        os.replace(tmp, paths.db)
        at = now().isoformat(timespec="seconds")
        paths.pause.write_text(f"{at} 退回前一份库以后暂停每晚换库；看过没问题再删掉这个档。\n", encoding="utf-8")
        keep_lists(paths)
        flag_swap(at)
        # one line of the night's shape: which library it went back to (library.after) and which one it left
        with paths.history.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"night": night_of(began), "started_at": began.isoformat(timespec="seconds"),
                                "finished_at": at, "outcome": "rolled_back", "step": "done", "error": None,
                                "library": {"before": _facts(left, set()), "after": _facts(library(paths.db), set())},
                                "db": paths.rel(bad)}, ensure_ascii=False) + "\n")
        print(f"退回了：现在用的是前一份库；被换下的留在 {bad.name}；已暂停每晚换库（{paths.pause.name}）。"
              "记得重启 Datasette。")
        return 0


# ---------------------------------------------------------------------------------------------- promote a trial

# What a build leaves beside its library (build.sh's <out>: the pipeline folder for the library in use, a trial's own
# folder for a trial), as the panels read it beside aca.db; the corpora are not among them (the library names its own,
# meta inputs_json, wherever they are).
REPORTS = ("build_report.json", "site_report.md", "site_checks.json", "totext/accounting.jsonl", "totext/summary.json",
           "totext/summary.md", "gold_docs.json", "gold_diff.txt", "offline_report_r3.md", "offline_report_r4.md",
           "checks.md", STUDENT_EVAL)
REPORT_GLOBS = ("offline_*.json",)
_TRIAL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_NOT_TRIALS = frozenset({"aca", "aca.prev", "aca.held", "aca.rolled-back"})


def trial_db(paths: Paths, name: str) -> Path:
    return paths.pipe / f"{name}.db"


def take_reports(paths: Paths, name: str) -> list[str]:
    """The reports in trial NAME's folder put where the build of the library in use leaves its own (beside aca.db),
    each renamed into place; a file that is the same already is left alone. What they replace is copied to
    lab_pc/previous/<time>/ first, as build.sh keeps a build's reports before the next one's. The names copied."""
    src = paths.pipe / name
    names = [n for n in REPORTS if (src / n).is_file()]
    names += sorted(q.name for g in REPORT_GLOBS for q in src.glob(g) if q.is_file())
    names = [n for n in dict.fromkeys(names)
             if not ((paths.pipe / n).is_file() and filecmp.cmp(src / n, paths.pipe / n, shallow=False))]
    keep = paths.kit / "previous" / now().strftime("%Y%m%d-%H%M%S")
    for n in names:
        if (paths.pipe / n).is_file():
            (keep / n).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths.pipe / n, keep / n)
    for n in names:
        dst = paths.pipe / n
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".promote")
        shutil.copy2(src / n, tmp)
        os.replace(tmp, dst)
    return names


def _cannot_promote(paths: Paths, name: str) -> str | None:
    """Why trial NAME may not take the library's place, in words; None when it may. It must be a library built beside
    the one in use, after it, whose build compared the two document by document and did not hold (its <NAME>.swap.json:
    the trial's name was a hard link of the library in use when the build began; forced does not count)."""
    if not _TRIAL_NAME.fullmatch(name) or name in _NOT_TRIALS:
        return f"试建库的名字不对：{name}"
    trial = trial_db(paths, name)
    if not trial.is_file() or not paths.db.is_file():
        return f"没有这份试建库或没有正式库（{trial.name}）"
    if trial.samefile(paths.db):
        return f"{trial.name} 和正式库是同一个档：还没建，或者已经换上了"
    mine, used = library(trial), library(paths.db)
    if mine is None or used is None or not mine["docs"]:
        return f"{trial.name} 或正式库读不了，或试建库是空的"
    if not mine["built_at"] > used["built_at"]:
        return f"{trial.name}（{mine['built_at']} 建）不比正式库（{used['built_at']} 建）新"
    try:
        told = json.loads(trial.with_name(f"{name}.swap.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        told = None
    checked = isinstance(told, dict) and isinstance(told.get("departures"), dict) and told["departures"].get("checked")
    if not checked:
        return f"{trial.name} 建的时候没有和正式库逐份核对（没有 {name}.swap.json，或它没比）"
    if told.get("swapped") is not True or told.get("forced"):
        return f"{trial.name} 的逐份核对没过（或是强制换的），不能直接换上"
    return None


def promote(paths: Paths, name: str) -> int:
    """Trial library NAME.db takes the place of the library in use without being built a second time (session
    爬虫judge for the owner, 11-18: once a trial has passed its checks students get it at once; a rebuild costs an hour
    and may take in exports nobody checked). The trial was built beside the library in use, compared with it by the
    build's own guards, and looked at by whoever runs this (the 33 questions, the real-data checks). What a night's
    swap leaves is left the same way: aca.prev.db is the library replaced (rollback works), the two lists of export
    folders, the status file and a history line (outcome swapped; promoted: the trial's file), the flag that restarts
    Datasette, the trial's swap file as aca.swap.json and its reports beside aca.db. aca.db becomes the trial's file
    by a hard link; the trial's own name and folder stay, since the library names its corpora there (meta
    inputs_json) and the trial's later checks (build.sh --db NAME.db --from 6) run on it: `reports NAME` brings those
    over. Exit 0 swapped; 1 refused (nothing changed) or failed before the swap."""
    global _LOG
    _LOG = paths.kit / "ingest.log"
    with one_at_a_time(paths) as mine:
        if not mine:
            print("进库正在跑，等它结束再换", file=sys.stderr)
            return 1
        why = _cannot_promote(paths, name)
        if why:
            print(f"不换：{why}", file=sys.stderr)
            return 1
        st = Status(paths)
        st.begin(now())
        try:
            return _promote(paths, st, name)
        except Exception as exc:  # noqa: BLE001  before the swap: the library in use is untouched
            st.end("failed", f"{type(exc).__name__}: {exc}")
            return 1


def _promote(paths: Paths, st: Status, name: str) -> int:
    trial = trial_db(paths, name)
    before, after = library(paths.db), library(trial)
    told = json.loads(trial.with_name(f"{name}.swap.json").read_text(encoding="utf-8"))
    todo = plan(paths)
    moved = sorted(s for s, f in after["exports"].items()
                   if not before["exports"].get(s) or paths.folder(before["exports"][s]) != paths.folder(f))
    changed = set(moved) | {s for s in set(before["sites"]) | set(after["sites"])
                            if before["sites"].get(s) != after["sites"].get(s)}
    free = disk_free_gb()
    st.data.update(sites_new=[{"site": s, "folder": paths.rel(paths.folder(after["exports"][s]))} for s in moved],
                   sites_unlisted=todo.unlisted, sites_not_ready=todo.not_ready, sites_held=todo.held,
                   sites_refused=todo.refused, promoted=paths.rel(trial),
                   disk_free_gb=None if free is None else round(free, 1))
    st.data["library"]["before"] = _facts(before, changed)
    st.step("keep")
    link_prev(paths)
    st.step("promote")
    tmp = paths.db.with_name("aca.db.promote")
    tmp.unlink(missing_ok=True)
    os.link(trial, tmp)
    os.replace(tmp, paths.db)  # from here on the swap stands, whatever the bookkeeping below does
    st.step("done")
    error = None
    try:
        left = told.get("departures")
        st.data["departures"] = left
        st.data["unexplained_left"] = left.get("unexplained_total") if left.get("checked") else None
        st.data["library"] = {"before": _facts(before, changed), "after": _facts(after, changed)}
        keep_lists(paths)
        flag_swap(after["built_at"])
        _write(paths.swap_json, told)
        # the trial's own step 5b (its folder), read before take_reports moves student_eval.json beside aca.db
        st.data["student_eval"] = student_eval_ran(paths, 0, folder=paths.pipe / name)
        st.data["reports"] = take_reports(paths, name)
        st.data["new_reminders"] = new_reminders(paths.checks, after["built_at"])
        if behind(paths, after):
            say("清单在这份试建以后又变了：下一次进库会照新清单重建")
    except Exception as exc:  # noqa: BLE001  said, never undone: the library is already the new one
        error = f"after_swap_{type(exc).__name__}: {exc}"
    st.end("swapped", error)
    return 0


def reports(paths: Paths, name: str) -> int:
    """After `promote NAME`: bring over the reports the trial's folder has gained since (its own checks). Only while
    the library in use is still that trial's file."""
    if not _TRIAL_NAME.fullmatch(name) or name in _NOT_TRIALS or not trial_db(paths, name).is_file() \
            or not paths.db.is_file() or not trial_db(paths, name).samefile(paths.db):
        print(f"不搬：{name}.db 不是现在的正式库", file=sys.stderr)
        return 1
    got = take_reports(paths, name)
    print("搬到正式库旁边的报告：" + ("、".join(got) if got else "没有新的"))
    return 0


_OUTCOME = {"running": "还在跑", "swapped": "换上了新库", "held": "把关没过，没换库", "nothing_new": "没有新资料，没重建",
            "paused": "暂停中：只转文字，没建库", "deferred": "转文字两小时内没走完，今晚没建库", "failed": "失败，库没动"}


def show(paths: Paths) -> int:
    try:
        d = json.loads(paths.status.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("还没有每晚进库的纪录")
        return 0
    n = d.get("unexplained_left")
    sites = list(((d.get("departures") or {}).get("by_site") or {}).items())[:3]
    look = (f"；有 {n} 份文件说不出原因地离开库或被掏空（" + "、".join(f"{s} {k}" for s, k in sites)
            + "），要人看" if n else "")
    print(f"{d.get('night')} 那一晚：{_OUTCOME.get(d.get('outcome'), d.get('outcome'))}{look}"
          f"（{d.get('started_at')} 开始，{d.get('finished_at') or '还没结束'}；步骤 {d.get('step')}）")
    if d.get("error"):
        err = str(d["error"])
        words = (f"主机 C 盘只剩约 {err[9:-2]} GB（不到 {DISK_MIN_GB} GB 就不建库）" if err.startswith("disk_low_") else
                 f"建库超过 {BUILD_LIMIT // 3600} 小时没做完，已停掉" if err == "build_timeout" else
                 "换库后的检查超时，库已经换上" if err == "checks_timeout" else err)
        print(f"  原因：{words}")
    if d.get("disk_free_gb") is not None or d.get("gpu_mb"):
        g = d.get("gpu_mb")
        print(f"  主机 C 盘剩 {d.get('disk_free_gb')} GB" + (f"；开建时显卡已用 {g[0]:,} / {g[1]:,} MB" if g else ""))
    new = d.get("sites_new") or []
    print(f"  有新资料夹的网站 {len(new)} 个" + ("：" + "、".join(s["site"] for s in new[:8]) + (" 等" if len(new) > 8 else "")
                                           if new else ""))
    lib = d.get("library") or {}
    if lib.get("before") and lib.get("after"):
        print(f"  文件 {lib['before']['docs']:,} → {lib['after']['docs']:,}，"
              f"小段 {lib['before']['chunks']:,} → {lib['after']['chunks']:,}")
    if d.get("held"):
        if d["held"].get("detail"):
            print("  没换库的原因：文件数掉到一半以下的网站：" + "、".join(
                f"{x['site']} {x['before']:,} → {x['after']:,}" for x in d["held"]["detail"][:8]))
        if "unexplained_departures" in (d["held"].get("reasons") or []):
            print("  没换库的原因：说不出原因地离开库（或被掏空）的文件太多，见下面")
        if "school_wave" in (d["held"].get("reasons") or []):
            print("  没换库的原因：有网站一次超过一成的文件被标成学校那边删掉了，要人看过是不是真的，见下面")
        print(f"  新库留在 {d['held'].get('db')}；看过确认要换，用 build.sh --force-swap 重建")
    for line in _left_words(d.get("departures")):
        print("  " + line)
    for key, words in (("sites_unlisted", "清单外的新网站（没自动加）"), ("sites_not_ready", "新资料夹还没导完的网站"),
                       ("sites_held", "留在原来资料夹的网站（ingest-hold.txt）")):
        if d.get(key):
            print(f"  {words}：" + "、".join(d[key][:8]))
    if d.get("sites_set_back"):  # 11-19: the guards refused these sites' new export; the others were built again
        print("  把关退回、这一晚没换新资料的网站："
              + "、".join(f"{r['site']}（{WHY_WORDS.get(r['why'], r['why'])}，{r['count']} 份）" for r in d["sites_set_back"][:8]))
        print(f"    它们的那份导出不会再试，等爬虫给新的导出；看过没问题要重试，删掉 {paths.refused.name} 里那一行")
    if d.get("sites_refused"):
        print("  最新的导出是上次被把关退回的那一份、这一晚没再试的网站：" + "、".join(d["sites_refused"][:8]))
    if d.get("sites_waiting"):  # 11-19: their unconverted files wait a night; the library keeps what it held of them
        print("  转文字到点还没转完、这一晚没换新资料的网站（下一晚接着转）："
              + "、".join(f"{w['site']}（还有 {w['files']} 个档）" for w in d["sites_waiting"][:8]))
    if d.get("new_reminders"):
        print("  新出现的提醒：" + "、".join(f"{r['site']} {r['kind']}" for r in d["new_reminders"][:8]))
    if d.get("promoted"):
        print(f"  这次没有重建：把检查过的试建库 {d['promoted']} 直接换成了正式库")
    if paths.pause.exists():
        print(f"  现在是暂停的（有 {paths.pause.name}）")
    return 0


def _left_words(left: dict | None) -> list[str]:
    """The departures of a night in words: how many documents left the library and why, then the ones without a
    reason, one by one (the first eight; all of them, up to fifty, are in the status file)."""
    if not left or not left.get("checked"):
        return []
    words = left.get("words") or {}

    def counted(part: dict) -> str:
        return "、".join(f"{words.get(k, k)} {n}" for k, n in part.items())

    lim = left.get("limits") or {}
    out = [f"换库会让 {left.get('left', 0)} 份文件离开库" + (f"，说得出原因的：{counted(left['explained'])}"
                                                    if left.get("explained") else "")]
    if left.get("waves"):
        out.append("一次被学校那边拿掉超过一成的网站：" + "、".join(
            f"{s} {n:,}/{of:,}" for s, (n, of) in list(left["waves"].items())[:5]))
    if not left.get("unexplained_total"):
        return [*out, "说不出原因的：0 份"]
    out.append(f"说不出原因的 {left['unexplained_total']} 份（{counted(left['unexplained'])}）；"
               f"超过全库 {lim.get('total')} 份或一站 {lim.get('site')} 份就不换库。最多的网站："
               + "、".join(f"{s} {n}" for s, n in list((left.get("by_site") or {}).items())[:5]))
    items = left.get("items") or []
    out += [f"  · {i.get('site')}｜{i.get('title')}｜{words.get(i.get('why'), i.get('why'))}"
            + (f"（{i['detail']}）" if i.get("detail") else "") for i in items[:8]]
    if len(items) > 8:
        out.append(f"  · ……其余的在 {PIPE}/ingest-status.json 的 departures.items")
    return out


def foresee(paths: Paths) -> int:
    """What a night would do now, in words; nothing is written."""
    todo = plan(paths)
    lib = library(paths.db)
    late = behind(paths, lib)
    listed = len(bh.read_exports(paths.exports, paths.repo))
    print(f"清单 {listed} 站；有新资料夹的 {len(todo.moved)} 站" + ("：" + "、".join(m.site for m in todo.moved[:8])
                                                         + (" 等" if len(todo.moved) > 8 else "") if todo.moved else ""))
    print(f"清单外的新网站 {len(todo.unlisted)} 个" + ("：" + "、".join(todo.unlisted[:8]) if todo.unlisted else "")
          + f"；新资料夹还没导完的 {len(todo.not_ready)} 站"
          + (f"；留在原来资料夹的 {len(todo.held)} 站：" + "、".join(todo.held[:8]) if todo.held else ""))
    if lib is None:
        print("现在没有正式库（或读不了）")
    else:
        print(f"正式库：{lib['docs']:,} 份文件、{lib['chunks']:,} 小段，{lib['built_at']} 建，{len(lib['sites'])} 站；"
              + ("不是照现在的清单建的" if late else "就是照现在的清单建的"))
    words = "今晚没有新资料，不重建" if not todo.moved and not late else (
        "会转文字，但暂停中、不建库" if paths.pause.exists() else "会转文字、重建、把关、换库")
    print("这时候开跑：" + words)
    return 0


def main(argv: list[str]) -> int:
    paths = Paths(Path(os.environ.get("NKNU_LANE_WORK") or bh.REPO))
    command = argv[0] if argv else "run"
    if command == "run":
        # NKNU_LANE_LIMIT: seconds the lanes may take, for a run by hand in the day when a site brings thousands of new
        # files (11-17: 特教中心's 20,188 new rows); the nights keep LANE_LIMIT
        return night(paths, limit=float(os.environ.get("NKNU_LANE_LIMIT") or LANE_LIMIT))
    if command == "rollback":
        return rollback(paths)
    if command == "status":
        return show(paths)
    if command == "plan":
        return foresee(paths)
    if command in ("promote", "reports") and len(argv) == 2:
        return (promote if command == "promote" else reports)(paths, argv[1])
    print("用法：nightly_ingest.py run｜rollback｜status｜plan｜promote 试建库名｜reports 试建库名", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
