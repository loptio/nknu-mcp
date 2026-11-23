"""11-18 (session 4bd91945): the 33 questions of a trial library against the library in use, and the rule for the swap
(the same as 11-17's: each of the nine cells, 问题原文 / agent r3 / agent r4 by top 1 / 3 / 10 with copies counted,
may drop by one question at most). Prints the cells, every question whose rank moved, and passes or not.
Usage: verdict.py OFFICIAL_DIR OFFICIAL_NAME TRIAL_DIR TRIAL_NAME   (dirs hold <name>_final_r3.json, _r4.json)"""
import json
import sys
from pathlib import Path


def load(folder: str, name: str) -> dict:
    return {r: json.loads((Path(folder) / f"{name}_final_{r}.json").read_text(encoding="utf-8")) for r in ("r3", "r4")}


def cells(runs: dict) -> dict:
    out = {"问题原文": runs["r3"]["metrics"]["question_copies"]["all"]}
    for r in ("r3", "r4"):
        out[f"agent {r}"] = runs[r]["metrics"]["agent_copies"]["all"]
    return out


def ranks(runs: dict) -> dict:
    out = {}
    for q in runs["r3"]["questions"]:
        out[(q["id"], "问题原文")] = q["question_query"].get("rank_copies")
    for r in ("r3", "r4"):
        for q in runs[r]["questions"]:
            out[(q["id"], f"agent {r}")] = (q.get("agent_best_copies") or {}).get("rank")
    return out


old, new = load(sys.argv[1], sys.argv[2]), load(sys.argv[3], sys.argv[4])
print("库：", old["r3"]["db_built_at"], "→", new["r3"]["db_built_at"])
ok = True
for row, a in cells(old).items():
    b = cells(new)[row]
    words = "　".join(f"前{k[3:]} {a[k]} → {b[k]}" for k in ("top1", "top3", "top10"))
    worst = min(b[k] - a[k] for k in ("top1", "top3", "top10"))
    ok &= worst >= -1
    print(f"{row}：{words}　MRR {a['mrr']:.3f} → {b['mrr']:.3f}" + ("　← 掉超过 1 题" if worst < -1 else ""))
a, b = ranks(old), ranks(new)
moved = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
print(f"名次变了的 {len(moved)} 处：")
for (qid, row), x, y in moved:
    mark = "升" if (y or 999) < (x or 999) else "降"
    print(f"  {qid} {row}：{x or '没进'} → {y or '没进'}（{mark}）")
print("33 题这一关：" + ("过" if ok else "没过"))
sys.exit(0 if ok else 4)
