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

Only round-one sites. A list left out is counted with its reason: a walk cut off with presses waiting is resume()'s to
finish, a walk with no end waits for a whole walk (stage 2's monthly one), a look still open waits for its end.
Stage 2 adds the weekly standing pages, the monthly whole walks and the recent notices (design §7.3).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from nknu.archive import crawler as crawl_mod
from nknu.archive import postback
from nknu.archive.frontier import Frontier

# the seed sources whose pages are a site's way in (seeds.py): its home, the owner's extra pages, a c.nknu template's
# SiteMap.aspx. Not sso_lists (lists: looks), nor the old sources (old_url, v1_known, commoncrawl), nor systems
HOME_SOURCES = ("site_home", "extra", "sitemap_aspx")


@dataclass
class Tonight:
    looks: list[int] = field(default_factory=list)  # list pages of lists walked by button
    looks_get: list[int] = field(default_factory=list)  # the first page of each list paged by GET
    homes: list[int] = field(default_factory=list)  # home and seed pages (and where a 3xx of one leads)
    skipped: Counter = field(default_factory=Counter)  # lists left out tonight, by reason

    def ids(self) -> list[int]:
        return [*self.looks, *self.looks_get, *self.homes]


def _has(frontier: Frontier, table: str) -> bool:
    return bool(frontier.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _in(ids) -> str:
    return ",".join("?" * len(ids))


def select(frontier: Frontier, root: Path) -> Tonight:
    """Tonight's lists and pages (the module's docstring), each url row in one of the three at most."""
    t = Tonight()
    r1 = {r[0] for r in frontier.q("SELECT site FROM site_plan WHERE round='1'")}
    open_looks = ({r[0] for r in frontier.q("SELECT list_id FROM list_look WHERE ended_at IS NULL")}
                  if _has(frontier, "list_look") else set())
    waiting = {r[0] for r in frontier.q("SELECT DISTINCT list_id FROM action WHERE kind NOT IN ('click', 'data') AND "
                                        "state IN ('pending', 'active')")}
    walked: set[int] = set()
    # a list walked by button: its listwalk counts its rows (page_stored, _pager_answer); get_walk writes rows NULL
    for r in frontier.q("SELECT w.list_id, w.ended_by, u.owner_site, u.state FROM listwalk w JOIN url u ON u.id = "
                        "w.list_id WHERE w.rows IS NOT NULL ORDER BY w.list_id"):
        if r["owner_site"] not in r1 or r["state"] not in ("done", "failed"):
            continue
        walked.add(r["list_id"])
        if r["list_id"] in open_looks:
            t.skipped["look_open"] += 1
        elif r["list_id"] in waiting:
            t.skipped["presses_waiting"] += 1
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
        if row is None or row["owner_site"] not in r1 or row["kind"] != "page" or row["state"] not in ("done", "failed"):
            if row is None:
                t.skipped["get_no_first_page"] += 1
            continue
        walks = frontier.q(f"SELECT ended_by FROM listwalk WHERE list_id IN ({_in(group)})", sorted(group))
        if group & open_looks:
            t.skipped["look_open"] += 1
        elif not walks or any(w[0] in (None, "walking") for w in walks):
            t.skipped["walk_unfinished"] += 1
        else:
            t.looks_get.append(first)
    taken = walked | grouped
    homes: list[int] = []
    for h in frontier.q(f"SELECT DISTINCT u.id, u.status, u.final_url, u.owner_site FROM seed_src s JOIN url u ON "
                        f"u.url = s.url WHERE s.source IN ({_in(HOME_SOURCES)}) AND u.kind = 'page' AND u.tier IN "
                        f"(0, 1) AND u.state IN ('done', 'failed') ORDER BY u.id", HOME_SOURCES):
        if h["owner_site"] not in r1 or h["id"] in taken:
            continue
        homes.append(h["id"])
        if h["status"] is not None and 300 <= h["status"] < 400 and h["final_url"]:
            # a seed that moves: the page it leads to is a dup of every reading, never fetched again by itself
            to = frontier.row(h["final_url"])
            if to is not None and to["kind"] == "page" and to["state"] in ("done", "failed") and to["id"] not in taken:
                homes.append(to["id"])
    t.homes = list(dict.fromkeys(homes))
    return t


def put_back(frontier: Frontier, root: Path, tonight: Tonight) -> Counter:
    """Tonight's looks opened and pages put back (a look that does not open is counted not_opened)."""
    chains = postback.Chains(frontier, root)
    got: Counter = Counter()
    for i in tonight.looks:
        got["looks" if chains.look(i) else "not_opened"] += 1
    for i in tonight.looks_get:
        got["looks_get" if chains.look_get(i) else "not_opened"] += 1
    for i in tonight.homes:
        got["homes" if frontier.put_back(i, "update_home") else "not_opened"] += 1
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
          f"首页和起点 {len(t.homes)} 页，共 {len(ids)} 个网址（之后有新东西才会再多）")
    if queues:
        print("按服务器：" + "、".join(f"{q} {n}" for q, n in queues.most_common()))
    if t.skipped:
        words = {"walk_unfinished": "上次翻页没翻完（等整个重翻）", "presses_waiting": "还有按钮等着按（下次 run 开头接着翻）",
                 "look_open": "上次的查看还没结束", "get_no_first_page": "认不出第 1 页"}
        print("今晚不看的列表：" + "、".join(f"{words.get(k, k)} {n}" for k, n in t.skipped.most_common()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.archive.update",
                                 description="定期更新：挑今晚要看的列表和网页、放回待抓（不连网；抓取照旧用 nknu archive run）")
    ap.add_argument("--dir", required=True, type=Path, help="存档目录（frontier.db 所在）")
    ap.add_argument("action", choices=("plan", "put-back", "go-live"),
                    help="plan 只读、只算数；put-back 放回今晚要看的；go-live 第一晚以前做一次")
    a = ap.parse_args(argv)
    root = a.dir
    if a.action == "plan":
        f = Frontier(root / "frontier.db", readonly=True)
        try:
            _report(f, select(f, root))
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
                sites = sorted(r[0] for r in f.q("SELECT site FROM site_plan WHERE round='1'"))
                f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps(sites))
                print(f"列表的每一行读进 list_row：{n} 行；{len(sites)} 个第一轮网站进入定期更新（每晚的增长上限）")
                return 0
            closed = postback.Chains(f, root).close_stale_looks()
            t = select(f, root)
            got = put_back(f, root, t)
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
