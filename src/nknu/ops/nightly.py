"""今晚: the nightly chain on the lab PC as one timeline, 爬取 → 導出 → 轉文字 → 建庫 → 換庫, each part exactly one of
還沒開始 / 跑中 (what it is doing, how long already) / 完成 (when, how long it took) / 停了或出錯 (where, who fixes it);
the last seven nights as marks; and the one sentence the home page opens with (owner 11-17: 「定时爬虫定时导出 定时建库
目前都是什么状态？运行中还是哪一步出了问题 还是已经完成 我没办法看到」; spec docs/superpowers/specs/
2026-11-17-panels-structure-design.md 三.2 and 三.4; no push: the owner decided the sentence stays on the page).

Everything is read from what each part of the chain leaves behind, read-only, nothing locked:

  爬取, 導出   the crawler's nightly script's own lines in update.log (its note(): 「2026-11-17 01:00:02 定期更新开始」,
              「… 备份好了」, 「… 停在：run（后面的步骤没做）」, …; research/crawler_lab_2026-11-04/nightly_update.sh), read
              incrementally, and its status file reports/update-status.json (the last night only: its counts, alarms
              and the sites it exported or failed to)
  轉文字, 建庫, 換庫  the nightly ingest (research/pipeline_2026-11-02/lab_pc/nightly_ingest.py): ingest-status.json
              (the run going on or the last one), ingest-history.jsonl (one line a run; of one night the last line
              wins), its log lab_pc/ingest.log (「[ingest 2026-11-17 02:41:57] 步骤 convert」: when each step began),
              build.sh's lab_pc/build.status (the build's step), the lanes' marks, and the library's meta: vec_built_at,
              written just before the new library takes the old one's place inside build step 5, so the checks of steps
              6-8 run after the swap (換庫 shows them)

A night is named by the day twelve hours before a time (nightly_ingest.night_of): the run at 01:00 on 11-17 is
11-16's. The crawler's task starts at 01:00; a night with nothing started by 02:00 is 沒跑 (the lead's brief, 11-17).
An ingest run belongs to a night's chain when it started after that night's crawler run (or, with none, after
midnight): the content session's manual runs in the evening are that night's by name only and are counted aside.
The rules turning these files into the four states, and the waits (QUIET, STUCK, INGEST_WAIT), are 【Claude】 (redesign
2026-11-17, research/dashboard_redesign_2026-11-17/diagnosis.md)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from nknu.ops import pipeline as pl
from nknu.ops.pipeline import BAD, CONTENT, CRAWLER, FIXERS, OK, TAIPEI, WARN, Where, span, tw, when_of

WAIT, RUN, DONE, STOP = "wait", "run", "done", "stop"  # the four states a part of the chain is ever in
CELLS = (("crawl", "爬取"), ("export", "導出"), ("totext", "轉文字"), ("build", "建庫"), ("swap", "換庫"))
NIGHTS = 7
PICKABLE = 60  # ?night= further back than this many days, or after tomorrow, is ignored (review 2026-11-17) 【Claude】
START = dtime(1, 0)  # the Windows task's hour (nightly_update.cmd, scheduled 01:00)
MISSED = dtime(2, 0)  # nothing started by then: 沒跑 (the lead's brief, 11-17)
QUIET = 30 * 60  # update.log silent this long while the latest night has not ended: it may be stuck 【Claude】
#                  (the run's own wait for resting servers is at most 600 s, --end-wait 600)
STUCK = 2 * 3600 + 10 * 60  # silent this long: it is not running. export_sites.sh waits on its lock up to two hours
#                             without a line (flock -w 7200), and a page that called that 出錯 after QUIET stopped
#                             asking (review 2026-11-17; research/dashboard_redesign_2026-11-17/diagnosis.md)
INGEST_WAIT = 30 * 60  # the ingest follows the crawler at once (nightly_update.cmd); none this long after: it did not
#                        start 【Claude】
INGEST_LONG = pl.INGEST_LONG  # a run still marked running after this long was cut off (the container stopped)
STATE_WORDS = {WAIT: "還沒開始", RUN: "跑中", DONE: "完成", STOP: "停了"}
# a night's mark: ok, warn, bad, run (going on), wait (not yet due), missed (沒跑), off (before the chain went live)
NIGHT_WORDS = {OK: "正常", WARN: "停了", BAD: "出錯", "run": "跑中", "wait": "還沒開始", "missed": "沒跑",
               "off": "還沒上線"}
# build.sh's steps (its header), in plain words; the library takes the old one's place at the end of step 5
BUILD_STEPS = {"3": "轉文字記帳", "4": "組裝", "5": "建庫和算向量", "6": "找標準答案", "7": "33 題測試", "8": "真資料檢查",
               "9": "排序常數"}
INGEST_STEPS = {"plan": "比清單", "repoint": "改清單", "convert": "轉文字", "keep": "留舊庫", "build": "建庫",
                "done": "收尾"}
# the crawler script's steps (its stop_at names), and the part of the chain each belongs to
CRAWL_STEPS = ("preflight", "backup", "put-back", "sites", "run", "review")
EXPORT_STEPS = ("export-list", "export", "seen")

# ------------------------------------------------------------------------------------------------ times


def night_of(at: datetime) -> date:
    """The night a time belongs to: the Taipei day twelve hours before it (nightly_ingest.night_of)."""
    return (at.astimezone(TAIPEI) - timedelta(hours=12)).date()


def at_night(night: date, clock: dtime) -> datetime:
    """The morning after a night at that clock time, Taipei (01:00 of night 11-16 is 11-17 01:00)."""
    return datetime.combine(night + timedelta(days=1), clock, TAIPEI)


def label_of(night: date, now: datetime | None = None) -> str:
    """A night named by its evening, 「11-16 夜」, whatever the time now (今晚 and 昨晚, which changed at 06:00, until
    the owner's 「一个页面叫"今晚"你觉得合理吗」, 11-17; the name picked with a cold reader)."""
    return f"{night:%m-%d} 夜"


def starts_at(night: date) -> str:
    """When a night's chain starts, in words: 「11-17 01:00 開跑」 (the floating card of its name)."""
    return f"{at_night(night, START):%m-%d %H:%M} 開跑"


def clock(at: datetime | None) -> str:
    """A time of the night as 01:51; a time on another day with its date."""
    if at is None:
        return ""
    at = at.astimezone(TAIPEI)
    return at.strftime("%H:%M")


def _local(text: str) -> datetime | None:
    """「2026-11-17 01:00:02」 (the scripts' own stamps, Taipei)."""
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------ the crawler's log

_NOTE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\S.*)$")
# the nightly script's note() lines (nightly_update.sh), by how they start, and what each says
MARKS = (("定期更新开始", "start"), ("有 update.pause", "pause"), ("上一晚的定期更新还在跑", "busy"), ("备份好了", "backup"),
         ("放回好了", "putback"), ("run 结束", "ran"), ("变动清单算好了", "reviewed"), ("run 没有好好结束", "badrun"),
         ("停在：", "stop"), ("导出好了", "exported"), ("今晚没有要导出的网站", "exported"), ("定期更新结束", "end"))
_logs: dict[str, dict] = {}  # path -> {'ino', 'offset', 'rest', 'events', 'head'}
_logs_lock = threading.Lock()
KEEP_EVENTS = 4000  # marker lines kept (about 12 a night) 【Claude】


