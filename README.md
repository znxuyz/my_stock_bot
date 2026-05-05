# 川投顧量化系統 ── 台股 Discord Bot

每個交易日盤後 17:00 自動抓 TWSE 法人 / 量價資料，篩選強勢股後推送到 Discord 頻道，
同步更新 [Web Dashboard](https://znxuyz.github.io/my_stock_bot/)。

| 服務 | 連結 |
|------|------|
| Bot HTTP 端點 | https://mystockbot.up.railway.app |
| Dashboard | https://znxuyz.github.io/my_stock_bot/ |
| Bot 公開 API | `https://mystockbot.up.railway.app/api/stock?sid=2330` |

詳細狀態見 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)；產品概覽見 [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md)。

---

## 快速開始

### 1. Discord 指令（部署完成後）

```
/help                          # 看所有指令
/run [auto|close|preview]      # 手動觸發分析
/stock [代號]                  # 個股技術分析（0–5 星推薦度）
/topbuyer / /topseller         # 外資買 / 賣超 Top 10
/buy [代號] [價格] [股數]       # 記錄買入
/sell [代號] [價格] [股數]      # FIFO 賣出 + 計算實現損益
/holding [@對象]               # 持倉與未實現損益
/leaderboard                   # 伺服器損益排行
/challenge [代號]              # 一週後比誰獲利高
/report / /stats               # 累積勝率 / 詳細統計
/setup [webhook]               # 管理員：設定推播頻道
```

### 2. 本機開發

```bash
git clone https://github.com/znxuyz/my_stock_bot.git
cd my_stock_bot

# 依賴
pip install -r requirements.txt
pip install pytest pyflakes

# 跑單元測試（純邏輯，不打 TWSE / 不需 DB）
python -m pytest tests/ -v

# 模擬 boot 流程（不綁 port）
DISCORD_BOT_TOKEN=dummy \
DISCORD_PUBLIC_KEY=$(printf 'a%.0s' {1..64}) \
DISCORD_APP_ID=dummy \
python -c "
import bot
class Fake:
    def __init__(self,*a,**kw): pass
    def serve_forever(self): print('boot ok')
bot.ThreadingHTTPServer = Fake
bot.scheduler = lambda: None
bot.register_commands = lambda: None
bot.main()
"
```

### 3. 環境變數（Railway 部署）

| 變數 | 必填 | 說明 |
|------|------|------|
| `DISCORD_BOT_TOKEN`  | ✅ | Discord Bot Token |
| `DISCORD_PUBLIC_KEY` | ✅ | App Public Key（Ed25519 簽章驗證） |
| `DISCORD_APP_ID`     | ✅ | App ID |
| `DATABASE_URL`       | ✅ | PostgreSQL（Railway 自動注入） |
| `GITHUB_TOKEN`       | ✅ | Fine-grained PAT（contents: read+write）給 dashboard 用 |
| `GITHUB_REPO`        | ⚪ | 預設 `znxuyz/my_stock_bot` |
| `GITHUB_BRANCH`      | ⚪ | 預設 `main` |
| `BOT_PUBLIC_URL`     | ✅ | `https://mystockbot.up.railway.app` |
| `DISCORD_WEBHOOK`    | ⚪ | 留空可，多伺服器靠 DB |
| `LOG_LEVEL`          | ⚪ | `INFO`（預設）/ `DEBUG` |
| `TWSE_VERIFY_SSL`    | ⚪ | `0`（預設關閉）/ `1` 強制驗證 |

---

## 篩選策略（4 層漏斗）

每天盤後 17:00 由 `discord_bot/scheduler.py` 觸發 `analysis.run_analysis()`：

### 1️⃣ 第一輪：基本條件
- 收盤價 ≥ **10 元**
- 漲幅 ≥ **+1%**
- 法人**雙買超**：外資 ≥ 10K 股 **且** 投信 ≥ 10K 股
  **或** 單方買超合計 ≥ 100K 股

### 2️⃣ 第二輪：候選保護
通過第一輪若 > 30 檔，依「外資+投信合計」取前 30 名。

### 3️⃣ 第三輪：技術門檻
- 量比 ≥ **1.5x**
- EMA 多頭排列：`20 > 60 > 120`（資料 ≥ 120 筆）<br>
  備援 `10 > 20 > 60`（60~119 筆）

### 4️⃣ 第四輪：8 項評分（總分 105）+ 加減項

