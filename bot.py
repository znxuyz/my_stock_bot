"""
川投顧量化系統 Discord Bot ── 完整版
指令：/help /run /status /stock /fortune /roast
      /topbuyer /topseller /holding /buy /sell
      /poll /leaderboard /challenge
"""
import os, sys, json, threading, time, random, io
from datetime import datetime, timedelta, date
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
import stock_bot as sb

BOT_TOKEN  = os.environ.get('DISCORD_BOT_TOKEN', '')
PUBLIC_KEY = os.environ.get('DISCORD_PUBLIC_KEY', '')
APP_ID     = os.environ.get('DISCORD_APP_ID', '')
PORT       = int(os.environ.get('PORT', 8080))

# ══════════════════════════════════════════════════════
# 資料儲存
# ══════════════════════════════════════════════════════
DATA_FILE = '/tmp/stockbot_data.json'

def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {'holdings': {}, 'trades': {}, 'pnl': {}, 'challenges': {}}

def save_data(d):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except:
        pass

db       = load_data()
last_run = {'time': None, 'mode': None, 'date': None, 'status': None, 'error': None}

# ══════════════════════════════════════════════════════
# Ed25519 驗證
# ══════════════════════════════════════════════════════
def verify_signature(raw_body, signature, timestamp):
    try:
        from nacl.signing import VerifyKey
        VerifyKey(bytes.fromhex(PUBLIC_KEY)).verify(
            timestamp.encode() + raw_body, bytes.fromhex(signature))
        return True
    except:
        return False

# ══════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════
def get_opt(options, name, default=None):
    return next((o['value'] for o in options if o['name'] == name), default)

def star_str(n):
    n = max(0, min(5, round(float(n))))
    return '★' * n + '☆' * (5 - n) + f'（{n}/5）'

def fmt_share(n):
    n = int(n)
    if abs(n) >= 1000000:
        return f'{n/1000000:.1f}M'
    if abs(n) >= 1000:
        return f'{n/1000:.0f}K'
    return str(n)

def tw_now():
    return datetime.utcnow() + timedelta(hours=8)

def get_user(body):
    member = body.get('member', {})
    user   = member.get('user', body.get('user', {}))
    uid    = user.get('id', 'unknown')
    name   = user.get('global_name') or user.get('username', '匿名')
    return uid, name

