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
# 股市趨勢新聞（Google News RSS，台股相關）
# ══════════════════════════════════════════════════════════
def fetch_stock_news(count=10):
    """
    從 Google News RSS 抓取台股相關新聞標題。
    回傳最新 count 則新聞的清單 [{'title': ..., 'source': ...}]。
    """
    import xml.etree.ElementTree as ET
    rss_url = 'https://news.google.com/rss/search?q=台股+股市&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
    r = safe_get(rss_url, timeout=15, retries=2, wait=5)
    if r is None:
        print("[新聞] 抓取失敗")
        return []
    try:
        root = ET.fromstring(r.content)
        items = root.findall('.//item')
        news = []
        for item in items[:count]:
            title  = item.findtext('title', '').strip()
            source = item.findtext('source', '').strip()
            # 去掉標題末尾的來源名稱（Google News 格式：標題 - 來源）
            if ' - ' in title:
                title, source = title.rsplit(' - ', 1)
            news.append({'title': title.strip(), 'source': source.strip()})
        print(f"[新聞] 取得 {len(news)} 則")
        return news
    except Exception as e:
        print(f"[新聞] 解析失敗：{e}")
        return []

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



def calc_consecutive_buy(sid, df_i_history):
    """
    計算法人連續買超天數。
    df_i_history: 近5天的法人資料 list，每個元素 {'foreign': int, 'trust': int}
    由舊到新，最後一筆是今日。
    """
    if not df_i_history:
        return {'foreign_days': 0, 'trust_days': 0, 'score': 0, 'label': ''}

    foreign_days = 0
    trust_days   = 0
    for day in reversed(df_i_history):
        if day.get('foreign', 0) > 0: foreign_days += 1
        else: break
    for day in reversed(df_i_history):
        if day.get('trust', 0) > 0: trust_days += 1
        else: break

    score = 0
    if   foreign_days >= 5: score += 8
    elif foreign_days >= 3: score += 5
    elif foreign_days >= 2: score += 2
    # 今日剛買但昨日賣超，可信度低
    if foreign_days == 1 and len(df_i_history) >= 2:
        if df_i_history[-2].get('foreign', 0) < 0:
            score -= 3

    label = f'外資連買 {foreign_days} 日　投信連買 {trust_days} 日'
    return {'foreign_days': foreign_days, 'trust_days': trust_days,
            'score': score, 'label': label}

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
    綜合積分（基礎100分 + 四項加減分）
    SS >= 85, S >= 68, A >= 52, 其餘淘汰
    """
    score = 0

    # 漲幅（25分）
    chg = entry.get('change', 0)
    if   chg >= 7:   score += 25
    elif chg >= 5:   score += 20
    elif chg >= 3.5: score += 15
    elif chg >= 2:   score += 10
    elif chg >= 1:   score += 5

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

    # 乖離率（15分）
    b  = entry.get('bias') or {}
    bp = b.get('bias_pct')
    if bp is None:         score += 8
    elif 0 <= bp <= 5:     score += 15
    elif bp < 0:           score += 10
    elif bp <= 8:          score += 5

    # RSI（10分）
    adv = entry.get('adv') or {}
    rsi = adv.get('rsi')
    if rsi is None:        score += 5
    elif 60 <= rsi <= 80:  score += 10
    elif rsi > 80:         score += 8
    elif rsi >= 50:        score += 5

    # 壓力位（5分）
    rs = adv.get('resistance_score', 0)
    if rs == 0:            score += 5
    elif rs == -0.25:      score += 2

    # 位階（5分）
    ps = adv.get('position_score', 0)
    if ps >= 0.5:          score += 5
    elif ps == 0:          score += 3
    elif ps == -0.5:       score += 1

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
    計算 10 日乖離率與建議入場價、目標價、停損價。
    回傳 dict：bias_pct, bias_label, bias_emoji,
               entry_price, target1, target2, stop_loss
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

    # 建議入場價：現價×0.98、MA10×1.02、近3日最低，取最低
    recent3_low  = df['close'].tail(3).min()
    candidate_a  = round(price * 0.98, 1)
    candidate_b  = round(ma10 * 1.02, 1)
    candidate_c  = round(float(recent3_low), 1)
    entry_price  = min(candidate_a, candidate_b, candidate_c)

    # 目標價（以 MA10 為基準算乖離）
    target1   = round(ma10 * 1.08, 1)
    target2   = round(ma10 * 1.12, 1)
    stop_loss = round(price * 0.95, 1)

    return {
        'bias_pct':   bias_pct,
        'bias_label': bias_label,
        'bias_emoji': bias_emoji,
        'entry_price': entry_price,
        'target1':    target1,
        'target2':    target2,
        'stop_loss':  stop_loss,
    }


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
    if not WEBHOOK_URL:
        print('[錯誤] 未設定 DISCORD_WEBHOOK 環境變數')
        return

    run_mode = os.environ.get('RUN_MODE', 'auto').strip().lower()
    date_str = get_target_date(run_mode)
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

                # 連買天數：需要歷史T86，目前只有當日，暫設為0，待後續加入
                entry['consec_score'] = 0
                entry['consec_label'] = ''

                # 融資：暫設為0（需要額外API），待後續加入
                entry['margin_score'] = 0
                entry['margin_label'] = ''

                change = entry['change']

                # 積分制分級
                score = calc_score(entry)
                entry['score'] = score
                if   score >= 85: ss_list.append(entry)
                elif score >= 68:  s_list.append(entry)
                elif score >= 52:  a_list.append(entry)
                # <50 淘汰，不加入任何列表

                print(f"  [{idx_c+1}/{len(candidates)}] {sid} {entry['name']} ✓ 漲{change}% 量比{vol_ratio:.2f} EMA:{ema_mode} {elapsed:.1f}s")

            except Exception as e:
                print(f"  [{idx_c+1}/{len(candidates)}] {sid} 錯誤：{e}")

        for lst in [ss_list, s_list, a_list]:
            lst.sort(key=lambda e: e.get('score', 0), reverse=True)

        # 寫入資料庫（對所有已設定伺服器各存一份）
        if _DB_OK:
            try:
                from datetime import date as _date
                _sd = _date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
                _all = []
                for _e, _g in (
                    [(e, 'SS') for e in ss_list] +
                    [(e, 'S')  for e in s_list]  +
                    [(e, 'A')  for e in a_list]
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

        total_elapsed = time.time() - t_start
        print(f"[完成] SS={len(ss_list)} S={len(s_list)} A={len(a_list)}，總耗時={total_elapsed:.0f}秒")

        # ══════════════════════════════════════
        # 組裝 Discord 訊息
        # ══════════════════════════════════════
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign    = '+' if e['change'] >= 0 else ''
            ema_tag = '(備援EMA)' if e.get('ema_mode') == 'fallback' else ''
            b       = e.get('bias')
            adv     = e.get('adv', {})
            score_str = f"　{e['score']}分" if 'score' in e else ''
            lines   = [
                f"{emoji_open}【{grade_label}{score_str}】{e['sid']} {e['name']}{emoji_close}",
                f"🔹收盤價格：{e['price']}　漲幅：{sign}{e['change']}%　量比：{e.get('vol_ratio', 0):.1f}x{ema_tag}",
                f"🔹外資：{fmt_share(e['foreign'])}股　投信：{fmt_share(e['trust'])}股",
            ]
            if b:
                sp = '+' if b['bias_pct'] >= 0 else ''
                lines.append(f"📐 乖離率（10日）：{sp}{b['bias_pct']}%　{b['bias_emoji']} {b['bias_label']}")
                lines.append(f"💡 建議入場：{b['entry_price']:,.1f} 元")
                lines.append(f"🎯 目標一：{b['target1']:,.1f} 元　目標二：{b['target2']:,.1f} 元")
            # ATR 動態停損（優先於固定-5%）
            if adv.get('atr_stop'):
                lines.append(f"⛔ 動態停損（2×ATR）：{adv['atr_stop']:,.1f} 元（{adv['atr_pct']}%）")
            elif b:
                lines.append(f"⛔ 停損參考：{b['stop_loss']:,.1f} 元（-5%）")
            # 進階指標
            if adv.get('rsi_label'):
                lines.append(f"📊 RSI：{adv['rsi_label']}")
            if adv.get('resistance_label'):
                lines.append(f"🏔 壓力位：{adv['resistance_label']}")
            if adv.get('position_label'):
                lines.append(f"📍 位階：{adv['position_label']}")
            if adv.get('obv_label'):
                lines.append(f"📦 OBV：{adv['obv_label']}")
            if e.get('chip_label') and e.get('chip_score', 0) > 0:
                lines.append(f"💎 籌碼：{e['chip_label']}")
            if e.get('consec_label'):
                lines.append(f"📅 連買：{e['consec_label']}")
            if e.get('margin_label'):
                lines.append(f"💳 融資：{e['margin_label']}")
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
        webhooks = [WEBHOOK_URL] if WEBHOOK_URL else []
        if _DB_OK:
            try:
                for gw in _db.get_all_webhooks():
                    wh = gw['webhook_url']
                    if wh and wh not in webhooks:
                        webhooks.append(wh)
            except Exception as _we:
                print(f"[DB] 取得 webhook 失敗：{_we}")

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

        # ── 股市趨勢新聞（股票清單發送完畢後獨立發送）──
        news_list = fetch_stock_news(count=10)
        if news_list:
            news_lines = ['📰 **【台股趨勢新聞】**\n' + '─'*25]
            for i, n in enumerate(news_list, 1):
                source_tag = f"　_{n['source']}_" if n['source'] else ''
                news_lines.append(f"{i}. {n['title']}{source_tag}")
            news_msg = '\n'.join(news_lines)
            requests.post(
                WEBHOOK_URL,
                json={'username': '川投顧量化系統', 'content': news_msg},
                timeout=15
            )
        else:
            print("[新聞] 無資料，跳過發送")

    except Exception as e:
        import traceback
        print(f"[主程式錯誤]\n{traceback.format_exc()}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()