# Alpha Radar Strategy v6.2 — Pure Stalker + 10-Point Scoring

> 對應 GitHub repo: `znxuyz/my_stock_bot`  
> **v6.2 = v6.1 純 Stalker 篩選 + 新 10 分制評分系統（Flow 5 + Trend 3 + Heat 2）+ NOISE/WATCH/SETUP/ACTIVE/MOMENTUM 五段分級 + 分數動能追蹤**
>
> 預計這是策略層面的最後一次大改版。Phase 1 上線後除非實盤跑出明顯結構性問題，否則不再動策略本身，只調整參數和補實作。
>
> **Breaking changes vs v5：**
> - DB schema 升 `v62-pure-stalker-10pt` → DROP screen_records 重建（歷史資料清空）
> - 評分制 100 → 10 分
> - 等級 SS/S/A → NOISE/WATCH/SETUP/ACTIVE/MOMENTUM
> - MACD 全面 (12,26,9) → (8,17,5)
> - CHASE / WATCH 路徑整個移除
> - 進場區間改純市價
> - 停損改 ATR + 收盤判定（Phase 2）

---

## 0. 設計哲學

### 對手是散戶，不是法人

我們不和高頻、不和量化基金、不和券商自營搶速度。散戶的資訊管道是 PTT / Dcard / Line 群 / 新聞 / 熱門股排行——**這些訊號發生時，我們應該已經在裡面了**。

### 進場時機

**法人正在累積、但散戶還沒看見** = 價格安靜、量能溫和、新聞稀少、但 T86 顯示法人連日小買。**不是**「法人今天大買 → 跟」（已同步進場），**不是**「價格突破前高 → 追」（散戶已開始注意）。

### 出場時機

**法人停止累積、但散戶開始衝進來** = 法人連續轉中性 / 賣超、社群討論度上升、量能爆出、新聞密度上升。趨勢線是輔助訊號。

### 「找不到標的」是 feature

純 Stalker 策略下，某些日子會 0 標的。**這是策略在告訴你今天沒有好的累積 setup，不要動。** 真正的法人安靜累積一週可能只出現 3~7 個。少而精，不要 FOMO。

### 核心洞察：「分數持續上升」比「分數高」重要

> 真正重要的不是「分數高」，而是「分數持續上升」。
> 這通常代表：資金開始部署 / 趨勢正在形成 / 市場開始注意。

這是 v6.2 最大的差異化設計。其他人都在做單日 snapshot，我們做時間序列。Phase 1 開始累積每日全 candidate 分數，Phase 2 起用「5 日分數斜率」當主排序鍵。

### 不變量（不能動的東西）

| 介面 | 規格 |
|------|------|
| Discord 17:00 推播 | 加權指數 + 5 段分區 + 指標說明 |
| Dashboard 四分頁 | 今日 / 結算 / 勝率 / 歷史 |
| /run /stock /buy /sell /holding | 全部保留（內部邏輯換新） |
| T+1 撮合 + W1/W2 週五結算 | 保留 |
| missed_hypothetical 追蹤 | 保留 |
| PWA「量化篩選系統」 | 保留 |
| MAX_CANDIDATES | 維持 30 |
| 五星推薦度（/stock） | 保留（從 10 分制 / 2 推算）|

### 變量（要動的東西）

- 篩選邏輯（純 Stalker）
- 評分系統（10 分制）
- 等級制度（5 段）
- 進場機制（市價）
- 停損 / 停利 / 時間停損 / OVERHEAT 出場
- MACD 參數 (8,17,5)
- DB schema 升版重建

---

## 1. 完整策略規格

### 1.1 Stalker 訊號定義

#### 必要條件（缺一不可，第一輪 + 第二輪硬過濾）

| 條件 | 範圍 | 解讀 |
|------|------|------|
| 收盤價 | ≥ 10 元 | 排除雞蛋水餃股 |
| 日成交金額 | ≥ 5000 萬 | 流動性夠 |
| 當日漲幅 | −1% ~ +3% | 今天沒爆發 |
| 過去 5 日法人淨買日數 | ≥ 4/5 | 連續累積 |
| 過去 5 日累積淨買 | > 0 股 | 整體在加碼 |
| 過去 5 日累積漲幅 | −2% ~ +3% | 價格沒動 |
| 過去 5 日 high/low 振幅 | < 5% | 盤整中 |
| 今日量比（vs 5 日均量） | 1.0 ~ 1.8 | 量能溫和 |
| 今日量 / 60 日均量 | < 2.0 | 量沒異常 |
| 乖離 10MA | ≤ 5% | 貼短均線 |
| 乖離 20MA | ≤ 3% | 貼中均線 |
| 10 日內漲停次數 | 0 | 完全沒過熱 |
| EMA 結構 | 20 > 60 | setup 還活 |

**為什麼這組條件**：Wyckoff accumulation 的價量籌碼特徵組合。任一破壞代表「不是 Stalker setup」。

#### 進場
- T+1 開盤**市價成交**
- 一字漲停開盤 = missed
- 跳空低開照常進場（這是好價格）

#### 停損（Phase 2 啟用，Phase 1 沿用 −5% 固定）
- ATR-based：max(1.5 × ATR14 / entry, 7%)
- **收盤判定**，盤中觸價不算

#### 停利（Phase 2 啟用）
- 第一目標 +5%：減碼 1/2、剩餘啟動移動停利
- 第二目標 +10%：全出
- 移動停利：max_close × 0.93 跌破 = 全出

#### 時間停損（Phase 2 啟用）
- 15 個交易日未到第一目標 → 收盤全出

#### OVERHEAT 出場（Phase 2 啟用）
- 持有期間任一日：Heat Score = 0 AND 當日漲幅 > 5% AND 今日量 / 60 日均量 > 3
- → 強制收盤全出（不管 ATR / 時間停損是否觸發）
- 邏輯：散戶開始進來了，我們就該出

### 1.2 10 分制評分系統

#### Flow Score（部署分數，max 5）

**目的**：判斷資金 / 法人累積強度。

| # | 條件 | 配分 |
|---|------|------|
| 1 | 過去 5 日法人連續買 = 5/5 天 | +2 |
| 2 | 5 日累積法人淨買量 ≥ 500K 股 | +2 |
| 3 | 籌碼集中度（外資+投信）/今日量 ≥ 10% | +1 |

**Stalker 適配說明**：原評分文件 Flow #1 是「今日成交金額 ≥ 20MA × 1.5」（量能爆出代表資金進場），但這在 Stalker 池子裡會被 filter 擋掉。改用「5/5 連續買」是更純粹的累積訊號——已通過 filter（4/5 最低）的標的裡，5/5 是更強訊號。

