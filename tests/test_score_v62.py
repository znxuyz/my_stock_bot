"""v6.2 10 分制評分系統測試。

涵蓋：Flow / Trend / Heat 子分數的條件邊界、calc_score_v62 組合、
以及 status_to_emoji / status_to_position_pct 的對映。
"""
import numpy as np
import pandas as pd

from scoring import (
    calc_flow_score,
    calc_heat_score,
    calc_score_v62,
    calc_trend_score,
    status_to_emoji,
    status_to_position_pct,
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


# ─────────── Flow Score ───────────

def test_flow_5_of_5_buy_gives_2pt():
    entry = {'acc_buy_days': 5, 'acc_cum_net': 0, 'foreign': 0, 'trust': 0}
    df = _make_df([100] * 20)
    assert calc_flow_score(entry, df) == 2


def test_flow_4_of_5_buy_gives_0pt_for_cond1():
    entry = {'acc_buy_days': 4, 'acc_cum_net': 0, 'foreign': 0, 'trust': 0}
    df = _make_df([100] * 20)
    assert calc_flow_score(entry, df) == 0


def test_flow_500k_threshold():
    entry = {'acc_buy_days': 0, 'acc_cum_net': 500_000, 'foreign': 0, 'trust': 0}
    df = _make_df([100] * 20)
    assert calc_flow_score(entry, df) == 2


def test_flow_499k_below_threshold():
    entry = {'acc_buy_days': 0, 'acc_cum_net': 499_999, 'foreign': 0, 'trust': 0}
    df = _make_df([100] * 20)
    assert calc_flow_score(entry, df) == 0


def test_flow_concentration_10pct_gives_1pt():
    # foreign + trust = 1000；vol = 10000 → 集中度 10.0%
    entry = {'acc_buy_days': 0, 'acc_cum_net': 0, 'foreign': 600, 'trust': 400}
    df = _make_df([100] * 20, volumes=[10_000] * 20)
    assert calc_flow_score(entry, df) == 1


def test_flow_concentration_below_10pct_gives_0pt():
    # 集中度 9.9%
    entry = {'acc_buy_days': 0, 'acc_cum_net': 0, 'foreign': 600, 'trust': 390}
    df = _make_df([100] * 20, volumes=[10_000] * 20)
    assert calc_flow_score(entry, df) == 0


def test_flow_all_three_conditions_max():
    entry = {'acc_buy_days': 5, 'acc_cum_net': 1_000_000, 'foreign': 600, 'trust': 400}
    df = _make_df([100] * 20, volumes=[10_000] * 20)
    assert calc_flow_score(entry, df) == 5


def test_flow_empty_df_no_concentration_point():
    entry = {'acc_buy_days': 5, 'acc_cum_net': 500_000, 'foreign': 1_000_000, 'trust': 0}
    df = pd.DataFrame()
    assert calc_flow_score(entry, df) == 4  # 兩個 acc 條件達標、集中度因 vol=0 不加分


# ─────────── Trend Score ───────────

def test_trend_all_three_uptrend():
    closes = list(np.linspace(80, 130, 60))  # 平穩上漲，MACD 多頭
    df = _make_df(closes)
    # 模擬 enrich 已經算過 MACD
    from indicators import calc_macd
    macd = calc_macd(df)
    entry = {'macd_info': macd}
    s = calc_trend_score(df, entry)
    assert s == 3


def test_trend_zero_when_macd_missing():
    closes = list(np.linspace(80, 130, 60))
    df = _make_df(closes)
    entry = {'macd_info': {}}
    s = calc_trend_score(df, entry)
    assert s <= 2  # MACD 那一項拿不到


def test_trend_zero_for_short_df():
    df = _make_df([100] * 10)
    assert calc_trend_score(df, {'macd_info': {}}) == 0


def test_trend_downtrend_gives_0pt():
    closes = list(np.linspace(130, 80, 60))
    df = _make_df(closes)
    from indicators import calc_macd
    macd = calc_macd(df)
    entry = {'macd_info': macd}
    assert calc_trend_score(df, entry) == 0


# ─────────── Heat Score ───────────

def test_heat_all_cold_gives_2pt():
    entry = {'cum_5d_pct': 1.5, 'vol_vs_60d': 1.0, 'bias_20': 1.0}
    assert calc_heat_score(None, entry) == 2


def test_heat_2_of_3_gives_1pt():
    entry = {'cum_5d_pct': 1.5, 'vol_vs_60d': 1.0, 'bias_20': 3.0}  # bias 不過
    assert calc_heat_score(None, entry) == 1


def test_heat_1_of_3_gives_0pt():
    entry = {'cum_5d_pct': 5.0, 'vol_vs_60d': 1.0, 'bias_20': 5.0}
    assert calc_heat_score(None, entry) == 0


def test_heat_boundary_inclusive():
    # 全部剛好等於門檻 → 滿足
    entry = {'cum_5d_pct': 2.0, 'vol_vs_60d': 1.3, 'bias_20': 2.0}
    assert calc_heat_score(None, entry) == 2


def test_heat_missing_data_zero():
    entry = {'cum_5d_pct': 1.0, 'vol_vs_60d': None, 'bias_20': 1.0}
    assert calc_heat_score(None, entry) == 0


# ─────────── calc_score_v62 整合 ───────────

def test_score_v62_high_score_active():
    closes = list(np.linspace(80, 100, 60))  # 緩步上漲
    df = _make_df(closes, volumes=[10_000] * 60)
    from indicators import calc_macd
    macd = calc_macd(df)
    entry = {
        'acc_buy_days': 5, 'acc_cum_net': 1_000_000,
        'foreign': 600, 'trust': 400,
        'cum_5d_pct': 1.0, 'vol_vs_60d': 1.0, 'bias_20': 1.5,
        'macd_info': macd,
    }
    r = calc_score_v62(entry, df)
    assert r['flow_score']  == 5
    assert r['heat_score']  == 2
    assert r['trend_score'] >= 2
    assert r['total_score'] >= 9
    assert r['status'] in ('ACTIVE', 'MOMENTUM')


def test_score_v62_low_score_noise():
    df = _make_df([100] * 10)
    entry = {
        'acc_buy_days': 0, 'acc_cum_net': 0,
        'foreign': 0, 'trust': 0,
        'macd_info': {},
    }
    r = calc_score_v62(entry, df)
    assert r['total_score'] == 0
    assert r['status'] == 'NOISE'


# ─────────── status helpers ───────────

def test_status_emoji_map():
    assert status_to_emoji('MOMENTUM') == '🔵'
    assert status_to_emoji('ACTIVE')   == '🟢'
    assert status_to_emoji('SETUP')    == '🟡'
    assert status_to_emoji('WATCH')    == '🟠'
    assert status_to_emoji('NOISE')    == '🔴'
    assert status_to_emoji('xxx')      == '⚪'


def test_status_to_position_pct():
    assert status_to_position_pct('MOMENTUM') == 0
    assert status_to_position_pct('ACTIVE')   == 30
    assert status_to_position_pct('SETUP')    == 18
    assert status_to_position_pct('WATCH')    == 0
    assert status_to_position_pct('NOISE')    == 0
    assert status_to_position_pct('xxx')      == 0
