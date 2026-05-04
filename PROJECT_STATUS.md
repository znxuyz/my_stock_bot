# PROJECT_STATUS.md

> 最後更新：2026-05-04
> Schema 版本：v4-macd-chase
> 部署狀態：main 上 commit `4e2bd23c`，Bot 服務 https://mystockbot.up.railway.app

---

## 一、九個檔案各自的功能

### 1. `bot.py`（79KB / 1543 行）
Discord Bot 主程式。職責：

- **Discord 互動端點**（Ed25519 簽章驗證 + Slash Command）
  - 註冊指令：`register_commands()` PUT 到 Discord API
  - HTTP server：`InteractionHandler.do_POST` 處理 type=1 (PING) 和 type=2 (Application Command)
- **指令處理函式**：`cmd_help / cmd_fortune / cmd_roast / cmd_holding / cmd_buy / cmd_sell / cmd_leaderboard / cmd_poll / cmd_challenge / cmd_report / cmd_stats`
- **個股查詢**：`analyze_stock_data(sid)` 抓 6 個月 K 棒、計算指標、評分；`format_stock_text(d)` 把 dict 渲染成 Discord 文字；`stock_api_get(sid, force)` 帶 15 分鐘快取的 API 入口
- **API endpoint**：`InteractionHandler.do_GET` 處理 `/api/stock?sid=XXXX&force=0/1`，回傳 CORS JSON
- **OPTIONS preflight**：`do_OPTIONS` 給 dashboard 用
- **scheduler 排程器**：每 60 秒 tick 一次
  - 平日 17:00 觸發分析（用 `_db.can_run_today` 檢查避免重複）
  - 週五 18:00 觸發 `settle_weekly`
  - 週五 21:00 觸發 `settle_challenge`
  - 啟動 90 秒緩衝期（防止重啟瞬間又觸發）
- **`_run_analysis_with_status(attempt)`**：包裝 `sb.run_analysis`，呼叫前 `_db.record_run_start`、結束後 `_db.record_run_end`
- **`settle_weekly(settle_date, round_num, guild_id)`**：抓 actual_entry_date~settle_date 整段 K 棒、掃 high/low 找觸發、計算 settle_pct
- **`fetch_top_traders(top_type, n)`**：用 `sb.fetch_t86_cached` 取外資買賣超 Top 10
- **`get_latest_price(sid)`**：fetch 個股當日收盤（給 /holding /challenge 用）
- **常數**：`ANALYSIS_TRIGGER_TIMES = [(17, 0)]`（只剩 17:00，不自動重試）

### 2. `stock_bot.py`（75KB / 1791 行）
選股核心邏輯 + TWSE 抓取 + 技術指標。職責：

- **TWSE 抓取**：
  - `safe_get(url, params, timeout, retries, wait)`：通用重試包裝
  - `parse_t86(text)`：解析 T86 CSV，固定欄位索引（外資 idx=4、投信 idx=10、合計 idx=18）
  - `fetch_t86_cached(date_str)`：30 分鐘共享快取，回傳 `DataFrame`/empty/None
  - `fetch_stock_day_fast(sid, yyyymm)`：抓單月 K 棒，內建 per-month 快取
  - `_kbar_cache_get/set`：快取讀寫（當月 1 天 TTL、歷史月份 30 天 TTL）
  - `build_history_fast(sid, months)`：逐月組成完整歷史 DataFrame
  - `get_market_info(date_str)`：抓加權指數
  - `fetch_margin_change(sid, date_str)`：融資 5 日增幅
  - `_fetch_kbars_with_open(sid, yyyymm)` / `get_t1_kbar` / `get_period_kbars`：含開盤的 K 棒抓取（給結算用）
- **技術指標**：
  - `calc_ema(series, span)`、`check_ema_bull(df)`：EMA 多頭排列檢查（主要 20>60>120、備援 10>20>60）
  - `calc_volume_ratio(df, target_date)`：當日量 ÷ 5 日均量
  - `calc_rsi(series, period=14)`、`calc_atr(df, period=14)`、`calc_obv(df)`
  - `calc_macd(df, fast=12, slow=26, signal=9)`：DIF/DEA/Histogram + 0~10 分
  - `calc_bias_and_entry(df, price)`：10 日乖離率（不含目標價）
  - `calc_advanced_indicators(df, price)`：包整 RSI、ATR 停損、壓力位、位階、OBV
  - `calc_chip_concentration(foreign, trust, volume)`：法人淨買超 / 成交量
  - `calc_market_env(market_foreign_history)`：大盤外資 3 日累積，連賣 500 億暫停
  - `calc_margin_score(margin_today, margin_5d_ago)`：融資 5 日增幅評分
