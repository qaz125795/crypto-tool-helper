# 本機自架部署包

此資料夾內為「把 JackBot 整包搬到另一台電腦、在本機依排程運行」的說明與範例腳本。  
**程式本體仍在上一層目錄**（`jackbot.py`、`requirements.txt`、`gold_signal_bot/` 等），請連同整個 repo 一起複製或壓縮後帶走。

---

## 1. 要打什麼包帶走？

在「加密貨幣推播工具」專案根目錄，建議帶走的內容：

| 項目 | 建議 |
|------|------|
| 程式碼 | 整個資料夾（含 `.github`、`gold_signal_bot`、`liquidations_chart` 等） |
| `requirements.txt`、`runtime.txt` | 必填 |
| `data/` | 可選：若希望延續冷卻／狀態就一併帶走；若要全新開始可不帶，執行時會自動重建 |
| `venv/` 或 `.venv/` | **不要帶**：到新机用 Python 重建虛擬環境較乾淨 |
| `.git/` | 可選：不需要版控可不帶，縮小包體積 |

**務必勿把真實金鑰寫進壓縮檔並傳輸給不信任的人。** 環境變數請在目標電腦用 `.env` 或系統環境變數單獨設定。

Windows 可利用同目錄下的 `打包帶走.ps1` 產生 zip（會排除 `venv`、`.git`、`data`、`__pycache__`，可依需求調整）。

---

## 2. 目標電腦環境（通用）

1. 安裝 **Python 3.11**（與 Actions 版本一致）。
2. 進入專案根目錄：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
pip install yfinance
```

Linux / macOS：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt && pip install yfinance
```

3. 在專案根目錄建立 `.env`，內容參考 `env-keys-對照.example`（將各變數改成你的 Token／Chat ID／Thread）。  
若你使用可直接 `export` 的 shell，`linux/load-env-run.sh` 會嘗試載入 `.env`。

4. **手動測一次**（確認能連 TG／API）：

```bash
python jackbot.py position_change
python jackbot.py hyperliquid
python jackbot.py crit_radar
```

---

## 3. 你目前在 Cron 的觸發間隔（已寫入範例）

| 功能 | CLI 指令 | 間隔 |
|------|----------|------|
| 持倉狙擊鏡 | `python jackbot.py position_change` | **每 30 分鐘** |
| 鏈上巨鯨動向（大佬錢包 / Hyperliquid 管線） | `python jackbot.py hyperliquid` | **每 30 分鐘** |
| 爆擊雷達 | `python jackbot.py crit_radar` | **每 15 分鐘** |

詳細 cron 規則見 `linux/crontab-範例.txt`。  
若要避免三個流程同一秒撞到 API，可將其中一組錯開 2～5 分鐘（範例內有加註）。

---

## 4. Linux：使用 crontab

1. 編輯 `linux/jackbot-cron.sh`，把 **`PROJECT_ROOT` 與 `VENV_PYTHON`** 改成你的實際路徑。
2. `chmod +x linux/jackbot-cron.sh`
3. `crontab -e`，貼上 `linux/crontab-範例.txt` 中的行（並依你的使用者與路徑修改）。

Cron 環境預設沒有完整 PATH，請**務必使用腳本內絕對路徑**呼叫 Python。

---

## 5. Windows：工作排程器

見 `windows\工作排程器-建立說明.md`（以「每 X 分鐘重複」設定 30／15 分鐘）。

---

## 6. GitHub Actions / Gist（若仍要與雲端同步）

本機跑時若要與過去設定的 **Gist 冷卻** 相容，請在 `.env` 保留 `GIST_ID`、`GITHUB_TOKEN` 等（與 Zeabur／Actions 相同邏輯）。若完全不使用雲端，可留空或不設，程式會以本機 `data/` 為主。

若有問題可先查看專案內 `data/jackbot.log`。
