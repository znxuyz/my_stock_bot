"""
Web Dashboard 匯出：
  1. 從 DB 撈彙總資料 → 寫成 docs/data/*.json
  2. 透過 GitHub API 推回 docs/data/，GitHub Pages 由 docs/ 目錄 serve
若 GITHUB_TOKEN / GITHUB_REPO 未設定則只在本機寫檔（除錯用）。
"""
import logging
import base64
import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import requests

import config
import db

logger = logging.getLogger(__name__)


DOCS_DIR = os.path.join(os.path.dirname(__file__), 'docs')
DATA_DIR = os.path.join(DOCS_DIR, 'data')


# ─────────── JSON 序列化輔助 ───────────
def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f'Type {type(o)} not serializable')


def _row_to_dict(row):
    """RealDictRow → 一般 dict，並轉換型別"""
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _tw_now_iso():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


# ─────────── 組裝 payloads ───────────
def build_payloads():
    """從 DB 撈所有需要的資料，組成 JSON payload dict。"""
    latest_date  = db.get_latest_screen_date()
    today_rows   = [_row_to_dict(r) for r in db.get_screens_by_date(latest_date)] if latest_date else []
    history_rows = [_row_to_dict(r) for r in db.get_history_records(limit_days=90)]

    stats    = db.get_aggregated_stats()
    summary  = db.get_aggregated_summary()
    timeline = db.get_settlement_timeline(limit_settlements=26)
    missed_hypo = db.get_missed_hypothetical_stats()

    stats_clean = {
        'status':  [_row_to_dict(r) for r in stats.get('status',  [])],
        'bias':    [_row_to_dict(r) for r in stats.get('bias',    [])],
        'monthly': [_row_to_dict(r) for r in stats.get('monthly', [])],
    }
    summary_clean = _row_to_dict(summary) if summary else {}
    timeline_clean = {
        'w1': [_row_to_dict(r) for r in timeline.get('w1', [])],
        'w2': [_row_to_dict(r) for r in timeline.get('w2', [])],
    }

    # v6.2：依今日篩選結果計算 5 段 status 計數
    counts = {'momentum': 0, 'active': 0, 'setup': 0, 'watch': 0, 'noise': 0}
    for r in today_rows:
        st = (r.get('status') or '').lower()
        if st in counts:
            counts[st] += 1
    counts['total_pushed'] = counts['momentum'] + counts['active'] + counts['setup']

    updated_at = _tw_now_iso()
    payloads = {
        'today.json': {
            'updated_at':  updated_at,
            'screen_date': latest_date.isoformat() if latest_date else None,
            'count':       len(today_rows),
            'counts':      counts,
            'records':     today_rows,
        },
        'stats.json': {
            'updated_at': updated_at,
            'summary':    summary_clean,
            'counts':     counts,
            'by_status':  stats_clean['status'],
            'by_bias':    stats_clean['bias'],
            'by_month':   stats_clean['monthly'],
            'timeline':   timeline_clean,
            # missed 假設結算：量化「保守過頭損失多少」
            'missed_hypo': missed_hypo,
        },
        'history.json': {
            'updated_at': updated_at,
            'count':      len(history_rows),
            'records':    history_rows,
        },
    }

    api_url = (
        os.environ.get('BOT_PUBLIC_URL', '').strip().rstrip('/')
        or os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip().rstrip('/')
    )
    if api_url and not api_url.startswith('http'):
        api_url = 'https://' + api_url
    payloads['config.json'] = {
        'updated_at': updated_at,
        'api_url':    api_url,
        'schema':     config.SCHEMA_VERSION,
    }

    if os.path.exists(config.TOP_FLOW_CACHE):
        try:
            with open(config.TOP_FLOW_CACHE, encoding='utf-8') as f:
                payloads['topflow.json'] = json.load(f)
        except Exception as e:
            logger.warning('[Web] 讀取 topflow 快取失敗：%s', e)
    return payloads


