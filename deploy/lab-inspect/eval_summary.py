"""The new question set's score as totals only, for 檢視台's 測試 page (2026-11-21).

The set (research/eval_2026-11-18: 194 questions, the 94 from the student's side the yardstick) and its runs hold the
school's own words and stay off git and off the lab PC; its sealed half is looked at as totals only. This writes one
small file of counts and no question: how many answered questions have their source in the first 1, 3 and 10 results,
for the question as written (q), the agent's first query (a1) and the better of its two (a), for all of B, B's two
halves, and A. The panel reads it beside its nights' record (/ops/eval194.json on the lab PC: ops' own volume).

    python deploy/lab-inspect/eval_summary.py --run research/eval_2026-11-18/runs/<name>.json \\
        [--questions research/eval_2026-11-18/questions.json] --out eval194.json

then, on the lab PC (the README's 「新題庫的成績」):
    docker run --rm -v nknu-ops:/ops -v C:\\nknu-transfer\\inspect:/in:ro nknu-pipeline cp /in/eval194.json /ops/
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TOPS = (1, 3, 10)
WAYS = ("q", "a1", "a")
# (key, the rows it takes: path, half or None)
GROUPS = (("B", "B", None), ("B/open", "B", "平时"), ("B/sealed", "B", "封存"), ("A", "A", None))


def summary(run: dict, questions: dict | None, measured_at: str, name: str) -> dict:
    rows = [r for r in run.get("rows") or [] if isinstance(r, dict)]
    groups = []
    for key, path, half in GROUPS:
        mine = [r for r in rows if r.get("path") == path and (half is None or r.get("half") == half)]
        groups.append({"key": key, "n": len(mine),
                       **{way: [sum(1 for r in mine if r.get(way) is not None and r[way] <= top) for top in TOPS]
                          for way in WAYS}})
    out = {"set": "2026-11-18", "run": name, "library": str(run.get("library") or ""), "measured_at": measured_at,
           "tops": list(TOPS), "groups": groups}
    if questions:
        asked = [q for q in questions.get("questions") or [] if str(q.get("path") or "").startswith("B")]
        verdicts: dict[str, int] = {}
        for q in asked:
            verdicts[str(q.get("verdict") or "")] = verdicts.get(str(q.get("verdict") or ""), 0) + 1
        # a question with a time limit (expires_on) past when the run was made is not in the run's rows (measure.py
        # leaves it out): how many of B's answered ones that was, so the page's words agree with its numbers
        expired = sum(1 for q in asked if q.get("verdict") == "有答案" and q.get("expires_on")
                      and str(q["expires_on"]) < measured_at[:10])
        out["b_asked"] = {"all": len(asked), "answered": verdicts.get("有答案", 0), "expired": expired,
                          "related_only": verdicts.get("只有相关", 0), "absent": verdicts.get("库里没有", 0)}
        out["questions"] = len(questions.get("questions") or [])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="新题库一次量测的总数（不含题目），给检视台的「測試」页")
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--questions", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    run = json.loads(a.run.read_text(encoding="utf-8"))
    questions = json.loads(a.questions.read_text(encoding="utf-8")) if a.questions else None
    at = datetime.fromtimestamp(a.run.stat().st_mtime, ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    got = summary(run, questions, at, a.run.stem)
    a.out.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    b = got["groups"][0]
    print(f"写好了 {a.out}：{got['library']}；学生角度 {b['n']} 题，agent 前 10 名 {b['a'][2]} 题")


if __name__ == "__main__":
    main()
