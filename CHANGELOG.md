# Changelog

## v6.2.1 — KBAR cache 持久化檢查（patch）

非破壞性改動，只擴充 `/health` 健康檢查 + 增加部署文件。

- `/health` Embed 新增 **KBAR cache** 欄位：
  - 顯示 `config.KBAR_CACHE_DIR` 路徑
  - 檔案數 + 目錄大小（人類可讀）
  - persistent 狀態：路徑不在 `/tmp` 下標 ✅，在 `/tmp` 下標 ⚠️（重啟會清空）
  - 目錄不存在 → 顯示「尚未建立（首次 /run 後會自動產生）」
  - 權限錯誤 / 例外 → 標 ❌ 並顯示原因
- 非 persistent 路徑會讓整體 `/health` 標題降為 ⚠️，提醒 deploy 設定不完整
- 新增 README「Railway Volume（持久化 KBAR 快取）」段落：
  完整步驟 + 預期效果表（首次 ~5 min → 一週後 30~60s）
- 對應 pytest：`test_health_shows_kbar_cache_stats` /
  `test_health_warns_when_cache_in_tmp` /
  `test_health_handles_missing_cache_dir` / `test_health_handles_empty_cache_dir_env`
  / `test_human_bytes_formatting`（共 5 個新測試）
- `config.KBAR_CACHE_DIR` **沒改**（一直都是 env-driven），所以從 `/tmp`
  搬到 persistent volume 是純 Railway dashboard 設定，不需要 code change

部署步驟（一次性）：
1. Railway → my_stock_bot → Settings → Volumes → New Volume
   `name=kbar-cache, mount=/data/kbar_cache`
2. Railway → Variables → 新增 `KBAR_CACHE_DIR=/data/kbar_cache`
3. 自動 redeploy → `/health` 確認顯示 ✅ persistent

---

## v6.2 Phase 1 — Pure Stalker + 10pt scoring + 5-tier status

> ⚠️ **Breaking change**：`SCHEMA_VERSION = 'v62-pure-stalker-10pt'`，第一次啟動會
> `DROP screen_records` 並重建。v5 累積的篩選 / 結算 / 勝率資料會永久消失。
> 首次部署後 `daily_t86_history` 需累積 5 個交易日 Stalker 才會出現訊號，
> 第一週可能 0 標的，這是預期行為。

### Breaking changes 一覽

- **DB schema**：`SCHEMA_VERSION` 升到 `v62-pure-stalker-10pt`，`screen_records`
  DROP 重建，欄位由 v5 的 grade / score / chase_mode / entry_zone_* …
  改為 v6.2 的 flow_score / trend_score / heat_score / total_score / status /
  acc_buy_days / acc_cum_net / cum_5d_pct / bias_20 / vol_vs_60d / atr14 / exit_reason …
- **評分制**：v5 的 100+ 分制（calc_score）→ v6.2 的 10 分制
  （Flow 5 + Trend 3 + Heat 2 = total 0~10）
- **等級制**：v5 的 SS / S / A 三級 → v6.2 的五段
  MOMENTUM / ACTIVE / SETUP / WATCH / NOISE
- **MACD 參數**：v5 的 (12, 26, 9) → v6.2 的 (8, 17, 5)，全面替換包括 `/stock` 個股查詢
- **CHASE / WATCH 路徑**：v5 的「強勢追漲」與「觀察」分支全部移除；
  `chase.check_strong_chase` 仍保留簽名但永遠回 `passed=0`（標 @deprecated）
- **進場機制**：v5 的限價進場區間 → v6.2 的純市價
  （T+1 開盤直接 filled，一字漲停 / 跌停 missed）；
  `entry_zone.calc_entry_zone` 永遠回 `(None, None)`

### Strategy（內核）

- **第一輪過濾**：收盤 ≥ 10 元、漲幅 −1% ~ +3%、外資 OR 投信當日 > 0。
- **第一輪排序**：依「5 日累積法人淨買」DESC 取前 30（Phase 2 起改 5 日分數斜率）。
- **第二輪過濾**：量比 1.0~1.8x、量/60日 < 2.0、日成交金額 ≥ 5000 萬、
  5 日累積漲幅 −2% ~ +3%、5 日 high/low 振幅 < 5%、10 日內漲停 = 0、
  EMA 20 > 60、乖離 10MA ≤ 5%、乖離 20MA ≤ 3%、Stalker 累積（5 日法人買 ≥ 4 天且
  累積淨買 > 0）。任一不過直接淘汰。
