import os, requests, io, time
try:
    import db as _db
    _DB_OK = True
except Exception as _e:
    _DB_OK = False
    print(f'[DB] 無法載入資料庫模組：{_e}')
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import pandas as pd
from datetime import datetime, timedelta, timezone

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer':         'https://www.twse.com.tw/',
}

# ══════════════════════════════════════════════════════════
# 篩選參數 ── 只改這裡就能調整條件
# ══════════════════════════════════════════════════════════
MIN_PRICE        = 10      # 1. 收盤價下限（元）
# X 級已移除
MIN_INST_SHARE   = 50000   # 3. 法人合計買超最低股數（50張 = 50,000股）
MAX_CANDIDATES   = 30      # 4. 候選數量保護上限（取法人買超最多的前N名）
VOLUME_RATIO_MIN = 1.5     # 5. 量比：當日量 ÷ 近5日均量
# 6. EMA 多頭排列（程式內判斷，含備援邏輯）

EMA_SHORT  = 10
EMA_MID    = 20
EMA_LONG1  = 60    # 主要：20EMA > 60EMA > 120EMA
EMA_LONG2  = 120
# 備援：資料不足120筆時改用 10EMA > 20EMA > 60EMA
EMA_FALLBACK_MIN = 60   # 備援模式最少需要幾筆資料

GRADE_SS   =  7.0
GRADE_S    =  3.5
GRADE_A    =  1.0
# X 級已移除
# X 級已移除

# 雙買超門檻：外資和投信各自的最低要求
MIN_FOREIGN_SHARE      = 10000   # 外資至少 10,000 股
MIN_TRUST_SHARE        = 10000   # 投信至少 10,000 股
MIN_INST_SHARE_SINGLE  = 100000  # 只有單方買超時，合計需達 100,000 股才入選

DATA_READY_HOUR = 17   # 台灣時間幾點後才有當天資料

# ══════════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════════
def clean_sid(series):
    return series.astype(str).str.replace(r'[=\" \t]', '', regex=True).str.strip()

def safe_get(url, params=None, timeout=25, retries=3, wait=15):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout, verify=False)
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            print(f"[逾時] {url}（第{attempt}次）")
        except requests.exceptions.RequestException as e:
            print(f"[失敗] {url}（第{attempt}次）：{e}")
        if attempt < retries:
            time.sleep(wait)
    return None

def safe_read_csv(text, label, skiprows=0, thousands=',', min_cols=2):
    try:
        df = pd.read_csv(
            io.StringIO(text), skiprows=skiprows,
            thousands=thousands, on_bad_lines='skip'
        )
        if df.shape[1] < min_cols:
            print(f"[{label}] 欄位不足({df.shape[1]})，前400字：\n{text[:400]}")
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"[{label}] 解析失敗：{e}\n前400字：\n{text[:400]}")
        return pd.DataFrame()

def find_col(df, *keywords):
    for c in df.columns:
        if all(k in str(c) for k in keywords):
            return c
    return None

def fmt_share(n):
    sign = '+' if n >= 0 else ''
    return f"{sign}{int(n):,}"

# ══════════════════════════════════════════════════════════
# 交易日判定
# ══════════════════════════════════════════════════════════
def get_target_date(run_mode):
    now  = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    base = now.date()
    hour = now.hour

    if run_mode == 'preview':
        delta = 3 if base.weekday() == 0 else 1
        base -= timedelta(days=delta)
    elif run_mode == 'close':
        if   base.weekday() == 5: base -= timedelta(days=1)
        elif base.weekday() == 6: base -= timedelta(days=2)
    else:  # auto
        if hour < DATA_READY_HOUR:
            delta = 3 if base.weekday() == 0 else (
                    2 if base.weekday() == 6 else 1)
            base -= timedelta(days=delta)
        else:
            if   base.weekday() == 5: base -= timedelta(days=1)
            elif base.weekday() == 6: base -= timedelta(days=2)

    return base.strftime('%Y%m%d')

def prev_months(date_str, n=7):
    target = datetime.strptime(date_str, '%Y%m%d')
    months, d = [], target.replace(day=1)
    for _ in range(n):
        months.append(d.strftime('%Y%m'))
        d = (d - timedelta(days=1)).replace(day=1)
    return months

# ══════════════════════════════════════════════════════════
# 大盤概況（非強制）
# ══════════════════════════════════════════════════════════
def get_market_info(date_str):
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'IND'},
        timeout=20, retries=2, wait=10
    )
    if r is None or '查詢無資料' in r.text:
        return None
    try:
        df = safe_read_csv(r.text, 'MI_INDEX-IND', skiprows=1)
        if df.empty:
            return None
        row = df[df.iloc[:, 0].astype(str).str.contains('發行量加權股價指數', na=False)]
        if row.empty:
            return None
        row = row.iloc[0]
        idx_p    = float(str(row.iloc[1]).replace(',', ''))
        idx_diff = pd.to_numeric(str(row.iloc[3]).replace(',', ''), errors='coerce')
        if '−' in str(row.iloc[2]) or str(row.iloc[2]).strip().startswith('-'):
            idx_diff = -abs(idx_diff)
        else:
            idx_diff =  abs(idx_diff)
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2) if (idx_p - idx_diff) != 0 else 0
        return {'close': idx_p, 'diff': idx_diff, 'pct': idx_chg}
    except Exception as e:
        print(f"[大盤解析失敗] {e}")
        return None


# ══════════════════════════════════════════════════════════
# T86 法人解析（用固定欄位索引，不依賴欄位名稱字串）
# T86 固定欄位順序：
#   idx 0  : 證券代號
#   idx 1  : 證券名稱
#   idx 4  : 外資及陸資買賣超股數  ← _foreign
#   idx 10 : 投信買賣超股數        ← _trust
#   idx 18 : 三大法人買賣超股數    ← _total
# ══════════════════════════════════════════════════════════
def parse_t86(text):
    print(f"[T86] 前300字：\n{text[:300]}\n{'─'*40}")
    lines = text.splitlines()

    # 找表頭行
    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
        print("[T86] 找不到表頭，發送原始內容供 debug")
        return pd.DataFrame()

    print(f"[T86] 表頭第 {header_idx} 行：{lines[header_idx][:120]}")
    df = safe_read_csv('\n'.join(lines[header_idx:]), 'T86', min_cols=11)
    if df.empty:
        return pd.DataFrame()

    # 印出全部欄位名稱
    print(f"[T86] 共 {len(df.columns)} 欄：{list(df.columns)}")

    # 過濾有效股票列
    df = df[df.iloc[:, 0].astype(str).str.match(r'^[0-9A-Z]{4,6}$', na=False)].copy()
    if df.empty:
        print("[T86] 過濾後無有效股票列")
        return pd.DataFrame()

    df['sid_clean'] = clean_sid(df.iloc[:, 0])
    n = len(df.columns)

    # 用索引取值
    if n >= 19:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = pd.to_numeric(df.iloc[:, 18], errors='coerce').fillna(0)
        print(f"[T86] 標準19欄格式，外資idx=4 投信idx=10 合計idx=18")
    elif n >= 11:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = df['_foreign'] + df['_trust']
        print(f"[T86] 備援{n}欄格式，外資idx=4 投信idx=10")
    else:
        print(f"[T86] 欄位數不足({n})，無法解析")
        return pd.DataFrame()

    print(f"[T86] 有效股票：{len(df)} 檔，外資非零：{(df['_foreign']!=0).sum()}，投信非零：{(df['_trust']!=0).sum()}")
    return df

