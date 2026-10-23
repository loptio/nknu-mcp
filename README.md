# 高師大校園事實層 MCP（nknu-facts）

把國立高雄師範大學（高師大）公開網頁上的**場館開放時間、教學行事曆、公告**整理成帶有效期和來源的資料，再透過 MCP 免登入提供給 AI 助理使用。

MCP（Model Context Protocol）是 AI 助理呼叫外部工具的共通規格。接上之後，你問 Claude、Cursor、Codex 或 Gemini CLI「和平校區重訓室今天幾點開？」，它會來這裡查預先算好的答案，不用自己翻舊網頁；這裡查不到的事，回答也會明說查不到。

- 這是學生維護的服務，**不是學校官方系統**。每個回答都附原公告網址，重要的事請以學校公告為準。
- 只能查詢，不能修改任何資料；查詢時伺服器不呼叫任何 AI 模型，答案都是事先依規則算好的句子。
- 程式碼採 Apache-2.0 授權。

## 4 個工具

| 工具 | 用途 | 參數 |
|---|---|---|
| `nknu_search` | 用關鍵字或問句查場館、行事曆和公告。不確定該用哪個工具時先用它。 | `query`；`kind`＝any / hours / calendar / announcement / place；`on_date`（YYYY-MM-DD，預設今天）；`campus`＝heping（和平）/ yanchao（燕巢）；`limit` 1–10 |
| `nknu_get_hours` | 查某個地點某天或連續幾天的開放時間、現在有沒有開、下次什麼時候變動。校區不明時列出候選，不替你猜。 | `place`（中文名、別名或 id）；`date`（YYYY-MM-DD 或民國 115-10-24，預設今天）；`days` 1–14 |
| `nknu_get_calendar` | 查教學行事曆事件：開學、考試、放假、選課等。 | `from_date`、`to_date`（預設今天起 30 天，有 keyword 時 180 天）；`keyword`；`limit` 1–50 |
| `nknu_get_fact` | 用回答裡的 id 取得單筆事實、有效期、來源，以及歷史版本。 | `id`；`include_history` |

4 個工具都免登入、唯讀。日期一律以台北時間計算。

## 資料來源與更新頻率

| 來源 | 網址 | 取得的資料 |
|---|---|---|
| 體育室場地開放時間 | https://c.nknu.edu.tw/reality/Page.aspx?PN=8 | 各場館的學期開放時段 |
| 體育室最新消息 | https://c.nknu.edu.tw/reality/ | 公告；寫明日期的停開公告另外轉成閉館資料 |
| 圖書館開館時間 | https://lis.nknu.edu.tw/open-time | 兩校區圖書館和愛閱館的開館時間、年度閉館日 |
| 教務處教學行事曆 | 公開的 Google 日曆 ICS（嵌在 https://c.nknu.edu.tw/aca/Page.aspx?PN=28&GI=2） | 開學、考試、放假、截止日等事件 |
| SSO 公告中心 | https://sso.nknu.edu.tw/News/SubjectList.aspx | 7 個分類的公告標題、單位、日期和摘錄 |

- **每 2 小時抓一次**（台北時間偶數整點的 17 分前後，例如 08:17、10:17），資料有變動就馬上重建答案。SSO 公告每天第一次全部掃過，其餘只看前 3 頁。
- **每晚 00:05 重建一次**，讓「今天、現在開不開」跟著日期往前走。
- **抓到就生效。** 從學校網站抓到、通過改版檢查的資料直接寫入，下一次重建就反映在回答裡（場館時段、閉館、行事曆改期都一樣），不等人工審核；維護者每輪收到變更摘要，抓錯的可以一鍵撤回。行事曆標「暫定」的事件照列，並註明「暫定，日期可能變動」。
- 只有圖片的公告（例如圖書館期中考延長開館）由維護者手動輸入，並附原公告網址。
- 抓取時對學校網站很客氣：每台主機同時只連 1 條線，每次請求間隔 2–5 秒；不繞過網站的任何保護機制。網站改版、抓取失敗時保留舊資料，不會用錯的資料蓋掉對的。

## 不知道就說不知道

每個回答都有 `status`，AI 助理被要求照著轉告，不得自己補：

