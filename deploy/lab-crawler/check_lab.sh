#!/usr/bin/env bash
# 在实验室电脑的容器里跑，只读：实验室上在用的档，和这一包（也就是仓库里那一版）是不是同一份。
#   docker run --rm --network none -v nknu-crawl:/crawl:ro -v nknu-work:/work:ro -v C:/nknu-crawl:/host:ro
#     -v C:/nknu-crawl/in/deploy:/in:ro nknu-pipeline bash /in/check_lab.sh
# 比三样：两份程序里包带着的每个档；/crawl/code-update/lab 里的脚本；主机上的 compose.yaml、nightly_update.cmd、
# nightly_hidden.vbs（/host 是 C:\nknu-crawl，/in 里是打包时抄出来的那三个）。全部一样退出码 0，有不一样的逐个列出、退出码 1。
# 另外说一句、不算进去：网站名单在内容那边还有第三份（/work/config/archive_round1.json，11-23 才知道：转文字和建库读
# 「第一轮有哪些网站」，面板读站名、首页、imp、第几轮）。那是内容那边的档，这里只说它和这一包的一不一样。
set -uo pipefail

CRAWL="${CRAWL:-/crawl}"
IN="${IN:-/in}"
HOST="${HOST:-/host}"
COPIES="${COPIES:-code-update export-code}"
(cd "$IN" && sha256sum -c --quiet crawler-code.tar.sha256) || { echo "包坏了或不是这一份" >&2; exit 2; }
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
tar -xf "$IN/crawler-code.tar" -C "$T"
same=0
diff=0
say_diff() { echo "  不一样：$1"; diff=$((diff + 1)); }
for c in $COPIES; do
  while IFS= read -r f; do
    if cmp -s "$T/$f" "$CRAWL/$c/$f"; then same=$((same + 1)); else say_diff "$CRAWL/$c/$f"; fi
  done < <(cd "$T" && find src config -type f 2>/dev/null | LC_ALL=C sort)
  for old in "$CRAWL/$c"/src/nknu/archive/*.py; do
    [ -f "$old" ] || continue
    [ -f "$T/src/nknu/archive/$(basename "$old")" ] || say_diff "${old}（仓库里已经没有这个档）"
  done
done
for f in "$T"/lab/*; do
  b="$(basename "$f")"
  if cmp -s "$f" "$CRAWL/code-update/lab/$b"; then same=$((same + 1)); else say_diff "$CRAWL/code-update/lab/$b"; fi
done
for b in compose.yaml nightly_update.cmd nightly_hidden.vbs; do
  if cmp -s "$IN/$b" "$HOST/$b"; then same=$((same + 1)); else say_diff "C:\\nknu-crawl\\$b"; fi
done
CONTENT_LIST="${CONTENT_LIST:-/work/config/archive_round1.json}"
if [ ! -f "$CONTENT_LIST" ]; then
  echo "  内容那边读的名单没挂进来，没比（要比就加 -v nknu-work:/work:ro）"
elif cmp -s "$T/config/archive_round1.json" "$CONTENT_LIST"; then
  echo "  内容那边读的名单（${CONTENT_LIST}）和这一包的一样"
else
  echo "  内容那边读的名单（${CONTENT_LIST}）和这一包的不一样（动了第一轮有哪些网站，或站名、首页、imp、platform、why，"\
"要告诉内容那边换；只动爬虫自己的栏位 caps、revisit、skip 不用管）"
fi
echo "一样的 ${same} 个，不一样的 ${diff} 个"
[ "$diff" -eq 0 ]