# ══════════════════════════════════════════════════════
# /stock 個股分析
# ══════════════════════════════════════════════════════
def analyze_stock(sid):
    import pandas as pd
    sid = sid.strip().upper()
    frames = []
    d = tw_now().date().replace(day=1)
    for _ in range(7):
        ym = d.strftime('%Y%m')
        r  = sb.safe_get(
            'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
            params={'response': 'csv', 'date': ym + '01', 'stockNo': sid},
            timeout=15, retries=2, wait=5
        )
        if r and '查詢無資料' not in r.text:
            try:
                text = r.text
                idx  = text.find('"日期"')
                if idx == -1:
                    for yr in ['115/', '114/', '113/']:
                        idx = text.find(yr)
                        if idx != -1:
                            idx = text.rfind('\n', 0, idx) + 1
                            break
                if idx != -1:
                    df = sb.safe_read_csv(text[idx:], f'SD-{sid}', min_cols=7)
                    df = df[df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)].copy()
                    if not df.empty:
                        def r2d(s):
                            y, m, dd = str(s).split('/')
                            return date(int(y)+1911, int(m), int(dd))
                        df['date']   = df.iloc[:, 0].apply(r2d)
                        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',',''), errors='coerce')
                        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',',''), errors='coerce')
                        frames.append(df[['date','close','volume']].dropna())
            except:
                pass
        d = (d - timedelta(days=1)).replace(day=1)
        time.sleep(0.2)

    if not frames:
        return None

    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    if len(df_all) < 2:
        return None

    latest = df_all.iloc[-1]
    prev   = df_all.iloc[-2]
    price  = latest['close']
    diff   = price - prev['close']
    change = round(diff / prev['close'] * 100, 2) if prev['close'] else 0.0

    vol_ratio        = sb.calc_volume_ratio(df_all, latest['date'])
    is_bull, ema_mode = sb.check_ema_bull(df_all)

    # 星級
    stars = 0.0
    if is_bull:
        stars += 1.5 if ema_mode == 'full' else 1.0
    if vol_ratio >= 2.0: stars += 1.0
    elif vol_ratio >= 1.5: stars += 0.5
    if change >= 3.5:   stars += 1.0
    elif change >= 1.0: stars += 0.5
    elif change < -1.0: stars -= 0.5

    foreign = trust = None
    try:
        date_str = sb.get_target_date('auto')
        r86 = sb.safe_get(
            'https://www.twse.com.tw/rwd/zh/fund/T86',
            params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
            timeout=15, retries=1, wait=5
        )
        if r86 and '查詢無資料' not in r86.text:
            df_i = sb.parse_t86(r86.text)
            if not df_i.empty and '_foreign' in df_i.columns:
                row = df_i[df_i['sid_clean'] == sid]
                if not row.empty:
                    foreign = int(row['_foreign'].values[0])
                    trust   = int(row['_trust'].values[0])
                    if foreign > 100000:   stars += 1.0
                    elif foreign > 50000:  stars += 0.5
                    elif foreign > 0:      stars += 0.25
                    if trust > 50000:      stars += 0.5
                    elif trust > 10000:    stars += 0.25
    except:
        pass

    stars = max(0, min(5, stars))

    # 分析文字
    trend  = '多頭排列' if is_bull else '非多頭'
    ema_lv = '（20>60>120）' if ema_mode == 'full' else '（10>20>60）' if ema_mode == 'fallback' else '（資料不足）'
    if stars >= 4:   rec = '強力推薦，法人站台、趨勢向上，可追蹤布局。'
    elif stars >= 3: rec = '條件不錯，但需確認大盤配合，可小量觀察。'
    elif stars >= 2: rec = '訊號普通，建議等待更明確突破再進場。'
    else:            rec = '條件偏弱，暫時觀望，等待法人明確進場。'

    sign = '+' if diff >= 0 else ''
    msg  = (
        f'🔍 **{sid}**\n'
        f'💰 收盤價：**{price:,.1f} 元**　{sign}{diff:.1f}（{sign}{change}%）\n'
        f'📊 量比：**{vol_ratio}x**\n'
        f'📉 EMA 排列：{trend} {ema_lv}\n'
    )
    if foreign is not None:
        msg += f'👥 外資：{fmt_share(foreign)} 股　投信：{fmt_share(trust)} 股\n'
    msg += (
        f'\n⭐ **推薦度：{star_str(stars)}**\n'
        f'📝 {rec}'
    )
    return msg

# ══════════════════════════════════════════════════════
# /topbuyer /topseller
# ══════════════════════════════════════════════════════
def fetch_top_traders(top_type='buy', n=10):
    date_str = sb.get_target_date('auto')
    r = sb.safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=30, retries=3, wait=10
    )
    if r is None or '查詢無資料' in r.text:
        return None, date_str
    df = sb.parse_t86(r.text)
    if df.empty or '_foreign' not in df.columns:
        return None, date_str

    name_col = df.columns[1]
    if top_type == 'buy':
        rows = df[df['_foreign'] > 0].sort_values('_foreign', ascending=False).head(n)
    else:
        rows = df[df['_foreign'] < 0].sort_values('_foreign', ascending=True).head(n)

    result = []
    for _, row in rows.iterrows():
        result.append({
            'sid':     row['sid_clean'],
            'name':    str(row[name_col]).strip(),
            'foreign': int(row['_foreign']),
            'trust':   int(row['_trust']),
        })
    return result, date_str

