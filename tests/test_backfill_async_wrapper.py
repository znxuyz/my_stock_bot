"""驗證 backfill() 的並發包裝。

對應 discord.py 的 `asyncio.to_thread`：因為這個 bot 是同步 HTTP（沒有
event loop），等價物是用 `ThreadPoolExecutor` 把 TWSE blocking I/O 拆成
N 個 worker 跑，總時延從 days×interval 降到 days÷N×interval。

本檔測：
  - concurrency > 1 時用 ThreadPoolExecutor（不阻塞單一 worker）
  - concurrency = 1 退回純序列模式（CLI 預設）
  - 平行模式下 fetch_t86_cached 真的被多次呼叫，且每次參數正確
  - 平行模式下個別 worker 例外不會打死整體
"""
import threading
import time

import pandas as pd

from tools import backfill_t86_history as bf


def test_concurrency_gt_1_uses_thread_pool(monkeypatch):
    """concurrency=3 → fetch_t86_cached 在 multiple thread 內被呼叫。

    判斷依據：紀錄 fetch_t86_cached 呼叫時的 thread ident，至少要有 2 個不同 ident。
    """
    seen_threads = []
    lock = threading.Lock()

    def fake_fetch(date_str):
        with lock:
            seen_threads.append(threading.get_ident())
        # 模擬 1.5s 的 I/O，讓 worker 真的能並發
        time.sleep(0.3)
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = bf.backfill(days=6, end_date_str='20260515',
                    sleep_between=0, concurrency=3)

    # 6 天全部成功
    assert len(r['written_dates']) == 6
    # 確實是平行（≥ 2 個 thread 參與）
    distinct = set(seen_threads)
    assert len(distinct) >= 2, f'平行模式應該至少 2 個 thread，實際 {len(distinct)}'


def test_concurrency_eq_1_runs_sequentially(monkeypatch):
    """concurrency=1 → 全程都在同一個 thread。"""
    seen_threads = []

    def fake_fetch(date_str):
        seen_threads.append(threading.get_ident())
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = bf.backfill(days=5, end_date_str='20260515',
                    sleep_between=0, concurrency=1)
    assert len(r['written_dates']) == 5
    # 序列：所有呼叫都在 caller thread
    assert len(set(seen_threads)) == 1


def test_parallel_speedup_over_sequential(monkeypatch):
    """模擬慢 I/O：並發模式應該明顯比序列快。

    每次 fetch 假裝睡 0.15s。10 天序列 ≈ 1.5s、並發=3 ≈ 0.5s。
    斷言並發比序列快 ≥ 1.5×（保守邊界，避免 CI 機器抖動誤判）。
    """
    def slow_fetch(date_str):
        time.sleep(0.15)
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', slow_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r1 = bf.backfill(days=10, end_date_str='20260515',
                     sleep_between=0, concurrency=1)
    r3 = bf.backfill(days=10, end_date_str='20260515',
                     sleep_between=0, concurrency=3)

    assert r1['elapsed_sec'] > r3['elapsed_sec'] * 1.5, (
        f'並發 (concurrency=3) 應該比序列明顯快，'
        f'結果序列 {r1["elapsed_sec"]}s vs 並發 {r3["elapsed_sec"]}s'
    )


def test_parallel_individual_failure_does_not_abort(monkeypatch):
    """並發模式下，某個 worker 失敗不能讓其他 worker 整體死掉。"""
    def fake_fetch(date_str):
        if date_str == '20260513':
            raise RuntimeError('boom on day 13')
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = bf.backfill(days=5, end_date_str='20260515',
                    sleep_between=0, concurrency=3)
    assert r['failed_dates'] == ['20260513']
    # 其他 4 天還是有寫入
    assert len(r['written_dates']) == 4


def test_default_concurrency_is_3(monkeypatch):
    """v6.2.1：預設 concurrency=3，給 Discord /backfill 有夠快的回應。"""
    seen_threads = []
    lock = threading.Lock()

    def fake_fetch(date_str):
        with lock:
            seen_threads.append(threading.get_ident())
        time.sleep(0.1)
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    # 不傳 concurrency → 用預設值
    bf.backfill(days=6, end_date_str='20260515', sleep_between=0)
    assert len(set(seen_threads)) >= 2


def test_save_to_history_is_serialised_under_concurrency(monkeypatch):
    """並發模式下 save 仍要被序列化（_SAVE_LOCK 保護），
    確保 5 個 worker 同時想寫時不會有兩個 save 同步進行。"""
    inflight = {'count': 0, 'max': 0}
    inflight_lock = threading.Lock()
    save_calls = []

    def fake_save(date_str, df):
        # 觀察「同時間有幾個 save 在跑」
        with inflight_lock:
            inflight['count'] += 1
            inflight['max'] = max(inflight['max'], inflight['count'])
        save_calls.append(date_str)
        time.sleep(0.05)
        with inflight_lock:
            inflight['count'] -= 1
        return 1

    def fake_fetch(date_str):
        time.sleep(0.05)
        return pd.DataFrame([{'sid_clean': '2330', '_foreign': 1,
                              '_trust': 0, '_dealer': 0}])

    monkeypatch.setattr(bf, 'fetch_t86_cached', fake_fetch)
    monkeypatch.setattr(bf, 'save_t86_to_history', fake_save)

    r = bf.backfill(days=5, end_date_str='20260515',
                    sleep_between=0, concurrency=3)
    assert len(r['written_dates']) == 5
    assert len(save_calls) == 5
    # _SAVE_LOCK 在 _fetch_one 內保護 save → 永遠只能有 1 個 save 在跑
    assert inflight['max'] == 1, (
        f'save_t86_to_history 應被 _SAVE_LOCK 序列化，'
        f'但偵測到最多有 {inflight["max"]} 個同時進行'
    )
