"""進場區間 calc_entry_zone 測試。"""
from entry_zone import calc_entry_zone


def test_normal_non_ss_uses_2_percent_ceiling():
    """v5：非 SS 級的 normal mode 上緣放寬到 1.02。"""
    lo, hi = calc_entry_zone(100, 'normal', grade='A')
    assert lo == 97.0
    assert hi == 102.0


def test_normal_ss_uses_3_percent_ceiling():
    """SS 級單獨放寬到 1.03。"""
    lo, hi = calc_entry_zone(100, 'normal', grade='SS')
    assert lo == 97.0
    assert hi == 103.0


def test_normal_s_grade_treated_as_other():
    """S 級用一般 normal 區間。"""
    lo, hi = calc_entry_zone(100, 'normal', grade='S')
    assert (lo, hi) == (97.0, 102.0)


def test_normal_no_grade_treated_as_other():
    """grade=None（個股查詢未達等級）也用一般 normal。"""
    lo, hi = calc_entry_zone(100, 'normal', grade=None)
    assert (lo, hi) == (97.0, 102.0)


def test_strong_chase_unchanged():
    """強勢追漲與等級無關，永遠 [1.00, 1.07]。"""
    assert calc_entry_zone(100, 'strong_chase', grade='A')  == (100.0, 107.0)
    assert calc_entry_zone(100, 'strong_chase', grade='SS') == (100.0, 107.0)


def test_watch_returns_none():
    assert calc_entry_zone(100, 'watch') == (None, None)


def test_reject_returns_none():
    """reject 模式（連漲停但條件不夠）也不撮合。"""
    assert calc_entry_zone(100, 'reject') == (None, None)


def test_precision_param():
    """precision=1 給 UI 顯示用。"""
    lo, hi = calc_entry_zone(123.45, 'normal', grade='A', precision=1)
    assert lo == 119.7
    assert hi == 125.9


def test_real_world_close():
    """真實場景：close=342.5（dashboard 截圖中的最高那檔）"""
    lo, hi = calc_entry_zone(342.5, 'normal', grade='A')
    # v5: 332.23 ~ 349.35
    assert lo == 332.22 or lo == 332.23
    assert abs(hi - 349.35) < 0.05
