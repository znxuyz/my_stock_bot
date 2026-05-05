"""
T+1 撮合：把 fill_status='pending' 的紀錄用其 T+1 K 棒判定進場結果。
"""
import logging
import time
from datetime import timedelta

import pandas as pd

import config
import db
from twse_kbar import fetch_kbars_with_open

logger = logging.getLogger(__name__)


def get_t1_kbar(sid, screen_date):
    """取 sid 在 screen_date 之後第一個交易日的 K 棒；找不到回 None。"""
    yyyymm = screen_date.strftime('%Y%m')
    df = fetch_kbars_with_open(sid, yyyymm)
    after = df[df['date'] > screen_date] if not df.empty else None
    if after is None or after.empty:
        next_dt    = screen_date.replace(day=28) + timedelta(days=4)
        next_month = next_dt.strftime('%Y%m')
        df2 = fetch_kbars_with_open(sid, next_month)
        if df2.empty:
            return None
        after = df2[df2['date'] > screen_date]
    if after.empty:
        return None
    row = after.iloc[0]
    return {
        'date':  row['date'],
        'open':  float(row['open']),
        'high':  float(row['high']),
        'low':   float(row['low']),
        'close': float(row['close']),
    }


def get_period_kbars(sid, start_date, end_date):
    """取 sid 在 [start_date, end_date]（含）區間的 K 棒。"""
    months = set()
    d = start_date.replace(day=1)
    while d <= end_date:
        months.add(d.strftime('%Y%m'))
        d = (d + timedelta(days=32)).replace(day=1)
    frames = []
    for ym in sorted(months):
        df = fetch_kbars_with_open(sid, ym)
        if not df.empty:
            frames.append(df)
        time.sleep(config.TWSE_CALL_INTERVAL_SEC)
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames, ignore_index=True).drop_duplicates(subset=['date'])
    return full[(full['date'] >= start_date) & (full['date'] <= end_date)].reset_index(drop=True)


def fill_pending_t1_entries(today):
    """
    把所有 fill_status='pending' 且 screen_date < today 的紀錄
    用其 T+1 K 棒判定進場結果（filled / missed），寫回 DB。
    """
    pendings = db.get_records_needing_t1_check(today)
    if not pendings:
        logger.info('[T+1撮合] 無待撮合紀錄')
        return

    cache = {}
    for r in pendings:
        sid = r['sid']
        sd  = r['screen_date']
        key = (sid, sd)
        if key not in cache:
            cache[key] = get_t1_kbar(sid, sd)
            time.sleep(config.TWSE_CALL_INTERVAL_SEC)
        kbar = cache[key]
        if kbar is None:
            logger.warning('[T+1撮合] %s %s 抓不到 T+1 K 棒，先跳過', sid, sd)
            continue

        # 強勢追漲不接刀：跳空跌破收盤就算 missed
        allow_gap_down = (r.get('chase_mode') != 'strong_chase')
        status, fill_price = db.determine_t1_fill(
            kbar['open'], kbar['high'], kbar['low'],
            float(r['entry_zone_low'])  if r['entry_zone_low']  is not None else None,
            float(r['entry_zone_high']) if r['entry_zone_high'] is not None else None,
            allow_gap_down=allow_gap_down,
        )
        try:
            # 把 T+1 開盤價也傳進去，missed 也會留下這個資料供後續「假設有買到」分析
            db.fill_t1_entry(r['id'], kbar['date'], status, fill_price, t1_open=kbar['open'])
            tag = '✅成交' if status == 'filled' else '❌未進場'
            fp  = f'@{fill_price}' if fill_price else ''
            logger.info('[T+1撮合] %s %s → %s %s %s', sid, sd, kbar['date'], tag, fp)
        except Exception as e:
            logger.error('[T+1撮合] %s 寫入失敗：%s', sid, e)
    logger.info('[T+1撮合] 完成，處理 %d 筆', len(pendings))
