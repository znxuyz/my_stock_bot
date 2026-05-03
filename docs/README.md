# Dashboard 部署說明

這個資料夾是 [GitHub Pages](https://pages.github.com/) 的靜態網站來源。

## 啟用步驟（GitHub UI）

1. 進到 repo → **Settings** → **Pages**
2. **Source**：選 `Deploy from a branch`
3. **Branch**：選 `main`，資料夾選 `/docs`
4. 按 **Save**，等 1~2 分鐘後 Pages 就會發布到
   `https://<user>.github.io/<repo>/`（例：`https://znxuyz.github.io/my_stock_bot/`）

## Bot 自動更新

Bot 每天盤後 17:00 篩選完成後（`stock_bot.run_analysis`），會呼叫 `web_export.export_dashboard()`：
1. 從 PostgreSQL 撈出最新一日的篩選結果、歷史紀錄與彙總勝率
2. 寫成 `data/today.json`、`data/stats.json`、`data/history.json`
3. 透過 GitHub REST API（`PUT /repos/{owner}/{repo}/contents/{path}`）推回此目錄
4. GitHub Pages 會在數十秒內自動 redeploy

每週五 18:00 結算 1 週/2 週報酬後也會再推一次。

## 必要環境變數（Railway）

| 變數 | 用途 |
|------|------|
| `GITHUB_TOKEN` | Personal Access Token（fine-grained，需 `Contents: Read & Write`） |
| `GITHUB_REPO`  | 預設 `znxuyz/my_stock_bot` |
| `GITHUB_BRANCH`| 預設 `main` |

若未設定 `GITHUB_TOKEN`，Bot 仍會在本機（Railway 容器內）寫檔，但不會推到 GitHub。

## 本機預覽

```bash
cd docs
python -m http.server 8000
# 開 http://localhost:8000/
```
