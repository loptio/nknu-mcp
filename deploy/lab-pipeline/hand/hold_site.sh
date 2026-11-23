#!/usr/bin/env bash
# 让一个网站先不换新导出（留在正式库用的那一份），或再放开。说明要写：为什么、等什么、谁说可以放开。
#   deploy/lab-pipeline/hand/hold_site.sh hold SITE "说明"      写进 ingest-hold.txt，清单退回正式库用的资料夹
#   deploy/lab-pipeline/hand/hold_site.sh let-go SITE           从 ingest-hold.txt 拿掉；下一次进库自己往前指
set -u
. "$(dirname -- "${BASH_SOURCE[0]}")/lab.sh"
case "${1:-}" in
  hold)
    site=${2:?SITE}; note=${3:?NOTE}
    tmp=$(mktemp); printf '%s\n' "$note" > "$tmp"
    scp -q "$tmp" nknu-lab:C:/nknu-transfer/out/hold-note.txt; rm -f "$tmp"
    lab_rw python $HAND/lab_hold_site.py "$site" /out/hold-note.txt --write ;;
  let-go) lab_rw python $HAND/lab_unhold_site.py "${2:?SITE}" --write ;;
  *) echo "usage: hold_site.sh hold SITE NOTE | let-go SITE"; exit 2 ;;
esac
