"""daily_scores 表的存取（v6.2）。

每天 17:00 對所有通過 enrich 的 candidate（不只 SETUP+）寫入一筆，
給 Phase 2 的「5 日分數斜率」當主排序鍵用。
"""
import logging

from psycopg2.extras import RealDictCursor

from db.conn import get_conn

logger = logging.getLogger(__name__)


def save_daily_score(sid, date, flow, trend, heat, total, status):
    """寫入 daily_scores（UPSERT by (sid, date)）。"""
    sql = """
    INSERT INTO daily_scores (sid, date, flow_score, trend_score,
                              heat_score, total_score, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (sid, date) DO UPDATE SET
        flow_score  = EXCLUDED.flow_score,
        trend_score = EXCLUDED.trend_score,
        heat_score  = EXCLUDED.heat_score,
        total_score = EXCLUDED.total_score,
        status      = EXCLUDED.status
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (sid, date, int(flow), int(trend), int(heat),
                              int(total), status))
        conn.commit()


def fetch_recent_scores(sid, days=5, end_date=None):
    """從 daily_scores 撈 sid 最近 N 日分數，回 list[dict] 由舊到新。

    end_date 為 date 物件；None 時 SQL 用 CURRENT_DATE。
    """
    if end_date is None:
        sql = """
        SELECT date, flow_score, trend_score, heat_score, total_score, status
        FROM daily_scores
        WHERE sid = %s AND date <= CURRENT_DATE
        ORDER BY date DESC LIMIT %s
        """
        params = (sid, days)
    else:
        sql = """
        SELECT date, flow_score, trend_score, heat_score, total_score, status
        FROM daily_scores
        WHERE sid = %s AND date <= %s
        ORDER BY date DESC LIMIT %s
        """
        params = (sid, end_date, days)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    # rows 由新到舊；反轉成由舊到新（給 velocity 計算用）
    return list(reversed(rows))
