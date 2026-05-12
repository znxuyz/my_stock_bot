# PROJECT_STATUS.md

> 最後更新：2026-05-12
> Schema 版本：`v4-macd-chase` + v5 追蹤欄位（ALTER TABLE 補上）
> 部署狀態：main 上 commit `cf6d05f`，Bot 服務 https://mystockbot.up.railway.app
> 重大改動：4 個大檔重構成 30+ 模組 + logging / DB 重試 / pytest + CI / 進場區間 v5 +
>           5/5 篩選 KeyError critical fix + 5/7 T86 parse 誤判假日 critical fix +
>           logging stdout/stderr 分流 + PWA icon / manifest（「量化篩選系統」）

---

## 一、目前的模組結構

從原本 4 個大檔（4607 行）拆成依功能分檔的模組架構：

### 進入點 / 通用工具
| 檔案 | 職責 |
|------|------|
| `bot.py` | 進入點（~70 行）：env 檢查 → init_db → register_commands → 啟動 scheduler / startup_export thread → ThreadingHTTPServer.serve_forever |
| `config.py` | **集中所有環境變數 / 策略常數 / 路徑**，避免散落各處 |
| `logging_setup.py` | 全域 logging 設定（時間戳 + level + 模組名稱）；**DEBUG/INFO → stdout、WARNING/ERROR/CRITICAL → stderr**（Railway log UI 才能正確分色）；`LOG_LEVEL` env 控制 |
| `time_utils.py` | `tw_now / get_target_date / prev_months / next_friday / roc_to_date` |
| `format_utils.py` | `fmt_share / fmt_share_signed / star_str / get_opt`（Discord interaction 取值） |

### TWSE 抓資料層
| 檔案 | 職責 |
|------|------|
| `twse_http.py` | `safe_get`（重試 + log）/ `safe_read_csv` / `clean_sid` / `find_col`；SSL 驗證可由 `TWSE_VERIFY_SSL` env 控制 |
| `twse_kbar.py` | 月 K 棒抓取與本機快取。`_fetch_full_kbars` 為單一底層（6 欄完整版），`fetch_stock_day_fast`（無 open）與 `fetch_kbars_with_open`（有 open）共用同一份快取 → 同檔同月只打 TWSE 一次 |
| `twse_t86.py` | T86 三大法人買賣超 + 30 分鐘共享快取 |
| `twse_market.py` | MI_INDEX 加權指數 + MI_QFIIS 大盤外資歷史 |
| `twse_margin.py` | MI_MARGN 融資餘額 5 日增幅 |

### 策略 / 指標
| 檔案 | 職責 |
|------|------|
| `indicators.py` | `calc_ema / check_ema_bull / calc_volume_ratio / calc_rsi / calc_atr / calc_obv（向量化）/ calc_macd / calc_bias_and_entry` |
| `advanced_indicators.py` | `calc_advanced_indicators`：RSI 評分 / ATR 停損 / 壓力位 / 位階 / OBV |
| `scoring.py` | `calc_score / calc_market_env / calc_margin_score / calc_chip_concentration` |
| `chase.py` | `count_consecutive_limit_ups / check_strong_chase`（5 項追漲門檻） |
| `topflow.py` | `extract_top_flow`：外資買賣超 Top N（給 dashboard 與 /topbuyer 用） |
| `entry_zone.py` | **v5 新增**：`calc_entry_zone(close, mode, grade)` 為所有進場區間的單一入口（DB 寫入 / Discord 訊息 / /stock 顯示三處共用） |

### 撮合 / 分析主流程
| 檔案 | 職責 |
|------|------|
| `matching.py` | T+1 撮合：`get_t1_kbar / get_period_kbars / fill_pending_t1_entries` |
| `analysis.py` | `run_analysis`：盤後分析主流程（爬資料 → 篩選 → 評分 → 推 Discord → 寫 DB → 推 dashboard） |

### DB 套件（`db/`）
原 db.py 拆 9 個檔，`db/__init__.py` 重新匯出全部 API → 對外 `import db` 介面不變。

