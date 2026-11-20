#!/usr/bin/env bash
# 先试建、检查过再换库（不动正式库）：
#   1. 放上暂停记号（没有就写一个）：每晚进库那支程序这时只改清单、转文字，不建库；
#   2. 清单比正式库新，或 current.tsv 有更新的导出：跑一次只转文字的进库，最多 3 次；
#   3. 试建：名字先做成正式库的硬链接（建库的逐份核对就会拿它和正式库比），再 build.sh --db；
#   4. 库一建好（<名>.swap.json 出现）就对 33 题和真资料检查，不等建库后面的每站报告；
#   5. 结果抓回 research/pipeline_2026-11-02/trials/<名>/；给了上一份检查过的结果，就逐格比 33 题。
# 换库不在这里：看过数字，用 promote.sh <名>。被把关拦下的试建以 7 结束。
#   deploy/lab-pipeline/hand/trial.sh NAME [BASE_NAME]     （BASE_NAME：trials/ 里上一份检查过的试建，拿来比 33 题）
#   转文字的时限：环境变数 LANE_LIMIT，秒，默认 10800；到点没转完的网站这一轮退回原来的导出。
set -u
. "$(dirname -- "${BASH_SOURCE[0]}")/lab.sh"
N=${1:?NAME}; BASE=${2:-}
OUT=$TRIALS/$N; mkdir -p "$OUT"
echo "start $(date '+%H:%M:%S')"
lab_rw bash $HAND/lab_state.sh pause | tail -1
got=$(names)
if echo "$got" | grep -q "nknu-lab-build-run"; then echo "a build runs: stopping"; exit 5; fi
converted=0
if echo "$got" | grep -q "nknu-lab-ingest-run"; then
  wait_ingest || exit 8; converted=1
elif echo "$got" | grep -qi "nknu-lane-"; then echo "a lane runs outside an ingest: stopping"; exit 5; fi
for round in 1 2 3; do
  plan=$(ingest plan); echo "$plan" | head -3
  if echo "$plan" | grep -q "有新资料夹的 0 站" && echo "$plan" | grep -q "就是照现在的清单建的"; then break; fi
  if echo "$plan" | grep -q "有新资料夹的 0 站" && [ "$converted" = 1 ]; then break; fi
  echo "conversion round $round: $(date '+%H:%M:%S')"
  ssh nknu-lab "$D $C run -d --rm -e NKNU_LANE_LIMIT=${LANE_LIMIT:-10800} ingest run" 2>&1 | clean | tail -1
  wait_ingest || exit 8
  now=$(lab_ro bash $HAND/lab_state.sh outcome | tail -1); echo "conversion ended $(date '+%H:%M:%S'): $now"
  case "$now" in *" paused "*) converted=1 ;; *) echo "it did not end as paused: stopping"; exit 7 ;; esac
done
lab_rw bash $HAND/lab_trial_prepare.sh "$N" | tail -3
same=$(lab_ro bash $HAND/lab_state.sh same "$N" | tail -1)
[ "$same" = same ] || { echo "$N.db is not the library in use (a name used before?): stopping"; exit 4; }
ssh nknu-lab "$D $C run -d --rm -e \"BUILD_OPTS=--db research/pipeline_2026-11-02/$N.db\" build" 2>&1 | clean | tail -1
state=""
for i in $(seq 1 240); do
  sleep 45
  if lab_ro test -f "research/pipeline_2026-11-02/$N.swap.json"; then state=told; break; fi
  got=$(names) || got=ERR
  if [ -n "$got" ] && [ "$got" != ERR ] && ! echo "$got" | grep -q nknu-lab-build-run; then state=ended; break; fi
done
echo "the build said its word ($state): $(date '+%H:%M:%S')"
same=$(lab_ro bash $HAND/lab_state.sh same "$N" | tail -1)
if [ "$state" != told ] || [ "$same" != different ]; then
  echo "no trial library stands (held by the guard, or the build failed)"
  lab_ro bash $HAND/lab_state.sh guard "$N.swap.json" | tail -3
  lab_ro tail -12 research/pipeline_2026-11-02/lab_pc/build.log | cut -c1-300
  exit 7
fi
lab_gpu bash $HAND/lab_trial_checks.sh "$N" | tail -4
for f in ${N}_final_r3.json ${N}_final_r4.json gold_$N.json ${N}_checks.md ${N}_checks.log ${N}_build_report.json $N.swap.json; do
  scp -q "nknu-lab:C:/nknu-transfer/out/trials/$N/$f" "$OUT/" || echo "missing $f"
done
if [ -n "$BASE" ]; then
  (cd "$REPO" && uv run --no-sync python deploy/lab-pipeline/hand/verdict.py "$TRIALS/$BASE" "$BASE" "$OUT" "$N"); echo "33 questions exit $?"
fi
echo "real-data checks passed: $(grep -c '| PASS |' "$OUT/${N}_checks.md" 2>/dev/null)"
grep "| FAIL |" "$OUT/${N}_checks.md" 2>/dev/null | cut -c1-120
lab_ro bash $HAND/lab_state.sh guard "$N.swap.json" | tail -3
echo "done $(date '+%H:%M:%S')"
