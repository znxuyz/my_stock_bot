"""v6.2 第一輪篩選 _filter_first_round 的 regression test。

關鍵 regression：欄位名含特殊字元（'漲跌(+/-)'）時不應全部 KeyError。
v6.2 規則：收盤 ≥ 10、漲幅 -1%~+3%、外資 OR 投信 > 0。
"""
import pandas as pd

from analysis import _filter_first_round


def _make_df_rows(rows):
    return pd.DataFrame(rows)


def test_filter_first_round_handles_special_column_names():
    """欄位名含 '漲跌(+/-)' 時不該全部例外。"""
    rows = [
        # 漲幅 +1.0%（在 -1~+3 內），外資買 → 通過
        {'sid_clean': '2330', '證券名稱': '台積電',
         '收盤價': 1000, '漲跌價差': 10, '漲跌(+/-)': '+',
         '_foreign': 50000, '_trust': 0},
        # 漲幅 +2.0%（在範圍內），投信買 → 通過
        {'sid_clean': '2317', '證券名稱': '鴻海',
         '收盤價': 200, '漲跌價差': 4, '漲跌(+/-)': '+',
         '_foreign': 0, '_trust': 200000},
        # 漲幅 +0.33%（在範圍內），但法人都 = 0 → 不通過
        {'sid_clean': '1101', '證券名稱': '台泥',
         '收盤價': 30, '漲跌價差': 0.1, '漲跌(+/-)': '+',
         '_foreign': 0, '_trust': 0},
    ]
    df = _make_df_rows(rows)
    df_i = df[['sid_clean', '_foreign', '_trust']]
    candidates = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    sids = sorted(c['sid'] for c in candidates)
    assert sids == ['2317', '2330'], f'expected [2317, 2330], got {sids}'


def test_v62_filters_high_gainers():
    """v6.2：漲幅 > +3% 應該被擋（已經爆發，不是 Stalker setup）。"""
    df = pd.DataFrame([{
        'sid_clean': '2330', '證券名稱': '台積電',
        '收盤價': 1000, '漲跌價差': 50, '漲跌(+/-)': '+',  # +5.26%
        '_foreign': 100000, '_trust': 100000,
    }])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    cands = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert cands == []


def test_v62_filters_strong_dippers():
    """v6.2：漲幅 < -1% 應該被擋（破壞 setup）。"""
    df = pd.DataFrame([{
        'sid_clean': '2330', '證券名稱': '台積電',
        '收盤價': 1000, '漲跌價差': 20, '漲跌(+/-)': '-',  # -1.96%
        '_foreign': 100000, '_trust': 100000,
    }])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    cands = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert cands == []


def test_v62_accepts_small_dip():
    """v6.2：漲幅在 -1%~+3% 內，且法人有買 → 通過。"""
    df = pd.DataFrame([{
        'sid_clean': '2330', '證券名稱': '台積電',
        '收盤價': 1000, '漲跌價差': 5, '漲跌(+/-)': '-',  # -0.5%
        '_foreign': 100000, '_trust': 0,
    }])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    cands = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert [c['sid'] for c in cands] == ['2330']


def test_filter_first_round_low_price_filtered():
    """收盤價 < 10 的應該被過濾。"""
    df = pd.DataFrame([{
        'sid_clean': '0001', '證券名稱': '雞蛋水餃股',
        '收盤價': 5, '漲跌價差': 0.05, '漲跌(+/-)': '+',
        '_foreign': 100000, '_trust': 100000,
    }])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    cands = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert cands == []


def test_filter_first_round_zero_institutional_filtered():
    """法人都 = 0 → 不通過（v6.2 要求外資 OR 投信 > 0）。"""
    df = pd.DataFrame([{
        'sid_clean': '9999', '證券名稱': '冷門股',
        '收盤價': 50, '漲跌價差': 0.5, '漲跌(+/-)': '+',  # +1.01%
        '_foreign': 0, '_trust': 0,
    }])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    cands = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert cands == []
