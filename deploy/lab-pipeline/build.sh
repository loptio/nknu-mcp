#!/usr/bin/env bash
# Phase 2 on the lab PC (lab_pc kit, 11-03; plan 2026-11-08 T5: every export of the list): the content build and the
# offline checks, once the to-text lanes are done.
# Plan docs/superpowers/plans/2026-11-02-pipeline.md, T11 steps 4-8, as amended by 2026-11-02-pipeline-addendum.md:
# I6 (the server dates are research/pipeline_2026-11-02/server_dates.json, re-read on 11-03 from the live archive;
# the lab PC has no archive/, so they are never regenerated here: 教务处's alone, other sites' attachments without a
# page date stay undated) and L (no series: no --corrections, no --candidates; check_realdata runs 22 checks). Before
# step 4 it runs the last command of step 3, the to-text accounting: the plan runs it once the lanes have finished,
# and lanes.sh does not run it.
#
# Many exports (plan 2026-11-08 T5). The exports are the list build_helpers.py reads: $EXPORTS, else
# research/pipeline_2026-11-02/exports.txt, else 教务处's 11-02 export alone (what this script built before). Each
# export's name is its site key with / -> _. Every export shares the to-text cache research/pipeline_2026-11-02/totext/
# cache (keyed by content). Step 3 accounts for every export in one report; step 4 assembles each export into
# corpus/<name>.json (the 10-29 fallback is 教务处's alone: assemble decides) and keeps assemble's report as
# corpus/<name>.assemble_report.json; step 5 builds one database from every pair (nknu.content.build --input
# CORPUS::EXPORT, once per export; 教务处's pair first, since the build's meta corpus and export_dir are its first
# input's, which check_realdata reads) and writes the per-site report site_report.md (site_report.py: it flags, it
# never stops the build). Steps 6-9 measure 教务处's questions, so they run only when 教务处's export (site c.nknu.edu.tw/aca)
# is in the list, its corpus giving the gold; without it they are skipped (and --from 6 or later is refused).
# 11-22: the night's measure is step 5b, a fixed sample of the question bank research/eval_2026-11-18 (student_eval.py,
# at most a few minutes of searching); steps 6 and 7, the 33 old questions of research/eval_2026-11-01, run only with
# --q33 (the owner: the 33 are no longer the yardstick).
#
# --db PATH (plan 2026-11-08 T5): the database to build, research/pipeline_2026-11-02/aca.db by default, and every
# output where it always was. Another path makes a test build: its corpora, accounting, reports and checks go to the
# folder named after it (research/pipeline_2026-11-02/aca-pilot.db -> research/pipeline_2026-11-02/aca-pilot/), so it
# overwrites nothing of the default build; the vector cache stays the shared research/pipeline_2026-11-02/
# vec_cache.sqlite (keyed by text, so a test build reuses the default build's vectors and adds its own). 【Claude】
# Accepted only directly in research/pipeline_2026-11-02/, beside aca.db, under a plain name (letters, digits, . _ -,
# not starting with .; ending in .db) whose folder is none of the default build's (corpus, totext, totext_jobs,
# lab_pc, previous) 【Claude】. Anywhere else that folder could be a place the kit never writes (review of plan
# 2026-11-08 T5: --db research/eval_2026-11-01.db made step 6 overwrite the skeleton's gold, research/eval_2026-11-01/
# gold_docs.json, which step 6's gold-diff and step 7's base runs read; research/skeleton_2026-11-01.db and data.db were
# stopped only by step 3's own guard).
#
# Runs only inside the nknu-pipeline container (compose service build: cwd /work, /opt/venv first on PATH, the code on
# PYTHONPATH=/work/src, the models under /models). One command at a time, so at most one process of this script holds
# the models (check 17 of step 8 starts the MCP server, which loads them a second time). Never the real-agent test
# (research/eval_2026-11-01/run_agent.py costs tokens and needs the owner).
#
#   build.sh [--first] [--from N] [--q33] [--constants] [--skip-marker-check] [--db PATH] [--dry-run]
#   The same words can come from BUILD_OPTS:  docker compose -f ... run --rm -e BUILD_OPTS="--first" build
#   Another export list:  docker compose -f ... run --rm -e EXPORTS=research/pipeline_2026-11-02/exports-pilot.txt build
#
# Steps (the plan's numbers), what they write (<out>: research/pipeline_2026-11-02 for the default database, else the
# test build's folder), the models they load (GPU), and the time on the lab PC (guesses; build.log records each
# command's real time):
#   3  to-text accounting  build_helpers.py account: <out>/totext/accounting.jsonl, summary.json, summary.md (every
#                          export, keys <name>:<序号>)                                                   no model  1-3 min
#   4  assemble            nknu.content.assemble, once per export: <out>/corpus/<name>.json and
#                          <name>.assemble_report.json                                    no model  10 s-3 min each
#   5  build, with vectors nknu.content.build --input ... --embed: the database, <out>/build_report.json,
#                          research/pipeline_2026-11-02/vec_cache.sqlite; build_helpers.py vectors; site_report.py:
#                          <out>/site_report.md (keyword search only)                              bge-m3   15-45 min
#   5b the question sample eval/student_eval.py, a fixed sample of research/eval_2026-11-18 (in no commit) on the
#                          library in use: <out>/student_eval.json, <out>/student_eval.run.json; never a stop, at most
#                          STUDENT_EVAL_LIMIT seconds (600)                                           both     ~5 min
#   6  gold for the new db only with --q33. offline_eval.py gold (教务处's corpus): <out>/gold_docs.json; gold-diff:
#                          gold_diff.txt (the plan's diff, by address since plan 2026-11-08 T1 changed every id)  1 min
#   7  offline checks      only with --q33. offline_eval.py run x7 and compare x2: <out>/offline_*.json, offline_report_r3.md,
#                          offline_report_r4.md. base_r3, base_r4 and kw_r3 load no model; new_r3 and new_r4 both;
#                          dense_r3 bge-m3; rerank_r3 bge-reranker-v2-m3                                    15-40 min
#   8  real-data checks    check_realdata.py --search hybrid (through build_helpers.py): <out>/checks.md
#                                                                                                 both     5-15 min
#   9  constants           only with --constants: step 7's optional sweep, <out>/offline_const_*_r3.json
#                                                                                                 both    10-30 min
#
# Options:
#   --first          the first database while the journals and past exam papers (the deferred files) are still waiting
#                    or converting: they count as not converted (assemble --allow-pending), and lane A may keep
#                    converting them. Default: every file has a result (or failed for good) and no lane runs.
#   --from N         start at step N (3-9); what the earlier steps wrote must be there. --from 9 implies --constants.
#                    Step 5b runs with any --from up to 8.
#   --q33            also run steps 6 and 7: the 33 old questions (off by default since 11-22).
#   --constants      also run step 9 after step 8 (it reads step 6's gold_docs.json).
#   --skip-marker-check  skip the guard on 'started' markers younger than an hour (a file a lane may be converting
#                    now): only once docker ps shows no nknu-lane-A, nknu-lane-B or nknu-lane-C, or the recent markers
#                    are known crashes. A lane that is running (lanes.sh holding lab_pc/lane{A,C,B}.lock) still stops
#                    the build.
#   --db PATH        the database to build (above).
#   --dry-run        print the commands and change nothing (also works outside the container: the export list is
#                    read with the first python or python3 on PATH).
#
# Exit 0 when every step ran; otherwise the failing command's exit code (10: files still to convert, 11: a lane may
# still be converting; both from build_helpers.py ready; 2: a missing input, a wrong option or an unusable export
# list). Earlier outputs of this script are copied to lab_pc/previous/<time>/ (a test build's to
# lab_pc/previous/<time>-<its name>/) before a run overwrites them (not the database or vec_cache.sqlite: the build
# replaces the database only when it succeeds, and the vector cache is only added to). Log: lab_pc/build.log
# (appended, also on stdout for docker logs); one status line: lab_pc/build.status.