# ══════════════════════════════════════════════════════════
# 歷史 K 棒（單股單月，快速模式）
# ══════════════════════════════════════════════════════════
def fetch_stock_day_fast(sid, yyyymm):
    import re as _re
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=8
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    try:
        text  = r.text
        lines = text.splitlines()

        # 找 header 行：含「日期」且含「收盤」或「成交」
        header_i = None
        for i, line in enumerate(lines):
            if '日期' in line and ('收盤' in line or '成交' in line):
                header_i = i
                break

        # 找不到 header，找第一個符合民國日期的行，退一行當 header
        if header_i is None:
            for i, line in enumerate(lines):
                if _re.search(r'\d{3}/\d{2}/\d{2}', line):
                    header_i = max(0, i - 1)
                    break

        if header_i is None:
            return pd.DataFrame()

        csv_text = '\n'.join(lines[header_i:])
        df = safe_read_csv(csv_text, f'STOCK_DAY-{sid}', min_cols=7)
        if df.empty:
            return pd.DataFrame()

        mask = df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)
        df   = df[mask].copy()
        if df.empty:
            return pd.DataFrame()

        def roc_to_date(s):
            y, m, d = str(s).split('/')
            return datetime(int(y) + 1911, int(m), int(d)).date()

        df['date']   = df.iloc[:, 0].apply(roc_to_date)
        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce')
        df['high']   = pd.to_numeric(df.iloc[:, 4].astype(str).str.replace(',', ''), errors='coerce')
        df['low']    = pd.to_numeric(df.iloc[:, 5].astype(str).str.replace(',', ''), errors='coerce')
        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce')
        cols = [c for c in ['date','close','high','low','volume'] if c in df.columns]
        return df[cols].dropna(subset=['date','close']).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

def build_history_fast(sid, months):
    """
    逐月抓歷史K棒，並快取到磁碟（同一天多次執行直接讀快取，確保結果一致）。
    快取路徑：/tmp/stock_cache_{date}/{sid}.json
    """
    import json, os

    # 快取目錄以月份清單第一個月（=最新月）命名，確保每天獨立
    cache_dir = f"/tmp/stock_cache_{months[0]}"
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = f"{cache_dir}/{sid}.json"

    # 有快取就直接讀，跳過 API 請求
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                records = json.load(f)
            if records:
                df = pd.DataFrame(records)
                df['date'] = pd.to_datetime(df['date']).dt.date
                return df
        except:
            pass  # 快取損壞就重新抓

    frames = []
    for yyyymm in months:
        df_m = fetch_stock_day_fast(sid, yyyymm)
        if df_m.empty:
            time.sleep(2)
            df_m = fetch_stock_day_fast(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        time.sleep(0.5)  # 避免 TWSE 限速

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)

    # 寫入快取
    try:
        records = df_all.copy()
        records['date'] = records['date'].astype(str)
        with open(cache_file, 'w') as f:
            json.dump(records.to_dict('records'), f)
    except:
        pass

    return df_all

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def check_ema_bull(df):
    """
    主要：20EMA > 60EMA > 120EMA（需 >= 120 筆資料）
    備援：10EMA > 20EMA > 60EMA（需 >= 60 筆資料）
    """
    if len(df) < EMA_FALLBACK_MIN:
        return False, 'insufficient'

    closes = df['close'].astype(float)

    if len(df) >= EMA_LONG2:
        # 主要模式
        ema20  = calc_ema(closes, EMA_MID).iloc[-1]
        ema60  = calc_ema(closes, EMA_LONG1).iloc[-1]
        ema120 = calc_ema(closes, EMA_LONG2).iloc[-1]
        return ema20 > ema60 > ema120, 'full'
    else:
        # 備援模式（60~119筆）
        ema10 = calc_ema(closes, EMA_SHORT).iloc[-1]
        ema20 = calc_ema(closes, EMA_MID).iloc[-1]
        ema60 = calc_ema(closes, EMA_LONG1).iloc[-1]
        return ema10 > ema20 > ema60, 'fallback'

def calc_volume_ratio(df, target_date):
    df = df[df['date'] <= target_date].reset_index(drop=True)
    if len(df) < 6:
        return 0.0
    today_vol = df['volume'].iloc[-1]
    avg5      = df['volume'].iloc[-6:-1].mean()
    return round(today_vol / avg5, 2) if avg5 > 0 else 0.0

# ══════════════════════════════════════════════════════════
# 主分析
# ══════════════════════════════════════════════════════════

def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    import numpy as np
    h, l, pc = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period-1, min_periods=period).mean()