def log_events(path: Path) -> tuple[list[tuple[datetime, str, str]], float | None]:
    """The nightly script's marker lines in update.log as (time, what, the line's words), read on from where the last
    call stopped (the log only grows; a new file or a shorter one is read again whole), and when the log last
    changed (POSIX), None when there is no log. The file is measured inside the lock: a size taken before it could
    be older than what a request in between read, and the log would look shorter than it is (review 2026-11-17)."""
    with _logs_lock:
        try:
            st = path.stat()
            with path.open("rb") as f:
                head = f.read(256)  # a log written anew from its start (rotated in place) reads again whole
        except OSError:
            return [], None
        seen = _logs.get(str(path))
        if (seen is None or seen["ino"] != st.st_ino or st.st_size < seen["offset"]
                or head[:len(seen["head"])] != seen["head"]):
            seen = _logs[str(path)] = {"ino": st.st_ino, "offset": 0, "rest": b"", "events": [], "head": head}
        if st.st_size > seen["offset"]:
            try:
                with path.open("rb") as f:
                    f.seek(seen["offset"])
                    data = f.read(st.st_size - seen["offset"])
            except OSError:
                return list(seen["events"]), st.st_mtime
            seen["offset"] += len(data)
            data = seen["rest"] + data
            lines = data.split(b"\n")
            seen["rest"] = lines.pop()  # a line still being written
            for raw in lines:
                m = _NOTE.match(raw.decode("utf-8", errors="replace").rstrip("\r"))
                if not m:
                    continue
                what = next((k for mark, k in MARKS if m.group(2).startswith(mark)), None)
                at = _local(m.group(1))
                if what and at:
                    seen["events"].append((at, what, m.group(2)))
            del seen["events"][:-KEEP_EVENTS]
        return list(seen["events"]), st.st_mtime


def crawler_runs(events: list[tuple[datetime, str, str]]) -> list[dict]:
    """The nights of the crawler's script, in order: {'start', 'kind' (run, pause, busy), 'marks': {what: (time,
    words)}}; a pause or a busy line is a night of its own that did nothing."""
    runs: list[dict] = []
    for at, what, words in events:
        if what in ("start", "pause", "busy"):
            runs.append({"start": at, "kind": "run" if what == "start" else what, "marks": {what: (at, words)}})
        elif runs and runs[-1]["kind"] == "run":
            runs[-1]["marks"].setdefault(what, (at, words))
    for r in runs:
        r["night"] = night_of(r["start"])
    return runs


def stop_step(words: str) -> tuple[str, bool]:
    """「停在：run（有人停了它）（后面的步骤没做）」 as (run, someone stopped it)."""
    step = words.removeprefix("停在：").removesuffix("（后面的步骤没做）")
    return step.split("（", 1)[0].strip(), "有人停了它" in step


def step_words(step: str) -> str:
    return next((w for k, w in pl.NIGHT_STEP_WORDS if step.startswith(k)), tw(step))


# ------------------------------------------------------------------------------------------------ the ingest

_SAID = re.compile(r"^\[ingest (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")


def ingest_steps(path: Path) -> list[tuple[datetime, str]]:
    """When each step of the ingest began: its log's 「[ingest …] 步骤 X」 lines as (time, X), and its ends
    (「结束：swapped」) as (time, 'end')."""
    out: list[tuple[datetime, str]] = []
    for line in pl.tail(path, 262144):
        m = _SAID.match(line.strip())
        if not m or (at := _local(m.group(1))) is None:
            continue
        said = m.group(2)
        if said.startswith("步骤 "):
            out.append((at, said[3:].strip()))
        elif said.startswith("结束："):
            out.append((at, "end"))
    return out


def ingest_runs(pipe: Path) -> list[dict]:
    """The ingest's runs, in the order they started: the history's lines, and the status file's run when it is still
    going (the history gets its line only at the end). Each with 'night' (its own field, else night_of)."""
    runs = [r for r in pl.read_lines(pipe / "ingest-history.jsonl") if when_of(r.get("started_at"))]
    status = pl.read_json(pipe / "ingest-status.json")
    if (isinstance(status, dict) and status.get("outcome") == "running" and when_of(status.get("started_at"))
            and not any(r.get("started_at") == status.get("started_at") for r in runs)):
        runs.append(status)
    for r in runs:
        try:
            r["_night"] = date.fromisoformat(str(r.get("night") or ""))
        except ValueError:
            r["_night"] = night_of(when_of(r["started_at"]))
        r["_start"] = when_of(r["started_at"])
        r["_end"] = when_of(r.get("finished_at"))
    runs.sort(key=lambda r: r["_start"])
    return runs


def build_status(kit: Path) -> dict:
    """build.status as pipeline reads it, with its time read."""
    got = pl.build_status(kit)
    got["_at"] = _local(str(got.get("at") or ""))
    got["_since"] = _local(str(got.get("since") or ""))
    return got


def build_step_words(step: str | None) -> str:
    step = str(step or "")
    return f"第 {step} 步（{BUILD_STEPS[step]}）" if step in BUILD_STEPS else (f"第 {step} 步" if step else "")


def known_error(error: str | None) -> str | None:
    """The plain words of an error code the ingest writes (error_words' dictionary), None for one it does not know."""
    error = str(error or "")
    words = error_words(error)
    return None if not error or words == tw(error)[:120] else words


# what the ingest's outcomes are, in plain words (nightly_ingest.py's outcome field): the one small dictionary; an
# outcome not in it is shown as it is with 沒見過 (spec 2026-11-17-panels-v3-questions.md 八.6) 【Claude】
OUTCOME_WORDS = {"running": "跑中", "swapped": "換上了", "nothing_new": "沒有新資料", "held": "把關沒過，新庫沒換上",
                 "paused": "暫停（有 ingest.pause）", "deferred": "兩小時內沒轉完", "failed": "失敗", "busy": "前一次還在跑",
                 "rolled_back": "退回前一份庫"}


def run_reason(run: dict, dep: dict | None = None) -> dict:
    """What an ingest run's own line says of it: {'reason' (plain words of its outcome, error and held reasons, from the
    dictionaries), 'code' (the raw codes, as written), 'unknown' (a code no dictionary knows)}."""
    outcome, error = str(run.get("outcome") or ""), str(run.get("error") or "")
    codes, words, unknown = [outcome] if outcome else [], [], False
    if outcome in OUTCOME_WORDS:
        words.append(OUTCOME_WORDS[outcome])
    else:
        unknown = True
    if error:
        codes.append(error)
        known = known_error(error)
        if known is None:
            unknown = True
        elif known not in words:  # deferred and its lanes_timeout say the same thing: once
            words.append(known)
    held = run.get("held") if isinstance(run.get("held"), dict) else {}
    for why in held.get("reasons") or ([held["reason"]] if held.get("reason") else []):
        codes.append(str(why))
        if why in HELD_WORDS:
            words.append(HELD_WORDS[why])
        else:
            unknown = True
    if dep and dep.get("unexplained"):
        words.append(f"說不出原因地離開 {dep['unexplained']:,} 份（{sites_words(dep['by_site'])}）")
    if waiting := waiting_words(run):
        words.append(waiting)
    words.extend(x for x in set_back_words(run) if x)
    return {"reason": "；".join(words), "code": " · ".join(codes), "unknown": unknown}


def error_words(error: str | None) -> str:
    """The ingest's error codes in plain words (nightly_ingest.py: lanes_timeout, lane_<X>_exit_<n>, build_exit_<n>,
    checks_exit_<n>, an exception's 'Type: words'; since 11-17 disk_low_<n>GB, build_timeout, checks_timeout: the
    content session's note research/pipeline_2026-11-02/guard_1117/note_for_panels_1117.md)."""
    error = str(error or "")
    if not error:
        return ""
    if error == "lanes_timeout":
        return "兩小時內沒轉完"
    if m := re.fullmatch(r"disk_low_(\d+(?:\.\d+)?)GB", error):
        return f"C 盤只剩 {m.group(1)} GB，沒建庫"
    if error == "build_timeout":
        return "建庫超過 3 小時停掉"
    if error == "checks_timeout":
        return "換庫後的檢查超時（庫已換上）"
    if m := re.fullmatch(r"lane_([A-Z])_exit_(-?\d+)", error):
        what = dict(pl.LANES).get(m.group(1), f"第 {m.group(1)} 條線")
        return f"{what}出錯（退出碼 {m.group(2)}）"
    if m := re.fullmatch(r"(build|checks)_exit_(-?\d+)", error):
        return ("建庫程式" if m.group(1) == "build" else "換庫後的檢查") + f"出錯（退出碼 {m.group(2)}）"
    if error.startswith("Stopped"):
        return "被停下了（容器被關掉）"
    return tw(error)[:120]


