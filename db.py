"""
資料庫模組 db.py ── 多伺服器版（v2：實際進場價策略）
================================
【策略改動 v2】
舊：以篩選日收盤 close_price 為基準計算結算報酬，會美化勝率
新：以隔日 T+1 開盤 actual_entry_price 為基準（無腦市價買，保證能成交）
    target/stop 改用 actual_entry × (1.05/1.10/0.95)
    settle_pct = (settle_close - actual_entry) / actual_entry × 100
    若持有期間 low 觸到 stop_loss → settle_pct 強制視為 -5%（停損出場）
    target1/2 達標只是 informational hit 旗標，不影響 settle_pct（讓利潤奔跑）
所有用戶資料以 (guild_id, user_id) 為 key
篩選/結算結果以 guild_id 為 key
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta

def get_conn():
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise Exception('DATABASE_URL 環境變數未設定')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url)


# 手動觸發舊資料清空：將此值改為 v3、v4… 即可重建 screen_records
SCHEMA_VERSION = 'v4-macd-chase'


def _ensure_schema_version_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            key   VARCHAR(50) PRIMARY KEY,
            value VARCHAR(50) NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)


def _get_schema_version(cur):
    _ensure_schema_version_table(cur)
    cur.execute("SELECT value FROM schema_version WHERE key = 'screen_records'")
    row = cur.fetchone()
    return row[0] if row else None


def _set_schema_version(cur, version):
    cur.execute("""
        INSERT INTO schema_version (key, value) VALUES ('screen_records', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """, (version,))


def init_db():
    """
    初始化資料表。若 screen_records schema 版本不符，自動 DROP 重建（清空舊資料）。
    """
    other_ddl = """
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id    VARCHAR(30) PRIMARY KEY,
        webhook_url TEXT NOT NULL,
        setup_by    VARCHAR(30),
        created_at  TIMESTAMP DEFAULT NOW()
    );
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
    CREATE TABLE IF NOT EXISTS pnl_summary (
        guild_id    VARCHAR(30) NOT NULL,
        user_id     VARCHAR(30) NOT NULL,
        total_pnl   NUMERIC(14,2) DEFAULT 0,
        updated_at  TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (guild_id, user_id)
    );
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

    screen_ddl = """
    CREATE TABLE IF NOT EXISTS screen_records (
        id                 SERIAL PRIMARY KEY,
        guild_id           VARCHAR(30) NOT NULL,
        screen_date        DATE NOT NULL,
        sid                VARCHAR(10) NOT NULL,
        name               VARCHAR(50),
        grade              VARCHAR(5),
        score              INT,
        close_price        NUMERIC(12,2),       -- 篩選日收盤（僅參考）
        change_pct         NUMERIC(8,2),
        vol_ratio          NUMERIC(8,2),
        foreign_shares     BIGINT,
        trust_shares       BIGINT,
        bias_pct           NUMERIC(8,2),
        bias_label         VARCHAR(20),
        position_pct       NUMERIC(5,1),
        -- 追漲模式：normal=回檔買 / strong_chase=連續漲停且通過5項追漲門檻 / watch=4項過僅觀察
        chase_mode         VARCHAR(15) DEFAULT 'normal',
        consec_limit_up    INT DEFAULT 0,        -- 連續漲停天數
        -- 建議進場區間（T 日寫入；normal vs strong_chase 區間不同）
        entry_zone_low     NUMERIC(12,2),
        entry_zone_high    NUMERIC(12,2),
        -- 實際進場：T+1 撮合後回填
        actual_entry_date  DATE,                -- T+1 日期（檢查後寫入，無論成交與否）
        actual_entry_price NUMERIC(12,2),       -- 實際成交價（NULL=未進場）
        actual_target1     NUMERIC(12,2),       -- entry × 1.05
        actual_target2     NUMERIC(12,2),       -- entry × 1.10
        actual_stop_loss   NUMERIC(12,2),       -- entry × 0.95
        fill_status        VARCHAR(10) DEFAULT 'pending',  -- pending/filled/missed
        -- 結算
        settle1_date       DATE,
        settle2_date       DATE,
        settle1_price      NUMERIC(12,2),
        settle2_price      NUMERIC(12,2),
        settle1_pct        NUMERIC(8,2),
        settle2_pct        NUMERIC(8,2),
        settle1_done       BOOLEAN DEFAULT FALSE,
        settle2_done       BOOLEAN DEFAULT FALSE,
        -- 持有期間觸發
        hit_target1        BOOLEAN,
        hit_target2        BOOLEAN,
        hit_stoploss       BOOLEAN,
        hit_target1_date   DATE,
        hit_target2_date   DATE,
        hit_stoploss_date  DATE,
        created_at         TIMESTAMP DEFAULT NOW()
    );
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(other_ddl)
            ver = _get_schema_version(cur)
            if ver != SCHEMA_VERSION:
                # schema 版本不符，DROP 重建（清空舊資料）
                print(f"[DB] screen_records schema 升級：{ver} → {SCHEMA_VERSION}（清空舊資料）")
                cur.execute("DROP TABLE IF EXISTS screen_records")
                cur.execute(screen_ddl)
                _set_schema_version(cur, SCHEMA_VERSION)
            else:
                cur.execute(screen_ddl)
        conn.commit()
    print(f"[DB] 資料表初始化完成（screen_records {SCHEMA_VERSION}）")


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
    """
    寫入篩選結果。actual_entry_* 留 NULL，由 fill_t1_entry 在 T+1 後回填。
    chase_mode 決定 entry_zone 與 fill_status 初始值：
      normal:       zone = [close × 0.97, close × 1.00]，fill_status='pending'
      strong_chase: zone = [close × 1.00, close × 1.07]，fill_status='pending'
      watch:        zone = NULL（不會撮合）           ，fill_status='watch'
    """
    settle1 = next_friday(screen_date, 1)
    settle2 = next_friday(screen_date, 2)
    rows = []
    for e in records:
        b     = e.get('bias') or {}
        pos   = calc_position_pct(e.get('grade',''), b.get('bias_pct'))
        close = float(e.get('price', 0))
        mode  = e.get('chase_mode', 'normal')
        consec = int(e.get('consec_limit_up', 0))

        if mode == 'strong_chase':
            zone_low  = round(close * 1.00, 2)
            zone_high = round(close * 1.07, 2)
            init_fill = 'pending'
        elif mode == 'watch':
            zone_low, zone_high = None, None
            init_fill = 'watch'
        else:  # normal
            zone_low  = round(close * 0.97, 2)
            zone_high = round(close * 1.00, 2)
            init_fill = 'pending'

        rows.append((
            guild_id, screen_date,
            e['sid'], e.get('name',''), e.get('grade',''),
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
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    print(f"[DB] 寫入 {len(rows)} 筆篩選記錄（{screen_date} guild:{guild_id}）")


def get_records_needing_t1_check(before_date):
    """
    撈取 fill_status='pending' 且 screen_date < before_date 的紀錄
    （由呼叫端在 T+1 抓 K 棒判定是否成交）
    'watch' 模式不會被撈出，因為它本來就不撮合。
    """
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


def determine_t1_fill(t1_open, t1_high, t1_low, zone_low, zone_high,
                      allow_gap_down=True):
    """
    根據 T+1 K 棒 + 進場區間決定撮合結果。
    回傳 (status, fill_price)：('filled', price) 或 ('missed', None)

    一般撮合規則（限價單模擬）：
      A. 開盤在區間內       → 以開盤成交（最佳）
      B. 開盤跳空高於區間   → 若盤中 low ≤ zone_high，以 zone_high 成交；否則 missed
      C. 開盤跳空低於區間   → 以開盤成交（撿便宜）

    allow_gap_down=False（強勢追漲模式專用）：
      C 改為直接 missed —— 強勢飆股若隔日跳空跌破代表趨勢反轉，不接刀。
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
    # o < zl
    if allow_gap_down:
        return 'filled', round(o, 2)
    return 'missed', None


