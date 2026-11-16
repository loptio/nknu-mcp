"""Periodic updates, stage 1, Task 6 (design docs/superpowers/specs/2026-11-16-periodic-update-design.md §7.3, §7.7;
plan docs/superpowers/plans/2026-11-16-update-stage-1.md): what a night reads again, put back for the next
`nknu archive run`. Run as `python -m nknu.archive.update --dir <archive> {plan,put-back,go-live}`: no command in
cli.py until the owner names the time (11-16: 「动 cli.py…动手前先和我核对时机」).

- plan: read only (the ledger opened read-only; an archive from before stage 1, with no list_look or list_row, too):
  what tonight would take, counted per kind and per server queue.
- put-back: holds run.lock (refused while a run holds it); first ends the looks that cannot end by themselves
  (postback.Chains.close_stale_looks), then opens a look on each list walked by button (Chains.look), a GET look on
  the first page of each list paged by GET (Chains.look_get), and puts each site's home and seed pages back
  (Frontier.put_back).
- go-live: holds run.lock; reads every list row the archive holds into list_row (postback.backfill_list_rows) and
  names the round-one sites as under update (crawler.UPDATE_SITES_KEY: their run caps). Once, before the first night.
  With --sites, only those (a trial night on one small site, the owner 11-16: 「挑一个小站试一晚」).

- review: holds run.lock; the change list (watch.night) of every night not yet reviewed (from FROM_KEY: a night whose
  run or review never ended is reviewed with the next), once (meta update:night_reviewed), in one transaction:
  reports/changes-<night>.csv and reports/update-status.json (counts, alarms, the sites whose export is due, the
  lists whose walk ended short, each site's last answer, tonight's run end). Exit 3 when tonight's run did not end
  finished or waiting (db_error, disk_low, a kill: no run_end), the status file saying so. The export is listed,
  never run (the owner's timing, 11-16). An alarmed site's rows go back every night until let go (watch.HELD_KEY).
- sites: the update sites as one comma list (what the nightly script gives `archive run --sites`).
- fail --step X: the status file says the night stopped at step X, kept with tonight's review when there is one (the
  nightly script, research/crawler_lab_2026-11-04/nightly_update.sh, on a failing step).

Only round-one sites. A list left out is counted with its reason: a walk cut off with presses waiting is resume()'s to
finish, a look still open waits for its end.

Stage 2 (plan 2026-11-16-update-stage-2 Task 3, design §7.3): shares keyed on the day's number (date.toordinal, Asia/
Taipei) and the row id, so a share is the same whichever night computes it and every share comes every WEEK or MONTH
days whatever the month's length 【Claude: the plan said weekday and day of month; February would skip two shares】:
- walks, walks_get: the lists of tonight's monthly share walked whole, as a whole look (postback.Chains.look and
  look_get with whole: every page read, news or not; an end short of a whole walk keeps the last record, a list
  shrunk past max(10, 10%) a warning: design §7.5), taken out of tonight's looks; a whole walk that ended short is
  walked again the next night (plan check wf_2249ba4d-a90: the first way, a restart from the GET, set the record to
  walking before any answer and lost the list for good on a failed first page).
- redo, redo_get: a list with no end (walking, or none) every night: it has no record to lose, so it starts again
  from its GET (restart_chain), or every page of its GET group is put back.
- recent: the pages list rows link whose row date (export.bare_date) is within RECENT_DAYS, one share a week.
- standing: the standing pages (a page that answered a good 200 once: the row or a url_version copy, whatever it
  answers now; not a list or a list's page, not a home or seed, not a platform detail page, not a page a list row
  links), one share a week (once the school deleted it, page_watch.gone 2 or more, one share a month); every night
  on DAILY_SITES in DAILY_PERIODS (speed-up rule 2); every other night when changed at their last OFTEN_CHANGES
  checks (rule 3, page_watch).
- follow (rule 1): what the notices that changed since the last night's start link in their own text (watch.
  content_links from the answer in the archive; not their menu, not what a home links): standing pages and files
  【Claude】. Lists are no source: their rows bring their details back (list_row).
- again: the rows of a site the review holds (watch.HELD_KEY): every night until a review lets them go.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nknu.archive import crawler as crawl_mod
from nknu.archive import export, outcome, postback, profiles, watch
from nknu.archive.frontier import Frontier, write_first

# the seed sources whose pages are a site's way in (seeds.py): its home, the owner's extra pages, a c.nknu template's
# SiteMap.aspx. Not sso_lists (lists: looks), nor the old sources (old_url, v1_known, commoncrawl), nor systems
HOME_SOURCES = ("site_home", "extra", "sitemap_aspx")
NIGHT_KEY = "update:night_started"  # put-back's time: the night's review takes what was fetched since
REVIEWED_KEY = "update:night_reviewed"  # the night_started a review has counted (page_watch counts each night once)
PUT_KEY = "update:night_put_back"  # what put-back took tonight, per batch (the status file's put_back)
FROM_KEY = "update:review_from"  # where the next review's window opens: the last review's end, or a night's start
REVIEWED_TO = "update:reviewed_to"  # the end of the window the last review counted (its clock)
RUN_OK = ("finished", "waiting")  # a run's end that leaves the night whole (crawler run_end)
# a whole look's ends that did not reach the list's end (a page that did not come, a list page read as no list):
# walked whole again the next night. Any other end (last_page, no_rows_ok, mismatch:*, max_pages, ...) reached the
# list's end as its walks do: next month (review of 4a5754d: a list that always ends mismatch:page was walked nightly)
SHORT_ENDS = ("post_failed", "pager_left", "no_list", "not_a_list", "no_warc")
WEEK, MONTH = 7, 30  # the shares (design §7.3)
RECENT_DAYS = 30  # design §7.3: the notices of the last 30 days, by the date their list row writes
OFTEN_CHANGES = 2  # rule 3: changed at its last two checks (10-24: 「最近两次都有变动的改成 2 天一次」)
OFTEN_GAP_S = 42 * 3600  # rule 3's two days: a check two nights ago a little later in the evening counts 【Claude】
# rule 2 (10-24: 「开学前后、选课和加退选、注册缴费期间，教务、学务（含住宿）、注册、课外活动的常设页每天抓」): the weeks
# and the sites 【Claude, design §7.3: a person sets them each term】: 教务处 (註冊組 in it), 学生事务处, 生活辅导组
# (宿舍), 课外活动指导组, 新生学务资讯
DAILY_PERIODS = (("01-20", "03-10"), ("08-01", "09-30"))
DAILY_SITES = frozenset({"c.nknu.edu.tw/aca", "sso.nknu.edu.tw/web/592", "sso.nknu.edu.tw/web/595",
                         "sso.nknu.edu.tw/web/596", "sso.nknu.edu.tw/web/600"})
STATUS = "update-status.json"
TAIPEI = ZoneInfo("Asia/Taipei")
SAY = {"new": "新的", "changed": "改了", "same": "没变", "no_answer": "这次没回话", "not_seen": "这次没看到",
       "not_got": "没拿到", "deleted": "学校删了", "back": "回来了"}


@dataclass
class Tonight:
    looks: list[int] = field(default_factory=list)  # list pages of lists walked by button
    looks_get: list[int] = field(default_factory=list)  # the first page of each list paged by GET
    homes: list[int] = field(default_factory=list)  # home and seed pages (and where a 3xx of one leads)
    walks: list[int] = field(default_factory=list)  # lists walked by button, walked whole tonight (a whole look)
    walks_get: list[int] = field(default_factory=list)  # the first page of the GET lists walked whole tonight
    redo: list[int] = field(default_factory=list)  # lists walked by button with no end: from their GET again
    redo_get: list[int] = field(default_factory=list)  # every page of the GET lists with no end
    recent: list[int] = field(default_factory=list)  # pages of the last RECENT_DAYS' list rows, tonight's share
    standing: list[int] = field(default_factory=list)  # standing pages: tonight's share, rules 2 and 3
    follow: list[int] = field(default_factory=list)  # rule 1: linked in the text of a notice that changed
    again: list[int] = field(default_factory=list)  # the rows of the sites the last review held
    skipped: Counter = field(default_factory=Counter)  # lists left out tonight, by reason
    why: Counter = field(default_factory=Counter)  # standing pages by why: week, month (deleted), daily, often

    def ids(self) -> list[int]:
        return [*self.looks, *self.looks_get, *self.homes, *self.walks, *self.walks_get, *self.redo, *self.redo_get,
                *self.recent, *self.standing, *self.follow, *self.again]


def _has(frontier: Frontier, table: str) -> bool:
    return bool(frontier.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _in(ids) -> str:
    return ",".join("?" * len(ids))


def update_keys(frontier: Frontier) -> set[str]:
    """The run keys under update: round one's sites, and the keys of the owner's extra seeds (archive_round1.json
    extra: pages on keys that site_plan lists as round two or not at all; review of 7f12e29..8474164)."""
    keys = {r[0] for r in frontier.q("SELECT site FROM site_plan WHERE round='1'")}
    keys |= {r[0] for r in frontier.q("SELECT DISTINCT u.owner_site FROM seed_src s JOIN url u ON u.url = s.url WHERE "
                                      "s.source='extra' AND u.owner_site IS NOT NULL")}
    return keys


def _daily(today: date) -> bool:
    md = f"{today:%m-%d}"
    return any(a <= md <= b for a, b in DAILY_PERIODS)


def select(frontier: Frontier, root: Path, *, sites: set[str] | None = None,
           closable: set[int] | frozenset[int] = frozenset(), today: date | None = None,
           last_night: float | None = None, now: float | None = None) -> Tonight:
    """Tonight's lists and pages (the module's docstring), each url row in one batch at most. sites: only these run
    keys (the run that follows is given the same: `archive run --sites`); closable: looks put-back will close first
    (Chains.stale_looks), counted as closed; today: the night's date (Asia/Taipei); last_night: the last night's start
    (rule 1); now: rule 3's clock."""
    t = Tonight()
    today = today or datetime.now(TAIPEI).date()
    now = time.time() if now is None else now
    n = today.toordinal()
    if sites is not None:
        r1 = set(sites)
    else:  # the sites go-live named (the run fetches these: `update sites`), else round one's
        named = frontier.meta(crawl_mod.UPDATE_SITES_KEY)
        r1 = set(json.loads(named)) if named else update_keys(frontier)
    open_looks = ({r[0] for r in frontier.q("SELECT list_id FROM list_look WHERE ended_at IS NULL")} - set(closable)
                  if _has(frontier, "list_look") else set())
    waiting = {r[0] for r in frontier.q("SELECT DISTINCT list_id FROM action WHERE kind NOT IN ('click', 'data') AND "
                                        "state IN ('pending', 'active')")}
    looks = _ended_looks(frontier)
    walked: set[int] = set()
    # a list walked by button: its listwalk counts its rows (page_stored, _pager_answer); get_walk writes rows NULL
    for r in frontier.q("SELECT w.list_id, w.ended_by, u.owner_site, u.state FROM listwalk w JOIN url u ON u.id = "
                        "w.list_id WHERE w.rows IS NOT NULL ORDER BY w.list_id"):
        if r["owner_site"] not in r1:
            continue
        walked.add(r["list_id"])
        if r["state"] in ("pending", "active"):
            t.skipped["first_page_waiting"] += 1
        elif r["state"] not in ("done", "failed"):
            t.skipped["first_page_not_got"] += 1
        elif r["list_id"] in open_looks:
            t.skipped["look_open"] += 1
        elif r["list_id"] in waiting:
            t.skipped["presses_waiting"] += 1
        elif r["ended_by"] in (None, "walking"):
            t.redo.append(r["list_id"])  # no record to lose: from its GET again, every night until it ends
        elif r["list_id"] % MONTH == n % MONTH or _short(looks, r["list_id"]):
            t.walks.append(r["list_id"])  # tonight's whole walk, or the one that ended short the last time
        else:
            t.looks.append(r["list_id"])
    chains = postback.Chains(frontier, root)
    grouped: set[int] = set()
    for (pid,) in frontier.q("SELECT DISTINCT page_id FROM page_res WHERE role='pager' AND how='get' AND res_id IS NOT "
                             "NULL ORDER BY page_id"):
        if pid in grouped:
            continue
        group, edges = chains._group(pid)
        grouped |= group
        if group & walked:  # a list walked by button that also links its pages: a look, already counted
            continue
        num, _nexts = postback._numbering(edges)
        first = min((i for i, n in num.items() if n == 1), default=None)
        row = frontier.row_by_id(first) if first is not None else None
        if row is None:
            # counted on its own site's plan only (11-16: every site's plan said 「认不出第 1 页 3」)
            owners = {r[0] for r in frontier.q(f"SELECT DISTINCT owner_site FROM url WHERE id IN ({_in(group)})",
                                               sorted(group))}
            if owners & r1:
                t.skipped["get_no_first_page"] += 1
            continue
        if row["owner_site"] not in r1 or row["kind"] != "page":
            continue
        if row["state"] not in ("done", "failed"):
            t.skipped["first_page_waiting" if row["state"] in ("pending", "active") else "first_page_not_got"] += 1
            continue
        ended = frontier.q(f"SELECT ended_by FROM listwalk WHERE list_id IN ({_in(group)})", sorted(group))
        if group & open_looks:
            t.skipped["look_open"] += 1
        elif not ended or any(w[0] in (None, "walking") for w in ended):
            for i in sorted(group):  # no record to lose: every page the group knows, every night until it ends
                g = frontier.row_by_id(i)
                if g is not None and g["kind"] == "page" and g["state"] in ("done", "failed") and g["owner_site"] in r1:
                    t.redo_get.append(i)
        elif first % MONTH == n % MONTH or _short(looks, first):
            t.walks_get.append(first)  # a whole look, handed over page by page
        else:
            t.looks_get.append(first)
    # a home inside a GET pager group (an SSO unit site's) stays a home, but for its first page or a redo
    taken = walked | set(t.looks_get) | set(t.walks_get) | set(t.redo_get)
    homes: list[int] = []
    for h in frontier.q(f"SELECT DISTINCT u.id, u.status, u.final_url, u.flags, u.owner_site FROM seed_src s "
                        f"JOIN url u ON "
                        f"u.url = s.url WHERE s.source IN ({_in(HOME_SOURCES)}) AND u.kind = 'page' AND u.tier IN "
                        f"(0, 1) AND u.state IN ('done', 'failed') ORDER BY u.id", HOME_SOURCES):
        if h["owner_site"] not in r1 or h["id"] in taken:
            continue
        homes.append(h["id"])
        # a seed that moves (a 3xx, a script or a refresh): the page it lands on is a dup of every reading, never
        # fetched again by itself; followed as far as a redirect chain goes
        at = h
        for _hop in range(outcome.MAX_HOPS):
            moved = ((at["status"] is not None and 300 <= at["status"] < 400)
                     or "js_redirect" in (at["flags"] or "").split(","))
            to = frontier.row(at["final_url"]) if moved and at["final_url"] else None
            if to is None or to["id"] in homes or to["kind"] != "page" or to["state"] not in ("done", "failed"):
                break
            if to["id"] not in taken:
                homes.append(to["id"])
            at = to
    t.homes = list(dict.fromkeys(homes))
    _pages(frontier, root, t, chains, r1, walked | grouped, today, n, last_night, now)
    return t


