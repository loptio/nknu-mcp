#!/usr/bin/env bash
# 实验室电脑上的一条转文字线（lab_pc 套件，11-03；计划 2026-11-08 T5 起转导出清单里的每一份导出）：在 Docker 容器里跑，
# 和 Mac 的 research/pipeline_2026-11-02/totext_lanes.sh 是同一个循环——一份导出跑到正常结束（退出码 0）就换下一份；
# 看门狗结束（3）、跑满 40 分钟（4）或崩溃就同一份重开下一轮，设定错误（2）就停；最多 400 轮，清单里每份导出再多一轮。
#
# 用法（compose 的 lane-a、lane-b、lane-c 服务替你启动，平常不用自己打）：bash lanes.sh A|C|B
#   A：PDF、Office（路线 pdf,office,office_old）  C：压缩档（archive）  B：网页、图片（page,image,data,unsupported；
#   11-03 在 Mac 上转过；计划 2026-11-08 T2 改了读网页的规则，旧的网页结果都要重读，所以这台也开 B 线）。
#   A、C 照 Mac 的做法：学报和考题（totext_defer_urls.txt 列的）排在每份导出的最后。三条线都每 40 分钟换一个新程序。
# 导出清单（计划 2026-11-08 T5）：一行一个导出资料夹，写法、放哪见 build_helpers.py（$EXPORTS，否则
#   research/pipeline_2026-11-02/exports.txt，都没有就只转教务处 11-02 的导出）。按清单的顺序一份一份转，所有导出共用
#   一个缓存 research/pipeline_2026-11-02/totext/cache（按档的内容存，同一个档在几份导出里只转一次）。清单走完时再读一次
#   清单：线开着时加进清单的导出接着转，没有新的才结束。清单不能用（资料夹不在、没有 manifest.json、同一个网站两份）
#   就不开跑，日志里有原因。
#   RETRY_FIRST：和 Mac 一样，只要不是空的（例如 RETRY_FIRST=1）就重转这次开线以前留下中断标记的档（上次转到一半中断的，
#   也包括真的让程序崩溃过或被看门狗结束的档），每个档这次开线只再试一次：每一轮都加 --retry-crashed-before <开线时间>，
#   再崩溃的档标记变新，之后哪份导出的哪一轮都跳过它（几份导出共用的档也只试一次，跑满 40 分钟换程序也不漏）。不设或设成空的
#   就不加。（计划 2026-11-08 T5 的审查：原来是每份导出的第一轮加 --retry-crashed，缓存是共用的，几份导出都有的档真的会让
#   程序崩溃时每份导出各试一次，卡住的每次耗掉看门狗的 62 分钟；第一轮跑满 40 分钟还没轮到的档也不重转。）
#   A、C 线每次开线先看 lab_pc/retry_cut_members.<A|C>.done：没有就先跑一次 retry_cut_members.py，把 Mac 停线时
#   打断的档（A 两个 PDF；C 七个压缩档里的教務會議附件）放回「还没转」，做完留下那个 .done 档，之后不再跑。
#   强制结束（停电、重开机、docker 等不及 2 分钟直接砍掉）：每一轮开始前写 lab_pc/lane<X>.inflight（这一轮的开始时间），
#   这一轮结束（不管程序怎么结束）就删掉；所以开线时它还在，就是上次这个脚本自己被砍了。三条线这时都先跑
#   retry_cut_members.py --after-stop，把那一轮转到一半的档（C 线连压缩档里那个成员）放回「还没转」：RETRY_FIRST
#   只管导出里的档，压缩档的成员留着中断标记就永远记成失败。它要知道哪些档是这条线的：清单里每份导出的档，
#   由 build_helpers.py jobs 列出（第一次要把导出读一遍）。
# 日志：research/pipeline_2026-11-02/lab_pc/lane<A|C|B>.log，同时印到画面（docker logs nknu-lane-A 也看得到）。
#   每一轮前一行「导出 第几份/共几份：名字（资料夹）」，再一行「run N start …」。
# 停线：docker compose stop 送 SIGTERM，这里改成 Ctrl-C（SIGINT）转给转文字程序：它把手上那个档放回「还没转」再结束，
#   下次开线不用加 RETRY_FIRST。compose 给 2 分钟（stop_grace_period）；来不及被强制结束的话，下次开线时上面的
#   --after-stop 会把那个档放回去（加 RETRY_FIRST=1 也行）。
#   怎么看：停干净的话，日志最后两行是「按要求停了」和「done」；最后一行不是 done，就是被强制结束了。
# 同一个字母的线同时只能有一条：开线时锁住 lab_pc/lane<A|C|B>.lock（build.sh 也看这个锁，知道线还在跑）。
# A、C 线看不到 GPU（torch 认不出 CUDA）就不开；NKNU_ALLOW_CPU=1 可以硬用 CPU 跑（慢很多）。B 线不用 GPU。
# 退出码：0 清单里每份导出、这条线的档都走过一遍了；2 设定错误、缺档、清单不能用、没有 GPU 或已经有一条同字母的线，
#   没跑；130 按要求停了；1 其他（例如跑满轮数）。
# 测试用：NKNU_LANE_WORK（默认 /work）、NKNU_LANE_PYTHON（默认 /opt/venv/bin/python）。
set -u

