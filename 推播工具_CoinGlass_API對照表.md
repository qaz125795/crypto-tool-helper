# 推播工具 × CoinGlass API 完整對照表（初創版 80 次/分鐘）

依 [CoinGlass 定價](https://www.coinglass.com/zh/pricing)：初創版 **80+ 端點**、**80 次/分鐘**。

---

## 一、各推播工具使用的 CoinGlass 端點與單次呼叫量

| 推播工具 | CoinGlass 端點 | 單次約略呼叫數 | 初創版 80/min 對策 |
|----------|----------------|----------------|---------------------|
| **主流板塊排行榜** | 無（僅 CoinGecko） | 0 | 無需變更 |
| **購買力監控** | stableCoin-marketCap-history、open-interest/aggregated-stablecoin-history | 2 | 已限速 |
| **巨鯨/多空比**（舊 whale_position） | global-long-short-account-ratio、top-long-short-account-ratio、top-long-short-position-ratio（×3 幣種） | 9 | 已限速 |
| **持倉變化篩選** | supported-exchange-pairs、coins-price-change、open-interest/history（×567） | 2 + 567 | 已限速；設 Variable=80 時單輪約 7–8 分鐘 |
| **重要經濟數據** | economic-data、financial-events、central-bank-activities | 3 | 已限速 |
| **經濟數據預告** | 同上 | 3 | 已限速 |
| **新聞快訊** | article/list、newsflash/list（Tree of Alpha 另計） | 2 | 已限速；已補 CG_API_KEY 與 Variable |
| **資金費率排行榜** | funding-rate/exchange-list | 1 | 已限速 |
| **長線牛熊導航儀** | ahr999、bitcoin/rainbow-chart、pi-cycle-indicator、fear-greed-history | 4 | 已限速（經 _coinglass_get） |
| **流動性獵取雷達** | liquidation/aggregated-history（×3 幣種） | 3 | 已限速 |
| **山寨爆發雷達** | altcoin-season、futures/rsi/list、orderbook/aggregated-ask-bids-history（×N）、open-interest/history、aggregated-cvd/history | 2 + 多筆 | 已限速（經 _coinglass_simple_get 與各 fetch） |
| **Hyperliquid 聰明錢** | hyperliquid/whale-alert、wallet/pnl-distribution、whale-position | 3 | 已限速 |

---

## 二、降版後必做設定（初創版）

1. **GitHub 倉庫 Variable**  
   **Settings** → **Secrets and variables** → **Actions** → **Variables** → 新增：  
   名稱 `COINGLASS_RATE_LIMIT_PER_MINUTE`，值 `80`。

2. **程式與 Workflow 已配合**  
   - 所有 CoinGlass 請求前都會呼叫 `_coinglass_rate_limit_wait()`，整體不超過 80 次/分鐘。  
   - 主程式啟動時若 `COINGLASS_RATE_LIMIT_PER_MINUTE > 0` 會建立限速器，供全專案使用。  
   - 各 workflow（持倉變化、經濟數據、購買力、Hyperliquid、流動性、資金費率、長線、新聞、山寨爆發、經濟數據預告）的 run step 皆已帶入 `COINGLASS_RATE_LIMIT_PER_MINUTE: ${{ vars.COINGLASS_RATE_LIMIT_PER_MINUTE }}`。

3. **無需刪除任何推播**  
   在「設 Variable = 80」的前提下，上述工具皆可於初創版運行；若某端點回傳 403/404 或資料為空，可能為 90+ 端點（僅標準版），再考慮停用該功能即可。

---

## 三、端點數量差異（80+ vs 90+）

初創版 **80+**、標準版 **90+**，約 10 個端點可能僅標準版提供。  
若降版後某功能出現「端點不存在」或權限錯誤，可對照日誌中的 API 路徑，必要時停用該 workflow。

---

## 四、總結

- **限速**：全專案 CoinGlass 請求已納入 80 次/分鐘限速，無需關閉任何推播。  
- **設定**：在 GitHub 設 `COINGLASS_RATE_LIMIT_PER_MINUTE` = `80` 即可。  
- **持倉變化**：單輪約 7–8 分鐘、全幣種覆蓋，策略不變。  
- **其餘工具**：單次呼叫數均不高，與限速相容。