def calc_obv(df):
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            obv.append(obv[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            obv.append(obv[-1] - df['volume'].iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)


def calc_macd(df, fast=12, slow=26, signal=9):
    """
    MACD：DIF = EMA(fast)-EMA(slow); DEA = EMA(DIF, signal); Hist = DIF-DEA
    回傳最新的 dif/dea/hist + 評分（0~10 分）+ 標籤。
    需要至少 slow+signal 筆資料才有意義（35 筆）。

    評分邏輯：
      ─ 黃金交叉發生在最近 3 天 + DIF > 0   → 10 分（最強多頭）
      ─ 黃金交叉發生在最近 3 天 + DIF ≤ 0   → 7 分（反彈起點）
      ─ DIF > DEA 且 Histogram 擴張        → 8 分（多頭動能增強）
      ─ DIF > DEA 但 Histogram 萎縮        → 5 分（動能轉弱）
      ─ DIF < DEA                         → 0 分（空頭排列）
    """
    if len(df) < slow + signal:
        return {'macd_score': 5, 'macd_label': '⚪ MACD 資料不足',
                'dif': None, 'dea': None, 'hist': None,
                'expanding': None, 'cross_up': False}
    closes   = df['close'].astype(float)
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea

    last_dif  = float(dif.iloc[-1])
    last_dea  = float(dea.iloc[-1])
    last_hist = float(hist.iloc[-1])
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 else 0.0

    # 最近 3 天有沒有發生黃金交叉
    cross_up = False
    for i in range(max(1, len(dif)-3), len(dif)):
        if dif.iloc[i-1] <= dea.iloc[i-1] and dif.iloc[i] > dea.iloc[i]:
            cross_up = True
            break

    expanding = last_hist > prev_hist  # Histogram 是否還在擴張

    if cross_up and last_dif > 0:
        score, label = 10, '🚀 MACD 黃金交叉（零軸上）'
    elif cross_up and last_dif <= 0:
        score, label = 7, '↗️ MACD 黃金交叉（反彈起點）'
    elif last_dif > last_dea and expanding:
        score, label = 8, '✅ MACD 多頭動能增強'
    elif last_dif > last_dea and not expanding:
        score, label = 5, '⚠️ MACD 多頭動能轉弱'
    else:
        score, label = 0, '❌ MACD 空頭排列'

    return {
        'macd_score': score, 'macd_label': label,
        'dif': round(last_dif, 4), 'dea': round(last_dea, 4),
        'hist': round(last_hist, 4),
        'expanding': expanding, 'cross_up': cross_up,
    }


def count_consecutive_limit_ups(df, threshold=9.5):
    """
    從最後一筆往回計算連續漲停天數（含當日）。
    台股漲停 +10%，實務上 +9.5% 以上視為漲停（含計算誤差）。
    """
    if len(df) < 2:
        return 0
    closes = df['close'].astype(float).values
    cnt = 0
    for i in range(len(closes) - 1, 0, -1):
        prev = closes[i-1]
        if prev == 0:
            break
        chg = (closes[i] - prev) / prev * 100
        if chg >= threshold:
            cnt += 1
        else:
            break
    return cnt


def check_strong_chase(entry, macd_info, market_score):
    """
    對 連續漲停 ≥3 日 的股票檢查 5 項追漲門檻：
      1. 法人今日合計買超 ≥ 200K 股
      2. 量比 ≥ 2.0x
      3. 籌碼集中度 ≥ 10%
      4. MACD: DIF > DEA 且 Histogram 擴張中
      5. 大盤環境分數 ≥ 0
    回傳 dict: {'passed': int, 'reasons': [str]}
    """
    foreign = entry.get('foreign', 0)
    trust   = entry.get('trust', 0)
    total_inst = foreign + trust

    chip_score = entry.get('chip_score', 0)  # 0/2/5/8 對應集中度 <5/5-10/10-20/≥20
    vr = entry.get('vol_ratio', 0)

    macd_dif = macd_info.get('dif')
    macd_dea = macd_info.get('dea')
    macd_exp = macd_info.get('expanding', False)
    macd_ok  = (macd_dif is not None and macd_dea is not None
                and macd_dif > macd_dea and macd_exp)

    checks = [
        ('法人合計買超 ≥ 200K 股', total_inst >= 200000),
        ('量比 ≥ 2.0x',           vr >= 2.0),
        ('籌碼集中度 ≥ 10%',       chip_score >= 5),  # chip_score>=5 對應 conc>=10%
        ('MACD 多頭擴張',         macd_ok),
        ('大盤環境分數 ≥ 0',       market_score >= 0),
    ]
    passed = sum(1 for _, ok in checks if ok)
    reasons = [('✅ ' if ok else '❌ ') + name for name, ok in checks]
    return {'passed': passed, 'checks': checks, 'reasons': reasons}


def calc_advanced_indicators(df, price):
    """RSI、ATR停損、壓力位、位階、OBV背離"""
    result = {
        'rsi': None, 'rsi_label': '', 'rsi_score': 0.0,
        'atr_stop': None, 'atr_pct': None,
        'resistance_label': '', 'resistance_score': 0.0,
        'position_label': '', 'position_score': 0.0,
        'obv_label': '', 'obv_score': 0.0,
    }
    if len(df) < 20 or 'high' not in df.columns:
        return result

    # RSI
    try:
        rsi_s = calc_rsi(df['close'])
        rsi_v = float(rsi_s.iloc[-1])
        if pd.isna(rsi_v):
            raise ValueError
        rsi_v  = round(rsi_v, 1)
        rsi_3d = [float(x) for x in rsi_s.tail(3) if not pd.isna(x)]
        result['rsi'] = rsi_v
        if rsi_v > 80 and len(rsi_3d) == 3 and all(r > 80 for r in rsi_3d):
            result['rsi_label'] = f'🚀 飆股鈍化（RSI {rsi_v}，連3日>80）'
            result['rsi_score'] = 0.5
        elif rsi_v > 80:
            result['rsi_label'] = f'⚠️ 短線過熱（RSI {rsi_v}）'
            result['rsi_score'] = 0.0
        elif rsi_v > 60:
            result['rsi_label'] = f'✅ 強勢動能（RSI {rsi_v}）'
            result['rsi_score'] = 0.5
        elif rsi_v > 50:
            result['rsi_label'] = f'🔄 動能普通（RSI {rsi_v}）'
            result['rsi_score'] = 0.0
        else:
            result['rsi_label'] = f'❌ 動能不足（RSI {rsi_v}）'
            result['rsi_score'] = -0.5
    except:
        pass

    # ATR 動態停損
    try:
        atr_v = float(calc_atr(df).iloc[-1])
        if pd.isna(atr_v):
            raise ValueError
        atr_stop = round(price - 2 * atr_v, 1)
        atr_pct  = round((atr_stop - price) / price * 100, 1)
        fixed    = round(price * 0.95, 1)
        if atr_stop > fixed:
            result['atr_stop'] = atr_stop
            result['atr_pct']  = atr_pct
        else:
            result['atr_stop'] = fixed
            result['atr_pct']  = -5.0
    except:
        pass

    # 壓力位 + 位階
    try:
        n60  = min(60, len(df))
        n120 = min(120, len(df))
        high_60  = float(df['high'].tail(n60).max())
        high_120 = float(df['high'].tail(n120).max())
        low_120  = float(df['low'].tail(n120).min())
        dist_60  = (price - high_60) / high_60 * 100
        dist_120 = (price - high_120) / high_120 * 100
        gain_low = (price - low_120) / low_120 * 100

        if dist_60 >= -3:
            result['resistance_label'] = f'⚠️ 接近{n60}日高點壓力（{high_60:.1f} 元）'
            result['resistance_score'] = -0.5
        elif dist_120 >= -3:
            result['resistance_label'] = f'⚠️ 接近{n120}日高點壓力（{high_120:.1f} 元）'
            result['resistance_score'] = -0.25
        else:
            result['resistance_label'] = f'✅ 無明顯壓力（{n60}日高 {high_60:.1f} 元）'
            result['resistance_score'] = 0.0

        if gain_low > 100:
            result['position_label'] = f'❌ 位階極高（距低點 +{gain_low:.0f}%）'
            result['position_score'] = -1.0
        elif gain_low > 50:
            result['position_label'] = f'⚠️ 位階偏高（距低點 +{gain_low:.0f}%）'
            result['position_score'] = -0.5
        elif gain_low > 20:
            result['position_label'] = f'🔄 位階中等（距低點 +{gain_low:.0f}%）'
            result['position_score'] = 0.0
        else:
            result['position_label'] = f'✅ 剛起漲（距低點 +{gain_low:.0f}%）'
            result['position_score'] = 0.5
    except:
        pass

    # OBV 背離
    try:
        obv = calc_obv(df)
        price_high = price >= float(df['close'].tail(20).iloc[:-1].max())
        obv_high   = float(obv.iloc[-1]) >= float(obv.tail(20).iloc[:-1].max())
        p_slope    = float(df['close'].iloc[-1]) - float(df['close'].tail(10).iloc[0])
        o_slope    = float(obv.iloc[-1]) - float(obv.tail(10).iloc[0])

        if price_high and not obv_high:
            result['obv_label'] = '⚠️ OBV 背離（量能未跟上價格）'
            result['obv_score'] = -0.5
        elif p_slope > 0 and o_slope > 0:
            result['obv_label'] = '✅ 量價同步上揚'
            result['obv_score'] = 0.25
        elif p_slope > 0 and o_slope < 0:
            result['obv_label'] = '⚠️ 量縮價漲，動能減弱'
            result['obv_score'] = -0.25
        else:
            result['obv_label'] = '🔄 量價方向不明'
            result['obv_score'] = 0.0
    except:
        pass

    return result






def fetch_margin_change(sid, date_str):
    """
    抓 sid 今日和 5 個交易日前的融資餘額，計算增幅。
    MI_MARGN 欄位 idx 5 = 融資餘額（張）
    """
    import re as _re
    from datetime import datetime as _dt2, timedelta as _td2
    # 取前5個交易日（排除週末）
    _base = _dt2.strptime(date_str, '%Y%m%d').date()
    _prev = []
    _d = _base - _td2(days=1)
    while len(_prev) < 5:
        if _d.weekday() < 5:
            _prev.append(_d.strftime('%Y%m%d'))
        _d -= _td2(days=1)
    date_5d = _prev[-1]  # 最舊的那天

    def get_margin(ds):
        r = safe_get(
            'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN',
            params={'response': 'csv', 'date': ds, 'selectType': 'ALLBUT0999'},
            timeout=12, retries=1, wait=3
        )
        if not r or '查詢無資料' in r.text:
            return None
        for line in r.text.splitlines():
            parts = [v.strip().strip('"').replace(',','') for v in line.split(',')]
            if len(parts) < 6:
                continue
            raw = _re.sub(r'[="\\s\t ]', '', parts[0]).strip()
            if raw == sid:
                try:
                    return float(parts[5]) * 1000  # 張→股
                except:
                    return None
        return None

    margin_today = get_margin(date_str)
    time.sleep(0.3)
    margin_5d    = get_margin(date_5d)

    if margin_today is None or margin_5d is None or margin_5d == 0:
        return {'score': 0, 'label': ''}

    pct = (margin_today - margin_5d) / margin_5d * 100
    if pct >= 30:   return {'score': -8, 'label': f'❌ 融資5日暴增 +{pct:.1f}%'}
    elif pct >= 15: return {'score': -4, 'label': f'⚠️ 融資5日增加 +{pct:.1f}%'}
    elif pct >= 0:  return {'score':  0, 'label': f'🔄 融資5日 +{pct:.1f}%'}
    else:           return {'score': +3, 'label': f'✅ 融資5日 {pct:.1f}%，籌碼健康'}


def calc_market_env(market_foreign_history):
    """
    大盤外資環境過濾。
    market_foreign_history: 近3天大盤外資買賣超金額（億元），由舊到新。
    """
    if not market_foreign_history:
        return {'score': 0, 'label': '', 'suspend': False}

    today       = market_foreign_history[-1]
    last3       = market_foreign_history[-3:]
    consec_sell = all(x < 0 for x in last3)
    total_3d    = sum(last3)

    if consec_sell and total_3d < -500:
        return {'score': 0,
                'label': '🚨 大盤外資連3日賣超逾500億，今日暫停發出進場訊號',
                'suspend': True}
    elif today < -100:
        return {'score': -5,
                'label': f'⚠️ 大盤外資賣超 {today:.0f} 億，環境偏弱',
                'suspend': False}
    elif today > 100:
        return {'score': +3,
                'label': f'✅ 大盤外資買超 {today:.0f} 億，環境有利',
                'suspend': False}
    else:
        return {'score': 0,
                'label': f'🔄 大盤外資中性（{today:.0f} 億）',
                'suspend': False}

def calc_margin_score(margin_today, margin_5d_ago):
    """
    融資增幅監控。
    margin_today: 今日融資餘額（股）
    margin_5d_ago: 5日前融資餘額
    """
    if not margin_5d_ago or margin_5d_ago == 0:
        return {'score': 0, 'label': ''}
    pct = (margin_today - margin_5d_ago) / margin_5d_ago * 100
    if pct >= 30:
        return {'score': -8, 'label': f'❌ 融資5日暴增 +{pct:.1f}%，散戶追高'}
    elif pct >= 15:
        return {'score': -4, 'label': f'⚠️ 融資5日增加 +{pct:.1f}%，留意'}
    elif pct >= 0:
        return {'score':  0, 'label': f'🔄 融資5日增幅 +{pct:.1f}%'}
    else:
        return {'score': +3, 'label': f'✅ 融資5日減少 {pct:.1f}%，籌碼健康'}

def calc_chip_concentration(foreign, trust, volume):
    """
    籌碼集中度 = 法人淨買超 / 成交量。
    """
    if not volume or volume == 0:
        return {'score': 0, 'label': '', 'concentration': 0}
    net_buy = max(0, int(foreign)) + max(0, int(trust))
    conc    = round(net_buy / volume * 100, 1)
    if conc >= 20:
        return {'score': 8, 'label': f'🔥 籌碼集中度 {conc}%，主力強力進場', 'concentration': conc}
    elif conc >= 10:
        return {'score': 5, 'label': f'✅ 籌碼集中度 {conc}%，法人積極布局', 'concentration': conc}
    elif conc >= 5:
        return {'score': 2, 'label': f'🔄 籌碼集中度 {conc}%', 'concentration': conc}
    else:
        return {'score': 0, 'label': f'（籌碼集中度 {conc}%）', 'concentration': conc}

def calc_score(entry):
    """
    綜合積分（v3 → v4：加入 MACD 10 分，漲幅從 15 降為 10，總分維持 100）
    SS >= 85, S >= 68, A >= 52, 其餘淘汰
    """
    score = 0

    # 漲幅（10分）── 2~5% 區間最高分；過度上漲反而扣分
    chg = entry.get('change', 0)
    if   3 <= chg <= 5:   score += 10
    elif 2 <= chg < 3:    score += 8
    elif 5 < chg <= 7:    score += 7
    elif 1 <= chg < 2:    score += 5
    elif chg > 7:         score += 3   # 漲停板 / 接近漲停，難進場

    # 量比（20分）
    vr = entry.get('vol_ratio', 0)
    if   vr >= 3.0: score += 20
    elif vr >= 2.0: score += 15
    elif vr >= 1.5: score += 10
    elif vr >= 1.2: score += 5

    # 法人買超強度（20分）
    foreign = entry.get('foreign', 0)
    trust   = entry.get('trust', 0)
    total   = foreign + trust
    both    = foreign >= 10000 and trust >= 10000
    if both and total >= 500000:   score += 20
    elif both and total >= 100000: score += 15
    elif both:                     score += 10
    elif total >= 100000:          score += 8
    else:                          score += 3

    # 乖離率（20分）── 越低越好（容易回檔買到）
    b  = entry.get('bias') or {}
    bp = b.get('bias_pct')
    if bp is None:         score += 10
    elif bp < 0:           score += 18           # 在均線下方
    elif 0 <= bp <= 3:     score += 20           # 完美區
    elif bp <= 5:          score += 15
    elif bp <= 8:          score += 5
    # bp > 8 → 0 分（過熱）

    # RSI（10分）
    adv = entry.get('adv') or {}
    rsi = adv.get('rsi')
    if rsi is None:        score += 5
    elif 60 <= rsi <= 80:  score += 10
    elif rsi > 80:         score += 5            # 過熱
    elif rsi >= 50:        score += 7

    # 壓力位（10分）
    rs = adv.get('resistance_score', 0)
    if rs == 0:            score += 10           # 無明顯壓力
    elif rs == -0.25:      score += 4            # 接近壓力

    # 位階（5分）
    ps = adv.get('position_score', 0)
    if ps >= 0.5:          score += 5
    elif ps == 0:          score += 3
    elif ps == -0.5:       score += 1

    # MACD（10分）── calc_macd 已直接給 0/5/7/8/10 分
    score += entry.get('macd_score', 5)

    # ── 新增四項加減分 ──

    # 連續買超（+8 ~ -3）
    score += entry.get('consec_score', 0)

    # 大盤環境（+3 ~ -5）
    score += entry.get('market_score', 0)

    # 融資增幅（+3 ~ -8）
    score += entry.get('margin_score', 0)

    # 籌碼集中度（0 ~ +8）
    score += entry.get('chip_score', 0)

    return max(0, score)



def calc_bias_and_entry(df, price):
    """
    計算 10 日乖離率（給評分用）。
    v2 後 entry_price/target/stop_loss 改由 actual_entry 在 T+1 回填，
    此函式不再回傳建議入場價，僅回傳 bias 相關資訊。
    """
    if len(df) < 10:
        return None
    closes = df['close'].astype(float)
    ma10   = closes.tail(10).mean()
    if ma10 == 0:
        return None

    bias_pct = round((price - ma10) / ma10 * 100, 2)

    if bias_pct > 8:
        bias_emoji = '❌'
        bias_label = '過高，不建議追'
    elif bias_pct > 5:
        bias_emoji = '⚠️'
        bias_label = '略高，小心追高'
    elif bias_pct >= 0:
        bias_emoji = '✅'
        bias_label = '理想進場區'
    else:
        bias_emoji = '🔄'
        bias_label = '底部觀察'

    return {
        'bias_pct':   bias_pct,
        'bias_label': bias_label,
        'bias_emoji': bias_emoji,
    }


# ══════════════════════════════════════════════════════
# T+1 撮合：把 fill_status='pending' 的紀錄用 T+1 K 棒判定進場
# ══════════════════════════════════════════════════════
def _fetch_kbars_with_open(sid, yyyymm):
    """
    與 fetch_stock_day_fast 類似，但同時取 open（第 4 欄）。
    回傳含 date, open, high, low, close, volume 的 DataFrame。
    """
    import re as _re
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=8
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    try:
        text  = r.text
        lines = text.splitlines()
        header_i = None
        for i, line in enumerate(lines):
            if '日期' in line and ('收盤' in line or '成交' in line):
                header_i = i
                break
        if header_i is None:
            for i, line in enumerate(lines):
                if _re.search(r'\d{3}/\d{2}/\d{2}', line):
                    header_i = max(0, i - 1)
                    break
        if header_i is None:
            return pd.DataFrame()
        csv_text = '\n'.join(lines[header_i:])
        df = safe_read_csv(csv_text, f'STOCK_DAY-{sid}', min_cols=7)
        if df.empty:
            return pd.DataFrame()
        mask = df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)
        df   = df[mask].copy()
        if df.empty:
            return pd.DataFrame()
        def roc_to_date(s):
            y, m, d = str(s).split('/')
            return datetime(int(y) + 1911, int(m), int(d)).date()
        df['date']   = df.iloc[:, 0].apply(roc_to_date)
        df['open']   = pd.to_numeric(df.iloc[:, 3].astype(str).str.replace(',', ''), errors='coerce')
        df['high']   = pd.to_numeric(df.iloc[:, 4].astype(str).str.replace(',', ''), errors='coerce')
        df['low']    = pd.to_numeric(df.iloc[:, 5].astype(str).str.replace(',', ''), errors='coerce')
        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce')
        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce')
        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        return df[cols].dropna(subset=['date', 'close']).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def get_t1_kbar(sid, screen_date):
    """取 sid 在 screen_date 之後第一個交易日的 K 棒"""
    yyyymm = screen_date.strftime('%Y%m')
    df = _fetch_kbars_with_open(sid, yyyymm)
    after = df[df['date'] > screen_date] if not df.empty else None
    if after is None or after.empty:
        # 試下個月
        next_dt = screen_date.replace(day=28) + timedelta(days=4)
        next_month = next_dt.strftime('%Y%m')
        df2 = _fetch_kbars_with_open(sid, next_month)
        if df2.empty:
            return None
        after = df2[df2['date'] > screen_date]
    if after.empty:
        return None
    row = after.iloc[0]
    return {
        'date':  row['date'],
        'open':  float(row['open']),
        'high':  float(row['high']),
        'low':   float(row['low']),
        'close': float(row['close']),
    }


def get_period_kbars(sid, start_date, end_date):
    """取 sid 在 [start_date, end_date]（含）區間的 K 棒"""
    months = set()
    d = start_date.replace(day=1)
    while d <= end_date:
        months.add(d.strftime('%Y%m'))
        # 下個月
        d = (d + timedelta(days=32)).replace(day=1)
    frames = []
    for ym in sorted(months):
        df = _fetch_kbars_with_open(sid, ym)
        if not df.empty:
            frames.append(df)
        time.sleep(0.5)
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames, ignore_index=True).drop_duplicates(subset=['date'])
    return full[(full['date'] >= start_date) & (full['date'] <= end_date)].reset_index(drop=True)


