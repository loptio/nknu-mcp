#!/usr/bin/env bash
# 在 Mac 上跑：把每晚进库这条线的程序打成一包，送到实验室电脑的 /work（11-19 起这批程序在仓库里）。
#   deploy/lab-pipeline/pack.sh [输出资料夹] [--with-src]
# 包里是：
#   deploy/lab-pipeline/      转文字线、建库脚本、每晚进库、每站报告、进度、量测和真资料检查（eval/）
#   tests/pipeline/           这批程序的测试
#   --with-src 时另加：src/nknu/content、totext、retrieval、core 和 config/（建库读的程序和设定）。
#     爬虫的 src/nknu/archive、面板的 src/nknu/ops 不在里面：各由自己的包送。
# 包里只有程序和设定，没有资料（题库、导出、库、报告都不在仓库里）；照规矩仍然只经 SSH 送。
# 写出：lab-pipeline.tar、lab-pipeline.tar.sha256，旁边放 install.sh、compose.yaml、nightly_ingest.cmd。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
OUT="${TMPDIR:-/tmp}/nknu-lab-pipeline"
WITH_SRC=0
for a in "$@"; do
  case "$a" in
    --with-src) WITH_SRC=1 ;;
    *) OUT="$a" ;;
  esac
done
mkdir -p "$OUT"
PACKED=(lab-pipeline.tar lab-pipeline.tar.sha256 install.sh compose.yaml nightly_ingest.cmd)
(cd "$OUT" && rm -f "${PACKED[@]}")

WHAT=(deploy/lab-pipeline tests/pipeline)
if ((WITH_SRC)); then
  WHAT+=(src/nknu/content src/nknu/totext src/nknu/retrieval src/nknu/core config)
fi
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  -cf "$OUT/lab-pipeline.tar" "${WHAT[@]}"
(cd "$OUT" && shasum -a 256 lab-pipeline.tar > lab-pipeline.tar.sha256)
cp deploy/lab-pipeline/install.sh deploy/lab-pipeline/compose.yaml deploy/lab-pipeline/nightly_ingest.cmd "$OUT/"

echo "打好了：${OUT}，$(tar -tf "$OUT/lab-pipeline.tar" | grep -vc '/$') 个档"
echo "接着（都只经 SSH）："
echo "  scp $OUT/lab-pipeline.tar $OUT/lab-pipeline.tar.sha256 nknu-lab:C:/nknu-transfer/out/"
echo "  scp $OUT/install.sh nknu-lab:C:/nknu-transfer/tools/lab_pipeline_install.sh"
echo "  再照 deploy/lab-pipeline/README.md「换程序」那一节在实验室电脑上装。"
