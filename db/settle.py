"""
T+1 撮合 + 結算寫入。
calc_position_pct / next_friday 也放這裡（給 screens 用）。
"""
from datetime import timedelta

from psycopg2.extras import RealDictCursor

from db.conn import get_conn


def next_friday(from_date, n=1):
    """從 from_date 往後找第 n 個週五"""
    d, cnt = from_date, 0
    while True:
        d += timedelta(days=1)
        if d.weekday() == 4:
            cnt += 1
            if cnt == n:
                return d


def calc_position_pct(grade, bias_pct):
    """各等級在不同乖離率區間建議的倉位百分比。"""
    if grade == 'SS':
        if bias_pct is None or bias_pct <= 5: return 25.0
        if bias_pct <= 8:                      return 15.0
        return 0.0
    if grade == 'S':
        if bias_pct is None or bias_pct <= 5: return 15.0
        if bias_pct <= 8:                      return 10.0
        return 0.0
    if grade in ('A', 'X'):
        if bias_pct is None or bias_pct <= 5: return 10.0
        if bias_pct <= 8:                      return 5.0
        return 0.0
    return 0.0


def determine_t1_fill(t1_open, t1_high, t1_low, zone_low, zone_high, allow_gap_down=True):
    """
    根據 T+1 K 棒 + 進場區間判定撮合結果。
    回傳 (status, fill_price)：('filled', price) 或 ('missed', None)
      A. 開盤在區間內       → 開盤價成交
      B. 開盤跳空高於區間   → 若盤中 low ≤ zone_high 以 zone_high 成交，否則 missed
      C. 開盤跳空低於區間   → allow_gap_down=True 用開盤價撿便宜；False 直接 missed
    """
    if zone_low is None or zone_high is None or t1_open is None:
        return 'missed', None
    o  = float(t1_open)
    lo = float(t1_low) if t1_low is not None else o
    zl = float(zone_low)
    zh = float(zone_high)
    if zl <= o <= zh:
        return 'filled', round(o, 2)
    if o > zh:
        if lo <= zh:
            return 'filled', round(zh, 2)
        return 'missed', None
    return ('filled', round(o, 2)) if allow_gap_down else ('missed', None)


def fill_t1_entry(record_id, t1_date, status, entry_price, t1_open=None):
    """
    寫入 T+1 撮合結果。
      filled：寫入 actual_entry_price + 三個目標停損（× 1.05/1.10/0.95）
      missed：只寫日期 + fill_status
    無論 filled / missed，t1_open_price 都會寫入（給「missed 假設有買到」分析用）。
    若呼叫端沒傳 t1_open，會回退用 entry_price（filled 場景）。
    """
    open_price = float(t1_open) if t1_open is not None else (
        float(entry_price) if entry_price is not None else None
    )
    if status == 'filled' and entry_price is not None:
        e = float(entry_price)
        sql = """
        UPDATE screen_records SET
            actual_entry_date  = %s,
            actual_entry_price = %s,
            actual_target1     = %s,
            actual_target2     = %s,
            actual_stop_loss   = %s,
            t1_open_price      = %s,
            fill_status        = 'filled'
        WHERE id = %s
        """
        params = (t1_date, e, round(e * 1.05, 2), round(e * 1.10, 2),
                  round(e * 0.95, 2), open_price, record_id)
    else:
        sql = """
        UPDATE screen_records SET
            actual_entry_date = %s,
            t1_open_price     = %s,
            fill_status       = 'missed'
        WHERE id = %s
        """
        params = (t1_date, open_price, record_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def get_missed_for_hypothetical(settle_date, guild_id):
    """
    撈出 settle1_date = settle_date 且 fill_status = 'missed'
    且還沒算過假設結算（missed_settle1_pct IS NULL）的紀錄。
    給 settle_weekly 補算「如果有買到的話會賺多少」用。
    """
    sql = """
    SELECT id, sid, name, grade, screen_date, actual_entry_date,
           t1_open_price, close_price, settle1_date, position_pct
    FROM screen_records
    WHERE guild_id = %s AND settle1_date = %s AND fill_status = 'missed'
      AND missed_settle1_pct IS NULL AND t1_open_price IS NOT NULL
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (guild_id, settle_date))
            return cur.fetchall()


def update_missed_hypothetical(record_id, settle_close, settle_pct):
    """
    寫入 missed 紀錄的「假設有買到」結算結果。
    不動 fill_status / settle1_done（避免影響真實勝率統計）。
    """
    sql = """
    UPDATE screen_records SET
        missed_settle1_close = %s,
        missed_settle1_pct   = %s
    WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settle_close, settle_pct, record_id))
        conn.commit()


def get_pending_settle(settle_date, round_num, guild_id):
    """撈出指定 settle_date 待結算且已成交的紀錄。"""
    col_done = 'settle1_done' if round_num == 1 else 'settle2_done'
    col_date = 'settle1_date' if round_num == 1 else 'settle2_date'
    sql = f"""
    SELECT * FROM screen_records
    WHERE guild_id = %s AND {col_date} = %s AND {col_done} = FALSE
      AND fill_status = 'filled' AND actual_entry_price IS NOT NULL
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (guild_id, settle_date))
            return cur.fetchall()


def update_settle(record_id, round_num, settle_close,
                  hit_target1=None, hit_target2=None, hit_stoploss=None,
                  hit_target1_date=None, hit_target2_date=None, hit_stoploss_date=None,
                  settle_pct=None):
    """寫入結算結果。settle_pct 由呼叫端傳入（已考慮停損強制 -5%）。"""
    if round_num == 1:
        sql = """
        UPDATE screen_records SET
            settle1_price = %s, settle1_pct = %s, settle1_done = TRUE,
            hit_target1   = %s, hit_target2  = %s, hit_stoploss = %s,
            hit_target1_date  = COALESCE(hit_target1_date,  %s),
            hit_target2_date  = COALESCE(hit_target2_date,  %s),
            hit_stoploss_date = COALESCE(hit_stoploss_date, %s)
        WHERE id = %s
        """
    else:
        sql = """
        UPDATE screen_records SET
            settle2_price = %s, settle2_pct = %s, settle2_done = TRUE,
            hit_target1   = %s, hit_target2  = %s, hit_stoploss = %s,
            hit_target1_date  = COALESCE(hit_target1_date,  %s),
            hit_target2_date  = COALESCE(hit_target2_date,  %s),
            hit_stoploss_date = COALESCE(hit_stoploss_date, %s)
        WHERE id = %s
        """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (settle_close, settle_pct,
                 hit_target1, hit_target2, hit_stoploss,
                 hit_target1_date, hit_target2_date, hit_stoploss_date,
                 record_id),
            )
        conn.commit()
