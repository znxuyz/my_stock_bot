"""
評分相關：
  v6.2 10 分制（calc_score_v62 + 子分數 + classify_status + status_to_*）
  v5 函式（calc_score）標 @deprecated 但保留（避免 import 鏈斷裂）
  通用環境函式（calc_market_env / calc_margin_score / calc_chip_concentration）不動
"""
import warnings

import config
from indicators import calc_ma_slope_5d


# ─────────── 通用環境（保留不動） ───────────

def calc_market_env(market_foreign_history):
    """
    大盤外資環境過濾。
    market_foreign_history: 近 3 天大盤外資買賣超（億元），由舊到新。
    """
    if not market_foreign_history:
        return {'score': 0, 'label': '', 'suspend': False}
    today       = market_foreign_history[-1]
    last3       = market_foreign_history[-3:]
    consec_sell = all(x < 0 for x in last3)
    total_3d    = sum(last3)

    if consec_sell and total_3d < -500:
        return {'score': 0,
                'label': '🚨 大盤外資連3日賣超逾500億，今日暫停發出進場訊號',
                'suspend': True}
    if today < -100:
        return {'score': -5,
                'label': f'⚠️ 大盤外資賣超 {today:.0f} 億，環境偏弱',
                'suspend': False}
    if today > 100:
        return {'score': 3,
                'label': f'✅ 大盤外資買超 {today:.0f} 億，環境有利',
                'suspend': False}
    return {'score': 0,
            'label': f'🔄 大盤外資中性（{today:.0f} 億）',
            'suspend': False}


def calc_margin_score(margin_today, margin_5d_ago):
    """融資 5 日增幅 → 評分。資料無效回 0 分。"""
    if not margin_5d_ago or margin_5d_ago == 0:
        return {'score': 0, 'label': ''}
    pct = (margin_today - margin_5d_ago) / margin_5d_ago * 100
    if pct >= 30:
        return {'score': -8, 'label': f'❌ 融資5日暴增 +{pct:.1f}%，散戶追高'}
    if pct >= 15:
        return {'score': -4, 'label': f'⚠️ 融資5日增加 +{pct:.1f}%，留意'}
    if pct >= 0:
        return {'score': 0,  'label': f'🔄 融資5日增幅 +{pct:.1f}%'}
    return {'score': 3, 'label': f'✅ 融資5日減少 {pct:.1f}%，籌碼健康'}


def calc_chip_concentration(foreign, trust, volume):
    """籌碼集中度 = 法人淨買超 ÷ 成交量。volume 為 0 回 0 分。"""
    if not volume or volume == 0:
        return {'score': 0, 'label': '', 'concentration': 0}
    net_buy = max(0, int(foreign)) + max(0, int(trust))
    conc    = round(net_buy / volume * 100, 1)
    if conc >= 20:
        return {'score': 8, 'label': f'🔥 籌碼集中度 {conc}%，主力強力進場',  'concentration': conc}
    if conc >= 10:
        return {'score': 5, 'label': f'✅ 籌碼集中度 {conc}%，法人積極布局',  'concentration': conc}
    if conc >= 5:
        return {'score': 2, 'label': f'🔄 籌碼集中度 {conc}%',                 'concentration': conc}
    return {'score': 0,     'label': f'（籌碼集中度 {conc}%）',                'concentration': conc}


# ─────────── v6.2 10 分制評分 ───────────

def calc_flow_score(entry, df_hist):
    """Flow Score：max 5。

    條件：
      1. 過去 5 日法人連續買 = 5/5 天 → +2
      2. 5 日累積法人淨買量 ≥ 500K 股 → +2
      3. 籌碼集中度（外資+投信）/今日量 ≥ 10% → +1
    """
    score = 0
    if int(entry.get('acc_buy_days', 0) or 0) >= 5:
        score += 2
    if int(entry.get('acc_cum_net', 0) or 0) >= 500_000:
        score += 2

    foreign = int(entry.get('foreign', 0) or 0)
    trust   = int(entry.get('trust', 0) or 0)
    vol_today = 0
    if df_hist is not None and not df_hist.empty:
        try:
            vol_today = int(df_hist['volume'].iloc[-1])
        except Exception:
            vol_today = 0
    if vol_today > 0:
        conc = (max(0, foreign) + max(0, trust)) / vol_today * 100
        if conc >= 10:
            score += 1
    return score


def calc_trend_score(df_hist, entry):
    """Trend Score：max 3。

    條件：
      1. 收盤價 > 10MA → +1
      2. 10MA 5 日斜率 > 0（上彎）→ +1
      3. MACD(8,17,5) DIF > DEA AND DIF > 0 → +1
    """
    if df_hist is None or len(df_hist) < 20:
        return 0
    closes = df_hist['close'].astype(float)
    today_close = float(closes.iloc[-1])
    ma10 = float(closes.rolling(10).mean().iloc[-1])

    score = 0
    if today_close > ma10:
        score += 1

    slope = calc_ma_slope_5d(df_hist, period=10)
    if slope is not None and slope > 0:
        score += 1

    macd = entry.get('macd_info') or {}
    dif = macd.get('dif')
    dea = macd.get('dea')
    if dif is not None and dea is not None and dif > dea and dif > 0:
        score += 1
    return score


