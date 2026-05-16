"""v6.2 新增的 indicators helpers 測試。"""
import numpy as np
import pandas as pd

from indicators import (
    calc_5d_cumulative_change,
    calc_5d_price_range,
    calc_bias_20,
    calc_daily_amount,
    calc_ma_slope_5d,
    calc_vol_vs_60d_ratio,
    count_limit_ups_in_window,
)


def _make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    return pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, 1) + pd.Timedelta(days=i) for i in range(n)],
        'close':  [float(c) for c in closes],
        'high':   highs   if highs   is not None else [c + 0.5 for c in closes],
        'low':    lows    if lows    is not None else [c - 0.5 for c in closes],
        'volume': volumes if volumes is not None else [1000] * n,
    })


# ─────────── 5 日累積漲幅 ───────────

def test_cum5d_basic():
    df = _make_df([100, 100, 100, 100, 100, 103])  # 6 筆
    assert calc_5d_cumulative_change(df) == 3.0


def test_cum5d_insufficient():
    df = _make_df([100, 101, 102])
    assert calc_5d_cumulative_change(df) is None


def test_cum5d_negative():
    df = _make_df([100, 100, 100, 100, 100, 98])
    assert calc_5d_cumulative_change(df) == -2.0


# ─────────── 5 日 high/low 振幅 ───────────

def test_price_range_basic():
    df = _make_df(
        closes=[100, 100, 100, 100, 100],
        highs=[101, 102, 103, 104, 105],
        lows=[99, 98, 97, 96, 95],
    )
    # max=105, min=95 → 10.53%
    pr = calc_5d_price_range(df)
    assert pr is not None and round(pr, 2) == 10.53


def test_price_range_short():
    df = _make_df([100, 100, 100, 100])
    assert calc_5d_price_range(df) is None


# ─────────── 漲停次數 ───────────

def test_limit_ups_window_zero():
    df = _make_df([100] * 15)  # 全平
    assert count_limit_ups_in_window(df, window=10) == 0


def test_limit_ups_window_counts_one():
    # 最後 10 天中有 1 天 +10%
    closes = [100] * 14 + [110]
    df = _make_df(closes)
    assert count_limit_ups_in_window(df, window=10) == 1


def test_limit_ups_window_threshold_boundary():
    """剛好 +9.5% 算漲停。"""
    closes = [100] * 14 + [109.5]
    df = _make_df(closes)
    assert count_limit_ups_in_window(df, window=10) == 1


# ─────────── 乖離 20MA ───────────

def test_bias_20_basic():
    closes = [100] * 19 + [110]  # MA20 = 100.5, 今收 110 → ~9.45%
    df = _make_df(closes)
    b = calc_bias_20(df)
    assert b is not None and b > 9


def test_bias_20_insufficient():
    df = _make_df([100] * 15)
    assert calc_bias_20(df) is None


# ─────────── 10MA 5 日斜率 ───────────

def test_ma_slope_uptrend_positive():
    closes = list(np.linspace(80, 130, 30))
    df = _make_df(closes)
    s = calc_ma_slope_5d(df, period=10)
    assert s is not None and s > 0


def test_ma_slope_downtrend_negative():
    closes = list(np.linspace(130, 80, 30))
    df = _make_df(closes)
    s = calc_ma_slope_5d(df, period=10)
    assert s is not None and s < 0


def test_ma_slope_insufficient():
    df = _make_df([100] * 10)
    assert calc_ma_slope_5d(df, period=10) is None


# ─────────── 日成交金額 ───────────

def test_daily_amount():
    df = _make_df(closes=[100], volumes=[1000])  # 100 × 1000 × 1000 = 1e8
    assert calc_daily_amount(df) == 1e8


def test_daily_amount_empty():
    df = pd.DataFrame(columns=['close', 'volume'])
    assert calc_daily_amount(df) == 0.0


# ─────────── 量 / 60 日均量 ───────────

def test_vol_vs_60d_basic():
    closes = [100] * 61
    volumes = [1000] * 60 + [2000]  # 今天 2000，前 60 天均 1000
    df = _make_df(closes, volumes=volumes)
    r = calc_vol_vs_60d_ratio(df)
    assert r == 2.0


def test_vol_vs_60d_insufficient():
    df = _make_df([100] * 30)
    assert calc_vol_vs_60d_ratio(df) is None