LANE=${1:-}
case $LANE in
  A) ROUTES=pdf,office,office_old ;;
  C) ROUTES=archive ;;  # 11-03 05:15 on the Mac: archives (mostly 教務會議 records) got their own lane
  B) ROUTES=page,image,data,unsupported ;;
  *) echo "用法：lanes.sh A|C|B（A：PDF、Office；C：压缩档；B：网页、图片）" >&2; exit 2 ;;
esac

WORK=${NKNU_LANE_WORK:-/work}
PY=${NKNU_LANE_PYTHON:-/opt/venv/bin/python}  # the image's venv, called directly (no uv run)
R=research/pipeline_2026-11-02
KIT=$R/lab_pc  # the state folder: this lane's lock, log, stamp and in-flight mark (no commit holds it)
# 11-19: the programs are in the repository (deploy/lab-pipeline), beside this script
PROG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HELP=$PROG/build_helpers.py  # the export list (plan 2026-11-08 T5) and the job list the forced-stop repair reads
OUT=$R/totext               # every export's: one cache, keyed by content
DEFER_FILE=$R/totext_defer_urls.txt
FIX=$PROG/retry_cut_members.py
STAMP=$KIT/retry_cut_members.$LANE.done
INFLIGHT=$KIT/lane$LANE.inflight  # a run's start time while it runs; left behind only when this script was killed
MAX_RUNS=400  # plus one run for each export of the list 【Claude】
MAX_MINUTES=40  # a fresh process this often (11-03); lane B too since plan 2026-11-08 T5 【Claude】
export TZ=${TZ:-CST-8}                   # log times in Taiwan time (the image sets Asia/Taipei)
export PYTHONUNBUFFERED=1                # the program's lines reach the log and docker logs as they happen
export PYTHONPATH=${PYTHONPATH:-$WORK/src}  # the image sets /work/src: our code runs from the volume

cd "$WORK" || { echo "[lane ${LANE}] 找不到 ${WORK}（nknu-work 磁碟区没挂上？）" >&2; exit 2; }
mkdir -p "$KIT" || exit 2
LOG=$KIT/lane$LANE.log
# from here on everything goes to the log and to the screen; tee ignores Ctrl-C and SIGTERM, so the last lines get out
exec > >(trap '' INT TERM HUP; exec tee -a "$LOG") 2>&1
TEE_PID=${!:-}

ts() { date '+%F %T'; }

finish() {  # exit with $1 once tee has written everything (the container ends, and kills tee, when this script does)
  exec 1>&- 2>&-
  if [[ -n $TEE_PID ]]; then wait "$TEE_PID" 2>/dev/null; fi
  exit "$1"
}

