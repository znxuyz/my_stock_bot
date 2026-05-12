# 台灣股市量化戰報 Discord Bot 專案說明

## 專案概述

部署在 Railway 的 Discord Bot，每個交易日下午 17:00 自動抓取台股資料，
篩選強勢股票並發送分析報告到 Discord 頻道。同時提供 Web Dashboard
即時查看篩選結果與勝率統計。

GitHub: https://github.com/znxuyz/my_stock_bot
Dashboard: https://znxuyz.github.io/my_stock_bot/
Bot API:   https://mystockbot.up.railway.app

---

## 部署架構

| 服務 | 平台 |
|------|------|
| Bot 服務 (loyal-cooperation) | Railway |
| PostgreSQL (wholesome-light) | Railway |
| Web Dashboard | GitHub Pages |
| 程式語言 | Python 3.12+ |

---

## 檔案結構（30+ 模組）

原本 4 個大檔（bot.py 1572 / stock_bot.py 1758 / db.py 961 / web_export.py 317 行）
已拆成依功能分檔的模組架構。完整檔案樹見 [`PROJECT_STATUS.md`](PROJECT_STATUS.md) 第一節。

| 區塊 | 主要模組 |
|------|---------|
| **進入點 + 通用** | `bot.py`（~70 行 main）/ `config.py`（env + 常數）/ `logging_setup.py` / `time_utils.py` / `format_utils.py` |
| **TWSE 抓資料** | `twse_http.py` / `twse_kbar.py` / `twse_t86.py` / `twse_market.py` / `twse_margin.py` |
| **策略 / 指標** | `indicators.py` / `advanced_indicators.py` / `scoring.py` / `chase.py` / `topflow.py` / `entry_zone.py` |
| **撮合 / 分析** | `matching.py` / `analysis.py` |
| **DB 套件** | `db/` 9 個檔（conn / schema / guilds / runs / screens / settle / stats / holdings / challenges） |
| **Discord Bot** | `discord_bot/` 11 個檔（handlers / scheduler / register / verify / content + 6 種 commands + settle） |
| **Web Dashboard** | `web_export.py` / `docs/index.html` / `docs/data/*.json` / `docs/manifest.json` / `docs/apple-touch-icon-*.png` 等 PWA 資產 |
| **向下相容** | `stock_bot.py`（thin shim 重新匯出）|
| **測試 / CI** | `tests/` 12 檔 87 個 pytest case；`.github/workflows/test.yml` 跑 pyflakes + pytest（Python 3.11 / 3.12）|
| **依賴** | `requirements.txt`：requests / pandas / PyNaCl / psycopg2-binary（鎖大版本） |

---

## 篩選策略 v5（v4 評分 + v5 進場區間放寬 + missed 反向統計）

### 篩選漏斗（依序執行）

**第一輪：法人 + 收盤價**
- 收盤價 ≥ 10 元
- 漲幅 ≥ 1%
- 法人雙買超（外資 + 投信各 ≥ 10K 股）OR 單方買超 ≥ 100K 股
- 取法人合計買超前 30 名
- 用 `to_dict('records')` 讀 DataFrame（5/5 fix：itertuples 對 `'漲跌(+/-)'` 之類欄位會改名導致 KeyError）

**第二輪：技術指標**
- 量比 ≥ 1.5x（當日量 ÷ 5 日均量）
- EMA 多頭排列：20 > 60 > 120（資料不足 120 天用備援 10 > 20 > 60）

**第三輪：8 項評分（總分 100+）**

| 項目 | 配分 | 說明 |
|------|------|------|
| 漲幅 | 10 | 3~5% 滿分；>7% 反而扣分 |
| 量比 | 20 | ≥3x 滿分 |
| 法人買超強度 | 20 | 雙買超 + 合計 ≥50 萬股 滿分 |
| 乖離率 | 20 | 0~3% 滿分；>8% 直接 0 分 |
| RSI | 10 | 60~80 滿分；>80 過熱扣分 |
| 壓力位 | 10 | 無明顯壓力滿分 |
| 位階 | 5 | 偏低滿分 |
| MACD | 10 | 黃金交叉 + DIF>0 滿分 |