# ─────────── 寫本機 ───────────
def write_local(payloads):
    os.makedirs(DATA_DIR, exist_ok=True)
    for fname, data in payloads.items():
        path = os.path.join(DATA_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
        size = len(json.dumps(data, default=_json_default))
        logger.info('[Web] 寫入本機：%s（%d bytes）', path, size)


# ─────────── GitHub push（含內容比對避免無謂 commit） ───────────
def _gh_request(method, path, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['Authorization']         = f'token {config.GITHUB_TOKEN}'
    headers['Accept']                = 'application/vnd.github+json'
    headers['X-GitHub-Api-Version']  = '2022-11-28'
    return requests.request(
        method,
        f'{config.GITHUB_API}{path}',
        headers=headers, timeout=20, **kwargs,
    )


def _strip_volatile(obj):
    """遞迴移除 updated_at / queried_at，做內容比對用。"""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in ('updated_at', 'queried_at')}
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def _is_meaningful_change(old_str, new_str):
    """忽略時間戳後比對 JSON 是否實質變動。解析失敗保守視為「有變動」。"""
    try:
        return _strip_volatile(json.loads(old_str)) != _strip_volatile(json.loads(new_str))
    except Exception:
        return True


def push_file_to_github(repo_path, content_str, commit_msg):
    """PUT /repos/{owner}/{repo}/contents/{path}；內容無實質變動就跳過。"""
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO:
        return False, 'GITHUB_TOKEN / GITHUB_REPO 未設定，跳過上傳'

    api_path = f'/repos/{config.GITHUB_REPO}/contents/{repo_path}'
    sha = None
    try:
        r = _gh_request('GET', api_path, params={'ref': config.GITHUB_BRANCH})
        if r.status_code == 200:
            existing = r.json()
            sha = existing.get('sha')
            try:
                existing_content = base64.b64decode(existing.get('content', '')).decode('utf-8')
                if not _is_meaningful_change(existing_content, content_str):
                    return True, 'skipped:no-change'
            except Exception:
                pass
    except Exception as e:
        logger.warning('[Web] 取現有內容失敗（將直接 PUT）：%s', e)

    body = {
        'message': commit_msg,
        'content': base64.b64encode(content_str.encode('utf-8')).decode('ascii'),
        'branch':  config.GITHUB_BRANCH,
    }
    if sha:
        body['sha'] = sha

    r = _gh_request('PUT', api_path, json=body)
    if r.status_code in (200, 201):
        return True, r.json().get('commit', {}).get('sha', '')
    return False, (f'HTTP {r.status_code} URL={config.GITHUB_API}{api_path} '
                   f'BRANCH={config.GITHUB_BRANCH} body={r.text[:300]}')


def _diag():
    """印出當前 GitHub 設定供除錯（Token 只顯示前後 4 碼）。"""
    token_disp = ''
    if config.GITHUB_TOKEN:
        token_disp = (
            config.GITHUB_TOKEN[:8] + '...' + config.GITHUB_TOKEN[-4:]
            if len(config.GITHUB_TOKEN) > 16 else f'len={len(config.GITHUB_TOKEN)}'
        )
    logger.info('[Web] 設定 → REPO=%r BRANCH=%r TOKEN=%r (len=%d)',
                config.GITHUB_REPO, config.GITHUB_BRANCH, token_disp, len(config.GITHUB_TOKEN))
    if not config.GITHUB_TOKEN:
        return
    try:
        r = _gh_request('GET', '/user')
        if r.status_code == 200:
            logger.info('[Web] Token 有效，身份 = %s', r.json().get('login', '?'))
        else:
            logger.warning('[Web] Token /user 測試失敗：HTTP %d %s', r.status_code, r.text[:200])
    except Exception as e:
        logger.warning('[Web] Token /user 測試例外：%s', e)
    try:
        r = _gh_request('GET', f'/repos/{config.GITHUB_REPO}')
        if r.status_code == 200:
            j = r.json()
            logger.info('[Web] Repo 可存取 → full_name=%s default_branch=%s permissions=%s',
                        j.get('full_name'), j.get('default_branch'), j.get('permissions'))
        else:
            logger.warning('[Web] Repo 測試失敗：HTTP %d %s', r.status_code, r.text[:200])
    except Exception as e:
        logger.warning('[Web] Repo 測試例外：%s', e)


def push_payloads(payloads):
    """全部 payload 推到 GitHub。內容無變動會跳過避免觸發 Railway redeploy。"""
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO:
        logger.warning('[Web] 略過 GitHub 上傳（未設定 GITHUB_TOKEN/GITHUB_REPO）')
        return False

    ts = _tw_now_iso()
    upload = skip = fail = 0
    for fname, data in payloads.items():
        content   = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
        repo_path = f'docs/data/{fname}'
        ok, info  = push_file_to_github(
            repo_path, content,
            f'chore(dashboard): update {fname} ({ts})',
        )
        if ok:
            if str(info).startswith('skipped:'):
                skip += 1
                logger.info('[Web] ⏭️  %s 內容無變動，跳過 push', repo_path)
            else:
                upload += 1
                logger.info('[Web] ✅ 上傳 %s → %s', repo_path, info[:8])
        else:
            fail += 1
            logger.error('[Web] ❌ 上傳 %s 失敗：%s', repo_path, info)
    logger.info('[Web] push 總結：上傳 %d / 跳過 %d / 失敗 %d', upload, skip, fail)
    return fail == 0


# ─────────── 對外入口 ───────────
def cache_top_flow(top_flow, screen_date_str=None):
    """run_analysis 跑完把外資榜寫入 /tmp 快取，build_payloads 會撈進 topflow.json。"""
    if not top_flow:
        return
    try:
        data = {
            'updated_at':  _tw_now_iso(),
            'screen_date': screen_date_str,
            'buyers':      top_flow.get('buyers',  []),
            'sellers':     top_flow.get('sellers', []),
        }
        with open(config.TOP_FLOW_CACHE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=_json_default)
        logger.info('[Web] 外資榜快取已寫入：%s', config.TOP_FLOW_CACHE)
    except Exception as e:
        logger.warning('[Web] 寫外資榜快取失敗：%s', e)


def export_dashboard(top_flow=None, screen_date_str=None):
    """主入口：盤後篩選完成後呼叫一次。"""
    try:
        if top_flow is not None:
            cache_top_flow(top_flow, screen_date_str)
        _diag()
        payloads = build_payloads()
        write_local(payloads)
        push_payloads(payloads)
        total_count = sum(d.get('count', 0) for d in payloads.values() if isinstance(d, dict))
        logger.info('[Web] Dashboard 匯出完成，共 %d 筆', total_count)
        return True
    except Exception as e:
        logger.error('[Web] 匯出失敗：%s\n%s', e, traceback.format_exc())
        return False


if __name__ == '__main__':
    from logging_setup import setup_logging
    setup_logging()
    export_dashboard()
