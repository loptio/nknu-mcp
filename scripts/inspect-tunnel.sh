#!/usr/bin/env bash
# 内部检视工具的隧道（plan 2026-11-15 T7）：在 Mac 上跑，把实验室电脑上只听本机的三个服务接到这台 Mac。
# 开着的时候才看得到；Ctrl+C 结束。实验室电脑的 SSH 名字默认是 nknu-lab，要换就设 NKNU_LAB_HOST。
set -euo pipefail

HOST="${NKNU_LAB_HOST:-nknu-lab}"
FORWARDS=(-L 18001:127.0.0.1:8001 -L 18766:127.0.0.1:8766 -L 18878:127.0.0.1:8878)
# 资料总览 11-21 并进检视台（18878 的 /sites），不再有自己的端口
# 爬虫看板 11-21 停用（看抓下来的原样在检视台的 /check），不再开 18877

cat <<'EOF'
正在连实验室电脑。连上以后（没有报错、停在这里就是连上了；Ctrl+C 结束）：
  资料库（Datasette）       http://127.0.0.1:18001
  检视台（每晚运行、网站、测试、文件搜寻、网页核查） http://127.0.0.1:18878
  MCP Inspector：另开一个终端机跑
      npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:18766/mcp --transport http
  两条命令在 Mac 上用：另开一个终端机先跑
      export NKNU_OPS_URL=http://127.0.0.1:18878
EOF

exec ssh -N -o ExitOnForwardFailure=yes "${FORWARDS[@]}" "$HOST"
