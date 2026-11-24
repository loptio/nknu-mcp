#!/usr/bin/env bash
# 在实验室电脑的容器里跑：把建库这条线的程序（/work/src、/work/config、/work/deploy/lab-pipeline、/work/tests/pipeline）
# 整份换成 git 的某一版（11-24 第一次用，对齐到 ba2c7ed）。打包和送法见 README 第 12 节「整份对齐 git」。
#   docker run --rm -v nknu-work:/work -v C:\nknu-transfer\tools:/tools:ro -v C:\nknu-transfer\out:/out -w /work ^
#     -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline bash /tools/full_sync_install.sh
# 检查和 install.sh 一样（包的 SHA-256；没有转文字、建库、进库在跑）。不同的是 src/nknu 整个换掉（旧的移到
# /work/previous，不删），git 已经没有的旧档（v1）不会留下；config/ 里 v1 的 entities.yaml、templates.yaml 一起移走。
# 退回：把新的 src/nknu 移开，在 /work 解开 /work/previous/full-sync-<时间>.tar。
set -eu
cd /work
(cd /out && sha256sum -c full-sync.tar.sha256)
S=research/pipeline_2026-11-02/lab_pc
python - $S/build.lock $S/ingest.lock $S/laneA.lock $S/laneB.lock $S/laneC.lock <<'PY'
import fcntl, os, sys
for p in sys.argv[1:]:
    if not os.path.exists(p):
        continue
    with open(p, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(f"{p} 有人拿着：有转文字、建库或进库在跑，现在不装")
        fcntl.flock(f, fcntl.LOCK_UN)
PY
if ls $S/*.inflight >/dev/null 2>&1; then echo "有转文字线正在转（$S/*.inflight），现在不装"; exit 3; fi
bad=$(tar -tf /out/full-sync.tar | grep -v '^src/\|^config/\|^deploy/$\|^deploy/lab-pipeline/\|^tests/$\|^tests/pipeline/\|^pax_global_header$' || true)
[ -z "$bad" ] || { echo "包里有不该有的东西："; echo "$bad" | head; exit 4; }

stamp=$(TZ=CST-8 date '+%Y%m%d-%H%M%S')
mkdir -p previous
keep=previous/full-sync-$stamp.tar
tar -cf "$keep" --exclude=__pycache__ --exclude='*.pyc' src config deploy/lab-pipeline tests/pipeline
echo "换之前的整份存在 /work/$keep（$(tar -tf "$keep" | grep -vc '/$') 个档）"

aside=previous/full-sync-$stamp-aside
mkdir -p "$aside/config"
mv src/nknu "$aside/src-nknu"
for f in entities.yaml templates.yaml; do [ -e "config/$f" ] && mv "config/$f" "$aside/config/"; done
echo "旧的 src/nknu 和 v1 的两个设定档移到 /work/$aside"

tar -xf /out/full-sync.tar -C /work --exclude=pax_global_header
sha256sum -c --quiet /out/full-sync.files.sha256 && echo "$(wc -l < /out/full-sync.files.sha256) 个档都和包里的一样"
left=$(find src/nknu -type f ! -path '*__pycache__*' | sed 's|^|./|' | sort | comm -23 - <(sed 's|^[0-9a-f]*  |./|' /out/full-sync.files.sha256 | sort))
[ -z "$left" ] && echo "src/nknu 里没有 git 没有的档" || { echo "src/nknu 里还有 git 没有的档："; echo "$left"; }

python -c "import nknu.content.build, nknu.totext.run, nknu.retrieval.search; print('建库、转文字、检索的程序都载得进来')"
python -m pytest tests/pipeline -q -p no:cacheprovider 2>&1 | tail -3