| 檔案 | 職責 |
|------|------|
| `db/conn.py` | `get_conn()`：psycopg2.OperationalError 自動退避重試 5/15/30s 三次；`is_available()`：應用層先檢查 |
| `db/schema.py` | `init_db`：schema 版本不符 DROP 重建；最後永遠跑 `_ENSURE_V5_COLUMNS_SQL`（IF NOT EXISTS 冪等）→ v4 升 v5 不掉資料 |
| `db/guilds.py` | `set_guild_webhook / get_all_webhooks / get_guild_webhook / remove_guild` |
| `db/runs.py` | `analysis_runs` 表（record_run_start / record_run_end / can_run_today） |
| `db/screens.py` | `save_screen_records / get_records_needing_t1_check / get_total_screened`；entry_zone 從 `entry_zone.py` 取 |
| `db/settle.py` | T+1 撮合 + 結算寫入；`fill_t1_entry` v5 起額外存 `t1_open_price`；`get_missed_for_hypothetical / update_missed_hypothetical` 給 missed 假設結算用 |
| `db/stats.py` | 統計查詢；新增 `get_missed_hypothetical_stats`（跨 guild 彙總 missed 假設勝率） |
| `db/holdings.py` | 持倉 / FIFO 賣出 / pnl_summary |
| `db/challenges.py` | 選股挑戰 |

### Discord Bot 套件（`discord_bot/`）
原 bot.py 拆 11 個檔。

| 檔案 | 職責 |
|------|------|
| `discord_bot/__init__.py` | 重新匯出 InteractionHandler / register_commands / scheduler / LAST_RUN |
| `discord_bot/verify.py` | Ed25519 簽章驗證（`verify_signature`） |
| `discord_bot/handlers.py` | `InteractionHandler`：HTTP server + 指令分派；`update_last_run / get_last_run` 用 Lock 包裝（thread-safe）；DB 不可用時 `_safe_call` 回友善訊息 |
| `discord_bot/register.py` | `register_commands` 註冊 slash commands |
| `discord_bot/scheduler.py` | 排程：平日 17:00 分析 / 週五 18:00 結算 / 週五 21:00 挑戰結算；啟動 90 秒緩衝 |
| `discord_bot/content.py` | 純資料：FORTUNES / ROASTS（運勢與川普語錄） |
| `discord_bot/basic_commands.py` | `cmd_help / cmd_fortune / cmd_roast / cmd_poll` |
| `discord_bot/stock_commands.py` | `analyze_stock_data / format_stock_text / stock_api_get / fetch_top_traders / get_latest_price` |
| `discord_bot/portfolio_commands.py` | `cmd_holding / cmd_buy / cmd_sell / cmd_leaderboard` |
| `discord_bot/challenge_commands.py` | `cmd_challenge / settle_challenge` |
| `discord_bot/stats_commands.py` | `cmd_report / cmd_stats` |
| `discord_bot/settle.py` | `settle_weekly`（包含 missed 紀錄假設結算補算） |

### Web Dashboard 匯出
| 檔案 | 職責 |
|------|------|
| `web_export.py` | `build_payloads / write_local / push_payloads / export_dashboard`；stats.json v5 起多 `missed_hypo` 欄位 |
| `docs/index.html` | 靜態 dashboard（4 分頁 + FAB + Modal）；`<title>` 為「**量化篩選系統**」，`<head>` 含完整 icon / `manifest.json` / Apple meta / `theme-color #7FD4C1`。**尚未顯示 missed_hypo 資料**（資料已寫入 stats.json，UI 待加） |
| `docs/manifest.json` | PWA manifest（standalone display，`#FFF5EC` 背景） |
| `docs/apple-touch-icon-180/152/120.png` | iOS Add to Home Screen icon |
| `docs/favicon-32x32.png` / `favicon-16x16.png` / `favicon.ico` | 桌面 / 舊瀏覽器分頁 icon（ico 含 16/32/48 多尺寸） |
| `docs/android-chrome-192x192.png` / `512x512.png` | Android PWA 主屏 / splash maskable |
| `docs/data/*.json` | Bot 自動產生的 5 個 JSON（today / stats / history / topflow / config） |
| `icon.png`（根目錄）| PWA 圖示原始檔（1024×1024 RGB 不透明，edge-replicate padding 無白邊；重切時用） |

### 向下相容 shim
| 檔案 | 職責 |
|------|------|
| `stock_bot.py` | 舊呼叫者 `import stock_bot as sb` 仍可用，把所有公開 API 重新匯出 |

