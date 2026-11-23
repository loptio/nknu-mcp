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
  nightly script, deploy/lab-crawler/lab/nightly_update.sh, on a failing step).
- thin-back (11-17): holds run.lock; marks again what reconcile judged of a page's words (thin, in_image) where
  a night's fetch dropped it (Frontier.thin_back); --dry-run only counts (read only); --out writes the sites to
  export again, one per line.
- round2 (11-17, design 2026-11-17-round2-batch2 §三.2): holds run.lock; --sites: the later rows of round two's batch
  sites that the list places this round go pending (batch.promote); --dry-run counts in a rolled-back
  transaction; --undo puts back what nothing fetched since; --close ends the batch.
- no-area (11-17): holds run.lock; the pages stored as got whose answer lacks the content area their profile
  requires are flagged no_area and put back (batch.no_area_back); --dry-run only counts; --out the sites.

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

11-19, the sites read again one layer deep (round1.Site.revisit: the off-campus unit sites of round two's batch,
crawled once): on the night REVISIT_WEEKDAY names (a monthly site on the month's first), or any night the last review
held one of its rows, a site's home (its seeds in the list, the seeds its batch or a sitemap gave it, and where a 3xx
of one leads) and the pages its home links go back (revisit), about 500 requests a week where reading every page of
these sites would be 2,000. A new post a home links is a new row, fetched as any new row within the site's own caps.
The same run fetches them (`sites` adds tonight's keys, REVISIT_KEY), the same review judges them, the night exports
those that changed (_night_sites). They are never in UPDATE_SITES_KEY: no nightly batch takes their rows, a run holds
them by their own caps, and a run reconciles them. revisit-back: the same rows put back by hand (run.lock), every
revisit site or --sites, whatever the day, no night started; --dry-run counts, read only.
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

from nknu.archive import batch, export, outcome, postback, profiles, round1, scope, watch
from nknu.archive import crawler as crawl_mod
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
# 11-19: the night that starts on this weekday (date.weekday(): Saturday, a week's posts behind it) reads the revisit
# sites again; a monthly one on the month's first such night
REVISIT_WEEKDAY = 5
REVISIT_KEY = "update:revisit_tonight"  # the revisit sites tonight's put-back took (a JSON list): `sites` adds them
# the seed sources that are a revisit site's way in: its batch's (batch.promote), a sitemap a person gave it
REVISIT_SOURCES = ("round2:%", "sitemap%")
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
    revisit: list[int] = field(default_factory=list)  # the revisit sites due tonight: home, the pages it links
    revisit_sites: list[str] = field(default_factory=list)  # their keys (the run is given them too)
    revisit_error: str = ""  # why tonight's revisit sites could not be chosen (the night goes on without them)
    skipped: Counter = field(default_factory=Counter)  # lists left out tonight, by reason
    why: Counter = field(default_factory=Counter)  # standing pages by why: week, month (deleted), daily, often

    def ids(self) -> list[int]:
        return [*self.looks, *self.looks_get, *self.homes, *self.walks, *self.walks_get, *self.redo, *self.redo_get,
                *self.recent, *self.standing, *self.follow, *self.again, *self.revisit]


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


def _plan() -> round1.Plan:
    """The site list (config/archive_round1.json): which sites are read again weekly or monthly."""
    return round1.load()


def revisit_sites(plan: round1.Plan) -> dict[str, str]:
    """The sites read again one layer deep, and how often (round1.Site.revisit)."""
    return {s.site: s.revisit for s in plan.sites.values() if s.revisit}


def revisit_due(plan: round1.Plan, today: date, held: set[str] | frozenset[str] = frozenset()) -> set[str]:
    """The revisit sites a night takes: the weekly ones on REVISIT_WEEKDAY, the monthly ones on the month's first;
    one the last review held a row of (an alarm), any night."""
    sites = revisit_sites(plan)
    due = ({k for k, how in sites.items() if how == "weekly" or today.day <= 7}
           if today.weekday() == REVISIT_WEEKDAY else set())
    return due | (set(held) & set(sites))


def _moves(frontier: Frontier, row) -> list:
    """A page and where it leads when it moves (a 3xx, a script or a refresh), as far as a redirect chain goes."""
    out, at = [row], row
    for _hop in range(outcome.MAX_HOPS):
        moved = ((at["status"] is not None and 300 <= at["status"] < 400)
                 or "js_redirect" in (at["flags"] or "").split(","))
        to = frontier.row(at["final_url"]) if moved and at["final_url"] else None
        if to is None or to["id"] in {r["id"] for r in out}:
            break
        out.append(to)
        at = to
    return out


def _is_sitemap(url: str) -> bool:
    return url.split("?", 1)[0].lower().endswith(".xml")


def revisit_rows(frontier: Frontier, root: Path, plan: round1.Plan, sites) -> dict[str, list[int]]:
    """Each site's rows a revisit takes: its home (the list's seeds, the seeds of REVISIT_SOURCES; where one moves
    to; a seed that is a sitemap, and the sitemaps it names) and the pages those link (the link table), its own, got
    or failed; and its rows the last review held. 性别所's old Wix site has no home in the archive: its way in is its
    sitemap, whose three sitemaps name every post, so its month's revisit reads each post again (about 590 pages)."""
    chains = postback.Chains(frontier, root)
    held = (json.loads(frontier.meta(watch.HELD_KEY) or "null") or {"ids": []})["ids"]
    like = " OR ".join("s.source LIKE ?" for _ in REVISIT_SOURCES)
    out: dict[str, list[int]] = {}
    for key in sorted(sites):
        site = plan.site(key)
        starts: dict[int, object] = {}
        found = [chains._fetched_row(scope.canonical(u) or u) for u in (site.seeds if site is not None else ())]
        found += frontier.q(f"SELECT u.* FROM seed_src s JOIN url u ON u.url = s.url WHERE u.owner_site = ? AND ({like}) "
                            "ORDER BY u.id", (key, *REVISIT_SOURCES))
        for row in found:
            for r in (_moves(frontier, row) if row is not None else ()):
                if r["owner_site"] == key:
                    starts.setdefault(r["id"], r)
        for r in [r for r in starts.values() if r["kind"] == "file" and _is_sitemap(r["url"])]:
            for (dst,) in frontier.q("SELECT dst FROM link WHERE src = ?", (r["id"],)):
                row = chains._fetched_row(dst) if _is_sitemap(dst) else None
                if row is not None and row["kind"] == "file" and row["owner_site"] == key:
                    starts.setdefault(row["id"], row)
        ids = [i for i, r in starts.items() if r["state"] in ("done", "failed")]
        linked: set[str] = set()
        for i in range(0, len(starts), 500):
            part = sorted(starts)[i:i + 500]
            linked |= {r[0] for r in frontier.q(f"SELECT dst FROM link WHERE src IN ({_in(part)})", part)}
        for dst in sorted(linked):
            row = chains._fetched_row(dst)
            if (row is not None and row["id"] not in starts and row["owner_site"] == key and row["kind"] == "page"
                    and row["state"] in ("done", "failed")):
                ids.append(row["id"])
        for i in held:
            row = frontier.row_by_id(i)
            if row is not None and row["owner_site"] == key and row["state"] in ("done", "failed"):
                ids.append(i)
        if ids:
            out[key] = sorted(set(ids))
    return out


def _plan_or_none() -> round1.Plan | None:
    """The site list for a night; None, said aloud, when it cannot be read: the night goes on without the revisit
    sites, never stops over them."""
    try:
        return _plan()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"网站名单读不到（{type(exc).__name__}: {exc}）：这一晚照常，不带每周再看一次的校外单位网站", file=sys.stderr)
        return None


