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

- review: holds run.lock; the night's change list (watch.night: each row fetched since put-back against the answer it
  replaced) once per night (meta update:night_reviewed), in one transaction: reports/changes-<night>.csv and
  reports/update-status.json (counts, alarms, the sites whose export is due, the run's end). The export is listed,
  never run (the owner's timing, 11-16).
- sites: the update sites as one comma list (what the nightly script gives `archive run --sites`).
- fail --step X: the status file says the night stopped at step X (the nightly script, research/crawler_lab_2026-11-04/
  nightly_update.sh, on a failing step).

Only round-one sites. A list left out is counted with its reason: a walk cut off with presses waiting is resume()'s to
finish, a look still open waits for its end, a walk with no end waits for its monthly whole walk.

Stage 2 (plan 2026-11-16-update-stage-2 Task 3, design §7.3): shares keyed on the day's number (date.toordinal, Asia/
Taipei) and the row id, so a share is the same whichever night computes it and every share comes every WEEK or MONTH
days whatever the month's length 【Claude: the plan said weekday and day of month; February would skip two shares】:
- walks: the lists of tonight's monthly share walked whole (a list walked by button: restart_chain from its GET; a
  list paged by GET: every page put back), taken out of tonight's looks; a walk with no end too, on its day. Their
  row counts before are kept (meta update:walks_before) for the review's shrink warning (design §7.5).
- recent: the pages list rows link whose row date (export.bare_date) is within RECENT_DAYS, one share a week.
- standing: the standing pages (answered 200, not a list or a list's page, not a home or seed, not a platform detail
  page, not a page a list row links), one share a week; every night on DAILY_SITES in DAILY_PERIODS (speed-up rule 2);
  every other night when changed at their last OFTEN_CHANGES checks (rule 3, page_watch).
- follow (rule 1): what the lists and notices that changed since the last night's start link: standing pages, and
  files under a changed notice; not what a home links (a menu: its weekly turn is enough) 【Claude】.
