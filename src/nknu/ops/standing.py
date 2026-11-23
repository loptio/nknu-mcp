"""新出現, 之前就有, 好了: the home's problems against the list kept for the night before.

A review of the panels as an ops panel (2026-11-21) found the one fault that matters in the morning hard to see: a site
down for two weeks and a site that broke last night were lit alike, so the reader had to remember which were old. The
home's rows (nknu.ops.tonight_page.problems) each carry what they are from one night to the next (issues: a key each),
檢視台 keeps the list a night (nknu.ops.history: problems), and this module compares:

  new   an issue the list of the night before does not have, or one that went from 留意 to 出錯
  old   the rest, each with the nights it has been listed in a row
  gone  what the night before listed and now is not there

What a key is: part:<step>[:<code>] a part of the night that stopped or warns (the same part stopping for another code
is another problem); step:<key> a step of 流水線 that is not normal; site:<site>:<cell> one cell of a site's row, and
site:<site>:unexplained / look what the last swap's comparison found for it; run:<night>:<n> one run of one night (an
event: never the same the night after, and never 好了).

Nothing is kept of a night while its chain runs: the list of the night before stays as it was when that chain began,
so what the run breaks reads as new and what it mends as gone. The rules are 【Claude】."""

from __future__ import annotations

from nknu.ops.pipeline import BAD, WARN

SWAP_KINDS = ("unexplained", "look")  # found by the last swap's comparison: not known while it is being made
RANK = {BAD: 0, WARN: 1}


def records(rows: list[dict]) -> list[dict]:
    """The rows as they are kept: one record an issue ({key, sev, place, state, reason, href})."""
    out: dict[str, dict] = {}
    for x in rows:
        for it in x.get("issues") or []:
            out.setdefault(it["key"], {"key": it["key"], "sev": it.get("sev") or x.get("sev") or WARN,
                                       "place": x.get("where") or "", "state": x.get("state") or "",
                                       "reason": it.get("what") or "", "href": x.get("href") or ""})
    return sorted(out.values(), key=lambda r: r["key"])


def swap_kind(key: str) -> bool:
    return key.startswith("site:") and key.rsplit(":", 1)[-1] in SWAP_KINDS


def age_words(nights: int, open_: bool) -> tuple[str, str]:
    """(the chip, its floating card) of a problem listed `nights` nights in a row counting this one; open_: the list
    goes no further back (it was there on the first night kept, or a night between was not kept). Days, not nights,
    since a night is named by the day it runs (owner 11-23: 「≥N 天」 on 總覽's 待處理)."""
    if open_:
        return f"≥{nights} 天", "從開始記問題清單的那一晚就在；更早有沒有，檢視台沒有紀錄"
    return f"第 {nights} 天", f"連續 {nights} 天都列在這裡"


def split(rows: list[dict], base: dict | None, *, reading: bool = False, swap_known: bool = True) -> dict | None:
    """The rows in three: {'night' (the night compared with), 'new', 'old' (rows, as given, with age / worse), 'gone'
    (records of the night before)}; None when no night before is kept (nothing to compare with). reading: the steps
    are still being read, so nothing is said to be gone; swap_known: the last swap's comparison is made (False: what it
    found is neither new nor gone)."""
    if not base or not base.get("night"):
        return None
    before: dict[str, dict] = base.get("rows") or {}
    runs: dict[str, tuple[int, bool]] = base.get("runs") or {}
    new, old, seen = [], [], set()
    for x in rows:
        issues = x.get("issues") or []
        seen.update(it["key"] for it in issues)
        fresh = [it for it in issues if it["key"] not in before
                 or RANK.get(it.get("sev"), 2) < RANK.get(before[it["key"]].get("sev"), 2)]
        if not swap_known:  # what the comparison found last night may still be found: not new until it is made
            fresh = [it for it in fresh if not swap_kind(it["key"])]
        row = dict(x)
        if fresh:
            lead = fresh[0]
            if len(issues) > 1:  # a site's row: the new issue leads, the others are counted
                row["reason"] = lead.get("what", "") + f"（還有 {len(issues) - 1} 件）"
            row["worse"] = lead["key"] in before
            new.append(row)
            continue
        lead = issues[0] if issues else None
        nights, open_ = runs.get(lead["key"], (1, True)) if lead else (1, True)
        row["age"], row["age_tip"] = age_words(nights + 1, open_)
        old.append(row)
    gone = []
    if not reading:
        for key, rec in before.items():
            if key in seen or key.startswith("run:") or (not swap_known and swap_kind(key)):
                continue
            gone.append(rec)
    gone.sort(key=lambda r: (RANK.get(r.get("sev"), 2), r.get("place") or ""))
    return {"night": base["night"], "new": new, "old": old, "gone": gone}
