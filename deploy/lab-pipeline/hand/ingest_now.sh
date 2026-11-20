#!/usr/bin/env bash
# 白天手动进一次库：用每晚进库同一支程序、同一套把关（改清单、转文字、留上一份库、建库、逐份核对、换或不换）。
# 库一换成别的档就重启 Datasette（它开着旧档）；等这一趟整个跑完（换库后还有建库自己的检查）再印结果。
#   deploy/lab-pipeline/hand/ingest_now.sh        （转文字的时限：环境变数 LANE_LIMIT，秒，默认 10800）
# 有 ingest.pause 时不跑（那样只会转文字）：要先试建、检查过再换，用 trial.sh 和 promote.sh。
set -u
. "$(dirname -- "${BASH_SOURCE[0]}")/lab.sh"
echo "start $(date '+%H:%M:%S')"
[ "$(lab_ro bash $HAND/lab_state.sh paused | tail -1)" = open ] || { echo "ingest.pause is there: this would only convert; stopping"; exit 6; }
if names | grep -q "nknu-lab-ingest-run\|nknu-lab-build-run"; then echo "an ingest or a build runs already: stopping"; exit 5; fi
plan=$(ingest plan)
echo "$plan" | head -4
if echo "$plan" | grep -q "有新资料夹的 0 站" && echo "$plan" | grep -q "就是照现在的清单建的"; then echo "nothing new: not started"; exit 0; fi
was=$(lab_ro bash $HAND/lab_state.sh inode | tail -1); echo "library file before: $was"
ssh nknu-lab "$D $C run -d --rm -e NKNU_LANE_LIMIT=${LANE_LIMIT:-10800} ingest run" 2>&1 | clean | tail -1
quiet=0; told=0; end=$(( $(date +%s) + 7 * 3600 ))
while [ "$quiet" -lt 2 ]; do
  sleep 120
  got=$(names) || got=ERR
  if [ -z "$got" ] || [ "$got" = ERR ]; then quiet=0; continue; fi
  if echo "$got" | grep -q nknu-lab-ingest-run; then quiet=0; else quiet=$((quiet + 1)); fi
  if [ "$told" = 0 ]; then
    now=$(lab_ro bash $HAND/lab_state.sh inode | tail -1)
    if [ -n "$now" ] && [ -n "$was" ] && [ "$now" != "$was" ]; then
      told=1; echo "swapped $(date '+%H:%M:%S') (library file $now)"
      datasette_again; echo "datasette restarted $(date '+%H:%M:%S')"
    fi
  fi
  [ "$(date +%s)" -lt "$end" ] || { echo "the ingest still runs after 7 h"; break; }
done
echo "ingest ended $(date '+%H:%M:%S')"
ssh nknu-lab "if exist C:\\nknu-transfer\\out\\ingest-swapped.flag del C:\\nknu-transfer\\out\\ingest-swapped.flag" 2>/dev/null
ingest status | head -18
lab_ro bash $HAND/lab_state.sh look | tail -8
lab_ro bash $HAND/lab_state.sh guard | tail -3
echo "done $(date '+%H:%M:%S')"