def _revisit_back(frontier: Frontier, root: Path, plan: round1.Plan, sites: set[str], *, write: bool) -> int:
    """revisit-back: the revisit rows of these sites put back (write) or counted, a line a site."""
    rows = revisit_rows(frontier, root, plan, sites)
    total = 0
    for key in sorted(sites):
        ids = rows.get(key, [])
        n = sum(1 for i in ids if frontier.put_back(i, "update_revisit")) if write else len(ids)
        total += n
        print(f"{key}\t{n}")
    print(("放回去了" if write else "会放回") + f"：{len(rows)} 个网站、{total} 页（首页和首页链到的页）"
          + ("；之后 archive run --sites 给同样这些网站" if write else "；没改账本"))
    return 0


def _daily(today: date) -> bool:
    md = f"{today:%m-%d}"
    return any(a <= md <= b for a, b in DAILY_PERIODS)


def select(frontier: Frontier, root: Path, *, sites: set[str] | None = None,
           closable: set[int] | frozenset[int] = frozenset(), today: date | None = None,
           last_night: float | None = None, now: float | None = None, plan: round1.Plan | None = None) -> Tonight:
    """Tonight's lists and pages (the module's docstring), each url row in one batch at most. sites: only these run
    keys (the run that follows is given the same: `archive run --sites`); closable: looks put-back will close first
    (Chains.stale_looks), counted as closed; today: the night's date (Asia/Taipei); last_night: the last night's start
    (rule 1); now: rule 3's clock; plan: the site list, for the revisit sites (none: no revisit site is taken)."""
    t = Tonight()
    today = today or datetime.now(TAIPEI).date()
    now = time.time() if now is None else now
    n = today.toordinal()
    if sites is not None:
        r1 = set(sites)
    else:  # the sites go-live named (the run fetches these: `update sites`), else round one's
        named = frontier.meta(crawl_mod.UPDATE_SITES_KEY)
        r1 = set(json.loads(named)) if named else update_keys(frontier)
    again = revisit_sites(plan) if plan is not None else {}
    asked = set(r1)
    r1 -= set(again)  # a revisit site is no update site: no nightly batch takes its rows
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
    if plan is not None and again:
        try:
            held = {row["owner_site"] for i in (json.loads(frontier.meta(watch.HELD_KEY) or "null") or {"ids": []})["ids"]
                    if (row := frontier.row_by_id(i)) is not None}
            due = revisit_due(plan, today, held)
            if sites is not None:
                due &= asked
            taken = set(t.ids())
            for key, ids in revisit_rows(frontier, root, plan, due).items():
                t.revisit_sites.append(key)
                t.revisit += [i for i in ids if i not in taken]
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            # the night never stops over these sites: it goes on without them, and says so (_report)
            t.revisit, t.revisit_sites, t.revisit_error = [], [], f"{type(exc).__name__}: {exc}"
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
           "again", "revisit", "not_opened")


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
                           ("follow", tonight.follow, "update_follow"), ("again", tonight.again, "update_again"),
                           ("revisit", tonight.revisit, "update_revisit")):
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
    if t.revisit_sites:
        print(f"轮到再看一次的校外单位网站 {len(t.revisit_sites)} 个：首页和首页链到的页 {len(t.revisit)} 页"
              "（算在上面的总数里）")
    if t.revisit_error:
        print(f"每周再看一次的校外单位网站这一晚没挑成（{t.revisit_error}）：这一晚照常，不带它们", file=sys.stderr)
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


