"""
川投顧量化系統 Discord Bot ── 完整版
指令：/help /run /status /stock /fortune /roast
      /topbuyer /topseller /holding /buy /sell
      /poll /leaderboard /challenge
"""
import os, sys, json, threading, time, random, io
try:
    import db as _db
    _DB_OK = True
except Exception:
    _DB_OK = False
from datetime import datetime, timedelta, timezone, date
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

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
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)

def get_user(body):
    member = body.get('member', {})
    user   = member.get('user', body.get('user', {}))
    uid    = user.get('id', 'unknown')
    name   = user.get('global_name') or user.get('username', '匿名')
    return uid, name

def get_guild(body):
    return body.get('guild_id', 'dm')

def is_admin(body):
    """檢查是否為伺服器管理員（MANAGE_GUILD 權限）"""
    perms = int(body.get('member', {}).get('permissions', 0))
    return bool(perms & 0x20)  # MANAGE_GUILD bit

# ══════════════════════════════════════════════════════
# /stock 個股分析
# ══════════════════════════════════════════════════════
def analyze_stock(sid):
    import pandas as pd
    sid = sid.strip().upper()

    # ── 抓 K 棒（含 high/low 供進階指標使用）──
    frames = []
    d = tw_now().date().replace(day=1)
    for _ in range(6):  # 6個月，EMA120需要約5個月
        ym = d.strftime('%Y%m')
        r  = sb.safe_get(
            'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
            params={'response': 'csv', 'date': ym + '01', 'stockNo': sid},
            timeout=15, retries=2, wait=5
        )
        if r and '查詢無資料' not in r.text:
            try:
                text  = r.text
                lines = text.splitlines()
                hi = None
                for i, line in enumerate(lines):
                    if '日期' in line and '收盤' in line:
                        hi = i; break
                if hi is None:
                    for i, line in enumerate(lines):
                        import re as _re
                        if _re.search(r'\d{3}/\d{2}/\d{2}', line):
                            hi = max(0, i-1); break
                if hi is not None:
                    df = sb.safe_read_csv('\n'.join(lines[hi:]), f'SD-{sid}', min_cols=7)
                    df = df[df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)].copy()
                    if not df.empty:
                        def r2d(s):
                            y, m, dd = str(s).split('/')
                            return date(int(y)+1911, int(m), int(dd))
                        df['date']   = df.iloc[:, 0].apply(r2d)
                        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',',''), errors='coerce')
                        df['high']   = pd.to_numeric(df.iloc[:, 4].astype(str).str.replace(',',''), errors='coerce')
                        df['low']    = pd.to_numeric(df.iloc[:, 5].astype(str).str.replace(',',''), errors='coerce')
                        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',',''), errors='coerce')
                        frames.append(df[['date','close','high','low','volume']].dropna(subset=['date','close']))
            except:
                pass
        d = (d - timedelta(days=1)).replace(day=1)
        time.sleep(0.3)

    if not frames:
        return None
    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    if len(df_all) < 10:
        return None

    # ── 基本數值 ──
    price     = float(df_all['close'].iloc[-1])
    prev_close= float(df_all['close'].iloc[-2])
    diff      = price - prev_close
    change    = round(diff / prev_close * 100, 2) if prev_close else 0.0
    vol_ratio = sb.calc_volume_ratio(df_all, df_all['date'].iloc[-1])
    is_bull, ema_mode = sb.check_ema_bull(df_all)

    # ── 法人資料 ──
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
    except:
        pass

    # ── 進階指標（需要 high/low）──
    bias  = sb.calc_bias_and_entry(df_all, price) if hasattr(sb, 'calc_bias_and_entry') else None
    adv   = sb.calc_advanced_indicators(df_all, price) if hasattr(sb, 'calc_advanced_indicators') else {}
    chip  = {}
    if foreign is not None and hasattr(sb, 'calc_chip_concentration'):
        vol_last = int(df_all['volume'].iloc[-1]) if not df_all.empty else 0
        chip = sb.calc_chip_concentration(foreign or 0, trust or 0, vol_last)

    # ── 積分制評分（直接呼叫 sb.calc_score，與每日分析完全一致）──
    _entry = {
        'change':       change,
        'vol_ratio':    vol_ratio,
        'foreign':      foreign or 0,
        'trust':        trust or 0,
        'bias':         bias,
        'adv':          adv,
        'chip_score':   chip.get('score', 0),
        'market_score': 0,
        'margin_score': 0,
        'consec_score': 0,
    }
    score = sb.calc_score(_entry)

    # 積分轉等級
    if   score >= 85: grade, grade_emoji = 'SS', '🔥'
    elif score >= 68: grade, grade_emoji = 'S',  '💎'
    elif score >= 52: grade, grade_emoji = 'A',  '📈'
    else:             grade, grade_emoji = None,  ''

    if   score >= 85: rec = '各項指標多數達標，可考慮進場布局。'
    elif score >= 68: rec = '條件不錯但非最佳，小量試水溫。'
    elif score >= 52: rec = '訊號普通，建議等待更明確訊號再進場。'
    else:             rec = '條件偏弱，暫時觀望。'

    # ── 組裝輸出（與每日分析格式一致）──
    sign    = '+' if diff >= 0 else ''
    ema_tag = '(備援EMA)' if ema_mode == 'fallback' else ''
    lines   = [
        f'🔍 **{sid}**',
        '',
        '🔹基本資料',
        f'收盤價格：{price:,.1f}　漲幅：{sign}{change}%　量比：{vol_ratio:.1f}x{ema_tag}',
    ]
    if foreign is not None:
        lines.append(f'外資：{fmt_share(foreign)} 股　投信：{fmt_share(trust)} 股')
    if bias:
        sp = '+' if bias['bias_pct'] >= 0 else ''
        lines.append(f"乖離率（10日）：{sp}{bias['bias_pct']}%　{bias['bias_emoji']} {bias['bias_label']}")
    # 進場區間 [close × 0.97, close × 1.00]，T+1 觸到才算進場
    zone_low  = round(price * 0.97, 1)
    zone_high = round(price * 1.00, 1)
    est_t1    = round(price * 1.05, 1)
    est_t2    = round(price * 1.10, 1)
    est_stop  = round(price * 0.95, 1)
    lines += ['', '🎯建議進場區（限價單）',
              f"進場區間：{zone_low:,.1f} ~ {zone_high:,.1f} 元",
              f"➡️ 隔日 T+1 觸及才算進場；以實際成交價為準，目標 +5% / +10%、停損 -5%",
              f"預估目標一：{est_t1:,.1f} 元　預估目標二：{est_t2:,.1f} 元　預估停損：{est_stop:,.1f} 元"]
    if adv.get('atr_stop'):
        lines.append(f"參考動態停損（2×ATR）：{adv['atr_stop']:,.1f} 元（{adv['atr_pct']}%）")
    has_adv = any([adv.get('rsi_label'), adv.get('resistance_label'),
                   adv.get('position_label'), adv.get('obv_label'),
                   chip.get('score', 0) > 0])
    if has_adv:
        lines += ['', '📊輔助數據']
        if adv.get('rsi_label'):
            lines.append(f"RSI：{adv['rsi_label']}")
        if adv.get('resistance_label'):
            lines.append(f"壓力位：{adv['resistance_label']}")
        if adv.get('position_label'):
            lines.append(f"位階：{adv['position_label']}")
        if adv.get('obv_label'):
            lines.append(f"OBV：{adv['obv_label']}")
        if chip.get('label') and chip.get('score', 0) > 0:
            lines.append(f"籌碼：{chip['label']}")
    if grade:
        lines += ['', f'{grade_emoji} **【{grade} {score}分】**', f'📝 {rec}']
    else:
        lines += ['', f'📝 {rec}（積分 {score} 分，未達推薦門檻）']
    return '\n'.join(lines)


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
    '今日運勢：你選的股票會漲，只是不一定是今天。',
    '法人今日神出鬼沒，建議你也神出鬼沒，不要讓他們看到你在追高。',
    '今日宜長線，忌短線，長線的定義是你忘記自己有買這支股票。',
    '財星高照，但照的是隔壁同事，你今天還是乖乖等訊號。',
    '今日大盤將有驚喜，驚喜是漲還是跌，請自行解讀。',
    '你的停損單今日將扮演重要角色，請好好珍惜它的存在。',
    '今日不宜看盤，因為你越看越想動，越動越虧。',
    '外資方向未明，但你的帳戶方向非常明，明顯在縮水。',
    '今日宜分批，忌全壓，分批的意思是每次都買貴一點點。',
    '大吉之日，宜靜待財富自己走進帳戶，預計需要等很久。',
    '今日市場情緒偏多，但你的情緒偏虧，請先處理情緒。',
    '技術面顯示今日適合進場，基本面顯示你的薪水不適合進場。',
    '今日犯月亮座，建議少看財經新聞，多看天空，股票跌了天也不會塌。',
    '量縮價穩，是在蓄勢，還是在放棄，答案一週後揭曉。',
    '今日適合做功課，功課做完了再問為什麼沒進場，答案是你沒錢。',
    '今日偏吉，尤其對還沒買股票的人而言，空手就是贏。',
    '法人今日態度曖昧，就像你前任，你永遠猜不透他們要幹嘛。',
    '今日宜控制倉位，忌梭哈，梭哈是一種信仰，信仰有時候是錯的。',
    '大盤今日會給你一個訊號，訊號是：你該去吃午飯了，別再盯著盤了。',
    '今日財運中等，適合小賺，不適合大賺，大賺留給明天的你後悔用。',
    '今日宜觀察法人動向，法人的方向就是你三天後的方向。',
    '股市今日如詩如畫，詩是悲詩，畫是跌破支撐位的K線圖。',
    '今日上升三角形型態，代表即將突破，突破方向請問問上帝。',
    '外資今日連續買超第N天，N的數字越大，代表回檔越可怕。',
    '今日適合長抱，長抱的好處是你不用每天看盤，壞處是你還是每天看。',
    '今日運勢：停損出場是一種智慧，繼續抱著是一種信仰，請選擇你的信仰。',
    '今日黑馬可能出現在你沒買的那支，請接受這個殘酷的事實。',
    '今日大吉，但大吉只限於你的心態，帳戶請另行確認。',
    '今日技術面多頭，籌碼面法人進場，消息面一片看好，所以今天一定大跌。',
    '宜：耐心等待訊號；忌：看別人賺錢然後衝動追買。今日請記住這個忌。',
    '今日你的持股將進入關鍵時刻，關鍵時刻通常就是最讓人焦慮的時刻。',
    '投信今日大買某檔，但那檔你沒買，這就是命。',
    '今日宜做筆記，把今天的操作記下來，以後看了才知道當時有多衝動。',
    '今日最佳策略：假裝自己沒有看盤，然後悄悄地看，這樣帳戶跌了也比較不痛。',
    '外資今日方向不明，就像你的人生方向，但至少你還有本金，感謝上天。',
    '今日提醒：帳戶裡的數字只是數字，重要的是心情，心情好了錢自然會來，大概啦。',
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
    '台積電今天的股價，非常漂亮，非常，我一直說台積電是最好的公司，沒有人比我更早說這個。',
    '今天大盤跌了，假新聞，完全是假新聞。我的帳戶是漲的，我的帳戶永遠是漲的。',
    '聯準會說要升息，我說不行。我比聯準會更懂經濟，我的經濟學是最好的，哈佛都教不出來的那種。',
    '有人說外資在跑，讓他們跑，我們台灣的錢比他們多，我們不需要外資，我們很厲害。',
    '今天某些股票跌停，這些公司的老闆應該來找我，我知道怎麼讓它漲停，非常簡單。',
    '投信今天大買，非常聰明的決定，和我當初買的時候一樣聰明，我們英雄所見略同。',
    '有分析師說台股高估，這個分析師是笨蛋，我認識很多分析師，這個最笨，相信我。',
    '今天量縮，我不喜歡量縮，量縮很無聊，我喜歡爆量，爆量才是真正的台股，最好的台股。',
    '有人說現在不適合進場，這種人一輩子都在等，等到死都沒進場，然後說股市很難，可悲。',
    '今天的台股就像我的人生，一直在創新高，一直，從來不停止創新高，美極了。',
    '聯電今天漲了，我很早就看好聯電，很早，比所有人都早，沒有人比我更早看好聯電。',
    '今天有人說要停損，停損？什麼是停損？真正的投資者不停損，他們只是換個角度看帳戶。',
    '外資今天買超，非常好，他們終於開竅了。我一直告訴他們台股的偉大，他們現在聽進去了。',
    '今天大盤漲了，是因為聽了我的建議。昨天跌了，是因為沒聽我的建議。規律非常明顯。',
    '有人說這波是假突破，我說是真突破，我的眼光是最準的，準得讓人害怕，相信我。',
    '今天的成交量，非常巨大，非常，我從來沒見過這麼巨大的成交量，等等我見過，但這次更大。',
    '台股今天創新高，很多人說是運氣，不是運氣，是實力，我的實力，我一直說會創新高。',
    '今天有支股票漲停，我知道哪支，但我不能說，因為說了你們都會去買，那我就賣不到好價格了。',
    '市場今天很震盪，我喜歡震盪，震盪代表機會，我最擅長在震盪中賺錢，沒有人比我更擅長。',
    '有人問我什麼時候該賣？我說：永遠不賣。你問為什麼？因為賣了就賺不到後面的漲幅了，對吧。',
    '今天法人的操作讓我想到我年輕的時候，充滿活力，充滿野心，不過他們還是沒我厲害。',
    '台股今天讓很多人虧錢，不是台股的問題，是那些人的問題，他們不懂我說的那套方法。',
    '今天某個產業大漲，我上週就說過了，我說過很多次了，沒有人聽，現在後悔了吧。',
    '外資賣了好多，讓他們賣，賣完就是我們撿便宜的時候，我一直用這套邏輯，一直賺錢。',
    '有人說現在是熊市，熊市？什麼熊市？我只看到機會，到處都是機會，你看不到是你的問題。',
    '今天的K線圖，非常漂亮，就像一幅畫，我很懂藝術，我也很懂K線，都是美的事物。',
    '我今天看了一下整體市場，結論是：買就對了。這是我研究三十年的精華，免費送給你們。',
    '今天有人說風險很高，風險？每個成功的人都是踩著風險上去的，膽小的人永遠賺不了大錢。',
    '台股今天的表現，全亞洲第一，全世界前三，這是我說的，也是數據說的，我和數據都不說謊。',
    '今天跌的那些股票，明天一定漲回來。我怎麼知道？因為我一直都是對的，一直，沒有例外。',
    '有人問我台股的目標價？我說：無限，台股的目標價是無限，因為台灣的潛力是無限的，相信我。',
    '今天的盤勢告訴我一件事：那些唱空的人，輸了。他們永遠在輸，因為他們不了解台灣的偉大。',
    '今天我看了很多數據，結論非常清晰，清晰到讓我自己都震驚，但我不能告訴你結論是什麼。',
    '有人說現在買太貴了，太貴？我年輕的時候也說太貴，結果那個「太貴」現在漲了十倍。學到了嗎。',
    '今天整個市場都在看我的臉色，我知道，因為每次我說話，市場就動，這就叫做影響力。',
    '有人說要分散風險，分散？輸家才分散，贏家all-in，我一直all-in，一直贏，相信我。',
    '台股今天需要我，就像美國需要我一樣，我是最好的救市者，沒有之一，從來沒有。',
    '今天有支股票讓我想起了我的第一筆投資，那筆投資賺了很多，非常多，多得我都不好意思說出來。',
    '有人說外資才是主力，錯了，我才是主力，我的資金就是市場，我說漲就漲，說跌就跌。',
    '今天的台股就是在等我，等我下指令，等我說話，等我給方向，台股需要領導，我就是那個領導。',
    '我對台股的愛是真實的，比任何分析師都真實，他們只看數據，我看的是靈魂，台股的靈魂。',
    '今天我總結了一下我的投資哲學：買對的股票，在對的時間，賣在最高點，非常簡單，人人都會，只有我做到了。',
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
        '💼 `/holding [@對象]` 查看持倉紀錄（不填則看自己）\n'
        '🛒 `/buy [代號] [價格] [股數]` 記錄買入\n'
        '💸 `/sell [代號] [價格] [股數]` 記錄賣出並計算損益\n'
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

