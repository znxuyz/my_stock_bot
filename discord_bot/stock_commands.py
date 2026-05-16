"""
個股相關指令：/stock /topbuyer /topseller。
共用 analyze_stock_data 給 Web /api/stock 用。
v6.2：MACD (8,17,5)，v6.2 評分 + 五星推薦度（total_score / 2）。
"""
import logging
import time

import config
from accumulation import detect_stalker_setup
from advanced_indicators import calc_advanced_indicators
from chase import count_consecutive_limit_ups
from format_utils import fmt_share
from indicators import (
    calc_5d_cumulative_change,
    calc_5d_price_range,
    calc_bias_and_entry,
    calc_bias_20,
    calc_daily_amount,
    calc_macd,
    calc_vol_vs_60d_ratio,
    calc_volume_ratio,
    check_ema_bull,
    count_limit_ups_in_window,
)
from scoring import (
    calc_chip_concentration,
    calc_score_v62,
    status_to_emoji,
    status_to_position_pct,
)
from time_utils import get_target_date, prev_months, tw_now
from twse_kbar import build_history_fast, fetch_stock_day_fast
from twse_t86 import fetch_t86_cached, get_inst_history

logger = logging.getLogger(__name__)


# /api/stock 快取
_STOCK_API_CACHE = {}