def fill_t1_entry(record_id, t1_date, status, entry_price):
    """
    寫入 T+1 撮合結果。
      filled: 設 actual_entry_price + 三個目標停損
      missed: actual_entry_price 留 NULL
    """
    if status == 'filled' and entry_price is not None:
        e   = float(entry_price)
        t1  = round(e * 1.05, 2)
        t2  = round(e * 1.10, 2)
        sl  = round(e * 0.95, 2)
        sql = """
        UPDATE screen_records SET
            actual_entry_date  = %s,
            actual_entry_price = %s,
            actual_target1     = %s,
            actual_target2     = %s,
            actual_stop_loss   = %s,
            fill_status        = 'filled'
        WHERE id = %s
        """
        params = (t1_date, e, t1, t2, sl, record_id)
    else:
        sql = """
        UPDATE screen_records SET
            actual_entry_date = %s,
            fill_status       = 'missed'
        WHERE id = %s
        """
        params = (t1_date, record_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def get_pending_settle(settle_date, round_num, guild_id):
    """撈出指定 settle_date 待結算的紀錄（fill_status='filled' 才能結算）"""
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
    """
    寫入結算結果。
    settle_pct 由呼叫端傳入（已考慮停損強制 -5%）。
    """
    if round_num == 1:
        sql = """
        UPDATE screen_records SET
            settle1_price = %s, settle1_pct = %s, settle1_done = TRUE,
            hit_target1 = %s, hit_target2 = %s, hit_stoploss = %s,
            hit_target1_date = COALESCE(hit_target1_date, %s),
            hit_target2_date = COALESCE(hit_target2_date, %s),
            hit_stoploss_date = COALESCE(hit_stoploss_date, %s)
        WHERE id = %s
        """
    else:
        sql = """
        UPDATE screen_records SET
            settle2_price = %s, settle2_pct = %s, settle2_done = TRUE,
            hit_target1 = %s, hit_target2 = %s, hit_stoploss = %s,
            hit_target1_date = COALESCE(hit_target1_date, %s),
            hit_target2_date = COALESCE(hit_target2_date, %s),
            hit_stoploss_date = COALESCE(hit_stoploss_date, %s)
        WHERE id = %s
        """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settle_close, settle_pct,
                              hit_target1, hit_target2, hit_stoploss,
                              hit_target1_date, hit_target2_date, hit_stoploss_date,
                              record_id))
        conn.commit()