# ------------------------------------------------------------------------------------------------ the build's check

# why a swap guard held the new library (nightly_ingest held.reasons; note_for_panels_1117.md) 【Claude's words】
HELD_WORDS = {"site_collapsed": "某站一次少了一半以上", "unexplained_departures": "說不出原因的離開太多",
              "school_wave": "某站一次被標成學校刪掉太多"}
SITES_SHOWN = 3  # sites a sentence names before 等 【Claude】


def departures_of(run: dict | None, swap: dict | None = None) -> dict | None:
    """What the build's own check found of the documents that left (the ingest line's departures and
    unexplained_left; else aca.swap.json's, when it was written during that run): {'unexplained', 'left', 'by_site'
    (site → documents, most first), 'items' (≤ 50: site, title, url, why, detail), 'words' (why → plain words,
    Traditional), 'waves'}; None when nothing was compared."""
    if not run:
        return None
    got = run.get("departures")
    if not (isinstance(got, dict) and got.get("checked")) and swap and swap.get("_run") is run:
        got = swap.get("departures")
    if not (isinstance(got, dict) and got.get("checked")):
        return None
    n = run.get("unexplained_left")
    n = got.get("unexplained_total") if not isinstance(n, int) else n
    by_site = {str(k): int(v) for k, v in (got.get("by_site") or {}).items() if k and isinstance(v, int)}
    return {"unexplained": int(n or 0), "left": got.get("left"),
            "by_site": dict(sorted(by_site.items(), key=lambda kv: -kv[1])),
            "items": [x for x in (got.get("items") or []) if isinstance(x, dict)][:50],
            "words": {str(k): tw(str(v)) for k, v in (got.get("words") or {}).items()},
            "waves": got.get("waves") if isinstance(got.get("waves"), dict) else {}}


def sites_words(by_site: dict, shown: int = SITES_SHOWN) -> str:
    """「a.example 2、b.example 1 等」: the sites with the most first."""
    pairs = list(by_site.items())
    return "、".join(f"{k} {v:,}" for k, v in pairs[:shown]) + (" 等" if len(pairs) > shown else "")


def waiting_words(run: dict) -> str:
    """The sites whose files were not all converted when the lanes' time was up, in words ('' for none). Since 11-19
    the ingest leaves such a site on the export the official library used for that night and builds the rest
    (ingest-status.json, ingest-history.jsonl: sites_waiting [{site, files}]; the next night converts on)."""
    sites = [w for w in run.get("sites_waiting") or [] if isinstance(w, dict) and w.get("site")]
    if not sites:
        return ""
    files = sum(w["files"] for w in sites if isinstance(w.get("files"), int))
    named = "、".join(str(w["site"]) for w in sites[:SITES_SHOWN]) + (" 等" if len(sites) > SITES_SHOWN else "")
    left = f"，還有 {files:,} 個檔" if files else ""
    return f"{len(sites)} 個網站到點還沒轉完（{named}{left}），這一晚沒換它們的新資料，下一晚接著轉"


def set_back_words(run: dict) -> tuple[str, str]:
    """(the sites the swap guard set back this night, the sites whose newest export is one it set back before), each
    in words or ''. Since 11-19 a library the guard stops is rebuilt without the sites it stopped for: those stay on
    the export the official library uses (sites_set_back [{site, why, count, folder}], why one of HELD_WORDS' keys),
    and a site whose newest export is still the one set back is not tried again until the crawler exports it anew
    (sites_refused [site]); both are written only when there are such sites."""
    back = [b for b in run.get("sites_set_back") or [] if isinstance(b, dict) and b.get("site")]
    refused = [str(x) for x in run.get("sites_refused") or [] if x and isinstance(x, str)]

    def more(items: list) -> str:
        return " 等" if len(items) > SITES_SHOWN else ""

    said_back = said_refused = ""
    if back:
        def one(b: dict) -> str:
            why = HELD_WORDS.get(str(b.get("why") or ""), tw(str(b.get("why") or "")))
            count = f"{b['count']:,} 份" if isinstance(b.get("count"), int) else ""
            inside = "，".join(x for x in (why.removeprefix("某站"), count) if x)
            return f"{b['site']}" + (f"（{inside}）" if inside else "")
        said_back = (f"把關退回 {len(back)} 個網站：{'、'.join(one(b) for b in back[:SITES_SHOWN])}{more(back)}，"
                     "這一晚沒換它們的新資料")
    if refused:
        said_refused = (f"{len(refused)} 個網站最新的導出還是上次被退回的那一份，這一晚沒再試"
                        f"（{'、'.join(refused[:SITES_SHOWN])}{more(refused)}），等爬蟲給新的導出")
    return said_back, said_refused


def held_words(run: dict, dep: dict | None) -> tuple[str, str]:
    """(the swap cell's words, its floating card) for a run the swap guard held, by held.reasons; a line from before
    11-17 (no reasons) keeps the words it had."""
    held = run.get("held") if isinstance(run.get("held"), dict) else {}
    reasons = [str(r) for r in held.get("reasons") or ([held["reason"]] if held.get("reason") else [])]
    detail = [h for h in held.get("detail") or [] if isinstance(h, dict)]
    tip = [f"{h.get('site')}：{h.get('before')} → {h.get('after')} 份" for h in detail[:12]]
    if not reasons:
        return "把關沒過，新庫沒換上（有網站的文件數掉到一半以下）", "\n".join(tip) or "新庫留在 aca.held.db"
    said = []
    for why in reasons:
        words = HELD_WORDS.get(why, tw(why))
        if why == "unexplained_departures" and dep:
            words += f"（{dep['unexplained']:,} 份）"
            tip.append("說不出原因的：" + sites_words(dep["by_site"], 8))
        elif why == "school_wave" and dep and dep["waves"]:
            words += f"（{'、'.join(list(dep['waves'])[:SITES_SHOWN])}）"
            tip.append("被標成學校刪掉的：" + "、".join(f"{k} {v[0]:,} / {v[1]:,} 份" for k, v in dep["waves"].items()
                                                if isinstance(v, (list, tuple)) and len(v) == 2))
        said.append(words)
    tip.append(f"新庫留在 {held.get('db') or 'aca.held.db'}")
    return "把關沒過，新庫沒換上：" + "、".join(said), "\n".join(tip)


# ------------------------------------------------------------------------------------------------ one night


def _cell(key: str, state: str, say: str = "", *, word: str = "", since: datetime | None = None,
          until: datetime | None = None, fix: str = "", sev: str = "", tip: str = "", phrase: str = "",
          now: datetime | None = None, reason: str = "", code: str = "", unknown: bool = False) -> dict:
    """One part of the chain as the page shows it: its state (WAIT, RUN, DONE, STOP) and the word for it, a short say
    (what it is doing, where it stopped, or a note), when (finished time and duration, or how long already), who
    fixes it (FIXERS' key) and how bad (sev: OK, WARN, BAD) when it stopped, a longer tip, and phrase: the words the
    first sentence uses for it while it runs; reason: what the source itself says of it (a status file's reason or
    error, a log line, as written, Traditional), code: the raw code, unknown: a code no dictionary here knows (spec
    2026-11-17-panels-v3-questions.md 八.1, 八.6: shown as it is, never as normal)."""
    name = dict(CELLS)[key]
    took = span((until - since).total_seconds()) if since and until else ""
    if state == DONE:
        when = " · ".join(x for x in (clock(until), took) if x)
    elif state == RUN:
        when = f"已經 {span((now - since).total_seconds())}" if since and now else ""
    elif state == STOP:
        when = clock(until or since)
    else:
        when = ""
    return {"key": key, "name": name, "state": state, "word": word or STATE_WORDS[state], "say": say, "when": when,
            "since": since.isoformat(timespec="seconds") if since else "", "since_ts": since.timestamp() if since else None,
            "until": until.isoformat(timespec="seconds") if until else "", "took": took, "fix": fix,
            "fixer": FIXERS.get(fix, "") if state == STOP or sev in (WARN, BAD) else "",
            "sev": sev or (OK if state == DONE else ""),
            "tip": tip, "phrase": phrase or name, "reason": reason, "code": code, "unknown": unknown}


