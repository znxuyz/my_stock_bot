"""
PostgreSQL 連線 + schema 版本管理。
所有 db/* 模組共用 get_conn()。
"""
import psycopg2

import config


def get_conn():
    url = config.DATABASE_URL
    if not url:
        raise RuntimeError('DATABASE_URL 環境變數未設定')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url)


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