- **連續漲停判定**：
  - `count_consecutive_limit_ups(df, threshold=9.5)`：從最後一筆往回算
  - `check_strong_chase(entry, macd_info, market_score)`：5 項追漲門檻檢查
- **核心**：
  - `calc_score(entry)`：總分（見下方第三節）
  - `extract_top_flow(df_merged, n=10)`：外資買超/賣超 Top N
  - `run_analysis(attempt=0)`：盤後分析主流程（爬資料 → 篩選 → 評分 → 推 Discord → 寫 DB → 推 dashboard）
  - `fill_pending_t1_entries(today)`：T+1 撮合（抓昨日批次的 T+1 K 棒、判定成交）
- **常數**：
  - `MIN_PRICE = 10`、`VOLUME_RATIO_MIN = 1.5`、`MAX_CANDIDATES = 30`
  - `MIN_FOREIGN_SHARE = 10000`、`MIN_TRUST_SHARE = 10000`、`MIN_INST_SHARE_SINGLE = 100000`
  - `T86_CACHE_TTL_SEC = 1800`（30 分鐘）
  - run_analysis 內：`RATE_LIMIT_THRESHOLD = 3`、`RATE_LIMIT_BACKOFF_SEC = 60`

### 3. `db.py`（39KB / 962 行）
PostgreSQL 資料庫操作。職責：

- **連線**：`get_conn()` 讀 `DATABASE_URL`，自動把 `postgres://` 轉 `postgresql://`
- **Schema 初始化**：`init_db()` 建立全部表，比對 `SCHEMA_VERSION` 不一致時 DROP+重建 `screen_records`
  - 目前版本：`v4-macd-chase`
- **每日執行狀態**（`analysis_runs` 表）：
  - `record_run_start(run_date, attempt)`：upsert 標 'running'
  - `record_run_end(run_date, status, last_error)`：標 'success'/'holiday'/'fail'
  - `get_run_state(run_date)`：回傳 (status, attempt, started_at)
  - `can_run_today(run_date, now_dt)`：判斷可否觸發（不自動重試版）
  - `RUN_TIMEOUT_SEC = 1800`：running 超過 30 分鐘視為卡死
- **篩選紀錄**：
  - `save_screen_records(records, screen_date, guild_id)`：DELETE 同日 pending → INSERT 新批次
  - `next_friday(from_date, n)`：取下個第 n 個週五（給 settle_date 用）
  - `calc_position_pct(grade, bias_pct)`：依等級 + 乖離計算建議倉位
- **T+1 撮合**：
  - `get_records_needing_t1_check(before_date)`：撈 fill_status='pending' 且 < before_date
  - `determine_t1_fill(t1_open, t1_high, t1_low, zone_low, zone_high, allow_gap_down)`：限價單模擬
  - `fill_t1_entry(record_id, t1_date, status, entry_price)`：寫回實際進場 + 計算 target/stop
- **結算**：
  - `get_pending_settle(settle_date, round_num, guild_id)`：撈 fill_status='filled' 且未結算
  - `update_settle(record_id, round_num, settle_close, hit_t1, hit_t2, hit_sl, dates, settle_pct)`
- **統計查詢**（給 /report /stats / Dashboard）：
  - `get_cumulative_stats(guild_id)`：等級 / 乖離 / 雙買超分組勝率
  - `get_aggregated_stats()`：跨伺服器 DISTINCT ON 去重的彙總
  - `get_aggregated_summary()`：跨伺服器總覽（filled/missed/pending/win1/win2 等）
  - `get_settlement_timeline(limit=26)`：依 settle_date 分組（給折線圖）
  - `get_screens_by_date(date)`、`get_history_records(days)`、`get_latest_screen_date()`
