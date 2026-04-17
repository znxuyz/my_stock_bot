"""
資料庫模組 db.py
================
負責：
  - 初始化 PostgreSQL 資料表
  - 寫入每日篩選結果
  - 讀取待結算記錄
  - 寫入結算結果
  - 讀取累積統計
"""

import os, psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta

DB_URL = os.environ.get('DATABASE_URL', '')

def get_conn():
    return psycopg2.connect(DB_URL)

def init_db():
    """建立資料表（若不存在）"""
    ddl = """
    CREATE TABLE IF NOT EXISTS screen_records (
        id            SERIAL PRIMARY KEY,
        screen_date   DATE NOT NULL,
        sid           VARCHAR(10) NOT NULL,
        name          VARCHAR(50),
        grade         VARCHAR(5),
        close_price   NUMERIC(12,2),
        change_pct    NUMERIC(8,2),
        vol_ratio     NUMERIC(8,2),
        foreign_shares BIGINT,
        trust_shares   BIGINT,
        bias_pct      NUMERIC(8,2),
        bias_label    VARCHAR(20),
        entry_price   NUMERIC(12,2),
        target1       NUMERIC(12,2),
        target2       NUMERIC(12,2),
        stop_loss     NUMERIC(12,2),
        position_pct  NUMERIC(5,1),
        settle1_date  DATE,
        settle2_date  DATE,
        settle1_price NUMERIC(12,2),
        settle2_price NUMERIC(12,2),
        settle1_pct   NUMERIC(8,2),
        settle2_pct   NUMERIC(8,2),
        settle1_done  BOOLEAN DEFAULT FALSE,
        settle2_done  BOOLEAN DEFAULT FALSE,
        hit_target1   BOOLEAN,
        hit_target2   BOOLEAN,
        hit_stoploss  BOOLEAN,
        created_at    TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS weekly_stats (
        id            SERIAL PRIMARY KEY,
        settle_date   DATE NOT NULL,
        settle_round  INT,
        total_count   INT,
        win_count     INT,
        lose_count    INT,
        flat_count    INT,
        win_rate      NUMERIC(5,2),
        avg_return    NUMERIC(8,2),
        ss_win_rate   NUMERIC(5,2),
        s_win_rate    NUMERIC(5,2),
        a_win_rate    NUMERIC(5,2),
        x_win_rate    NUMERIC(5,2),
        bias_ok_rate  NUMERIC(5,2),
        bias_warn_rate NUMERIC(5,2),
        bias_high_rate NUMERIC(5,2),
        dual_buy_rate NUMERIC(5,2),
        single_buy_rate NUMERIC(5,2),
        anomalies     TEXT,
        suggestions   TEXT,
        created_at    TIMESTAMP DEFAULT NOW()
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    print("[DB] 資料表初始化完成")

def next_friday(from_date, n=1):
    """從 from_date 開始找第 n 個週五"""
    d   = from_date
    cnt = 0
    while True:
        d += timedelta(days=1)
        if d.weekday() == 4:  # 週五
            cnt += 1
            if cnt == n:
                return d

def save_screen_records(records, screen_date):
    """
    寫入每日篩選結果。
    records: list of dict（stock_block 的 entry）
    screen_date: date 物件
    """
    settle1 = next_friday(screen_date, 1)
    settle2 = next_friday(screen_date, 2)

    rows = []
    for e in records:
        b   = e.get('bias') or {}
        pos = calc_position_pct(e.get('grade',''), b.get('bias_pct', 0))
        rows.append((
            screen_date,
            e['sid'], e.get('name',''),
            e.get('grade',''), e.get('price', 0),
            e.get('change', 0), e.get('vol_ratio', 0),
            e.get('foreign', 0), e.get('trust', 0),
            b.get('bias_pct'), b.get('bias_label',''),
            b.get('entry_price'), b.get('target1'),
            b.get('target2'), b.get('stop_loss'),
            pos, settle1, settle2,
        ))

    sql = """
    INSERT INTO screen_records
      (screen_date, sid, name, grade, close_price, change_pct,
       vol_ratio, foreign_shares, trust_shares, bias_pct, bias_label,
       entry_price, target1, target2, stop_loss, position_pct,
       settle1_date, settle2_date)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT DO NOTHING
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    print(f"[DB] 寫入 {len(rows)} 筆篩選記錄（{screen_date}）")

