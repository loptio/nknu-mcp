"""The two per-night files other programs write for the panels, read here once for every page (11-23 port of the
owner's design copy, research/design_2026-11-23):

  coverage.jsonl     the crawler's coverage of each night (nknu.archive.coverage, compose service `coverage`, run
                     after the ingest; 11-16..11-23 backfilled from the nightly ledger backups): one line a night,
                     keyed by the run day, {night, ledger, written_at, round2_open, sites: {key: {buckets, review,
                     batch?}}, others}; zeros omitted, owner_site '' under "". It holds no totals: which sites count is
                     the panels' to say, so a night's numbers are summed here over the library's sites, the batch of
                     round two kept apart (pipeline.crawl_buckets' scope; the design's 抓取覆蓋 and its 7-night trend)
  nights/<day>.json  each swap's per-site numbers (deploy/lab-pipeline/night_sites.py, written by
                     nightly_ingest.record_night after every swap; backfilled from 11-15): {day, swaps: [{started_at,
                     finished_at, how: run|promote, built_at, prev_built_at, departures | null, sites: {key: {export,
                     export_changed, export_rows | null, records, totext, docs, docs_before, unexplained_left}}}]};
                     a night without a swap has no file; export_rows is null once the crawler cleared the folder
                     (the pages say 「舊導出資料夾已清掉」, never 0)

Both are read-only here, kept in memory by file and time of change, and missing files are quiet: [] or None (an old
install, a test, a night the step did not run). Nothing here writes.

A night is named by the day its 01:00 run starts (the owner, 11-23: 「名字按开跑那天」): coverage.jsonl and the nights
folder use that run day already; ops.db's nights, steps, sites and problems (nknu.ops.history) and the swap comparison
(nknu.ops.changes) key a night by the Taipei day twelve hours before (nightly.night_of), one day earlier. The one
mapping is nightly.run_day (re-exported here) and its inverse night_of_run(), so no page works the ±1 out by itself
(critic CR-03). A swap is filed under the day nights/<day>.json files it (swap_day: the day its run started; a hand
promote in the afternoon is that day's, beside the 01:00 run's swap), and ops.db's night_changes uses the same day."""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from nknu.ops import pipeline as pl
from nknu.ops.nightly import run_day  # noqa: F401 - the one mapping night key → run day (CR-03), re-exported

COVERAGE = "coverage.jsonl"  # nknu.archive.coverage.FILE, under <archive>/reports
NIGHTS = "nights"  # night_sites.NIGHTS, beside the library
NOT_MEANT = pl.NOT_MEANT  # rule, next_round: what this round is not to fetch, no loss
OURS = ("review", "failed", "queued")  # 要處理: ours to look at or retry (the design's cards_all.py)
SCHOOL = ("gone", "login")  # 學校那邊: the school's, only noted
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()
_files: dict[str, tuple[tuple, object]] = {}  # path -> (its stat key, what was read from it)


def night_of_run(day: str | date) -> str:
    """ops.db's night key (and Changes' 'night') of the run that starts on day: nightly.run_day's inverse."""
    d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    return (d - timedelta(days=1)).isoformat()


def library_dir(where: pl.Where) -> Path:
    """The folder of the library in use (the nights folder and aca.prev.db sit beside it)."""
    return where.library.parent


def library_sites(library: Path) -> set[str]:
    """The site keys of a library's sites table (the scope every crawl number is summed over); empty when unreadable."""
    try:
        return {s.get("site") for s in pl.library_facts(library)["sites"] if s.get("site")}
    except Exception:  # noqa: BLE001 - a library being swapped in, a test without one: no scope, said by the caller
        return set()


