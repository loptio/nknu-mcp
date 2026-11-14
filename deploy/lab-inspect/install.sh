#!/usr/bin/env bash
# 在实验室电脑的容器里跑（plan 2026-11-15 T8）：把检视工具的程序装到磁碟区的 /work/inspect-code，不动 /work/src。
#   docker run --rm --network none -v nknu-work:/work -v C:\nknu-transfer\inspect:/in:ro nknu-pipeline bash /in/install.sh
# 做法：把 /work/src 现在的样子抄一份，再把包里的档盖上去。动手以前先核对两件事：包没坏；那四个改过的档，
# 这台电脑上的是打包时当作基础的那一版（不是的话，多半是建库的程序更新过了，要在 Mac 上重新打包）。
# 上一版留在 /work/inspect-code.prev，要退回就把两个资料夹的名字换回来。
set -euo pipefail

cd /in
sha256sum -c --quiet inspect-code.tar.sha256
if [ "${FORCE:-0}" != "1" ]; then
  if ! (cd /work && sha256sum -c --quiet /in/inspect-code.base.sha256); then
    echo "停：/work/src 里那几个档和打包时的基础不一样。先确认建库的程序是不是更新过，再在 Mac 上重新打包。" >&2
    exit 1
  fi
fi

NEW=/work/inspect-code.new
rm -rf "$NEW"
mkdir -p "$NEW"
cp -a /work/src "$NEW/src"
tar -xf /in/inspect-code.tar -C "$NEW"
find "$NEW" -name __pycache__ -type d -prune -exec rm -rf {} +
# 检视台每一页读这两个档：什么时候装的、哪一包；那四个档是照建库程序的哪一版改的（建库的程序之后要是变了，页面会提醒重装）
cp /in/inspect-code.base.sha256 "$NEW/BASE.sha256"
printf '%s %s\n' "$(TZ=Asia/Taipei date +%Y-%m-%dT%H:%M:%S%z)" "$(cut -c1-16 /in/inspect-code.tar.sha256)" > "$NEW/INSTALLED"

rm -rf /work/inspect-code.prev
if [ -d /work/inspect-code ]; then
  mv /work/inspect-code /work/inspect-code.prev
fi
mv "$NEW" /work/inspect-code
echo "装好了：/work/inspect-code（上一版在 /work/inspect-code.prev）。"
echo "接着开服务，或已经开着就重启：docker compose -f C:\\nknu-transfer\\inspect\\compose.yaml up -d / restart"