#### Trend Score（趨勢分數，max 3）

**目的**：確認趨勢是否形成。

| # | 條件 | 配分 |
|---|------|------|
| 1 | 收盤價 > 10MA | +1 |
| 2 | 10MA 5 日斜率 > 0（上彎） | +1 |
| 3 | MACD(8,17,5) DIF > DEA AND DIF > 0 | +1 |

#### Heat Score（熱度分數，max 2）

**目的**：判斷市場是否開始過熱。**冷門加分**。

Phase 1~2：價量代理。三個條件：

- A. 5 日累積漲幅 ≤ 2%
- B. 今日量 / 60 日均量 ≤ 1.3
- C. 乖離 20MA ≤ 2%

| 滿足條件數 | 狀態 | 配分 |
|-----------|------|------|
| 3 / 3 | 極冷（沒人在看） | +2 |
| 2 / 3 | 偏冷（開始有點動靜） | +1 |
| 0~1 / 3 | 已有熱度 | 0 |

Phase 3+：切換到真實資料源（PTT mention / 新聞密度 / Google Trends），介面相容。

#### Total Score = Flow + Trend + Heat（max 10）

### 1.3 五段分級

| 分數 | 狀態 | 特徵 | 操作建議 |
|------|------|------|----------|
| 9-10 | 🔵 MOMENTUM | 強勢主升、市場大量關注 | 已持有續抱、新進避免追高 |
| 7-8  | 🟢 ACTIVE   | 趨勢與資金同步、波段正式啟動 | **主進場區** |
| 5-6  | 🟡 SETUP    | 資金開始進場、趨勢開始形成 | 觀察拉回點、準備分批進場 |
| 3-4  | 🟠 WATCH    | 出現異常但未明確啟動 | 加入觀察名單、不交易 |
| 0-2  | 🔴 NOISE    | 無趨勢、無資金、無關注 | 不交易 |

**Discord / Dashboard 推播範圍**：分數 ≥ 5（SETUP 以上）才會在「今日篩選」分頁顯示和 Discord 推播。WATCH / NOISE 仍會寫進 `daily_scores` 表給「分數動能追蹤」用，但不打擾使用者。

**Stalker 池子裡的分數分佈預期**：
- 🟢 ACTIVE（7-8）：主要訊號，Flow 4-5 + Trend 2-3 + Heat 1-2
- 🟡 SETUP（5-6）：累積中但尚未完整
- 🔵 MOMENTUM（9-10）：極少見，因為 Stalker 過濾本來就排除已啟動的
- 🟠 WATCH / 🔴 NOISE：通常不會通過 Stalker filter，但 daily_scores 仍記錄

### 1.4 倉位建議

| 狀態 | 倉位 |
|------|------|
| 🔵 MOMENTUM | 不新進（持有續抱） |
| 🟢 ACTIVE | 30% |
| 🟡 SETUP | 18% |
| 🟠 WATCH | 不進 |
| 🔴 NOISE | 不進 |

MOMENTUM 不新進的原因：在 Stalker 哲學下，MOMENTUM 通常意味著「累積完成、突破發生、散戶開始注意」——這時候進已經慢了。如果你早就在 ACTIVE 時持有，那就續抱。

### 1.5 分數動能追蹤

每天 17:00 對通過 Stalker filter + enrich 的所有 candidate（不只 SETUP+）寫入 `daily_scores`。

**Phase 1：寫入但不讀**——累積資料。  
**Phase 2 起：讀取做「5 日分數斜率」當主排序鍵**：

```python
def calc_score_velocity_5d(sid, today):
    scores = db.fetch_recent_scores(sid, days=5, end_date=today)
    if len(scores) < 2:
        return 0
    # 5 日簡單線性斜率
    n = len(scores)
    x = list(range(n))
    y = [s['total_score'] for s in scores]
    x_mean, y_mean = sum(x)/n, sum(y)/n
    num = sum((x[i]-x_mean)*(y[i]-y_mean) for i in range(n))
    den = sum((x[i]-x_mean)**2 for i in range(n))
    return num / den if den != 0 else 0
```

**排序規則**：velocity DESC，total_score DESC 作為 tiebreaker。

**Discord 卡片新增「分數軌跡」欄**：例如 `2 → 3 → 5 → 6 → 7`，視覺化最近 5 天分數變化。

### 1.6 第一輪硬過濾（取代現行 `_filter_first_round`）

對所有 ≥ 10 元上市股，依序檢查（任一不過即淘汰）：

1. 收盤價 ≥ 10 元
2. 當日漲幅在 −1% ~ +3%
3. 今日法人單日有買（外資 > 0 或投信 > 0）

通過後依「5 日累積法人淨買量」DESC 取前 30 名。

### 1.7 第二輪硬過濾（取代現行 `_enrich_candidate`）

對前 30 名 candidate 依序檢查（任一不過即淘汰）：

1. 量比（vs 5 日均量）1.0 ~ 1.8
2. 今日量 / 60 日均量 < 2.0
3. 日成交金額 ≥ 5000 萬
4. 5 日累積漲幅 −2% ~ +3%
5. 5 日 high/low 振幅 < 5%
6. 10 日內漲停次數 = 0
7. EMA 20 > EMA 60
8. 乖離 10MA ≤ 5%
9. 乖離 20MA ≤ 3%
10. Stalker 累積偵測：5 日法人買 ≥ 4 天 且累積淨買 > 0
11. 計算 10 分制評分 + 分級

通過第 10 道的標的進入評分階段，依分數分到 5 段。

---

## 2. 實作分階段

> 每個 Phase 都要：(1) pytest 全綠、(2) 通過 acceptance、(3) 至少觀察 2~4 週實盤資料、(4) 才進下一階段。

### Phase 1：篩選重寫 + 10 分制評分 + 5 段分級 + Schema 升版

**目標**：把整個策略內核換掉。一上線就是純 Stalker + 10 分制 + 5 段分級。停損 / 時間停損 / OVERHEAT 出場留到 Phase 2。

#### DB Schema 升版（Breaking）

`config.py`：
```python
SCHEMA_VERSION = 'v62-pure-stalker-10pt'  # 觸發 DROP + 重建
```

