"""
PostgreSQL 連線 + schema 版本管理。
所有 db/* 模組共用 get_conn()。

可用性檢查：
  db.is_available() ── 應用層先檢查 DATABASE_URL 是否設定，避免每次都吃連線錯誤。

連線重試：
  get_conn() 連線失敗（OperationalError）時會以指數退避重試最多 3 次（5/15/30s）。
"""
import logging
import time

import psycopg2

import config

logger = logging.getLogger(__name__)

# 連線重試延遲（秒）。第 0 次立即試，後續退避。
_RETRY_DELAYS = (0, 5, 15, 30)


def is_available():
    """應用層檢查：DATABASE_URL 是否有設定。"""
    return bool(config.DATABASE_URL)


def get_conn():
    """
    取得新的 PostgreSQL 連線。
    OperationalError 自動重試 3 次（5/15/30s 指數退避）；其他例外直接丟出。
    呼叫端用 `with get_conn() as conn:` 做交易管理（context manager 結束會 commit/rollback）。
    """
    url = config.DATABASE_URL
    if not url:
        raise RuntimeError('DATABASE_URL 環境變數未設定')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)

    last_exc = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        if delay:
            logger.warning('[DB] 連線失敗，%ds 後重試 (第 %d 次)', delay, attempt)
            time.sleep(delay)
        try:
            return psycopg2.connect(url)
        except psycopg2.OperationalError as e:
            last_exc = e
    logger.error('[DB] 連線重試 %d 次仍失敗：%s', len(_RETRY_DELAYS) - 1, last_exc)
    raise last_exc


def ensure_schema_version_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            key   VARCHAR(50) PRIMARY KEY,
            value VARCHAR(50) NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)


def get_schema_version(cur):
    ensure_schema_version_table(cur)
    cur.execute("SELECT value FROM schema_version WHERE key = 'screen_records'")
    row = cur.fetchone()
    return row[0] if row else None


def set_schema_version(cur, version):
    cur.execute(
        """
        INSERT INTO schema_version (key, value) VALUES ('screen_records', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (version,),
    )
