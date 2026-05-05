"""技術指標測試（純函式，不打 TWSE）。"""
import numpy as np
import pandas as pd
import pytest

from indicators import (
    calc_atr,
    calc_bias_and_entry,
    calc_ema,
    calc_macd,
    calc_obv,
    calc_rsi,
    calc_volume_ratio,
    check_ema_bull,
)


def _make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    return pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, 1) + pd.Timedelta(days=i) for i in range(n)],
        'close':  closes,
        'high':   highs   if highs   is not None else [c + 1 for c in closes],
        'low':    lows    if lows    is not None else [c - 1 for c in closes],
        'volume': volumes if volumes is not None else [1000] * n,
    })


def test_calc_ema_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    ema = calc_ema(s, span=3)
    assert pytest.approx(ema.iloc[-1], rel=1e-3) == 4.0625


def test_check_ema_bull_insufficient():
    df = _make_df([100] * 30)
    is_bull, mode = check_ema_bull(df)
    assert is_bull is False
    assert mode == 'insufficient'


def test_check_ema_bull_fallback_uptrend():
    closes = list(np.linspace(80, 120, 80))  # 平穩上漲
    df = _make_df(closes)
    is_bull, mode = check_ema_bull(df)
    assert mode == 'fallback'
    assert bool(is_bull) is True


def test_check_ema_bull_full_uptrend():
    closes = list(np.linspace(80, 130, 130))
    df = _make_df(closes)
    is_bull, mode = check_ema_bull(df)
    assert mode == 'full'
    assert bool(is_bull) is True


def test_calc_volume_ratio():
    """target 日當日量 200，前 5 日均量 100 → 量比 2.0。"""
    target = pd.Timestamp(2025, 1, 7)
    df = pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, d) for d in range(1, 8)],
        'volume': [100] * 6 + [200],
    })
    assert calc_volume_ratio(df, target) == 2.0


def test_calc_volume_ratio_insufficient():
    df = pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, d) for d in range(1, 4)],
        'volume': [100, 100, 100],
    })
    assert calc_volume_ratio(df, pd.Timestamp(2025, 1, 3)) == 0.0


def test_calc_rsi_range():
    """有漲有跌的隨機序列，RSI 應該在 0~100 之間且非 NaN。"""
    rng = np.random.default_rng(42)
    deltas = rng.normal(0.3, 1.0, 50)  # mean=0.3 → 整體上漲，stdev=1 → 有時下跌
    closes = pd.Series(100 + deltas.cumsum())
    rsi = calc_rsi(closes)
    last = float(rsi.iloc[-1])
    assert not np.isnan(last)
    assert 0 <= last <= 100


def test_calc_atr_positive():
    df = _make_df(list(range(20, 50)))
    atr = calc_atr(df)
    assert float(atr.iloc[-1]) > 0


def test_calc_obv_vectorised():
    """向量化版本必須與原始定義一致。"""
    df = pd.DataFrame({
        'close':  [100, 102, 101, 103, 103],
        'volume': [10,  20,  15,  25,  30],
    })
    obv = calc_obv(df)
    # +20 → -15 → +25 → 持平
    assert list(obv.values) == [0, 20, 5, 30, 30]


def test_calc_macd_insufficient():
    df = _make_df([100] * 20)
    m = calc_macd(df)
    assert m['macd_score'] == 5
    assert '資料不足' in m['macd_label']


def test_calc_macd_uptrend():
    closes = list(np.linspace(50, 100, 60))
    df = _make_df(closes)
    m = calc_macd(df)
    assert m['macd_score'] >= 5
    assert m['dif'] is not None


def test_calc_bias_normal_zone():
    closes = list(np.linspace(95, 100, 15))  # 緩步上揚
    df = _make_df(closes)
    bias = calc_bias_and_entry(df, price=101)
    assert bias is not None
    assert bias['bias_pct'] >= 0
    assert bias['bias_emoji'] in ('✅', '⚠️', '🔄', '❌')


def test_calc_bias_too_high():
    closes = [100] * 15
    df = _make_df(closes)
    bias = calc_bias_and_entry(df, price=110)
    assert bias['bias_pct'] == 10
    assert bias['bias_emoji'] == '❌'