### 測試 / CI
| 檔案 | 職責 |
|------|------|
| `tests/` | 12 個測試檔，87 個 pytest 測試（indicators / scoring / chase / settle / entry_zone / topflow / time_utils / imports / LAST_RUN 並發 / logging_setup 路由 / filter_first_round regression / t86 holiday vs parse-fail regression） |
| `.github/workflows/test.yml` | 每個 push / PR 跑 pyflakes + pytest（Python 3.11 與 3.12 雙版本） |
| `requirements.txt` | 鎖版號：`requests>=2.31,<3` `pandas>=2.0,<3` `PyNaCl>=1.5,<2` `psycopg2-binary>=2.9,<3` |

---

## 二、目前的選股邏輯細節

每天 17:00 由 `discord_bot/scheduler.py` 觸發 `analysis.run_analysis()`，流程：

### Step 1：抓資料
1. `get_target_date('auto')` 計算目標日期（17:00 後 = 今日；之前 = 前一交易日）
2. 依序抓取：
   - 加權指數（`get_market_info`）
   - 大盤外資 3 日歷史（`fetch_market_foreign_history`）
   - T86 法人買賣超（`fetch_t86_cached`，30 分鐘 TTL）
   - MI_INDEX 收盤價
3. 大盤外資連 3 日賣超 ≥500 億 → 直接 `suspend`，發 Discord 通知後 return 'success'
4. T86 / MI_INDEX 結果三分支（v5.3 起 `fetch_t86_cached` 明確拆兩種失敗）：
   - **抓取 / parse 失敗** → `df_i is None` → `'fail'` + Discord 通知，**不會被當假日吞掉**
   - **真假日**（TWSE 回「查詢無資料」）→ `df_i.empty` → `'holiday'` 靜默跳過
   - 成功 → 繼續走第一輪
   `parse_t86` 找不到表頭時會 log 前 500 字回應內容供 debug

### Step 2：第一輪過濾（基本條件）
`_filter_first_round` 對全部上市股票檢查（用 `to_dict('records')` 保留欄位名；
itertuples 會把 `'漲跌(+/-)'`、底線開頭欄位改名為位置別名 `_4` 等，曾在 5/5
17:00 造成全部 1000+ 列 KeyError → 0 檔通過篩選，已寫 regression 測試擋住）：
1. 收盤價 ≥ `MIN_PRICE = 10`
2. 漲幅 ≥ `GRADE_A = 1.0` (1%)
3. 法人雙買超：外資 ≥ `MIN_FOREIGN_SHARE = 10000` AND 投信 ≥ `MIN_TRUST_SHARE = 10000`
   OR 單方買超合計 ≥ `MIN_INST_SHARE_SINGLE = 100000`
4. 取法人合計買超前 `MAX_CANDIDATES = 30` 名

`extract_top_flow(df, n=10)` 抽外資買超 / 賣超 Top 10 給 dashboard 用。

### Step 3：第二輪過濾（技術指標）
對每個候選股：
1. `build_history_fast(sid, months)` 抓 7 個月 K 棒（共用 `_fetch_full_kbars`）
2. **限速退避**：empty 或 < 10 筆 → `consec_fails += 1`，達 `RATE_LIMIT_THRESHOLD = 3` 暫停 60 秒
3. 量比 ≥ `VOLUME_RATIO_MIN = 1.5`
4. EMA 多頭排列 `check_ema_bull(df_hist)`：主要 20>60>120，備援 10>20>60（資料 ≥ 60 筆）
5. `_enrich_candidate` 計算所有進階指標（bias / adv / macd / chip / margin / consec / chase_mode）

### Step 4：評分 + 分級（v5 進場區間）
`score = calc_score(entry)`，由 `_classify` 分配到對應 list：
- `chase_mode == 'strong_chase'` → chase_list
- `chase_mode == 'watch'` → watch_list
- `chase_mode == 'reject'`（連漲停但條件不夠）→ 跳過
- score ≥ 85 → ss_list
- score ≥ 68 → s_list
- score ≥ 52 → a_list
- < 52 → 淘汰

### Step 5：寫 DB + 推 Discord + 推 Dashboard
1. 各 list 依 score 降序排序
2. `db.save_screen_records` 寫每個 guild（DELETE 同日 pending → INSERT）
3. 組裝 Discord 訊息（每段 ≤ 1900 字元自動分塊）
4. `fill_pending_t1_entries(today)` T+1 撮合昨天批次（v5：filled / missed 都寫入 `t1_open_price`）
5. `web_export.export_dashboard(top_flow=...)` 推 4+1 個 JSON