class Reading:
    """What one look at the night needs, read once (the log, the history, the status files)."""

    def __init__(self, where: Where, now: datetime) -> None:
        self.where, self.now = where, now
        archive = where.archive
        self.events, self.log_moved = log_events(archive / "update.log") if archive else ([], None)
        self.log_seen = archive is not None and self.log_moved is not None
        self.runs = crawler_runs(self.events)
        status = pl.read_json(archive / "reports" / "update-status.json") if archive else None
        self.status = status if isinstance(status, dict) else {}
        self.status_night = night_of(at) if (at := when_of(self.status.get("night_started"))) else None
        pipe = where.pipe
        self.ingests = ingest_runs(pipe)
        self.said = ingest_steps(pipe / "lab_pc" / "ingest.log")
        self.build = build_status(pipe / "lab_pc")
        self.lanes = pl.lane_states(pipe / "lab_pc", now)
        self.paused = (pipe / "ingest.pause").exists()
        self.swap = _swap_file(where.library.with_name(f"{where.library.stem}.swap.json"), self.ingests)
        lib = _meta(where.library)
        self.swapped_at = when_of(lib.get("vec_built_at") or lib.get("built_at"))
        crawls = [r["night"] for r in self.runs] + ([self.status_night] if self.status_night else [])
        self.first_crawl = min(crawls, default=None)  # the crawler's nightly went live (before it: 還沒上線)
        self.first_ingest = min((r["_night"] for r in self.ingests), default=None)

    def crawler_run(self, night: date) -> dict | None:
        found = [r for r in self.runs if r["night"] == night]
        real = [r for r in found if r["kind"] == "run"]
        return (real or found or [None])[-1]

    def quiet(self, run: dict) -> float | None:
        """How long update.log has been silent, for the crawler's latest night only (None for an older one: the log
        moving tonight says nothing of a night before that never wrote its end; review 2026-11-17). A busy line
        after a run says that run still goes on, so it stays the latest 【Claude】."""
        latest = next((x for x in reversed(self.runs) if x["kind"] != "busy"), None)
        if self.log_moved is None or run is not latest:
            return None
        return max(0.0, self.now.timestamp() - self.log_moved)

    def moved_at(self) -> datetime | None:
        return datetime.fromtimestamp(self.log_moved, TAIPEI) if self.log_moved is not None else None

    def archive_missing(self) -> bool:
        return self.where.archive is None or not self.where.archive.exists()


def _swap_file(path: Path, runs: list[dict]) -> dict | None:
    """aca.swap.json (the build writes it beside the library each time), tied to the ingest run it was written
    during (_run; None when no run's time holds it: a build by hand)."""
    got = pl.read_json(path)
    if not isinstance(got, dict):
        return None
    try:
        at = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI)
    except OSError:
        return None
    got["_run"] = next((r for r in reversed(runs) if r["_start"] - timedelta(seconds=5) <= at
                        <= (r["_end"] or at) + timedelta(seconds=120)), None)
    return got


def _meta(db: Path) -> dict:
    try:
        conn = pl.open_ro(db)
    except Exception:  # noqa: BLE001  no library yet, or one being swapped: no swap moment to show
        return {}
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('built_at', 'vec_built_at')")}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        conn.close()