| status | 意思 | 回答會怎麼說 |
|---|---|---|
| `current` | 資料在有效期內 | 直接給時段，附有效期和來源 |
| `stale` | 手上的資料已經過期，新的還沒公布（例如學期表過了） | 給最後一筆官方資料，寫明來源和有效期，並說明之後的時段尚未公布、可能已變動 |
| `conflict` | 兩個同等級的來源說法不同 | 兩種說法並列，不替你選 |
| `no_data` | 查無資料 | 明說查不到、請勿推測，並建議洽詢的單位 |

另外幾點：公告只是摘要，不是核實過的事實；發布超過一年的公告（就算學校把下架日期填到 2030 年）不算現行公告，只有在找不到任何現行資料時才會列出，並標明發布日期和「內容可能已變動」；學校放假不等於場館關閉（例如行事曆放假那天，圖書館可能延長開館）。回答一律完整給出，不為了省字數刪掉附註、來源或逐日資料。

## 隱私

- 公告標題和摘錄一律照抄學校原文；學校已經遮罩的內容保持原樣，不還原也不重複遮罩。
- 只有榮譽榜這類個人內容，學校沒遮罩的人名會用學校的格式遮成「陳O余」（保留頭尾字，中間換成 O；兩個字遮成「陳O」）。絕不編造名字。是不是榮譽內容看標題用詞（恭賀、榮獲、考取、錄取名單、榜單……），不看公告放在哪個分類；這類公告只收遮過的標題，不抓內文摘錄。
- 學校誤公開的內部內容（例如行事曆裡的職員請假、組長行程、內部會議，或「人名＋私人行程」如「林O，看牙」）在抓取階段就丟掉，不入資料庫，也不寫進 log。
- 公開倉庫裡的測試資料不用假名，人名同樣用 O 遮罩。
- 伺服器只收到 AI 助理送來的查詢參數（例如「圖書館」「2026-10-24」），看不到你和 AI 的整段對話。不需要帳號，伺服器也不記錄存取紀錄或 IP；限流用的 IP 只放在記憶體裡，最後一次請求後一、兩分鐘就忘掉。（對外網址經過的隧道服務商有它自己的紀錄，不在本服務的控制範圍內。）
- 查不到答案的問題（`nknu_search`、`nknu_get_hours` 回 `found=false` 時）會記下日期、工具和問題文字，電話、email 等個資先遮罩，不記錄是誰問的。維護者用它來補別名和資料來源，所以請不要在問題裡放個資。

## 接上你的 AI 助理

對外網址還沒公布。公布後，把下面所有的 `<對外網址>` 換成它（例如 `https://mcp.example.org`，角括號一起換掉）。網址結尾是 `/mcp`，後面不要再加斜線。

每個用戶端都直接連線，不需要安裝本機橋接程式（例如 mcp-remote），也不需要任何登入或金鑰。

### Claude 網頁版和 Claude Desktop 桌面版（自訂連接器）

兩邊用同一個 Claude 帳號，加一次就會同時出現在網頁、桌面和手機。沒有設定檔，只能在介面裡加。不要寫進 `claude_desktop_config.json`，那是給本機伺服器用的另一套機制。

**加入**

- Free / Pro / Max 方案：打開 Customize > Connectors（https://claude.ai/customize/connectors），點「+ Add」，再選「Add custom connector」。桌面版從左側欄的 Customize 進 Connectors。
- Team / Enterprise 方案：只有 Owner（Enterprise 還包括有管理 Libraries 權限的人）能加，路徑是 Organization settings > Connectors，點 Add，選 Custom，再選 Web。加好之後，成員在 Customize > Connectors 找到帶「Custom」標籤的 nknu，點 Connect。

「Add custom connector」對話框這樣填，然後點「Add」：

```text
Name:             nknu
MCP server URL:   <對外網址>/mcp
Authentication:   No sign-in（說明中心的文章寫作 "No sign in"）
OAuth client:     選了 No sign-in 就不會出現，不用填
Request headers:  留空（這是測試中的功能，可能根本不會出現）
Advanced > Transport: 不要改。網址以 /mcp 結尾就是 Streamable HTTP
```

如果你看到的是單頁版對話框（Name、URL 和 Advanced settings 在同一頁），只填 Name 和 URL，Advanced settings 裡的 OAuth Client ID / Secret 全部留空。選 No sign-in 表示任何知道網址的人都能使用，這正是本服務的設計。

**確認**