# ══════════════════════════════════════════════════════
# 趣味素材
# ══════════════════════════════════════════════════════
FORTUNES = [
    '今日宜觀望，外資在暗處偷看你的帳戶。',
    '量比爆表，法人正在慶生，你只是被邀請買單的那個。',
    '今日犯小人，小人名叫聯準會。',
    '財運亨通，但只限台積電股東。',
    '今日宜抄底，忌追高，抄底忌比你先到的人更早。',
    'EMA 多頭排列，是真多頭還是假突破，各位自求多福。',
    '今日大吉，宜買股，忌看損益。',
    '外資今日大買，但他們明天可能就跑了。',
    '本日運勢：等等，讓我先問問法人。',
    '你的持股今日將面臨考驗，考驗就是：你敢不敢繼續持有。',
    '今日偏財運旺，但股市不是賭場，嗯，差不多是啦。',
    '投信今日發功，請跟緊，但別問為什麼要跟。',
    '今日宜換股，忌抱怨為何沒早點換。',
    '大盤今日考驗你的意志，意志不夠就先去喝個珍奶。',
]

ROASTS = [
    '台股今天的表現，我必須說，非常非常棒。我認識很多人，他們都說這是有史以來最好的漲幅，相信我。',
    '外資賣超？沒問題，他們不懂台股的偉大。我們的股市是最強的，沒有之一。',
    '今天的大盤，有人說很糟糕，但我不這樣認為。這只是短暫回調，很快就會回來，非常快。',
    '法人今天買超的股票，我早就說過了，早就說過，沒有人比我更早看出來。',
    '市場下跌？這是非常好的買入機會。我的股票從不跌，好吧，偶爾跌，但馬上漲回來，相信我。',
    '今天有些人說台股很弱，這些人是失敗者，Loser。台股很強，我從小就知道台股很強。',
    '我剛看了一下法人數據，結果非常驚人，非常，我從來沒看過這麼驚人的數據，但我不能說是好還是壞。',
    '有人問我台股明天漲還是跌？我說：會大漲。為什麼？因為我說了算。',
]

# ══════════════════════════════════════════════════════
# 指令處理器
# ══════════════════════════════════════════════════════
def cmd_help():
    return (
        '📋 **川投顧量化系統 ── 指令說明**\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🚀 `/run [模式]` 手動觸發選股分析（盤後/盤前/自動）\n'
        '📊 `/status` 查看上次執行時間與狀態\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🔍 `/stock [代號]` 個股技術分析 + 0–5 星推薦度\n'
        '📈 `/topbuyer` 今日外資買超前 10 名\n'
        '📉 `/topseller` 今日外資賣超前 10 名\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '💼 `/holding` 查看你的持倉紀錄\n'
        '🛒 `/buy [代號] [價格] [張數]` 記錄買入\n'
        '💸 `/sell [代號] [價格] [張數]` 記錄賣出並計算損益\n'
        '🏆 `/leaderboard` 伺服器損益排行榜\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🗳️ `/poll [題目] [選項1] [選項2]` 發起投票\n'
        '⚔️ `/challenge [代號]` 發起選股挑戰，一週後比誰獲利高\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🔮 `/fortune` 今日股市運勢\n'
        '🗣️ `/roast` 川投顧用川普語氣評論大盤\n'
    )

def cmd_fortune():
    emoji = random.choice(['🔮','🎱','🧧','🀄','🎰'])
    return f'{emoji} **今日股市運勢**\n\n{random.choice(FORTUNES)}'

def cmd_roast():
    return f'🗣️ **川投顧語錄**\n\n"{random.choice(ROASTS)}"'

def cmd_holding(uid, uname):
    holdings = db['holdings'].get(uid, [])
    if not holdings:
        return f'💼 **{uname} 的持倉**\n\n目前尚無持倉記錄。使用 `/buy` 記錄買入。'
    lines = [f'💼 **{uname} 的持倉**\n']
    total_cost = 0
    for h in holdings:
        cost = h['price'] * h['lots'] * 1000
        total_cost += cost
        lines.append(
            f'**{h["sid"]}**　{h["price"]} 元 × {h["lots"]} 張'
            f'　成本 {cost:,.0f} 元　（{h["date"]}）'
        )
    lines.append(f'\n總成本：**{total_cost:,.0f} 元**')
    return '\n'.join(lines)

