# 对外的服务（MCP、网页、HTTP 接口）

给学生和他们的 AI 用的那个服务。一个进程、一个端口，里面有：

| 路径 | 是什么 |
|---|---|
| `/mcp` | MCP，免登录，工具 `search` 和 `fetch` |
| `/` | 说明页，带搜索框、各家 AI 怎么接 |
| `/search?q=…` | 搜索结果页（可加 `unit=`、`sort=date`、`limit=`） |
| `/doc/<编号>` | 一份文件的页（长文件用 `?part=节号`） |
| `/api/search`、`/api/fetch` | 同样的结果，回 JSON，和 MCP 回的一字不差 |
| `/llms.txt` | 给逛到网站的 AI 读的说明 |
| `/health` | 活着没有、库是什么时候建的 |

设计：`docs/superpowers/specs/2026-11-21-outward-interfaces-design.md`。程序：`src/nknu/retrieval/public.py`（服务）、`web.py`（网页）、`server.py`（MCP 的两个工具）。测试：`tests/retrieval/test_public_1121.py`。

## 现在的状态（2026-11-24）

- 装在实验室电脑上，容器 `nknu-public-public-1`，只开本机的 `127.0.0.1:8770`。**还没有对外网址**，外面连不到。
- 读的是正式库 `aca.db`，只读。每晚换库不用重启。
- 两个算分模型用实验室上共用的那一份（检视台 compose 里的 `models` 服务，本机 8780 端口），这个服务自己不占显存，每次搜索约 1 秒。
- 共用的那份没回应或在重启时，这个服务当场自己载一份接着答（那一次约 17 秒，之后占显存约 3 GB，到下次重启为止）。11-21 实测过：把模型服务的地址指到空端口，8 个问题各前 20 笔和平常完全一样。
- 查询纪录在实验室电脑的 `C:\nknu-transfer\public\log\queries.jsonl`。
- 换过的程序：
  - 11-22 13:09 装了 `sort=date` 的修正（`a97ffd2`），旧包留成 `public-src.1121.tar`。
  - 11-22 20:5x 装了「附带其他单位」：填了 unit 时多给 `other_units`，见接口设计稿 §二 `unit`。这次是已提交的 `3a7a61e` 加上工作区的 `server.py`、`respond.py`、`web.py`、`public.py`（这四个档同一晚提交在 `b6f2701`），旧包留成 `public-src.1122a.tar`。查询纪录多一栏 `other_ids`。
  - `pack.sh` 从这次起也带工作区的 `respond.py`：它和 `server.py` 要成对，只换其中一个，填了 unit 的搜索会出错。
  - 11-24 09:5x 照 git `ba2c7ed` 整份重装，跟实验室上的其他程序一起对齐 git。和上一版只差 `retrieval/hybrid.py`、`search.py`（`7f0e86d` 删掉不再用的开关，行为不变）。旧包留成 `public-src.1122b.tar`，旧的程序资料夹改名 `src.1122b`（这次没用 `rmdir`）。重启后 `/health` 正常、89,317 份，搜索答得出来，日志没有错误。

## 从 Mac 上看

开一条隧道（开着的时候才看得到，Ctrl+C 结束）：

```bash
ssh -N -L 18770:127.0.0.1:8770 nknu-lab
```

然后浏览器开 http://127.0.0.1:18770/ 。MCP 的网址是 `http://127.0.0.1:18770/mcp`，可以用 MCP Inspector 或加进 Claude Code 试：

```bash
npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:18770/mcp --transport http
```

## 查询纪录

每次查询一行 JSON，没答成的也记一行：

```json
{"t": "2026-11-21T23:02:54+08:00", "via": "mcp", "tool": "search", "query": "畢業門檻", "detail": "snippet", "limit": 2, "found": true, "ids": ["du…", "du…"], "ms": 1295}
{"t": "2026-11-21T23:02:54+08:00", "via": "mcp", "tool": "search", "query": "畢業門檻", "sort": "newest", "found": false, "ids": [], "ms": 3, "error": "bad_args"}
```

