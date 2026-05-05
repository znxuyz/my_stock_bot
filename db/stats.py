"""
統計查詢：累積勝率、按等級/乖離/月分組、settlement timeline。
給 /report /stats 與 dashboard 用。
"""
import logging
import traceback

from psycopg2.extras import RealDictCursor

from db.conn import get_conn

logger = logging.getLogger(__name__)


def get_cumulative_stats(guild_id):
    """單一 guild 的等級 / 乖離 / 雙買超分組統計。"""
    grade_sql = """
    SELECT grade,
        COUNT(*) AS total,
        SUM(CASE WHEN fill_status='filled'  THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed'  THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN fill_status='pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled1,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        AVG(settle1_pct) AS avg_ret1,
        SUM(CASE WHEN settle2_done THEN 1 ELSE 0 END) AS settled2,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle2_pct) AS avg_ret2,
        SUM(CASE WHEN hit_target1  THEN 1 ELSE 0 END) AS hit_t1,
        SUM(CASE WHEN hit_target2  THEN 1 ELSE 0 END) AS hit_t2,
        SUM(CASE WHEN hit_stoploss THEN 1 ELSE 0 END) AS hit_sl
    FROM screen_records WHERE guild_id = %s
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
    FROM screen_records WHERE guild_id = %s
    GROUP BY buy_type
    """
    grade_rows, bias_rows, dual_rows = [], [], []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(grade_sql, (guild_id,)); grade_rows = list(cur.fetchall())
                cur.execute(bias_sql,  (guild_id,)); bias_rows  = list(cur.fetchall())
                cur.execute(dual_sql,  (guild_id,)); dual_rows  = list(cur.fetchall())
    except Exception as e:
        logger.error('[DB] get_cumulative_stats 錯誤：%s\n%s', e, traceback.format_exc())
    return grade_rows, bias_rows, dual_rows