---

## 三、三層權重的實際配置

### 第一層：基本條件（硬過濾）
| 條件 | 數值 |
|------|------|
| 收盤價下限 | ≥ 10 元 |
| 漲幅下限 | ≥ 1% |
| 雙買超：外資門檻 | ≥ 10,000 股 |
| 雙買超：投信門檻 | ≥ 10,000 股 |
| 單方買超門檻 | 合計 ≥ 100,000 股 |
| 候選保留數量 | 前 30 名 |

### 第二層：技術過濾（硬過濾）
| 條件 | 數值 |
|------|------|
| 量比下限 | ≥ 1.5x |
| EMA 多頭排列（主要） | 20 > 60 > 120 |
| EMA 多頭排列（備援） | 10 > 20 > 60（資料 < 120 筆時） |
| EMA 備援最少資料 | ≥ 60 筆 |

### 第三層：評分權重（軟過濾，加總分）

#### 主項目（總分 105）
| 項目 | 配分 | 區間 / 規則 |
|------|------|------------|
| 漲幅 | 10 | 3~5%=10、2~3%=8、5~7%=7、1~2%=5、>7%=3 |
| 量比 | 20 | ≥3x=20、≥2x=15、≥1.5x=10、≥1.2x=5 |
| 法人買超強度 | 20 | 雙買超+合計 ≥50 萬股=20、雙買超+ ≥10 萬股=15、雙買超=10、單方 ≥10 萬股=8、其他=3 |
| 乖離率 | 20 | 0~3%=20、<0%=18、3~5%=15、5~8%=5、>8%=0 |
| RSI | 10 | 60~80=10、50~60=7、>80=5、<50=0 |
| 壓力位 | 10 | 無明顯壓力=10、接近壓力=4、其他=0 |
| 位階 | 5 | 偏低=5、中=3、偏高=1 |
| MACD | 10 | 黃金交叉+DIF>0=10、黃金交叉+DIF≤0=7、DIF>DEA+Hist擴張=8、Hist萎縮=5、DIF<DEA=0 |

#### 加減項
| 項目 | 範圍 | 規則 |
|------|------|------|
| 籌碼集中度 | 0 ~ +8 | 法人淨買超/成交量；≥20%=+8、≥10%=+5、≥5%=+2 |
| 大盤環境 | +3 ~ -5 | 加權外資今日 >100 億=+3、<-100 億=-5、連3日賣超 500 億 → suspend |
| 融資增幅 | +3 ~ -8 | 5 日 >+30%=-8、>+15%=-4、≥0%=0、<0%=+3 |
| 連續買超 | +0 (停用) | 資料來源不可靠移除 |

#### 等級門檻
| 等級 | 分數 | 倉位（依乖離） |
|------|------|----------------|
| SS | ≥ 85 | 乖離 ≤5%: **25%**、≤8%: **15%**、>8%: 0% |
| S  | ≥ 68 | 乖離 ≤5%: **15%**、≤8%: **10%**、>8%: 0% |
| A  | ≥ 52 | 乖離 ≤5%: **10%**、≤8%: **5%**、>8%: 0% |
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
| 5/5 | strong_chase | [close × 1.00, close × 1.07] | 跳空跌破 → 不接刀 |
| 4/5 | watch | NULL（不撮合） | fill_status='watch'，永不結算 |
| <4/5 | reject | — | 不寫入 DB |

#### 進場 / 結算數值（v5 已更新）
| 項目 | 數值 |
|------|------|
| **normal SS 級進場區下限** | close × 0.97 |
| **normal SS 級進場區上限** | **close × 1.03**（v5 新增；容忍 3% 跳空） |
| **normal 其他級進場區下限** | close × 0.97 |
| **normal 其他級進場區上限** | **close × 1.02**（v5：原本一律 1.00） |
| 強勢追漲下限 | close × 1.00 |
| 強勢追漲上限 | close × 1.07 |
| 目標 1 | actual_entry × 1.05 (+5%) |
| 目標 2 | actual_entry × 1.10 (+10%) |
| 停損 | actual_entry × 0.95 (-5%) |
| 第一次結算 | 進場日 → 下個週五 |
| 第二次結算 | 進場日 → 再下個週五 |
| 觸停損 settle_pct | 強制 -5% |

