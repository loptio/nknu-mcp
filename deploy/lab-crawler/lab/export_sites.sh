#!/usr/bin/env bash
# 导出服务（第 3 步设计第四节第 3 小步、附「导出」；11-08 用户：「先做导出，别让内容管线等」）：把清单里的网站一个一个
# 导出到 nknu-work 磁碟区，给内容 session 转文字、建库。用爬虫自己的程序（/crawl/code）只读它的存档，不连网。
#   - 开工先跑一次 nknu archive ready --json（和进度看板同一套判断）：还没爬完的网站（还有网址或按钮在排队）跳过，
#     记「还没爬完，跳过」；
#   - 这个网站有现用的那份（current.tsv），就加 --skip-same <它>：导出先读存档，算出这一次的「导出依据」（资料截至、
#     存档内容、程式、旧存档副本，写在 manifest.json），和现用那份的一样就什么都不写、退出码 3，记「没有变动，跳过」。
#     只比资料截至会漏掉不用重抓的改动（11-08 审查）：人工标记（nknu archive review）、rejudge、手改资料库、换了程式、
#     加了或换了 OLD_COPIES，这些都会让导出依据不一样、照导。换了程式（/crawl/code）以后，每个网站都会重导一次。
#     FORCE=1 时不比，照导；
#   - 这个网站有现用的那份，也加 --reuse <它>：附件和图片内容一样的（sha256 对得上）直接硬链接过来，不再从存档读、不再
#     多占一份（全部网站导一次约 60 GB，磁碟只放得下一份）；
#   - 加 --old-copies "$OLD_COPIES"：这一轮没抓到能用内容的行，用 10-24 旧存档的正常副本补上（缺口概况
#     第 ⑦ 项；结果、分类照这一轮的，「来源」栏写「10-24 旧存档」）。OLD_COPIES 是 old_copies_1108.py 在 Mac 上做的副本
#     资料夹（index.json 和 files/，不是 10-24 旧存档本身），要先传到实验室电脑、挂进容器。11-17 起没设就用实验室的
#     /crawl/old-archive-2026-10-24/copies-1108（11-16 晚全量重导和每晚导出都忘了设，711 行丢了副本）；只有设成空的
#     （-e OLD_COPIES=）才不用。读不了
#     （路径打错、没挂进容器、index.json 坏了），开工就停，一个都不导（不然这一批会导成没有补缺口的、还被当成现用的）；
#   - 每站先写进 /work/archive-export/sites/<网站>-<时间>.partial，导完、manifest.json 读得出来，才改名成正式资料夹；
#   - 开工时清掉上次残留的 .partial；
#   - 每站只留现用的和上一份，更旧的删掉（硬链接的档不会跟着没：删的只是旧资料夹里的那个名字）；内容 session 的导出清单
#     （EXPORT_LISTS 资料夹里 exports 开头的 .txt，平时是 /work/research/pipeline_2026-11-02/：build.sh 读 exports.txt，
#     或 EXPORTS 指的另一份，例如 exports-pilot.txt）写着的资料夹一律不删，它的建库要读（11-08 内容 session 的要求）；
#     那个资料夹里一份清单都找不到时，更旧的一个都不删，并在 export.log 里说出来（11-23）；
#   - 导好的写进 current.tsv（网站键<TAB>资料夹，一站一行），失败的写进 failed.tsv；过程写 export.log。
#   - NIGHTLY=1（每晚的定期更新叫它，导出那一批 T7）：不看「还没爬完」（休息中的服务器的网址留到下一晚，不挡导出）；
#     设了 EXPORT_RESULTS 就把每站的结果写进那个档（网站键<TAB>ok｜unchanged｜failed｜not_ready｜held，held 再加<TAB>原因），
#     给 seen.tsv 用。
#   - 副本漏带就拦下（11-17，爬虫judge 照用户 11-17 的授权：「上一份用了旧存档副本、这一份没用上就停下来说明」）：现用的
#     那份有一行是用 10-24 旧存档副本补上的，这一份那一行既没有副本、也没有这一轮自己抓到的档、也不是学校已删除，
#     就不换成现用的：资料夹改名成 <网站>-<时间>.held（里面 held.txt 列出每一行），记 held 和原因，接着导下一站；
#     每晚的导出把它留在「还欠导出」。核对过确实要用这一份：加 -e ALLOW_COPY_DROP=1 再导一次。
# 网站键里的 / 在资料夹名里换成 _（c.nknu.edu.tw/aca → c.nknu.edu.tw_aca-20261108-204012，导出开始的时间）。
# 用法（实验室电脑，cmd 前面加 set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" &）：
#   docker run -d --name nknu-export --network none -e TZ=Asia/Taipei -e PYTHONPATH=/crawl/code/src
#     -e PYTHONDONTWRITEBYTECODE=1 -w /crawl/code -v nknu-crawl:/crawl -v E:/nknu/warc:/crawl/archive/warc:ro
#     -v nknu-work:/work -v C:/nknu-crawl/tools:/tools:ro nknu-pipeline bash /tools/export_sites.sh /tools/<清单>.txt
#   全部重导加 -e FORCE=1；副本资料夹不在默认的地方就加 -e OLD_COPIES=<副本资料夹在容器里的路径>（例如传到 nknu-crawl 磁碟区的
#   /crawl/old-copies-1024）。副本资料夹有学生全名和学号，只用 SSH 或随身硬盘传。
# 清单一行一个网站键，# 后面是注释；照清单的先后导（11-08 用户：先全校性单位，再系所）。
# 变量后面紧接中文时一律写成 ${变量}：Mac 的 bash 3.2 会把中文的第一个位元组当成变量名的一部分（11-08 测试时发现）
set -uo pipefail
LIST=${1:?usage: export_sites.sh <site list>}
BASE=${EXPORT_BASE:-/work/archive-export/sites}
ARCHIVE=${ARCHIVE_DIR:-/crawl/archive}
LISTS=${EXPORT_LISTS:-/work/research/pipeline_2026-11-02}
LOG=$BASE/export.log
mkdir -p "$BASE"
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
# one export at a time on this folder (the export batch's review, F7: a nightly export starting while a manual one runs
# would clear its .partial folders, overwrite ready.tsv and race on current.tsv): wait for the other, at most
# EXPORT_LOCK_WAIT seconds (two hours), else export nothing (a night's sites stay owed for the next night)
if command -v flock >/dev/null 2>&1; then
  exec 8> "$BASE/.export.lock"
  if ! flock -w "${EXPORT_LOCK_WAIT:-7200}" 8; then
    say "另一个导出在用 ${BASE}，等了 ${EXPORT_LOCK_WAIT:-7200} 秒还没做完：这一批不导"
    exit 1
  fi
