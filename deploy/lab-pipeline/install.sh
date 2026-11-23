#!/usr/bin/env bash
# 在实验室电脑的容器里跑（/work 可写、/out 是 C:\nknu-transfer\out）：把 pack.sh 打的包解到 /work。
#   docker run --rm -v nknu-work:/work -v C:\nknu-transfer\tools:/tools:ro -v C:\nknu-transfer\out:/out ^
#     -w /work -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline bash /tools/lab_pipeline_install.sh
# 只在没有转文字、建库、进库在跑的时候装（看几把锁）；包要对得上它的 SHA-256；被换掉的档先存进
# /work/previous/lab-pipeline-<时间>.tar（要退回就在 /work 解开它）。装完印每个档的校验码，并跑这条线自己的测试。
# 主机上的两个档（C:\nknu-transfer\build\compose.yaml、nightly_ingest.cmd）不在容器里，另外用 scp 换，见 README。
set -eu
cd /work
(cd /out && sha256sum -c lab-pipeline.tar.sha256)
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
bad=$(tar -tf /out/lab-pipeline.tar | grep -v '^deploy/lab-pipeline/\|^tests/pipeline/\|^src/nknu/\(content\|totext\|retrieval\|core\)/\|^config/\|^deploy/$\|^tests/$\|^src/$\|^src/nknu/$' || true)
[ -z "$bad" ] || { echo "包里有不该有的东西："; echo "$bad" | head; exit 4; }
mkdir -p previous
keep=previous/lab-pipeline-$(TZ=CST-8 date '+%Y%m%d-%H%M%S').tar
tar -tf /out/lab-pipeline.tar | grep -v '/$' | while read -r f; do [ -e "$f" ] && echo "$f"; done > /tmp/there.txt || true
if [ -s /tmp/there.txt ]; then tar -cf "$keep" -T /tmp/there.txt; echo "被换掉的档存在 /work/$keep"; fi
tar -xf /out/lab-pipeline.tar -C /work
tar -tf /out/lab-pipeline.tar | grep -v '/$' | xargs sha256sum > /out/lab-pipeline.lab.sha256
echo "装了 $(wc -l < /out/lab-pipeline.lab.sha256) 个档；校验码在 C:\\nknu-transfer\\out\\lab-pipeline.lab.sha256"
python -m pytest tests/pipeline -q -p no:cacheprovider 2>&1 | tail -3
