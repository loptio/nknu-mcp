#!/usr/bin/env bash
# 在实验室电脑的容器里跑（plan 2026-11-15 T8）：把检视工具的程序装到磁碟区的 /work/inspect-code，不动 /work/src。
#   docker run --rm --network none -v nknu-work:/work -v C:\nknu-transfer\inspect:/in:ro nknu-pipeline bash /in/install.sh
# 做法：把 /work/src 现在的样子抄一份，再把包里的档盖上去。动手以前先核对两件事：
#   1. 包没坏；
#   2. 包会盖掉的那几个档，这台电脑上的是「打包时当作基础的那一版」，或者已经是「包里的这一版」
#      （建库的程序同步过、已经带着检视工具的改动）。两个都不是就停：多半是建库的程序又改过，要在 Mac 上重新打包。
# 上一版留在 /work/inspect-code.prev，要退回就把两个资料夹的名字换回来。
# WORK、IN 两个变数只给测试用（默认 /work、/in）。
set -euo pipefail

WORK="${WORK:-/work}"
IN="${IN:-/in}"

cd "$IN"
sha256sum -c --quiet inspect-code.tar.sha256
if [ "${FORCE:-0}" != "1" ]; then
  while read -r base f; do
    [ -n "$f" ] || continue
    have=$(sha256sum < "$WORK/$f" | cut -d' ' -f1)
    packed=$(tar -xOf "$IN/inspect-code.tar" "$f" | sha256sum | cut -d' ' -f1)
    if [ "$have" != "$base" ] && [ "$have" != "$packed" ]; then
      echo "停：$WORK/$f 不是打包时当作基础的那一版，也不是包里的这一版。" >&2
      echo "先确认建库的程序是不是又改过，再在 Mac 上重新打包（deploy/lab-inspect/pack.sh）。什么都还没装。" >&2
      exit 1
    fi
  done < "$IN/inspect-code.base.sha256"
fi

NEW="$WORK/inspect-code.new"
rm -rf "$NEW"
mkdir -p "$NEW"
cp -a "$WORK/src" "$NEW/src"
tar -xf "$IN/inspect-code.tar" -C "$NEW"
find "$NEW" -name __pycache__ -type d -prune -exec rm -rf {} +
# 检视台每一页读这两个档：什么时候装的、哪一包；包盖掉的那几个档是照建库程序的哪一版改的
# （建库的程序之后要是变了，页面会提醒重装）
cp "$IN/inspect-code.base.sha256" "$NEW/BASE.sha256"
printf '%s %s\n' "$(TZ=Asia/Taipei date +%Y-%m-%dT%H:%M:%S%z)" "$(cut -c1-16 "$IN/inspect-code.tar.sha256")" > "$NEW/INSTALLED"

rm -rf "$WORK/inspect-code.prev"
if [ -d "$WORK/inspect-code" ]; then
  mv "$WORK/inspect-code" "$WORK/inspect-code.prev"
fi
mv "$NEW" "$WORK/inspect-code"
echo "装好了：$WORK/inspect-code（上一版在 $WORK/inspect-code.prev）。"
echo "接着开服务，或已经开着就重启：docker compose -f C:\\nknu-transfer\\inspect\\compose.yaml up -d / restart"