1. 回到 Customize > Connectors，nknu 應該出現在「Your connectors」下，狀態是「Connected」。如果顯示「Connect」或「Reconnect」，就是沒連上。
2. 點開 nknu，「Tool permissions」下應該有 4 個工具：`nknu_search`、`nknu_get_hours`、`nknu_get_calendar`、`nknu_get_fact`。它們都是唯讀的，可以設成「Always allow」。
3. 開一個對話，點輸入框的「+」，選「Connectors」，打開 nknu 的開關，問一句「高師大圖書館今天幾點關門？」。出現授權提示時點「Allow once」。

**限制**：Free 方案只能加 1 個自訂連接器；Team / Enterprise 的成員不能自己加，要請 Owner 加，管理員也可能關掉這個功能。加好之後不能改認證設定，要刪掉重加。

官方文件：
- https://claude.com/docs/connectors/custom/remote-mcp
- https://claude.com/docs/connectors/custom/add-unlisted
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- https://claude.com/docs/connectors/building/testing
- https://claude.com/docs/connectors/building/authentication
- https://claude.com/docs/connectors/building/index
- https://claude.com/docs/connectors/getting-started
- https://claude.com/docs/connectors/building/troubleshooting
- https://modelcontextprotocol.io/docs/develop/connect-remote-servers （這份教學寫的 Settings > Connectors 是舊路徑，以 claude.com 文件的 Customize > Connectors 為準）

### Claude Code（命令列）

如果你已經在 claude.ai 加了 nknu 連接器，用同一個帳號登入的 Claude Code 會自動帶入。要手動加，任選一種：

方式 A：一行指令（建議，所有專案都能用）

```bash
claude mcp add --transport http --scope user nknu <對外網址>/mcp
```

方式 B：專案根目錄的 `.mcp.json`（可以提交到 git，跟團隊共用）

```json
{
  "mcpServers": {
    "nknu": {
      "type": "http",
      "url": "<對外網址>/mcp"
    }
  }
}
```

方式 C：與方式 B 等價的一行指令

```bash
claude mcp add-json nknu '{"type":"http","url":"<對外網址>/mcp"}'
```

- `--scope` 決定設定寫在哪裡：`local`（預設）寫進 `~/.claude.json`，只在目前專案有效；`user` 也寫進 `~/.claude.json`，所有專案都能用；`project` 寫進專案根目錄的 `.mcp.json`，第一次使用時 Claude Code 會請你批准。
- 一定要寫 `--transport http`，不寫會被當成本機程式（stdio）。JSON 裡也一定要有 `"type": "http"`，只有 `url` 會被當成設定錯誤而跳過。不要用 `--transport sse`，官方已經不建議。
- 想讓 4 個工具一開始就完整載入，可以在 JSON 的 nknu 條目裡加 `"alwaysLoad": true`。

**確認**

1. 在終端機執行 `claude mcp list`，nknu 那一行應該顯示 `✔ Connected`。如果是 `✘ Failed to connect`，執行 `claude mcp get nknu`，看 `Issue:` 那一行的 HTTP 狀態碼和錯誤內容（較新的版本才有這一行）。
2. 進入 Claude Code 後輸入 `/mcp`，nknu 應該打勾，旁邊的工具數是 4。工具的完整名稱是 `mcp__nknu__<工具名>`。

官方文件：https://code.claude.com/docs/en/mcp

### Cursor

設定檔：
- 個人、所有專案都能用：`~/.cursor/mcp.json`
- 只用在某個專案：專案根目錄的 `.cursor/mcp.json`（可以提交到 git 給隊友用）

兩個檔案會合併；同名的伺服器以專案那份為準。Cursor 的命令列工具 `agent` 也讀同一份設定。把下面的 `nknu` 放進最外層的 `mcpServers`，存檔後重新啟動 Cursor：

```json
{
  "mcpServers": {
    "nknu": {
      "url": "<對外網址>/mcp"
    }
  }
}
```

只寫 `url` 就好。不要加 `"type"`（官方文件只有本機程式才寫 type），也不要加 `headers` 或 `auth`。

**確認**