def get_cumulative_stats(guild_id):
    grade_sql = """
    SELECT grade,
        COUNT(*) AS total,
        SUM(CASE WHEN fill_status='filled' THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed' THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN fill_status='pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled1,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        AVG(settle1_pct) AS avg_ret1,
        SUM(CASE WHEN settle2_done THEN 1 ELSE 0 END) AS settled2,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle2_pct) AS avg_ret2,
        SUM(CASE WHEN hit_target1 THEN 1 ELSE 0 END) AS hit_t1,
        SUM(CASE WHEN hit_target2 THEN 1 ELSE 0 END) AS hit_t2,
        SUM(CASE WHEN hit_stoploss THEN 1 ELSE 0 END) AS hit_sl
    FROM screen_records
    WHERE guild_id = %s
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
        SUM(CASE WHEN fill_status='filled' THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed' THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM screen_records
    WHERE guild_id = %s AND bias_pct IS NOT NULL
    GROUP BY bias_zone
    """
    dual_sql = """
    SELECT
        CASE WHEN foreign_shares >= 10000 AND trust_shares >= 10000
             THEN '雙買超' ELSE '單方買超' END AS buy_type,
        COUNT(*) AS total,
        SUM(CASE WHEN fill_status='filled' THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed' THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM screen_records
    WHERE guild_id = %s
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
# 跨伺服器彙總（給 Web Dashboard 使用）
#   因為所有 guild 的 screen_records 內容相同，
#   彙總時用 DISTINCT ON (screen_date, sid) 避免重複計算。
# ══════════════════════════════════════════════════
def get_latest_screen_date():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(screen_date) FROM screen_records")
            row = cur.fetchone()
            return row[0] if row and row[0] else None

def get_screens_by_date(screen_date):
    sql = """
    SELECT DISTINCT ON (sid)
        screen_date, sid, name, grade, score, close_price, change_pct,
        vol_ratio, foreign_shares, trust_shares, bias_pct, bias_label,
        position_pct, chase_mode, consec_limit_up,
        entry_zone_low, entry_zone_high, fill_status,
        actual_entry_date, actual_entry_price,
        actual_target1, actual_target2, actual_stop_loss,
        settle1_date, settle2_date, settle1_price, settle2_price,
        settle1_pct, settle2_pct, settle1_done, settle2_done,
        hit_target1, hit_target2, hit_stoploss,
        hit_target1_date, hit_target2_date, hit_stoploss_date
    FROM screen_records
    WHERE screen_date = %s
    ORDER BY sid, id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (screen_date,))
            return cur.fetchall()