set -Eeuo pipefail

# 11-19: the programs are in the repository (deploy/lab-pipeline; on the lab PC /work/deploy/lab-pipeline). The
# project root is the nearest folder above this script that holds src/nknu; what a build writes (its lock, log,
# status, the reports kept from before) stays in the pipeline's own folder $K, which no commit holds.
KIT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$KIT
while [[ $REPO != / && ! -d $REPO/src/nknu ]]; do REPO=$(dirname -- "$REPO"); done
KP=${KIT#"$REPO"/}  # the programs' folder from the project root
P=research/pipeline_2026-11-02
K=$P/lab_pc
SK=research/skeleton_2026-11-01
EV=research/eval_2026-11-01
E=$KP/eval/offline_eval.py  # the program (in the repository); its questions and gold stay in $EV
R3=$EV/runs/r3/summary.json  # offline_eval's default --runs; named here only to check it is there
R4=$EV/runs/r4/summary.json
FALLBACK=research/content_recon_2026-10-29/scratchpad_copy/content/aca_work/corpus.json  # assemble's default
SITE_LIST=research/site_probe_2026-10-23/data.json  # nknu.content.build's default --site-list
SITE_UNITS=config/site_units.yaml  # site -> unit (nknu.content.sites); the build stops without it (plan 2026-11-08 T3)
ROUND1=config/archive_round1.json  # the round-1 sites, which pagetext.page_kind reads when assemble names pages
HELP=$KP/build_helpers.py
SITE_REPORT=$KP/site_report.py
SEEN=archive-export/sites/seen.tsv  # the crawler's nightly export writes it last (agreed 11-16)
ACA_SITE=c.nknu.edu.tw/aca  # whose questions steps 6-9 ask (build_helpers.ACA_SITE)
DB_DEFAULT=$P/aca.db
LOG=$K/build.log
STATUS=$K/build.status
TODAY=2026-11-01  # check_realdata's --today in the plan (offline_eval fixes the same day itself)
MIN_FREE_GB=5

FIRST=0 FROM=3 Q33=0 CONSTANTS=0 SKIP_MARKERS=0 DRY=0 FAILED=0 STEP=准备 DB=$DB_DEFAULT HAS_ACA=0 ACA_NAME=""

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

read -r -a OPTS <<< "${BUILD_OPTS:-}"
set -- ${OPTS[@]+"${OPTS[@]}"} "$@"
while (($#)); do
  case $1 in
    --first) FIRST=1 ;;
    --from) [[ $# -ge 2 ]] || { echo "--from 后面要接步骤号（3 到 9）" >&2; exit 2; }; FROM=$2; shift ;;
    --from=*) FROM=${1#--from=} ;;
    --q33) Q33=1 ;;
    --constants) CONSTANTS=1 ;;
    --skip-marker-check) SKIP_MARKERS=1 ;;
    --db) [[ $# -ge 2 ]] || { echo "--db 后面要接资料库的路径" >&2; exit 2; }; DB=$2; shift ;;
    --db=*) DB=${1#--db=} ;;
    --dry-run) DRY=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "不认得的选项：$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[[ $FROM =~ ^[3-9]$ ]] || { echo "--from 要是 3 到 9 的步骤号，不是「${FROM}」" >&2; exit 2; }
((FROM < 9)) || CONSTANTS=1

# ---- the database and where this run writes (plan 2026-11-08 T5) --------------------------------------------------

DB=${DB#"$REPO"/}  # /work/research/... is the same file as research/...
DB=${DB#./}
db_name=${DB#"$P"/}  # the header: a test database sits directly in $P, beside aca.db, and nowhere else
if [[ $DB != "$P/$db_name" || ! $db_name =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*[.]db$ ]]; then
  echo "--db 要是直接放在 ${P}/ 里（和 aca.db 放在一起）、.db 结尾、名字只用英文字母、数字和 . _ -（不用 . 开头）的档：${DB}" >&2
  exit 2
fi
if [[ $DB == "$DB_DEFAULT" ]]; then
  O=$P
else
  O=${DB%.db}  # a test build: everything of it in its own folder
  case $O in
    "$P"/corpus | "$P"/totext | "$P"/totext_jobs | "$K" | "$P"/previous)
      echo "--db ${DB}：它的资料夹 ${O} 是预设建库在用的，换个名字" >&2; exit 2 ;;
  esac
fi
ACC=$O/totext   # the accounting; for the default build the to-text folder itself, beside the cache, as before
C=$O/corpus     # corpus/<name>.json, one per export

now() { TZ=CST-8 date '+%F %T'; }  # Taipei time (UTC+8, no summer time) whether or not the image has zoneinfo
log() {
  if ((DRY)); then printf '%s\n' "$*"; else printf '[build %s] %s\n' "$(now)" "$*" | tee -a "$LOG"; fi
}
status() { ((DRY)) || printf '%s %s\n' "$(now)" "$*" > "$STATUS"; }
die() { FAILED=1; echo "build.sh：$*" >&2; exit 2; }  # before the log exists: stderr only

fail() {  # fail RC WHY: stop the build at the current step
  local rc=$1
  shift
  FAILED=1
  log "失败：步骤 ${STEP}，退出码 ${rc}。$*"
  if [[ $STEP =~ ^[3-9]\  ]]; then
    log "这一步之前写好的结果都留着；问题解决后可以从这一步接着跑：--from ${STEP%% *}"
  fi
  status "failed step ${STEP%% *} (exit $rc)"
  exit "$rc"
}

finish() {  # an exit that fail() did not announce (a bash error, a signal)
  local rc=$?
  if ((rc && !FAILED && !DRY)); then
    log "意外结束：步骤 ${STEP}，退出码 $rc"
    status "failed step ${STEP%% *} (exit $rc, unexpected)"
  fi
}
trap finish EXIT

run() {  # run CMD...: one command, logged with its output, exit code and time; returns the command's exit code
  if ((DRY)); then
    printf '    %s\n' "$(printf '%q ' "$@")"
    return 0
  fi
  log "→ $(printf '%q ' "$@")"
  local t0=$SECONDS rc
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  set -e
  log "← 退出码 ${rc}，用时 $(((SECONDS - t0) / 60)) 分 $(((SECONDS - t0) % 60)) 秒"
  return "$rc"
}

# step N runs in this invocation: from FROM on, steps 6 and 7 only with --q33, step 9 only with --constants, steps 6-9
# only with 教务处's export
will() {
  (($1 >= FROM)) && { (($1 != 6 && $1 != 7)) || ((Q33)); } && { (($1 < 9)) || ((CONSTANTS)); } \
    && { (($1 < 6)) || ((HAS_ACA)); }
}

step() {  # step N TITLE: start step N; 0 when it is to run, 1 when this invocation skips it
  will "$1" || return 1
  STEP="$1 $2"
  log ""
  log "==== 步骤 $STEP ===="
  status "running step $1 since $(now)"
  return 0
}

# ---- where we are -------------------------------------------------------------------------------------------------

cd -- "$REPO"
[[ -d src/nknu && -f $HELP ]] || die "找不到项目（${REPO}）：build.sh 要放在项目里的 deploy/lab-pipeline/"
mkdir -p "$K"
if ((!DRY)); then
  # the container: Docker's marker file, or this image's layout (the repo at /work, the venv at /opt/venv); never the Mac
  if [[ ! -f /.dockerenv ]] && ! [[ $REPO == /work && -x /opt/venv/bin/python ]]; then
    die "只在实验室电脑的容器里跑（docker compose ... run --rm build）；在别处只能加 --dry-run 看命令"
  fi
  exec 9> "$K/build.lock"
  if command -v flock > /dev/null; then
    flock -n 9 || die "另一个 build 正在跑（$K/build.lock 有人拿着）"
  fi
fi
export PYTHONUNBUFFERED=1         # progress lines reach the log as they are printed
export PYTHONDONTWRITEBYTECODE=1  # no __pycache__ beside the scripts under research/

MODE=$( ((FIRST)) && echo "第一版：学报考题没转完的算没转" || echo "完整")
log ""
log "######## 第二阶段：建库和离线检查（${MODE}），从步骤 $FROM 开始$( ((CONSTANTS)) && echo "，含步骤 9" )$( ((DRY)) && echo "；只印命令" ) ########"
log "资料库：${DB}$( [[ $O == "$P" ]] || echo "（试建：这次的语料、记账、报告都写在 ${O}/）" )"
((DRY)) || log "python：$(command -v python)（$(python --version 2>&1)）；NKNU_DEVICE=${NKNU_DEVICE:-（没设）}；HF_HUB_CACHE=${HF_HUB_CACHE:-（没设）}"

# ---- the export list (plan 2026-11-08 T5) -------------------------------------------------------------------------

STEP="准备 导出清单"
HPY=$(command -v python || command -v python3 || echo python)  # the list needs no package: a dry run on the Mac too
errs=$(mktemp)
if ! listing=$("$HPY" "$HELP" exports 2> "$errs"); then
  why=$(cat "$errs")
  rm -f "$errs"
  fail 2 "导出清单不能用：${why}"
fi
log "$(cat "$errs")"
rm -f "$errs"
EXP_DIRS=() EXP_NAMES=()
while IFS=$'\t' read -r folder name site; do
  [[ -n $folder ]] || continue
  EXP_DIRS+=("$folder")
  EXP_NAMES+=("$name")
  if [[ $site == "$ACA_SITE" ]]; then HAS_ACA=1 ACA_NAME=$name; fi
  log "  ${name}：${folder}"
done <<< "$listing"
((${#EXP_DIRS[@]})) || fail 2 "导出清单是空的"
if ((!HAS_ACA)); then
  ((FROM < 6)) || fail 2 "清单里没有教务处的导出（${ACA_SITE}）：步骤 6–9 只量教务处的题，--from ${FROM} 就没有要跑的了"
  log "清单里没有教务处的导出（${ACA_SITE}）：步骤 6–9（标准出处、离线检查、真资料检查）只量教务处的题，这次不跑"
fi

# ---- inputs, models, GPU, disk ------------------------------------------------------------------------------------

STEP="准备 检查输入"
need=("$HELP")
for folder in "${EXP_DIRS[@]}"; do need+=("$folder/manifest.json"); done
if will 3 || will 4; then need+=("$P/totext/cache"); fi
if ((FROM <= 4)); then need+=("$P/totext_defer_urls.txt"); fi  # ready: which files are the journals and exams
if will 4; then
  need+=("$ROUND1")
  if ((HAS_ACA)); then need+=("$FALLBACK"); fi  # read for 教务处's export alone
fi
if will 5; then
  need+=("$P/server_dates.json" config/aliases.yaml config/units.yaml "$SITE_UNITS" "$ROUND1" "$SITE_LIST"
         "$SITE_REPORT")
fi
if will 5 || will 8; then  # step 8 reads them through the database's meta
  for name in "${EXP_NAMES[@]}"; do need+=("$C/$name.json"); done
fi
if will 6; then need+=("$C/$ACA_NAME.json"); fi
if will 6 || will 7 || will 8 || will 9; then need+=("$DB"); fi
if will 6 || will 7 || will 9; then need+=("$E" "$EV/core20.json" "$EV/noanswer22.json"); fi
if will 6 || will 7; then need+=("$EV/gold_docs.json" "$SK/aca.db"); fi  # the skeleton's: step 6's diff, step 7's base
if will 7 || will 9; then need+=("$O/gold_docs.json" "$R3"); fi
if will 7; then need+=("$R4" "$P/totext"); fi
if will 8; then need+=("$KP/eval/check_realdata.py"); fi
made_here() {  # made by an earlier step of this invocation, so not needed beforehand
  case $1 in
    "$C"/*.json) will 4 ;;
    "$DB") will 5 ;;
    "$O/gold_docs.json") will 6 ;;
    *) return 1 ;;
  esac
}
missing=()
for f in "${need[@]}"; do
  if made_here "$f" || [[ -e $f ]]; then continue; fi
  missing+=("$f")
done
if ((${#missing[@]})); then
  if ((DRY)); then
    log "（真的跑时会因为缺这些输入停下：${missing[*]}）"
  else
    fail 2 "缺输入（传输包里应该有；从后面的步骤开始时，前面步骤的结果要在）：${missing[*]}"
  fi
fi

if ((!DRY)); then
  free_gb=$(df -Pk . | awk 'NR == 2 { printf "%d", $4 / 1048576 }')
  ((free_gb >= MIN_FREE_GB)) || fail 2 "/work 只剩 ${free_gb} GB（要至少 ${MIN_FREE_GB} GB）"
  log "/work 剩 ${free_gb} GB（Docker 虚拟硬盘还能长的空间，不是 C 盘真的剩多少）"
  if command -v nvidia-smi > /dev/null; then
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>&1 | sed 's/^/GPU：/' | tee -a "$LOG" || true
  fi
fi

STEP="准备 模型和装置"
log "两个模型在不在、用哪个装置（不连网）："
run python -m nknu.core.models check || fail $? "模型不在 /models/huggingface/hub（跑 models-download 服务），或缺套件"
if ((!DRY)); then
  device=$(python -c 'from nknu.core.models import pick_device; print(pick_device())' 2>> "$LOG") \
    || fail $? "看不出用哪个装置（见 ${LOG}）"
  if [[ $device != cuda* && -z ${NKNU_DEVICE:-} ]]; then
    fail 2 "看不到 GPU（装置是 ${device}）：先跑 check 服务看 CUDA。真的要用 CPU（慢很多）就加 -e NKNU_DEVICE=cpu"
  fi
  log "装置：$device"
fi

if ((FROM <= 4)); then
  STEP="准备 转文字跑完了没有"
  ready=(python "$HELP" ready)
  ((FIRST)) && ready+=(--first)
  ((SKIP_MARKERS)) && ready+=(--skip-marker-check)
  run "${ready[@]}" || fail $? "转文字还没到建库的时候（见上面）"
fi

# ---- keep the previous run's reports ------------------------------------------------------------------------------

STEP="准备 保留上一次的报告"
previous=()
for f in "$ACC"/accounting.jsonl "$ACC"/summary.json "$ACC"/summary.md "$C"/*.json "$O"/build_report.json \
  "$O"/gold_docs.json "$O"/gold_diff.txt "$O"/offline_*.json "$O"/offline_report_r3.md "$O"/offline_report_r4.md \
  "$O"/checks.md "$O"/site_report.md "$O"/site_checks.json "$O"/student_eval.json "$O"/student_eval.run.json; do
  if [[ -f $f ]]; then previous+=("$f"); fi  # an unmatched glob stays a literal name: dropped here too
done
if ((${#previous[@]})); then
  keep=$K/previous/$(TZ=CST-8 date '+%Y%m%d-%H%M%S')
  [[ $O == "$P" ]] || keep+=-$(basename -- "$O")
  log "上一次的报告先复制到 $keep/（${#previous[@]} 个档，照原来的资料夹；资料库和 vec_cache.sqlite 不复制）"
  if ((!DRY)); then
    for f in "${previous[@]}"; do
      rel=${f#"$O"/}
      mkdir -p "$keep/$(dirname -- "$rel")"
      cp -p "$f" "$keep/$rel"
    done
  fi
fi

# ---- 3. to-text accounting ----------------------------------------------------------------------------------------

if step 3 "转文字记账"; then
  rc=0
  run python "$HELP" account --out "$ACC" || rc=$?
  if ((rc == 1 && FIRST)); then
    log "还有没转的档（学报考题）：第一版库照样建，它们算没转。这份记账是现在的快照（线 A 正在转的那个档记成失败），"
    log "学报考题转完、建完整的库时会重写"
  elif ((rc)); then
    fail "$rc" "转文字记账说还有档没转（或出错）；完整的库要等全部转完，要先建第一版就加 --first"
  fi
fi

# ---- 4. assemble, once per export ---------------------------------------------------------------------------------

if step 4 "组装 corpus/<网站>.json"; then
  ((DRY)) || mkdir -p "$C"
  for k in "${!EXP_DIRS[@]}"; do
    name=${EXP_NAMES[k]}
    cmd=(python -m nknu.content.assemble --export "${EXP_DIRS[k]}" --totext "$P/totext" --out "$C/$name.json")
    ((FIRST)) && cmd+=(--allow-pending)
    run "${cmd[@]}" || fail $? "组装 ${name} 失败（见上面）"
    # assemble writes its report beside the corpus under one name: kept per export 【Claude】
    run mv -f "$C/assemble_report.json" "$C/$name.assemble_report.json" || fail $? "留不下 ${name} 的组装报告"
  done
fi

# ---- 5. one database from every export, with vectors; the per-site report -----------------------------------------

if step 5 "建库，含 bge-m3 向量"; then
  # 教务处's pair first, the others in the list's order: the build's meta corpus and export_dir are its first input's,
  # and step 8 (check_realdata) reads 教务处's records and calendar through them (plan 2026-11-08 T4)
  inputs=()
  for k in "${!EXP_DIRS[@]}"; do
    pair=(--input "$C/${EXP_NAMES[k]}.json::${EXP_DIRS[k]}")
    if [[ ${EXP_NAMES[k]} == "$ACA_NAME" ]]; then
      inputs=("${pair[@]}" ${inputs[@]+"${inputs[@]}"})
    else
      inputs+=("${pair[@]}")
    fi
  done
  # spec 2026-11-16 (nightly ingest) §五: each site's last-seen day from the crawler's seen.tsv, once it writes one
  seen=()
  if [[ -f $SEEN ]]; then seen=(--site-checked "$SEEN"); fi
  run python -m nknu.content.build "${inputs[@]}" --out "$DB" --report "$O/build_report.json" \
    --server-dates "$P/server_dates.json" --embed --vec-cache "$P/vec_cache.sqlite" ${seen[@]+"${seen[@]}"} \
    || fail $? "建库失败；旧的 ${DB} 没动"
  run python "$HELP" vectors "$O/build_report.json" || fail $? "向量不完整"
  # flags only, never a stop (plan 2026-11-08 T5)
  # spec 2026-11-16 §五: the checks as data too (site_checks.json), and what is new against the last build's, which
  # is still in place here (the step above only copied it)
  since=()
  if [[ -f $O/site_checks.json ]]; then since=(--prev "$O/site_checks.json"); fi
  run python "$SITE_REPORT" --db "$DB" --out "$O/site_report.md" --checks "$O/site_checks.json" \
    ${since[@]+"${since[@]}"} || log "每个网站的检查没写成（退出码 $?）：它只是提醒，建库照样算成功"
fi

# ---- 5b. the question sample on the library students get ----------------------------------------------------------
# student_eval.py: a fixed sample of research/eval_2026-11-18 (in no commit; its 平时 half only), raw and agent ranks,
# into $O/student_eval.json, which the panels read (11-22: the owner wants the night's searching kept within 5 minutes,
# so a sample; the 33 old questions only with --q33). Past step 5 the swap is decided and $DB is the library in use (a
# build the swap guard held stopped at step 5: the file keeps the last measurement, which says which library it
# measured). One process, at most SE_LIMIT seconds; what it did is kept in $O/student_eval.run.json for the night's
# status (nightly_ingest.py). Never a stop.
SE=research/eval_2026-11-18
SE_PROG=$KP/eval/student_eval.py
SE_LIMIT=${STUDENT_EVAL_LIMIT:-600}
if ((FROM <= 8)) && [[ -f $SE_PROG && -f $SE/questions.json && -f $SE/agent_queries.json ]]; then
  STEP="5b 题库抽样量测"
  log ""
  log "==== 步骤 5b 题库抽样量测 ===="
  status "running step 5b since $(now)"
  se_rc=0 se_t0=$SECONDS
  run timeout "$SE_LIMIT" python "$SE_PROG" run --db "$DB" --questions "$SE" --out "$O/student_eval.json" \
    || se_rc=$?
  if ((!DRY)); then
    printf '{"exit": %d, "seconds": %d, "at": "%s"}\n' "$se_rc" "$((SECONDS - se_t0))" \
      "$(TZ=CST-8 date '+%FT%T+08:00')" > "$O/student_eval.run.json"
  fi
  if ((se_rc == 124)); then
    log "题库抽样量测超过 ${SE_LIMIT} 秒没量完，停掉了：只是量测，不影响换库"
  elif ((se_rc)); then
    log "题库抽样量测没量成（退出码 ${se_rc}）：只是量测，不影响换库"
  fi
elif ((FROM <= 8)); then
  log "题库抽样量测这次不跑：缺 ${SE_PROG} 或 ${SE}/questions.json、agent_queries.json（题库不进 git，要手放到实验室）"
fi

# ---- 6. gold for the new database ---------------------------------------------------------------------------------

if step 6 "新库的标准出处"; then
  run python "$E" gold --db "$DB" --corpus "$C/$ACA_NAME.json" --out "$O/gold_docs.json" || fail $? "做 gold 失败"
  # by address, each gold with its own database: the ids changed with plan 2026-11-08 T1 (was build_helpers gold-diff)
  run python "$E" gold-diff --base-gold "$EV/gold_docs.json" --base-db "$SK/aca.db" --gold "$O/gold_docs.json" \
    --db "$DB" --out "$O/gold_diff.txt" || fail $? "比 gold 失败"
fi

# ---- 7. offline checks, baseline and new --------------------------------------------------------------------------

offline() {  # offline OUT ARGS...: one offline_eval run into $O/OUT, then its own check
  local out=$1
  shift
  run python "$E" run "$@" --out "$O/$out" || fail $? "离线检查 $out 失败"
  run python "$HELP" runs "$O/$out" || fail $? "离线检查 $out 的结果不能用"
}

if step 7 "离线检查"; then
  NEW=(--db "$DB" --gold "$O/gold_docs.json")
  offline offline_base_r3.json --db "$SK/aca.db"
  offline offline_base_r4.json --db "$SK/aca.db" --runs "$R4"
  offline offline_new_r3.json "${NEW[@]}" --search hybrid --totext "$P/totext"
  offline offline_new_r4.json "${NEW[@]}" --search hybrid --totext "$P/totext" --runs "$R4"
  offline offline_dense_r3.json "${NEW[@]}" --search hybrid --hybrid '{"rerank": false}'
  offline offline_rerank_r3.json "${NEW[@]}" --search hybrid --hybrid '{"dense": false}'
  offline offline_kw_r3.json "${NEW[@]}"
  run python "$E" compare --base "$O/offline_base_r3.json" --new "$O/offline_new_r3.json" \
    --out "$O/offline_report_r3.md" || fail $? "并排比较 r3 失败"
  run python "$E" compare --base "$O/offline_base_r4.json" --new "$O/offline_new_r4.json" \
    --out "$O/offline_report_r4.md" || fail $? "并排比较 r4 失败"
fi

# ---- 8. real-data checks ------------------------------------------------------------------------------------------

if step 8 "真资料检查"; then
  run python "$HELP" check-realdata --db "$DB" --out "$O/checks.md" --search hybrid --today "$TODAY" \
    || fail $? "真资料检查跑不完（不是某一项不过：那些写在 checks.md 里）"
fi

# ---- 9. constants (optional) --------------------------------------------------------------------------------------

if step 9 "排序常数（可选）"; then
  NEW=(--db "$DB" --gold "$O/gold_docs.json" --search hybrid --no-scope-effect)
  offline offline_const_w_rerank2_r3.json "${NEW[@]}" --hybrid '{"w_rerank": 2.0}'
  offline offline_const_w_rerank4_r3.json "${NEW[@]}" --hybrid '{"w_rerank": 4.0}'
  offline offline_const_rerank_docs50_r3.json "${NEW[@]}" --hybrid '{"rerank_docs": 50}'
  offline offline_const_rerank_norm_raw_r3.json "${NEW[@]}" --hybrid '{"rerank_norm": "raw"}'
fi

# ---- done ---------------------------------------------------------------------------------------------------------

STEP="完成"
log ""
checks=""  # (an assignment whose $( ) fails would end the script under set -e)
((HAS_ACA)) && checks="、checks.md"
((HAS_ACA && Q33)) && checks+="、gold_docs.json、gold_diff.txt、offline_*.json、offline_report_r3.md、offline_report_r4.md"
[[ -f $O/student_eval.json ]] && checks+="、student_eval.json（题库抽样量测）"
log "全部跑完。资料库 ${DB}；结果在 ${O}/：corpus/（每份导出一个 corpus 和组装报告）、build_report.json、"
log "site_report.md（每个网站一行）${checks}；转文字记账在 ${ACC}/summary.md。"
log "把这些交给 Claude 看；真 agent 测试（花 token）不在这里跑。"
if ((!DRY)); then
  shown=("$DB" "$O"/build_report.json "$O"/site_report.md)
  ((HAS_ACA)) && shown+=("$O"/gold_docs.json "$O"/offline_report_r3.md "$O"/offline_report_r4.md "$O"/checks.md)
  ls -l "${shown[@]}" "$C"/*.json 2>&1 | tee -a "$LOG" || true
fi
status "ok $MODE, from step $FROM$( ((CONSTANTS)) && echo ", with step 9" ), ${#EXP_DIRS[@]} exports, db $DB"
