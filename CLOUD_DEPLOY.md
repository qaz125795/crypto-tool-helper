# 雲端執行：撿屍雷達（GitHub Actions + Zeabur）

本專案**不需本機常駐**。推播與附圖由 **GitHub Actions** 定時執行 `jackbot.py liquidity_radar`；**Zeabur** 部署同一套程式碼，提供 HTTP 健康檢查與**手動／外部 Cron 觸發**備援。

## 你需要上傳到 GitHub 的內容

將**整個倉庫**推上 GitHub（至少包含）：

| 路徑 | 說明 |
|------|------|
| `jackbot.py` | 主程式（含撿屍雷達與 Telegram 推播） |
| `liquidations_chart/` | BTC 清算柱狀圖（Binance 公開資料） |
| `requirements.txt` | 含 `matplotlib`、`tqdm` 等 |
| `app.py` | Zeabur／Flask 入口 |
| `zeabur.json` | Zeabur 建置與啟動指令 |
| `.github/workflows/liquidity-radar.yml` | 撿屍雷達定時 workflow |

> 不需要把 `data/` 裡的執行快取、`.env`、金鑰檔推上公開庫；金鑰只放在 **GitHub Secrets** 與 **Zeabur 環境變數**。

## GitHub Repository Secrets（Actions 用）

在 repo → **Settings → Secrets and variables → Actions** 新增：

- **`CG_API_KEY`** — CoinGlass API Key  
- **`TG_TOKEN`** — Telegram Bot Token  
- **`CHAT_ID`** — 群組或頻道 ID  
- **`TG_THREAD_IDS`**（JSON）或分開設 **`TG_THREAD_LIQUIDITY_RADAR`** — 撿屍雷達話題 thread id  

設定完成後，**Actions** 會依 `liquidity-radar.yml` 的 cron 執行；首次若需下載 Binance 清算快照，單次可能較久（workflow 已 `timeout 600`）。

### 避免重複推播

- **建議擇一**：以 **GitHub Actions 定時**為主，**不要**再對 Zeabur 設同一頻率的 Cron 去呼叫 `/liquidity_radar`，否則同一小時可能推兩次。  
- 若 Zeabur 僅作備援：僅在需要時手動 `POST`，或設較長間隔／與 Actions 錯開。

## Zeabur 設定

1. 連結 GitHub 倉庫，使用根目錄 `zeabur.json` 建置。  
2. 在 Zeabur 專案 **Environment Variables** 設定與本機相同之：`CG_API_KEY`、`TG_TOKEN`、`CHAT_ID`、`TG_THREAD_*` 等。  
3. **建議**設定 **`CRON_SECRET`**（自訂一段隨機字串）：設定後，除 `/` 健康檢查外，所有任務路由需帶驗證，例如：

```http
GET https://你的服務.zeabur.app/liquidity_radar?token=你的CRON_SECRET
```

或：

```http
POST https://你的服務.zeabur.app/liquidity_radar
Authorization: Bearer 你的CRON_SECRET
```

未設定 `CRON_SECRET` 時，行為與舊版相同（端點公開，**不建議在公開網址長期使用**）。

## 撿屍雷達內容說明（非跟單）

- 文案為 **多空結構參考**，含 **非投資建議、非跟單** 聲明。  
- 成功時會先發文字（含 CoinGlass 數據脈絡），再發 **BTC 清算柱狀圖**（Binance 歷史快照，非即時熱力圖）。  
- 關閉附圖可設環境變數：`LIQ_CHART_DISABLED=1`。

## 驗證是否成功

1. GitHub → **Actions** →「流動性獵取雷達」→ 手動 **Run workflow** 測一次。  
2. Telegram 對應話題應收到訊息；若條件未觸發（未達爆倉門檻），可能無推播，屬正常。  
3. Zeabur 日誌應可看到 gunicorn 啟動；瀏覽器開根路徑 `/` 應回傳 `status: ok`。
