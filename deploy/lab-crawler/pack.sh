#!/usr/bin/env bash
# 在 Mac 上跑：把爬虫在实验室电脑上用的东西打成一包。
#   deploy/lab-crawler/pack.sh [输出资料夹]          小包：只带爬虫自己的档（平常换程序用）
#   deploy/lab-crawler/pack.sh --full [输出资料夹]   整包：git 里已经提交的 src、config、certs（空机器重建用）
# 小包只带爬虫这条线的档，不带工作区里别的 session 改到一半的档：
#   src/nknu/archive/*.py、src/nknu/admin/cli.py、config/archive_round1.json、config/album_folders.json、
#   lab/（deploy/lab-crawler/lab：每晚脚本、导出脚本、要开浏览器的网站清单）
# 整包照 git 里的 HEAD 取（没提交的改动不会进去），再加同样的 lab/。
# 写出：crawler-code.tar、crawler-code.tar.sha256，并把 install.sh、check_lab.sh、proof_night.sh、export_and_compare.sh、
# compare_export.py 和主机上要放的三个档
# （compose.yaml、nightly_update.cmd、nightly_hidden.vbs）抄到同一个资料夹。包里只有程序，没有资料、没有金钥。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
FULL=0
if [ "${1:-}" = "--full" ]; then FULL=1; shift; fi
OUT="${1:-${TMPDIR:-/tmp}/nknu-crawler}"
KIT=deploy/lab-crawler
HOST_FILES=(compose.yaml nightly_update.cmd nightly_hidden.vbs)
TOOLS=(install.sh check_lab.sh proof_night.sh export_and_compare.sh compare_export.py)
mkdir -p "$OUT"
(cd "$OUT" && rm -f crawler-code.tar crawler-code.tar.sha256 "${HOST_FILES[@]}" "${TOOLS[@]}")

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
if [ "$FULL" = 1 ]; then
  git archive HEAD src config certs pyproject.toml uv.lock | tar -x -C "$STAGE"
else
  mkdir -p "$STAGE/src/nknu/archive" "$STAGE/src/nknu/admin" "$STAGE/config"
  cp src/nknu/archive/*.py "$STAGE/src/nknu/archive/"
  cp src/nknu/admin/cli.py "$STAGE/src/nknu/admin/"
  cp config/archive_round1.json config/album_folders.json "$STAGE/config/"
fi
mkdir -p "$STAGE/lab"
cp "$KIT"/lab/* "$STAGE/lab/"
(cd "$STAGE" && find . -type f ! -name MANIFEST.sha256 | LC_ALL=C sort | sed 's|^\./||' | tr '\n' '\0' \
  | xargs -0 shasum -a 256 > MANIFEST.sha256)
COPYFILE_DISABLE=1 tar --no-xattrs -C "$STAGE" -cf "$OUT/crawler-code.tar" .
(cd "$OUT" && shasum -a 256 crawler-code.tar > crawler-code.tar.sha256)
for f in "${HOST_FILES[@]}" "${TOOLS[@]}"; do cp "$KIT/$f" "$OUT/"; done

echo "打好了（$([ "$FULL" = 1 ] && echo 整包 || echo 小包)）：$OUT"
ls -l "$OUT"
echo "接着（都只经 SSH）："
echo "  ssh nknu-lab 'mkdir C:\\nknu-crawl\\in\\deploy'   （第一次才要）"
echo "  (cd $OUT && scp crawler-code.tar crawler-code.tar.sha256 ${HOST_FILES[*]} ${TOOLS[*]} nknu-lab:C:/nknu-crawl/in/deploy/)"
echo "  再照 deploy/lab-crawler/README.md「换程序」那一节在实验室电脑上装。"
