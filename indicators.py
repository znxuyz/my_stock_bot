"""
基礎技術指標：EMA、RSI、ATR、OBV、MACD、量比、乖離率。
這層只做數學運算，輸入 DataFrame、輸出純值或 dict，不打 TWSE。
"""
import pandas as pd

import config


def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def check_ema_bull(df):
    """
    EMA 多頭排列判斷。
      主要：20EMA > 60EMA > 120EMA（資料 ≥ 120 筆）
      備援：10EMA > 20EMA > 60EMA（資料 60~119 筆）
    回 (is_bull, mode)，mode 為 'full' / 'fallback' / 'insufficient'
    """
    if len(df) < config.EMA_FALLBACK_MIN:
        return False, 'insufficient'
    closes = df['close'].astype(float)
    if len(df) >= config.EMA_LONG2:
        ema20  = calc_ema(closes, config.EMA_MID).iloc[-1]
        ema60  = calc_ema(closes, config.EMA_LONG1).iloc[-1]
        ema120 = calc_ema(closes, config.EMA_LONG2).iloc[-1]
        return ema20 > ema60 > ema120, 'full'
    ema10 = calc_ema(closes, config.EMA_SHORT).iloc[-1]
    ema20 = calc_ema(closes, config.EMA_MID).iloc[-1]
    ema60 = calc_ema(closes, config.EMA_LONG1).iloc[-1]
    return ema10 > ema20 > ema60, 'fallback'


def calc_volume_ratio(df, target_date):
    """當日量 ÷ 近 5 日均量。資料不足 6 天回 0.0。"""
    df = df[df['date'] <= target_date].reset_index(drop=True)
    if len(df) < 6:
        return 0.0
    today_vol = df['volume'].iloc[-1]
    avg5      = df['volume'].iloc[-6:-1].mean()
    return round(today_vol / avg5, 2) if avg5 > 0 else 0.0


def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))


def calc_atr(df, period=14):
    h, l, pc = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def calc_obv(df):
    """量價同步指標。向量化版（替代原本的逐列 for 迴圈）。"""
    if len(df) == 0:
        return pd.Series([], dtype=float)
    close = df['close'].astype(float)
    vol   = df['volume'].astype(float)
    direction = close.diff().fillna(0)
    signed = vol.where(direction > 0, -vol.where(direction < 0, 0))
    return signed.cumsum()


def calc_macd(df, fast=None, slow=None, signal=None):
    """
    MACD：DIF = EMA(fast) - EMA(slow)；DEA = EMA(DIF, signal)；Hist = DIF - DEA。
    v6.2 預設 (8, 17, 5)。
    回傳 dict 含 macd_score (0~10) / macd_label / dif / dea / hist / expanding / cross_up
    """
    fast   = fast   if fast   is not None else config.MACD_FAST
    slow   = slow   if slow   is not None else config.MACD_SLOW
    signal = signal if signal is not None else config.MACD_SIGNAL

    if len(df) < slow + signal:
        return {'macd_score': 5, 'macd_label': '⚪ MACD 資料不足',
                'dif': None, 'dea': None, 'hist': None,
                'expanding': None, 'cross_up': False}

    closes   = df['close'].astype(float)
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif  = ema_fast - ema_slow
    dea  = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea

    last_dif  = float(dif.iloc[-1])
    last_dea  = float(dea.iloc[-1])
    last_hist = float(hist.iloc[-1])
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 else 0.0

    cross_up = False
    for i in range(max(1, len(dif) - 3), len(dif)):
        if dif.iloc[i - 1] <= dea.iloc[i - 1] and dif.iloc[i] > dea.iloc[i]:
            cross_up = True
            break
    expanding = last_hist > prev_hist

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
        'dif':  round(last_dif,  4),
        'dea':  round(last_dea,  4),
        'hist': round(last_hist, 4),
        'expanding': expanding, 'cross_up': cross_up,
    }


def calc_bias_and_entry(df, price):
    """
    10 日乖離率（評分用）。
    v2 起目標價 / 停損改用 actual_entry × 倍率，不在這裡產生。
    """
    if len(df) < 10:
        return None
    closes = df['close'].astype(float)
    ma10 = closes.tail(10).mean()
    if ma10 == 0:
        return None
    bias_pct = round((price - ma10) / ma10 * 100, 2)

    if bias_pct > 8:
        emoji, label = '❌', '過高，不建議追'
    elif bias_pct > 5:
        emoji, label = '⚠️', '略高，小心追高'
    elif bias_pct >= 0:
        emoji, label = '✅', '理想進場區'
    else:
        emoji, label = '🔄', '底部觀察'
    return {'bias_pct': bias_pct, 'bias_label': label, 'bias_emoji': emoji}


# ─────────── v6.2 新增 ───────────

def calc_5d_cumulative_change(df):
    """近 5 日累積漲幅（%）= (今收 / 5 個交易日前收 - 1) × 100。
    需 ≥ 6 筆資料；不足回 None。"""
    if len(df) < 6:
        return None
    closes = df['close'].astype(float)
    base = closes.iloc[-6]
    if base == 0:
        return None
    return round((closes.iloc[-1] / base - 1) * 100, 2)


def calc_5d_price_range(df):
    """近 5 日 high/low 振幅（%）= (max_high / min_low - 1) × 100。
    需 ≥ 5 筆資料；不足或 low ≤ 0 回 None。"""
    if len(df) < 5:
        return None
    recent = df.tail(5)
    h = recent['high'].astype(float).max()
    l = recent['low'].astype(float).min()
    if l <= 0:
        return None
    return round((h / l - 1) * 100, 2)


def count_limit_ups_in_window(df, window=10, threshold=9.5):
    """近 N 個交易日內漲停（≥ threshold%）次數。資料不足回 0。"""
    if len(df) < window + 1:
        return 0
    closes = df['close'].astype(float).values
    cnt = 0
    start = max(1, len(closes) - window)
    for i in range(start, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        if (closes[i] - prev) / prev * 100 >= threshold:
            cnt += 1
    return cnt


def calc_bias_20(df):
    """20 日乖離率（%）= (今收 - MA20) / MA20 × 100。資料不足回 None。"""
    if len(df) < 20:
        return None
    closes = df['close'].astype(float)
    ma20 = closes.tail(20).mean()
    if ma20 == 0:
        return None
    return round((closes.iloc[-1] - ma20) / ma20 * 100, 2)


def calc_ma_slope_5d(df, period=10):
    """N 日 MA 的近 5 日斜率（>0 = 上彎）。資料不足回 None。"""
    if len(df) < period + 4:
        return None
    closes = df['close'].astype(float)
    ma = closes.rolling(period).mean().dropna()
    if len(ma) < 5:
        return None
    recent = ma.tail(5).values
    return float(recent[-1] - recent[0])


def calc_daily_amount(df):
    """日成交金額（元）= close × volume × 1000。空 df 回 0。"""
    if df.empty:
        return 0.0
    return float(df['close'].iloc[-1]) * float(df['volume'].iloc[-1]) * 1000


def calc_vol_vs_60d_ratio(df):
    """今日量 / 過去 60 日均量（不含今日）。資料不足回 None。"""
    if len(df) < 61:
        return None
    avg60 = df['volume'].astype(float).tail(61).iloc[:-1].mean()
    if avg60 == 0:
        return None
    return round(float(df['volume'].iloc[-1]) / avg60, 2)