def _recommend_from_total(total):
    """v6.2 五星推薦度：total_score // 2（0~5）。"""
    star = max(0, min(5, int(total) // 2))
    if star >= 4: rec = '訊號強烈，已進入 ACTIVE 主進場區。'
    elif star >= 3: rec = '累積中，可分批布局觀察。'
    elif star >= 2: rec = '條件混雜，建議等待更明確訊號。'
    else: rec = '條件偏弱，暫時觀望。'
    return star, rec


def _stalker_checklist(price, change, vol_ratio, vol60, amount, cum5d,
                       price_range, limit_ups_10d, ema_ok, bias_pct,
                       bias20, acc_setup):
    """逐項回傳 (label, passed, value_str) 給 Discord 顯示。"""
    checks = []
    checks.append(('收盤 ≥ 10 元', price >= config.MIN_PRICE, f'{price:.2f}'))
    checks.append((
        f'漲幅 {config.STALKER_MIN_TODAY_CHANGE}% ~ {config.STALKER_MAX_TODAY_CHANGE}%',
        config.STALKER_MIN_TODAY_CHANGE <= change <= config.STALKER_MAX_TODAY_CHANGE,
        f'{change:+.2f}%',
    ))
    checks.append((
        f'量比 {config.STALKER_VOL_RATIO_MIN}~{config.STALKER_VOL_RATIO_MAX}x',
        config.STALKER_VOL_RATIO_MIN <= vol_ratio <= config.STALKER_VOL_RATIO_MAX,
        f'{vol_ratio:.2f}x',
    ))
    if vol60 is not None:
        checks.append((
            f'量/60日 < {config.STALKER_MAX_VOL_VS_60D}',
            vol60 < config.STALKER_MAX_VOL_VS_60D,
            f'{vol60:.2f}x',
        ))
    checks.append((
        f'日成交金額 ≥ {config.MIN_DAILY_AMOUNT/1e7:.0f}E (千萬)',
        amount >= config.MIN_DAILY_AMOUNT,
        f'{amount/1e8:.2f}億',
    ))
    if cum5d is not None:
        checks.append((
            f'5日累積 {config.STALKER_MIN_CUM_CHANGE}~{config.STALKER_MAX_CUM_CHANGE}%',
            config.STALKER_MIN_CUM_CHANGE <= cum5d <= config.STALKER_MAX_CUM_CHANGE,
            f'{cum5d:+.2f}%',
        ))
    if price_range is not None:
        checks.append((
            f'5日振幅 < {config.STALKER_MAX_PRICE_RANGE}%',
            price_range < config.STALKER_MAX_PRICE_RANGE,
            f'{price_range:.2f}%',
        ))
    checks.append((
        '10日內漲停 = 0',
        limit_ups_10d <= config.STALKER_MAX_LIMIT_UPS_10D,
        f'{limit_ups_10d} 次',
    ))
    checks.append(('EMA 20 > 60', ema_ok, ''))
    if bias_pct is not None:
        checks.append((
            f'乖離 10MA ≤ {config.STALKER_MAX_BIAS_10}%',
            bias_pct <= config.STALKER_MAX_BIAS_10,
            f'{bias_pct:+.2f}%',
        ))
    if bias20 is not None:
        checks.append((
            f'乖離 20MA ≤ {config.STALKER_MAX_BIAS_20}%',
            bias20 <= config.STALKER_MAX_BIAS_20,
            f'{bias20:+.2f}%',
        ))
    if acc_setup is not None:
        checks.append((
            f'Stalker 累積（{config.STALKER_MIN_BUY_DAYS}/{config.STALKER_DAYS} 天）',
            acc_setup['is_stalker'],
            f"{acc_setup['buy_days']}/{config.STALKER_DAYS}",
        ))
    return checks


def analyze_stock_data(sid):
    """
    完整個股分析（fetch K bars + 計算指標 + v6.2 評分）。
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
    ema_ok = bool(is_bull)

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
    macd = calc_macd(df_all)  # v6.2 預設 (8,17,5)
    chip = {}
    if foreign is not None:
        vol_last = int(df_all['volume'].iloc[-1]) if not df_all.empty else 0
        chip = calc_chip_concentration(foreign or 0, trust or 0, vol_last)

    consec = count_consecutive_limit_ups(df_all)

    # v6.2 額外資料
    cum5d         = calc_5d_cumulative_change(df_all)
    price_range   = calc_5d_price_range(df_all)
    limit_ups_10d = count_limit_ups_in_window(df_all, window=10)
    bias20        = calc_bias_20(df_all)
    vol60         = calc_vol_vs_60d_ratio(df_all)
    amount        = calc_daily_amount(df_all)

    # Stalker 累積偵測（資料可能不足；不致命）
    acc_setup = None
    try:
        target_date = df_all['date'].iloc[-1]
        if hasattr(target_date, 'date'):
            target_date = target_date.date()
        inst_hist = get_inst_history(sid, days=config.STALKER_DAYS, end_date=target_date)
        acc_setup = detect_stalker_setup(df_all, inst_hist)
    except Exception as e:
        logger.info('[/stock] %s Stalker 累積資料不足：%s', sid, e)
        acc_setup = {
            'is_stalker': False, 'buy_days': 0, 'cum_net': 0,
            'cum_change_pct': None, 'price_range_pct': None,
            'reason': 'no_history',
        }

    # v6.2 評分
    entry_for_score = {
        'acc_buy_days': acc_setup.get('buy_days', 0),
        'acc_cum_net':  acc_setup.get('cum_net', 0),
        'foreign': foreign or 0,
        'trust':   trust or 0,
        'cum_5d_pct': cum5d,
        'vol_vs_60d': vol60,
        'bias_20':    bias20,
        'macd_info':  macd,
    }
    score_result = calc_score_v62(entry_for_score, df_all)

    star, rec = _recommend_from_total(score_result['total_score'])
    checklist = _stalker_checklist(price, change, vol_ratio, vol60, amount,
                                   cum5d, price_range, limit_ups_10d, ema_ok,
                                   bias['bias_pct'] if bias else None, bias20, acc_setup)

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
        'bias_20':   bias20,
        'adv':       {k: v for k, v in adv.items() if not callable(v)},
        'macd':      macd,
        'chip':      chip,
        'consec_limit_up': consec,
        'cum_5d_pct': cum5d,
        'vol_vs_60d': vol60,
        'daily_amount': amount,
        'limit_ups_10d': limit_ups_10d,
        'price_range_5d': price_range,
        'acc_setup': acc_setup,
        # v6.2 評分
        'flow_score':  score_result['flow_score'],
        'trend_score': score_result['trend_score'],
        'heat_score':  score_result['heat_score'],
        'total_score': score_result['total_score'],
        'status':      score_result['status'],
        'status_emoji': status_to_emoji(score_result['status']),
        'position_pct': status_to_position_pct(score_result['status']),
        'star':        star,
        'rec':         rec,
        'checklist':   [(name, ok, val) for name, ok, val in checklist],
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
        f"收盤價格：{d['price']:,.2f}　漲幅：{sign}{d['change']:.2f}%　量比：{d['vol_ratio']:.2f}x{ema_tag}",
    ]
    if d['foreign'] is not None:
        lines.append(f"外資：{fmt_share(d['foreign'])} 股　投信：{fmt_share(d['trust'])} 股")
    if bias:
        sp = '+' if bias['bias_pct'] >= 0 else ''
        lines.append(f"乖離率（10日）：{sp}{bias['bias_pct']:.2f}%　{bias['bias_emoji']} {bias['bias_label']}")

    # v6.2 Stalker 條件檢查
    if d.get('checklist'):
        lines += ['', '🧭 **v6.2 Stalker 條件檢查**']
        for name, ok, val in d['checklist']:
            mark = '✅' if ok else '❌'
            suffix = f'（{val}）' if val else ''
            lines.append(f'  {mark} {name}{suffix}')

    # v6.2 評分
    lines += [
        '',
        f"📊 **v6.2 評分**：Flow {d['flow_score']}/5 + Trend {d['trend_score']}/3 "
        f"+ Heat {d['heat_score']}/2 = **{d['total_score']}/10** "
        f"({d['status_emoji']} {d['status']})",
        f"五星推薦度：{'★' * d['star']}{'☆' * (5 - d['star'])}（{d['star']}/5）",
        f"📝 {d['rec']}",
        "進場：市價（T+1 開盤；一字漲停 missed）",
        f"倉位建議：{d['position_pct']}%",
    ]

    # 輔助指標
    has_adv = any([adv.get('rsi_label'), adv.get('resistance_label'),
                   adv.get('position_label'), adv.get('obv_label'),
                   chip.get('score', 0) > 0, macd.get('macd_label')])
    if has_adv:
        lines += ['', '📊輔助數據']
        if macd.get('macd_label'):       lines.append(f"MACD(8,17,5)：{macd['macd_label']}")
        if adv.get('rsi_label'):         lines.append(f"RSI：{adv['rsi_label']}")
        if adv.get('resistance_label'):  lines.append(f"壓力位：{adv['resistance_label']}")
        if adv.get('position_label'):    lines.append(f"位階：{adv['position_label']}")
        if adv.get('obv_label'):         lines.append(f"OBV：{adv['obv_label']}")
        if chip.get('label') and chip.get('score', 0) > 0:
            lines.append(f"籌碼：{chip['label']}")

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
        months = prev_months(today_str, n=2)
        if len(months) >= 2:
            df = fetch_stock_day_fast(sid, months[1])
    if df.empty:
        return None
    try:
        return float(df['close'].iloc[-1])
    except Exception:
        return None