def cmd_buy(uid, uname, sid, price, lots):
    sid = sid.strip().upper()
    entry = {'sid': sid, 'price': price, 'lots': lots,
             'date': tw_now().strftime('%Y/%m/%d')}
    if uid not in db['holdings']:
        db['holdings'][uid] = []
    db['holdings'][uid].append(entry)
    if uid not in db['trades']:
        db['trades'][uid] = []
    db['trades'][uid].append({**entry, 'action': 'buy'})
    save_data(db)
    cost = price * lots * 1000
    return (
        f'🛒 **買入記錄成功**\n'
        f'股票：{sid}　價格：{price} 元　張數：{lots} 張\n'
        f'投入金額：**{cost:,.0f} 元**'
    )

def cmd_sell(uid, uname, sid, price, lots):
    sid      = sid.strip().upper()
    holdings = db['holdings'].get(uid, [])
    owned    = [h for h in holdings if h['sid'] == sid]
    if not owned:
        return f'❌ 你的持倉中沒有 {sid}，請先用 `/buy` 記錄買入。'

    # FIFO 計算損益
    sold_lots = lots
    realized  = 0.0
    remaining = []
    for h in holdings:
        if h['sid'] != sid or sold_lots <= 0:
            remaining.append(h)
            continue
        take = min(sold_lots, h['lots'])
        realized  += (price - h['price']) * take * 1000
        sold_lots -= take
        if h['lots'] > take:
            remaining.append({**h, 'lots': h['lots'] - take})

    db['holdings'][uid] = remaining
    if uid not in db['trades']:
        db['trades'][uid] = []
    db['trades'][uid].append({'sid': sid, 'price': price, 'lots': lots,
                               'action': 'sell', 'pnl': realized,
                               'date': tw_now().strftime('%Y/%m/%d')})
    db['pnl'][uid] = db['pnl'].get(uid, 0.0) + realized
    save_data(db)

    emoji = '🟢' if realized >= 0 else '🔴'
    sign  = '+' if realized >= 0 else ''
    return (
        f'💸 **賣出記錄成功**\n'
        f'股票：{sid}　價格：{price} 元　張數：{lots} 張\n'
        f'{emoji} 本次損益：**{sign}{realized:,.0f} 元**\n'
        f'累計損益：**{sign}{db["pnl"][uid]:,.0f} 元**'
    )

def cmd_leaderboard():
    pnl = db.get('pnl', {})
    if not pnl:
        return '🏆 **損益排行榜**\n\n目前尚無任何交易記錄。'
    sorted_users = sorted(pnl.items(), key=lambda x: x[1], reverse=True)
    medals = ['🥇','🥈','🥉','4️⃣','5️⃣']
    lines  = ['🏆 **損益排行榜**\n']
    for i, (uid, total) in enumerate(sorted_users[:5]):
        sign = '+' if total >= 0 else ''
        lines.append(f'{medals[i]} <@{uid}>　{sign}{total:,.0f} 元')
    return '\n'.join(lines)

def cmd_poll(question, opt1, opt2):
    return (
        f'🗳️ **{question}**\n\n'
        f'🅰️ {opt1}\n'
        f'🅱️ {opt2}\n\n'
        f'請點下方表情回應投票！（🅰️ 或 🅱️）'
    )