def fill_pending_t1_entries(today):
    """
    把所有 fill_status='pending' 且 screen_date < today 的紀錄
    用其 T+1 K 棒判定進場結果（成交/未進場），寫回 DB。
    跨 guild 共用（同一個 sid+screen_date 的紀錄會多次更新，但結果相同）。
    """
    if not _DB_OK:
        return
    pendings = _db.get_records_needing_t1_check(today)
    if not pendings:
        print('[T+1撮合] 無待撮合紀錄')
        return
    # 同 (sid, screen_date) 只抓一次 K 棒
    cache = {}
    for r in pendings:
        sid = r['sid']
        sd  = r['screen_date']
        key = (sid, sd)
        if key not in cache:
            cache[key] = get_t1_kbar(sid, sd)
            time.sleep(0.4)  # 避免 TWSE 限速
        kbar = cache[key]
        if kbar is None:
            print(f'[T+1撮合] {sid} {sd} 抓不到 T+1 K 棒，先跳過')
            continue
        # 強勢追漲不接刀：跳空跌破收盤就算 missed
        allow_gap_down = (r.get('chase_mode') != 'strong_chase')
        status, fill_price = _db.determine_t1_fill(
            kbar['open'], kbar['high'], kbar['low'],
            float(r['entry_zone_low']) if r['entry_zone_low'] is not None else None,
            float(r['entry_zone_high']) if r['entry_zone_high'] is not None else None,
            allow_gap_down=allow_gap_down,
        )
        try:
            _db.fill_t1_entry(r['id'], kbar['date'], status, fill_price)
            tag = '✅成交' if status == 'filled' else '❌未進場'
            fp  = f'@{fill_price}' if fill_price else ''
            print(f'[T+1撮合] {sid} {sd} → {kbar["date"]} {tag} {fp}')
        except Exception as e:
            print(f'[T+1撮合] {sid} 寫入失敗：{e}')
    print(f'[T+1撮合] 完成，處理 {len(pendings)} 筆')


