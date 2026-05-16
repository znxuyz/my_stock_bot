"""進場區間 calc_entry_zone 測試（v6.2 純市價）。

v5 的限價區間 / 強勢追漲區間 / WATCH 三種模式都已移除，
calc_entry_zone 永遠回 (None, None)。
"""
from entry_zone import calc_entry_zone


def test_returns_none_for_default():
    assert calc_entry_zone(100) == (None, None)


def test_returns_none_for_legacy_normal_a():
    assert calc_entry_zone(100, 'normal', grade='A') == (None, None)


def test_returns_none_for_legacy_normal_ss():
    assert calc_entry_zone(100, 'normal', grade='SS') == (None, None)


def test_returns_none_for_legacy_strong_chase():
    assert calc_entry_zone(100, 'strong_chase', grade='SS') == (None, None)


def test_returns_none_for_legacy_watch():
    assert calc_entry_zone(100, 'watch') == (None, None)


def test_returns_none_for_legacy_reject():
    assert calc_entry_zone(100, 'reject') == (None, None)


def test_returns_none_regardless_of_precision():
    assert calc_entry_zone(123.45, 'normal', grade='A', precision=1) == (None, None)