def get_guild_id_from_body(body):
    return body.get('guild_id', 'dm')

def get_latest_price(sid):
    """抓單股最新收盤價"""
    ym = tw_now().strftime('%Y%m')
    r  = sb.safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': ym + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=5
    )
    if r is None or '查詢無資料' in r.text:
        return None
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
        if idx == -1:
            return None
        df = sb.safe_read_csv(text[idx:], f'LP-{sid}', min_cols=7)
        df = df[df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)]
        if df.empty:
            return None
        return float(str(df.iloc[-1, 6]).replace(',', ''))
    except:
        return None

def cmd_holding(uid, uname, guild_id='dm'):
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    holdings = _db.get_holdings(guild_id, uid)
    if not holdings:
        return f'💼 **{uname} 的持倉**\n\n目前尚無持倉記錄。'
    lines = [f'💼 **{uname} 的持倉**\n']
    total_cost = total_mkt = total_unreal = 0
    for h in holdings:
        cost = float(h['price']) * int(h['shares'])
        cur  = get_latest_price(h['sid'])
        if cur:
            mkt    = cur * int(h['shares'])
            unreal = mkt - cost
            sign   = '+' if unreal >= 0 else ''
            emoji  = '🟢' if unreal >= 0 else '🔴'
            pct    = (cur - float(h['price'])) / float(h['price']) * 100
            cur_str = f'{cur:,.1f} 元　{emoji} {sign}{unreal:,.0f}（{sign}{pct:.1f}%）'
            total_mkt    += mkt
            total_unreal += unreal
        else:
            cur_str = '（無法取得最新價格）'
        total_cost += cost
        lines.append(
            f'**{h["sid"]}**　成本 {float(h["price"]):,.1f} 元 × {int(h["shares"]):,} 股\n'
            f'　　　現價 {cur_str}\n'
            f'　　　買入日：{h["buy_date"]}'
        )
    st = '+' if total_unreal >= 0 else ''
    et = '🟢' if total_unreal >= 0 else '🔴'
    lines.append(
        f'\n━━━━━━━━━━━━━━━━\n'
        f'總成本：**{total_cost:,.0f} 元**\n'
        f'市值：**{total_mkt:,.0f} 元**\n'
        f'{et} 未實現損益：**{st}{total_unreal:,.0f} 元**'
    )
    total_pnl = _db.get_pnl(guild_id, uid)
    sp = '+' if total_pnl >= 0 else ''
    lines.append(f'💰 已實現損益：**{sp}{total_pnl:,.0f} 元**')
    return '\n'.join(lines)