1. 存檔並重新啟動 Cursor。
2. 在側邊欄打開 Customize，再進 MCPs，nknu 應該在清單裡，開關是打開的。
3. 在 Agent 對話面板上方的工具清單，應該看到 4 個工具：`nknu_get_calendar`、`nknu_get_fact`、`nknu_get_hours`、`nknu_search`。點工具名稱可以個別開關。
4. 沒看到的話，按 Cmd+Shift+U（Windows / Linux 是 Ctrl+Shift+U），在 Output 面板選「MCP Logs」，找初始化或連線錯誤。
5. 也可以在終端機用 Cursor CLI：`agent mcp list` 看伺服器狀態，`agent mcp list-tools nknu` 看工具和參數名稱。

企業版的管理員設了 MCP 允許清單時，不在清單裡的網址會被擋。

官方文件：
- https://cursor.com/docs/mcp
- https://cursor.com/help/customization/mcp
- https://cursor.com/docs/cli/reference/parameters
- https://cursor.com/docs/cli/reference/slash-commands
- https://cursor.com/llms.txt

### OpenAI Codex CLI

`~/.codex/config.toml` 由 Codex CLI、ChatGPT 桌面版和 Codex IDE 擴充功能共用，所以加一次三個地方都能用。也可以只對某個專案生效：寫在專案的 `.codex/config.toml`，但只有這個專案被標記為「信任」時才會讀取。

```toml
# 寫進 ~/.codex/config.toml（或受信任專案的 .codex/config.toml）
[mcp_servers.nknu]
url = "<對外網址>/mcp"

# 選填：startup_timeout_sec = 20   # 預設 10 秒
#       tool_timeout_sec = 60      # 預設 60 秒
```

也可以不手動編輯，用一行指令達到同樣效果（會寫進 `~/.codex/config.toml`）：

```bash
codex mcp add nknu --url <對外網址>/mcp
```

- 不需要 `bearer_token_env_var`、`http_headers`、`oauth` 或任何 `[features]` 旗標。網路上的舊教學要你加 `experimental_use_rmcp_client = true` 或 `[features] rmcp_client = true`，那只適用於舊版；版本很舊的話請升級 Codex，不要加這些旗標。
- 執行 `codex mcp add` 時如果印出「MCP server may or may not require login...」，可以忽略。

**確認**

1. 在終端機執行 `codex mcp list`，清單裡要有 nknu（加 `--json` 可以看機器可讀格式）；`codex mcp get nknu` 會顯示這一條的設定。
2. 執行 `codex` 進入互動介面，輸入 `/mcp`，nknu 底下應該有 4 個工具。
3. 沒看到工具的話，輸入 `/mcp verbose` 看伺服器的診斷訊息（例如連線或初始化錯誤）。

官方文件：
- https://developers.openai.com/codex/mcp （現在會轉到 learn.chatgpt.com）
- https://learn.chatgpt.com/docs/extend/mcp?surface=cli
- https://learn.chatgpt.com/docs/extend/mcp.md
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://learn.chatgpt.com/docs/developer-commands?surface=cli
- https://github.com/openai/codex/blob/main/codex-rs/cli/src/mcp_cmd.rs

### Google Gemini CLI

（依 v0.65.0 確認，2026-10-20 發布。）

把 nknu 加進 `settings.json` 最外層的 `mcpServers`：`~/.gemini/settings.json`（使用者層級，所有專案都能用），或專案根目錄的 `.gemini/settings.json`（專案層級）。是合併進現有檔案，不要把其他設定蓋掉。

```json
{
  "mcpServers": {
    "nknu": {
      "httpUrl": "<對外網址>/mcp"
    }
  }
}
```

等價的一行指令（寫進去的是 `"url"` 加 `"type": "http"`，而不是 `httpUrl`）：

```bash
gemini mcp add --scope user --transport http nknu <對外網址>/mcp
```

- 兩種寫法在 v0.65.0 都能用，但 `httpUrl` 和 `url` 不要同時設定。
- `gemini mcp add` 預設寫進專案層級，而且在不受信任的資料夾或家目錄會拒絕，所以加上 `--scope user` 最保險。

**確認**

1. 在終端機執行 `gemini mcp list`，應該看到 `✓ nknu: <對外網址>/mcp (http) - Connected`。
2. 在 gemini 對話裡執行 `/mcp`（等同 `/mcp list`），nknu 應該是 CONNECTED，Tools 那一行列出 4 個工具。`/mcp desc` 會加上工具說明，`/mcp schema` 會加上參數格式。如果是在對話開著的時候改了 settings.json，先執行 `/mcp reload`。模型看到的工具名稱是 `mcp_nknu_<工具名>`。

