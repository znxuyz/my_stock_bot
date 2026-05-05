"""
screen_records：篩選結果寫入、依日期/歷史查詢。
T+1 撮合與結算寫入請看 db.settle。
"""
import logging
from psycopg2.extras import RealDictCursor

from db.conn import get_conn
from db.settle import calc_position_pct, next_friday
from entry_zone import calc_entry_zone

logger = logging.getLogger(__name__)


def save_screen_records(records, screen_date, guild_id):
    """
    寫入篩選結果。chase_mode + grade 決定 entry_zone（見 entry_zone.py）。
    fill_status 初始：strong_chase / normal → 'pending'；watch → 'watch'（不撮合）。

    冪等：寫入前先 DELETE 同 (screen_date, guild_id) 的 pending 紀錄。
    """
    settle1 = next_friday(screen_date, 1)
    settle2 = next_friday(screen_date, 2)
    rows = []
    for e in records:
        b      = e.get('bias') or {}
        grade  = e.get('grade', '')
        pos    = calc_position_pct(grade, b.get('bias_pct'))
        close  = float(e.get('price', 0))
        mode   = e.get('chase_mode', 'normal')
        consec = int(e.get('consec_limit_up', 0))

        zone_low, zone_high = calc_entry_zone(close, mode, grade=grade, precision=2)
        init_fill = 'watch' if mode == 'watch' else 'pending'

        rows.append((
            guild_id, screen_date,
            e['sid'], e.get('name', ''), e.get('grade', ''),
            int(e.get('score', 0)),
            close, e.get('change', 0), e.get('vol_ratio', 0),
            e.get('foreign', 0), e.get('trust', 0),
            b.get('bias_pct'), b.get('bias_label', ''),
            pos, mode, consec,
            zone_low, zone_high, init_fill,
            settle1, settle2,
        ))

    sql = """
    INSERT INTO screen_records
      (guild_id, screen_date, sid, name, grade, score, close_price,
       change_pct, vol_ratio, foreign_shares, trust_shares,
       bias_pct, bias_label, position_pct, chase_mode, consec_limit_up,
       entry_zone_low, entry_zone_high, fill_status,
       settle1_date, settle2_date)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    del_sql = """
    DELETE FROM screen_records
    WHERE guild_id = %s AND screen_date = %s AND fill_status = 'pending'
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(del_sql, (guild_id, screen_date))
            cur.executemany(sql, rows)
        conn.commit()
    logger.info('[DB] 寫入 %d 筆篩選記錄（%s guild:%s）', len(rows), screen_date, guild_id)


def get_records_needing_t1_check(before_date):
    """撈 fill_status='pending' 且 screen_date < before_date 的紀錄。watch 不會被撈出。"""
    sql = """
    SELECT id, guild_id, screen_date, sid, name, close_price,
           entry_zone_low, entry_zone_high, chase_mode
    FROM screen_records
    WHERE fill_status = 'pending' AND screen_date < %s
    ORDER BY screen_date, sid
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (before_date,))
            return cur.fetchall()


def get_total_screened(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM screen_records WHERE guild_id = %s',
                (guild_id,),
            )
            return cur.fetchone()[0]
