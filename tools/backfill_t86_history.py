"""
T86 歷史資料回填工具（v6.2）。

可由兩個入口呼叫：
  1. CLI：python -m tools.backfill_t86_history [--days N] [--end-date YYYYMMDD]
  2. Discord：/backfill days:N（owner-only；走 `discord_bot.admin_commands`）

兩條路徑都共用 backfill() 主函式：UPSERT 進 daily_t86_history，
個別日期失敗不中斷，假日獨立計數。
"""
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

import config
from twse_t86 import fetch_t86_cached, save_t86_to_history

logger = logging.getLogger(__name__)


def _prev_workdays(end_date_str, n):
    """從 end_date_str（含當日）往回找 n 個工作日（Mon-Fri）。

    這層只用週末過濾，不打 TWSE；實際是否假日由 fetch_t86_cached 判定。
    回傳 ['YYYYMMDD', ...] 由舊到新。
    """
    base = datetime.strptime(end_date_str, '%Y%m%d').date()
    out = []
    cur = base
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.strftime('%Y%m%d'))
        cur -= timedelta(days=1)
    return list(reversed(out))


def backfill(days=10, end_date_str=None, sleep_between=None):
    """執行 T86 回填。CLI 與 Discord /backfill 共用的主函式。

    參數：
      days          往回抓 N 個交易日（預設 10）
      end_date_str  終止日期 YYYYMMDD（預設今日）
      sleep_between TWSE 呼叫間隔秒數（None 表示用 config 預設）

    回傳 dict：
      requested_days  請求天數
      end_date        終止日期字串
      written_dates   成功寫入的日期 list[YYYYMMDD]
      holiday_dates   假日 list
      failed_dates    失敗 list
      total_rows      總共寫入幾筆紀錄
    """
    if end_date_str is None:
        end_date_str = datetime.now().strftime('%Y%m%d')
    if sleep_between is None:
        sleep_between = config.TWSE_CALL_INTERVAL_SEC

    dates = _prev_workdays(end_date_str, days)
    result = {
        'requested_days': days,
        'end_date': end_date_str,
        'written_dates': [],
        'holiday_dates': [],
        'failed_dates': [],
        'total_rows': 0,
    }
    logger.info('[backfill] 目標 %d 個交易日：%s ~ %s',
                len(dates), dates[0], dates[-1])

    for d in dates:
        try:
            df = fetch_t86_cached(d)
            if df is None:
                result['failed_dates'].append(d)
                logger.warning('[backfill] %s 抓取失敗（TWSE 限速 / 異常），跳過', d)
            elif df.empty:
                result['holiday_dates'].append(d)
                logger.info('[backfill] %s 假日（TWSE 回查詢無資料）', d)
            else:
                n = save_t86_to_history(d, df)
                result['written_dates'].append(d)
                result['total_rows'] += n
                logger.info('[backfill] %s 寫入 %d 筆 → daily_t86_history', d, n)
        except Exception as e:
            result['failed_dates'].append(d)
            logger.warning('[backfill] %s 例外，跳過：%s', d, e)
        if sleep_between > 0:
            time.sleep(sleep_between)

    return result


def main():
    p = argparse.ArgumentParser(description='手動補抓 T86 歷史資料到 daily_t86_history')
    p.add_argument('--days', type=int, default=10,
                   help='往回抓 N 個交易日（預設 10）')
    p.add_argument('--end-date', type=str, default=None,
                   help='終止日期 YYYYMMDD（預設今日）')
    args = p.parse_args()

    from logging_setup import setup_logging
    setup_logging()
    logger.info('[backfill] 開始：days=%d end_date=%s',
                args.days, args.end_date or '(today)')
    r = backfill(days=args.days, end_date_str=args.end_date)
    s = len(r['written_dates'])
    h = len(r['holiday_dates'])
    f = len(r['failed_dates'])
    logger.info('[backfill] 完成：成功 %d / 假日 %d / 失敗 %d / 總筆數 %d',
                s, h, f, r['total_rows'])
    return 0 if f == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
