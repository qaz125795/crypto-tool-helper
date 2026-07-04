# 刷量 Runner 上線手冊

> 目標：一週 Testnet 驗證後，開放帶單號刷量（雙邊掛單造市優先）。

## 架構

- **模組**：`backend/volume_runner/`（gate-quant 容器內）
- **策略**：`mm` 雙邊掛單造市（買一 Maker 買入 → 掛賣平倉賺價差）
- **狀態**：`/app/data/volume_runner/runner_state.json`
- **API**（需 admin token）：
  - `GET /volume/status`
  - `POST /volume/start` | `/volume/stop` | `/volume/tick` | `/volume/unpause`

## 一週時程（建議）

| 天 | 動作 | 環境變數 |
|----|------|----------|
| D1–D3 | Testnet dry-run，確認 tick 正常、日報收到 | `ENABLED=1`, `DRY_RUN=1`, `GATE_BASE_URL=https://api-testnet.gateapi.io/api/v4` |
| D4–D5 | Testnet 實單小額（100U 保證金） | `DRY_RUN=0`，觀察 `volume_usdt` / `net_pnl_usdt` |
| D6 | 調 spread / 槓桿，確認淨利 ≥ 0 或打平 | 依日報微調 `SPREAD_BPS` |
| D7 | 開帶單號（主網小資金） | `ALLOW_MAINNET=1`, `GATE_BASE_URL` 改主網 |

## 啟用步驟

1. 在 Gate **Testnet** 建立 API Key，填入 gate-quant 帳號 slot（預設 slot 1）。
2. `.env` 設定：

```env
GATE_BASE_URL=https://api-testnet.gateapi.io/api/v4
VOLUME_RUNNER_ENABLED=1
VOLUME_RUNNER_DRY_RUN=1
VOLUME_RUNNER_ALLOW_MAINNET=0
VOLUME_RUNNER_ACCOUNT_SLOT=1
```

3. 重建並重啟 gate-quant：

```bash
docker compose build gate-quant && docker compose up -d gate-quant
```

4. 檢查狀態：

```bash
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" http://127.0.0.1:8001/volume/status | jq
```

5. TG 通知 bot 應收到「刷量 Runner 已啟動」。

## Cron 備援（可選）

若不想用背景 task，可每分鐘打一次 tick：

```bash
*/1 * * * * curl -s -X POST -H "X-Admin-Token: YOUR_TOKEN" http://127.0.0.1:8001/volume/tick >/dev/null
```

## 帶單開放條件（全部滿足才開）

- [ ] Testnet 連續 3 天 `net_pnl_usdt >= -5`（打平或小賺）
- [ ] 日成交量達預期（例如 > 500万 USDT/日，依本金調整）
- [ ] 成交率 > 60%（fills / (fills+cancels)）
- [ ] 無連續 API 錯誤 / 無主網誤觸（`ALLOW_MAINNET=0` 時主網會自動暫停）

## 風控

- `VOLUME_RUNNER_MAX_DAILY_LOSS_USDT`：日虧達上限自動暫停
- 非 testnet 且未設 `ALLOW_MAINNET=1`：**自動阻擋下單**
- 掛單逾時 `ORDER_TIMEOUT_S` 自動撤單

## 後續策略（待實作）

- `funding` 資費對沖
- `grid` 網格刷量
- `trendmaker` 趨勢掛單跟量

目前僅 `mm` 已接實盤引擎；其餘在 `volume.html` 試算器可先給代理看帳。