def cmd_challenge(uid, uname, sid):
    sid = sid.strip().upper()
    week_key = tw_now().strftime('%Y-W%W')
    if uid not in db['challenges']:
        db['challenges'][uid] = {}

    if week_key in db['challenges'][uid]:
        existing = db['challenges'][uid][week_key]
        return (
            f'⚔️ 你本週已提交挑戰股票：**{existing["sid"]}**\n'
            f'起始價格：{existing["start_price"]} 元\n'
            f'挑戰截止：{existing["end_date"]}'
        )

    # 抓最新收盤價
    r = sb.safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': tw_now().strftime('%Y%m') + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=5
    )
    start_price = None
    if r and '查詢無資料' not in r.text:
        try:
            import pandas as pd
            text = r.text
            idx  = text.find('"日期"')
            if idx == -1:
                for yr in ['115/', '114/', '113/']:
                    idx = text.find(yr)
                    if idx != -1:
                        idx = text.rfind('\n', 0, idx) + 1
                        break
            if idx != -1:
                df = sb.safe_read_csv(text[idx:], 'CH', min_cols=7)
                df = df[df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)]
                if not df.empty:
                    start_price = float(str(df.iloc[-1, 6]).replace(',', ''))
        except:
            pass

    if start_price is None:
        return f'❌ 無法取得 {sid} 的最新價格，請確認股票代號是否正確。'

    end_date = (tw_now() + timedelta(days=7)).strftime('%Y/%m/%d')
    db['challenges'][uid][week_key] = {
        'sid': sid, 'start_price': start_price, 'end_date': end_date
    }
    save_data(db)

    # 顯示本週所有挑戰
    all_ch = []
    for u, weeks in db['challenges'].items():
        if week_key in weeks:
            ch = weeks[week_key]
            all_ch.append(f'<@{u}>　**{ch["sid"]}**　起始 {ch["start_price"]} 元')

    lines = [
        f'⚔️ **本週選股挑戰**\n',
        f'✅ <@{uid}> 加入挑戰！股票：**{sid}**　起始價：{start_price} 元\n',
        f'📅 截止日期：{end_date}\n',
        '**本週參賽名單：**',
    ] + all_ch
    return '\n'.join(lines)

# ══════════════════════════════════════════════════════
# 註冊 Slash Commands
# ══════════════════════════════════════════════════════
def register_commands():
    import requests as req
    url = f'https://discord.com/api/v10/applications/{APP_ID}/commands'
    headers = {'Authorization': f'Bot {BOT_TOKEN}', 'Content-Type': 'application/json'}
    commands = [
        {'name': 'help',    'description': '📋 顯示所有指令說明'},
        {'name': 'run',     'description': '🚀 手動觸發選股分析',
         'options': [{'name': 'mode', 'description': '執行模式', 'type': 3, 'required': False,
                      'choices': [{'name': '自動判斷（auto）', 'value': 'auto'},
                                  {'name': '盤後結算（close）', 'value': 'close'},
                                  {'name': '盤前複習（preview）', 'value': 'preview'}]}]},
        {'name': 'status',  'description': '📊 查看上次執行時間與狀態'},
        {'name': 'stock',   'description': '🔍 個股技術分析與 0–5 星推薦度',
         'options': [{'name': 'code', 'description': '股票代號（例如：2330）', 'type': 3, 'required': True}]},
        {'name': 'fortune', 'description': '🔮 抽取今日股市運勢'},
        {'name': 'roast',   'description': '🗣️ 川投顧用川普語氣評論今日大盤'},
        {'name': 'topbuyer',  'description': '📈 今日外資買超前 10 名'},
        {'name': 'topseller', 'description': '📉 今日外資賣超前 10 名'},
        {'name': 'holding', 'description': '💼 查看你的持倉紀錄'},
        {'name': 'buy',     'description': '🛒 記錄買入股票',
         'options': [
             {'name': 'code',  'description': '股票代號', 'type': 3,  'required': True},
             {'name': 'price', 'description': '買入價格（元）', 'type': 10, 'required': True},
             {'name': 'lots',  'description': '買入張數', 'type': 4,  'required': True},
         ]},
        {'name': 'sell',    'description': '💸 記錄賣出股票並計算損益',
         'options': [
             {'name': 'code',  'description': '股票代號', 'type': 3,  'required': True},
             {'name': 'price', 'description': '賣出價格（元）', 'type': 10, 'required': True},
             {'name': 'lots',  'description': '賣出張數', 'type': 4,  'required': True},
         ]},
        {'name': 'poll',    'description': '🗳️ 發起股票話題投票',
         'options': [
             {'name': 'question', 'description': '投票題目', 'type': 3, 'required': True},
             {'name': 'option1',  'description': '選項一',   'type': 3, 'required': True},
             {'name': 'option2',  'description': '選項二',   'type': 3, 'required': True},
         ]},
        {'name': 'leaderboard', 'description': '🏆 查看伺服器成員損益排行榜'},
        {'name': 'challenge',   'description': '⚔️ 提交本週選股挑戰，一週後比誰獲利高',
         'options': [{'name': 'code', 'description': '你的挑戰股票代號', 'type': 3, 'required': True}]},
    ]
    r = req.put(url, headers=headers, json=commands, timeout=10)
    print(f"[Bot] Slash Commands 註冊{'成功' if r.status_code in (200,201) else f'失敗：{r.status_code}'}")

