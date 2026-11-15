# 资料总览（实时）：怎么开、怎么看

和检视台一样，在实验室电脑的 Docker 里跑，只读，只有那台电脑自己连得到。从 Mac 看要先开隧道。

## 平常怎么看（在 Mac 上）

1. 开隧道（和检视台同一条，开着的时候才看得到，Ctrl+C 结束）：

   ```bash
   scripts/inspect-tunnel.sh
   ```

2. 打开 http://127.0.0.1:18879

页面每分钟问一次实验室电脑。正式库重建以后，统计会在库不再变动一分钟后自动重做（约 5 秒），页面跟着刷新，正在看的单位、网站、按钮和捲动位置都保留。顶栏写着「资料截至」正式库建好的时间。

顶栏出现红字时：

- 「连不上实验室电脑」：隧道断了，或服务没开。显示的是上一次的资料。
- 「更新失败」：统计出错，滑鼠移上去看原因。显示的是上一次的资料，下一分钟会再试。

## 它读什么

全部只读，都在磁碟区 nknu-work：

- 正式库 `research/pipeline_2026-11-02/aca.db`：每个网站有哪些文件、几个字、日期、单位树（拿来按学院分组）、合并掉的副本。
- 每个网站建库用的那份导出清单（`manifest.csv`）：抓取的结果。爬虫重新导出、但还没重建库时，这里不会变。
- 建库报告 `research/pipeline_2026-11-02/build_report.json`：抓到但没进库的原因和数量。
- `config/archive_round1.json`：网站名单（名字、重要性、首页、留到第二轮的网站）。

## 在实验室电脑上

这个资料夹的五个档放在 `C:\nknu-transfer\overview\`。

| 要做什么 | 命令 |
|---|---|
| 开 | `docker compose -f C:\nknu-transfer\overview\compose.yaml up -d` |
| 看有没有在跑 | `docker compose -f C:\nknu-transfer\overview\compose.yaml ps` |
| 看纪录（每次统计一行） | `docker compose -f C:\nknu-transfer\overview\compose.yaml logs --tail 30` |
| 关 | `docker compose -f C:\nknu-transfer\overview\compose.yaml down` |

电脑重开机以后，要有人登入、Docker Desktop 开着，它才会自己起来（和检视台、爬虫看板一样）。

## 换程序

1. 在 Mac 上把这个资料夹的五个档送过去（只经 SSH）：

   ```bash
   scp deploy/lab-overview/{server.py,overview_data.py,page.html,compose.yaml,README.md} nknu-lab:C:/nknu-transfer/overview/
   ```

2. 在实验室电脑上：`docker compose -f C:\nknu-transfer\overview\compose.yaml restart`

它不用 `code.tar`，也不读建库用的 `/work/src`，所以送它不会动到建库和检视台。