def _stat_key(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _cached(path: Path, read):
    """read(path) once per version of the file; None when it is not there."""
    key = _stat_key(path)
    if key is None:
        return None
    with _lock:
        got = _files.get(str(path))
        if got is not None and got[0] == key:
            return got[1]
    value = read(path)
    with _lock:
        _files[str(path)] = (key, value)
        while len(_files) > 64:
            _files.pop(next(iter(_files)))
    return value


# ------------------------------------------------------------------------------------------------ coverage


def coverage_path(crawl_root: Path) -> Path:
    """Where the crawler writes its coverage lines: <archive>/reports/coverage.jsonl (the archive is Where.archive,
    /crawl/archive in the ops container, as crawl_sides reads update-status.json beside it)."""
    return Path(crawl_root) / "reports" / COVERAGE


def _read_coverage(path: Path) -> list[dict]:
    by_night: dict[str, dict] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        try:
            got = json.loads(line)
        except ValueError:
            continue  # a line cut short: its night is simply absent (a gap)
        if isinstance(got, dict) and _DAY.match(str(got.get("night") or "")) and isinstance(got.get("sites"), dict):
            by_night[got["night"]] = got
    return [by_night[k] for k in sorted(by_night)]


def coverage_lines(crawl_root: Path | None) -> list[dict]:
    """Every line of coverage.jsonl as written, oldest first, one a night; [] when there is none."""
    if crawl_root is None:
        return []
    return _cached(coverage_path(crawl_root), _read_coverage) or []


def bucket_word(bucket: str, words: dict[str, str] | None = None) -> str:
    """A crawl bucket in the panel's words (names.py: 已取得, 連線失敗, 校方已移除 …)."""
    return (words if words is not None else pl.bucket_words()).get(bucket, bucket)


def _sum(into: dict, add: dict | None) -> None:
    for k, n in (add or {}).items():
        if isinstance(n, int):
            into[k] = into.get(k, 0) + n


def _figures(buckets: dict[str, int], review: dict[str, int]) -> dict:
    """got, meant, pct and the renamed review causes of one set of bucket counts (meant = all − rule − next_round)."""
    got = int(buckets.get("got", 0))
    meant = sum(n for b, n in buckets.items() if b not in NOT_MEANT)
    words: dict[str, int] = {}
    for code, n in sorted(review.items(), key=lambda kv: -kv[1]):
        w = pl.review_word(code)
        words[w] = words.get(w, 0) + n
    return {"got": got, "meant": meant, "pct": (got / meant * 100) if meant else None,
            "buckets": dict(sorted(buckets.items(), key=lambda kv: -kv[1])), "review": words,
            "ours": sum(buckets.get(b, 0) for b in OURS), "school": sum(buckets.get(b, 0) for b in SCHOOL)}


def labels_of(buckets: dict[str, int], review: dict[str, int]) -> dict[str, dict]:
    """{word: {'bucket', 'n'}}: every bucket by its word, 待確認 split into its causes (the words of the crawl step's lost
    list and the drawer's labels: one source, the design's cov_labels.json), largest first."""
    out: dict[str, dict] = {}
    split = review and sum(review.values()) == buckets.get("review")
    words = pl.bucket_words()
    for b, n in buckets.items():
        if not n or (b == "review" and split):
            continue
        w = bucket_word(b, words)
        out.setdefault(w, {"bucket": b, "n": 0})["n"] += n
    if split:
        for code, n in review.items():
            w = pl.review_word(code)
            out.setdefault(w, {"bucket": "review", "n": 0})["n"] += n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))


def night_line(line: dict, sites: set[str] | None = None) -> dict:
    """One coverage line summed over sites (None: every site the line names): the night's figures (got, meant, pct as
    a percentage, buckets, review by renamed word, ours = 要處理, school = 學校那邊, labels), the batch of round two
    apart, and the same per site."""
    per = line.get("sites") or {}
    keys = set(per) if sites is None else set(sites)
    buckets: dict[str, int] = {}
    review: dict[str, int] = {}
    batch: dict[str, int] = {}
    out_sites: dict[str, dict] = {}
    for key in sorted(keys & set(per)):
        s = per[key] if isinstance(per[key], dict) else {}
        b = {k: v for k, v in (s.get("buckets") or {}).items() if isinstance(v, int)}
        r = {k: v for k, v in (s.get("review") or {}).items() if isinstance(v, int)}
        _sum(buckets, b)
        _sum(review, r)
        _sum(batch, s.get("batch"))
        out_sites[key] = {**_figures(b, r), **({"batch": s["batch"]} if s.get("batch") else {})}
    return {"night": line.get("night"), "written_at": line.get("written_at"), "ledger": line.get("ledger"),
            "round2_open": list(line.get("round2_open") or []), **_figures(buckets, review),
            "labels": labels_of(buckets, review), "batch": batch, "scope": len(out_sites), "sites": out_sites}


COVERAGE_DUE = 12  # by noon the night's coverage line is written (it follows the ingest; the chain ends by ~10:00) 【Claude】


def expected_night(now: datetime | None = None) -> str:
    """The newest run day whose coverage line should be written by now: today's after COVERAGE_DUE, else yesterday's."""
    now = (now or pl.now_tpe()).astimezone(pl.TAIPEI)
    day = now.date() if now.hour >= COVERAGE_DUE else now.date() - timedelta(days=1)
    return day.isoformat()


