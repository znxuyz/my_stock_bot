"""連續漲停 + 強勢追漲（deprecated）測試。

v6.2 起 check_strong_chase 永遠回 reject；count_consecutive_limit_ups 仍正常運作
（Stalker filter 「10 日內漲停 = 0」會用到）。
"""
import warnings

import pandas as pd

from chase import check_strong_chase, count_consecutive_limit_ups


def test_count_consecutive_4_days():
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


def test_check_strong_chase_always_rejects_in_v62():
    """v6.2：check_strong_chase 永遠回 reject（passed=0）。"""
    entry = {
        'foreign': 150000, 'trust': 100000,
        'vol_ratio': 2.5,
        'chip_score': 5,
    }
    macd = {'dif': 1.0, 'dea': 0.5, 'expanding': True}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        result = check_strong_chase(entry, macd, market_score=3)
    assert result['passed'] == 0
    assert result['checks'] == []
    assert any('v6.2' in r for r in result['reasons'])


def test_check_strong_chase_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        check_strong_chase({}, {}, market_score=0)
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
