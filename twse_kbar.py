"""
TWSE 個股月 K 棒抓取與本機快取。

底層：_fetch_full_kbars(sid, yyyymm) 抓 6 欄完整 K 棒（含 open）並存快取。
對外：
  fetch_stock_day_fast  ── 5 欄（不含 open），給歷史指標計算
  fetch_kbars_with_open ── 6 欄（含 open），給 T+1 撮合 / 結算
兩者共用同一份快取 → 同一檔同一個月只打 TWSE 一次。
"""
import json
import logging
import os
import re as _re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from time_utils import roc_to_date
from twse_http import safe_get, safe_read_csv

logger = logging.getLogger(__name__)

_STOCK_DAY_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY'
_FULL_COLS = ['date', 'open', 'high', 'low', 'close', 'volume']


# ─────────── 快取 ───────────
def _cache_path(sid, yyyymm):
    return f'{config.KBAR_CACHE_DIR}/{sid}_{yyyymm}.json'


def _kbar_cache_get(sid, yyyymm, required_cols=('date', 'close')):
    """命中快取回 DataFrame；過期、缺欄位、讀失敗回 None。"""
    path = _cache_path(sid, yyyymm)
    if not os.path.exists(path):
        return None
    today_yyyymm = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y%m')
    is_current = (yyyymm >= today_yyyymm)
    max_age = config.KBAR_CACHE_TTL_CURRENT_SEC if is_current else config.KBAR_CACHE_TTL_HISTORY_SEC
    if (time.time() - os.path.getmtime(path)) > max_age:
        return None
    try:
        with open(path, encoding='utf-8') as f:
            records = json.load(f)
        if not records:
            return None
        df = pd.DataFrame(records)
        if not all(c in df.columns for c in required_cols):
            # 舊快取格式（5 欄缺 open）→ 視為 cache miss 重抓
            return None
        df['date'] = pd.to_datetime(df['date']).dt.date
        return df
    except Exception as e:
        logger.warning('[KBar 快取] %s/%s 讀取失敗：%s', sid, yyyymm, e)
        return None


def _kbar_cache_set(sid, yyyymm, df):
    try:
        os.makedirs(config.KBAR_CACHE_DIR, exist_ok=True)
        path = _cache_path(sid, yyyymm)
        records = df.copy()
        records['date'] = records['date'].astype(str)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(records.to_dict('records'), f, ensure_ascii=False)
    except Exception as e:
        logger.warning('[KBar 快取] %s/%s 寫入失敗：%s', sid, yyyymm, e)


# ─────────── CSV 共用解析 ───────────
def _locate_header(lines):
    """找出 STOCK_DAY CSV 的表頭行 index，找不到回 None。"""
    for i, line in enumerate(lines):
        if '日期' in line and ('收盤' in line or '成交' in line):
            return i
    for i, line in enumerate(lines):
        if _re.search(r'\d{3}/\d{2}/\d{2}', line):
            return max(0, i - 1)
    return None


def _parse_stock_day_csv(text, sid, columns):
    """columns: 'date'/'open'/'high'/'low'/'close'/'volume' 子集；資料無效回 empty。"""
    lines = text.splitlines()
    header_i = _locate_header(lines)
    if header_i is None:
        return pd.DataFrame()
    df = safe_read_csv('\n'.join(lines[header_i:]), f'STOCK_DAY-{sid}', min_cols=7)
    if df.empty:
        return pd.DataFrame()
    mask = df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)
    df = df[mask].copy()
    if df.empty:
        return pd.DataFrame()

    # STOCK_DAY 欄位固定順序：日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數
    df['date']   = df.iloc[:, 0].apply(roc_to_date)
    df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce')
    df['open']   = pd.to_numeric(df.iloc[:, 3].astype(str).str.replace(',', ''), errors='coerce')
    df['high']   = pd.to_numeric(df.iloc[:, 4].astype(str).str.replace(',', ''), errors='coerce')
    df['low']    = pd.to_numeric(df.iloc[:, 5].astype(str).str.replace(',', ''), errors='coerce')
    df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce')

    out_cols = [c for c in columns if c in df.columns]
    return df[out_cols].dropna(subset=['date', 'close']).reset_index(drop=True)


def _fetch_full_kbars(sid, yyyymm):
    """
    抓並快取 6 欄完整 K 棒（date / open / high / low / close / volume）。
    所有對外 fetcher 都走這個底層 → 同一檔同一個月只打 TWSE 一次。
    """
    cached = _kbar_cache_get(sid, yyyymm, required_cols=_FULL_COLS)
    if cached is not None:
        return cached
    r = safe_get(
        _STOCK_DAY_URL,
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=8,
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    df = _parse_stock_day_csv(r.text, sid, _FULL_COLS)
    if not df.empty:
        _kbar_cache_set(sid, yyyymm, df)
    return df


# ─────────── 對外 API ───────────
def fetch_stock_day_fast(sid, yyyymm):
    """5 欄 K 棒（無 open），給歷史指標計算。"""
    df = _fetch_full_kbars(sid, yyyymm)
    if df.empty:
        return df
    cols = [c for c in ['date', 'close', 'high', 'low', 'volume'] if c in df.columns]
    return df[cols].copy()


def fetch_kbars_with_open(sid, yyyymm):
    """6 欄 K 棒（含 open），給 T+1 撮合 / 結算用。"""
    return _fetch_full_kbars(sid, yyyymm)


def build_history_fast(sid, months):
    """逐月抓並串接成完整歷史 DataFrame，重複日期會去重。"""
    frames = []
    for yyyymm in months:
        df_m = fetch_stock_day_fast(sid, yyyymm)
        if df_m.empty:
            time.sleep(2)
            df_m = fetch_stock_day_fast(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        time.sleep(config.TWSE_CALL_INTERVAL_SEC)
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames)
        .drop_duplicates('date')
        .sort_values('date')
        .reset_index(drop=True)
    )
