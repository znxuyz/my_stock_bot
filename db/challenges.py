"""
選股挑戰：每位用戶每週一檔。週五自動結算清零。
"""
from psycopg2.extras import RealDictCursor

from db.conn import get_conn


def get_challenge(guild_id, user_id, week_key):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM challenges
                WHERE guild_id = %s AND user_id = %s AND week_key = %s
                """,
                (guild_id, user_id, week_key),
            )
            return cur.fetchone()


def add_challenge(guild_id, user_id, week_key, sid, start_price, end_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO challenges (guild_id, user_id, week_key, sid, start_price, end_date)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (guild_id, user_id, week_key) DO NOTHING
                """,
                (guild_id, user_id, week_key, sid, start_price, end_date),
            )
        conn.commit()


def get_all_challenges(guild_id, week_key):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM challenges
                WHERE guild_id = %s AND week_key = %s
                """,
                (guild_id, week_key),
            )
            return cur.fetchall()


def clear_challenges(guild_id, week_key):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM challenges
                WHERE guild_id = %s AND week_key = %s
                """,
                (guild_id, week_key),
            )
        conn.commit()
