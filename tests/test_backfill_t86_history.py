"""tools/backfill_t86_history.py 測試（mock fetch + save，不打 TWSE / DB）。"""
import pandas as pd

from tools import backfill_t86_history as bf


def test_prev_workdays_skips_weekend():
    """從週日往回應該只回工作日。"""
    # 2026-05-17 是星期日 → 往回 5 個工作日 = 5/11,12,13,14,15
    out = bf._prev_workdays('20260517', 5)
    assert out == ['20260511', '20260512', '20260513', '20260514', '20260515']


def test_prev_workdays_from_friday():
    """從週五往回 3 個工作日（含當日）= 三 / 四 / 五。"""
    out = bf._prev_workdays('20260515', 3)  # Fri
    assert out == ['20260513', '20260514', '20260515']


def test_backfill_counts_success_holiday_failed(monkeypatch):
    """三種回值的計數要分開：成功 / 假日 / 失敗。"""
    calls = []

    def fake_fetch(date_str):
        calls.append(('fetch', date_str))
        if date_str.endswith('11'):
            return None                       # 失敗
        if date_str.endswith('12'):
            return pd.DataFrame()             # 假日
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 100,
                              '_trust': 0, '_dealer': 0}])  # 成功

    def fake_save(date_str, df):
        calls.append(('save', date_str))
        return len(df)

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', fake_save)

    s, h, f = bf.backfill(days=5, end_date_str='20260515', sleep_between=0)
    # 5/11~15：11 失敗、12 假日、13/14/15 成功
    assert s == 3
    assert h == 1
    assert f == 1
    # 成功的天才會呼叫 save
    save_calls = [d for tag, d in calls if tag == 'save']
    assert set(save_calls) == {'20260513', '20260514', '20260515'}


def test_backfill_does_not_abort_on_exception(monkeypatch):
    """個別日期 raise → 該日記 failed 但其他日繼續跑。"""
    def fake_fetch(date_str):
        if date_str == '20260514':
            raise RuntimeError('boom')
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 100,
                              '_trust': 0, '_dealer': 0}])

    def fake_save(date_str, df):
        return 1

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', fake_save)

    s, h, f = bf.backfill(days=5, end_date_str='20260515', sleep_between=0)
    # 5 個工作日，5/14 例外 → 成功 4 / 失敗 1
    assert s == 4
    assert h == 0
    assert f == 1


def test_backfill_default_days_is_10(monkeypatch):
    """預設 days=10。"""
    seen_days = []
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: (seen_days.append(d) or pd.DataFrame()))
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 0)
    bf.backfill(end_date_str='20260515', sleep_between=0)
    assert len(seen_days) == 10


def test_backfill_upsert_safe_repeated_calls(monkeypatch):
    """重複跑同樣的日期：fetch 命中快取（mock 直接回成功），save 被呼叫多次都 OK。"""
    save_calls = []
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                                                  '_trust': 0, '_dealer': 0}]))
    monkeypatch.setattr(bf, 'save_t86_to_history',
                        lambda d, df: (save_calls.append(d) or 1))
    s1, _, _ = bf.backfill(days=3, end_date_str='20260515', sleep_between=0)
    s2, _, _ = bf.backfill(days=3, end_date_str='20260515', sleep_between=0)
    assert s1 == s2 == 3
    # 同日期被呼叫兩次（重複跑不應該爆炸；UPSERT 由 SQL 端保證冪等）
    assert save_calls.count('20260515') == 2
