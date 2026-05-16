"""
管理 slash commands（v6.2）：
  /backfill — 手動補抓 T86 歷史
  /health   — 系統健康檢查（schema / 表完整性 / 今日筆數）

開放給所有伺服器成員使用（不限管理員）。
"""
import logging
from datetime import date, timedelta

import config
from tools.backfill_t86_history import backfill

logger = logging.getLogger(__name__)


_STATUS_COLOR = {
    'success': 0x3FB950,
    'partial': 0xD29922,
    'failure': 0xF85149,
    'info':    0x58A6FF,
}


def _trim(items, max_n=15):
    """日期清單顯示用：超過 max_n 截斷並附「…還有 X 個」。"""
    if len(items) <= max_n:
        return ', '.join(items) if items else '（無）'
    head = ', '.join(items[:max_n])
    return f'{head}, …還有 {len(items) - max_n} 個'


def cmd_backfill_core(days=10):
    """核心邏輯：跑 backfill 主函式 + 組 Embed dict。

    days 會 clamp 到 [1, 60]。
    回傳 dict（Discord embed payload，可直接放進 followup `embeds` 陣列）。
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 10
    days = max(1, min(60, days))

    try:
        r = backfill(days=days)
    except Exception as e:
        logger.error('[/backfill] 例外：%s', e)
        return {
            'title': '❌ /backfill 失敗',
            'description': f'執行 backfill 時發生例外：`{e}`',
            'color': _STATUS_COLOR['failure'],
        }

    written  = r['written_dates']
    holiday  = r['holiday_dates']
    failed   = r['failed_dates']
    n_total  = r['total_rows']
    n_w, n_h, n_f = len(written), len(holiday), len(failed)

    if n_f == 0 and n_w > 0:
        title, color = '✅ /backfill 完成', _STATUS_COLOR['success']
    elif n_w == 0 and n_f == 0:
        title, color = 'ℹ️ /backfill 完成（無資料寫入）', _STATUS_COLOR['info']
    elif n_f > 0 and n_w > 0:
        title, color = '⚠️ /backfill 部分成功', _STATUS_COLOR['partial']
    else:
        title, color = '❌ /backfill 失敗', _STATUS_COLOR['failure']

    fields = [
        {'name': '請求天數',  'value': f'過去 {r["requested_days"]} 個交易日',
         'inline': True},
        {'name': '寫入日期數', 'value': f'{n_w} 天',  'inline': True},
        {'name': '總筆數',    'value': f'{n_total:,}', 'inline': True},
    ]
    if written:
        fields.append({'name': '最早日期', 'value': written[0],  'inline': True})
        fields.append({'name': '最晚日期', 'value': written[-1], 'inline': True})
        fields.append({'name': '​',   'value': '​',    'inline': True})
    if holiday:
        fields.append({'name': f'假日（{n_h}）',
                       'value': _trim(holiday), 'inline': False})
    if failed:
        fields.append({'name': f'❌ 失敗（{n_f}）',
                       'value': _trim(failed), 'inline': False})

    return {
        'title': title,
        'description': f'終止日期：`{r["end_date"]}`',
        'color': color,
        'fields': fields,
    }


def cmd_health_core(today=None):
    """系統健康檢查 → Embed dict。

    today 預設 = 今日（date object），測試時可傳入固定值。
    回傳的 dict 可直接當 Discord followup `embeds` 元素。
    """
    if today is None:
        today = date.today()

    sections = []
    overall_ok = True

    # 1. SCHEMA_VERSION
    schema = config.SCHEMA_VERSION
    schema_ok = schema == 'v62-pure-stalker-10pt'
    sections.append({
        'name': 'SCHEMA_VERSION',
        'value': f'`{schema}` {"✅" if schema_ok else "⚠️"}',
        'inline': False,
    })
    if not schema_ok:
        overall_ok = False

    # 2. daily_t86_history 過去 10 個工作日
    try:
        present_days, missing_days = _check_t86_completeness(today, days=10)
        if len(missing_days) == 0:
            t86_value = f'過去 10 天完整 ✅（{len(present_days)}/10）'
        else:
            overall_ok = False
            missing_str = ', '.join(d.strftime('%Y-%m-%d') for d in missing_days[:10])
            t86_value = (f'缺 {len(missing_days)} 天 ⚠️（{len(present_days)}/10）\n'
                         f'`{missing_str}`')
    except Exception as e:
        overall_ok = False
        t86_value = f'查詢失敗 ❌：`{e}`'
    sections.append({'name': 'daily_t86_history（過去 10 天）',
                     'value': t86_value, 'inline': False})

    # 3. daily_scores 過去 5 天筆數
    try:
        ds_count = _count_daily_scores(today, days=5)
        ds_value = f'{ds_count:,} 筆（過去 5 個日曆日）'
    except Exception as e:
        overall_ok = False
        ds_value = f'查詢失敗 ❌：`{e}`'
    sections.append({'name': 'daily_scores（過去 5 天）',
                     'value': ds_value, 'inline': True})

    # 4. screen_records 今日筆數
    try:
        sr_count = _count_screen_records_today(today)
        sr_value = f'{sr_count} 筆'
    except Exception as e:
        overall_ok = False
        sr_value = f'查詢失敗 ❌：`{e}`'
    sections.append({'name': f'screen_records（{today.isoformat()}）',
                     'value': sr_value, 'inline': True})

    title = '🩺 系統健康檢查' + (' ✅' if overall_ok else ' ⚠️')
    color = _STATUS_COLOR['success'] if overall_ok else _STATUS_COLOR['partial']
    return {'title': title, 'color': color, 'fields': sections}


# ─────────── 內部：DB 查詢 ───────────

def _check_t86_completeness(today, days=10):
    """回傳 (present_dates, missing_dates)，都是 list[date]。"""
    from db.conn import get_conn
    from twse_t86 import _prev_trading_days

    end_str = today.strftime('%Y%m%d')
    expected_strs = _prev_trading_days(end_str, days)
    from datetime import datetime as _dt
    expected = [_dt.strptime(d, '%Y%m%d').date() for d in expected_strs]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT date FROM daily_t86_history "
                "WHERE date >= %s AND date <= %s",
                (expected[0], expected[-1]),
            )
            present = {r[0] for r in cur.fetchall()}
    missing = [d for d in expected if d not in present]
    return sorted(present), missing


def _count_daily_scores(today, days=5):
    from db.conn import get_conn
    start = today - timedelta(days=days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM daily_scores WHERE date >= %s AND date <= %s",
                (start, today),
            )
            row = cur.fetchone()
            return int(row[0] or 0)


def _count_screen_records_today(today):
    from db.conn import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM screen_records WHERE screen_date = %s",
                (today,),
            )
            row = cur.fetchone()
            return int(row[0] or 0)
