"""
連續漲停 + 強勢追漲門檻判定。
"""


def count_consecutive_limit_ups(df, threshold=9.5):
    """
    從最後一筆往回計算連續漲停天數（含當日）。
    台股漲停 +10%，實務上 ≥ +9.5% 視為漲停（含計算誤差）。
    """
    if len(df) < 2:
        return 0
    closes = df['close'].astype(float).values
    cnt = 0
    for i in range(len(closes) - 1, 0, -1):
        prev = closes[i - 1]
        if prev == 0:
            break
        chg = (closes[i] - prev) / prev * 100
        if chg >= threshold:
            cnt += 1
        else:
            break
    return cnt


def check_strong_chase(entry, macd_info, market_score):
    """
    對連續漲停 ≥ 3 日的股票檢查 5 項追漲門檻。
    回 {'passed': int, 'reasons': [str], 'checks': [(name, ok)...]}
    """
    foreign = entry.get('foreign', 0)
    trust   = entry.get('trust', 0)
    total_inst = foreign + trust

    chip_score = entry.get('chip_score', 0)
    vr         = entry.get('vol_ratio', 0)

    macd_dif = macd_info.get('dif')
    macd_dea = macd_info.get('dea')
    macd_exp = macd_info.get('expanding', False)
    macd_ok  = (macd_dif is not None and macd_dea is not None
                and macd_dif > macd_dea and macd_exp)

    checks = [
        ('法人合計買超 ≥ 200K 股', total_inst >= 200000),
        ('量比 ≥ 2.0x',           vr >= 2.0),
        ('籌碼集中度 ≥ 10%',       chip_score >= 5),
        ('MACD 多頭擴張',         macd_ok),
        ('大盤環境分數 ≥ 0',       market_score >= 0),
    ]
    passed  = sum(1 for _, ok in checks if ok)
    reasons = [('✅ ' if ok else '❌ ') + name for name, ok in checks]
    return {'passed': passed, 'checks': checks, 'reasons': reasons}