def get_history_records(limit_days=90):
    sql = """
    SELECT DISTINCT ON (screen_date, sid)
        screen_date, sid, name, grade, score, close_price, change_pct,
        vol_ratio, bias_pct,
        chase_mode, consec_limit_up,
        entry_zone_low, entry_zone_high, fill_status,
        actual_entry_date, actual_entry_price,
        actual_target1, actual_target2, actual_stop_loss,
        settle1_pct, settle2_pct, settle1_done, settle2_done,
        hit_target1, hit_target2, hit_stoploss,
        hit_target1_date, hit_target2_date, hit_stoploss_date
    FROM screen_records
    WHERE screen_date >= CURRENT_DATE - %s::int
    ORDER BY screen_date DESC, sid, id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (limit_days,))
            return cur.fetchall()

def get_aggregated_stats():
    grade_sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid)
            grade, fill_status,
            settle1_pct, settle2_pct, settle1_done, settle2_done,
            hit_target1, hit_target2, hit_stoploss
        FROM screen_records
        ORDER BY screen_date, sid, id
    )
    SELECT grade,
        COUNT(*) AS total,
        SUM(CASE WHEN fill_status='filled' THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed' THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN fill_status='pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled1,
        SUM(CASE WHEN settle2_done THEN 1 ELSE 0 END) AS settled2,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle1_pct) AS avg_ret1,
        AVG(settle2_pct) AS avg_ret2,
        SUM(CASE WHEN hit_target1 THEN 1 ELSE 0 END) AS hit_t1,
        SUM(CASE WHEN hit_target2 THEN 1 ELSE 0 END) AS hit_t2,
        SUM(CASE WHEN hit_stoploss THEN 1 ELSE 0 END) AS hit_sl
    FROM dedup
    GROUP BY grade
    ORDER BY CASE grade WHEN 'SS' THEN 1 WHEN 'S' THEN 2
                        WHEN 'A' THEN 3 WHEN 'X' THEN 4 ELSE 5 END
    """
    bias_sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid)
            bias_pct, settle1_pct, settle1_done
        FROM screen_records
        WHERE bias_pct IS NOT NULL
        ORDER BY screen_date, sid, id
    )
    SELECT
        CASE WHEN bias_pct < 0  THEN '底部(<0%)'
             WHEN bias_pct <= 5 THEN '理想(0-5%)'
             WHEN bias_pct <= 8 THEN '略高(5-8%)'
             ELSE '過高(>8%)' END AS bias_zone,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM dedup
    GROUP BY bias_zone
    ORDER BY bias_zone
    """
    monthly_sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid)
            screen_date, settle1_pct, settle1_done
        FROM screen_records
        ORDER BY screen_date, sid, id
    )
    SELECT
        TO_CHAR(screen_date, 'YYYY-MM') AS ym,
        COUNT(*) AS total,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
        AVG(settle1_pct) AS avg_ret
    FROM dedup
    GROUP BY ym
    ORDER BY ym DESC
    LIMIT 12
    """
    out = {'grade': [], 'bias': [], 'monthly': []}
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(grade_sql);   out['grade']   = list(cur.fetchall())
                cur.execute(bias_sql);    out['bias']    = list(cur.fetchall())
                cur.execute(monthly_sql); out['monthly'] = list(cur.fetchall())
    except Exception as e:
        import traceback
        print(f"[DB] get_aggregated_stats 錯誤：{e}\n{traceback.format_exc()}")
    return out

def get_aggregated_summary():
    sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid)
            fill_status, settle1_pct, settle2_pct, settle1_done, settle2_done
        FROM screen_records
        ORDER BY screen_date, sid, id
    )
    SELECT
        COUNT(*)::int AS total,
        SUM(CASE WHEN fill_status='filled' THEN 1 ELSE 0 END)::int AS filled,
        SUM(CASE WHEN fill_status='missed' THEN 1 ELSE 0 END)::int AS missed,
        SUM(CASE WHEN fill_status='pending' THEN 1 ELSE 0 END)::int AS pending,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END)::int AS settled1,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END)::int AS win1,
        AVG(settle1_pct) AS avg_ret1,
        SUM(CASE WHEN settle2_done THEN 1 ELSE 0 END)::int AS settled2,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END)::int AS win2,
        AVG(settle2_pct) AS avg_ret2,
        MAX(settle1_pct) AS best_ret1,
        MIN(settle1_pct) AS worst_ret1
    FROM dedup
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                return cur.fetchone() or {}
    except Exception as e:
        print(f"[DB] get_aggregated_summary 錯誤：{e}")
        return {}

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