> **v5 進場區間放寬動機**：原本 normal mode 上緣一律 `× 1.00`，跳空高開不回測就 missed → 強勢股最容易被排除，統計勝率被低估。新版讓 SS / 其他級各放寬 3% / 2%。

---

## 四、評分系統的因子（資料來源 → 計算 → 分數）

| 因子 | 資料來源 | 計算函式 | 對應評分 |
|------|---------|---------|---------|
| 漲幅 | MI_INDEX 漲跌價差 / 前收盤 | `_filter_first_round` 內的 `change` | 主項 10 |
| 量比 | STOCK_DAY 成交量 | `calc_volume_ratio(df, date)` | 主項 20 |
| 法人買超 | T86 idx=4 (外資) + idx=10 (投信) | `_filter_first_round` 內的 `foreign + trust` | 主項 20 |
| 乖離率 | STOCK_DAY 收盤 + MA10 | `calc_bias_and_entry(df, price)` | 主項 20 |
| RSI(14) | STOCK_DAY 收盤 | `calc_rsi(closes, 14)` | 主項 10 |
| 壓力位 | STOCK_DAY 60 日 high | `calc_advanced_indicators` 內 | 主項 10 |
| 位階 | STOCK_DAY 60 日 high/low | 同上 | 主項 5 |
| MACD | STOCK_DAY 收盤 + EMA(12)/(26)/(9) | `calc_macd(df)` | 主項 10 |
| ATR(14) 動態停損 | STOCK_DAY high/low/close | `calc_atr(df)` / `adv['atr_stop']` | 顯示用，不計分 |
| OBV（向量化）| STOCK_DAY close + volume | `calc_obv(df)` | 顯示用，不計分 |
| 籌碼集中度 | T86 (foreign + trust) / STOCK_DAY volume | `calc_chip_concentration(...)` | 加分 0~+8 |
| 大盤環境 | MI_QFIIS 3 日外資合計 | `calc_market_env(history)` | 加減 +3~-5 |
| 融資增幅 | MI_MARGN 今日 vs 5 日前 | `fetch_margin_change(...)` | 加減 +3~-8 |
| 連續漲停 | STOCK_DAY 連續日漲幅 ≥9.5% | `count_consecutive_limit_ups(df)` | 觸發 chase_mode |
| 5 項追漲門檻 | 法人 / 量比 / 籌碼 / MACD / 大盤 | `check_strong_chase(...)` | 決定 chase_mode |

---

## 五、v5 missed 反向統計（量化「保守過頭損失多少」）

`screen_records` 表 v5 新增 3 個欄位（用 `ALTER TABLE IF NOT EXISTS` 加上去 → 保留歷史資料）：

| 欄位 | 寫入時機 |
|------|---------|
| `t1_open_price` | T+1 撮合時，**filled / missed 都寫入**（給後續假設結算用） |
| `missed_settle1_close` | 週五 18:00 settle_weekly round 1 補算 missed 紀錄時寫入 |
| `missed_settle1_pct` | 同上：`(settle_close − t1_open) / t1_open × 100` |

`db.get_missed_hypothetical_stats()` 跨 guild 彙總：
- 總筆數、勝率（漲的 / 總筆數）、平均報酬、最佳 / 最差
- 各等級 break-down（SS / S / A / CHASE / WATCH）

寫進 `stats.json.missed_hypo`。dashboard HTML 之後加 UI 即可顯示。

> **真實結算（settle1_pct）只算 filled 紀錄；missed 假設結算寫在獨立欄位 `missed_settle1_pct`，不影響真實勝率統計。**

---

## 六、待辦清單

### 高優先（明顯問題）
- [ ] **觀察 v5 進場區間實際效果** — 跑 2~4 週後比對 missed 比例 vs 之前
- [ ] **觀察 missed_hypo 統計** — 累積樣本後評估保守度成本
- [ ] **dashboard HTML 顯示 missed_hypo** — 資料已寫入 stats.json，UI 等樣本累積後再加（沒資料的卡片不好看）

### 中優先（功能完整性）
- [ ] **大盤外資 MI_QFIIS API 欄位解析驗證** — 抓出來的 `today / last3 / total_3d` 數值是否正確
- [ ] **震盪期 / 熊市的大盤趨勢過濾層** — 累積 4~6 週樣本後決定（例如 KD 死叉時降低 SS/S 倉位）
- [ ] **持倉成本含手續費 + 證交稅** — `/holding` 目前 FIFO 不含手續費（買 0.1425% × 0.7 折、賣 0.1% + 0.3% 證交稅）
- [ ] **挑戰排行榜在 dashboard 顯示** — 目前 leaderboard 只在 Discord

