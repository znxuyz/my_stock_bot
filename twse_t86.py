"""
TWSE T86 三大法人買賣超：抓取 + 解析 + 30 分鐘共享快取。
v6.2 起新增多日抓取 + 歷史寫入 + sid 法人歷史查詢。
"""
import logging
import time
from datetime import datetime, timedelta

import pandas as pd

import config
from twse_http import clean_sid, safe_get, safe_read_csv

logger = logging.getLogger(__name__)

_T86_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86'

# date_str → (timestamp, parsed_df)
_T86_CACHE = {}


def parse_t86(text):
    """T86 CSV → DataFrame（含 sid_clean / _foreign / _trust / _total / _dealer）"""
    logger.debug('[T86] 前300字：%s', text[:300])
    lines = text.splitlines()

    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
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

    # 固定欄位索引：4=外資, 10=投信, 11~17=自營（合計通常 17）, 18=三大法人合計
    if n >= 19:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        # 自營商淨買賣（合計）：欄位 17（自營商買賣超合計）；備援用合計減去外資+投信
        try:
            df['_dealer'] = pd.to_numeric(df.iloc[:, 17], errors='coerce').fillna(0)
        except Exception:
            df['_dealer'] = 0
        df['_total']   = pd.to_numeric(df.iloc[:, 18], errors='coerce').fillna(0)
        logger.debug('[T86] 標準19欄格式（外資idx=4 投信idx=10 自營idx=17 合計idx=18）')
    elif n >= 11:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_dealer']  = 0
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
    if df.empty:
        logger.warning(
            '[T86] %s 抓回的內容無法解析（既非假日也非有效 T86），視為抓取失敗',
            date_str,
        )
        return None
    _T86_CACHE[date_str] = (now, df)
    return df


# ─────────── v6.2 新增 ───────────

def _prev_trading_days(date_str, n):
    """從 date_str（YYYYMMDD，含當日）往回找 n 個交易日候選。
    這層不打 TWSE，只用工作日（Mon-Fri）粗略推算；實際命中與否由 fetch_t86_cached 判定假日。
    回傳 ['YYYYMMDD', ...] 由舊到新。
    """
    base = datetime.strptime(date_str, '%Y%m%d').date()
    out = []
    cur = base
    while len(out) < n:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur.strftime('%Y%m%d'))
        cur -= timedelta(days=1)
    return list(reversed(out))


def fetch_t86_multi_day(date_str, days=None):
    """抓 date_str 為基準的近 N 個交易日 T86。

    回傳 dict[YYYYMMDD] = parsed_df（只放成功且非空的）。
    """
    if days is None:
        days = config.STALKER_DAYS
    # 多抓一點工作日 candidates 以容錯假日
    candidates = _prev_trading_days(date_str, days + 3)
    result = {}
    for d in candidates:
        df = fetch_t86_cached(d)
        if df is not None and not df.empty:
            result[d] = df
            if len(result) >= days:
                break
        time.sleep(config.TWSE_CALL_INTERVAL_SEC)
    return result


def save_t86_to_history(date_str, df_t86):
    """寫進 daily_t86_history（UPSERT）。空 df 直接 noop。"""
    from db.conn import get_conn
    if df_t86 is None or df_t86.empty:
        return 0
    d = datetime.strptime(date_str, '%Y%m%d').date()
    rows = []
    for r in df_t86.to_dict('records'):
        sid = str(r.get('sid_clean', '')).strip()
        if not sid:
            continue
        rows.append((
            sid, d,
            int(r.get('_foreign', 0) or 0),
            int(r.get('_trust', 0) or 0),
            int(r.get('_dealer', 0) or 0),
        ))
    if not rows:
        return 0
    sql = """
    INSERT INTO daily_t86_history (sid, date, foreign_net, trust_net, dealer_net)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (sid, date) DO UPDATE SET
        foreign_net = EXCLUDED.foreign_net,
        trust_net   = EXCLUDED.trust_net,
        dealer_net  = EXCLUDED.dealer_net
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    logger.info('[T86 history] 寫入 %d 筆（%s）', len(rows), date_str)
    return len(rows)


def get_inst_history(sid, days=None, end_date=None):
    """從 daily_t86_history 撈 N 日法人淨買（外資 + 投信），回 list[(date, net)] 由舊到新。

    end_date 為 date 物件；None 視為今天。
    """
    from datetime import date as _date_cls
    from db.conn import get_conn
    if days is None:
        days = config.STALKER_DAYS
    end = end_date or _date_cls.today()
    sql = """
    SELECT date, COALESCE(foreign_net, 0) + COALESCE(trust_net, 0) AS net
    FROM daily_t86_history
    WHERE sid = %s AND date <= %s
    ORDER BY date DESC LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (sid, end, days))
            rows = cur.fetchall()
    # rows 由新到舊；反轉成由舊到新
    return [(r[0], int(r[1] or 0)) for r in reversed(rows)]