def calc_heat_score(df_hist, entry):
    """Heat Score：max 2。Phase 1~2 用價量代理。冷門加分。

    三個條件：
      A. 5 日累積漲幅 ≤ HEAT_PROXY_CUM5D
      B. 今日量 / 60 日均量 ≤ HEAT_PROXY_VOL60D
      C. 乖離 20MA ≤ HEAT_PROXY_BIAS20

    3/3 → +2、2/3 → +1、其餘 → 0。
    任一項缺資料 → 0（保守）。
    """
    cum5d  = entry.get('cum_5d_pct')
    vol60  = entry.get('vol_vs_60d')
    bias20 = entry.get('bias_20')

    if cum5d is None or vol60 is None or bias20 is None:
        return 0

    cond_a = cum5d  <= config.HEAT_PROXY_CUM5D
    cond_b = vol60  <= config.HEAT_PROXY_VOL60D
    cond_c = bias20 <= config.HEAT_PROXY_BIAS20

    n = sum([cond_a, cond_b, cond_c])
    if n == 3: return 2
    if n == 2: return 1
    return 0


def classify_status(total):
    """0-10 → 5 段分級。"""
    if total >= config.SCORE_MOMENTUM: return 'MOMENTUM'
    if total >= config.SCORE_ACTIVE:   return 'ACTIVE'
    if total >= config.SCORE_SETUP:    return 'SETUP'
    if total >= config.SCORE_WATCH:    return 'WATCH'
    return 'NOISE'


def status_to_emoji(status):
    return {
        'MOMENTUM': '🔵', 'ACTIVE': '🟢', 'SETUP': '🟡',
        'WATCH': '🟠', 'NOISE': '🔴',
    }.get(status, '⚪')


def status_to_position_pct(status):
    """v6.2 倉位建議（取代舊的 calc_position_pct）。"""
    return {
        'MOMENTUM': 0,   # 已啟動，不新進
        'ACTIVE':   30,
        'SETUP':    18,
        'WATCH':    0,
        'NOISE':    0,
    }.get(status, 0)


def calc_score_v62(entry, df_hist):
    """v6.2 完整評分。回傳 dict 含子分數 / 總分 / 狀態。"""
    flow  = calc_flow_score(entry, df_hist)
    trend = calc_trend_score(df_hist, entry)
    heat  = calc_heat_score(df_hist, entry)
    total = flow + trend + heat
    return {
        'flow_score':  flow,
        'trend_score': trend,
        'heat_score':  heat,
        'total_score': total,
        'status':      classify_status(total),
    }


# ─────────── v5 已棄用（保留以免 import 鏈斷裂） ───────────

def calc_score(entry):
    """v5 105 分制。v6.2 起 @deprecated；本體保留給歷史相容呼叫。

    新程式請改用 calc_score_v62。
    """
    warnings.warn(
        'scoring.calc_score is deprecated since v6.2; use calc_score_v62 instead.',
        DeprecationWarning,
        stacklevel=2,
    )
    score = 0

    chg = entry.get('change', 0)
    if   3 <= chg <= 5:    score += 10
    elif 2 <= chg < 3:     score += 8
    elif 5 < chg <= 7:     score += 7
    elif 1 <= chg < 2:     score += 5
    elif chg > 7:          score += 3

    vr = entry.get('vol_ratio', 0)
    if   vr >= 3.0: score += 20
    elif vr >= 2.0: score += 15
    elif vr >= 1.5: score += 10
    elif vr >= 1.2: score += 5

    foreign = entry.get('foreign', 0)
    trust   = entry.get('trust', 0)
    total   = foreign + trust
    both    = foreign >= 10000 and trust >= 10000
    if   both and total >= 500000: score += 20
    elif both and total >= 100000: score += 15
    elif both:                      score += 10
    elif total >= 100000:           score += 8
    else:                            score += 3

    b = entry.get('bias') or {}
    bp = b.get('bias_pct')
    if   bp is None:       score += 10
    elif bp < 0:           score += 18
    elif 0 <= bp <= 3:     score += 20
    elif bp <= 5:          score += 15
    elif bp <= 8:          score += 5

    adv = entry.get('adv') or {}
    rsi = adv.get('rsi')
    if   rsi is None:        score += 5
    elif 60 <= rsi <= 80:    score += 10
    elif rsi > 80:           score += 5
    elif rsi >= 50:          score += 7

    rs = adv.get('resistance_score', 0)
    if   rs == 0:        score += 10
    elif rs == -0.25:    score += 4

    ps = adv.get('position_score', 0)
    if   ps >= 0.5:    score += 5
    elif ps == 0:      score += 3
    elif ps == -0.5:   score += 1

    score += entry.get('macd_score', 5)
    score += entry.get('consec_score', 0)
    score += entry.get('market_score', 0)
    score += entry.get('margin_score', 0)
    score += entry.get('chip_score', 0)

    return max(0, score)
