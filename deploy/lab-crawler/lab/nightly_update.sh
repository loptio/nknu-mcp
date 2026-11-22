#!/usr/bin/env bash
# 每晚的定期更新（设计 docs/superpowers/specs/2026-11-16-periodic-update-design.md §7.7；计划
# docs/superpowers/plans/2026-11-16-update-stage-2.md Task 4）：
#   备份账本（留 KEEP 份）→ 放回今晚要看的（update put-back，记下这一晚从几点算起）→ 印出定期更新的网站键
#   → 照现有的 archive run 跑这些网站（cli.py 不改）→ 算这一晚的变动清单和警报（update review）。
# 任何一步失败，后面的不做，状态档 reports/update-status.json 写明停在哪一步（update fail --step）；只有 run 失败时
# 照样算一次变动清单（不连网），再记停在 run（review 认得 run 有没有好好结束：没有就退出码 3）。
# 暂停：存档目录里放一个 update.pause，这一晚什么都不做。两晚不会重叠：/crawl/locks/nightly.lock（flock）。
# 导出（导出那一批 T7，11-16 晚用户「去吧」）：review 之后照状态档导今晚有变动的网站（警报压着的不导），最后写
# /work/archive-export/sites/seen.tsv。
# go-live 要在第一晚以前做一次（不在这个脚本里）。11-16 第一次真跑（只跑中文系）以后：排程只用一行固定的命令
# `docker compose -f C:\nknu-crawl\compose.yaml run --rm nightly`（compose.yaml 的 nightly 服务写死了程序、时区、全机锁、
# 备份位置和要挂的资料夹；Windows 工作排程器跑 nightly_update.cmd），开跑前先检查环境：时区是台北、设了全机锁、
# 存网页的资料夹能写、而且是账本记的那一个，不对就停在 preflight、什么都不动（status 写不成就由这个脚本自己写）。备份叫 nightly-*.db，
# 只清这种名字的（同一个资料夹里手动做的备份不会被删）。定期更新的网站 archive run 不再做不连网的对账（crawler
# 的 _reconciled_here：全校一晚要多 1.5–3 小时，wf_9c6e1448-2cc）。
# 测试用假的 python 跑（tests/archive/test_update_stage2_1116.py、test_update_fixes_1116.py、
# test_update_nightfix_1116.py）：PY、ROOT、BACKUPS、KEEP、SECRETS、LOG、LOCKS 都可以换。
set -u
PY="${PY:-python}"
ROOT="${ROOT:-/crawl/archive}"
BACKUPS="${BACKUPS:-/crawl/backup/nightly}"
KEEP="${KEEP:-7}"
SECRETS="${SECRETS:-/run/secrets/google.env}"
LOG="${LOG:-$ROOT/update.log}"
LOCKS="${LOCKS:-/crawl/locks}"
child=0
stopping=0
trap 'stopping=1; [ "$child" -ne 0 ] && kill -TERM "$child" 2>/dev/null' TERM INT

note() { echo "$(date '+%F %T') $*" >> "$LOG"; }
update() { "$PY" -m nknu.archive.update --dir "$ROOT" "$@"; }
stop_at() {
  note "停在：$1（后面的步骤没做）"
  if ! update fail --step "$1" >> "$LOG" 2>&1; then
    # the program itself does not load (no PYTHONPATH, a broken install): the status file is written here, so a
    # night that stopped is never one that left no word
    note "update fail 也跑不起来：状态档由脚本自己写"
    printf '{"failed_step": "%s", "failed_at": "%s", "note": "update fail 也跑不起来（程序载不进来？看 update.log）"}\n' \
      "$1" "$(date '+%FT%T%z')" > "$ROOT/reports/update-status.json" 2>/dev/null || note "状态档也没写成"
  fi
  exit 1
}

if [ -e "$ROOT/update.pause" ]; then
  note "有 update.pause：这一晚不更新（什么都没动）"
  exit 0
