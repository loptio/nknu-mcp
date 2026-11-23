# 实验室的 Strata：载入、调用、卸下

Strata 是装在实验室电脑上的本地模型，拿来测我们的 MCP。桌面程序在 `E:\Strata`。装了两个模型：`iq3_s`（Qwen3.8-Flash-Next IQ3_S，平常用这个）和 `swift-iq2_xs`。

平时不载着。**要用的时候才载入，用完卸下。** 每晚 00:45 会自动卸下（见最后一节）。

## 显卡怎么让

- 显卡 20 GB。Strata 载入时，会把当时剩下的显卡扣掉预留以后全部拿走。11-22 起预留存成 1.5 GB（原来界面上选「留 20%」，是 4 GB）。我们自己常驻的服务（检视台、对外服务、共用的检索模型）约占 3.7 GB，所以 Strata 大约拿 13.9 GB，载好后显卡只剩不到 1 GB。
- **先开我们要用的工具，再载 Strata。** 后开的东西只分得到 1.5 GB 预留，连一份检索模型都放不下（两个模型一起要 2～3 GB）。
- **要建库、跑 33 题或 194 题的量测、转文字时，先把 Strata 卸下。**
- 不要在命令行跑 `strata launch`。它会另开一份服务器，同一个模型再占一次内存和显卡。

## 跳专家

- Strata 在 NVIDIA 显卡上默认开着「跳专家」（`STRATA_ROUTE_TAIL_SKIP=7`）：要用的专家不在显卡上、分量又小的，直接跳过不算。Strata 自己的说明写，这样写得快约 13～16%，但答案会稍微走样。显卡越挤跳得越多：11-22 留 4 GB 时，约 15% 的专家查询被跳过。
- **11-22 起存的设定是关掉跳专家、预留 1.5 GB。** 存在 `C:\Users\user\.strata\data\native-tune\iq3_s.json`，载入时照它（11-22 用接口载入验过）。关掉以后每个专家都照算，显卡多给少给只影响速度、不影响答案。
- 改设定用 `PATCH http://127.0.0.1:47831/api/native/models/iq3_s/settings`，下次载入才生效，改时它会留一份 `.bak`。
  - 现在存的：`{"values":{"env":{"STRATA_ROUTE_TAIL_SKIP":"0"},"vram_reserve_mib":1536}}`。
  - 回到 Strata 原来的样子（跳专家、留 4 GB）：`{"values":{"env":{},"vram_reserve_mib":4096}}`。
- 11-22 凌晨答 83 题时这样配（369 次请求，一次都没跳）：
  - Strata 拿约 13.9 GB，写出中位 48 token/秒，读入约 1,550；开着跳专家、留 4 GB 时是 51。
  - 载好后显卡只剩约 0.85 GB，引擎警告低于 1 GB 可能变慢，但整晚没有变慢。
  - 不能再多给（例如 15 GB）：我们的检索模型也在卡上，搜索时会多用约 1.2 GB。

## Strata 的本机接口在哪

Strata 桌面程序开着时，会在实验室电脑上开一个本机接口，只听 127.0.0.1。载入、卸下、调用都走这个接口。11-21 的端口是 **47831**，下面都用它；重开 Strata 后端口可能会变。

先确认端口还对（在实验室电脑上跑）：

```bat
curl -s http://127.0.0.1:47831/api/app/mode
```

回 `{"mode":...,"canQuit":...}` 就是它。没回答的话，用这段 PowerShell 重找。它在 node 进程里找命令行带 `Strata\host\server` 的那个，列出它听的端口：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object { $_.CommandLine -like '*Strata*host*server*' } |
  ForEach-Object { Get-NetTCPConnection -State Listen -OwningProcess $_.ProcessId } | Select-Object LocalAddress, LocalPort
```

`unload_strata.ps1` 里的 `Find-StrataHost` 也是这样找的。

## 载入

**最稳的办法：在 Strata 界面选模型，按载入。** 这是用户平常用的路，确定能用。远端的 session 没办法按，就请用户按。

**用接口载入（11-22 00:10 实测过）：** 要带 `"vision":"no"`，把读图关掉。

```bat
curl -s -X POST http://127.0.0.1:47831/api/native/operations -H "content-type: application/json" -d "{\"kind\":\"load\",\"args\":{\"model\":\"iq3_s\",\"vision\":\"no\"}}"
```

- 不带 `vision` 时照上次存的设定载入，而存的设定开着读图。Windows 的「智慧型应用程式控制」会挡掉读图元件 `strata-vision.exe`（`WinError 4551`），引擎一启动就退出，载不起来（11-11、11-21 两次都这样）。测 MCP 用不到读图。
- 带 `"vision":"no"` 会把这个模型的读图偏好存成关，界面上可以再打开（但开着读图照样会被 Windows 挡）。其余设定（上下文长度、预留）照上次存的，存在 `C:\Users\user\.strata\data\native-tune\iq3_s.json`。
- 载好约 50 秒。
- 看载好了没有：`/v1/models` 的 `data` 里出现 `iq3_s` 就好了。载入工作本身的状态和错误讯息在 `GET /api/native` 的 `jobs`（最后一笔 `kind` 是 `load` 的那笔）。`/api/runtime` 的 `instances` 看不到桌面程序载的模型，不能拿来判断。

## 调用

Strata 本机接口同时就是一个 OpenAI 格式的地址：

| 用途 | 地址 |
|---|---|
| 对话（OpenAI 格式） | `http://127.0.0.1:47831/v1/chat/completions` |
| 列出载着的模型 | `http://127.0.0.1:47831/v1/models`（没载模型时 `data` 是空的） |
| Anthropic 格式 | `http://127.0.0.1:47831/v1/messages`（看到有这个口，没试过） |

