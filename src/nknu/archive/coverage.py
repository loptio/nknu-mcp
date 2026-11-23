"""Each night's coverage by site, kept for the panels.

Run as `python -m nknu.archive.coverage --dir <archive> [--night YYYY-MM-DD] [--db <a ledger file>] [--quiet]`. On the lab it is
compose's service coverage, which the schedule's file runs after the content's ingest: it only reads the ledger, and
nothing it does can hold up the night (deploy/lab-crawler/README.md).

Why (2026-11-23): the panels show how much of what a site was meant to give has been fetched, night by night. They
drew it from the nightly ledger backups, which their container cannot read, and a backup is gone after seven nights.
So the count of each night is written here once, where the panels read the crawler's other status files:
<archive>/reports/coverage.jsonl, one line a night, by night; counting a night again replaces its line.

The counting is the panels' own (nknu.ops.pipeline.crawl_buckets; the crawler's copy of the program on the lab has no
nknu.ops, so the query is written here too): url_result by owner_site and result, each result under the crawler's
category of it (outcome.user_bucket); of a site whose batch of round two is open (meta round2:open), the rows the
batch took from the next round (round2_promoted) and the rows added since the batch began are counted apart, so the
batch is never read as round one falling behind.

A line: night (the day the run started; the line is the ledger as that run left it), ledger ("live", or the backup
file a line was made from), written_at (Taipei), round2_open, and sites: {site key: {"buckets": {category: n},
"review": {result code: n}, "batch": {category: n}}}; a category with nothing is not written, batch is there only for
a site of the open batch. Only the sites of the list (site_plan, and the keys of the pages added as seeds of their
own, which the library holds too) are written one by one; the ledger names thousands
of other owners (every host a page links off campus, the rows no site owns), and those are one sum: others
{"sites": how many, "buckets": {category: n}}. No total is written: which sites count is the panels' to say (the
sites the library holds), and every listed site's numbers are here.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from nknu.archive import outcome
from nknu.archive.batch import OPEN_KEY

FILE = "coverage.jsonl"
_NIGHTLY = re.compile(r"nightly-(\d{4})(\d{2})(\d{2})-\d{6}\.db$")


def _open_sites(conn: sqlite3.Connection) -> list[str]:
    """The sites of round two's open batch (meta round2:open: {"sites": [...]}, or the list itself); [] when none."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (OPEN_KEY,)).fetchone()
        got = json.loads(row[0]) if row and row[0] else None
    except (sqlite3.Error, TypeError, ValueError):
        return []
    sites = got.get("sites") if isinstance(got, dict) else got
    return sorted({s for s in sites if isinstance(s, str)}) if isinstance(sites, list) else []


def count(conn: sqlite3.Connection) -> dict:
    """{"round2_open": [...], "sites": {site: {"buckets", "review", and "batch" for a site of the open batch}},
    "others": {"sites": how many owners are not sites of the list, "buckets": their rows in one sum}}."""
    open_sites = _open_sites(conn)
    rows = None
    if open_sites:
        qs = ",".join("?" * len(open_sites))
        try:
            start = conn.execute(f"SELECT min(k.at) FROM round2_promoted k JOIN url u ON u.id = k.url_id"
                                 f" WHERE u.owner_site IN ({qs})", tuple(open_sites)).fetchone()[0]
            rows = conn.execute(
                f"SELECT owner_site, result, owner_site IN ({qs}) AND (id IN (SELECT url_id FROM round2_promoted)"
                " OR added_at >= ?), count(*) FROM url_result GROUP BY 1, 2, 3",
                (*open_sites, start if start is not None else float("inf"))).fetchall()
        except sqlite3.Error:  # a ledger of before the batches: no round2_promoted, nothing to count apart
            rows = None
    if rows is None:
        rows = [(s, r, 0, n) for s, r, n in conn.execute("SELECT owner_site, result, count(*) FROM url_result GROUP BY 1, 2")]
    try:
        listed = {r[0] for r in conn.execute("SELECT site FROM site_plan")}
    except sqlite3.Error:  # a ledger without the list's table: every owner is written
        listed = None
    if listed is not None:
        try:  # and the keys of the pages added as seeds of their own (update.update_keys): the library holds them too
            listed |= {r[0] for r in conn.execute("SELECT DISTINCT u.owner_site FROM seed_src s JOIN url u ON u.url = s.url "
                                                  "WHERE s.source = 'extra' AND u.owner_site IS NOT NULL")}
        except sqlite3.Error:
            pass
    sites: dict[str, dict] = {}
    others: dict[str, int] = {}
    other_names: set[str] = set()
    for site, result, apart, n in rows:
        site = site or ""
        bucket = outcome.user_bucket(result)
        if listed is not None and site not in listed:
            others[bucket] = others.get(bucket, 0) + n
            other_names.add(site)
            continue
        per = sites.setdefault(site, {"buckets": {}, "review": {}})
        into = per.setdefault("batch", {}) if apart else per["buckets"]
        into[bucket] = into.get(bucket, 0) + n
        if bucket == "review" and not apart:
            per["review"][result] = per["review"].get(result, 0) + n
    return {"round2_open": open_sites, "sites": dict(sorted(sites.items())),
            "others": {"sites": len(other_names), "buckets": others}}


