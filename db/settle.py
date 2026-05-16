"""
T+1 撮合 + 結算寫入。
v6.2：T+1 純市價成交（一字漲跌停 missed）。
ATR 停損 / 移動停利 / 時間停損 / OVERHEAT 出場留待 Phase 2。
"""
import warnings
from datetime import timedelta

from psycopg2.extras import RealDictCursor

from db.conn import get_conn


def next_friday(from_date, n=1):
    """從 from_date 往後找第 n 個週五。"""
    d, cnt = from_date, 0
    while True:
        d += timedelta(days=1)
        if d.weekday() == 4:
            cnt += 1
            if cnt == n:
                return d


def calc_position_pct(grade, bias_pct):  # noqa: ARG001
    """v5 等級制倉位。v6.2 起 @deprecated，新程式改用 status_to_position_pct。

    保留簽名給少數仍引用的舊測試 / 文件用，永遠回 0.0。
    """
    warnings.warn(
        'db.settle.calc_position_pct is deprecated since v6.2; '
        'use scoring.status_to_position_pct instead.',
        DeprecationWarning,
        stacklevel=2,
    )
    return 0.0


def determine_t1_fill(t1_open, t1_high, t1_low,
                      zone_low=None, zone_high=None,  # noqa: ARG001  (v5 簽名相容)
                      allow_gap_down=True,            # noqa: ARG001
                      **kwargs):                       # noqa: ARG001
    """v6.2：T+1 純市價成交。

      - t1_open is None              → missed
      - 一字漲停 / 跌停（high == low == open）→ missed
      - 其他                         → ('filled', round(open, 2))

    舊的 zone_low/zone_high/allow_gap_down 參數保留簽名但忽略，方便逐步移除呼叫端。
    """
    if t1_open is None:
        return 'missed', None
    o = float(t1_open)
    if t1_high is not None and t1_low is not None:
        hi = float(t1_high)
        lo = float(t1_low)
        # 一字（漲停或跌停開盤即鎖死）
        if abs(hi - o) < 1e-9 and abs(lo - o) < 1e-9:
            return 'missed', None
    return 'filled', round(o, 2)


def fill_t1_entry(record_id, t1_date, status, entry_price, t1_open=None):
    """寫入 T+1 撮合結果。

    filled：寫入 actual_entry_price + 三個目標停損（× 1.05 / 1.10 / 0.95；
            Phase 2 起改為 ATR-based，這裡先沿用固定倍率作為佔位）。
    missed：只寫日期 + fill_status。
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
    """撈 settle1_date = settle_date 且 fill_status='missed' 且尚未補算者。"""
    sql = """
    SELECT id, sid, name, screen_date, actual_entry_date,
           t1_open_price, close_price, settle1_date, total_score, status
    FROM screen_records
    WHERE guild_id = %s AND settle1_date = %s AND fill_status = 'missed'
      AND missed_settle1_pct IS NULL AND t1_open_price IS NOT NULL
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (guild_id, settle_date))
            return cur.fetchall()


def update_missed_hypothetical(record_id, settle_close, settle_pct):
    """寫入 missed 紀錄的「假設有買到」結算結果。"""
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