- `via`：从哪个接口来，`mcp`、`web`、`api`。
- `found`：有没有查到；`ids`：回了哪几份文件。
- `ms`：从收到到答完的毫秒数，排队等的时间算在里面。退回自己载模型的那一次约 17 秒，会如实记下来。
- `error`：只有没答成的那一行有。`busy`（排队超过 20 秒）、`bad_args`（参数填错）、`unavailable`（库读不到）、`internal`（程序出错）。这种行 `found` 是 false、`ids` 是空的。
- 查无资料不算没答成：`found` 是 false，但没有 `error`。
- 只记工具自己的参数（`query`、`detail`、`limit`、`unit`、`sort`、`id`、`part`）。不记 IP，不记是谁。查询里像电话、学号、信箱的字会先遮掉。
- 没带 `q` 的搜索网址不算一次查询，不记。
- 满 20 MB 换档（`queries.jsonl.1`…），留 5 份。
- 检视台首页的「查詢服務」读这个档（只读）；栏位名要改先跟面板那边说。

看最近几笔：

```
ssh nknu-lab "powershell -NoProfile -Command Get-Content C:\nknu-transfer\public\log\queries.jsonl -Tail 20 -Encoding UTF8"
```

## 换程序

1. 在 Mac 上打包（带的是 git 里已提交的程序，加上这个服务自己的三个档和要成对带的 `respond.py`）：

   ```bash
   deploy/lab-public/pack.sh
   ```

2. 照它印出来的那一行 `scp` 到实验室电脑的 `C:\nknu-transfer\public\`。

3. 在实验室电脑上解开、重启（经 SSH 下 docker 命令要先加 `set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" &`）：

   ```
   cd /d C:\nknu-transfer\public
   rmdir /s /q src
   tar -xf public-src.tar
   docker compose -f C:\nknu-transfer\public\compose.yaml up -d
   docker compose -f C:\nknu-transfer\public\compose.yaml restart
   ```

4. 核对：

   ```
   curl -s http://127.0.0.1:8770/health
   ```

   看到 `"status":"ok"` 和文件数就是好了。

建库那边加了网站或单位以后，也要 `restart` 一次：说明页和 MCP 连上来时的说明是启动时从库里生成的。

## 真的对外时要做的

服务本身不用改，只要三件：

1. **架隧道**，把一个固定的主机名转到这台电脑的 `127.0.0.1:8770`。两种做法和踩过的坑在 `deploy/tunnel/README.md`（端口换成 8770）。主机名公开以后不能再改。
2. **写 `.env`**，放在 `C:\nknu-transfer\public\.env`，然后 `up -d`：

   ```
   NKNU_ALLOWED_HOSTS=mcp.example.org
   NKNU_ALLOWED_ORIGINS=https://mcp.example.org
   NKNU_PUBLIC_URL=https://mcp.example.org
   NKNU_TRUST_CF_HEADER=1
   ```

   `NKNU_TRUST_CF_HEADER`：Cloudflare 隧道填 `1`；Tailscale Funnel 一定要填 `0`（那时所有人共用一份每分钟 600 次的额度）。

3. **从外面核对**（用手机网络）：

   ```bash
   curl -s https://mcp.example.org/health                                                    # 200
   curl -s -o /dev/null -w '%{http_code}\n' https://mcp.example.org/.well-known/oauth-protected-resource   # 404
   ```

   给 AI 填的网址是 `https://mcp.example.org/mcp`，结尾不要加斜线（`/mcp/` 会被转址，有些客户端不接受）。

没写进白名单的主机名一律回 421。

## 保护

- 主机名白名单：只回答白名单里的名字和本机名字。
- 限流：每个来源每分钟 60 次；Anthropic 的网段合计每分钟 600 次。超过回 429。
- 同时最多算 4 个搜索，其余排队；排超过 20 秒回「稍后再试，这次失败不代表查无资料」。
- `/search`、`/doc`、`/api` 的回应都带「不要收录」的标记，搜索引擎不会把我们的副本排到学校原页前面。

## 关掉

```
docker compose -f C:\nknu-transfer\public\compose.yaml down
```

只关服务，库和查询纪录都不会动。
