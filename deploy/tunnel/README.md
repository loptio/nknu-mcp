# 對外網址（隧道）設定

MCP 伺服器只聽實驗室機器本機的 `127.0.0.1:8765`，外面連不到。「隧道」是在同一台機器上執行的小程式，它主動連到 Cloudflare 或 Tailscale，把外面打到固定網址的請求轉回本機。這樣不用開防火牆，也不用公網 IP。

v1 還沒決定用哪一種，兩種都寫在下面。**網址公開之後就不能再改**，否則所有人設定好的連接器都會失效。

## 兩種方案都要做的事

1. 客戶端填的網址是 `https://<主機名>/mcp`。結尾不要加斜線：`/mcp/` 會被轉址（307），有些客戶端不接受轉址。
2. 主機名要有公網 IPv4 的 A 記錄。Claude 的自訂連接器從 Anthropic 雲端（160.79.104.0/21）連過來，只走 IPv4。用 `dig +short A <主機名>` 檢查，要看到公網 IPv4。
3. 編輯 `/etc/nknu/env`：
   - `NKNU_ALLOWED_HOSTS=127.0.0.1,localhost,<主機名>`：只寫主機名，不含 `https://` 和埠號。沒加的話，外部請求一律回 `421 Misdirected Request`。
   - `NKNU_ALLOWED_ORIGINS=https://<主機名>`（建議）：伺服器在隧道後面看到的是 http，所以帶著 `Origin: https://<主機名>` 的瀏覽器請求，沒設定這一項會被當成跨來源而回 403。沒帶 Origin 的請求（多數客戶端）不受影響。之後如果有客戶端回報 `403 Forbidden Origin`，把它送的 Origin 也加進來（逗號分隔）。
   - `NKNU_TRUST_CF_HEADER`：方案 A（Cloudflare）填 `1`（預設），方案 B（Tailscale Funnel）一定要填 `0`。理由見各方案的「限流」。
   - 改完執行 `sudo systemctl restart nknu-mcp`。
4. 驗證：
   ```bash
   curl -s http://127.0.0.1:8765/health                                              # 本機：應該看到 "status":"ok"
   curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example' http://127.0.0.1:8765/health   # 本機：421
   curl -s https://<主機名>/health                                                   # 外部（用手機網路或別台電腦）：200
   curl -s -o /dev/null -w '%{http_code}\n' https://<主機名>/.well-known/oauth-protected-resource  # 404
   ```
   最後一行要是 404：Codex 加入伺服器時會探這類路徑；而且免登入的伺服器不能回 401，否則 Claude Code、Gemini CLI 會以為要登入。
5. 外部探測：在 UptimeRobot 或 Healthchecks.io 加一個每 5 分鐘打 `https://<主機名>/health` 的探測（路徑是 `/health`，不是 `/healthz`）。回 503 表示 serve.db 超過 36 小時沒重建，或讀不到。

## 方案 A：社團網域 + Cloudflare named tunnel（設計文件建議）

需要一個放在 Cloudflare 的網域（免費方案即可），網址例如 `https://mcp.<社團網域>`。cloudflared 的安裝方式請照 Cloudflare 官方文件裡 Linux 套件的安裝說明。

```bash
cloudflared tunnel login                                  # 瀏覽器登入 Cloudflare，選社團網域
cloudflared tunnel create nknu-mcp                        # 印出 tunnel UUID，憑證寫到 ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns nknu-mcp mcp.<社團網域>       # 建立指向這條 tunnel 的 DNS 記錄
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<UUID>.json /etc/cloudflared/
sudo cp /opt/nknu/deploy/tunnel/cloudflared-config.example.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml                     # 換掉 REPLACE-WITH-TUNNEL-UUID（兩處）和 mcp.example.org
cloudflared tunnel ingress validate --config /etc/cloudflared/config.yml
sudo cloudflared --config /etc/cloudflared/config.yml service install   # 裝成 cloudflared.service，開機自動啟動
systemctl status cloudflared
```

設定檔範本是 [cloudflared-config.example.yml](cloudflared-config.example.yml)，把 `mcp.<社團網域>` 轉到 `http://127.0.0.1:8765`。接著照上面的共同步驟，把 `mcp.<社團網域>` 加進 `NKNU_ALLOWED_HOSTS`，把 `https://mcp.<社團網域>` 填進 `NKNU_ALLOWED_ORIGINS`。

注意：
- `<UUID>.json` 等於這條 tunnel 的密碼，只放在 `/etc/cloudflared/`，不要提交到 git。
- 這個主機名不要開 Bot Fight Mode、Under Attack 模式或任何 JavaScript 驗證。MCP 客戶端不是瀏覽器，過不了驗證，Claude 的連接器多半只會顯示「Couldn't reach the MCP server」。也不要加轉址規則（例如裸網域轉到 www）。
- 限流（`NKNU_TRUST_CF_HEADER=1`，預設）：Cloudflare 會自己設定 `CF-Connecting-IP`（真實的客戶端 IP），伺服器只在連線來自本機（也就是 cloudflared）時採用它。所以「每個 IP 每分鐘 60 次、Anthropic 網段合計每分鐘 600 次」照設計運作。IPv6 用戶以整個 /64 網段算一個客戶端（同一個網段裡換位址也是同一份額度）。

## 方案 B：Tailscale Funnel（零成本）

網址形如 `https://<機器名>.<tailnet 名稱>.ts.net`。只要機器名和 tailnet 名稱不改，網址就固定。需要 Tailscale 帳號、機器上裝好 tailscale 並登入，並在 tailnet 管理後台開啟 HTTPS 憑證和 Funnel。官方說明：https://tailscale.com/kb/1223/funnel

```bash
sudo tailscale funnel --bg 8765    # 把 https://<機器名>.<tailnet 名稱>.ts.net 轉到 http://127.0.0.1:8765，重開機後仍有效
tailscale funnel status
```

接著照共同步驟，把 `<機器名>.<tailnet 名稱>.ts.net` 加進 `NKNU_ALLOWED_HOSTS`，把 `https://<機器名>.<tailnet 名稱>.ts.net` 填進 `NKNU_ALLOWED_ORIGINS`，並且設 **`NKNU_TRUST_CF_HEADER=0`**，再 `sudo systemctl restart nknu-mcp`。

注意（選這個方案前要知道）：
- **拿不到真實 IP。** Funnel 的流量由本機的 tailscaled 轉進來，伺服器看到的對端全是 127.0.0.1，分不出誰是誰。
- **一定要設 `NKNU_TRUST_CF_HEADER=0`。** 對端是本機時，伺服器預設會採用 `CF-Connecting-IP` 標頭（給 Cloudflare 用的）。Tailscale 不會設定這個標頭，所以 Funnel 下這個值都是客戶端自己填的：沒設成 0 的話，任何人換一個值就換到一份新的額度，甚至能冒用 Anthropic 網段的共用額度。
- **設成 0 之後的限流**：伺服器不看這個標頭，所有來自本機的請求（也就是全校加上 Claude）合用一份每分鐘 600 次的額度，超過的請求回 429，一分鐘內恢復。有人刻意刷，大家會一起被擋一下；這是 Funnel 拿不到真實 IP 的代價，換成方案 A 才能每人分開算。
- 設計文件提到 Funnel 有靜默停擺的已知問題，所以第 5 步的外部探測一定要設。

## 之後換網址

改用學校網址時，新舊網址並行一段時間：`NKNU_ALLOWED_HOSTS` 和 `NKNU_ALLOWED_ORIGINS` 同時列兩個主機名，並通知大家更新連接器設定。
