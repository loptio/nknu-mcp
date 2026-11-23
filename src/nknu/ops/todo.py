"""要處理: what 總覽 asks the owner to look at, in one list (2026-11-22 evening; owner: 「你先全部做」 after the review
that the panels showed the same things in several places and lit old problems like new ones every morning).

Its rows come from what the panels already know, each kept where it is computed:

  the night and the steps   tonight_page.problems (a part that stopped, a run that failed, a step not normal, a site
                            to look at), split by standing.split into new since the night before and standing
  this machine              a drive or a memory past its warning line (pipeline's machine numbers)

(The student questions' nightly measurement gave rows too for an evening; the owner dropped that measurement the same
night, 2026-11-22: the question bank checks the whole project, never the night.)

A problem the owner has said is known is not counted: it is listed folded, with the note, so it can be found and
taken off again. He marks it on 總覽 itself (a button on the row, a note if he likes; 取消 takes it off), kept in the
panel's own ops.db (Known); until 2026-11-22 night it was a file edited by hand on his word, which made him ask
someone for every row.

Rows name what is wrong and where, never who fixes it (spec 2026-11-17-panels-v3-questions.md 八.2). 【Claude】

已知 (owner 11-23, the design's third tab): every mark, kept until he takes it off (no automatic clearing), each with
when it was made and where its problem stands: still listed (「今晚還在」), or the run day of the first kept list
without it (「MM-DD 起沒再出現」, history.Store.problem_sightings over the lists kept a night in the same ops.db).

One list for both places (night_rows): the night's problems (tonight_page.problems) and the raw files' backup behind
(warc_rows) are what 待處理 shows and what the nights' record keeps, so a row ages and a mark is seen the same way.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from nknu.ops import tonight_page
from nknu.ops.nightly import CELLS, TONIGHT, night_href
from nknu.ops.pipeline import BAD, TAIPEI, WARN, tw

RANK = {BAD: 0, WARN: 1}


class Known:
    """The problems the owner marked known: issue key -> (note, when), in ops.db's table known. path None: none kept
    (nothing is known, marking does nothing)."""

    SCHEMA = "CREATE TABLE IF NOT EXISTS known (key TEXT PRIMARY KEY, note TEXT NOT NULL, at TEXT NOT NULL)"

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute(self.SCHEMA)
        return conn

    def load(self) -> dict[str, str]:
        """{issue key: the owner's note}; {} without the file (every problem counts: the safe side)."""
        if self.path is None or not self.path.exists():
            return {}
        conn = self._connect()
        try:
            return {k: n for k, n in conn.execute("SELECT key, note FROM known")}
        finally:
            conn.close()

    def marks(self) -> list[dict]:
        """Every mark, the latest first: [{key, note, at}] ([] without the file)."""
        if self.path is None or not self.path.exists():
            return []
        conn = self._connect()
        try:
            return [{"key": k, "note": n, "at": a} for k, n, a in
                    conn.execute("SELECT key, note, at FROM known ORDER BY at DESC, key")]
        finally:
            conn.close()

    def mark(self, keys: list[str], note: str) -> None:
        if self.path is None:
            return
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            with conn:
                conn.executemany("INSERT OR REPLACE INTO known (key, note, at) VALUES (?, ?, ?)",
                                 [(k, note[:200], at) for k in keys if k])
        finally:
            conn.close()

    def unmark(self, keys: list[str]) -> None:
        if self.path is None or not self.path.exists():
            return
        conn = self._connect()
        try:
            with conn:
                conn.executemany("DELETE FROM known WHERE key = ?", [(k,) for k in keys])
        finally:
            conn.close()


def _known_note(row: dict, known: dict[str, str]) -> str | None:
    """The note when every issue of the row is known (an issue's own key, or its site's key: site:<site>), else None."""
    keys = [it.get("key") or "" for it in row.get("issues") or []]
    if not keys or not known:
        return None
    notes = []
    for key in keys:
        site = ":".join(key.split(":")[:2]) if key.startswith("site:") else ""
        note = known.get(key) if key in known else known.get(site) if site and site in known else None
        if note is None:
            return None
        notes.append(note)
    return next((x for x in notes if x), "")


def machine_rows(meters: list[dict]) -> list[dict]:
    """A drive or a memory past its warning line (machine_meters' tone), as a row."""
    out = []
    for m in meters:
        if m.get("tone") not in (WARN, BAD):
            continue
        out.append({"sev": m["tone"], "kind": "這台電腦", "where": m.get("label") or "", "href": "",
                    "reason": f"用了 {m.get('pct') or 0:.0f}%：{m.get('detail') or ''}".rstrip("："), "tag": "",
                    "issues": [{"key": f"machine:{m.get('label')}"}]})
    return out




# the nightly copy of the raw crawl files to the backup disk (nknu.archive.warcbackup, the night's last step from the
# 11-24 run on): its status file in the crawl volume; a copy behind is a row of 待處理 only, never a mark on the
# calendar or the night (owner 11-23: the backup must not make the night look failed)
WARC_STATUS = Path("reports") / "warc-backup.json"
WARC_WHY = ("run_in_progress", "copy_failed", "backup_folder_empty", "source_empty", "source_missing",
            "backup_folder_missing")
WARC_RUNNING = timedelta(hours=3)  # a copy still said running this long after its start was cut off (its timeout: 7 h)
WARC_DUE = 12  # by noon the night's copy has ended (the chain ends by about 10:00): an older one is behind 【Claude】


def warc_rows(archive: Path | None, now: datetime | None = None) -> list[dict]:
    """The WARC backup as 待處理's row when it is behind: it failed or was not tried (the status's own message and
    code), it has said running for hours, or its last end is not from the latest night. [] while it holds, or
    without its status file (before its first run)."""
    if archive is None:
        return []
    try:
        said = json.loads((Path(archive) / WARC_STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(said, dict):
        return []
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    started, ended = str(said.get("started_at") or ""), str(said.get("ended_at") or "")
    why = str(said.get("why") or "")
    message = tw(str(said.get("message") or ""))

    def when(text: str) -> datetime | None:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
        except ValueError:
            return None

    reason, code = "", why
    if said.get("state") == "running":
        began = when(started)
        if began is None or now - began < WARC_RUNNING:
            return []
        reason, code = f"{started[5:16]} 開始拷，到現在還說在拷（多半是被中途停掉了）", why or "running"
    elif said.get("ok") is False:
        reason = message or "原檔備份沒做完"
    else:
        due = now.date() if now.hour >= WARC_DUE else now.date() - timedelta(days=1)
        last = when(ended)
        if last is not None and last.date() >= due:
            return []
        reason, code = f"上次備份完是 {ended[5:16] or '—'}，這一晚的還沒有", "stale"
    key = f"part:warc_backup:{code or 'behind'}"
    return [{"sev": WARN, "where": "原檔備份", "href": "", "state": "留意", "reason": reason, "code": code,
             "unknown": bool(code) and code not in (*WARC_WHY, "running", "stale"), "when": ended,
             "issues": [{"key": key, "sev": WARN, "what": reason}]}]


def night_rows(t: dict, snap: dict, handle: list[dict], archive: Path | None,
               now: datetime | None = None) -> list[dict]:
    """The night's problems, the one list 待處理 shows (web.py's todo_now) and the nights' record keeps
    (history.Recorder.keep_problems): tonight_page.problems' rows, then the raw files' backup when it is behind
    (warc_rows). Kept alike, the backup's row ages (第 N 天) and a mark of it is seen on 已知 like any other.
    t: nightly.tonight; handle: tonight_page.handle_rows; archive: the crawl volume (Where.archive)."""
    return tonight_page.problems(t, snap, handle) + warc_rows(archive, now)


KIND_OF = (("part:", "每晚流程"), ("run:", "每晚流程"), ("step:", "每晚流程"), ("site:", "網站"),
           ("machine:", "這台電腦"))


def kind_of(row: dict) -> str:
    key = ((row.get("issues") or [{}])[0].get("key")) or ""
    return next((w for p, w in KIND_OF if key.startswith(p)), "每晚流程")


# the two steps of no night (tonight_page.problems keeps '#step-<key>' for them): 查詢服務 (the MCP, the library, the
# model service and the outward service answering; nothing else on the panels shows it) goes to 網站訪問量's card on
# 總覽, 品質檢查 (by hand since 11-23) to /try, where the last check by hand is
STEP_LINKS = {"#step-serve": "/#usage", "#step-check": "/try"}


def link(href: str, night: str = "") -> str:
    """Where a row leads from 總覽: an address as it is; a step of no night to its place (STEP_LINKS); any other
    anchor (a list kept before 11-23: #nights, #step-<key>) to the night view of its night (nightly.night_href), or
    to /tonight, which sends on to the latest night, when the night is not known."""
    if not href.startswith("#"):
        return href
    if href in STEP_LINKS:
        return STEP_LINKS[href]
    return night_href(night) if night else TONIGHT


def collect(parts: dict | None, rows: list[dict], meters: list[dict], known: dict[str, str], night: str = "") -> dict:
    """{'open': rows to handle, the new first; 'known': rows the owner said are known, each with its note; 'night':
    the night compared with ('' when none)}. parts: standing.split of rows (None: no night before kept, every row is
    listed as it is); night: the night the rows are of (tonight()'s default: where a row's anchor leads, link)."""
    listed: list[dict] = []
    if parts is not None:
        listed += [{**x, "tag": "新出現", "kind": kind_of(x)} for x in parts["new"]]
        listed += [{**x, "tag": x.get("age") or "", "kind": kind_of(x)} for x in parts["old"]]
    else:
        listed += [{**x, "tag": "", "kind": kind_of(x)} for x in rows]
    listed += machine_rows(meters)
    open_, kept = [], []
    for x in listed:
        note = _known_note(x, known)
        (kept if note is not None else open_).append({**x, "href": link(x.get("href") or "", night),
                                                     "note": note or ""})
    new_first = {"新出現": 0}
    open_.sort(key=lambda x: (new_first.get(x.get("tag"), 1), RANK.get(x.get("sev"), 2)))
    return {"open": open_, "known": kept, "night": (parts or {}).get("night") or ""}


def covers(mark: str, key: str) -> bool:
    """A mark covers an issue key: the same key, or a site's mark (site:<site>) over its site's keys."""
    return key == mark or (mark.startswith("site:") and mark.count(":") == 1 and key.startswith(mark + ":"))


def name_of(key: str) -> str:
    """A name for a mark whose problem no list has (where it was is not kept): the site, the machine's part, the
    step; the key as it is otherwise."""
    kind, _, rest = key.partition(":")
    if kind == "site":
        return rest.split(":", 1)[0]
    if kind == "machine":
        return rest
    if kind in ("part", "step"):
        step = rest.split(":", 1)[0]
        return dict(CELLS).get(step, step)
    return key


def sightings(store, keys: list[str]) -> dict[str, dict]:
    """Where each mark's problem was last listed (history.Store.problem_sightings: {mark: {key, night, run_day,
    gone_run_day, place, reason, href}}), each mark's own only: a site's mark (site:<site>) is seen in its site's keys,
    any other in its own key alone (covers; part:crawl is not part:crawl:dns, another problem). {} without a store or
    its lists. A mark seen only under another key is left out (said 「沒再出現」, no day): the store's query looks
    under every mark's keys and gives the latest, so an older sighting of the mark's own key is not handed back."""
    read = getattr(store, "problem_sightings", None) if store is not None else None
    if not keys or not callable(read) or not getattr(store, "enabled", True):
        return {}
    try:
        got = read(keys) or {}
    except sqlite3.Error:  # a store from before the lists: nothing seen, never an error on the page
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict) and covers(k, str(v.get("key") or k))}


def known_rows(marks: list[dict], todo: dict | None, seen: dict[str, dict], night: str = "") -> list[dict]:
    """已知's rows, those still listed first, each group the latest mark first: {key, kind, name, href, issue, note,
    at (MM-DD), here (still listed), seen}. seen says where the problem stands: 「今晚還在」 (listed now), 「MM-DD
    起沒再出現」 (the run day of the first kept list without it), 「MM-DD 後沒再出現」 (the latest kept list, of that
    run, still had it; the list now does not) or 「沒再出現」 (no kept list had it). todo: collect's answer (its open
    and known rows are what is listed now); seen: sightings(); night: the night listed now (tonight()'s default),
    where an old list's anchor leads when its own night is not known (link)."""
    listed = [*((todo or {}).get("open") or []), *((todo or {}).get("known") or [])]
    here_rows, gone_rows = [], []
    for m in marks:
        key = m.get("key") or ""
        row = next((x for x in listed if any(covers(key, it.get("key") or "") for it in x.get("issues") or [])), None)
        at = str(m.get("at") or "")[5:10]
        if row is not None:
            here_rows.append({"key": key, "kind": row.get("kind") or kind_of({"issues": [{"key": key}]}),
                              "name": row.get("where") or name_of(key), "href": row.get("href") or "",
                              "issue": row.get("reason") or "", "note": m.get("note") or "", "at": at, "here": True,
                              "seen": "今晚還在"})
            continue
        last = seen.get(key) or {}
        if last.get("gone_run_day"):
            words = f"{str(last['gone_run_day'])[5:10]} 起沒再出現"
        elif last.get("run_day"):
            words = f"{str(last['run_day'])[5:10]} 後沒再出現"
        else:
            words = "沒再出現"
        gone_rows.append({"key": key, "kind": kind_of({"issues": [{"key": key}]}),
                          "name": last.get("place") or name_of(key),
                          "href": link(last.get("href") or "", last.get("night") or night),
                          "issue": last.get("reason") or "", "note": m.get("note") or "", "at": at, "here": False,
                          "seen": words})
    return here_rows + gone_rows
