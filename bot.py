"""
川投顧量化系統 Discord Bot
==========================
功能：
  /run [模式]  手動觸發選股分析（close=盤後 / preview=盤前 / auto=自動）
  /status      查看上次執行時間與結果摘要

部署需要的環境變數：
  DISCORD_BOT_TOKEN   Bot Token
  DISCORD_PUBLIC_KEY  公開金鑰
  DISCORD_WEBHOOK     發送結果的 Webhook URL（沿用原本的）
  DISCORD_APP_ID      應用程式 ID
"""

import os, sys, json, hmac, hashlib, threading, time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from stock_bot import run_analysis, get_target_date

BOT_TOKEN  = os.environ.get('DISCORD_BOT_TOKEN', '')
PUBLIC_KEY = os.environ.get('DISCORD_PUBLIC_KEY', '')
APP_ID     = os.environ.get('DISCORD_APP_ID', '')
PORT       = int(os.environ.get('PORT', 8080))

last_run = {
    'time':   None,
    'mode':   None,
    'date':   None,
    'status': None,
    'error':  None,
}

# ════════════════════════════════════════════════════════════
# 簽章驗證
# ════════════════════════════════════════════════════════════
def verify_signature(raw_body: bytes, signature: str, timestamp: str) -> bool:
    try:
        message  = timestamp.encode() + raw_body
        expected = hmac.new(
            bytes.fromhex(PUBLIC_KEY),
            message,
            digestmod=hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False

# ════════════════════════════════════════════════════════════
# 註冊 Slash Commands（啟動時執行一次）
# ════════════════════════════════════════════════════════════
def register_commands():
    import requests as req
    url = f'https://discord.com/api/v10/applications/{APP_ID}/commands'
    headers = {
        'Authorization': f'Bot {BOT_TOKEN}',
        'Content-Type':  'application/json',
    }
    commands = [
        {
            'name': 'run',
            'description': '手動觸發選股分析',
            'options': [
                {
                    'name': 'mode',
                    'description': '執行模式（預設 auto）',
                    'type': 3,
                    'required': False,
                    'choices': [
                        {'name': '自動判斷（auto）',  'value': 'auto'},
                        {'name': '盤後結算（close）', 'value': 'close'},
                        {'name': '盤前複習（preview）', 'value': 'preview'},
                    ],
                }
            ],
        },
        {
            'name': 'status',
            'description': '查看上次執行時間與狀態',
        },
    ]
    r = req.put(url, headers=headers, json=commands, timeout=10)
    if r.status_code in (200, 201):
        print("[Bot] Slash Commands 註冊成功")
    else:
        print(f"[Bot] Slash Commands 註冊失敗：{r.status_code} {r.text[:200]}")

# ════════════════════════════════════════════════════════════
# HTTP Server
# ════════════════════════════════════════════════════════════
class InteractionHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length   = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)
        sig      = self.headers.get('X-Signature-Ed25519', '')
        ts       = self.headers.get('X-Signature-Timestamp', '')

        if not verify_signature(raw_body, sig, ts):
            self.send_response(401)
            self.end_headers()
            return

        body  = json.loads(raw_body)
        itype = body.get('type')

        # PING
        if itype == 1:
            self.send_json(200, {'type': 1})
            return

        # APPLICATION_COMMAND
        if itype == 2:
            cmd   = body['data']['name']
            token = body['token']

            if cmd == 'run':
                options    = body['data'].get('options', [])
                mode       = next((o['value'] for o in options if o['name'] == 'mode'), 'auto')
                mode_label = {'auto': '自動判斷', 'close': '盤後結算', 'preview': '盤前複習'}.get(mode, mode)

                # 立刻回傳 DEFERRED（顯示「正在思考...」避免逾時）
                self.send_json(200, {'type': 5})

                def run_bg():
                    import requests as req
                    global last_run
                    last_run.update({
                        'time':   datetime.utcnow() + timedelta(hours=8),
                        'mode':   mode,
                        'date':   get_target_date(mode),
                        'status': 'running',
                        'error':  None,
                    })

                    followup = f'https://discord.com/api/v10/webhooks/{APP_ID}/{token}/messages/@original'

                    req.patch(followup, json={
                        'content': f'⏳ 正在執行 **{mode_label}** 分析，約需 3–5 分鐘...'
                    }, timeout=10)

                    try:
                        os.environ['RUN_MODE'] = mode
                        run_analysis()
                        last_run['status'] = 'success'
                        req.patch(followup, json={
                            'content': f'✅ **{mode_label}** 分析完成，結果已發送至頻道。'
                        }, timeout=10)
                    except Exception as e:
                        last_run['status'] = 'error'
                        last_run['error']  = str(e)
                        req.patch(followup, json={
                            'content': f'❌ 執行失敗：{e}'
                        }, timeout=10)

                threading.Thread(target=run_bg, daemon=True).start()
                return

            if cmd == 'status':
                if last_run['time'] is None:
                    content = 'ℹ️ 尚未執行過任何分析。'
                else:
                    tw  = last_run['time'].strftime('%Y/%m/%d %H:%M')
                    emo = {'success': '✅', 'running': '⏳', 'error': '❌'}.get(last_run['status'], '❓')
                    ml  = {'auto': '自動判斷', 'close': '盤後結算', 'preview': '盤前複習'}.get(last_run['mode'], '')
                    content = (
                        f"{emo} **上次執行紀錄**\n"
                        f"🕐 時間：{tw}（台灣時間）\n"
                        f"📋 模式：{ml}\n"
                        f"📅 分析日期：{last_run['date']}\n"
                        f"📊 狀態：{last_run['status']}"
                    )
                    if last_run['error']:
                        content += f"\n⚠️ 錯誤：{last_run['error']}"

                self.send_json(200, {'type': 4, 'data': {'content': content}})
                return

            self.send_json(200, {'type': 4, 'data': {'content': '❓ 未知的指令'}})