# ─────────── 跨 guild 彙總（給 dashboard 用） ───────────
def get_latest_screen_date():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT MAX(screen_date) FROM screen_records')
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
    """跨 guild 去重後依 grade / bias / month 分組統計。"""
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
        SUM(CASE WHEN fill_status='filled'  THEN 1 ELSE 0 END) AS filled,
        SUM(CASE WHEN fill_status='missed'  THEN 1 ELSE 0 END) AS missed,
        SUM(CASE WHEN fill_status='pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled1,
        SUM(CASE WHEN settle2_done THEN 1 ELSE 0 END) AS settled2,
        SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win1,
        SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END) AS win2,
        AVG(settle1_pct) AS avg_ret1,
        AVG(settle2_pct) AS avg_ret2,
        SUM(CASE WHEN hit_target1  THEN 1 ELSE 0 END) AS hit_t1,
        SUM(CASE WHEN hit_target2  THEN 1 ELSE 0 END) AS hit_t2,
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
        SELECT DISTINCT ON (screen_date, sid) screen_date, settle1_pct, settle1_done
        FROM screen_records
        ORDER BY screen_date, sid, id
    )
    SELECT TO_CHAR(screen_date, 'YYYY-MM') AS ym,
           COUNT(*) AS total,
           SUM(CASE WHEN settle1_done THEN 1 ELSE 0 END) AS settled,
           SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END) AS win,
           AVG(settle1_pct) AS avg_ret
    FROM dedup GROUP BY ym ORDER BY ym DESC LIMIT 12
    """
    out = {'grade': [], 'bias': [], 'monthly': []}
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(grade_sql);   out['grade']   = list(cur.fetchall())
                cur.execute(bias_sql);    out['bias']    = list(cur.fetchall())
                cur.execute(monthly_sql); out['monthly'] = list(cur.fetchall())
    except Exception as e:
        logger.error('[DB] get_aggregated_stats 錯誤：%s\n%s', e, traceback.format_exc())
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
        SUM(CASE WHEN fill_status='filled'  THEN 1 ELSE 0 END)::int AS filled,
        SUM(CASE WHEN fill_status='missed'  THEN 1 ELSE 0 END)::int AS missed,
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
        logger.error('[DB] get_aggregated_summary 錯誤：%s', e)
        return {}


def get_missed_hypothetical_stats():
    """
    跨 guild 彙總「missed 但漲了 X%」的反向統計，用來量化「保守過頭損失多少」。
    只用 round 1 假設結算（missed_settle1_pct）。
    回傳 dict：
      total       missed 但已補算的筆數
      win         其中 missed 但漲（pct > 0）的筆數 → 真的「該追沒追到」
      win_rate    win / total（百分比；無資料 → None）
      avg_ret     平均假設報酬
      best        最大漲幅
      worst       最大跌幅
      by_grade    各等級的 [{grade, total, win, win_rate, avg_ret}]
    """
    summary_sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid) missed_settle1_pct
        FROM screen_records
        WHERE fill_status = 'missed' AND missed_settle1_pct IS NOT NULL
        ORDER BY screen_date, sid, id
    )
    SELECT
        COUNT(*)::int AS total,
        SUM(CASE WHEN missed_settle1_pct > 0 THEN 1 ELSE 0 END)::int AS win,
        AVG(missed_settle1_pct) AS avg_ret,
        MAX(missed_settle1_pct) AS best,
        MIN(missed_settle1_pct) AS worst
    FROM dedup
    """
    grade_sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid) grade, missed_settle1_pct
        FROM screen_records
        WHERE fill_status = 'missed' AND missed_settle1_pct IS NOT NULL
        ORDER BY screen_date, sid, id
    )
    SELECT grade,
        COUNT(*)::int AS total,
        SUM(CASE WHEN missed_settle1_pct > 0 THEN 1 ELSE 0 END)::int AS win,
        AVG(missed_settle1_pct) AS avg_ret
    FROM dedup
    GROUP BY grade
    ORDER BY CASE grade WHEN 'SS' THEN 1 WHEN 'S' THEN 2
                        WHEN 'A' THEN 3 WHEN 'CHASE' THEN 4
                        WHEN 'WATCH' THEN 5 ELSE 6 END
    """
    out = {'total': 0, 'win': 0, 'win_rate': None,
           'avg_ret': None, 'best': None, 'worst': None,
           'by_grade': []}
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(summary_sql)
                row = cur.fetchone() or {}
                total = int(row.get('total') or 0)
                win   = int(row.get('win')   or 0)
                out.update({
                    'total':   total,
                    'win':     win,
                    'win_rate': round(win / total * 100, 1) if total else None,
                    'avg_ret': float(row['avg_ret']) if row.get('avg_ret') is not None else None,
                    'best':    float(row['best'])    if row.get('best')    is not None else None,
                    'worst':   float(row['worst'])   if row.get('worst')   is not None else None,
                })
                cur.execute(grade_sql)
                rows = []
                for r in cur.fetchall():
                    g_total = int(r['total'] or 0)
                    g_win   = int(r['win']   or 0)
                    rows.append({
                        'grade':    r['grade'],
                        'total':    g_total,
                        'win':      g_win,
                        'win_rate': round(g_win / g_total * 100, 1) if g_total else None,
                        'avg_ret':  float(r['avg_ret']) if r['avg_ret'] is not None else None,
                    })
                out['by_grade'] = rows
    except Exception as e:
        logger.error('[DB] get_missed_hypothetical_stats 錯誤：%s\n%s', e, traceback.format_exc())
    return out


def get_settlement_timeline(limit_settlements=26):
    """依 settle1_date / settle2_date 分組，回傳兩條時間序列折線。"""
    sql = """
    WITH dedup AS (
        SELECT DISTINCT ON (screen_date, sid)
            settle1_date, settle1_pct, settle1_done,
            settle2_date, settle2_pct, settle2_done
        FROM screen_records
        ORDER BY screen_date, sid, id
    ),
    s1 AS (
        SELECT settle1_date AS sdate, COUNT(*)::int AS total,
               SUM(CASE WHEN settle1_pct > 0 THEN 1 ELSE 0 END)::int AS wins,
               AVG(settle1_pct) AS avg_ret
        FROM dedup WHERE settle1_done = TRUE GROUP BY settle1_date
    ),
    s2 AS (
        SELECT settle2_date AS sdate, COUNT(*)::int AS total,
               SUM(CASE WHEN settle2_pct > 0 THEN 1 ELSE 0 END)::int AS wins,
               AVG(settle2_pct) AS avg_ret
        FROM dedup WHERE settle2_done = TRUE GROUP BY settle2_date
    )
    SELECT 'w1' AS series, sdate, total, wins, avg_ret FROM s1
    UNION ALL
    SELECT 'w2' AS series, sdate, total, wins, avg_ret FROM s2
    ORDER BY sdate
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                rows = list(cur.fetchall())
        w1 = [r for r in rows if r['series'] == 'w1'][-limit_settlements:]
        w2 = [r for r in rows if r['series'] == 'w2'][-limit_settlements:]
        return {'w1': w1, 'w2': w2}
    except Exception as e:
        logger.error('[DB] get_settlement_timeline 錯誤：%s\n%s', e, traceback.format_exc())
        return {'w1': [], 'w2': []}