### 低優先（優化體驗）
- [ ] **個股查詢 K 線圖** — 用 TradingView widget 嵌入（dashboard FAB 第 6 個位置）
- [ ] **大盤摘要卡** — 加權指數 / 外資 3 日 / 漲停家數 / 漲跌家數
- [ ] **匯出 CSV** — 結算狀態 / 歷史紀錄能下載 CSV
- [ ] **個股訊號通知** — 追蹤清單裡的股被篩到時 Discord 通知
- [ ] **手動觸發 dashboard refresh** — Discord `/refresh_dashboard` 指令強制 push

### 觀察期（4~6 週後評估）
- [ ] **評分權重再調整** — 累積 100+ 樣本後看哪些項目和勝率正相關
- [ ] **進場區間進一步放寬** — 若 missed_hypo 顯示 SS 仍很多漲超 5% 的，可考慮 SS 放到 1.05、其他級放到 1.03
- [ ] **A 級門檻** — 目前 52 分，看 A 級勝率決定是否提高到 55 / 60
- [ ] **強勢追漲 5 項門檻調整** — 觀察追漲股的真實表現

---

## 七、重要設計決策（避免回頭走錯路）

1. **不自動重試 17:00**：失敗發 Discord，使用者手動 `/run`。原因：自動重試會在 TWSE 限速時連續打請求加重情況。
2. **DB 驅動的 scheduler 狀態**：`analysis_runs` 表記錄每日執行狀態。Bot 重啟後讀 DB 不會重複觸發。
3. **GitHub push 內容比對**：`_is_meaningful_change` 移除時間戳後比對，避免 dashboard JSON 只有 updated_at 改變就觸發 Railway redeploy 迴圈。
4. **T86 共享快取**：30 分鐘 TTL。run_analysis / /api/stock / /topbuyer 三處共用。
5. **K 棒分層快取（v5 統一）**：`_fetch_full_kbars` 一次抓 6 欄完整版，`fetch_stock_day_fast`（無 open）和 `fetch_kbars_with_open`（有 open）共用同一份快取。當月 1 天 TTL、歷史月份 30 天 TTL。
6. **限速自動退避**：連續 3 檔抓不到 → 暫停 60 秒。
7. **TWSE sleep 統一 0.8s**：每分鐘 ~75 次。
8. **schema_version 升級機制**：策略邏輯改動時 DROP 重建 `screen_records`；**v5 起額外用 `ALTER TABLE IF NOT EXISTS` 補欄位 → 不掉資料**。
9. **進場用實際 T+1 開盤**：而非「建議進場價」。
10. **目標停損用實際進場價**：`actual_entry × 1.05/1.10/0.95`。
11. **進場區間集中管理（v5）**：所有用到進場區間的地方都呼叫 `entry_zone.calc_entry_zone(close, mode, grade)`，避免散落各處各改。
12. **logging 全面換掉 print（v5）**：可由 `LOG_LEVEL=DEBUG` 控制細度，errors 帶完整 traceback。
13. **DB 連線重試**：`get_conn` 在 `OperationalError` 時自動退避 5/15/30s 重試三次，Railway DB 短暫斷線不會炸掉整個分析。
14. **LAST_RUN thread-safe**：scheduler thread 與 handler thread 透過 `update_last_run / get_last_run` 加鎖存取，不再依賴 GIL atomic dict。
15. **graceful degradation**：DB 不可用時 handler 用 `_safe_call` 回友善訊息，而非直接 500。
16. **DataFrame 不用 itertuples 讀含特殊字元欄位**：`'漲跌(+/-)'`、底線開頭欄位會被 pandas 改成位置別名 `_4` 之類，造成 `row_dict['原欄位']` KeyError。`_filter_first_round` / `fetch_top_traders` / `extract_top_flow` 全改用 `to_dict('records')`，regression 測試擋住此 case。
17. **T86 / MI_INDEX「真假日」與「parse 失敗」必須區分**：`fetch_t86_cached` 真假日（TWSE 回「查詢無資料」）回 empty DataFrame（且快取）；parse 失敗（TWSE 回了不能解析的內容如限速錯誤頁）**回 `None` 且不快取**。analysis 對 `None` 視為 `'fail'` 通知用戶；對 empty 才當 `'holiday'` 靜默跳過。5/7 那次就是 parse 失敗被誤判假日靜默吞掉，已有 regression 測試。
18. **logging stdout / stderr 分流**：DEBUG / INFO → stdout（Railway 一般顏色），WARNING / ERROR / CRITICAL → stderr（Railway 紅色）。讓「紅字 = 真的要看的問題」，雜訊比恢復正常。`logging_setup.setup_logging` 用 `_MaxLevelFilter` 把 INFO 留 stdout，stderr handler 從 WARNING 起。
19. **PWA icon 用 edge-replicate 滿版**：原圖 4 角不能有 vignette 淺白漸層，否則 iOS 圓角 mask 切下去就是視覺白邊。處理流程：zoom 1.18× → center crop 中央 942×942 → `np.pad mode='edge'` 41px 四周到 1024×1024。對比驗證見 `docs/icon-before-after.png`。

