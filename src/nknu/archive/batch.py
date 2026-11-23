"""Round two's batches (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.2, §四; the owner 11-17,
relayed by 爬虫judge: 「现在开始准备」 round two's batches that need no browser; the design approved by 爬虫judge under
the owner's 11-17 delegation). Run as `python -m nknu.archive.update round2 --sites …` (no command in cli.py: the
owner's timing rule, 11-16).

The list (round1.Plan) says which sites a batch crawls now: a round-one site whose paths grew, or a round-two site
that carries the batch (round1.Site.now). What the ledger holds of them from before is `later` rows (round two's):
`promote` judges each `later` row of the named sites again by the list as it is (round1.verdict, with no page behind
it: the row's own address) and makes the ones the list now places this round pending, as a page would have brought
them in: its tier, its leaf (a file, image … stays a leaf), and for a row only an old source named (the 10-24
archive, Common Crawl) depth 1, as a seed (its old depth, up to 15, would hold its own attachments at 16), under the
site's caps (crawler.cap_reason with crawler.Crawler.site_caps' numbers: held, never skipped, as a new row). A done
row of the site that was round two's is given its tier (outcome judges it as this round's page). A row the list still
leaves out (another's page on the unit's host) stays later. What each row was is kept in round2_promoted, so `undo`
puts back every row nothing fetched since; a row fetched since is this round's for good. `promote` opens the batch for
its sites (OPEN_KEY): a site under periodic update is held by a round-one site's caps while it is open, not by the
nightly growth caps (crawler.Crawler._cap); `close` ends it. --dry-run does all of it in one transaction and rolls it
back: the same counts, nothing changed.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from nknu.archive import crawler as crawl_mod
from nknu.archive import round1
from nknu.archive.frontier import OLD_VIAS, Frontier

OPEN_KEY = "round2:open"  # meta: {"sites": [...]}: the sites of round two's open batch
TABLE = """CREATE TABLE IF NOT EXISTS round2_promoted(url_id INTEGER PRIMARY KEY, batch TEXT NOT NULL, at REAL NOT NULL,
  state TEXT, reason TEXT, tier INTEGER, leaf INTEGER, depth INTEGER, next_at REAL, attempts INTEGER,
  fetched_at TEXT)"""
_KEPT = ("state", "reason", "tier", "leaf", "depth", "next_at", "attempts", "fetched_at")
_LEAF_KINDS = frozenset({"file", "image", "form", "script", "click", "embed"})  # round1._LEAF_KINDS


@dataclass
class SiteCount:
    promoted: Counter = field(default_factory=Counter)  # kind -> rows made pending
    held: Counter = field(default_factory=Counter)  # cap reason -> rows held by a cap
    left: int = 0  # later rows the list still leaves out
    retiered: int = 0  # done rows given this round's tier
    rebased: int = 0  # rows only an old source named: depth 1


def open_sites(f: Frontier) -> set[str]:
    raw = f.meta(OPEN_KEY)
    try:
        got = json.loads(raw) if raw else {}
    except ValueError:
        return set()
    sites = got.get("sites") if isinstance(got, dict) else None
    return {s for s in sites if isinstance(s, str)} if isinstance(sites, list) else set()


def _set_open(f: Frontier, sites: set[str]) -> None:
    if sites:
        f.meta(OPEN_KEY, json.dumps({"sites": sorted(sites)}, ensure_ascii=False))
    else:
        f.x("DELETE FROM meta WHERE key=?", (OPEN_KEY,))


def close(f: Frontier, sites: Iterable[str] | None) -> None:
    """The batch ends for these sites (all of them: None): a site under periodic update is held by the nightly growth
    caps again."""
    _set_open(f, set() if sites is None else open_sites(f) - set(sites))


def _batch_sites(plan: round1.Plan, sites: Iterable[str]) -> list[round1.Site]:
    out = []
    for key in sites:
        s = plan.site(key)
        if s is None or not s.this_round:
            raise ValueError(f"{key}: no site of the list crawled now (round one, or a round-two batch's: now)")
        out.append(s)
    return out


def promote(f: Frontier, plan: round1.Plan, sites: Iterable[str], *, write: bool,
            now: float | None = None) -> dict[str, SiteCount]:
    """The later rows of these sites that the list places this round go pending (see the module's note); write False:
    counted in a transaction rolled back. Each site's counts."""
    chosen = _batch_sites(plan, sites)
    keys = [s.site for s in chosen]
    out = {k: SiteCount() for k in keys}
    at = time.time() if now is None else now
    caps = _Caps(f, plan)
    with f.lock:
        f.conn.execute("SAVEPOINT round2_promote")
        try:
            f.conn.execute(TABLE)
            for key in keys:
                _promote_site(f, plan, plan.site(key), out[key], caps, at)
            _set_open(f, open_sites(f) | set(keys))
        except BaseException:
            f.conn.execute("ROLLBACK TO round2_promote")
            f.conn.execute("RELEASE round2_promote")
            raise
        if not write:
            f.conn.execute("ROLLBACK TO round2_promote")
        f.conn.execute("RELEASE round2_promote")
    return out


class _Caps:
    """The caps of crawler.Crawler.site_caps with the crawler's own defaults (the batch's promote judges as a run
    would)."""

    def __init__(self, f: Frontier, plan: round1.Plan) -> None:
        self.f, self.plan = f, plan

    def reason(self, url: str, owner: str, kind: str, depth: int) -> str | None:
        if depth > crawl_mod.MAX_DEPTH:
            return "cap:depth"
        own = self.plan.site(owner)
        caps = own.caps if own is not None and own.caps else (crawl_mod.SITE_CAP, crawl_mod.FILE_CAP,
                                                              crawl_mod.IMAGE_CAP)
        return crawl_mod.cap_reason(self.f, url, owner, kind, caps)


def _promote_site(f: Frontier, plan: round1.Plan, site: round1.Site, count: SiteCount, caps: _Caps,
                  at: float) -> None:
    olds = ",".join("?" * len(OLD_VIAS))
    rows = f.conn.execute(f"SELECT * FROM url WHERE owner_site=? AND state='later' ORDER BY "
                          f"CASE WHEN found_via IN ({olds}) THEN 1 ELSE depth END, id", (site.site, *OLD_VIAS)).fetchall()
    for r in rows:
        p = round1.verdict(plan, r["url"])
        if p.verdict != "r1" or p.owner_site != site.site:
            count.left += 1
            continue
        kind0 = r["kind0"] or r["kind"]
        leaf = 1 if kind0 in _LEAF_KINDS else p.leaf
        depth = r["depth"]
        if r["found_via"] in OLD_VIAS and depth > 1:
            depth = 1
            count.rebased += 1
        cap = caps.reason(r["url"], site.site, r["kind"], depth)
        _keep(f, r, site, at)
        if cap is not None:
            f.conn.execute("UPDATE url SET state='held', reason=?, tier=?, leaf=?, depth=? WHERE id=?",
                           (cap, p.tier, leaf, depth, r["id"]))
            count.held[cap] += 1
            continue
        f.conn.execute("UPDATE url SET state='pending', next_at=0, attempts=0, tier=?, leaf=?, depth=? WHERE id=?",
                       (p.tier, leaf, depth, r["id"]))
        count.promoted[r["kind"]] += 1
    for r in f.conn.execute("SELECT * FROM url WHERE owner_site=? AND state='done' AND tier=? ORDER BY id",
                            (site.site, round1.ROUND2_TIER)).fetchall():
        p = round1.verdict(plan, r["url"])
        if p.verdict == "r1" and p.owner_site == site.site:
            _keep(f, r, site, at)
            f.conn.execute("UPDATE url SET tier=? WHERE id=?", (p.tier, r["id"]))
            count.retiered += 1


def _keep(f: Frontier, row, site: round1.Site, at: float) -> None:
    """What the row was before the batch changed it (the first time only: promote again keeps the first)."""
    f.conn.execute(f"INSERT OR IGNORE INTO round2_promoted(url_id, batch, at, {', '.join(_KEPT)}) "
                   f"VALUES (?, ?, ?, {', '.join('?' * len(_KEPT))})",
                   (row["id"], site.now or "round1", at, *(row[c] for c in _KEPT)))


def undo(f: Frontier, sites: Iterable[str], *, write: bool) -> dict[str, int]:
    """Every row promote changed for these sites that nothing fetched since (its fetched_at as it was) goes back to what
    it was; the sites leave the open batch. Rows put back per site."""
    keys = list(sites)
    out = {k: 0 for k in keys}
    with f.lock:
        f.conn.execute("SAVEPOINT round2_undo")
        try:
            have = f.conn.execute("SELECT 1 FROM sqlite_master WHERE name='round2_promoted'").fetchone()
            for key in keys if have else ():
                for k in f.conn.execute(
                        "SELECT k.*, u.fetched_at AS now_fetched FROM round2_promoted k JOIN url u ON u.id = k.url_id "
                        "WHERE u.owner_site=? ORDER BY k.url_id", (key,)).fetchall():
                    if k["now_fetched"] != k["fetched_at"]:
                        continue  # fetched since: this round's for good
                    f.conn.execute(f"UPDATE url SET {', '.join(f'{c}=?' for c in _KEPT)} WHERE id=?",
                                   (*(k[c] for c in _KEPT), k["url_id"]))
                    f.conn.execute("DELETE FROM round2_promoted WHERE url_id=?", (k["url_id"],))
                    out[key] += 1
            _set_open(f, open_sites(f) - set(keys))
        except BaseException:
            f.conn.execute("ROLLBACK TO round2_undo")
            f.conn.execute("RELEASE round2_undo")
            raise
        if not write:
            f.conn.execute("ROLLBACK TO round2_undo")
        f.conn.execute("RELEASE round2_undo")
    return out


def no_area_back(f: Frontier, root: Path, *, write: bool) -> dict[str, int]:
    """The pages stored as got whose answer lacks the content area their profile requires (profiles.area_missing; the
    nights before 11-17 had no such check: a teacher's CV page answered with no card replaced the good one, unid=1732
    and 4906 on the lab): flagged no_area and put back pending, so the next run asks again (crawler._no_area_again
    then); the export meanwhile falls back on the last good answer. Per owner site; write False only counts. Once: a
    row flagged already is not counted again. `python -m nknu.archive.update no-area` (design 2026-11-17-round2-batch2
    §八)."""
    from nknu.archive import profiles, viewer

    out: dict[str, int] = {}
    rows = f.q("SELECT id, url, owner_site, flags, warc, warc_offset FROM url WHERE state='done' AND kind='page' "
               "AND status=200 AND warc IS NOT NULL AND warc_offset IS NOT NULL ORDER BY id")
    for r in rows:
        prof = profiles.profile_for(r["url"])
        if prof is None or not prof.area_required:
            continue
        flags = [x for x in (r["flags"] or "").split(",") if x]
        if crawl_mod.NO_AREA in flags:
            continue
        try:
            _status, _headers, body = viewer.read_record(root, r["warc"], r["warc_offset"])
        except (OSError, ValueError):
            continue
        if not profiles.area_missing(r["url"], body.decode("utf-8", "replace")):
            continue
        out[r["owner_site"]] = out.get(r["owner_site"], 0) + 1
        if write:
            f.x("UPDATE url SET flags=?, state='pending', reason=?, attempts=0, next_at=0 WHERE id=?",
                (",".join([*flags, crawl_mod.NO_AREA]), crawl_mod.NO_AREA, r["id"]))
    return out