def _ended_looks(frontier: Frontier) -> dict[int, tuple[float, int, str | None]]:
    """Each list page's last look that ended (list_look keeps one per page): (started, whole, ended_by)."""
    if not _has(frontier, "list_look") or "whole" not in {c[1] for c in frontier.q("PRAGMA table_info(list_look)")}:
        return {}
    return {r[0]: (r[1], r[2], r[3]) for r in frontier.q("SELECT list_id, started, whole, ended_by FROM list_look "
                                                          "WHERE ended_at IS NOT NULL")}


def _short(looks: dict[int, tuple[float, int, str | None]], list_id: int) -> bool:
    """The list page's last look was a whole walk that ended short of the list's end (SHORT_ENDS, or its page not
    got). For a GET list, its first page's: a later page that always fails waits for next month's walk."""
    got = looks.get(list_id)
    if got is None or not got[1]:
        return False
    end = got[2] or ""
    return end in SHORT_ENDS or end.startswith("not_got")


def _pages(frontier: Frontier, root: Path, t: Tonight, chains: postback.Chains, r1: set[str], lists: set[int],
           today: date, n: int, last_night: float | None, now: float) -> None:
    """The recent notices, the standing pages and rule 1's (the module's docstring); lists: every list page (walked
    by button or in a GET pager group, any site), never put back as a page here."""
    taken = set(t.ids()) | lists
    rows = _has(frontier, "list_row")
    # the pages a list's rows link: its items and details (page_res) and the list rows' links
    linked = {r[0] for r in frontier.q("SELECT DISTINCT p.res_id FROM page_res p JOIN url l ON l.id = p.page_id WHERE "
                                       "p.res_id IS NOT NULL AND p.role IN ('item', 'detail') AND (l.page_role = 'list' "
                                       "OR l.id IN (SELECT list_id FROM listwalk))")}
    if rows:
        linked |= {r[0] for r in frontier.q("SELECT u.id FROM list_row r JOIN url u ON u.url = r.href")}
    standing: dict[int, str] = {}
    notices: set[int] = set(linked)
    clean = " AND ".join(f"instr(',' || coalesce(flags, '') || ',', ',{f},') = 0" for f in sorted(watch.BAD_FLAGS))
    good_once = (f"(status = 200 AND {clean}) OR id IN (SELECT url_id FROM url_version WHERE status = 200 AND kind = "
                 f"'page' AND {clean})" if _has(frontier, "url_version") else f"status = 200 AND {clean}")
    for r in frontier.q("SELECT id, url, owner_site FROM url WHERE kind = 'page' AND tier IN (0, 1) AND state IN "
                        f"('done', 'failed') AND depth > 0 AND coalesce(page_role, '') != 'list' AND ({good_once})"):
        if r["owner_site"] not in r1 or r["id"] in lists:
            continue
        if profiles.detail_of(r["url"]) is not None:
            notices.add(r["id"])
        elif r["id"] not in linked:
            standing[r["id"]] = r["owner_site"]
    if rows:
        lo = today - timedelta(days=RECENT_DAYS)
        for r in frontier.q("SELECT DISTINCT href, date FROM list_row WHERE href IS NOT NULL AND date IS NOT NULL"):
            got = export.bare_date(r["date"])
            if got is None or date.fromisoformat(got[0]) <= lo:
                continue
            row = chains._fetched_row(r["href"])
            if (row is None or row["kind"] != "page" or row["state"] not in ("done", "failed") or row["owner_site"]
                    not in r1 or row["id"] in taken or row["id"] % WEEK != n % WEEK):
                continue
            t.recent.append(row["id"])
            taken.add(row["id"])
    watched = _has(frontier, "page_watch")
    often = ({r["url_id"]: r["checked_at"] for r in frontier.q("SELECT url_id, checked_at FROM page_watch WHERE "
                                                               "changes >= ?", (OFTEN_CHANGES,))} if watched else {})
    deleted = ({r[0] for r in frontier.q("SELECT url_id FROM page_watch WHERE gone >= 2")} if watched else set())
    daily = _daily(today)
    for i, site in standing.items():
        if i in taken:
            continue
        if i in deleted:  # the school deleted it: once a month, to see it back
            if i % MONTH != n % MONTH:
                continue
            t.why["month"] += 1
        elif i % WEEK == n % WEEK:
            t.why["week"] += 1
        elif daily and site in DAILY_SITES:
            t.why["daily"] += 1
        elif often.get(i) is not None and now - often[i] >= OFTEN_GAP_S:
            t.why["often"] += 1
        else:
            continue
        t.standing.append(i)
        taken.add(i)
    for i in (json.loads(frontier.meta(watch.HELD_KEY) or "null") or {"ids": []})["ids"]:
        row = frontier.row_by_id(i)
        if row is not None and i not in taken and row["state"] in ("done", "failed") and row["owner_site"] in r1:
            t.again.append(i)
            taken.add(i)
    if last_night is None or not watched:
        return
    changed = [r[0] for r in frontier.q("SELECT url_id FROM page_watch WHERE changed_at >= ? ORDER BY url_id",
                                        (last_night,))]
    menu: set[str] = set()
    for i in range(0, len(t.homes), 500):
        part = t.homes[i:i + 500]
        menu |= {r[0] for r in frontier.q(f"SELECT dst FROM link WHERE src IN ({_in(part)})", part)}
    for src in changed:
        if src in lists or src not in notices:
            continue
        got = frontier.row_by_id(src)
        if got is None or got["warc"] is None or got["warc_offset"] is None:
            continue
        try:
            url, _status, headers, body = postback.read_response(root, got["warc"], got["warc_offset"])
        except (OSError, ValueError):
            continue
        for dst in watch.content_links(postback.as_text(body, headers.get("content-type")), url):
            if dst in menu:
                continue
            row = chains._fetched_row(dst)
            if row is None or row["id"] in taken:
                continue
            if row["id"] in standing or (row["kind"] == "file" and row["state"] == "done" and row["owner_site"] in r1):
                t.follow.append(row["id"])
                taken.add(row["id"])