- **使用者資料**（per guild_id + user_id）：
  - `get_holdings / add_holding / remove_holding / get_pnl / get_leaderboard`
  - `get_challenge / add_challenge / get_all_challenges / clear_challenges`
- **伺服器設定**：`set_guild_webhook / get_guild_webhook / get_all_webhooks / remove_guild`

### 4. `web_export.py`（12KB / 318 行）
Dashboard JSON 匯出 + GitHub 推送。職責：

- **Build**：`build_payloads()` 從 DB 撈 4 份 JSON（today/stats/history/config），讀 /tmp 取 topflow
- **本機寫檔**：`write_local(payloads)` 寫到 `docs/data/*.json`
- **GitHub API**：
  - `_gh_request(method, path)`：通用 REST 包裝（Bearer token）
  - `_diag()`：診斷 token 與 repo 權限（log 用）
  - `_strip_volatile(obj)` + `_is_meaningful_change(old, new)`：移除 updated_at/queried_at 後比對是否實質變動
  - `push_file_to_github(repo_path, content_str, commit_msg)`：GET 現有內容 → 比對 → 內容無變動則跳過 PUT
  - `push_payloads(payloads)`：呼叫 `push_file_to_github`，分類計數（上傳/跳過/失敗）
- **快取**：`cache_top_flow(top_flow, screen_date_str)` 寫到 `/tmp/stockbot_topflow_cache.json`
- **入口**：`export_dashboard(top_flow=None, screen_date_str=None)` 由 run_analysis、settle_weekly、Bot 啟動時呼叫
- **環境變數**：`GITHUB_TOKEN` / `GITHUB_REPO` / `GITHUB_BRANCH` / `BOT_PUBLIC_URL` / `RAILWAY_PUBLIC_DOMAIN`

### 5. `requirements.txt`（4 行）
```
requests
pandas
PyNaCl
psycopg2-binary
```
不固定版本，用最新（保持輕量）。

### 6. `docs/index.html`（68KB / ~1100 行）
靜態 Web Dashboard。職責：

- **CSS**：clamp + rem 平滑 RWD（`:root font-size: clamp(13px, 3.5vw, 16px)`），41 處 clamp、24 處 rem
- **4 個分頁**：今日篩選、結算狀態、勝率統計、歷史紀錄
- **勝率統計分頁**：4 個 stat 卡（總篩選/進場率/1週勝率/2週勝率）+ SVG 折線圖（最近 26 次）+ 三種分組表
- **FAB 浮動按鈕**：5 個子鈕（策略參數一覽 / 外資買賣超榜 / 個股查詢 / 指標教學 / 追蹤清單）
- **Modal 系統**：點 backdrop / ✕ / ESC 關閉
- **個股查詢**：呼叫 Bot `/api/stock`，含 API URL localStorage 設定 + 強制重抓
- **追蹤清單**：localStorage 儲存（key: `stockbot_watchlist`）
- **資料載入**：每 5 分鐘 reload 5 個 JSON（today/stats/history/topflow/config）
- **Mobile 例外**：`@media (max-width: 480px)` 處理極窄螢幕的 modal kv 改單欄

### 7. `docs/data/*.json`（5 個檔）
Bot 自動產生，**不要手動編輯**。

| 檔案 | 內容 |
|------|------|
| `today.json`    | 當日篩選結果（records[]） |
| `stats.json`    | 累積總覽 + 等級/乖離/月份分組 + timeline {w1, w2} |
| `history.json`  | 最近 90 天紀錄（去重後） |
| `topflow.json`  | 外資買超/賣超 Top 10（每日 17:00 寫一次） |
| `config.json`   | Bot 公開 API URL（給前端 /api/stock 用） |

### 8. `docs/README.md`（1.4KB）
GitHub Pages 啟用步驟（Settings → Pages → Branch: main / Folder: /docs）。

### 9. `.gitignore`（73B）
忽略 `__pycache__/`、`*.pyc`、`*.pyo`、`.env`、`.venv/`、`backtest_data/`、`backtest_results.csv`。

---

## 二、目前的選股邏輯細節

每天 17:00 由 scheduler 觸發 `sb.run_analysis(attempt=0)`，流程：

