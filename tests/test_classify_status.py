"""分數 → 5 段 status 對映測試（含邊界）。"""
from scoring import classify_status


def test_classify_momentum_boundary():
    assert classify_status(10) == 'MOMENTUM'
    assert classify_status(9)  == 'MOMENTUM'


def test_classify_active_range():
    assert classify_status(8) == 'ACTIVE'
    assert classify_status(7) == 'ACTIVE'


def test_classify_setup_range():
    assert classify_status(6) == 'SETUP'
    assert classify_status(5) == 'SETUP'


def test_classify_watch_range():
    assert classify_status(4) == 'WATCH'
    assert classify_status(3) == 'WATCH'


def test_classify_noise_range():
    for v in (0, 1, 2):
        assert classify_status(v) == 'NOISE'


def test_classify_boundary_4_to_5():
    """SETUP / WATCH 分界線。"""
    assert classify_status(5) == 'SETUP'
    assert classify_status(4) == 'WATCH'


def test_classify_boundary_6_to_7():
    """ACTIVE / SETUP 分界線。"""
    assert classify_status(7) == 'ACTIVE'
    assert classify_status(6) == 'SETUP'


def test_classify_boundary_8_to_9():
    """MOMENTUM / ACTIVE 分界線。"""
    assert classify_status(9) == 'MOMENTUM'
    assert classify_status(8) == 'ACTIVE'