# one lane of each letter at a time across containers sharing the volume; build_helpers.py reads the same lock
if command -v flock >/dev/null 2>&1; then
  exec 9>>"$KIT/lane$LANE.lock"
  if ! flock -n 9; then
    echo "[lane ${LANE}] 已经有一条 ${LANE} 线在跑（${KIT}/lane${LANE}.lock 被占着），这条不开 $(ts)"
    finish 2
  fi
else
  echo "[lane ${LANE}] 这台没有 flock：不锁 ${KIT}/lane${LANE}.lock，别同时开两条 ${LANE} 线 $(ts)"
fi

missing=()
for path in "$PY" src/nknu/totext/run.py "$HELP"; do
  if [[ ! -e $path ]]; then missing+=("$path"); fi
done
if [[ $LANE != B && ! -e $DEFER_FILE ]]; then missing+=("$DEFER_FILE"); fi
if (( ${#missing[@]} > 0 )); then
  echo "[lane ${LANE}] 缺这些档，没开跑：${missing[*]}（映像建好了吗？base.tar、update.tar 解开了吗？）$(ts)"
  finish 2
fi

# The exports of the list as it is now (build_helpers.py exports: one per line, folder<TAB>name<TAB>site, checked; why
# it cannot be used goes to the log): LIST_DIRS and LIST_NAMES. 1, the arrays untouched, when the list is not usable.
LIST_DIRS=()
LIST_NAMES=()
read_list() {
  local listing folder name site
  local -a dirs=() names=()
  listing=$("$PY" "$HELP" exports) || return 1
  while IFS=$'\t' read -r folder name site; do
    if [[ -n $folder ]]; then dirs+=("$folder"); names+=("$name"); fi
  done <<< "$listing"
  (( ${#dirs[@]} > 0 )) || return 1
  LIST_DIRS=("${dirs[@]}")
  LIST_NAMES=("${names[@]}")
}
if ! read_list; then
  echo "[lane ${LANE}] 导出清单不能用（原因在上一行），没开跑 $(ts)"
  finish 2
fi

RETRY=0
if [[ -n ${RETRY_FIRST:-} ]]; then RETRY=1; fi  # the Mac's meaning: any non-empty value

child=0
stops=0
trapped=0
# shellcheck disable=SC2329  # called by the trap below
on_signal() {  # docker compose stop (SIGTERM), Ctrl-C, a closed terminal: ask the program to finish, never restart
  trapped=1
  stops=$((stops + 1))
  if (( child == 0 )); then return 0; fi
  if (( stops == 1 )); then
    echo "[lane ${LANE}] 收到停止信号：请转文字程序把手上的档放回「还没转」再结束 $(ts)"
    kill -INT "$child" 2>/dev/null
  else
    echo "[lane ${LANE}] 又收到停止信号：直接结束转文字程序（手上的档留下中断标记，下次开线加 RETRY_FIRST=1）$(ts)"
    kill -TERM "$child" 2>/dev/null
  fi
}
trap on_signal TERM INT HUP

# bash starts a background command with SIGINT ignored, and Python then never installs its KeyboardInterrupt handler:
# GNU env (coreutils 8.31 and later; Ubuntu 24.04 has 9.4) puts the default back first, so the forwarded Ctrl-C arrives
RESET_INT=()
if env --default-signal=INT true >/dev/null 2>&1; then
  RESET_INT=(env --default-signal=INT)
else
  echo "[lane ${LANE}] 这台的 env 不支持 --default-signal：停线时转文字程序收不到 Ctrl-C，只能直接结束（下次开线加 RETRY_FIRST=1）$(ts)"
fi

if [[ $LANE != B ]]; then
  "$PY" - <<'PYEOF'
import sys

import torch

ok = torch.backends.cuda.is_built() and torch.cuda.is_available()  # Docling's own test (decide_device)
gpu = torch.cuda.get_device_name(0) if ok else "看不到（torch.cuda.is_available() 是 False）"
print(f"torch {torch.__version__}（CUDA {torch.version.cuda}），GPU：{gpu}", flush=True)
sys.exit(0 if ok else 1)
PYEOF
  gpu_rc=$?
  if (( gpu_rc != 0 )); then
    if [[ ${NKNU_ALLOW_CPU:-} == 1 ]]; then
      echo "[lane ${LANE}] 没有 GPU，照 NKNU_ALLOW_CPU=1 用 CPU 跑（慢很多）$(ts)"
    else
      echo "[lane ${LANE}] 没有 GPU，没开跑：先跑 check 服务看原因（显卡驱动要 580 版以上：这个 torch 用 CUDA 13；" \
           "compose 要把 GPU 给容器）。真的要用 CPU 跑就加 NKNU_ALLOW_CPU=1 $(ts)"
      finish 2
    fi
  fi
  if (( stops == 0 )) && [[ ! -e $STAMP ]]; then
    echo "[lane ${LANE}] 先把 Mac 停线时打断的档放回「还没转」（只做一次）$(ts)"
    fix_rc=127
    if [[ -e $FIX ]]; then
      "$PY" "$FIX" --lane "$LANE" --cache "$OUT/cache" --stamp "$STAMP"
      fix_rc=$?
    fi
    if (( fix_rc != 0 )); then
      echo "[lane ${LANE}] retry_cut_members.py 没做完（退出码 ${fix_rc}），照样开跑；那几个档会记成中断，下次开线再试 $(ts)"
    fi
  fi
fi
# the note a killed run left: put back the files that run was converting (a crash ends only the program, and the
# loop below removes the note then, so this never touches a file that really crashed it). Every lane since plan
# 2026-11-08 T5; the repair tells this lane's files by the job list of every export of the list.
if (( stops == 0 )) && [[ -e $INFLIGHT ]]; then
  since=$(head -n 1 "$INFLIGHT" 2>/dev/null)
  echo "[lane ${LANE}] 上次这条线是被强制结束的（停电、重开机或 docker 等不及）：先把那时转到一半的档放回「还没转」 $(ts)"
  after_rc=127
  jobs_file=$(mktemp "${TMPDIR:-/tmp}/lane$LANE-jobs.XXXXXX" 2>/dev/null) || jobs_file=""
  if [[ -e $FIX && -n $since && -n $jobs_file ]] && "$PY" "$HELP" jobs --out "$jobs_file"; then
    "$PY" "$FIX" --lane "$LANE" --cache "$OUT/cache" --jobs "$jobs_file" --after-stop "$since"
    after_rc=$?
  fi
  if [[ -n $jobs_file ]]; then rm -f "$jobs_file"; fi
  if (( after_rc != 0 )); then
    echo "[lane ${LANE}] 放回没做完（退出码 ${after_rc}）：那个档会记成中断；告诉 Claude（压缩档里的成员 RETRY_FIRST 也救不回来） $(ts)"
  fi
  rm -f "$INFLIGHT"
fi

# RETRY_FIRST (header; review of plan 2026-11-08 T5): every run of this lane start retries the files whose 'started'
# marker is older than this moment, each once, since one that crashes or hangs again now gets a newer marker
# (Cache.begin, the watchdog). Taken after the repairs above, which only remove markers; sub-second when bash has it,
# with a decimal point whatever the locale writes
LANE_START=${EPOCHREALTIME:-$(date +%s)}
LANE_START=${LANE_START/,/.}
retry_words=$([[ $RETRY == 1 ]] && echo 是 || echo 否)
echo "[lane ${LANE}] 开线：路线 ${ROUTES}；导出 ${#LIST_DIRS[@]} 份；重转这次开线以前被打断的档（各一次）：${retry_words} $(ts)"
declare -A walked=()   # export folder -> 1 once a run on it ended with 0 in this lane start
max_runs=$((MAX_RUNS + ${#LIST_DIRS[@]}))
rc=0
ended=runs  # all: every export walked; error: a setting error; runs: max_runs reached
i=0
while (( stops == 0 )); do
  next=-1
  for k in "${!LIST_DIRS[@]}"; do
    if [[ -z ${walked[${LIST_DIRS[k]}]:-} ]]; then next=$k; break; fi
  done
  if (( next < 0 )); then  # through the list: read it again, for exports added while this lane ran
    if ! read_list; then
      echo "[lane ${LANE}] 再读一次导出清单读不了（原因在上一行），照原来的清单算 $(ts)"
    fi
    fresh=0  # every export the list held is walked by now: what is not walked is new
    for k in "${!LIST_DIRS[@]}"; do
      if [[ -z ${walked[${LIST_DIRS[k]}]:-} ]]; then
        if (( next < 0 )); then next=$k; fi
        fresh=$((fresh + 1))
      fi
    done
    if (( next < 0 )); then ended=all; break; fi
    echo "[lane ${LANE}] 清单里多了 ${fresh} 份导出（现在共 ${#LIST_DIRS[@]} 份），接着转 $(ts)"
    max_runs=$((max_runs + fresh))
  fi
  if (( i >= max_runs )); then ended=runs; break; fi
  i=$((i + 1))
  export_dir=${LIST_DIRS[next]}
  EXTRA=()
  if (( RETRY == 1 )); then EXTRA=(--retry-crashed-before "$LANE_START"); fi  # every run: each old crash once 【Claude】
  PACE=(--max-minutes "$MAX_MINUTES")  # a fresh process (11-03: one grew to 6.3 GB in a few hours)
  if [[ $LANE != B ]]; then PACE=(--defer "$DEFER_FILE" "${PACE[@]}"); fi  # journals, exams last
  echo "[lane ${LANE}] 导出 $((next + 1))/${#LIST_DIRS[@]}：${LIST_NAMES[next]}（${export_dir}）"
  echo "[lane ${LANE}] run $i start $(ts)"
  # sub-second when bash has it (5.0+: Ubuntu 24.04's 5.2), so a file that crashed the run before, in the same second,
  # stays older than this run
  printf '%s\n' "${EPOCHREALTIME:-$(date +%s)}" > "$INFLIGHT.tmp" && mv -f "$INFLIGHT.tmp" "$INFLIGHT"
  ${RESET_INT[@]+"${RESET_INT[@]}"} "$PY" -m nknu.totext run --export "$export_dir" --out "$OUT" --routes "$ROUTES" \
    ${EXTRA[@]+"${EXTRA[@]}"} "${PACE[@]}" &
  child=$!
  if (( stops > 0 )); then kill -INT "$child" 2>/dev/null; fi  # a stop that came while it was starting
  while :; do
    trapped=0
    wait "$child"
    rc=$?
    if (( trapped )) && kill -0 "$child" 2>/dev/null; then continue; fi  # a signal ended the wait, not the program
    if (( trapped )); then  # it ended right after the signal: its own exit code, when bash still has it
      wait "$child" 2>/dev/null
      again=$?
      if (( again != 127 )); then rc=$again; fi
    fi
    break
  done
  child=0
  rm -f "$INFLIGHT"  # the run ended (any exit code): nothing it left behind came from a forced stop
  echo "[lane ${LANE}] run $i exit $rc $(ts)"
  if (( rc == 0 )); then walked[$export_dir]=1; fi
  if (( rc == 2 )); then ended=error; break; fi
done

if (( stops > 0 )); then
  echo "[lane ${LANE}] 按要求停了；下次开线前用 progress 看进度 $(ts)"
  echo "[lane ${LANE}] done $(ts)"
  finish 130
fi
case $ended in
  all) echo "[lane ${LANE}] 这条线的档都走过一遍了（导出 ${#LIST_DIRS[@]} 份；转好、失败、跳过都记在缓存里）；用 progress 看还剩什么 $(ts)" ;;
  error) echo "[lane ${LANE}] 设定错误，停了：看上面「错误：」那一行（导出 ${export_dir}） $(ts)" ;;
  *) echo "[lane ${LANE}] 跑满 ${max_runs} 轮还没结束（最后一轮退出码 ${rc}）；看上面的错误，再开一次线 $(ts)" ;;
esac
echo "[lane ${LANE}] done $(ts)"
case $ended in
  all) finish 0 ;;
  error) finish 2 ;;
  *) finish 1 ;;
esac