加減項：籌碼集中度 0~+8、大盤環境 +3~−5、融資增幅 +3~−8

### 等級門檻

- **SS**：≥ 85 分
- **S**：≥ 68 分
- **A**：≥ 52 分
- **淘汰**：< 52 分

### 連續漲停強勢追漲（特殊模式）

連續 ≥ 3 日漲停才檢查 5 項追漲門檻：
1. 法人合計買超 ≥ 200K 股
2. 量比 ≥ 2.0x
3. 籌碼集中度 ≥ 10%
4. MACD 多頭擴張
5. 大盤環境分數 ≥ 0

| 通過數 | 處理 |
|--------|------|
| 5/5 | 🚀 強勢追漲，進場區 [close, close × 1.07] |
| 4/5 | ⚠️ 觀察名單（顯示但不買） |
| <4   | 跳過 |

---

## 進場與結算邏輯（v5）

### 進場區間（v5 放寬）

| 模式 | 區間 | 撮合特性 |
|------|------|----------|
| normal SS 級 | [close × 0.97, **close × 1.03**] | 容忍 3% 跳空（v5 放寬，避免最強標的常 missed） |
| normal 其他級 | [close × 0.97, **close × 1.02**] | 容忍 2% 跳空 |
| strong_chase | [close × 1.00, close × 1.07] | 跳空跌破不接刀（趨勢反轉訊號） |
| watch | NULL | 不撮合，永遠標 'watch' |

所有進場區間統一由 `entry_zone.calc_entry_zone(close, mode, grade)` 計算 — DB 寫入、
Discord 訊息、`/stock` 顯示三處共用同一函式，避免散落各處改錯。

### T+1 撮合規則（限價單模擬）

- 開盤在區間內 → 以開盤價成交
- 開盤跳空高於區間，盤中 low ≤ zone_high → 以 zone_high 成交
- 開盤跳空低於區間 → normal 撿便宜成交、strong_chase 不接刀
- 都沒觸到 → fill_status='missed'，不計入賺錢勝率

### 結算邏輯

- **第一次**：實際進場日 → 下個週五（5 個交易日）
- **第二次**：實際進場日 → 再下個週五（10 個交易日）
- **目標**：actual_entry × 1.05 / 1.10
- **停損**：actual_entry × 0.95
- 抓 actual_entry_date ~ settle_date 整段 K 棒
- 掃每日 high/low 檢查是否觸 target/stop（記錄日期）
- 觸停損 → settle_pct 強制 -5%
- 否則 settle_pct = (settle_close - actual_entry) / actual_entry × 100
- **假日週五**：自動使用最後可用交易日收盤

### 雙勝率指標

- **進場率** = filled / (filled + missed)
- **賺錢勝率** = (filled & settle_pct > 0) / filled

### v5 missed 反向統計（量化「保守過頭損失多少」）

`screen_records` 新增 `t1_open_price` / `missed_settle1_close` / `missed_settle1_pct`
三個欄位（用 `ALTER TABLE IF NOT EXISTS` 加，**不掉歷史資料**）。

每週五 18:00 結算時，除了結算 filled 紀錄外，會額外用 missed 紀錄的 T+1 開盤價
+ 結算日收盤計算「假設有買到會賺多少」。`db.get_missed_hypothetical_stats` 跨 guild
彙總後寫進 `stats.json.missed_hypo`，用來判斷進場區間是否該再放寬。

---

## Discord 指令