BATCHES = ("looks", "looks_get", "walks", "walks_get", "redo", "redo_get", "homes", "recent", "standing", "follow",
           "again", "not_opened")


def put_back(frontier: Frontier, root: Path, tonight: Tonight) -> Counter:
    """Tonight's looks and whole looks opened, lists with no end started again and pages put back (a look that does
    not open, a page not done or failed, is counted not_opened)."""
    chains = postback.Chains(frontier, root)
    got: Counter = Counter()
    for i in tonight.looks:
        got["looks" if chains.look(i) else "not_opened"] += 1
    for i in tonight.looks_get:
        got["looks_get" if chains.look_get(i) else "not_opened"] += 1
    for i in tonight.walks:
        got["walks" if chains.look(i, whole=True) else "not_opened"] += 1
    for i in tonight.walks_get:
        got["walks_get" if chains.look_get(i, whole=True) else "not_opened"] += 1
    for i in tonight.redo:
        chains.restart_chain(i, "update: no end", walk=True, keep_look=False)
        got["redo"] += 1
    for name, ids, why in (("redo_get", tonight.redo_get, "update_redo"), ("homes", tonight.homes, "update_home"),
                           ("recent", tonight.recent, "update_recent"),
                           ("standing", tonight.standing, "update_standing"),
                           ("follow", tonight.follow, "update_follow"), ("again", tonight.again, "update_again")):
        for i in ids:
            got[name if frontier.put_back(i, why) else "not_opened"] += 1
    return got


