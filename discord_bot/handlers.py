"""
Discord Interaction HTTP handler。所有 /xxx 指令都從這裡分派。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler

import requests as req

import config
import db
from format_utils import fmt_share, get_opt
from time_utils import get_target_date, tw_now

from discord_bot.basic_commands import cmd_fortune, cmd_help, cmd_poll, cmd_roast
from discord_bot.challenge_commands import cmd_challenge
from discord_bot.portfolio_commands import (
    cmd_buy, cmd_holding, cmd_leaderboard, cmd_sell,
)
from discord_bot.stats_commands import cmd_report, cmd_stats
from discord_bot.stock_commands import (
    analyze_stock, fetch_top_traders, stock_api_get,
)
from discord_bot.verify import verify_signature

# 由 scheduler 寫入。模組層共享，handler 在 /status 讀。
LAST_RUN = {
    'time': None, 'mode': None, 'date': None,
    'status': None, 'error': None, 'attempt': 0,
}


def _get_user(body):
    member = body.get('member', {})
    user   = member.get('user', body.get('user', {}))
    uid    = user.get('id', 'unknown')
    name   = user.get('global_name') or user.get('username', '匿名')
    return uid, name


def _get_guild(body):
    return body.get('guild_id', 'dm')


def _is_admin(body):
    """MANAGE_GUILD 權限位"""
    perms = int(body.get('member', {}).get('permissions', 0))
    return bool(perms & 0x20)


def _followup_url(token):
    return f'https://discord.com/api/v10/webhooks/{config.DISCORD_APP_ID}/{token}/messages/@original'


def _patch(token, content):
    try:
        req.patch(_followup_url(token), json={'content': content}, timeout=10)
    except Exception as e:
        print(f'[Followup] patch 失敗：{e}')


class InteractionHandler(BaseHTTPRequestHandler):
    """Discord HTTP Interaction 端點 + Web Dashboard /api/stock。"""

    def log_message(self, fmt, *args):
        pass

    # ─────────── HTTP 基礎 ───────────
    def send_json(self, code, body, cors=False):
        data = json.dumps(body, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data))
        if cors:
            self.send_header('Access-Control-Allow-Origin',  '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Cache-Control',                'public, max-age=300')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age',       '600')
        self.end_headers()

    # ─────────── GET ───────────
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u  = urlparse(self.path)
        qs = parse_qs(u.query or '')

        if u.path == '/api/stock':
            sid   = (qs.get('sid', [''])[0] or '').strip().upper()
            force = (qs.get('force', ['0'])[0] or '0').lower() in ('1', 'true', 'yes')
            if not sid or not sid.isalnum() or len(sid) > 6:
                self.send_json(400, {'ok': False,
                                     'error': '請提供有效的股票代號（最多 6 碼）'}, cors=True)
                return
            data = stock_api_get(sid, force=force)
            if data is None:
                self.send_json(404, {'ok': False,
                                     'error': f'查無 {sid} 資料（代號錯誤或 TWSE 無資料）'}, cors=True)
                return
            self.send_json(200, {'ok': True, 'data': data}, cors=True)
            return

        # health check
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    # ─────────── POST（Discord interaction） ───────────
    def do_POST(self):
        length   = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)
        sig      = self.headers.get('X-Signature-Ed25519',  '')
        ts       = self.headers.get('X-Signature-Timestamp', '')

        if not verify_signature(raw_body, sig, ts):
            self.send_response(401)
            self.end_headers()
            return

        body  = json.loads(raw_body)
        itype = body.get('type')

        if itype == 1:
            self.send_json(200, {'type': 1})
            return
        if itype != 2:
            self.send_json(200, {'type': 4, 'data': {'content': '❓ 未知指令'}})
            return

        cmd   = body['data']['name']
        opts  = body['data'].get('options', [])
        token = body['token']
        uid, uname = _get_user(body)

        if self._handle_immediate(cmd, opts, body, uid, uname):
            return
        if self._handle_holding(cmd, opts, body, uid, uname, token):
            return
        if self._handle_setup(cmd, opts, body, uid):
            return
        if self._handle_async_stats(cmd, body, token):
            return
        if self._handle_deferred(cmd, opts, body, uid, uname, token):
            return

        self.send_json(200, {'type': 4, 'data': {'content': '❓ 未知指令'}})

    # ─────────── 各組指令 ───────────
    def _handle_immediate(self, cmd, opts, body, uid, uname):
        """立即回覆的簡單指令。返回 True 表示已處理。"""
        if cmd == 'help':
            self.send_json(200, {'type': 4, 'data': {'content': cmd_help()}})
            return True
        if cmd == 'status':
            content = self._format_status()
            self.send_json(200, {'type': 4, 'data': {'content': content}})
            return True
        if cmd == 'fortune':
            self.send_json(200, {'type': 4, 'data': {'content': cmd_fortune()}})
            return True
        if cmd == 'roast':
            self.send_json(200, {'type': 4, 'data': {'content': cmd_roast()}})
            return True
        if cmd == 'buy':
            sid, price, lots = get_opt(opts, 'code'), get_opt(opts, 'price'), get_opt(opts, 'lots')
            self.send_json(200, {'type': 4, 'data': {
                'content': cmd_buy(uid, uname, sid, price, lots, _get_guild(body))}})
            return True
        if cmd == 'sell':
            sid, price, lots = get_opt(opts, 'code'), get_opt(opts, 'price'), get_opt(opts, 'lots')
            self.send_json(200, {'type': 4, 'data': {
                'content': cmd_sell(uid, uname, sid, price, lots, _get_guild(body))}})
            return True
        if cmd == 'leaderboard':
            self.send_json(200, {'type': 4, 'data': {
                'content': cmd_leaderboard(_get_guild(body))}})
            return True
        if cmd == 'poll':
            q  = get_opt(opts, 'question')
            o1 = get_opt(opts, 'option1')
            o2 = get_opt(opts, 'option2')
            self.send_json(200, {'type': 4, 'data': {'content': cmd_poll(q, o1, o2)}})
            return True
        return False

    def _format_status(self):
        if LAST_RUN['time'] is None:
            return 'ℹ️ 尚未執行過任何分析。'
        tw  = LAST_RUN['time'].strftime('%Y/%m/%d %H:%M')
        emo = {'success': '✅', 'running': '⏳', 'error': '❌'}.get(LAST_RUN['status'], '❓')
        ml  = {'auto': '自動判斷', 'close': '盤後結算',
               'preview': '盤前複習'}.get(LAST_RUN['mode'], '')
        content = (f"{emo} **上次執行紀錄**\n🕐 {tw}（台灣時間）\n📋 {ml}\n"
                   f"📅 {LAST_RUN['date']}\n📊 {LAST_RUN['status']}")
        if LAST_RUN['error']:
            content += f"\n⚠️ {LAST_RUN['error']}"
        return content

    def _handle_holding(self, cmd, opts, body, uid, uname, token):
        if cmd != 'holding':
            return False
        self.send_json(200, {'type': 5})

        target_opt = get_opt(opts, 'target')
        h_uid, h_name = uid, uname
        if target_opt:
            resolved = body['data'].get('resolved', {})
            users    = resolved.get('users', {})
            tid      = str(target_opt)
            if tid in users:
                u      = users[tid]
                h_uid  = tid
                h_name = (u.get('global_name') or u.get('username', f'用戶{tid}'))
            else:
                h_uid  = tid
                h_name = f'用戶{tid}'
        guild_id = _get_guild(body)
        label    = f'**{h_name}**' if h_uid != uid else '你'

        def _bg():
            _patch(token, f'💼 正在查詢 {label} 的持倉...')
            try:
                _patch(token, cmd_holding(h_uid, h_name, guild_id))
            except Exception as e:
                _patch(token, f'❌ 查詢失敗：{e}')

        threading.Thread(target=_bg, daemon=True).start()
        return True

    def _handle_setup(self, cmd, opts, body, uid):
        if cmd != 'setup':
            return False
        if not _is_admin(body):
            self.send_json(200, {'type': 4, 'data': {
                'content': '❌ 只有伺服器管理員才能使用 `/setup`。',
                'flags': 64}})
            return True

        webhook_input = (get_opt(opts, 'webhook', '') or '').strip()
        guild_id      = _get_guild(body)
        if webhook_input.lower() == 'remove':
            try:
                db.remove_guild(guild_id)
            except Exception as e:
                print(f'[setup] remove 失敗：{e}')
            self.send_json(200, {'type': 4, 'data': {
                'content': '✅ 已移除本伺服器的推播設定。', 'flags': 64}})
            return True

        try:
            test_msg = ('✅ **川投顧量化系統** 設定成功！\n'
                        '每日 17:00 盤後分析、07:00 盤前複習將自動推播至此頻道。')
            r = req.post(webhook_input, json={'content': test_msg}, timeout=10)
            if r.status_code in (200, 204):
                db.set_guild_webhook(guild_id, webhook_input, uid)
                self.send_json(200, {'type': 4, 'data': {
                    'content': '✅ 設定成功！測試訊息已發送至目標頻道。', 'flags': 64}})
            else:
                self.send_json(200, {'type': 4, 'data': {
                    'content': f'❌ Webhook 測試失敗（{r.status_code}），請確認 URL 是否正確。',
                    'flags': 64}})
        except Exception as e:
            self.send_json(200, {'type': 4, 'data': {
                'content': f'❌ 無法連接 Webhook：{e}', 'flags': 64}})
        return True

    def _handle_async_stats(self, cmd, body, token):
        if cmd not in ('report', 'stats'):
            return False
        self.send_json(200, {'type': 5})
        guild_id = _get_guild(body)
        fn = cmd_report if cmd == 'report' else cmd_stats
        empty_msg = 'ℹ️ 尚無結算記錄。' if cmd == 'report' else 'ℹ️ 尚無統計資料。'

        def _bg():
            try:
                result = fn(guild_id)
            except Exception as e:
                result = f'❌ 查詢失敗：{e}'
            _patch(token, result or empty_msg)

        threading.Thread(target=_bg, daemon=True).start()
        return True

    def _handle_deferred(self, cmd, opts, body, uid, uname, token):
        """需要打 TWSE / 跑分析的指令，先送 type=5 再背景處理。"""
        if cmd not in ('run', 'stock', 'topbuyer', 'topseller', 'challenge'):
            return False
        self.send_json(200, {'type': 5})

        def _bg():
            if cmd == 'run':
                self._bg_run(opts, token)
            elif cmd == 'stock':
                sid = get_opt(opts, 'code', '')
                _patch(token, f'🔍 正在分析 **{sid}**...')
                result = analyze_stock(sid)
                _patch(token, result or f'❌ 無法取得 {sid} 的資料，請確認代號是否正確。')
            elif cmd in ('topbuyer', 'topseller'):
                self._bg_top(cmd, token)
            elif cmd == 'challenge':
                sid = get_opt(opts, 'code', '')
                _patch(token, f'⚔️ 正在查詢 {sid} 最新股價...')
                _patch(token, cmd_challenge(uid, uname, sid, _get_guild(body)))

        threading.Thread(target=_bg, daemon=True).start()
        return True

    def _bg_run(self, opts, token):
        from analysis import run_analysis
        mode = get_opt(opts, 'mode', 'auto')
        ml   = {'auto': '自動判斷', 'close': '盤後結算',
                'preview': '盤前複習'}.get(mode, mode)
        _patch(token, f'⏳ 正在執行 **{ml}** 分析，約需 3–5 分鐘...')
        LAST_RUN.update({
            'time': tw_now(), 'mode': mode,
            'date': get_target_date(mode),
            'status': 'running', 'error': None,
        })
        try:
            status = run_analysis(run_mode=mode) or 'success'
            LAST_RUN['status'] = status
            if status == 'success':
                _patch(token, f'✅ **{ml}** 分析完成，結果已發送至頻道。')
            elif status == 'holiday':
                _patch(token, f'ℹ️ {LAST_RUN["date"]} 為國定假日或 TWSE 尚未更新資料，已跳過分析。')
            else:
                _patch(token, f'❌ 執行失敗（status={status}），請查看 log。')
        except Exception as e:
            LAST_RUN['status'] = 'error'
            LAST_RUN['error']  = str(e)
            _patch(token, f'❌ 執行失敗：{e}')

    def _bg_top(self, cmd, token):
        top_type = 'buy' if cmd == 'topbuyer' else 'sell'
        label    = '買超' if top_type == 'buy' else '賣超'
        emoji    = '📈'   if top_type == 'buy' else '📉'
        _patch(token, f'{emoji} 正在抓取外資{label}資料...')
        data, date_str = fetch_top_traders(top_type)
        if data is None:
            _patch(token, f'❌ 無法取得資料（{date_str}），可能尚未更新。')
            return
        lines = [f'{emoji} **今日外資{label}前 {len(data)} 名**（{date_str}）\n']
        for i, e in enumerate(data, 1):
            sign = '+' if e['foreign'] >= 0 else ''
            lines.append(f'**{i}.** {e["sid"]} {e["name"]}　外資 {sign}{fmt_share(e["foreign"])} 股')
        _patch(token, '\n'.join(lines))