---

## 八、環境變數清單（Railway Variables）

| 變數 | 必填 | 說明 |
|------|------|------|
| `DISCORD_BOT_TOKEN`  | ✅ | Discord Bot Token |
| `DISCORD_PUBLIC_KEY` | ✅ | Discord App Public Key（簽章驗證用） |
| `DISCORD_APP_ID`     | ✅ | Discord App ID |
| `DATABASE_URL`       | ✅ | Railway 自動注入 |
| `GITHUB_TOKEN`       | ✅ | Fine-grained PAT，contents read+write |
| `GITHUB_REPO`        | ⚪ | `znxuyz/my_stock_bot`（預設） |
| `GITHUB_BRANCH`      | ⚪ | `main`（預設） |
| `BOT_PUBLIC_URL`     | ✅ | `https://mystockbot.up.railway.app` |
| `DISCORD_WEBHOOK`    | ⚪ | 空可，多伺服器靠 DB |
| `RAILWAY_PUBLIC_DOMAIN` | ⚪ | Railway 自動注入（fallback） |
| `PORT`               | ⚪ | Railway 自動注入（預設 8080） |
| `LOG_LEVEL`          | ⚪ | `INFO`（預設）/ `DEBUG`（除錯時）；DEBUG/INFO 走 stdout、WARNING+ 走 stderr |
| `TWSE_VERIFY_SSL`    | ⚪ | `0`（預設關閉，TWSE 偶爾憑證錯誤）/ `1`（強制驗證） |

---

## 九、版本資訊

| 項目 | 值 |
|------|-----|
| Schema 版本 | `v4-macd-chase` + v5 追蹤欄位 |
| Bot 最新 commit | `cf6d05f`（main） |
| 重大改動 | 模組化重構 + logging / DB 重試 / pytest+CI / 進場區間 v5 |
| Dashboard 版本 | v3 ・ 進場區間 + 雙勝率 |
| Python | 3.11+（CI 跑 3.11 與 3.12） |
| 測試覆蓋 | pytest 87 個測試（12 個檔） |
| Railway 服務 | loyal-cooperation |
| Railway 公開網址 | https://mystockbot.up.railway.app |
| GitHub Pages | https://znxuyz.github.io/my_stock_bot/ |

---

## 十、開發 / 測試流程

### 本機跑測試（87 個 pytest case）
```bash
pip install -r requirements.txt
pip install pytest pyflakes Pillow numpy   # Pillow / numpy 給 PWA icon 切圖用
python -m pytest tests/ -v
python -m pyflakes *.py db/*.py discord_bot/*.py tests/*.py
```

### 模擬 boot（不需設真實 env）
```bash
DISCORD_BOT_TOKEN=dummy DISCORD_PUBLIC_KEY=$(printf 'a%.0s' {1..64}) DISCORD_APP_ID=dummy python -c "
import bot
class FakeServer:
    def __init__(self, *a, **kw): pass
    def serve_forever(self): print('boot ok')
bot.ThreadingHTTPServer = FakeServer
bot.scheduler = lambda: None
bot.register_commands = lambda: None
bot.main()
"
```

### CI（GitHub Actions）
每個 push 與 PR 自動跑 `.github/workflows/test.yml`：
- pyflakes（無 unused import / undefined name）
- pytest（Python 3.11 與 3.12 雙版本）