def record(root: Path, night: str, counted: dict, ledger: str) -> dict:
    """Write the night's line into <root>/reports/coverage.jsonl (in place of a line the night already has); the line."""
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    out = reports / FILE
    line = {"night": night, "ledger": ledger, "written_at": time.strftime("%Y-%m-%d %H:%M:%S"), **counted}
    kept = []
    if out.exists():
        for text in out.read_text(encoding="utf-8").splitlines():
            try:
                old = json.loads(text)
            except ValueError:
                continue  # a line cut short by a kill: dropped, its night is counted again when it is asked for
            if isinstance(old, dict) and old.get("night") != night:
                kept.append(old)
    lines = sorted([*kept, line], key=lambda x: str(x.get("night")))
    tmp = reports / (FILE + ".tmp")
    tmp.write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in lines), encoding="utf-8")
    os.replace(tmp, out)
    return line


def _night_of(root: Path) -> str:
    """The day the night's run started (reports/update-status.json's night_started), else today."""
    try:
        said = json.loads((root / "reports" / "update-status.json").read_text(encoding="utf-8")).get("night_started")
    except (OSError, ValueError, AttributeError):
        said = None
    if isinstance(said, str) and re.match(r"\d{4}-\d{2}-\d{2}", said):
        return said[:10]
    return time.strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把这一晚各网站抓到、没抓到的数量记一行（给面板读），只读账本")
    ap.add_argument("--dir", required=True, help="存档的资料夹（账本是它里面的 frontier.db，结果写进 reports/coverage.jsonl）")
    ap.add_argument("--night", help="这一行算哪一晚（YYYY-MM-DD，开跑那天）；不给就照状态档里这一晚的开跑时间")
    ap.add_argument("--db", help="改读这一份账本（补历史用：每晚的备份 nightly-年月日-时分秒.db 是那一天开跑前的账本，"
                                "不给 --night 就算成前一天那一行）")
    ap.add_argument("--quiet", action="store_true", help="不印结果（排程用）")
    a = ap.parse_args(argv)
    root = Path(a.dir)
    if a.db:
        named = _NIGHTLY.search(Path(a.db).name)
        if not a.night and not named:
            ap.error("--db 不是 nightly-年月日-时分秒.db 这种名字时，要给 --night")
        night = a.night or (datetime.date(*map(int, named.groups())) - datetime.timedelta(days=1)).isoformat()
        conn = sqlite3.connect(f"file:{a.db}?mode=ro&immutable=1", uri=True)
        ledger = Path(a.db).name
    else:
        night = a.night or _night_of(root)
        conn = sqlite3.connect(f"file:{root / 'frontier.db'}?mode=ro", uri=True)
        ledger = "live"
    try:
        counted = count(conn)
    finally:
        conn.close()
    line = record(root, night, counted, ledger)
    got = sum(s["buckets"].get("got", 0) for s in line["sites"].values())
    if not a.quiet:
        print(f"{night}：{len(line['sites'])} 个网站，抓到 {got} 个网址（读的是 {ledger}），写进 {root / 'reports' / FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