# ══════════════════════════════════════════════════════
# HTTP Server
# ══════════════════════════════════════════════════════
class InteractionHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'OK')

    def do_POST(self):
        length   = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)
        sig      = self.headers.get('X-Signature-Ed25519', '')
        ts       = self.headers.get('X-Signature-Timestamp', '')

        if not verify_signature(raw_body, sig, ts):
            self.send_response(401); self.end_headers(); return

        body  = json.loads(raw_body)
        itype = body.get('type')

        if itype == 1:
            self.send_json(200, {'type': 1}); return

        if itype == 2:
            cmd     = body['data']['name']
            opts    = body['data'].get('options', [])
            token   = body['token']
            uid, uname = get_user(body)

            # ── 即時回應指令 ──
            if cmd == 'help':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_help()}}); return

            if cmd == 'status':
                if last_run['time'] is None:
                    content = 'ℹ️ 尚未執行過任何分析。'
                else:
                    tw  = last_run['time'].strftime('%Y/%m/%d %H:%M')
                    emo = {'success':'✅','running':'⏳','error':'❌'}.get(last_run['status'],'❓')
                    ml  = {'auto':'自動判斷','close':'盤後結算','preview':'盤前複習'}.get(last_run['mode'],'')
                    content = f"{emo} **上次執行紀錄**\n🕐 {tw}（台灣時間）\n📋 {ml}\n📅 {last_run['date']}\n📊 {last_run['status']}"
                    if last_run['error']:
                        content += f"\n⚠️ {last_run['error']}"
                self.send_json(200, {'type': 4, 'data': {'content': content}}); return

            if cmd == 'fortune':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_fortune()}}); return

            if cmd == 'roast':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_roast()}}); return

            if cmd == 'holding':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_holding(uid, uname)}}); return

            if cmd == 'buy':
                sid, price, lots = get_opt(opts,'code'), get_opt(opts,'price'), get_opt(opts,'lots')
                self.send_json(200, {'type': 4, 'data': {'content': cmd_buy(uid, uname, sid, price, lots)}}); return

            if cmd == 'sell':
                sid, price, lots = get_opt(opts,'code'), get_opt(opts,'price'), get_opt(opts,'lots')
                self.send_json(200, {'type': 4, 'data': {'content': cmd_sell(uid, uname, sid, price, lots)}}); return

            if cmd == 'leaderboard':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_leaderboard()}}); return

            if cmd == 'poll':
                q, o1, o2 = get_opt(opts,'question'), get_opt(opts,'option1'), get_opt(opts,'option2')
                self.send_json(200, {'type': 4, 'data': {'content': cmd_poll(q, o1, o2)}}); return

            # ── 需要 API 的指令（DEFERRED）──
            if cmd in ('run', 'stock', 'topbuyer', 'topseller', 'challenge'):
                self.send_json(200, {'type': 5})

                def bg():
                    import requests as req
                    followup = f'https://discord.com/api/v10/webhooks/{APP_ID}/{token}/messages/@original'

                    if cmd == 'run':
                        mode = get_opt(opts, 'mode', 'auto')
                        ml   = {'auto':'自動判斷','close':'盤後結算','preview':'盤前複習'}.get(mode, mode)
                        req.patch(followup, json={'content': f'⏳ 正在執行 **{ml}** 分析，約需 3–5 分鐘...'}, timeout=10)
                        global last_run
                        last_run.update({'time': tw_now(), 'mode': mode,
                                         'date': sb.get_target_date(mode), 'status': 'running', 'error': None})
                        try:
                            os.environ['RUN_MODE'] = mode
                            sb.run_analysis()
                            last_run['status'] = 'success'
                            req.patch(followup, json={'content': f'✅ **{ml}** 分析完成，結果已發送至頻道。'}, timeout=10)
                        except Exception as e:
                            last_run['status'] = 'error'; last_run['error'] = str(e)
                            req.patch(followup, json={'content': f'❌ 執行失敗：{e}'}, timeout=10)

                    elif cmd == 'stock':
                        sid = get_opt(opts, 'code', '')
                        req.patch(followup, json={'content': f'🔍 正在分析 **{sid}**...'}, timeout=10)
                        result = analyze_stock(sid)
                        req.patch(followup, json={'content': result or f'❌ 無法取得 {sid} 的資料，請確認代號是否正確。'}, timeout=10)

                    elif cmd in ('topbuyer', 'topseller'):
                        top_type = 'buy' if cmd == 'topbuyer' else 'sell'
                        label    = '買超' if top_type == 'buy' else '賣超'
                        emoji    = '📈' if top_type == 'buy' else '📉'
                        req.patch(followup, json={'content': f'{emoji} 正在抓取外資{label}資料...'}, timeout=10)
                        data, date_str = fetch_top_traders(top_type)
                        if data is None:
                            req.patch(followup, json={'content': f'❌ 無法取得資料（{date_str}），可能尚未更新。'}, timeout=10)
                        else:
                            lines = [f'{emoji} **今日外資{label}前 {len(data)} 名**（{date_str}）\n']
                            for i, e in enumerate(data, 1):
                                sign = '+' if e['foreign'] >= 0 else ''
                                lines.append(f'**{i}.** {e["sid"]} {e["name"]}　外資 {sign}{fmt_share(e["foreign"])} 股')
                            req.patch(followup, json={'content': '\n'.join(lines)}, timeout=10)

                    elif cmd == 'challenge':
                        sid = get_opt(opts, 'code', '')
                        req.patch(followup, json={'content': f'⚔️ 正在查詢 {sid} 最新股價...'}, timeout=10)
                        result = cmd_challenge(uid, uname, sid)
                        req.patch(followup, json={'content': result}, timeout=10)

                threading.Thread(target=bg, daemon=True).start()
                return

            self.send_json(200, {'type': 4, 'data': {'content': '❓ 未知指令'}})