fi
nknu() { python -c 'from nknu.admin.cli import app; app()' "$@"; }
field() {  # field <file> <site> <column>: that column of the site's line (Tab 分隔), 没有就空的
  [ -f "$1" ] && awk -F'\t' -v s="$2" -v c="$3" '$1 == s {print $c; exit}' "$1"
}
# 内容那边的导出清单一份都找不到（它的资料夹搬走了、清单改了名）时，更旧的导出一个都不删（11-23，内容 session 发现的）：
# 不然每个资料夹都会被当成没人用，照「每站留两份」删下去，库还在读的旧导出就跟着没了。读不了的清单本来就当成写着。
no_lists=0
if ! ls "$LISTS"/exports*.txt >/dev/null 2>&1; then
  no_lists=1
  say "找不到内容那边的导出清单（${LISTS} 里没有 exports 开头的 .txt）：这一次更旧的导出一个都不删"
fi
for p in "$BASE"/*.partial; do
  [ -e "$p" ] || continue
  say "清掉上次残留的 $p"
  rm -rf "$p"
done
in_use() {  # in_use <folder>: prints the content stage's export list that names it (each line read as build_helpers.py
  # reads it: trimmed, \ as /, a trailing / dropped, # a comment), exit 0; a list it cannot read counts as naming it
  python - "$1" "$LISTS" <<'PY'
import sys
from pathlib import Path

name = Path(sys.argv[1]).name
for f in sorted(Path(sys.argv[2]).glob("exports*.txt")):
    try:
        lines = f.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as e:
        print(f"{f}（读不了，{type(e).__name__}，当成写着）")
        sys.exit(0)
    for line in lines:
        line = line.strip().replace("\\", "/").rstrip("/")
        if line and not line.startswith("#") and line.rsplit("/", 1)[-1] == name:
            print(f)
            sys.exit(0)
sys.exit(1)
PY
}
result() {  # result <site> <what> [why]: the night's result of a site (EXPORT_RESULTS, export batch T7; why: held's)
  [ -n "${EXPORT_RESULTS:-}" ] && printf '%s\t%s%s\n' "$1" "$2" "${3:+$(printf '\t%s' "$3")}" >> "$EXPORT_RESULTS"
  return 0
}
dropped_copies() {  # dropped_copies <current> <new>: prints why the new export may not replace the current one, if it
  # drops a row the current one filled from the 10-24 archive's copy (export.copies_dropped; 11-17 爬虫judge) and
  # writes held.txt into the new folder; prints nothing when it drops none. A comparison that fails holds it too.
  python - "$1" "$2" <<'PY' 2>>"$LOG" || echo "没比成上一份（细节在 export.log），先拦下"
import sys
from pathlib import Path

from nknu.archive.export import copies_dropped

rows = copies_dropped(Path(sys.argv[1]), Path(sys.argv[2]))
if rows:
    lines = ["网址\t这一份那一行"] + [f"{u}\t{w}" for u, w in rows]
    Path(sys.argv[2], "held.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} 行上一份用 10-24 旧存档副本补上的，这一份没补上（例：{rows[0][0]}，{rows[0][1]}）")
PY
}
put_line() {  # put_line <file> <site> <value>: one line per site
  local f=$1 site=$2 value=$3
  { [ -f "$f" ] && awk -F'\t' -v s="$site" 'NF && $1 != s' "$f"; printf '%s\t%s\n' "$site" "$value"; } > "$f.new"
  mv "$f.new" "$f"
}
# unset: the lab's copies folder (11-17: the go-live re-export and the nightly export forgot it); '' only: none
OLD_COPIES=${OLD_COPIES-/crawl/old-archive-2026-10-24/copies-1108}
if [ -n "${OLD_COPIES:-}" ] && ! python - "$OLD_COPIES" >>"$LOG" 2>&1 <<'PY'
import sys

from nknu.archive.export import OldCopies

why = OldCopies(sys.argv[1]).why
print(f"OLD_COPIES {sys.argv[1]}：{why or '读得到'}")
sys.exit(1 if why else 0)
PY
then
  say "OLD_COPIES=$OLD_COPIES 读不了（原因在 ${LOG}）：这一批不导；路径改对（容器里的路径），或设成空的（OLD_COPIES=）就不用副本"
  exit 1
fi
sites=()
while IFS= read -r line || [ -n "$line" ]; do
  site=$(printf '%s' "${line%%#*}" | tr -d '\r' | xargs)
  [ -n "$site" ] && sites+=("$site")
done < "$LIST"
if [ ${#sites[@]} -eq 0 ]; then
  say "清单 $LIST 里没有网站"
  exit 1
fi
# 哪些网站现在可以导出、各自的资料截至：清单上的网站一次问完，写成 ready.tsv（网站键<TAB>1 可以导出/0<TAB>资料截至）
if ! nknu archive ready --dir "$ARCHIVE" --sites "$(IFS=,; echo "${sites[*]}")" --json >"$BASE/ready.json" 2>>"$LOG" \
   || ! python - "$BASE/ready.json" >"$BASE/ready.tsv" 2>>"$LOG" <<'PY'
import json
import sys

for r in json.load(open(sys.argv[1], encoding="utf-8"))["网站"]:
    print(f"{r['网站']}\t{1 if r['可以导出'] else 0}\t{r['资料截至']}")
PY
then
  say "看不到哪些网站爬完了（nknu archive ready 失败，原因在 ${LOG}），这一批不导"
  exit 1
fi
say "这一批 ${#sites[@]} 个网站，现在可以导出的 $(awk -F'\t' '$2 == 1' "$BASE/ready.tsv" | wc -l | tr -d ' ') 个$([ "${FORCE:-0}" = 1 ] && echo '（FORCE=1：没有变动的也重导）')"
ok=0
bad=0
skipped=0
held=0
for site in "${sites[@]}"; do
  slug=${site//\//_}
  # 人工暂缓（11-17，爬虫judge：内容 session 今晚不在，教师履历 5.8 万行那份在用户决定以前不能变成现用的）：导出资料夹里
  # export-hold.txt 写着的网站（网站键<TAB>原因，# 后面是注释）不导，记 held 和原因（每晚的导出把它留在「还欠导出」），
  # 现用的不动。拿掉那一行就恢复
  hold=$([ -f "$BASE/export-hold.txt" ] && awk -F'\t' -v s="$site" '$0 !~ /^[[:space:]]*#/ && $1 == s {print $2; exit}' "$BASE/export-hold.txt")
  if [ -n "$hold" ] || { [ -f "$BASE/export-hold.txt" ] && awk -F'\t' -v s="$site" '$0 !~ /^[[:space:]]*#/ && $1 == s {f=1} END {exit !f}' "$BASE/export-hold.txt"; }; then
    held=$((held + 1))
    result "$site" held "人工暂缓：${hold:-export-hold.txt 写着}"
    say "暂缓 ${site}：export-hold.txt 写着（${hold:-没写原因}），这一次不导，现用的不动"
    continue
  fi
  if [ "$(field "$BASE/ready.tsv" "$site" 2)" != 1 ] && [ "${NIGHTLY:-0}" != 1 ]; then
    say "$site 还没爬完，跳过（还有网址或按钮在排队，或存档里没有这个网站：nknu archive ready 看得到）"
    skipped=$((skipped + 1))
    result "$site" not_ready
    continue
  fi
  as_of=$(field "$BASE/ready.tsv" "$site" 3)
  cur=$(field "$BASE/current.tsv" "$site" 2)
  [ -n "$cur" ] && [ ! -f "$cur/manifest.json" ] && cur=""
  # 名字带到秒，同一秒导第二次就等一秒：同名的话 mv 会把新的搬进现用的那份里面；名字照时间排，留两份时才删得对
  # （11-08 以前的名字只到分，排起来一样在前面）
  name="$slug-$(date +%Y%m%d-%H%M%S)"
  while [ -e "$BASE/$name" ] || [ -e "$BASE/$name.partial" ]; do
    sleep 1
    name="$slug-$(date +%Y%m%d-%H%M%S)"
  done
  out="$BASE/$name.partial"
  opts=()
  [ -n "$cur" ] && opts+=(--reuse "$cur")
  [ -n "$cur" ] && [ "${FORCE:-0}" != 1 ] && opts+=(--skip-same "$cur")
  [ -n "${OLD_COPIES:-}" ] && opts+=(--old-copies "$OLD_COPIES")
  say "开始 $site → $name${cur:+（附件、图片内容一样的从 $cur 硬链接）}"
  nknu archive export --dir "$ARCHIVE" --site "$site" --out "$out" ${opts[@]+"${opts[@]}"} >>"$LOG" 2>&1
  code=$?
  if [ "$code" = 3 ]; then  # export.UNCHANGED: --skip-same found nothing that would change, wrote nothing
    rm -rf "$out"
    say "$site 没有变动，跳过（导出依据和现用的 $cur 一样，资料截至 ${as_of:-（还没抓到东西）}）"
    skipped=$((skipped + 1))
    result "$site" unchanged
    continue
  fi
  if [ "$code" = 0 ] \
     && python -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$out/manifest.json" >>"$LOG" 2>&1; then
    why=""
    [ -n "$cur" ] && [ "${ALLOW_COPY_DROP:-0}" != 1 ] && why=$(dropped_copies "$cur" "$out")
    if [ -n "$why" ]; then
      for old in "$BASE/$slug"-2*.held; do [ -e "$old" ] && rm -rf "$old"; done  # only the newest held one is kept
      mv "$out" "$BASE/$name.held"
      held=$((held + 1))
      result "$site" held "$why"
      say "拦下 ${site}：${why}；现用的还是 ${cur}，这一份留在 $BASE/$name.held（held.txt 列出每一行；核对过要用就加 ALLOW_COPY_DROP=1 再导）"
      continue
    fi
    for old in "$BASE/$slug"-2*.held; do [ -e "$old" ] && rm -rf "$old"; done  # a held one this export now replaces
    mv "$out" "$BASE/$name"
    # all but the newest two (awk, not GNU head -n -2: the same on the Mac, where the tests run it too)
    ls -d "$BASE/$slug"-2*[0-9] 2>/dev/null | sort | awk '{a[NR] = $0} END {for (i = 1; i <= NR - 2; i++) print a[i]}' \
      | while IFS= read -r old; do
      [ "$no_lists" -eq 0 ] || continue
      if lst=$(in_use "$old"); then
        say "没删这个网站更旧的一份 ${old}：内容 session 的导出清单 ${lst} 写着它（建库要读）"
        continue
      fi
      say "删掉这个网站更旧的一份 $old"
      rm -rf "$old"
    done
    put_line "$BASE/current.tsv" "$site" "$BASE/$name"
    [ -f "$BASE/failed.tsv" ] && awk -F'\t' -v s="$site" 'NF && $1 != s' "$BASE/failed.tsv" > "$BASE/failed.tsv.new" \
      && mv "$BASE/failed.tsv.new" "$BASE/failed.tsv"
    ok=$((ok + 1))
    result "$site" ok
    say "好了 ${site}（$(du -sh "$BASE/$name" | cut -f1)）"
  else
    bad=$((bad + 1))
    result "$site" failed
    put_line "$BASE/failed.tsv" "$site" "$(date '+%F %T')"
    say "失败 ${site}：资料夹 $out 留着看原因，下次开工时清掉"
  fi
done
say "这一批结束：成功 ${ok}，失败 ${bad}，跳过 ${skipped}，拦下 ${held}（current.tsv 是每站现用的资料夹）"
