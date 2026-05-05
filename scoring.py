"""
評分相關：總分 calc_score、大盤環境、融資增幅、籌碼集中度。
"""


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


def calc_score(entry):
    """
    綜合積分（v4）。SS ≥ 85, S ≥ 68, A ≥ 52，其餘淘汰。
    各 entry 期待包含 change / vol_ratio / foreign / trust / bias / adv /
    macd_score / consec_score / market_score / margin_score / chip_score。
    """
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
