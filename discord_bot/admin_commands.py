"""
管理 slash commands（v6.2）：
  /backfill — 手動補抓 T86 歷史
  /health   — 系統健康檢查（schema / 表完整性 / 今日筆數）
  /diag     — TWSE endpoint 連通性診斷（找出哪個 URL 路徑還活著）

開放給所有伺服器成員使用（不限管理員）。
"""
import logging
import time
from datetime import date, timedelta

import requests

import config
from tools.backfill_t86_history import backfill

logger = logging.getLogger(__name__)


_STATUS_COLOR = {
    'success': 0x3FB950,
    'partial': 0xD29922,
    'failure': 0xF85149,
    'info':    0x58A6FF,
}


# v6.2.2：TWSE endpoint 診斷用候選 URL（按 endpoint 分組）。
# 每個 endpoint 有多個歷史候選；/diag 會逐一打看哪個回 200。
# 第一個欄位是 label，第二個是 URL template（含 {date} placeholder）。
_TWSE_DIAG_TARGETS = {
    'MI_INDEX (每日收盤)': [
        ('current /rwd/zh/', 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=csv&date={date}&type=ALLBUT0999'),
        ('legacy /exchangeReport/', 'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date}&type=ALLBUT0999'),
        ('OpenAPI v1', 'https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX20'),
    ],
    'T86 (法人買賣超)': [
        ('current /rwd/zh/', 'https://www.twse.com.tw/rwd/zh/fund/T86?response=csv&date={date}&selectType=ALLBUT0999'),
        ('legacy /fund/', 'https://www.twse.com.tw/fund/T86?response=csv&date={date}&selectType=ALLBUT0999'),
        ('OpenAPI v1', 'https://openapi.twse.com.tw/v1/fund/T86'),
    ],
    'STOCK_DAY (個股日)': [
        ('current /rwd/zh/', 'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?response=csv&date={date}&stockNo=2330'),
        ('legacy /exchangeReport/', 'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=csv&date={date}&stockNo=2330'),
        ('OpenAPI v1', 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'),
    ],
    'MI_MARGN (融資融券)': [
        ('current /rwd/zh/', 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=csv&date={date}&selectType=ALL'),
        ('legacy /exchangeReport/', 'https://www.twse.com.tw/exchangeReport/MI_MARGN?response=csv&date={date}&selectType=ALL'),
    ],
    'MI_QFIIS (大盤外資)': [
        ('current /rwd/zh/', 'https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?response=csv&date={date}&selectType=ALLBUT0999'),
        ('legacy /fund/', 'https://www.twse.com.tw/fund/MI_QFIIS?response=csv&date={date}&selectType=ALLBUT0999'),
    ],
    'TWSE 首頁（連線健康度）': [
        ('homepage', 'https://www.twse.com.tw/'),
    ],
}


def _probe_url(url, timeout=10):
    """單一 URL 探測。回 dict 含 status_code / elapsed / body_head / error。"""
    t0 = time.time()
    try:
        r = requests.get(url, headers=config.TWSE_HEADERS,
                         timeout=timeout, verify=config.TWSE_VERIFY_SSL)
        elapsed = time.time() - t0
        body = r.text[:80].replace('\n', ' ').replace('\r', ' ')
        return {
            'status_code': r.status_code,
            'elapsed': round(elapsed, 2),
            'body_head': body,
            'error': None,
        }
    except requests.exceptions.Timeout:
        return {'status_code': None, 'elapsed': round(time.time() - t0, 2),
                'body_head': '', 'error': 'TIMEOUT'}
    except Exception as e:
        return {'status_code': None, 'elapsed': round(time.time() - t0, 2),
                'body_head': '', 'error': str(e)[:80]}


def _format_probe_line(label, result):
    code = result['status_code']
    if code is None:
        mark, code_str = '⚠️', f'ERR ({result["error"]})'
    elif 200 <= code < 300:
        mark, code_str = '✅', str(code)
    elif 300 <= code < 400:
        mark, code_str = '↪️', str(code)
    elif 400 <= code < 500:
        mark, code_str = '❌', str(code)
    else:
        mark, code_str = '🔥', str(code)
    return f'{mark} `{label}` → **{code_str}** ({result["elapsed"]}s)'


def cmd_diag_core(target_date=None):
    """測試多個 TWSE endpoint 的候選 URL，回 Embed dict。

    target_date 預設用「昨天」(避免今日資料還沒上架)；可傳 YYYYMMDD 字串。
    """
    if target_date is None:
        # 昨天工作日
        d = date.today() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        target_date = d.strftime('%Y%m%d')

    fields = []
    success_total = 0
    total_probes = 0

    for endpoint_label, candidates in _TWSE_DIAG_TARGETS.items():
        lines = []
        any_success = False
        for cand_label, url_template in candidates:
            url = url_template.replace('{date}', target_date)
            result = _probe_url(url)
            lines.append(_format_probe_line(cand_label, result))
            total_probes += 1
            if result['status_code'] and 200 <= result['status_code'] < 300:
                success_total += 1
                any_success = True
        prefix = '✅ ' if any_success else '❌ '
        fields.append({
            'name': f'{prefix}{endpoint_label}',
            'value': '\n'.join(lines)[:1020],  # Discord field value 1024 字元上限
            'inline': False,
        })

    if success_total == total_probes:
        color, title = _STATUS_COLOR['success'], '🩺 TWSE 連線診斷 ✅ 全綠'
    elif success_total == 0:
        color, title = _STATUS_COLOR['failure'], '🩺 TWSE 連線診斷 ❌ 全部失敗'
    else:
        color, title = _STATUS_COLOR['partial'], '🩺 TWSE 連線診斷 ⚠️ 部分成功'

    return {
        'title': title,
        'description': f'測試日期：`{target_date}` · 成功 {success_total}/{total_probes}',
        'color': color,
        'fields': fields,
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

    # 5. KBAR cache 持久化檢查
    cache_value, cache_ok = _kbar_cache_status()
    sections.append({'name': 'KBAR cache', 'value': cache_value, 'inline': False})
    if not cache_ok:
        # ⚠️ 不影響功能但標記黃，提醒部署不是 persistent
        overall_ok = False

    title = '🩺 系統健康檢查' + (' ✅' if overall_ok else ' ⚠️')
    color = _STATUS_COLOR['success'] if overall_ok else _STATUS_COLOR['partial']
    return {'title': title, 'color': color, 'fields': sections}


# ─────────── 內部：KBAR cache 健康檢查 ───────────

def _human_bytes(n):
    """整數位元組 → 人類可讀。"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def _kbar_cache_status():
    """檢查 KBAR cache 目錄狀態。回 (value_str, is_persistent_bool)。

    is_persistent = True 表示 cache_dir 不在 /tmp 下、跨 deploy 持久化。
    cache_dir 不存在或無權限都會優雅 fallback 成警示訊息。
    """
    import os as _os
    cache_dir = config.KBAR_CACHE_DIR or ''
    is_persistent = bool(cache_dir) and not cache_dir.startswith('/tmp')

    # 路徑存在性 / 統計
    try:
        if not cache_dir:
            return ('⚠️ KBAR_CACHE_DIR 未設定', False)
        if not _os.path.exists(cache_dir):
            # 目錄還沒建（第一次啟動）— 不算 fail，但提示
            marker = '✅ persistent' if is_persistent else '⚠️ /tmp（重啟會清空）'
            return (
                f'路徑：`{cache_dir}` {marker}\n'
                f'狀態：目錄尚未建立（首次 /run 後會自動產生）',
                is_persistent,
            )
        if not _os.path.isdir(cache_dir):
            return (f'⚠️ `{cache_dir}` 不是目錄', False)

        # 算檔案數 + 總大小
        n_files = 0
        total_bytes = 0
        for root, _dirs, files in _os.walk(cache_dir):
            n_files += len(files)
            for f in files:
                try:
                    total_bytes += _os.path.getsize(_os.path.join(root, f))
                except OSError:
                    pass

        marker = '✅ persistent' if is_persistent else '⚠️ /tmp（重啟會清空）'
        return (
            f'路徑：`{cache_dir}` {marker}\n'
            f'檔案數：**{n_files:,}**　目錄大小：**{_human_bytes(total_bytes)}**',
            is_persistent,
        )
    except PermissionError as e:
        return (f'❌ 權限錯誤：`{e}`', False)
    except Exception as e:
        return (f'❌ 查詢失敗：`{e}`', False)


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