def crawl_cells(r: Reading, night: date) -> tuple[dict, dict, datetime | None]:
    """爬取 and 導出 of one night, and when the crawler's part ended (None: not ended)."""
    now = r.now
    run = r.crawler_run(night)
    status = r.status if r.status_night == night else {}
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    count_tip = "、".join(f"{pl.NIGHT_COUNTS.get(k, k)} {v:,}" for k, v in counts.items() if isinstance(v, int) and v)
    if run is None and status and not r.log_seen:
        run = _run_from_status(status)  # the log is not readable here: the status file alone, for its own night
    if run is None:
        if r.archive_missing():
            return (_cell("crawl", WAIT, "檢視台沒有掛上爬蟲的存檔", word="看不到"),
                    _cell("export", WAIT, word="看不到"), None)
        if r.first_crawl is None or night < r.first_crawl:
            return (_cell("crawl", WAIT, word="還沒上線"), _cell("export", WAIT, word="還沒上線"), None)
        if now >= at_night(night, MISSED):
            return (_cell("crawl", STOP, "01:00 該開始，沒有動靜", word="沒跑", fix=CRAWLER, sev=BAD,
                          tip="電腦關著、Docker 沒開、排程停了，都會這樣；排程是 Windows 工作排程器的 nknu-nightly-update"),
                    _cell("export", WAIT, word="沒跑到"), None)
        return (_cell("crawl", WAIT, "01:00 開始" if now < at_night(night, START) else "該開始了"),
                _cell("export", WAIT), None)
    marks, start = run["marks"], run["start"]
    if run["kind"] in ("pause", "busy"):
        say = "暫停中（有 update.pause），這晚沒更新" if run["kind"] == "pause" else "沒開（前一晚的還在跑）"
        line = tw(str(marks.get(run["kind"], (None, ""))[1]))
        return (_cell("crawl", STOP, say, since=start, until=start, fix=CRAWLER, sev=WARN, reason=line,
                      code=run["kind"]),
                _cell("export", WAIT, word="沒跑到"), start)
    stopped = marks.get("stop")
    bad_run = marks.get("badrun")
    if bad_run and not stopped:
        stopped = (bad_run[0], "停在：run")
    quiet = r.quiet(run)
    alive = quiet is not None and quiet < QUIET
    stuck = quiet is not None and QUIET <= quiet < STUCK  # maybe waiting (the export's lock): said, still asked for
    moved = r.moved_at()
    stuck_say = f"{clock(moved)} 以後沒有動靜" if moved else ""
    stuck_until = f"；{clock(moved + timedelta(seconds=STUCK))} 還沒動靜就算沒有收尾" if moved else ""
    reviewed = marks.get("reviewed", (None,))[0]
    ended = marks.get("end", (None,))[0]
    # 爬取: from the script's start to its change list
    if stopped and stop_step(stopped[1])[0] not in EXPORT_STEPS:
        step, by_hand = stop_step(stopped[1])
        say = f"停在「{step_words(step)}」" + ("（有人停了它）" if by_hand else "")
        crawl = _cell("crawl", STOP, say, word="出錯", since=start, until=stopped[0], fix=CRAWLER, sev=BAD,
                      tip=str(status.get("note") or "") or "細節在存檔的 update.log", reason=tw(stopped[1]), code=step)
        return crawl, _cell("export", WAIT, word="沒跑到"), stopped[0]
    if reviewed:
        crawl = _cell("crawl", DONE, since=start, until=reviewed, tip=count_tip)
    elif alive:
        doing = ("抓網頁" if "putback" in marks and "ran" not in marks else "算有哪些變動" if "ran" in marks
                 else "排今晚要看的網址" if "backup" in marks else "備份帳本")
        crawl = _cell("crawl", RUN, doing, since=start, now=now, phrase=f"爬取（{doing}）")
        if (now - start).total_seconds() > pl.CRAWL_LONG:
            crawl["say"] += "，比平常久很多"
        return crawl, _cell("export", WAIT), None
    elif stuck:
        crawl = _cell("crawl", RUN, stuck_say, word="可能卡住", since=start, now=now, fix=CRAWLER, sev=WARN,
                      tip="紀錄半小時以上沒動：可能還在做久的一步，也可能卡住了" + stuck_until,
                      reason=f"update.log {stuck_say}")
        return crawl, _cell("export", WAIT), None
    else:
        last = max(at for at, _w in marks.values())
        return (_cell("crawl", STOP, f"沒有收尾（紀錄停在 {clock(last)}）", word="出錯", since=start, until=last,
                      fix=CRAWLER, sev=BAD, tip="爬蟲的每晚更新沒寫完就不動了：可能被關掉，或卡住了",
                      reason=last_line(marks)),
                _cell("export", WAIT, word="沒跑到"), last)
    # 導出: from the change list to the script's end
    export_info = status.get("export") if isinstance(status.get("export"), dict) else {}
    exported = len(export_info.get("exported") or [])
    tip = "；".join(x for x in (f"導了 {exported} 站" if export_info else "",
                               f"沒變的 {len(export_info.get('unchanged') or [])} 站" if export_info else "",
                               f"還欠 {len(export_info.get('owed') or [])} 站" if export_info.get("owed") else "") if x)
    if stopped:
        step, by_hand = stop_step(stopped[1])
        export = _cell("export", STOP, f"停在「{step_words(step)}」" + ("（有人停了它）" if by_hand else ""), word="出錯",
                       since=reviewed, until=stopped[0], fix=CRAWLER, sev=BAD, tip="細節在存檔的 update.log",
                       reason=tw(stopped[1]), code=step)
        return crawl, export, stopped[0]
    if ended:
        # held by the crawler's copy guard (a warning for it) or by export-hold.txt (人工暫緩: news, never a warning;
        # said after the rest of what the cell says, never in the first sentence); both are in failed too (11-17)
        held, paused = pl.export_held(export_info), pl.export_paused(export_info)
        failed = [str(x) for x in export_info.get("failed") or [] if str(x) not in held and str(x) not in paused]
        alarms = status.get("alarms") if isinstance(status.get("alarms"), dict) else {}
        held_tip = "\n".join(x for x in (pl.held_words(held), pl.held_words(paused, paused=True)) if x)
        aside = f"；人工暫緩 {len(paused)} 站" if paused else ""
        if failed:
            export = _cell("export", STOP, f"有 {len(failed)} 站失敗（下一晚再導）", word="出錯", since=reviewed,
                           until=ended, fix=CRAWLER, sev=BAD,
                           tip="\n".join(x for x in ("、".join(failed[:12]), held_tip) if x),
                           reason="失敗的網站：" + "、".join(failed[:6]) + (" 等" if len(failed) > 6 else ""))
        elif held:  # the crawler held the new export back on purpose: the old one stays in use (11-17)
            export = _cell("export", DONE, f"攔下 {len(held)} 站{aside}", since=reviewed, until=ended, fix=CRAWLER,
                           sev=WARN, tip="\n".join(x for x in (held_tip, tip) if x),
                           reason="；".join(f"{k}：{tw(v)}" for k, v in list(held.items())[:6]))
        elif alarms:
            export = _cell("export", STOP, f"有 {len(alarms)} 站警報，這晚沒導出它們", word="出錯", since=reviewed,
                           until=ended, fix=CRAWLER, sev=BAD,
                           tip="\n".join(f"{k}：{tw(str(v))}" for k, v in list(alarms.items())[:12]),
                           reason="；".join(f"{k}：{tw(str(v))}" for k, v in list(alarms.items())[:6]))
        else:
            n = re.search(r"导出 (\d+) 个网站", marks["end"][1])
            say = (f"導了 {n.group(1)} 站" if n else "") + aside
            export = _cell("export", DONE, say.removeprefix("；"), since=reviewed, until=ended,
                           tip="\n".join(x for x in (held_tip, tip) if x))
        return crawl, export, ended
    if alive:
        doing = "記下各站看過的時間" if "exported" in marks else "導出"
        return crawl, _cell("export", RUN, doing, since=reviewed, now=now, phrase=f"導出（{doing}）"), None
    if stuck:
        return crawl, _cell("export", RUN, stuck_say, word="可能卡住", since=reviewed, now=now, fix=CRAWLER, sev=WARN,
                            tip="導出會排隊等前一次導出跑完，最多等兩小時，等的時候不寫紀錄" + stuck_until,
                            reason=f"update.log {stuck_say}"), None
    last = max(at for at, _w in marks.values())
    return crawl, _cell("export", STOP, f"沒有收尾（紀錄停在 {clock(last)}）", word="出錯", since=reviewed, until=last,
                        fix=CRAWLER, sev=BAD, reason=last_line(marks)), last


def last_line(marks: dict) -> str:
    """The crawler script's last line of a night that wrote no end, as written (Traditional), with its time."""
    at, words = max(marks.values(), key=lambda m: m[0])
    return f"最後一行（{clock(at)}）：{tw(words)}" if words else f"最後一行在 {clock(at)}"


def _run_from_status(status: dict) -> dict | None:
    """A crawler night from its status file alone (update.log not readable): its start, its change list, its export
    and where it stopped."""
    start = when_of(status.get("night_started"))
    if start is None:
        return None
    marks: dict = {"start": (start, "")}
    if (rev := when_of(status.get("reviewed"))) is not None:
        marks["reviewed"] = (rev, "")
    export = status.get("export")
    if isinstance(export, dict) and (at := when_of(export.get("at"))) is not None:
        marks["end"] = (at, f"导出 {len(export.get('exported') or [])} 个网站")
    if status.get("failed_step"):
        marks["stop"] = (when_of(status.get("failed_at")) or start, f"停在：{status['failed_step']}")
    return {"start": start, "kind": "run", "marks": marks, "night": night_of(start)}


def _chain(r: Reading, night: date, crawl_start: datetime | None) -> list[dict]:
    """The ingest runs of a night's chain: named for it, started after its crawler run began (or after midnight
    without one) and before the next night's crawler run, rollbacks aside."""
    floor = crawl_start or at_night(night, dtime(0, 0))
    after = r.crawler_run(night + timedelta(days=1))
    ceiling = after["start"] if after else None
    return [x for x in r.ingests if x["_night"] == night and x["_start"] >= floor - timedelta(seconds=1)
            and x.get("outcome") != "rolled_back" and (ceiling is None or x["_start"] < ceiling)]


def chain_ingest(r: Reading, night: date, crawl_start: datetime | None
                 ) -> tuple[dict | None, dict | None, int, dict | None]:
    """The ingest run of a night's chain, a rollback line after it (None: none), how many other runs that night had
    (said as 另外跑過 N 次), and the scheduled run when it is not the one shown (None: it is).

    The night's runs are those named for it that started after its crawler run began (or after midnight without
    one) and before the next night's crawler run. The scheduled run is the FIRST of them that is not busy:
    nightly_update.cmd calls the ingest once, so a later one is someone's by hand, and a busy line is one that found
    another still running (a run by hand while the night's converted writes it, and the night's own line comes later
    in the history); only when the night had nothing but busy lines is busy what it shows. The last run used to win,
    so a busy line or a run the morning after (named this night: twelve hours back) hid the night's swap (review
    2026-11-17). But when any of the night's runs swapped the library, that run is the night's (the last that did: the
    library the night left in use) and the scheduled one, stopped or failed before it, is said beside it: the lab's
    11-16, scheduled run paused at 02:47 and one by hand swapped at 04:09, read as 「停了」 while the library had
    been updated (the lead, 11-17). A rollback is looked for after the chosen run, up to the next run that swapped a
    library (one in the afternoon is named the next night and still undoes this one's swap) 【Claude】."""
    runs = [x for x in r.ingests if x["_night"] == night]
    chain = _chain(r, night, crawl_start)
    scheduled = next((x for x in chain if x.get("outcome") != "busy"), chain[0] if chain else None)
    swaps = [x for x in chain if x.get("outcome") == "swapped"]
    run = swaps[-1] if swaps else scheduled
    first = scheduled if scheduled is not run else None
    rolled = None
    if run is not None:
        for x in r.ingests:
            if x is run or x["_start"] < run["_start"]:
                continue
            if x.get("outcome") == "rolled_back":
                rolled = x
                break
            if x.get("outcome") == "swapped":
                break
    others = sum(1 for x in runs if x is not run and x is not rolled and x is not first)
    return run, rolled, others, first


