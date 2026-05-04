"""
guild_settings：每個 Discord 伺服器的推播 webhook 設定。
"""
from psycopg2.extras import RealDictCursor

from db.conn import get_conn


def set_guild_webhook(guild_id, webhook_url, setup_by):
    sql = """
    INSERT INTO guild_settings (guild_id, webhook_url, setup_by)
    VALUES (%s, %s, %s)
    ON CONFLICT (guild_id) DO UPDATE
        SET webhook_url = EXCLUDED.webhook_url,
            setup_by    = EXCLUDED.setup_by,
            created_at  = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (guild_id, webhook_url, setup_by))
        conn.commit()


def remove_guild(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM guild_settings WHERE guild_id = %s', (guild_id,))
        conn.commit()


def get_all_webhooks():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT guild_id, webhook_url FROM guild_settings')
            return cur.fetchall()


def get_guild_webhook(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT webhook_url FROM guild_settings WHERE guild_id = %s',
                (guild_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