fi
if command -v flock >/dev/null 2>&1; then
  mkdir -p "$LOCKS" || exit 1
  exec 9> "$LOCKS/nightly.lock"
  if ! flock -n 9; then
    note "上一晚的定期更新还在跑：这一晚不开"
    exit 1
  fi
fi
mkdir -p "$ROOT/reports" || exit 1
note "定期更新开始"

# 0. 环境（11-16 第一晚的核对：排程的命令少一个设定，这一晚就悄悄出错）：台北时间（备份档名、log、状态档的时间；
#    UTC 和台北混着，留 KEEP 份时按名字排就排错）、全机锁（没设就是这个容器自己的锁，别的程序和它同时对学校发请求）、
#    存网页的资料夹能写（唯读挂进来，开跑才失败，放回的已经放了）
bad=""
[ "$(date +%z)" = "+0800" ] || bad="${bad} 时区不是台北（TZ=Asia/Taipei）；"
[ -n "${NKNU_SCHOOL_LOCK:-}" ] || bad="${bad} 没设全机锁 NKNU_SCHOOL_LOCK；"
{ [ -d "$ROOT/warc" ] && [ -w "$ROOT/warc" ]; } || bad="${bad} ${ROOT}/warc 不在或不能写；"
# 存网页的资料夹是不是账本记的那一个（11-23 原档搬到别的硬盘）：挂载的路径写错时 Docker 会自己建一个空资料夹，上面那条
# 「在、能写」照样过，这一晚就会把新抓的存进空资料夹、旧的一笔都读不回来。update warc-there 只读账本和档名：最新的档
# 不在（退出码 1，或程序跑不起来）就停在这里；只少旧的档（退出码 2）记一行、照常跑
if [ -z "$bad" ]; then
  there="$(update warc-there 2>&1)"
  rc=$?
  there="$(echo "$there" | tail -1)"
  if [ "$rc" -eq 2 ]; then
    note "$there"
  elif [ "$rc" -ne 0 ]; then
    bad=" ${there}；"
  fi
fi
if [ -n "$bad" ]; then
  note "环境不对：${bad}这一晚什么都没动（排程用 compose.yaml 的 nightly 服务）"
  stop_at preflight
fi
mkdir -p "$BACKUPS" || stop_at backup

# 1. 备份账本：sqlite 的 backup（读的是同一个时间点的整份，WAL 里的也在），整份检查过（integrity_check）、先写
#    .part 再改名；只留最新 KEEP 份（约 860 MB 一份：放 D 槽的话 BACKUPS 指过去）
"$PY" - "$ROOT/frontier.db" "$BACKUPS" "$KEEP" >> "$LOG" 2>&1 <<'PY' || stop_at backup
import sqlite3
import sys
import time
from pathlib import Path

src, out, keep = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
for left in out.glob("nightly-*.part*"):  # a backup a kill or a failure left half written
    left.unlink()
dst = out / f"nightly-{time.strftime('%Y%m%d-%H%M%S')}.db"  # only these are pruned (a hand-made backup stays)
part = dst.with_suffix(".part")
a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
b = sqlite3.connect(part)
a.backup(b)
ok = b.execute("PRAGMA integrity_check").fetchone()[0]
b.close()
a.close()
if ok != "ok":
    part.unlink()
    sys.exit(f"备份检查没过：{ok}")
part.replace(dst)
for old in sorted(out.glob("nightly-*.db"))[:-keep]:
    old.unlink()
print("备份了账本：", dst)
PY

[ "$stopping" -eq 0 ] || stop_at "backup（有人停了它）"
note "备份好了"

# 2. 放回今晚要看的（拿 run.lock：archive run 正在跑就不放，停在这一步）
update put-back >> "$LOG" 2>&1 || stop_at put-back
note "放回好了"
[ "$stopping" -eq 0 ] || stop_at "put-back（有人停了它，还没开跑）"

