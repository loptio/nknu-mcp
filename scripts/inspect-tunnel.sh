#!/usr/bin/env bash
# 内部检视工具的隧道（plan 2026-11-15 T7）：在 Mac 上跑，把实验室电脑上只听本机的三个服务接到这台 Mac。
# 开着的时候才看得到；Ctrl+C 结束。实验室电脑的 SSH 名字默认是 nknu-lab，要换就设 NKNU_LAB_HOST。
set -euo pipefail

HOST="${NKNU_LAB_HOST:-nknu-lab}"
FORWARDS=(-L 18001:127.0.0.1:8001 -L 18766:127.0.0.1:8766 -L 18878:127.0.0.1:8878)
# 爬虫检视器（逐份追踪第一步会链过去）：已经另外开着隧道就不重复开
if ! nc -z 127.0.0.1 18877 >/dev/null 2>&1; then
  FORWARDS+=(-L 18877:127.0.0.1:8877)
fi

cat <<'EOF'
正在连实验室电脑。连上以后（没有报错、停在这里就是连上了；Ctrl+C 结束）：
  翻库里的表（Datasette）   http://127.0.0.1:18001
  检视台（搜索试验台、逐份追踪） http://127.0.0.1:18878
  爬虫检视器              http://127.0.0.1:18877
  MCP Inspector：另开一个终端机跑
      npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:18766/mcp --transport http
  两条命令在 Mac 上用：另开一个终端机先跑
      export NKNU_OPS_URL=http://127.0.0.1:18878
EOF

exec ssh -N -o ExitOnForwardFailure=yes "${FORWARDS[@]}" "$HOST"