def _revisit_keys() -> list[str]:
    """The revisit sites of the list; none when the list cannot be read (the night's own sites stay)."""
    try:
        return sorted(revisit_sites(_plan()))
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _seen(frontier: Frontier) -> dict[str, str | None]:
    """Each update site's last answer (design §7.5: 「每站最后一次看过」; the export's 资料截至 stays the last export's)."""
    keys = json.loads(frontier.meta(crawl_mod.UPDATE_SITES_KEY) or "[]")
    keys += [k for k in _revisit_keys() if k not in keys]  # 11-19: the revisit sites, whether tonight was theirs
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
                  "sites_seen": _seen(frontier), "export": None,  # the seen step writes it after the export
                  "revisit_tonight": (tonight := json.loads(frontier.meta(REVISIT_KEY) or "[]")),
                  "revisit_idle": [k for k in _revisit_keys() if k not in tonight]}
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
SEEN_WORDS = {"ok": "导了", "failed": "导出失败", "held": "导出拦下", "alarm": "警报压着", "unanswered": "连不上",
              "same": "没变动", "idle": "没轮到"}


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


def _owed_by_night(sites, only: set[str] | None) -> list[str]:
    """Owed means owed by the night: only a site the night's export can take (export_list's `only`, the sites under
    periodic update) is owed; None (the update sites cannot be read) drops nothing. 11-18 (爬虫judge): a round-two site
    a day's run fetched is in the night's change list, the night never exports it, so it joined the owed list and
    never left it (33 sites on 11-18, all exported by day)."""
    return sorted(s for s in set(sites) if only is None or s in only)