"""

from __future__ import annotations

import argparse
import csv
import json
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
WALKS_KEY = "update:walks_before"  # tonight's whole walks: list id -> its rows before (the shrink warning)
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
SHRINK_MIN, SHRINK_SHARE = 10, 0.1  # design §7.5: a whole walk short of the last by max(10 rows, 10%): a warning
STATUS = "update-status.json"
TAIPEI = ZoneInfo("Asia/Taipei")
SAY = {"new": "新的", "changed": "改了", "same": "没变", "not_seen": "这次没看到", "not_got": "没拿到",
       "deleted": "学校删了", "back": "回来了"}


@dataclass
class Tonight:
    looks: list[int] = field(default_factory=list)  # list pages of lists walked by button
    looks_get: list[int] = field(default_factory=list)  # the first page of each list paged by GET
    homes: list[int] = field(default_factory=list)  # home and seed pages (and where a 3xx of one leads)
    walks: list[int] = field(default_factory=list)  # lists walked by button, walked whole tonight (their list page)
    walks_get: list[int] = field(default_factory=list)  # every page of the lists paged by GET walked whole tonight
    recent: list[int] = field(default_factory=list)  # pages of the last RECENT_DAYS' list rows, tonight's share
    standing: list[int] = field(default_factory=list)  # standing pages: tonight's share, rules 2 and 3
    follow: list[int] = field(default_factory=list)  # rule 1: linked from a list or notice that changed
    skipped: Counter = field(default_factory=Counter)  # lists left out tonight, by reason
    why: Counter = field(default_factory=Counter)  # standing pages by why: week, daily, often

    def ids(self) -> list[int]:
        return [*self.looks, *self.looks_get, *self.homes, *self.walks, *self.walks_get, *self.recent, *self.standing,
                *self.follow]


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
    r1 = update_keys(frontier) if sites is None else set(sites)
    open_looks = ({r[0] for r in frontier.q("SELECT list_id FROM list_look WHERE ended_at IS NULL")} - set(closable)
                  if _has(frontier, "list_look") else set())
    waiting = {r[0] for r in frontier.q("SELECT DISTINCT list_id FROM action WHERE kind NOT IN ('click', 'data') AND "
                                        "state IN ('pending', 'active')")}
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
        elif r["list_id"] % MONTH == n % MONTH:
            t.walks.append(r["list_id"])  # tonight's whole walk, a walk with no end too
        elif r["ended_by"] in (None, "walking"):
            t.skipped["walk_unfinished"] += 1
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
        elif first % MONTH == n % MONTH:  # tonight's whole walk: every page the group knows, a walk with no end too
            for i in sorted(group):
                g = frontier.row_by_id(i)
                if g is not None and g["kind"] == "page" and g["state"] in ("done", "failed") and g["owner_site"] in r1:
                    t.walks_get.append(i)
        elif not ended or any(w[0] in (None, "walking") for w in ended):
            t.skipped["walk_unfinished"] += 1
        else:
            t.looks_get.append(first)
    # a home inside a GET pager group (an SSO unit site's) stays a home, but for its first page or a whole walk
    taken = walked | set(t.looks_get) | set(t.walks_get)
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
    _pages(frontier, t, chains, r1, walked | grouped, today, n, last_night, now)
    return t


def _pages(frontier: Frontier, t: Tonight, chains: postback.Chains, r1: set[str], lists: set[int], today: date,
           n: int, last_night: float | None, now: float) -> None:
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
    for r in frontier.q("SELECT id, url, owner_site FROM url WHERE kind = 'page' AND tier IN (0, 1) AND state = 'done' "
                        "AND status = 200 AND depth > 0 AND coalesce(page_role, '') != 'list'"):
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
    daily = _daily(today)
    for i, site in standing.items():
        if i in taken:
            continue
        if i % WEEK == n % WEEK:
            t.why["week"] += 1
        elif daily and site in DAILY_SITES:
            t.why["daily"] += 1
        elif often.get(i) is not None and now - often[i] >= OFTEN_GAP_S:
            t.why["often"] += 1
        else:
            continue
        t.standing.append(i)
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
        if src not in lists and src not in notices:
            continue
        for (dst,) in frontier.q("SELECT dst FROM link WHERE src = ? ORDER BY dst", (src,)):
            if dst in menu:
                continue
            row = chains._fetched_row(dst)
            if row is None or row["id"] in taken:
                continue
            if row["id"] in standing or (src not in lists and row["kind"] == "file" and row["state"] == "done"
                                         and row["owner_site"] in r1):
                t.follow.append(row["id"])
                taken.add(row["id"])


BATCHES = ("looks", "looks_get", "walks", "walks_get", "homes", "recent", "standing", "follow", "not_opened")


def put_back(frontier: Frontier, root: Path, tonight: Tonight) -> Counter:
    """Tonight's looks opened, whole walks started and pages put back (a look that does not open, a page not done or
    failed, is counted not_opened). A whole walk of a list walked by button starts from its GET (restart_chain, a look
    on it closed first); its rows before are kept for the review (WALKS_KEY)."""
    chains = postback.Chains(frontier, root)
    got: Counter = Counter()
    for i in tonight.looks:
        got["looks" if chains.look(i) else "not_opened"] += 1
    for i in tonight.looks_get:
        got["looks_get" if chains.look_get(i) else "not_opened"] += 1
    before: dict[str, int | None] = {}
    for i in tonight.walks:
        w = frontier.q("SELECT rows FROM listwalk WHERE list_id = ?", (i,))
        before[str(i)] = w[0][0] if w else None
        chains.restart_chain(i, "update_walk", walk=True, keep_look=False)
        got["walks"] += 1
    frontier.meta(WALKS_KEY, json.dumps(before))
    for name, ids, why in (("walks_get", tonight.walks_get, "update_walk"), ("homes", tonight.homes, "update_home"),
                           ("recent", tonight.recent, "update_recent"),
                           ("standing", tonight.standing, "update_standing"),
                           ("follow", tonight.follow, "update_follow")):
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
          f"首页和起点 {len(t.homes)} 页；翻到底的列表 {len(t.walks)} 个、按网址翻页的整串 {len(t.walks_get)} 页；"
          f"最近 {RECENT_DAYS} 天的公告 {len(t.recent)} 页；常设页 {len(t.standing)} 页（轮到的 {t.why['week']}、"
          f"开学前后每天的 {t.why['daily']}、常改的 {t.why['often']}）；改了的列表和公告链到的 {len(t.follow)} 个；"
          f"共 {len(ids)} 个网址（之后有新东西才会再多）")
    if queues:
        print("按服务器：" + "、".join(f"{q} {n}" for q, n in queues.most_common()))
    if t.skipped:
        words = {"walk_unfinished": "上次翻页没有结尾（轮到它的那晚翻到底）",
                 "presses_waiting": "还有按钮等着按（下次 run 开头接着翻）", "look_open": "上次的查看还没结束",
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


def _run_end(frontier: Frontier, since: float) -> dict | None:
    """The last run_end the night's run wrote (event.at is UTC ISO, as watch._iso writes it)."""
    got = frontier.q("SELECT message FROM event WHERE code='run_end' AND at >= ? ORDER BY rowid DESC LIMIT 1",
                     (watch._iso(since),))
    if not got:
        return None
    try:
        return json.loads(got[0]["message"])
    except (TypeError, ValueError):
        return {"message": got[0]["message"]}


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
    """The night's change list and status file (the module's docstring); (status, done now). A night counted already
    is not counted again (page_watch's changes and gone would be counted twice): its status file is read back."""
    raw = frontier.meta(NIGHT_KEY)
    since = float(raw)
    now = time.time() if now is None else now
    if frontier.meta(REVIEWED_KEY) == raw and (root / "reports" / STATUS).is_file():
        return json.loads((root / "reports" / STATUS).read_text(encoding="utf-8")), False
    with write_first(frontier.conn, "review"):
        n = watch.night(frontier, root, since, now=now)
        frontier.meta(REVIEWED_KEY, raw)
    name = f"changes-{datetime.fromtimestamp(since, TAIPEI):%Y%m%d-%H%M}.csv"
    (root / "reports").mkdir(parents=True, exist_ok=True)
    with (root / "reports" / name).open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("site", "url", "kind", "change", "previous_at"))
        for c in n.rows:
            w.writerow((c.site, c.url, c.kind, c.change, c.previous_at or ""))
    status = {"started": _local(since), "reviewed": _local(now), "failed_step": None,
              "counts": dict(sorted(n.counts().items())), "alarms": n.alarms, "sites_changed": n.sites_changed,
              "changes_file": name, "run_end": _run_end(frontier, since),
              "put_back": json.loads(frontier.meta(PUT_KEY) or "{}"), "sites_seen": _seen(frontier),
              "export": "没有跑：导出的时机等用户定（11-16）；要导出的是 sites_changed"}
    _write_status(root, status)
    return status, True


