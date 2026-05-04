"""
大盤資料：MI_INDEX 加權指數、MI_QFIIS 大盤外資買賣超歷史。
"""
from datetime import datetime, timedelta

import pandas as pd

from twse_http import safe_get, safe_read_csv


_MI_INDEX_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
_MI_QFIIS_URL = 'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS'


def get_market_info(date_str):
    """
    抓加權指數的收盤、漲跌、漲跌幅。
    回傳 {'close': ..., 'diff': ..., 'pct': ...} 或 None。
    """
    r = safe_get(
        _MI_INDEX_URL,
        params={'response': 'csv', 'date': date_str, 'type': 'IND'},
        timeout=20, retries=2, wait=10,
    )
    if r is None or '查詢無資料' in r.text:
        return None
    try:
        df = safe_read_csv(r.text, 'MI_INDEX-IND', skiprows=1)
        if df.empty:
            return None
        row = df[df.iloc[:, 0].astype(str).str.contains('發行量加權股價指數', na=False)]
        if row.empty:
            return None
        row = row.iloc[0]

        idx_p    = float(str(row.iloc[1]).replace(',', ''))
        idx_diff = pd.to_numeric(str(row.iloc[3]).replace(',', ''), errors='coerce')
        sign_str = str(row.iloc[2])
        if '−' in sign_str or sign_str.strip().startswith('-'):
            idx_diff = -abs(idx_diff)
        else:
            idx_diff = abs(idx_diff)
        denom = idx_p - idx_diff
        idx_chg = round((idx_diff / denom) * 100, 2) if denom != 0 else 0
        return {'close': idx_p, 'diff': idx_diff, 'pct': idx_chg}
    except Exception as e:
        print(f'[大盤解析失敗] {e}')
        return None


def fetch_market_foreign_history(date_str, days=3):
    """
    抓近 days 個交易日的大盤外資淨買超（億元）。
    回傳 list（由舊到新），抓不到的日子省略；無資料回空 list。
    """
    base_date = datetime.strptime(date_str, '%Y%m%d').date()
    history = []
    checked = 0
    try:
        for i in range(1, 8):
            d = base_date - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            ds = d.strftime('%Y%m%d')
            r = safe_get(
                _MI_QFIIS_URL,
                params={'response': 'csv', 'date': ds, 'selectType': 'ALLBUT0999'},
                timeout=10, retries=1, wait=3,
            )
            checked += 1
            if r is None or '查詢無資料' in r.text:
                if checked >= days:
                    break
                continue
            for line in r.text.splitlines():
                if '合計' not in line:
                    continue
                parts = [v.strip().strip('"').replace(',', '') for v in line.split(',')]
                try:
                    buy  = float(parts[2]) if len(parts) > 2 else 0
                    sell = float(parts[3]) if len(parts) > 3 else 0
                    net  = round((buy - sell) / 100_000_000, 1)
                    history.insert(0, net)
                except (ValueError, IndexError):
                    history.insert(0, 0)
                break
            if checked >= days:
                break
    except Exception as e:
        print(f'[大盤外資歷史] 抓取失敗：{e}')
    return history