| 指令 | 功能 |
|------|------|
| /help | 所有指令說明 |
| /run [mode] | 手動觸發分析（auto / close / preview） |
| /status | 上次執行狀態 |
| /setup [webhook] | 管理員設定本伺服器 webhook |
| /stock [代號] | 個股分析（積分制 + 進場區間 + MACD + 強勢追漲偵測） |
| /topbuyer | 外資買超前 10 名 |
| /topseller | 外資賣超前 10 名 |
| /holding [@成員] | 查看持倉與損益 |
| /buy [代號] [價格] [股數] | 記錄買入 |
| /sell [代號] [價格] [股數] | 記錄賣出（FIFO 計算實現損益） |
| /leaderboard | 伺服器損益排行 |
| /poll [題目] [選項] | 投票 |
| /challenge [代號] | 選股挑戰（每週五結算清零） |
| /report | 累積統計（進場率 + 賺錢勝率 + 均報酬） |
| /stats | 詳細統計 + 修正建議 |
| /fortune | 今日股市運勢（趣味） |
| /roast | 川普語氣評論大盤（趣味） |

---

## Web Dashboard（PWA 支援）

網址：https://znxuyz.github.io/my_stock_bot/

`<title>` 為「**量化篩選系統**」，含完整 `manifest.json` + 多尺寸 icon。
iOS Safari「分享 → 加到主畫面」、Android Chrome「加到主畫面」都會把 dashboard
變成獨立 app（`display: standalone`），icon 為主體背景色 edge-replicate 滿版
（無白邊，套 iOS 圓角 mask 後邊緣仍是飽和橘/綠色）。

### 主畫面（4 個分頁）

1. **今日篩選** — 等級、分數、模式、進場區間、撮合狀態、實際進場、目標停損
2. **結算狀態** — 每檔詳細狀態（最近 90 天）
3. **勝率統計** — 4 個總覽卡 + 結算勝率折線圖（最近 26 次）+ 三種分組
4. **歷史紀錄** — 可搜尋日期、代號、模式、狀態

### FAB 浮動按鈕（右下角）

點擊主圓鈕展開 5 個子鈕：

| 圖示 | 功能 |
|------|------|
| 📋 | 策略參數一覽（篩選條件 / 評分權重 / 進場規則 / 結算邏輯） |
| 📊 | 外資買賣超榜（買超 / 賣超 Top 10） |
| 🔍 | 個股查詢（任意股票，呼叫 Bot /api/stock） |
| 📚 | 指標教學（RSI/MACD/乖離/量比/EMA/ATR/OBV/籌碼） |
| ⭐ | 追蹤清單（localStorage 收藏，點代號跳到個股查詢） |

### RWD（手機適配）

- 採方案 A：clamp + rem 平滑縮放
- :root font-size: clamp(13px, 3.5vw, 16px)
- 任何裝置（手機 / 平板 / 桌機）平滑過渡無跳變
- 手機點 ⋯ 任意元素 → 適合的尺寸自動套用

---

## 自動排程（台灣時間）

| 時間 | 動作 |
|------|------|
| 週一~五 17:00 | 盤後分析 + T+1 撮合昨日批次 |
| 週五 18:00 | 1 週 + 2 週結算（同步推 dashboard） |
| 週五 21:00 | 選股挑戰結算 + 清零 |
| Bot 啟動 | 自動 export dashboard 一次（內容無變動則跳過 push） |

**17:00 失敗不自動重試**：發 Discord 通知，使用者手動 /run。

**國定假日**：靜默跳過，不騷擾 Discord。

---

## DB Schema

### screen_records (v4-macd-chase)
- 篩選結果、實際進場、結算狀態
- chase_mode (normal/strong_chase/watch)
- consec_limit_up（連續漲停天數）
- entry_zone_low/high、actual_entry_*、actual_target1/2、actual_stop_loss
- settle1/2_date/price/pct/done
- hit_target1/2/_date、hit_stoploss/_date

### analysis_runs（執行狀態持久化）
- 每日分析的 status / attempt / started_at / finished_at / last_error
- Bot 重啟也不會重複觸發 17:00

### 其他表
- guild_settings、holdings、trades、pnl_summary、challenges

### Schema 升級機制
- SCHEMA_VERSION 變動時自動 DROP 重建 screen_records（清空舊資料）
- v5 起額外用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 補欄位 → **不掉歷史資料**（新增 `t1_open_price` / `missed_settle1_close` / `missed_settle1_pct` 三欄走這條路）

