"""
init_db：建立所有資料表，必要時 DROP 重建 screen_records（schema 升級）。
v6.2：screen_records 結構大改、新增 daily_scores / daily_t86_history。
"""
import logging
import config
from db.conn import get_conn, get_schema_version, set_schema_version

logger = logging.getLogger(__name__)


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

# v6.2：screen_records 結構大改（取代 v5）
_SCREEN_DDL = """
CREATE TABLE IF NOT EXISTS screen_records (
    id SERIAL PRIMARY KEY,
    sid VARCHAR(20) NOT NULL,
    name VARCHAR(50),
    screen_date DATE NOT NULL,
    guild_id VARCHAR(50) NOT NULL,

    -- v6.2 評分（10 分制）
    flow_score INTEGER,
    trend_score INTEGER,
    heat_score INTEGER,
    total_score INTEGER,
    status VARCHAR(15),

    -- Stalker 累積資料
    acc_buy_days INTEGER,
    acc_cum_net BIGINT,
    cum_5d_pct NUMERIC(6,2),
    bias_20 NUMERIC(6,2),
    vol_vs_60d NUMERIC(6,2),

    -- 進場
    close_price NUMERIC(10,2),
    fill_status VARCHAR(20),
    actual_entry_date DATE,
    actual_entry_price NUMERIC(10,2),
    t1_open_price NUMERIC(10,2),

    -- 出場（Phase 2 填）
    actual_target1 NUMERIC(10,2),
    actual_target2 NUMERIC(10,2),
    actual_stop_loss NUMERIC(10,2),
    atr14 NUMERIC(10,4),
    atr_stop_pct NUMERIC(6,3),
    max_close_since_entry NUMERIC(10,2),
    time_stop_date DATE,
    exit_reason VARCHAR(20),

    -- 結算
    settle1_date DATE,
    settle1_price NUMERIC(10,2),
    settle1_pct NUMERIC(6,2),
    settle1_done BOOLEAN DEFAULT FALSE,
    settle2_date DATE,
    settle2_price NUMERIC(10,2),
    settle2_pct NUMERIC(6,2),
    settle2_done BOOLEAN DEFAULT FALSE,
    hit_target1 BOOLEAN,
    hit_target2 BOOLEAN,
    hit_stoploss BOOLEAN,
    hit_target1_date DATE,
    hit_target2_date DATE,
    hit_stoploss_date DATE,

    -- missed hypothetical
    missed_settle1_close NUMERIC(10,2),
    missed_settle1_pct NUMERIC(6,2),

    -- meta
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_screen_sid_date   ON screen_records (sid, screen_date DESC);
CREATE INDEX IF NOT EXISTS idx_screen_guild_date ON screen_records (guild_id, screen_date DESC);
"""

# v6.2 新表：每日全 candidate 分數（給「分數動能追蹤」用）
_DAILY_SCORES_DDL = """
CREATE TABLE IF NOT EXISTS daily_scores (
    sid VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    flow_score INTEGER,
    trend_score INTEGER,
    heat_score INTEGER,
    total_score INTEGER,
    status VARCHAR(15),
    PRIMARY KEY (sid, date)
);
CREATE INDEX IF NOT EXISTS idx_ds_sid_date ON daily_scores (sid, date DESC);
"""

# v6.2 新表：法人歷史（給 Stalker 5 日累積偵測用）
_DAILY_T86_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS daily_t86_history (
    sid VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    foreign_net BIGINT,
    trust_net BIGINT,
    dealer_net BIGINT,
    PRIMARY KEY (sid, date)
);
CREATE INDEX IF NOT EXISTS idx_t86_sid_date ON daily_t86_history (sid, date DESC);
"""


def init_db():
    """初始化所有資料表；schema 版本不符 → DROP 重建 screen_records；
    daily_scores / daily_t86_history 用 IF NOT EXISTS 保護，重啟誤觸也不會清空。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_OTHER_DDL)
            ver = get_schema_version(cur)
            if ver != config.SCHEMA_VERSION:
                logger.warning('[DB] screen_records schema 升級：%s → %s（清空舊資料）',
                               ver, config.SCHEMA_VERSION)
                cur.execute('DROP TABLE IF EXISTS screen_records CASCADE')
                cur.execute(_SCREEN_DDL)
                set_schema_version(cur, config.SCHEMA_VERSION)
            else:
                cur.execute(_SCREEN_DDL)
            cur.execute(_DAILY_SCORES_DDL)
            cur.execute(_DAILY_T86_HISTORY_DDL)
        conn.commit()
    logger.info('[DB] 資料表初始化完成（schema=%s）', config.SCHEMA_VERSION)
