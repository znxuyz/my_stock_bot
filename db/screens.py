"""
screen_records 寫入（v6.2）。

寫入欄位對應新 schema：flow/trend/heat/total/status/acc_*/cum_5d_pct/bias_20/vol_vs_60d ...
冪等：寫入前先 DELETE 同 (screen_date, guild_id) 的 pending 紀錄。
"""
import logging

from psycopg2.extras import RealDictCursor

from db.conn import get_conn
from db.settle import next_friday

logger = logging.getLogger(__name__)


def save_screen_records(records, screen_date, guild_id):
    """寫入篩選結果。v6.2：純市價 → fill_status 一律 'pending'。

    參數 records 是 dict list；對應欄位請看下方 SQL 參數順序。
    """
    settle1 = next_friday(screen_date, 1)
    settle2 = next_friday(screen_date, 2)
    rows = []
    for e in records:
        close  = float(e.get('price', 0))
        rows.append((
            e['sid'], e.get('name', ''), screen_date, guild_id,
            int(e.get('flow_score',  0) or 0),
            int(e.get('trend_score', 0) or 0),
            int(e.get('heat_score',  0) or 0),
            int(e.get('total_score', 0) or 0),
            e.get('status', 'NOISE'),
            int(e.get('acc_buy_days', 0) or 0),
            int(e.get('acc_cum_net',  0) or 0),
            e.get('cum_5d_pct'),
            e.get('bias_20'),
            e.get('vol_vs_60d'),
            close,
            'pending',
            settle1, settle2,
        ))

    sql = """
    INSERT INTO screen_records
      (sid, name, screen_date, guild_id,
       flow_score, trend_score, heat_score, total_score, status,
       acc_buy_days, acc_cum_net,
       cum_5d_pct, bias_20, vol_vs_60d,
       close_price, fill_status,
       settle1_date, settle2_date)
    VALUES (%s,%s,%s,%s,
            %s,%s,%s,%s,%s,
            %s,%s,
            %s,%s,%s,
            %s,%s,
            %s,%s)
    """
    del_sql = """
    DELETE FROM screen_records
    WHERE guild_id = %s AND screen_date = %s AND fill_status = 'pending'
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(del_sql, (guild_id, screen_date))
            if rows:
                cur.executemany(sql, rows)
        conn.commit()
    logger.info('[DB] 寫入 %d 筆篩選記錄（%s guild:%s）',
                len(rows), screen_date, guild_id)


def get_records_needing_t1_check(before_date):
    """撈 fill_status='pending' 且 screen_date < before_date 的紀錄。"""
    sql = """
    SELECT id, guild_id, screen_date, sid, name, close_price
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
