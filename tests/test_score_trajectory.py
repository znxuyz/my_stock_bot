"""analysis._backfill_score_trajectory 測試。

驗證：
  - 對單一 candidate 回算過去 5 天分數，UPSERT 進 daily_scores
  - 不打 TWSE（fetch_t86_cached / build_history_fast 都不該被呼叫）
  - df_hist 不足、inst_full 為空時優雅返回 0
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

import analysis


def _make_df_hist(end_date, n_days=80):
    """80 天緩漲 K 棒，足夠跑所有 v6.2 指標。"""
    closes = list(np.linspace(95, 101, n_days))
    return pd.DataFrame({
        'date':   [pd.Timestamp(end_date - timedelta(days=n_days - 1 - i))
                   for i in range(n_days)],
        'close':  [float(c) for c in closes],
        'high':   [c + 0.3 for c in closes],
        'low':    [c - 0.3 for c in closes],
        'volume': [10_000] * n_days,
    })


def _make_inst(end_date, days=10, nets=None):
    """過去 `days` 天法人淨買，由舊到新。預設每天 +100K。"""
    if nets is None:
        nets = [100_000] * days
    return [(end_date - timedelta(days=days - 1 - i), int(nets[i]))
            for i in range(days)]


def test_trajectory_writes_5_rows(monkeypatch):
    target = date(2026, 5, 15)
    df_hist = _make_df_hist(target, n_days=80)
    inst_full = _make_inst(target, days=10, nets=[100_000] * 10)

    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda sid, days, end_date: inst_full)

    saves = []
    monkeypatch.setattr(analysis.db, 'save_daily_score',
                        lambda **kwargs: saves.append(kwargs))

    written = analysis._backfill_score_trajectory('2330', df_hist, target, days=5)
    assert written == 5
    assert len(saves) == 5
    # 寫入的日期應該是 df_hist 中最近 5 個交易日（含 target）
    written_dates = sorted({s['date'] for s in saves})
    assert len(written_dates) == 5
    assert written_dates[-1] == target


def test_trajectory_does_not_call_twse(monkeypatch):
    """純讀本機快取 + DB；不該呼叫 fetch_t86_cached / build_history_fast。"""
    target = date(2026, 5, 15)
    df_hist = _make_df_hist(target)
    inst_full = _make_inst(target, days=10)

    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda sid, days, end_date: inst_full)
    monkeypatch.setattr(analysis.db, 'save_daily_score', lambda **kw: None)

    def fail(*a, **kw):
        raise AssertionError('TWSE 不該被呼叫')

    monkeypatch.setattr(analysis, 'fetch_t86_cached', fail)
    monkeypatch.setattr(analysis, 'build_history_fast', fail)

    analysis._backfill_score_trajectory('2330', df_hist, target, days=5)


def test_trajectory_empty_df_returns_zero(monkeypatch):
    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda *a, **kw: [])
    monkeypatch.setattr(analysis.db, 'save_daily_score', lambda **kw: None)

    assert analysis._backfill_score_trajectory(
        '2330', pd.DataFrame(), date(2026, 5, 15)) == 0


def test_trajectory_insufficient_inst_returns_zero(monkeypatch):
    target = date(2026, 5, 15)
    df_hist = _make_df_hist(target)

    # 法人歷史只回 2 天 → 不足 STALKER_DAYS=5
    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda *a, **kw: _make_inst(target, days=2))
    saves = []
    monkeypatch.setattr(analysis.db, 'save_daily_score',
                        lambda **kw: saves.append(kw))

    written = analysis._backfill_score_trajectory('2330', df_hist, target, days=5)
    # 每日 window 都湊不滿 5 天 → 全跳過
    assert written == 0
    assert saves == []


def test_trajectory_upsert_idempotent(monkeypatch):
    """連續跑兩次 → save 被呼叫 10 次（5×2），每次參數一致。

    daily_scores 表用 ON CONFLICT UPSERT，重複呼叫不會壞資料。
    """
    target = date(2026, 5, 15)
    df_hist = _make_df_hist(target)
    inst_full = _make_inst(target, days=10, nets=[200_000] * 10)

    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda *a, **kw: inst_full)
    saves = []
    monkeypatch.setattr(analysis.db, 'save_daily_score',
                        lambda **kw: saves.append(kw))

    analysis._backfill_score_trajectory('2330', df_hist, target, days=5)
    analysis._backfill_score_trajectory('2330', df_hist, target, days=5)
    assert len(saves) == 10
    # 第一輪和第二輪的對應日期應該寫入相同內容
    first  = sorted(saves[:5],  key=lambda s: s['date'])
    second = sorted(saves[5:], key=lambda s: s['date'])
    for a, b in zip(first, second):
        assert a == b


def test_trajectory_scores_in_valid_range(monkeypatch):
    """所有寫入的分數應該都在 0~10 範圍內。"""
    target = date(2026, 5, 15)
    df_hist = _make_df_hist(target)
    inst_full = _make_inst(target, days=10, nets=[100_000] * 10)

    monkeypatch.setattr(analysis, 'get_inst_history',
                        lambda *a, **kw: inst_full)
    saves = []
    monkeypatch.setattr(analysis.db, 'save_daily_score',
                        lambda **kw: saves.append(kw))

    analysis._backfill_score_trajectory('2330', df_hist, target, days=5)
    for s in saves:
        assert 0 <= s['flow']  <= 5
        assert 0 <= s['trend'] <= 3
        assert 0 <= s['heat']  <= 2
        assert 0 <= s['total'] <= 10
        assert s['status'] in ('MOMENTUM', 'ACTIVE', 'SETUP', 'WATCH', 'NOISE')
        assert s['sid'] == '2330'
