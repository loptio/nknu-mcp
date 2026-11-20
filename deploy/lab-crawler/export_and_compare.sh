#!/usr/bin/env bash
# 在实验室电脑上，用每晚那个服务跑：导出几个网站（照每晚导出的做法，没变动的站会跳过），再把每站现用的资料夹对着
# 正式库用的那一份（内容那边的 exports-official.txt）逐行比。交导出给内容以前跑这个，把输出一起给它。
#   docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/deploy:/in:ro -e "SITES=<网站键 网站键 …>"
#     nightly bash /in/export_and_compare.sh
# 只想比、不导出：加 -e EXPORT=0。比对看三样：消失的行、有档变没档的行（每一行都要说得出原因）、
# 每页网页档里的图在清单里配得上档的有几张（变少的页会列出来）。
set -uo pipefail
OFFICIAL="${OFFICIAL:-/work/research/pipeline_2026-11-02/exports-official.txt}"
SITES="${SITES:?要给网站键：-e \"SITES=a.nknu.edu.tw b.nknu.edu.tw/x\"}"
if [ "${EXPORT:-1}" = 1 ]; then
  LOG="$(mktemp)"
  RESULTS="$(mktemp)"
  echo "$SITES" | tr ' ' '\n' | grep . > /tmp/export-and-compare.txt
  NIGHTLY=1 EXPORT_RESULTS="$RESULTS" bash /crawl/code-update/lab/export_sites.sh /tmp/export-and-compare.txt > "$LOG" 2>&1
  echo "导出结束（退出码 $?）：$(cut -f2 "$RESULTS" | sort | uniq -c | tr '\n' ' ')"
  grep -a "失败\|拦下\|暂缓\|读不了" "$LOG" | cut -c1-200 | tail -5
fi
for site in $SITES; do
  name="$(echo "$site" | tr '/' '_')"
  old="$(grep -E "sites/${name}-2[0-9]{7}-[0-9]{6}$" "$OFFICIAL" | tail -1 | sed 's|^|/work/|')"
  new="$(grep -P "^\Q${site}\E\t" /work/archive-export/sites/current.tsv | cut -f2)"
  echo "== ${site}"
  echo "   正式库用的：${old:-（没有）}"
  echo "   现用的：    ${new:-（没有）}"
  if [ -n "$old" ] && [ -n "$new" ] && [ "$old" != "$new" ]; then
    python /in/compare_export.py "$old" "$new"
  elif [ "$old" = "$new" ]; then
    echo "   是同一份"
  fi
done