`db/schema.py`：
```sql
-- 篩選結果（取代 v5 結構）
DROP TABLE IF EXISTS screen_records CASCADE;
CREATE TABLE screen_records (
    id SERIAL PRIMARY KEY,
    sid VARCHAR(20) NOT NULL,
    name VARCHAR(50),
    screen_date DATE NOT NULL,
    guild_id VARCHAR(50) NOT NULL,

    -- v6.2 評分（10 分制）
    flow_score INTEGER,
    trend_score INTEGER,
    heat_score INTEGER,
    total_score INTEGER,
    status VARCHAR(15),  -- NOISE/WATCH/SETUP/ACTIVE/MOMENTUM

    -- Stalker 累積資料
    acc_buy_days INTEGER,
    acc_cum_net BIGINT,
    cum_5d_pct NUMERIC(6,2),
    bias_20 NUMERIC(6,2),
    vol_vs_60d NUMERIC(6,2),

    -- 進場
    close_price NUMERIC(10,2),
    fill_status VARCHAR(20),  -- pending/filled/missed
    actual_entry_date DATE,
    actual_entry_price NUMERIC(10,2),
    t1_open_price NUMERIC(10,2),

    -- 出場（Phase 2 填）
    actual_target1 NUMERIC(10,2),
    actual_target2 NUMERIC(10,2),
    actual_stop_loss NUMERIC(10,2),
    atr14 NUMERIC(10,4),
    atr_stop_pct NUMERIC(6,3),
    max_close_since_entry NUMERIC(10,2),
    time_stop_date DATE,
    exit_reason VARCHAR(20),  -- target1/target2/stop/trailing/time/overheat

    -- 結算
    settle1_date DATE,
    settle1_price NUMERIC(10,2),
    settle1_pct NUMERIC(6,2),
    settle1_done BOOLEAN DEFAULT FALSE,
    settle2_date DATE,
    settle2_price NUMERIC(10,2),
    settle2_pct NUMERIC(6,2),
    settle2_done BOOLEAN DEFAULT FALSE,

    -- missed hypothetical
    missed_settle1_close NUMERIC(10,2),
    missed_settle1_pct NUMERIC(6,2),

    -- meta
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_screen_sid_date ON screen_records (sid, screen_date DESC);
CREATE INDEX idx_screen_guild_date ON screen_records (guild_id, screen_date DESC);

-- 每日全 candidate 分數（給「分數動能追蹤」用）
CREATE TABLE IF NOT EXISTS daily_scores (
    sid VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    flow_score INTEGER,
    trend_score INTEGER,
    heat_score INTEGER,
    total_score INTEGER,
    status VARCHAR(15),
    PRIMARY KEY (sid, date)
);
CREATE INDEX idx_ds_sid_date ON daily_scores (sid, date DESC);

-- 法人歷史
CREATE TABLE IF NOT EXISTS daily_t86_history (
    sid VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    foreign_net BIGINT,
    trust_net BIGINT,
    dealer_net BIGINT,
    PRIMARY KEY (sid, date)
);
CREATE INDEX idx_t86_sid_date ON daily_t86_history (sid, date DESC);
```

#### 改動清單

**檔案 1：`config.py`**

```python
# ─────────── Schema ───────────
SCHEMA_VERSION = 'v62-pure-stalker-10pt'

# ─────────── Stalker 過濾條件 ───────────
STALKER_DAYS              = 5
STALKER_MIN_BUY_DAYS      = 4
STALKER_MAX_CUM_CHANGE    = 3.0
STALKER_MIN_CUM_CHANGE    = -2.0
STALKER_MAX_PRICE_RANGE   = 5.0
STALKER_MAX_TODAY_CHANGE  = 3.0
STALKER_MIN_TODAY_CHANGE  = -1.0
STALKER_VOL_RATIO_MIN     = 1.0
STALKER_VOL_RATIO_MAX     = 1.8
STALKER_MAX_VOL_VS_60D    = 2.0
STALKER_MAX_BIAS_10       = 5.0
STALKER_MAX_BIAS_20       = 3.0
STALKER_MAX_LIMIT_UPS_10D = 0

# ─────────── 流動性 + 持有 ───────────
MIN_DAILY_AMOUNT      = 50_000_000
MAX_HOLD_DAYS_STALKER = 15  # Phase 2 啟用

# ─────────── 評分門檻 ───────────
SCORE_MOMENTUM = 9   # 9-10
SCORE_ACTIVE   = 7   # 7-8
SCORE_SETUP    = 5   # 5-6
SCORE_WATCH    = 3   # 3-4
# 0-2 = NOISE
SCORE_PUSH_MIN = SCORE_SETUP  # 推播門檻

# ─────────── MACD（v6.2 全面換敏感版） ───────────
MACD_FAST   = 8
MACD_SLOW   = 17
MACD_SIGNAL = 5

# ─────────── Heat 代理門檻 ───────────
HEAT_PROXY_CUM5D    = 2.0   # 5 日累積漲幅 ≤
HEAT_PROXY_VOL60D   = 1.3   # 量 / 60 日均量 ≤
HEAT_PROXY_BIAS20   = 2.0   # 乖離 20MA ≤

# ─────────── 已棄用（v6.2 移除） ───────────
# GRADE_SS / GRADE_S / GRADE_A
# VOLUME_RATIO_MIN（用 STALKER_VOL_RATIO_MIN/MAX）
# MIN_FOREIGN_SHARE / MIN_TRUST_SHARE / MIN_INST_SHARE_SINGLE（Stalker filter 取代）
```

**檔案 2：`indicators.py`** — MACD 換參數 + 新增 helpers

```python
def calc_macd(df, fast=None, slow=None, signal=None):
    """v6.2 預設用 config 設定值 (8,17,5)。"""
    fast = fast or config.MACD_FAST
    slow = slow or config.MACD_SLOW
    signal = signal or config.MACD_SIGNAL
    # 其餘邏輯不變


def calc_5d_cumulative_change(df):
    if len(df) < 6: return None
    closes = df['close'].astype(float)
    return round((closes.iloc[-1] / closes.iloc[-6] - 1) * 100, 2)


def calc_5d_price_range(df):
    if len(df) < 5: return None
    recent = df.tail(5)
    h, l = recent['high'].astype(float).max(), recent['low'].astype(float).min()
    return round((h / l - 1) * 100, 2) if l > 0 else None


def count_limit_ups_in_window(df, window=10, threshold=9.5):
    if len(df) < window + 1: return 0
    closes = df['close'].astype(float).values
    cnt = 0
    for i in range(len(closes) - window, len(closes)):
        if i <= 0: continue
        if (closes[i] - closes[i-1]) / closes[i-1] * 100 >= threshold:
            cnt += 1
    return cnt


def calc_bias_20(df):
    if len(df) < 20: return None
    ma20 = df['close'].astype(float).tail(20).mean()
    if ma20 == 0: return None
    return round((df['close'].astype(float).iloc[-1] - ma20) / ma20 * 100, 2)


def calc_ma_slope_5d(df, period=10):
    """N 日 MA 的近 5 日斜率（>0 = 上彎）。"""
    if len(df) < period + 5: return None
    closes = df['close'].astype(float)
    ma = closes.rolling(period).mean()
    recent_ma = ma.dropna().tail(5).values
    if len(recent_ma) < 5: return None
    return float(recent_ma[-1] - recent_ma[0])


def calc_daily_amount(df):
    if df.empty: return 0
    return float(df['close'].iloc[-1]) * float(df['volume'].iloc[-1]) * 1000


def calc_vol_vs_60d_ratio(df):
    if len(df) < 61: return None
    avg60 = df['volume'].astype(float).tail(61).iloc[:-1].mean()
    if avg60 == 0: return None
    return round(float(df['volume'].iloc[-1]) / avg60, 2)
```

