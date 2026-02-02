# GitHub Actions「全部掛掉」— Runner 與伺服器錯誤排查

## 你看到的錯誤說明

當 **Annotations** 出現以下兩種錯誤時，代表問題出在 **GitHub 端**，不是你的 workflow 設定或程式碼。

### 錯誤 1：作業無法被 Runner 取得

```
The job was not acquired by Runner of type hosted even after multiple attempts
（即使多次嘗試後，該作業仍未被託管型 Runner 取得）
```

- **意思**：GitHub 無法為這次執行分配一台託管 Runner（虛擬機）。
- **常見原因**：
  - GitHub Actions 服務忙碌或暫時異常
  - 免費方案同時排程太多，超過並行 job 上限
  - 該時段全球使用量大，Runner 被搶光

### 錯誤 2：內部伺服器錯誤

```
Internal server error. Correlation ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
（內部伺服器錯誤。關聯 ID: ...）
```

- **意思**：GitHub 後端發生錯誤，Correlation ID 是給 GitHub 追蹤用的。
- **常見原因**：GitHub 伺服器端暫時故障或負載過高，與你的 repo 設定無關。

---

## 結論：不是你的錯

- 這兩種錯誤都是 **GitHub 基礎設施／服務** 的問題。
- 你的 YAML 與程式碼沒有需要為此修改的錯誤。
- 同一時間多個 workflow（持倉變化、經濟數據、Hyperliquid、流動性雷達、Keep Repository Active 等）一起掛，也符合「整區服務異常」的情況。

---

## 你現在可以做的事

### 1. 查看 GitHub 服務狀態（優先）

- 打開：**https://www.githubstatus.com/**
- 看 **Actions** 是否為 "Operational"。
- 若有 "Minor outage" 或 "Major outage"，就等 GitHub 修復，修好後再重跑即可。

### 2. 手動重新執行失敗的 workflow

- 到 **Actions** 標籤 → 點進失敗的那次 run（例如 Keep Repository Active #355）。
- 右上角 **Re-run all jobs**（或 Re-run failed jobs）。
- 服務恢復後，重跑通常會成功。

### 3. 不必改設定，可選：錯開排程降低搶 Runner 機率

若你希望「在 GitHub 很忙時」稍微減少同時排程的機率，可以適度錯開 cron，例如：

- 保持 `keep-alive` 在整點（`0 * * * *`）沒問題。
- 其他 workflow 已經有錯開（如 3,13,23 / 5,15,25 / */15 等），目前設定是可接受的。
- 若之後仍常在同一分鐘大量失敗，再考慮把部分 workflow 改到不同分鐘執行。

### 4. 免費方案並行限制（僅供了解）

- 免費帳號有 **並行 job 數上限**。
- 若多個 schedule 在同一分鐘觸發，可能部分 job 會一直拿不到 Runner而出現錯誤 1。
- 做法同上：要麼等服務恢復後重跑，要麼適度錯開 cron。

---

## 何時需要檢查自己的設定

若出現的是以下類型，才需要檢查 repo 與 Secrets：

- **Secrets 錯誤**：日誌裡有 `KeyError`、`None`、或明確寫某個 Secret 未設定。
- **Telegram / API 錯誤**：日誌裡有 401、403、404、或「發送失敗」。
- **腳本或依賴錯誤**：日誌裡有 Python traceback、`ModuleNotFoundError`、`pip install` 失敗等。

若畫面上**只有**「job was not acquired」和「Internal server error」，請以「先看 GitHub 狀態 + 重跑」為主，不必懷疑自己的設定。

---

## 快速對照表

| 錯誤訊息 | 可能原因 | 建議動作 |
|---------|----------|----------|
| Job was not acquired by Runner... | GitHub 無法分配 Runner／服務忙碌或異常 | 查 [githubstatus.com](https://www.githubstatus.com)，稍後 **Re-run** |
| Internal server error + Correlation ID | GitHub 伺服器端錯誤 | 同上，等恢復後 **Re-run** |
| KeyError / Secrets / 401 / 403 | 自己的 Secrets 或 API 設定 | 檢查 [修復GitHub_Actions錯誤指南](./修復GitHub_Actions錯誤指南.md)、[檢查Workflow執行問題](./檢查Workflow執行問題.md) |

---

**總結**：你這次「全部都掛掉」是典型的 **GitHub Actions 服務端問題**。先看 [GitHub Status](https://www.githubstatus.com)，恢復後到 Actions 頁面對失敗的 run 按 **Re-run all jobs** 即可。