def _report(frontier: Frontier, t: Tonight) -> None:
    ids = sorted(set(t.ids()))
    queues: Counter = Counter()
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in frontier.q(f"SELECT coalesce(h.queue, '?') FROM url u LEFT JOIN host h ON h.host = u.host WHERE u.id IN "
                            f"({_in(part)})", part):
            queues[r[0]] += 1
    print(f"今晚要看：按按钮翻页的列表 {len(t.looks)} 个、按网址翻页的列表第 1 页 {len(t.looks_get)} 个、"
          f"首页和起点 {len(t.homes)} 页；翻到底的列表 {len(t.walks)} 个、按网址翻页的 {len(t.walks_get)} 个；"
          f"没有结尾、从头再翻的 {len(t.redo)} 个、按网址翻页的 {len(t.redo_get)} 页；"
          f"最近 {RECENT_DAYS} 天的公告 {len(t.recent)} 页；常设页 {len(t.standing)} 页（轮到的 {t.why['week']}、"
          f"学校删了每月看一次的 {t.why['month']}、开学前后每天的 {t.why['daily']}、常改的 {t.why['often']}）；"
          f"改了的公告内文链到的 {len(t.follow)} 个；整站警报压着、再看一次的 {len(t.again)} 个；"
          f"共 {len(ids)} 个网址（之后有新东西才会再多）")
    if queues:
        print("按服务器：" + "、".join(f"{q} {n}" for q, n in queues.most_common()))
    if t.skipped:
        words = {"presses_waiting": "还有按钮等着按（下次 run 开头接着翻）", "look_open": "上次的查看还没结束",
                 "get_no_first_page": "认不出第 1 页", "first_page_waiting": "第 1 页还在等着抓",
                 "first_page_not_got": "第 1 页上次没抓到"}
        print("今晚不看的列表：" + "、".join(f"{words.get(k, k)} {n}" for k, n in t.skipped.most_common()))