def calc_position_pct(grade, bias_pct):
    """根據等級和乖離率計算建議倉位（%）"""
    if grade == 'SS':
        if bias_pct is None or bias_pct <= 5:   return 25.0
        elif bias_pct <= 8:                       return 15.0
        else:                                     return 0.0
    elif grade == 'S':
        if bias_pct is None or bias_pct <= 5:   return 15.0
        elif bias_pct <= 8:                       return 10.0
        else:                                     return 0.0
    elif grade in ('A', 'X'):
        if bias_pct is None or bias_pct <= 5:   return 10.0
        elif bias_pct <= 8:                       return 5.0
        else:                                     return 0.0
    return 0.0

def get_pending_settle(settle_date, round_num):
    """取得待結算記錄（round_num=1 或 2）"""
    col_done  = 'settle1_done'  if round_num == 1 else 'settle2_done'
    col_date  = 'settle1_date'  if round_num == 1 else 'settle2_date'
    sql = f"""
    SELECT * FROM screen_records
    WHERE {col_date} = %s AND {col_done} = FALSE
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (settle_date,))
            return cur.fetchall()

def update_settle(record_id, round_num, settle_price,
                  hit_target1=None, hit_target2=None, hit_stoploss=None):
    """更新結算結果"""
    if round_num == 1:
        sql = """
        UPDATE screen_records SET
            settle1_price = %s,
            settle1_pct   = ROUND(((%s - close_price) / close_price * 100)::numeric, 2),
            settle1_done  = TRUE,
            hit_target1   = %s,
            hit_target2   = %s,
            hit_stoploss  = %s
        WHERE id = %s
        """
    else:
        sql = """
        UPDATE screen_records SET
            settle2_price = %s,
            settle2_pct   = ROUND(((%s - close_price) / close_price * 100)::numeric, 2),
            settle2_done  = TRUE,
            hit_target1   = %s,
            hit_target2   = %s,
            hit_stoploss  = %s
        WHERE id = %s
        """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settle_price, settle_price,
                              hit_target1, hit_target2, hit_stoploss,
                              record_id))
        conn.commit()

def get_cumulative_stats():
    """取得累積統計數據（供邏輯檢討用）"""
    sql = """
    SELECT
        grade,
        COUNT(*)                                         AS total,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        AVG(settle1_pct)                                 AS avg_ret1,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle2_pct)                                 AS avg_ret2
    FROM screen_records
    WHERE settle1_done = TRUE
    GROUP BY grade
    ORDER BY CASE grade WHEN 'SS' THEN 1 WHEN 'S' THEN 2
                        WHEN 'A'  THEN 3 WHEN 'X' THEN 4 END
    """
    bias_sql = """
    SELECT
        CASE
            WHEN bias_pct <= 5  THEN '理想(0-5%)'
            WHEN bias_pct <= 8  THEN '略高(5-8%)'
            WHEN bias_pct > 8   THEN '過高(>8%)'
            ELSE '底部(<0%)'
        END AS bias_zone,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM screen_records
    WHERE settle1_done = TRUE AND bias_pct IS NOT NULL
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
    WHERE settle1_done = TRUE
    GROUP BY buy_type
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql);      grade_rows = cur.fetchall()
            cur.execute(bias_sql); bias_rows  = cur.fetchall()
            cur.execute(dual_sql); dual_rows  = cur.fetchall()
    return grade_rows, bias_rows, dual_rows

def get_total_screened():
    """取得總篩選筆數"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM screen_records")
            return cur.fetchone()[0]
