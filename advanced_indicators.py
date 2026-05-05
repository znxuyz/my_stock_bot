"""
進階指標：RSI 評分、ATR 動態停損、壓力位、位階、OBV 量價背離。
分離出來避免 indicators.py 變太胖。
"""
import pandas as pd

from indicators import calc_rsi, calc_atr, calc_obv


def _calc_rsi_block(df, result):
    rsi_s = calc_rsi(df['close'])
    rsi_v = float(rsi_s.iloc[-1])
    if pd.isna(rsi_v):
        return
    rsi_v = round(rsi_v, 1)
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


def _calc_atr_block(df, price, result):
    atr_v = float(calc_atr(df).iloc[-1])
    if pd.isna(atr_v):
        return
    atr_stop = round(price - 2 * atr_v, 1)
    atr_pct  = round((atr_stop - price) / price * 100, 1)
    fixed    = round(price * 0.95, 1)
    if atr_stop > fixed:
        result['atr_stop'] = atr_stop
        result['atr_pct']  = atr_pct
    else:
        result['atr_stop'] = fixed
        result['atr_pct']  = -5.0


def _calc_resistance_position(df, price, result):
    n60      = min(60, len(df))
    n120     = min(120, len(df))
    high_60  = float(df['high'].tail(n60).max())
    high_120 = float(df['high'].tail(n120).max())
    low_120  = float(df['low'].tail(n120).min())
    dist_60  = (price - high_60)  / high_60  * 100
    dist_120 = (price - high_120) / high_120 * 100
    gain_low = (price - low_120)  / low_120  * 100

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


def _calc_obv_block(df, price, result):
    obv = calc_obv(df)
    price_high = price >= float(df['close'].tail(20).iloc[:-1].max())
    obv_high   = float(obv.iloc[-1]) >= float(obv.tail(20).iloc[:-1].max())
    p_slope    = float(df['close'].iloc[-1]) - float(df['close'].tail(10).iloc[0])
    o_slope    = float(obv.iloc[-1])         - float(obv.tail(10).iloc[0])

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


def calc_advanced_indicators(df, price):
    """整合 RSI / ATR / 壓力位 / 位階 / OBV，回傳分數與標籤的 dict。"""
    result = {
        'rsi': None, 'rsi_label': '', 'rsi_score': 0.0,
        'atr_stop': None, 'atr_pct': None,
        'resistance_label': '', 'resistance_score': 0.0,
        'position_label': '', 'position_score': 0.0,
        'obv_label': '', 'obv_score': 0.0,
    }
    if len(df) < 20 or 'high' not in df.columns:
        return result

    for fn in (
        lambda: _calc_rsi_block(df, result),
        lambda: _calc_atr_block(df, price, result),
        lambda: _calc_resistance_position(df, price, result),
        lambda: _calc_obv_block(df, price, result),
    ):
        try:
            fn()
        except Exception as e:
            print(f'[adv 指標] 子計算失敗：{e}')
    return result