**注意**：官方部落格說，從 2026-06-18 起，Gemini CLI 不再提供個人免費 Gemini Code Assist，以及 Google AI Pro / Ultra 方案的使用；付費的 Gemini API 金鑰和 Code Assist Standard / Enterprise 仍然可以用。很多同學因此改用 Antigravity CLI，它的遠端伺服器設定格式不同，這裡還沒有驗證過，暫不提供寫法。

官方文件：
- https://geminicli.com/docs/tools/mcp-server/
- https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md
- https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md
- https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/commands.md
- https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/tools/mcp-client.ts
- https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/commands/mcp/add.ts
- https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli
- https://antigravity.google/docs/cli/gcli-migration

## 維護者

### 指令

開發在 Mac 上用 `uv run nknu …`；實驗室機器上直接打 `nknu …`（見下面的部署）。

| 指令 | 作用 |
|---|---|
| `nknu init` | 建立 facts.db，載入 `config/entities.yaml`（地點、別名、主管單位）；舊的 facts.db 會就地升級到目前的格式（schema 2）。可以重複執行 |
| `nknu crawl` | 禮貌抓取 5 個來源，通過改版檢查的資料直接寫進 facts.db；資料有變動就自動重建 serve.db，並送出抓取摘要。每個來源印出 `records=`（抓到幾筆）、`written=`（寫入）、`retired=`（下架）。`--only <來源>` 只跑一個，`--full` 強制全掃 |
| `nknu changes` | 列出最近的變更（新增、更新、被取代、下架、撤回），每條附撤回用的 id 或 key。`--since YYYY-MM-DD`（預設 7 天前）；公告預設只列條數，`--docs` 逐條列出 |
| `nknu undo <id 或 key>` | 下架一條現行資料（場館時段、閉館、假日、行事曆事件或公告），並記住這份內容：爬蟲之後抓到一模一樣的內容不會再寫回來，內容變了才會再寫。完成後自動重建 serve.db。`--cancel` 取消撤回 |
| `nknu alerts` | 看告警（改版偵測失敗、新地點、解析問題、詳情頁讀不到、來源衝突、備份或重建失敗）；`--all` 連已處理的一起列，`--resolve <編號>` 標為已處理 |
| `nknu add hours …` | 手動錄入一段特殊開放時間或閉館（只有圖片的公告），要附原公告網址。用同一個網址、同一個地點再錄一次（例如改正日期），會取代日期重疊的前一筆 |
| `nknu review` | 只處理舊版留下的待審項目（現在爬蟲直接寫入，不會再新增）；某一條壞掉會略過，不影響其他條 |
| `nknu build` | 由 facts.db 重建 serve.db；`--rollback` 暫時換回上一版（只能退一步，見日常維護） |
| `nknu serve` | 啟動 MCP 服務（`/mcp` 和 `/health`） |
| `nknu check` | 授權、個資、TLS、啟動檢查。個資掃描涵蓋所有 git 追蹤的文字檔（含 tests），刻意放的假樣例列在 `src/nknu/admin/checks.py` 的 `PII_SAMPLES`；推送前一定要跑。需要開發用套件，所以在 Mac 上用 `uv run nknu check` |
| `nknu backup` | 把 facts.db 線上備份成 `facts-YYYYmmdd-HHMMSS.db`，只保留最新 14 份；`--to <目錄>`，預設 `NKNU_BACKUP_DIR` |
| `nknu notify-failure <服務>` | systemd 在備份或夜間重建失敗時自動執行：寫一條告警並通知，不用手動跑 |

開發：`uv sync`，`uv run pytest -q`，`uv run ruff check src tests`。

### 部署到實驗室機器（Ubuntu 24.04）

```bash
sudo git clone https://github.com/loptio/nknu-mcp.git /opt/nknu   # 合併到 main 之前加 --branch feat/v1
sudo bash /opt/nknu/deploy/install.sh
```

`install.sh` 可以重複執行，更新程式也是跑同一個指令。它會：建立系統用戶 `nknu`；更新 `/opt/nknu`（只做 fast-forward，機器上有本地修改就停下）；`uv sync --frozen --no-dev`；建立 `/var/lib/nknu`；第一次從 `deploy/env.example` 複製 `/etc/nknu/env`；安裝並啟用 systemd 的 unit；安裝 `/usr/local/bin/nknu`。

