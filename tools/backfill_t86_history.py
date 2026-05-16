"""
T86 歷史資料回填工具（v6.2）。

可由兩個入口呼叫：
  1. CLI：python -m tools.backfill_t86_history [--days N] [--end-date YYYYMMDD]
  2. Discord：/backfill days:N（走 `discord_bot.admin_commands.cmd_backfill_core`）

兩條路徑都共用 backfill() 主函式：UPSERT 進 daily_t86_history，
個別日期失敗不中斷，假日獨立計數。

v6.2.1：新增 concurrency 參數做平行抓取（fetch_t86_cached 共享記憶體
快取，dict 寫入有 GIL 保護不會壞）。預設 3 個 worker 把 10 日抓取
從 ~8s 降到 ~3s，減少 Discord interaction 超時風險。
"""
import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock

import config
from twse_t86 import fetch_t86_cached, save_t86_to_history

logger = logging.getLogger(__name__)


# DB UPSERT 用 lock 避免兩個 thread 同時寫同一行（雖然 SQL 端有 UPSERT
# 保護，但 psycopg2 connection 不是 thread-safe，每個 thread 各拿自己的 conn 即可）
_SAVE_LOCK = Lock()


def _prev_workdays(end_date_str, n):
    """從 end_date_str（含當日）往回找 n 個工作日（Mon-Fri）。"""
    base = datetime.strptime(end_date_str, '%Y%m%d').date()
    out = []
    cur = base
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.strftime('%Y%m%d'))
        cur -= timedelta(days=1)
    return list(reversed(out))


def _fetch_one(date_str, sleep_between):
    """單日抓取 + 寫入。回傳 ('written'|'holiday'|'failed', date_str, rows_written)。"""
    try:
        df = fetch_t86_cached(date_str)
        if df is None:
            logger.warning('[backfill] %s 抓取失敗', date_str)
            return ('failed', date_str, 0)
        if df.empty:
            logger.debug('[backfill] %s 假日', date_str)
            return ('holiday', date_str, 0)
        with _SAVE_LOCK:
            n = save_t86_to_history(date_str, df)
        logger.info('[backfill] %s 寫入 %d 筆', date_str, n)
        return ('written', date_str, n)
    except Exception as e:
        logger.warning('[backfill] %s 例外：%s', date_str, e)
        return ('failed', date_str, 0)
    finally:
        if sleep_between > 0:
            time.sleep(sleep_between)


def backfill(days=10, end_date_str=None, sleep_between=None, concurrency=3):
    """執行 T86 回填。CLI 與 Discord /backfill 共用的主函式。

    參數：
      days          往回抓 N 個交易日（預設 10）
      end_date_str  終止日期 YYYYMMDD（預設今日）
      sleep_between TWSE 呼叫間隔秒數（None 表示用 config 預設）
      concurrency   平行 worker 數（預設 3）。設 1 = 純序列模式。

    回傳 dict：
      requested_days  請求天數
      end_date        終止日期字串
      written_dates   成功寫入的日期 list[YYYYMMDD]（已排序）
      holiday_dates   假日 list（已排序）
      failed_dates    失敗 list（已排序）
      total_rows      總共寫入幾筆紀錄
      elapsed_sec     實際耗時（秒）
    """
    if end_date_str is None:
        end_date_str = datetime.now().strftime('%Y%m%d')
    if sleep_between is None:
        sleep_between = config.TWSE_CALL_INTERVAL_SEC
    concurrency = max(1, int(concurrency))

    dates = _prev_workdays(end_date_str, days)
    result = {
        'requested_days': days,
        'end_date': end_date_str,
        'written_dates': [],
        'holiday_dates': [],
        'failed_dates': [],
        'total_rows': 0,
        'elapsed_sec': 0.0,
    }
    logger.info('[backfill] 目標 %d 個交易日：%s ~ %s（concurrency=%d）',
                len(dates), dates[0], dates[-1], concurrency)
    t0 = time.time()

    # 併發模式下每個 worker 自己 sleep；序列模式維持原本節奏
    per_call_sleep = sleep_between if concurrency == 1 else (sleep_between / concurrency)

    if concurrency == 1:
        outcomes = [_fetch_one(d, per_call_sleep) for d in dates]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_fetch_one, d, per_call_sleep) for d in dates]
            outcomes = [f.result() for f in as_completed(futures)]

    for outcome, d, n in outcomes:
        if outcome == 'written':
            result['written_dates'].append(d)
            result['total_rows'] += n
        elif outcome == 'holiday':
            result['holiday_dates'].append(d)
        else:
            result['failed_dates'].append(d)
    result['written_dates'].sort()
    result['holiday_dates'].sort()
    result['failed_dates'].sort()
    result['elapsed_sec'] = round(time.time() - t0, 2)
    logger.info('[backfill] 完成（%.2fs）：成功 %d / 假日 %d / 失敗 %d / 總筆數 %d',
                result['elapsed_sec'],
                len(result['written_dates']), len(result['holiday_dates']),
                len(result['failed_dates']), result['total_rows'])
    return result


def main():
    p = argparse.ArgumentParser(description='手動補抓 T86 歷史資料到 daily_t86_history')
    p.add_argument('--days', type=int, default=10,
                   help='往回抓 N 個交易日（預設 10）')
    p.add_argument('--end-date', type=str, default=None,
                   help='終止日期 YYYYMMDD（預設今日）')
    p.add_argument('--concurrency', type=int, default=3,
                   help='平行 worker 數（預設 3；設 1 為純序列）')
    args = p.parse_args()

    from logging_setup import setup_logging
    setup_logging()
    logger.info('[backfill] 開始：days=%d end_date=%s concurrency=%d',
                args.days, args.end_date or '(today)', args.concurrency)
    r = backfill(days=args.days, end_date_str=args.end_date,
                 concurrency=args.concurrency)
    s = len(r['written_dates'])
    h = len(r['holiday_dates'])
    f = len(r['failed_dates'])
    logger.info('[backfill] 完成：成功 %d / 假日 %d / 失敗 %d / 總筆數 %d / 耗時 %.2fs',
                s, h, f, r['total_rows'], r['elapsed_sec'])
    return 0 if f == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
