"""
時間相關小工具。所有「目前台灣時間」的計算統一從這裡來。
"""
from datetime import datetime, timedelta, timezone

import config


def tw_now():
    """目前台灣時間（naive datetime；UTC+8）"""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def get_target_date(run_mode):
    """
    依執行模式決定目標分析日期（YYYYMMDD 字串）。
      preview ── 盤前複習：往前找一個交易日
      close   ── 盤後結算：今天若是週六/日往前找
      auto    ── 17:00 之後用今天，之前用前一交易日
    """
    now  = tw_now()
    base = now.date()
    hour = now.hour

    if run_mode == 'preview':
        delta = 3 if base.weekday() == 0 else 1
        base -= timedelta(days=delta)
    elif run_mode == 'close':
        if   base.weekday() == 5:
            base -= timedelta(days=1)
        elif base.weekday() == 6:
            base -= timedelta(days=2)
    else:  # auto
        if hour < config.DATA_READY_HOUR:
            delta = 3 if base.weekday() == 0 else (
                    2 if base.weekday() == 6 else 1)
            base -= timedelta(days=delta)
        else:
            if   base.weekday() == 5:
                base -= timedelta(days=1)
            elif base.weekday() == 6:
                base -= timedelta(days=2)
    return base.strftime('%Y%m%d')


def prev_months(date_str, n=7):
    """從 date_str (YYYYMMDD) 往回取 n 個月（含當月），回 ['YYYYMM', ...]"""
    target = datetime.strptime(date_str, '%Y%m%d')
    months, d = [], target.replace(day=1)
    for _ in range(n):
        months.append(d.strftime('%Y%m'))
        d = (d - timedelta(days=1)).replace(day=1)
    return months


def next_friday(from_date, n=1):
    """從 from_date 往後找第 n 個週五（不含當天）"""
    d, cnt = from_date, 0
    while True:
        d += timedelta(days=1)
        if d.weekday() == 4:
            cnt += 1
            if cnt == n:
                return d


def roc_to_date(s):
    """民國日期字串（'114/01/02'）→ datetime.date"""
    y, m, d = str(s).split('/')
    return datetime(int(y) + 1911, int(m), int(d)).date()
