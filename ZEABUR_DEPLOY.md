# Zeabur 部署指南

本指南將幫助您將區塊鏈船長—傑克推播系統部署到 Zeabur 平台。

## 前置準備

1. 註冊 Zeabur 帳號：https://zeabur.com
2. 準備 GitHub 帳號（用於存放代碼）
3. 準備您的 API 密鑰和配置

## 部署步驟

### 1. 將代碼推送到 GitHub

```bash
# 初始化 git 倉庫（如果還沒有）
git init

# 添加所有文件
git add .

# 提交
git commit -m "Initial commit: JackBot deployment ready"

# 連接到 GitHub 遠程倉庫
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git

# 推送到 GitHub
git push -u origin main
```

### 2. 在 Zeabur 上創建專案

1. 登入 Zeabur
2. 點擊「Create Project」或「新增專案」
3. 選擇「Deploy from GitHub」
4. 選擇您的 GitHub 倉庫
5. 選擇要部署的分支（通常是 `main` 或 `master`）

### 3. 配置環境變量

在 Zeabur 專案的「Environment Variables」或「環境變量」中設置以下變量：

#### 必需的環境變量：

```bash
# CoinGecko API
CG_GECKO_API_KEY=CG-RR9dam92RCAGpdV5VF7km59o

# CoinGlass API
CG_API_KEY=4a2fd6ee6d2e49b091d81f1cfdf6315c

# Tree of Alpha API
TREE_API_KEY=131c5449bc84d0b1f9cb17f399c62c21f9f4c06a70d0911e76cfbfa8cdbc070d

# Telegram Bot
TG_TOKEN=8522999860:AAEIxFmxNWMCMZSzGJPwHF3JZaIDLbUs2BE
CHAT_ID=-1003611242392
```

#### 可選的環境變量（Thread IDs）：

如果不設置，會使用預設值。

```bash
TG_THREAD_SECTOR_RANKING=5
TG_THREAD_WHALE_POSITION=246
TG_THREAD_POSITION_CHANGE=250
TG_THREAD_ECONOMIC_DATA=13
TG_THREAD_NEWS=7
TG_THREAD_FUNDING_RATE=244
```

或者使用 JSON 格式一次性設置：

```bash
TG_THREAD_IDS={"sector_ranking":5,"whale_position":246,"position_change":250,"economic_data":13,"news":7,"funding_rate":244}
```

### 4. 設置定時任務（Cron Jobs）

Zeabur 支持通過 HTTP 請求觸發定時任務。您可以使用以下方式：

#### 方式一：使用 Zeabur Cron Jobs（如果可用）

在 Zeabur 的 Cron Jobs 設置中添加：

```bash
# 每小時執行主流板塊排行榜
0 * * * * curl https://YOUR_APP.zeabur.app/sector_ranking

# 每小時執行巨鯨持倉監控
5 * * * * curl https://YOUR_APP.zeabur.app/whale_position

# 每小時執行持倉變化篩選
10 * * * * curl https://YOUR_APP.zeabur.app/position_change

# 每10分鐘執行經濟數據推播
*/10 * * * * curl https://YOUR_APP.zeabur.app/economic_data

# 每分鐘執行新聞快訊推播
* * * * * curl https://YOUR_APP.zeabur.app/news

# 每天 0、4、8、12、16、20 點執行資金費率排行榜
0 0,4,8,12,16,20 * * * curl https://YOUR_APP.zeabur.app/funding_rate
```

#### 方式二：使用外部 Cron 服務

可以使用以下服務來定時觸發 HTTP 請求：

- **cron-job.org**: https://cron-job.org
- **EasyCron**: https://www.easycron.com
- **UptimeRobot** (監控模式): https://uptimerobot.com

設置示例（cron-job.org）：
1. 註冊帳號
2. 創建新的 cron job
3. 設置 URL: `https://YOUR_APP.zeabur.app/sector_ranking`
4. 設置執行頻率（例如：每小時）
5. 選擇 HTTP Method: GET 或 POST

#### 方式三：使用 GitHub Actions

創建 `.github/workflows/cron.yml`：

```yaml
name: Scheduled Jobs

on:
  schedule:
    - cron: '0 * * * *'  # 每小時執行主流板塊排行榜
  workflow_dispatch:

jobs:
  run-tasks:
    runs-on: ubuntu-latest
    steps:
      - name: Call Sector Ranking
        run: |
          curl -X POST https://YOUR_APP.zeabur.app/sector_ranking
```

### 5. 驗證部署

部署完成後，訪問以下端點進行測試：

- 健康檢查: `https://YOUR_APP.zeabur.app/`
- 測試執行任務: `https://YOUR_APP.zeabur.app/sector_ranking`

## API 端點說明

### 健康檢查
```
GET /
```
返回系統狀態和可用端點列表

### 執行任務
```
GET/POST /sector_ranking      # 主流板塊排行榜推播
GET/POST /whale_position      # 巨鯨持倉動向
GET/POST /position_change     # 持倉變化篩選
GET/POST /economic_data       # 重要經濟數據推播
GET/POST /news                # 新聞快訊推播
GET/POST /funding_rate        # 資金費率排行榜
GET/POST /run/<task>          # 通用任務執行端點
```

## 數據持久化

由於 Zeabur 的容器是無狀態的，數據存儲在 `data/` 目錄中的 JSON 文件會在容器重啟後丟失。

### 解決方案：

1. **使用 Zeabur Database** (推薦)
   - 在 Zeabur 中添加 Database 服務（PostgreSQL 或 MongoDB）
   - 修改代碼將數據存儲到數據庫而非文件

2. **使用外部存儲**
   - 使用 Redis、Supabase 等外部服務
   - 修改代碼支持外部存儲

3. **接受無狀態**
   - 如果數據丟失不影響功能，可以接受（例如已推送記錄會在下次運行時重新獲取）

## 監控和日誌

- Zeabur 會在控制台顯示應用日誌
- 可以設置監控告警
- 建議使用 UptimeRobot 等服務監控端點健康狀態

## 故障排除

### 常見問題：

1. **構建失敗**
   - 檢查 `requirements.txt` 是否正確
   - 確認 Python 版本兼容性

2. **環境變量未生效**
   - 確認在 Zeabur 中正確設置環境變量
   - 重新部署應用

3. **任務執行失敗**
   - 檢查應用日誌
   - 確認 API 密鑰有效
   - 檢查網絡連接

4. **數據丟失**
   - 使用數據庫替代文件存儲
   - 或接受無狀態部署

## 安全建議

1. **不要在代碼中硬編碼 API 密鑰**
   - 使用環境變量存儲敏感信息
   - 不要將 `.env` 文件提交到 Git

2. **限制端點訪問**
   - 考慮添加身份驗證
   - 使用 IP 白名單（如果 Zeabur 支持）

3. **定期更新依賴**
   - 定期檢查並更新 `requirements.txt` 中的套件
   - 修復安全漏洞

## 成本優化

1. **合理設置執行頻率**
   - 避免過於頻繁的 API 調用
   - 根據實際需求調整定時任務頻率

2. **使用免費額度**
   - Zeabur 提供免費額度
   - 合理規劃資源使用

## 支援

如有問題，請查看：
- Zeabur 文檔: https://zeabur.com/docs
- 項目 README.md

