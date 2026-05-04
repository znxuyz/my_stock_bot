"""
持倉 / 交易 / 累積損益。FIFO 賣出計算實現損益。
"""
from psycopg2.extras import RealDictCursor

from db.conn import get_conn


def get_holdings(guild_id, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM holdings
                WHERE guild_id = %s AND user_id = %s
                ORDER BY buy_date
                """,
                (guild_id, user_id),
            )
            return cur.fetchall()


def add_holding(guild_id, user_id, sid, price, shares, buy_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO holdings (guild_id, user_id, sid, price, shares, buy_date)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (guild_id, user_id, sid, price, shares, buy_date),
            )
            cur.execute(
                """
                INSERT INTO trades (guild_id, user_id, sid, action, price, shares, pnl, trade_date)
                VALUES (%s,%s,%s,'buy',%s,%s,0,%s)
                """,
                (guild_id, user_id, sid, price, shares, buy_date),
            )
        conn.commit()


def remove_holding(guild_id, user_id, sid, sell_price, sell_shares):
    """FIFO 賣出，回傳 (realized_pnl, error_msg)。失敗時 realized_pnl=None。"""
    holdings = get_holdings(guild_id, user_id)
    owned    = [h for h in holdings if h['sid'] == sid]
    if not owned:
        return None, '你的持倉中沒有此股票'

    remaining_shares = sell_shares
    realized_pnl     = 0.0
    to_delete        = []
    to_update        = []

    for h in owned:
        if remaining_shares <= 0:
            break
        take = min(remaining_shares, h['shares'])
        realized_pnl     += (sell_price - float(h['price'])) * take
        remaining_shares -= take
        if h['shares'] == take:
            to_delete.append(h['id'])
        else:
            to_update.append((h['shares'] - take, h['id']))

    if remaining_shares > 0:
        return None, f'持倉不足，最多可賣 {sell_shares - remaining_shares} 股'

    with get_conn() as conn:
        with conn.cursor() as cur:
            for hid in to_delete:
                cur.execute('DELETE FROM holdings WHERE id = %s', (hid,))
            for new_shares, hid in to_update:
                cur.execute('UPDATE holdings SET shares = %s WHERE id = %s', (new_shares, hid))
            cur.execute(
                """
                INSERT INTO trades (guild_id, user_id, sid, action, price, shares, pnl, trade_date)
                VALUES (%s,%s,%s,'sell',%s,%s,%s,NOW()::date)
                """,
                (guild_id, user_id, sid, sell_price, sell_shares, realized_pnl),
            )
            cur.execute(
                """
                INSERT INTO pnl_summary (guild_id, user_id, total_pnl)
                VALUES (%s,%s,%s)
                ON CONFLICT (guild_id, user_id) DO UPDATE
                    SET total_pnl  = pnl_summary.total_pnl + EXCLUDED.total_pnl,
                        updated_at = NOW()
                """,
                (guild_id, user_id, realized_pnl),
            )
        conn.commit()
    return realized_pnl, None


def get_pnl(guild_id, user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT total_pnl FROM pnl_summary
                WHERE guild_id = %s AND user_id = %s
                """,
                (guild_id, user_id),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0


def get_leaderboard(guild_id, limit=10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT user_id, total_pnl FROM pnl_summary
                WHERE guild_id = %s
                ORDER BY total_pnl DESC LIMIT %s
                """,
                (guild_id, limit),
            )
            return cur.fetchall()
