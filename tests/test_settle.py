"""T+1 撮合（v6.2 純市價）與排程結算測試。

v6.2：determine_t1_fill 一律市價成交，唯一 missed 條件是 t1_open=None
或一字漲停 / 跌停（high == low == open）。
calc_position_pct 已 deprecated，永遠回 0.0。
"""
import warnings
from datetime import date

from db.settle import calc_position_pct, determine_t1_fill, next_friday


# ─────────── determine_t1_fill（v6.2 純市價） ───────────

def test_t1_open_normal_fills_at_open():
    """正常開盤即成交。"""
    assert determine_t1_fill(t1_open=98, t1_high=100, t1_low=96) == ('filled', 98)


def test_t1_open_gap_up_fills_at_open():
    """v6.2：跳空高開仍以開盤價成交（不再用區間判斷）。"""
    assert determine_t1_fill(102, 105, 99) == ('filled', 102)


def test_t1_open_gap_down_fills_at_open():
    """v6.2：跳空低開也以開盤價成交（撿便宜）。"""
    assert determine_t1_fill(94, 96, 93) == ('filled', 94)


def test_t1_limit_up_locked_misses():
    """一字漲停（open == high == low）→ missed。"""
    assert determine_t1_fill(110, 110, 110) == ('missed', None)


def test_t1_limit_down_locked_misses():
    """一字跌停（open == high == low）→ missed。"""
    assert determine_t1_fill(90, 90, 90) == ('missed', None)


def test_t1_open_none_misses():
    """沒有 T+1 K 棒 → missed。"""
    assert determine_t1_fill(None, 100, 100) == ('missed', None)


def test_t1_legacy_zone_args_ignored():
    """傳入舊版 zone 參數仍照新邏輯處理，不會擋下成交。"""
    assert determine_t1_fill(98, 100, 96, zone_low=95, zone_high=99) == ('filled', 98)


def test_t1_legacy_allow_gap_down_ignored():
    """allow_gap_down 已不影響結果。"""
    assert determine_t1_fill(94, 96, 93, allow_gap_down=False) == ('filled', 94)


# ─────────── next_friday ───────────
def test_next_friday_from_monday():
    assert next_friday(date(2025, 1, 6), 1) == date(2025, 1, 10)


def test_next_friday_from_friday():
    assert next_friday(date(2025, 1, 10), 1) == date(2025, 1, 17)


def test_next_friday_n_2():
    assert next_friday(date(2025, 1, 6), 2) == date(2025, 1, 17)


# ─────────── calc_position_pct（deprecated） ───────────
def test_calc_position_pct_deprecated_always_zero():
    """v6.2：calc_position_pct 永遠回 0.0；新程式改用 status_to_position_pct。"""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        assert calc_position_pct('SS', 3) == 0.0
        assert calc_position_pct('A',  5) == 0.0
        assert calc_position_pct('Z',  None) == 0.0


def test_calc_position_pct_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        calc_position_pct('SS', 3)
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