| 項目 | 配分 | 滿分條件 |
|------|------|---------|
| 漲幅 | 10 | 3~5%；>7% 反扣 |
| 量比 | 20 | ≥3x |
| 法人買超強度 | 20 | 雙買超 + 合計 ≥50 萬股 |
| 乖離率（10 日） | 20 | 0~3%；>8% 直接 0 |
| RSI | 10 | 60~80 |
| 壓力位 | 10 | 無明顯壓力 |
| 位階 | 5  | <20%（剛起漲） |
| MACD | 10 | 黃金交叉 + DIF>0 |
| 籌碼集中度 | 0~+8 | 法人淨買超 / 成交量 |
| 大盤環境 | +3~-5 | 加權外資今日 / 連 3 日累積 |
| 融資增幅 | +3~-8 | 5 日 % 變化 |

**等級門檻**：SS ≥ 85｜S ≥ 68｜A ≥ 52｜< 52 淘汰

### 🚀 強勢追漲特殊路徑
連續 ≥ 3 日漲停才檢查 5 項追漲門檻（法人買超 ≥200K / 量比 ≥2x / 籌碼集中度 ≥10% / MACD 多頭擴張 / 大盤分數 ≥0）：

| 通過數 | 處理 | 進場區間 |
|--------|------|---------|
| 5/5 | 🚀 CHASE | `[close, close × 1.07]` |
| 4/5 | ⚠️ WATCH（觀察名單，不買） | NULL |
| <4  | reject（跳過） | — |

### 進場 / 結算邏輯（v5）

| 模式 | 進場區間 | 撮合特性 |
|------|---------|---------|
| **normal SS 級** | `[close × 0.97, close × 1.03]` | 容忍 3% 跳空（v5 放寬，避免最強標的常 missed） |
| **normal 其他級** | `[close × 0.97, close × 1.02]` | 容忍 2% 跳空 |
| **strong_chase** | `[close × 1.00, close × 1.07]` | 跳空跌破不接刀 |
| **watch** | — | 不撮合 |

- **T+1 撮合**：開盤在區間內 → 開盤價成交；跳空高開但盤中 low ≤ zone_high → 用 zone_high 成交；都沒觸到 → missed
- **目標 / 停損**：以實際進場價計算，目標 +5% / +10%、停損 -5%
- **結算**：第 1 / 2 個週五；觸停損 settle_pct 強制 -5%
- **missed 反向統計（v5）**：missed 紀錄也會記下 T+1 開盤價，週五補算「假設有買到會賺多少」，量化「保守過頭損失多少」

---

## 模組結構

```
my_stock_bot/
├── bot.py                  # 進入點
├── config.py               # 集中所有 env / 常數
├── logging_setup.py        # 全域 logging 設定
├── time_utils.py           # tw_now / get_target_date / prev_months / next_friday
├── format_utils.py         # fmt_share / star_str / get_opt
│
├── twse_http.py            # safe_get / safe_read_csv（共用 HTTP 層）
├── twse_kbar.py            # 月 K 棒抓取 + 本機快取（共用底層 _fetch_full_kbars）
├── twse_t86.py             # 三大法人買賣超 + 30 分鐘共享快取
├── twse_market.py          # 加權指數 + 大盤外資歷史
├── twse_margin.py          # 融資 5 日增幅
│
├── indicators.py           # EMA / RSI / ATR / OBV / MACD / 量比 / 乖離
├── advanced_indicators.py  # RSI 評分 / ATR 停損 / 壓力位 / 位階 / OBV 背離
├── scoring.py              # calc_score / market_env / margin_score / chip_concentration
├── chase.py                # 連續漲停 + 5 項追漲門檻
├── topflow.py              # 外資買賣超 Top N
├── entry_zone.py           # 進場區間單一入口（v5）
├── matching.py             # T+1 撮合
├── analysis.py             # run_analysis 主流程
│
├── db/                     # PostgreSQL 套件（9 個檔）
│   ├── conn.py             # 連線 + OperationalError 自動重試 5/15/30s
│   ├── schema.py           # init_db + ALTER TABLE 升級不掉資料
│   ├── guilds.py           # 伺服器設定
│   ├── runs.py             # analysis_runs 執行狀態
│   ├── screens.py          # screen_records 寫入 / 查詢
│   ├── settle.py           # T+1 撮合 + 結算寫入 + missed 假設結算
│   ├── stats.py            # 統計查詢（含 missed 反向統計）
│   ├── holdings.py         # 持倉 / FIFO / pnl
│   └── challenges.py       # 選股挑戰
│
├── discord_bot/            # Discord 套件（11 個檔）
│   ├── handlers.py         # InteractionHandler + LAST_RUN 加鎖
│   ├── scheduler.py        # 排程：17:00 分析 / 週五 18:00 結算
│   ├── register.py         # slash command 註冊
│   ├── verify.py           # Ed25519 簽章驗證
│   ├── content.py          # FORTUNES / ROASTS 純資料
│   ├── basic_commands.py   # /help /fortune /roast /poll
│   ├── stock_commands.py   # /stock /topbuyer /topseller + analyze_stock_data
│   ├── portfolio_commands.py  # /buy /sell /holding /leaderboard
│   ├── challenge_commands.py  # /challenge + 週五結算
│   ├── stats_commands.py   # /report /stats
│   └── settle.py           # settle_weekly
│
├── web_export.py           # Dashboard JSON + GitHub API push
│
├── stock_bot.py            # 向下相容 shim
│
├── docs/                   # GitHub Pages 來源
│   ├── index.html          # 4 分頁 Dashboard（今日 / 結算 / 勝率 / 歷史）
│   └── data/*.json         # Bot 自動產生
│
└── tests/                  # 68 個 pytest 測試
    ├── test_indicators.py
    ├── test_scoring.py
    ├── test_chase.py
    ├── test_settle.py
    ├── test_entry_zone.py
    ├── test_topflow.py
    ├── test_time_utils.py
    ├── test_imports.py
    └── test_last_run.py
```