INDICATOR_GUIDE = """
━━━━━━━━━━━━━━━━━━━━━━━━
📖 **【指標說明】**
📐 **乖離率（BIAS）**：股價偏離10日均線的幅度
　0~5% ✅ 理想進場　5~8% ⚠️ 略高　>8% ❌ 過高勿追　負值 🔄 底部

📊 **RSI**：動能強弱指標（0~100）
　>80 短線過熱（飆股可能鈍化）　60~80 ✅ 強勢　50~60 普通　<50 ❌ 動能弱

🏔 **壓力位**：前期高點，股價容易在此遇賣壓
　接近壓力區時分批操作，突破壓力才加碼

📍 **位階**：距近期低點的漲幅，越高追高風險越大
　<20% ✅ 剛起漲　20~50% 中等　50~100% ⚠️ 偏高　>100% ❌ 極高

📦 **OBV（能量潮）**：用成交量確認漲勢是否健康
　量價同步 ✅ 健康　OBV背離 ⚠️ 漲勢可能假突破

⛔ **動態停損（2×ATR）**：根據股票波動幅度計算的停損點
　比固定-5%更精準，跌破此價格建議出場

💡 **建議入場價**：考量乖離率、均線、近期低點後的合理買入區間
━━━━━━━━━━━━━━━━━━━━━━━━
"""

