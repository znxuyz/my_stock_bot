"""
資料庫模組 db.py ── 多伺服器版
================================
所有涉及用戶資料的操作都以 (guild_id, user_id) 為 key
分析結果、結算報告以 guild_id 為 key
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta

def get_conn():
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise Exception('DATABASE_URL 環境變數未設定')
    # psycopg2 需要 postgresql:// 而非 postgres://
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url)

def init_db():
    ddl = """
    -- 伺服器設定
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id    VARCHAR(30) PRIMARY KEY,
        webhook_url TEXT NOT NULL,
        setup_by    VARCHAR(30),
        created_at  TIMESTAMP DEFAULT NOW()
    );

    -- 每日篩選記錄（含 guild_id，每個伺服器各自結算）
    CREATE TABLE IF NOT EXISTS screen_records (
        id              SERIAL PRIMARY KEY,
        guild_id        VARCHAR(30) NOT NULL,
        screen_date     DATE NOT NULL,
        sid             VARCHAR(10) NOT NULL,
        name            VARCHAR(50),
        grade           VARCHAR(5),
        close_price     NUMERIC(12,2),
        change_pct      NUMERIC(8,2),
        vol_ratio       NUMERIC(8,2),
        foreign_shares  BIGINT,
        trust_shares    BIGINT,
        bias_pct        NUMERIC(8,2),
        bias_label      VARCHAR(20),
        entry_price     NUMERIC(12,2),
        target1         NUMERIC(12,2),
        target2         NUMERIC(12,2),
        stop_loss       NUMERIC(12,2),
        position_pct    NUMERIC(5,1),
        settle1_date    DATE,
        settle2_date    DATE,
        settle1_price   NUMERIC(12,2),
        settle2_price   NUMERIC(12,2),
        settle1_pct     NUMERIC(8,2),
        settle2_pct     NUMERIC(8,2),
        settle1_done    BOOLEAN DEFAULT FALSE,
        settle2_done    BOOLEAN DEFAULT FALSE,
        hit_target1     BOOLEAN,
        hit_target2     BOOLEAN,
        hit_stoploss    BOOLEAN,
        created_at      TIMESTAMP DEFAULT NOW()
    );

    -- 持倉記錄（guild_id + user_id 隔離）
    CREATE TABLE IF NOT EXISTS holdings (
        id          SERIAL PRIMARY KEY,
        guild_id    VARCHAR(30) NOT NULL,
        user_id     VARCHAR(30) NOT NULL,
        sid         VARCHAR(10) NOT NULL,
        price       NUMERIC(12,2) NOT NULL,
        shares      BIGINT NOT NULL,
        buy_date    DATE NOT NULL,
        created_at  TIMESTAMP DEFAULT NOW()
    );

    -- 交易記錄（guild_id + user_id 隔離）
    CREATE TABLE IF NOT EXISTS trades (
        id          SERIAL PRIMARY KEY,
        guild_id    VARCHAR(30) NOT NULL,
        user_id     VARCHAR(30) NOT NULL,
        sid         VARCHAR(10) NOT NULL,
        action      VARCHAR(5) NOT NULL,
        price       NUMERIC(12,2) NOT NULL,
        shares      BIGINT NOT NULL,
        pnl         NUMERIC(14,2) DEFAULT 0,
        trade_date  DATE NOT NULL,
        created_at  TIMESTAMP DEFAULT NOW()
    );

    -- 損益累計（guild_id + user_id 隔離）
    CREATE TABLE IF NOT EXISTS pnl_summary (
        guild_id    VARCHAR(30) NOT NULL,
        user_id     VARCHAR(30) NOT NULL,
        total_pnl   NUMERIC(14,2) DEFAULT 0,
        updated_at  TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (guild_id, user_id)
    );

    -- 選股挑戰（guild_id 隔離）
    CREATE TABLE IF NOT EXISTS challenges (
        id          SERIAL PRIMARY KEY,
        guild_id    VARCHAR(30) NOT NULL,
        user_id     VARCHAR(30) NOT NULL,
        week_key    VARCHAR(20) NOT NULL,
        sid         VARCHAR(10) NOT NULL,
        start_price NUMERIC(12,2),
        end_date    DATE,
        created_at  TIMESTAMP DEFAULT NOW(),
        UNIQUE(guild_id, user_id, week_key)
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    print("[DB] 資料表初始化完成")

# ══════════════════════════════════════════════════
# 伺服器設定
# ══════════════════════════════════════════════════
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
            cur.execute("DELETE FROM guild_settings WHERE guild_id = %s", (guild_id,))
        conn.commit()

def get_all_webhooks():
    """取得所有已設定伺服器的 webhook"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT guild_id, webhook_url FROM guild_settings")
            return cur.fetchall()

def get_guild_webhook(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT webhook_url FROM guild_settings WHERE guild_id = %s", (guild_id,))
            row = cur.fetchone()
            return row[0] if row else None

# ══════════════════════════════════════════════════
# 篩選記錄
# ══════════════════════════════════════════════════
def next_friday(from_date, n=1):
    d, cnt = from_date, 0
    while True:
        d += timedelta(days=1)
        if d.weekday() == 4:
            cnt += 1
            if cnt == n:
                return d

def calc_position_pct(grade, bias_pct):
    if grade == 'SS':
        if bias_pct is None or bias_pct <= 5: return 25.0
        elif bias_pct <= 8:                    return 15.0
        else:                                  return 0.0
    elif grade == 'S':
        if bias_pct is None or bias_pct <= 5: return 15.0
        elif bias_pct <= 8:                    return 10.0
        else:                                  return 0.0
    elif grade in ('A', 'X'):
        if bias_pct is None or bias_pct <= 5: return 10.0
        elif bias_pct <= 8:                    return 5.0
        else:                                  return 0.0
    return 0.0

def save_screen_records(records, screen_date, guild_id):
    settle1 = next_friday(screen_date, 1)
    settle2 = next_friday(screen_date, 2)
    rows = []
    for e in records:
        b   = e.get('bias') or {}
        pos = calc_position_pct(e.get('grade',''), b.get('bias_pct'))
        rows.append((
            guild_id, screen_date,
            e['sid'], e.get('name',''), e.get('grade',''),
            e.get('price',0), e.get('change',0), e.get('vol_ratio',0),
            e.get('foreign',0), e.get('trust',0),
            b.get('bias_pct'), b.get('bias_label',''),
            b.get('entry_price'), b.get('target1'),
            b.get('target2'), b.get('stop_loss'),
            pos, settle1, settle2,
        ))
    sql = """
    INSERT INTO screen_records
      (guild_id, screen_date, sid, name, grade, close_price,
       change_pct, vol_ratio, foreign_shares, trust_shares,
       bias_pct, bias_label, entry_price, target1, target2,
       stop_loss, position_pct, settle1_date, settle2_date)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    print(f"[DB] 寫入 {len(rows)} 筆篩選記錄（{screen_date} guild:{guild_id}）")

def get_pending_settle(settle_date, round_num, guild_id):
    col_done = 'settle1_done' if round_num == 1 else 'settle2_done'
    col_date = 'settle1_date' if round_num == 1 else 'settle2_date'
    sql = f"""
    SELECT * FROM screen_records
    WHERE guild_id = %s AND {col_date} = %s AND {col_done} = FALSE
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (guild_id, settle_date))
            return cur.fetchall()

def update_settle(record_id, round_num, settle_price,
                  hit_target1=None, hit_target2=None, hit_stoploss=None):
    if round_num == 1:
        sql = """
        UPDATE screen_records SET
            settle1_price = %s,
            settle1_pct   = ROUND(((%s - close_price)/close_price*100)::numeric,2),
            settle1_done  = TRUE,
            hit_target1   = %s, hit_target2 = %s, hit_stoploss = %s
        WHERE id = %s
        """
    else:
        sql = """
        UPDATE screen_records SET
            settle2_price = %s,
            settle2_pct   = ROUND(((%s - close_price)/close_price*100)::numeric,2),
            settle2_done  = TRUE,
            hit_target1   = %s, hit_target2 = %s, hit_stoploss = %s
        WHERE id = %s
        """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settle_price, settle_price,
                              hit_target1, hit_target2, hit_stoploss, record_id))
        conn.commit()

def get_cumulative_stats(guild_id):
    grade_sql = """
    SELECT grade,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        AVG(settle1_pct) AS avg_ret1,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle2_pct) AS avg_ret2
    FROM screen_records
    WHERE guild_id = %s AND settle1_done = TRUE
    GROUP BY grade
    ORDER BY CASE grade WHEN 'SS' THEN 1 WHEN 'S' THEN 2
                        WHEN 'A' THEN 3 WHEN 'X' THEN 4 END
    """
    bias_sql = """
    SELECT
        CASE WHEN bias_pct <= 5 THEN '理想(0-5%)'
             WHEN bias_pct <= 8 THEN '略高(5-8%)'
             WHEN bias_pct > 8  THEN '過高(>8%)'
             ELSE '底部(<0%)' END AS bias_zone,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM screen_records
    WHERE guild_id = %s AND settle1_done = TRUE AND bias_pct IS NOT NULL
    GROUP BY bias_zone
    """
    dual_sql = """
    SELECT
        CASE WHEN foreign_shares >= 10000 AND trust_shares >= 10000
             THEN '雙買超' ELSE '單方買超' END AS buy_type,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM screen_records
    WHERE guild_id = %s AND settle1_done = TRUE
    GROUP BY buy_type
    """
    grade_rows, bias_rows, dual_rows = [], [], []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(grade_sql, (guild_id,))
                grade_rows = list(cur.fetchall())
                cur.execute(bias_sql, (guild_id,))
                bias_rows = list(cur.fetchall())
                cur.execute(dual_sql, (guild_id,))
                dual_rows = list(cur.fetchall())
        conn.close()
    except Exception as e:
        import traceback
        print(f"[DB] get_cumulative_stats 錯誤：{e}\n{traceback.format_exc()}")
    return grade_rows, bias_rows, dual_rows

def get_total_screened(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM screen_records WHERE guild_id = %s", (guild_id,))
            return cur.fetchone()[0]

# ══════════════════════════════════════════════════
# 持倉（guild 隔離）
# ══════════════════════════════════════════════════
def get_holdings(guild_id, user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM holdings
                WHERE guild_id = %s AND user_id = %s
                ORDER BY buy_date
            """, (guild_id, user_id))
            return cur.fetchall()

