"""tools/backfill_t86_history.py 測試（mock fetch + save，不打 TWSE / DB）。

v6.2：backfill() 回 dict（不再是 tuple），含 written/holiday/failed 三條日期清單 +
total_rows 總筆數。CLI 與 Discord /backfill 都共用這個主函式。
"""
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


def test_backfill_returns_dict_with_expected_keys(monkeypatch):
    """v6.2 backfill 回 dict，含 written/holiday/failed/total_rows 等欄位。"""
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: pd.DataFrame([{'sid_clean': '2330',
                                                  '_foreign': 100, '_trust': 0,
                                                  '_dealer': 0}]))
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = bf.backfill(days=3, end_date_str='20260515', sleep_between=0)
    assert isinstance(r, dict)
    assert set(r.keys()) >= {
        'requested_days', 'end_date',
        'written_dates', 'holiday_dates', 'failed_dates', 'total_rows',
    }
    assert r['requested_days'] == 3
    assert r['end_date'] == '20260515'


def test_backfill_counts_success_holiday_failed(monkeypatch):
    """三種回值的計數要分開：成功 / 假日 / 失敗。"""
    def fake_fetch(date_str):
        if date_str.endswith('11'):
            return None                       # 失敗
        if date_str.endswith('12'):
            return pd.DataFrame()             # 假日
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 100,
                              '_trust': 0, '_dealer': 0}])  # 成功

    save_calls = []
    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history',
                        lambda d, df: (save_calls.append(d) or 1))

    r = bf.backfill(days=5, end_date_str='20260515', sleep_between=0)
    assert len(r['written_dates']) == 3
    assert len(r['holiday_dates']) == 1
    assert len(r['failed_dates']) == 1
    assert r['total_rows'] == 3
    assert set(save_calls) == {'20260513', '20260514', '20260515'}
    # holiday / failed 日期落在正確 bucket
    assert '20260511' in r['failed_dates']
    assert '20260512' in r['holiday_dates']


def test_backfill_does_not_abort_on_exception(monkeypatch):
    """個別日期 raise → 該日記 failed 但其他日繼續跑。"""
    def fake_fetch(date_str):
        if date_str == '20260514':
            raise RuntimeError('boom')
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 100,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = bf.backfill(days=5, end_date_str='20260515', sleep_between=0)
    assert len(r['written_dates']) == 4
    assert r['failed_dates'] == ['20260514']
    assert r['holiday_dates'] == []


def test_backfill_default_days_is_10(monkeypatch):
    """預設 days=10。"""
    seen_days = []
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: (seen_days.append(d) or pd.DataFrame()))
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 0)
    r = bf.backfill(end_date_str='20260515', sleep_between=0)
    assert len(seen_days) == 10
    assert r['requested_days'] == 10


def test_backfill_upsert_safe_repeated_calls(monkeypatch):
    """重複跑同樣的日期：fetch 命中快取（mock 直接回成功），save 被呼叫多次都 OK。"""
    save_calls = []
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                                                  '_trust': 0, '_dealer': 0}]))
    monkeypatch.setattr(bf, 'save_t86_to_history',
                        lambda d, df: (save_calls.append(d) or 1))
    r1 = bf.backfill(days=3, end_date_str='20260515', sleep_between=0)
    r2 = bf.backfill(days=3, end_date_str='20260515', sleep_between=0)
    assert len(r1['written_dates']) == len(r2['written_dates']) == 3
    # 同日期被呼叫兩次（重複跑不應該爆炸；UPSERT 由 SQL 端保證冪等）
    assert save_calls.count('20260515') == 2


def test_cli_main_runs_with_default_args(monkeypatch):
    """CLI 入口 main() 仍能跑（重構後 backfill 介面改變，CLI 要跟著對齊）。"""
    monkeypatch.setattr(bf, 'fetch_t86_cached', lambda d: pd.DataFrame())
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 0)
    # 假裝沒給 args（用預設 days=10）
    monkeypatch.setattr('sys.argv', ['backfill_t86_history'])
    # 不要實際 setup_logging 接管 root logger
    import logging_setup
    monkeypatch.setattr(logging_setup, 'setup_logging', lambda: None)

    rc = bf.main()
    assert rc == 0  # 沒有失敗 → exit 0


def test_cli_main_returns_nonzero_when_any_day_failed(monkeypatch):
    """任何一天失敗 → main 回 exit code 非 0。"""
    monkeypatch.setattr(bf, 'fetch_t86_cached', lambda d: None)  # 全失敗
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 0)
    monkeypatch.setattr('sys.argv', ['backfill_t86_history', '--days', '2'])
    import logging_setup
    monkeypatch.setattr(logging_setup, 'setup_logging', lambda: None)

    rc = bf.main()
    assert rc != 0