def owe(root: Path, sites) -> None:
    """The sites a night's review found changed join those owed an export (the review's F6: a night whose export did
    not happen, or a site whose export failed, is exported on a later night, never forgotten); a site not under
    periodic update is never owed, and one owed from before is let go here (_owed_by_night)."""
    _write_owed(root, _owed_by_night(set(_owed(root)) | set(sites), _night_sites(root)))


def export_list(status: dict, owed: list[str] | None = None, only: set[str] | None = None) -> list[str]:
    """The sites tonight's export takes (design §7.4: the nightly script tells the export which): those owed an export
    (owe: changed tonight or on a night whose export did not happen) and those the change list says changed, none
    an alarm holds tonight (§7.5: an alarmed site is not exported tonight; it stays owed). only: the sites under
    periodic update (crawl_mod.UPDATE_SITES_KEY), when known: a round-two batch's site a day's run fetched is in the
    change list too, and is exported when its batch is done, not by the night (design 2026-11-17-round2-batch2 §三.5)."""
    held = set(status.get("alarms") or {})
    return sorted(s for s in set(owed or []) | set(status.get("sites_changed") or [])
                  if s not in held and (only is None or s in only))


def _update_sites(root: Path) -> set[str] | None:
    """The sites under periodic update (meta UPDATE_SITES_KEY, read only); None when it cannot be read (no filter)."""
    try:
        f = Frontier(root / "frontier.db", readonly=True)
    except (OSError, sqlite3.Error):
        return None
    try:
        raw = f.meta(crawl_mod.UPDATE_SITES_KEY)
    finally:
        f.close()
    try:
        got = json.loads(raw) if raw else None
    except ValueError:
        return None
    return {x for x in got if isinstance(x, str)} if isinstance(got, list) else None


def _night_sites(root: Path) -> set[str] | None:
    """The sites a night may export: those under periodic update and the revisit sites (11-19: one of them that
    changed on its night, or by a person's run, is exported by the night); None when the update sites cannot be read."""
    got = _update_sites(root)
    return None if got is None else got | set(_revisit_keys())


def _result_lines(path: Path | None) -> dict[str, list[str]]:
    """EXPORT_RESULTS's lines by site: the word (ok, unchanged, failed, not_ready, held) and, for held, its reason."""
    out: dict[str, list[str]] = {}
    if path is not None and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                site, *rest = line.split("\t")
                out[site.strip()] = [x.strip() for x in rest]
    return out


def _results(path: Path | None) -> dict[str, str]:
    return {site: rest[0] for site, rest in _result_lines(path).items()}