**檔案 3：`twse_t86.py`** — 多日抓取 + 寫 history

```python
def fetch_t86_multi_day(date_str, days=5):
    """抓 date_str 為基準的過去 N 個交易日 T86。"""
    from time_utils import prev_trading_days
    dates = prev_trading_days(date_str, n=days)
    result = {}
    for d in dates:
        df = fetch_t86_cached(d)
        if not df.empty:
            result[d] = df
    return result


def save_t86_to_history(date_str, df_t86):
    """寫進 daily_t86_history（UPSERT）。"""
    if df_t86.empty: return
    from datetime import datetime
    d = datetime.strptime(date_str, '%Y%m%d').date()
    rows = [(r.get('sid_clean', ''), d,
             int(r.get('_foreign', 0) or 0),
             int(r.get('_trust', 0) or 0),
             int(r.get('_dealer', 0) or 0))
            for r in df_t86.to_dict('records')]
    sql = """
    INSERT INTO daily_t86_history (sid, date, foreign_net, trust_net, dealer_net)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (sid, date) DO UPDATE SET
        foreign_net = EXCLUDED.foreign_net,
        trust_net   = EXCLUDED.trust_net,
        dealer_net  = EXCLUDED.dealer_net
    """
    # executemany


def get_inst_history(sid, days=5, end_date=None):
    """從 daily_t86_history 撈 N 日法人淨買，回 list[(date, net)] 由舊到新。"""
    from datetime import date
    end_date = end_date or date.today()
    sql = """
    SELECT date, foreign_net + COALESCE(trust_net, 0) AS net
    FROM daily_t86_history
    WHERE sid = %s AND date <= %s
    ORDER BY date DESC LIMIT %s
    """
    # fetch, reverse, return
```

**檔案 4：`accumulation.py`** — Stalker 核心偵測（新檔）

```python
"""法人前哨累積偵測。"""
import config


def detect_stalker_setup(df_hist, inst_history):
    N = config.STALKER_DAYS
    if len(df_hist) < N + 1 or len(inst_history) < N:
        return {'is_stalker': False, 'reason': 'insufficient_data'}

    recent = df_hist.tail(N)
    closes = recent['close'].astype(float)
    cum_change = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
    price_range = (recent['high'].astype(float).max() /
                   recent['low'].astype(float).min() - 1) * 100

    nets = [n for _, n in inst_history[-N:]]
    buy_days = sum(1 for n in nets if n > 0)
    cum_net = sum(nets)

    is_stalker = (
        buy_days >= config.STALKER_MIN_BUY_DAYS and
        cum_net > 0 and
        config.STALKER_MIN_CUM_CHANGE <= cum_change <= config.STALKER_MAX_CUM_CHANGE and
        price_range < config.STALKER_MAX_PRICE_RANGE
    )

    return {
        'is_stalker': is_stalker,
        'buy_days': buy_days,
        'cum_net': cum_net,
        'cum_change_pct': round(cum_change, 2),
        'price_range_pct': round(price_range, 2),
    }
```

**檔案 5：`scoring.py`** — 全面改 10 分制

整個檔案大改寫：

```python
"""v6.2 10 分制評分系統。"""
import config
from indicators import (
    calc_5d_cumulative_change, calc_ma_slope_5d, calc_bias_20,
    calc_vol_vs_60d_ratio, calc_ema,
)


def calc_flow_score(entry, df_hist):
    """Flow Score：max 5。"""
    score = 0
    # 1. 5 日連續買 = 5/5
    if entry.get('acc_buy_days', 0) >= 5:
        score += 2
    # 2. 5 日累積淨買 ≥ 500K
    if entry.get('acc_cum_net', 0) >= 500_000:
        score += 2
    # 3. 籌碼集中度 ≥ 10%
    foreign = entry.get('foreign', 0)
    trust = entry.get('trust', 0)
    vol_today = int(df_hist['volume'].iloc[-1]) if not df_hist.empty else 0
    if vol_today > 0:
        conc = (max(0, foreign) + max(0, trust)) / vol_today * 100
        if conc >= 10:
            score += 1
    return score


def calc_trend_score(df_hist, entry):
    """Trend Score：max 3。"""
    score = 0
    if len(df_hist) < 20:
        return 0
    closes = df_hist['close'].astype(float)
    ma10 = closes.rolling(10).mean().iloc[-1]
    today_close = float(closes.iloc[-1])

    # 1. 收盤 > 10MA
    if today_close > ma10:
        score += 1
    # 2. 10MA 上彎
    slope = calc_ma_slope_5d(df_hist, period=10)
    if slope is not None and slope > 0:
        score += 1
    # 3. MACD(8,17,5)：DIF > DEA AND DIF > 0
    macd = entry.get('macd_info') or {}
    dif = macd.get('dif')
    dea = macd.get('dea')
    if dif is not None and dea is not None and dif > dea and dif > 0:
        score += 1
    return score


def calc_heat_score(df_hist, entry):
    """Heat Score：max 2。Phase 1~2 用價量代理。"""
    cum5d = entry.get('cum_5d_pct')
    vol60 = entry.get('vol_vs_60d')
    bias20 = entry.get('bias_20')

    if cum5d is None or vol60 is None or bias20 is None:
        return 0

    cond_a = cum5d <= config.HEAT_PROXY_CUM5D
    cond_b = vol60 <= config.HEAT_PROXY_VOL60D
    cond_c = bias20 <= config.HEAT_PROXY_BIAS20

    n = sum([cond_a, cond_b, cond_c])
    if n == 3: return 2
    if n == 2: return 1
    return 0


def calc_score_v62(entry, df_hist):
    """v6.2 完整評分。回傳 dict 含子分數 / 總分 / 狀態。"""
    flow = calc_flow_score(entry, df_hist)
    trend = calc_trend_score(df_hist, entry)
    heat = calc_heat_score(df_hist, entry)
    total = flow + trend + heat
    return {
        'flow_score': flow,
        'trend_score': trend,
        'heat_score': heat,
        'total_score': total,
        'status': classify_status(total),
    }


def classify_status(total):
    """0-10 → 5 段分級。"""
    if   total >= config.SCORE_MOMENTUM: return 'MOMENTUM'
    elif total >= config.SCORE_ACTIVE:   return 'ACTIVE'
    elif total >= config.SCORE_SETUP:    return 'SETUP'
    elif total >= config.SCORE_WATCH:    return 'WATCH'
    return 'NOISE'


def status_to_emoji(status):
    return {
        'MOMENTUM': '🔵', 'ACTIVE': '🟢', 'SETUP': '🟡',
        'WATCH': '🟠', 'NOISE': '🔴',
    }.get(status, '⚪')


def status_to_position_pct(status):
    """v6.2 倉位建議（不再用 grade 函式）。"""
    return {
        'MOMENTUM': 0,    # 不新進
        'ACTIVE':   30,
        'SETUP':    18,
        'WATCH':    0,
        'NOISE':    0,
    }.get(status, 0)


# v5 函式 deprecated（留著向下相容）
def calc_score(entry):
    """v5 105 分制。v6.2 起 deprecated，仍保留給 unit test 用。"""
    # 原邏輯保留
    ...


def calc_market_env(*args, **kwargs):
    """保留沒變。"""
    ...
```

