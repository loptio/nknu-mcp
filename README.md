# 高師大校園資訊 MCP（nknu-facts）

把國立高雄師範大學（高師大）公開網站上的資訊整理好，透過 MCP 免登入開放給任何 AI 助理。學生用自己的 AI 問學校的事，就能拿到學校原文，每一筆都附來源網址和日期。

MCP（Model Context Protocol）是 AI 助理呼叫外部工具的共通規格。接上之後，你問 Claude、ChatGPT、Cursor 或 Gemini「畢業門檻在哪裡看？」「宿舍什麼時候開放進住？」，它會來這裡搜尋學校的網頁和附件，讀原文再回答你。

- 這是學生維護的服務，**不是學校官方系統**。重要的事請以學校公告為準。
- 回答時不呼叫會產生文字的 AI 模型。伺服器只用兩個打分模型（[bge-m3](https://huggingface.co/BAAI/bge-m3) 找相近的內容、[bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) 排先後），回傳的是學校原文、程式的固定句子和程式算出的值，不會替學校編內容。
- 只能查詢，不能修改任何資料。
- 程式碼採 Apache-2.0 授權。

## 現在的狀態（2026-11-22）

- 收錄 146 個網站：107 個學校網站、5 個要用瀏覽器才看得到內容的網站、30 個學校單位放在校外的網站（Google 協作平台、Wix 等），以及 4 個只留存檔的舊網站。
- 約 8.9 萬份文件（網頁和附件），每晚自動更新一次。
- 服務在實驗室的電腦上執行，**還沒有對外網址**，目前外面連不到。有了網址會寫在這裡。

## 兩個工具

| 工具 | 做什麼 | 參數 |
|---|---|---|
| `search` | 搜尋高師大資料，回傳最相關的幾份文件和原文段落 | `query`：關鍵字或問句（繁體中文、台灣用語）<br>`detail`：每筆給多少原文，`passage`（預設，約 500 字）／`snippet`（約 200 字）／`full`（前 5 筆給全文或命中的章節）<br>`limit`：1～50，預設 10<br>`unit`：只看某個單位，全稱或簡稱都可以，填系所時也包含全校適用的文件<br>`sort`：`relevance`（預設，最相關的在前）／`date`（相關的結果裡由新到舊） |
| `fetch` | 讀一份文件 | `id`：`search` 回傳的 id，也可以填收錄的網頁或檔案網址<br>`part`：留空讀全文（太長給目錄和開頭）、填節號讀某一節、填 `toc` 只看目錄 |

兩個工具都免登入、唯讀。

同一個服務也有網頁和 JSON 介面，給沒有 MCP 的人和程式用：

| 路徑 | 是什麼 |
|---|---|
| `/mcp` | MCP |
| `/` | 說明頁，有搜尋框和各家 AI 的接法 |
| `/search?q=…` | 搜尋結果頁（可加 `unit=`、`sort=date`、`limit=`） |
| `/doc/<編號>` | 一份文件的頁面 |
| `/api/search`、`/api/fetch` | 和 MCP 一樣的結果，回 JSON |
| `/llms.txt` | 給 AI 讀的說明 |
| `/health` | 服務還在不在、資料庫是什麼時候建的 |

## 接上你的 AI 助理

對外網址還沒公布。公布後，把下面的 `<對外網址>` 換成它（角括號一起換掉）。網址結尾是 `/mcp`。不需要登入、金鑰或本機橋接程式。

- **Claude 網頁版和桌面版**：Customize > Connectors，點「+ Add」，選「Add custom connector」，網址填 `<對外網址>/mcp`。
- **Claude Code**：

  ```bash
  claude mcp add --transport http --scope user nknu <對外網址>/mcp
  ```

- **Cursor**：在 `~/.cursor/mcp.json` 最外層的 `mcpServers` 加上：

  ```json
  { "mcpServers": { "nknu": { "url": "<對外網址>/mcp" } } }
  ```

- **OpenAI Codex CLI、ChatGPT 桌面版**（共用 `~/.codex/config.toml`）：

  ```bash
  codex mcp add nknu --url <對外網址>/mcp
  ```

- **Google Gemini CLI**：

  ```bash
  gemini mcp add --scope user --transport http nknu <對外網址>/mcp
  ```

## 資料怎麼來

每晚 01:00 在實驗室的電腦上自動跑完四步：

1. **抓取**：各網站的首頁、公告列表、最近的公告和新附件；要用瀏覽器的網站用瀏覽器開。原樣存檔，抓過的每一版都留著。
2. **轉文字**：網頁和附件（PDF、Word、Excel、簡報、圖片裡的字）轉成文字。
3. **建資料庫**：分出文件的標題、日期、發布單位、適用對象，切成段落，算好向量。換上新資料庫之前，逐份核對舊資料庫有、新資料庫沒有的文件，說不出原因的太多就不換。
4. **對外服務**直接讀新的資料庫，不用重新啟動。

抓取的規矩：每台伺服器同時只連一條線，每次回應後等 1～2 秒，遵守 robots.txt，不繞過登入或任何保護。抓取失敗或網站改版時保留上一次好的資料，不拿壞的蓋掉好的。

沒有收的：要登入的網頁、學校網站連出去的校外網站（只記網址）、老師著作的論文全文、給中小學老師的教材下載區、超過上限的活動照片。

## 隱私

- 不需要登入，不記 IP，也不記是誰在查。
- 查詢的內容會記下來，用來改進搜尋；查詢裡像電話、學號、信箱的字會先遮掉。
- 每個用戶每分鐘最多 60 次查詢。

## 程式在哪

| 資料夾 | 內容 |
|---|---|
| `src/nknu/archive/` | 爬蟲：抓取、存檔、每晚更新、匯出 |
| `src/nknu/totext/` | 網頁和附件轉文字 |
| `src/nknu/content/` | 建資料庫：文件、日期、單位、段落、向量 |
| `src/nknu/retrieval/` | 搜尋、MCP、網頁和 JSON 介面（`public.py` 是對外服務） |
| `src/nknu/ops/` | 維護用的檢視台（每晚跑得怎樣、每個網站的情況、一份文件從抓取到搜尋的經過） |
| `src/nknu/core/` | 共用的部分（模型、個資遮蔽等） |
| `config/` | 單位表、別名、網站和單位的對應 |
| `deploy/` | 實驗室電腦上的安裝和每晚排程，各資料夾的 `README.md` 是維護說明（簡體中文） |
| `tests/` | 測試 |

第一版（只有場館開放時間、教學行事曆和公告的 4 個工具）的程式還在 `src/nknu/crawl/`、`build/`、`server/`、`admin/`，也在 `feat/v1` 分支。

## 自己架一份

資料庫不在 git 裡：它很大，而且是從學校網站抓下來的。要自己架，得先跑抓取和建資料庫（見 `deploy/lab-crawler/` 和 `deploy/lab-pipeline/`）。有了資料庫以後：

```bash
uv sync
uv run python -m nknu.retrieval.public --db <資料庫路徑> --port 8770
```

第一次執行會從 Hugging Face 下載兩個打分模型（合計幾 GB）。預設只聽本機 `127.0.0.1`。

跑測試：

```bash
uv run pytest
```