def ingest_cells(r: Reading, night: date, crawl_end: datetime | None, crawl_cells: tuple[dict, dict],
                 crawl_start: datetime | None) -> list[dict]:
    """轉文字, 建庫, 換庫 of one night, each part that is not normal carrying its run's own reason (run_reason)."""
    cells = _ingest_cells(r, night, crawl_end, crawl_cells, crawl_start)
    run, _rolled, _others, _first = chain_ingest(r, night, crawl_start)
    if run is not None:
        said = run_reason(run, departures_of(run, r.swap))
        for c in cells:
            if (c["state"] == STOP or c["sev"] in (WARN, BAD)) and not c["reason"]:
                c.update(said)
    return cells


def _ingest_cells(r: Reading, night: date, crawl_end: datetime | None, crawl_cells: tuple[dict, dict],
                  crawl_start: datetime | None) -> list[dict]:
    now = r.now
    run, rolled, _others, _first = chain_ingest(r, night, crawl_start)
    extra = ""
    if run is None:
        if r.first_ingest is None or night < r.first_ingest:
            return [_cell(k, WAIT, word="還沒上線") for k in ("totext", "build", "swap")]
        crawl, _export = crawl_cells
        if crawl_end is None:  # the crawler still runs, has not begun, or never did (沒跑)
            word = "沒跑到" if crawl["state"] == STOP else ""
            return [_cell(k, WAIT, word=word, tip=extra) for k in ("totext", "build", "swap")]
        if (now - crawl_end).total_seconds() > INGEST_WAIT:
            return [_cell("totext", STOP, "沒有開始（爬蟲跑完了，進庫沒接上）", word="沒跑", since=crawl_end, fix=CONTENT, sev=BAD,
                          tip="nightly_update.cmd 跑完爬蟲會接著叫 nightly_ingest.cmd；細節在 "
                              "C:\\nknu-transfer\\build\\nightly-ingest-task.log"),
                    _cell("build", WAIT, word="沒跑到"), _cell("swap", WAIT, word="沒跑到")]
        return [_cell(k, WAIT, tip=extra) for k in ("totext", "build", "swap")]
    start, end = run["_start"], run["_end"]
    outcome, step, error = str(run.get("outcome") or ""), str(run.get("step") or ""), run.get("error")
    running = outcome == "running"
    if running and (now - start).total_seconds() > INGEST_LONG:
        running, outcome, error = False, "failed", f"跑了 {span((now - start).total_seconds())} 還沒完（可能被中斷）"
    # when each step began, from the log, within this run
    upto = end or now
    began: dict[str, datetime] = {}
    for at, what in r.said:
        if start - timedelta(seconds=2) <= at <= upto + timedelta(seconds=2) and what != "end":
            began.setdefault(what, at)
    keep_at = began.get("keep") or began.get("build")
    done_at = began.get("done")
    words = error_words(error) if outcome == "failed" else ""
    tip_run = "；".join(x for x in (f"{clock(start)} 開始", extra,
                                   f"有新資料的網站 {len(run.get('sites_new') or [])} 個" if run.get("sites_new") else "")
                        if x)
    waiting = waiting_words(run)  # sites left on their old export this night: said on 轉文字, a note to look at
    # 轉文字: plan, repoint and the lanes
    early = step in ("plan", "repoint", "convert")
    if outcome == "busy":
        return [_cell("totext", STOP, "沒開（前一晚的進庫還在跑）", since=start, until=end or start, fix=CONTENT,
                      sev=WARN), _cell("build", WAIT, word="沒跑到"), _cell("swap", WAIT, word="沒跑到")]
    if running and early:
        doing = INGEST_STEPS.get(step, step)
        lanes = [ln["what"] for ln in r.lanes if ln["state"] == "running"]
        say = doing + (f"（{'、'.join(lanes)}還在轉）" if step == "convert" and lanes else "")
        return [_cell("totext", RUN, say, since=start, now=now, tip=tip_run, phrase=f"轉文字（{doing}）"),
                _cell("build", WAIT), _cell("swap", WAIT)]
    if outcome == "nothing_new":
        quiet = [_cell("totext", DONE, "沒有新資料，不用轉", since=start, until=end, tip=tip_run)]
        return quiet + [_cell("build", DONE, "不用重建", until=end), _cell("swap", DONE, "庫沒換", until=end)]
    if outcome == "deferred":
        say = f"{waiting}；沒有別的新資料，這晚不建庫" if waiting else "兩小時內沒轉完，這晚不建庫（下一晚接著轉）"
        return [_cell("totext", STOP, say, since=start, until=end, fix=CONTENT,
                      sev=WARN, tip=tip_run), _cell("build", WAIT, word="沒跑到"), _cell("swap", WAIT, word="沒跑到")]
    if outcome == "failed" and str(error or "").startswith("disk_low_"):  # checked after the lanes, before the build
        disk = f"C 盤剩 {run['disk_free_gb']} GB" if run.get("disk_free_gb") is not None else ""
        return [_cell("totext", DONE, since=start, until=end, tip=tip_run),
                _cell("build", STOP, words, word="出錯", since=end, until=end, fix=CONTENT, sev=BAD,
                      tip="；".join(x for x in (disk, "建庫要 C 盤至少 15 GB") if x)),
                _cell("swap", WAIT, word="沒跑到")]
    if outcome == "failed" and (early or not keep_at):
        say = (words or "出錯了") if step == "convert" else (
            f"在「{INGEST_STEPS.get(step, step)}」出錯" + (f"：{words}" if words else ""))
        return [_cell("totext", STOP, say, word="出錯", since=start, until=end, fix=CONTENT, sev=BAD, tip=tip_run),
                _cell("build", WAIT, word="沒跑到"), _cell("swap", WAIT, word="沒跑到")]
    totext = _cell("totext", DONE, waiting, since=start, until=keep_at or end, tip=tip_run,
                   sev=WARN if waiting else "", fix=CONTENT if waiting else "")
    if outcome == "paused":
        return [totext, _cell("build", STOP, "暫停中（有 ingest.pause），只轉文字、沒建庫", since=end, until=end,
                              fix=CONTENT, sev=WARN, tip="看過沒問題，刪掉 ingest.pause 就恢復每晚換庫"),
                _cell("swap", WAIT, word="沒跑到")]
    # 建庫 from keep to the swap (the library's vec_built_at, inside build step 5); 換庫 from there to the end
    build_from = keep_at or start
    swapped = r.swapped_at if r.swapped_at and r.swapped_at >= build_from else None
    bstat = r.build
    in_run = bstat.get("_at") is not None and build_from - timedelta(seconds=5) <= bstat["_at"] <= upto + timedelta(minutes=1)
    if running:
        if swapped is None:
            doing = build_step_words(bstat.get("step")) if bstat.get("state") == "running" and in_run else ""
            return [totext, _cell("build", RUN, doing, since=build_from, now=now, phrase="建庫" + doing),
                    _cell("swap", WAIT)]
        doing = build_step_words(bstat.get("step")) if bstat.get("state") == "running" and in_run else ""
        say = "已換上，" + (f"正在檢查：{doing}" if doing else "正在收尾")
        return [totext, _cell("build", DONE, since=build_from, until=swapped),
                _cell("swap", RUN, say, since=swapped, now=now, phrase="換庫後的檢查" + doing)]
    if outcome == "failed":
        where_ = build_step_words(bstat.get("step")) if bstat.get("state") == "failed" and in_run else ""
        say = f"停在{where_}" if where_ else f"失敗：{words}" if words else "失敗了"
        return [totext, _cell("build", STOP, say, word="出錯", since=build_from, until=end, fix=CONTENT, sev=BAD,
                              tip=words), _cell("swap", WAIT, word="沒跑到")]
    if outcome == "held":
        said, tip = held_words(run, departures_of(run, r.swap))
        return [totext, _cell("build", DONE, since=build_from, until=done_at or end),
                _cell("swap", STOP, said, since=done_at or end, until=end, fix=CONTENT, sev=WARN, tip=tip)]
    if outcome in ("swapped", "rolled_back"):
        moment = swapped if swapped and swapped <= (end or now) else None
        build = _cell("build", DONE, since=build_from, until=moment or done_at or end,
                      tip="" if moment else "含換庫後的檢查（換上的時間點看不到了）")
        if rolled is not None:
            at = when_of(rolled.get("finished_at")) or rolled["_start"]
            return [totext, build, _cell("swap", STOP, f"換上後又退回前一份庫（{clock(at)}）", since=moment or done_at,
                                         until=at, fix=CONTENT, sev=WARN)]
        checks = error_words(error) if error else ""
        dep = departures_of(run, r.swap)
        unx = dep["unexplained"] if dep else 0
        # left for no reason the build could give: lit even when the swap went ahead (爬虫judge, 11-17: 「只要有说不通
        # 的离开，即使没到门槛，也请在人会看的地方亮出来」)
        said = "換上了" + (f"，但有 {unx:,} 份說不出原因地離開（{sites_words(dep['by_site'])}）" if unx else "")
        # sites the guard set back, or did not try again: the library was swapped without their new export (11-19)
        back, refused = set_back_words(run)
        said = "；".join(x for x in (said, back, refused) if x)
        tip = "\n".join(x for x in ("從換上到檢查跑完" if moment else "",
                                    f"說不出原因的：{sites_words(dep['by_site'], 12)}" if unx else "") if x)
        fix = CONTENT if unx or back else CRAWLER if refused else ""
        swap = _cell("swap", DONE, said + (f"；{checks}" if checks else ""), since=moment or done_at or end, until=end,
                     sev=WARN if checks or unx or back or refused else OK, fix=fix, tip=tip)
        return [totext, build, swap]
    return [totext, _cell("build", STOP, "", word="沒見過", since=build_from, until=end or now, fix=CONTENT, sev=WARN,
                          tip=f"進庫的結果是 {outcome}"), _cell("swap", WAIT, word="沒跑到")]