# ════════════════════════════════════════════════════════════
# 內建排程（取代 GitHub Actions）
# ════════════════════════════════════════════════════════════
def scheduler():
    fired = set()
    while True:
        now = datetime.utcnow() + timedelta(hours=8)
        h, wd, mn = now.hour, now.weekday(), now.minute

        # 盤後：週一~週五 16:00
        if wd < 5 and h == 16 and mn == 0:
            key = (now.date(), 'close')
            if key not in fired:
                fired.add(key)
                print(f"[排程] 盤後 {now.strftime('%Y-%m-%d %H:%M')}")
                os.environ['RUN_MODE'] = 'auto'
                threading.Thread(target=run_analysis, daemon=True).start()

        # 盤前：週二~週六 06:00
        if 0 < wd <= 5 and h == 6 and mn == 0:
            key = (now.date(), 'preview')
            if key not in fired:
                fired.add(key)
                print(f"[排程] 盤前 {now.strftime('%Y-%m-%d %H:%M')}")
                os.environ['RUN_MODE'] = 'auto'
                threading.Thread(target=run_analysis, daemon=True).start()

        # 每天 00:01 清除舊紀錄（保留近7天）
        if h == 0 and mn == 1:
            cutoff = now.date() - timedelta(days=7)
            fired  = {k for k in fired if k[0] >= cutoff}

        time.sleep(60)

# ════════════════════════════════════════════════════════════
# 啟動
# ════════════════════════════════════════════════════════════
if __name__ == '__main__':
    missing = [k for k, v in {
        'DISCORD_BOT_TOKEN':  BOT_TOKEN,
        'DISCORD_PUBLIC_KEY': PUBLIC_KEY,
        'DISCORD_APP_ID':     APP_ID,
    }.items() if not v]

    if missing:
        print(f"[錯誤] 缺少環境變數：{', '.join(missing)}")
        sys.exit(1)

    register_commands()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"[Bot] 已啟動，監聽 port {PORT}")
    HTTPServer(('0.0.0.0', PORT), InteractionHandler).serve_forever()
