"""
TWSE T86 三大法人買賣超：抓取 + 解析 + 30 分鐘共享快取。
"""
import time

import pandas as pd

import config
from twse_http import safe_get, safe_read_csv, clean_sid


_T86_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86'

# date_str → (timestamp, parsed_df)
_T86_CACHE = {}


def parse_t86(text):
    """T86 CSV → DataFrame（含 sid_clean / _foreign / _trust / _total）"""
    print(f'[T86] 前300字：\n{text[:300]}\n{"─" * 40}')
    lines = text.splitlines()

    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
        print('[T86] 找不到表頭')
        return pd.DataFrame()

    print(f'[T86] 表頭第 {header_idx} 行：{lines[header_idx][:120]}')
    df = safe_read_csv('\n'.join(lines[header_idx:]), 'T86', min_cols=11)
    if df.empty:
        return pd.DataFrame()
    print(f'[T86] 共 {len(df.columns)} 欄：{list(df.columns)}')

    df = df[df.iloc[:, 0].astype(str).str.match(r'^[0-9A-Z]{4,6}$', na=False)].copy()
    if df.empty:
        print('[T86] 過濾後無有效股票列')
        return pd.DataFrame()

    df['sid_clean'] = clean_sid(df.iloc[:, 0])
    n = len(df.columns)

    # 固定欄位索引：4=外資, 10=投信, 18=三大法人合計
    if n >= 19:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = pd.to_numeric(df.iloc[:, 18], errors='coerce').fillna(0)
        print('[T86] 標準19欄格式，外資idx=4 投信idx=10 合計idx=18')
    elif n >= 11:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = df['_foreign'] + df['_trust']
        print(f'[T86] 備援{n}欄格式，外資idx=4 投信idx=10')
    else:
        print(f'[T86] 欄位數不足({n})，無法解析')
        return pd.DataFrame()

    print(f'[T86] 有效股票：{len(df)} 檔，外資非零：{(df["_foreign"] != 0).sum()}，'
          f'投信非零：{(df["_trust"] != 0).sum()}')
    return df


def fetch_t86_cached(date_str):
    """
    抓 T86 並 30 分鐘共享快取。
    回傳：
      DataFrame 非空 — 成功
      DataFrame 空    — 假日 / 查詢無資料
      None            — 抓取失敗（網路 / 限速）
    """
    now = time.time()
    if date_str in _T86_CACHE:
        ts, df = _T86_CACHE[date_str]
        if now - ts < config.T86_CACHE_TTL_SEC and df is not None:
            return df

    r = safe_get(
        _T86_URL,
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=30, retries=3, wait=10,
    )
    if r is None:
        return None
    if '查詢無資料' in r.text:
        empty = pd.DataFrame()
        _T86_CACHE[date_str] = (now, empty)
        return empty

    df = parse_t86(r.text)
    if not df.empty:
        _T86_CACHE[date_str] = (now, df)
    return df