---

## 自動排程（台灣時間）

| 時間 | 動作 |
|------|------|
| 週一~五 17:00 | 盤後分析 + 昨日批次 T+1 撮合 + Dashboard 同步 |
| 週五 18:00 | 1 週 + 2 週結算（含 missed 假設結算） |
| 週五 21:00 | 選股挑戰結算 + 清零 |
| Bot 啟動 | 自動 export Dashboard 一次（內容無變動 → 跳過 push 避免 redeploy 迴圈）|

**17:00 失敗不自動重試**：發 Discord 通知，使用者手動 `/run`。

---

## CI / 測試

```bash
# 完整測試（68 個）
python -m pytest tests/

# 靜態檢查
python -m pyflakes *.py db/*.py discord_bot/*.py tests/*.py
```

`.github/workflows/test.yml` 在每個 push / PR 自動跑：
- pyflakes（unused import / undefined name）
- pytest on Python 3.11 與 3.12

---

## 部署架構

```
                     ┌────────────────┐
                     │ TWSE Open API  │
                     └───────┬────────┘
                             │ T86 / MI_INDEX / STOCK_DAY / MI_MARGN / MI_QFIIS
                             ▼
┌──────────────────────────────────────────────┐
│ Railway: loyal-cooperation                   │
│  ┌────────────────────────────────────────┐  │
│  │ bot.py (Python 3.11+)                  │  │
│  │  - HTTP server (Discord Interactions)  │  │
│  │  - scheduler thread                    │  │
│  │  - run_analysis / settle_weekly         │  │
│  └─────────┬────────────────────┬─────────┘  │
└────────────┼────────────────────┼────────────┘
             │                    │
             ▼                    ▼
   ┌──────────────────┐   ┌────────────────────┐
   │ Railway Postgres │   │ GitHub API (PUT)   │
   │  screen_records  │   │ docs/data/*.json   │
   │  analysis_runs   │   └─────────┬──────────┘
   │  holdings/trades │             │
   │  challenges      │             ▼
   └──────────────────┘   ┌────────────────────┐
                          │ GitHub Pages       │
                          │ docs/index.html    │
                          └────────────────────┘
                                    ▲
                                    │ HTTPS
                                    │
                          ┌─────────┴──────────┐
                          │ User Browser       │
                          │ 直接呼叫 Bot:      │
                          │ /api/stock?sid=... │
                          └────────────────────┘
```

---

## 重大設計決策（避免回頭走錯路）

1. **不自動重試 17:00**：失敗發 Discord，使用者手動 `/run`，避免 TWSE 限速時連續打請求
2. **DB 驅動的 scheduler 狀態**：`analysis_runs` 表 → Bot 重啟也不會重複觸發
3. **GitHub push 內容比對**：移除時間戳後比對，避免無實質變動的 commit 觸發 Railway redeploy 迴圈
4. **K 棒分層快取**：當月 1 天 TTL、歷史月份 30 天 TTL；filled / missed 兩個 fetcher 共用同一份
5. **限速自動退避**：連續 3 檔抓不到 → 暫停 60 秒
6. **schema_version 升級機制 + ALTER TABLE 補欄位**：策略大改 DROP 重建，欄位增補不掉資料
7. **進場用實際 T+1 開盤** + **目標停損用實際進場價**：勝率為真實可達成
8. **進場區間集中管理（v5）**：`entry_zone.py` 是所有 zone 計算的單一入口
9. **logging 全面取代 print**：可由 `LOG_LEVEL=DEBUG` 控制細度
10. **DB 連線重試 + LAST_RUN 加鎖**：Railway DB 短斷線不會炸；scheduler / handler thread 不再 race

---

## 授權 / 免責

本專案僅供研究與教育用途，**不構成投資建議**。
策略勝率為歷史模擬，不保證未來表現；實際下單應自行判斷風險。

更詳細的策略說明、待辦清單與環境變數說明見 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。