**檔案 6：`analysis.py`** — 主流程改寫

```python
def _filter_first_round_v62(df, df_i, col_close, col_diff, col_sign):
    """v6.2 第一輪：價格 + 漲幅 + 法人單日有買。"""
    candidates = []
    for row_dict in df.to_dict('records'):
        try:
            sid = row_dict['sid_clean']
            name = str(row_dict.get('證券名稱',
                       list(row_dict.values())[1])).strip()
            price = pd.to_numeric(str(row_dict[col_close]).replace(',', ''),
                                  errors='coerce')
            diff = pd.to_numeric(str(row_dict[col_diff]).replace(',', ''),
                                 errors='coerce')
            if pd.isna(price) or pd.isna(diff) or price < config.MIN_PRICE:
                continue
            if col_sign:
                s = str(row_dict[col_sign])
                diff = -abs(diff) if ('−' in s or s.strip() == '-') else abs(diff)
            change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0

            # v6.2 漲幅雙向硬擋
            if not (config.STALKER_MIN_TODAY_CHANGE <= change <= config.STALKER_MAX_TODAY_CHANGE):
                continue

            inst_row = df_i[df_i['sid_clean'] == sid]
            if inst_row.empty:
                continue
            foreign = float(inst_row['_foreign'].values[0])
            trust = float(inst_row['_trust'].values[0])
            if foreign <= 0 and trust <= 0:
                continue

            candidates.append({
                'sid': sid, 'name': name,
                'price': price, 'change': change,
                'foreign': int(foreign), 'trust': int(trust),
                'total': int(foreign + trust),
            })
        except Exception:
            pass
    return candidates


def _enrich_candidate_v62(entry, df_hist, target_date, market_env, date_str, inst_hist):
    """v6.2 第二輪 + 評分。任一硬條件不過回 None。"""
    sid = entry['sid']

    # 第二輪硬過濾
    vol_ratio = calc_volume_ratio(df_hist, target_date)
    if not (config.STALKER_VOL_RATIO_MIN <= vol_ratio <= config.STALKER_VOL_RATIO_MAX):
        return None
    entry['vol_ratio'] = vol_ratio

    vol60_ratio = calc_vol_vs_60d_ratio(df_hist)
    if vol60_ratio is not None and vol60_ratio > config.STALKER_MAX_VOL_VS_60D:
        return None
    entry['vol_vs_60d'] = vol60_ratio

    if calc_daily_amount(df_hist) < config.MIN_DAILY_AMOUNT:
        return None

    cum5d = calc_5d_cumulative_change(df_hist)
    if cum5d is None or not (config.STALKER_MIN_CUM_CHANGE <= cum5d <= config.STALKER_MAX_CUM_CHANGE):
        return None
    entry['cum_5d_pct'] = cum5d

    if (pr := calc_5d_price_range(df_hist)) is None or pr > config.STALKER_MAX_PRICE_RANGE:
        return None

    if count_limit_ups_in_window(df_hist, 10) > config.STALKER_MAX_LIMIT_UPS_10D:
        return None

    closes = df_hist['close'].astype(float)
    if len(df_hist) < 60: return None
    if calc_ema(closes, 20).iloc[-1] <= calc_ema(closes, 60).iloc[-1]:
        return None

    bias_info = calc_bias_and_entry(df_hist, entry['price'])
    if not bias_info or bias_info['bias_pct'] > config.STALKER_MAX_BIAS_10:
        return None
    entry['bias'] = bias_info

    bias20 = calc_bias_20(df_hist)
    if bias20 is None or bias20 > config.STALKER_MAX_BIAS_20:
        return None
    entry['bias_20'] = bias20

    from accumulation import detect_stalker_setup
    setup = detect_stalker_setup(df_hist, inst_hist)
    if not setup['is_stalker']:
        return None
    entry['acc_buy_days'] = setup['buy_days']
    entry['acc_cum_net'] = setup['cum_net']

    # 評分
    entry['macd_info'] = calc_macd(df_hist)
    score_result = calc_score_v62(entry, df_hist)
    entry.update(score_result)

    return entry


def run_analysis(...):
    # ... 抓資料

    # v6.2 新增：抓多日 T86 + 寫 history
    fetch_t86_multi_day(date_str, days=config.STALKER_DAYS)
    save_t86_to_history(date_str, df_i)

    # 第一輪
    candidates = _filter_first_round_v62(df, df_i, col_close, col_diff, col_sign)

    # v6.2 排序鍵：5 日累積淨買 DESC（Phase 2 起改 velocity）
    def _sort_key(c):
        h = get_inst_history(c['sid'], days=5, end_date=target_date)
        return sum(n for _, n in h) if h else 0

    candidates.sort(key=_sort_key, reverse=True)
    candidates = candidates[:config.MAX_CANDIDATES]

    # 第二輪 + 評分
    results = []
    for entry in candidates:
        inst_hist = get_inst_history(entry['sid'], days=5, end_date=target_date)
        df_hist = build_history_fast(entry['sid'], months)
        if df_hist.empty: continue
        enriched = _enrich_candidate_v62(entry, df_hist, target_date,
                                          market_env, date_str, inst_hist)
        if enriched is not None:
            results.append(enriched)

    # v6.2：所有 enriched candidate 都寫進 daily_scores（不只 SETUP+）
    for r in results:
        db.save_daily_score(
            sid=r['sid'], date=target_date,
            flow=r['flow_score'], trend=r['trend_score'],
            heat=r['heat_score'], total=r['total_score'],
            status=r['status'],
        )

    # 分到 5 個 list
    momentum, active, setup, watch, noise = [], [], [], [], []
    for r in results:
        {'MOMENTUM': momentum, 'ACTIVE': active, 'SETUP': setup,
         'WATCH': watch, 'NOISE': noise}[r['status']].append(r)

    # 寫 screen_records（只寫 SETUP 以上，WATCH/NOISE 不入主表）
    push_list = momentum + active + setup
    for r in push_list:
        db.save_screen_records(...)

    # Discord 推播：只推 SETUP+
    # Dashboard JSON：只匯出 SETUP+
```