# ══════════════════════════════════════════════════════
# 內建排程
# ══════════════════════════════════════════════════════
def scheduler():
    fired = set()
    while True:
        now = tw_now()
        h, wd, mn = now.hour, now.weekday(), now.minute
        if wd < 5 and h == 16 and mn == 0:
            k = (now.date(), 'close')
            if k not in fired:
                fired.add(k)
                os.environ['RUN_MODE'] = 'auto'
                threading.Thread(target=sb.run_analysis, daemon=True).start()
        if 0 < wd <= 5 and h == 6 and mn == 0:
            k = (now.date(), 'preview')
            if k not in fired:
                fired.add(k)
                os.environ['RUN_MODE'] = 'auto'
                threading.Thread(target=sb.run_analysis, daemon=True).start()
        if h == 0 and mn == 1:
            cutoff = now.date() - timedelta(days=7)
            fired  = {k for k in fired if k[0] >= cutoff}
        time.sleep(60)

# ══════════════════════════════════════════════════════
# 啟動
# ══════════════════════════════════════════════════════
if __name__ == '__main__':
    missing = [k for k, v in {
        'DISCORD_BOT_TOKEN':  BOT_TOKEN,
        'DISCORD_PUBLIC_KEY': PUBLIC_KEY,
        'DISCORD_APP_ID':     APP_ID,
    }.items() if not v]
    if missing:
        print(f"[錯誤] 缺少環境變數：{', '.join(missing)}"); __import__('sys').exit(1)

    register_commands()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"[Bot] 已啟動，監聽 port {PORT}")
    HTTPServer(('0.0.0.0', PORT), InteractionHandler).serve_forever()