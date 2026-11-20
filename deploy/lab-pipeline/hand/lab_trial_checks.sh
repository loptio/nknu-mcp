#!/usr/bin/env bash
# 实验室电脑上（映像里，要显卡和模型）：一份试建库换上以前的检查：33 题（问题原文和两种 agent 重放）和真资料检查。
#   lab_trial_checks.sh NAME      （试建库 research/pipeline_2026-11-02/NAME.db，它的语料在同名资料夹）
# 写到 /out/trials/NAME/：NAME_final_r3.json、NAME_final_r4.json、gold_NAME.json、NAME_checks.md 和各自的 .log。
# 一次只跑一个行程（都在抢同一张显卡）。
set -u
db=${1:?NAME}
cd /work
export NKNU_CONFIG_DIR=/work/config PYTHONPATH=/work/src
PY=/opt/venv/bin/python
EV=research/eval_2026-11-01          # the questions, gold and the agents' recorded queries (in no commit)
E=deploy/lab-pipeline/eval/offline_eval.py
P=research/pipeline_2026-11-02
O=/out/trials/$db
mkdir -p "$O"
G="$O/gold_$db.json"
"$PY" "$E" gold --db "$P/$db.db" --corpus "$P/$db/corpus/c.nknu.edu.tw_aca.json" --out "$G" > "$O/gold_$db.log" 2>&1 \
  || { echo "gold failed: $O/gold_$db.log" >&2; exit 8; }
for runs in r3 r4; do
  "$PY" "$E" run --search hybrid --db "$P/$db.db" --gold "$G" --runs "$EV/runs/$runs/summary.json" \
    --no-scope-effect --out "$O/${db}_final_${runs}.json" > "$O/${db}_final_${runs}.log" 2>&1
  echo "exit $?" >> "$O/${db}_final_${runs}.log"
done
"$PY" deploy/lab-pipeline/build_helpers.py check-realdata --db "$P/$db.db" --out "$O/${db}_checks.md" --search hybrid \
  --today "$(TZ=CST-8 date +%F)" > "$O/${db}_checks.log" 2>&1
echo "checks exit $?" >> "$O/${db}_checks.log"
cp -f "$P/$db.swap.json" "$O/" 2>/dev/null || echo "no $db.swap.json"
cp -f "$P/$db/build_report.json" "$O/${db}_build_report.json" 2>/dev/null || echo "no build_report.json"
tail -3 "$O/${db}_checks.log"
