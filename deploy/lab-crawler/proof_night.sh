#!/usr/bin/env bash
# 在实验室电脑上，用每晚那个服务空跑一遍，证明排程跑的那条路还通；不抓任何网页、不导出、不改账本里的网址。
#   docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/deploy:/in:ro nightly bash /in/proof_night.sh
# 做这几样，哪一样不对就说、最后退出码 1：
#   1. 环境和每晚脚本开工前自己查的一样：台北时间、全机锁设了、存网页的资料夹能写、备份的资料夹能写、金钥档读得到
#      （只看在不在，绝不印出）、内容那边的导出资料夹挂着。
#   2. 放一个 update.pause，照排程那样跑 /crawl/code-update/lab/nightly_update.sh：它应该写一行「这一晚不更新」就结束，
#      退出码 0；跑完把 update.pause 拿掉（原来就有的话不动它，也不跑这一步）。这一行写在临时档里，不进 update.log
#      （白天多一行，面板会当成上一晚的）。
#   3. 今晚会看哪些（update plan）、定期更新的网站键（update sites）、课表学期那一步（学期没变就不发请求）、
#      导出清单（update export-list）：都只读账本，照每晚的程序和设定各跑一次。
set -uo pipefail
ROOT="${ROOT:-/crawl/archive}"
LAB="${LAB:-/crawl/code-update/lab}"
bad=0
ok() { echo "  好：$1"; }
no() { echo "  不对：$1"; bad=1; }

echo "== 环境"
[ "$(date +%z)" = "+0800" ] && ok "台北时间" || no "时区不是台北"
[ -n "${NKNU_SCHOOL_LOCK:-}" ] && ok "全机锁 ${NKNU_SCHOOL_LOCK}" || no "没设全机锁 NKNU_SCHOOL_LOCK"
{ [ -d "$ROOT/warc" ] && [ -w "$ROOT/warc" ]; } && ok "存网页的资料夹能写" || no "$ROOT/warc 不在或不能写"
{ [ -n "${BACKUPS:-}" ] && [ -d "$BACKUPS" ] && [ -w "$BACKUPS" ]; } && ok "备份的资料夹能写（$(ls "$BACKUPS" | grep -c '^nightly-.*\.db$') 份）" \
  || no "备份的资料夹 ${BACKUPS:-（没设）} 不在或不能写"
[ -r /run/secrets/google.env ] && ok "金钥档读得到（没有打开看）" || no "读不到金钥档"
[ -f /work/archive-export/sites/current.tsv ] && ok "导出资料夹挂着（current.tsv $(grep -c . /work/archive-export/sites/current.tsv) 行）" \
  || no "/work/archive-export/sites/current.tsv 不在"
bash -n "$LAB/nightly_update.sh" && bash -n "$LAB/export_sites.sh" && ok "两支脚本语法过" || no "脚本语法不过"
[ -s "$LAB/js_sites.txt" ] && ok "要开浏览器的网站 $(grep -vc '^[[:space:]]*#' "$LAB/js_sites.txt") 个" || no "js_sites.txt 不在或是空的"
google-chrome --version >/dev/null 2>&1 && ok "映像里有 Chrome" || no "映像里没有 Chrome（nightly 要用 nknu-browser）"

echo "== 照排程跑每晚脚本（放着 update.pause）"
if [ -e "$ROOT/update.pause" ]; then
  echo "  $ROOT/update.pause 本来就在：不动它，这一步不跑"
else
  # the script's own log goes to a file of this run: a line in update.log by day would read as the last night's
  PROOF_LOG="$(mktemp)"
  trap 'rm -f "$ROOT/update.pause" "$PROOF_LOG"' EXIT
  : > "$ROOT/update.pause"
  LOG="$PROOF_LOG" bash "$LAB/nightly_update.sh"
  rc=$?
  rm -f "$ROOT/update.pause"
  last="$(tail -1 "$PROOF_LOG")"
  rm -f "$PROOF_LOG"
  trap - EXIT
  if [ "$rc" -eq 0 ] && echo "$last" | grep -q "update.pause"; then
    ok "脚本跑起来、看到暂停就结束（${last}）"
  else
    no "退出码 ${rc}，最后一行：${last}"
  fi
  [ -e "$ROOT/update.pause" ] && no "update.pause 没拿掉！现在就拿掉，不然今晚不会更新" || ok "update.pause 拿掉了"
fi

echo "== 照每晚的程序各问一次（只读账本）"
update() { python -m nknu.archive.update --dir "$ROOT" "$@"; }
out="$(update plan 2>&1)" && ok "今晚会看：$(echo "$out" | tail -1 | cut -c1-160)" || no "update plan：$(echo "$out" | tail -1 | cut -c1-200)"
out="$(update sites 2>&1)" && ok "定期更新的网站键 $(echo "$out" | tr ',' '\n' | grep -c .) 个" || no "update sites：$(echo "$out" | tail -1 | cut -c1-200)"
out="$(python -m nknu.archive.schedules --dir "$ROOT" night --gap 1-2 2>&1)" && ok "课表学期那一步：$(echo "$out" | tail -1 | cut -c1-120)" \
  || no "课表学期那一步：$(echo "$out" | tail -1 | cut -c1-200)"
out="$(update export-list 2>&1)" && ok "导出清单 $(echo "$out" | grep -c .) 个网站（照上一晚的状态档）" || no "update export-list：$(echo "$out" | tail -1 | cut -c1-200)"

if [ "$bad" -eq 0 ]; then echo "空跑通过：每晚那条路是通的"; else echo "空跑有不对的地方（见上）"; fi
exit "$bad"
