"""
個股相關指令：/stock /topbuyer /topseller。
共用 analyze_stock_data 給 Web /api/stock 用。
"""
import logging
import time

import config
from advanced_indicators import calc_advanced_indicators
from chase import check_strong_chase, count_consecutive_limit_ups
from entry_zone import calc_entry_zone
from format_utils import fmt_share
from indicators import (
    calc_bias_and_entry, calc_macd, calc_volume_ratio, check_ema_bull,
)
from scoring import calc_chip_concentration, calc_score
from time_utils import get_target_date, prev_months, tw_now
from twse_kbar import build_history_fast, fetch_stock_day_fast
from twse_t86 import fetch_t86_cached

logger = logging.getLogger(__name__)


# /api/stock 快取
_STOCK_API_CACHE = {}


def _grade_from_score(score):
    if score >= 85: return 'SS', '🔥', '各項指標多數達標，可考慮進場布局。'
    if score >= 68: return 'S',  '💎', '條件不錯但非最佳，小量試水溫。'
    if score >= 52: return 'A',  '📈', '訊號普通，建議等待更明確訊號再進場。'
    return None, '', '條件偏弱，暫時觀望。'


def analyze_stock_data(sid):
    """
    完整個股分析（fetch K bars + 計算指標 + 評分）
    回傳結構化 dict 供 Discord /stock 與 Web /api/stock 共用，失敗回 None。
    """
    sid = sid.strip().upper()

    today_str = tw_now().strftime('%Y%m%d')
    months = prev_months(today_str, n=6)
    df_all = build_history_fast(sid, months)
    if df_all.empty or 'date' not in df_all.columns or len(df_all) < 10:
        return None

    latest_kbar_date = df_all['date'].iloc[-1].isoformat() if not df_all.empty else None
    kbar_count       = len(df_all)

    price      = float(df_all['close'].iloc[-1])
    prev_close = float(df_all['close'].iloc[-2])
    diff       = price - prev_close
    change     = round(diff / prev_close * 100, 2) if prev_close else 0.0
    vol_ratio  = calc_volume_ratio(df_all, df_all['date'].iloc[-1])
    is_bull, ema_mode = check_ema_bull(df_all)

    foreign = trust = None
    try:
        date_str = get_target_date('auto')
        df_i = fetch_t86_cached(date_str)
        if df_i is not None and not df_i.empty and '_foreign' in df_i.columns:
            row = df_i[df_i['sid_clean'] == sid]
            if not row.empty:
                foreign = int(row['_foreign'].values[0])
                trust   = int(row['_trust'].values[0])
    except Exception as e:
        logger.warning('[/stock] 取法人資料失敗：%s', e)

    bias = calc_bias_and_entry(df_all, price)
    adv  = calc_advanced_indicators(df_all, price)
    macd = calc_macd(df_all)
    chip = {}
    if foreign is not None:
        vol_last = int(df_all['volume'].iloc[-1]) if not df_all.empty else 0
        chip = calc_chip_concentration(foreign or 0, trust or 0, vol_last)

    consec = count_consecutive_limit_ups(df_all)

    entry = {
        'change':       change,
        'vol_ratio':    vol_ratio,
        'foreign':      foreign or 0,
        'trust':        trust or 0,
        'bias':         bias,
        'adv':          adv,
        'chip_score':   chip.get('score', 0),
        'macd_score':   macd.get('macd_score', 5),
        'market_score': 0,
        'margin_score': 0,
        'consec_score': 0,
    }
    score = calc_score(entry)
    grade, grade_emoji, rec = _grade_from_score(score)

    chase_mode  = 'normal'
    chase_check = None
    if consec >= 3:
        chase = check_strong_chase(entry, macd, entry.get('market_score', 0))
        chase_check = chase
        if   chase['passed'] >= 5: chase_mode = 'strong_chase'
        elif chase['passed'] >= 4: chase_mode = 'watch'
        else:                       chase_mode = 'reject'

    zone_low, zone_high = calc_entry_zone(price, chase_mode, grade=grade, precision=1)

    return {
        'sid':       sid,
        'price':     round(price, 2),
        'prev_close': round(prev_close, 2),
        'diff':      round(diff, 2),
        'change':    change,
        'vol_ratio': round(float(vol_ratio), 2) if vol_ratio is not None else None,
        'ema_mode':  ema_mode,
        'foreign':   foreign,
        'trust':     trust,
        'bias':      bias,
        'adv':       {k: v for k, v in adv.items() if not callable(v)},
        'macd':      macd,
        'chip':      chip,
        'consec_limit_up': consec,
        'chase_mode':      chase_mode,
        'chase_check':     chase_check,
        'entry_zone_low':  zone_low,
        'entry_zone_high': zone_high,
        'est_target1':     round(price * 1.05, 1),
        'est_target2':     round(price * 1.10, 1),
        'est_stop_loss':   round(price * 0.95, 1),
        'score':       score,
        'grade':       grade,
        'grade_emoji': grade_emoji,
        'rec':         rec,
        'latest_kbar_date': latest_kbar_date,
        'kbar_count':       kbar_count,
        'queried_at':       tw_now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def format_stock_text(d):
    """analyze_stock_data 的 dict → Discord 顯示文字。失敗回 None。"""
    if d is None:
        return None
    sign    = '+' if d['diff'] >= 0 else ''
    ema_tag = '(備援EMA)' if d['ema_mode'] == 'fallback' else ''
    bias    = d['bias']; adv = d['adv'] or {}; macd = d['macd'] or {}; chip = d['chip'] or {}

    lines = [
        f"🔍 **{d['sid']}**",
        '',
        '🔹基本資料',
        f"收盤價格：{d['price']:,.1f}　漲幅：{sign}{d['change']}%　量比：{d['vol_ratio']:.1f}x{ema_tag}",
    ]
    if d['foreign'] is not None:
        lines.append(f"外資：{fmt_share(d['foreign'])} 股　投信：{fmt_share(d['trust'])} 股")
    if bias:
        sp = '+' if bias['bias_pct'] >= 0 else ''
        lines.append(f"乖離率（10日）：{sp}{bias['bias_pct']}%　{bias['bias_emoji']} {bias['bias_label']}")

    consec = d['consec_limit_up']
    chase  = d['chase_check']
    if d['chase_mode'] == 'strong_chase':
        lines += ['', f'🚀強勢追漲（連續{consec}日漲停，5/5 條件達標）',
                  f"進場區間：{d['entry_zone_low']:,.1f} ~ {d['entry_zone_high']:,.1f} 元（容忍跳空 0~7%）",
                  '➡️ T+1 開盤在此區間以開盤價買；跳空 >7% 放棄；跌破收盤不接刀']
    elif d['chase_mode'] == 'watch':
        lines += ['', f'⚠️觀察名單（連續{consec}日漲停但僅 {chase["passed"]}/5 過）',
                  '➡️ **不建議買進**，僅供觀察']
        lines += [f'  {r}' for r in chase.get('reasons', [])]
    elif d['chase_mode'] == 'reject':
        lines += ['', f'❌連續{consec}日漲停但僅 {chase["passed"]}/5 過 — 風險過高，不推薦']
    else:
        gap_label = '3%' if d.get('grade') == 'SS' else '2%'
        lines += ['', '🎯建議進場區（限價單）',
                  f"進場區間：{d['entry_zone_low']:,.1f} ~ {d['entry_zone_high']:,.1f} 元（容忍跳空 0~{gap_label}）",
                  '➡️ 隔日 T+1 觸及才算進場；以實際成交價為準，目標 +5% / +10%、停損 -5%',
                  f"預估目標一：{d['est_target1']:,.1f} 元　預估目標二：{d['est_target2']:,.1f} 元　預估停損：{d['est_stop_loss']:,.1f} 元"]

    if adv.get('atr_stop'):
        lines.append(f"參考動態停損（2×ATR）：{adv['atr_stop']:,.1f} 元（{adv['atr_pct']}%）")

    has_adv = any([adv.get('rsi_label'), adv.get('resistance_label'),
                   adv.get('position_label'), adv.get('obv_label'),
                   chip.get('score', 0) > 0, macd.get('macd_label')])
    if has_adv:
        lines += ['', '📊輔助數據']
        if macd.get('macd_label'):       lines.append(f"MACD：{macd['macd_label']}")
        if adv.get('rsi_label'):         lines.append(f"RSI：{adv['rsi_label']}")
        if adv.get('resistance_label'):  lines.append(f"壓力位：{adv['resistance_label']}")
        if adv.get('position_label'):    lines.append(f"位階：{adv['position_label']}")
        if adv.get('obv_label'):         lines.append(f"OBV：{adv['obv_label']}")
        if chip.get('label') and chip.get('score', 0) > 0:
            lines.append(f"籌碼：{chip['label']}")

    if d['grade']:
        lines += ['', f"{d['grade_emoji']} **【{d['grade']} {d['score']}分】**", f"📝 {d['rec']}"]
    else:
        lines += ['', f"📝 {d['rec']}（積分 {d['score']} 分，未達推薦門檻）"]
    return '\n'.join(lines)


def analyze_stock(sid):
    """Discord /stock 舊接口：直接回傳格式化文字（或 None）"""
    return format_stock_text(analyze_stock_data(sid))


def stock_api_get(sid, force=False):
    """Web /api/stock：含 15 分鐘記憶體快取。"""
    now = time.time()
    if not force and sid in _STOCK_API_CACHE:
        ts, data = _STOCK_API_CACHE[sid]
        if now - ts < config.STOCK_API_CACHE_TTL_SEC:
            data = dict(data)
            data['_from_cache'] = True
            return data
    data = analyze_stock_data(sid)
    if data is not None:
        _STOCK_API_CACHE[sid] = (now, data)
        data['_from_cache'] = False
    return data


def fetch_top_traders(top_type='buy', n=10):
    """/topbuyer / /topseller：抓 T86 共享快取，回傳前 N 名。"""
    date_str = get_target_date('auto')
    df = fetch_t86_cached(date_str)
    if df is None or df.empty or '_foreign' not in df.columns:
        return None, date_str

    name_col = df.columns[1]
    if top_type == 'buy':
        rows = df[df['_foreign'] > 0].sort_values('_foreign', ascending=False).head(n)
    else:
        rows = df[df['_foreign'] < 0].sort_values('_foreign', ascending=True).head(n)

    # 用 to_dict('records'):itertuples 會把底線開頭欄位改名（同 topflow.py 的雷）
    result = []
    for row_dict in rows.to_dict('records'):
        result.append({
            'sid':     row_dict['sid_clean'],
            'name':    str(row_dict[name_col]).strip(),
            'foreign': int(row_dict['_foreign']),
            'trust':   int(row_dict['_trust']),
        })
    return result, date_str


def get_latest_price(sid):
    """抓 sid 最新收盤價；資料抓不到回 None。"""
    today_str = tw_now().strftime('%Y%m%d')
    df = fetch_stock_day_fast(sid, today_str[:6])
    if df.empty:
        # 跨月初幾天可能本月還沒有資料 → 退一個月再試
        months = prev_months(today_str, n=2)
        if len(months) >= 2:
            df = fetch_stock_day_fast(sid, months[1])
    if df.empty:
        return None
    try:
        return float(df['close'].iloc[-1])
    except Exception:
        return None