實驗室機器上的 `nknu` 會以 `nknu` 用戶的身分、讀 `/etc/nknu/env` 來執行，和排程用的是同一份資料。不要直接執行 `/opt/nknu/.venv/bin/nknu`：沒有讀環境檔時，資料會寫到目前目錄的 `./data`。

第一次上線：

1. 編輯 `/etc/nknu/env`：`NKNU_ALLOWED_HOSTS`、`NKNU_ALLOWED_ORIGINS`、`NKNU_TRUST_CF_HEADER`（Cloudflare 填 1，Tailscale Funnel 填 0）照 [deploy/tunnel/README.md](deploy/tunnel/README.md) 設定；`NKNU_BACKUP_DIR` 最好指到另一顆磁碟或 NAS（改了要重跑 install.sh）；想收通知就填 `NKNU_DISCORD_WEBHOOK`。
2. `sudo systemctl start nknu-crawl.service`：第一次抓取（全掃），約 4 分鐘，資料直接寫入並建好 serve.db。進度用 `sudo journalctl -u nknu-crawl -f` 看。
3. `nknu changes`：看第一次寫進了什麼。場館時段和閉館日大約 87 條「新增」，另外列出行事曆事件和公告的條數。抓錯的用 `nknu undo <key>` 撤回。
4. `sudo systemctl start nknu-mcp.service`，然後 `curl -s http://127.0.0.1:8765/health` 應該看到 `"status":"ok"`。
5. `systemctl list-timers 'nknu-*'` 應該有 3 個排程：
   - `nknu-crawl.timer`：每 2 小時抓一次（台北時間 00:17、02:17……，最多隨機晚 2 分鐘）
   - `nknu-build.timer`：每晚 00:05 重建
   - `nknu-backup.timer`：每天 03:30 備份
6. 設定對外網址（Cloudflare tunnel 或 Tailscale Funnel，見 [deploy/tunnel/README.md](deploy/tunnel/README.md)），並在 UptimeRobot 或 Healthchecks.io 加 `/health` 探測。
7. 公布網址前自測：
   ```bash
   npx -y @modelcontextprotocol/inspector@2.12.0 --cli <對外網址>/mcp --method tools/list   # 應列出 4 個工具
   curl -sS -X POST <對外網址>/mcp -H 'content-type: application/json' \
     -H 'accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
   # 應回 HTTP 200，result.protocolVersion 是 2025-11-25（Cursor 用的是這個舊版握手）
   ```
   再用 Claude 的自訂連接器實際連一次，確認沒有被 Host / Origin 檢查擋下。

### 日常維護

**日常沒有例行審核。** 抓到的資料直接生效，維護者靠「變更摘要＋一鍵撤回＋告警」事後把關。

- **抓取摘要**：每輪抓取只要有寫入、下架或告警，就在 log 寫一則摘要（標籤 `CRAWL`）；有設 `NKNU_DISCORD_WEBHOOK` 時同時發到 Discord。內容是每個來源的新增／更新／被取代／下架／撤回條數、最多 10 行變更說明（可撤回的行尾附 key）、這輪的告警（包括這輪重建 serve.db 時發現的來源衝突，和未處理告警的每日提醒）。變更說明先列場館時段、閉館和行事曆，公告排在最後，10 行用完的部分只算條數。每晚 00:05 的重建不發通知（它發現的來源衝突只記在 `nknu alerts`），但重建失敗會通知。
- **看變更**：`nknu changes`（預設最近 7 天，`--since` 改起點），每條附撤回用的 id 或 key。
- **撤回**：`nknu undo <key>` 立刻下架並重建答案。爬蟲之後抓到一模一樣的內容不會再寫回來，學校改了內容才會再寫；後悔了用 `nknu undo <key> --cancel`。撤回只下架，不會把更早的版本恢復成現行。
- **告警**：`nknu alerts` 看，處理完 `nknu alerts --resolve <編號>`。列出的時間是最後一次提醒的時間：作廢、當機這類讓整個來源停住的告警，沒處理時每天提醒一次。
- **新說法取代舊說法**：寫入一條資料時，同一網址、同一地點、同一項目、日期重疊的舊資料會一起被取代（例如閉館從 10/25–10/26 改成 10/24–10/26）。學期時段表以學期第一天區分，下學期的表是另一條，不會提前蓋掉本學期。手動錄入也一樣：用同一個網址再 `nknu add hours` 一次就能改正；它取代掉的爬蟲資料會被記住，下一輪不會被寫回來，網頁內容變了才會。
- **什麼時候自動下架**：
  - 表格頁上的資料（閉館日清單、學期時段表）不會因為頁面上不見了就下架，到期自己失效；學校提早取消某個閉館日的話，要手動 `nknu undo`。
  - 公告的詳情頁回 404：公告下架，從它轉出來的閉館資料一起撤掉。
  - 其他資料（公告、行事曆事件、假日）連續 3 次「這一輪本來看得到它」的全掃都沒出現，才下架。行事曆只算抓取範圍內（今天前 60 天到後 400 天）的事件；體育室停開公告轉出來的閉館，只在那則公告的詳情頁這輪有讀到時才算。
