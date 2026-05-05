"""時間工具測試。"""
from datetime import date

from time_utils import prev_months, roc_to_date


def test_roc_to_date():
    assert roc_to_date('114/01/02') == date(2025, 1, 2)
    assert roc_to_date('113/12/31') == date(2024, 12, 31)


def test_prev_months_3():
    assert prev_months('20250115', n=3) == ['202501', '202412', '202411']


def test_prev_months_1():
    assert prev_months('20250115', n=1) == ['202501']


def test_prev_months_cross_year():
    assert prev_months('20250105', n=4) == ['202501', '202412', '202411', '202410']