### Step 1：抓資料
1. `get_target_date('auto')` 計算目標日期（17:00 後 = 今日；之前 = 前一交易日）
2. 平行抓取（threads）：
   - T86 法人買賣超（用 `fetch_t86_cached`，30 分鐘 TTL）
   - MI_INDEX 收盤價
   - 加權指數（`get_market_info`）
   - 大盤外資 3 日歷史
3. 失敗時 `_notify_all` 發 Discord「請手動 /run」訊息，return 'fail'

### Step 2：第一輪過濾（基本條件）
依序檢查每一檔（`for _, row in df.iterrows()`）：
1. 收盤價 ≥ `MIN_PRICE = 10`
2. 漲幅 ≥ `GRADE_A = 1.0` (1%)
3. 法人雙買超：外資 ≥ `MIN_FOREIGN_SHARE = 10000` AND 投信 ≥ `MIN_TRUST_SHARE = 10000`
   OR 單方買超合計 ≥ `MIN_INST_SHARE_SINGLE = 100000`
4. 取法人合計買超前 `MAX_CANDIDATES = 30` 名
5. 抽 `extract_top_flow(df, n=10)` 給 dashboard 用（買超/賣超 Top 10）

### Step 3：第二輪過濾（技術指標）
對每個候選股（`for idx_c, entry in enumerate(candidates)`）：
1. `build_history_fast(sid, months)` 抓 7 個月 K 棒
2. **限速退避**：若 `df_hist` empty 或 < 10 筆 → `consec_fails += 1`，達 `RATE_LIMIT_THRESHOLD = 3` 暫停 `RATE_LIMIT_BACKOFF_SEC = 60` 秒，重置計數
3. 量比 `calc_volume_ratio(df_hist, target_date)` ≥ `VOLUME_RATIO_MIN = 1.5`
4. EMA 多頭排列 `check_ema_bull(df_hist)`：主要 20>60>120，備援 10>20>60（資料 ≥ 60 筆）
5. 計算進階指標：`calc_bias_and_entry`、`calc_advanced_indicators`、`calc_macd`
6. `calc_chip_concentration(foreign, trust, volume_today)`
7. `count_consecutive_limit_ups(df_hist)`：≥ 3 日漲停 → 跑 `check_strong_chase`
8. `fetch_margin_change(sid, date_str)`：融資 5 日增幅

### Step 4：評分 + 分級
`score = calc_score(entry)`，依分數分配：
- score ≥ 85 → ss_list
- score ≥ 68 → s_list
- score ≥ 52 → a_list
- chase_mode='strong_chase' → chase_list（不論分數）
- chase_mode='watch' → watch_list（不論分數）
- < 52 且非 chase/watch → 淘汰

### Step 5：寫 DB + 推 Discord + 推 Dashboard
1. 各 list 依 score 降序排序（chase_list/watch_list 也排序）
2. `_db.save_screen_records(records, screen_date, guild_id)` 寫 DB（先 DELETE 同日 pending → INSERT）
3. 組裝 Discord 訊息（每段 ≤ 1900 字元自動分塊發送）
4. `fill_pending_t1_entries(today)` T+1 撮合昨天批次
5. `web_export.export_dashboard(top_flow=...)` 推 4+1 個 JSON

---

## 三、三層權重的實際配置

### 第一層：基本條件（硬過濾，全有全無）
| 條件 | 數值 | 來源 |
|------|------|------|
| 收盤價下限 | ≥ 10 元 | `MIN_PRICE` |
| 漲幅下限 | ≥ 1% | `GRADE_A` |
| 雙買超：外資門檻 | ≥ 10,000 股 | `MIN_FOREIGN_SHARE` |
| 雙買超：投信門檻 | ≥ 10,000 股 | `MIN_TRUST_SHARE` |
| 單方買超門檻 | 合計 ≥ 100,000 股 | `MIN_INST_SHARE_SINGLE` |
| 候選保留數量 | 前 30 名 | `MAX_CANDIDATES` |

