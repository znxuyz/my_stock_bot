"""連續漲停 + 強勢追漲測試。"""
import pandas as pd

from chase import check_strong_chase, count_consecutive_limit_ups


def test_count_consecutive_4_days():
    """從 100 連續 +10% 4 天。"""
    df = pd.DataFrame({'close': [100, 110, 121, 133.1, 146.41]})
    assert count_consecutive_limit_ups(df) == 4


def test_count_consecutive_zero():
    df = pd.DataFrame({'close': [100, 102, 110]})  # 最後一天 +7.8% 不到漲停
    assert count_consecutive_limit_ups(df) == 0


def test_count_consecutive_short_data():
    df = pd.DataFrame({'close': [100]})
    assert count_consecutive_limit_ups(df) == 0


def test_count_consecutive_threshold_boundary():
    """剛好 +9.5% 應該算漲停。"""
    df = pd.DataFrame({'close': [100, 109.5]})
    assert count_consecutive_limit_ups(df) == 1


def test_check_strong_chase_all_pass():
    entry = {
        'foreign': 150000, 'trust': 100000,  # 合計 250K
        'vol_ratio': 2.5,
        'chip_score': 5,
    }
    macd = {'dif': 1.0, 'dea': 0.5, 'expanding': True}
    result = check_strong_chase(entry, macd, market_score=3)
    assert result['passed'] == 5


def test_check_strong_chase_all_fail():
    entry = {
        'foreign': 50000, 'trust': 30000,
        'vol_ratio': 1.0,
        'chip_score': 2,
    }
    macd = {'dif': -1.0, 'dea': 0.5, 'expanding': False}
    result = check_strong_chase(entry, macd, market_score=-3)
    assert result['passed'] == 0


def test_check_strong_chase_4_of_5():
    """通過 4 項對應 watch 模式。"""
    entry = {
        'foreign': 150000, 'trust': 100000,
        'vol_ratio': 2.5,
        'chip_score': 5,
    }
    macd = {'dif': 1.0, 'dea': 0.5, 'expanding': True}
    result = check_strong_chase(entry, macd, market_score=-3)  # 大盤分數負
    assert result['passed'] == 4
