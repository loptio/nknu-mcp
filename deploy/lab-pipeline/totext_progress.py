"""全量转文字进度（主 session 11-03 写；计划 2026-11-08 T5 起看导出清单里的每一份导出）：两条线各还剩几个档，学报和考题另算。

导出清单和 build.sh、转文字线是同一份（lab_pc/build_helpers.py：$EXPORTS，否则 research/pipeline_2026-11-02/exports.txt，
都没有就只看教务处 11-02 的导出）。每份导出的档的路线和 sha256 存在 totext_jobs/<网站>.json（第一次要把那份导出读一遍，
导出换了就重算；只写这些档），之后只看缓存里有没有结果。有结果要是现在的转文字版本，网页还要是现在的网页规则读的
（Cache.done；计划 2026-11-08 T2 的检查发现：原来只看 result.json 在不在，规则改了、要重读的网页会被算成转完）。
「线 A」包括 C 线的压缩档；让程序崩溃过的档没有结果，一直算「还剩」。
退出码：0 = 学报和考题以外都转完（可以建第一版库），1 = 还没，2 = 导出清单不能用。
用法：uv run --extra pipeline python research/pipeline_2026-11-02/totext_progress.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent  # deploy/lab-pipeline (11-19: the programs are in the repository)
sys.path.insert(0, str(HERE))  # build_helpers: the export list and each export's job list

import build_helpers

sys.path.insert(0, str(build_helpers.REPO / "src"))  # nknu, when not run with the project on the path

CACHE = build_helpers.CACHE
DEFER = build_helpers.DEFER
LANE_A = {"pdf", "office", "office_old", "archive"}
LANE_B = {"page", "image", "data", "unsupported"}
SHOWN = 30  # exports listed by name


def main() -> int:
    from nknu.totext.cache import Cache

    try:
        list_file = build_helpers.exports_file()
        exports = build_helpers.read_exports(list_file)
    except build_helpers.ExportsError as e:
        print(f"导出清单不能用：{e}")
        return 2
    cache = Cache(CACHE)
    defer = build_helpers.deferred_urls(DEFER)
    counts, left = Counter(), Counter()
    for e in exports:
        for job in build_helpers.export_jobs(e, defer):
            lane = "A" if job["route"] in LANE_A else "B"
            part = "学报考题" if job["deferred"] and lane == "A" else "其余"
            done = cache.done(job["sha"]) is not None  # the current version, and a page read by the current rule
            counts[(lane, part, "done" if done else "left")] += 1
            if not done:
                left[(e.name, lane, part)] += 1
    for lane, part in (("A", "其余"), ("A", "学报考题"), ("B", "其余")):
        print(f"线 {lane} {part}：完成 {counts[(lane, part, 'done')]}，还剩 {counts[(lane, part, 'left')]}")
    print(f"{build_helpers.list_words(list_file)}：{len(exports)} 份导出")
    waiting = [e for e in exports if any(left[(e.name, lane, part)] for lane, part in
                                         (("A", "其余"), ("A", "学报考题"), ("B", "其余")))]
    for e in waiting[:SHOWN]:
        print(f"  {e.name}：线 A 还剩 {left[(e.name, 'A', '其余')]}（学报考题 {left[(e.name, 'A', '学报考题')]}），"
              f"线 B 还剩 {left[(e.name, 'B', '其余')]}")
    if len(waiting) > SHOWN:
        print(f"  ……另外 {len(waiting) - SHOWN} 份导出还有档没转")
    ready = counts[("A", "其余", "left")] == 0 and counts[("B", "其余", "left")] == 0
    print("可以建第一版库" if ready else "还没到建库的时候")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