### 第二層：技術過濾（硬過濾）
| 條件 | 數值 | 來源 |
|------|------|------|
| 量比下限 | ≥ 1.5x | `VOLUME_RATIO_MIN` |
| EMA 多頭排列（主要） | 20 > 60 > 120 | `check_ema_bull` |
| EMA 多頭排列（備援） | 10 > 20 > 60（資料 < 120 筆時） | 同上 |
| EMA 備援最少資料 | ≥ 60 筆 | `EMA_FALLBACK_MIN` |

### 第三層：評分權重（軟過濾，加總分）

#### 主項目（總分 105）
| 項目 | 配分 | 區間 / 規則 |
|------|------|------------|
| **漲幅** | **10** | 3~5%=10、2~3%=8、5~7%=7、1~2%=5、>7%=3（漲停難進場） |
| **量比** | **20** | ≥3x=20、≥2x=15、≥1.5x=10、≥1.2x=5 |
| **法人買超強度** | **20** | 雙買超+合計 ≥50 萬股=20、雙買超+ ≥10 萬股=15、雙買超=10、單方 ≥10 萬股=8、其他=3 |
| **乖離率** | **20** | 0~3%=20、<0%=18、3~5%=15、5~8%=5、>8%=0（過熱） |
| **RSI** | **10** | 60~80=10、50~60=7、>80=5（過熱）、<50=0 |
| **壓力位** | **10** | 無明顯壓力=10、接近壓力=4、其他=0 |
| **位階** | **5** | 偏低=5、中=3、偏高=1 |
| **MACD** | **10** | 黃金交叉+DIF>0=10、黃金交叉+DIF≤0=7、DIF>DEA+Hist擴張=8、DIF>DEA+Hist萎縮=5、DIF<DEA=0 |

#### 加減項
| 項目 | 範圍 | 規則 |
|------|------|------|
| **籌碼集中度** | 0 ~ +8 | 法人淨買超/成交量；≥20%=+8、≥10%=+5、≥5%=+2、其他=0 |
| **大盤環境** | +3 ~ -5 | 加權外資今日 >100 億=+3、<-100 億=-5、連3日賣超 500 億 → suspend=True 直接放棄 |
| **融資增幅** | +3 ~ -8 | 5 日 >+30%=-8（散戶追高）、>+15%=-4、≥0%=0、<0%=+3（籌碼健康） |
| **連續買超** | +0 (停用) | `consec_score` 永遠是 0（資料不可靠移除） |

#### 等級門檻
| 等級 | 分數 | 倉位（依乖離） |
|------|------|----------------|
| **SS** | ≥ 85 | 乖離 ≤5%: **25%**、≤8%: **15%**、>8%: **0%** |
| **S** | ≥ 68 | 乖離 ≤5%: **15%**、≤8%: **10%**、>8%: **0%** |
| **A** | ≥ 52 | 乖離 ≤5%: **10%**、≤8%: **5%**、>8%: **0%** |
| 淘汰 | < 52 | — |

#### 強勢追漲特殊規則（連續 ≥3 日漲停才檢查）
| 5 項條件 | 數值 |
|---------|------|
| 法人合計買超 | ≥ 200,000 股 |
| 量比 | ≥ 2.0x |
| 籌碼集中度 | ≥ 10%（chip_score ≥ 5） |
| MACD | DIF > DEA 且 Histogram 擴張中 |
| 大盤環境分數 | ≥ 0 |

| 通過數 | chase_mode | 進場區間 | T+1 撮合 |
|--------|-----------|---------|---------|
| **5/5** | strong_chase | [close × 1.00, close × 1.07] | 跳空跌破 → 不接刀 |
| **4/5** | watch | NULL（不撮合） | fill_status='watch'，永不結算 |
| **<4/5** | 跳過 | — | 不寫入 DB |

#### 進場 / 結算數值
| 項目 | 數值 |
|------|------|
| 一般股進場區下限 | close × 0.97 |
| 一般股進場區上限 | close × 1.00 |
| 強勢追漲下限 | close × 1.00 |
| 強勢追漲上限 | close × 1.07 |
| 目標 1 | actual_entry × 1.05 (+5%) |
| 目標 2 | actual_entry × 1.10 (+10%) |
| 停損 | actual_entry × 0.95 (-5%) |
| 第一次結算 | 進場日 → 下個週五 |
| 第二次結算 | 進場日 → 再下個週五 |
| 觸停損 settle_pct | 強制 -5% |

