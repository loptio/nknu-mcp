#!/usr/bin/env bash
# 在实验室电脑的容器里跑：把 crawler-code.tar 装进爬虫的两份程序和每晚脚本的位置。账本不动。
#   docker run --rm --network none -e TZ=Asia/Taipei -v nknu-crawl:/crawl -v C:/nknu-crawl/in/deploy:/in:ro
#     nknu-pipeline bash /in/install.sh
# 主机上的三个档（compose.yaml、nightly_update.cmd、nightly_hidden.vbs）不归这支管，照 README「换程序」那一节用
# copy 换。
# 做法：
#   1. 只在 04:00～23:00 做（01:00 起每晚的定期更新在跑）；包要完整；没有抓取拿着 run.lock。少一样就停，什么都不动。
#   2. 两份程序（/crawl/code-update、/crawl/export-code）各自：包里有、内容不同的档先留成
#      <档名>.prev-<时间>，再换上；src/nknu/archive 里包里没有的 .py（仓库里已经删掉的）也改名成 .prev-<时间>。
#      换完用那一份程序载入一次（载不进来就说，退出码 1；旧档都还在）。
#   3. 包里的 lab/ 放到 /crawl/code-update/lab（每晚脚本、导出脚本、网站清单），旧的留 .prev-<时间>。
# 退回：把 .prev-<时间> 的档改回原名（同一个时间戳就是同一次安装）。
# CRAWL、IN、COPIES、ANY_HOUR 四个变数只给测试和空机器重建用（默认 /crawl、/in、两份都装、要看钟点）。
set -euo pipefail

CRAWL="${CRAWL:-/crawl}"
IN="${IN:-/in}"
COPIES="${COPIES:-code-update export-code}"
stamp="$(date +%Y%m%d-%H%M%S)"
hour="$(date +%H)"
if [ "${ANY_HOUR:-0}" != 1 ] && { [ "$hour" -lt 4 ] || [ "$hour" -ge 23 ]; }; then
  echo "停：现在不在 04:00～23:00（每晚的定期更新 01:00 起在跑）。什么都没装。" >&2
  exit 1
fi
(cd "$IN" && sha256sum -c --quiet crawler-code.tar.sha256) || { echo "停：包坏了或不是这一份。什么都没装。" >&2; exit 1; }
held="$(python3 - "$CRAWL/archive/run.lock" <<'PY'
import fcntl
import sys

try:
    fh = open(sys.argv[1])
except FileNotFoundError:
    sys.exit(0)
try:
    fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
except BlockingIOError:
    print(fh.read().strip() or "?")
PY
)"
[ -z "$held" ] || { echo "停：有抓取正拿着 run.lock（pid ${held}）。等它结束再装。什么都没装。" >&2; exit 1; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
tar -xf "$IN/crawler-code.tar" -C "$T"
(cd "$T" && sha256sum -c --quiet MANIFEST.sha256) || { echo "停：包里的档和它自己的清单对不上。什么都没装。" >&2; exit 1; }
[ -f "$T/src/nknu/archive/crawler.py" ] && [ -f "$T/lab/nightly_update.sh" ] \
  || { echo "停：包里没有爬虫的程序或每晚脚本。什么都没装。" >&2; exit 1; }

bad=0
for c in $COPIES; do
  d="$CRAWL/$c"
  mkdir -p "$d"
  changed=0
  added=0
  while IFS= read -r f; do
    if [ -f "$d/$f" ]; then
      if cmp -s "$T/$f" "$d/$f"; then continue; fi
      cp -a "$d/$f" "$d/$f.prev-$stamp"
      changed=$((changed + 1))
    else
      added=$((added + 1))
    fi
    mkdir -p "$(dirname "$d/$f")"
    cp "$T/$f" "$d/$f"
  done < <(cd "$T" && find src config certs pyproject.toml uv.lock -type f 2>/dev/null | LC_ALL=C sort)
  gone=0
  for old in "$d"/src/nknu/archive/*.py; do
    [ -f "$old" ] || continue
    if [ ! -f "$T/src/nknu/archive/$(basename "$old")" ]; then
      mv "$old" "$old.prev-$stamp"
      gone=$((gone + 1))
    fi
  done
  find "$d/src" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  if (cd "$d" && PYTHONPATH="$d/src" PYTHONDONTWRITEBYTECODE=1 python3 -c "
from nknu.archive import crawler, export, monitor, round1, schedules, update, viewer, warcbackup, coverage
from nknu.admin.cli import app
round1.load()" 2>"$T/load.err"); then
    echo "  ${d}：换了 ${changed} 个档、新放 ${added} 个、拿掉 ${gone} 个；程序载得进来"
  else
    echo "  ${d}：换了 ${changed} 个档、新放 ${added} 个、拿掉 ${gone} 个；**程序载不进来**：$(tail -1 "$T/load.err")" >&2
    bad=1
  fi
done

L="$CRAWL/code-update/lab"
mkdir -p "$L"
for f in "$T"/lab/*; do
  b="$(basename "$f")"
  if [ -f "$L/$b" ] && ! cmp -s "$f" "$L/$b"; then cp -a "$L/$b" "$L/$b.prev-$stamp"; fi
  cp "$f" "$L/$b"
done
chmod 755 "$L"/*.sh
bash -n "$L/nightly_update.sh" && bash -n "$L/export_sites.sh" && echo "  ${L}：每晚脚本、导出脚本、网站清单放好了"

if [ "$bad" = 1 ]; then
  echo "有一份程序载不进来：别让今晚自己跑，把 .prev-$stamp 的档改回去或重新打包。" >&2
  exit 1
fi
echo "装好了（旧档留成 .prev-${stamp}）。接着照 README「换程序」第 4 步核对和空跑。"
