"""定時更新: one night of the nightly chain, drawn inside 內容 at /sites?date=<run day> (owner 11-23: 「内容是全部内容，
tonight是一个晚上内容的变化…用内容已经整理好的可视化看更方便，哪里出问题直接那个区块变黄色或者红色」; the design copy
research/design_2026-11-23/sites.html, its scripts content_night.py, night.py and nm_site.py).

The page itself is 內容's (overview_page.html, which exposes window.APP and calls window.NM's hooks); this module gives it
two pieces of markup (band_html: the night's head, 問題 and the five steps on a time line, above the map; log_html: the
night's 紀錄, under everything) and the night's data as JSON, fetched by the browser (theme/design/night.js) one night at
a time: nothing of a night is baked into the 2 MB page.

What one night is made of, and where each part comes from:

  cells, runs, departures   nightly.one_night: the five steps' state, times and words, every ingest run of the
                            night, what the build's own check found of the documents that left
  問題                       the night's own problems in the sources' words (night.py's rule: a cell that warns, stops
                            or carries a code nobody knows; a run beside the shown one that did not end well; a document
                            that left with no reason), each with the sites its text names (by the library's keys)
  the head's numbers        history.Store's record of the night (ops.db: docs, new, changed, left, unexplained, the
                            chain's start and end); for a night not yet recorded, its cells and the swap's comparison
  the steps' in → out       the latest night: the steps as last read (pipeline.Pipeline); an older night: the steps kept
                            for it after it ended (Store.night_flow), else what the per-night files give (the crawl's
                            coverage line, nights/<day>.json); a night with none of these says so
  the map and side card     the swap's comparison of that night: the live one when it is this night's (by the day
                            history.Recorder files it under: changes_day), else the one kept for it
                            (Store.night_changes); else ops.db's per-site new and left (no 改了, no lists)
  a site's own numbers      coverage.jsonl and nights/<day>.json (nknu.ops.sources), else the kept or live steps' per-site
                            cells
  紀錄                       nknu.ops.logs' /api/logs over the night's own window (its start − 10 min to its last end
                            + 10 min: logs.night_bounds, the design's rule)
  a site's moments          the ledger's request table for 爬取 (critic CR-08: exact, by address), the logs for the rest

A night is named by its run day (the day its 01:00 run starts: 「名字按开跑那天」); ops.db and the comparison key it by the
day before (nightly.night_of); sources.run_day (nightly's, the one mapping, CR-03) and sources.night_of_run turn one
into the other: nothing here works the ±1 out by itself."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from nknu.ops import logs, nightly, sources
from nknu.ops import pipeline as pl
from nknu.ops.names import TITLES, link_name, link_tip, merge, rename
from nknu.ops.pipeline import TAIPEI, Where, when_of
from nknu.ops.sources import night_of_run, run_day

log = logging.getLogger(__name__)

# The routes web.py adds for this module (the integrator wires them; this module never imports web). Each handler
# takes the request's query parameters (a Mapping) and the app's objects named in args, and returns a dict that
# web.py sends as JSON with Cache-Control: no-store.
ROUTES_TO_WIRE = [
    {"path": "/api/night", "method": "GET", "handler": "api",
     "args": "where, store, request.query_params, flow=flow (the app's Pipeline), changes=night_changes (the app's "
             "Changes)",
     "returns": "JSON: one night (?date=<run day>; else the night 內容 opens on): {run, night, default, latest, nav, n, "
                "changes, flow, sites, sites_kind, store {on, why}, note, paused, reading, sig, live}; ?brief=1: "
                "{run, sig, live, reading} (the page's poll)"},
    {"path": "/api/night/ticks", "method": "GET", "handler": "ticks",
     "args": "where, store, request.query_params",
     "returns": "JSON: the moments of the night that name one site (?date, site): {run, site, ticks: [{st, t, n, msg}], "
                "cut}"},
]
# The page: web.sites_page replaces 內容's two placeholders, '<!--NIGHT-BAND-->' with band_html() and '<!--NIGHT-LOG-->'
# with log_html(), and loads this module's layer with 內容's own: design.layer("content", "night", "drawers"). /tonight
# and /tonight?night=N redirect (307) to /sites?date=<N + 1 day> (nightly.night_href). The night's 紀錄 reads
# nknu.ops.logs' own route (/api/logs?date=<run day>&lv=&src=&q=&n=&before=: its window is the night's, logs.night_bounds).

WD = "一二三四五六日"
STEPS = (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("swap", "換庫"))
FLOW_STEPS = ("crawl", "export", "totext", "build")  # the pipeline's own steps; 換庫 is built here (swap_flow)
CELL_KEYS = ("name", "state", "word", "sev", "say", "tip", "reason", "code", "since", "until", "took", "unknown")
RUN_KEYS = ("run", "since", "until", "outcome", "shown", "reason", "code", "unknown")
LIST_SHOWN = 12  # documents a site's side card lists per kind (the design's night.py: _lists) 【Claude】
PAD = timedelta(minutes=10)  # 紀錄 covers the night from its start − PAD to its last end + PAD (the design's window)
OPEN_SPAN = timedelta(hours=12)  # a night that has started and no end yet: its window this long after the start
TICK_SECONDS = 90  # a site's moments merged into buckets this wide (the design: a quarter percent of nine hours)
TICK_LINES = 4000  # log lines read for one site's moments 【Claude】
TICK_REQUESTS = 50_000  # ledger requests read for one site's 爬取 moments 【Claude】
SWAP_DOES = "建好的庫先把關（有網站少一半就不換），再換上"
# what each step does, when no reading of the steps is at hand (pipeline.py's own sentences)
DOES = {"crawl": "爬蟲照網站清單抓網頁和檔案，存進帳本和存檔；每晚再看一次各站的列表和首頁",
        "export": "爬蟲把每個網站抓到的網頁和檔案整理成一份清單和一個資料夾，給轉文字和建庫用",
        "totext": "把導出的網頁、PDF、Office、圖片（掃描的用 OCR 認字）、壓縮檔讀成文字；三條線分工轉，同一個檔只轉一次",
        "build": "把轉好的文字整理成文件、算向量、建成學生搜的庫", "swap": SWAP_DOES}
# what an older night lacks, said where its numbers would be (the owner's rule: no silent gaps)
LACKS = {"kept": "這一晚的檔案和逐筆紀錄只看得到最近一晚的（它們是現在的樣子）",
         "records": "這一晚只留下每一步的輸入、輸出和逐站數字；對帳、檔案和逐筆紀錄只看得到最近一晚的",
         "none": "這一晚的輸入、輸出沒留下"}
# the latest night while the steps are still being read (a restart: Pipeline.snapshot's first reading has not ended)
READING = "正在讀每一步的紀錄（第一次要幾秒到幾十秒），讀完自己換上"
NO_RECORDS = "還沒有紀錄：第一晚跑完以後就有"  # ops.db is on and holds no night yet (the old /tonight's words)
# a standing pause, put by hand, said on the night the page opens on and after (the old home's hm-paused line; the
# sources' own words: pipeline.nightly_step's note, nightly's 「暫停中（有 update.pause）」)
PAUSES = (("ingest.pause", "pipe", "進庫暫停中", "現在是暫停的（有 ingest.pause）：每晚只轉文字、不換庫"),
          ("update.pause", "archive", "更新暫停中", "現在是暫停的（有 update.pause）：每晚更新不跑，這晚沒更新"))
HOSTS = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[A-Za-z0-9_./-]+)?", re.IGNORECASE)
LOG_LEVELS = {"error", "warn", "info", "debug"}
# a log line's source → the time line's row; 爬取's moments are the ledger's requests, never its log lines (CR-08)
TICK_STEP = {"export": "export", "totext": "totext", "build": "build", "ingest": "build"}
_ticks_lock = threading.Lock()
_ticks_kept: dict[tuple, dict] = {}  # (archive, library, run day, site, window) -> the answer, nights that ended


# ------------------------------------------------------------------------------------------------ days


def label_of(run: str) -> tuple[str, str]:
    """('M/D', the weekday's character) of a run day, as the head shows it: 11/22 週日."""
    d = date.fromisoformat(run)
    return f"{d.month}/{d.day}", WD[d.weekday()]


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")) else None
    except ValueError:
        return None


def first_night(r: nightly.Reading, store, last: date) -> date:
    """The oldest night a page can step to: since the chain went live (or the oldest recorded), at most PICKABLE back."""
    starts = [d for d in (r.first_crawl, r.first_ingest) if d is not None]
    if store is not None and getattr(store, "enabled", False):
        try:
            got = store.nights((last - timedelta(days=nightly.PICKABLE)).isoformat(), last.isoformat())
        except Exception:  # noqa: BLE001  an unreadable record never breaks the page
            got = []
        starts += [d for d in (_day(x.get("night")) for x in got) if d is not None]
    oldest = last - timedelta(days=nightly.PICKABLE)
    return max(min(starts, default=last - timedelta(days=nightly.NIGHTS - 1)), oldest)


def nav(first: date, last: date) -> list[dict]:
    """Every night a page can step to, oldest first: {run, night, label, wd}."""
    out = []
    d = first
    while d <= last:
        run = run_day(d)
        lab, wd = label_of(run)
        out.append({"run": run, "night": d.isoformat(), "label": lab, "wd": wd})
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------------------------------------ problems


def site_of(x: str, keys: set[str]) -> str | None:
    """The site a host or address names (the longest key it is, or lies under); a bare host several keys share names
    none of them (the design picked one at random: the longest key under it)."""
    x = x.rstrip("/").lower()
    hit = [k for k in keys if x == k.lower() or x.startswith(k.lower() + "/")]
    if hit:
        return max(hit, key=len)
    under = [k for k in keys if k.lower().startswith(x + "/")]
    return under[0] if len(under) == 1 else None


def sites_named(text: str, keys: set[str]) -> list[str]:
    """The sites a problem's words name, in order, once each."""
    out: list[str] = []
    for m in HOSTS.finditer(str(text or "")):
        k = site_of(m.group(0), keys)
        if k and k not in out:
            out.append(k)
    return out


def problems(one: dict, keys: set[str]) -> list[dict]:
    """A night's problems in the sources' own words (the design's night.py): {sev, where, what, code, sites}."""
    out = []
    for c in one.get("cells") or []:
        if c.get("sev") in (pl.WARN, pl.BAD) or c.get("state") == nightly.STOP or c.get("unknown"):
            what = c.get("say") or c.get("reason") or ""
            out.append({"sev": pl.BAD if c.get("sev") == pl.BAD else pl.WARN, "where": c.get("name") or "",
                        "what": what, "code": c.get("code") or "",
                        "sites": sites_named(f"{what} {c.get('reason') or ''}", keys)})
    for x in one.get("runs") or []:
        if x.get("shown") or x.get("outcome") in ("swapped", "nothing_new", "running"):
            continue
        out.append({"sev": pl.BAD if x.get("outcome") == "failed" else pl.WARN, "where": f"進庫第 {x['run']} 次",
                    "what": x.get("reason") or "", "code": x.get("code") or "",
                    "sites": sites_named(x.get("reason") or "", keys)})
    for it in (one.get("departures") or {}).get("items") or []:
        site = str(it.get("site") or "")
        out.append({"sev": pl.WARN, "where": "說不出原因地離開", "what": f"{site}：{it.get('title') or ''}", "code": "",
                    "sites": [site] if site in keys else sites_named(site, keys)})
    return out


# ------------------------------------------------------------------------------------------------ the changes


def _get(obj, *names: str):
    """obj itself when it is a dict, else the first of its methods named that answers (a Pipeline's snapshot, a
    Changes' snapshot); None for None."""
    if obj is None or isinstance(obj, dict):
        return obj
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn()
    return None


def _labels(ch: dict) -> dict[str, str]:
    return {k: rename((v or {}).get("label") or k) for k, v in (ch.get("reasons") or {}).items()}


def _items(xs: list, why: str = "") -> list[dict]:
    """A list's documents as the side card links them: title, address, and the id of one still in the library (a
    document that left is not in it: it links to the school's page only, the design's night.py)."""
    out = []
    for x in xs or []:
        if isinstance(x, dict):
            item = {"t": x.get("title") or "", "u": x.get("url") or ""}
            if x.get("id") and not why:
                item["id"] = x["id"]
            if why:
                item["why"] = why
            out.append(item)
    return out


def changes_view(ch: dict) -> dict:
    """A swap's comparison as the map and the side card draw it: every site that changed with its counts and the first
    LIST_SHOWN of each list (title, address, id; a left one with its reason's key), the reasons' counts and labels."""
    sites = []
    for s in ch.get("sites") or []:
        lists = s.get("lists") or {}
        left = lists.get("left") or {}
        flat = ([x for k, xs in left.items() for x in _items(xs, k)] if isinstance(left, dict) else _items(left))
        sites.append({"site": s.get("site"), "name": s.get("name") or s.get("site"),
                      **{k: int(s.get(k) or 0) for k in ("new", "changed", "left", "unexplained")},
                      "lists": {"new": _items(lists.get("new"))[:LIST_SHOWN],
                                "changed": _items(lists.get("changed"))[:LIST_SHOWN], "left": flat[:LIST_SHOWN]}})
    t = ch.get("totals") or {}
    return {"kind": "full", "night": ch.get("night") or "", "sites": sites,
            "totals": {k: int(t.get(k) or 0) for k in ("new", "changed", "left", "unexplained")},
            "by_reason": dict(sorted((ch.get("by_reason") or {}).items(), key=lambda kv: -kv[1])),
            "labels": _labels(ch), "kinds": {k: (v or {}).get("kind") for k, v in (ch.get("reasons") or {}).items()},
            "prev": (ch.get("prev") or {}).get("docs"), "new": (ch.get("new") or {}).get("docs")}


def counts_view(rec: dict | None) -> dict:
    """An older night's per-site new and left from ops.db (no 改了 and no lists were kept for it), only when ops.db
    says they are exact (site_counts: the comparison's, or a night that swapped nothing); an ingest-counted swap keeps
    only some sites' rows (its unexplained and documents), never the night's split by site: none then."""
    if not rec or not rec.get("counted_by") or not rec.get("site_counts"):
        return {"kind": "none", "sites": [], "by_reason": {}, "labels": {}}
    sites = [{"site": s["site"], "name": s.get("name") or s["site"], "new": int(s.get("new") or 0), "changed": None,
              "left": int(s.get("left") or 0), "unexplained": int(s.get("unexplained") or 0), "lists": None}
             for s in rec.get("sites") or [] if (s.get("new") or 0) + (s.get("left") or 0) > 0]
    return {"kind": "counts", "sites": sites, "by_reason": {}, "labels": {}}


def changes_day(library, changes: dict | None) -> str | None:
    """The run day a swap's comparison belongs to, by the one rule history.Recorder.keep_changes files it under:
    sources.swap_day (the day the swap's run started, found through the nights/<day>.json that holds its built_at;
    else the Taipei day it was swapped in), else the run day of its 'night' (the swap time − 12 h). A hand promote at
    15:00 on 11-23 (its 'night' 11-23, whose run day is 11-24) is thus 11-23's on the page as in ops.db, never shown
    as 11-24's before 11-24 has run. None for a comparison not ready or naming no day."""
    if not isinstance(changes, dict) or changes.get("state") != "ready" or not changes.get("night"):
        return None
    try:
        return sources.swap_day(library, changes) or run_day(changes["night"])
    except (TypeError, ValueError, OSError):  # a comparison whose days are no dates: no night's
        return None


def night_changes(store, changes: dict | None, key: date, library=None) -> dict | None:
    """The swap comparison of the night (Changes.snapshot's shape): the live one when it is this night's (changes_day:
    the Recorder's own rule, so the live match and the copy it keeps in ops.db agree on the night), else the one kept
    for its run day; None without either. library: the library's folder (sources.library_dir)."""
    if changes and changes_day(library, changes) == run_day(key):
        return changes
    kept = getattr(store, "night_changes", None) if store is not None else None
    if callable(kept):
        try:
            got = kept(run_day(key))
        except Exception:
            log.exception("定时更新：读不到这一晚留下的比对")
            got = None
        if isinstance(got, dict) and got.get("sites") is not None:
            return got
    return None


# ------------------------------------------------------------------------------------------------ the steps


def _lost_rows(step: dict) -> list[list]:
    """A step's lost list as [word, n, key]: the reading's own words and keys (pipeline: every row, in the names.py
    words, with lost_keys), else the old words renamed and merged."""
    lost, keys = step.get("lost") or [], step.get("lost_keys") or []
    if keys and len(keys) == len(lost):
        return [[rename(str(w)), int(n), str(k[0])] for (w, n), k in zip(lost, keys, strict=True)]
    return [[w, n, ""] for w, n in merge(lost)]


def _title(step: dict) -> tuple[str, str]:
    """(the lost list's heading, its long label for the hover)."""
    long_ = step.get("lost_label") or "沒走到下一步的"
    return step.get("lost_title") or TITLES.get(long_, long_), long_


def flow_step(s: dict) -> dict | None:
    """One pipeline step as the night's detail draws it; None when it has no in and out."""
    if not s or not s.get("into") or not s.get("out"):
        return None
    title, tip = _title(s)
    return {"does": s.get("does") or DOES.get(s.get("key"), ""), "into": [list(x) for x in s["into"]],
            "out": [list(x) for x in s["out"]],
            "lost": _lost_rows(s), "lost_label": title, "lost_tip": tip,
            "link": [[sg, link_name(str(lb)), v, link_tip(str(tp or ""))] for sg, lb, v, tp in s.get("link") or []],
            "link_left": s.get("link_left"), "share": s.get("share", True),
            "sides": (s.get("sides") or []) if s.get("key") == "crawl" else []}


def swap_flow(before: int | None, after: int | None, ch: dict | None) -> dict | None:
    """換庫 as a step (the design's content_night.py): the library before → after, 對帳 換前 + 新進 − 離開 = 換後, and
    what left by its reasons. None when neither count is known."""
    if ch is not None:
        before = before if before is not None else (ch.get("prev") or {}).get("docs")
        after = after if after is not None else (ch.get("new") or {}).get("docs")
    if before is None or after is None:
        return None
    step = {"does": SWAP_DOES, "into": [["份文件（換前）", int(before)]], "out": [["份文件（換後）", int(after)]],
            "lost": [], "lost_label": TITLES.get("離開庫的（都說得出原因）", "離開庫的原因"),
            "lost_tip": "離開庫的（都說得出原因）", "link": [], "link_left": None, "share": False, "sides": []}
    if ch is not None:
        t = ch.get("totals") or {}
        labels = _labels(ch)
        by = sorted((ch.get("by_reason") or {}).items(), key=lambda kv: -kv[1])
        kinds = {k: (v or {}).get("kind") for k, v in (ch.get("reasons") or {}).items()}
        unknown = sum(n for k, n in by if kinds.get(k) == "unknown")
        step["link"] = [["", link_name("換前的庫"), int(before), ""], ["+", "新進", int(t.get("new") or 0), ""],
                        ["−", "離開", int(t.get("left") or 0), "、".join(f"{labels.get(k, k)} {n}" for k, n in by)],
                        ["=", link_name("換後的庫"), int(after), f"改了 {int(t.get('changed') or 0)} 份（份數不變）"]]
        step["lost"] = [[labels.get(k, k), int(n), k] for k, n in by]
        if unknown:  # the label said 都說得出原因 even when some could not be (the map's NV16)
            step["lost_tip"] = f"離開庫的（{unknown:,} 份說不出原因）"
    return step


def flow_of_snapshot(snap: dict, ch: dict | None, swap_out: tuple[int | None, int | None] = (None, None)) -> dict:
    """The five steps from a reading of the pipeline (live, or the one kept for an older night)."""
    st = {s.get("key"): s for s in snap.get("steps") or [] if isinstance(s, dict)}
    steps = {k: f for k in FLOW_STEPS if (f := flow_step(st.get(k) or {})) is not None}
    nightly_out = (st.get("nightly") or {}).get("out") or []
    before = nightly_out[0][1] if len(nightly_out) > 1 else swap_out[0]
    after = nightly_out[1][1] if len(nightly_out) > 1 else swap_out[1]
    if (swap := swap_flow(before, after, ch)) is not None:
        steps["swap"] = swap
    return steps


def chain_swap(rec: dict | None) -> dict | None:
    """The night's own swap in nights/<day>.json: the last one its chain ran (a hand promote later that day is not
    the night's), else the last; in the shape the readers below take (swap_shape)."""
    swaps = rec.get("swaps") if isinstance(rec, dict) else None
    swaps = [s for s in swaps if isinstance(s, dict)] if isinstance(swaps, list) else []
    own = [s for s in swaps if s.get("how") != "promote"]
    return swap_shape((own or swaps or [None])[-1])


def swap_shape(swap) -> dict | None:
    """A swap of nights/<day>.json with its sites an object of objects: a file cut short or written by hand can hold
    anything there (a list, a number, a site that is a string); what is not that shape is left out, so the night
    draws what the file does give and never fails (a copy: sources caches the file's own dict)."""
    if not isinstance(swap, dict):
        return None
    sites = swap.get("sites")
    sites = {str(k): v for k, v in sites.items() if isinstance(v, dict)} if isinstance(sites, dict) else {}
    return {**swap, "sites": sites}


def _sum_sites(swap: dict | None, field: str) -> tuple[int | None, int]:
    """The sum of one number over a swap's sites, and how many sites lack it (None: no site has it)."""
    vals = [s.get(field) for s in _obj(_obj(swap).get("sites")).values() if isinstance(s, dict)]
    have = [v for v in vals if isinstance(v, int)]
    return (sum(have) if have else None), len(vals) - len(have)


def flow_of_records(cov: dict | None, swap: dict | None, ch: dict | None, does: dict[str, str]) -> dict:
    """An older night's steps from what the per-night files hold: the crawl's coverage line, the swap's per-site
    numbers (nights/<day>.json); no 對帳, no notes, no side runs."""
    steps: dict[str, dict] = {}

    def step(key: str, into: list, out: list, lost: list | None = None, label: str = "沒走到下一步的",
             share: bool = True) -> None:
        title = TITLES.get(label, label)
        steps[key] = {"does": does.get(key) or DOES[key], "into": into, "out": out, "lost": lost or [],
                      "lost_label": title, "lost_tip": label, "link": [], "link_left": None, "share": share,
                      "sides": []}

    if cov and cov.get("meant"):
        lost = [[w, int(v["n"]), v["bucket"]] for w, v in (cov.get("labels") or {}).items()
                if v.get("bucket") not in ("got", *pl.NOT_MEANT) and v.get("n")]
        step("crawl", [[f"個這一輪該抓的網址（{cov.get('scope') or 0} 站）", cov["meant"]]], [["個抓到了", cov["got"]]],
             lost, "還沒抓到的（照爬蟲自己的分類）")
    rows, gone = _sum_sites(swap, "export_rows")
    if rows is not None:
        noun = "筆（每筆一個網頁、檔案或圖片）" + (f"；{gone} 站的舊導出資料夾已清掉，沒算進來" if gone else "")
        step("export", [[noun, rows]], [])
    status: dict[str, int] = {}
    for s in _obj(_obj(swap).get("sites")).values():
        for k, n in _obj(_obj(s).get("totext")).items():
            if isinstance(n, int):
                status[k] = status.get(k, 0) + n
    if status:
        lost = [[pl.STATUS_WORDS.get(k, k), n, k] for k, n in sorted(status.items(), key=lambda kv: -kv[1])
                if k != "ok" and n]
        step("totext", [["個檔和網頁", sum(status.values())]], [["個轉成文字", status.get("ok", 0)]], lost)
    records, _ = _sum_sites(swap, "records")
    docs, _ = _sum_sites(swap, "docs")
    if records is not None and docs is not None:
        step("build", [["筆記錄", records]], [["份文件", docs]], share=False)
    before, _ = _sum_sites(swap, "docs_before")
    if (sw := swap_flow(before, docs, ch)) is not None:
        steps["swap"] = sw
    return steps


# ------------------------------------------------------------------------------------------------ a site's numbers


def _obj(value) -> dict:
    """value when it is an object, else an empty one (a per-night file's part not in the shape written)."""
    return value if isinstance(value, dict) else {}


def _bucket_word(b: str) -> str:
    try:
        return sources.bucket_word(b)
    except Exception:  # noqa: BLE001  the crawler's package is not importable here
        return b


def _site_labels(buckets: dict, review: dict) -> dict[str, int]:
    """One site's addresses by the lost list's words (待確認 split by its causes when the codes add up)."""
    return {w: int(v["n"]) for w, v in sources.labels_of(buckets, review).items()
            if v.get("bucket") not in ("got", *pl.NOT_MEANT)}


def site_numbers(cov_line: dict | None, swap: dict | None, cells: list[dict] | None, keys: set[str],
                 names: dict[str, str]) -> tuple[dict, str]:
    """Each site's own numbers of the night ({key: {name, crawl: [meant, got], lost: [[bucket, word, n]], labels,
    export, export_gone, text, records, docs, docs_before}}) and where they are from: records (coverage.jsonl and
    nights/<day>.json), cells (a reading of the steps), none."""
    out: dict[str, dict] = {}
    kind = "none"
    # a line or a file not in the shape written (cut short, by hand) gives what it does give, never a failure
    per_cov = cov_line.get("sites") if isinstance(cov_line, dict) else None
    for key, s in (per_cov if isinstance(per_cov, dict) else {}).items():
        if key not in keys or not isinstance(s, dict):
            continue
        b = {k: v for k, v in _obj(s.get("buckets")).items() if isinstance(v, int)}
        r = {k: v for k, v in _obj(s.get("review")).items() if isinstance(v, int)}
        meant = sum(n for k, n in b.items() if k not in pl.NOT_MEANT)
        lost = [[k, _bucket_word(k), n] for k, n in sorted(b.items(), key=lambda kv: -kv[1])
                if k not in ("got", *pl.NOT_MEANT) and n]
        out.setdefault(key, {})["crawl"] = [meant, int(b.get("got", 0))]
        out[key]["lost"] = lost
        out[key]["labels"] = _site_labels(b, r)
        kind = "records"
    for key, s in _obj((swap_shape(swap) or {}).get("sites")).items():
        o = out.setdefault(key, {})
        rows = s.get("export_rows")
        o["export"] = rows if isinstance(rows, int) else None
        o["export_gone"] = not isinstance(rows, int)
        tx = _obj(s.get("totext"))
        ok = tx.get("ok")
        o["text"] = (ok if isinstance(ok, int) else 0 if ok is None else None) if tx else None
        for f in ("records", "docs", "docs_before"):
            o[f] = s.get(f) if isinstance(s.get(f), int) else None
        kind = "records"
    for row in cells or []:  # a reading's per-site cells fill what the records lack
        key = row.get("key")
        if not key or (keys and key not in keys):
            continue
        c = row.get("cells") or {}
        o = out.setdefault(key, {})
        cr = c.get("crawl") or {}
        if "crawl" not in o and cr:
            o["crawl"] = [int(cr.get("whole") or 0), int(cr.get("n") or 0)]
            o["lost"] = [[b, rename(str(w)), int(n)] for b, w, n in cr.get("lost") or []]
            o["labels"] = None  # 待確認's causes are not in a cell: the page asks the drawer's counts
        for col, f in (("export", "export"), ("text", "text"), ("docs", "docs")):
            if o.get(f) is None and (c.get(col) or {}).get("n") is not None:
                o[f] = int(c[col]["n"])
        if kind == "none":
            kind = "cells"
    for key, o in out.items():
        o["name"] = names.get(key) or key
    return out, kind


# ------------------------------------------------------------------------------------------------ one night


def _cell(c: dict) -> dict:
    return {k: c.get(k) for k in CELL_KEYS}


def _span(one: dict, rec: dict | None) -> tuple[str, str]:
    """(started, ended) of a night: the record's, else from its cells (a running night has no record: map NV05)."""
    started = (rec or {}).get("started_at") or ""
    ended = (rec or {}).get("ended_at") or ""
    sinces = sorted(c["since"] for c in one.get("cells") or [] if c.get("since"))
    if not started and sinces:
        started = sinces[0]
    running = any(c.get("state") == nightly.RUN for c in one.get("cells") or []) or one.get("state") == "run"
    if not ended and started and not running:
        untils = [c["until"] for c in one.get("cells") or [] if c.get("until")]
        untils += [x["until"] for x in one.get("runs") or [] if x.get("until")]
        ended = max(untils, default="")
    return started, ended


def window_of(n: dict) -> tuple[datetime, datetime] | None:
    """紀錄's window of a night: its start − PAD to its last end (the night's or a later run's) + PAD; None when it has
    not started."""
    a = when_of(n.get("started"))
    if a is None:
        return None
    ends = [x for x in (when_of(n.get("ended")), *(when_of(r.get("until")) for r in n.get("runs") or [])) if x]
    b = max(ends) if ends else a + OPEN_SPAN
    return a - PAD, b + PAD


def _reading(where: Where, now: datetime | None):
    now = now or pl.now_tpe()
    return nightly.Reading(where, now), now


def _pick(r: nightly.Reading, store, asked: str | None, now: datetime) -> tuple[date, date, date, date]:
    """(the night shown, the default night, the first and last a page can step to)."""
    default = nightly.default_night(r)
    last = nightly.night_of(now)
    first = first_night(r, store, last)
    d = _day(asked)
    key = date.fromisoformat(night_of_run(d)) if d else None
    if key is None or not first <= key <= last:
        key = default
    return key, default, first, last


def _night(r: nightly.Reading, key: date, store, keys: set[str]) -> tuple[dict, dict, dict | None]:
    """(the night as the page draws it, nightly.one_night's own, the ops.db record)."""
    one = nightly.one_night(r, key)
    rec = None
    if store is not None and getattr(store, "enabled", False):
        try:
            rec = store.get(key.isoformat())
        except Exception:
            log.exception("定时更新：读不到这一晚的纪录")
    run = run_day(key)
    lab, wd = label_of(run)
    started, ended = _span(one, rec)
    cells = {c["key"]: _cell(c) for c in one.get("cells") or []}
    n = {"night": key.isoformat(), "run": run, "label": lab, "wd": wd, "cells": cells,
         "runs": [{k: x.get(k) for k in RUN_KEYS} for x in one.get("runs") or []],
         "probs": problems(one, keys), "state": one.get("state"),
         **{k: (rec or {}).get(k) for k in ("swapped", "new", "changed", "left", "unexplained", "docs")},
         "started": started, "ended": ended, "kept": rec is not None,
         "live": any(c.get("state") == nightly.RUN for c in one.get("cells") or []) or one.get("state") == "run"}
    return n, one, rec


def store_state(store) -> dict:
    """{on, why}: whether ops.db keeps the nights, and why not (its own error: no file set, one inside the data or the
    crawler's folders, one that cannot be written) or that it holds none yet; why is '' when it keeps them."""
    on = bool(store is not None and getattr(store, "enabled", False))
    if not on:
        return {"on": False, "why": str(getattr(store, "error", "") or "沒有設定紀錄檔（--ops-db）")}
    try:
        empty = not store.count()
    except Exception:
        log.exception("定时更新：读不到纪录档")
        return {"on": True, "why": "讀不到紀錄檔"}
    return {"on": True, "why": NO_RECORDS if empty else ""}


def note_of(n: dict, st: dict, default: bool) -> str:
    """What the head says under it when the night's numbers are not kept (the owner's rule: no silent gaps), on any
    night: ops.db is off (its own reason) or holds no night yet (the old /tonight's words); on an older night that ran
    and ended, that ops.db holds none of it; '' otherwise (a night still to come or going on, or the one the page opens
    on, whose numbers are its cells' and the comparison's)."""
    if not st["on"]:
        return f"每晚的紀錄沒有存：{st['why']}；前幾晚的數字看不到"
    if st["why"]:
        return st["why"]
    if n.get("kept") or default or not n.get("started") or n.get("live"):
        return ""
    return "這一晚沒有存下紀錄：上面的數字和地圖只有這一晚留下的檔案給得出的"


def paused_now(where: Where) -> list[list[str]]:
    """The pauses put by hand that stand now: [[file, short words, the source's words]]."""
    out = []
    for name, base, short, words in PAUSES:
        folder = where.pipe if base == "pipe" else where.archive
        try:
            if folder is not None and (folder / name).exists():
                out.append([name, short, words])
        except OSError:
            continue
    return out


def _sig(n: dict, extra: str = "") -> str:
    parts = [str(n.get("state")), str(n.get("docs")), str(n.get("new")), str(n.get("left")), extra,
             *(f"{k}{c.get('state')}{c.get('sev')}{c.get('say')}{c.get('until')}" for k, c in n["cells"].items()),
             *(f"{x.get('run')}{x.get('outcome')}{x.get('until')}" for x in n.get("runs") or [])]
    return hashlib.blake2b("|".join(parts).encode(), digest_size=6).hexdigest()


def _library(where: Where) -> tuple[set[str], dict[str, str]]:
    try:
        sites = pl.library_facts(where.library)["sites"]
    except Exception:  # noqa: BLE001  a library being swapped in: no names for a moment
        return set(), {}
    return ({s["site"] for s in sites if s.get("site")},
            {s["site"]: s.get("name") or s["site"] for s in sites if s.get("site")})


def data(where: Where, store, run_day_asked: str | None = None, *, flow=None, changes=None,
         now: datetime | None = None) -> dict:
    """One night as the page draws it (module docstring): {run, night, default (its run day), latest (the steps
    shown are the ones read now, and the drawers can open), nav, n, changes, flow ({kind, lacks, steps}), sites,
    sites_kind, store ({on, why}: store_state), note (note_of: what the head says of numbers not kept), paused (the
    pauses that stand now, on the night the page opens on and after: paused_now), reading (the latest night's steps
    are still being read: the page asks again soon), sig, live}. run_day_asked: YYYY-MM-DD, a night a page can step
    to; else the night 內容 opens on."""
    r, now = _reading(where, now)
    key, default, first, last = _pick(r, store, run_day_asked, now)
    keys, names = _library(where)
    n, _one, rec = _night(r, key, store, keys)
    library = sources.library_dir(where)
    live_ch = _get(changes, "snapshot")
    ch = night_changes(store, live_ch, key, library)
    cv = changes_view(ch) if ch is not None else counts_view(rec)
    if cv["kind"] == "full":  # the head's numbers when ops.db has none yet: the comparison's
        for k in ("new", "changed", "left", "unexplained"):
            if n.get(k) is None:
                n[k] = cv["totals"][k]
        if n.get("docs") is None and cv.get("new") is not None:
            n["docs"] = cv["new"]
    run = n["run"]
    try:  # nights/<day>.json not in the shape written: the night drawn from what else there is, never a 500
        swap = chain_swap(sources.night_record(library, run))
    except Exception:
        log.exception("定时更新：读不到这一晚的换库纪录")
        swap = None
    cov = None
    cov_raw = None
    if where.archive is not None:
        try:
            got = sources.coverage_nights(where.archive, keys or None, limit=1, until=run, library=library)
            cov = next((x for x in got if x.get("night") == run), None)
            cov_raw = next((x for x in sources.coverage_lines(where.archive) if x.get("night") == run), None)
        except Exception:
            log.exception("定时更新：读不到抓取覆盖")
    latest = key == default
    snap = _get(flow, "snapshot") if latest else None
    # the latest night before the first reading of the steps has ended (a restart): said as being read, and the steps
    # kept for it in ops.db (if the Recorder kept them) or its own files meanwhile (G7)
    reading = bool(latest and snap is not None and snap.get("running") and not snap.get("steps"))
    kept = None
    if (not latest or not (snap or {}).get("steps")) and store is not None and callable(
            getattr(store, "night_flow", None)):
        try:
            kept = store.night_flow(run)
        except Exception:
            log.exception("定时更新：读不到这一晚留下的各步骤")
    docs_before, _ = _sum_sites(swap, "docs_before")
    docs_after, _ = _sum_sites(swap, "docs")
    if snap and snap.get("steps"):
        steps, fkind = flow_of_snapshot(snap, ch, (docs_before, docs_after)), "live"
    elif kept and kept.get("steps"):
        steps, fkind = flow_of_snapshot(kept, ch, (docs_before, docs_after)), "kept"
    else:
        does = {s.get("key"): s.get("does") for s in ((_get(flow, "last") or {}).get("steps") or [])
                if isinstance(s, dict)}
        steps = flow_of_records(cov, swap, ch, does)
        fkind = "records" if steps else "none"
    cells_src = (snap or {}).get("sites") if fkind == "live" else (kept or {}).get("sites")
    sites, sites_kind = site_numbers(cov_raw, swap, cells_src, keys, names)
    st = store_state(store)
    note = note_of(n, st, latest)
    if run_day_asked and str(run_day_asked)[:10] != run:  # never another night in its place without a word
        asked = _day(run_day_asked)
        said = f"{asked.month}/{asked.day}" if asked else str(run_day_asked)[:20]
        span = "–".join(label_of(run_day(d))[0] for d in (first, last))
        miss = f"沒有 {said} 這一晚的紀錄（可看 {span}），這裡是 {n['label']}"
        note = f"{miss}；{note}" if note else miss
    paused = paused_now(where) if key >= default else []
    lacks = READING if reading else LACKS.get(fkind, "")
    extra = f"{cv['kind']}{len(cv['sites'])}{fkind}{reading}{note}{[p[0] for p in paused]}"
    return {"run": run, "night": key.isoformat(), "default": run_day(default), "latest": latest and fkind == "live",
            "nav": nav(first, last), "n": n, "changes": cv,
            "flow": {"kind": fkind, "lacks": lacks, "steps": steps},
            "sites": sites, "sites_kind": sites_kind, "store": st, "note": note, "paused": paused,
            "reading": reading, "sig": _sig(n, extra), "live": n["live"]}


def api(where: Where, store, params: Mapping[str, str], *, flow=None, changes=None,
        now: datetime | None = None) -> dict:
    """GET /api/night: data() for ?date=; with ?brief=1 only what the page's poll compares ({run, sig, live,
    reading})."""
    got = data(where, store, params.get("date"), flow=flow, changes=changes, now=now)
    if params.get("brief") == "1":
        return {"run": got["run"], "sig": got["sig"], "live": got["live"], "reading": got["reading"]}
    return got


# ------------------------------------------------------------------------------------------------ a site's moments


def _iso(at: datetime) -> str:
    return at.astimezone(TAIPEI).isoformat(timespec="seconds")


def _window_for(where: Where, store, asked: str | None, now: datetime | None) -> tuple[str, tuple | None]:
    """(the run day shown for a date asked, the night's window: window_of)."""
    r, now = _reading(where, now)
    key, *_ = _pick(r, store, asked, now)
    n, _one, _rec = _night(r, key, store, set())
    return n["run"], window_of(n)


def _under(url: str, site: str) -> bool:
    """Whether an address lies under a site key (host, or host/path)."""
    parts = urlsplit(url if "//" in url else f"//{url}")
    rest = (parts.netloc + parts.path).lower().rstrip("/")
    key = site.lower().rstrip("/")
    return rest == key or rest.startswith(key + "/")


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _crawl_moments(where: Where, site: str, a: datetime, b: datetime,
                   keys: set[str] | None = None) -> tuple[list[tuple[float, str]], bool]:
    """The site's requests of the night from the ledger's request table (its started index): (epoch, words). An address
    is the site's when it lies under its key and under no longer key of the library (a host key, sso.nknu.edu.tw,
    beside the path keys on the same host, sso.nknu.edu.tw/news …: their requests are theirs, CR-08); the longer keys'
    addresses are left out in the query too, so the TICK_REQUESTS read are the site's own."""
    if where.archive is None or not (where.archive / "frontier.db").exists():
        return [], False
    host = site.split("/", 1)[0].lower()
    base = site.lower().rstrip("/")
    inner = sorted(k for k in (keys or ()) if k.lower().rstrip("/").startswith(base + "/"))
    sql = ("SELECT started, url, purpose, status FROM request WHERE started >= ? AND started <= ? AND host = ?")
    args: list = [a.timestamp(), b.timestamp(), host]
    for k in inner:  # the address forms under a longer key: …//key, …//key/…, …//key?…, …//key#…
        k = _like(k.lower().rstrip("/"))
        sql += " AND NOT (" + " OR ".join(["url LIKE ? ESCAPE '\\'"] * 4) + ")"
        args += [f"%//{k}", f"%//{k}/%", f"%//{k}?%", f"%//{k}#%"]
    try:
        conn = pl.open_ledger(where.archive)
    except (sqlite3.Error, OSError):
        return [], False
    try:
        rows = conn.execute(sql + " ORDER BY started LIMIT ?", (*args, TICK_REQUESTS + 1)).fetchall()
    except sqlite3.Error:
        return [], False
    finally:
        conn.close()
    cut = len(rows) > TICK_REQUESTS
    out = [(float(r[0]), f"{r[2] or ''} {r[3] if r[3] is not None else '—'} {r[1]}") for r in rows[:TICK_REQUESTS]
           if _under(str(r[1]), site) and not any(_under(str(r[1]), k) for k in inner)]
    return out, cut


def _log_moments(where: Where, site: str, a: datetime, b: datetime) -> tuple[list[tuple[str, float, str]], bool]:
    """The night's log lines (導出, 轉文字, 建庫, 進庫) naming the site: its key, or the key with / as _ (an export
    folder's name)."""
    out, seen, cut = [], set(), False
    for form in dict.fromkeys((site, site.replace("/", "_"))):
        got = logs.window(where, since=_iso(a), until=_iso(b), q=form, levels=LOG_LEVELS,
                          sources={"export", "totext", "build", "ingest"}, limit=TICK_LINES)
        cut = cut or bool((got or {}).get("more"))
        for row in (got or {}).get("rows") or []:
            t, _lv, src, msg = (list(row) + ["", "", "", ""])[:4]
            at = when_of(t)
            if at is None or src not in TICK_STEP or (t, msg) in seen:
                continue
            seen.add((t, msg))
            out.append((TICK_STEP[src], at.timestamp(), str(msg)))
    return out, cut


def ticks(where: Where, store, params: Mapping[str, str], *, now: datetime | None = None) -> dict:
    """GET /api/night/ticks: the moments of the night that name one site, per step, merged into TICK_SECONDS
    buckets: {run, site, ticks: [{st, t (the first moment, ISO), n, msg (its words, masked, 140 characters)}], cut (a
    list was longer than read)}."""
    site = str(params.get("site") or "").strip()
    run, win = _window_for(where, store, params.get("date"), now)
    if not site or win is None:
        return {"run": run, "site": site, "ticks": [], "cut": False}
    a, b = win
    ended = (now or pl.now_tpe()) > b
    memo = (str(where.archive), str(where.library), run, site, a.isoformat(), b.isoformat())
    if ended:
        with _ticks_lock:
            if memo in _ticks_kept:
                return _ticks_kept[memo]
    crawl, cut1 = _crawl_moments(where, site, a, b, _library(where)[0])
    other, cut2 = _log_moments(where, site, a, b)
    buckets: dict[tuple[str, int], dict] = {}
    for st, at, msg in [("crawl", t, m) for t, m in crawl] + other:
        k = (st, int((at - a.timestamp()) // TICK_SECONDS))  # buckets from the window's start
        got = buckets.get(k)
        if got is None:  # masked as every log row and drawer row is (pipeline._SECRETS), before it is cut
            buckets[k] = {"st": st, "t": _iso(datetime.fromtimestamp(at, TAIPEI)), "n": 1,
                          "msg": logs.mask(str(msg))[:140]}
        else:
            got["n"] += 1
    out = {"run": run, "site": site, "ticks": sorted(buckets.values(), key=lambda x: (x["st"], x["t"])),
           "cut": cut1 or cut2}
    if ended:
        with _ticks_lock:
            _ticks_kept[memo] = out
            while len(_ticks_kept) > 64:
                _ticks_kept.pop(next(iter(_ticks_kept)))
    return out


# ------------------------------------------------------------------------------------------------ the markup

HELP = ("日期：開跑那天，01:00 起\n網站圖：灰 沒變 · 紫 有變（越深越多）· 黃紅 被問題點名\n點網站：右邊看它這一晚的文件\n← →：換一天")
CFG = {"api": "/api/night", "log": "/api/logs", "ticks": "/api/night/ticks"}
SEARCH_ICON = ("<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6'>"
               "<circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/></svg>")
MAX_BTN = ("<button type='button' class='lv-max-btn' aria-label='放大紀錄' data-tip='放大看紀錄（Esc 關閉）'>"
           "<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6' "
           "stroke-linecap='round'><path d='M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9'/></svg></button>")


def band_html() -> str:
    """The night's band above 內容's map (hidden until night mode): its head (state, the night's span, ‹ day ›, 文件總數
    and +/−, ?), 問題, and the five steps on a time line; night.js fills them from /api/night."""
    tip = html.escape(HELP, quote=True).replace("\n", "&#10;")
    cfg = json.dumps(CFG, ensure_ascii=False).replace("</", "<\\/")
    return (
        "<section class='nt' id='nightBand' hidden>\n"
        "<div class='pn-card nt-head'>\n"
        "  <div class='nt-left'><span class='nt-badge' id='ntBadge'></span><span class='nt-span' id='ntSpan'></span>"
        "<span class='nt-pause' id='ntPause' hidden></span></div>\n"
        "  <div class='nt-nav'><a class='nt-step' id='ntPrev' aria-label='前一晚'>‹</a><div class='nt-day'><b id='ntDay'>"
        "</b><span id='ntWd'></span></div>\n"
        "    <a class='nt-step' id='ntNext' aria-label='後一晚'>›</a></div>\n"
        "  <div class='nt-right'><div class='nt-kpis' id='ntKpis'></div>\n"
        f"  <button type='button' class='hp' aria-label='定時更新怎麼看' data-tip='{tip}'>?</button></div>\n"
        "  <p class='nt-note' id='ntNote' hidden></p>\n"
        "</div>\n"
        "<div class='pn-card nt-probs' hidden><div class='pn-chd'><h2>問題</h2><span class='nt-n' id='ntProbN'></span>"
        "</div><ul class='td' id='ntProbs'></ul></div>\n"
        "<div class='pn-card nt-flow'><div id='ntGantt'></div></div>\n"
        f"<script type='application/json' id='nt-cfg'>{cfg}</script>\n"
        "</section>")


def log_html() -> str:
    """The night's 紀錄 card under everything (hidden until night mode): a search box, the maximize button, the level
    and source chips, the histogram and the list; night.js fills it from /api/logs?date=<run day>."""
    return (
        "<section class='card nt-log' id='nightLog' hidden><div class='pn-chd'><h2>紀錄</h2>\n"
        f"  <label class='lv-find'>{SEARCH_ICON}\n"
        "  <input id='lv-q' type='search' placeholder='搜尋紀錄（網址、站名、字詞）' autocomplete='off'></label>"
        f"{MAX_BTN}</div>\n"
        "  <div class='lv'><div class='lv-bar'><div class='lv-chips' id='lv-lv'></div><div class='lv-chips' "
        "id='lv-src'></div></div>\n"
        "  <div class='lv-hist' id='lv-hist'></div><div class='lv-list' id='lv-list' role='log'></div></div></section>")
