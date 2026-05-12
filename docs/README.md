# Dashboard 部署說明（「量化篩選系統」PWA）

這個資料夾是 [GitHub Pages](https://pages.github.com/) 的靜態網站來源。
含 `manifest.json` + 多尺寸 icon，瀏覽器會把 dashboard 視為可安裝 PWA。

## 資料夾內容

| 檔案 | 用途 |
|------|------|
| `index.html` | 4 分頁 dashboard 主畫面（標題：量化篩選系統） |
| `manifest.json` | PWA manifest（`standalone` display、`#FFF5EC` 背景、`#7FD4C1` theme color） |
| `apple-touch-icon-180/152/120.png` | iOS Add to Home Screen icon |
| `favicon-32x32.png` / `favicon-16x16.png` / `favicon.ico` | 桌面 / 舊瀏覽器分頁 icon |
| `android-chrome-192x192.png` / `512x512.png` | Android PWA 主屏 / splash maskable |
| `icon-before-after.png` | iOS 白邊修正前後對比（純記錄用，不被 HTML 引用） |
| `data/today.json` | 當日篩選結果 |
| `data/stats.json` | 累積勝率 + missed 反向統計（v5）|
| `data/history.json` | 最近 90 天紀錄 |
| `data/topflow.json` | 外資買賣超 Top 10 |
| `data/config.json` | 給前端讀取 Bot 公開 API URL |

## 啟用步驟（GitHub UI）

1. 進到 repo → **Settings** → **Pages**
2. **Source**：選 `Deploy from a branch`
3. **Branch**：選 `main`，資料夾選 `/docs`
4. 按 **Save**，等 1~2 分鐘後 Pages 就會發布到
   `https://<user>.github.io/<repo>/`（例：`https://znxuyz.github.io/my_stock_bot/`）

## Bot 自動更新

Bot 每天盤後 17:00 篩選完成後（`analysis.run_analysis`），會呼叫 `web_export.export_dashboard()`：
1. 從 PostgreSQL 撈出最新一日的篩選結果、歷史紀錄與彙總勝率（含 v5 missed_hypo）
2. 寫成 `data/today.json`、`data/stats.json`、`data/history.json`、`data/topflow.json`、`data/config.json`
3. 透過 GitHub REST API（`PUT /repos/{owner}/{repo}/contents/{path}`）推回此目錄
4. **內容比對**：移除 `updated_at` / `queried_at` 時間戳後與遠端比對，無實質變動 → 跳過 push（避免觸發 Railway redeploy 迴圈）
5. GitHub Pages 會在數十秒內自動 redeploy

每週五 18:00 結算 1 週/2 週報酬後也會再推一次。Icon / `manifest.json` 不會被 Bot 動，
要重切 icon 時用根目錄的 `icon.png` 作為原始檔（1024×1024 RGB 不透明、邊到邊滿版）。

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
