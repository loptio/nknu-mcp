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
   | 翻库里的表（Datasette） | http://127.0.0.1:18001 |
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

要注意的三件事：

- **换库以后重启一次。** 建库程序把 `aca.db` 换掉以后，Datasette 还开着旧的那个档，要重启才会换。检视台和 MCP 服务每次都重新开库，马上就是新的；三个一起重启最省事。检视台每一页上面写着它读的库是什么时候建的。
- **检视工具用的是自己那份程序**，装在磁碟区的 `/work/inspect-code`，不是建库用的 `/work/src`。所以平常送 `code.tar` 不会换到它；建库的程序更新以后，要照下面「换程序」重装一次，检视台看到的打分和「没进库的原因」才是新的。忘了也会知道：检视台每一页上面写着它是什么时候装的，建库的程序和它装的那份不一样时，页面最上面会出一条红色的提醒。
- **电脑重开机以后**要有人登入、Docker Desktop 开着，三个服务才会自己起来（和爬虫看板一样）。

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

## 三个服务各是什么

| 服务 | 实验室电脑的口 | 是什么 |
|---|---|---|
| `datasette` | 8001 | 现成的工具 Datasette（官方映像，0.65.5）。把资料库变成可以点、筛、数的网页。读正式库 `aca.db` 和教务处对照库 `aca1115.db` |
| `mcp` | 8766 | 我们的 MCP 服务，HTTP 模式。和以后给学生的 AI 用的是同一支程序、同一个库 |
| `ops` | 8878 | 检视台：搜索试验台、逐份追踪两页 |

三个都不回答别的网站借你的浏览器、用它自己的名字连进来的请求。Datasette 和检视台只认本机名字（127.0.0.1、localhost）；MCP 服务另外也认它自己那台机器的位址。谁连得到它们，靠的是 Docker 只把口开在实验室电脑自己的 127.0.0.1 上。