def _local(t: float) -> str:
    return datetime.fromtimestamp(t, TAIPEI).isoformat(timespec="seconds")


def _write_status(root: Path, status: dict) -> Path:
    out = root / "reports"
    out.mkdir(parents=True, exist_ok=True)
    part = out / (STATUS + ".part")
    part.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    part.replace(out / STATUS)
    return out / STATUS


def _run_end(frontier: Frontier, night: float) -> dict | None:
    """Tonight's run end: the first run_end after the first run_start since the night started (event.at is UTC ISO, as
    watch._iso writes it); None when no run started, or none ended (a kill, a reboot)."""
    start = frontier.q("SELECT rowid FROM event WHERE code='run_start' AND at >= ? ORDER BY rowid LIMIT 1",
                       (watch._iso(night),))
    if not start:
        return None
    got = frontier.q("SELECT message FROM event WHERE code='run_end' AND rowid > ? ORDER BY rowid LIMIT 1",
                     (start[0][0],))
    if not got:
        return None
    try:
        return json.loads(got[0]["message"])
    except (TypeError, ValueError):
        return {"message": got[0]["message"]}


def _short_ends(frontier: Frontier, night: float) -> list[dict]:
    """The looks and whole walks that ended short in the window (postback._close_look's look_ended events)."""
    return [{"url": r["url"], "why": r["message"]} for r in frontier.q(
        "SELECT url, message FROM event WHERE code='look_ended' AND at >= ? ORDER BY rowid", (watch._iso(night),))]