def cmd_buy(uid, uname, sid, price, shares, guild_id='dm'):
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    sid = sid.strip().upper()
    try:
        _db.add_holding(guild_id, uid, sid, price, int(shares), tw_now().date())
        cost = price * int(shares)
        return (
            f'🛒 **買入記錄成功**\n'
            f'股票：{sid}　價格：{price} 元　股數：{int(shares):,} 股\n'
            f'投入金額：**{cost:,.0f} 元**'
        )
    except Exception as e:
        return f'❌ 記錄失敗：{e}'

def cmd_sell(uid, uname, sid, price, shares, guild_id='dm'):
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    sid = sid.strip().upper()
    realized, err = _db.remove_holding(guild_id, uid, sid, price, int(shares))
    if err:
        return f'❌ {err}'
    total_pnl = _db.get_pnl(guild_id, uid)
    emoji = '🟢' if realized >= 0 else '🔴'
    sign  = '+' if realized >= 0 else ''
    sp    = '+' if total_pnl >= 0 else ''
    return (
        f'💸 **賣出記錄成功**\n'
        f'股票：{sid}　價格：{price} 元　股數：{int(shares):,} 股\n'
        f'{emoji} 本次損益：**{sign}{realized:,.0f} 元**\n'
        f'累計損益：**{sp}{total_pnl:,.0f} 元**'
    )

