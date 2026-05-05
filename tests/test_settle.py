"""T+1 撮合與排程結算測試（純邏輯，不需 DB）。"""
from datetime import date

from db.settle import calc_position_pct, determine_t1_fill, next_friday


# ─────────── determine_t1_fill ───────────
def test_t1_open_in_zone_fills_at_open():
    assert determine_t1_fill(t1_open=98, t1_high=100, t1_low=96,
                             zone_low=95, zone_high=100) == ('filled', 98)


def test_t1_gap_up_but_intraday_returns():
    """跳空高於區間但盤中觸到 zone_high → 以 zone_high 成交。"""
    assert determine_t1_fill(102, 105, 99, 95, 100) == ('filled', 100)


def test_t1_gap_up_no_return_misses():
    assert determine_t1_fill(102, 105, 101, 95, 100) == ('missed', None)


def test_t1_gap_down_with_allow():
    assert determine_t1_fill(94, 96, 93, 95, 100) == ('filled', 94)


def test_t1_gap_down_strong_chase_no_catch():
    assert determine_t1_fill(94, 96, 93, 95, 100, allow_gap_down=False) == ('missed', None)


def test_t1_zone_none_misses():
    assert determine_t1_fill(100, 100, 100, None, None) == ('missed', None)


def test_t1_open_none_misses():
    assert determine_t1_fill(None, 100, 100, 95, 100) == ('missed', None)


# ─────────── next_friday ───────────
def test_next_friday_from_monday():
    # 2025-01-06 (Mon) → 1/10 (Fri)
    assert next_friday(date(2025, 1, 6), 1) == date(2025, 1, 10)


def test_next_friday_from_friday():
    # 2025-01-10 (Fri) → 1/17 (next Fri)
    assert next_friday(date(2025, 1, 10), 1) == date(2025, 1, 17)


def test_next_friday_n_2():
    # 從週一到第 2 個週五 = 隔週週五
    assert next_friday(date(2025, 1, 6), 2) == date(2025, 1, 17)


# ─────────── calc_position_pct ───────────
def test_position_ss_low_bias():
    assert calc_position_pct('SS', 3) == 25.0


def test_position_ss_mid_bias():
    assert calc_position_pct('SS', 7) == 15.0


def test_position_ss_high_bias():
    assert calc_position_pct('SS', 10) == 0.0


def test_position_a_grade():
    assert calc_position_pct('A', 2) == 10.0
    assert calc_position_pct('A', 7) == 5.0


def test_position_unknown_grade():
    assert calc_position_pct('Z', 2) == 0.0


def test_position_bias_none():
    """bias_pct=None → 視同低乖離。"""
    assert calc_position_pct('SS', None) == 25.0
