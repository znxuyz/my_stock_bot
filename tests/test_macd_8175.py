"""確認 v6.2 MACD 預設參數已換成 (8, 17, 5)。"""
import numpy as np
import pandas as pd

import config
from indicators import calc_macd


def _make_df(closes):
    n = len(closes)
    return pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, 1) + pd.Timedelta(days=i) for i in range(n)],
        'close':  [float(c) for c in closes],
        'high':   [c + 0.5 for c in closes],
        'low':    [c - 0.5 for c in closes],
        'volume': [1000] * n,
    })


def test_macd_config_values():
    assert config.MACD_FAST == 8
    assert config.MACD_SLOW == 17
    assert config.MACD_SIGNAL == 5


def test_macd_default_differs_from_classic():
    """v6.2 (8,17,5) 與 v5 經典 (12,26,9) 應該算出不同的 DIF / DEA。"""
    closes = list(np.linspace(50, 100, 80))
    df = _make_df(closes)
    m_v62     = calc_macd(df)                              # 預設 (8,17,5)
    m_classic = calc_macd(df, fast=12, slow=26, signal=9)
    assert m_v62['dif'] is not None and m_classic['dif'] is not None
    assert m_v62['dif'] != m_classic['dif']


def test_macd_short_df_returns_insufficient_under_v62():
    """資料 < slow+signal = 22 筆 → 「資料不足」。
    v5 經典版 (12+9=21) 反而能算，所以邊界不同也是回歸測試的一部分。
    """
    df = _make_df([100] * 20)
    m = calc_macd(df)
    assert '資料不足' in m['macd_label']


def test_macd_22_bars_enough_for_v62():
    """22 筆剛好夠 (8+17? no, slow+signal=17+5=22)。"""
    closes = list(np.linspace(50, 100, 22))
    df = _make_df(closes)
    m = calc_macd(df)
    assert m['dif'] is not None
    assert m['dea'] is not None


def test_macd_explicit_params_override_defaults():
    closes = list(np.linspace(50, 100, 60))
    df = _make_df(closes)
    m_a = calc_macd(df, fast=8, slow=17, signal=5)
    m_b = calc_macd(df)  # 預設應該等價於 (8,17,5)
    assert m_a['dif'] == m_b['dif']
    assert m_a['dea'] == m_b['dea']