def cmd_leaderboard(guild_id='dm'):
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    rows = _db.get_leaderboard(guild_id)
    if not rows:
        return '🏆 **損益排行榜**\n\n目前尚無任何交易記錄。'
    medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    lines  = ['🏆 **損益排行榜**\n']
    for i, row in enumerate(rows):
        total = float(row['total_pnl'])
        sign  = '+' if total >= 0 else ''
        medal = medals[i] if i < len(medals) else f'{i+1}.'
        lines.append(f'{medal} <@{row["user_id"]}>　{sign}{total:,.0f} 元')
    return '\n'.join(lines)

def cmd_poll(question, opt1, opt2):
    return (
        f'🗳️ **{question}**\n\n'
        f'🅰️ {opt1}\n'
        f'🅱️ {opt2}\n\n'
        f'請點下方表情回應投票！（🅰️ 或 🅱️）'
    )

def cmd_challenge(uid, uname, sid, guild_id='dm'):
    sid = sid.strip().upper()
    week_key = tw_now().strftime('%Y-W%W')
    if _DB_OK:
        existing = _db.get_challenge(guild_id, uid, week_key)
    else:
        existing = None

    if existing:
        return (
            f'⚔️ 你本週已提交挑戰股票：**{existing["sid"]}**\n'
            f'起始價格：{float(existing["start_price"]):,.1f} 元\n'
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

    # 找下一個週五作為結算日
    now_d    = tw_now().date()
    days_to_fri = (4 - now_d.weekday()) % 7
    if days_to_fri == 0:
        days_to_fri = 7  # 今天就是週五，算下週五
    end_date = (now_d + timedelta(days=days_to_fri)).strftime('%Y/%m/%d')
    if _DB_OK:
        _db.add_challenge(guild_id, uid, week_key, sid, start_price, end_date)

    # 顯示本週所有挑戰
    all_ch = []
    if _DB_OK:
        for ch in _db.get_all_challenges(guild_id, week_key):
            all_ch.append(f'<@{ch["user_id"]}>　**{ch["sid"]}**　起始 {float(ch["start_price"]):,.1f} 元')

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
        {'name': 'holding', 'description': '💼 查看持倉紀錄（不填看自己，填 @ 看別人）',
         'options': [
             {'name': 'target', 'description': '@某人（不填則看自己）',
              'type': 6, 'required': False}
         ]},
        {'name': 'buy',     'description': '🛒 記錄買入股票',
         'options': [
             {'name': 'code',  'description': '股票代號', 'type': 3,  'required': True},
             {'name': 'price', 'description': '買入價格（元）', 'type': 10, 'required': True},
             {'name': 'lots',  'description': '買入股數（例如：1000）', 'type': 4,  'required': True},
         ]},
        {'name': 'sell',    'description': '💸 記錄賣出股票並計算損益',
         'options': [
             {'name': 'code',  'description': '股票代號', 'type': 3,  'required': True},
             {'name': 'price', 'description': '賣出價格（元）', 'type': 10, 'required': True},
             {'name': 'lots',  'description': '賣出股數（例如：1000）', 'type': 4,  'required': True},
         ]},
        {'name': 'poll',    'description': '🗳️ 發起股票話題投票',
         'options': [
             {'name': 'question', 'description': '投票題目', 'type': 3, 'required': True},
             {'name': 'option1',  'description': '選項一',   'type': 3, 'required': True},
             {'name': 'option2',  'description': '選項二',   'type': 3, 'required': True},
         ]},
        {'name': 'leaderboard', 'description': '🏆 查看伺服器成員損益排行榜'},
        {'name': 'setup', 'description': '⚙️ 設定本伺服器的分析推播頻道（僅管理員）',
         'options': [
             {'name': 'webhook', 'description': 'Webhook URL（輸入 remove 可移除）',
              'type': 3, 'required': True}
         ]},
        {'name': 'report',    'description': '📊 查看最近結算報告與累積勝率統計'},
        {'name': 'stats',     'description': '📈 查看詳細統計與篩選邏輯修正建議'},
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
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

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
                self.send_json(200, {'type': 5})
                # 解析 target（被 @ 的人）
                target_opt = get_opt(opts, 'target')
                h_uid  = uid
                h_name = uname
                if target_opt:
                    resolved = body['data'].get('resolved', {})
                    users    = resolved.get('users', {})
                    tid      = str(target_opt)
                    if tid in users:
                        u      = users[tid]
                        h_uid  = tid
                        h_name = (u.get('global_name') or
                                  u.get('username', f'用戶{tid}'))
                    else:
                        # resolved 沒有找到，還是用 tid 當 key 查資料庫
                        h_uid  = tid
                        h_name = f'用戶{tid}'

                # 明確傳入參數避免 closure 捕捉問題
                _g_id = get_guild(body)
                def _holding_bg(_h_uid=h_uid, _h_name=h_name, _self_uid=uid, _g=_g_id):
                    import requests as req
                    followup = f'https://discord.com/api/v10/webhooks/{APP_ID}/{token}/messages/@original'
                    label = f'**{_h_name}**' if _h_uid != _self_uid else '你'
                    req.patch(followup, json={'content': f'💼 正在查詢 {label} 的持倉...'}, timeout=10)
                    result = cmd_holding(_h_uid, _h_name, _g)
                    req.patch(followup, json={'content': result}, timeout=10)
                threading.Thread(target=_holding_bg, daemon=True).start()
                return

            if cmd == 'buy':
                sid, price, lots = get_opt(opts,'code'), get_opt(opts,'price'), get_opt(opts,'lots')
                self.send_json(200, {'type': 4, 'data': {'content': cmd_buy(uid, uname, sid, price, lots, get_guild(body))}}); return

            if cmd == 'sell':
                sid, price, lots = get_opt(opts,'code'), get_opt(opts,'price'), get_opt(opts,'lots')
                self.send_json(200, {'type': 4, 'data': {'content': cmd_sell(uid, uname, sid, price, lots, get_guild(body))}}); return

            if cmd == 'leaderboard':
                self.send_json(200, {'type': 4, 'data': {'content': cmd_leaderboard(get_guild(body))}}); return

            if cmd == 'setup':
                if not is_admin(body):
                    self.send_json(200, {'type': 4, 'data': {
                        'content': '❌ 只有伺服器管理員才能使用 `/setup`。',
                        'flags': 64
                    }}); return
                webhook_input = get_opt(opts, 'webhook', '').strip()
                guild_id_setup = get_guild(body)
                if webhook_input.lower() == 'remove':
                    if _DB_OK:
                        _db.remove_guild(guild_id_setup)
                    self.send_json(200, {'type': 4, 'data': {
                        'content': '✅ 已移除本伺服器的推播設定。',
                        'flags': 64
                    }}); return
                # 測試 webhook
                import requests as req
                try:
                    test_msg = '✅ **川投顧量化系統** 設定成功！\n每日 17:00 盤後分析、07:00 盤前複習將自動推播至此頻道。'
                    r = req.post(webhook_input, json={'content': test_msg}, timeout=10)
                    if r.status_code in (200, 204):
                        if _DB_OK:
                            _db.set_guild_webhook(guild_id_setup, webhook_input, uid)
                        self.send_json(200, {'type': 4, 'data': {
                            'content': '✅ 設定成功！測試訊息已發送至目標頻道。',
                            'flags': 64
                        }})
                    else:
                        self.send_json(200, {'type': 4, 'data': {
                            'content': f'❌ Webhook 測試失敗（{r.status_code}），請確認 URL 是否正確。',
                            'flags': 64
                        }})
                except Exception as e:
                    self.send_json(200, {'type': 4, 'data': {
                        'content': f'❌ 無法連接 Webhook：{e}',
                        'flags': 64
                    }})
                return

            if cmd == 'report':
                self.send_json(200, {'type': 5})
                _g = get_guild(body)
                _tk = token
                def _report_bg(_g=_g, _tk=_tk):
                    import requests as req
                    followup = f'https://discord.com/api/v10/webhooks/{APP_ID}/{_tk}/messages/@original'
                    try:
                        result = cmd_report(_g)
                    except Exception as e:
                        result = f'❌ 查詢失敗：{e}'
                    req.patch(followup, json={'content': result or 'ℹ️ 尚無結算記錄。'}, timeout=10)
                threading.Thread(target=_report_bg, daemon=True).start()
                return

            if cmd == 'stats':
                self.send_json(200, {'type': 5})
                _g = get_guild(body)
                _tk = token
                def _stats_bg(_g=_g, _tk=_tk):
                    import requests as req
                    followup = f'https://discord.com/api/v10/webhooks/{APP_ID}/{_tk}/messages/@original'
                    try:
                        result = cmd_stats(_g)
                    except Exception as e:
                        result = f'❌ 查詢失敗：{e}'
                    req.patch(followup, json={'content': result or 'ℹ️ 尚無統計資料。'}, timeout=10)
                threading.Thread(target=_stats_bg, daemon=True).start()
                return

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
                        result = cmd_challenge(uid, uname, sid, get_guild(body))
                        req.patch(followup, json={'content': result}, timeout=10)

                threading.Thread(target=bg, daemon=True).start()
                return

            self.send_json(200, {'type': 4, 'data': {'content': '❓ 未知指令'}})

# ══════════════════════════════════════════════════════
# 內建排程
# ══════════════════════════════════════════════════════
def settle_weekly(settle_date, round_num, guild_id='default'):
    """
    結算 settle_date 這天到期的第 round_num 次結算。
    v3 改動：
      - 只結算 fill_status='filled' 的紀錄（未進場的不計）
      - 報酬以 actual_entry_price 為基準
      - 抓 actual_entry_date ~ settle_date 整段 K 棒，檢查盤中是否觸 target/stop
      - 若觸停損 → settle_pct 強制視為 -5%（停損出場）
      - 否則 settle_pct = (settle_close - actual_entry) / actual_entry × 100
    """
    import requests as req
    webhook = os.environ.get('DISCORD_WEBHOOK', '')
    if not webhook or not _DB_OK:
        return

    records = _db.get_pending_settle(settle_date, round_num, guild_id)
    if not records:
        print(f"[結算] {settle_date} 第{round_num}次：無待結算記錄")
        return

    round_label = f'第{"一" if round_num==1 else "二"}次結算（{"1週" if round_num==1 else "2週"}）'
    screen_dates = sorted(set(str(r['screen_date']) for r in records))
    week_range   = f'{screen_dates[0]} ~ {screen_dates[-1]}'

    results = []
    for r in records:
        actual_entry      = float(r['actual_entry_price'])
        actual_entry_date = r['actual_entry_date']
        t1 = float(r['actual_target1']) if r['actual_target1'] else None
        t2 = float(r['actual_target2']) if r['actual_target2'] else None
        sl = float(r['actual_stop_loss']) if r['actual_stop_loss'] else None

        # 抓 actual_entry_date ~ settle_date 整段 K 棒
        df = sb.get_period_kbars(r['sid'], actual_entry_date, settle_date)
        if df.empty:
            print(f"[結算] {r['sid']} 抓不到 K 棒，跳過")
            continue
        # 找最新的 close 當 settle_close（K 棒最後一天 ≤ settle_date）
        last_row     = df.iloc[-1]
        settle_close = float(last_row['close'])

        # 掃每天 high/low 檢查觸發
        hit_t1 = hit_t2 = hit_sl = False
        hit_t1_date = hit_t2_date = hit_sl_date = None
        for _, row in df.iterrows():
            d  = row['date']
            hi = float(row['high'])
            lo = float(row['low'])
            if t1 and not hit_t1 and hi >= t1:
                hit_t1, hit_t1_date = True, d
            if t2 and not hit_t2 and hi >= t2:
                hit_t2, hit_t2_date = True, d
            if sl and not hit_sl and lo <= sl:
                hit_sl, hit_sl_date = True, d

        # 報酬：若觸停損 → 視為停損出場 -5%；否則用 settle_close
        if hit_sl:
            settle_pct = round((sl - actual_entry) / actual_entry * 100, 2)
        else:
            settle_pct = round((settle_close - actual_entry) / actual_entry * 100, 2)

        _db.update_settle(
            r['id'], round_num, settle_close,
            hit_t1, hit_t2, hit_sl,
            hit_t1_date, hit_t2_date, hit_sl_date,
            settle_pct=settle_pct,
        )
        results.append({
            'sid': r['sid'], 'name': r.get('name',''),
            'grade': r['grade'], 'base': actual_entry,
            'cur': settle_close, 'pct': settle_pct,
            't1': t1, 't2': t2, 'sl': sl,
            'hit_t1': hit_t1, 'hit_t2': hit_t2, 'hit_sl': hit_sl,
            'pos_pct': float(r['position_pct']) if r['position_pct'] else 0,
        })

    if not results:
        return

    # 組裝報告
    lines = [
        f'📊 **【週報結算】{round_label}**',
        f'篩選週：{week_range}　結算日：{settle_date}',
        '━━━━━━━━━━━━━━━━━━━━',
    ]
    wins = losses = flats = 0
    sim_pnl = 0.0
    capital = 100000  # 模擬資金

    for r in results:
        sign  = '+' if r['pct'] >= 0 else ''
        emoji = '✅' if r['pct'] > 0 else ('❌' if r['pct'] < -3 else '➖')
        if r['pct'] > 0:   wins   += 1
        elif r['pct'] < 0: losses += 1
        else:               flats  += 1

        invest  = capital * r['pos_pct'] / 100
        pnl     = invest * r['pct'] / 100
        sim_pnl += pnl
        pnl_str = f'+{pnl:,.0f}' if pnl >= 0 else f'{pnl:,.0f}'

        t1_str = f'目標一 {r["t1"]:,.1f} {"✅" if r["hit_t1"] else "❌"}　' if r['t1'] else ''
        t2_str = f'目標二 {r["t2"]:,.1f} {"✅" if r["hit_t2"] else "❌"}　' if r['t2'] else ''
        sl_str = f'停損 {r["sl"]:,.1f} {"⚠️觸及" if r["hit_sl"] else "✅未觸"}' if r['sl'] else ''

        lines.append(
            f'{emoji} **{r["sid"]} {r["name"]}**｜{r["grade"]} 級\n'
            f'   實際進場 {r["base"]:,.1f} → 結算價 {r["cur"]:,.1f}（{sign}{r["pct"]}%）\n'
            f'   {t1_str}{t2_str}{sl_str}\n'
            f'   建議倉位 {r["pos_pct"]}%　假設投入 {invest:,.0f} 元 → **{pnl_str} 元**'
        )

    total = len(results)
    wr    = round(wins/total*100, 1) if total else 0
    sim_sign = '+' if sim_pnl >= 0 else ''

    lines += [
        '━━━━━━━━━━━━━━━━━━━━',
        f'📈 **績效摘要**',
        f'篩選支數：{total}　獲利：{wins}支　虧損：{losses}支　持平：{flats}支',
        f'勝率：**{wr}%**',
        f'模擬總損益（資金10萬）：**{sim_sign}{sim_pnl:,.0f} 元**',
    ]

    # 邏輯檢討
    grade_rows, bias_rows, dual_rows = _db.get_cumulative_stats(guild_id)
    total_n = _db.get_total_screened(guild_id)
    lines.append('━━━━━━━━━━━━━━━━━━━━')
    lines.append(f'🔍 **邏輯檢討**（累積 {total_n} 筆）')

    if grade_rows:
        lines.append('各等級勝率：')
        for g in grade_rows:
            w1 = int(g['win1'] or 0); t1_ = int(g['total'])
            wr_ = round(w1/t1_*100,1) if t1_ else 0
            ar  = round(float(g['avg_ret1'] or 0), 2)
            sign_ = '+' if ar >= 0 else ''
            lines.append(f'  {g["grade"]} 級：{wr_}%（{w1}/{t1_}）　平均 {sign_}{ar}%')

    if bias_rows:
        lines.append('乖離率勝率：')
        for b in bias_rows:
            w_ = int(b['win'] or 0); t_ = int(b['total'])
            wr_ = round(w_/t_*100,1) if t_ else 0
            ar  = round(float(b['avg_ret'] or 0), 2)
            sign_ = '+' if ar >= 0 else ''
            lines.append(f'  {b["bias_zone"]}：{wr_}%　平均 {sign_}{ar}%')

    # 異常偵測
    anomalies = []
    for g in grade_rows:
        w1 = int(g['win1'] or 0); t1_ = int(g['total'])
        if t1_ >= 10:
            wr_ = w1/t1_*100
            ar  = float(g['avg_ret1'] or 0)
            if wr_ < 45:
                anomalies.append(f'・{g["grade"]} 級勝率偏低（{wr_:.0f}%），建議提高門檻或移除')
            if ar < 0:
                anomalies.append(f'・{g["grade"]} 級平均報酬為負（{ar:.1f}%），建議調整')
    for b in bias_rows:
        w_ = int(b['win'] or 0); t_ = int(b['total'])
        if t_ >= 10 and b['bias_zone'] == '過高(>8%)':
            wr_ = w_/t_*100
            if wr_ < 40:
                anomalies.append('・乖離率 >8% 勝率過低，建議加入硬過濾（不發出信號）')

    if anomalies:
        lines.append('⚠️ **異常偵測：**')
        lines += anomalies

    # 修正建議（根據異常自動產生）
    suggestions = []
    for g in grade_rows:
        t1_ = int(g['total'])
        if t1_ >= 10:
            wr_ = int(g['win1'] or 0)/t1_*100
            if g['grade'] == 'A' and wr_ < 50:
                suggestions.append('1. 考慮將 A 級門檻從 1% 提高至 2%')
            if g['grade'] == 'SS' and wr_ > 70:
                suggestions.append('1. SS 級勝率良好，可考慮提高 SS 級倉位至 30%')
    for b in bias_rows:
        t_ = int(b['total'])
        if t_ >= 10 and b['bias_zone'] == '過高(>8%)':
            if int(b['win'] or 0)/t_*100 < 40:
                suggestions.append('2. 加入乖離率 >8% 硬過濾，直接排除不發信號')

    if suggestions:
        lines.append('📋 **修正建議（供討論）：**')
        lines += suggestions

    # 分段發送（Discord 2000字限制）
    msg = '\n'.join(lines)
    chunks, buf = [], ''
    for line in msg.splitlines(keepends=True):
        if len(buf) + len(line) > 1900 and buf:
            chunks.append(buf); buf = ''
        buf += line
    if buf:
        chunks.append(buf)
    guild_wh = _db.get_guild_webhook(guild_id) if _DB_OK else webhook
    send_wh  = guild_wh or webhook
    for chunk in chunks:
        req.post(send_wh, json={'content': chunk}, timeout=15)
        time.sleep(0.5)

    print(f"[結算] {settle_date} 第{round_num}次結算完成，{len(results)} 筆")

def cmd_report(guild_id='dm'):
    """累積統計摘要：進場率 + 賺錢勝率"""
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    try:
        grade_rows, bias_rows, _ = _db.get_cumulative_stats(guild_id)
        total_n = _db.get_total_screened(guild_id)
        if total_n == 0:
            return 'ℹ️ 尚無任何篩選記錄，等每日分析跑完後才會有資料。'
        lines = [f'📊 **累積統計（共 {total_n} 筆）**',
                 '進場率 = T+1 觸及進場區間的比例；賺錢勝率 = 已進場中賺錢的比例\n']
        if grade_rows:
            lines.append('**各等級指標：**')
            for g in grade_rows:
                t  = int(g["total"])
                fi = int(g["filled"] or 0); mi = int(g["missed"] or 0)
                decided = fi + mi
                fr = round(fi/decided*100, 1) if decided else 0
                w1 = int(g["win1"] or 0); s1 = int(g["settled1"] or 0)
                wr = round(w1/s1*100, 1) if s1 else 0
                ar = round(float(g["avg_ret1"] or 0), 2)
                sgn= '+' if ar >= 0 else ''
                lines.append(
                    f'  {g["grade"]} 級（{t} 筆）：進場率 {fr}%（{fi}/{decided}）　'
                    f'1週賺錢勝率 {wr}%（{w1}/{s1}）　均報酬 {sgn}{ar}%'
                )
        if bias_rows:
            lines.append('\n**依乖離率：**')
            for b in bias_rows:
                fi = int(b["filled"] or 0); mi = int(b["missed"] or 0)
                decided = fi + mi
                fr = round(fi/decided*100, 1) if decided else 0
                w  = int(b["win"] or 0); st = int(b["settled"] or 0)
                wr = round(w/st*100, 1) if st else 0
                ar = round(float(b["avg_ret"] or 0), 2)
                sgn= '+' if ar >= 0 else ''
                lines.append(
                    f'  {b["bias_zone"]}：進場率 {fr}%　賺錢勝率 {wr}%　均報酬 {sgn}{ar}%'
                )
        return '\n'.join(lines)
    except Exception as e:
        import traceback
        return f'❌ 查詢失敗：{e}\n```\n{traceback.format_exc()[-500:]}\n```'

def cmd_stats(guild_id='dm'):
    """詳細統計 + 修正建議（含法人組合的進場率與勝率）"""
    if not _DB_OK:
        return '❌ 資料庫未連接。'
    try:
        grade_rows, bias_rows, dual_rows = _db.get_cumulative_stats(guild_id)
        total_n = _db.get_total_screened(guild_id)
        if total_n == 0:
            return 'ℹ️ 尚無任何篩選記錄。'
        lines = [f'📈 **詳細統計（累積 {total_n} 筆）**\n']
        lines.append('**法人組合：**')
        for d in dual_rows:
            t  = int(d["total"])
            if t == 0: continue
            fi = int(d["filled"] or 0); mi = int(d["missed"] or 0)
            decided = fi + mi
            fr = round(fi/decided*100, 1) if decided else 0
            w  = int(d["win"] or 0); st = int(d["settled"] or 0)
            wr = round(w/st*100, 1) if st else 0
            ar = round(float(d["avg_ret"] or 0), 2)
            s  = '+' if ar >= 0 else ''
            lines.append(
                f'  {d["buy_type"]}（{t} 筆）：'
                f'進場率 {fr}%　賺錢勝率 {wr}%　均報酬 {s}{ar}%'
            )

        lines.append('\n**命中目標 / 觸停損：**')
        for g in grade_rows:
            t = int(g["total"])
            if t == 0: continue
            ht1 = int(g["hit_t1"] or 0)
            ht2 = int(g["hit_t2"] or 0)
            hsl = int(g["hit_sl"] or 0)
            fi  = int(g["filled"] or 0)
            lines.append(
                f'  {g["grade"]} 級：命中目標一 {ht1}/{fi}、'
                f'命中目標二 {ht2}/{fi}、觸停損 {hsl}/{fi}'
            )

        # 修正建議
        suggestions = []
        for g in grade_rows:
            t  = int(g["total"])
            fi = int(g["filled"] or 0); mi = int(g["missed"] or 0)
            decided = fi + mi
            if decided >= 10:
                fr = fi/decided*100
                if fr < 40:
                    suggestions.append(
                        f'・{g["grade"]} 級進場率僅 {fr:.0f}% (<40%)，'
                        '建議放寬進場區間（例如改用 close × 0.95~1.00）'
                    )
            s1 = int(g["settled1"] or 0)
            if s1 >= 10:
                wr = int(g["win1"] or 0)/s1*100
                ar = float(g["avg_ret1"] or 0)
                if g["grade"] == "A" and wr < 50:
                    suggestions.append('・A 級賺錢勝率 <50%，考慮提高分數門檻')
                if g["grade"] == "SS" and wr > 70:
                    suggestions.append('・SS 級勝率良好（>70%），可考慮加大倉位')
                if ar < -1:
                    suggestions.append(f'・{g["grade"]} 級平均報酬為負，需重新評估')
        for b in bias_rows:
            st = int(b["settled"] or 0)
            if st >= 10 and b["bias_zone"] == "過高(>8%)" and int(b["win"] or 0)/st*100 < 40:
                suggestions.append('・乖離率 >8% 賺錢勝率過低，建議加入硬過濾')
        if suggestions:
            lines.append('\n📋 **修正建議：**')
            lines += suggestions
        else:
            lines.append('\n✅ 目前尚無明顯異常，繼續累積樣本中。')
        return '\n'.join(lines)
    except Exception as e:
        return f'❌ 查詢失敗：{e}'

def settle_challenge():
    """週五 21:00 自動結算本週挑戰"""
    import requests as req
    webhook = os.environ.get('DISCORD_WEBHOOK', '')
    if not webhook:
        return

    now      = tw_now()
    week_key = now.strftime('%Y-W%W')
    results  = []

    if _DB_OK:
        guilds = _db.get_all_webhooks()
    else:
        guilds = []

    for gw in guilds:
        g_id = gw['guild_id']
        g_wh = gw['webhook_url']
        g_results = []
        for ch in _db.get_all_challenges(g_id, week_key):
            cur = get_latest_price(ch['sid'])
            if cur is None:
                continue
            start = float(ch['start_price'])
            pct   = round((cur - start) / start * 100, 2)
            g_results.append({'uid': ch['user_id'], 'sid': ch['sid'],
                               'start': start, 'cur': cur, 'pct': pct,
                               'webhook': g_wh, 'guild_id': g_id})
        results.extend(g_results)

    if not results:
        if webhook:
            req.post(webhook, json={'content': '⚔️ **本週選股挑戰結算**\n\n本週無人參賽。'}, timeout=10)
        return

    if not results:
        req.post(webhook, json={'content': '⚔️ **本週選股挑戰結算**\n\n本週無人參賽。'}, timeout=10)
        return

    results.sort(key=lambda x: x['pct'], reverse=True)
    medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    lines  = ['⚔️ **本週選股挑戰結算！**\n']
    for i, r in enumerate(results):
        sign  = '+' if r['pct'] >= 0 else ''
        emoji = '🟢' if r['pct'] >= 0 else '🔴'
        medal = medals[i] if i < len(medals) else f'{i+1}.' 
        lines.append(
            f'{medal} <@{r["uid"]}> **{r["sid"]}**\n'
            f'   起始 {r["start"]:,.1f} → 現價 {r["cur"]:,.1f} 元\n'
            f'   {emoji} {sign}{r["pct"]}%'
        )

    winner = results[0]
    lines.append(f'\n🏆 本週冠軍：<@{winner["uid"]}> 的 **{winner["sid"]}**，報酬率 {("+" if winner["pct"]>=0 else "")}{winner["pct"]}%！')
    lines.append('\n⚔️ 下週挑戰已重置，歡迎用 `/challenge` 繼續參賽！')
    req.post(webhook, json={'content': '\n'.join(lines)}, timeout=10)

    # 結算後清零本週所有挑戰
    if _DB_OK:
        for gw in _db.get_all_webhooks():
            _db.clear_challenges(gw['guild_id'], week_key)
    print(f"[挑戰] 週五結算完成並清零，共 {len(results)} 人參賽")

def scheduler():
    fired = set()
    while True:
        now = tw_now()
        h, wd, mn = now.hour, now.weekday(), now.minute
        if wd < 5 and h == 17 and mn == 0:
            k = (now.date(), 'close')
            if k not in fired:
                fired.add(k)
                os.environ['RUN_MODE'] = 'auto'
                threading.Thread(target=sb.run_analysis, daemon=True).start()
        # 週五（weekday=4）18:00 自動結算週報（兩次）
        if wd == 4 and h == 18 and mn == 0:
            k = (now.date(), 'weekly_settle')
            if k not in fired:
                fired.add(k)
                def _do_settle(_d=now.date()):
                    if _DB_OK:
                        guilds = _db.get_all_webhooks()
                        for gw in guilds:
                            settle_weekly(_d, 1, gw['guild_id'])
                            time.sleep(1)
                            settle_weekly(_d, 2, gw['guild_id'])
                            time.sleep(1)
                    # 結算後同步更新 Web Dashboard
                    try:
                        import web_export as _we
                        _we.export_dashboard()
                    except Exception as _we_e:
                        print(f"[Web] 結算後 Dashboard 匯出失敗：{_we_e}")
                threading.Thread(target=_do_settle, daemon=True).start()
        # 週五（weekday=4）21:00 自動結算挑戰
        if wd == 4 and h == 21 and mn == 0:
            k = (now.date(), 'challenge_settle')
            if k not in fired:
                fired.add(k)
                threading.Thread(target=settle_challenge, daemon=True).start()
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

    # 初始化資料庫
    if _DB_OK:
        try:
            _db.init_db()
        except Exception as e:
            print(f"[DB] 初始化失敗：{e}")

    register_commands()
    threading.Thread(target=scheduler, daemon=True).start()

    # Bot 啟動時自動匯出一次 Dashboard（讓 redeploy 後立刻同步歷史資料）
    def _startup_export():
        time.sleep(5)  # 等資料庫連線穩定
        try:
            import web_export as _we
            _we.export_dashboard()
        except Exception as _e:
            print(f"[Web] 啟動時 Dashboard 匯出失敗：{_e}")
    threading.Thread(target=_startup_export, daemon=True).start()

    print(f"[Bot] 已啟動，監聽 port {PORT}")
    ThreadingHTTPServer(('0.0.0.0', PORT), InteractionHandler).serve_forever()