- **評分**：10 分制 = Flow(5) + Trend(3) + Heat(2)。
- **狀態分級**：9-10 MOMENTUM、7-8 ACTIVE、5-6 SETUP、3-4 WATCH、0-2 NOISE。
  推播範圍 SETUP+；WATCH / NOISE 仍寫進 `daily_scores`。
- **倉位**：MOMENTUM 0%（不新進）/ ACTIVE 30% / SETUP 18% / WATCH 0% / NOISE 0%。
- **進場**：T+1 開盤市價（一字漲停 / 跌停 missed）。停損 Phase 1 沿用 −5%，Phase 2 改 ATR。

### MACD 換敏感版

全面 (12, 26, 9) → **(8, 17, 5)**，包括 `/stock` 個股查詢的 MACD 顯示。

### DB Schema

新表：
- `screen_records` — v6.2 結構（flow/trend/heat/total/status、acc_*、bias_20、vol_vs_60d、
  atr14、atr_stop_pct、exit_reason …）。**`DROP + CREATE`**。
- `daily_scores` — 每日全 enriched candidate 的 v6.2 分數（給 Phase 2 分數動能用）。`IF NOT EXISTS`。
- `daily_t86_history` — 法人歷史（給 Stalker 偵測讀）。`IF NOT EXISTS`。

### 已棄用但保留（標 `@deprecated`，仍可呼叫）

- `scoring.calc_score`（v5 105 分制）
- `chase.check_strong_chase`（CHASE 路徑已移除，永遠回 `passed=0`）
- `db.settle.calc_position_pct`（v5 等級制倉位，永遠回 0.0；改用 `scoring.status_to_position_pct`）
- `entry_zone.calc_entry_zone`（限價區間已移除，永遠回 `(None, None)`）

### 已移除

- `stock_bot.py` shim 不再 re-export `GRADE_SS` / `GRADE_S` / `GRADE_A` 三個常數
- v5 評分流程的所有 SS/S/A 分桶、CHASE / WATCH 路徑、`_ENSURE_V5_COLUMNS_SQL`

### UI

- Discord 卡片：5 段狀態 emoji + `N/10` 分數 + Flow/Trend/Heat 子分數 + 累積天數 +「進場：市價」
- `/stock`：加 v6.2 Stalker 條件 ✅/❌ 逐項檢查 + v6.2 評分區 + 五星推薦度（`total_score / 2`）
- Dashboard：統計卡 3 → 5 張、欄位由 grade / chase_mode 改 status / 累積天數、
  分數欄改 `N/10`、進場區改「市價」灰字、策略參數面板換 v6.2 內容

### Tests

新增：`test_score_v62`、`test_classify_status`、`test_daily_scores`（mocked）、
`test_accumulation`、`test_macd_8175`、`test_indicators_v62`、`test_stalker_filter_full`。

更新：`test_chase`（驗證 deprecated reject）、`test_entry_zone`（驗證一律 (None, None)）、
`test_filter_first_round`（驗證 v6.2 漲幅與法人 OR 規則）、`test_settle`（純市價 + 一字 missed）、
`test_imports`（v6.2 export + 確認 GRADE_* 已移除）。

### Phase 2 預留（**本次未實作**）

- ATR 停損 + 收盤判定
- 時間停損（15 個交易日未達 +5% 全出）
- 移動停利（max_close × 0.93）
- OVERHEAT 出場（Heat=0 + 爆量長紅）
- 排序鍵改 5 日分數斜率 velocity
- Discord 卡片新增「分數軌跡」欄

---

## v5（pre-v6.2）

最後一版策略：100+ 分制、SS/S/A 等級、CHASE/WATCH 路徑、限價進場區間、固定 ±5%/+10%/−5% 出場。
詳見 git history。