def add_holding(guild_id, user_id, sid, price, shares, buy_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO holdings (guild_id, user_id, sid, price, shares, buy_date)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (guild_id, user_id, sid, price, shares, buy_date))
            cur.execute("""
                INSERT INTO trades (guild_id, user_id, sid, action, price, shares, pnl, trade_date)
                VALUES (%s,%s,%s,'buy',%s,%s,0,%s)
            """, (guild_id, user_id, sid, price, shares, buy_date))
        conn.commit()

def remove_holding(guild_id, user_id, sid, sell_price, sell_shares):
    """FIFO 賣出，回傳 realized_pnl"""
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
        realized_pnl    += (sell_price - float(h['price'])) * take
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
                cur.execute("DELETE FROM holdings WHERE id = %s", (hid,))
            for new_shares, hid in to_update:
                cur.execute("UPDATE holdings SET shares = %s WHERE id = %s", (new_shares, hid))
            cur.execute("""
                INSERT INTO trades (guild_id, user_id, sid, action, price, shares, pnl, trade_date)
                VALUES (%s,%s,%s,'sell',%s,%s,%s,NOW()::date)
            """, (guild_id, user_id, sid, sell_price, sell_shares, realized_pnl))
            cur.execute("""
                INSERT INTO pnl_summary (guild_id, user_id, total_pnl)
                VALUES (%s,%s,%s)
                ON CONFLICT (guild_id, user_id) DO UPDATE
                    SET total_pnl  = pnl_summary.total_pnl + EXCLUDED.total_pnl,
                        updated_at = NOW()
            """, (guild_id, user_id, realized_pnl))
        conn.commit()
    return realized_pnl, None

