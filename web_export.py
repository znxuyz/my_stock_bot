"""
Web Dashboard 匯出模組
======================
盤後篩選完成後將彙總資料寫成 JSON，並透過 GitHub API 推回 repo 的 docs/data/
GitHub Pages 從 docs/ 目錄 deploy，前端 fetch ./data/*.json 即可顯示。

環境變數：
    GITHUB_TOKEN  - GitHub PAT，需要 contents:write 權限
    GITHUB_REPO   - 例如 'znxuyz/my_stock_bot'
    GITHUB_BRANCH - 預設 'main'
若任一未設定則只在本機寫檔（除錯用），不推到 GitHub。
"""
import os
import json
import base64
import traceback
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal

import requests

try:
    import db as _db
    _DB_OK = True
except Exception as _e:
    _DB_OK = False
    print(f'[Web] 無法載入 db 模組：{_e}')

DOCS_DIR  = os.path.join(os.path.dirname(__file__), 'docs')
DATA_DIR  = os.path.join(DOCS_DIR, 'data')
TOP_FLOW_CACHE = '/tmp/stockbot_topflow_cache.json'

GITHUB_API     = 'https://api.github.com'
GITHUB_TOKEN   = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO    = os.environ.get('GITHUB_REPO', 'znxuyz/my_stock_bot')
GITHUB_BRANCH  = os.environ.get('GITHUB_BRANCH', 'main')


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


def build_payloads():
    """從 DB 撈所有需要的資料，組成 JSON payload dict"""
    if not _DB_OK:
        raise RuntimeError('DB 模組未載入')

    latest_date = _db.get_latest_screen_date()
    today_rows  = []
    if latest_date:
        for r in _db.get_screens_by_date(latest_date):
            today_rows.append(_row_to_dict(r))

    history_rows = [_row_to_dict(r) for r in _db.get_history_records(limit_days=90)]

    stats   = _db.get_aggregated_stats()
    summary = _db.get_aggregated_summary()

    stats_clean = {
        'grade':   [_row_to_dict(r) for r in stats.get('grade',   [])],
        'bias':    [_row_to_dict(r) for r in stats.get('bias',    [])],
        'monthly': [_row_to_dict(r) for r in stats.get('monthly', [])],
    }
    summary_clean = _row_to_dict(summary) if summary else {}

    updated_at = _tw_now_iso()
    payloads = {
        'today.json': {
            'updated_at':  updated_at,
            'screen_date': latest_date.isoformat() if latest_date else None,
            'count':       len(today_rows),
            'records':     today_rows,
        },
        'stats.json': {
            'updated_at': updated_at,
            'summary':    summary_clean,
            'by_grade':   stats_clean['grade'],
            'by_bias':    stats_clean['bias'],
            'by_month':   stats_clean['monthly'],
        },
        'history.json': {
            'updated_at': updated_at,
            'count':      len(history_rows),
            'records':    history_rows,
        },
    }

    # 外資買賣超榜（從 /tmp 快取讀；run_analysis 會在跑分析時寫入）
    if os.path.exists(TOP_FLOW_CACHE):
        try:
            with open(TOP_FLOW_CACHE, encoding='utf-8') as f:
                payloads['topflow.json'] = json.load(f)
        except Exception as e:
            print(f'[Web] 讀取 topflow 快取失敗：{e}')

    return payloads


