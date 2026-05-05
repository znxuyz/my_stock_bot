"""評分相關測試。"""
import pytest

from scoring import (
    calc_chip_concentration,
    calc_margin_score,
    calc_market_env,
    calc_score,
)


def test_calc_score_high_combo():
    """各項都拿好分數應該 >= 85（SS）。"""
    entry = {
        'change': 4,
        'vol_ratio': 3.5,
        'foreign': 100000, 'trust': 50000,
        'bias': {'bias_pct': 2},
        'adv': {'rsi': 70, 'resistance_score': 0, 'position_score': 0.5},
        'macd_score': 10,
        'consec_score': 0,
        'market_score': 3, 'margin_score': 3, 'chip_score': 8,
    }
    assert calc_score(entry) >= 85


def test_calc_score_minimal():
    """所有條件僅勉強通過，分數應該偏低。"""
    entry = {
        'change': 1.5,
        'vol_ratio': 1.5,
        'foreign': 5000, 'trust': 5000,  # 不到雙買超
        'bias': {'bias_pct': 6},
        'adv': {'rsi': 55, 'resistance_score': -0.25, 'position_score': 0},
        'macd_score': 0,
    }
    score = calc_score(entry)
    assert 0 <= score < 52  # 應該被淘汰


def test_calc_score_negative_terms_dont_underflow():
    """所有減分都觸發時，最後仍 >= 0。"""
    entry = {
        'change': 0,
        'vol_ratio': 0, 'foreign': 0, 'trust': 0,
        'macd_score': 0,
        'market_score': -5, 'margin_score': -8, 'chip_score': 0,
    }
    assert calc_score(entry) >= 0


def test_market_env_suspend():
    """連 3 日大幅賣超應該觸發 suspend。"""
    res = calc_market_env([-200, -250, -300])
    assert res['suspend'] is True


def test_market_env_neutral():
    res = calc_market_env([10, -20, 50])
    assert res['suspend'] is False
    assert res['score'] == 0


def test_market_env_buy_today():
    res = calc_market_env([0, 0, 150])
    assert res['score'] == 3
    assert '買超' in res['label']


def test_calc_margin_score_thresholds():
    # +30% 暴增 → -8
    res = calc_margin_score(130_000, 100_000)
    assert res['score'] == -8
    # 持平 → 0
    res = calc_margin_score(100_000, 100_000)
    assert res['score'] == 0
    # 減少 → +3
    res = calc_margin_score(90_000, 100_000)
    assert res['score'] == 3


def test_calc_chip_concentration_zero_volume():
    res = calc_chip_concentration(50000, 30000, 0)
    assert res['score'] == 0
    assert res['concentration'] == 0


def test_calc_chip_concentration_high():
    # foreign+trust = 200K，volume = 1M → 集中度 20%
    res = calc_chip_concentration(150000, 50000, 1_000_000)
    assert res['score'] == 8
    assert res['concentration'] == pytest.approx(20.0)
