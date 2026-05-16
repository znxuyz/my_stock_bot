"""detect_stalker_setup 測試（純函式，不打 DB / TWSE）。"""
from datetime import date, timedelta

import pandas as pd

from accumulation import detect_stalker_setup


def _make_df(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        'date':   [pd.Timestamp(2025, 1, 1) + pd.Timedelta(days=i) for i in range(n)],
        'close':  [float(c) for c in closes],
        'high':   highs if highs is not None else [c + 0.5 for c in closes],
        'low':    lows  if lows  is not None else [c - 0.5 for c in closes],
        'volume': [1000] * n,
    })


def _make_inst(nets):
    today = date(2025, 1, 31)
    return [(today - timedelta(days=len(nets) - 1 - i), int(n))
            for i, n in enumerate(nets)]


def test_insufficient_data_returns_false():
    df = _make_df([100, 101, 102])  # 太少
    out = detect_stalker_setup(df, _make_inst([1, 1, 1, 1, 1]))
    assert out['is_stalker'] is False
    assert out['reason'] == 'insufficient_data'


def test_5_of_5_buy_passes_when_all_other_ok():
    # 100 → 101 → 100 → 101 → 100 → 101（5 天累積 +1%，振幅 ~1%）
    df = _make_df([100, 101, 100, 101, 100, 101])
    inst = _make_inst([100_000, 200_000, 50_000, 100_000, 80_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is True
    assert out['buy_days'] == 5
    assert out['cum_net'] == 530_000


def test_4_of_5_buy_still_passes_min_threshold():
    df = _make_df([100, 101, 100, 101, 100, 101])
    inst = _make_inst([100_000, -50_000, 50_000, 100_000, 80_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is True
    assert out['buy_days'] == 4


def test_3_of_5_buy_fails():
    df = _make_df([100, 101, 100, 101, 100, 101])
    inst = _make_inst([100_000, -50_000, 50_000, -100_000, 80_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is False
    assert out['buy_days'] == 3


def test_cum_net_negative_fails():
    """法人 4/5 天買但累積為負（賣的那天太大）→ 不通過。"""
    df = _make_df([100, 101, 100, 101, 100, 101])
    inst = _make_inst([10_000, -500_000, 10_000, 10_000, 10_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is False


def test_too_large_cumulative_change_fails():
    """5 日累積漲幅 > 3% → 不通過（已爆發）。"""
    df = _make_df([100, 100, 100, 100, 100, 105])  # +5%
    inst = _make_inst([100_000, 100_000, 100_000, 100_000, 100_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is False


def test_too_volatile_fails():
    """5 日 high/low 振幅 ≥ 5% → 不通過（不是盤整）。"""
    df = _make_df(
        closes=[100, 101, 100, 101, 100, 101],
        highs=[101, 103, 102, 110, 102, 102],
        lows=[99, 100, 98, 99, 95, 100],   # min low = 95，max high = 110 → 振幅 ~15.8%
    )
    inst = _make_inst([100_000, 100_000, 100_000, 100_000, 100_000])
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is False


def test_boundary_cum_change_below_3pct_passes():
    """5 日累積 +2.5%（< +3% 上界） → 通過。"""
    df = _make_df([99, 100, 100, 100, 100, 102.5])
    inst = _make_inst([100_000] * 5)
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is True


def test_boundary_cum_change_above_3pct_fails():
    """5 日累積 +4% → 不通過。"""
    df = _make_df([99, 100, 100, 100, 100, 104])
    inst = _make_inst([100_000] * 5)
    out = detect_stalker_setup(df, inst)
    assert out['is_stalker'] is False