def run_analysis():
    # 允許只靠 DB webhook 運作（不強制要求環境變數）
    if not WEBHOOK_URL and not _DB_OK:
        print('[錯誤] 未設定 DISCORD_WEBHOOK 且資料庫未連線，無法發送')
        return

    run_mode = os.environ.get('RUN_MODE', 'auto').strip().lower()
    date_str = get_target_date(run_mode)

    # 取得所有 webhook 供全程使用
    def _get_all_webhooks():
        whs = [WEBHOOK_URL] if WEBHOOK_URL else []
        if _DB_OK:
            try:
                for gw in _db.get_all_webhooks():
                    if gw['webhook_url'] and gw['webhook_url'] not in whs:
                        whs.append(gw['webhook_url'])
            except:
                pass
        return whs

    def _notify_all(msg):
        for wh in _get_all_webhooks():
            try:
                requests.post(wh, json={'username': '川投顧量化系統', 'content': msg}, timeout=10)
            except:
                pass

    _notify_all(f'⏳ 量化分析啟動中（{date_str}），預計 10~20 分鐘後發送結果...')
    now_tw   = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)

    if run_mode == 'preview':
        report_type = '盤前複習'
    elif run_mode == 'close':
        report_type = '盤後結算'
    else:
        report_type = '盤後結算' if now_tw.hour >= DATA_READY_HOUR else '盤前複習'

    print(f"[執行] 模式={run_mode}，日期={date_str}，台灣時間={now_tw.strftime('%H:%M')}")
    t_start = time.time()

    # ── 平行抓取大盤、T86、MI_INDEX ──
    market  = get_market_info(date_str)

    # ── 大盤外資歷史（近3天），用於環境過濾 ──
    _mkt_foreign_hist = []
    try:
        from datetime import datetime as _dt2, timedelta as _td2
        _base_date = _dt2.strptime(date_str, '%Y%m%d').date()
        _checked = 0
        for _i in range(1, 8):
            _d = _base_date - _td2(days=_i)
            if _d.weekday() >= 5:
                continue
            _ds2 = _d.strftime('%Y%m%d')
            _rm2 = safe_get(
                'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS',
                params={'response': 'csv', 'date': _ds2, 'selectType': 'ALLBUT0999'},
                timeout=10, retries=1, wait=3
            )
            if _rm2 and '查詢無資料' not in _rm2.text:
                try:
                    for _ln in _rm2.text.splitlines():
                        if '合計' in _ln:
                            _parts = [v.strip().strip('"').replace(',','') for v in _ln.split(',')]
                            try:
                                _buy  = float(_parts[2]) if len(_parts) > 2 else 0
                                _sell = float(_parts[3]) if len(_parts) > 3 else 0
                                _net  = round((_buy - _sell) / 100000000, 1)
                                _mkt_foreign_hist.insert(0, _net)
                            except:
                                _mkt_foreign_hist.insert(0, 0)
                            break
                except:
                    pass
            _checked += 1
            if _checked >= 3:
                break
    except Exception as _me:
        print(f"[大盤外資歷史] 抓取失敗：{_me}")
    _market_env = calc_market_env(_mkt_foreign_hist) if _mkt_foreign_hist else {'score': 0, 'label': '', 'suspend': False}
    if _market_env.get('suspend'):
        _all_wh = [WEBHOOK_URL] if WEBHOOK_URL else []
        if _DB_OK:
            try:
                for _gw in _db.get_all_webhooks():
                    if _gw['webhook_url'] not in _all_wh:
                        _all_wh.append(_gw['webhook_url'])
            except:
                pass
        for _wh in _all_wh:
            requests.post(_wh, json={'content': f'⚠️ {_market_env["label"]}'}, timeout=10)
        return

        # ── 大盤外資歷史（近3天），用於環境過濾 ──
        _mkt_foreign_hist = []
        try:
            from datetime import datetime as _dt2, timedelta as _td2
            _base_date = _dt2.strptime(date_str, '%Y%m%d').date()
            _checked = 0
            for _i in range(1, 8):
                _d = _base_date - _td2(days=_i)
                if _d.weekday() >= 5:
                    continue
                _ds2 = _d.strftime('%Y%m%d')
                _rm2 = safe_get(
                    'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS',
                    params={'response': 'csv', 'date': _ds2, 'selectType': 'ALLBUT0999'},
                    timeout=10, retries=1, wait=3
                )
                if _rm2 and '查詢無資料' not in _rm2.text:
                    try:
                        for _ln in _rm2.text.splitlines():
                            if '合計' in _ln:
                                _parts = [v.strip().strip('"').replace(',','') for v in _ln.split(',')]
                                # 取淨買超（買進-賣出），欄位約在 index 2~4
                                try:
                                    _buy  = float(_parts[2]) if len(_parts) > 2 else 0
                                    _sell = float(_parts[3]) if len(_parts) > 3 else 0
                                    _net  = round((_buy - _sell) / 100000000, 1)
                                    _mkt_foreign_hist.insert(0, _net)
                                except:
                                    _mkt_foreign_hist.insert(0, 0)
                                break
                    except:
                        pass
                _checked += 1
                if _checked >= 3:
                    break
        except Exception as _me:
            print(f"[大盤外資歷史] 抓取失敗：{_me}")
        _market_env = calc_market_env(_mkt_foreign_hist) if _mkt_foreign_hist else {'score': 0, 'label': '', 'suspend': False}
        if _market_env.get('suspend'):
            _all_wh = []
            if WEBHOOK_URL:
                _all_wh.append(WEBHOOK_URL)
            if _DB_OK:
                try:
                    for _gw in _db.get_all_webhooks():
                        if _gw['webhook_url'] not in _all_wh:
                            _all_wh.append(_gw['webhook_url'])
                except:
                    pass
            for _wh in _all_wh:
                requests.post(_wh, json={'content': f'⚠️ {_market_env["label"]}'}, timeout=10)
            return


    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=40, retries=5, wait=20
    )
    r_price = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=40, retries=5, wait=20
    )

    if r_inst is None or r_price is None:
        which = 'T86(法人)' if r_inst is None else 'MI_INDEX(價格)'
        requests.post(WEBHOOK_URL, json={
            'content': (
                f'❌ 無法取得個股資料（{date_str}）\n'
                f'失敗來源：{which}\n'
                f'已重試 5 次，可能是 TWSE 暫時封鎖海外 IP，請稍後用 /run 手動重試。'
            )
        }, timeout=15)
        return
    if '查詢無資料' in r_inst.text or '查詢無資料' in r_price.text:
        requests.post(WEBHOOK_URL, json={'content': f'ℹ️ {date_str} 查無資料（假日或尚未更新）。'}, timeout=15)
        return

    try:
        # ── 解析 T86 ──
        df_i = parse_t86(r_inst.text)
        if df_i.empty:
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ T86 解析失敗（{date_str}）\n前300字：\n{r_inst.text[:300]}'
            }, timeout=15)
            return

        # parse_t86 已用固定索引解析，欄位名稱固定為 _foreign/_trust/_total
        # 先確認欄位存在
        for required_col in ['_foreign', '_trust', '_total']:
            if required_col not in df_i.columns:
                requests.post(WEBHOOK_URL, json={
                    'content': (
                        f'❌ T86 欄位 {required_col} 不存在（{date_str}）\n'
                        f'現有欄位：{list(df_i.columns)}\n'
                        f'T86前300字：\n{r_inst.text[:300]}'
                    )
                }, timeout=15)
                return

        col_foreign = '_foreign'
        col_trust   = '_trust'
        col_total   = '_total'
        print(f"[T86] 外資非零：{(df_i['_foreign']!=0).sum()} 投信非零：{(df_i['_trust']!=0).sum()}")

        # 數據驗證：外資和投信不應同時全為0
        if (df_i['_foreign'] == 0).all() and (df_i['_trust'] == 0).all():
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ T86 法人數據異常（外資+投信全為0），請查看 Actions log。'
            }, timeout=15)
            return

        # ── 解析 MI_INDEX ──
        price_text = r_price.text
        start_idx  = price_text.find('"證券代號"')
        if start_idx == -1:
            start_idx = price_text.find('證券代號')
        if start_idx == -1:
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ MI_INDEX 找不到表頭（{date_str}）\n前300字：{price_text[:300]}'
            }, timeout=15)
            return

        df_p = safe_read_csv(price_text[start_idx:], 'MI_INDEX-PRICE', min_cols=5)
        if df_p.empty:
            requests.post(WEBHOOK_URL, json={'content': f'❌ MI_INDEX 解析失敗（{date_str}）。'}, timeout=15)
            return

        df_p = df_p.dropna(thresh=5)
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        print(f"[MI_INDEX] {len(df_p)} 檔")

        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')
        print(f"[合併] {len(df)} 檔")

        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns if '漲跌價差' in str(c) or
                         ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or '漲跌符號' in str(c)), None)

        if not all([col_close, col_diff]):
            raise ValueError(f"找不到收盤/漲跌欄：{list(df.columns)}")

        # ══════════════════════════════════════
        # 第一輪：基本條件（1.收盤價 2.漲跌幅 3.法人買超）
        # ══════════════════════════════════════
        candidates = []
        for _, row in df.iterrows():
            try:
                sid   = row['sid_clean']
                name  = str(row.get('證券名稱', row.iloc[1])).strip()
                price = pd.to_numeric(str(row[col_close]).replace(',', ''), errors='coerce')
                diff  = pd.to_numeric(str(row[col_diff]).replace(',', ''),  errors='coerce')

                # 1. 收盤價下限
                if pd.isna(price) or pd.isna(diff) or price < MIN_PRICE:
                    continue

                if col_sign:
                    s = str(row[col_sign])
                    diff = -abs(diff) if ('−' in s or s.strip() == '-') else abs(diff)

                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0

                # 2. 漲幅需 ≥ 1%（X 級已移除）
                if change < GRADE_A:
                    continue

                inst_row = df_i[df_i['sid_clean'] == sid]
                if inst_row.empty:
                    continue

                foreign = float(inst_row[col_foreign].values[0]) if col_foreign else 0.0
                trust   = float(inst_row[col_trust].values[0])   if col_trust   else 0.0
                total   = foreign + trust

                # 3. 法人買超下限（雙買超優先，單方買超門檻提高）
                both_buy   = foreign >= MIN_FOREIGN_SHARE and trust >= MIN_TRUST_SHARE
                single_buy = total >= MIN_INST_SHARE_SINGLE
                if not (both_buy or single_buy):
                    continue

                candidates.append({
                    'sid': sid, 'name': name,
                    'price': price, 'change': change,
                    'foreign': int(foreign), 'trust': int(trust),
                    'total': int(total),
                })
            except:
                continue

        print(f"[過濾1] 基本條件通過：{len(candidates)} 檔")

        # 4. 候選數量保護
        if len(candidates) > MAX_CANDIDATES:
            candidates.sort(key=lambda e: e['total'], reverse=True)
            candidates = candidates[:MAX_CANDIDATES]
            print(f"[過濾4] 截斷至前 {MAX_CANDIDATES} 名（依法人買超）")

        # ══════════════════════════════════════
        # 第二輪：量比（5）→ EMA（6）
        # 量比用當日資料即可，先過濾，減少需要抓歷史K棒的數量
        # ══════════════════════════════════════
        months      = prev_months(date_str, n=7)
        target_date = datetime.strptime(date_str, '%Y%m%d').date()
        print(f"[EMA] 月份清單：{months}")

        ss_list, s_list, a_list = [], [], []
        chase_list = []   # 強勢追漲（連續≥3日漲停且通過5項門檻）
        watch_list = []   # 觀察名單（連續≥3日漲停且通過4項門檻）

        for idx_c, entry in enumerate(candidates):
            sid = entry['sid']
            try:
                t0 = time.time()
                df_hist = build_history_fast(sid, months)
                elapsed = time.time() - t0

                # 歷史資料不足則跳過
                if df_hist.empty or 'date' not in df_hist.columns or len(df_hist) < 10:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} 歷史資料不足 ✗ {elapsed:.1f}s")
                    continue

                # 5. 量比（用歷史資料計算，含當日）
                vol_ratio = calc_volume_ratio(df_hist, target_date)
                if vol_ratio < VOLUME_RATIO_MIN:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} 量比{vol_ratio:.2f} ✗ {elapsed:.1f}s")
                    continue

                # 6. EMA 多頭排列（含備援）
                is_bull, ema_mode = check_ema_bull(df_hist)
                if not is_bull:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} EMA{ema_mode} ✗ {elapsed:.1f}s")
                    continue

                entry['vol_ratio'] = vol_ratio
                entry['ema_mode']  = ema_mode
                # 計算乖離率與入場建議
                bias_info = calc_bias_and_entry(df_hist, entry['price'])
                entry['bias'] = bias_info
                # 計算進階指標（RSI / ATR / 壓力位 / OBV）
                adv = calc_advanced_indicators(df_hist, entry['price'])
                entry['adv'] = adv

                # 大盤環境分數（全股同一個）
                entry['market_score'] = _market_env.get('score', 0)

                # 籌碼集中度（當日成交量從 df_hist 取最後一筆）
                _vol_today = int(df_hist['volume'].iloc[-1]) if not df_hist.empty else 0
                _chip = calc_chip_concentration(entry['foreign'], entry['trust'], _vol_today)
                entry['chip_score'] = _chip['score']
                entry['chip_label'] = _chip['label']

                # 連買天數：已移除（資料來源不可靠）
                entry['consec_score'] = 0
                entry['consec_label'] = ''

                # 融資增幅
                try:
                    _margin = fetch_margin_change(sid, date_str)
                    entry['margin_score'] = _margin['score']
                    entry['margin_label'] = _margin['label']
                except Exception as _me2:
                    entry['margin_score'] = 0
                    entry['margin_label'] = ''
                    print(f"[融資] {sid} 失敗：{_me2}")

                # MACD（10 分；calc_macd 會回傳 macd_score/dif/dea/hist/expanding/cross_up）
                _macd_info = calc_macd(df_hist)
                entry['macd_score']     = _macd_info['macd_score']
                entry['macd_label']     = _macd_info['macd_label']
                entry['macd_info']      = _macd_info

                # 連續漲停判定 + 5項追漲門檻檢查
                consec = count_consecutive_limit_ups(df_hist)
                entry['consec_limit_up'] = consec
                if consec >= 3:
                    chase = check_strong_chase(entry, _macd_info, entry.get('market_score', 0))
                    entry['chase_check'] = chase
                    if chase['passed'] >= 5:
                        entry['chase_mode'] = 'strong_chase'
                    elif chase['passed'] >= 4:
                        entry['chase_mode'] = 'watch'
                    else:
                        # 連續漲停但條件不夠，跳過（不浪費 SS/S/A 名額）
                        print(f"  [{idx_c+1}/{len(candidates)}] {sid} 連續{consec}日漲停但只過{chase['passed']}/5 項 ✗")
                        continue
                else:
                    entry['chase_mode'] = 'normal'

                change = entry['change']
                score  = calc_score(entry)
                entry['score'] = score

                # 分級：strong_chase 與 watch 走專屬清單，其他依評分
                if entry['chase_mode'] == 'strong_chase':
                    chase_list.append(entry)
                elif entry['chase_mode'] == 'watch':
                    watch_list.append(entry)
                elif score >= 85: ss_list.append(entry)
                elif score >= 68:  s_list.append(entry)
                elif score >= 52:  a_list.append(entry)
                # <52 淘汰

                print(f"  [{idx_c+1}/{len(candidates)}] {sid} {entry['name']} ✓ "
                      f"漲{change}% 量比{vol_ratio:.2f} EMA:{ema_mode} mode:{entry['chase_mode']} {elapsed:.1f}s")

            except Exception as e:
                print(f"  [{idx_c+1}/{len(candidates)}] {sid} 錯誤：{e}")

        for lst in [ss_list, s_list, a_list, chase_list, watch_list]:
            lst.sort(key=lambda e: e.get('score', 0), reverse=True)

        # 寫入資料庫（對所有已設定伺服器各存一份）
        if _DB_OK:
            try:
                from datetime import date as _date
                _sd = _date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
                _all = []
                for _e, _g in (
                    [(e, 'SS')    for e in ss_list]    +
                    [(e, 'S')     for e in s_list]     +
                    [(e, 'A')     for e in a_list]     +
                    [(e, 'CHASE') for e in chase_list] +
                    [(e, 'WATCH') for e in watch_list]
                ):
                    _e2 = dict(_e); _e2['grade'] = _g
                    _all.append(_e2)
                if _all:
                    # 轉換 numpy 型別為 Python 原生型別
                    def _to_native(v):
                        import numpy as _np
                        if isinstance(v, (_np.integer,)): return int(v)
                        if isinstance(v, (_np.floating,)): return float(v)
                        return v
                    _all_clean = []
                    for _e in _all:
                        _all_clean.append({k: _to_native(v) for k, v in _e.items()
                                           if k not in ('bias',)})
                        if _e.get('bias'):
                            _all_clean[-1]['bias'] = {k: _to_native(v)
                                                      for k, v in _e['bias'].items()}
                    _guilds = _db.get_all_webhooks()
                    for _gw in _guilds:
                        try:
                            _db.save_screen_records(_all_clean, _sd, _gw['guild_id'])
                        except Exception as _ge:
                            print(f"[DB] guild {_gw['guild_id']} 寫入失敗：{_ge}")
                    print(f"[DB] 儲存 {len(_all_clean)} 筆至 {len(_guilds)} 個伺服器")
            except Exception as _dbe:
                print(f"[DB] 寫入失敗：{_dbe}")

            # T+1 撮合：把今天以前 fill_status='pending' 的紀錄，用其 T+1 K 棒判定進場
            try:
                from datetime import date as _date2
                _today = _date2(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
                fill_pending_t1_entries(_today)
            except Exception as _fe:
                print(f"[T+1撮合] 失敗：{_fe}")

            # 匯出至 Web Dashboard（GitHub Pages）
            try:
                import web_export as _we
                _we.export_dashboard()
            except Exception as _we_e:
                print(f"[Web] Dashboard 匯出失敗：{_we_e}")

        total_elapsed = time.time() - t_start
        print(f"[完成] SS={len(ss_list)} S={len(s_list)} A={len(a_list)}，總耗時={total_elapsed:.0f}秒")

        # ══════════════════════════════════════
        # 組裝 Discord 訊息
        # ══════════════════════════════════════
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign      = '+' if e['change'] >= 0 else ''
            ema_tag   = '(備援EMA)' if e.get('ema_mode') == 'fallback' else ''
            b         = e.get('bias')
            adv       = e.get('adv', {})
            score_str = f" {e['score']}分" if 'score' in e else ''
            lines = [
                f"{emoji_open}【{grade_label}{score_str}】{e['sid']} {e['name']}{emoji_close}",
                '',
                '🔹基本資料',
                f"收盤價格：{e['price']}　漲幅：{sign}{e['change']}%　量比：{e.get('vol_ratio', 0):.1f}x{ema_tag}",
                f"外資：{fmt_share(e['foreign'])}股　投信：{fmt_share(e['trust'])}股",
            ]
            if b:
                sp = '+' if b['bias_pct'] >= 0 else ''
                lines.append(f"乖離率（10日）：{sp}{b['bias_pct']}%　{b['bias_emoji']} {b['bias_label']}")
            # 進場區間（根據 chase_mode）
            mode = e.get('chase_mode', 'normal')
            if mode == 'strong_chase':
                zl = round(e['price'] * 1.00, 1)
                zh = round(e['price'] * 1.07, 1)
                lines += ['', f"🚀強勢追漲（連續{e.get('consec_limit_up', 0)}日漲停）",
                          f"進場區間：{zl:,.1f} ~ {zh:,.1f} 元（容忍隔日跳空 0~7%）",
                          f"➡️ T+1 開盤在此區間以開盤價買；跳空 >7% 則放棄；跳空跌破收盤視為趨勢反轉，不接刀"]
            elif mode == 'watch':
                lines += ['', f"⚠️觀察名單（連續{e.get('consec_limit_up', 0)}日漲停但條件不足）",
                          f"➡️ 5 項追漲門檻僅通過 {e.get('chase_check', {}).get('passed', 0)}/5，**不買入**只觀察"]
                if e.get('chase_check', {}).get('reasons'):
                    lines += [f"  {r}" for r in e['chase_check']['reasons']]
            else:
                zl = round(e['price'] * 0.97, 1)
                zh = round(e['price'] * 1.00, 1)
                lines += ['', '🎯建議進場區（限價單）',
                          f"進場區間：{zl:,.1f} ~ {zh:,.1f} 元",
                          f"➡️ 隔日 T+1 觸及才算進場；以實際成交價為準，目標 +5% / +10%、停損 -5%"]
            if adv.get('atr_stop'):
                lines.append(f"參考動態停損（2×ATR）：{adv['atr_stop']:,.1f} 元（{adv['atr_pct']}%）")
            has_adv = any([adv.get('rsi_label'), adv.get('resistance_label'),
                           adv.get('position_label'), adv.get('obv_label'),
                           (e.get('chip_label') and e.get('chip_score', 0) > 0),
                           e.get('margin_label'), e.get('macd_label')])
            if has_adv:
                lines += ['', '📊輔助數據']
                if e.get('macd_label'):
                    lines.append(f"MACD：{e['macd_label']}")
                if adv.get('rsi_label'):
                    lines.append(f"RSI：{adv['rsi_label']}")
                if adv.get('resistance_label'):
                    lines.append(f"壓力位：{adv['resistance_label']}")
                if adv.get('position_label'):
                    lines.append(f"位階：{adv['position_label']}")
                if adv.get('obv_label'):
                    lines.append(f"OBV：{adv['obv_label']}")
                if e.get('chip_label') and e.get('chip_score', 0) > 0:
                    lines.append(f"籌碼：{e['chip_label']}")
                if e.get('margin_label'):
                    lines.append(f"融資：{e['margin_label']}")
            lines.append('─' * 25)
            return '\n'.join(lines)

        if market:
            sd = '+' if market['diff'] >= 0 else ''
            sp = '+' if market['pct']  >= 0 else ''
            mkt_line = f"加權指數：{market['close']:,.2f}　({sd}{market['diff']:.2f} / {sp}{market['pct']:.2f}%)"
        else:
            mkt_line = "加權指數：資料未取得"

        header = (
            f"🔶【{report_type}】🔶\n"
            f"日期：{date_str}\n"
            f"{mkt_line}\n\n"
            f"{'='*25}"
        )

        sections = []
        if ss_list:
            sections += [stock_block(e, '🔥', 'SS', '🔥') for e in ss_list[:10]]
        if s_list:
            sections += [stock_block(e, '💎', 'S',  '💎') for e in  s_list[:10]]
        if a_list:
            sections += [stock_block(e, '📈', 'A',  '📈') for e in  a_list[:10]]
        if chase_list:
            sections += [stock_block(e, '🚀', 'CHASE', '🚀') for e in chase_list[:5]]
        if watch_list:
            sections += [stock_block(e, '⚠️', 'WATCH', '⚠️') for e in watch_list[:5]]
        if not sections:
            sections.append('（今日無符合條件之標的）')

        full_message = header + '\n\n' + '\n\n'.join(sections) + '\n\n' + INDICATOR_GUIDE

        chunks, buf = [], ''
        for line in full_message.splitlines(keepends=True):
            if len(buf) + len(line) > 1900 and buf:
                chunks.append(buf)
                buf = ''
            buf += line
        if buf:
            chunks.append(buf)

        # 發送至所有已設定伺服器
        webhooks = _get_all_webhooks()

        for wh in webhooks:
            for i, chunk in enumerate(chunks):
                requests.post(
                    wh,
                    json={'username': '川投顧量化系統', 'content': chunk},
                    timeout=15
                )
                if i < len(chunks) - 1:
                    time.sleep(0.5)
            time.sleep(1)



    except Exception as e:
        import traceback
        print(f"[主程式錯誤]\n{traceback.format_exc()}")
        _notify_all(f'❌ 系統錯誤：{e}')

if __name__ == '__main__':
    run_analysis()