def _held(path: Path | None) -> dict[str, str]:
    """The sites whose export was held (export_sites.sh, 11-17 爬虫judge: a copy of the 10-24 archive went) and why."""
    return {site: (rest[1] if len(rest) > 1 else "") for site, rest in _result_lines(path).items() if rest[0] == "held"}


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
    failed, its export was held (a copy of the 10-24 archive went, 11-17), it was exported, it did not answer tonight
    (its last answer is older than tonight's start), or it did not change; a revisit site whose night it was not
    (status revisit_idle, 11-19) says so, with its last answer. A site an alarm held or whose export failed
    or was held keeps the time the previous seen.tsv gave it (the review's F5: the export the content stage holds of it
    is that night's, not tonight's)."""
    night = status.get("night_started")
    start = datetime.fromisoformat(night) if night else None
    alarms = set(status.get("alarms") or {})
    idle = set(status.get("revisit_idle") or ())
    rows = []
    for site, at in sorted((status.get("sites_seen") or {}).items()):
        when = datetime.fromisoformat(at).astimezone(TAIPEI) if at else None
        if site in alarms:
            what = "alarm"
        elif results.get(site) in ("ok", "failed", "held"):
            what = results[site]
        elif site in idle:
            what = "idle"
        elif when is None or (start is not None and when < start):
            what = "unanswered"
        else:
            what = "same"
        shown = when.strftime("%Y-%m-%d %H:%M") if when else ""
        if what in ("alarm", "failed", "held"):
            shown = (previous or {}).get(site, "")
        rows.append((site, shown, SEEN_WORDS[what]))
    return rows


def _write_seen(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """seen.tsv, whole: written to a .new beside it, then renamed over it (the content stage never reads half)."""
    part = path.with_name(path.name + ".new")
    part.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    part.replace(path)


def _thin_back(marked: dict[str, int], out: Path | None, dry: bool) -> int:
    """thin-back's report (Frontier.thin_back, 11-17): the pages per site, and the sites to export again written to
    out (one per line; empty when none), for export_sites.sh."""
    total = sum(marked.values())
    verb = "要标回" if dry else "标回了"
    print(f"{verb} {total} 页（{len(marked)} 个网站）" + ("：" + "、".join(f"{s} {n}" for s, n in sorted(marked.items()))
                                                       if marked else ""))
    if out is not None:
        out.write_text("".join(f"{s}\n" for s in sorted(marked)), encoding="utf-8")
    return 0


def _round2(f: Frontier, a, sites: set[str] | None) -> int:
    """round2 (batch.py; design 2026-11-17-round2-batch2 §三.2): promote, --undo or --close, each site's counts."""
    if a.close:
        batch.close(f, sorted(sites) if sites else None)
        left = sorted(batch.open_sites(f))
        print("第二轮这一批关掉了" + (f"；还开着：{'、'.join(left)}" if left else "；没有开着的了"))
        return 0
    if not sites:
        print("round2 要 --sites（这一批的网站键，逗号分隔）", file=sys.stderr)
        return 1
    verb = "会" if a.dry_run else ""
    if a.undo:
        back = batch.undo(f, sorted(sites), write=not a.dry_run)
        for site, n in sorted(back.items()):
            print(f"{site}：{verb}放回 {n} 行（升级以后没抓过的）")
        return 0
    try:
        got = batch.promote(f, round1.load(), sorted(sites), write=not a.dry_run)
    except ValueError as exc:
        print(f"{exc}：什么都没改", file=sys.stderr)
        return 1
    for site, c in sorted(got.items()):
        kinds = "、".join(f"{_KIND_WORD.get(k, k)} {n}" for k, n in sorted(c.promoted.items())) or "0"
        held = "、".join(f"{k} {n}" for k, n in sorted(c.held.items())) or "0"
        print(f"{site}：{verb}升成待抓 {kinds}；撞上限 {held}；仍留下一轮 {c.left}；已抓到的改成这一轮 {c.retiered}；"
              f"旧来源的深度改从 1 算 {c.rebased}")
    return 0


_KIND_WORD = {"page": "网页", "file": "附件", "image": "图片"}


def js_take(root: Path, sites: set[str], crawler=None) -> dict[str, tuple[int, int]]:
    """js-take (the JS sites' handoff, item ④; plan 2026-11-17-jsfetch-integration task 3): each page of these sites
    whose browser look shows something of its own (jsfetch.classify: links or text), its look the export gives
    (jsfetch.for_export: the newest complete one that added something) read for what it links (Crawler.take_rendered:
    pending by the rules, page_res added to). Run between jsfetch run and the next archive run, holding run.lock.
    Returns (pages read, url rows added) by site."""
    from nknu.archive import jsfetch

    crawler = crawler or crawl_mod.Crawler(root, sites=sorted(sites))
    f = crawler.frontier
    if not f.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='js_render'"):
        return {}
    out: dict[str, tuple[int, int]] = {}
    for uid, kind in sorted(jsfetch.classify(f, sites).items()):
        if kind == "none":
            continue
        look, _why = jsfetch.for_export(f, uid)
        row = f.row_by_id(uid)
        if look is None or row is None or look["warc"] is None or look["warc_offset"] is None:
            continue
        try:
            _url, html = jsfetch.read_rendered(Path(root), look["warc"], look["warc_offset"])
        except (OSError, ValueError):
            continue
        added = crawler.take_rendered(row, html, look["warc"], look["warc_offset"])
        site = row["owner_site"] or row["site"]
        pages, total = out.get(site, (0, 0))
        out[site] = (pages + 1, total + added)
    return out


def _no_area(marked: dict[str, int], out: Path | None, dry: bool) -> int:
    """no-area's report (batch.no_area_back, 11-17): the pages per site, and the sites to export again written to out."""
    verb = "要标出" if dry else "标出、放回重抓了"
    print(f"{verb} {sum(marked.values())} 页（{len(marked)} 个网站）"
          + ("：" + "、".join(f"{s} {n}" for s, n in sorted(marked.items())) if marked else ""))
    if out is not None:
        out.write_text("".join(f"{s}\n" for s in sorted(marked)), encoding="utf-8")
    return 0


# every column that points at a stored answer: the pages' own, the button presses' (and the page a press was made
# from), and the answers a later night replaced
_WARC_COLS = (("url", "warc"), ("action", "warc"), ("action", "form_src_warc"), ("url_version", "warc"))


def warcs_named(conn: sqlite3.Connection) -> list[str]:
    """The WARC files the ledger's stored answers are in, as it names them (under the archive: warc/<year>/<month>/
    <day>/<file>, so the last by name is the newest). A table an older ledger does not have is passed over."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    named: set[str] = set()
    for table, col in _WARC_COLS:
        if table in have:
            named.update(r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL"))
    return sorted(named)


def warc_there(root: Path) -> tuple[int, str]:
    """Whether the WARC folder under root is the one the ledger's answers were stored in (11-23, the folder moved to
    another disk: Docker makes an empty folder for a mount whose path is wrong, and a folder that is there and can be
    written is all the night's own check asked). 0: every file the ledger names is there. 1: the newest is not, which
    is an empty or a half-copied folder, or one a later night did not write in: the night must not run on it. 2: the
    newest is there and an older one is not, a hole in the right folder. With what to say."""
    conn = sqlite3.connect(f"file:{root / 'frontier.db'}?mode=ro", uri=True)
    try:
        named = warcs_named(conn)
    finally:
        conn.close()
    if not named:
        return 0, "账本里还没有存下来的网页：存档资料夹不用查"
    gone = [w for w in named if not (root / w).is_file()]
    if not gone:
        return 0, f"账本记的存档档 {len(named)} 个都在（最旧 {named[0]}，最新 {named[-1]}）"
    if named[-1] in gone:
        return 1, (f"账本记的存档档 {len(named)} 个，{root / 'warc'} 里少了 {len(gone)} 个，最新的 {named[-1]} 也不在："
                   "这不是爬虫存档的那个资料夹（挂载的路径不对，或搬的时候没搬齐）")
    return 2, f"账本记的存档档 {len(named)} 个，少了 {len(gone)} 个旧的（例：{gone[0]}）；最新的 {named[-1]} 在"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.archive.update",
                                 description="定期更新：挑今晚要看的列表和网页、放回待抓（不连网；抓取照旧用 nknu archive run）")
    ap.add_argument("--dir", required=True, type=Path, help="存档目录（frontier.db 所在）")
    ap.add_argument("action", choices=("plan", "put-back", "go-live", "review", "sites", "fail", "export-list", "seen",
                                       "thin-back", "round2", "no-area", "js-take", "revisit-back", "warc-there"),
                    help="plan 只读、只算数；put-back 放回今晚要看的；go-live 第一晚以前做一次；review 算这一晚的变动清单；"
                         "sites 印出定期更新的网站键（逗号分隔）；fail --step 记下这一晚停在哪一步；export-list 印出今晚要导出的"
                         "网站（一行一个：有变动的，警报压着的不算）；seen --results --out 写 seen.tsv（每站最后一次看过）；"
                         "thin-back 把每晚重抓冲掉的「页面几乎是空的」「内容在图片里」标回来（--dry-run 只数），--out 写要重导的网站；"
                         "round2 --sites 把第二轮这一批网站的「下一轮」行升成这一轮（--dry-run 只数，--undo 撤回，--close 关掉这一批）；"
                         "no-area 把存成「抓到了」、其实学校这次没回内容那一块的网页标出来、放回重抓（--dry-run 只数，--out 写要重导的网站）；"
                         "js-take --sites 把浏览器（python -m nknu.archive.jsfetch run）开过、显示出东西的网页，照它的显示版找链接、"
                         "排进待抓（之后再 archive run）；"
                         "revisit-back 手动把每周／每月再看一次的校外单位网站的首页和首页链到的页放回（不看星期几；"
                         "--sites 只放这些，--dry-run 只数；不算一晚，之后 archive run --sites 同样这些）；"
                         "warc-there 只读：账本记的存档档（WARC）是不是都在这个存档目录里（退出码 0 都在；1 最新的不在，"
                         "这不是爬虫存档的那个资料夹；2 只少旧的）")
    ap.add_argument("--results", type=Path, default=None,
                    help="seen 用：今晚导出的结果（网站键<TAB>ok/failed/…，export_sites.sh 的 EXPORT_RESULTS）")
    ap.add_argument("--out", type=Path, default=None, help="seen 用：写到哪里（例如 /work/archive-export/sites/seen.tsv）；"
                    "thin-back 用：要重导的网站写到哪里（一行一个）")
    ap.add_argument("--dry-run", action="store_true", help="thin-back、round2、revisit-back 用：只数，不改账本")
    ap.add_argument("--undo", action="store_true", help="round2 用：撤回这些网站升级时改的行（之后没抓过的）")
    ap.add_argument("--close", action="store_true", help="round2 用：这一批结束（不给 --sites 就全部结束）")
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
    if a.action == "warc-there":
        code, said = warc_there(root)
        print(said)
        return code
    if a.action in ("export-list", "seen"):
        try:
            status = json.loads((root / "reports" / STATUS).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"读不到这一晚的状态档（{type(exc).__name__}）：先跑 review", file=sys.stderr)
            return 1
        if a.action == "export-list":
            print("\n".join(export_list(status, _owed(root), _night_sites(root))))
            return 0
        if a.out is None:
            print("seen 要 --out", file=sys.stderr)
            return 1
        results = _results(a.results)
        _write_seen(a.out, seen_rows(status, results, _previous_seen(a.out)))
        done = {s for s, w in results.items() if w in ("ok", "unchanged")}
        left = _owed_by_night(set(_owed(root)) - done, _night_sites(root))
        _write_owed(root, left)
        # the night's export in the status file (the review's F8: the dashboard's mark and a person read it there)
        status["export"] = {"at": _local(time.time()), "exported": sorted(s for s, w in results.items() if w == "ok"),
                            "unchanged": sorted(s for s, w in results.items() if w == "unchanged"),
                            "failed": sorted(s for s, w in results.items() if w not in ("ok", "unchanged")),
                            "held": _held(a.results), "owed": left}
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
            tonight = f.meta(REVISIT_KEY)
        finally:
            f.close()
        if not raw:
            print("还没做过 go-live：没有定期更新的网站", file=sys.stderr)
            return 1
        keys = json.loads(raw)
        # 11-19: and the revisit sites tonight's put-back took (the same run fetches them)
        print(",".join(keys + [k for k in json.loads(tonight or "[]") if k not in keys]))
        return 0
    if a.action == "thin-back" and a.dry_run:
        f = Frontier(root / "frontier.db", readonly=True)
        try:
            return _thin_back(f.thin_back(write=False), a.out, dry=True)
        finally:
            f.close()
    if a.action == "plan":
        f = Frontier(root / "frontier.db", readonly=True)
        try:
            closable = ({r["list_id"] for r in postback.Chains(f, root).stale_looks()}
                        if _has(f, "list_look") else set())
            last = f.meta(NIGHT_KEY)
            _report(f, select(f, root, sites=sites, closable=closable, today=a.today,
                              last_night=float(last) if last else None, plan=_plan_or_none()))
        finally:
            f.close()
        return 0
    if a.action == "revisit-back":
        try:
            plan = _plan()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"网站名单读不到（{type(exc).__name__}: {exc}）：什么都没做", file=sys.stderr)
            return 1
        known = revisit_sites(plan)
        unknown = sorted((sites or set()) - set(known))
        if unknown:
            print("不是每周／每月再看一次的网站键（打错了？）：" + "、".join(unknown) + "；什么都没做", file=sys.stderr)
            return 1
        if a.dry_run:
            f = Frontier(root / "frontier.db", readonly=True)
            try:
                return _revisit_back(f, root, plan, sites or set(known), write=False)
            finally:
                f.close()
    try:
        lock = crawl_mod.hold_run_lock(root)
    except crawl_mod.AlreadyRunning as exc:
        print(f"archive run 正在用这个目录（pid {exc.pid}）：等它结束或先 nknu archive stop，再放回", file=sys.stderr)
        return 1
    try:
        f = Frontier(root / "frontier.db")
        try:
            if a.action == "thin-back":
                return _thin_back(f.thin_back(write=True), a.out, dry=False)
            if a.action == "revisit-back":
                return _revisit_back(f, root, plan, sites or set(known), write=True)
            if a.action == "round2":
                return _round2(f, a, sites)
            if a.action == "no-area":
                return _no_area(batch.no_area_back(f, root, write=not a.dry_run), a.out, dry=a.dry_run)
            if a.action == "js-take":
                if not sites:
                    print("js-take 要 --sites（要 JavaScript 的网站键，逗号分隔）", file=sys.stderr)
                    return 1
                got = js_take(root, sites)
                for site, (pages, added) in sorted(got.items()):
                    print(f"{site}：读了 {pages} 页的显示版，新排进待抓 {added} 行")
                if not got:
                    print("没有要读的显示版（浏览器还没开过这些网站，或开出来的都没有自己的东西）")
                return 0
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
            t = select(f, root, sites=sites, today=a.today, last_night=float(last) if last else None,
                       plan=_plan_or_none())
            got = put_back(f, root, t)
            f.meta(REVISIT_KEY, json.dumps(t.revisit_sites))  # the run's sites tonight (`sites`); none: []
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
                  f"{sum(got[k] for k in ('redo_get', 'homes', 'recent', 'standing', 'follow', 'again', 'revisit'))} 页"
                  + (f"；没放成 {got['not_opened']} 个" if got["not_opened"] else "")
                  + (f"；结束了 {closed} 个上次没回来的查看" if closed else ""))
            return 0
        finally:
            f.close()
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
