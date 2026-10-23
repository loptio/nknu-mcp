"""Search regression metrics on the fixture world (spec §6.5).

    uv run python -m tests.search.evaluate                  # per-type report on queries.yaml
    uv run python -m tests.search.evaluate --calibrate      # grid-search T_LOW / T_HIGH on calibration.yaml
    uv run python -m tests.search.evaluate --write-baseline # rewrite baseline.json (on purpose only: say why in the commit)

R@1 / R@5 count the answerable queries whose gold appears first / within the first five facts of the envelope an agent
receives (found=false gives no facts); noans_acc is the share of noans queries answered found=false."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

from nknu.core.timeutil import TAIPEI
from nknu.server.query import CURRENT_DOC_SQL, current_doc_params
from nknu.server.search import retrieve, search, strength
from nknu.server.servedb_reader import ServeDB

HERE = Path(__file__).parent
NOW = datetime(2026, 10, 22, 14, 5, tzinfo=TAIPEI)  # the fixture world's build day
TODAY = NOW.date()
GRID = [round(i * 0.05, 2) for i in range(21)]


def load(name: str) -> list[dict]:
    return yaml.safe_load((HERE / name).read_text())


def _db(world) -> ServeDB:
    return ServeDB(Path(world[1]))


def matches(fact: dict, gold: dict) -> bool:
    if "card" in gold:
        g = gold["card"]
        return g["intent"] == "hours" and fact.get("entity_id") == g["entity"] and "label" not in fact
    if "calendar" in gold:
        g = gold["calendar"]
        return ("summary" in fact and fact.get("category") == g["category"]
                and (not g.get("summary_re") or re.search(g["summary_re"], fact["summary"]) is not None))
    g = gold["doc"]
    return bool(fact.get("label")) and re.search(g["title_re"], fact.get("title", "")) is not None


def rank_of(facts: list[dict], gold: list[dict]) -> int | None:
    for i, f in enumerate(facts):
        if any(matches(f, g) for g in gold):
            return i + 1
    return None


def _envelopes(world, queries: list[dict]) -> list[tuple[dict, dict]]:
    db = _db(world)
    return [(q, search(db, q["q"], now=NOW)) for q in queries]


def _metrics(rows: list[tuple[dict, dict]]) -> dict:
    ranks = [rank_of(e.get("facts", []) if e["found"] else [], q["gold"]) for q, e in rows if q["gold"]]
    noans = [e["found"] is False for q, e in rows if not q["gold"]]
    return {"r1": sum(r == 1 for r in ranks) / len(ranks) if ranks else 0.0,
            "r5": sum(r is not None and r <= 5 for r in ranks) / len(ranks) if ranks else 0.0,
            "noans_acc": sum(noans) / len(noans) if noans else 1.0}


def evaluate(world, queries: list[dict]) -> dict:
    return _metrics(_envelopes(world, queries))


def evaluate_found(world, queries: list[dict]) -> float:
    """Share of queries whose found flag is right: answerable -> found=true, noans -> found=false."""
    rows = _envelopes(world, queries)
    return sum(e["found"] is bool(q["gold"]) for q, e in rows) / len(rows)


def gold_exists(world, gold: dict) -> bool:
    """The gold item is really in the fixture world, and searchable on TODAY."""
    conn = sqlite3.connect(Path(world[1]).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        d = TODAY.isoformat()
        if "card" in gold:
            g = gold["card"]
            return conn.execute("SELECT 1 FROM answer_card WHERE entity_id=? AND intent=? AND valid_from<=? AND ?<valid_to",
                                (g["entity"], g["intent"], d, d)).fetchone() is not None
        if "calendar" in gold:
            g = gold["calendar"]
            rows = conn.execute("SELECT payload_json FROM answer_card WHERE intent='calendar' AND valid_to>?", (d,))
            return any(p["category"] == g["category"] and (not g.get("summary_re") or re.search(g["summary_re"], p["summary"]))
                       for p in (json.loads(r[0]) for r in rows))
        rows = conn.execute(f"SELECT title FROM doc WHERE {CURRENT_DOC_SQL}", current_doc_params(TODAY))
        return any(re.search(gold["doc"]["title_re"], r[0]) for r in rows)
    finally:
        conn.close()


# ---------------------------------------------------------------- threshold calibration

def _wanted(q: dict, facts: list[dict]) -> str:
    """The strength a full-text answer should get: none for noans or a miss, text when the first fact is right, else weak."""
    if not q["gold"]:
        return "none"
    r = rank_of(facts, q["gold"])
    return "text" if r == 1 else "weak" if r is not None and r <= 5 else "none"


def calibrate(world, queries: list[dict]) -> tuple[float, float, float, float]:
    """Grid search (step 0.05): best found-accuracy, then best strength-accuracy; the middle of the best plateau.
    Returns (t_low, t_high, found_acc, strength_acc)."""
    db = _db(world)
    conn, tpl = db.conn(), db.templates()
    rows = []
    for q in queries:
        r = retrieve(conn, tpl, q["q"], kind="any", on=TODAY, today=TODAY, campus=None, limit=5)
        rows.append((q, r, _wanted(q, r.facts)))
    scored = []
    for lo in GRID:
        for hi in (h for h in GRID if h > lo):
            found_ok = strength_ok = 0
            for q, r, want in rows:
                got = "text" if r.coverage is None else strength(r.coverage, lo, hi)
                found_ok += (got != "none") == bool(q["gold"])
                strength_ok += r.coverage is None or got == want
            scored.append((found_ok / len(rows), strength_ok / len(rows), lo, hi))
    best = max(s[:2] for s in scored)
    plateau = [s for s in scored if s[:2] == best]
    lo = statistics.median_low(sorted({s[2] for s in plateau}))
    hi = statistics.median_low(sorted(s[3] for s in plateau if s[2] == lo))
    return lo, hi, best[0], best[1]


# ---------------------------------------------------------------- report / CLI

def report(world, queries: list[dict]) -> str:
    rows = _envelopes(world, queries)
    by_type: dict[str, list] = defaultdict(list)
    lines = []
    for q, e in rows:
        by_type[q["type"]].append((q, e))
        facts = e.get("facts", []) if e["found"] else []
        r = rank_of(facts, q["gold"]) if q["gold"] else None
        ok = (r == 1) if q["gold"] else e["found"] is False
        lines.append(f"{'ok ' if ok else 'MISS'} {q['type']:6} rank={r} found={e['found']} match={e.get('match')} {q['q']}")
    m = _metrics(rows)
    lines.append(f"ALL  R@1={m['r1']:.3f} R@5={m['r5']:.3f} noans_acc={m['noans_acc']:.3f} n={len(rows)}")
    for t, rs in sorted(by_type.items()):
        mt = _metrics(rs)
        lines.append(f"  {t:6} n={len(rs):2} R@1={mt['r1']:.2f} R@5={mt['r5']:.2f} noans_acc={mt['noans_acc']:.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m tests.search.evaluate")
    ap.add_argument("--write-baseline", action="store_true", help="rewrite tests/search/baseline.json")
    ap.add_argument("--calibrate", action="store_true", help="grid-search T_LOW/T_HIGH on calibration.yaml")
    args = ap.parse_args(argv)
    from tests.world import build_fixture_world

    with tempfile.TemporaryDirectory() as tmp:
        world = build_fixture_world(Path(tmp), date(2026, 10, 22))
        if args.calibrate:
            lo, hi, found_acc, strength_acc = calibrate(world, load("calibration.yaml"))
            print(f"T_LOW={lo} T_HIGH={hi} found_acc={found_acc:.3f} strength_acc={strength_acc:.3f}")
            return
        print(report(world, load("queries.yaml")))
        if args.write_baseline:
            m = evaluate(world, load("queries.yaml"))
            (HERE / "baseline.json").write_text(json.dumps(m, indent=2) + "\n")
            print(f"wrote {HERE / 'baseline.json'}: {m}")


if __name__ == "__main__":
    main()
