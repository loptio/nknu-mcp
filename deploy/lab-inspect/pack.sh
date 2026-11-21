#!/usr/bin/env bash
# 在 Mac 上跑（plan 2026-11-15 T8）：把检视工具自己的程序打成一包。只带下面这几样，
# 不带工作区里别的 session 改到一半的档（平常送的 code.tar 是整个工作区）。
#   deploy/lab-inspect/pack.sh [输出资料夹]
# 写出三个档：inspect-code.tar、inspect-code.tar.sha256（核对包没坏）、
# inspect-code.base.sha256（包会盖掉的那四个档，各是照哪一版改出来的；install.sh 拿它核对实验室电脑上的是那一版）。
# 「照哪一版」：这个档现在和 git 里一样，就是它上一次提交以前的那一版；改过还没提交，就是 git 里现在这一版。
# 包里只有程序，没有资料；照规矩仍然只经 SSH 送到实验室电脑。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
OUT="${1:-${TMPDIR:-/tmp}/nknu-inspect}"
mkdir -p "$OUT"
PACKED=(inspect-code.tar inspect-code.tar.sha256 inspect-code.base.sha256 install.sh compose.yaml)
(cd "$OUT" && rm -f "${PACKED[@]}")  # 输出资料夹用过的话，先拿掉上一次的五个档

NEW=(src/nknu/ops deploy/lab-inspect src/nknu/core/model_service.py)
CHANGED=(src/nknu/retrieval/hybrid.py src/nknu/retrieval/search.py src/nknu/retrieval/server.py src/nknu/admin/cli.py
         src/nknu/core/models.py)

COPYFILE_DISABLE=1 tar --no-xattrs --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  -cf "$OUT/inspect-code.tar" "${NEW[@]}" "${CHANGED[@]}"
(cd "$OUT" && shasum -a 256 inspect-code.tar > inspect-code.tar.sha256)
: > "$OUT/inspect-code.base.sha256"
for f in "${CHANGED[@]}"; do
  if git diff --quiet HEAD -- "$f"; then
    from=$(git rev-list -2 HEAD -- "$f" | tail -1)   # 和 git 里一样：上一次提交以前的那一版
  else
    from=HEAD                                        # 改过还没提交：git 里现在这一版
  fi
  printf '%s  %s\n' "$(git show "$from:$f" | shasum -a 256 | cut -d' ' -f1)" "$f" >> "$OUT/inspect-code.base.sha256"
done
cp deploy/lab-inspect/install.sh deploy/lab-inspect/compose.yaml "$OUT/"

echo "打好了：$OUT"
ls -l "$OUT"
echo "接着（都只经 SSH）："
echo "  (cd $OUT && scp ${PACKED[*]} nknu-lab:C:/nknu-transfer/inspect/)"
echo "  再照 deploy/lab-inspect/README.md「换程序」那一节在实验室电脑上装。"
