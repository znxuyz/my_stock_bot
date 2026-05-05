"""
analysis_runs：每日分析執行狀態，避免 Bot 重啟導致 17:00 重複觸發。
"""
import logging
import config
from db.conn import get_conn

logger = logging.getLogger(__name__)


def record_run_start(run_date, attempt):
    """標記分析開始（upsert）"""
    sql = """
    INSERT INTO analysis_runs (run_date, status, attempt, started_at, finished_at, last_error, updated_at)
    VALUES (%s, 'running', %s, NOW(), NULL, NULL, NOW())
    ON CONFLICT (run_date) DO UPDATE SET
        status      = 'running',
        attempt     = EXCLUDED.attempt,
        started_at  = NOW(),
        finished_at = NULL,
        last_error  = NULL,
        updated_at  = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (run_date, attempt))
        conn.commit()


def record_run_end(run_date, status, last_error=None):
    """標記分析結束。status: 'success' / 'holiday' / 'fail'"""
    sql = """
    UPDATE analysis_runs SET
        status = %s, finished_at = NOW(), last_error = %s, updated_at = NOW()
    WHERE run_date = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, last_error, run_date))
        conn.commit()


def get_run_state(run_date):
    """回 (status, attempt, started_at)；無紀錄 / 失敗皆回 (None, 0, None)"""
    sql = 'SELECT status, attempt, started_at FROM analysis_runs WHERE run_date = %s'
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_date,))
                row = cur.fetchone()
                if row is None:
                    return None, 0, None
                return row[0], int(row[1] or 0), row[2]
    except Exception as e:
        logger.error('[DB] get_run_state 錯誤：%s', e)
        return None, 0, None


def can_run_today(run_date, now_dt):
    """
    回 (can_run, next_attempt, reason)。
    success/holiday 直接拒絕；fail 不自動重試（請手動 /run）；
    running 超時視為卡死，允許重跑。
    """
    status, attempt, started_at = get_run_state(run_date)
    if status in ('success', 'holiday'):
        return False, attempt, f'已 {status}，跳過'
    if status == 'running':
        if started_at is None:
            return False, attempt, 'running（剛啟動）'
        elapsed = (now_dt - started_at).total_seconds()
        if elapsed > config.RUN_TIMEOUT_SEC:
            return True, attempt, f'running 已 {int(elapsed)}s 視為卡死，重跑'
        return False, attempt, f'進行中（{int(elapsed)}s）'
    if status is None:
        return True, 0, '首次'
    return False, attempt, '已失敗，不自動重試（請手動 /run）'