---

## 四、評分系統的因子（資料來源 → 計算 → 分數）

| 因子 | 資料來源 | 計算函式 | 對應評分 |
|------|---------|---------|---------|
| 漲幅 | MI_INDEX 漲跌價差 / 前收盤 | run_analysis 內的 `change` | 主項 10 |
| 量比 | STOCK_DAY 成交量 | `calc_volume_ratio(df, date)` | 主項 20 |
| 法人買超 | T86 idx=4 (外資) + idx=10 (投信) | run_analysis 內的 `foreign + trust` | 主項 20 |
| 乖離率 | STOCK_DAY 收盤 + MA10 | `calc_bias_and_entry(df, price)` | 主項 20 |
| RSI(14) | STOCK_DAY 收盤 | `calc_rsi(closes, 14)` | 主項 10 |
| 壓力位 | STOCK_DAY 60 日 high | `calc_advanced_indicators` 內 | 主項 10 |
| 位階 | STOCK_DAY 60 日 high/low | 同上 | 主項 5 |
| MACD | STOCK_DAY 收盤 + EMA(12)/(26)/(9) | `calc_macd(df)` | 主項 10 |
| ATR(14) 動態停損 | STOCK_DAY high/low/close | `calc_atr(df)`、`adv['atr_stop']` | 顯示用，不計分 |
| OBV | STOCK_DAY close + volume | `calc_obv(df)` | 顯示用，不計分 |
| 籌碼集中度 | T86 (foreign + trust) / STOCK_DAY volume | `calc_chip_concentration(...)` | 加分 0~+8 |
| 大盤環境 | MI_QFIIS 3 日外資合計 | `calc_market_env(history)` | 加減 +3~-5 |
| 融資增幅 | MI_MARGN 今日 vs 5 日前 | `calc_margin_score(today, 5d)` | 加減 +3~-8 |
| 連續漲停 | STOCK_DAY 連續日漲幅 ≥9.5% | `count_consecutive_limit_ups(df)` | 觸發 chase_mode |
| 5 項追漲門檻 | 法人 / 量比 / 籌碼 / MACD / 大盤 | `check_strong_chase(...)` | 決定 chase_mode |

### 失敗 / 缺值的預設分數
| 因子 | 缺值 | 預設加分 |
|------|------|---------|
| 乖離率 | bias=None（資料 < 10 筆） | +10 |
| RSI | rsi=None（資料 < 20 筆） | +5 |
| MACD | 資料 < 35 筆 | +5（macd_score） |
| 籌碼集中度 | volume=0 | +0 |
| 大盤環境 | history < 3 日 | +0 |
| 融資增幅 | margin_5d_ago=None | +0 |

---

## 五、待辦清單

### 高優先（明顯問題）
- [ ] **觀察 5/5 17:00 自動跑的結果** — 驗證限速退避 + cache 是否解決今天「歷史資料不足」問題
- [ ] **觀察 dashboard 是否還會被 redeploy 迴圈** — 已加 `_is_meaningful_change`，預期不再被觸發
- [ ] **/api/stock 在 Bot 升級後的可用性測試** — 用 `https://mystockbot.up.railway.app/api/stock?sid=2330` 直接測

### 中優先（功能完整性）
- [ ] **大盤外資 MI_QFIIS API 欄位解析驗證** — 抓出來的 `today / last3 / total_3d` 數值是否正確（目前未實際驗證）
- [ ] **震盪期 / 熊市的大盤趨勢過濾層** — 累積 4~6 週樣本後決定是否要加（例如 KD 死叉時降低 SS/S 倉位）
- [ ] **持倉成本 + 損益模擬** — `/holding` 目前用 FIFO 計算實現損益，但**不含手續費和證交稅**（買 0.1425% × 0.7 折、賣 0.1% + 0.3% 證交稅）
- [ ] **挑戰排行榜在 dashboard 顯示** — 目前 leaderboard 只在 Discord，可考慮搬到 dashboard

