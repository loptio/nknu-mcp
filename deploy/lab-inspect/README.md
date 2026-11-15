# 内部检视工具：怎么开、怎么看

三样东西都在实验室电脑的 Docker 里跑，只读，只有那台电脑自己连得到。从 Mac 看要先开隧道。

## 平常怎么看（在 Mac 上）

1. 开隧道（开着的时候才看得到，Ctrl+C 结束）：

   ```bash
   scripts/inspect-tunnel.sh
   ```

2. 打开网页：

   | 要看什么 | 网址 |
   |---|---|
   | 有哪些网站、每个网站的资料长什么样（资料总览，deploy/lab-overview） | http://127.0.0.1:18879 |
   | 资料库（Datasette） | http://127.0.0.1:18001 |
   | 搜索为什么这样排、一份文件走到哪（检视台） | http://127.0.0.1:18878 |
   | 抓下来的网页原样（爬虫检视器，本来就有） | http://127.0.0.1:18877 |

3. 亲手调 `search`、`read`（MCP Inspector）：另开一个终端机跑下面这条，它会自己打开一页。

   ```bash
   npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:18766/mcp --transport http
   ```

4. 两条命令（另开一个终端机，先告诉它检视台在哪）：

   ```bash
   export NKNU_OPS_URL=http://127.0.0.1:18878
   ```

   ```bash
   uv run --no-sync nknu search "115-1 停修" --explain
   ```

   ```bash
   uv run --no-sync nknu trace <网址或文件编号>
   ```

   想知道某一份为什么没排上来，加 `--doc <文件编号或网址>`。要给程序读就加 `--json`。

## 在实验室电脑上（PowerShell）

compose 档放在 `C:\nknu-transfer\inspect\compose.yaml`。

| 要做什么 | 命令 |
|---|---|
| 三个都开 | `docker compose -f C:\nknu-transfer\inspect\compose.yaml up -d` |
| 看有没有在跑 | `docker compose -f C:\nknu-transfer\inspect\compose.yaml ps` |
| 换库或换程序以后 | `docker compose -f C:\nknu-transfer\inspect\compose.yaml restart` |
| 看检视台的纪录 | `docker compose -f C:\nknu-transfer\inspect\compose.yaml logs --tail 50 ops` |
| 全关 | `docker compose -f C:\nknu-transfer\inspect\compose.yaml down` |

要注意的四件事：

- **换库以后重启一次。** 建库程序把 `aca.db` 换掉以后，Datasette 还开着旧的那个档，要重启才会换。检视台和 MCP 服务每次都重新开库，马上就是新的；三个一起重启最省事。检视台每一页上面写着它读的库是什么时候建的。
- **检视工具用的是自己那份程序**，装在磁碟区的 `/work/inspect-code`，不是建库用的 `/work/src`。所以平常送 `code.tar` 不会换到它；建库的程序更新以后，要照下面「换程序」重装一次，检视台看到的打分和「没进库的原因」才是新的。忘了也会知道：检视台每一页上面写着它是什么时候装的，建库的程序和它装的那份不一样时，页面最上面会出一条红色的提醒。
- **电脑重开机以后**要有人登入、Docker Desktop 开着，三个服务才会自己起来（和爬虫看板一样）。
- **检视台的「测试题成绩板」是现算的。** 首页和搜索测试那一页最上面，每道测试题一格，写它最好的一份标准答案排第几。库换过、或隔了一天以后第一次打开那一页，它会在背景把 33 题各搜一次（一次一题，约一分钟，用显卡），格子一题一题填上；算过就记着，不再重搜。你自己搜的问题会排在它两题之间，最多等一题。

## 换程序

为什么不用平常的 `code.tar`：它带的是整个工作区，别的 session 改到一半的档也会一起过去。检视工具只送自己的档。

1. 在 Mac 上打包（写到仓库外面）：

   ```bash
   deploy/lab-inspect/pack.sh
   ```

2. 照它印出来的那一行送到实验室电脑的 `C:\nknu-transfer\inspect\`（只经 SSH）。

3. 在实验室电脑上装，再重启：

   ```
   docker run --rm --network none -v nknu-work:/work -v C:\nknu-transfer\inspect:/in:ro nknu-pipeline bash /in/install.sh
   docker compose -f C:\nknu-transfer\inspect\compose.yaml up -d
   docker compose -f C:\nknu-transfer\inspect\compose.yaml restart
   ```

`install.sh` 会先核对两件事：包没坏；检视工具改过的那四个档，实验室电脑上的是打包时当作基础的那一版。对不上它会停下来，不会装坏。上一版留在 `/work/inspect-code.prev`。

## 页面长什么样子是哪里定的

两个面板和资料总览用同一套纸色、墨色和卡片；「好坏」用绿、橙、红，文件种类用资料总览那四个颜色；画「多少」Datasette 用资料总览的紫色（蓝色是公告、一般网页的颜色），检视台还是蓝色。

名字和说明的规矩（11-15 用户：「很多名字看着不直觉 然后感觉是想缩短文字 但又想保留细节导致不上不下的」）：页面上只放一个直白的名字，细节放进鼠标停上去才出现的悬浮框，样子和资料总览的一样。两个面板跑同一段悬浮框程序：Datasette 的 `static/tip.js`，检视台的 `look.py` 的 `TIP_JS`（测试会核对两份一样）；元素上写 `data-tip`（说明）和 `data-tip-h`（说明上面的名字），不用浏览器自带的 `title`。同一个意思在三个面板用同一个词（类型、主料／背景、合并、出处……），栏位的短名就是查询结果的栏名。

Datasette 的三样东西各在哪：表格页上面那排栏位和默认显示哪几栏是 `plugins/columns.py`（要改默认栏位就改它的 `DEFAULT`）；找网站的清单是 `templates/_sitefind.html` 加 `static/sitefind.js`；简体繁体都认是 `plugins/script.py`（字表 `plugins/t2s.json`，不用维护）。

| 面板 | 样子写在哪 |
|---|---|
| 检视台 | `src/nknu/ops/look.py`（颜色、样式、小脚本、图示）、`search_page.py`（搜索测试、成绩板）、`trace_page.py`（文件追踪）；成绩板的计算在 `board.py` |
| Datasette | `deploy/lab-inspect/datasette/templates/`（每一种页面，照 Datasette 0.65.5 的原版改写成中文）、`static/tc.css`（样式）、`plugins/guide.py`（表的中文名、栏位短名、地图）、`plugins/cells.py`（格子里的小标签和链接）；每个栏位的意思只写在 `metadata.yaml`，写法是「短名：说明」 |

Datasette 的模板是照 0.65.5 改的：换 Datasette 版本以前，要先拿新版的原版模板对一次。

## 三个服务各是什么

| 服务 | 实验室电脑的口 | 是什么 |
|---|---|---|
| `datasette` | 8001 | 现成的工具 Datasette（官方映像，0.65.5）。把资料库变成可以点、筛、数的网页。读正式库 `aca.db` 和教务处对照库 `aca1115.db` |
| `mcp` | 8766 | 我们的 MCP 服务，HTTP 模式。和以后给学生的 AI 用的是同一支程序、同一个库 |
| `ops` | 8878 | 检视台：搜索测试、文件追踪两页 |

三个都不回答别的网站借你的浏览器、用它自己的名字连进来的请求。Datasette 和检视台只认本机名字（127.0.0.1、localhost）；MCP 服务另外也认它自己那台机器的位址。谁连得到它们，靠的是 Docker 只把口开在实验室电脑自己的 127.0.0.1 上。