def _seen(frontier: Frontier) -> dict[str, str | None]:
    """Each update site's last answer (design §7.5: 「每站最后一次看过」; the export's 资料截至 stays the last export's)."""
    keys = json.loads(frontier.meta(crawl_mod.UPDATE_SITES_KEY) or "[]")
    got = {k: None for k in keys}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for r in frontier.q(f"SELECT owner_site, max(fetched_at) FROM url WHERE owner_site IN ({_in(part)}) AND state = "
                            f"'done' GROUP BY owner_site", part):
            got[r[0]] = r[1]
    return got


def review(frontier: Frontier, root: Path, *, now: float | None = None) -> tuple[dict, bool]:
    """The change list and status file of the nights not yet reviewed (the module's docstring); (status, done now).
    The change list and the status are written before the night is counted (one transaction: a review that cannot
    write them counts nothing, the next one counts it). A night counted already is not counted again (page_watch's
    changes and gone would be counted twice): its status file is read back."""
    raw = frontier.meta(NIGHT_KEY)
    night_at = float(raw)
    since = float(frontier.meta(FROM_KEY) or raw)
    now = time.time() if now is None else now
    out = root / "reports"
    if frontier.meta(REVIEWED_KEY) == raw:
        try:
            old = json.loads((out / STATUS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if old.get("night_started") != _local(night_at) or "counts" not in old:
            raise RuntimeError("这一晚已经算过了，可是状态档不是这一晚的：变动清单在 reports/changes-*.csv")
        return old, False
    out.mkdir(parents=True, exist_ok=True)
    name = f"changes-{datetime.fromtimestamp(since, TAIPEI):%Y%m%d-%H%M}.csv"
    with write_first(frontier.conn, "review"):
        n = watch.night(frontier, root, since, now=now)
        with (out / name).open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(("site", "url", "kind", "change", "previous_at"))
            for c in n.rows:
                w.writerow((c.site, c.url, c.kind, c.change, c.previous_at or ""))
        end = _run_end(frontier, night_at)
        status = {"started": _local(since), "night_started": _local(night_at), "reviewed": _local(now),
                  "failed_step": None if end is not None and end.get("reason") in RUN_OK else "run",
                  "counts": dict(sorted(n.counts().items())), "alarms": n.alarms, "sites_changed": n.sites_changed,
                  "held": len(n.again), "lists_short": _short_ends(frontier, since), "changes_file": name,
                  "run_end": end, "put_back": json.loads(frontier.meta(PUT_KEY) or "{}"),
                  "sites_seen": _seen(frontier), "export": None}  # the seen step writes it after the export
        part = out / (STATUS + ".part")
        part.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        frontier.meta(REVIEWED_KEY, raw)
        frontier.meta(REVIEWED_TO, repr(now))
    part.replace(out / STATUS)
    return status, True


def _say(status: dict) -> str:
    counts = status["counts"]
    words = "、".join(f"{SAY[k]} {counts.get(k, 0)}" for k in SAY)
    alarms = status["alarms"]
    end = (status.get("run_end") or {}).get("reason")
    short = status.get("lists_short") or []
    return (f"这一晚（{status['started']} 起）：{words}；有变动的网站 {len(status['sites_changed'])} 个"
            + (f"；警报 {len(alarms)} 个：" + "、".join(f"{k}（{v}）" for k, v in alarms.items()) if alarms else "")
            + (f"；没翻完、照旧留着上一次的列表 {len(short)} 个" if short else "")
            + (f"；run 结束的原因 {end}" if end else "；这一晚没有 run 结束的纪录")
            + ("（没有好好结束：状态档写停在 run）" if status.get("failed_step") == "run" else "")
            + f"；清单 reports/{status['changes_file']}")



def _sites(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    if raw.startswith("@"):
        lines = Path(raw[1:]).read_text(encoding="utf-8").splitlines()
        return {x.split("#", 1)[0].strip() for x in lines if x.split("#", 1)[0].strip()}
    return {x.strip() for x in raw.split(",") if x.strip()}


# what tonight did with each update site, in seen.tsv's third column (export batch T7; the content session's handover
# point 2 reads the first two columns only)
SEEN_WORDS = {"ok": "导了", "failed": "导出失败", "alarm": "警报压着", "unanswered": "连不上", "same": "没变动"}


OWED = "export-owed.json"  # the sites changed and not exported yet (the review's F6): reports/, beside the status


def _owed(root: Path) -> list[str]:
    try:
        got = json.loads((root / "reports" / OWED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in got if isinstance(s, str)] if isinstance(got, list) else []


def _write_owed(root: Path, sites) -> None:
    path = root / "reports" / OWED
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".new")
    part.write_text(json.dumps(sorted(set(sites)), ensure_ascii=False), encoding="utf-8")
    part.replace(path)


def owe(root: Path, sites) -> None:
    """The sites a night's review found changed join those owed an export (the review's F6: a night whose export did
    not happen, or a site whose export failed, is exported on a later night, never forgotten)."""
    _write_owed(root, set(_owed(root)) | set(sites))


def export_list(status: dict, owed: list[str] | None = None) -> list[str]:
    """The sites tonight's export takes (design §7.4: the nightly script tells the export which): those owed an export
    (owe: changed tonight or on a night whose export did not happen) and those the change list says changed, none
    an alarm holds tonight (§7.5: an alarmed site is not exported tonight; it stays owed)."""
    held = set(status.get("alarms") or {})
    return sorted(s for s in set(owed or []) | set(status.get("sites_changed") or []) if s not in held)


def _results(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path is not None and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                site, what = line.split("\t", 1)
                out[site.strip()] = what.strip()
    return out


def _previous_seen(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return {x.split("\t")[0]: x.split("\t")[1] for x in lines if x.count("\t") >= 2}


def seen_rows(status: dict, results: dict[str, str], previous: dict[str, str] | None = None) \
        -> list[tuple[str, str, str]]:
    """seen.tsv's rows, by site key (export batch T7; design §7.5 「每站最后一次看过」): the site, its last answer in
    Taipei time (YYYY-MM-DD HH:MM; '' when it never answered), and what tonight did: an alarm held it, its export
    failed, it was exported, it did not answer tonight (its last answer is older than tonight's start), or it did not
    change. A site an alarm held or whose export failed keeps the time the previous seen.tsv gave it (the review's
    F5: the export the content stage holds of it is that night's, not tonight's)."""
    night = status.get("night_started")
    start = datetime.fromisoformat(night) if night else None
    alarms = set(status.get("alarms") or {})
    rows = []
    for site, at in sorted((status.get("sites_seen") or {}).items()):
        when = datetime.fromisoformat(at).astimezone(TAIPEI) if at else None
        if site in alarms:
            what = "alarm"
        elif results.get(site) in ("ok", "failed"):
            what = results[site]
        elif when is None or (start is not None and when < start):
            what = "unanswered"
        else:
            what = "same"
        shown = when.strftime("%Y-%m-%d %H:%M") if when else ""
        if what in ("alarm", "failed"):
            shown = (previous or {}).get(site, "")
        rows.append((site, shown, SEEN_WORDS[what]))
    return rows


def _write_seen(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """seen.tsv, whole: written to a .new beside it, then renamed over it (the content stage never reads half)."""
    part = path.with_name(path.name + ".new")
    part.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    part.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.archive.update",
                                 description="定期更新：挑今晚要看的列表和网页、放回待抓（不连网；抓取照旧用 nknu archive run）")
    ap.add_argument("--dir", required=True, type=Path, help="存档目录（frontier.db 所在）")
    ap.add_argument("action", choices=("plan", "put-back", "go-live", "review", "sites", "fail", "export-list", "seen"),
                    help="plan 只读、只算数；put-back 放回今晚要看的；go-live 第一晚以前做一次；review 算这一晚的变动清单；"
                         "sites 印出定期更新的网站键（逗号分隔）；fail --step 记下这一晚停在哪一步；export-list 印出今晚要导出的"
                         "网站（一行一个：有变动的，警报压着的不算）；seen --results --out 写 seen.tsv（每站最后一次看过）")
    ap.add_argument("--results", type=Path, default=None,
                    help="seen 用：今晚导出的结果（网站键<TAB>ok/failed/…，export_sites.sh 的 EXPORT_RESULTS）")
    ap.add_argument("--out", type=Path, default=None, help="seen 用：写到哪里（例如 /work/archive-export/sites/seen.tsv）")
    ap.add_argument("--step", default=None, help="fail 用：停在哪一步")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="plan、put-back 用：当作哪一天挑（YYYY-MM-DD，台北日期；默认今天）")
    ap.add_argument("--sites", default=None,
                    help="只挑这些网站键（逗号分隔，或 @档名：一行一个）；之后的 archive run 给同样的 --sites。go-live 用："
                         "只把这些网站列进定期更新（试一晚）")
    a = ap.parse_args(argv)
    root = a.dir
    if not (root / "frontier.db").is_file():
        print(f"{root} 里没有 frontier.db（--dir 打错了？）：什么都没做", file=sys.stderr)
        return 1
    sites = _sites(a.sites)
    if a.action in ("export-list", "seen"):
        try:
            status = json.loads((root / "reports" / STATUS).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"读不到这一晚的状态档（{type(exc).__name__}）：先跑 review", file=sys.stderr)
            return 1
        if a.action == "export-list":
            print("\n".join(export_list(status, _owed(root))))
            return 0
        if a.out is None:
            print("seen 要 --out", file=sys.stderr)
            return 1
        results = _results(a.results)
        _write_seen(a.out, seen_rows(status, results, _previous_seen(a.out)))
        done = {s for s, w in results.items() if w in ("ok", "unchanged")}
        left = sorted(set(_owed(root)) - done)
        _write_owed(root, left)
        # the night's export in the status file (the review's F8: the dashboard's mark and a person read it there)
        status["export"] = {"at": _local(time.time()), "exported": sorted(s for s, w in results.items() if w == "ok"),
                            "unchanged": sorted(s for s, w in results.items() if w == "unchanged"),
                            "failed": sorted(s for s, w in results.items() if w not in ("ok", "unchanged")),
                            "owed": left}
        _write_status(root, status)
        print(f"写了 {a.out}：{len(status.get('sites_seen') or {})} 个网站；还欠导出 {len(left)} 个")
        return 0
    if a.action == "fail":
        status: dict = {}
        try:
            f = Frontier(root / "frontier.db", readonly=True)
            try:
                night = f.meta(NIGHT_KEY)
            finally:
                f.close()
            old = json.loads((root / "reports" / STATUS).read_text(encoding="utf-8"))
            if a.step not in ("backup", "put-back") and night and old.get("night_started") == _local(float(night)):
                status = old  # tonight's review is kept beside where the night stopped
        except (OSError, ValueError, sqlite3.Error):
            pass
        path = _write_status(root, {**status, "failed_step": a.step or "?", "failed_at": _local(time.time())})
        print(f"状态档写了停在「{a.step}」：{path}")
        return 0
    if a.action == "sites":
        f = Frontier(root / "frontier.db", readonly=True)
        try:
            raw = f.meta(crawl_mod.UPDATE_SITES_KEY)
        finally:
            f.close()
        if not raw:
            print("还没做过 go-live：没有定期更新的网站", file=sys.stderr)
            return 1
        print(",".join(json.loads(raw)))
        return 0
    if a.action == "plan":
        f = Frontier(root / "frontier.db", readonly=True)
        try:
            closable = ({r["list_id"] for r in postback.Chains(f, root).stale_looks()}
                        if _has(f, "list_look") else set())
            last = f.meta(NIGHT_KEY)
            _report(f, select(f, root, sites=sites, closable=closable, today=a.today,
                              last_night=float(last) if last else None))
        finally:
            f.close()
        return 0
    try:
        lock = crawl_mod.hold_run_lock(root)
    except crawl_mod.AlreadyRunning as exc:
        print(f"archive run 正在用这个目录（pid {exc.pid}）：等它结束或先 nknu archive stop，再放回", file=sys.stderr)
        return 1
    try:
        f = Frontier(root / "frontier.db")
        try:
            if a.action == "go-live":
                keys = sorted(update_keys(f))
                if sites is not None:
                    # the first real night on one small site (the owner 11-16: 「挑一个小站试一晚」): only these are under
                    # update, so the nightly script's put-back, sites and run take them alone, and a run's start lets go
                    # of no other site's capped rows (_release_caps: every other site keeps the round's caps). A later
                    # go-live without --sites names every round-one site
                    unknown = sorted(sites - set(keys))
                    if unknown:
                        print("不是第一轮的网站键（打错了？）：" + "、".join(unknown) + "；什么都没做", file=sys.stderr)
                        return 1
                    keys = sorted(sites)
                miss: dict[str, int] = {}
                n = postback.backfill_list_rows(f, root, counts=miss)
                f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps(keys))
                print(f"列表的每一行读进 list_row：{n} 行；{len(keys)} 个网站键进入定期更新（每晚的增长上限）"
                      + (f"；存档里读不到的列表页 {miss['unread']} 页（WARC 没挂上？读不到的列表，第一晚每一行都当新的）"
                         if miss.get("unread") else ""))
                return 0
            if a.action == "review":
                if not f.meta(NIGHT_KEY):
                    print("还没放回过（put-back）：没有这一晚可以算", file=sys.stderr)
                    return 1
                try:
                    status, done = review(f, root)
                except RuntimeError as exc:
                    print(str(exc), file=sys.stderr)
                    return 1
                if done:
                    owe(root, status.get("sites_changed") or [])  # until exported (the review's F6)
                print(("" if done else "这一晚已经算过了，照上次的：") + _say(status))
                return 3 if status.get("failed_step") == "run" else 0
            if not f.meta(crawl_mod.UPDATE_SITES_KEY):
                # list_row empty: every row news, every look a whole walk (review of 7f12e29..8474164)
                print("还没做过 go-live（列表的每一行还没读进 list_row）：先跑 go-live，再放回", file=sys.stderr)
                return 1
            last = f.meta(NIGHT_KEY)
            start = repr(time.time())
            closed = postback.Chains(f, root).close_stale_looks()
            t = select(f, root, sites=sites, today=a.today, last_night=float(last) if last else None)
            got = put_back(f, root, t)
            # the review's windows follow one another: the next opens where the last review ended (what a person's
            # run fetched in the day is in it); a night never reviewed (its run or review did not end) keeps its
            # window open, so the next review takes both. Written once tonight's rows are back (rule 1's last night
            # stays the last night's if put-back stops midway)
            if last is None:
                f.meta(FROM_KEY, start)
            elif f.meta(REVIEWED_KEY) == last:
                f.meta(FROM_KEY, f.meta(REVIEWED_TO) or start)
            elif not f.meta(FROM_KEY):
                f.meta(FROM_KEY, last)
            f.meta(NIGHT_KEY, start)  # the night: its run_end, rule 1's last night for tomorrow
            f.meta(PUT_KEY, json.dumps({k: got.get(k, 0) for k in BATCHES}))
            _report(f, t)
            print(f"放回去了：列表 {got['looks']} 个、按网址翻页的第 1 页 {got['looks_get']} 个、翻到底的 "
                  f"{got['walks'] + got['walks_get']} 个、从头再翻的 {got['redo']} 个、其余的页 "
                  f"{sum(got[k] for k in ('redo_get', 'homes', 'recent', 'standing', 'follow', 'again'))} 页"
                  + (f"；没放成 {got['not_opened']} 个" if got["not_opened"] else "")
                  + (f"；结束了 {closed} 个上次没回来的查看" if closed else ""))
            return 0
        finally:
            f.close()
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
