"""
init_db：建立所有資料表，必要時 DROP 重建 screen_records（schema 升級）。
"""
import config
from db.conn import get_conn, get_schema_version, set_schema_version


_OTHER_DDL = """
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
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_date    DATE PRIMARY KEY,
    status      VARCHAR(15) NOT NULL,
    attempt     INT DEFAULT 0,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    last_error  TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);
"""

_SCREEN_DDL = """
CREATE TABLE IF NOT EXISTS screen_records (
    id                 SERIAL PRIMARY KEY,
    guild_id           VARCHAR(30) NOT NULL,
    screen_date        DATE NOT NULL,
    sid                VARCHAR(10) NOT NULL,
    name               VARCHAR(50),
    grade              VARCHAR(5),
    score              INT,
    close_price        NUMERIC(12,2),
    change_pct         NUMERIC(8,2),
    vol_ratio          NUMERIC(8,2),
    foreign_shares     BIGINT,
    trust_shares       BIGINT,
    bias_pct           NUMERIC(8,2),
    bias_label         VARCHAR(20),
    position_pct       NUMERIC(5,1),
    chase_mode         VARCHAR(15) DEFAULT 'normal',
    consec_limit_up    INT DEFAULT 0,
    entry_zone_low     NUMERIC(12,2),
    entry_zone_high    NUMERIC(12,2),
    actual_entry_date  DATE,
    actual_entry_price NUMERIC(12,2),
    actual_target1     NUMERIC(12,2),
    actual_target2     NUMERIC(12,2),
    actual_stop_loss   NUMERIC(12,2),
    fill_status        VARCHAR(10) DEFAULT 'pending',
    settle1_date       DATE,
    settle2_date       DATE,
    settle1_price      NUMERIC(12,2),
    settle2_price      NUMERIC(12,2),
    settle1_pct        NUMERIC(8,2),
    settle2_pct        NUMERIC(8,2),
    settle1_done       BOOLEAN DEFAULT FALSE,
    settle2_done       BOOLEAN DEFAULT FALSE,
    hit_target1        BOOLEAN,
    hit_target2        BOOLEAN,
    hit_stoploss       BOOLEAN,
    hit_target1_date   DATE,
    hit_target2_date   DATE,
    hit_stoploss_date  DATE,
    created_at         TIMESTAMP DEFAULT NOW()
);
"""


def init_db():
    """初始化所有資料表；screen_records schema 版本不符時自動 DROP 重建。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_OTHER_DDL)
            ver = get_schema_version(cur)
            if ver != config.SCHEMA_VERSION:
                print(f'[DB] screen_records schema 升級：{ver} → {config.SCHEMA_VERSION}（清空舊資料）')
                cur.execute('DROP TABLE IF EXISTS screen_records')
                cur.execute(_SCREEN_DDL)
                set_schema_version(cur, config.SCHEMA_VERSION)
            else:
                cur.execute(_SCREEN_DDL)
        conn.commit()
    print(f'[DB] 資料表初始化完成（screen_records {config.SCHEMA_VERSION}）')
