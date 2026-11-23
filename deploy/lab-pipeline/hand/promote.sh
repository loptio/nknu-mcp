#!/usr/bin/env bash
# 试建库检查过了（33 题、真资料检查、逐份核对没拦）：不再重建，直接把它换成正式库（nightly_ingest.py promote），
# 重启 Datasette（它开着被换掉的档），拿掉暂停记号，印出现在的状态。
#   deploy/lab-pipeline/hand/promote.sh NAME
# 换完，试建库的名字 <名>.db 和资料夹 <名>/ 要留到下一次换库以后（库里记着的语料档在那个资料夹）。
# 试建的建库收尾（每站报告、建库自己的检查）还在跑的话，之后用 reports_later.sh NAME 把报告补搬到正式库旁边。
set -u
. "$(dirname -- "${BASH_SOURCE[0]}")/lab.sh"
N=${1:?NAME}
echo "start $(date '+%H:%M:%S')"
ingest promote "$N" | tail -12
same=$(lab_ro bash $HAND/lab_state.sh same "$N" | tail -1)
echo "aca.db and $N.db: $same"
[ "$same" = same ] || { echo "not promoted: the library in use is untouched, the pause stays"; exit 7; }
datasette_again; echo "datasette restarted $(date '+%H:%M:%S')"
ssh nknu-lab "if exist C:\\nknu-transfer\\out\\ingest-swapped.flag del C:\\nknu-transfer\\out\\ingest-swapped.flag" 2>/dev/null
lab_rw bash $HAND/lab_state.sh unpause | tail -1
lab_ro bash $HAND/lab_state.sh look "$N" | tail -12
lab_ro bash $HAND/lab_state.sh guard | tail -3
ingest status | head -14
ingest plan | head -6
echo "done $(date '+%H:%M:%S')"