---

## 環境變數（Railway）

| 變數 | 說明 |
|------|------|
| DISCORD_BOT_TOKEN  | Discord Bot Token |
| DISCORD_PUBLIC_KEY | Discord App Public Key |
| DISCORD_APP_ID     | Discord App ID |
| DISCORD_WEBHOOK    | 預設 webhook（可空） |
| DATABASE_URL       | PostgreSQL 連線字串 |
| GITHUB_TOKEN       | Fine-grained PAT（contents: read+write） |
| GITHUB_REPO        | znxuyz/my_stock_bot |
| GITHUB_BRANCH      | main |
| BOT_PUBLIC_URL     | https://mystockbot.up.railway.app |

---

## TWSE API 使用

| API | 用途 |
|-----|------|
| T86 | 法人買賣超（外資/投信/自營商） |
| MI_INDEX | 所有個股收盤價 |
| STOCK_DAY | 個股月 K 棒（open/close/high/low/volume） |
| MI_MARGN | 融資融券餘額 |
| MI_QFIIS | 大盤外資買賣超 |

### 流量控制（避免限速）

- T86 共享快取：30 分鐘 TTL，三個來源（run_analysis / /api/stock / /topbuyer）共用
- K 棒智慧快取：當月 1 天 TTL，歷史月份 30 天 TTL（per-month per-sid）
- 所有 TWSE 呼叫間 sleep 0.8s（每分鐘 ~75 次，低於限速門檻）
- 連續 3 檔抓不到 → 自動退避 60 秒等 TWSE 恢復

---

## 已修正的重大問題

1. **篩選結果美化勝率** — entry_price 買不到 → 改用實際 T+1 開盤撮合
2. **目標價低於收盤** — target 用 ma10×1.08 → 改用 actual_entry × 1.05/1.10
3. **Railway redeploy 迴圈** — _startup_export 推同樣內容觸發新 commit → push 前比對內容，無變動則跳過
4. **17:00 重複觸發** — Bot 重啟時 in-memory fired set 重置 → 用 DB analysis_runs 持久化
5. **TWSE 限速** — 並發抓取超過 60~80/分鐘 → T86 快取 + K 棒快取 + sleep 0.8s + 自動退避
6. **5/5 全部 0 檔通過篩選** — `_filter_first_round` 用 itertuples 對 `'漲跌(+/-)'` 等含特殊字元欄位會被改名為 `_4` → 改用 `to_dict('records')` 並加 regression test 擋住
7. **5/7 真假日誤判** — TWSE 限速錯誤頁讓 `parse_t86` 回 empty df，被 caller 當假日靜默跳過 → `fetch_t86_cached` 拆兩種失敗：真假日回 empty + 快取，parse 失敗回 `None` + 不快取（caller 視為 `'fail'` 通知用戶）
8. **Railway log 全紅難判讀** — 原 logging 全送 stderr，Railway UI 一律紅色 → 拆兩個 handler：DEBUG/INFO 走 stdout、WARNING+ 走 stderr
9. **iOS Add to Home Screen 白邊** — 原 icon.png 4 角有 vignette 淺白漸層，iOS 圓角 mask 切下去變視覺白邊 → zoom 1.18× + center crop + edge-replicate pad 處理，邊緣全飽和色

---

## 已移除的功能

- X 級（逆勢小跌法人大買）→ 改用積分制自然淘汰
- 法人連買天數 → 資料來源不可靠
- 新聞推播功能
- 近 3 日走勢（個股分析）
- generate_advice（綜合說明）→ 改為指標說明區塊
- 自動重試機制 → 改為 Discord 通知 + 手動 /run

---

## 待觀察事項

- 大盤外資 MI_QFIIS API 欄位解析正確性
- 震盪期 / 熊市的大盤趨勢過濾層（未實作）
- TWSE 限速恢復速度（觀察一週後評估退避秒數是否要調整）
- 累積樣本後的策略勝率（建議至少 4~6 週後評估是否要再調整評分權重）
