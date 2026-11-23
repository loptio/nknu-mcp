#!/usr/bin/env bash
# promote 以后：等试建那次建库的收尾（每站报告、gold、离线量测、真资料检查）跑完，把这些报告补搬到正式库旁边，
# 面板才读得到这一份库的报告。  deploy/lab-pipeline/hand/reports_later.sh NAME
set -u
. "$(dirname -- "${BASH_SOURCE[0]}")/lab.sh"
N=${1:?NAME}
echo "start $(date '+%H:%M:%S')"
quiet=0; end=$(( $(date +%s) + 3 * 3600 ))
while [ "$quiet" -lt 2 ]; do
  sleep 120
  got=$(names) || got=ERR
  if [ -z "$got" ] || [ "$got" = ERR ]; then quiet=0; continue; fi
  if echo "$got" | grep -q nknu-lab-build-run; then quiet=0; else quiet=$((quiet + 1)); fi
  [ "$(date +%s)" -lt "$end" ] || { echo "the build still runs after 3 h"; exit 8; }
done
echo "the trial's build ended $(date '+%H:%M:%S')"
ingest reports "$N"
echo "done $(date '+%H:%M:%S')"
