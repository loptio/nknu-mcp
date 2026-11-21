#!/usr/bin/env bash
# 在 Mac 上跑：把对外服务要的程序打成一包。带的是 git 里已经提交的 src/nknu，
# 不带工作区里别的 session 改到一半的档。
#   deploy/lab-public/pack.sh [输出资料夹]
# 写出：public-src.tar、public-src.tar.sha256、compose.yaml。包里只有程序，没有资料；只经 SSH 送到实验室电脑。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
OUT="${1:-${TMPDIR:-/tmp}/nknu-public}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"
(cd "$OUT" && rm -f public-src.tar public-src.tar.sha256 compose.yaml)

git archive HEAD src/nknu | tar -x -C "$STAGE"
# 对外服务自己的三个档：还没提交时带工作区里的这一版（提交以后和上面那份一样，这一步等于没做）
OWN=(src/nknu/retrieval/public.py src/nknu/retrieval/web.py src/nknu/retrieval/server.py)
for f in "${OWN[@]}"; do cp "$f" "$STAGE/$f"; done

(cd "$STAGE" && COPYFILE_DISABLE=1 tar --no-xattrs -cf "$OUT/public-src.tar" src)
(cd "$OUT" && shasum -a 256 public-src.tar > public-src.tar.sha256)
cp deploy/lab-public/compose.yaml "$OUT/"

echo "打好了：${OUT}（照 $(git rev-parse --short HEAD) 这一版，加上对外服务自己的三个档）"
ls -l "$OUT"
echo "接着（都只经 SSH）："
echo "  (cd $OUT && scp public-src.tar public-src.tar.sha256 compose.yaml nknu-lab:C:/nknu-transfer/public/)"
echo "  再照 deploy/lab-public/README.md「换程序」那一节在实验室电脑上装。"
