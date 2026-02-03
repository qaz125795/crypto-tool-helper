# GitHub Actions 資料持久化設置指南

## 📊 當前狀況

目前 JackBot 使用文件系統存儲以下數據：
- `sent_economic_data_ids.json` - 經濟數據去重（已推送的 ID 列表）
- `last_news_time.json` - 新聞時間戳去重（最後抓取時間）

**問題**：GitHub Actions 是無狀態的，每次執行都是全新環境，文件會在執行結束後消失。

---

## 🎯 解決方案比較

### 選項 1：使用 GitHub Actions Cache（⭐ 推薦，最簡單）

**優點**：
- ✅ 完全免費
- ✅ 不需要額外設置
- ✅ 自動處理數據持久化
- ✅ 數據在 workflow 之間共享

**缺點**：
- ⚠️ 有大小限制（10GB，但去重數據通常很小）
- ⚠️ 緩存可能過期（7 天無使用會自動清理）

**適用場景**：適合小量去重數據（ID 列表、時間戳等）

---

### 選項 2：使用既有資料庫（⭐ 如果已有資料庫）

**優點**：
- ✅ 數據永久保存
- ✅ 可以與其他服務共享數據
- ✅ 無大小限制
- ✅ 更可靠的持久化

**缺點**：
- ⚠️ 需要設置資料庫連接
- ⚠️ 需要配置環境變量

**適用場景**：
- 如果您的 Zeabur 部署已經有資料庫
- 如果需要與其他服務共享數據
- 如果需要更可靠的數據持久化

---

### 選項 3：接受無狀態（最簡單，但可能重複）

**優點**：
- ✅ 不需要任何額外設置
- ✅ 最簡單

**缺點**：
- ⚠️ 可能會有重複推送
- ⚠️ 經濟數據可能重複發送

**適用場景**：
- 如果重複推送不是問題
- 如果 API 端點有內建去重邏輯

---

## 🚀 推薦方案：使用 GitHub Actions Cache

對於您的情況（只需要存儲少量去重數據），**推薦使用 GitHub Actions Cache**，因為：

1. **簡單**：只需要修改少量代碼
2. **免費**：不需要額外服務
3. **足夠**：去重數據通常很小，完全夠用

---

## 📝 實作方案

### 方案 A：使用 GitHub Actions Cache（推薦）

需要修改代碼以支持 Cache，然後在 workflow 中使用 cache action。

#### 步驟 1：安裝 cache 工具（可選）

在 workflow 中添加 cache 步驟：

```yaml
- name: Cache data files
  uses: actions/cache@v4
  with:
    path: data/
    key: jackbot-data-${{ github.run_number }}
    restore-keys: |
      jackbot-data-
```

#### 步驟 2：使用 GitHub 環境變量或 Secret（簡單方案）

更簡單的方式是使用 GitHub 的環境變量或直接接受無狀態。

---

### 方案 B：使用既有資料庫

如果您已經有資料庫（例如：Zeabur 的 PostgreSQL/MongoDB），可以：

1. **共用連接**：在 GitHub Secrets 中添加資料庫連接字串
2. **修改代碼**：將文件讀寫改為資料庫讀寫
3. **共享數據**：GitHub Actions 和 Zeabur 可以共用同一份數據

---

## 🤔 建議

### 如果您**已經有資料庫**：

**建議：使用既有資料庫**

優點：
- 數據統一管理
- 與 Zeabur 部署共用
- 更可靠的持久化

需要做的事：
1. 在 GitHub Secrets 中添加資料庫連接資訊
2. 修改 `jackbot.py` 以支持資料庫存儲
3. 確保資料庫可以被 GitHub Actions 訪問（可能需要允許外部 IP）

---

### 如果您**沒有資料庫**或**不想設置**：

**建議：接受無狀態或使用簡單的時間戳去重**

對於 Tree of Alpha 新聞：
- 已經使用時間戳去重（`last_news_time`）
- 即使沒有持久化，也只在第一次執行時可能重複推送少量新聞
- 可以接受

對於經濟數據：
- 如果每天只執行一次，重複推送的可能性較低
- 或者可以在 Telegram 端設置去重邏輯

---

## 💡 我的建議

基於您的使用場景，我建議：

### **短期方案（立即可用）**：
**接受無狀態**，因為：
- Tree of Alpha 新聞使用時間戳去重，影響較小
- 經濟數據每天只執行一次，重複機率低
- 最簡單，不需要額外設置

### **長期方案（如果需要更可靠）**：
1. 如果已有資料庫 → 使用既有資料庫
2. 如果沒有資料庫 → 考慮使用 GitHub Actions Cache 或設置簡單的外部存儲

---

## ❓ 請告訴我

1. **您是否已經有資料庫？**
   - 如果有，是什麼類型的資料庫？（PostgreSQL、MongoDB、MySQL 等）
   - 連接字串是什麼格式？

2. **您對重複推送的容忍度？**
   - 可以接受偶爾重複嗎？
   - 還是必須完全避免重複？

根據您的回答，我可以提供更具體的實作方案！

---

## 📚 參考資料

- [GitHub Actions Cache 文檔](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows)
- [GitHub Actions 環境變量](https://docs.github.com/en/actions/learn-github-actions/variables)