def night_state(cells: list[dict]) -> str:
    """A night's mark from its parts (those live and seen: 還沒上線 and 看不到 aside): run while any runs, or while
    one has ended and the next has not begun (the ingest follows the crawler); missed when the crawler did not start;
    else the worst stop; ok when every part is done; wait when none began; off when none is live yet."""
    live = [c for c in cells if c["word"] not in ("還沒上線", "看不到")]
    if not live:
        return "off"
    states = [c["state"] for c in live]
    if RUN in states:
        return "run"
    if cells[0]["word"] == "沒跑":
        return "missed"
    stops = [c for c in live if c["state"] == STOP]
    if stops:
        return BAD if any(c["sev"] == BAD for c in stops) else WARN
    if all(s == DONE for s in states):
        return WARN if any(c["sev"] == WARN for c in live) else OK
    if not any(s == DONE for s in states):
        return "wait"
    return "run" if any(c["word"] == STATE_WORDS[WAIT] for c in live) else OK


def one_night(r: Reading, night: date) -> dict:
    crawl, export, crawl_end = crawl_cells(r, night)
    run = r.crawler_run(night)
    crawl_start = run["start"] if run else None
    cells = [crawl, export, *ingest_cells(r, night, crawl_end, (crawl, export), crawl_start)]
    # a build going on that is not this chain's (one run by hand): said beside 建庫, never as its state
    b = r.build
    if (b.get("state") == "running" and b.get("_at") and night_of(b["_at"]) == night
            and RUN not in (cells[3]["state"], cells[4]["state"])):
        since = b.get("_since") or b["_at"]
        extra = f"另外有一次建庫在跑：{build_step_words(b.get('step'))}，{clock(since)} 開始（不是每晚進庫叫的，多半是手動）"
        cells[3]["tip"] = "\n".join(x for x in (cells[3]["tip"], extra) if x)
        cells[3]["aside"] = extra
    state = night_state(cells)
    # the scheduled ingest stopped short and one by hand swapped: said in the first sentence too (chain_ingest)
    ran, _rolled, _others, _first = chain_ingest(r, night, crawl_start)
    return {"night": night.isoformat(), "date": f"{night:%m-%d}", "label": label_of(night), "tip": starts_at(night),
            "state": state, "word": NIGHT_WORDS[state], "cells": cells, "note": "",
            "departures": departures_of(ran, r.swap), "runs": night_runs(r, night, crawl_start, ran)}


def night_runs(r: Reading, night: date, crawl_start: datetime | None, shown: dict | None) -> list[dict]:
    """Every ingest run of a night's chain, in the order they started (spec 2026-11-17-panels-v3-questions.md 八.3: one
    run, one segment), each {'run' (1, 2, …), 'since', 'until', 'outcome', 'shown' (the one the night's parts are
    drawn from), 'reason', 'code', 'unknown'} — nothing said of why there were several."""
    out = []
    for k, x in enumerate(_chain(r, night, crawl_start), 1):
        end = x["_end"] or (r.now if x.get("outcome") == "running" else x["_start"])
        out.append({"run": k, "since": x["_start"].isoformat(timespec="seconds"),
                    "until": end.isoformat(timespec="seconds"), "outcome": str(x.get("outcome") or ""),
                    "shown": x is shown, **run_reason(x, departures_of(x, r.swap))})
    return out


def default_night(r: Reading) -> date:
    """The night the page opens on: tonight's once its hour has come or anything of it started, else last night."""
    last = night_of(r.now)
    due = r.now >= at_night(last, START)
    active = r.crawler_run(last) is not None or chain_ingest(r, last, None)[0] is not None
    return last if due or active else last - timedelta(days=1)


# ------------------------------------------------------------------------------------------------ the sentence