- **改版守衛**：條數、必要的選擇器、日期解析率任一項不對，這一輪就作廢、保留舊資料，並發告警。條數比平常少、其他檢查都通過，而且連續 3 輪是同一個較低的數字時，當成新常態接受（告警 `count_new_normal`）；條數變多不會自動接受，最多 7 天內仍會作廢，等舊紀錄過了 7 天的比較範圍。
- **公告摘錄**：每輪最多補抓 30 則 SSO、15 則體育室公告的詳情頁（先抓新出現的，再補舊的）。前 3 頁以外的舊公告只在每天第一次全掃時補，全部補完大約要 17 天。單一詳情頁讀不到只會發 `detail_failed` 告警（之後讀到會自動解除），不會讓整輪作廢。
- **學校要求稍後再來**：網站回的 `Retry-After` 超過 120 秒時，這一輪就不再連那台主機（同主機的其他來源也一樣），下一輪再試。
- **備份或夜間重建失敗**：systemd 會執行 `nknu notify-failure`，寫一條 `unit_failed` 告警並通知（同一則一天最多一次）。照訊息看 `journalctl`，修好後 `nknu alerts --resolve <編號>`。
- log：`sudo journalctl -u nknu-crawl -n 100`、`sudo journalctl -u nknu-mcp -n 100`。
- `/var/lib/nknu/misses.jsonl` 是查無資料的問題，用來補 `config/entities.yaml` 的別名。改完提交、推送，再到機器上跑一次 install.sh。檔案滿 5 MB 會輪替成 `misses.jsonl.1`～`.3`，更舊的丟掉。
- **答案有問題時**：先 `nknu changes` 找到那一條，再 `nknu undo <key>`，這是長期有效的修法。`nknu build --rollback` 只是急救：只能退一步（第二次會說沒有上一版可換），而且下一次重建（每晚 00:05，或下一輪有變動的抓取）會照 facts.db 重新算回來。要讓某個來源整個先別出現，在 `/etc/nknu/env` 設 `NKNU_DISABLED_SOURCES=<來源>` 再 `nknu build`；`NKNU_READONLY=1` 只停止寫入，夜間重建仍會照現有資料算。
- 遮名規則更新後，facts.db 裡已存的公告標題要等下一次全掃（每天第一輪抓取）才會用新規則重寫，重寫時順便清掉變成榮譽內容的公告摘錄；某則要立刻下架，用 `nknu undo sso_news:<UNID>`。
- 緊急開關（在 `/etc/nknu/env`）：`NKNU_READONLY=1` 讓抓取不寫入，`nknu review`、`nknu add`、`nknu undo` 也停用；`NKNU_DISABLED_SOURCES=<來源>` 停用某個來源，接著執行 `nknu build`。
- 更新程式：重跑 `sudo bash /opt/nknu/deploy/install.sh`。它會升級 facts.db（`nknu init`）、重建 serve.db（新的回答措辭重建後才生效），並列出 `/etc/nknu/env` 缺少的新設定（沒寫的用預設值）。
- 從備份還原 facts.db：
  ```bash
  sudo systemctl stop nknu-crawl.timer nknu-build.timer nknu-backup.timer nknu-crawl.service
  sudo rm -f /var/lib/nknu/facts.db-wal /var/lib/nknu/facts.db-shm
  sudo install -o nknu -g nknu -m 0640 <備份目錄>/facts-YYYYmmdd-HHMMSS.db /var/lib/nknu/facts.db
  nknu build
  sudo systemctl start nknu-crawl.timer nknu-build.timer nknu-backup.timer
  ```