def coverage_nights(crawl_root: Path | None, sites: set[str] | None = None, limit: int = 7, *,
                    until: str | None = None, library: Path | None = None, now: datetime | None = None) -> list[dict]:
    """The crawl coverage of the last `limit` run days, newest last (night_line's shape for each). The window is the
    `limit` days ending at `until` (a run day; default the newest line's), and a night inside it without a line is
    simply absent: the page draws a gap for it, never half a line (compare the nights' dates). sites: the scope (the
    library's site keys: library_sites); library: the library's folder, when given a night that has a swap record
    (nights/<day>.json) is summed over that night's own library sites instead (CR-01: an older night's scope is its
    library's, not today's). [] when the file is not there.

    Without `until` every night also says whether the file is behind ('expected': the run day whose line should be
    written by now, expected_night(now); 'stale': the newest line is older than that, so the step stopped writing
    and the last night is not tonight's: the card names its night instead of showing it as current; 'missing': how
    many nights after the newest line have none). Pass until=expected_night() to have those nights as gaps at the
    end of the window instead (C02/C03)."""
    lines = coverage_lines(crawl_root)
    if not lines or limit <= 0:
        return []
    newest = lines[-1]["night"]
    last = until if until and _DAY.match(until) else newest
    first = (date.fromisoformat(last) - timedelta(days=limit - 1)).isoformat()
    behind: dict = {}
    if not until:
        want = expected_night(now)
        gap = (date.fromisoformat(want) - date.fromisoformat(newest)).days
        behind = {"expected": want, "stale": gap > 0, "missing": max(0, gap)}
    out = []
    for line in lines:
        if not first <= line["night"] <= last:
            continue
        scope = sites
        if library is not None:
            own = night_sites(library, line["night"])
            scope = own if own else sites
        out.append({**night_line(line, scope), **behind})
    return out


# ------------------------------------------------------------------------------------------------ nights/<day>.json


def nights_dir(library: Path) -> Path:
    """library: the library's folder (library_dir), or the library file itself."""
    library = Path(library)
    return (library.parent if library.suffix == ".db" else library) / NIGHTS


def _read_json(path: Path) -> dict | None:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def night_record(library: Path, day: str) -> dict | None:
    """nights/<run day>.json as written ({day, swaps: [...]}); None for a night without a swap, a day that is no date,
    or a file that cannot be read."""
    if not _DAY.match(str(day or "")):
        return None
    got = _cached(nights_dir(library) / f"{day}.json", _read_json)
    if not isinstance(got, dict) or not isinstance(got.get("swaps"), list):
        return None
    return got


def night_record_days(library: Path) -> list[str]:
    """The run days that have a record, oldest first."""
    try:
        files = list(nights_dir(library).glob("*.json"))
    except OSError:
        return []
    return sorted(p.stem for p in files if _DAY.match(p.stem))


def night_swap(library: Path, day: str) -> dict | None:
    """The night's last swap (a day can hold several: a hand promote after the night's own), None without one."""
    rec = night_record(library, day)
    swaps = [s for s in (rec or {}).get("swaps") or [] if isinstance(s, dict)]
    return swaps[-1] if swaps else None


def night_sites(library: Path, day: str) -> set[str] | None:
    """The site keys of the library the night's last swap put in place; None without a record."""
    swap = night_swap(library, day)
    if swap is None or not isinstance(swap.get("sites"), dict) or not swap["sites"]:
        return None
    return set(swap["sites"])


def export_rows_word(row: dict) -> str:
    """How a swap record's export_rows is said: the count, or that the old export folder was cleared (never 0)."""
    n = row.get("export_rows")
    return f"{n:,}" if isinstance(n, int) else "舊導出資料夾已清掉"


def swap_day(library: Path | None, changes: dict | None) -> str | None:
    """The day a swap comparison (Changes.snapshot(), ready) is filed under, the way nights/<day>.json files the swap:
    the day its ingest run started (night_sites.write_swap: started_at[:10], Taipei). Found by the record whose swap
    put this library in place (its built_at); without one (no nights folder yet, a swap before 11-15) the Taipei day
    the new library was swapped in. None when the comparison names neither. A hand promote at 15:00 is that day's,
    beside the 01:00 run's swap, never the next night's (CR-03)."""
    if not isinstance(changes, dict):
        return None
    new = changes.get("new") if isinstance(changes.get("new"), dict) else {}
    built, swapped = new.get("built_at"), pl.when_of(new.get("swapped_at") or new.get("built_at"))
    if library is not None and built:
        near = swapped.astimezone(pl.TAIPEI).date() if swapped else None
        days = ([(near + timedelta(days=d)).isoformat() for d in (0, -1, 1)] if near
                else night_record_days(library)[::-1][:7])
        for day in days:
            if any(isinstance(s, dict) and s.get("built_at") == built
                   for s in (night_record(library, day) or {}).get("swaps") or []):
                return day
    return swapped.astimezone(pl.TAIPEI).date().isoformat() if swapped else None
