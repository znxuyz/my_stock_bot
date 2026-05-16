"""analysis._ensure_t86_history_complete 測試（mock DB + fetch，不打 TWSE）。"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pandas as pd

import analysis


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self, cursor_factory=None):  # noqa: ARG002
        return self._cur

    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_db(monkeypatch, existing_dates):
    """讓 `from db.conn import get_conn` 回 mock，cursor.fetchall() 回 existing_dates。"""
    rows = [(d,) for d in existing_dates]
    cur = _FakeCur(rows)

    @contextmanager
    def _cm():
        yield _FakeConn(cur)

    # _ensure_t86_history_complete 用 `from db.conn import get_conn` 進函式內部
    import db.conn
    monkeypatch.setattr(db.conn, 'get_conn', lambda: _cm())
    return cur


def test_complete_when_all_present(monkeypatch):
    """10 個工作日全部已在 DB → 不呼叫 fetch，回 refetched=0。"""
    target = date(2026, 5, 15)  # Fri
    # 過去 10 個工作日，含 5/15 → 5/4~5/8, 5/11~5/15
    expected = []
    cur = target
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    _patch_db(monkeypatch, expected)

    fetch_calls = []
    monkeypatch.setattr(analysis, 'fetch_t86_cached',
                        lambda d: (fetch_calls.append(d) or pd.DataFrame()))
    monkeypatch.setattr(analysis, 'save_t86_to_history', lambda d, df: 0)

    out = analysis._ensure_t86_history_complete(target, days=10)
    assert out['refetched'] == 0
    assert out['holiday'] == 0
    assert out['failed'] == 0
    assert fetch_calls == []  # 完整 → 不必補抓


def test_refetches_missing_days(monkeypatch):
    """缺 3 天 → fetch 三次，全部回有資料 → refetched=3。"""
    target = date(2026, 5, 15)
    expected = []
    cur = target
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    # DB 只有 7 天，缺最近 3 天
    present = expected[:7]
    missing = expected[7:]
    _patch_db(monkeypatch, present)

    fetch_calls = []
    save_calls = []
    monkeypatch.setattr(analysis, 'fetch_t86_cached',
                        lambda d: (fetch_calls.append(d) or
                                   pd.DataFrame([{'sid_clean': '2330',
                                                   '_foreign': 100, '_trust': 0,
                                                   '_dealer': 0}])))
    monkeypatch.setattr(analysis, 'save_t86_to_history',
                        lambda d, df: (save_calls.append(d) or 1))

    out = analysis._ensure_t86_history_complete(target, days=10)
    assert out['refetched'] == 3
    assert out['holiday'] == 0
    assert out['failed'] == 0
    # missing 三天都被 fetch + save
    assert len(fetch_calls) == 3
    assert len(save_calls) == 3
    # 確認是缺漏那三天，不是其他天
    fetched_dates = {datetime.strptime(d, '%Y%m%d').date() for d in fetch_calls}
    assert fetched_dates == set(missing)


def test_holiday_returned_as_empty_df(monkeypatch):
    """fetch 回空 df（TWSE 真假日）→ 計入 holiday，不算失敗。"""
    target = date(2026, 5, 15)
    _patch_db(monkeypatch, [])  # DB 全空

    monkeypatch.setattr(analysis, 'fetch_t86_cached', lambda d: pd.DataFrame())
    monkeypatch.setattr(analysis, 'save_t86_to_history', lambda d, df: 0)

    out = analysis._ensure_t86_history_complete(target, days=5)
    assert out['holiday'] == 5
    assert out['refetched'] == 0
    assert out['failed'] == 0


def test_fetch_failure_counted_as_failed(monkeypatch):
    """fetch 回 None → 失敗計數，但不中斷其他天。"""
    target = date(2026, 5, 15)
    _patch_db(monkeypatch, [])

    def fake_fetch(d):
        # 第一天 fail、第二天成功、第三天 fail
        if d.endswith(('15', '13')):
            return None
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(analysis, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(analysis, 'save_t86_to_history', lambda d, df: 1)

    out = analysis._ensure_t86_history_complete(target, days=3)
    # 過去 3 個工作日：5/13 (三)、5/14 (四)、5/15 (五)
    # 5/13 fail、5/14 OK、5/15 fail
    assert out['refetched'] == 1
    assert out['failed'] == 2
    assert out['holiday'] == 0


def test_db_query_failure_returns_safe_default(monkeypatch):
    """DB 查詢例外 → 不要 raise，回 safe default 讓 run_analysis 繼續跑。"""
    import db.conn

    def _bad_get_conn():
        raise RuntimeError('db down')

    monkeypatch.setattr(db.conn, 'get_conn', _bad_get_conn)

    out = analysis._ensure_t86_history_complete(date(2026, 5, 15), days=5)
    # 應該回 dict 不要 raise；refetched 為 0
    assert out['refetched'] == 0