def _say(status: dict) -> str:
    counts = status["counts"]
    words = "、".join(f"{SAY[k]} {counts.get(k, 0)}" for k in SAY)
    alarms = status["alarms"]
    end = (status.get("run_end") or {}).get("reason")
    return (f"这一晚（{status['started']} 起）：{words}；有变动的网站 {len(status['sites_changed'])} 个"
            + (f"；警报 {len(alarms)} 个：" + "、".join(f"{k}（{v}）" for k, v in alarms.items()) if alarms else "")
            + (f"；run 结束的原因 {end}" if end else "；这一晚没有 run 结束的纪录")
            + f"；清单 reports/{status['changes_file']}")



def _sites(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    if raw.startswith("@"):
        lines = Path(raw[1:]).read_text(encoding="utf-8").splitlines()
        return {x.split("#", 1)[0].strip() for x in lines if x.split("#", 1)[0].strip()}
    return {x.strip() for x in raw.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.archive.update",
                                 description="定期更新：挑今晚要看的列表和网页、放回待抓（不连网；抓取照旧用 nknu archive run）")
    ap.add_argument("--dir", required=True, type=Path, help="存档目录（frontier.db 所在）")
    ap.add_argument("action", choices=("plan", "put-back", "go-live", "review", "sites", "fail"),
                    help="plan 只读、只算数；put-back 放回今晚要看的；go-live 第一晚以前做一次；review 算这一晚的变动清单；"
                         "sites 印出定期更新的网站键（逗号分隔）；fail --step 记下这一晚停在哪一步")
    ap.add_argument("--step", default=None, help="fail 用：停在哪一步")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="plan、put-back 用：当作哪一天挑（YYYY-MM-DD，台北日期；默认今天）")
    ap.add_argument("--sites", default=None,
                    help="只挑这些网站键（逗号分隔，或 @档名：一行一个）；之后的 archive run 给同样的 --sites")
    a = ap.parse_args(argv)
    root = a.dir
    if not (root / "frontier.db").is_file():
        print(f"{root} 里没有 frontier.db（--dir 打错了？）：什么都没做", file=sys.stderr)
        return 1
    sites = _sites(a.sites)
    if a.action == "fail":
        path = _write_status(root, {"failed_step": a.step or "?", "failed_at": _local(time.time())})
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
                n = postback.backfill_list_rows(f, root)
                keys = sorted(update_keys(f))
                f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps(keys))
                print(f"列表的每一行读进 list_row：{n} 行；{len(keys)} 个网站键进入定期更新（每晚的增长上限）")
                return 0
            if a.action == "review":
                if not f.meta(NIGHT_KEY):
                    print("还没放回过（put-back）：没有这一晚可以算", file=sys.stderr)
                    return 1
                status, done = review(f, root)
                print(("" if done else "这一晚已经算过了，照上次的：") + _say(status))
                return 0
            if not f.meta(crawl_mod.UPDATE_SITES_KEY):
                # list_row empty: every row news, every look a whole walk (review of 7f12e29..8474164)
                print("还没做过 go-live（列表的每一行还没读进 list_row）：先跑 go-live，再放回", file=sys.stderr)
                return 1
            last = f.meta(NIGHT_KEY)
            f.meta(NIGHT_KEY, repr(time.time()))  # the night starts: its review takes what is fetched from now
            closed = postback.Chains(f, root).close_stale_looks()
            t = select(f, root, sites=sites, today=a.today, last_night=float(last) if last else None)
            got = put_back(f, root, t)
            f.meta(PUT_KEY, json.dumps({k: got.get(k, 0) for k in BATCHES}))
            _report(f, t)
            print(f"放回去了：列表 {got['looks']} 个、按网址翻页的第 1 页 {got['looks_get']} 个、首页和起点 "
                  f"{got['homes']} 页" + (f"；没放成 {got['not_opened']} 个" if got["not_opened"] else "")
                  + (f"；结束了 {closed} 个上次没回来的查看" if closed else ""))
            return 0
        finally:
            f.close()
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