def first_sentence(night: dict, changes: dict | None) -> dict:
    """The night in one line for /api/tonight (the page draws its own headline: tonight_page.headline): {'state',
    'text', 'fix'}; state as the night's mark, made worse by what the last swap's comparison found (documents that left
    with no reason, or with one on our side). fix: which side's step it is, as data; the text names no one (spec
    2026-11-17-panels-v3-questions.md 八.2)."""
    label, cells, state = night["label"], night["cells"], night["state"]
    if state == "run":
        cur = next((c for c in cells if c["state"] == RUN), None)
        if cur is not None and cur["sev"] == WARN:  # silent a while, maybe waiting: said, and the page still asks
            return {"state": WARN, "text": f"{label}{cur['name']}{cur['word']}：{cur['say']}",
                    "fix": cur["fix"]}
        if cur is None:  # between two parts: the last done and the next not begun
            done = [c for c in cells if c["state"] == DONE]
            return {"state": "run", "text": f"{label}正在跑：{done[-1]['name'] if done else ''}跑完了，等下一步開始",
                    "fix": ""}
        took = f"，{cur['when']}" if cur["when"] else ""
        lead = f"{label}正在跑：{cur['phrase']}"
        return {"state": "run", "text": lead + took, "fix": "", "lead": lead, "since_ts": cur["since_ts"]}
    if state == "missed":
        return {"state": BAD, "text": f"{label}沒跑：01:00 該開始的每晚更新沒有動靜", "fix": CRAWLER}
    if state in (BAD, WARN):
        # the worst part that stopped, not the first: an ingest that failed after a paused crawl is the content
        # session's (review 2026-11-17)
        stops = [c for c in cells if c["state"] == STOP]
        c = next((c for c in stops if c["sev"] == BAD), stops[0] if stops else None)
        if c is None:  # every part done, one with a warning: an export held back, the checks after a swap
            c = next(c for c in cells if c["sev"] == WARN)
            unx = (night.get("departures") or {}).get("unexplained") or 0
            if c["key"] == "swap" and unx and cells[-1]["state"] == DONE:  # the build's own count of the unexplained
                return _with_changes(label, night, changes)
            if c["key"] != "swap":  # said, unless the last swap's comparison found worse (查不出原因)
                if cells[-1]["state"] == DONE and cells[-1]["say"] != "庫沒換":
                    lib = _with_changes(label, night, changes)
                    if lib["state"] == BAD or unx:
                        return lib
                said = c["say"].split("；", 1)[0]  # what follows is news (人工暫緩), never the worst thing
                return {"state": WARN, "text": f"{label}{c['name']}{said}", "fix": c["fix"]}
            swapped = night.get("note") or "換了新庫"
            return {"state": WARN, "text": f"{label}{swapped}，{c['say'].split('；', 1)[-1]}",
                    "fix": CONTENT}
        head = "出錯" if c["sev"] == BAD else "停了"
        return {"state": state, "text": f"{label}{head}：{c['name']}{c['say']}", "fix": c["fix"]}
    if state == "wait":
        return {"state": "wait", "text": f"{label}還沒開始：01:00 開跑", "fix": ""}
    if state == "off":
        return {"state": "wait", "text": f"{label}的每晚更新還沒上線", "fix": ""}
    swap = cells[-1]
    if swap["say"] == "庫沒換":
        return {"state": OK, "text": f"{label}正常：沒有新資料，庫沒換", "fix": ""}
    return _with_changes(label, night, changes)


def _with_changes(label: str, night: dict, changes: dict | None) -> dict:
    """A swapped night's sentence with what the last swap's comparison found; the scheduled run that stopped short
    before one by hand swapped (the night's note) is said in place of 換了新庫, or after the counts."""
    note = night.get("note") or ""
    swapped, after = note or "換了新庫", f"；{note}" if note else ""
    ready = changes and changes.get("state") == "ready" and changes.get("night") == night["night"]
    dep = night.get("departures")  # the build's own check, when it made one: its count of the unexplained wins
    if dep and dep["unexplained"]:
        tail = f"但有 {dep['unexplained']:,} 份說不出原因地離開（{sites_words(dep['by_site'])}）"
        if ready:
            t = changes["totals"]
            return {"state": WARN, "fix": CONTENT, "text": f"{label}換了新庫：新進 {t['new']:,} 份、離開 "
                    f"{t['left']:,} 份，{tail}" + after}
        return {"state": WARN, "text": f"{label}{swapped}，{tail}", "fix": CONTENT}
    if changes and changes.get("state") == "computing":
        return {"state": OK, "text": f"{label}正常：{swapped}（變了什麼還在算）", "fix": ""}
    if not ready:
        return {"state": OK, "text": f"{label}正常：{swapped}", "fix": ""}
    t = changes["totals"]
    if dep is not None:  # compared, nothing unexplained: the panel's own count gives way
        t = {**t, "unexplained": 0}
    base = f"新進 {t['new']:,} 份、離開 {t['left']:,} 份"
    if t["unexplained"]:
        return {"state": BAD, "text": f"{label}換了新庫：{base}，其中 {t['unexplained']:,} 份查不出原因"
                + after, "fix": CONTENT}
    if t["look"]:
        fix = t.get("look_fix") or CONTENT
        return {"state": WARN, "text": f"{label}換了新庫：{base}，其中 {t['look']:,} 份可能是我們這邊漏的"
                + after, "fix": fix}
    return {"state": OK, "text": f"{label}正常：{base}" + ("（都有原因）" if t["left"] else "") + after, "fix": ""}


# ------------------------------------------------------------------------------------------------ the whole


def tonight(where: Where, *, night: str | None = None, changes: dict | None = None,
            now: datetime | None = None) -> dict:
    """The home page's top: {'now', 'default', 'shown' (a night: label, state, word, cells), 'nights' (the last
    NIGHTS, oldest first: night, date, label, state, word, href), 'sentence' (of the default night), 'crawler' (the
    crawler's own counts of its last night), 'sig' (changes when anything shown changes: the page reloads then),
    'live' (something runs)}. night: the night to show (YYYY-MM-DD, at most PICKABLE days back and not after
    tomorrow), else the default one."""
    now = now or pl.now_tpe()
    r = Reading(where, now)
    base = default_night(r)
    last = night_of(now)
    days = [last - timedelta(days=i) for i in range(NIGHTS - 1, -1, -1)]
    nights = {d: one_night(r, d) for d in days}
    shown_day = base
    if night:
        try:
            shown_day = date.fromisoformat(night)
        except ValueError:
            shown_day = base
        # only a night there can be something of: one far off (9999-12-31) overflowed the day after it (review
        # 2026-11-17) 【Claude】
        today = now.astimezone(TAIPEI).date()
        if not today - timedelta(days=PICKABLE) <= shown_day <= today + timedelta(days=1):
            shown_day = base
    if shown_day not in nights:
        nights[shown_day] = one_night(r, shown_day)
    if base not in nights:
        nights[base] = one_night(r, base)
    for d, n in nights.items():
        n["href"] = "/" if d == base else f"/?night={d.isoformat()}"
    sentence = first_sentence(nights[base], changes)
    # what the last swap's comparison found (查不出原因, ours to look at) marks that night too, not only the sentence
    rank = {OK: 0, WARN: 1, BAD: 2}
    if nights[base]["state"] in rank and rank.get(sentence["state"], 0) > rank[nights[base]["state"]]:
        nights[base].update(state=sentence["state"], word=pl.STATE_WORDS[sentence["state"]])
    shown = nights[shown_day]
    status = r.status
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    crawler = {"night": r.status_night.isoformat() if r.status_night else "",
               "started": str(status.get("night_started") or ""),
               "counts": [(pl.NIGHT_COUNTS.get(k, k), v) for k, v in counts.items() if isinstance(v, int)],
               "sites_changed": len(status.get("sites_changed") or [])}
    live = any(c["state"] == RUN for c in shown["cells"]) or nights[base]["state"] == "run"
    # what decides a reload: never the minutes a running part has taken (the page ticks those itself)
    sig_parts = [shown["state"], sentence["state"], sentence.get("lead") or sentence["text"],
                 *(f"{c['state']}{c['say']}{c['until']}" for c in shown["cells"]),
                 str((changes or {}).get("state")), str((changes or {}).get("keys"))]
    sig = hashlib.blake2b("|".join(sig_parts).encode(), digest_size=6).hexdigest()
    return {"now": now.isoformat(timespec="seconds"), "default": base.isoformat(), "shown": shown,
            "nights": [nights[d] for d in days], "sentence": sentence, "crawler": crawler, "sig": sig, "live": live}


def strip_for_json(data: dict) -> dict:
    """tonight()'s answer as plain JSON (no datetimes are in it; kept for the API's shape)."""
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))