def write_local(payloads):
    """寫到 docs/data/ 本機目錄"""
    os.makedirs(DATA_DIR, exist_ok=True)
    for fname, data in payloads.items():
        path = os.path.join(DATA_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
        print(f'[Web] 寫入本機：{path}（{len(json.dumps(data, default=_json_default))} bytes）')


def _gh_request(method, path, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = f'token {GITHUB_TOKEN}'
    headers['Accept']        = 'application/vnd.github+json'
    headers['X-GitHub-Api-Version'] = '2022-11-28'
    return requests.request(method, f'{GITHUB_API}{path}', headers=headers, timeout=20, **kwargs)


def push_file_to_github(repo_path, content_str, commit_msg):
    """PUT /repos/{owner}/{repo}/contents/{path}"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, 'GITHUB_TOKEN / GITHUB_REPO 未設定，跳過上傳'

    api_path = f'/repos/{GITHUB_REPO}/contents/{repo_path}'
    full_url = f'{GITHUB_API}{api_path}'
    # 先查 sha（更新需要）
    sha = None
    try:
        r = _gh_request('GET', api_path, params={'ref': GITHUB_BRANCH})
        if r.status_code == 200:
            sha = r.json().get('sha')
    except Exception:
        pass

    body = {
        'message': commit_msg,
        'content': base64.b64encode(content_str.encode('utf-8')).decode('ascii'),
        'branch':  GITHUB_BRANCH,
    }
    if sha:
        body['sha'] = sha

    r = _gh_request('PUT', api_path, json=body)
    if r.status_code in (200, 201):
        return True, r.json().get('commit', {}).get('sha', '')
    return False, f'HTTP {r.status_code} URL={full_url} BRANCH={GITHUB_BRANCH} body={r.text[:300]}'


def _diag():
    """印出當前 GitHub 設定供 debug（Token 只顯示前後 4 碼）"""
    token_disp = ''
    if GITHUB_TOKEN:
        token_disp = (GITHUB_TOKEN[:8] + '...' + GITHUB_TOKEN[-4:]
                      if len(GITHUB_TOKEN) > 16 else f'len={len(GITHUB_TOKEN)}')
    print(f'[Web] 設定 → REPO={GITHUB_REPO!r} BRANCH={GITHUB_BRANCH!r} '
          f'TOKEN={token_disp!r} (len={len(GITHUB_TOKEN)})')
    if not GITHUB_TOKEN:
        return
    # 試打 /user 確認 token 有效、認證身份
    try:
        r = _gh_request('GET', '/user')
        if r.status_code == 200:
            who = r.json().get('login', '?')
            print(f'[Web] Token 有效，身份 = {who}')
        else:
            print(f'[Web] Token /user 測試失敗：HTTP {r.status_code} {r.text[:200]}')
    except Exception as e:
        print(f'[Web] Token /user 測試例外：{e}')
    # 試打 /repos/{REPO} 確認 token 看得到此 repo
    try:
        r = _gh_request('GET', f'/repos/{GITHUB_REPO}')
        if r.status_code == 200:
            j = r.json()
            print(f'[Web] Repo 可存取 → full_name={j.get("full_name")} '
                  f'default_branch={j.get("default_branch")} '
                  f'permissions={j.get("permissions")}')
        else:
            print(f'[Web] Repo 測試失敗：HTTP {r.status_code} {r.text[:200]}')
    except Exception as e:
        print(f'[Web] Repo 測試例外：{e}')


def push_payloads(payloads):
    """把所有 payload 推到 GitHub"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print('[Web] 略過 GitHub 上傳（未設定 GITHUB_TOKEN/GITHUB_REPO）')
        return False

    ts = _tw_now_iso()
    ok_count = 0
    for fname, data in payloads.items():
        content = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
        repo_path = f'docs/data/{fname}'
        ok, info = push_file_to_github(repo_path, content,
                                       f'chore(dashboard): update {fname} ({ts})')
        if ok:
            ok_count += 1
            print(f'[Web] ✅ 上傳 {repo_path} → {info[:8]}')
        else:
            print(f'[Web] ❌ 上傳 {repo_path} 失敗：{info}')
    return ok_count == len(payloads)


def cache_top_flow(top_flow, screen_date_str=None):
    """把外資買賣超 Top 10 寫進 /tmp 快取，下次 build_payloads 會讀進 topflow.json"""
    if not top_flow:
        return
    try:
        data = {
            'updated_at':  _tw_now_iso(),
            'screen_date': screen_date_str,
            'buyers':      top_flow.get('buyers', []),
            'sellers':     top_flow.get('sellers', []),
        }
        with open(TOP_FLOW_CACHE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=_json_default)
        print(f'[Web] 外資榜快取已寫入：{TOP_FLOW_CACHE}')
    except Exception as e:
        print(f'[Web] 寫外資榜快取失敗：{e}')


def export_dashboard(top_flow=None, screen_date_str=None):
    """主入口：盤後篩選完成後呼叫一次。若提供 top_flow 會一併輸出 topflow.json"""
    try:
        if top_flow is not None:
            cache_top_flow(top_flow, screen_date_str)
        _diag()
        payloads = build_payloads()
        write_local(payloads)
        push_payloads(payloads)
        print(f'[Web] Dashboard 匯出完成，共 {sum(d.get("count", 0) for d in payloads.values() if isinstance(d, dict))} 筆')
        return True
    except Exception as e:
        print(f'[Web] 匯出失敗：{e}\n{traceback.format_exc()}')
        return False


if __name__ == '__main__':
    export_dashboard()