### 低優先（優化體驗）
- [ ] **個股查詢 K 線圖** — 用 TradingView widget 嵌入（dashboard FAB 第 6 個位置）
- [ ] **大盤摘要卡** — 加權指數 / 外資 3 日 / 漲停家數 / 漲跌家數
- [ ] **匯出 CSV** — 結算狀態 / 歷史紀錄能下載 CSV
- [ ] **個股訊號通知** — 追蹤清單裡的股被篩到時 Discord 通知
- [ ] **手動觸發 dashboard refresh** — Discord `/refresh_dashboard` 指令強制 push（繞過 skip-no-change）

### 觀察期（4~6 週後評估）
- [ ] **評分權重再調整** — 累積 100+ 樣本後看哪些項目和勝率正相關（`/stats` 提供統計依據）
- [ ] **進場區間調整** — 目前 normal=[-3%, 0%]、strong_chase=[0%, +7%]，看實際進場率調整
- [ ] **A 級門檻** — 目前 52 分，看 A 級勝率決定是否提高到 55 / 60
- [ ] **強勢追漲 5 項門檻調整** — 觀察追漲股的真實表現

---

## 六、重要設計決策（避免回頭走錯路）

1. **不自動重試 17:00**：失敗發 Discord，使用者手動 `/run`。原因：自動重試會在 TWSE 限速時連續打請求加重情況。
2. **DB 驅動的 scheduler 狀態**：`analysis_runs` 表記錄每日執行狀態。Bot 重啟後讀 DB 不會重複觸發。
3. **GitHub push 內容比對**：`_is_meaningful_change` 移除時間戳後比對。避免 dashboard JSON 只有 updated_at 改變就觸發 Railway redeploy。
4. **T86 共享快取**：30 分鐘 TTL。run_analysis / /api/stock / /topbuyer 三處共用，避免重複打 TWSE。
5. **K 棒分層快取**：當月 1 天 TTL（每日新增最後一筆 K 棒）、歷史月份 30 天 TTL（資料不變動）。減少 TWSE 流量 80%。
6. **限速自動退避**：連續 3 檔抓不到 → 暫停 60 秒。
7. **TWSE sleep 統一 0.8s**：每分鐘 ~75 次，落在 60~80 限速門檻內。
8. **schema_version 升級機制**：策略邏輯改動時把 `SCHEMA_VERSION` 換新值，自動 DROP 重建 `screen_records`。其他表（持倉、挑戰）不受影響。
9. **進場用實際 T+1 開盤**：而非「建議進場價」。確保結算的勝率是真實可達成的。
10. **目標停損用實際進場價**：actual_entry × 1.05/1.10/0.95，永遠在進場之上/下，不會出現「站著不動就贏」的 bug。

---

## 七、環境變數清單（Railway Variables）

| 變數 | 必填 | 範例值 |
|------|------|--------|
| `DISCORD_BOT_TOKEN`  | ✅ | `MTM2...` |
| `DISCORD_PUBLIC_KEY` | ✅ | `abc123...` |
| `DISCORD_APP_ID`     | ✅ | `1310...` |
| `DATABASE_URL`       | ✅ | Railway 自動注入 |
| `GITHUB_TOKEN`       | ✅ | Fine-grained PAT，contents read+write |
| `GITHUB_REPO`        | ⚪ | `znxuyz/my_stock_bot`（預設） |
| `GITHUB_BRANCH`      | ⚪ | `main`（預設） |
| `BOT_PUBLIC_URL`     | ✅ | `https://mystockbot.up.railway.app` |
| `DISCORD_WEBHOOK`    | ⚪ | 空可，多伺服器靠 DB |
| `RAILWAY_PUBLIC_DOMAIN` | ⚪ | Railway 自動注入（fallback） |
| `PORT`               | ⚪ | Railway 自動注入（預設 8080） |

---

## 八、版本資訊

| 項目 | 值 |
|------|-----|
| Schema 版本 | `v4-macd-chase` |
| Bot 最新 commit | `4e2bd23c`（main） |
| Dashboard 版本 | v3 ・ 進場區間 + 雙勝率（HTML 標題裡寫的） |
| Python | 3.12+ |
| Railway 服務 | loyal-cooperation |
| Railway 公開網址 | https://mystockbot.up.railway.app |
| GitHub Pages | https://znxuyz.github.io/my_stock_bot/ |