def get_pnl(guild_id, user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT total_pnl FROM pnl_summary
                WHERE guild_id = %s AND user_id = %s
            """, (guild_id, user_id))
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

def get_leaderboard(guild_id, limit=10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, total_pnl FROM pnl_summary
                WHERE guild_id = %s
                ORDER BY total_pnl DESC LIMIT %s
            """, (guild_id, limit))
            return cur.fetchall()

# ══════════════════════════════════════════════════
# 選股挑戰（guild 隔離）
# ══════════════════════════════════════════════════
def get_challenge(guild_id, user_id, week_key):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM challenges
                WHERE guild_id = %s AND user_id = %s AND week_key = %s
            """, (guild_id, user_id, week_key))
            return cur.fetchone()

def add_challenge(guild_id, user_id, week_key, sid, start_price, end_date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO challenges (guild_id, user_id, week_key, sid, start_price, end_date)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (guild_id, user_id, week_key) DO NOTHING
            """, (guild_id, user_id, week_key, sid, start_price, end_date))
        conn.commit()

def get_all_challenges(guild_id, week_key):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM challenges
                WHERE guild_id = %s AND week_key = %s
            """, (guild_id, week_key))
            return cur.fetchall()

def clear_challenges(guild_id, week_key):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM challenges
                WHERE guild_id = %s AND week_key = %s
            """, (guild_id, week_key))
        conn.commit()