**檔案 7：`chase.py`** — 整個移除策略層使用

```python
def count_consecutive_limit_ups(df, threshold=9.5):
    """保留函式，v6.2 起不再被呼叫（給歷史相容）。"""
    # 原邏輯

def check_strong_chase(*args, **kwargs):
    """v6.2 起 deprecated，永遠回 reject。"""
    return {'passed': 0, 'checks': [], 'reasons': ['v6.2 已移除 CHASE 路徑']}
```

**檔案 8：`entry_zone.py`** — 純市價

```python
def calc_entry_zone(close, *args, **kwargs):
    """v6.2：永遠市價。回 (None, None)。"""
    return None, None
```

**檔案 9：`db/settle.py` 的 `determine_t1_fill`**

```python
def determine_t1_fill(t1_open, t1_high, t1_low, zone_low=None, zone_high=None, **kwargs):
    """v6.2 純市價成交：T+1 開盤直接 filled，一字漲跌停 missed。"""
    if t1_open is None:
        return 'missed', None
    o = float(t1_open)
    if t1_high is not None and t1_low is not None:
        if abs(float(t1_high) - o) < 0.01 and abs(float(t1_low) - o) < 0.01:
            return 'missed', None
    return 'filled', round(o, 2)
```

**檔案 10：`db/__init__.py` + `db/schema.py` + 新 `db/scores.py`**

新模組 `db/scores.py`：

```python
"""daily_scores 表的存取。"""
from psycopg2.extras import RealDictCursor
from db.conn import get_conn


def save_daily_score(sid, date, flow, trend, heat, total, status):
    sql = """
    INSERT INTO daily_scores (sid, date, flow_score, trend_score,
                              heat_score, total_score, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (sid, date) DO UPDATE SET
        flow_score = EXCLUDED.flow_score,
        trend_score = EXCLUDED.trend_score,
        heat_score = EXCLUDED.heat_score,
        total_score = EXCLUDED.total_score,
        status = EXCLUDED.status
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (sid, date, flow, trend, heat, total, status))
        conn.commit()


def fetch_recent_scores(sid, days=5, end_date=None):
    sql = """
    SELECT date, flow_score, trend_score, heat_score, total_score, status
    FROM daily_scores
    WHERE sid = %s AND date <= %s
    ORDER BY date DESC LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (sid, end_date, days))
            return list(reversed(cur.fetchall()))  # 由舊到新
```

`db/__init__.py` 加上 `from db.scores import save_daily_score, fetch_recent_scores`。

**檔案 11：`format_utils.py` + Discord 卡片**

```python
def fmt_status_block(entry):
    """v6.2 卡片格式。"""
    emoji = status_to_emoji(entry['status'])
    sid = entry['sid']
    name = entry['name']
    total = entry['total_score']
    flow = entry['flow_score']
    trend = entry['trend_score']
    heat = entry['heat_score']
    acc_days = entry.get('acc_buy_days', 0)

    return (
        f"{emoji} **{entry['status']}** ({total}/10) {sid} {name}\n"
        f"分數明細：Flow {flow}/5 · Trend {trend}/3 · Heat {heat}/2\n"
        f"累積：{acc_days}/5 天買、漲幅 {entry['change']:+.2f}%、"
        f"量比 {entry['vol_ratio']:.1f}x、乖離 {entry['bias']['bias_pct']:+.2f}%\n"
        f"進場：市價 · 倉位建議 {status_to_position_pct(entry['status'])}%"
    )
```

Discord 訊息分段改為：
```python
sections = []
for lst, label in [
    (momentum, 'MOMENTUM'),
    (active,   'ACTIVE'),
    (setup,    'SETUP'),
]:
    if not lst: continue
    sections.append(f"\n━━━ {status_to_emoji(label)} {label} ({len(lst)} 檔) ━━━")
    for e in lst[:10]:
        sections.append(fmt_status_block(e))

# WATCH / NOISE 不推播
```

**檔案 12：`docs/index.html`（Dashboard）**

統計卡從 3 張（SS / S / A）改 5 張：
```html
<div class="grid grid-cols-5 gap-3">
  <Card emoji="🔵" label="MOMENTUM" value={data.counts.momentum} />
  <Card emoji="🟢" label="ACTIVE"   value={data.counts.active} />
  <Card emoji="🟡" label="SETUP"    value={data.counts.setup} />
  <Card emoji="🟠" label="WATCH"    value={data.counts.watch} />
  <Card emoji="🔴" label="NOISE"    value={data.counts.noise} />
</div>
```

「今日篩選」表格的「等級」欄改顯示狀態 emoji + 名稱（🟢 ACTIVE 等）。  
「模式」欄改顯示「累積天數」（5/5、4/5 等）。  
「分數」欄顯示 `total/10` 格式（如 `7/10`）。

**檔案 13：`discord_bot/stock_commands.py`** — `/stock` 個股查詢

`analyze_stock_data` 的「五星推薦度」：
```python
# v6.2：用 total_score / 2 推算
star = min(5, score_result['total_score'] // 2)
```

加進個股分析訊息：
```
v6.2 Stalker 條件檢查：
  ✅ 漲幅 +1.23%（在 -1~+3% 範圍）
  ❌ 量比 2.3x（超過 1.8x 上限）
  ✅ 5 日累積 +1.5%（在 -2~+3% 範圍）
  ...
v6.2 評分：Flow 3/5 + Trend 2/3 + Heat 1/2 = 6/10 (🟡 SETUP)
```

**檔案 14：`web_export.py`**

```python
# stats.json 結構改：
{
  "counts": {
    "momentum": 0, "active": 2, "setup": 5,
    "watch": 0, "noise": 0,
    "total_pushed": 7  # = momentum + active + setup
  },
  "stats_v6_2": {
    "by_status": {
      "MOMENTUM": {"n": 0, "winrate": null, "avg_pct": null},
      "ACTIVE":   {"n": 12, "winrate": 0.58, "avg_pct": 2.34},
      "SETUP":    {"n": 18, "winrate": 0.50, "avg_pct": 0.89}
    }
  }
}
```