# 2b. 课表换学期（11-19）：课程查询页（这一站的首页，每晚都重抓）上的默认学期和账本里记的不一样，就重走查询表单、
#     把新学期的班级页交给今晚的抓取；新学期的页还在进的那几晚，放行这一站被每晚增长上限挡住的行（每晚最多 1,500）。
#     学期没变时不发任何请求。读不到那一页、没走完都只记下来，不挡这一晚（reports/schedule-term.json 写着这一步看到什么）
"$PY" -m nknu.archive.schedules --dir "$ROOT" night --gap 1-2 >> "$LOG" 2>&1 \
  || note "课表这一步没做成，或读不到学期（细节在上面）：这一晚照常，下一晚再看"
[ "$stopping" -eq 0 ] || stop_at "课表（有人停了它，还没开跑）"

# 3. 定期更新的网站键（go-live 写的）
SITES="$(update sites 2>> "$LOG")" || stop_at sites
[ -n "$SITES" ] || stop_at sites
[ "$stopping" -eq 0 ] || stop_at "sites（有人停了它，还没开跑）"

# 4. 照现有的 archive run 跑（金钥只有这个容器拿得到；绝不印出）
if [ -r "$SECRETS" ]; then
  set -a; . "$SECRETS"; set +a
else
  note "读不到金钥档（${SECRETS}）：云端硬盘、行事历的网址这一晚会等着（no_api_key）"
fi
# --end-wait 600（11-16 全校第一晚以后）：只剩在休息的服务器（连不上的每次歇 15 分钟、回 503 的也是）时这一晚就结束，
# 它们的网址留到下一晚。archive run 默认等 1800 秒，回过话的服务器要连不上约一天才放弃（CONN_GIVE_UP_ANSWERED），
# 那一晚会一直跑到第二天、下一晚开不了。结束原因是 waiting，review 照样算一整晚
"$PY" -c 'from nknu.admin.cli import app; app()' archive run --dir "$ROOT" --gap 1-2 --end-wait 600 --sites "$SITES" \
  >> "$LOG" 2>&1 &
child=$!
rc=0
while kill -0 "$child" 2>/dev/null; do
  wait "$child"
  rc=$?
done
child=0
run_stopped=$stopping
note "run 结束（退出码 ${rc}）"

# 5. 这一晚的变动清单、警报、状态档（只读存档、不连网；导出不跑）。run 失败了也算：抓到的照样算进清单
update review >> "$LOG" 2>&1
rv=$?
note "变动清单算好了（退出码 ${rv}）"
[ "$run_stopped" -eq 0 ] || stop_at "run（有人停了它）"
[ "$rc" -eq 0 ] || stop_at run
[ "$rv" -ne 3 ] || { note "run 没有好好结束（状态档写了原因）"; exit 1; }
[ "$rv" -eq 0 ] || stop_at review

LAB="$(cd "$(dirname "$0")" && pwd)"
# 5b. 要 JavaScript 的网站（第二轮第一批，11-17；计划 2026-11-17-jsfetch-integration 第 6 件）：JS_SITES_FILE（默认
#     $LAB/js_sites.txt，一行一个网站键，# 是注释）有网站时，浏览器开（jsfetch run：首页、列表每晚开，没开过的页也开）→
#     收下显示版（update js-take：显示出来的新网址排进待抓）→ 照旧抓那些页（archive run）→ 浏览器开新抓的页 → 再收下一次。
#     这几站的网址不在定期更新的清单里，所以放在变动清单之后、导出之前；哪一步出错只记下来，不挡这一晚的导出
JS_FILE="${JS_SITES_FILE:-$LAB/js_sites.txt}"
JS=""
[ -s "$JS_FILE" ] && JS="$(grep -v '^[[:space:]]*#' "$JS_FILE" | grep . | paste -sd, -)"
if [ -n "$JS" ] && [ "$stopping" -eq 0 ]; then
  note "JS 网站开始（${JS}）"
  js_ok=1
  "$PY" -m nknu.archive.jsfetch run --dir "$ROOT" --sites "$JS" --gap 1-2 >> "$LOG" 2>&1 || js_ok=0
  update js-take --sites "$JS" >> "$LOG" 2>&1 || js_ok=0
  "$PY" -c 'from nknu.admin.cli import app; app()' archive run --dir "$ROOT" --gap 1-2 --end-wait 600 --sites "$JS" \
    >> "$LOG" 2>&1 || js_ok=0
  "$PY" -m nknu.archive.jsfetch run --dir "$ROOT" --sites "$JS" --gap 1-2 >> "$LOG" 2>&1 || js_ok=0
  update js-take --sites "$JS" >> "$LOG" 2>&1 || js_ok=0
  if [ "$js_ok" -eq 1 ]; then note "JS 网站结束（都好）"; else note "JS 网站结束（有一步出错，细节在 update.log；这一晚照常导出）"; fi
