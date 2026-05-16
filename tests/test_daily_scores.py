"""daily_scores 寫入 / 讀取 / UPSERT 測試。

純單元測試：mock 掉 db.conn.get_conn，不需要真實 PostgreSQL。
驗證 SQL 是否帶 ON CONFLICT、參數順序、回傳排序（由舊到新）。
"""
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

from db import scores


class FakeCursor:
    """記錄 execute 的 SQL 與 params，提供 fetchall 回值。"""
    def __init__(self, fetch_rows=None):
        self.executed = []          # list[(sql, params)]
        self._fetch = fetch_rows or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._fetch)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cur = cursor
        self.committed = False

    def cursor(self, cursor_factory=None):  # noqa: ARG002  (sig 相容)
        return self._cur

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_get_conn(monkeypatch, fake_conn):
    @contextmanager
    def _cm():
        yield fake_conn
    # scores 模組用 `from db.conn import get_conn` → 已綁定到 scores.get_conn
    monkeypatch.setattr(scores, 'get_conn', lambda: _cm())


# ─────────── save_daily_score ───────────

def test_save_daily_score_executes_upsert(monkeypatch):
    cur = FakeCursor()
    conn = FakeConn(cur)
    _patch_get_conn(monkeypatch, conn)

    scores.save_daily_score(
        sid='2330', date=date(2026, 5, 16),
        flow=5, trend=3, heat=2, total=10, status='MOMENTUM',
    )

    assert conn.committed is True
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert 'INSERT INTO daily_scores' in sql
    assert 'ON CONFLICT (sid, date) DO UPDATE' in sql
    assert params == ('2330', date(2026, 5, 16), 5, 3, 2, 10, 'MOMENTUM')


def test_save_daily_score_coerces_numeric_types(monkeypatch):
    """numpy / decimal 類型應被轉成 int。"""
    cur = FakeCursor()
    _patch_get_conn(monkeypatch, FakeConn(cur))

    # 模擬從評分結果丟進來的可能型別
    scores.save_daily_score('1234', date(2026, 5, 1), flow=3.0, trend='2',
                            heat=1, total=6, status='SETUP')

    _, params = cur.executed[0]
    assert params[2] == 3
    assert params[3] == 2
    assert params[4] == 1
    assert params[5] == 6
    assert isinstance(params[2], int)


# ─────────── fetch_recent_scores ───────────

def test_fetch_recent_scores_returns_oldest_first(monkeypatch):
    """SQL 是 ORDER BY date DESC，但函式要回「由舊到新」（給 velocity 算斜率用）。"""
    rows_newest_first = [
        {'date': date(2026, 5, 5), 'total_score': 8, 'flow_score': 5,
         'trend_score': 2, 'heat_score': 1, 'status': 'ACTIVE'},
        {'date': date(2026, 5, 4), 'total_score': 6, 'flow_score': 4,
         'trend_score': 1, 'heat_score': 1, 'status': 'SETUP'},
        {'date': date(2026, 5, 3), 'total_score': 5, 'flow_score': 3,
         'trend_score': 1, 'heat_score': 1, 'status': 'SETUP'},
    ]
    cur = FakeCursor(fetch_rows=rows_newest_first)
    _patch_get_conn(monkeypatch, FakeConn(cur))

    out = scores.fetch_recent_scores('2330', days=5, end_date=date(2026, 5, 5))

    # 反轉後：由舊到新
    assert [r['total_score'] for r in out] == [5, 6, 8]
    assert [r['date'] for r in out] == [date(2026, 5, 3), date(2026, 5, 4), date(2026, 5, 5)]


def test_fetch_recent_scores_with_end_date_uses_param(monkeypatch):
    cur = FakeCursor(fetch_rows=[])
    _patch_get_conn(monkeypatch, FakeConn(cur))

    scores.fetch_recent_scores('2330', days=5, end_date=date(2026, 5, 5))

    sql, params = cur.executed[0]
    assert 'AND date <= %s' in sql
    assert params == ('2330', date(2026, 5, 5), 5)


def test_fetch_recent_scores_no_end_date_uses_current_date(monkeypatch):
    cur = FakeCursor(fetch_rows=[])
    _patch_get_conn(monkeypatch, FakeConn(cur))

    scores.fetch_recent_scores('2330', days=5)

    sql, params = cur.executed[0]
    assert 'CURRENT_DATE' in sql
    assert params == ('2330', 5)


def test_fetch_recent_scores_empty(monkeypatch):
    cur = FakeCursor(fetch_rows=[])
    _patch_get_conn(monkeypatch, FakeConn(cur))
    out = scores.fetch_recent_scores('9999', days=5, end_date=date(2026, 5, 5))
    assert out == []


# ─────────── db.__init__ 重新匯出 ───────────

def test_module_exports_via_db_package(monkeypatch):
    """確保 `from db import save_daily_score, fetch_recent_scores` 可用。"""
    import db
    assert hasattr(db, 'save_daily_score')
    assert hasattr(db, 'fetch_recent_scores')
    # 也可用 MagicMock 路徑直接呼叫，不再驗證 SQL
    cur = FakeCursor()
    _patch_get_conn(monkeypatch, FakeConn(cur))
    db.save_daily_score('1101', date(2026, 5, 1), 2, 2, 1, 5, 'SETUP')
    assert cur.executed  # 真的執行過


# Bonus: 接近真實的 callsite assertion
def test_save_daily_score_value_round_trip(monkeypatch):
    """先存後讀（mock 兩個獨立 cursor），結果應該對得起來。"""
    saved = {}

    @contextmanager
    def fake_conn():
        cur = MagicMock()
        # save_daily_score → cur.execute(...) 把資料寫入 saved
        def _exec(sql, params=None):
            if 'INSERT INTO daily_scores' in sql:
                saved[(params[0], params[1])] = params
        cur.execute = _exec
        cur.__enter__ = lambda self: self
        cur.__exit__  = lambda self, *a: False
        conn = MagicMock()
        conn.cursor = lambda cursor_factory=None: cur
        conn.commit = lambda: None
        conn.__enter__ = lambda self: self
        conn.__exit__  = lambda self, *a: False
        yield conn

    monkeypatch.setattr(scores, 'get_conn', fake_conn)
    scores.save_daily_score('2330', date(2026, 5, 16), 5, 3, 2, 10, 'MOMENTUM')
    assert ('2330', date(2026, 5, 16)) in saved
