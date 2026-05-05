"""extract_top_flow 測試。"""
import pandas as pd

from topflow import extract_top_flow


def _make_t86_merged(records):
    """records: list of dict（含 sid_clean / 證券名稱 / _foreign / _trust / 收盤價 / 漲跌價差）"""
    return pd.DataFrame(records)


def test_empty_returns_empty_lists():
    res = extract_top_flow(pd.DataFrame())
    assert res == {'buyers': [], 'sellers': []}


def test_top_buyers_sorted_desc():
    df = _make_t86_merged([
        {'sid_clean': '1101', '證券名稱': '台泥', '_foreign': 100, '_trust': 0,
         '收盤價': 30, '漲跌價差': 0.5},
        {'sid_clean': '2330', '證券名稱': '台積電', '_foreign': 500, '_trust': 0,
         '收盤價': 1000, '漲跌價差': 5},
        {'sid_clean': '2317', '證券名稱': '鴻海', '_foreign': 300, '_trust': 0,
         '收盤價': 200, '漲跌價差': 2},
        {'sid_clean': '2454', '證券名稱': '聯發科', '_foreign': -50, '_trust': 0,
         '收盤價': 1200, '漲跌價差': -10},
    ])
    res = extract_top_flow(df, n=3)
    assert len(res['buyers']) == 3
    assert res['buyers'][0]['sid'] == '2330'  # 外資買最多
    assert res['buyers'][1]['sid'] == '2317'
    assert res['buyers'][2]['sid'] == '1101'

    assert len(res['sellers']) == 1
    assert res['sellers'][0]['sid'] == '2454'


def test_change_pct_calculated():
    df = _make_t86_merged([
        {'sid_clean': '2330', '證券名稱': '台積電', '_foreign': 500, '_trust': 0,
         '收盤價': 1010, '漲跌價差': 10},  # 前一日 1000, 漲 1%
    ])
    res = extract_top_flow(df, n=1)
    assert res['buyers'][0]['change_pct'] is not None
    # (10 / (1010 - 10)) * 100 = 1.0
    assert abs(res['buyers'][0]['change_pct'] - 1.0) < 0.01