fi

# 5c. 浏览器备用（11-22）：别的网站这一晚抓到了、但内容区不见或正文变空的网页（no_area、thin、empty_detail），用浏览器
#     再开一次（jsfetch fallback，每晚最多 50 页、每站 10 页）。开出来有原文没有的内容，导出就用浏览器版（浏览器显示版）；
#     开不出来照旧用上一次好的那份。设了 JS_FALLBACK=1、或 lab/ 里有 js_fallback.on 这个档才跑（默认不跑：还没在实验室
#     试过）；出错只记下来，不挡导出
if { [ "${JS_FALLBACK:-0}" = "1" ] || [ -e "$LAB/js_fallback.on" ]; } && [ "$stopping" -eq 0 ]; then
  note "浏览器备用开始"
  if "$PY" -m nknu.archive.jsfetch fallback --dir "$ROOT" --exclude-sites "$JS" --gap 1-2 >> "$LOG" 2>&1; then
    note "浏览器备用结束"
  else
    note "浏览器备用出错（细节在 update.log；这一晚照常导出）"
  fi
fi

# 6. 导出今晚有变动的网站（导出那一批 T7；设计 §7.4）：状态档的 sites_changed 去掉警报压着的（update export-list），
#    照导出服务的 export_sites.sh 导，NIGHTLY=1：定期更新的网站不看「还没爬完」（休息中的服务器的网址留到下一晚，
#    不挡导出）。每站的结果写进 EXPORT_RESULTS，给下一步写 seen.tsv。JS 网站每晚都列进去（导出依据算进浏览器显示版：
#    没变的照样「没有变动，跳过」）
EXPORT_SCRIPT="${EXPORT_SCRIPT:-$LAB/export_sites.sh}"
EXPORT_BASE="${EXPORT_BASE:-/work/archive-export/sites}"
LIST="$ROOT/reports/export-sites.txt"
RESULTS="$ROOT/reports/export-results.tsv"
update export-list > "$LIST" 2>> "$LOG" || stop_at export-list
if [ -n "$JS" ]; then
  { cat "$LIST"; echo "$JS" | tr ',' '\n'; } | grep . | sort -u > "$LIST.new" && mv "$LIST.new" "$LIST"
fi
: > "$RESULTS"
n=$(grep -c . "$LIST" || true)
if [ "$n" -gt 0 ]; then
  NIGHTLY=1 EXPORT_RESULTS="$RESULTS" EXPORT_BASE="$EXPORT_BASE" bash "$EXPORT_SCRIPT" "$LIST" >> "$LOG" 2>&1 \
    || stop_at export
  note "导出好了（${n} 个网站；每站的结果在 ${RESULTS}）"
else
  note "今晚没有要导出的网站"
fi

# 7. seen.tsv（内容 session 每晚进库读它：网站键、最后一次看过（台北时间，YYYY-MM-DD 开头）、今晚怎样）：最后写，
#    整份写好再改名；这一晚在这以前停了就不改写，内容那边看到的还是上一晚的
update seen --results "$RESULTS" --out "$EXPORT_BASE/seen.tsv" >> "$LOG" 2>&1 || stop_at seen
note "定期更新结束（导出 ${n} 个网站；seen.tsv 写好了）"
