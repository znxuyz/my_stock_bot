"""
TWSE T86 三大法人買賣超：抓取 + 解析 + 30 分鐘共享快取。
"""
import logging
import time

import pandas as pd

import config
from twse_http import clean_sid, safe_get, safe_read_csv

logger = logging.getLogger(__name__)

_T86_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86'

# date_str → (timestamp, parsed_df)
_T86_CACHE = {}


def parse_t86(text):
    """T86 CSV → DataFrame（含 sid_clean / _foreign / _trust / _total）"""
    logger.debug('[T86] 前300字：%s', text[:300])
    lines = text.splitlines()

    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
        # 找不到表頭 ─ 連 log 前 500 字回應內容，方便日後 debug 是 TWSE 改格式
        # 還是限速 / 暫時錯誤頁。注意：caller (fetch_t86_cached) 會把這個情境
        # 視為「parse 失敗」回 None，而不是當成假日。
        logger.warning(
            '[T86] 找不到「證券代號」表頭（可能 TWSE 暫時錯誤或格式變更），'
            '前 500 字回應內容：%r', text[:500],
        )
        return pd.DataFrame()

    logger.debug('[T86] 表頭第 %d 行：%s', header_idx, lines[header_idx][:120])
    df = safe_read_csv('\n'.join(lines[header_idx:]), 'T86', min_cols=11)
    if df.empty:
        return pd.DataFrame()
    logger.info('[T86] 共 %d 欄', len(df.columns))

    df = df[df.iloc[:, 0].astype(str).str.match(r'^[0-9A-Z]{4,6}$', na=False)].copy()
    if df.empty:
        logger.warning('[T86] 過濾後無有效股票列')
        return pd.DataFrame()

    df['sid_clean'] = clean_sid(df.iloc[:, 0])
    n = len(df.columns)

    # 固定欄位索引：4=外資, 10=投信, 18=三大法人合計
    if n >= 19:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = pd.to_numeric(df.iloc[:, 18], errors='coerce').fillna(0)
        logger.debug('[T86] 標準19欄格式（外資idx=4 投信idx=10 合計idx=18）')
    elif n >= 11:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = df['_foreign'] + df['_trust']
        logger.warning('[T86] 備援 %d 欄格式（外資idx=4 投信idx=10）', n)
    else:
        logger.error('[T86] 欄位數不足(%d)，無法解析', n)
        return pd.DataFrame()

    logger.info('[T86] 有效股票：%d 檔，外資非零：%d，投信非零：%d',
                len(df), (df['_foreign'] != 0).sum(), (df['_trust'] != 0).sum())
    return df


def fetch_t86_cached(date_str):
    """
    抓 T86 並 30 分鐘共享快取。
    回傳：
      DataFrame 非空 — 成功
      DataFrame 空    — 真假日（TWSE 明確回「查詢無資料」；快取避免重複探查）
      None           — 抓取失敗 / parse 失敗
                       caller 應視為 fail（通知用戶手動 /run），**絕對不可當假日跳過**
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
        # 真假日 — 快取空 DataFrame 避免同一天反覆探查
        empty = pd.DataFrame()
        _T86_CACHE[date_str] = (now, empty)
        return empty

    df = parse_t86(r.text)
    if df.empty:
        # TWSE 有回應但無法 parse（既不是「查詢無資料」也不是有效 T86）
        # → 不是假日，不要快取，回 None 讓 analysis.py 視為 'fail' 通知用戶
        logger.warning(
            '[T86] %s 抓回的內容無法解析（既非假日也非有效 T86），視為抓取失敗',
            date_str,
        )
        return None
    _T86_CACHE[date_str] = (now, df)
    return df