**檔案 15：tests/**

新增 / 更新：
- `test_score_v62.py`：10 分制各種邊界（Flow 5/5, 4/5 邊界、Trend MACD 邊界、Heat 三條件組合）
- `test_classify_status.py`：分數→狀態對映
- `test_stalker_filter_full.py`：12 道過濾的 regression
- `test_daily_scores.py`：寫入 / 讀取 / UPSERT
- `test_accumulation.py`：detect_stalker_setup
- `test_macd_8175.py`：confirm 用 (8,17,5) 不是 (12,26,9)

刪除：
- `test_chase.py` 內舊的 strong_chase 測試（保留 count_consecutive_limit_ups 函式測試）
- `test_filter_first_round.py` 內舊邏輯測試（保留 KeyError regression）

#### Acceptance（Phase 1）

- pytest 全綠
- pyflakes 無警告
- DB schema 升版成功（首次啟動會 DROP 重建）
- 手動 `/run` 一次：篩選池 0~5 檔，這是正常
- 通過的標的：
  - 等級顯示 🟢 ACTIVE / 🟡 SETUP（MOMENTUM 極罕見）
  - 分數明細顯示 Flow/Trend/Heat 子分數
  - 累積天數顯示 4/5 或 5/5
  - 進場顯示「市價」
  - 倉位建議 ACTIVE 30% / SETUP 18%
- Dashboard 5 張統計卡正確顯示
- `daily_scores` 表開始累積資料
- `daily_t86_history` 表開始累積資料
- 連續觀察 4 週：
  - 過熱進場 = 0
  - 市價成交比例 100%（除非一字漲停）
  - 各狀態勝率初步看（樣本太少，僅參考）

#### 注意事項

- **歷史資料會被清空**——schema 升版會 DROP screen_records。這是預期的，因為舊資料用的是不同策略 / 不同撮合，混在一起算勝率會誤導。
- 第一週可能完全沒有 MOMENTUM 等級（這正常，Stalker 池子裡 MOMENTUM 本來就罕見）
- 連續 5 天 0 標的不要動參數
- 連續 10 天 0 標的才放寬：第一順位放寬乖離 20MA ≤ 3% → ≤ 4%，再不行放寬振幅 < 5% → < 6%

---

### Phase 2：ATR 停損 + 時間停損 + 移動停利 + OVERHEAT 出場 + 分數動能排序

**目標**：把出場系統升級成 4 重訊號（ATR 停損 / 時間 / 移動 / OVERHEAT），把排序鍵從「5 日累積淨買」換成「5 日分數斜率」。

#### 改動

**檔案 1：`db/settle.py`** — ATR 停損計算

```python
def fill_t1_entry_v62(record_id, t1_date, status, entry_price, t1_open, atr14=None):
    if status != 'filled' or entry_price is None:
        # missed 邏輯保留
        return

    e = float(entry_price)
    # ATR 停損
    if atr14 and atr14 > 0:
        stop_pct = max(0.07, 1.5 * float(atr14) / e)
    else:
        stop_pct = 0.07
    actual_stop_loss = round(e * (1 - stop_pct), 2)
    time_stop = _add_trading_days(t1_date, config.MAX_HOLD_DAYS_STALKER)

    # 更新 record
    ...
```

**檔案 2：`discord_bot/settle.py`** — 4 重出場訊號

```python
def check_exit_signals_v62(record, kbars_since_entry, current_heat_score):
    if kbars_since_entry.empty:
        return False, None, None

    entry = float(record['actual_entry_price'])
    stop  = float(record['actual_stop_loss'])
    tgt1  = float(record['actual_target1'])
    tgt2  = float(record['actual_target2'])
    today_close = float(kbars_since_entry['close'].iloc[-1])
    today_high  = float(kbars_since_entry['high'].iloc[-1])
    today_vol   = float(kbars_since_entry['volume'].iloc[-1])
    days_held = len(kbars_since_entry)

    # 1. 第二目標 → 全出
    if today_close >= tgt2:
        return True, 'target2', today_close

    # 2. 收盤跌破停損 → 全出
    if today_close <= stop:
        return True, 'stop_close', today_close

    # 3. 時間停損
    if days_held >= config.MAX_HOLD_DAYS_STALKER:
        return True, 'time_stop', today_close

    # 4. 移動停利（觸過第一目標後啟動）
    max_close = max(float(record.get('max_close_since_entry') or entry),
                    float(kbars_since_entry['close'].max()))
    if max_close >= tgt1:
        if today_close <= max_close * 0.93:
            return True, 'trailing', today_close

    # 5. OVERHEAT 出場：Heat=0 且爆量長紅
    today_change = (today_close / float(kbars_since_entry['close'].iloc[-2]) - 1) * 100 \
                   if days_held >= 2 else 0
    vol_60d_avg = ...  # 從 K 棒算 60 日均量
    vol_vs_60d = today_vol / vol_60d_avg if vol_60d_avg > 0 else 0
    if current_heat_score == 0 and today_change > 5 and vol_vs_60d > 3:
        return True, 'overheat', today_close

    return False, None, None
```

`settle_weekly` 改：每天（不是只有週五）跑一次 `check_exit_signals_v62`，因為 OVERHEAT / 時間停損可能任何一天觸發。週五的 W1/W2 結算仍照舊。

**檔案 3：`analysis.py`** — 排序改 velocity

```python
def _calc_velocity_or_fallback(sid, target_date):
    """5 日分數斜率；資料不足回 0。"""
    scores = db.fetch_recent_scores(sid, days=5, end_date=target_date)
    if len(scores) < 2:
        return 0
    n = len(scores)
    y = [s['total_score'] for s in scores]
    x_mean, y_mean = (n-1)/2, sum(y)/n
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0


def _sort_key_v62_phase2(c, target_date):
    velocity = _calc_velocity_or_fallback(c['sid'], target_date)
    h = get_inst_history(c['sid'], days=5, end_date=target_date)
    cum_net = sum(n for _, n in h) if h else 0
    return (velocity, cum_net)  # 主鍵 velocity, 次鍵 cum_net


candidates.sort(key=lambda c: _sort_key_v62_phase2(c, target_date), reverse=True)
```

**檔案 4：Discord 卡片新增「分數軌跡」欄**

```python
def fmt_score_trajectory(sid, end_date, days=5):
    scores = db.fetch_recent_scores(sid, days=days, end_date=end_date)
    if not scores: return "(無歷史)"
    return ' → '.join(str(s['total_score']) for s in scores)


# 卡片裡加：
分數軌跡：2 → 3 → 5 → 6 → 7（上升中）
```

#### Acceptance（Phase 2）

- pytest 全綠（新增 `test_atr_stop.py`、`test_time_stop.py`、`test_trailing.py`、`test_overheat_exit.py`、`test_velocity.py`）
- 結算 `exit_reason` 出現多樣化（不只 target/stop）
- 排序鍵改 velocity，至少 5 個交易日後生效
- Discord 卡片顯示分數軌跡
- 4 週後比對：
  - 各狀態勝率應單調：MOMENTUM > ACTIVE > SETUP（樣本太少先看趨勢）
  - 停損出場比例應從 ~50% 降到 ~30%

---

### Phase 3：真實 Heat Score 資料源 + OBV 進階判定 + 因子分析工具

**目標**：把代理 Heat 換成真實散戶熱度資料。

#### 3a：PTT Stock 板爬蟲

新模組 `crawlers/ptt_stock.py`，DB 表 `social_signals`：

```sql
CREATE TABLE IF NOT EXISTS social_signals (
    sid VARCHAR(20),
    date DATE,
    source VARCHAR(20),    -- 'ptt' / 'news' / 'gtrends'
    raw INTEGER,           -- 原始計數
    z_score NUMERIC(8,2),  -- 標準化（vs 60 日均）
    PRIMARY KEY (sid, date, source)
);
```

爬 PTT Stock 板每日股號 mention，過濾年份等假股號，寫進 `social_signals`。

#### 3b：新聞密度

抓鉅亨 / MoneyDJ / Anue RSS，計算當日提及篇數。

#### 3c：Google Trends

每日批次跑頭部 200 檔 Google Trends，寫進 `social_signals`。

#### 3d：Heat Score 切換

`heat.py`：
```python
def calc_heat_real(sid, date):
    """從 social_signals 算真實 Heat。"""
    signals = db.fetch_social_signals(sid, date)
    # 規則：
    #   所有 source 的 z_score 都 < 0.5 → +2（極冷）
    #   z_score 平均 < 1.0 → +1（偏冷）
    #   否則 → 0
    ...


def calc_heat(sid, date, df_hist, cum5d, vol60, bias20, use_real=False):
    if use_real:
        try:
            return calc_heat_real(sid, date)
        except (NotImplementedError, MissingDataError):
            pass
    return calc_heat_proxy(df_hist, cum5d, vol60, bias20)
```

`config.py` 加 `HEAT_USE_REAL = True/False`，可逐步切換。

#### 3e：OBV 真實判定 + 因子分析

換掉 Phase 1~2 的 RSI 代理，做真實 OBV / 價格背離判定（雖然 Trend Score 沒用 OBV，但 `/stock` 個股分析會用）。

新工具 `tools/factor_analysis.py`：每月跑一次，對歷史已結算紀錄做：
- Flow 子分數 vs settle_pct 相關係數
- 各 status 等級勝率
- 各 exit_reason 平均報酬

#### Acceptance（Phase 3）

- PTT / News / GTrends 資料源運作
- Heat Score 真實版本上線後勝率對比代理版本
- 因子分析每月自動產出

---

### Phase 4：Backtest 框架

**目標**：建立離線可回測 + walk-forward validation。

新模組 `tools/backtest.py`：
- Input：歷史 K 棒、歷史 T86、歷史 social_signals
- Process：對每個交易日跑離線版 `run_analysis`
- Output：模擬 filled / 模擬結算 / 模擬報酬

#### Acceptance（Phase 4）

- 一年歷史能在 30 分鐘內跑完
- 策略改動可離線預演

---

## 3. UI / 呈現變動清單

### 保留不動

- Discord 17:00 推播時間
- Dashboard 四分頁結構（今日 / 結算 / 勝率 / 歷史）
- /run /stock /buy /sell /holding /leaderboard /report /stats 所有指令
- T+1 撮合 + W1/W2 週五結算
- missed_hypothetical 追蹤
- PWA 圖示 / manifest

### v6.2 必要變動

| 元素 | 舊 | 新 |
|------|-----|-----|
| 統計卡 | 總數 / SS / S / A（3 張）| MOMENTUM / ACTIVE / SETUP / WATCH / NOISE（5 張） |
| 等級欄 | SS / S / A | 🔵 MOMENTUM / 🟢 ACTIVE / 🟡 SETUP（WATCH / NOISE 不顯示在主表）|
| 模式欄 | 一般 / CHASE | 累積天數「5/5」「4/5」 |
| 分數欄 | 數字（如 87）| `N/10` 格式（如 7/10）|
| 進場區欄 | 價格區間 | 「市價」（灰字）|
| 停損欄 | 固定 -5% | 實際 ATR 算出價（如 235.40，下標 -5.2% / 1.5×ATR）|
| 出場原因 | （沒有）| target1/target2/stop/trailing/time/overheat |
| 分數軌跡（Phase 2）| （沒有）| `2 → 3 → 5 → 6 → 7`|

---

## 4. 給 Claude Code 的執行提示

請逐 Phase 做，每個 Phase 完成後：

1. `python -m pytest tests/ -v` 全綠
2. `python -m pyflakes *.py db/*.py discord_bot/*.py` 無警告
3. 寫一個 dry-run 確認 import 正常
4. Commit 到 feature branch（例如 `feature/v62-phase1`）
5. Commit message：`v6.2 Phase 1: pure Stalker + 10pt scoring + 5-tier status`
6. **不要繼續往下一個 Phase 做**——等使用者驗證 2~4 週實盤資料

**特別重要**：

- Phase 1 上線會 DROP screen_records，這是預期行為（schema_version 升版機制）
- 「找不到標的」是 feature，不要為了「有東西可推」放寬條件
- DB schema 用 `IF NOT EXISTS` / `IF EXISTS` 保護，避免重啟誤觸
- 所有 v5 函式（`calc_score`、`check_strong_chase`）標 deprecated 但保留，不刪除（避免 import 鏈斷裂）
- MACD 全面換 (8,17,5)：`/stock` 個股查詢也要跟著換

---

## 5. 風險聲明 / Caveats

- 純 Stalker 樣本會非常少（每週可能 5~15 筆，甚至更少），勝率統計要 3~6 個月才有意義
- DROP screen_records = 歷史資料永久消失（兩週的 v5 資料）。確認接受再做
- ATR 停損 + 收盤判定 = 單筆損失可能 > 7%。要接受個股可能 -10% 才出場，但整體勝率提升
- Heat 代理在 Phase 4 上線前是價量近似，會錯過「價量看似冷門但 PTT 已狂熱」的情況
- 「分數持續上升」訊號要 5 個交易日後才開始有意義
- 本策略仍是研究用途，不構成投資建議

---

**Document version**：v6.2.0 (Pure Stalker + 10-pt + 5-tier)  
**Supersedes**：v6.0、v6.1  
**Stability**：預計穩定版（除非實盤跑出結構性問題）  
**Status**：Draft（待 Claude Code 實作）
