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


def calc_macd(df, fast=12, slow=26, signal=9):
    """
    MACD：DIF = EMA(fast) - EMA(slow)；DEA = EMA(DIF, signal)；Hist = DIF - DEA
    回傳 dict 含 macd_score (0~10) / macd_label / dif / dea / hist / expanding / cross_up
    需至少 slow + signal 筆才有意義（35 筆）。
    """
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

    # 最近 3 天有沒有發生黃金交叉
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
