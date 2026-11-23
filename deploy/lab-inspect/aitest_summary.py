"""The real-AI test's verdicts as totals only, for 總覽's tile (2026-11-22).

The test (research/eval_2026-11-18/tools/strata_agent.py: the lab's own model answers the students' questions with
nothing but the public service's search and fetch) is graded into a report whose 批改 table gives how many answers were
right, partly right, wrong and empty. Its questions and answers hold the school's own words and stay off the lab; this
writes one small file of counts, which the panel reads beside its nights' record (/ops/aitest.json, web.AITEST_FILE):

    python deploy/lab-inspect/aitest_summary.py --report research/eval_2026-11-18/runs/strata_full_1122.md --out aitest.json

then, on the lab PC (as eval194.json; no restart):
    docker run --rm --network none -v nknu-ops:/ops -v C:\\nknu-transfer\\inspect:/in:ro nknu-pipeline cp /in/aitest.json /ops/
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERDICTS = (("right", "对"), ("partly", "部分对"), ("wrong", "错"), ("none", "没答到"))


def summary(text: str, source: str) -> dict | None:
    """The graders' column of the report's 批改 table ({right, partly, wrong, none, n, date, source}), or None when
    the report has no such table. date: the day in the report's title, 'YYYY-MM-DD'."""
    got = {}
    for key, word in VERDICTS:
        m = re.search(rf"^\| {word} \| (\d+)", text, re.MULTILINE)
        if not m:
            return None
        got[key] = int(m.group(1))
    day = re.search(r"（(\d{4}-\d{2}-\d{2})", text.split("\n", 1)[0])
    return {**got, "n": sum(got.values()), "date": day.group(1) if day else "", "source": source}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把一次 AI 实测的批改结果写成只有题数的小档，给总览用")
    ap.add_argument("--report", type=Path, required=True, help="实测报告（有「批改」那张表的 .md）")
    ap.add_argument("--out", type=Path, default=Path("aitest.json"))
    args = ap.parse_args(argv)
    path = args.report.resolve()
    source = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else path.name
    got = summary(path.read_text(encoding="utf-8"), source)
    if got is None:
        print(f"报告里找不到「批改」那张表：{args.report}")
        return 1
    args.out.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"写好了 {args.out}：{got['n']} 题，对 {got['right']}、部分对 {got['partly']}、错 {got['wrong']}、没答 {got['none']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
