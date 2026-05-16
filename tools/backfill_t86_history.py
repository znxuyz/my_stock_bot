"""
手動補抓 T86 歷史資料，寫入 daily_t86_history。

用途：v6.2 首次部署 / 資料庫遷移 / 長假後補洞，把過去 N 個交易日的
三大法人買賣超灌進 daily_t86_history。Stalker 累積偵測需要至少 5 天歷史
才能出訊號，部署後第一次跑這個腳本可以省掉 5 個交易日的等候期。

使用：
    python -m tools.backfill_t86_history                  # 預設過去 10 個交易日
    python -m tools.backfill_t86_history --days 20
    python -m tools.backfill_t86_history --days 30 --end-date 20260515

行為：
- UPSERT 進 daily_t86_history，重複執行不會產生重複資料
- 個別日期失敗（網路 / 限速）會 log warning 但不中斷整體流程
- TWSE 假日（回「查詢無資料」）會被識別為 holiday，不計入失敗
- 完整結束後回報 (success / holiday / failed) 三個計數
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
    """執行 T86 回填。

    參數：
      days          往回抓 N 個交易日（預設 10）
      end_date_str  終止日期 YYYYMMDD（預設今日 = 系統日期）
      sleep_between TWSE 呼叫間隔秒數（None 表示用 config 預設）

    回傳 (success, holiday, failed) 三個 int。
    """
    if end_date_str is None:
        end_date_str = datetime.now().strftime('%Y%m%d')
    if sleep_between is None:
        sleep_between = config.TWSE_CALL_INTERVAL_SEC

    dates = _prev_workdays(end_date_str, days)
    success = holiday = failed = 0
    logger.info('[backfill] 目標 %d 個交易日：%s ~ %s',
                len(dates), dates[0], dates[-1])

    for d in dates:
        try:
            df = fetch_t86_cached(d)
            if df is None:
                failed += 1
                logger.warning('[backfill] %s 抓取失敗（TWSE 限速 / 異常），跳過', d)
            elif df.empty:
                holiday += 1
                logger.info('[backfill] %s 假日（TWSE 回查詢無資料）', d)
            else:
                n = save_t86_to_history(d, df)
                success += 1
                logger.info('[backfill] %s 寫入 %d 筆 → daily_t86_history', d, n)
        except Exception as e:
            failed += 1
            logger.warning('[backfill] %s 例外，跳過：%s', d, e)
        if sleep_between > 0:
            time.sleep(sleep_between)

    return success, holiday, failed


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
    s, h, f = backfill(days=args.days, end_date_str=args.end_date)
    logger.info('[backfill] 完成：成功 %d / 假日 %d / 失敗 %d', s, h, f)
    # 任何日期失敗就回傳非 0 exit code，方便 CI / shell 偵測
    return 0 if f == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