- **钥匙**：11-21 没设，不用带。以后在 Strata 的设定里设了，就要带 `Authorization: Bearer <钥匙>` 或 `x-api-key: <钥匙>`。钥匙不要印出来，也不要贴进对话。
- **模型名**：填 `/v1/models` 列出来的那个（平常是 `iq3_s`）。
- 例子（在实验室电脑上）：

```bat
curl -s http://127.0.0.1:47831/v1/chat/completions -H "content-type: application/json" -d "{\"model\":\"iq3_s\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"max_tokens\":64}"
```

- **从 Mac 连**：先开隧道 `ssh -N -L 18791:127.0.0.1:47831 nknu-lab`，再把地址换成 `http://127.0.0.1:18791/v1/...`。
- **引擎自己的端口**：模型引擎另外开一个端口（以前看到过 18500），每次载入都换一把新钥匙，存在 `C:\Users\user\.strata\data\native-instances\<编号>.json`。一般不用它，走上面那个地址就好。
- `strata connect` 只认 `strata launch` 开的服务器，不认桌面程序载的模型。

## 让 Strata 的 agent 用我们的 MCP

在 Strata 的 Settings → Tools 加一个 streamable-HTTP 工具服务器：

- 名字：`nknu`
- 网址：`http://127.0.0.1:8770/mcp`（对外服务，见 `deploy/lab-public/README.md`）

加好以后，agent 里的工具名是 `mcp__nknu__search`、`mcp__nknu__fetch`。agent 的纪录在 `C:\Users\user\.strata\data\agent`。

## 卸下

- **界面**：在 Strata 里按卸下。
- **脚本**：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File C:\nknu-strata\unload_strata.ps1
```

  加 `-DryRun` 只看载着什么、不卸。
- **接口（11-22 实测过）**：从 `/v1/models` 的 `data[0].meta.instanceId` 拿实例编号，再 `POST /api/native/instances/<编号>/unload`。`/api/runtime/<id>/unload` 对桌面程序载的模型不管用（`/api/runtime` 里没有它）。没试过的另一条：`POST /api/models/unload`，不管它在不在忙直接卸。

## 每晚 00:45 自动卸下

每晚 01:00 抓取、进库要用显卡，所以 00:45 先把 Strata 的模型卸下，Strata 程序本身不关。早上不会自动载回去。

| 东西 | 位置 |
|---|---|
| 排程 | 工作排程器 `nknu-strata-unload`：每天 00:45，只在有人登入时跑（和爬虫的 `nknu-nightly-update` 一样） |
| 排程执行的指令 | `wscript.exe //B //Nologo C:\nknu-strata\unload_strata.vbs`（不跳视窗） |
| 脚本 | `C:\nknu-strata\unload_strata.ps1`（本资料夹的同名档，存成 UTF-8 带 BOM，不然中文会乱码） |
| 纪录 | `C:\nknu-strata\unload.log`：每跑一次写一行，卸了没有、前后各用了多少显卡 |

模型还在回答问题时，脚本每 5 分钟再试一次；等满 60 分钟还在忙，就强制卸下。

- 马上跑一次排程：`schtasks /run /tn nknu-strata-unload`
- 停掉排程：`schtasks /change /tn nknu-strata-unload /disable`
- 换脚本或重装：把本资料夹的 `unload_strata.ps1`、`unload_strata.vbs` 复制到 `C:\nknu-strata\`，再执行：

```bat
schtasks /create /tn nknu-strata-unload /tr "wscript.exe //B //Nologo C:\nknu-strata\unload_strata.vbs" /sc daily /st 00:45 /f
```

## 验过什么（11-21）

- **验过**（当时 Strata 开着、没载模型）：
  - 本机接口在 47831，`/api/app/mode`、`/api/runtime`、`/api/serving`、`/v1/models` 都有回答。
  - `/api/serving` 写着没设钥匙（`apiKeySet: false`）。
  - 卸下脚本找得到接口、读得到状态；排程手动跑过一次，结果 0，纪录有写。
- **11-22 00:10～00:26 验过**（从 Mac 经隧道）：
  - 用接口载入：带 `"vision":"no"` 成功，约 50 秒；不带就被 Windows 挡掉读图元件而失败（见「载入」）。
  - 对话：`/v1/chat/completions` 带 OpenAI 格式的 `tools` 能正常调用工具，回 `tool_calls`。19 轮实测：写出中位 51 token/秒（按 token 加权 49，个别轮掉到 25～29），读入中位约 1,600 token/秒。同一段对话接着问时前面读过的部分会重用；但请求里带 `tool_choice: "none"` 时会把整段对话重读一次。一次只服务一个请求。
  - 显卡：载入时我们这边（共用的两个打分模型加 Windows 桌面）占约 3.6 GB，Strata 拿了约 12.5 GB，合计约 16.2 GB，留下设定的 4 GB 预留。Strata 独占显卡时（11-04 自动调校）约用 17 GB、写出 54.6 token/秒。
  - 接我们的对外服务当工具（search、fetch）答题：一题 2.5～6 分钟，这个模型每轮常同时查两次、每次要 15～20 笔，上下文很快涨到十几万 token。
  - 卸下：`POST /api/native/instances/<编号>/unload` 成功，显卡从 16.2 GB 回到 3.8 GB。
- **没验过**：`POST /api/models/unload`；载着模型时的自动卸下。
- Strata 改版后接口可能会变。自动卸下出问题时，`unload.log` 会写「出错」。
