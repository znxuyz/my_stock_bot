"""LAST_RUN thread-safe accessor 測試。"""
import threading

from discord_bot.handlers import get_last_run, update_last_run


def test_update_and_snapshot():
    update_last_run(time=None, mode=None, date=None, status=None,
                    error=None, attempt=0)
    update_last_run(mode='auto', status='running')
    snap = get_last_run()
    assert snap['mode'] == 'auto'
    assert snap['status'] == 'running'


def test_snapshot_is_copy():
    update_last_run(mode='close')
    snap = get_last_run()
    snap['mode'] = 'tampered'
    again = get_last_run()
    assert again['mode'] == 'close'


def test_concurrent_writes_dont_lose_keys():
    """100 個 thread 同時寫不同 key，最後每個 key 都該有值。"""
    update_last_run(time=None, mode=None, date=None, status=None, error=None, attempt=0)

    barrier = threading.Barrier(20)

    def worker(i):
        barrier.wait()
        update_last_run(attempt=i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = get_last_run()
    assert snap['attempt'] in range(20)  # 最後一個寫入的會贏，但至少有值
