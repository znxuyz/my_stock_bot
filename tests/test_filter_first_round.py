"""第一輪篩選 _filter_first_round 的 regression test。

這個 case 在 v5 出現過：把 iterrows 改成 itertuples 後，pandas 把含特殊字元
（括號、加號、底線開頭）的欄位名改成位置別名（_4 之類），導致用原欄位名取值
KeyError，全部 1000+ 檔都被當例外跳過 → 0 檔通過篩選。
"""
import pandas as pd

from analysis import _filter_first_round


def _make_df():
    """模擬 T86 + MI_INDEX 合併後的 DataFrame，包含 '漲跌(+/-)' 這類特殊欄位。"""
    return pd.DataFrame([
        {
            'sid_clean': '2330', '證券名稱': '台積電',
            '收盤價': 1000, '漲跌價差': 10, '漲跌(+/-)': '+',
            '_foreign': 50000, '_trust': 50000,
        },
        {
            'sid_clean': '2317', '證券名稱': '鴻海',
            '收盤價': 200, '漲跌價差': 4, '漲跌(+/-)': '+',
            '_foreign': 200000, '_trust': 0,
        },
        {
            'sid_clean': '1101', '證券名稱': '台泥',
            '收盤價': 30, '漲跌價差': 0.1, '漲跌(+/-)': '+',
            '_foreign': 1000, '_trust': 1000,
        },
    ])


def test_filter_first_round_handles_special_column_names():
    """欄位名含 '漲跌(+/-)' 時不該全部例外，應該正確走完篩選。"""
    df = _make_df()
    df_i = df[['sid_clean', '_foreign', '_trust']]

    candidates = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')

    sids = sorted(c['sid'] for c in candidates)
    # 2330 雙買超 + 收盤 ≥ 10 + 漲幅 ~1% → 通過
    # 2317 單方 ≥ 100K + 漲幅 ~2% → 通過
    # 1101 法人都不夠 → 不通過
    assert sids == ['2317', '2330'], f'expected [2317, 2330], got {sids}'


def test_filter_first_round_negative_change_filtered():
    """漲幅 < 1% 的應該被過濾。"""
    df = pd.DataFrame([
        {
            'sid_clean': '2330', '證券名稱': '台積電',
            '收盤價': 1000, '漲跌價差': 5, '漲跌(+/-)': '-',  # 漲幅 -0.5%
            '_foreign': 100000, '_trust': 100000,
        },
    ])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    candidates = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert candidates == []


def test_filter_first_round_low_price_filtered():
    """收盤價 < 10 的應該被過濾。"""
    df = pd.DataFrame([
        {
            'sid_clean': '0001', '證券名稱': '雞蛋水餃股',
            '收盤價': 5, '漲跌價差': 0.5, '漲跌(+/-)': '+',
            '_foreign': 100000, '_trust': 100000,
        },
    ])
    df_i = df[['sid_clean', '_foreign', '_trust']]
    candidates = _filter_first_round(df, df_i, '收盤價', '漲跌價差', '漲跌(+/-)')
    assert candidates == []
