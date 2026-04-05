# 黛安娜的科技蟹蟹水果報 🦀

> 每週五 18:00 自動搜尋科技新聞，整理成週報，發送到 Discord。

由 Claude AI 驅動，透過 GitHub Actions 全自動排程。

---

## 這個專案做了什麼

1. **自動搜尋**：Claude 搜尋本週 5 則最重要的科技新聞（來源限定主流媒體）
2. **自動撰寫**：用固定語氣風格整理成週報，分商業視角與科技視角解析
3. **自動發送**：透過 Discord Webhook 推送到指定頻道
4. **全自動排程**：GitHub Actions 每週五 18:00（台灣時間）自動執行，不需人工操作

---

## 技術架構

```
GitHub Actions（排程觸發）
    └── weekly_news.py
          ├── search_real_urls()     # Claude API + web_search 搜尋真實 URL
          ├── write_report_with_urls()  # Claude API 撰寫週報
          └── post_to_discord()      # Discord Webhook 發送
```

使用工具：
- [Anthropic Claude API](https://www.anthropic.com/) (`claude-opus-4-5` + `web_search` 工具)
- Discord Webhook
- GitHub Actions（免費方案即可）

---

## 自己部署

### 1. Fork 這個 repo

按右上角的 **Fork**

### 2. 設定 GitHub Secrets

進入你的 repo → Settings → Secrets and variables → Actions → New repository secret

| Secret 名稱 | 說明 |
|---|---|
| `ANTHROPIC_API_KEY` | 到 [console.anthropic.com](https://console.anthropic.com) 取得 |
| `DISCORD_WEBHOOK_URL` | 頻道設定 → 整合 → Webhook → 建立並複製網址 |

### 3. 手動測試

Actions → 黛安娜的科技蟹蟹水果報 → Run workflow

成功後每週五 18:00 會自動執行，不需要任何操作。

---

## 週報樣式

```
黛安娜的科技蟹蟹水果報｜本週科技趨勢整理

開場：
這週科技圈的主旋律是...

本週 5 大趨勢：

1. 新聞標題
發生什麼：一句話說清楚核心。
商業視角：對市場、商業模式、投資邏輯的意義。
科技視角：對開發者、技術選型、工作流程的意義。
來源：媒體名稱（網址）

...

本週觀察：
這週主旋律是什麼、接下來最值得追什麼。
```

---

## 注意事項

- API 費用：每次執行約消耗 `claude-opus-4-5` 的 4000-6000 tokens，約 NT$3-5 元
- 免費方案：GitHub Actions 每月 2000 分鐘免費，本專案每週約用 2-3 分鐘
- 金鑰安全：所有金鑰存放在 GitHub Secrets，不會出現在程式碼中

---

Made with Claude API × GitHub Actions
